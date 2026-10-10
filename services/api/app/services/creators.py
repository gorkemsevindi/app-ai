"""Creator economy (V4 Stage D, spec §5/§6): creator profiles, versioned revenue policies, an append-only
earnings ledger, settlements with frozen snapshots, risk holds and creator analytics.

Money rules:
- Only *qualifying paid* consumption earns: the paid-bucket credits (purchased/subscription, production) that a
  settled job actually consumed, valued at the policy's `usd_per_paid_credit` (net of store fees/taxes).
  Promo, reward, adjustment, legacy and sandbox credits never create earnings.
- No active revenue policy => nothing accrues (rates are business decisions, never code defaults).
- Every money event is an idempotent append-only row (USD micros) carrying the policy version it used.
- Self-use never earns; template share vs referral commission follow the policy's precedence (no double pay
  unless `referral_stacking` is explicitly true).
- Refunds claw back: a support refund of a job, or a store refund of the credits a job was paid with, writes
  negative rows (proportional for partial funding). Paid-out money stays offset against future earnings.
- Earnings become payable after `settlement_delay_days`; settlements snapshot rows + policies, are held on
  risk signals, and are paid only to verified payout accounts."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import ApiError, not_found
from ..models import (
    Attribution,
    CreatorEarning,
    CreatorProfile,
    CreatorRiskHold,
    CreditAllocation,
    CreditLedger,
    CreditLot,
    GenerationJob,
    LedgerReason,
    Report,
    RevenuePolicy,
    Settlement,
    SettlementItem,
    Template,
    User,
)

CREATOR_TERMS = "creator-terms-v1"
PAID_BUCKETS = {"purchased", "subscription"}
MICROS = 1_000_000


def _now() -> datetime:
    return datetime.now(UTC)


class RiskRules(BaseModel):
    max_refund_rate: float = Field(default=0.2, ge=0, le=1)
    max_payer_concentration: float = Field(default=0.5, ge=0, le=1)
    min_events_for_concentration: int = Field(default=10, ge=1)
    max_open_reports: int = Field(default=3, ge=0)


class PolicyConfig(BaseModel):
    creator_share_rate: float = Field(ge=0, le=1)
    referral_rate: float = Field(default=0.0, ge=0, le=1)
    actor_share_rate: float = Field(default=0.0, ge=0, le=1)
    referral_stacking: bool = False
    precedence: str = Field(default="template_first", pattern=r"^(template_first|referral_first)$")
    usd_per_paid_credit: float = Field(gt=0, description="net revenue per paid credit after store fees/taxes")
    settlement_delay_days: int = Field(default=30, ge=0, le=365)
    min_payout_micros: int = Field(default=50 * MICROS, ge=0)
    risk: RiskRules = Field(default_factory=RiskRules)


# ---------------------------------------------------------------- profiles

def get_profile(db: Session, user_id: uuid.UUID) -> CreatorProfile | None:
    return db.get(CreatorProfile, user_id)


def require_profile(db: Session, user: User) -> CreatorProfile:
    p = get_profile(db, user.id)
    if p is None:
        raise ApiError(403, "creator_required", "create a creator profile first")
    if p.status != "active":
        raise ApiError(403, "creator_suspended", "this creator account is suspended")
    return p


def create_profile(db: Session, user: User, handle: str, display_name: str, bio: str, payout_country: str | None,
                   accept_terms: bool) -> CreatorProfile:
    from . import moderation

    if not accept_terms:
        raise ApiError(422, "terms_required", "accept the creator terms")
    if user.age_confirmed_at is None:
        raise ApiError(403, "not_eligible", "creators must be adults")
    for t in (handle, display_name, bio):
        if not moderation.check_text(t).allowed:
            raise ApiError(422, "content_blocked", "this text is not allowed")
    if get_profile(db, user.id) is not None:
        raise ApiError(409, "already_creator", "creator profile exists")
    p = CreatorProfile(user_id=user.id, handle=handle.lower(), display_name=display_name, bio=bio,
                       payout_country=payout_country.upper() if payout_country else None, status="active",
                       terms_version=CREATOR_TERMS, terms_accepted_at=_now(), payout_status="none")
    try:
        with db.begin_nested():
            db.add(p)
            db.flush()
    except IntegrityError as e:
        raise ApiError(409, "handle_taken", "this handle is taken") from e
    return p


# ---------------------------------------------------------------- policies

def active_policy(db: Session, at: datetime | None = None) -> RevenuePolicy | None:
    return db.execute(select(RevenuePolicy).where(RevenuePolicy.effective_from <= (at or _now()))
                      .order_by(RevenuePolicy.effective_from.desc(), RevenuePolicy.version.desc()).limit(1)
                      ).scalar_one_or_none()


def create_policy(db: Session, admin: User, config: dict, effective_from: datetime | None) -> RevenuePolicy:
    try:
        cfg = PolicyConfig.model_validate(config)
    except ValidationError as e:
        raise ApiError(422, "bad_policy", "invalid revenue policy",
                       {"errors": [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()[:10]]}) from e
    eff = effective_from or _now()
    if eff < _now() - timedelta(minutes=5):
        raise ApiError(422, "bad_policy", "policies cannot start in the past (history is immutable)")
    n = db.execute(select(func.coalesce(func.max(RevenuePolicy.version), 0))).scalar_one()
    p = RevenuePolicy(version=n + 1, config=cfg.model_dump(), effective_from=eff, created_by=admin.id)
    db.add(p)
    db.flush()
    return p


# ---------------------------------------------------------------- accrual

def _paid_credits_by_lot(db: Session, job_id: uuid.UUID) -> dict[uuid.UUID, int]:
    rows = db.execute(select(CreditAllocation.lot_id, CreditLot.bucket, func.sum(-CreditAllocation.amount))
                      .join(CreditLedger, CreditLedger.id == CreditAllocation.ledger_id)
                      .join(CreditLot, CreditLot.id == CreditAllocation.lot_id)
                      .where(CreditLedger.idempotency_key == f"gen:{job_id}")
                      .group_by(CreditAllocation.lot_id, CreditLot.bucket)).all()
    return {lot: int(n) for lot, bucket, n in rows if bucket in PAID_BUCKETS and n > 0}


def _accrue(db: Session, creator_id: uuid.UUID, kind: str, amount: int, gross: int, policy: RevenuePolicy,
            key: str, cfg: PolicyConfig, **refs) -> CreatorEarning | None:
    if amount <= 0:
        return None
    existing = db.execute(select(CreatorEarning).where(CreatorEarning.idempotency_key == key)).scalar_one_or_none()
    if existing is not None:
        return existing
    e = CreatorEarning(creator_id=creator_id, kind=kind, amount_micros=amount, gross_basis_micros=gross,
                       policy_id=policy.id, policy_version=policy.version, idempotency_key=key,
                       available_at=_now() + timedelta(days=cfg.settlement_delay_days), **refs)
    db.add(e)
    db.flush()
    return e


def _eligible(db: Session, user_id: uuid.UUID | None) -> bool:
    p = db.get(CreatorProfile, user_id) if user_id else None
    return p is not None and p.status == "active"


def on_job_settled(db: Session, job: GenerationJob) -> list[CreatorEarning]:
    """Called once a job's charge is confirmed. Idempotent per job."""
    policy = active_policy(db)
    if policy is None or not job.credit_cost:
        return []
    cfg = PolicyConfig.model_validate(policy.config)
    paid = sum(_paid_credits_by_lot(db, job.id).values())
    if paid <= 0:
        return []
    gross = int(round(paid * cfg.usd_per_paid_credit * MICROS))
    payer = job.user_id
    out: list[CreatorEarning] = []
    t = db.get(Template, job.template_id) if job.template_id else None
    creator = t.creator_id if t is not None and t.creator_id != payer and _eligible(db, t.creator_id) else None
    attr = db.execute(select(Attribution).where(Attribution.user_id == payer, Attribution.expires_at > _now())
                      ).scalar_one_or_none()
    referrer = attr.referrer_id if attr is not None and attr.referrer_id not in (None, payer) and \
        _eligible(db, attr.referrer_id) else None
    pay_template = creator is not None and cfg.creator_share_rate > 0
    pay_referral = referrer is not None and cfg.referral_rate > 0
    if pay_template and pay_referral and not cfg.referral_stacking:
        if cfg.precedence == "template_first":
            pay_referral = False
        else:
            pay_template = False
    refs = {"job_id": job.id, "template_id": job.template_id, "payer_id": payer}
    if pay_template:
        out.append(_accrue(db, creator, "template_share", int(gross * cfg.creator_share_rate), gross, policy,
                           f"tshare:{job.id}", cfg, **refs))
    if pay_referral:
        out.append(_accrue(db, referrer, "referral_commission", int(gross * cfg.referral_rate), gross, policy,
                           f"ref:{job.id}", cfg, attribution_id=attr.id, **refs))
    return [e for e in out if e is not None]


def _claw(db: Session, orig: CreatorEarning, fraction: float, key: str, note: str, actor: uuid.UUID | None) -> None:
    amount = int(orig.amount_micros * min(1.0, fraction))
    if amount <= 0 or db.execute(select(CreatorEarning.id).where(CreatorEarning.idempotency_key == key)).first():
        return
    db.add(CreatorEarning(creator_id=orig.creator_id, kind="clawback", amount_micros=-amount,
                          gross_basis_micros=-int(orig.gross_basis_micros * min(1.0, fraction)),
                          policy_id=orig.policy_id, policy_version=orig.policy_version, job_id=orig.job_id,
                          template_id=orig.template_id, attribution_id=orig.attribution_id,
                          license_id=orig.license_id, payer_id=orig.payer_id, idempotency_key=key,
                          available_at=_now(), actor_id=actor, note=note[:300]))
    db.flush()


def _accruals_for_job(db: Session, job_id: uuid.UUID) -> list[CreatorEarning]:
    return list(db.execute(select(CreatorEarning).where(
        CreatorEarning.job_id == job_id, CreatorEarning.kind.in_(("template_share", "referral_commission")))
    ).scalars())


def on_job_refunded(db: Session, job: GenerationJob, why: str, actor: uuid.UUID | None = None) -> None:
    for e in _accruals_for_job(db, job.id):
        _claw(db, e, 1.0, f"claw:{e.idempotency_key}", f"job refunded: {why}", actor)


def on_purchase_reversed(db: Session, purchase_key: str, why: str) -> None:
    """Store refund/chargeback of a credit purchase: claw back earnings of the jobs those credits paid for,
    in proportion to how much of each job's paid funding came from the reversed purchase."""
    lot = db.execute(select(CreditLot).join(CreditLedger, CreditLedger.id == CreditLot.source_ledger_id)
                     .where(CreditLedger.idempotency_key == purchase_key)).scalar_one_or_none()
    if lot is None:
        return
    rows = db.execute(select(CreditLedger.ref_id, func.sum(-CreditAllocation.amount))
                      .join(CreditAllocation, CreditAllocation.ledger_id == CreditLedger.id)
                      .where(CreditAllocation.lot_id == lot.id, CreditLedger.reason == LedgerReason.generation_debit)
                      .group_by(CreditLedger.ref_id)).all()
    for job_ref, from_lot in rows:
        job_id = uuid.UUID(job_ref)
        total_paid = sum(_paid_credits_by_lot(db, job_id).values())
        if total_paid <= 0 or from_lot <= 0:
            continue
        for e in _accruals_for_job(db, job_id):
            _claw(db, e, from_lot / total_paid, f"claw:{e.idempotency_key}:{purchase_key}",
                  f"purchase reversed: {why}", None)


# ---------------------------------------------------------------- balances, settlements, risk

def _unsettled(db: Session, creator_id: uuid.UUID, until: datetime | None = None) -> list[CreatorEarning]:
    settled = select(SettlementItem.earning_id).join(Settlement, Settlement.id == SettlementItem.settlement_id) \
        .where(Settlement.status != "cancelled")
    q = select(CreatorEarning).where(CreatorEarning.creator_id == creator_id, CreatorEarning.kind != "payout",
                                     CreatorEarning.id.not_in(settled))
    if until is not None:
        q = q.where(CreatorEarning.available_at <= until)
    return list(db.execute(q.order_by(CreatorEarning.id)).scalars())


def balances(db: Session, creator_id: uuid.UUID) -> dict:
    total = db.execute(select(func.coalesce(func.sum(CreatorEarning.amount_micros), 0))
                       .where(CreatorEarning.creator_id == creator_id)).scalar_one()
    now = _now()
    unsettled = _unsettled(db, creator_id)
    available = sum(e.amount_micros for e in unsettled if e.available_at <= now)
    pending = sum(e.amount_micros for e in unsettled if e.available_at > now)
    in_settlement = db.execute(select(func.coalesce(func.sum(Settlement.amount_micros), 0)).where(
        Settlement.creator_id == creator_id, Settlement.status.in_(("pending", "held", "approved")))).scalar_one()
    paid = -db.execute(select(func.coalesce(func.sum(CreatorEarning.amount_micros), 0)).where(
        CreatorEarning.creator_id == creator_id, CreatorEarning.kind == "payout")).scalar_one()
    return {"currency": "USD", "balance_micros": int(total), "available_micros": int(available),
            "pending_micros": int(pending), "in_settlement_micros": int(in_settlement), "paid_micros": int(paid)}


def risk_signals(db: Session, creator_id: uuid.UUID, rules: RiskRules) -> dict:
    since = _now() - timedelta(days=90)
    rows = db.execute(select(CreatorEarning).where(CreatorEarning.creator_id == creator_id,
                                                   CreatorEarning.created_at >= since)).scalars().all()
    accruals = [e for e in rows if e.amount_micros > 0 and e.kind != "adjustment"]
    claws = [e for e in rows if e.kind == "clawback"]
    by_payer: dict = defaultdict(int)
    for e in accruals:
        by_payer[e.payer_id] += e.gross_basis_micros
    gross = sum(by_payer.values())
    conc = max(by_payer.values()) / gross if gross else 0.0
    reports = db.execute(select(func.count()).select_from(Report).join(Template, Template.id == Report.template_id)
                         .where(Template.creator_id == creator_id, Report.status == "open")).scalar_one()
    open_hold = db.execute(select(CreatorRiskHold.id).where(CreatorRiskHold.creator_id == creator_id,
                                                            CreatorRiskHold.status == "open")).first()
    reasons = []
    refund_rate = len(claws) / len(accruals) if accruals else 0.0
    if accruals and refund_rate > rules.max_refund_rate:
        reasons.append("refund_rate")
    if len(accruals) >= rules.min_events_for_concentration and conc > rules.max_payer_concentration:
        reasons.append("payer_concentration")
    if reports > rules.max_open_reports:
        reasons.append("open_reports")
    if open_hold:
        reasons.append("open_risk_hold")
    return {"refund_rate": round(refund_rate, 4), "payer_concentration": round(conc, 4), "open_reports": reports,
            "accruals_90d": len(accruals), "reasons": reasons}


def run_settlements(db: Session, period_end: datetime | None = None) -> list[Settlement]:
    """Batch every creator's payable, unsettled earnings (idempotent: settled rows are never batched twice)."""
    policy = active_policy(db)
    if policy is None:
        raise ApiError(409, "no_policy", "create a revenue policy first")
    cfg = PolicyConfig.model_validate(policy.config)
    until = min(period_end or _now(), _now())
    creators = db.execute(select(CreatorEarning.creator_id).where(CreatorEarning.available_at <= until)
                          .distinct()).scalars().all()
    out = []
    for cid in creators:
        db.execute(select(User.id).where(User.id == cid).with_for_update())  # serialize per creator
        rows = _unsettled(db, cid, until)
        amount = sum(e.amount_micros for e in rows)
        if not rows or amount < cfg.min_payout_micros or amount <= 0:
            continue
        sig = risk_signals(db, cid, cfg.risk)
        prof = db.get(CreatorProfile, cid)
        held = bool(sig["reasons"]) or prof is None or prof.status != "active"
        s = Settlement(creator_id=cid, period_end=until, amount_micros=amount, status="held" if held else "pending",
                       risk=sig, snapshot={
                           "earnings": [{"id": e.id, "kind": e.kind, "amount_micros": e.amount_micros,
                                         "policy_version": e.policy_version,
                                         "job_id": str(e.job_id) if e.job_id else None} for e in rows],
                           "policies": sorted({e.policy_version for e in rows if e.policy_version}),
                           "settlement_policy_version": policy.version, "created_at": _now().isoformat()})
        db.add(s)
        db.flush()
        for e in rows:
            db.add(SettlementItem(earning_id=e.id, settlement_id=s.id))
        if held and sig["reasons"] and "open_risk_hold" not in sig["reasons"]:
            db.add(CreatorRiskHold(creator_id=cid, reason=",".join(sig["reasons"]), signals=sig))
        db.flush()
        out.append(s)
    return out


def get_settlement(db: Session, settlement_id: uuid.UUID) -> Settlement:
    s = db.execute(select(Settlement).where(Settlement.id == settlement_id).with_for_update()).scalar_one_or_none()
    if s is None:
        raise not_found("settlement")
    return s


def transition_settlement(db: Session, admin: User, s: Settlement, action: str, external_ref: str | None,
                          note: str | None) -> Settlement:
    allowed = {"approve": ({"pending", "held"}, "approved"), "hold": ({"pending", "approved"}, "held"),
               "cancel": ({"pending", "held", "approved"}, "cancelled"), "pay": ({"approved"}, "paid")}
    if action not in allowed:
        raise ApiError(422, "bad_action", "approve, hold, cancel or pay")
    src, dst = allowed[action]
    if s.status not in src:
        raise ApiError(409, "bad_transition", f"{s.status} -> {dst}")
    if action == "pay":
        prof = db.get(CreatorProfile, s.creator_id)
        if prof is None or prof.payout_status != "verified":
            raise ApiError(409, "payout_not_verified", "the creator's payout account is not verified")
        if not external_ref:
            raise ApiError(422, "external_ref_required", "record the payout provider reference")
        db.add(CreatorEarning(creator_id=s.creator_id, kind="payout", amount_micros=-s.amount_micros,
                              settlement_id=s.id, idempotency_key=f"payout:{s.id}", available_at=_now(),
                              actor_id=admin.id, note=(note or "")[:300]))
        s.external_ref, s.paid_at = external_ref, _now()
    s.status = dst
    s.risk = {**(s.risk or {}), "last_action": {"action": action, "by": str(admin.id), "note": note,
                                                "at": _now().isoformat()}}
    db.flush()
    return s


# ---------------------------------------------------------------- analytics

def analytics(db: Session, creator_id: uuid.UUID, days: int) -> dict:
    from ..models import JobStatus, TemplateMetricDaily

    since = _now() - timedelta(days=days)
    templates = db.execute(select(Template).where(Template.creator_id == creator_id)).scalars().all()
    ids = [t.id for t in templates]
    metrics = defaultdict(lambda: defaultdict(int))
    if ids:
        for m in db.execute(select(TemplateMetricDaily).where(TemplateMetricDaily.template_id.in_(ids),
                                                              TemplateMetricDaily.day >= since.date())).scalars():
            for f in ("opens", "shares", "generations", "successes", "failures", "reports"):
                metrics[m.template_id][f] += getattr(m, f)
    jobs = defaultdict(lambda: {"generations": 0, "successful": 0})
    if ids:
        for tid, st, n in db.execute(select(GenerationJob.template_id, GenerationJob.status, func.count())
                                     .where(GenerationJob.template_id.in_(ids), GenerationJob.created_at >= since)
                                     .group_by(GenerationJob.template_id, GenerationJob.status)).all():
            jobs[tid]["generations"] += n
            if st == JobStatus.completed:
                jobs[tid]["successful"] += n
    earn = defaultdict(lambda: {"paid_uses": 0, "gross_micros": 0, "earnings_micros": 0})
    q = select(CreatorEarning).where(CreatorEarning.creator_id == creator_id, CreatorEarning.created_at >= since,
                                     CreatorEarning.kind.in_(("template_share", "clawback")))
    for e in db.execute(q).scalars():
        if e.kind == "clawback" and not e.idempotency_key.startswith("claw:tshare"):
            continue  # referral clawbacks don't belong to the template's earnings
        row = earn[e.template_id]
        row["paid_uses"] += 1 if e.kind == "template_share" else 0
        row["gross_micros"] += e.gross_basis_micros
        row["earnings_micros"] += e.amount_micros
    out = []
    for t in templates:
        j, e, m = jobs[t.id], earn[t.id], metrics[t.id]
        out.append({"template_id": str(t.id), "title": t.title, "moderation_status": t.moderation_status,
                    "visibility": t.visibility, "views": m["opens"], "shares": m["shares"],
                    "generations": j["generations"], "successful_generations": j["successful"],
                    "paid_qualifying_uses": e["paid_uses"],
                    "conversion": round(e["paid_uses"] / j["generations"], 4) if j["generations"] else None,
                    "gross_attributable_micros": e["gross_micros"],
                    "platform_deductions_micros": e["gross_micros"] - e["earnings_micros"],
                    "creator_earnings_micros": e["earnings_micros"], "reports": m["reports"]})
    referral = db.execute(select(func.coalesce(func.sum(CreatorEarning.amount_micros), 0)).where(
        CreatorEarning.creator_id == creator_id, CreatorEarning.kind == "referral_commission",
        CreatorEarning.created_at >= since)).scalar_one()
    return {"days": days, "templates": out, "referral_earnings_micros": int(referral),
            "balances": balances(db, creator_id)}
