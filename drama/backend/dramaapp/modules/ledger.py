"""Append-only double-entry ledger (spec §5).

Convention: every transaction's entries sum to zero per currency. Positive = debit (asset/expense
grows, liability shrinks), negative = credit. Liability/revenue accounts therefore carry negative
balances; helpers below flip signs where a "how much do we owe X" number is wanted.

Accounts (examples):
  cash:{store}                         clearing receivable from Apple/Google/Stripe/sandbox (asset)
  expense:store_fees                   store/payment processor fees
  liability:tax                        indirect tax collected
  liability:creator:{id}:pending       creator earnings inside hold window
  liability:creator:{id}:available     creator earnings payable
  liability:creator:{id}:payout_in_flight
  revenue:platform:series              platform's share of distributable net
  credits:user:{id}                    production credit wallet (currency CRD, liability to user)
  credits:issued / credits:consumed    counter-accounts for credit issuance / consumption
"""

from collections import defaultdict
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import AppError
from ..models import AuditEvent, LedgerEntry, LedgerTransaction, now


def post(
    db: Session,
    *,
    idempotency_key: str,
    kind: str,
    entries: list[tuple[str, str, int] | tuple[str, str, int, datetime | None]],
    memo: str = "",
    ref: dict | None = None,
    reverses_id: str | None = None,
) -> tuple[LedgerTransaction, bool]:
    """Post a balanced transaction exactly once. Returns (txn, created)."""
    existing = db.scalar(select(LedgerTransaction).where(LedgerTransaction.idempotency_key == idempotency_key))
    if existing:
        return existing, False
    totals: dict[str, int] = defaultdict(int)
    for e in entries:
        totals[e[1]] += e[2]
    if any(v != 0 for v in totals.values()):
        raise AppError("ledger.unbalanced", f"Unbalanced transaction {dict(totals)}", 500)
    txn = LedgerTransaction(idempotency_key=idempotency_key, kind=kind, memo=memo, ref=ref or {},
                            reverses_id=reverses_id)
    for e in entries:
        if e[2] == 0:
            continue
        txn.entries.append(LedgerEntry(account=e[0], currency=e[1], amount_minor=e[2],
                                       available_at=e[3] if len(e) > 3 else None))
    db.add(txn)
    try:
        db.flush()
    except IntegrityError:  # concurrent duplicate: someone else posted it first
        db.rollback()
        existing = db.scalar(select(LedgerTransaction).where(LedgerTransaction.idempotency_key == idempotency_key))
        if existing is None:
            raise
        return existing, False
    db.add(AuditEvent(action=f"ledger.{kind}", target_type="ledger_txn", target_id=txn.id,
                      data={"key": idempotency_key}))
    return txn, True


def reverse(db: Session, txn: LedgerTransaction, *, idempotency_key: str, memo: str = "") -> LedgerTransaction:
    rev, _ = post(db, idempotency_key=idempotency_key, kind=f"reversal:{txn.kind}",
                  entries=[(e.account, e.currency, -e.amount_minor) for e in txn.entries],
                  memo=memo or f"reversal of {txn.id}", reverses_id=txn.id)
    return rev


def balance(db: Session, account: str, currency: str) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0)).where(
        LedgerEntry.account == account, LedgerEntry.currency == currency)) or 0)


def owed(db: Session, account: str, currency: str) -> int:
    """For liability accounts: positive number = amount owed to the account holder."""
    return -balance(db, account, currency)


def trial_balance(db: Session) -> dict[str, int]:
    rows = db.execute(select(LedgerEntry.currency, func.sum(LedgerEntry.amount_minor)).group_by(LedgerEntry.currency))
    return {c: int(s) for c, s in rows}


# ------------------------------------------------------------------------------- credit wallet
CRD = "CRD"


def wallet_account(user_id: str) -> str:
    return f"credits:user:{user_id}"


def credit_balance(db: Session, user_id: str) -> int:
    return owed(db, wallet_account(user_id), CRD)


def grant_credits(db: Session, user_id: str, amount: int, key: str, memo: str) -> None:
    post(db, idempotency_key=key, kind="credits.grant", memo=memo,
         entries=[("credits:issued", CRD, amount), (wallet_account(user_id), CRD, -amount)])


def consume_credits(db: Session, user_id: str, amount: int, key: str, memo: str, ref: dict | None = None) -> bool:
    """Charge credits once per key (retries never double-bill). Returns True if newly charged."""
    if amount <= 0:
        return False
    _, created = post(db, idempotency_key=key, kind="credits.consume", memo=memo, ref=ref,
                      entries=[(wallet_account(user_id), CRD, amount), ("credits:consumed", CRD, -amount)])
    return created


def credits_spent_since(db: Session, user_id: str, since: datetime) -> int:
    q = select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0)).join(LedgerTransaction).where(
        LedgerEntry.account == wallet_account(user_id), LedgerEntry.currency == CRD,
        LedgerTransaction.kind == "credits.consume", LedgerEntry.created_at >= since)
    return int(db.scalar(q) or 0)


def pending_vs_available(db: Session, creator_id: str, currency: str, at: datetime | None = None) -> dict:
    at = at or now()
    pending = owed(db, f"liability:creator:{creator_id}:pending", currency)
    available = owed(db, f"liability:creator:{creator_id}:available", currency)
    in_flight = owed(db, f"liability:creator:{creator_id}:payout_in_flight", currency)
    return {"pending_minor": pending, "available_minor": available, "in_flight_minor": in_flight,
            "currency": currency, "as_of": at.isoformat()}
