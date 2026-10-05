"""Multi-person feature: API rules + full e2e with the real worker loop (mock models, real tracking,
compositing, encode)."""

import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

from app.models import FeatureFlag, JobKind, SourceVideo, SourceVideoStatus

from .conftest import ready_profile, signup

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "worker"))

MP4_HEAD = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64


def enable(db, **over):
    value = {"analysis_model": "mock_mp_analyzer", "preferred_model": "mock_mp", "fallback_model": None,
             "min_face_px": 10, "min_coverage": 0.1, **over}
    f = db.get(FeatureFlag, "multi_person") or FeatureFlag(key="multi_person")
    f.enabled, f.value = True, value
    db.add(f)
    db.commit()


def create_video(client, h, data=MP4_HEAD, storage=None, **kw):
    body = {"mime": "video/mp4", "size_bytes": len(data), "owns_rights": True, "people_consented": True, **kw}
    r = client.post("/source-videos", json=body, headers=h)
    if r.status_code != 201:
        return r, None
    v = r.json()
    storage.put(v["fields"]["key"], data, "video/mp4")
    return r, v


def test_feature_flag_gates_everything(client, db, storage):
    h, _ = signup(client)
    r, _ = create_video(client, h, storage=storage)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "feature_disabled"
    assert client.get("/multi-person/config").json()["enabled"] is False
    enable(db, max_persons=3)
    cfg = client.get("/multi-person/config").json()
    assert cfg["enabled"] and cfg["max_persons"] == 3


def test_attestation_required(client, db, storage):
    enable(db)
    h, _ = signup(client)
    r, _ = create_video(client, h, storage=storage, people_consented=False)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "attestation_required"


def test_upload_validation(client, db, storage):
    enable(db, max_upload_mb=1)
    h, _ = signup(client)
    r, _ = create_video(client, h, data=b"\x00" * (2 * 1024 * 1024), storage=storage)
    assert r.status_code == 413
    r, v = create_video(client, h, data=b"not a video at all, sorry" * 4, storage=storage)
    out = client.post(f"/source-videos/{v['id']}/complete", headers=h).json()
    assert out["status"] == "rejected"


def _seed_ready_video(db, user_id, persons=3, duration_ms=5000, flags=None):
    from app.models import VideoPerson

    v = SourceVideo(user_id=user_id, storage_key=f"users/{user_id}/source_videos/{uuid.uuid4()}/original",
                    mime="video/mp4", declared_size=10, status=SourceVideoStatus.ready,
                    rights_attested_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
                    attestation_version="t", duration_ms=duration_ms, width=720, height=1280, fps=16)
    db.add(v)
    db.flush()
    for i in range(1, persons + 1):
        db.add(VideoPerson(source_video_id=v.id, track_id=i, first_frame=0, last_frame=79, coverage=0.9,
                           median_face_px=100, selectable=not (flags and i in flags), flags=flags.get(i, [])
                           if flags else [], stats={"worker_track_id": i}))
    db.commit()
    return v


def _uid(client, h):
    return uuid.UUID(client.get("/me", headers=h).json()["id"])


def test_quote_scales_with_persons_duration_resolution(client, db, storage):
    enable(db, pricing={"base": 10, "per_person_second": 2, "preview": 5},
           resolutions={"480x832": 1.0, "720x1280": 1.5})
    h, _ = signup(client)
    p1 = ready_profile(client, h, storage)
    v = _seed_ready_video(db, _uid(client, h), persons=3, duration_ms=4200)
    q = lambda a, res="480x832", prev=False: client.post(  # noqa: E731
        f"/source-videos/{v.id}/quote", json={"assignments": a, "resolution": res, "preview": prev}, headers=h)
    one = q([{"track_id": 1, "profile_id": p1}]).json()["credits"]
    two = q([{"track_id": 1, "profile_id": p1}, {"track_id": 2, "profile_id": p1}]).json()["credits"]
    hd = q([{"track_id": 1, "profile_id": p1}], "720x1280").json()["credits"]
    prev = q([{"track_id": 1, "profile_id": p1}], prev=True).json()["credits"]
    assert (one, two, hd, prev) == (10 + 2 * 1 * 5, 10 + 2 * 2 * 5, 30, 5)
    assert q([{"track_id": 1, "profile_id": p1}], "1080x1920").status_code == 422


def test_assignment_rules(client, db, storage):
    enable(db, max_persons=2)
    h, _ = signup(client)
    h2, _ = signup(client)
    mine = ready_profile(client, h, storage)
    theirs = ready_profile(client, h2, storage)
    v = _seed_ready_video(db, _uid(client, h), persons=3, flags={3: ["too_small"]})

    def gen(assigns, key=None):
        return client.post("/generations/multi", json={"source_video_id": str(v.id), "assignments": assigns,
                                                       "resolution": "480x832"},
                           headers={**h, "Idempotency-Key": key or uuid.uuid4().hex})

    # someone else's identity profile can never be assigned (consent belongs to its owner)
    assert gen([{"track_id": 1, "profile_id": theirs}]).status_code == 404
    assert gen([{"track_id": 3, "profile_id": mine}]).json()["detail"]["code"] == "person_not_selectable"
    assert gen([{"track_id": 1, "profile_id": mine}] * 2).json()["detail"]["code"] == "duplicate_person"
    three = [{"track_id": i, "profile_id": mine} for i in (1, 2, 3)]
    assert gen(three).json()["detail"]["code"] == "too_many_persons"
    assert gen([{"track_id": 9, "profile_id": mine}]).json()["detail"]["code"] == "unknown_person"
    # other users can't see or use my video
    assert client.get(f"/source-videos/{v.id}", headers=h2).status_code == 404
    r = client.post("/generations/multi", json={"source_video_id": str(v.id),
                                                "assignments": [{"track_id": 1, "profile_id": theirs}]},
                    headers={**h2, "Idempotency-Key": uuid.uuid4().hex})
    assert r.status_code == 404
    ok = gen([{"track_id": 1, "profile_id": mine}, {"track_id": 2, "profile_id": mine}], key="mp-key-0001")
    assert ok.status_code == 201 and ok.json()["kind"] == "multi_replace"
    again = gen([{"track_id": 1, "profile_id": mine}, {"track_id": 2, "profile_id": mine}], key="mp-key-0001")
    assert again.status_code == 200 and again.json()["id"] == ok.json()["id"]


def test_minor_flag_rejects_video_and_logs_moderation(client, db, storage):
    from app.models import GenerationJob, ModerationAction
    from app.services import multiperson as mp

    enable(db)
    h, _ = signup(client)
    r, v = create_video(client, h, storage=storage)
    client.post(f"/source-videos/{v['id']}/complete", headers=h)
    job = db.query(GenerationJob).filter(GenerationJob.kind == JobKind.analysis).one()
    mp.apply_analysis(db, job, {"analysis": {"duration_ms": 5000, "flags": ["minor_suspected"], "persons": []}})
    db.commit()
    out = client.get(f"/source-videos/{v['id']}", headers=h).json()
    assert out["status"] == "rejected"
    assert db.query(ModerationAction).filter(ModerationAction.category == "minors").count() == 1
    assert not any(k.endswith("/original") for k in storage.objects)


def _synth_video(path: Path) -> bytes:
    """3 people: red walks in front of blue; green exits and re-enters."""
    import cv2
    import numpy as np

    w, h, fps, n = 360, 640, 16, 64
    wr = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))

    def person(f, x, y, color, ph=150):
        cv2.rectangle(f, (int(x), int(y)), (int(x + 60), int(y + ph)), color, -1)
        cv2.rectangle(f, (int(x + 15), int(y + 10)), (int(x + 45), int(y + 35)), (230, 230, 230), -1)

    for i in range(n):
        f = np.full((h, w, 3), 70, np.uint8)
        person(f, 150, 220, (220, 60, 40))
        if i < 24 or i >= 40:
            person(f, 20 if i < 24 else 260, 60, (40, 200, 40), 120)
        person(f, 10 + i * 4, 260, (40, 40, 220), 170)
        wr.write(f)
    wr.release()
    # mp4v in an .mp4 container starts with an ftyp box -> passes magic-byte sniffing
    return path.read_bytes()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_e2e_upload_analyze_assign_generate(client, db, storage, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from worker import runner
    from worker.client import ApiClient
    from worker.registry import FACTORIES

    from app.main import app

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
    api.worker_id, api.http = "mp-worker", wc

    def work_one():
        payload = api.claim(list(adapters), "test", "cpu")
        assert payload is not None
        wd = Path(tempfile.mkdtemp())
        try:
            runner.process(api, adapters, payload, wd)
        finally:
            shutil.rmtree(wd, ignore_errors=True)
        return payload

    enable(db)
    h, _ = signup(client)
    prof = ready_profile(client, h, storage)
    data = _synth_video(tmp_path / "in.mp4")
    _, v = create_video(client, h, data=data, storage=storage)
    assert client.post(f"/source-videos/{v['id']}/complete", headers=h).json()["status"] == "analyzing"
    work_one()  # analysis
    video = client.get(f"/source-videos/{v['id']}", headers=h).json()
    assert video["status"] == "ready", video
    labels = [p["label"] for p in video["persons"]]
    assert labels == ["Person 1", "Person 2", "Person 3"]
    assert all(p["thumbnail_url"] for p in video["persons"])
    reentry = [p for p in video["persons"] if "reentry" in p["flags"]]
    assert len(reentry) == 1  # green kept ONE id across exit/re-entry

    before = client.get("/credits", headers=h).json()["balance"]
    assigns = [{"track_id": p["track_id"], "profile_id": prof} for p in video["persons"][:2]]
    quote = client.post(f"/source-videos/{v['id']}/quote", json={"assignments": assigns, "resolution": "480x832"},
                        headers=h).json()["credits"]
    job = client.post("/generations/multi", json={"source_video_id": v["id"], "assignments": assigns,
                                                  "resolution": "480x832"},
                      headers={**h, "Idempotency-Key": uuid.uuid4().hex}).json()
    assert job["credit_cost"] == quote
    assert client.get("/credits", headers=h).json()["balance"] == before - quote
    work_one()  # replacement
    g = client.get(f"/generations/{job['id']}", headers=h).json()
    assert g["status"] == "completed", g
    assert g["output"]["width"] == 480 and g["output"]["height"] == 832
    assert g["output"]["watermarked"] is True  # forced for real-footage replacement
    # analysis jobs are internal: not listed in the user's generations
    kinds = {x["kind"] for x in client.get("/generations", headers=h).json()}
    assert kinds == {"multi_replace"}
    # deleting the video removes its media (original, tracks, thumbnails)
    assert client.delete(f"/source-videos/{v['id']}", headers=h).status_code == 204
    assert not any("/source_videos/" in k for k in storage.objects)
