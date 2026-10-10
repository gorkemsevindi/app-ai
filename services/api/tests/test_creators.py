"""V4 Stage D: creator onboarding + template submission/moderation, revenue policies, earnings accrual rules,
clawbacks (support refund + store refund), settlements with risk holds and payout, analytics."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db import get_engine
from app.models import (
    CreatorEarning,
    FeatureFlag,
    LedgerReason,
    Purchase,
    SourceVideo,
    SourceVideoStatus,
    Template,
    VideoPerson,
)
from app.services import billing, credits

from .conftest import WORKER_H, make_admin, make_template, ready_profile, signup
from .test_generation import MP4_BYTES, _claim, _gen

POLICY = {"creator_share_rate": 0.5, "referral_rate": 0.1, "usd_per_paid_credit": 0.01,
          "settlement_delay_days": 0, "min_payout_micros": 0}


def enable(db, key="creator_marketplace", value=None):
    f = db.get(FeatureFlag, key) or FeatureFlag(key=key)
    f.enabled, f.value = True, value or {}
    db.add(f)
    db.commit()


def uid(client, h):
    return uuid.UUID(client.get("/me", headers=h).json()["id"])


def admin(client, db):
    h, _ = signup(client)
    make_admin(db, h, client)
    return h


def creator(client, db, handle=None):
    h, _ = signup(client)
    r = client.post("/creator/profile", headers=h, json={"handle": handle or f"c{uuid.uuid4().hex[:8]}",
                                                         "display_name": "Ece", "accept_terms": True})
    assert r.status_code == 201, r.text
    return h


def policy(client, ah, **over):
    r = client.post("/admin/revenue-policies", headers=ah, json={"config": {**POLICY, **over}})
    assert r.status_code == 201, r.text
    return r.json()


def buy(db, user_id, n=200):
    key = f"purchase:apple:{uuid.uuid4().hex}"
    credits.apply(db, user_id, n, LedgerReason.purchase, key, ref_type="purchase", bucket="purchased")
    db.commit()
    return key


def complete(client, storage, jid):
    p = _claim(client).json()
    assert p["job_id"] == jid, p
    storage.put(p["upload"]["video"]["key"], MP4_BYTES, "video/mp4")
    r = client.post(f"/internal/worker/jobs/{jid}/complete", headers=WORKER_H, json={
        "worker_id": "w1", "attempt": p["attempt"], "output": {}, "moderation": {"scores": {"nsfw": 0.0}},
        "metrics": {}})
    assert r.json()["status"] == "completed"


def earnings(db, creator_id=None):
    q = select(CreatorEarning).order_by(CreatorEarning.id)
    if creator_id:
        q = q.where(CreatorEarning.creator_id == creator_id)
    return db.execute(q).scalars().all()


def test_onboarding_and_public_profile(client, db):
    h, _ = signup(client)
    assert client.post("/creator/profile", headers=h, json={"handle": "ece", "display_name": "E"}).status_code == 403
    enable(db)
    assert client.post("/creator/profile", headers=h, json={"handle": "ece", "display_name": "E"}
                       ).json()["detail"]["code"] == "terms_required"
    r = client.post("/creator/profile", headers=h, json={"handle": "Ece_1", "display_name": "Ece",
                                                         "accept_terms": True, "payout_country": "tr"}).json()
    assert r["handle"] == "ece_1" and r["payout_status"] == "none" and r["payout_country"] == "TR"
    h2, _ = signup(client)
    assert client.post("/creator/profile", headers=h2, json={"handle": "ECE_1", "display_name": "x",
                                                              "accept_terms": True}).json()["detail"]["code"] == \
        "handle_taken"
    pub = client.get("/creators/ece_1").json()
    assert pub["display_name"] == "Ece" and "payout_status" not in pub and pub["templates"] == []


def test_creator_template_submission_requires_rights_consent_and_moderation(client, db, storage):
    enable(db)
    ah = admin(client, db)
    h = creator(client, db, "maker")
    t = client.post("/creator/templates", headers=h, json={"title": "Duet dance", "category": "dance"}).json()
    assert t["visibility"] == "draft" and t["moderation_status"] == "pending"
    body = {"mime": "video/mp4", "size_bytes": 100, "basis": "creator_owned", "evidence": ["self-recorded"]}
    r = client.post(f"/creator/templates/{t['id']}/source-video", headers=h, json=body)
    assert r.json()["detail"]["code"] == "consent_required"
    r = client.post(f"/creator/templates/{t['id']}/source-video", headers=h, json={**body, "people_consented": True})
    vid = uuid.UUID(r.json()["source_video_id"])
    v = db.get(SourceVideo, vid)
    assert v.user_id == uid(client, h)  # the clip belongs to its creator, not to an admin account
    v.status, v.duration_ms, v.width, v.height, v.fps = SourceVideoStatus.ready, 5000, 720, 1280, 16
    for i in (1, 2):
        db.add(VideoPerson(source_video_id=vid, track_id=i, first_frame=0, last_frame=79, coverage=0.9,
                           median_face_px=100, selectable=True, flags=[], stats={"worker_track_id": i}))
    db.commit()
    ver = client.post(f"/creator/templates/{t['id']}/versions", headers=h, json={
        "source_video_id": str(vid), "slots": [{"slot_id": "a", "track_id": 1}, {"slot_id": "b", "track_id": 2}]})
    assert ver.status_code == 201, ver.text
    # another user can't touch it
    h2, _ = signup(client)
    assert client.post(f"/creator/templates/{t['id']}/submit", headers=h2,
                       json={"version_id": ver.json()["id"]}).status_code in (403, 404)
    assert client.post(f"/creator/templates/{t['id']}/submit", headers=h,
                       json={"version_id": ver.json()["id"]}).json()["moderation_status"] == "review"
    assert client.get(f"/templates/{t['id']}").status_code == 404  # not live before approval
    q = client.get("/admin/templates/review-queue", headers=ah).json()["items"]
    assert q[0]["id"] == t["id"] and q[0]["rights"]["people_consented"] is True
    client.post(f"/admin/templates/{t['id']}/publish", headers=ah, json={"version_id": ver.json()["id"]})
    assert client.get(f"/templates/{t['id']}").json()["mode"] == "remix"
    assert [c["id"] for c in client.get("/creators/maker").json()["templates"]] == [t["id"]]
    # suspension takes the creator's templates out of the app
    client.post(f"/admin/creators/{uid(client, h)}/status", headers=ah, json={"status": "suspended", "note": "test"})
    assert client.get(f"/templates/{t['id']}").status_code == 404


def test_accrual_rules_policy_promo_selfuse_referral(client, db, storage):
    enable(db)
    ah = admin(client, db)
    ch = creator(client, db)
    cid = uid(client, ch)
    t = make_template(db, cost=40, creator_id=cid)
    h, _ = signup(client)
    payer = uid(client, h)
    pid = ready_profile(client, h, storage)
    buy(db, payer, 100)
    # no policy -> nothing accrues
    complete(client, storage, _gen(client, h, t.id, pid).json()["id"])  # 30 promo + 10 purchased
    assert earnings(db) == []
    policy(client, ah)
    # promo is spent first: 0 promo left now, all 40 from purchased -> 40 * $0.01 * 50% = $0.20
    j2 = _gen(client, h, t.id, pid).json()["id"]
    complete(client, storage, j2)
    e = earnings(db)
    assert [(x.kind, x.amount_micros, x.gross_basis_micros, x.policy_version) for x in e] == [
        ("template_share", 200_000, 400_000, 1)]
    # self-use never earns
    cpid = ready_profile(client, ch, storage)
    buy(db, cid, 100)
    complete(client, storage, _gen(client, ch, t.id, cpid).json()["id"])
    assert len(earnings(db)) == 1
    # an earning row is immutable
    with pytest.raises(DBAPIError):
        with get_engine().begin() as c:
            c.execute(text("update creator_earnings set amount_micros = 1"))


def test_referral_precedence_and_stacking(client, db, storage):
    from app.models import Attribution

    enable(db)
    ah = admin(client, db)
    ch = creator(client, db)
    rh = creator(client, db)  # referrer is also a creator (needs a profile to earn)
    cid, rid = uid(client, ch), uid(client, rh)
    t = make_template(db, cost=30, creator_id=cid)
    h, _ = signup(client)
    payer = uid(client, h)
    pid = ready_profile(client, h, storage)
    db.add(Attribution(user_id=payer, referrer_id=rid, model="first_touch", policy={},
                       expires_at=datetime.now(UTC) + timedelta(days=30)))
    db.commit()
    buy(db, payer, 300)
    p1 = policy(client, ah)  # template_first, no stacking
    credits.apply(db, payer, -30, LedgerReason.admin_adjust, f"burn:{uuid.uuid4()}")  # drop the promo bonus
    db.commit()
    complete(client, storage, _gen(client, h, t.id, pid).json()["id"])
    assert [(x.kind, x.creator_id) for x in earnings(db)] == [("template_share", cid)]
    # a newer policy (stacking) applies to new jobs only; the old row keeps version 1
    policy(client, ah, referral_stacking=True)
    complete(client, storage, _gen(client, h, t.id, pid).json()["id"])
    rows = earnings(db)
    assert [(x.kind, x.policy_version) for x in rows] == [("template_share", p1["version"]), ("template_share", 2),
                                                          ("referral_commission", 2)]
    assert rows[2].creator_id == rid and rows[2].amount_micros == 30_000  # 30 credits * $0.01 * 10%
    # a plain (non-creator) template only pays the referral
    t2 = make_template(db, cost=10)
    complete(client, storage, _gen(client, h, t2.id, pid).json()["id"])
    assert earnings(db)[-1].kind == "referral_commission"


def test_clawbacks_support_refund_and_store_refund(client, db, storage):
    enable(db)
    ah = admin(client, db)
    ch = creator(client, db)
    cid = uid(client, ch)
    t = make_template(db, cost=50, creator_id=cid)
    h, _ = signup(client)
    payer = uid(client, h)
    pid = ready_profile(client, h, storage)
    policy(client, ah)
    k1 = buy(db, payer, 30)
    k2 = buy(db, payer, 30)
    j = _gen(client, h, t.id, pid).json()["id"]  # 30 promo + 20 from first purchase lot... order: promo first
    complete(client, storage, j)
    first = earnings(db)[0]
    assert first.amount_micros == 100_000  # 20 paid credits * $0.01 * 50%
    # store refund of the purchase that funded the job claws back proportionally (all 20 paid credits came from k1)
    db.add(Purchase(user_id=payer, provider="apple", product_id="p", transaction_id=k1.split(":")[-1],
                    kind="consumable", status="verified"))
    db.commit()
    billing.reverse(db, "apple", k1.split(":")[-1], "refund")
    db.commit()
    claws = [x for x in earnings(db) if x.kind == "clawback"]
    assert len(claws) == 1 and claws[0].amount_micros == -100_000
    # support refund of a second job claws back in full, once
    j2 = _gen(client, h, t.id, pid)
    assert j2.status_code == 402  # only 30 credits (k2) left after the reversal
    credits.apply(db, payer, 40, LedgerReason.purchase, f"purchase:apple:{uuid.uuid4().hex}", bucket="purchased")
    db.commit()
    j2 = _gen(client, h, t.id, pid).json()["id"]
    complete(client, storage, j2)
    assert client.post(f"/admin/jobs/{j2}/refund", headers=ah, json={"reason": "bad output"}).status_code == 200
    assert sum(x.amount_micros for x in earnings(db)) == 0
    assert k2


def test_settlement_risk_hold_payout_and_balances(client, db, storage):
    enable(db)
    ah = admin(client, db)
    ch = creator(client, db)
    cid = uid(client, ch)
    t = make_template(db, cost=20, creator_id=cid)
    h, _ = signup(client)
    payer = uid(client, h)
    pid = ready_profile(client, h, storage)
    buy(db, payer, 500)
    policy(client, ah, risk={"min_events_for_concentration": 2, "max_payer_concentration": 0.5})
    for _ in range(3):
        complete(client, storage, _gen(client, h, t.id, pid).json()["id"])
    b = client.get("/creator/earnings", headers=ch).json()["balances"]
    assert b["available_micros"] == b["balance_micros"] > 0
    run = client.post("/admin/settlements/run", headers=ah).json()["created"]
    assert len(run) == 1 and run[0]["status"] == "held" and "payer_concentration" in run[0]["risk"]["reasons"]
    assert client.post("/admin/settlements/run", headers=ah).json()["created"] == []  # never batched twice
    holds = client.get("/admin/creators/risk-holds", headers=ah).json()["items"]
    assert holds and holds[0]["reason"] == "payer_concentration"
    sid = run[0]["id"]
    assert client.post(f"/admin/settlements/{sid}/pay", headers=ah, json={}).json()["detail"]["code"] == \
        "bad_transition"
    client.post(f"/admin/creators/risk-holds/{holds[0]['id']}/resolve", headers=ah,
                json={"status": "released", "note": "single fan, verified"})
    assert client.post(f"/admin/settlements/{sid}/approve", headers=ah, json={"note": "ok"}).json()["status"] == \
        "approved"
    r = client.post(f"/admin/settlements/{sid}/pay", headers=ah, json={"external_ref": "po_1"})
    assert r.json()["detail"]["code"] == "payout_not_verified"
    client.post(f"/admin/creators/{cid}/payout-status", headers=ah, json={"payout_status": "verified",
                                                                         "kyc_ref": "kyc_123"})
    assert client.post(f"/admin/settlements/{sid}/pay", headers=ah, json={}).json()["detail"]["code"] == \
        "external_ref_required"
    assert client.post(f"/admin/settlements/{sid}/pay", headers=ah,
                       json={"external_ref": "po_1"}).json()["status"] == "paid"
    e = client.get("/creator/earnings", headers=ch).json()
    assert e["balances"]["balance_micros"] == 0 and e["balances"]["paid_micros"] == run[0]["amount_micros"]
    assert e["settlements"][0]["status"] == "paid"
    a = client.get("/creator/analytics", headers=ch).json()
    row = a["templates"][0]
    assert row["paid_qualifying_uses"] == 2 and row["generations"] == 3  # the first job was paid with promo credits
    assert row["platform_deductions_micros"] == row["gross_attributable_micros"] - row["creator_earnings_micros"]


def test_policy_validation_and_immutability(client, db):
    ah = admin(client, db)
    assert client.post("/admin/revenue-policies", headers=ah, json={"config": {"creator_share_rate": 2}}
                       ).json()["detail"]["code"] == "bad_policy"
    past = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    assert client.post("/admin/revenue-policies", headers=ah, json={"config": POLICY, "effective_from": past}
                       ).json()["detail"]["code"] == "bad_policy"
    policy(client, ah)
    with pytest.raises(DBAPIError):
        with get_engine().begin() as c:
            c.execute(text("update revenue_policies set config = '{}'"))
    assert client.post("/admin/settlements/run", headers=signup(client)[0]).status_code == 403
    assert db.execute(select(Template)).first() is None
