import threading
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import session_factory
from app.models import FeatureFlag, GenerationJob, User
from app.services import credits

from .conftest import WORKER_H, make_template, ready_profile, signup

MP4_BYTES = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 2048


def _gen(client, h, tid, pid, key=None, text=None):
    return client.post("/generations", json={"template_id": str(tid), "profile_id": pid, "text": text},
                       headers={**h, "Idempotency-Key": key or uuid.uuid4().hex})


def _claim(client, models=("dreamid_v",), wid="w1"):
    return client.post("/internal/worker/claim", json={"worker_id": wid, "models": list(models)}, headers=WORKER_H)


def _balance(client, h):
    return client.get("/credits", headers=h).json()["balance"]


def test_happy_path(client, db, storage):
    h, _ = signup(client)
    pid = ready_profile(client, h, storage)
    t = make_template(db, cost=10, accepts_text=True)
    r = _gen(client, h, t.id, pid, text="on the beach")
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "queued" and job["queue_position"] == 1
    assert _balance(client, h) == 20

    c = _claim(client)
    assert c.status_code == 200
    p = c.json()
    assert p["job_id"] == job["id"] and p["model"] == "dreamid_v"
    assert "on the beach" in p["prompt"] and len(p["identity_assets"]) == 5

    hb = client.post(f"/internal/worker/jobs/{p['job_id']}/heartbeat",
                     json={"worker_id": "w1", "attempt": 1, "status": "generating", "progress": 0.5},
                     headers=WORKER_H)
    assert hb.status_code == 200 and hb.json()["cancel"] is False
    assert client.get(f"/generations/{job['id']}", headers=h).json()["status"] == "generating"

    storage.put(p["upload"]["video"]["key"], MP4_BYTES, "video/mp4")
    done = client.post(f"/internal/worker/jobs/{p['job_id']}/complete",
                       json={"worker_id": "w1", "attempt": 1, "output": {"width": 720, "height": 1280},
                             "moderation": {"scores": {"nsfw": 0.01}},
                             "metrics": {"gpu_seconds": 40, "est_cost_usd": 0.03}}, headers=WORKER_H)
    assert done.json()["status"] == "completed"
    g = client.get(f"/generations/{job['id']}", headers=h).json()
    assert g["status"] == "completed" and g["output"]["video_url"] and g["output"]["watermarked"] is True
    assert _balance(client, h) == 20


def test_idempotent_create(client, db, storage):
    h, _ = signup(client)
    pid = ready_profile(client, h, storage)
    t = make_template(db, cost=10)
    a = _gen(client, h, t.id, pid, key="same-key-123")
    b = _gen(client, h, t.id, pid, key="same-key-123")
    assert a.status_code == 201 and b.status_code == 200 and a.json()["id"] == b.json()["id"]
    assert _balance(client, h) == 20
    t2 = make_template(db, cost=5)
    assert _gen(client, h, t2.id, pid, key="same-key-123").status_code == 409


def test_insufficient_credits(client, db, storage):
    h, _ = signup(client)
    pid = ready_profile(client, h, storage)
    t = make_template(db, cost=31)
    r = _gen(client, h, t.id, pid)
    assert r.status_code == 402 and r.json()["detail"]["code"] == "insufficient_credits"
    assert db.execute(select(GenerationJob)).first() is None


def test_blocked_text_consumes_no_credit(client, db, storage):
    h, _ = signup(client)
    pid = ready_profile(client, h, storage)
    t = make_template(db, cost=10, accepts_text=True)
    r = _gen(client, h, t.id, pid, text="make her n4ked")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "content_blocked"
    assert _balance(client, h) == 30


def test_concurrent_double_spend(client, db, storage):
    """10 parallel debits of 10 credits on a 30-credit balance -> exactly 3 succeed."""
    h, _ = signup(client)
    uid = uuid.UUID(client.get("/me", headers=h).json()["id"])
    results = []

    def spend(i):
        s = session_factory()()
        try:
            credits.apply(s, uid, -10, credits.LedgerReason.generation_debit, f"t:{i}")
            s.commit()
            results.append(True)
        except Exception:
            s.rollback()
            results.append(False)
        finally:
            s.close()

    ts = [threading.Thread(target=spend, args=(i,)) for i in range(10)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert results.count(True) == 3
    assert _balance(client, h) == 0
    assert credits.reconcile_user(db, uid)["consistent"]


def test_failure_refund_and_retry_limit(client, db, storage):
    h, _ = signup(client)
    pid = ready_profile(client, h, storage)
    t = make_template(db, cost=10)
    jid = _gen(client, h, t.id, pid).json()["id"]
    for attempt in (1, 2, 3):
        p = _claim(client).json()
        assert p["attempt"] == attempt
        r = client.post(f"/internal/worker/jobs/{jid}/fail", json={"worker_id": "w1", "attempt": attempt,
                                                                   "error_code": "oom", "retryable": True},
                        headers=WORKER_H)
    assert r.json()["status"] == "failed"
    g = client.get(f"/generations/{jid}", headers=h).json()
    assert g["refunded"] is True
    assert _balance(client, h) == 30
    # one-tap retry creates a fresh job (debits again)
    rr = client.post(f"/generations/{jid}/retry", headers=h)
    assert rr.status_code == 201 and _balance(client, h) == 20


def test_worker_crash_lease_expiry_requeues_without_double_charge(client, db, storage):
    h, _ = signup(client)
    pid = ready_profile(client, h, storage)
    t = make_template(db, cost=10)
    jid = _gen(client, h, t.id, pid).json()["id"]
    assert _claim(client, wid="dead").status_code == 200
    job = db.get(GenerationJob, uuid.UUID(jid))
    job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    p = _claim(client, wid="alive").json()
    assert p["job_id"] == jid and p["attempt"] == 2
    # The dead worker coming back must not be able to complete.
    stale = client.post(f"/internal/worker/jobs/{jid}/heartbeat", json={"worker_id": "dead", "attempt": 1},
                        headers=WORKER_H)
    assert stale.status_code == 409
    assert _balance(client, h) == 20


def test_cancel_queued_refunds(client, db, storage):
    h, _ = signup(client)
    pid = ready_profile(client, h, storage)
    t = make_template(db, cost=10)
    jid = _gen(client, h, t.id, pid).json()["id"]
    r = client.post(f"/generations/{jid}/cancel", headers=h).json()
    assert r["status"] == "cancelled" and r["refunded"]
    assert _balance(client, h) == 30


def test_cancel_running_via_heartbeat(client, db, storage):
    h, _ = signup(client)
    pid = ready_profile(client, h, storage)
    t = make_template(db, cost=10)
    jid = _gen(client, h, t.id, pid).json()["id"]
    _claim(client)
    client.post(f"/generations/{jid}/cancel", headers=h)
    hb = client.post(f"/internal/worker/jobs/{jid}/heartbeat", json={"worker_id": "w1", "attempt": 1},
                     headers=WORKER_H).json()
    assert hb["cancel"] is True
    r = client.post(f"/internal/worker/jobs/{jid}/fail", json={"worker_id": "w1", "attempt": 1,
                                                               "error_code": "cancelled", "retryable": False},
                    headers=WORKER_H)
    assert r.json()["status"] == "cancelled"
    assert _balance(client, h) == 30


def test_output_moderation_blocks_and_refunds(client, db, storage):
    h, _ = signup(client)
    pid = ready_profile(client, h, storage)
    t = make_template(db, cost=10)
    jid = _gen(client, h, t.id, pid).json()["id"]
    p = _claim(client).json()
    storage.put(p["upload"]["video"]["key"], MP4_BYTES, "video/mp4")
    r = client.post(f"/internal/worker/jobs/{jid}/complete",
                    json={"worker_id": "w1", "attempt": 1, "moderation": {"scores": {"nsfw": 0.9}}},
                    headers=WORKER_H)
    assert r.json()["status"] == "failed"
    assert p["upload"]["video"]["key"] not in storage.objects
    assert _balance(client, h) == 30


def test_model_routing_kill_switch_and_fallback(client, db, storage):
    h, _ = signup(client)
    pid = ready_profile(client, h, storage)
    t = make_template(db, cost=10)
    _gen(client, h, t.id, pid)
    # Worker that only has the fallback model can't take a fresh job...
    assert _claim(client, models=("wan22_animate_14b",)).status_code == 204
    # ...until the preferred model is emergency-disabled.
    db.add(FeatureFlag(key="model_disabled:dreamid_v", enabled=True))
    db.commit()
    assert _claim(client, models=("dreamid_v",)).status_code == 204
    p = _claim(client, models=("wan22_animate_14b",)).json()
    assert p["model"] == "wan22_animate_14b"


def test_priority_queue_pro_first(client, db, storage):
    hf, _ = signup(client)
    hp, _ = signup(client)
    pf = ready_profile(client, hf, storage)
    pp = ready_profile(client, hp, storage)
    pro = db.get(User, uuid.UUID(client.get("/me", headers=hp).json()["id"]))
    pro.plan = "pro"
    db.commit()
    t = make_template(db, cost=1)
    free_job = _gen(client, hf, t.id, pf).json()
    pro_job = _gen(client, hp, t.id, pp).json()
    assert pro_job["queue_class"] == "paid_high" and free_job["queue_class"] == "free"
    first = [_claim(client, wid=f"w{i}").json()["job_id"] for i in range(2)]
    assert set(first) == {free_job["id"], pro_job["id"]}


def test_generation_idor(client, db, storage):
    h1, _ = signup(client)
    h2, _ = signup(client)
    pid = ready_profile(client, h1, storage)
    t = make_template(db, cost=1)
    jid = _gen(client, h1, t.id, pid).json()["id"]
    assert client.get(f"/generations/{jid}", headers=h2).status_code == 404
    assert client.post(f"/generations/{jid}/cancel", headers=h2).status_code == 404
    assert client.post(f"/generations/{jid}/report", json={"reason": "other"}, headers=h2).status_code == 404
    # someone else's profile can't be used either
    assert _gen(client, h2, t.id, pid).status_code == 404


def test_worker_auth_required(client):
    assert client.post("/internal/worker/claim", json={"worker_id": "x", "models": []}).status_code == 401
    assert client.post("/internal/worker/claim", json={"worker_id": "x", "models": []},
                       headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_report_and_admin_moderation(client, db, storage):
    from .conftest import make_admin

    h, _ = signup(client)
    ha, _ = signup(client)
    make_admin(db, ha, client)
    pid = ready_profile(client, h, storage)
    t = make_template(db, cost=1)
    jid = _gen(client, h, t.id, pid).json()["id"]
    rep = client.post(f"/generations/{jid}/report", json={"reason": "impersonation"}, headers=h)
    assert rep.status_code == 201
    assert client.get("/admin/reports", headers=h).status_code == 403
    reports = client.get("/admin/reports", headers=ha).json()
    assert len(reports) == 1
    d = client.post(f"/admin/reports/{reports[0]['id']}/decision", json={"action": "dismiss"}, headers=ha)
    assert d.json()["status"] == "dismissed"


def test_admin_template_lifecycle_no_app_update(client, db):
    from .conftest import make_admin

    ha, _ = signup(client)
    make_admin(db, ha, client)
    r = client.post("/admin/templates", json={"slug": "beach-walk", "title": "Beach walk", "category": "travel",
                                              "credit_cost": 12}, headers=ha)
    tid = r.json()["id"]
    assert client.patch(f"/admin/templates/{tid}", json={"is_active": True}, headers=ha).status_code == 409
    v = client.post(f"/admin/templates/{tid}/versions",
                    json={"prompt_recipe": "slow-motion walk on a beach at sunset"}, headers=ha)
    assert v.status_code == 201
    assert client.patch(f"/admin/templates/{tid}", json={"is_active": True}, headers=ha).status_code == 200
    public = client.get("/templates").json()
    assert [x["slug"] for x in public] == ["beach-walk"]
    assert "prompt_recipe" not in public[0]
    grant = client.post(f"/admin/users/{client.get('/me', headers=ha).json()['id']}/credits",
                        json={"amount": 50, "reason": "launch promo", "idempotency_key": "promo-0001"}, headers=ha)
    assert grant.json()["balance"] == 80
