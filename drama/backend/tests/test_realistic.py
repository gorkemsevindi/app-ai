"""Realistic route (Nano Banana + Veo) contract tests with a fake google-genai client.

The fake mimics the SDK's response shapes; the real GoogleMedia adapter code (request building,
operation polling, download, cost accounting) runs unchanged. Outputs are test fixtures, not AI video.
A live run requires GEMINI_API_KEY (see docs/DECISIONS-NEEDED.md)."""

import io
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from PIL import Image
from sqlalchemy import func, select

from dramaapp.config import get_settings
from dramaapp.models import Episode, LedgerTransaction, ProviderCall
from dramaapp.pipeline import orchestrator as orch
from dramaapp.providers import google_media, registry
from dramaapp.worker import run_once

from .conftest import signup


class FakeGenAI:
    def __init__(self, filter_veo: bool = False):
        self.image_calls, self.video_calls = [], []
        self.filter_veo = filter_veo
        s = get_settings()
        names = [s.image_model, s.video_model_preview, s.video_model_final, "gemini-x"]
        self.models = NS(list=lambda: [NS(name=f"models/{n}") for n in names],
                         generate_content=self._image, generate_videos=self._video)
        self.operations = NS(get=self._get)
        self.files = NS(download=lambda file: file.video_bytes)

    def _image(self, *, model, contents, config):
        self.image_calls.append({"model": model, "contents": contents, "config": config})
        buf = io.BytesIO()
        Image.new("RGB", (360, 640), (40 + len(self.image_calls) * 7 % 200, 90, 120)).save(buf, "PNG")
        part = NS(inline_data=NS(data=buf.getvalue(), mime_type="image/png"))
        return NS(candidates=[NS(content=NS(parts=[part]))], model_version="fake-1", prompt_feedback=None)

    def _video(self, *, model, source, config):
        self.video_calls.append({"model": model, "source": source, "config": config})
        return NS(name=f"operations/{len(self.video_calls)}", done=False, error=None, response=None, result=None,
                  _secs=config.duration_seconds)

    def _get(self, op):
        if self.filter_veo:
            return NS(name=op.name, done=True, error=None, result=None,
                      response=NS(generated_videos=[], rai_media_filtered_reasons=["test filter"]))
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "v.mp4"
            # 9:16 clip with a voiced burst in the middle (stands in for speech)
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                            f"testsrc2=s=360x640:d={op._secs}:r=24", "-f", "lavfi", "-i",
                            f"sine=f=220:d={op._secs},volume='if(between(t,0.6,{op._secs - 0.6}),1,0.01)':eval=frame",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p)], check=True)
            data = p.read_bytes()
        return NS(name=op.name, done=True, error=None, result=None,
                  response=NS(generated_videos=[NS(video=NS(video_bytes=data, uri=None))]))


@pytest.fixture
def fake_google(monkeypatch):
    fake = FakeGenAI()
    monkeypatch.setattr(registry, "google_media", lambda: google_media.GoogleMedia(client=fake))
    s = get_settings()
    monkeypatch.setattr(s, "veo_poll_seconds", 0.0)
    return fake


def _project(client, h, lang="tr"):
    r = client.post("/studio/projects", headers=h, json={"genre": "drama", "language": lang, "episode_count": 1,
                                                         "character_count": 2, "seed": 5})
    assert r.status_code == 201, r.text
    return r.json()


def _grant(client, db, uid, credits=10_000):
    ah, _ = signup(client, role="admin", db=db)
    r = client.post(f"/admin/creators/{uid}/credits", headers=ah, json={"credits": credits, "reason": "test budget"})
    assert r.status_code == 200, r.text


def test_realistic_unavailable_without_key(client, db, monkeypatch):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    h, user = signup(client, creator=True)
    p = _project(client, h)
    est = client.post(f"/studio/episodes/{p['episodes'][0]['id']}/estimate", headers=h,
                      json={"route": "preview_2d"}).json()
    assert est["realistic_available"] == {"available": False, "missing": "GEMINI_API_KEY (Google AI Studio key, billing enabled)"}
    r = client.post("/studio/generations", headers=h, json={"episode_id": p["episodes"][0]["id"], "route": "realistic"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "provider.unavailable"


def test_realistic_episode_end_to_end(client, db, fake_google):
    h, user = signup(client, creator=True)
    _grant(client, db, user["id"])
    p = _project(client, h)
    eid = p["episodes"][0]["id"]
    est = client.post(f"/studio/episodes/{eid}/estimate", headers=h, json={"route": "realistic"}).json()
    assert est["provider_cost_usd"]["video"] > 0 and est["reference_portraits_needed"] == 2
    r = client.post("/studio/generations", headers=h, json={"episode_id": eid, "route": "realistic"})
    assert r.status_code == 202, r.text
    assert r.json()["route"] == "realistic" and r.json()["steps"][1]["name"] == "references"
    assert run_once("test")
    job = client.get(f"/studio/jobs/{r.json()['id']}", headers=h).json()
    assert job["status"] in ("succeeded", "needs_review"), (job["error_code"], job["error_detail"])
    # SDK requests are well-formed: 9:16, native audio, keyframe image attached, exact dialogue in prompt
    v0 = fake_google.video_calls[1]
    assert v0["config"].aspect_ratio == "9:16" and v0["config"].generate_audio is True
    assert v0["source"].image.image_bytes and "Turkish" in v0["source"].prompt
    assert v0["config"].person_generation == "allow_adult"
    shots = len(fake_google.video_calls)
    assert len(fake_google.image_calls) == shots + 2  # keyframes + 2 reference portraits
    kf = fake_google.image_calls[3]
    assert kf["config"].image_config.aspect_ratio == "9:16"
    assert sum(1 for c in kf["contents"] if not isinstance(c, str)) >= 1  # reference portraits attached
    ep = db.get(Episode, eid)
    db.refresh(ep)
    assert ep.status == "rendered" and 45 <= ep.duration_s <= 84
    assert not ep.provenance["mock_components"]
    assert any(m.startswith("video:google_gemini/veo") for m in ep.provenance["models"])
    checks = {c["id"]: c["ok"] for c in ep.qc_report["checks"]}
    assert checks["aspect_9_16"] and checks["audio_present"] and checks["identity_locked"]
    # cost accounting: every paid call recorded and billed exactly once
    calls = db.scalars(select(ProviderCall).where(ProviderCall.job_id == job["id"])).all()
    assert sum(c.cost_usd_micros for c in calls if c.capability in ("video", "image")) > 0
    spent = job["spent_credits"]
    assert spent <= job["max_spend_credits"]

    # re-render of the same script reuses cached refs, keyframes and clips: no new provider spend
    ep.status = "draft"
    db.commit()
    before = len(fake_google.video_calls)
    r2 = client.post("/studio/generations", headers=h, json={"episode_id": eid, "route": "realistic"})
    assert run_once("test")
    assert len(fake_google.video_calls) == before
    j2 = client.get(f"/studio/jobs/{r2.json()['id']}", headers=h).json()
    assert j2["status"] in ("succeeded", "needs_review")


def test_realistic_spend_cap_stops_before_paid_call(client, db, fake_google):
    h, user = signup(client, creator=True)
    _grant(client, db, user["id"])
    p = _project(client, h, lang="en")
    eid = p["episodes"][0]["id"]
    est = client.post(f"/studio/episodes/{eid}/estimate", headers=h, json={"route": "realistic"}).json()
    jid = client.post("/studio/generations", headers=h, json={"episode_id": eid, "route": "realistic"}).json()["id"]
    job = orch.claim_next(db, "w")
    job.max_spend_credits = est["credits"] // 3  # cap tightened after creation (e.g. admin)
    db.commit()
    orch.run_job(db, job)
    db.refresh(job)
    assert job.status == "failed" and job.error_code == "credits.max_spend_exceeded"
    assert job.output.get("refunded_credits") is not None
    n = db.scalar(select(func.count()).select_from(LedgerTransaction).where(
        LedgerTransaction.idempotency_key.like(f"refund:job:{jid}:%")))
    assert n >= 1


def test_realistic_safety_filter_fails_cleanly(client, db, monkeypatch):
    fake = FakeGenAI(filter_veo=True)
    monkeypatch.setattr(registry, "google_media", lambda: google_media.GoogleMedia(client=fake))
    monkeypatch.setattr(get_settings(), "veo_poll_seconds", 0.0)
    h, user = signup(client, creator=True)
    _grant(client, db, user["id"])
    p = _project(client, h, lang="en")
    jid = client.post("/studio/generations", headers=h, json={"episode_id": p["episodes"][0]["id"],
                                                             "route": "realistic"}).json()["id"]
    assert run_once("test")
    job = client.get(f"/studio/jobs/{jid}", headers=h).json()
    assert job["status"] == "failed" and job["error_code"] == "provider.safety_filtered"
    assert len(fake.video_calls) == 1  # non-retryable: no blind retries burning money


def test_model_not_found_names_available_models(monkeypatch):
    fake = FakeGenAI()
    fake.models.list = lambda: [NS(name="models/veo-9-new"), NS(name="models/gemini-x")]
    gm = google_media.GoogleMedia(client=fake)
    with pytest.raises(google_media.ProviderError) as e:
        gm.check_model("veo-3.1-fast-generate-preview")
    assert e.value.code == "provider.model_not_found" and "veo-9-new" in e.value.detail
