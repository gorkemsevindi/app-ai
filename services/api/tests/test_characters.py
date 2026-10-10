"""V6 Phase B: Character Creator, multi-angle identity, persistent character ID, character lock and @character
casting — end to end with the real worker (mock image + mock video providers, real QC code, real ffmpeg)."""

import uuid

from sqlalchemy import select

from app.models import (
    CharacterAsset,
    CharacterQualityReport,
    FeatureFlag,
    GenerationJob,
    JobKind,
    JobStatus,
    LedgerReason,
)
from app.services import credits

from .conftest import WORKER_H, signup
from .test_generation import MP4_BYTES
from .test_studio import enable as enable_studio
from .test_studio import project

RIGHTS = {"original_creation": True, "no_real_person_likeness": True, "no_third_party_ip": True,
          "adult_appearance": True, "accept_terms": True}
DESC = "An original street dancer with short copper hair, freckles and a green bomber jacket"
SPEC = {"display_name": "Mert", "aesthetic": "stylized", "age_appearance": "young_adult",
        "appearance": {"face": "oval face, freckles", "hair": "short copper hair", "eyes": "hazel"},
        "wardrobe_baseline": "green bomber jacket", "languages": ["tr", "en"],
        "personality": {"archetype": "charming dancer", "traits": ["playful"], "speech_style": "quick"},
        "variants": [{"key": "winter", "description": "wool coat and scarf"}]}


def enable_chars(db, **over):
    f = db.get(FeatureFlag, "characters") or FeatureFlag(key="characters")
    f.enabled, f.value = True, over
    db.add(f)
    db.commit()


def uid(client, h):
    return uuid.UUID(client.get("/me", headers=h).json()["id"])


def fund(client, db, h, n=1000):
    credits.apply(db, uid(client, h), n, LedgerReason.purchase, f"p:{uuid.uuid4()}", bucket="purchased")
    db.commit()


def bal(client, h):
    return client.get("/credits", headers=h).json()["balance"]


def create(client, h, handle="mert", **over):
    r = client.post("/characters", headers=h, json={"handle": handle, "description": DESC, "spec": SPEC,
                                                    "rights": RIGHTS, **over})
    return r


def pay(client, h, path, body, key=None):
    return client.post(path, headers={**h, "Idempotency-Key": key or uuid.uuid4().hex}, json=body)


def locked_character(client, db, h, drain, handle="mert", spec=None):
    """Full creator flow: create -> previews -> master -> multi-view build -> review all -> lock."""
    r = create(client, h, handle, **({"spec": spec} if spec else {}))
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    q = client.get(f"/characters/{cid}/quote?kind=preview&count=2", headers=h).json()
    assert pay(client, h, f"/characters/{cid}/preview", {"count": 2, "confirmed_credits": q["credits"]}
               ).status_code == 202
    drain()
    ch = client.get(f"/characters/{cid}", headers=h).json()
    master = next(a for a in ch["assets"] if a["status"] == "ready")
    assert client.post(f"/characters/{cid}/approve-master", headers=h, json={"asset_id": master["id"]}
                       ).status_code == 200
    q = client.get(f"/characters/{cid}/quote?kind=build", headers=h).json()
    assert pay(client, h, f"/characters/{cid}/identity-build", {"confirmed_credits": q["credits"]}).status_code == 202
    drain()
    for a in client.get(f"/characters/{cid}", headers=h).json()["assets"]:
        if a["kind"] == "view":
            assert client.post(f"/characters/{cid}/assets/{a['id']}/review", headers=h,
                               json={"decision": "approved"}).status_code == 200
    r = client.post(f"/characters/{cid}/lock", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_creation_rights_screens_and_handles(client, db):
    h, _ = signup(client)
    assert create(client, h).status_code == 403  # flag off
    enable_chars(db, blocked_terms=["sherlock"])
    r = create(client, h, rights={**RIGHTS, "no_third_party_ip": False})
    assert r.json()["detail"]["code"] == "rights_declaration_required"
    for desc, code in [("A cheerful 12 year-old schoolgirl dancer", "minor_not_allowed"),
                       ("A dancer who looks like a famous pop star", "impersonation_not_allowed"),
                       ("Sherlock as a modern detective in Istanbul", "rights_conflict")]:
        assert create(client, h, description=desc).json()["detail"]["code"] == code, desc
    assert create(client, h, spec={**SPEC, "age_appearance": "child"}).json()["detail"]["code"] == \
        "bad_character_spec"
    r = create(client, h)
    assert r.status_code == 201, r.text
    ch = r.json()
    assert ch["handle"] == "@mert" and ch["status"] == "draft" and ch["origin"] == "synthetic"
    assert ch["current_version"]["version"] == "1.0" and ch["current_version"]["original_prompt"] == DESC
    assert ch["current_version"]["spec"]["schema"] == "cs1" and ch["locked"] is False
    assert create(client, h).json()["detail"]["code"] == "handle_taken"
    h2, _ = signup(client)
    assert create(client, h2).status_code == 201  # handles are unique per creator only
    # tenant isolation: a private draft is invisible to others
    assert client.get(f"/characters/{ch['id']}", headers=h2).status_code == 404
    assert client.get(f"/characters/{ch['id']}/package", headers=h2).status_code == 404
    assert create(client, h, "self", origin="real_person").status_code == 404  # needs own ready profile


def test_identity_build_review_repair_lock_and_versions(client, db, drain):
    enable_chars(db)
    h, _ = signup(client)
    fund(client, db, h)
    start = bal(client, h)
    cid = create(client, h).json()["id"]
    q = client.get(f"/characters/{cid}/quote?kind=preview&count=3", headers=h).json()
    assert q["credits"] == 6 and q["provider"] == "mock_image"
    assert pay(client, h, f"/characters/{cid}/preview", {"count": 3}).json()["detail"]["code"] == \
        "confirmation_required"
    key = uuid.uuid4().hex
    r = pay(client, h, f"/characters/{cid}/preview", {"count": 3, "confirmed_credits": 6}, key)
    assert r.status_code == 202 and len(r.json()["jobs"]) == 3
    assert pay(client, h, f"/characters/{cid}/preview", {"count": 3, "confirmed_credits": 6}, key).json()["replay"]
    assert bal(client, h) == start - 6
    assert drain() == 3
    ch = client.get(f"/characters/{cid}", headers=h).json()
    previews = [a for a in ch["assets"] if a["kind"] == "seed_preview"]
    assert len(previews) == 3 and all(a["status"] == "ready" and a["sha256"] and a["qc"]["dhash"] for a in previews)
    assert len({a["sha256"] for a in previews}) == 3  # distinct seeds -> distinct candidates
    assert pay(client, h, f"/characters/{cid}/identity-build", {"confirmed_credits": 34}).json()["detail"]["code"] \
        == "master_required"
    m = client.post(f"/characters/{cid}/approve-master", headers=h, json={"asset_id": previews[1]["id"]}).json()
    assert m["master_asset_id"] == previews[1]["id"]
    assert client.post(f"/characters/{cid}/approve-master", headers=h, json={"asset_id": previews[0]["id"]}
                       ).json()["detail"]["code"] == "identity_approved"
    q = client.get(f"/characters/{cid}/quote?kind=build", headers=h).json()
    assert q["images"] == 17 and q["credits"] == 34
    assert pay(client, h, f"/characters/{cid}/identity-build", {"confirmed_credits": 34}).status_code == 202
    assert drain() == 17
    ch = client.get(f"/characters/{cid}", headers=h).json()
    assert ch["current_version"]["status"] == "built"
    views = {a["view"]: a for a in ch["assets"] if a["kind"] == "view"}
    assert len(views) == 17 and views["rear"]["face_meaningful"] is False and views["closeup"]["face_meaningful"]
    rep = ch["report"]
    assert rep["verdict"] == "warn" and rep["method"] == "proxy" and rep["verified"] is False
    assert any("not meaningful" in v.get("note", "") for v in rep["metrics"]["views"] if v["view"] == "rear")
    assert client.post(f"/characters/{cid}/lock", headers=h).json()["detail"]["code"] == "creator_review_required"
    # creator rejects one view: repair regenerates only that one
    client.post(f"/characters/{cid}/assets/{views['yaw45_left']['id']}/review", headers=h,
                json={"decision": "rejected"})
    assert client.post(f"/characters/{cid}/lock", headers=h).json()["detail"]["code"] == \
        "identity_validation_failed"
    q = client.get(f"/characters/{cid}/quote?kind=repair", headers=h).json()
    assert q["images"] == 1
    r = pay(client, h, f"/characters/{cid}/repair", {"confirmed_credits": 2})
    assert r.status_code == 202 and len(r.json()["jobs"]) == 1
    assert drain() == 1
    ch = client.get(f"/characters/{cid}", headers=h).json()
    live = [a for a in ch["assets"] if a["kind"] == "view"]
    assert len(live) == 17 and next(a for a in live if a["view"] == "yaw45_left")["id"] != views["yaw45_left"]["id"]
    for a in live:
        client.post(f"/characters/{cid}/assets/{a['id']}/review", headers=h, json={"decision": "approved"})
    locked = client.post(f"/characters/{cid}/lock", headers=h).json()
    assert locked["status"] == "private" and locked["locked"] and locked["locked_version"]["version"] == "1.0"
    pkg = client.get(f"/characters/{cid}/package", headers=h).json()
    man = pkg["manifest"]
    assert man["schema"] == "ipm1" and man["character_uuid"] == cid and len(pkg["checksum"]) == 64
    assert len(man["checksums"]) == 18 and all(man["checksums"].values())  # master + 17 approved views
    assert man["embeddings"]["stored"] is False and man["canonical"]["portrait"]["asset_id"] == previews[1]["id"]
    assert {x["view"] for x in man["expression_sheet"]} >= {"expr_smile", "mouth_aa"}
    assert man["canonical"]["portrait"]["provenance"]["seed"] is not None
    assert client.post(f"/characters/{cid}/assets/{live[0]['id']}/review", headers=h,
                       json={"decision": "rejected"}).json()["detail"]["code"] == "identity_locked"
    # versions: minor = cosmetic (inherits identity package), immutable traits need a major version
    r = client.post(f"/characters/{cid}/versions", headers=h,
                    json={"change": "minor", "spec": {"appearance": {"face": "square jaw"}}})
    assert r.json()["detail"]["code"] == "immutable_trait_change"
    r = client.post(f"/characters/{cid}/versions", headers=h,
                    json={"change": "minor", "spec": {"wardrobe_baseline": "red tracksuit"}}).json()
    assert r["locked_version"]["version"] == "1.1" and r["current_version"]["status"] == "locked"
    r = client.post(f"/characters/{cid}/versions", headers=h,
                    json={"change": "major", "spec": {"appearance": {"face": "square jaw"}}}).json()
    assert r["current_version"]["version"] == "2.0" and r["current_version"]["status"] == "draft"
    assert r["locked_version"]["version"] == "1.1"  # still usable until 2.0 is locked
    jobs = db.execute(select(GenerationJob).where(GenerationJob.kind == JobKind.character_asset)).scalars().all()
    assert len(jobs) == 21 and {j.billing_state for j in jobs} == {"settled"}
    assert bal(client, h) == start - 6 - 34 - 2
    assert credits.reconcile_user(db, uid(client, h))["consistent"]


def test_failed_image_is_refunded_and_voice_rules(client, db):
    enable_chars(db, tts_voices={"stock_tts": ["tr_female_1"]})
    h, _ = signup(client)
    fund(client, db, h, 50)
    cid = create(client, h).json()["id"]
    pay(client, h, f"/characters/{cid}/preview", {"count": 1, "confirmed_credits": 2})
    assert bal(client, h) == 48 + 30  # signup bonus 30
    p = client.post("/internal/worker/claim", headers=WORKER_H, json={"worker_id": "w1", "models": ["mock_image"]})
    p = p.json()
    assert p["kind"] == "character_asset" and p["upload"]["image"]["key"].startswith(f"characters/{cid}/")
    r = client.post(f"/internal/worker/jobs/{p['job_id']}/fail", headers=WORKER_H,
                    json={"worker_id": "w1", "attempt": p["attempt"], "error_code": "provider_blocked",
                          "retryable": False})
    assert r.json()["status"] == "failed"
    assert bal(client, h) == 80  # refunded
    a = db.execute(select(CharacterAsset)).scalar_one()
    assert a.status == "failed"
    # voice: licensed catalogue only; voice cloning stays disabled
    v = client.post(f"/characters/{cid}/voice", headers=h,
                    json={"provider": "stock_tts", "voice_id": "tr_female_1", "languages": ["tr"]})
    assert v.status_code == 201 and v.json()["synthesis"].startswith("not enabled")
    assert client.post(f"/characters/{cid}/voice", headers=h, json={"provider": "stock_tts", "voice_id": "x"}
                       ).json()["detail"]["code"] == "voice_not_licensed"
    assert client.post(f"/characters/{cid}/voice", headers=h,
                       json={"kind": "consented_likeness", "provider": "clone", "voice_id": "me"}
                       ).json()["detail"]["code"] == "voice_cloning_disabled"


def test_cast_mentions_strict_lock_and_two_scenes_e2e(client, db, drain):
    enable_chars(db)
    enable_studio(db)
    h, _ = signup(client)
    fund(client, db, h, 2000)
    ch = locked_character(client, db, h, drain)
    pid = project(client, h)
    r = client.post(f"/studio/projects/{pid}/resolve-script", headers=h, json={"text": "@mert dances. @ghost waits."})
    st = {m["mention"]: m["status"] for m in r.json()["mentions"]}
    assert st == {"@mert": "needs_cast", "@ghost": "not_found"} and r.json()["all_bound"] is False
    brief = "@mert wakes up at dawn in Istanbul. @mert dances on a rooftop as the sun rises."
    r = client.post(f"/studio/projects/{pid}/storyboard", headers=h, json={"brief": brief, "target_duration_s": 12})
    assert r.json()["detail"]["code"] == "cast_resolution_required"  # never resolved silently
    m = client.post(f"/studio/projects/{pid}/cast", headers=h, json={"mention": "@mert", "lock_mode": "strict"})
    assert m.status_code == 201, m.text
    m = m.json()
    assert m["identity_version"]["version"] == "1.0" and m["lock_mode"] == "strict" and m["own"]
    assert client.post(f"/studio/projects/{pid}/cast", headers=h, json={"mention": "@mert"}
                       ).json()["detail"]["code"] == "already_cast"
    r = client.post(f"/studio/projects/{pid}/storyboard", headers=h, json={"brief": brief, "target_duration_s": 12})
    assert r.status_code == 201, r.text
    sb, est = r.json()["storyboard"], r.json()["estimate"]
    assert sb["characters"] == [{"key": "mert", "name": "Mert", "description": sb["characters"][0]["description"],
                                 "character_id": None, "cast_member_id": m["id"]}]
    shots = [s for sc in sb["scenes"] for s in sc["shots"]]
    assert len(shots) == 2 and all(s["characters"] == ["mert"] for s in shots)
    assert all(row["lock_mode"] == "strict" and "CHARACTER_REFERENCE" in row["capabilities"] for row in est["shots"])
    base = 2 * shots[0]["duration_s"]
    assert est["shots"][0]["credits"] == int(-(-base * 1.5 // 1))  # strong/strict identity multiplier
    assert any("measured per shot" in x for x in est["limitations"])
    before = bal(client, h)
    r = client.post(f"/studio/projects/{pid}/render", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                    json={"confirmed_credits": est["credits"]})
    assert r.status_code == 202, r.text
    assert drain() == 3  # two scenes + assembly
    p = client.get(f"/studio/projects/{pid}", headers=h).json()
    assert p["status"] == "ready" and [s["status"] for s in p["shots"]] == ["ready", "ready"]
    rep = client.get(f"/studio/projects/{pid}/identity-report", headers=h).json()["items"]
    assert len(rep) == 2 and all(x["verdict"] == "pass" and x["method"] == "proxy" for x in rep)
    assert all(x["label"] == "measured (proxy)" and x["lock_mode"] == "strict" for x in rep)  # never "verified"
    assert bal(client, h) == before - est["credits"]
    # the creator moves on (minor + major versions): this project's cast snapshot and renders don't change
    client.post(f"/characters/{ch['id']}/versions", headers=h, json={"change": "minor",
                                                                     "spec": {"wardrobe_baseline": "red"}})
    e = client.post(f"/studio/projects/{pid}/estimate", headers=h, json={}).json()
    assert e["new_shots"] == 0 and e["credits"] == 0
    cast = client.get(f"/studio/projects/{pid}/cast", headers=h).json()["items"]
    assert cast[0]["identity_version"]["version"] == "1.0"
    shot_jobs = db.execute(select(GenerationJob).where(GenerationJob.kind == JobKind.studio_shot)).scalars().all()
    assert all(j.spec["inputs"]["characters"][0]["cast"]["package_checksum"] ==
               m["identity_version"]["package_checksum"] for j in shot_jobs)


def _strict_project(client, db, drain, lock_mode="strict"):
    enable_chars(db)
    enable_studio(db)
    h, _ = signup(client)
    fund(client, db, h, 2000)
    locked_character(client, db, h, drain)
    pid = project(client, h)
    client.post(f"/studio/projects/{pid}/cast", headers=h, json={"mention": "@mert", "lock_mode": lock_mode})
    r = client.post(f"/studio/projects/{pid}/storyboard", headers=h,
                    json={"brief": "@mert dances on a rooftop at sunrise.", "target_duration_s": 4})
    assert r.status_code == 201, r.text
    est = r.json()["estimate"]
    assert client.post(f"/studio/projects/{pid}/render", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                       json={"confirmed_credits": est["credits"]}).status_code == 202
    return h, pid, est


def _finish_shot(client, storage, score):
    p = client.post("/internal/worker/claim", headers=WORKER_H, json={"worker_id": "w1", "models": ["mock_t2v"]})
    p = p.json()
    assert p["kind"] == "studio_shot" and len(p["reference_images"]["mert"]) == 3
    storage.put(p["upload"]["video"]["key"], MP4_BYTES, "video/mp4")
    return client.post(f"/internal/worker/jobs/{p['job_id']}/complete", headers=WORKER_H, json={
        "worker_id": "w1", "attempt": p["attempt"], "output": {}, "moderation": {"scores": {}},
        "metrics": {"qa": {"identity": {"mert": {"method": "proxy", "score": score, "frames": 8}}}}}).json()


def test_strict_lock_bounded_retries_refunds_and_explicit_failure(client, db, storage, drain):
    h, pid, est = _strict_project(client, db, drain)
    before = bal(client, h)  # after the render reserve
    for _ in range(3):  # first attempt + retry budget 2
        assert _finish_shot(client, storage, 0.1)["status"] == "failed"
    assert client.post("/internal/worker/claim", headers=WORKER_H,
                       json={"worker_id": "w1", "models": ["mock_t2v"]}).status_code == 204  # no 4th attempt
    jobs = db.execute(select(GenerationJob).where(GenerationJob.kind == JobKind.studio_shot)
                      .order_by(GenerationJob.created_at)).scalars().all()
    assert [j.spec["identity_gate"]["attempt"] for j in jobs] == [0, 1, 2]
    assert all(j.status == JobStatus.failed and j.error_code == "identity_check_failed" and j.refunded for j in jobs)
    assert bal(client, h) == before + est["credits"]  # every failed attempt refunded: never charged
    p = client.get(f"/studio/projects/{pid}", headers=h).json()
    assert p["status"] == "failed" and p["shots"][0]["status"] == "identity_failed"
    rep = client.get(f"/studio/projects/{pid}/identity-report", headers=h).json()["items"]
    assert [x["verdict"] for x in rep] == ["fail"] * 3 and not any(x["label"] == "measured" for x in rep)


def test_strict_retry_then_pass_and_standard_mode_reports_only(client, db, storage, drain):
    h, pid, est = _strict_project(client, db, drain)
    assert _finish_shot(client, storage, None)["status"] == "failed"  # unmeasurable -> strict fails
    assert _finish_shot(client, storage, 0.93)["status"] == "completed"
    reps = db.execute(select(CharacterQualityReport).where(CharacterQualityReport.scope == "shot")
                      .order_by(CharacterQualityReport.created_at)).scalars().all()
    assert [r.verdict for r in reps] == ["insufficient", "pass"]
    settled = db.execute(select(GenerationJob).where(GenerationJob.kind == JobKind.studio_shot,
                                                     GenerationJob.status == JobStatus.completed)).scalar_one()
    assert settled.billing_state == "settled" and settled.credit_cost == est["shots"][0]["credits"]


def test_standard_lock_never_blocks_delivery(client, db, storage, drain):
    h, pid, est = _strict_project(client, db, drain, lock_mode="standard")
    assert _finish_shot(client, storage, 0.05)["status"] == "completed"  # measured + reported, not gated
    rep = client.get(f"/studio/projects/{pid}/identity-report", headers=h).json()["items"]
    assert rep[0]["verdict"] == "fail" and rep[0]["lock_mode"] == "standard"
    assert est["shots"][0]["credits"] == 2 * 4  # no identity multiplier in standard mode
