"""Credit ledger. Rules:
- append-only (DB trigger rejects UPDATE/DELETE on credit_ledger);
- every entry has a globally unique idempotency key -> retries never double-apply;
- writes for one user are serialized with a row lock on users -> no double spend;
- balance_after is a denormalized running total, verifiable against SUM(delta) (see reconcile_user).
Callers own the transaction (commit happens in the caller) so a debit and the
job row it pays for commit atomically."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import ApiError
from ..models import CreditLedger, LedgerReason, User


def lock_user(db: Session, user_id: uuid.UUID) -> User:
    user = db.execute(select(User).where(User.id == user_id).with_for_update()).scalar_one()
    return user


def balance(db: Session, user_id: uuid.UUID) -> int:
    last = db.execute(
        select(CreditLedger.balance_after).where(CreditLedger.user_id == user_id)
        .order_by(CreditLedger.id.desc()).limit(1)
    ).scalar_one_or_none()
    return last or 0


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
) -> CreditLedger:
    """Apply a ledger entry exactly once. Returns the (new or pre-existing) entry."""
    existing = db.execute(
        select(CreditLedger).where(CreditLedger.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        if existing.user_id != user_id or existing.delta != delta:
            raise ApiError(409, "idempotency_conflict", "idempotency key reused with different parameters")
        return existing

    lock_user(db, user_id)
    # Re-check after acquiring the lock: a concurrent tx may have inserted it.
    existing = db.execute(
        select(CreditLedger).where(CreditLedger.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    current = balance(db, user_id)
    new_balance = current + delta
    if new_balance < 0:
        if not allow_negative:
            raise ApiError(402, "insufficient_credits", "not enough credits",
                           {"balance": current, "required": -delta})
        # Purchase reversals (refund/chargeback) may exceed the remaining balance: clamp at 0 and
        # record the shortfall so finance can see it.
        note = f"{note or ''} shortfall={-new_balance}".strip()
        delta, new_balance = -current, 0

    entry = CreditLedger(
        user_id=user_id, delta=delta, balance_after=new_balance, reason=reason,
        idempotency_key=idempotency_key, ref_type=ref_type, ref_id=ref_id, actor_id=actor_id, note=note,
    )
    db.add(entry)
    db.flush()
    return entry


def reconcile_user(db: Session, user_id: uuid.UUID) -> dict:
    total = db.execute(
        select(func.coalesce(func.sum(CreditLedger.delta), 0)).where(CreditLedger.user_id == user_id)
    ).scalar_one()
    running = balance(db, user_id)
    return {"sum_delta": int(total), "balance_after": running, "consistent": int(total) == running}
