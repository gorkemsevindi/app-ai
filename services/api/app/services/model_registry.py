"""Model registry governance (Master Spec V5, Phase E — optional fine-tuning).

This module does not train anything. It is the gate a self-hosted or fine-tuned artifact must pass before the
router may use it (V5 §6: "commercially usable open-weight models with documented licenses, rights-cleared
datasets, evaluation, model cards and human approval"):

1. Registration requires: licence id + verified commercial use, a rights-cleared external dataset reference,
   no third-party model outputs in the training data unless a contract permits it, no user content unless a
   documented consented source exists (none does today), and a complete model card.
2. Evaluation: a benchmark run with blind reviews for this provider; quality must not regress more than
   `max_quality_regression` below the best currently deployed/benchmarked provider.
3. Two different admins approve (the registrant may be one of them). Only then can it be deployed; it then appears
   in the router registry like any provider (kill switch, health, quality floor still apply). Retire removes it."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import ApiError, not_found
from ..models import BenchmarkResult, ModelRegistryVersion, User

CARD_FIELDS = ("intended_use", "limitations", "training_data", "evaluation", "ethical_considerations")


class Attestations(BaseModel):
    license_verified: bool
    commercial_use_allowed: bool
    license_evidence: str = Field(min_length=3, max_length=300)
    dataset_ref: str = Field(min_length=3, max_length=300)
    dataset_source: str = Field(pattern=r"^(licensed_external|owned)$")
    dataset_rights_cleared: bool
    includes_user_content: bool = False
    includes_third_party_model_outputs: bool = False
    third_party_contract_ref: str | None = Field(default=None, max_length=300)


class RegisterIn(BaseModel):
    provider_name: str = Field(pattern=r"^[a-z0-9_]{3,60}$")
    base_model: str = Field(min_length=2, max_length=120)
    license: str = Field(min_length=2, max_length=120)
    attestations: Attestations
    model_card: dict
    capabilities: list[str] = Field(min_length=1, max_length=20)
    usd_per_second: float = Field(ge=0)


def register(db: Session, admin: User, raw: dict) -> ModelRegistryVersion:
    try:
        body = RegisterIn.model_validate(raw)
    except ValidationError as e:
        raise ApiError(422, "bad_registration", "incomplete registration",
                       {"errors": [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()[:10]]}) from e
    a = body.attestations
    problems = []
    if not (a.license_verified and a.commercial_use_allowed):
        problems.append("license_not_commercial")
    if not a.dataset_rights_cleared:
        problems.append("dataset_rights_not_cleared")
    if a.includes_user_content:
        problems.append("user_content_not_permitted")  # no consented training corpus exists (V5 §5)
    if a.includes_third_party_model_outputs and not a.third_party_contract_ref:
        problems.append("third_party_outputs_without_contract")
    missing = [f for f in CARD_FIELDS if not str(body.model_card.get(f, "")).strip()]
    if missing:
        problems.append("model_card_incomplete:" + ",".join(missing))
    if problems:
        raise ApiError(422, "governance_failed", "the artifact does not meet the registration requirements",
                       {"problems": problems})
    n = db.execute(select(func.coalesce(func.max(ModelRegistryVersion.version), 0))
                   .where(ModelRegistryVersion.provider_name == body.provider_name)).scalar_one()
    m = ModelRegistryVersion(provider_name=body.provider_name, version=n + 1, base_model=body.base_model,
                             license=body.license, attestations=a.model_dump(), model_card=body.model_card,
                             capabilities=sorted({c.upper() for c in body.capabilities}),
                             usd_per_second=body.usd_per_second, status="registered", created_by=admin.id)
    db.add(m)
    db.flush()
    return m


def get(db: Session, model_id: uuid.UUID) -> ModelRegistryVersion:
    m = db.get(ModelRegistryVersion, model_id)
    if m is None:
        raise not_found("model")
    return m


def evaluate(db: Session, m: ModelRegistryVersion, run_id: uuid.UUID, min_reviews: int,
             max_quality_regression: float) -> dict:
    from . import benchmarks

    rows = db.execute(select(BenchmarkResult).where(BenchmarkResult.run_id == run_id,
                                                    BenchmarkResult.provider == m.provider_name,
                                                    BenchmarkResult.reviewed_at.is_not(None))).scalars().all()
    scores = [((r.adherence + r.quality) / 2 - 1) / 4 for r in rows]
    quality = round(sum(scores) / len(scores), 4) if scores else None
    others = {p: q for p, q in benchmarks.provider_quality(db).items() if p != m.provider_name}
    baseline = max(others.values()) if others else None
    ok = quality is not None and len(scores) >= min_reviews and (
        baseline is None or quality >= baseline - max_quality_regression)
    return {"run_id": str(run_id), "reviews": len(scores), "quality": quality, "baseline_quality": baseline,
            "passed": ok, "min_reviews": min_reviews, "max_quality_regression": max_quality_regression,
            "evaluated_at": datetime.now(UTC).isoformat()}


def approve(db: Session, admin: User, m: ModelRegistryVersion, note: str) -> ModelRegistryVersion:
    if m.status != "registered":
        raise ApiError(409, "bad_transition", f"{m.status} -> approved")
    if not (m.evaluation or {}).get("passed"):
        raise ApiError(409, "evaluation_required", "attach a passing blind-reviewed benchmark evaluation first")
    if any(a["by"] == str(admin.id) for a in m.approvals or []):
        raise ApiError(409, "already_approved", "a second, different admin must approve")
    m.approvals = [*(m.approvals or []), {"by": str(admin.id), "note": note, "at": datetime.now(UTC).isoformat()}]
    if len({a["by"] for a in m.approvals}) >= 2:
        m.status = "approved"
    db.flush()
    return m


def set_status(m: ModelRegistryVersion, to: str) -> None:
    allowed = {"deployed": {"approved"}, "retired": {"approved", "deployed", "registered"},
               "rejected": {"registered"}}
    if m.status not in allowed.get(to, set()):
        raise ApiError(409, "bad_transition", f"{m.status} -> {to}")
    m.status = to


def deployed(db: Session) -> list[ModelRegistryVersion]:
    """Latest deployed version per provider name."""
    rows = db.execute(select(ModelRegistryVersion).where(ModelRegistryVersion.status == "deployed")
                      .order_by(ModelRegistryVersion.version.desc())).scalars().all()
    seen, out = set(), []
    for m in rows:
        if m.provider_name not in seen:
            seen.add(m.provider_name)
            out.append(m)
    return out
