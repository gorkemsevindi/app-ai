"""Phase 2: audio modes, speaker timeline, auto/manual speaker mapping, lip-sync pricing, cost ceiling,
custom licensed audio, template curated timelines, and a real worker e2e (mock lip-sync, real QC + mux)."""

import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import FeatureFlag, GenerationJob, ModelRun, SourceVideo, SourceVideoStatus, VideoPerson

from .conftest import ready_profile, signup
from .test_multiperson_api import _synth_video, create_video
from .test_multiperson_api import enable as enable_mp

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "worker"))

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 40


def enable_lip(db, **over):
    f = db.get(FeatureFlag, "lip_sync") or FeatureFlag(key="lip_sync")
    f.enabled, f.value = True, {"provider": "mock_lipsync", **over}
    db.add(f)
    db.commit()


def seed_video(db, uid, segments, persons=3):
    """Ready source video with an analysed speaker timeline (worker track ids 11, 12, 13)."""
    v = SourceVideo(user_id=uid, storage_key=f"users/{uid}/source_videos/{uuid.uuid4()}/original", mime="video/mp4",
                    declared_size=10, status=SourceVideoStatus.ready, rights_attested_at=datetime.now(UTC),
                    attestation_version="t", duration_ms=6000, width=720, height=1280, fps=24,
                    analysis={"audio": {"has_audio": True, "separated": False, "segments": segments}})
    db.add(v)
    db.flush()
    for i in range(1, persons + 1):
        db.add(VideoPerson(source_video_id=v.id, track_id=i, first_frame=0, last_frame=143, coverage=0.9,
                           median_face_px=100, selectable=True, flags=[], stats={"worker_track_id": 10 + i}))
    db.commit()
    return v


def seg(a, b, wt, conf, scores):
    return {"start_ms": a, "end_ms": b, "suggested_worker_track": wt, "confidence": conf,
            "track_scores": {str(k): v for k, v in scores.items()}}


def _uid(client, h):
    return uuid.UUID(client.get("/me", headers=h).json()["id"])


def grant(client, db, h, n=200):
    from app.models import LedgerReason
    from app.services import credits
    credits.apply(db, _uid(client, h), n, LedgerReason.promo, f"promo:{uuid.uuid4()}")
    db.commit()


def _job(client, h, vid, prof, tracks, audio=None, key=None):
    body = {"source_video_id": str(vid), "resolution": "480x832",
            "assignments": [{"track_id": t, "profile_id": prof} for t in tracks]}
    if audio is not None:
        body["audio"] = audio
    return client.post("/generations/multi", json=body, headers={**h, "Idempotency-Key": key or uuid.uuid4().hex})


def test_audio_defaults_flag_gate_and_timeline(client, db, storage):
    enable_mp(db)
    h, _ = signup(client)
    prof = ready_profile(client, h, storage)
    v = seed_video(db, _uid(client, h), [seg(0, 2000, 11, 0.9, {11: 0.8, 12: 0.1}),
                                         seg(2500, 4500, 12, 0.2, {11: 0.3, 12: 0.35})])
    sp = client.get(f"/source-videos/{v.id}/speakers", headers=h).json()
    assert sp["available"] and [s["suggested_track_id"] for s in sp["segments"]] == [1, 2]  # API track ids
    assert sp["segments"][0]["track_scores"] == {"1": 0.8, "2": 0.1}
    r = _job(client, h, v.id, prof, [1])
    assert r.status_code == 201, r.text
    job = db.get(GenerationJob, uuid.UUID(r.json()["id"]))
    assert job.spec["audio"]["audio_mode"] == "original"  # soundtrack preserved by default (spec §2.3)
    r = _job(client, h, v.id, prof, [1], audio={"lip_sync": {"enabled": True}})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "feature_disabled"
    # other users can't read the timeline
    h2, _ = signup(client)
    assert client.get(f"/source-videos/{v.id}/speakers", headers=h2).status_code == 404


def test_auto_mapping_ambiguity_manual_fix_and_pricing(client, db, storage):
    enable_mp(db)
    enable_lip(db, credits_per_second=3)
    h, _ = signup(client)
    prof = ready_profile(client, h, storage)
    grant(client, db, h)
    v = seed_video(db, _uid(client, h), [seg(0, 2000, 11, 0.9, {11: 0.8, 12: 0.1}),
                                         seg(2500, 4500, 12, 0.2, {11: 0.3, 12: 0.35}),
                                         seg(5000, 5800, 13, 0.95, {13: 0.9})])
    lip = {"lip_sync": {"enabled": True, "mode": "singing"}}
    # replacing persons 1+2: segment 2 is ambiguous between them -> must ask, never guess
    r = _job(client, h, v.id, prof, [1, 2], audio=lip)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "speaker_mapping_required"
    assert [s["start_ms"] for s in r.json()["detail"]["segments"]] == [2500]
    # replacing only person 1: seg 2 still shows person 1's mouth moving at low confidence -> ask
    r = _job(client, h, v.id, prof, [1], audio=lip)
    assert r.status_code == 409 and [s["start_ms"] for s in r.json()["detail"]["segments"]] == [2500]
    # replacing only person 3: seg 3 auto-mapped (0.8 s), segs 1-2 belong to untouched people -> skipped
    q = client.post(f"/source-videos/{v.id}/quote", headers=h,
                    json={"assignments": [{"track_id": 3, "profile_id": prof}], "resolution": "480x832",
                          "audio": lip}).json()
    assert q["breakdown"]["lip_sync"] == 3 and [m["track_id"] for m in q["speaker_mapping"]] == [3]
    # manual correction
    manual = {**lip, "speaker_mapping": [{"start_ms": 0, "end_ms": 2000, "track_id": 1},
                                         {"start_ms": 2500, "end_ms": 4500, "track_id": 2}]}
    before = client.get("/credits", headers=h).json()["balance"]
    r = _job(client, h, v.id, prof, [1, 2], audio=manual)
    assert r.status_code == 201, r.text
    job = db.get(GenerationJob, uuid.UUID(r.json()["id"]))
    m = job.spec["audio"]["speaker_mapping"]
    assert [(x["track_id"], x["worker_track_id"], x["source"]) for x in m] == [(1, 11, "manual"), (2, 12, "manual")]
    assert job.spec["audio"]["lip_sync"]["mode"] == "singing" and job.spec["audio"]["preserve"]["head_motion"]
    base = client.post(f"/source-videos/{v.id}/quote", headers=h, json={
        "assignments": [{"track_id": 1, "profile_id": prof}, {"track_id": 2, "profile_id": prof}],
        "resolution": "480x832"}).json()["credits"]
    assert job.credit_cost == base + 12  # 4 s of lip-sync x 3 credits
    assert client.get("/credits", headers=h).json()["balance"] == before - job.credit_cost
    bad = {**lip, "speaker_mapping": [{"start_ms": 0, "end_ms": 2000, "track_id": 3}]}
    assert _job(client, h, v.id, prof, [1], audio=bad).json()["detail"]["code"] == "speaker_not_replaced"
    bad = {**lip, "speaker_mapping": [{"start_ms": 0, "end_ms": 99000, "track_id": 1}]}
    assert _job(client, h, v.id, prof, [1], audio=bad).json()["detail"]["code"] == "bad_speaker_segment"


def test_cost_ceiling_and_margin_floor_block_before_debit(client, db, storage):
    enable_mp(db)
    h, _ = signup(client)
    prof = ready_profile(client, h, storage)
    v = seed_video(db, _uid(client, h), [seg(0, 2000, 11, 0.9, {11: 0.8})])
    before = client.get("/credits", headers=h).json()["balance"]
    enable_lip(db, max_job_cost_usd=0.01, provider_usd_per_second={"mock_lipsync": 0.5})
    r = _job(client, h, v.id, prof, [1], audio={"lip_sync": {"enabled": True}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "cost_ceiling_exceeded"
    enable_lip(db, margin_floor_usd=100.0)
    r = _job(client, h, v.id, prof, [1], audio={"lip_sync": {"enabled": True}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "requote_required"
    assert client.get("/credits", headers=h).json()["balance"] == before


def test_custom_audio_rights_validation_and_payload(client, db, storage):
    enable_mp(db)
    enable_lip(db)
    h, _ = signup(client)
    prof = ready_profile(client, h, storage)
    r = client.post("/audio-assets", headers=h, json={"mime": "audio/wav", "size_bytes": 60, "rights_basis": "own",
                                                       "attest_rights": False})
    assert r.status_code == 422
    bad = client.post("/audio-assets", headers=h, json={"mime": "audio/wav", "size_bytes": 60,
                                                         "rights_basis": "licensed", "attest_rights": True}).json()
    storage.put(bad["fields"]["key"], b"definitely not audio" * 3, "audio/wav")
    assert client.post(f"/audio-assets/{bad['id']}/complete", headers=h).json()["status"] == "rejected"
    a = client.post("/audio-assets", headers=h, json={"mime": "audio/wav", "size_bytes": 60, "rights_basis": "own",
                                                       "attest_rights": True}).json()
    storage.put(a["fields"]["key"], WAV, "audio/wav")
    assert client.post(f"/audio-assets/{a['id']}/complete", headers=h).json()["status"] == "ready"
    grant(client, db, h)
    v = seed_video(db, _uid(client, h), [seg(0, 2000, 11, 0.9, {11: 0.8})])
    custom = {"audio_mode": "custom", "audio_asset_id": a["id"], "lip_sync": {"enabled": True}}
    r = _job(client, h, v.id, prof, [1], audio=custom)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "speaker_mapping_required"
    r = _job(client, h, v.id, prof, [1], audio={**custom, "speaker_mapping": [
        {"start_ms": 0, "end_ms": 3000, "track_id": 1}]})
    assert r.status_code == 201, r.text
    # another user's audio is invisible (IDOR)
    h2, _ = signup(client)
    prof2 = ready_profile(client, h2, storage)
    v2 = seed_video(db, _uid(client, h2), [])
    r = _job(client, h2, v2.id, prof2, [1], audio={"audio_mode": "custom", "audio_asset_id": a["id"]})
    assert r.status_code == 404
    # worker payload carries a signed audio URL
    wc = client
    wc.headers["Authorization"] = "Bearer test-worker-token"
    payload = wc.post("/internal/worker/claim", json={"worker_id": "w1", "models": ["mock_mp"],
                                                       "gpu_provider": "t", "gpu_type": "cpu"}).json()
    assert payload["audio_url"].startswith("memory://get/") and payload["spec"]["audio"]["audio_mode"] == "custom"


def test_template_curated_timeline_by_slot(client, db, storage):
    from .test_templates_v3 import admin_headers, enable_remix, ingest, make_version

    enable_remix(db)
    enable_lip(db)
    ah = admin_headers(client, db)
    t, vid = ingest(client, db, storage, ah, persons=2)
    r = make_version(client, ah, t, vid, [{"slot_id": "lead", "track_id": 1}, {"slot_id": "duet", "track_id": 2}],
                     config={"speaker_mapping": [{"start_ms": 0, "end_ms": 2000, "slot_id": "lead"},
                                                 {"start_ms": 2000, "end_ms": 4000, "slot_id": "duet"}]})
    assert r.status_code == 201, r.text
    bad = make_version(client, ah, t, vid, [{"slot_id": "lead", "track_id": 1}],
                       config={"speaker_mapping": [{"start_ms": 0, "end_ms": 10, "slot_id": "ghost"}]})
    assert bad.json()["detail"]["code"] == "bad_speaker_mapping"
    client.post(f"/admin/templates/{t.id}/publish", headers=ah, json={"version_id": r.json()["id"]})
    h, _ = signup(client)
    prof = ready_profile(client, h, storage)
    assert client.get(f"/templates/{t.id}", headers=h).json()["lip_sync_available"] is True
    est = client.post("/generations/estimate", headers=h, json={"template_id": str(t.id), "slots": ["lead"],
                                                                "resolution": "480x832", "lip_sync": True}).json()
    assert est["breakdown"]["lip_sync"] == 6
    r = client.post("/generations/remix", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                    json={"template_id": str(t.id), "resolution": "480x832",
                          "assignments": [{"slot_id": "lead", "profile_id": prof}],
                          "audio": {"lip_sync": {"enabled": True, "mode": "singing"}}})
    assert r.status_code == 201, r.text
    job = db.get(GenerationJob, uuid.UUID(r.json()["id"]))
    m = job.spec["audio"]["speaker_mapping"]
    assert [(x["track_id"], x["source"]) for x in m] == [(1, "template")]  # 'duet' kept original -> skipped
    assert r.json()["credit_cost"] == est["credits"]


def _mux_tone(video: Path, out: Path) -> bytes:
    expr = "0.8*(0.5+0.5*sin(2*PI*4*t))*between(t,0.4,2.2)"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-f", "lavfi", "-i",
           f"sine=f=200:d=4,volume='{expr}':eval=frame", "-c:v", "copy", "-c:a", "aac", "-shortest", str(out)]
    subprocess.run(cmd, check=True)  # noqa: S603
    return out.read_bytes()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_e2e_lipsync_with_worker(client, db, storage, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from worker import runner
    from worker.audio.core import has_audio
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
    api.worker_id, api.http = "lip-worker", wc

    def work_one():
        payload = api.claim(list(adapters), "test", "cpu")
        assert payload is not None
        wd = Path(tempfile.mkdtemp())
        try:
            runner.process(api, adapters, payload, wd)
        finally:
            shutil.rmtree(wd, ignore_errors=True)

    enable_mp(db)
    enable_lip(db, min_sync_score=-1.0, max_offset_ms=400)  # mock replacer tints faces; thresholds relaxed
    h, _ = signup(client)
    prof = ready_profile(client, h, storage)
    grant(client, db, h)
    _synth_video(tmp_path / "in.mp4")
    data = _mux_tone(tmp_path / "in.mp4", tmp_path / "av.mp4")
    _, v = create_video(client, h, data=data, storage=storage)
    client.post(f"/source-videos/{v['id']}/complete", headers=h)
    work_one()  # analysis incl. audio timeline
    sp = client.get(f"/source-videos/{v['id']}/speakers", headers=h).json()
    assert sp["available"] and sp["has_audio"] and sp["segments"], sp
    audio = {"lip_sync": {"enabled": True, "mode": "speech"},
             "speaker_mapping": [{"start_ms": s["start_ms"], "end_ms": s["end_ms"], "track_id": 1}
                                 for s in sp["segments"]]}
    r = _job(client, h, v["id"], prof, [1, 2], audio=audio)
    assert r.status_code == 201, r.text
    work_one()
    g = client.get(f"/generations/{r.json()['id']}", headers=h).json()
    assert g["status"] == "completed", g
    run = db.execute(select(ModelRun).where(ModelRun.job_id == uuid.UUID(r.json()["id"]))).scalars().first()
    ls = run.metrics["qa"]["lip_sync"]
    assert ls["provider"] == "mock_lipsync" and set(ls["tracks"]) <= {"1", "2"} and ls["tracks"], ls
    out_key = g["output"]["video_url"].split("memory://get/", 1)[1].split("?", 1)[0]
    p = tmp_path / "out.mp4"
    p.write_bytes(storage.objects[out_key][0])
    assert has_audio(p)  # original soundtrack muxed into the delivered video
