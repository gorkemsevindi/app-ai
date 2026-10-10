"""Learning foundation (Master Spec V5, Phase B): consent-aware, content-free instrumentation and technical memory.

What is recorded (only with the `learning` flag on and the user's `technical_improvement` consent, default on):
one `job_outcome` event per finished job, with feature/intent category, creative mode, provider, prompt-strategy
version, duration/resolution, success, error code, retries, latency, credits, estimated vs actual cost and numeric
QA signals; and one `feedback` event per job (rating + reason tags) from its owner. No prompts, media, face data or
outputs are stored here; `content_training` (opt-in, off by default) is a recorded preference only — nothing in
this phase retains content for training.

Feedback is kept separate from popularity (views/shares never enter these tables). Aggregates (technical memory)
count jobs and ratings, never identities, and report rating coverage so selection bias is visible."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session, attributes

from ..errors import ApiError, not_found
from ..models import (
    TERMINAL_STATUSES,
    ConsentRecord,
    FeatureFlag,
    GenerationJob,
    JobKind,
    JobStatus,
    LearningEvent,
    ModelPerformanceAggregate,
    ModelRun,
    Template,
    User,
)

FLAG = "learning"
SCHEMA_VERSION = 1
POLICY_VERSION = "learning-consent-v1"
PURPOSES = {"technical_improvement": True, "content_training": False, "personalization": False}  # defaults
REASONS = {"identity", "lip_sync", "motion", "artifacts", "prompt_mismatch", "audio", "too_slow", "great", "other"}
DEFAULTS = {"retention_days": 400, "drift_success_drop": 0.15, "drift_rating_drop": 0.5, "min_jobs_for_drift": 20}


def _now() -> datetime:
    return datetime.now(UTC)


def config(db: Session) -> tuple[bool, dict]:
    f = db.get(FeatureFlag, FLAG)
    return bool(f and f.enabled), {**DEFAULTS, **((f.value or {}) if f else {})}


# ---------------------------------------------------------------- consent

def consents(db: Session, user_id: uuid.UUID) -> dict[str, bool]:
    out = dict(PURPOSES)
    rows = db.execute(select(ConsentRecord).where(ConsentRecord.user_id == user_id)
                      .order_by(ConsentRecord.id)).scalars()
    for r in rows:
        out[r.purpose] = r.granted
    return out


def set_consents(db: Session, user: User, changes: dict[str, bool], source: str = "user") -> dict[str, bool]:
    bad = set(changes) - set(PURPOSES)
    if bad:
        raise ApiError(422, "bad_purpose", f"unknown purposes: {sorted(bad)}")
    current = consents(db, user.id)
    for purpose, granted in changes.items():
        if current[purpose] != granted:
            db.add(ConsentRecord(user_id=user.id, purpose=purpose, granted=bool(granted),
                                 policy_version=POLICY_VERSION, source=source))
    db.flush()
    return consents(db, user.id)


def delete_learning_data(db: Session, user: User, source: str = "data_deletion") -> int:
    """User control (V5 §5): remove their learning records; aggregates hold no identities and stay."""
    n = db.execute(select(func.count()).select_from(LearningEvent).where(LearningEvent.user_id == user.id)
                   ).scalar_one()
    db.query(LearningEvent).filter(LearningEvent.user_id == user.id).delete(synchronize_session=False)
    set_consents(db, user, {"content_training": False, "personalization": False}, source=source)
    return int(n)


# ---------------------------------------------------------------- job outcome instrumentation

def _intent(db: Session, job: GenerationJob) -> str:
    if job.kind == JobKind.studio_shot or job.kind == JobKind.studio_assemble:
        return "studio"
    if job.template_id:
        t = db.get(Template, job.template_id)
        return t.category if t else "template"
    return "multi_person" if job.kind == JobKind.multi_replace else job.kind.value


def _numeric_qa(qa: dict | None, prefix: str = "") -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v in (qa or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out[key] = float(v)
        elif isinstance(v, dict) and k != "tracks":
            out.update(_numeric_qa(v, f"{key}."))
    return out


def record_outcome(db: Session, job: GenerationJob) -> LearningEvent | None:
    from .economics import feature_of

    enabled, cfg = config(db)
    if not enabled or (job.spec or {}).get("benchmark_run_id"):
        return None  # evaluation runs are not product telemetry
    c = consents(db, job.user_id)
    if not c["technical_improvement"]:
        return None
    if db.execute(select(LearningEvent.id).where(LearningEvent.job_id == job.id,
                                                 LearningEvent.kind == "job_outcome")).first():
        return None
    runs = db.execute(select(ModelRun).where(ModelRun.job_id == job.id).order_by(ModelRun.attempt)).scalars().all()
    spec = job.spec or {}
    ev = LearningEvent(
        schema_version=SCHEMA_VERSION, kind="job_outcome", job_id=job.id, user_id=job.user_id,
        feature=feature_of(job), intent_category=_intent(db, job),
        creative_mode=spec.get("creative_mode", "balanced"), provider=job.model_used or job.preferred_model,
        prompt_strategy=spec.get("prompt_strategy", "v0"), resolution=spec.get("resolution"),
        duration_s=float((spec.get("shot") or {}).get("duration_s") or 0) or None,
        status=job.status.value, success=job.status == JobStatus.completed, error_code=job.error_code,
        retries=max(0, job.attempts - 1),
        latency_s=round((job.finished_at - job.created_at).total_seconds(), 2) if job.finished_at else None,
        credits=job.credit_cost, est_cost_usd=job.est_cost_usd,
        actual_cost_usd=round(sum(r.est_cost_usd or 0.0 for r in runs), 5),
        quality=_numeric_qa((runs[-1].metrics or {}).get("qa") if runs else None),
        consent={k: c[k] for k in ("technical_improvement", "content_training")}, purpose="technical_improvement",
        provenance={"source": "api.job_terminal", "schema": SCHEMA_VERSION},
        retention_until=_now() + timedelta(days=int(cfg["retention_days"])))
    db.add(ev)
    return ev


@event.listens_for(Session, "before_flush")
def _capture_terminal_jobs(session: Session, flush_context, instances) -> None:
    """Every path that finishes a job (complete, fail, cancel, refuse at dispatch, lease reaping) goes through a
    flush; capture the transition here so instrumentation can't be forgotten on a new code path."""
    for obj in list(session.dirty):
        if not isinstance(obj, GenerationJob) or obj.status not in TERMINAL_STATUSES:
            continue
        hist = attributes.get_history(obj, "status")
        if not hist.has_changes():
            continue
        with session.no_autoflush:
            try:
                record_outcome(session, obj)
            except Exception:  # noqa: BLE001 - telemetry must never break job processing
                import logging

                logging.getLogger("learning").warning("learning event failed for %s", obj.id, exc_info=True)


# ---------------------------------------------------------------- feedback

def feedback(db: Session, user: User, job_id: uuid.UUID, rating: int, reasons: list[str],
             regenerated: bool) -> LearningEvent:
    enabled, cfg = config(db)
    if not enabled:
        raise ApiError(403, "feature_disabled", "feedback is not available yet")
    job = db.get(GenerationJob, job_id)
    if job is None or job.user_id != user.id:
        raise not_found("generation")  # owner only: no one can rate (or poison) someone else's job
    if job.status != JobStatus.completed:
        raise ApiError(409, "not_completed", "rate a finished video")
    bad = set(reasons) - REASONS
    if bad or not 1 <= rating <= 5:
        raise ApiError(422, "bad_feedback", "rating 1..5 and known reasons only")
    c = consents(db, user.id)
    ev = db.execute(select(LearningEvent).where(LearningEvent.job_id == job.id, LearningEvent.kind == "feedback")
                    ).scalar_one_or_none()
    if ev is None:
        ev = LearningEvent(schema_version=SCHEMA_VERSION, kind="feedback", job_id=job.id, user_id=user.id,
                           feature=_feature(job), intent_category=_intent(db, job),
                           creative_mode=(job.spec or {}).get("creative_mode", "balanced"),
                           provider=job.model_used, prompt_strategy=(job.spec or {}).get("prompt_strategy", "v0"),
                           retention_until=_now() + timedelta(days=int(cfg["retention_days"])),
                           provenance={"source": "api.feedback", "schema": SCHEMA_VERSION})
        db.add(ev)
    # one rating per job: a repeat replaces it instead of adding weight (duplicate / spam resistance)
    ev.rating, ev.reasons, ev.regenerated = rating, sorted(set(reasons)), regenerated
    ev.consent = {k: c[k] for k in ("technical_improvement", "content_training")}
    db.flush()
    return ev


def _feature(job: GenerationJob) -> str:
    from .economics import feature_of

    return feature_of(job)


# ---------------------------------------------------------------- technical memory + dashboard

def _pct(v: list[float], q: float) -> float | None:
    if not v:
        return None
    v = sorted(v)
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 2)


def aggregate(db: Session, day: date) -> int:
    """Upsert the day's roll-up from consented events only. Idempotent (re-running replaces the day)."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    rows = db.execute(select(LearningEvent).where(LearningEvent.created_at >= start,
                                                  LearningEvent.created_at < start + timedelta(days=1))).scalars().all()
    ratings = {e.job_id: e.rating for e in rows if e.kind == "feedback" and e.rating}
    groups: dict[tuple, list[LearningEvent]] = defaultdict(list)
    for e in rows:
        if e.kind == "job_outcome" and e.consent.get("technical_improvement", True):
            groups[(e.provider or "unknown", e.feature, e.creative_mode or "balanced")].append(e)
    db.query(ModelPerformanceAggregate).filter(ModelPerformanceAggregate.day == day).delete()
    for (prov, feat, mode), evs in groups.items():
        lat = [e.latency_s for e in evs if e.success and e.latency_s is not None]
        rs = [ratings[e.job_id] for e in evs if e.job_id in ratings]
        q: dict[str, list[float]] = defaultdict(list)
        errs: dict[str, int] = defaultdict(int)
        for e in evs:
            for k, v in (e.quality or {}).items():
                q[k].append(v)
            if e.error_code:
                errs[e.error_code] += 1
        db.add(ModelPerformanceAggregate(
            day=day, provider=prov, feature=feat, creative_mode=mode, jobs=len(evs),
            successes=sum(1 for e in evs if e.success), p50_latency_s=_pct(lat, 0.5), p95_latency_s=_pct(lat, 0.95),
            cost_usd=round(sum(e.actual_cost_usd or 0 for e in evs), 5), ratings=len(rs),
            rating_mean=round(sum(rs) / len(rs), 3) if rs else None,
            quality={k: round(sum(v) / len(v), 4) for k, v in q.items()}, errors=dict(errs), computed_at=_now()))
    db.flush()
    return len(groups)


def overview(db: Session, days: int) -> dict:
    _, cfg = config(db)
    today = _now().date()
    cur = db.execute(select(ModelPerformanceAggregate).where(
        ModelPerformanceAggregate.day > today - timedelta(days=days))).scalars().all()
    prev = db.execute(select(ModelPerformanceAggregate).where(
        ModelPerformanceAggregate.day <= today - timedelta(days=days),
        ModelPerformanceAggregate.day > today - timedelta(days=2 * days))).scalars().all()

    def roll(rows):
        g: dict[tuple, dict] = defaultdict(lambda: {"jobs": 0, "successes": 0, "cost_usd": 0.0, "ratings": 0,
                                                     "rating_sum": 0.0})
        for r in rows:
            x = g[(r.provider, r.feature)]
            x["jobs"] += r.jobs
            x["successes"] += r.successes
            x["cost_usd"] += r.cost_usd
            x["ratings"] += r.ratings
            x["rating_sum"] += (r.rating_mean or 0) * r.ratings
        return g

    now_g, prev_g = roll(cur), roll(prev)
    rows, alerts = [], []
    for (prov, feat), x in sorted(now_g.items()):
        sr = x["successes"] / x["jobs"] if x["jobs"] else None
        rating = x["rating_sum"] / x["ratings"] if x["ratings"] else None
        row = {"provider": prov, "feature": feat, "jobs": x["jobs"], "success_rate": round(sr, 4) if sr else sr,
               "cost_per_success_usd": round(x["cost_usd"] / x["successes"], 5) if x["successes"] else None,
               "rating_mean": round(rating, 3) if rating else None,
               "rating_coverage": round(x["ratings"] / x["jobs"], 4) if x["jobs"] else None}
        rows.append(row)
        p = prev_g.get((prov, feat))
        if p and p["jobs"] >= cfg["min_jobs_for_drift"] and x["jobs"] >= cfg["min_jobs_for_drift"]:
            psr = p["successes"] / p["jobs"]
            if sr is not None and psr - sr > cfg["drift_success_drop"]:
                alerts.append({"provider": prov, "feature": feat, "type": "success_rate_drop",
                               "before": round(psr, 4), "now": round(sr, 4)})
            prev_rating = p["rating_sum"] / p["ratings"] if p["ratings"] else None
            if prev_rating is not None and rating is not None and prev_rating - rating > cfg["drift_rating_drop"]:
                alerts.append({"provider": prov, "feature": feat, "type": "rating_drop",
                               "before": round(p["rating_sum"] / p["ratings"], 3), "now": round(rating, 3)})
    events = db.execute(select(LearningEvent.kind, func.count()).group_by(LearningEvent.kind)).all()
    diversity = _diversity(db, days)
    training_eligible = db.execute(select(func.count()).select_from(LearningEvent).where(
        LearningEvent.consent["content_training"].as_boolean().is_(True))).scalar_one()
    return {"days": days, "rows": rows, "alerts": alerts, "diversity": diversity,
            "eligibility": {"events": dict(events), "content_training_opt_in_events": training_eligible,
                            "note": "no content is retained for training in this phase"}}


def _diversity(db: Session, days: int) -> dict:
    """V5 §4/§8: creative diversity by genre (entropy of composition strategies over planned versions) and the
    near-duplicate rate from similarity audits. Low entropy in a genre = the planner keeps repeating itself."""
    from collections import Counter

    from ..models import SimilarityAudit, StudioProjectVersion
    from .creative import entropy

    since = _now() - timedelta(days=days)
    by_genre: dict[str, Counter] = defaultdict(Counter)
    for c in db.execute(select(StudioProjectVersion.creative).where(StudioProjectVersion.created_at >= since)
                        ).scalars():
        if c and c.get("composition"):
            by_genre[(c.get("intent") or {}).get("genre", "general")][c["composition"]] += 1
    audits = db.execute(select(SimilarityAudit.decision, func.count()).where(SimilarityAudit.created_at >= since)
                        .group_by(SimilarityAudit.decision)).all()
    total = sum(n for _, n in audits)
    near = sum(n for d, n in audits if d == "near_duplicate")
    return {"by_genre": {g: {"plans": sum(c.values()), "compositions": dict(c), "entropy_bits": entropy(c)}
                         for g, c in sorted(by_genre.items())},
            "near_duplicate_rate": round(near / total, 4) if total else None, "audits": dict(audits)}
