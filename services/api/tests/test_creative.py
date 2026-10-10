"""V5 Phase C acceptance: original vs optimized prompts, creative modes, candidate scoring, controlled diversity over
100 repeats, similarity audits (own history + public catalog only, template reuse exempt), variations, popularity
never feeding original plans, diversity dashboard."""

import json
import uuid
from collections import Counter

from sqlalchemy import select

from app.models import FeatureFlag, SimilarityAudit, StudioProject, User
from app.services import creative, studio

from .conftest import signup
from .test_creators import admin
from .test_studio import enable, project

BRIEF = 'A street dancer wakes up at dawn. She runs across a bridge. She dances "on the rooftop" at sunrise.'


def plan(client, h, pid, brief=BRIEF, **kw):
    r = client.post(f"/studio/projects/{pid}/storyboard", headers=h,
                    json={"brief": brief, "target_duration_s": 12, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def shots(sb):
    return [s for sc in sb["scenes"] for s in sc["shots"]]


def test_original_prompt_kept_optimized_derived_and_versioned(client, db):
    enable(db)
    h, _ = signup(client)
    pid = project(client, h)
    out = plan(client, h, pid)
    sb, c = out["storyboard"], out["creative"]
    assert sb["creative_mode"] == "balanced" and sb["prompt_strategy"] == creative.STRATEGY
    for s in shots(sb):
        assert s["optimized_prompt"].startswith(s["prompt"].rstrip(". ")) and "Lighting:" in s["optimized_prompt"]
    assert c["intent"]["required_phrases"] == ["on the rooftop"] and c["intent"]["genre"] == "dance"
    assert len(c["candidates"]) == 2 and all("total" in x["scores"] for x in c["candidates"])
    # faithful adds nothing
    f = plan(client, h, project(client, h), creative_mode="faithful")
    assert all(s["optimized_prompt"] == s["prompt"] for s in shots(f["storyboard"]))
    assert len(f["creative"]["candidates"]) == 1
    # editing the original re-derives the optimized prompt (never stale, never overwriting the original)
    sb2 = json.loads(json.dumps(sb))
    sb2["scenes"][0]["shots"][0]["prompt"] = "A dancer stretches on a quiet street"
    sb2["scenes"][0]["shots"][0]["optimized_prompt"] = "tampered"
    client.post(f"/studio/projects/{pid}/versions", headers=h, json={"storyboard": sb2})
    cur = client.get(f"/studio/projects/{pid}", headers=h).json()["current_version"]["storyboard"]
    first = shots(cur)[0]
    assert first["prompt"] == "A dancer stretches on a quiet street"
    assert first["optimized_prompt"].startswith("A dancer stretches on a quiet street")
    # legacy storyboards (no creative mode) keep their exact render hash
    legacy = {k: v for k, v in sb.items() if k not in ("creative_mode", "composition", "seed", "prompt_strategy")}
    for s in shots(legacy):
        s.pop("optimized_prompt")
    lv = studio.Storyboard.model_validate(legacy)
    inp = studio._shot_inputs(db, lv, lv.shots()[0], "")
    assert "optimized_prompt" not in inp and "creative" not in inp


def test_same_prompt_100_times_controlled_diversity_intent_preserved(client, db):
    enable(db)
    h, _ = signup(client)
    user = db.get(User, uuid.UUID(client.get("/me", headers=h).json()["id"]))
    comps, cams = Counter(), Counter()
    for i in range(100):
        p = StudioProject(user_id=user.id, title=f"r{i}", status="draft")
        db.add(p)
        db.flush()
        v = studio.plan_storyboard(db, user, p, studio.Brief(brief=BRIEF, target_duration_s=12,
                                                             creative_mode="experimental" if i % 2 else "balanced"))
        sb = v.storyboard
        comps[sb["composition"]] += 1
        cams[tuple(s["camera"] for s in shots(sb))] += 1
        # intent: same sentences as prompts (no invented story), required phrase kept, duration on target
        assert [s["prompt"] for s in shots(sb)] == ["A street dancer wakes up at dawn.", "She runs across a bridge.",
                                                    'She dances "on the rooftop" at sunrise.']
        assert sum(s["duration_s"] for s in shots(sb)) == 12
    db.commit()
    assert len(comps) >= 4 and max(comps.values()) <= 50, comps  # no single default pattern dominates
    assert len(cams) >= 4 and creative.entropy(comps) >= 1.5


def test_similarity_own_history_and_catalog_only_template_reuse_exempt(client, db):
    enable(db)
    h, _ = signup(client)
    plan(client, h, project(client, h))
    again = plan(client, h, project(client, h))  # same user, same brief, other project
    assert again["creative"]["near_duplicate"] is True and again["creative"]["suggestion"]
    assert again["creative"]["similarity"]["matched"] == "own_project"  # warned, never rejected (201)
    # another user's identical plan is never compared (no cross-user creative memory)
    h2, _ = signup(client)
    other = plan(client, h2, project(client, h2))
    assert other["creative"]["similarity"]["decision"] == "ok"
    # the public licensed catalog is a lawful comparison source
    from .conftest import make_template

    t = make_template(db)
    t.title, t.description = "Rooftop sunrise dance", BRIEF
    db.commit()
    h3, _ = signup(client)
    cat = plan(client, h3, project(client, h3))
    assert cat["creative"]["similarity"]["matched"] == "template" and cat["creative"]["near_duplicate"]
    audits = db.execute(select(SimilarityAudit)).scalars().all()
    assert {a.method for a in audits} == {"char3_jaccard"} and all(a.threshold == 0.78 for a in audits)


def test_variations_distinct_and_current_unchanged(client, db):
    enable(db)
    h, _ = signup(client)
    pid = project(client, h)
    base = plan(client, h, pid)
    r = client.post(f"/studio/projects/{pid}/variations", headers=h, json={"count": 3, "creative_mode": "experimental"})
    items = r.json()["items"]
    assert r.status_code == 201 and len(items) == 3 and r.json()["current_version_id"] == base["version_id"]
    comps = {i["creative"]["composition"] for i in items} | {base["creative"]["composition"]}
    seeds = {i["creative"]["seed"] for i in items}
    assert len(comps) == 4 and len(seeds) == 3  # distinct strategies, not only distinct seeds
    for i in items:
        assert [s["prompt"] for s in shots(i["storyboard"])] == [s["prompt"] for s in shots(base["storyboard"])]
    versions = client.get(f"/studio/projects/{pid}/versions", headers=h).json()["items"]
    assert [v["source"] for v in versions].count("variation") == 3


def test_popular_templates_never_feed_original_plans(client, db, storage):
    from .conftest import make_template

    enable(db)
    t = make_template(db)
    t.title, t.description = "VIRAL CHICKEN DANCE", "the most used template ever"
    db.commit()
    h, _ = signup(client)
    out = plan(client, h, project(client, h), brief="A chef cooks pasta. He plates it. He smiles at the camera.")
    blob = json.dumps(out["storyboard"]).lower()
    assert "chicken" not in blob and "viral" not in blob


def test_diversity_dashboard_and_worker_uses_optimized_prompt(client, db):
    enable(db)
    db.add(FeatureFlag(key="learning", enabled=True, value={}))
    db.commit()
    ah = admin(client, db)
    h, _ = signup(client)
    for _ in range(3):
        plan(client, h, project(client, h))
    o = client.get("/admin/learning/overview", headers=ah).json()
    d = o["diversity"]
    assert d["by_genre"]["dance"]["plans"] == 3 and d["near_duplicate_rate"] is not None
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "worker"))
    from worker.studio.shots import compose_prompt

    spec = {"inputs": {"prompt": "orig", "optimized_prompt": "orig. Lighting: x.", "style": "", "characters": []}}
    assert compose_prompt(spec) == "orig. Lighting: x."
