"""Unit economics (V4 Stage A4, spec §15/§28): per-job telemetry joined with the credit ledger.

- Revenue only counts *paid* credits of *settled* jobs: the buckets a reservation consumed are known from
  `credit_allocations` (purchased/subscription = paid; promo/reward/adjustment/legacy = not revenue).
- USD per paid credit is remote config (`economics.usd_per_paid_credit`, net of store fees). When it is
  not set, revenue and margin are reported as null instead of invented numbers.
- Cost is the sum of every attempt's recorded cost (failed attempts and retries included)."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import CreditAllocation, CreditLedger, CreditLot, FeatureFlag, GenerationJob, JobKind, ModelRun
from .generation import spend_today

FLAG = "economics"
DEFAULTS = {"usd_per_paid_credit": None, "alert_min_jobs": 20, "alert_min_success_rate": 0.8,
            "alert_max_cost_per_success_usd": None, "daily_budget_alert_ratio": 0.8}
PAID_BUCKETS = {"purchased", "subscription"}


def config(db: Session) -> dict:
    f = db.get(FeatureFlag, FLAG)
    return {**DEFAULTS, **((f.value or {}) if f else {})}


def feature_of(job: GenerationJob) -> str:
    if job.kind == JobKind.analysis:
        return "analysis"
    if job.kind in (JobKind.studio_shot, JobKind.studio_assemble):
        return "studio"
    if job.kind == JobKind.character_asset:
        return "character"
    if job.kind == JobKind.editor_render:
        return "editor_export"
    base = "template" if job.kind == JobKind.template else ("remix" if job.template_id else "multi_person")
    lip = ((job.spec or {}).get("audio") or {}).get("lip_sync", {}).get("enabled")
    return f"{base}+lip_sync" if lip else base


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 2)


def _jobs(db: Session, since: datetime, limit: int = 50000) -> list[GenerationJob]:
    return list(db.execute(select(GenerationJob).where(GenerationJob.created_at >= since)
                           .order_by(GenerationJob.created_at.desc()).limit(limit)).scalars())


def _costs(db: Session, ids: list) -> dict:
    if not ids:
        return {}
    return dict(db.execute(select(ModelRun.job_id, func.coalesce(func.sum(ModelRun.est_cost_usd), 0.0))
                           .where(ModelRun.job_id.in_(ids)).group_by(ModelRun.job_id)).all())


def _paid_credits(db: Session, ids: list) -> dict:
    """job_id -> credits of its reservation that came from paid buckets."""
    if not ids:
        return {}
    keys = [f"gen:{i}" for i in ids]
    rows = db.execute(select(CreditLedger.ref_id, CreditLot.bucket, func.sum(-CreditAllocation.amount))
                      .join(CreditAllocation, CreditAllocation.ledger_id == CreditLedger.id)
                      .join(CreditLot, CreditLot.id == CreditAllocation.lot_id)
                      .where(CreditLedger.idempotency_key.in_(keys))
                      .group_by(CreditLedger.ref_id, CreditLot.bucket)).all()
    out: dict = defaultdict(int)
    for ref, bucket, amount in rows:
        if bucket in PAID_BUCKETS:
            out[ref] += int(amount)
    return out


def telemetry(db: Session, job: GenerationJob, cost: float | None = None) -> dict:
    runs = db.execute(select(ModelRun).where(ModelRun.job_id == job.id).order_by(ModelRun.attempt)).scalars().all()
    first = runs[0].started_at if runs else None
    actual = sum(r.est_cost_usd or 0.0 for r in runs) if cost is None else cost
    return {
        "job_id": str(job.id), "feature": feature_of(job), "status": job.status.value, "kind": job.kind.value,
        "model": job.model_used, "template_id": str(job.template_id) if job.template_id else None,
        "template_version_id": str(job.template_version_id) if job.template_version_id else None,
        "credits": job.credit_cost, "billing_state": job.billing_state, "refunded": job.refunded,
        "est_cost_usd": job.est_cost_usd, "actual_cost_usd": round(actual, 4),
        "attempts": job.attempts, "error_code": job.error_code,
        "queue_s": round((first - job.created_at).total_seconds(), 1) if first else None,
        "time_to_result_s": round((job.finished_at - job.created_at).total_seconds(), 1) if job.finished_at else None,
        "gpu_seconds": round(sum(r.gpu_seconds or 0 for r in runs), 1),
        "runs": [{"attempt": r.attempt, "model": r.model, "status": r.status, "gpu_type": r.gpu_type,
                  "gpu_seconds": r.gpu_seconds, "cost_usd": r.est_cost_usd, "error": r.error,
                  "qa": (r.metrics or {}).get("qa")} for r in runs],
    }


def report(db: Session, days: int, group: str) -> dict:
    cfg = config(db)
    since = datetime.now(UTC) - timedelta(days=days)
    jobs = _jobs(db, since)
    ids = [j.id for j in jobs]
    costs, paid = _costs(db, ids), _paid_credits(db, ids)
    usd_per_credit = cfg.get("usd_per_paid_credit")

    def key(j: GenerationJob) -> str:
        if group == "model":
            return j.model_used or j.preferred_model or "unrouted"
        if group == "template":
            return str(j.template_id) if j.template_id else "none"
        return feature_of(j)

    groups: dict[str, dict] = defaultdict(lambda: {"jobs": 0, "completed": 0, "failed": 0, "cancelled": 0,
                                                   "credits_settled": 0, "credits_released": 0, "paid_credits": 0,
                                                   "cost_usd": 0.0, "est_cost_usd": 0.0, "retries": 0, "_ttr": []})
    for j in jobs:
        g = groups[key(j)]
        g["jobs"] += 1
        st = j.status.value
        if st in ("completed", "failed", "cancelled"):
            g[st] += 1
        if j.billing_state == "settled":
            g["credits_settled"] += j.credit_cost
            g["paid_credits"] += paid.get(str(j.id), 0)
        elif j.billing_state == "released":
            g["credits_released"] += j.credit_cost
        g["cost_usd"] += float(costs.get(j.id, 0.0))
        g["est_cost_usd"] += float(j.est_cost_usd or 0.0)
        g["retries"] += max(0, j.attempts - 1)
        if st == "completed" and j.finished_at:
            g["_ttr"].append((j.finished_at - j.created_at).total_seconds())

    rows, alerts = [], []
    for k, g in groups.items():
        done, terminal = g["completed"], g["completed"] + g["failed"]
        revenue = round(g["paid_credits"] * float(usd_per_credit), 4) if usd_per_credit is not None else None
        row = {
            "key": k, **{x: g[x] for x in ("jobs", "completed", "failed", "cancelled", "credits_settled",
                                           "credits_released", "paid_credits", "retries")},
            "success_rate": round(done / terminal, 4) if terminal else None,
            "cost_usd": round(g["cost_usd"], 4), "est_cost_usd": round(g["est_cost_usd"], 4),
            "cost_per_success_usd": round(g["cost_usd"] / done, 4) if done else None,
            "revenue_usd": revenue,
            "contribution_margin_usd": round(revenue - g["cost_usd"], 4) if revenue is not None else None,
            "p50_time_to_result_s": _pct(g["_ttr"], 0.5), "p95_time_to_result_s": _pct(g["_ttr"], 0.95),
        }
        rows.append(row)
        if g["jobs"] >= int(cfg["alert_min_jobs"]):
            if row["success_rate"] is not None and row["success_rate"] < float(cfg["alert_min_success_rate"]):
                alerts.append({"key": k, "type": "low_success_rate", "value": row["success_rate"]})
            if row["contribution_margin_usd"] is not None and row["contribution_margin_usd"] < 0:
                alerts.append({"key": k, "type": "negative_margin", "value": row["contribution_margin_usd"]})
            cap = cfg.get("alert_max_cost_per_success_usd")
            if cap is not None and row["cost_per_success_usd"] is not None and row["cost_per_success_usd"] > cap:
                alerts.append({"key": k, "type": "cost_per_success_above_cap", "value": row["cost_per_success_usd"]})
    from ..config import get_settings

    budget = get_settings().gpu_daily_budget_usd
    spent = spend_today(db)
    if budget and spent >= budget * float(cfg["daily_budget_alert_ratio"]):
        alerts.append({"key": "gpu_daily_budget", "type": "spend_burn_rate", "value": round(spent / budget, 3)})
    rows.sort(key=lambda r: -r["jobs"])
    return {"days": days, "group": group, "usd_per_paid_credit": usd_per_credit, "rows": rows, "alerts": alerts,
            "gpu_spend_today_usd": round(spent, 4), "gpu_daily_budget_usd": budget}
