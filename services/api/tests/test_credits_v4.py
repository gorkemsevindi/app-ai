"""V4 Stage A1: credit buckets with independent expiry, reserve -> settle / release, legacy migration."""

import os
import uuid
from datetime import UTC, datetime, timedelta

from alembic.config import Config
from sqlalchemy import select, text

from alembic import command
from app.db import get_engine
from app.models import CreditLedger, CreditLot, FeatureFlag, GenerationJob, LedgerReason
from app.services import credits

from .conftest import API_DIR, WORKER_H, make_template, ready_profile, signup
from .test_generation import MP4_BYTES, _claim, _gen


def _uid(client, h):
    return uuid.UUID(client.get("/me", headers=h).json()["id"])


def _credits(client, h):
    return client.get("/credits", headers=h).json()


def _buckets(client, h):
    return {b["bucket"]: b["remaining"] for b in _credits(client, h)["buckets"]}


def test_signup_bonus_is_a_promo_bucket_and_reconciles(client, db):
    h, _ = signup(client)
    c = _credits(client, h)
    assert c["balance"] == 30 and _buckets(client, h) == {"promo": 30}
    assert credits.reconcile_user(db, _uid(client, h))["consistent"]


def test_reserve_settle_release_use_the_right_buckets(client, db, storage):
    h, _ = signup(client)
    uid = _uid(client, h)
    pid = ready_profile(client, h, storage)
    credits.apply(db, uid, 100, LedgerReason.purchase, f"p:{uuid.uuid4()}", ref_type="purchase")
    soon = datetime.now(UTC) + timedelta(days=2)
    credits.apply(db, uid, 20, LedgerReason.subscription_grant, f"s:{uuid.uuid4()}", expires_at=soon)
    db.commit()
    assert _buckets(client, h) == {"promo": 30, "subscription": 20, "purchased": 100}
    t = make_template(db, cost=40)
    # 40 credits: the expiring subscription grant goes first (20), then promo (20); purchased untouched
    jid = _gen(client, h, t.id, pid).json()["id"]
    assert _buckets(client, h) == {"promo": 10, "purchased": 100}
    job = db.get(GenerationJob, uuid.UUID(jid))
    assert job.billing_state == "reserved"
    # failure -> credits return to the exact lots they came from (subscription keeps its expiry)
    for attempt in (1, 2, 3):
        _claim(client)
        client.post(f"/internal/worker/jobs/{jid}/fail", json={"worker_id": "w1", "attempt": attempt,
                                                               "error_code": "oom", "retryable": True},
                    headers=WORKER_H)
    assert _buckets(client, h) == {"promo": 30, "subscription": 20, "purchased": 100}
    sub = next(b for b in _credits(client, h)["buckets"] if b["bucket"] == "subscription")
    assert sub["next_expiry"] and sub["next_expiry_amount"] == 20
    db.expire_all()
    assert db.get(GenerationJob, uuid.UUID(jid)).billing_state == "released"
    # release is idempotent
    credits.release(db, db.get(GenerationJob, uuid.UUID(jid)), "again")
    db.commit()
    assert _credits(client, h)["balance"] == 150

    # success -> settle row (0 delta) confirms the charge exactly once
    jid2 = _gen(client, h, t.id, pid).json()["id"]
    p = _claim(client).json()
    storage.put(p["upload"]["video"]["key"], MP4_BYTES, "video/mp4")
    client.post(f"/internal/worker/jobs/{jid2}/complete",
                json={"worker_id": "w1", "attempt": 1, "output": {}, "moderation": {"scores": {"nsfw": 0.0}},
                      "metrics": {}}, headers=WORKER_H)
    db.expire_all()
    assert db.get(GenerationJob, uuid.UUID(jid2)).billing_state == "settled"
    settle = db.execute(select(CreditLedger).where(CreditLedger.idempotency_key == f"settle:{jid2}")).scalar_one()
    assert settle.delta == 0 and settle.reason == LedgerReason.generation_settle
    assert _credits(client, h)["balance"] == 110
    assert credits.reconcile_user(db, uid)["consistent"]


def test_expired_bucket_is_swept_never_spent(client, db, storage):
    h, _ = signup(client)
    uid = _uid(client, h)
    pid = ready_profile(client, h, storage)
    past = datetime.now(UTC) - timedelta(minutes=1)
    credits.apply(db, uid, 50, LedgerReason.promo, f"promo:{uuid.uuid4()}", expires_at=past)
    db.commit()
    # expired credits are not spendable, even before the sweep runs
    assert _credits(client, h)["balance"] == 30
    t = make_template(db, cost=40)
    r = _gen(client, h, t.id, pid)
    assert r.status_code == 402
    # the sweep is written together with the next successful credit write
    assert _gen(client, h, make_template(db, cost=10).id, pid).status_code == 201
    assert db.execute(select(CreditLedger).where(CreditLedger.reason == LedgerReason.expire)).scalar_one().delta == -50
    assert credits.reconcile_user(db, uid)["consistent"]


def test_consume_order_is_remote_config(client, db, storage):
    h, _ = signup(client)
    uid = _uid(client, h)
    pid = ready_profile(client, h, storage)
    credits.apply(db, uid, 100, LedgerReason.purchase, f"p:{uuid.uuid4()}")
    db.add(FeatureFlag(key="credits", enabled=True, value={"consume_order": ["purchased", "promo"]}))
    db.commit()
    _gen(client, h, make_template(db, cost=10).id, pid)
    assert _buckets(client, h) == {"promo": 30, "purchased": 90}


def test_purchase_reversal_hits_its_own_lot_and_retries_cleanly(client, db):
    h, _ = signup(client)
    uid = _uid(client, h)
    credits.apply(db, uid, 100, LedgerReason.purchase, "purchase:tx-1")
    db.commit()
    e1 = credits.apply(db, uid, -100, LedgerReason.purchase_reversal, "reversal:tx-1",
                       prefer_source_key="purchase:tx-1", allow_negative=True)
    db.commit()
    assert _buckets(client, h) == {"promo": 30}
    lots = {lot.bucket for lot, _ in credits.lots(db, uid)}
    assert lots == {"promo"}
    # clamped reversal (more than left) records the shortfall and is idempotent on retry
    credits.apply(db, uid, -500, LedgerReason.purchase_reversal, "reversal:tx-2", allow_negative=True)
    db.commit()
    again = credits.apply(db, uid, -500, LedgerReason.purchase_reversal, "reversal:tx-2", allow_negative=True)
    assert again.delta == -30 and "shortfall=470" in again.note
    assert e1.delta == -100 and credits.reconcile_user(db, uid)["consistent"]


def test_lots_and_allocations_are_append_only(client, db):
    h, _ = signup(client)
    import pytest
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError):
        with get_engine().begin() as c:
            c.execute(text("update credit_lots set granted = 999"))
    with pytest.raises(DBAPIError):
        with get_engine().begin() as c:
            c.execute(text("delete from credit_lots"))


def test_migration_turns_existing_balances_into_legacy_lots(client, db, storage):
    cfg = Config(os.path.join(API_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(API_DIR, "alembic"))
    h, _ = signup(client)
    uid = _uid(client, h)
    pid = ready_profile(client, h, storage)
    t = make_template(db, cost=10)
    jid = _gen(client, h, t.id, pid).json()["id"]  # in-flight reservation that predates the migration
    db.close()
    command.downgrade(cfg, "0004")
    try:
        with get_engine().begin() as c:
            assert c.execute(text("select sum(delta) from credit_ledger where user_id=:u"), {"u": uid}).scalar() == 20
    finally:
        command.upgrade(cfg, "head")
    assert _buckets(client, h) == {"legacy": 20}
    with get_engine().begin() as c:
        state = c.execute(text("select billing_state from generation_jobs where id=:j"), {"j": jid}).scalar()
    assert state == "reserved"
    # releasing a pre-bucket reservation opens a legacy lot, the total stays consistent
    client.post(f"/generations/{jid}/cancel", headers=h)
    assert _credits(client, h)["balance"] == 30 and _buckets(client, h) == {"legacy": 30}
    from app.db import session_factory

    s = session_factory()()
    try:
        assert credits.reconcile_user(s, uid)["consistent"]
        assert s.execute(select(CreditLot).where(CreditLot.user_id == uid)).scalars().all()
    finally:
        s.close()
