"""Credit ledger. Rules:
- append-only (DB trigger rejects UPDATE/DELETE on credit_ledger, credit_lots, credit_allocations);
- every entry has a globally unique idempotency key -> retries never double-apply;
- writes for one user are serialized with a row lock on users -> no double spend;
- balance_after is a denormalized running total, verifiable against SUM(delta) (see reconcile_user).
Callers own the transaction (commit happens in the caller) so a debit and the
job row it pays for commit atomically.

Buckets (V4 Stage A): every positive entry opens a `credit_lot` in a bucket (promo, subscription,
purchased, reward, adjustment, legacy) with its own optional expiry; every negative entry records which
lots it consumed in `credit_allocations`. A lot's remaining amount is derived from those append-only rows.
Buckets with different rules are never merged. Expired lots are swept with an `expire` entry the next
time the user's credits are written, so the ledger total always equals the sum of live lots.

Generation billing maps onto the existing events: `generation_debit` = reserve (before dispatch),
`generation_settle` = confirm the charge on billable completion (0 delta, records it for creator
earnings), `refund` = release back to the exact lots the reservation consumed."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import ApiError
from ..models import CreditAllocation, CreditLedger, CreditLot, FeatureFlag, LedgerReason, User

BUCKETS = ("promo", "subscription", "purchased", "reward", "adjustment", "legacy")
BUCKET_FOR_REASON = {
    LedgerReason.signup_bonus: "promo",
    LedgerReason.promo: "promo",
    LedgerReason.purchase: "purchased",
    LedgerReason.subscription_grant: "subscription",
    LedgerReason.admin_adjust: "adjustment",
    LedgerReason.refund: "legacy",  # only when the original debit predates buckets
}
# Lots that expire sooner are always used first; ties use this order (remote config `credits.consume_order`).
DEFAULT_CONSUME_ORDER = ["promo", "reward", "subscription", "adjustment", "legacy", "purchased"]


def _now() -> datetime:
    return datetime.now(UTC)


def lock_user(db: Session, user_id: uuid.UUID) -> User:
    user = db.execute(select(User).where(User.id == user_id).with_for_update()).scalar_one()
    return user


def balance(db: Session, user_id: uuid.UUID) -> int:
    last = db.execute(
        select(CreditLedger.balance_after).where(CreditLedger.user_id == user_id)
        .order_by(CreditLedger.id.desc()).limit(1)
    ).scalar_one_or_none()
    return last or 0


def _consume_order(db: Session) -> list[str]:
    f = db.get(FeatureFlag, "credits")
    order = list((f.value or {}).get("consume_order", [])) if f else []
    return [b for b in order if b in BUCKETS] + [b for b in DEFAULT_CONSUME_ORDER if b not in order]


def lots(db: Session, user_id: uuid.UUID, include_expired: bool = False) -> list[tuple[CreditLot, int]]:
    """(lot, remaining) for lots with something left."""
    used = (select(CreditAllocation.lot_id, func.sum(CreditAllocation.amount).label("s"))
            .group_by(CreditAllocation.lot_id).subquery())
    rows = db.execute(select(CreditLot, (CreditLot.granted + func.coalesce(used.c.s, 0)).label("rem"))
                      .outerjoin(used, used.c.lot_id == CreditLot.id)
                      .where(CreditLot.user_id == user_id)).all()
    now = _now()
    return [(lot, int(rem)) for lot, rem in rows
            if rem > 0 and (include_expired or lot.expires_at is None or lot.expires_at > now)]


def available(db: Session, user_id: uuid.UUID) -> int:
    return sum(r for _, r in lots(db, user_id))


def buckets(db: Session, user_id: uuid.UUID) -> list[dict]:
    out: dict[str, dict] = {}
    for lot, rem in lots(db, user_id):
        b = out.setdefault(lot.bucket, {"bucket": lot.bucket, "remaining": 0, "next_expiry": None,
                                        "next_expiry_amount": 0})
        b["remaining"] += rem
        if lot.expires_at is not None:
            if b["next_expiry"] is None or lot.expires_at < b["next_expiry"]:
                b["next_expiry"], b["next_expiry_amount"] = lot.expires_at, rem
            elif lot.expires_at == b["next_expiry"]:
                b["next_expiry_amount"] += rem
    order = {k: i for i, k in enumerate(BUCKETS)}
    return [{**b, "next_expiry": b["next_expiry"].isoformat() if b["next_expiry"] else None}
            for b in sorted(out.values(), key=lambda b: order[b["bucket"]])]


def _insert(db: Session, user_id: uuid.UUID, delta: int, reason: LedgerReason, key: str,
            allocations: list[tuple[uuid.UUID, int]], **meta) -> CreditLedger:
    new_balance = balance(db, user_id) + delta
    entry = CreditLedger(user_id=user_id, delta=delta, balance_after=new_balance, reason=reason,
                         idempotency_key=key, **meta)
    db.add(entry)
    db.flush()
    for lot_id, amount in allocations:
        if amount:
            db.add(CreditAllocation(ledger_id=entry.id, lot_id=lot_id, amount=amount))
    db.flush()
    return entry


def _sweep_expired(db: Session, user_id: uuid.UUID) -> None:
    now = _now()
    for lot, rem in lots(db, user_id, include_expired=True):
        if lot.expires_at is not None and lot.expires_at <= now and rem > 0:
            _insert(db, user_id, -rem, LedgerReason.expire, f"expire:{lot.id}", [(lot.id, -rem)],
                    ref_type="credit_lot", ref_id=str(lot.id), note=f"bucket={lot.bucket}")


def _plan_consumption(db: Session, user_id: uuid.UUID, amount: int, prefer_source_key: str | None
                      ) -> list[tuple[uuid.UUID, int]]:
    order = {b: i for i, b in enumerate(_consume_order(db))}
    live = lots(db, user_id)
    preferred_ledger = None
    if prefer_source_key:
        preferred_ledger = db.execute(select(CreditLedger.id).where(
            CreditLedger.idempotency_key == prefer_source_key)).scalar_one_or_none()
    far = datetime.max.replace(tzinfo=UTC)
    live.sort(key=lambda lr: (lr[0].source_ledger_id != preferred_ledger, lr[0].expires_at or far,
                              order.get(lr[0].bucket, 99), lr[0].created_at, str(lr[0].id)))
    plan, left = [], amount
    for lot, rem in live:
        if left == 0:
            break
        take = min(rem, left)
        plan.append((lot.id, -take))
        left -= take
    if left:
        raise RuntimeError(f"credit lots out of sync with ledger for user {user_id}: short by {left}")
    return plan


def apply(
    db: Session,
    user_id: uuid.UUID,
    delta: int,
    reason: LedgerReason,
    idempotency_key: str,
    ref_type: str | None = None,
    ref_id: str | None = None,
    actor_id: uuid.UUID | None = None,
    note: str | None = None,
    allow_negative: bool = False,
    *,
    bucket: str | None = None,
    expires_at: datetime | None = None,
    reverse_of: str | None = None,
    prefer_source_key: str | None = None,
) -> CreditLedger:
    """Apply a ledger entry exactly once. Returns the (new or pre-existing) entry.

    Positive entries open a lot in `bucket` (default by reason) unless `reverse_of` names an earlier
    entry whose consumed lots get the credits back. Negative entries consume live lots."""
    existing = db.execute(
        select(CreditLedger).where(CreditLedger.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        clamped = existing.reason == LedgerReason.purchase_reversal and "shortfall=" in (existing.note or "")
        if existing.user_id != user_id or (existing.delta != delta and not clamped):
            raise ApiError(409, "idempotency_conflict", "idempotency key reused with different parameters")
        return existing

    lock_user(db, user_id)
    # Re-check after acquiring the lock: a concurrent tx may have inserted it.
    existing = db.execute(
        select(CreditLedger).where(CreditLedger.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    if bucket is not None and bucket not in BUCKETS:
        raise ValueError(f"unknown bucket {bucket}")
    _sweep_expired(db, user_id)

    current = balance(db, user_id)
    new_balance = current + delta
    if new_balance < 0:
        if not allow_negative:
            raise ApiError(402, "insufficient_credits", "not enough credits",
                           {"balance": current, "required": -delta})
        # Purchase reversals (refund/chargeback) may exceed the remaining balance: clamp at 0 and
        # record the shortfall so finance can see it.
        note = f"{note or ''} shortfall={-new_balance}".strip()
        delta = -current

    meta = {"ref_type": ref_type, "ref_id": ref_id, "actor_id": actor_id, "note": note}
    if delta < 0:
        return _insert(db, user_id, delta, reason, idempotency_key,
                       _plan_consumption(db, user_id, -delta, prefer_source_key), **meta)
    if delta == 0:
        return _insert(db, user_id, 0, reason, idempotency_key, [], **meta)

    if reverse_of is not None:
        src = db.execute(select(CreditLedger).where(CreditLedger.idempotency_key == reverse_of)
                         ).scalar_one_or_none()
        allocs = db.execute(select(CreditAllocation).where(CreditAllocation.ledger_id == src.id)
                            ).scalars().all() if src is not None else []
        if allocs and -sum(a.amount for a in allocs) == delta:
            return _insert(db, user_id, delta, reason, idempotency_key,
                           [(a.lot_id, -a.amount) for a in allocs], **meta)
    entry = _insert(db, user_id, delta, reason, idempotency_key, [], **meta)
    db.add(CreditLot(user_id=user_id, bucket=bucket or BUCKET_FOR_REASON.get(reason, "adjustment"),
                     source_ledger_id=entry.id, granted=delta, expires_at=expires_at))
    db.flush()
    return entry


def sweep_expired(db: Session, user_id: uuid.UUID) -> None:
    lock_user(db, user_id)
    _sweep_expired(db, user_id)


# ---------------------------------------------------------------- generation billing

def reserve(db: Session, job) -> None:
    """Reserve the quoted price before the job can be dispatched (same tx as the job row)."""
    if job.credit_cost:
        apply(db, job.user_id, -job.credit_cost, LedgerReason.generation_debit, f"gen:{job.id}",
              ref_type="generation_job", ref_id=str(job.id))
        job.billing_state = "reserved"


def settle(db: Session, job) -> None:
    """Billable completion: confirm the reservation. Idempotent."""
    if job.credit_cost and job.billing_state == "reserved":
        apply(db, job.user_id, 0, LedgerReason.generation_settle, f"settle:{job.id}",
              ref_type="generation_job", ref_id=str(job.id), note=f"charged={job.credit_cost}")
        job.billing_state = "settled"


def release(db: Session, job, why: str) -> None:
    """Failure/cancel/blocked output: give the reserved credits back to the lots they came from."""
    if job.refunded or job.credit_cost == 0:
        return
    apply(db, job.user_id, job.credit_cost, LedgerReason.refund, f"refund:{job.id}",
          ref_type="generation_job", ref_id=str(job.id), note=why, reverse_of=f"gen:{job.id}")
    job.refunded = True
    job.billing_state = "released"


def reconcile_user(db: Session, user_id: uuid.UUID) -> dict:
    total = db.execute(
        select(func.coalesce(func.sum(CreditLedger.delta), 0)).where(CreditLedger.user_id == user_id)
    ).scalar_one()
    running = balance(db, user_id)
    in_lots = sum(r for _, r in lots(db, user_id, include_expired=True))
    return {"sum_delta": int(total), "balance_after": running, "lots_remaining": in_lots,
            "consistent": int(total) == running == in_lots}
