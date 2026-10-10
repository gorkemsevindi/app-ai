"""Scheduled operations (V4 Stage E). Run from cron / a Kubernetes CronJob:

    python -m app.scheduler all            # every task once
    python -m app.scheduler credit_expiry  # one task

or via `POST /internal/cron/{task}` with the cron token. Each task runs in its own transaction under a
PostgreSQL advisory lock (two schedulers can never run the same task concurrently) and is recorded in
`scheduled_runs` (status, result, error) for the admin console and alerting."""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import (
    CreditAllocation,
    CreditLot,
    FeatureFlag,
    GenerationJob,
    GenerationOutput,
    JobKind,
    ScheduledRun,
    StudioProject,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _flag(db: Session, key: str) -> tuple[bool, dict]:
    f = db.get(FeatureFlag, key)
    return bool(f and f.enabled), dict((f.value or {}) if f else {})


def template_metrics(db: Session) -> dict:
    from .services import templates_v3 as tv3

    today = _now().date()
    return {"templates": tv3.refresh_metrics(db, today - timedelta(days=1)) + tv3.refresh_metrics(db, today)}


def credit_expiry(db: Session) -> dict:
    """Write `expire` entries for every lot past its expiry that still has credits (users who never came back)."""
    from sqlalchemy import func

    from .services import credits

    used = (select(CreditAllocation.lot_id, func.sum(CreditAllocation.amount).label("s"))
            .group_by(CreditAllocation.lot_id).subquery())
    users = db.execute(select(CreditLot.user_id).outerjoin(used, used.c.lot_id == CreditLot.id)
                       .where(CreditLot.expires_at <= _now(),
                              CreditLot.granted + func.coalesce(used.c.s, 0) > 0).distinct()).scalars().all()
    for uid in users:
        credits.sweep_expired(db, uid)
    return {"users": len(users)}


def settlements(db: Session) -> dict:
    """Only when ops turned on automatic batching (`creator_marketplace.auto_settle`)."""
    from .services import creators

    enabled, cfg = _flag(db, "creator_marketplace")
    if not (enabled and cfg.get("auto_settle")) or creators.active_policy(db) is None:
        return {"skipped": True}
    return {"created": len(creators.run_settlements(db))}


def retention(db: Session) -> dict:
    """Storage lifecycle: purge outputs of deleted studio projects after a grace period, and (if configured)
    outputs older than `retention.output_days`. Template remix / face swap outputs of active users are kept
    unless a retention period is set."""
    from .services.storage import get_storage

    _, cfg = _flag(db, "retention")
    st = get_storage()
    purged = 0
    grace = _now() - timedelta(days=int(cfg.get("deleted_project_days", 7)))
    job_ids = select(GenerationJob.id).join(StudioProject, StudioProject.id == GenerationJob.studio_project_id) \
        .where(StudioProject.deleted_at.is_not(None), StudioProject.deleted_at <= grace)
    rows = list(db.execute(select(GenerationOutput).where(GenerationOutput.job_id.in_(job_ids),
                                                          GenerationOutput.deleted_at.is_(None))).scalars())
    days = cfg.get("output_days")
    if days:
        old = select(GenerationJob.id).where(GenerationJob.kind.in_((JobKind.template, JobKind.multi_replace)))
        rows += list(db.execute(select(GenerationOutput).where(
            GenerationOutput.job_id.in_(old), GenerationOutput.deleted_at.is_(None),
            GenerationOutput.created_at <= _now() - timedelta(days=int(days)))).scalars())
    for o in rows:
        for key in (o.video_key, o.thumbnail_key):
            if key:
                st.delete(key)
        o.deleted_at = _now()
        purged += 1
    return {"outputs_purged": purged}


def play_finalize_retry(db: Session) -> dict:
    from .services import billing

    try:
        billing.verifier("android")
    except Exception:  # noqa: BLE001 - Play not configured
        return {"skipped": True}
    return billing.retry_finalize(db)


def webhook_retry(db: Session) -> dict:
    from .services import billing

    return billing.retry_failed_events(db)


def learning_aggregate(db: Session) -> dict:
    """V5 technical memory: roll up yesterday and today (idempotent)."""
    from .services import learning

    today = _now().date()
    return {"groups": learning.aggregate(db, today - timedelta(days=1)) + learning.aggregate(db, today)}


def policy_guard(db: Session) -> dict:
    """V5 Phase D: evaluate live learning policies, roll back automatically on regression."""
    from .services import policies

    return policies.guard(db)


TASKS: dict[str, Callable[[Session], dict]] = {
    "template_metrics": template_metrics,
    "credit_expiry": credit_expiry,
    "settlements": settlements,
    "retention": retention,
    "play_finalize_retry": play_finalize_retry,
    "webhook_retry": webhook_retry,
    "learning_aggregate": learning_aggregate,
    "policy_guard": policy_guard,
}


def _lock_key(task: str) -> int:
    return int(hashlib.sha256(f"scheduler:{task}".encode()).hexdigest()[:15], 16)


def run_task(task: str) -> ScheduledRun:
    from .db import session_factory

    if task not in TASKS:
        raise KeyError(task)
    db = session_factory()()
    try:
        got = db.execute(text("select pg_try_advisory_xact_lock(:k)"), {"k": _lock_key(task)}).scalar_one()
        run = ScheduledRun(task=task, status="running", started_at=_now())
        if not got:
            run.status, run.finished_at, run.result = "skipped", _now(), {"reason": "already running"}
            db.add(run)
            db.commit()
            return run
        try:
            run.result = TASKS[task](db)
            run.status = "succeeded"
        except Exception as e:  # noqa: BLE001 - record, roll back the task's changes, keep the run row
            db.rollback()
            run = ScheduledRun(task=task, status="failed", started_at=run.started_at, error=repr(e)[:500])
        run.finished_at = _now()
        db.add(run)
        db.commit()
        db.refresh(run)
        return run
    finally:
        db.close()


def main(argv: list[str]) -> int:
    names = list(TASKS) if not argv or argv[0] == "all" else argv
    bad = 0
    for n in names:
        r = run_task(n)
        print(f"{n}: {r.status} {r.result or ''} {r.error or ''}")  # noqa: T201
        bad += r.status == "failed"
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
