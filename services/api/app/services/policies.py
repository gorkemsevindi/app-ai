"""Adaptive learning policies (Master Spec V5, Phase D): constrained bandit routing, experiments, staged rollout,
guarded rollback.

Constraints that always hold:
- Exploration is *technical* only (which provider renders) and only among providers the rule-based router already
  considers eligible (capability, kill switch, price, health, quality floor). A candidate costing more than
  `max_cost_increase` over the baseline choice is excluded. Credits charged never depend on the provider, so
  exploring can't raise what the user pays (V5 §4).
- Lifecycle: draft -> shadow (decisions logged, baseline used) -> ab (sticky server-side assignment for
  `rollout_pct` of users) -> active. Every promotion is a human action that must pass an evidence check; nothing
  self-deploys (V5 §9). Rollback is automatic when the guard sees a regression (success rate, cost per success,
  latency, rating) with enough samples, or manual at any time.
- Decisions are deterministic per (user, policy, capability set, day) so the price/estimate and the render agree."""

from __future__ import annotations

import hashlib
import random
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import ApiError, not_found
from ..models import (
    ExperimentAssignment,
    LearningEvent,
    LearningPolicyVersion,
    ModelPerformanceAggregate,
    User,
)

LIVE = ("shadow", "ab", "active")
TRANSITIONS = {"draft": {"shadow", "retired"}, "shadow": {"ab", "retired", "rolled_back"},
               "ab": {"active", "rolled_back", "retired"}, "active": {"rolled_back", "retired"},
               "rolled_back": {"retired"}, "retired": set()}


class Guard(BaseModel):
    min_samples: int = Field(default=30, ge=1)
    max_success_drop: float = Field(default=0.05, ge=0, le=1)
    max_cost_increase: float = Field(default=0.2, ge=0)
    max_latency_increase: float = Field(default=0.3, ge=0)
    max_rating_drop: float = Field(default=0.3, ge=0)
    window_days: int = Field(default=14, ge=1, le=90)


class RoutingPolicyConfig(BaseModel):
    algorithm: str = Field(default="thompson", pattern=r"^thompson$")
    exploration_share: float = Field(default=0.1, ge=0, le=0.5)
    max_cost_increase: float = Field(default=0.25, ge=0, le=2)
    prior_window_days: int = Field(default=14, ge=1, le=90)
    guard: Guard = Field(default_factory=Guard)


def _now() -> datetime:
    return datetime.now(UTC)


def live_policy(db: Session, kind: str = "routing") -> LearningPolicyVersion | None:
    return db.execute(select(LearningPolicyVersion).where(LearningPolicyVersion.kind == kind,
                                                          LearningPolicyVersion.status.in_(LIVE))).scalars().first()


def create(db: Session, admin: User, kind: str, config: dict) -> LearningPolicyVersion:
    if kind != "routing":
        raise ApiError(422, "bad_policy", "only routing policies exist")
    try:
        cfg = RoutingPolicyConfig.model_validate(config)
    except ValidationError as e:
        raise ApiError(422, "bad_policy", "invalid policy",
                       {"errors": [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()[:10]]}) from e
    n = db.execute(select(func.coalesce(func.max(LearningPolicyVersion.version), 0))
                   .where(LearningPolicyVersion.kind == kind)).scalar_one()
    p = LearningPolicyVersion(kind=kind, version=n + 1, config=cfg.model_dump(), status="draft", created_by=admin.id,
                              history=[{"to": "draft", "by": str(admin.id), "at": _now().isoformat()}])
    db.add(p)
    db.flush()
    return p


# ---------------------------------------------------------------- bandit

def _posteriors(db: Session, providers: list[str], days: int) -> dict[str, tuple[int, int]]:
    since = _now().date() - timedelta(days=days)
    rows = db.execute(select(ModelPerformanceAggregate.provider, func.sum(ModelPerformanceAggregate.jobs),
                             func.sum(ModelPerformanceAggregate.successes))
                      .where(ModelPerformanceAggregate.day >= since,
                             ModelPerformanceAggregate.provider.in_(providers))
                      .group_by(ModelPerformanceAggregate.provider)).all()
    out = {p: (1, 1) for p in providers}  # Beta(1,1) prior = cold start
    for p, jobs, succ in rows:
        out[p] = (1 + int(succ or 0), 1 + int(jobs or 0) - int(succ or 0))
    return out


def bandit_choice(db: Session, route: dict, cfg: RoutingPolicyConfig, rng: random.Random) -> dict:
    eligible = [c for c in route["candidates"] if c["eligible"]]
    if not eligible or route.get("baseline_provider") is None:
        return {"provider": route.get("baseline_provider"), "explored": False, "reason": "no_candidates"}
    base = next(c for c in eligible if c["provider"] == route["baseline_provider"])
    cap = base["usd_per_second"] * (1 + cfg.max_cost_increase) + 1e-12
    allowed = [c for c in eligible if c["usd_per_second"] <= cap]  # budgeted: never much pricier than baseline
    post = _posteriors(db, [c["provider"] for c in allowed], cfg.prior_window_days)
    if rng.random() < cfg.exploration_share:
        samples = {p: rng.betavariate(a, b) for p, (a, b) in post.items()}
        best = max(samples, key=lambda p: (samples[p], p))
        return {"provider": best, "explored": True, "reason": "thompson_sample", "posteriors": post}
    means = {p: a / (a + b) for p, (a, b) in post.items()}
    best = max(means, key=lambda p: (round(means[p], 6), -next(c["usd_per_second"] for c in allowed
                                                                if c["provider"] == p)))
    return {"provider": best, "explored": False, "reason": "posterior_mean", "posteriors": post}


def _bucket(user_id: uuid.UUID, policy: LearningPolicyVersion) -> int:
    return int(hashlib.sha256(f"{policy.id}:{user_id}".encode()).hexdigest()[:8], 16) % 100


def assign(db: Session, user_id: uuid.UUID, policy: LearningPolicyVersion) -> str:
    key = f"{policy.kind}:v{policy.version}"
    a = db.execute(select(ExperimentAssignment).where(ExperimentAssignment.experiment_key == key,
                                                      ExperimentAssignment.user_id == user_id)).scalar_one_or_none()
    if a is not None:
        return a.variant
    variant = "candidate" if _bucket(user_id, policy) < policy.rollout_pct else "baseline"
    db.add(ExperimentAssignment(experiment_key=key, user_id=user_id, variant=variant, policy_version_id=policy.id))
    db.flush()
    return variant


def apply(db: Session, route: dict, user_id: uuid.UUID | None, needed: set[str]) -> dict:
    """Wrap a baseline route with the live policy's decision (if any)."""
    p = live_policy(db)
    route = {**route, "baseline_provider": route["provider"]}
    if p is None or user_id is None or route["provider"] is None:
        return route
    cfg = RoutingPolicyConfig.model_validate(p.config)
    seed = hashlib.sha256(f"{p.id}:{user_id}:{sorted(needed)}:{_now().date()}".encode()).hexdigest()
    choice = bandit_choice(db, route, cfg, random.Random(int(seed[:16], 16)))
    info = {"policy_version": p.version, "status": p.status, "candidate_provider": choice["provider"],
            "explored": choice["explored"]}
    if p.status == "shadow":
        return {**route, "policy": {**info, "variant": "baseline", "mode": "shadow"}}
    variant = "candidate" if p.status == "active" else assign(db, user_id, p)
    if variant == "candidate" and choice["provider"]:
        alt = [c["provider"] for c in route["candidates"] if c["eligible"] and c["provider"] != choice["provider"]]
        return {**route, "provider": choice["provider"], "fallback": alt[0] if alt else None,
                "reason": "policy", "policy": {**info, "variant": "candidate", "mode": p.status}}
    return {**route, "policy": {**info, "variant": "baseline", "mode": p.status}}


# ---------------------------------------------------------------- evaluation, guard, lifecycle

def evaluate(db: Session, p: LearningPolicyVersion) -> dict:
    g = RoutingPolicyConfig.model_validate(p.config).guard
    since = _now() - timedelta(days=g.window_days)
    evs = db.execute(select(LearningEvent).where(LearningEvent.created_at >= since,
                                                 LearningEvent.kind.in_(("job_outcome", "feedback")))).scalars().all()
    ratings = {e.job_id: e.rating for e in evs if e.kind == "feedback" and e.rating}
    arms: dict[str, list[LearningEvent]] = defaultdict(list)
    shadow_n = agree = 0
    for e in evs:
        r = (e.provenance or {}).get("routing") or {}
        if e.kind != "job_outcome" or r.get("policy_version") != p.version:
            continue
        if r.get("mode") == "shadow":
            shadow_n += 1
            agree += r.get("candidate_provider") == e.provider
        arms[r.get("variant", "baseline")].append(e)

    def stats(xs):
        n = len(xs)
        ok = [x for x in xs if x.success]
        lat = sorted(x.latency_s for x in ok if x.latency_s is not None)
        rs = [ratings[x.job_id] for x in xs if x.job_id in ratings]
        return {"n": n, "success_rate": round(len(ok) / n, 4) if n else None,
                "cost_per_success": round(sum(x.actual_cost_usd or 0 for x in xs) / len(ok), 6) if ok else None,
                "p50_latency_s": lat[len(lat) // 2] if lat else None,
                "rating_mean": round(sum(rs) / len(rs), 3) if rs else None}

    b, c = stats(arms["baseline"]), stats(arms["candidate"])
    regressions = []
    if c["n"] >= g.min_samples and b["n"] >= g.min_samples:
        if b["success_rate"] is not None and c["success_rate"] is not None and \
                b["success_rate"] - c["success_rate"] > g.max_success_drop:
            regressions.append("success_rate")
        if b["cost_per_success"] and c["cost_per_success"] and \
                c["cost_per_success"] > b["cost_per_success"] * (1 + g.max_cost_increase):
            regressions.append("cost_per_success")
        if b["p50_latency_s"] and c["p50_latency_s"] and \
                c["p50_latency_s"] > b["p50_latency_s"] * (1 + g.max_latency_increase):
            regressions.append("latency")
        if b["rating_mean"] is not None and c["rating_mean"] is not None and \
                b["rating_mean"] - c["rating_mean"] > g.max_rating_drop:
            regressions.append("rating")
    enough = c["n"] >= g.min_samples and b["n"] >= g.min_samples
    return {"baseline": b, "candidate": c, "shadow": {"n": shadow_n, "agreement": round(agree / shadow_n, 4)
                                                      if shadow_n else None},
            "enough_evidence": enough, "regressions": regressions, "evaluated_at": _now().isoformat()}


def _move(p: LearningPolicyVersion, to: str, by: str, note: str, rollout: int | None = None) -> None:
    if to not in TRANSITIONS[p.status]:
        raise ApiError(409, "bad_transition", f"{p.status} -> {to}")
    p.status = to
    if rollout is not None:
        p.rollout_pct = rollout
    if to in ("rolled_back", "retired"):
        p.rollout_pct = 0
    p.history = [*(p.history or []), {"to": to, "by": by, "note": note, "rollout_pct": p.rollout_pct,
                                      "at": _now().isoformat()}]


def transition(db: Session, admin: User, p: LearningPolicyVersion, to: str, rollout_pct: int | None,
               note: str) -> LearningPolicyVersion:
    """Human-gated promotion with evidence checks (no self-deploying updates)."""
    if to not in TRANSITIONS[p.status]:
        raise ApiError(409, "bad_transition", f"{p.status} -> {to}")
    other = live_policy(db, p.kind)
    if to in LIVE and other is not None and other.id != p.id:
        raise ApiError(409, "policy_conflict", f"policy v{other.version} is already {other.status}")
    ev = evaluate(db, p)
    p.evaluation = ev
    if to == "ab":
        g = RoutingPolicyConfig.model_validate(p.config).guard
        if ev["shadow"]["n"] < g.min_samples:
            raise ApiError(409, "insufficient_evidence", "run the policy in shadow mode longer",
                           {"shadow_decisions": ev["shadow"]["n"], "required": g.min_samples})
        if not rollout_pct or not 1 <= rollout_pct <= 50:
            raise ApiError(422, "bad_rollout", "A/B rollout must be 1..50 %")
    if to == "active" and (not ev["enough_evidence"] or ev["regressions"]):
        raise ApiError(409, "insufficient_evidence", "the candidate has not shown non-inferior results",
                       {"evaluation": ev})
    _move(p, to, str(admin.id), note, rollout_pct if to == "ab" else (100 if to == "active" else None))
    db.flush()
    return p


def guard(db: Session) -> dict:
    """Scheduled: evaluate live policies and roll back automatically on regression."""
    out = {}
    for p in db.execute(select(LearningPolicyVersion).where(LearningPolicyVersion.status.in_(("ab", "active"))
                                                            )).scalars():
        ev = evaluate(db, p)
        p.evaluation = ev
        if ev["regressions"]:
            _move(p, "rolled_back", "guard", "automatic rollback: " + ",".join(ev["regressions"]))
        out[f"{p.kind}:v{p.version}"] = {"status": p.status, "regressions": ev["regressions"]}
    db.flush()
    return out


def get(db: Session, policy_id: uuid.UUID) -> LearningPolicyVersion:
    p = db.get(LearningPolicyVersion, policy_id)
    if p is None:
        raise not_found("policy")
    return p
