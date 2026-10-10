"""V5 Phase D: constrained bandit routing, sticky A/B assignment, shadow -> ab -> active with evidence checks,
automatic rollback on regression, no extra charge from exploration, policy provenance on learning events."""

import random
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app import scheduler
from app.models import (
    ExperimentAssignment,
    FeatureFlag,
    GenerationJob,
    LearningEvent,
    LearningPolicyVersion,
    ModelPerformanceAggregate,
)
from app.services import policies, router

from .conftest import signup
from .test_creators import admin, uid
from .test_studio import enable as enable_studio
from .test_studio import plan, project

PROVIDERS = {"cheap": {"capabilities": ["TEXT_TO_VIDEO"], "usd_per_second": 0.02},
             "mid": {"capabilities": ["TEXT_TO_VIDEO"], "usd_per_second": 0.024},
             "lux": {"capabilities": ["TEXT_TO_VIDEO"], "usd_per_second": 0.20}}


def setup(db):
    enable_studio(db)
    for key, value in (("routing", {"providers": PROVIDERS}), ("model_disabled:mock_t2v", {}),
                       ("learning", {})):
        f = db.get(FeatureFlag, key) or FeatureFlag(key=key)
        f.enabled, f.value = True, value
        db.add(f)
    today = datetime.now(UTC).date()
    db.add_all([ModelPerformanceAggregate(day=today, provider="cheap", feature="studio", creative_mode="balanced",
                                          jobs=100, successes=60, cost_usd=1, ratings=0, quality={}, errors={}),
                ModelPerformanceAggregate(day=today, provider="mid", feature="studio", creative_mode="balanced",
                                          jobs=100, successes=97, cost_usd=1, ratings=0, quality={}, errors={}),
                ModelPerformanceAggregate(day=today, provider="lux", feature="studio", creative_mode="balanced",
                                          jobs=100, successes=99, cost_usd=1, ratings=0, quality={}, errors={})])
    db.commit()


def new_policy(client, ah, **cfg):
    r = client.post("/admin/learning/policies", headers=ah, json={"kind": "routing", "config": {
        "exploration_share": 0.0, "guard": {"min_samples": 3}, **cfg}})
    assert r.status_code == 201, r.text
    return r.json()


def move(client, ah, pid, to, rollout=None):
    return client.post(f"/admin/learning/policies/{pid}/transition", headers=ah,
                       json={"to": to, "rollout_pct": rollout, "note": "test step"})


def outcomes(db, version, variant, n, success_rate, mode="ab", provider="mid"):
    for i in range(n):
        db.add(LearningEvent(kind="job_outcome", job_id=uuid.uuid4(), feature="studio", provider=provider,
                             success=i < round(n * success_rate), status="completed", latency_s=10.0,
                             actual_cost_usd=0.1, consent={"technical_improvement": True},
                             provenance={"routing": {"policy_version": version, "variant": variant, "mode": mode,
                                                     "candidate_provider": "mid"}},
                             retention_until=datetime.now(UTC) + timedelta(days=30)))
    db.commit()


def test_bandit_is_constrained_and_never_raises_price(client, db):
    setup(db)
    route = router.choose(db, {"TEXT_TO_VIDEO"})
    assert route["provider"] == "cheap"  # baseline: cheapest eligible
    cfg = policies.RoutingPolicyConfig(exploration_share=0.0, max_cost_increase=0.25)
    # exploit: best posterior within the cost budget -> "mid" (lux is 10x the price: never considered)
    assert policies.bandit_choice(db, route, cfg, random.Random(1))["provider"] == "mid"
    explore = policies.RoutingPolicyConfig(exploration_share=0.5, max_cost_increase=0.25)
    picks = {policies.bandit_choice(db, route, explore, random.Random(s))["provider"] for s in range(200)}
    assert picks <= {"cheap", "mid"} and "lux" not in picks


def test_lifecycle_shadow_ab_active_with_evidence_and_sticky_assignment(client, db):
    setup(db)
    ah = admin(client, db)
    p = new_policy(client, ah)
    assert move(client, ah, p["id"], "ab", 10).json()["detail"]["code"] == "bad_transition"  # draft -> ab
    assert move(client, ah, p["id"], "shadow").json()["status"] == "shadow"
    other = new_policy(client, ah)
    assert move(client, ah, other["id"], "shadow").json()["detail"]["code"] == "policy_conflict"
    # shadow: decisions logged, baseline used
    h, _ = signup(client)
    pid = project(client, h)
    plan(client, h, pid)
    est = client.post(f"/studio/projects/{pid}/estimate", headers=h, json={}).json()
    assert est["provider"] == "cheap" and est["routing_policy"]["mode"] == "shadow"
    assert est["routing_policy"]["candidate_provider"] == "mid"
    assert move(client, ah, p["id"], "ab", 20).json()["detail"]["code"] == "insufficient_evidence"
    outcomes(db, p["version"], "baseline", 3, 1.0, mode="shadow")
    assert move(client, ah, p["id"], "ab", 80).json()["detail"]["code"] == "bad_rollout"  # A/B capped at 50 %
    assert move(client, ah, p["id"], "ab", 50).json()["status"] == "ab"
    # sticky server-side assignment, roughly the rollout share, credits identical in both arms
    variants, credits_seen = [], set()
    from app.services import ratelimit

    for _ in range(40):
        ratelimit.reset_local()  # 40 sign-ups from one test client would hit the per-IP auth limit
        hu, _ = signup(client)
        pj = project(client, hu)
        plan(client, hu, pj)
        e1 = client.post(f"/studio/projects/{pj}/estimate", headers=hu, json={}).json()
        e2 = client.post(f"/studio/projects/{pj}/estimate", headers=hu, json={}).json()
        assert e1["routing_policy"]["variant"] == e2["routing_policy"]["variant"]
        assert e1["provider"] == ("mid" if e1["routing_policy"]["variant"] == "candidate" else "cheap")
        variants.append(e1["routing_policy"]["variant"])
        credits_seen.add(e1["credits"])
    assert 8 <= variants.count("candidate") <= 32 and credits_seen == {24}  # exploring never changes the price
    assert db.execute(select(ExperimentAssignment)).scalars().first().experiment_key == f"routing:v{p['version']}"
    # promotion needs non-inferior evidence
    assert move(client, ah, p["id"], "active").json()["detail"]["code"] == "insufficient_evidence"
    outcomes(db, p["version"], "baseline", 5, 0.8)
    outcomes(db, p["version"], "candidate", 5, 1.0)
    act = move(client, ah, p["id"], "active").json()
    assert act["status"] == "active" and act["rollout_pct"] == 100
    assert [x["to"] for x in act["history"]] == ["draft", "shadow", "ab", "active"]


def test_guard_rolls_back_regressions_automatically(client, db):
    setup(db)
    ah = admin(client, db)
    p = new_policy(client, ah)
    move(client, ah, p["id"], "shadow")
    outcomes(db, p["version"], "baseline", 3, 1.0, mode="shadow")
    move(client, ah, p["id"], "ab", 50)
    outcomes(db, p["version"], "baseline", 10, 0.95)
    outcomes(db, p["version"], "candidate", 10, 0.6)
    r = scheduler.run_task("policy_guard")
    # fewer successes at the same spend also means a higher cost per success: both regressions are reported
    assert r.status == "succeeded" and set(r.result[f"routing:v{p['version']}"]["regressions"]) == {
        "success_rate", "cost_per_success"}
    db.expire_all()
    pol = db.get(LearningPolicyVersion, uuid.UUID(p["id"]))
    assert pol.status == "rolled_back" and pol.rollout_pct == 0 and pol.history[-1]["by"] == "guard"
    # after rollback everyone is back on the baseline route
    h, _ = signup(client)
    pid = project(client, h)
    plan(client, h, pid)
    est = client.post(f"/studio/projects/{pid}/estimate", headers=h, json={}).json()
    assert est["provider"] == "cheap" and est["routing_policy"] is None


def test_render_records_policy_provenance(client, db, storage):
    setup(db)
    ah = admin(client, db)
    p = new_policy(client, ah)
    move(client, ah, p["id"], "shadow")
    h, _ = signup(client)
    from app.models import LedgerReason
    from app.services import credits

    credits.apply(db, uid(client, h), 100, LedgerReason.purchase, f"p:{uuid.uuid4()}")
    db.commit()
    pid = project(client, h)
    plan(client, h, pid)
    r = client.post(f"/studio/projects/{pid}/render", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                    json={"confirmed_credits": 24})
    assert r.status_code == 202, r.text
    job = db.execute(select(GenerationJob)).scalars().first()
    assert job.spec["routing"]["policy_version"] == p["version"] and job.spec["routing"]["mode"] == "shadow"
    client.post(f"/generations/{job.id}/cancel", headers=h)
    db.expire_all()
    ev = db.execute(select(LearningEvent).where(LearningEvent.job_id == job.id)).scalar_one()
    assert ev.provenance["routing"]["candidate_provider"] == "mid"
