"""Template V3: ingestion (rights → analysis → slots → publish), variable-slot remix, pricing/confirmation,
idempotency, IDOR, moderation takedown, aggregated metrics + server ranking, and a full worker e2e."""

import shutil
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.models import (
    FeatureFlag,
    GenerationJob,
    GenerationOutput,
    JobStatus,
    Report,
    SourceVideo,
    SourceVideoStatus,
    TemplateVersion,
    VideoPerson,
)

from .conftest import make_admin, make_template, ready_profile, signup

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "worker"))


def enable_remix(db, **value):
    f = db.get(FeatureFlag, "template_remix") or FeatureFlag(key="template_remix")
    f.enabled, f.value = True, value
    db.add(f)
    db.commit()


def admin_headers(client, db):
    h, _ = signup(client)
    make_admin(db, h, client)
    return h


def ingest(client, db, storage, ah, persons=3, flags=None, cost=10, active=False, **tpl_kw):
    """Admin ingestion with analysis simulated in the DB (the worker path is covered by the e2e test)."""
    t = make_template(db, cost=cost, active=active, **tpl_kw)
    r = client.post(f"/admin/templates/{t.id}/source-video", headers=ah,
                    json={"mime": "video/mp4", "size_bytes": 100, "rights": {"basis": "first_party"}})
    assert r.status_code == 201, r.text
    vid = uuid.UUID(r.json()["source_video_id"])
    v = db.get(SourceVideo, vid)
    v.status, v.duration_ms, v.width, v.height, v.fps = SourceVideoStatus.ready, 5000, 720, 1280, 16
    for i in range(1, persons + 1):
        bad = flags and i in flags
        db.add(VideoPerson(source_video_id=vid, track_id=i, first_frame=0, last_frame=79, coverage=0.9,
                           median_face_px=100, selectable=not bad, flags=flags.get(i, []) if flags else [],
                           stats={"worker_track_id": i}))
    db.commit()
    return t, vid


def make_version(client, ah, t, vid, slots, **kw):
    r = client.post(f"/admin/templates/{t.id}/versions-v3", headers=ah,
                    json={"source_video_id": str(vid), "slots": slots, "preferred_model": "mock_mp",
                          "fallback_model": None, **kw})
    return r


def published_remix(client, db, storage, ah, n_slots=2, **kw):
    t, vid = ingest(client, db, storage, ah, persons=max(3, n_slots))
    slots = [{"slot_id": f"p{i}", "track_id": i, "label": f"Star {i}"} for i in range(1, n_slots + 1)]
    r = make_version(client, ah, t, vid, slots, **kw)
    assert r.status_code == 201, r.text
    p = client.post(f"/admin/templates/{t.id}/publish", headers=ah, json={"version_id": r.json()["id"]})
    assert p.status_code == 200, p.text
    return t


def test_legacy_templates_unchanged(client, db, storage):
    t = make_template(db, cost=12)
    h, _ = signup(client)
    assert [x["id"] for x in client.get("/templates").json()] == [str(t.id)]
    d = client.get(f"/templates/{t.id}").json()
    assert d["mode"] == "single" and d["person_slots"] == [] and d["est_credits"] == 12
    feed = client.get("/feed").json()["items"]
    assert feed[0]["id"] == str(t.id) and feed[0]["mode"] == "single" and feed[0]["est_credits"] == 12
    est = client.post("/generations/estimate", json={"template_id": str(t.id)}, headers=h).json()
    assert est["credits"] == 12 and est["mode"] == "single"


def test_ingestion_requires_rights_and_valid_slots(client, db, storage):
    ah = admin_headers(client, db)
    t = make_template(db, active=False)
    base = {"mime": "video/mp4", "size_bytes": 100}
    r = client.post(f"/admin/templates/{t.id}/source-video", headers=ah, json={**base, "rights": {}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "rights_required"
    r = client.post(f"/admin/templates/{t.id}/source-video", headers=ah, json={**base, "rights": {"basis": "licensed"}})
    assert r.json()["detail"]["code"] == "rights_evidence_required"
    r = client.post(f"/admin/templates/{t.id}/source-video", headers=ah,
                    json={**base, "rights": {"basis": "licensed", "evidence": ["contract-42"]}})
    assert r.status_code == 201
    vid = r.json()["source_video_id"]
    r = client.post(f"/admin/templates/{t.id}/source-video/{vid}/complete", headers=ah)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "upload_missing"
    # non-admins cannot ingest
    uh, _ = signup(client)
    assert client.post(f"/admin/templates/{t.id}/source-video", headers=uh,
                       json={**base, "rights": {"basis": "first_party"}}).status_code == 403

    t2, vid2 = ingest(client, db, storage, ah, persons=3, flags={2: ["minor_suspected"]})
    r = make_version(client, ah, t2, vid2, [{"slot_id": "p1", "track_id": 1}, {"slot_id": "p2", "track_id": 2}])
    assert r.status_code == 422 and r.json()["detail"]["code"] == "track_not_selectable"
    r = make_version(client, ah, t2, vid2, [{"slot_id": "p1", "track_id": 1}, {"slot_id": "p1", "track_id": 3}])
    assert r.json()["detail"]["code"] == "duplicate_slot"
    r = make_version(client, ah, t2, vid2, [{"slot_id": "lead", "track_id": 1, "required": True},
                                            {"slot_id": "friend", "track_id": 3}])
    assert r.status_code == 201
    ver_id = r.json()["id"]
    # unpublished → invisible
    assert client.get(f"/templates/{t2.id}").status_code == 404
    assert all(i["id"] != str(t2.id) for i in client.get("/feed").json()["items"])
    p = client.post(f"/admin/templates/{t2.id}/publish", headers=ah, json={"version_id": ver_id})
    assert p.status_code == 200 and p.json()["visibility"] == "public"
    d = client.get(f"/templates/{t2.id}").json()
    assert d["mode"] == "remix" and [s["slot_id"] for s in d["person_slots"]] == ["lead", "friend"]
    card = next(i for i in client.get("/feed?category=multi_person").json()["items"] if i["id"] == str(t2.id))
    assert card["person_slots"] == 2 and card["safety_badge"] == "verified_rights"
    # published recipe is immutable (DB trigger)
    with pytest.raises(Exception, match="immutable"):
        db.execute(text("update template_versions set prompt_recipe='changed recipe!!' where id=:i"), {"i": ver_id})
        db.commit()
    db.rollback()


def test_remix_flag_pricing_idempotency_and_idor(client, db, storage):
    ah = admin_headers(client, db)
    t = published_remix(client, db, storage, ah, n_slots=2)
    h, _ = signup(client)
    prof = ready_profile(client, h, storage)
    body = {"template_id": str(t.id), "resolution": "480x832",
            "assignments": [{"slot_id": "p1", "profile_id": prof}, {"slot_id": "p2", "profile_id": prof}]}
    key = {"Idempotency-Key": uuid.uuid4().hex}
    r = client.post("/generations/remix", json=body, headers={**h, **key})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "feature_disabled"
    enable_remix(db)

    e1 = client.post("/generations/estimate", headers=h,
                     json={"template_id": str(t.id), "slots": ["p1"], "resolution": "480x832"}).json()
    e2 = client.post("/generations/estimate", headers=h,
                     json={"template_id": str(t.id), "slots": ["p1", "p2"], "resolution": "480x832"}).json()
    assert e1["credits"] == 10 and e2["credits"] == 25 and e2["mode"] == "remix"

    other_h, _ = signup(client)
    foreign = ready_profile(client, other_h, storage)
    bad = {**body, "assignments": [{"slot_id": "p1", "profile_id": foreign}]}
    assert client.post("/generations/remix", json=bad, headers={**h, "Idempotency-Key": uuid.uuid4().hex}
                       ).status_code == 404  # no IDOR oracle
    bad = {**body, "assignments": [{"slot_id": "zz", "profile_id": prof}]}
    assert client.post("/generations/remix", json=bad, headers={**h, "Idempotency-Key": uuid.uuid4().hex}
                       ).json()["detail"]["code"] == "unknown_slot"

    before = client.get("/credits", headers=h).json()["balance"]
    r1 = client.post("/generations/remix", json=body, headers={**h, **key})
    r2 = client.post("/generations/remix", json=body, headers={**h, **key})
    assert r1.status_code == 201 and r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    assert r1.json()["credit_cost"] == 25
    assert client.get("/credits", headers=h).json()["balance"] == before - 25  # debited once
    job = db.get(GenerationJob, uuid.UUID(r1.json()["id"]))
    assert job.template_version_id is not None and job.spec["template_slots"][1]["slot_id"] == "p2"
    assert job.preferred_model == "mock_mp"

    # material price → explicit confirmation required (configurable threshold)
    enable_remix(db, confirm_above_credits=20)
    h2, _ = signup(client)
    prof2 = ready_profile(client, h2, storage)
    b2 = {**body, "assignments": [{"slot_id": "p1", "profile_id": prof2}, {"slot_id": "p2", "profile_id": prof2}]}
    r = client.post("/generations/remix", json=b2, headers={**h2, "Idempotency-Key": uuid.uuid4().hex})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "confirmation_required"
    r = client.post("/generations/remix", json={**b2, "confirmed_credits": 25},
                    headers={**h2, "Idempotency-Key": uuid.uuid4().hex})
    assert r.status_code == 201


def test_required_slot_and_takedown(client, db, storage):
    ah = admin_headers(client, db)
    enable_remix(db)
    t, vid = ingest(client, db, storage, ah)
    v = make_version(client, ah, t, vid, [{"slot_id": "lead", "track_id": 1, "required": True},
                                          {"slot_id": "side", "track_id": 2}]).json()
    client.post(f"/admin/templates/{t.id}/publish", headers=ah, json={"version_id": v["id"]})
    h, _ = signup(client)
    prof = ready_profile(client, h, storage)
    r = client.post("/generations/remix", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                    json={"template_id": str(t.id), "resolution": "480x832",
                          "assignments": [{"slot_id": "side", "profile_id": prof}]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "slots_missing"
    r = client.post(f"/admin/templates/{t.id}/moderation", headers=ah,
                    json={"status": "rejected", "visibility": "blocked", "note": "rights complaint"})
    assert r.status_code == 200
    assert client.get(f"/templates/{t.id}").status_code == 404
    assert all(i["id"] != str(t.id) for i in client.get("/feed").json()["items"])
    r = client.post("/generations/remix", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                    json={"template_id": str(t.id), "assignments": [{"slot_id": "lead", "profile_id": prof}]})
    assert r.status_code == 404


def test_metrics_aggregation_and_ranking(client, db, storage):
    ah = admin_headers(client, db)
    a = make_template(db, cost=5)
    b = make_template(db, cost=5)
    c = make_template(db, cost=5)
    h, _ = signup(client)
    uid = uuid.UUID(client.get("/me", headers=h).json()["id"])
    for i in range(6):
        db.add(GenerationJob(user_id=uid, template_id=a.id, template_version_id=a.current_version_id,
                             preferred_model="m", idempotency_key=f"a{i}", credit_cost=5,
                             status=JobStatus.completed, queue_class="paid_high" if i < 2 else "free"))
    db.add(GenerationJob(user_id=uid, template_id=b.id, preferred_model="m", idempotency_key="b0", credit_cost=5,
                         status=JobStatus.failed, refunded=True))
    for _ in range(5):
        db.add(Report(template_id=c.id, reason="impersonation"))
    db.commit()
    r = client.post("/admin/templates/metrics/refresh", headers=ah)
    assert r.status_code == 200 and r.json()["templates"] == 3
    client.post("/admin/templates/metrics/refresh", headers=ah)  # idempotent upsert
    n = db.execute(text("select count(*), sum(successes), sum(paid_uses) from template_metrics_daily "
                        "where template_id=:t"), {"t": a.id}).one()
    assert tuple(n) == (1, 6, 2)
    items = client.get("/feed").json()["items"]
    ids = [i["id"] for i in items]
    assert ids[0] == str(a.id) and str(b.id) in ids
    assert str(c.id) not in ids  # report-rate safety penalty
    assert items[0]["use_count"] == 6
    assert "score" not in items[0] and "weights" not in str(items)  # weights never exposed


def _synth_template_clip(path: Path) -> bytes:
    from .test_multiperson_api import _synth_video
    return _synth_video(path)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_e2e_ingest_publish_remix_with_worker(client, db, storage, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from worker import runner
    from worker.client import ApiClient
    from worker.registry import FACTORIES

    from app.main import app

    from .test_multiperson_api import enable as enable_mp

    def fake_download(url, dest, max_bytes=0):
        dest.write_bytes(storage.objects[url.split("memory://get/", 1)[1].split("?", 1)[0]][0])
        return dest

    def fake_upload(url, path, mime):
        storage.put(url.split("memory://put/", 1)[1], Path(path).read_bytes(), mime)

    monkeypatch.setattr(runner, "download", fake_download)
    monkeypatch.setattr(runner, "upload", fake_upload)
    adapters = {"mock_mp_analyzer": FACTORIES["mock_mp_analyzer"](), "mock_mp": FACTORIES["mock_mp"]()}
    wc = TestClient(app)
    wc.headers["Authorization"] = "Bearer test-worker-token"
    api = ApiClient.__new__(ApiClient)
    api.worker_id, api.http = "tpl-worker", wc

    def work_one():
        payload = api.claim(list(adapters), "test", "cpu")
        assert payload is not None
        wd = Path(tempfile.mkdtemp())
        try:
            runner.process(api, adapters, payload, wd)
        finally:
            shutil.rmtree(wd, ignore_errors=True)

    # consumer multi-person stays DISABLED: ingestion must still work (only its analysis model config is read)
    enable_mp(db)
    db.get(FeatureFlag, "multi_person").enabled = False
    db.commit()
    enable_remix(db)
    ah = admin_headers(client, db)
    t = make_template(db, cost=10, active=False)
    r = client.post(f"/admin/templates/{t.id}/source-video", headers=ah,
                    json={"mime": "video/mp4", "size_bytes": 10, "rights": {"basis": "first_party"}}).json()
    storage.put(r["fields"]["key"], _synth_template_clip(tmp_path / "clip.mp4"), "video/mp4")
    st = client.post(f"/admin/templates/{t.id}/source-video/{r['source_video_id']}/complete", headers=ah).json()
    assert st["status"] == "analyzing"
    work_one()
    st = client.get(f"/admin/templates/{t.id}/source-video/{r['source_video_id']}", headers=ah).json()
    assert st["status"] == "ready" and len(st["tracks"]) == 3
    v = make_version(client, ah, t, r["source_video_id"],
                     [{"slot_id": "a", "track_id": 1}, {"slot_id": "b", "track_id": 2}]).json()
    client.post(f"/admin/templates/{t.id}/publish", headers=ah, json={"version_id": v["id"]})

    h, _ = signup(client)
    prof = ready_profile(client, h, storage)
    detail = client.get(f"/templates/{t.id}").json()
    assigns = [{"slot_id": s["slot_id"], "profile_id": prof} for s in detail["person_slots"]]
    job = client.post("/generations/remix", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                      json={"template_id": str(t.id), "assignments": assigns, "resolution": "480x832"}).json()
    work_one()
    g = client.get(f"/generations/{job['id']}", headers=h).json()
    assert g["status"] == "completed", g
    out = db.execute(select(GenerationOutput).where(GenerationOutput.job_id == uuid.UUID(job["id"]))).scalar_one()
    assert out.provenance["template_version_id"] == v["id"]  # output traceable to the exact version
    assert db.get(TemplateVersion, uuid.UUID(v["id"])).published_at <= datetime.now(UTC)
