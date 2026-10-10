"""V7 AI Cinema & Short Drama Factory — acceptance scenarios A–H (Master Spec V7 §13) with the real worker code
(mock video provider, real ffmpeg assembly/animatic), plus unit checks. Mock outputs are labelled test_mode."""

import uuid

from sqlalchemy import select

from app import scheduler
from app.models import FeatureFlag, GenerationJob, JobKind, JobStatus, LedgerReason, ProductionEvent
from app.services import budget, credits, moderation, screenplay

from .conftest import WORKER_H, signup

STYLES = ["STYLE_PHOTOREAL", "STYLE_CINEMATIC", "STYLE_CARTOON_2D", "STYLE_ANIMATION_3D", "STYLE_ANIME",
          "STYLE_STYLIZED", "STYLE_MIXED"]


def enable(db, **prod_over):
    for key, value in (
        ("studio", {"credits_per_second": {"standard": 1, "premium": 3},
                    "provider_capabilities": {"mock_t2v": ["TEXT_TO_VIDEO", "CHARACTER_REFERENCE", "VIDEO_EXTEND",
                                                           "VIDEO_PREPEND", *STYLES]},
                    "scene_type_credit_multiplier": {"action": 1.5}}),
        ("productions", {"currency_per_credit": {"USD": "0.05"}, **prod_over}),
    ):
        f = db.get(FeatureFlag, key) or FeatureFlag(key=key)
        f.enabled, f.value = True, value
        db.add(f)
    db.commit()


def fund(client, db, h, n=5000):
    uid = uuid.UUID(client.get("/me", headers=h).json()["id"])
    credits.apply(db, uid, n, LedgerReason.purchase, f"p:{uuid.uuid4()}", bucket="purchased")
    db.commit()


def bal(client, h):
    return client.get("/credits", headers=h).json()["balance"]


def script(scenes: int, lines: int, words: int = 8, action: bool = False) -> str:
    out = []
    for i in range(1, scenes + 1):
        out.append(f"INT. EV {i} - GECE" if i % 2 else f"EXT. SAHİL {i} - GÜNDÜZ")
        out.append("Ayşe pencereden bakıyor." if not action else "Mert koşuyor ve kavga başlıyor.")
        for j in range(lines):
            who = "AYŞE" if j % 2 else "MERT"
            out.append(who)
            if j % 3 == 0:
                out.append("(fısıltıyla)")
            out.append(" ".join(["söz"] * (words - 2)) + f" sahne {i} replik {j}.")
        out.append("")
    return "\n".join(out)


def create(client, h, **over):
    body = {"kind": "series", "title": "Galata", "format": "short_series", "episode_duration_s": 120,
            "aspect_ratio": "1:1", "visual_style": "cinematic", "profile": "economy", **over}
    r = client.post("/productions", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def plan(client, h, pid, n, text, season=1):
    r = client.post(f"/productions/{pid}/episodes/{n}/plan?season={season}", headers=h, json={"script": text})
    assert r.status_code == 201, r.text
    return r.json()


def approve(client, h, pid, episodes=None, cap=None):
    e = client.post(f"/productions/{pid}/estimate", headers=h, json={"episodes": episodes}).json()
    r = client.post(f"/productions/{pid}/approve", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                    json={"estimate_id": e["id"], **({"cap_credits": cap} if cap is not None else {})})
    assert r.status_code == 201, r.text
    return e, r.json()


def render(client, h, pid, n, purpose="render", credits_=None, shots=None):
    return client.post(f"/productions/{pid}/episodes/{n}/{purpose}", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                       json={"confirmed_credits": credits_, **({"shots": shots} if shots else {})})


# ---------------------------------------------------------------- unit level

def test_screenplay_parser_keeps_dialogue_exact_and_plans_long_episodes():
    text = ("INT. MUTFAK - GECE\nAyşe kapıyı çarpar.\n\nAYŞE\n(bağırarak)\nSiktir git, şerefsiz!\n\n"
            "MERT: Lan, sakin ol be!\nEXT. ÇATI - ŞAFAK\nŞehir manzarası, gökyüzü kızıl.\n")
    sc = screenplay.parse(text)
    assert [s.location for s in sc] == ["MUTFAK", "ÇATI"] and sc[0].time_of_day == "night"
    lines = [b for k, b in sc[0].beats if k == "line"]
    assert [(x.character, x.text, x.delivery) for x in lines] == [
        ("AYŞE", "Siktir git, şerefsiz!", "shout"), ("MERT", "Lan, sakin ol be!", None)]
    frag, rep = screenplay.plan(sc, 60, [4, 6, 8], {"ayşe": "ayse"})
    d = frag["scenes"][0]["shots"][0]["dialogue"][0]
    assert d["text"] == "Siktir git, şerefsiz!" and d["exact"] is True and d["character"] == "ayse"
    assert frag["scenes"][1]["scene_type"] == "landscape" and rep["uncast_speakers"] == ["MERT"]
    long_sc = screenplay.parse(script(40, 8))
    frag, rep = screenplay.plan(long_sc, 1800, [4, 6, 8], {})
    assert 1600 <= rep["duration_s"] <= 1900 and rep["shots"] >= 200 and rep["lines"] == 320
    assert all(len(s["dialogue"]) <= 6 for sc_ in frag["scenes"] for s in sc_["shots"])


def test_fiction_policy_matrix():
    ok = moderation.check_fiction
    assert ok("Siktir git, şerefsiz!", "teen").allowed and "profanity" in ok("Siktir git", "teen").reasons
    assert ok("Siktir git", "general").category == "rating_required:teen"
    assert ok("I'm going to kill you, you bastard", "teen").allowed  # fictional threat between characters
    assert ok("Seni öldüreceğim", "general").allowed
    assert ok("a loli scene", "mature").category == "minors_sexual"  # never, at any rating
    assert ok("sexy child", "mature").category == "minors_sexual"
    assert ok("fake passport for the president", "mature").category == "impersonation_fraud"
    assert ok("sexy lingerie dance", "mature", kind="dialogue").allowed
    assert ok("sexy lingerie dance", "mature", kind="visual").category == "sexual_visual"
    assert not moderation.check_text("I'm going to kill you").allowed  # the default (non-fiction) screen is unchanged


def test_budget_currency_precision():
    cfg = {"currency_per_credit": {"USD": "0.05", "TRY": "1.7"}, "tax_rate": "0.2"}
    assert budget.to_minor(300, "USD", cfg) == 1800  # 300 × 0.05 × 1.2 = 18.00 USD
    assert budget.from_minor(1500, "USD", cfg) == 250  # 15 USD incl. tax buys 250 credits (floor)
    assert budget.to_minor(7, "TRY", cfg) == 1428  # 7 × 1.7 × 1.2 = 14.28
    assert budget.to_minor(1, "EUR", cfg) is None


# ---------------------------------------------------------------- scenarios

def test_a_ten_episode_two_minute_series_end_to_end(client, db, drain):
    enable(db)
    h, _ = signup(client)
    fund(client, db, h)
    entry = client.get("/productions/entry").json()
    assert [c["kind"] for c in entry["cards"]] == ["series", "film", "stars", "life_story"]
    assert entry["question_tr"] == "Hangi hikâyeyi anlatmak istersin?"
    p = create(client, h, episodes_per_season=10)
    assert len(p["episodes"]) == 10 and all(e["target_duration_s"] == 120 for e in p["episodes"])
    out = plan(client, h, p["id"], 1, script(6, 4))
    shots = [s for sc in out["storyboard"]["scenes"] for s in sc["shots"]]
    assert 100 <= sum(s["duration_s"] for s in shots) <= 160
    assert out["storyboard"]["visual_style"] == "cinematic" and "rule-based" in out["report"]["planner"]
    # free animatic first (storyboard cards, exact subtitles)
    a = client.post(f"/productions/{p['id']}/episodes/1/animatic", headers=h).json()
    assert a["credits"] == 0 and "not AI video" in a["label"]
    before = bal(client, h)
    assert drain() == 1 and bal(client, h) == before
    assert render(client, h, p["id"], 1, credits_=out["estimate"]["credits"]).json()["detail"]["code"] == \
        "budget_approval_required"
    est, auth = approve(client, h, p["id"])
    assert len(est["episodes"]) == 10 and est["episodes"][0]["planned"] and not est["episodes"][1]["planned"]
    usd_cfg = {"currency_per_credit": {"USD": "0.05"}, "tax_rate": "0"}
    assert est["currency"] == "USD" and est["amount_high_minor"] == budget.to_minor(est["credits_high"], "USD", usd_cfg)
    assert any("not available" in n for n in est["episodes"][0]["notes"])  # no TTS: said, not hidden
    r = render(client, h, p["id"], 1, credits_=out["estimate"]["credits"])
    assert r.status_code == 202, r.text
    assert drain() == len(shots) + 1
    ep = client.get(f"/productions/{p['id']}/episodes/1", headers=h).json()
    assert ep["status"]["status"] == "ready" and ep["status"]["shots_done"] == len(shots)
    assert bal(client, h) == before - out["estimate"]["credits"]
    ex = client.post(f"/productions/{p['id']}/episodes/1/export", headers=h).json()
    assert ex["manifest"]["test_mode"] is True and ex["manifest"]["ai_generated"]  # mock is never "production"
    assert ex["timeline"]["schema"] == "v7-timeline-1" and ex["captions_url"]
    types = {e["type"] for e in client.get(f"/productions/{p['id']}/events", headers=h).json()["items"]}
    assert {"production.created", "production.planned", "budget.approved", "shot.queued", "shot.completed",
            "render.completed", "export.ready"} <= types
    assert scheduler.run_task("production_outbox").result["delivered"] >= len(types)
    rep = client.get(f"/productions/{p['id']}/budget", headers=h).json()
    assert rep["settled_credits"] == out["estimate"]["credits"] and rep["remaining_credits"] == \
        auth["cap_credits"] - out["estimate"]["credits"]


def test_b_ten_minute_photoreal_film_pilot_first_then_reuse(client, db, drain):
    enable(db)
    h, _ = signup(client)
    fund(client, db, h)
    p = create(client, h, kind="film", format="short_film", episode_duration_s=600, visual_style="photoreal")
    out = plan(client, h, p["id"], 1, script(20, 6))
    shots = [s for sc in out["storyboard"]["scenes"] for s in sc["shots"]]
    assert 520 <= sum(s["duration_s"] for s in shots) <= 690
    approve(client, h, p["id"])
    est = client.post(f"/studio/projects/{p['episodes'][0]['studio_project_id']}/estimate", headers=h,
                      json={}).json()
    assert all("STYLE_PHOTOREAL" in s["capabilities"] for s in est["shots"])
    r = render(client, h, p["id"], 1, "pilot", credits_=None)
    pilot_credits = r.json()["detail"]["credits"]
    r = render(client, h, p["id"], 1, "pilot", credits_=pilot_credits)
    assert r.status_code == 202, r.text
    keys = r.json()["pilot_keys"]
    secs = sum(s["duration_s"] for s in shots if s["key"] in keys)
    assert 30 <= secs <= 60 and len(r.json()["jobs"]) == len(keys)
    assert drain() == len(keys) + 1  # pilot shots + pilot assembly
    ep = client.get(f"/productions/{p['id']}/episodes/1", headers=h).json()
    assert ep["status"]["status"] == "pilot_ready" and ep["project"]["status"] != "ready"
    pilot = db.get(GenerationJob, uuid.UUID(str(db.execute(select(GenerationJob.id).where(
        GenerationJob.kind == JobKind.studio_assemble)).scalar_one())))
    assert pilot.status == JobStatus.completed and pilot.spec["purpose"] == "pilot"
    full = client.post(f"/studio/projects/{p['episodes'][0]['studio_project_id']}/estimate", headers=h,
                       json={}).json()
    assert full["reused_shots"] == len(keys) and full["credits"] == est["credits"] - pilot_credits


def test_c_thirty_minute_animation_staged_with_profiles_and_cap(client, db, drain):
    enable(db)
    h, _ = signup(client)
    fund(client, db, h, 3000)
    p = create(client, h, kind="film", format="long_episode", episode_duration_s=1800, visual_style="animation_3d")
    out = plan(client, h, p["id"], 1, script(40, 8))
    shots = [s for sc in out["storyboard"]["scenes"] for s in sc["shots"]]
    assert len(shots) >= 200 and 1600 <= sum(s["duration_s"] for s in shots) <= 2000
    eco = client.post(f"/productions/{p['id']}/estimate", headers=h, json={"profile": "economy"}).json()
    pro = client.post(f"/productions/{p['id']}/estimate", headers=h, json={"profile": "cinema_pro"}).json()
    assert eco["profile"]["quality"] == "standard" and pro["profile"]["lock_mode"] == "strict"
    assert eco["credits_high"] > 0 and eco["seconds"] == sum(s["duration_s"] for s in shots)
    # approve only enough for the pilot: the full render is refused before anything is reserved
    _, auth = approve(client, h, p["id"], cap=80)
    before = bal(client, h)
    r = render(client, h, p["id"], 1, credits_=out["estimate"]["credits"])
    assert r.status_code == 402 and r.json()["detail"]["code"] == "budget_cap_exceeded"
    assert bal(client, h) == before and db.execute(select(GenerationJob).where(
        GenerationJob.kind == JobKind.studio_shot)).first() is None
    pc = render(client, h, p["id"], 1, "pilot").json()["detail"]["credits"]
    r = render(client, h, p["id"], 1, "pilot", credits_=pc)
    assert r.status_code == 202 and pc <= 80
    drain()
    st = client.get(f"/productions/{p['id']}/episodes/1", headers=h).json()["status"]
    assert st["status"] == "pilot_ready" and st["shots_done"] == len(r.json()["pilot_keys"])


def test_d_line_change_at_01_12_and_range_edit_with_undo_redo(client, db, drain):
    enable(db)
    h, _ = signup(client)
    fund(client, db, h)
    p = create(client, h, format="standard_episode", episode_duration_s=300, content_rating="teen")
    out = plan(client, h, p["id"], 1, script(8, 6))
    approve(client, h, p["id"])
    assert render(client, h, p["id"], 1, credits_=out["estimate"]["credits"]).status_code == 202
    drain()
    lines = client.get(f"/productions/{p['id']}/episodes/1/dialogue", headers=h).json()["items"]
    tl = client.get(f"/productions/{p['id']}/episodes/1/timeline", headers=h).json()
    at = next(x for x in tl["tracks"]["dialogue"] if x["start_ms"] <= 72000 < x["end_ms"] or x["start_ms"] >= 72000)
    lid = at["id"]
    assert lid in {x["id"] for x in lines}
    base = f"/productions/{p['id']}/episodes/1/dialogue/{lid}"
    prev = client.patch(base, headers=h, json={"text": "Bunu bir daha söyleme, salak!", "emotion": "öfkeli"}).json()
    assert prev["applied"] is False and prev["impact"]["levels"][:2] == ["subtitles", "voice_not_available"]
    assert prev["impact"]["credits_delta"] == 0 and prev["impact"]["new_shots"] == 0  # no shot regeneration
    done = client.patch(base + "?apply=true", headers=h, json={"text": "Bunu bir daha söyleme, salak!"}).json()
    assert done["applied"] and done["line_after"]["rev"] == 1
    hist = client.get(base + "/history", headers=h).json()["items"]
    assert [x["text"] for x in hist][-1] == "Bunu bir daha söyleme, salak!" and len(hist) == 2
    # lock: no silent change; unlock is explicit
    client.patch(base + "?apply=true", headers=h, json={"locked": True})
    assert client.patch(base + "?apply=true", headers=h, json={"text": "x yok"}).json()["detail"]["code"] == \
        "dialogue_locked"
    pid_ = p["episodes"][0]["studio_project_id"]
    assert client.post(f"/studio/projects/{pid_}/undo", headers=h).status_code == 201  # undo the lock
    assert client.post(f"/studio/projects/{pid_}/redo", headers=h).status_code == 201  # and redo it
    assert next(x for x in client.get(f"/productions/{p['id']}/episodes/1/dialogue", headers=h).json()["items"]
                if x["id"] == lid)["locked"] is True
    # range edit 01:12–01:17: only the touched shot(s) regenerate, the plan says so
    r = client.post(f"/productions/{p['id']}/episodes/1/range-edit", headers=h, json={
        "start_ms": 72000, "end_ms": 77000, "change": {"expression": "gözleri dolu, titreyen ses"}}).json()
    plan_ = r["plan"]
    assert 1 <= len(plan_["changed_clips"]) <= 2 and plan_["new_shots"] == len(plan_["changed_clips"])
    assert plan_["credits"] > 0 and any("whole shot" in x for x in plan_["risks"])
    assert client.post(r["apply"], headers=h).status_code == 200
    e = client.post(f"/studio/projects/{pid_}/estimate", headers=h, json={}).json()
    assert e["new_shots"] == len(plan_["changed_clips"]) and e["reused_shots"] == len(e["shots"]) - e["new_shots"]


def test_e_slang_is_kept_exactly_under_the_fiction_policy(client, db, drain):
    enable(db)
    h, _ = signup(client)
    fund(client, db, h)
    slang = "Siktir git lan, seni şerefsiz! Bir daha gelme buraya."
    text = f"INT. BAR - GECE\nAyşe bardağı yere atar.\nAYŞE\n(bağırarak)\n{slang}\nMERT\nSeni öldüreceğim.\n"
    g = create(client, h, content_rating="general")
    r = client.post(f"/productions/{g['id']}/episodes/1/plan", headers=h, json={"script": text})
    assert r.json()["detail"]["code"] == "content_blocked" and r.json()["detail"]["category"] == "rating_required:teen"
    m = create(client, h, content_rating="mature", title="Bar")
    out = plan(client, h, m["id"], 1, text)
    lines = client.get(f"/productions/{m['id']}/episodes/1/dialogue", headers=h).json()["items"]
    assert [x["text"] for x in lines] == [slang, "Seni öldüreceğim."] and all(x["exact"] for x in lines)
    assert lines[0]["delivery"] == "shout"
    client.post(f"/productions/{m['id']}/episodes/1/animatic", headers=h)
    drain()
    from app.services.storage import get_storage

    vtt = next(v for k, v in get_storage().objects.items() if k.endswith("captions.vtt"))[0].decode()
    assert f"AYŞE: {slang}" in vtt or f"Ayşe: {slang}" in vtt  # byte-for-byte in the subtitles
    bad = client.patch(f"/productions/{m['id']}/episodes/1/dialogue/{lines[0]['id']}", headers=h,
                       json={"text": "a loli joke"}).json()
    assert bad["detail"]["code"] == "content_blocked"  # never allowed, at any rating
    assert out["storyboard"]["scenes"][0]["shots"][0]["dialogue"][0]["text"] == slang


def test_f_episode_four_story_change_branches_without_rewriting(client, db):
    enable(db)
    h, _ = signup(client)
    p = create(client, h, episodes_per_season=5)
    bible = {"world": "İstanbul, bugün", "locations": ["EV", "SAHİL"],
             "characters": [{"key": "ayse", "name": "Ayşe"}, {"key": "mert", "name": "Mert"}],
             "mysteries": [{"key": "secret", "keywords": ["mektup"], "reveal_episode": 5}]}
    assert client.put(f"/productions/{p['id']}/bible", headers=h, json=bible).json()["version"] == 1
    talk = "INT. EV - GECE\nAYŞE\nMerhaba Mert.\nMERT\nMerhaba Ayşe.\n"
    for n in (1, 2, 3, 4, 5):
        plan(client, h, p["id"], n, talk)
    r = client.put(f"/productions/{p['id']}/episodes/4/events", headers=h,
                   json={"events": [{"type": "death", "character": "ayse"}]}).json()
    assert r["impact"][0]["episode"] == 5 and r["impact"][0]["new_findings"][0]["code"] == "dead_character_appears"
    f5 = client.get(f"/productions/{p['id']}/episodes/5/continuity", headers=h).json()["findings"]
    assert f5[0]["severity"] == "error" and f5[0]["code"] == "dead_character_appears"
    assert render(client, h, p["id"], 5, credits_=0).json()["detail"]["code"] == "continuity_errors"
    leak = "INT. EV - GECE\nMERT\nMektup nerede?\n"
    plan(client, h, p["id"], 2, leak)
    assert any(f["code"] == "mystery_leak" for f in
               client.get(f"/productions/{p['id']}/episodes/2/continuity", headers=h).json()["findings"])
    assert client.post(f"/productions/{p['id']}/episodes/4/approve", headers=h).json()["state"]["characters"][
        "ayse"]["status"] == "dead"
    # "don't let her die in episode 4": the approved episode is not rewritten — the story branches
    r = client.put(f"/productions/{p['id']}/episodes/4/events", headers=h, json={"events": []})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "episode_locked"
    imp = client.post(f"/productions/{p['id']}/episodes/4/events/impact", headers=h, json={"events": []}).json()
    assert imp["impact"][0]["resolved_findings"][0]["code"] == "dead_character_appears"
    old_branch = client.get(f"/productions/{p['id']}", headers=h).json()["active_branch_id"]
    b = client.post(f"/productions/{p['id']}/branches", headers=h,
                    json={"from_episode": 4, "reason": "Ayşe yaşasın", "events": []}).json()
    eps = b["production"]["episodes"]
    assert b["production"]["active_branch_id"] == b["branch_id"] != old_branch
    assert [e["branch_id"] == b["branch_id"] for e in eps] == [False, False, False, True, True]  # 1-3 shared
    assert client.get(f"/productions/{p['id']}/episodes/5/continuity", headers=h).json()["findings"] == [] or all(
        f["code"] != "dead_character_appears"
        for f in client.get(f"/productions/{p['id']}/episodes/5/continuity", headers=h).json()["findings"])
    from app.models import ProductionEpisode

    old4 = db.execute(select(ProductionEpisode).where(ProductionEpisode.branch_id == uuid.UUID(old_branch),
                                                      ProductionEpisode.number == 4)).scalar_one()
    assert old4.events == [{"type": "death", "character": "ayse"}]  # history untouched


def test_g_fifteen_dollar_hard_cap_and_honest_alternatives(client, db):
    enable(db)
    h, _ = signup(client)
    fund(client, db, h)
    p = create(client, h, kind="film", format="short_film", episode_duration_s=1200, profile="cinema_pro")
    out = plan(client, h, p["id"], 1, script(30, 7))
    e = client.post(f"/productions/{p['id']}/estimate", headers=h,
                    json={"cap_minor": 1500, "currency": "USD"}).json()
    assert e["cap_credits"] == 300 and e["feasible"] is False  # 15 USD at 0.05/credit
    kinds = {a["type"] for a in e["alternatives"]}
    assert {"reduce_duration", "economy_profile", "animatic_and_pilot", "increase_budget"} <= kinds
    red = next(a for a in e["alternatives"] if a["type"] == "reduce_duration")
    assert 0 < red["max_seconds"] < 1200
    r = client.post(f"/productions/{p['id']}/approve", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                    json={"estimate_id": e["id"]}).json()
    assert r["cap_credits"] == 300 and r["cap_minor"] == 1500
    before = bal(client, h)
    r = render(client, h, p["id"], 1, credits_=out["estimate"]["credits"])
    assert r.status_code == 402 and r.json()["detail"]["code"] == "budget_cap_exceeded"
    assert r.json()["detail"]["cap"] == 300 and bal(client, h) == before  # nothing reserved past the cap
    no = client.post(f"/productions/{p['id']}/estimate", headers=h, json={"cap_minor": 1500, "currency": "EUR"})
    assert no.json()["detail"]["code"] == "pricing_not_configured"


def test_h_user_cancel_refunds_everything(client, db):
    enable(db)
    h, _ = signup(client)
    fund(client, db, h)
    p = create(client, h)
    out = plan(client, h, p["id"], 1, script(6, 4))
    approve(client, h, p["id"])
    before = bal(client, h)
    r = render(client, h, p["id"], 1, credits_=out["estimate"]["credits"])
    assert r.status_code == 202 and bal(client, h) == before - out["estimate"]["credits"]
    claimed = client.post("/internal/worker/claim", headers=WORKER_H, json={"worker_id": "w1", "models": ["mock_t2v"]})
    assert claimed.status_code == 200  # one shot is in flight
    c = client.post(f"/productions/{p['id']}/episodes/1/cancel", headers=h).json()
    assert c["cancelled_jobs"] == len(r.json()["jobs"])
    j = claimed.json()
    client.post(f"/internal/worker/jobs/{j['job_id']}/fail", headers=WORKER_H,
                json={"worker_id": "w1", "attempt": j["attempt"], "error_code": "cancelled", "retryable": False})
    assert bal(client, h) == before
    st = client.get(f"/productions/{p['id']}/episodes/1", headers=h).json()["status"]
    assert st["status"] == "cancelled" and st["credits_refunded"] == out["estimate"]["credits"]
    assert db.execute(select(ProductionEvent).where(ProductionEvent.type == "credits.adjusted")).first()


def test_life_story_privacy_people_gate_and_access_control(client, db):
    enable(db)
    h, _ = signup(client)
    s = client.post("/life-stories", headers=h).json()
    assert s["private"] and len(s["questions"]) == 6
    client.put(f"/life-stories/{s['id']}/answers", headers=h, json={"key": "childhood",
                                                                     "text": "Karaköy'de büyüdüm, Zeynep ile."})
    r = client.post(f"/life-stories/{s['id']}/chronology", headers=h, json={"events": [
        {"when": "1998", "title": "Çocukluk", "text": "Zeynep Kaya ile Karaköy'de; tel 0532 123 45 67",
         "kind": "fact"},
        {"when": "2010", "title": "Ayrılık", "text": "Babam hastalandı, kanser.", "kind": "dramatized"}]}).json()
    assert "Zeynep Kaya" in r["privacy"]["possible_people"] and r["privacy"]["contact_data"]["phone"]
    assert "health" in r["privacy"]["sensitive_topics"]
    body = {"confirm_privacy": True, "title": "Benim Hikâyem", "format": "short_film", "episode_duration_s": 300}
    assert client.post(f"/life-stories/{s['id']}/approve", headers=h, json=body).json()["detail"]["code"] == \
        "contact_data_present"
    r = client.post(f"/life-stories/{s['id']}/anonymise", headers=h, json={"mapping": {"Zeynep Kaya": "Z."}}).json()
    assert "Zeynep" not in r["chronology"][0]["text"] and "[removed]" in r["chronology"][0]["text"]
    r = client.post(f"/life-stories/{s['id']}/approve", headers=h, json=body).json()
    assert r["production"]["kind"] == "life_story" and r["production"]["visibility"] == "private"
    assert "(DRAMATIZED)" in r["suggested_script"]
    pid = r["production"]["id"]
    chk = client.get(f"/productions/{pid}/publish-check", headers=h).json()
    assert chk["can_publish"] is False and "people_and_consent_not_confirmed" in chk["blockers"]
    client.put(f"/productions/{pid}/people-confirmed", headers=h, json={"confirm": True})
    assert "people_and_consent_not_confirmed" not in client.get(f"/productions/{pid}/publish-check",
                                                                headers=h).json()["blockers"]
    other, _ = signup(client)  # tenant isolation
    assert client.get(f"/productions/{pid}", headers=other).status_code == 404
    assert client.get(f"/life-stories/{s['id']}", headers=other).status_code == 404
    young, tok = signup(client)
    from app.models import User

    u = db.get(User, uuid.UUID(client.get("/me", headers=young).json()["id"]))
    u.age_confirmed_at = None
    db.commit()
    r = client.post("/productions", headers=young, json={"kind": "film", "title": "x", "format": "micro",
                                                         "episode_duration_s": 60, "content_rating": "mature"})
    assert r.json()["detail"]["code"] == "adults_only"


def test_timeline_roundtrip_stale_import_and_v6_cast(client, db, drain):
    enable(db)
    f = db.get(FeatureFlag, "characters") or FeatureFlag(key="characters")
    f.enabled, f.value = True, {}
    db.add(f)
    db.commit()
    h, _ = signup(client)
    fund(client, db, h)
    from .test_characters import locked_character

    ch = locked_character(client, db, h, drain)
    p = create(client, h, cast=[{"character_id": ch["id"], "alias": "mert", "lock_mode": "strict"}])
    assert p["cast"][0]["lock_mode"] == "strict"
    out = plan(client, h, p["id"], 1, "INT. EV - GECE\nMERT\nBen buradayım.\nAYŞE\nBiliyorum.\n")
    chars = {c["key"]: c for c in out["storyboard"]["characters"]}
    assert chars["mert"].get("cast_member_id") and chars["ayse"].get("cast_member_id") is None  # V6 cast snapshot
    est = client.post(f"/studio/projects/{p['episodes'][0]['studio_project_id']}/estimate", headers=h,
                      json={}).json()
    assert est["shots"][0]["lock_mode"] == "strict" and "CHARACTER_REFERENCE" in est["shots"][0]["capabilities"]
    tl = client.get(f"/productions/{p['id']}/episodes/1/timeline", headers=h).json()
    assert {"video", "dialogue", "subtitles", "camera", "characters", "scenes", "audio"} <= set(tl["tracks"])
    tl["storyboard"]["scenes"][0]["shots"][0]["camera"] = "slow push-in"
    r = client.post(f"/productions/{p['id']}/episodes/1/timeline", headers=h, json=tl)
    assert r.status_code == 201
    assert client.post(f"/productions/{p['id']}/episodes/1/timeline", headers=h, json=tl).json()["detail"][
        "code"] == "stale_timeline"
