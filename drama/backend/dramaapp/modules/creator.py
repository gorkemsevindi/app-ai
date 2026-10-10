"""Creator economy (spec §5): analytics, earnings (pending vs available), payouts."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import creator_user
from ..errors import AppError
from ..models import (
    AuditEvent,
    CreatorProfile,
    GenerationJob,
    Payout,
    ProviderCall,
    Purchase,
    RevenueAllocation,
    Series,
    User,
    now,
)
from . import ledger
from .catalog import series_stats

router = APIRouter(prefix="/creator", tags=["creator"])


@router.get("/analytics")
def analytics(user: User = Depends(creator_user), db: Session = Depends(get_db)):
    out = []
    for s in db.scalars(select(Series).where(Series.creator_id == user.id)):
        st = series_stats(db, s.id)
        purchases = db.scalars(select(Purchase).where(Purchase.series_id == s.id)).all()
        allocs = db.scalars(select(RevenueAllocation).join(Purchase).where(Purchase.series_id == s.id)).all()
        buyers = {p.user_id for p in purchases if p.status == "verified"}
        out.append({
            "series_id": s.id, "title": s.title, "status": s.status, **st,
            "completion_rate": round(st["completions"] / st["views"], 3) if st["views"] else None,
            "episode_progression_rate": round(st["viewers_progressed"] / st["viewers"], 3) if st["viewers"] else None,
            "paywall_conversion": round(len(buyers) / st["viewers"], 3) if st["viewers"] else None,
            "purchases": sum(1 for p in purchases if p.status == "verified"),
            "refunds": sum(1 for p in purchases if p.status == "refunded"),
            "gross_minor": sum(a.gross_minor for a in allocs if not a.reversed),
            "net_distributable_minor": sum(a.net_minor for a in allocs if not a.reversed),
            "creator_share_minor": sum(a.creator_minor for a in allocs if not a.reversed),
        })
    return out


@router.get("/earnings")
def earnings(user: User = Depends(creator_user), db: Session = Depends(get_db)):
    s = get_settings()
    allocs = db.scalars(select(RevenueAllocation).where(RevenueAllocation.creator_id == user.id)
                        .order_by(RevenueAllocation.created_at.desc()).limit(100)).all()
    spend = db.scalar(select(func.coalesce(func.sum(ProviderCall.cost_usd_micros), 0)).where(
        ProviderCall.job_id.in_(select(GenerationJob.id).where(GenerationJob.owner_id == user.id))))
    return {
        **ledger.pending_vs_available(db, user.id, s.currency),
        "share_bps": s.creator_share_bps, "hold_days": s.earnings_hold_days, "min_payout_minor": s.min_payout_minor,
        "definition": "Creator share = share_bps × (gross − indirect tax − store/payment fees − refunds/chargebacks "
                      "− approved adjustments). Views alone do not generate earnings.",
        "credits_balance": ledger.credit_balance(db, user.id),
        "provider_cost_usd": round((spend or 0) / 1e6, 4),
        "allocations": [{"purchase_id": a.purchase_id, "gross_minor": a.gross_minor, "tax_minor": a.tax_minor,
                         "store_fee_minor": a.store_fee_minor, "net_minor": a.net_minor,
                         "creator_minor": a.creator_minor, "available_at": a.available_at, "reversed": a.reversed}
                        for a in allocs],
    }


class PayoutIn(BaseModel):
    amount_minor: int = Field(gt=0)


@router.post("/payouts", status_code=201)
def request_payout(body: PayoutIn, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    s = get_settings()
    cp = db.get(CreatorProfile, user.id)
    if not cp or cp.kyc_status != "verified" or cp.tax_form_status != "verified":
        raise AppError("payout.kyc_required", "Identity verification and tax form are required before payouts", 403)
    bal = ledger.pending_vs_available(db, user.id, s.currency)["available_minor"]
    if body.amount_minor < s.min_payout_minor:
        raise AppError("payout.below_minimum", "Below minimum payout", 400, minimum=s.min_payout_minor)
    if body.amount_minor > bal:
        raise AppError("payout.insufficient", "Amount exceeds available balance", 400, available=bal)
    p = Payout(creator_id=user.id, amount_minor=body.amount_minor, currency=s.currency)
    db.add(p)
    db.flush()
    ledger.post(db, idempotency_key=f"payout:{p.id}:request", kind="payout.request",
                entries=[(f"liability:creator:{user.id}:available", s.currency, body.amount_minor),
                         (f"liability:creator:{user.id}:payout_in_flight", s.currency, -body.amount_minor)])
    db.add(AuditEvent(actor_id=user.id, action="payout.requested", target_type="payout", target_id=p.id))
    db.commit()
    return payout_out(p)


@router.get("/payouts")
def list_payouts(user: User = Depends(creator_user), db: Session = Depends(get_db)):
    return [payout_out(p) for p in db.scalars(select(Payout).where(Payout.creator_id == user.id))]


def payout_out(p: Payout) -> dict:
    return {"id": p.id, "amount_minor": p.amount_minor, "currency": p.currency, "status": p.status, "rail": p.rail,
            "settled_at": p.settled_at, "requested_at": p.created_at}


def settle_payout(db: Session, p: Payout, actor_id: str, external_ref: str) -> Payout:
    if p.status != "requested":
        return p
    ledger.post(db, idempotency_key=f"payout:{p.id}:settle", kind="payout.settle",
                entries=[(f"liability:creator:{p.creator_id}:payout_in_flight", p.currency, p.amount_minor),
                         (f"cash:payout_rail:{p.rail}", p.currency, -p.amount_minor)])
    p.status, p.settled_at, p.external_ref = "settled", now(), external_ref
    from ..models import OutboxEvent
    db.add(OutboxEvent(type="PayoutSettled", dedup_key=f"PayoutSettled:{p.id}", payload={"payout_id": p.id}))
    db.add(AuditEvent(actor_id=actor_id, action="payout.settled", target_type="payout", target_id=p.id))
    db.commit()
    return p
