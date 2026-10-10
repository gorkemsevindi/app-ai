"""AI Budget Director (Master Spec V7 §7): budget-driven production.

- Profiles economy / standard / cinema_pro are *config versions* (quality tier → credits/s and routing, default
  identity lock mode, expected retry rate, retry limit), not marketing labels.
- An estimate breaks every shot down: video compute (credits + provider USD), identity-lock multiplier,
  character licence credits, TTS / lip-sync (only when a provider is configured — otherwise "not available"),
  storage/egress, moderation, expected retries. Provider costs are a range; the user's credit price is the
  quote (failed attempts are refunded, so retries never raise the user's charge).
- estimate ≠ quote ≠ actual: the approval stores the estimate and a **hard cap**. Every paid render checks
  committed (reserved + settled) + new ≤ cap; an overrun needs a new approval. Currency amounts are integer
  minor units computed with Decimal from an ops-configured retail price per credit (never hard-coded).
- Infeasible budgets ("20 minutes for 15 USD") get an honest answer plus concrete alternatives."""

from __future__ import annotations

import math
import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import ApiError, not_found
from ..models import (
    BudgetAuthorization,
    CostEstimate,
    FeatureFlag,
    GenerationJob,
    ModelRun,
    Production,
    ProductionEpisode,
    StudioProject,
    User,
)

FLAG = "productions"
PROFILES = ("economy", "standard", "cinema_pro")
DEFAULT_PROFILES = {
    "version": 1,
    "economy": {"quality": "standard", "lock_mode": "standard", "retry_rate": 0.10, "max_retries": 1,
                "resolution": "720p"},
    "standard": {"quality": "standard", "lock_mode": "strong", "retry_rate": 0.15, "max_retries": 2,
                 "resolution": "720p"},
    "cinema_pro": {"quality": "premium", "lock_mode": "strict", "retry_rate": 0.25, "max_retries": 3,
                   "resolution": "720p"},
}
DEFAULTS: dict = {
    "max_episode_seconds": 1800,  # quota: one episode/part is at most 30 min (many short shots + assembly)
    "max_episodes": 100,
    "max_productions_per_user": 20,
    "formats": {"micro": [30, 120], "short_series": [120, 300], "standard_episode": [300, 600],
                "long_episode": [600, 1800], "short_film": [300, 1800], "feature": [1800, 10800]},
    "profiles": DEFAULT_PROFILES,
    "currency_per_credit": {},  # retail price per credit by currency, set by ops (e.g. {"USD": "0.01"})
    "tax_rate": "0",
    "costs_usd": {"tts_per_1k_chars": None, "lip_sync_per_second": None, "storage_per_gb_month": None,
                  "egress_per_gb": None, "moderation_per_minute": None},
    "video_mbps": 4.0,
    "pilot_seconds": [30, 60],
    "allow_mature": True,  # regional/store policy switch for mature fiction
}


def config(db: Session) -> tuple[bool, dict]:
    f = db.get(FeatureFlag, FLAG)
    raw = (f.value or {}) if f else {}
    v = {**DEFAULTS, **raw}
    v["costs_usd"] = {**DEFAULTS["costs_usd"], **(raw.get("costs_usd") or {})}
    v["profiles"] = {**DEFAULT_PROFILES, **(raw.get("profiles") or {})}
    return bool(f and f.enabled), v


def require_enabled(db: Session) -> dict:
    ok, cfg = config(db)
    if not ok:
        raise ApiError(403, "feature_disabled", "productions are not available yet")
    return cfg


def to_minor(credits: int, currency: str, cfg: dict) -> int | None:
    rate = cfg["currency_per_credit"].get(currency)
    if rate is None:
        return None
    amount = Decimal(int(credits)) * Decimal(str(rate)) * (Decimal(1) + Decimal(str(cfg["tax_rate"])))
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def from_minor(minor: int, currency: str, cfg: dict) -> int:
    rate = cfg["currency_per_credit"].get(currency)
    if rate is None:
        raise ApiError(422, "pricing_not_configured", f"no retail price per credit is configured for {currency}")
    per_credit = Decimal(str(rate)) * (Decimal(1) + Decimal(str(cfg["tax_rate"])))
    return int((Decimal(int(minor)) / 100 / per_credit).to_integral_value(rounding="ROUND_FLOOR"))


# ---------------------------------------------------------------- estimate

def _episode_estimate(db: Session, user: User, ep: ProductionEpisode, cfg: dict, prof: dict) -> dict:
    from . import studio

    project = db.get(StudioProject, ep.studio_project_id)
    scfg = studio.config(db)[1]
    costs = cfg["costs_usd"]
    if project is None or project.current_version_id is None:  # not planned yet: rough, from the target duration
        cps = float(scfg["credits_per_second"][prof["quality"]])
        mult = 1.0 if prof["lock_mode"] == "standard" else 1.5
        c = int(math.ceil(ep.target_duration_s * cps * mult))
        return {"episode": ep.number, "season": ep.season, "planned": False, "credits": c,
                "credits_low": int(c * 0.8), "credits_high": int(c * 1.2), "provider_usd": None, "shots": [],
                "seconds": ep.target_duration_s, "notes": ["not planned yet: rough estimate from the target duration"]}
    v = studio.get_version(db, project, None)
    est = studio.estimate(db, user, project, v)
    seconds = sum(r["duration_s"] for r in est["shots"])
    mb = seconds * float(cfg["video_mbps"]) / 8
    extras, notes = {}, []
    chars = sum(len(d.get("text", "")) for sc in v.storyboard.get("scenes", []) for sh in sc.get("shots", [])
                for d in sh.get("dialogue", []))
    talk_s = sum(len(sh.get("dialogue", [])) for sc in v.storyboard.get("scenes", []) for sh in sc.get("shots", []))
    if costs["tts_per_1k_chars"] is not None:
        extras["tts_usd"] = round(chars / 1000 * float(costs["tts_per_1k_chars"]), 4)
    else:
        notes.append("Voice (TTS) is not available: dialogue is delivered as exact subtitles")
    if costs["lip_sync_per_second"] is not None:
        extras["lip_sync_usd"] = round(talk_s * 2 * float(costs["lip_sync_per_second"]), 4)
    else:
        notes.append("Lip-sync is not available for productions yet")
    if costs["storage_per_gb_month"] is not None:
        extras["storage_usd"] = round(mb / 1024 * float(costs["storage_per_gb_month"]), 4)
    if costs["egress_per_gb"] is not None:
        extras["egress_usd"] = round(mb / 1024 * float(costs["egress_per_gb"]), 4)
    if costs["moderation_per_minute"] is not None:
        extras["moderation_usd"] = round(seconds / 60 * float(costs["moderation_per_minute"]), 4)
    video = est["est_cost_usd"]
    low = None if video is None else round(video + sum(extras.values()), 4)
    high = None if video is None else round(video * (1 + float(prof["retry_rate"])) + sum(extras.values()), 4)
    return {"episode": ep.number, "season": ep.season, "planned": True, "credits": est["credits"],
            "credits_low": est["credits"], "credits_high": est["credits"], "provider_usd": video,
            "provider_usd_low": low, "provider_usd_high": high, "extras_usd": extras, "seconds": seconds,
            "new_shots": est["new_shots"], "reused_shots": est["reused_shots"], "provider": est["provider"],
            "missing_capabilities": est["missing_capabilities"], "blocked": est["blocked"],
            "shots": [{k: r[k] for k in ("key", "duration_s", "status", "credits", "est_cost_usd", "lock_mode",
                                         "license_credits", "capabilities")} for r in est["shots"]],
            "limitations": est["limitations"], "notes": notes,
            "action_credits": sum(r["credits"] for r in est["shots"]
                                  if _scene_type(v.storyboard, r["key"]) == "action")}


def _scene_type(sb: dict, shot_key: str) -> str | None:
    for sc in sb.get("scenes", []):
        if any(s["key"] == shot_key for s in sc.get("shots", [])):
            return sc.get("scene_type")
    return None


def estimate(db: Session, user: User, prod: Production, episodes: list[ProductionEpisode], profile: str | None,
             cap_credits: int | None, cap_minor: int | None, currency: str | None, purpose: str = "full"
             ) -> CostEstimate:
    cfg = require_enabled(db)
    profile = profile or prod.profile
    if profile not in PROFILES:
        raise ApiError(422, "bad_profile", "profile: economy, standard or cinema_pro")
    prof = cfg["profiles"][profile]
    if cap_minor is not None:
        if not currency:
            raise ApiError(422, "currency_required", "give the currency of the budget")
        cap_credits = from_minor(cap_minor, currency, cfg)
    rows = [_episode_estimate(db, user, ep, cfg, prof) for ep in episodes]
    lo, hi = sum(r["credits_low"] for r in rows), sum(r["credits_high"] for r in rows)
    usd = [r for r in rows if r.get("provider_usd_low") is not None]
    usd_lo = round(sum(r["provider_usd_low"] for r in usd), 4) if usd else None
    usd_hi = round(sum(r["provider_usd_high"] for r in usd), 4) if usd else None
    seconds = sum(r["seconds"] for r in rows)
    feasible = None if cap_credits is None else hi <= cap_credits
    data = {"episodes": rows, "seconds": seconds, "profile": {"name": profile, **prof},
            "estimate_is_not_quote": "provider prices can change; the approval sets a hard cap and every render is "
                                     "re-quoted before it starts",
            "refund_policy": "failed, blocked or cancelled shots are refunded; strict-lock retries are never charged",
            "alternatives": [] if feasible in (True, None) else alternatives(db, cfg, prof, rows, cap_credits, hi)}
    cur = currency or (next(iter(cfg["currency_per_credit"]), None))
    est = CostEstimate(production_id=prod.id, scope={"episodes": [str(e.id) for e in episodes], "purpose": purpose,
                                                     "cap_credits": cap_credits},
                       profile=profile, profile_version=int(cfg["profiles"].get("version", 1)), credits_low=lo,
                       credits_high=hi, currency=cur, amount_low_minor=to_minor(lo, cur, cfg) if cur else None,
                       amount_high_minor=to_minor(hi, cur, cfg) if cur else None, provider_usd_low=usd_lo,
                       provider_usd_high=usd_hi, feasible=feasible, data=data, created_by=user.id)
    db.add(est)
    db.flush()
    return est


def alternatives(db: Session, cfg: dict, prof: dict, rows: list[dict], cap: int, needed: int) -> list[dict]:
    from . import studio

    scfg = studio.config(db)[1]
    seconds = sum(r["seconds"] for r in rows) or 1
    per_s = needed / seconds
    out = [{"type": "reduce_duration", "max_seconds": int(cap / per_s) if per_s else 0,
            "detail": f"about {int(cap / per_s) // 60} min fit this budget at the current profile" if per_s else ""}]
    eco_cps = float(scfg["credits_per_second"]["standard"])
    eco = int(math.ceil(seconds * eco_cps)) + sum(sum(s.get("license_credits", 0) for s in r["shots"]) for r in rows)
    if eco < needed:
        out.append({"type": "economy_profile", "credits": eco, "fits": eco <= cap,
                    "detail": "standard quality, best-effort identity lock (measured, not guaranteed)"})
    action = sum(r.get("action_credits", 0) for r in rows)
    if action:
        out.append({"type": "less_action", "action_credits": action,
                    "detail": "rewrite action scenes as dialogue/landscape scenes (action costs more)"})
    if len(rows) > 1:
        first = rows[0]["credits_high"]
        out.append({"type": "episode_by_episode", "credits": first, "fits": first <= cap,
                    "detail": "produce and approve one episode at a time"})
    out.append({"type": "animatic_and_pilot", "credits": 0,
                "detail": "free storyboard animatic first, then a 30-60 s pilot before committing"})
    out.append({"type": "increase_budget", "missing_credits": needed - cap})
    return out


def estimate_out(e: CostEstimate) -> dict:
    return {"id": str(e.id), "profile": e.profile, "profile_version": e.profile_version, "credits_low": e.credits_low,
            "credits_high": e.credits_high, "currency": e.currency, "amount_low_minor": e.amount_low_minor,
            "amount_high_minor": e.amount_high_minor, "provider_usd_low": e.provider_usd_low,
            "provider_usd_high": e.provider_usd_high, "feasible": e.feasible, "cap_credits": e.scope.get("cap_credits"),
            **e.data}


# ---------------------------------------------------------------- authorization + hard cap

def approve(db: Session, user: User, prod: Production, estimate_id: uuid.UUID, cap_credits: int | None,
            idem: str) -> BudgetAuthorization:
    cfg = require_enabled(db)
    key = f"budget:{prod.id}:{idem}"
    existing = db.execute(select(BudgetAuthorization).where(BudgetAuthorization.idempotency_key == key)
                          ).scalar_one_or_none()
    if existing is not None:
        return existing
    est = db.get(CostEstimate, estimate_id)
    if est is None or est.production_id != prod.id:
        raise not_found("estimate")
    cap = cap_credits if cap_credits is not None else est.scope.get("cap_credits") or est.credits_high
    if cap < committed(db, prod.id):
        raise ApiError(409, "cap_below_committed", "the cap is below what is already committed",
                       {"committed": committed(db, prod.id)})
    for old in db.execute(select(BudgetAuthorization).where(BudgetAuthorization.production_id == prod.id,
                                                            BudgetAuthorization.status == "active")).scalars():
        old.status = "superseded"
    a = BudgetAuthorization(production_id=prod.id, estimate_id=est.id, cap_credits=int(cap), currency=est.currency,
                            cap_minor=to_minor(int(cap), est.currency, cfg) if est.currency else None,
                            idempotency_key=key, created_by=user.id)
    db.add(a)
    db.flush()
    return a


def active_authorization(db: Session, production_id: uuid.UUID) -> BudgetAuthorization | None:
    return db.execute(select(BudgetAuthorization).where(BudgetAuthorization.production_id == production_id,
                                                        BudgetAuthorization.status == "active")).scalar_one_or_none()


def _jobs(production_id: uuid.UUID):
    projects = select(StudioProject.id).join(
        ProductionEpisode, ProductionEpisode.studio_project_id == StudioProject.id).where(
        ProductionEpisode.production_id == production_id)
    return GenerationJob.studio_project_id.in_(projects)


def committed(db: Session, production_id: uuid.UUID) -> int:
    return int(db.execute(select(func.coalesce(func.sum(GenerationJob.credit_cost), 0)).where(
        _jobs(production_id), GenerationJob.billing_state.in_(("reserved", "settled")))).scalar_one())


def check_cap(db: Session, prod: Production, new_credits: int) -> BudgetAuthorization:
    a = active_authorization(db, prod.id)
    if a is None:
        raise ApiError(409, "budget_approval_required", "approve a budget before paid production work")
    used = committed(db, prod.id)
    if used + new_credits > a.cap_credits:
        raise ApiError(402, "budget_cap_exceeded", "this would exceed the approved budget; approve a new budget",
                       {"cap": a.cap_credits, "committed": used, "requested": new_credits,
                        "missing": used + new_credits - a.cap_credits})
    return a


def report(db: Session, prod: Production) -> dict:
    rows = db.execute(select(GenerationJob.billing_state, func.count(),
                             func.coalesce(func.sum(GenerationJob.credit_cost), 0))
                      .where(_jobs(prod.id)).group_by(GenerationJob.billing_state)).all()
    by = {state or "free": {"jobs": n, "credits": int(c)} for state, n, c in rows}
    actual = db.execute(select(func.coalesce(func.sum(ModelRun.est_cost_usd), 0.0)).join(
        GenerationJob, GenerationJob.id == ModelRun.job_id).where(_jobs(prod.id))).scalar_one()
    retries = db.execute(select(func.count()).select_from(ModelRun).join(
        GenerationJob, GenerationJob.id == ModelRun.job_id).where(_jobs(prod.id), ModelRun.attempt > 1)).scalar_one()
    a = active_authorization(db, prod.id)
    est = db.get(CostEstimate, a.estimate_id) if a and a.estimate_id else None
    used = committed(db, prod.id)
    return {"cap_credits": a.cap_credits if a else None, "committed_credits": used,
            "settled_credits": by.get("settled", {}).get("credits", 0),
            "reserved_credits": by.get("reserved", {}).get("credits", 0),
            "refunded_credits": by.get("released", {}).get("credits", 0),
            "remaining_credits": (a.cap_credits - used) if a else None,
            "actual_provider_usd": round(float(actual), 4), "retries": int(retries),
            "estimated_provider_usd": [est.provider_usd_low, est.provider_usd_high] if est else None,
            "by_state": by}
