"""Model registry + adaptive provider router (V4 Stage E, spec §7/§28 and V4 §9).

Selection for a capability set:
1. Candidates = providers known to the registry (studio provider config + `routing.providers` overrides).
2. A provider is *ineligible* when: kill-switched (`model_disabled:<name>`), missing a needed capability,
   unpriced (no verified USD/s), unhealthy (failure rate over the window above the limit, once enough runs
   exist), or below the quality floor (benchmark quality, when measured).
3. Standard tier: the cheapest eligible provider (ties: higher quality). Premium tier: highest quality.
   The next eligible provider is the fallback.
Health and quality are measured, not configured: failure rate comes from `model_runs`, quality from blind
benchmark reviews (see benchmarks.py). With the `routing` flag off, the configured provider is used exactly as
before, and this module only reports health."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..models import FeatureFlag, ModelRun

FLAG = "routing"
DEFAULTS = {"max_failure_rate": 0.3, "failure_window_hours": 24, "min_runs_for_health": 10, "min_quality": 0.0,
            "providers": {}}


def config(db: Session) -> tuple[bool, dict]:
    f = db.get(FeatureFlag, FLAG)
    return bool(f and f.enabled), {**DEFAULTS, **((f.value or {}) if f else {})}


def disabled_models(db: Session) -> set[str]:
    rows = db.execute(select(FeatureFlag.key).where(FeatureFlag.key.like("model_disabled:%"),
                                                    FeatureFlag.enabled.is_(True))).scalars()
    return {k.split(":", 1)[1] for k in rows}


def health(db: Session, providers: list[str], window_h: int) -> dict[str, dict]:
    since = datetime.now(UTC) - timedelta(hours=window_h)
    rows = db.execute(select(ModelRun.model, func.count(),
                             func.sum(case((ModelRun.status == "failed", 1), else_=0)),
                             func.avg(func.extract("epoch", ModelRun.finished_at - ModelRun.started_at)))
                      .where(ModelRun.model.in_(providers), ModelRun.started_at >= since,
                             ModelRun.status.in_(("succeeded", "failed")))
                      .group_by(ModelRun.model)).all()
    out = {p: {"runs": 0, "failure_rate": None, "avg_seconds": None} for p in providers}
    for model, n, failed, avg_s in rows:
        out[model] = {"runs": int(n), "failure_rate": round(int(failed or 0) / int(n), 4) if n else None,
                      "avg_seconds": round(float(avg_s), 1) if avg_s is not None else None}
    return out


def registry(db: Session) -> dict[str, dict]:
    """provider -> {capabilities, usd_per_second, quality} merged from studio config and routing overrides."""
    from . import benchmarks, studio

    _, scfg = studio.config(db)
    _, rcfg = config(db)
    from . import model_registry

    gated = {m.provider_name: m for m in model_registry.deployed(db)}  # V5 Phase E: only approved+deployed
    names = set(scfg["provider_capabilities"]) | set(scfg["provider_usd_per_second"]) | set(rcfg["providers"]) \
        | set(gated)
    measured = benchmarks.provider_quality(db)
    out = {}
    for n in sorted(names):
        o = rcfg["providers"].get(n, {})
        if n in gated:
            o = {"capabilities": gated[n].capabilities, "usd_per_second": gated[n].usd_per_second, **o}
        out[n] = {"capabilities": sorted(set(o.get("capabilities", scfg["provider_capabilities"].get(n, [])))),
                  "usd_per_second": o.get("usd_per_second", scfg["provider_usd_per_second"].get(n)),
                  "quality": measured.get(n, o.get("quality")), "quality_source":
                      "benchmark" if n in measured else ("config" if "quality" in o else None)}
    return out


def choose(db: Session, needed: set[str], tier: str = "standard", user_id=None) -> dict:
    from . import studio

    enabled, rcfg = config(db)
    _, scfg = studio.config(db)
    reg = registry(db)
    dis = disabled_models(db)
    hl = health(db, list(reg), int(rcfg["failure_window_hours"]))
    cands = []
    for name, info in reg.items():
        reasons = []
        if name in dis:
            reasons.append("kill_switch")
        if needed - set(info["capabilities"]):
            reasons.append("missing:" + ",".join(sorted(needed - set(info["capabilities"]))))
        if info["usd_per_second"] is None:
            reasons.append("unpriced")
        h = hl[name]
        if h["runs"] >= int(rcfg["min_runs_for_health"]) and (h["failure_rate"] or 0) > float(rcfg["max_failure_rate"]):
            reasons.append("unhealthy")  # automatic suspension on failure rate (spec V4 §12)
        if info["quality"] is not None and info["quality"] < float(rcfg["min_quality"]):
            reasons.append("below_quality_floor")
        cands.append({"provider": name, "eligible": not reasons, "reasons": reasons, **info, **h})
    eligible = [c for c in cands if c["eligible"]]
    if tier == "premium":
        eligible.sort(key=lambda c: (-(c["quality"] if c["quality"] is not None else -1), c["usd_per_second"]))
    else:
        eligible.sort(key=lambda c: (c["usd_per_second"], -(c["quality"] if c["quality"] is not None else -1)))
    if not enabled:  # static routing (backward compatible): the configured provider, health reported only
        return {"adaptive": False, "provider": scfg["shot_provider"], "fallback": scfg.get("fallback_provider"),
                "reason": "configured", "candidates": cands}
    if not eligible:
        return {"adaptive": True, "provider": None, "fallback": None, "reason": "no_eligible_provider",
                "candidates": cands}
    route = {"adaptive": True, "provider": eligible[0]["provider"],
             "fallback": eligible[1]["provider"] if len(eligible) > 1 else None,
             "reason": "cheapest_eligible" if tier != "premium" else "best_quality", "candidates": cands}
    from . import policies  # V5 Phase D: a live learning policy may explore among the eligible providers

    return policies.apply(db, route, user_id, needed)
