"""V5 Phase B: consent-aware, content-free learning events, feedback (owner-only, one per job), technical memory
roll-up, drift alerts, user deletion, scheduler task."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app import scheduler
from app.db import get_engine
from app.models import FeatureFlag, LearningEvent, ModelPerformanceAggregate

from .conftest import WORKER_H, make_template, ready_profile, signup
from .test_creators import admin
from .test_economics import _complete, _fail
from .test_generation import _gen


def enable(db, **value):
    f = db.get(FeatureFlag, "learning") or FeatureFlag(key="learning")
    f.enabled, f.value = True, value
    db.add(f)
    db.commit()


def events(db, kind=None):
    q = select(LearningEvent).order_by(LearningEvent.id)
    if kind:
        q = q.where(LearningEvent.kind == kind)
    db.expire_all()
    return db.execute(q).scalars().all()


def test_outcome_events_are_content_free_and_flagged(client, db, storage):
    h, _ = signup(client)
    pid = ready_profile(client, h, storage)
    t = make_template(db, cost=10, accepts_text=True)
    j0 = client.post("/generations", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                     json={"template_id": str(t.id), "profile_id": pid, "text": "my secret birthday party"}).json()
    _complete(client, storage, j0["id"], 0.01)
    assert events(db) == []  # flag off: nothing recorded
    enable(db)
    j1 = _gen(client, h, t.id, pid).json()["id"]
    _complete(client, storage, j1, 0.03)
    j2 = _gen(client, h, t.id, pid).json()["id"]
    _fail(client, j2, 0.01)
    j3 = _gen(client, h, t.id, pid).json()["id"]
    client.post(f"/generations/{j3}/cancel", headers=h)
    ev = {str(e.job_id): e for e in events(db, "job_outcome")}
    assert set(ev) == {j1, j2, j3}
    ok = ev[j1]
    assert ok.success is True and ok.provider == "dreamid_v" and ok.feature == "template" and ok.credits == 10
    assert ok.actual_cost_usd == 0.03 and ok.latency_s is not None and ok.intent_category == "trending"
    assert ok.consent == {"technical_improvement": True, "content_training": False}
    assert ok.retention_until > datetime.now(UTC) + timedelta(days=399) and ok.schema_version == 1
    assert ev[j2].success is False and ev[j2].error_code == "oom" and ev[j3].status == "cancelled"
    # no prompt / user text / media anywhere in the learning tables
    with get_engine().begin() as c:
        dump = c.execute(text("select row_to_json(l)::text from learning_events l")).scalars().all()
    assert not any("secret" in d or "birthday" in d or "memory://" in d for d in dump)


def test_consent_opt_out_opt_in_receipts_and_deletion(client, db, storage):
    enable(db)
    h, _ = signup(client)
    c = client.get("/me/learning-consent", headers=h).json()
    assert c["consents"] == {"technical_improvement": True, "content_training": False, "personalization": False}
    r = client.put("/me/learning-consent", headers=h, json={"technical_improvement": False, "content_training": True})
    assert r.json()["consents"]["technical_improvement"] is False
    pid = ready_profile(client, h, storage)
    t = make_template(db, cost=10)
    _complete(client, storage, _gen(client, h, t.id, pid).json()["id"], 0.0)
    assert events(db) == []  # opted out: not recorded
    client.put("/me/learning-consent", headers=h, json={"technical_improvement": True})
    _complete(client, storage, _gen(client, h, t.id, pid).json()["id"], 0.0)
    assert len(events(db)) == 1 and events(db)[0].consent["content_training"] is True
    with pytest.raises(DBAPIError):  # consent receipts are append-only
        with get_engine().begin() as conn:
            conn.execute(text("update consent_records set granted = true"))
    d = client.delete("/me/learning-data", headers=h).json()
    assert d["deleted_events"] == 1 and d["consents"]["content_training"] is False and events(db) == []
    assert client.put("/me/learning-consent", headers=h, json={}).json()["consents"]["technical_improvement"]


def test_feedback_owner_only_single_and_validated(client, db, storage):
    h, _ = signup(client)
    pid = ready_profile(client, h, storage)
    t = make_template(db, cost=10)
    jid = _gen(client, h, t.id, pid).json()["id"]
    assert client.post(f"/generations/{jid}/feedback", headers=h, json={"rating": 5}).status_code == 403
    enable(db)
    assert client.post(f"/generations/{jid}/feedback", headers=h, json={"rating": 5}).json()["detail"]["code"] == \
        "not_completed"
    _complete(client, storage, jid, 0.0)
    h2, _ = signup(client)
    assert client.post(f"/generations/{jid}/feedback", headers=h2, json={"rating": 1}).status_code == 404
    assert client.post(f"/generations/{jid}/feedback", headers=h, json={"rating": 4, "reasons": ["hack"]}
                       ).json()["detail"]["code"] == "bad_feedback"
    client.post(f"/generations/{jid}/feedback", headers=h, json={"rating": 2, "reasons": ["identity"]})
    r = client.post(f"/generations/{jid}/feedback", headers=h, json={"rating": 4, "reasons": ["great", "great"]})
    assert r.json() == {"job_id": jid, "rating": 4, "reasons": ["great"]}
    fb = events(db, "feedback")
    assert len(fb) == 1 and fb[0].rating == 4  # a repeat replaces, never adds weight


def test_technical_memory_rollup_overview_and_drift(client, db, storage):
    enable(db, min_jobs_for_drift=2)
    ah = admin(client, db)
    h, _ = signup(client)
    pid = ready_profile(client, h, storage)
    from app.models import LedgerReason
    from app.services import credits

    credits.apply(db, uuid.UUID(client.get("/me", headers=h).json()["id"]), 100,
                  LedgerReason.promo, f"p:{uuid.uuid4()}")
    db.commit()
    t = make_template(db, cost=10)
    for i in range(4):
        jid = _gen(client, h, t.id, pid).json()["id"]
        if i < 2:
            _complete(client, storage, jid, 0.02)
            client.post(f"/generations/{jid}/feedback", headers=h, json={"rating": 2})
        else:
            _fail(client, jid, 0.01)
    r = scheduler.run_task("learning_aggregate")
    assert r.status == "succeeded" and r.result["groups"] >= 1
    assert scheduler.run_task("learning_aggregate").status == "succeeded"  # idempotent re-run
    agg = db.execute(select(ModelPerformanceAggregate)).scalars().all()
    assert len(agg) == 1 and agg[0].jobs == 4 and agg[0].successes == 2 and agg[0].ratings == 2
    assert agg[0].errors == {"oom": 2} and agg[0].rating_mean == 2.0
    # previous window was healthy -> drift alerts
    old = datetime.now(UTC).date() - timedelta(days=10)
    db.add(ModelPerformanceAggregate(day=old, provider="dreamid_v", feature="template", creative_mode="balanced",
                                     jobs=10, successes=10, cost_usd=0.2, ratings=5, rating_mean=4.6, quality={},
                                     errors={}))
    db.commit()
    o = client.get("/admin/learning/overview?days=7", headers=ah).json()
    row = o["rows"][0]
    assert row["success_rate"] == 0.5 and row["rating_coverage"] == 0.5 and row["rating_mean"] == 2.0
    assert {a["type"] for a in o["alerts"]} == {"success_rate_drop", "rating_drop"}
    assert o["eligibility"]["events"] == {"job_outcome": 4, "feedback": 2}
    assert client.get("/admin/learning/overview", headers=h).status_code == 403


def test_account_deletion_removes_learning_data(client, db, storage):
    enable(db)
    h, _ = signup(client)
    pid = ready_profile(client, h, storage)
    t = make_template(db, cost=10)
    _complete(client, storage, _gen(client, h, t.id, pid).json()["id"], 0.0)
    assert len(events(db)) == 1
    assert client.request("DELETE", "/account", headers=h).status_code == 202
    assert events(db) == []
    assert WORKER_H
