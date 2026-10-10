"""V4 Stage A4: per-job telemetry, unit economics by feature/model/template, job lineage, manual refund."""

import uuid

from sqlalchemy import select

from app.models import CreditLedger, FeatureFlag, LedgerReason
from app.services import credits

from .conftest import WORKER_H, make_admin, make_template, ready_profile, signup
from .test_generation import MP4_BYTES, _claim, _gen


def _complete(client, storage, jid, cost):
    p = _claim(client).json()
    assert p["job_id"] == jid
    storage.put(p["upload"]["video"]["key"], MP4_BYTES, "video/mp4")
    r = client.post(f"/internal/worker/jobs/{jid}/complete",
                    json={"worker_id": "w1", "attempt": p["attempt"], "output": {},
                          "moderation": {"scores": {"nsfw": 0.0}},
                          "metrics": {"gpu_seconds": 30, "est_cost_usd": cost}}, headers=WORKER_H)
    assert r.json()["status"] == "completed"


def _fail(client, jid, cost):
    p = _claim(client).json()
    client.post(f"/internal/worker/jobs/{jid}/fail", json={"worker_id": "w1", "attempt": p["attempt"],
                                                           "error_code": "oom", "retryable": False,
                                                           "metrics": {"est_cost_usd": cost}}, headers=WORKER_H)


def test_economics_counts_only_paid_settled_credits(client, db, storage):
    ha, _ = signup(client)
    make_admin(db, ha, client)
    h, _ = signup(client)
    uid = uuid.UUID(client.get("/me", headers=h).json()["id"])
    pid = ready_profile(client, h, storage)
    t = make_template(db, cost=20)
    j1 = _gen(client, h, t.id, pid).json()["id"]          # paid by promo (signup bonus): no revenue
    _complete(client, storage, j1, 0.05)
    credits.apply(db, uid, 100, LedgerReason.purchase, f"p:{uuid.uuid4()}")
    db.commit()
    j2 = _gen(client, h, t.id, pid).json()["id"]          # 10 promo + 10 purchased
    _complete(client, storage, j2, 0.07)
    j3 = _gen(client, h, t.id, pid).json()["id"]          # purchased, fails -> released, cost still counted
    _fail(client, j3, 0.02)

    r = client.get("/admin/economics?days=1", headers=ha).json()
    row = next(x for x in r["rows"] if x["key"] == "template")
    assert row["jobs"] == 3 and row["completed"] == 2 and row["failed"] == 1 and row["success_rate"] == 0.6667
    assert row["credits_settled"] == 40 and row["credits_released"] == 20 and row["paid_credits"] == 10
    assert row["cost_usd"] == 0.14 and row["cost_per_success_usd"] == 0.07
    assert row["revenue_usd"] is None and row["contribution_margin_usd"] is None  # no invented prices
    db.add(FeatureFlag(key="economics", enabled=True, value={"usd_per_paid_credit": 0.008, "alert_min_jobs": 1}))
    db.commit()
    r = client.get("/admin/economics?days=1", headers=ha).json()
    row = next(x for x in r["rows"] if x["key"] == "template")
    assert row["revenue_usd"] == 0.08 and row["contribution_margin_usd"] == -0.06
    kinds = {(a["key"], a["type"]) for a in r["alerts"]}
    assert ("template", "negative_margin") in kinds and ("template", "low_success_rate") in kinds
    by_model = client.get("/admin/economics?days=1&group=model", headers=ha).json()["rows"]
    assert {x["key"] for x in by_model} == {"dreamid_v"}
    assert client.get("/admin/economics?group=nope", headers=ha).status_code == 422
    assert client.get("/admin/economics", headers=h).status_code == 403


def test_job_lineage_and_manual_refund(client, db, storage):
    ha, _ = signup(client)
    make_admin(db, ha, client)
    h, _ = signup(client)
    pid = ready_profile(client, h, storage)
    t = make_template(db, cost=20)
    jid = _gen(client, h, t.id, pid).json()["id"]
    assert client.post(f"/admin/jobs/{jid}/refund", headers=ha,
                       json={"reason": "still running"}).json()["detail"]["code"] == "not_refundable"
    _complete(client, storage, jid, 0.05)
    lin = client.get(f"/admin/jobs/{jid}", headers=ha).json()
    assert [e["reason"] for e in lin["ledger"]] == ["generation_debit", "generation_settle"]
    assert lin["actual_cost_usd"] == 0.05 and lin["runs"][0]["model"] == "dreamid_v"
    assert lin["output"]["provenance"]["ai_generated"] is True
    assert lin["queue_s"] is not None and lin["time_to_result_s"] is not None
    r = client.post(f"/admin/jobs/{jid}/refund", headers=ha, json={"reason": "customer complaint #123"})
    assert r.status_code == 200 and r.json()["balance"] == 30
    assert client.post(f"/admin/jobs/{jid}/refund", headers=ha,
                       json={"reason": "again please"}).json()["detail"]["code"] == "already_refunded"
    e = db.execute(select(CreditLedger).where(CreditLedger.idempotency_key == f"admin_refund:{jid}")).scalar_one()
    assert e.actor_id is not None and e.note == "customer complaint #123"
    items = client.get(f"/admin/jobs?template_id={t.id}", headers=ha).json()["items"]
    assert len(items) == 1 and items[0]["billing_state"] == "released"
    assert client.get("/admin/jobs?status=bogus", headers=ha).status_code == 422
