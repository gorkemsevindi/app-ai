"""V4 Stage B: AI Studio — director, immutable versions, estimate/confirm/render, selective re-render, consent,
fail-closed pricing/capabilities, and a real worker e2e (mock shots + real ffmpeg assembly with captions/music)."""

import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from app.db import get_engine
from app.models import FeatureFlag, GenerationJob, JobKind, JobStatus, LedgerReason, StudioShotRender
from app.services import credits, studio

from .conftest import WORKER_H, ready_profile, signup

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "worker"))

BRIEF = ("A street dancer wakes up in Istanbul at dawn. She runs across the Galata bridge. "
         "She dances on a rooftop as the sun rises.")


def enable(db, **over):
    f = db.get(FeatureFlag, "studio") or FeatureFlag(key="studio")
    f.enabled, f.value = True, {"credits_per_second": {"standard": 2, "premium": 5}, **over}
    db.add(f)
    db.commit()


def _uid(client, h):
    return uuid.UUID(client.get("/me", headers=h).json()["id"])


def grant(client, db, h, n=500):
    credits.apply(db, _uid(client, h), n, LedgerReason.purchase, f"p:{uuid.uuid4()}")
    db.commit()


def project(client, h, **kw):
    r = client.post("/studio/projects", headers=h, json={"title": "Dawn", **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def plan(client, h, pid, **kw):
    r = client.post(f"/studio/projects/{pid}/storyboard", headers=h, json={"brief": BRIEF, "target_duration_s": 12,
                                                                          **kw})
    assert r.status_code == 201, r.text
    return r.json()


def render(client, h, pid, credits_=None, version_id=None, key=None):
    body = {"confirmed_credits": credits_}
    if version_id:
        body["version_id"] = version_id
    return client.post(f"/studio/projects/{pid}/render", headers={**h, "Idempotency-Key": key or uuid.uuid4().hex},
                       json=body)


def test_flag_director_label_and_estimate(client, db):
    h, _ = signup(client)
    assert client.post("/studio/projects", headers=h, json={"title": "x"}).status_code == 403
    enable(db)
    pid = project(client, h)
    out = plan(client, h, pid)
    sb, est = out["storyboard"], out["estimate"]
    assert out["director"]["provider"] == "rule_based" and "not AI" in out["director"]["label"]
    shots = [s for sc in sb["scenes"] for s in sc["shots"]]
    assert len(shots) == 3 and all(s["duration_s"] == 4 for s in shots)
    assert est["credits"] == 3 * 4 * 2 and est["new_shots"] == 3 and est["est_cost_usd"] == 0.0
    assert any("not an AI director" in x for x in est["limitations"])
    assert client.get("/credits", headers=h).json()["balance"] == 30  # planning charges nothing
    tl = client.get(f"/studio/projects/{pid}/timeline", headers=h).json()
    assert tl["duration_ms"] == 12000 and [c["start_ms"] for c in tl["tracks"]["video"]] == [0, 4000, 8000]
    h2, _ = signup(client)
    assert client.get(f"/studio/projects/{pid}", headers=h2).status_code == 404  # IDOR


class FakeGemini:
    def __init__(self, payload):
        self.payload, self.calls = payload, []
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, model, contents, config):
        self.calls.append((model, json.loads(contents), config))
        return SimpleNamespace(text=self.payload if isinstance(self.payload, str) else json.dumps(self.payload))


def _gemini_sb(**over):
    sb = {"title": "Dawn run", "language": "tr", "aspect_ratio": "16:9", "style": "cinematic",
          "characters": [{"key": "c1", "name": "Ece", "description": "dancer", "character_id": str(uuid.uuid4())}],
          "scenes": [{"key": "s1", "title": "Bridge", "shots": [
              {"key": "a", "duration_s": 6, "prompt": "Ece runs across the bridge at dawn", "characters": ["c1"],
               "dialogue": [{"character": "c1", "text": "Günaydın İstanbul!", "start_s": 1}]},
              {"key": "b", "duration_s": 4, "prompt": "Ece dances on a rooftop", "characters": ["c1"]}]}],
          "limitations": ["Likeness is not guaranteed"]}
    sb.update(over)
    return sb


def test_gemini_director_output_is_validated_and_rebound(client, db):
    enable(db)
    h, _ = signup(client)
    pid = project(client, h)
    ch = client.post("/studio/characters", headers=h, json={"name": "Ece", "description": "dancer"}).json()
    fake = FakeGemini(_gemini_sb())
    studio.set_director(studio.GeminiDirector("gemini-test-model", client=fake))
    try:
        out = plan(client, h, pid, character_ids=[ch["id"]], language="tr", aspect_ratio="9:16")
        sb = out["storyboard"]
        # model-provided ids are never trusted: characters re-bound to the user's own, aspect from the brief
        assert sb["characters"][0]["character_id"] == ch["id"] and sb["aspect_ratio"] == "9:16"
        assert out["director"]["provider"] == "gemini" and fake.calls[0][0] == "gemini-test-model"
        assert fake.calls[0][2].response_mime_type == "application/json"
        fake.payload = "not json"
        r = client.post(f"/studio/projects/{pid}/storyboard", headers=h, json={"brief": BRIEF})
        assert r.status_code == 502 and r.json()["detail"]["code"] == "director_invalid_output"
        fake.payload = _gemini_sb(scenes=[{"key": "s1", "shots": [{"key": "a", "duration_s": 5,
                                                                    "prompt": "five second shot"}]}])
        r = client.post(f"/studio/projects/{pid}/storyboard", headers=h, json={"brief": BRIEF})
        assert r.json()["detail"] == {**r.json()["detail"], "code": "director_invalid_output", "reason": "bad_duration"}
    finally:
        studio.set_director(None)
    enable(db, director="gemini")  # no model configured -> fail closed
    r = client.post(f"/studio/projects/{pid}/storyboard", headers=h, json={"brief": BRIEF})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "director_not_configured"


def test_edits_validation_and_moderation(client, db):
    enable(db)
    h, _ = signup(client)
    pid = project(client, h)
    v1 = plan(client, h, pid)
    sb = v1["storyboard"]
    bad = json.loads(json.dumps(sb))
    bad["scenes"][0]["shots"][0]["duration_s"] = 7
    r = client.post(f"/studio/projects/{pid}/versions", headers=h, json={"storyboard": bad})
    assert r.json()["detail"]["code"] == "bad_duration"
    bad = json.loads(json.dumps(sb))
    bad["scenes"][0]["shots"][0]["characters"] = ["ghost"]
    assert client.post(f"/studio/projects/{pid}/versions", headers=h,
                       json={"storyboard": bad}).json()["detail"]["code"] == "bad_storyboard"
    r = client.post(f"/studio/projects/{pid}/storyboard", headers=h,
                    json={"brief": "make a nude video of a 12 year old child please"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "content_blocked"
    # versions are immutable rows
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError):
        with get_engine().begin() as c:
            c.execute(text("update studio_project_versions set source='x'"))


def test_render_confirm_reserve_replay_and_selective_rerender(client, db):
    enable(db)
    h, _ = signup(client)
    grant(client, db, h)
    pid = project(client, h)
    v1 = plan(client, h, pid)
    r = render(client, h, pid, credits_=1)
    assert r.status_code == 409 and r.json()["detail"] == {**r.json()["detail"], "code": "confirmation_required",
                                                         "credits": 24}
    key = uuid.uuid4().hex
    r = render(client, h, pid, 24, key=key)
    assert r.status_code == 202 and len(r.json()["jobs"]) == 3, r.text
    assert client.get("/credits", headers=h).json()["balance"] == 530 - 24
    again = render(client, h, pid, 24, key=key)  # client retry with the same key
    assert again.json()["replay"] is True and client.get("/credits", headers=h).json()["balance"] == 530 - 24
    jobs = db.execute(select(GenerationJob).where(GenerationJob.kind == JobKind.studio_shot)).scalars().all()
    assert {j.billing_state for j in jobs} == {"reserved"} and all(j.studio_project_id for j in jobs)

    # edit one shot's prompt -> only that shot is new; caption-only edit -> nothing to render
    sb = json.loads(json.dumps(v1["storyboard"]))
    sb["scenes"][0]["shots"][1]["prompt"] = "She sprints across the bridge, seagulls flying"
    e = client.post(f"/studio/projects/{pid}/versions", headers=h,
                    json={"storyboard": sb, "parent_version_id": v1["version_id"]}).json()["estimate"]
    assert e["new_shots"] == 1 and e["reused_shots"] == 2 and e["credits"] == 8
    sb2 = json.loads(json.dumps(v1["storyboard"]))
    sb2["scenes"][0]["shots"][0]["caption"] = "Sabah"
    e2 = client.post(f"/studio/projects/{pid}/versions", headers=h, json={"storyboard": sb2}).json()["estimate"]
    assert e2["new_shots"] == 0 and e2["credits"] == 0
    # restore v1 creates v4 with identical shots (all reused)
    rr = client.post(f"/studio/projects/{pid}/revisions/{v1['version_id']}/restore", headers=h).json()
    assert rr["version"] == 4 and rr["estimate"]["new_shots"] == 0
    versions = client.get(f"/studio/projects/{pid}/versions", headers=h).json()["items"]
    assert [v["source"] for v in versions] == ["restore", "edit", "edit", "director"]


def test_fail_closed_pricing_capability_budget_and_ceiling(client, db, storage):
    enable(db, shot_provider="veo")
    h, _ = signup(client)
    grant(client, db, h)
    pid = project(client, h)
    plan(client, h, pid)
    assert render(client, h, pid, 0).json()["detail"]["code"] == "pricing_not_configured"
    enable(db, shot_provider="veo", provider_usd_per_second={"veo": 0.5}, max_job_cost_usd=1.0)
    assert render(client, h, pid, 24).json()["detail"]["code"] == "cost_ceiling_exceeded"
    enable(db)
    client.patch(f"/studio/projects/{pid}", headers=h, json={"budget_credits": 10})
    assert render(client, h, pid, 24).json()["detail"]["code"] == "project_budget_exceeded"
    client.patch(f"/studio/projects/{pid}", headers=h, json={"budget_credits": None})
    # a real likeness needs a provider that supports character references
    prof = ready_profile(client, h, storage)
    assert client.post("/studio/characters", headers=h, json={"name": "Me", "identity_profile_id": prof}
                       ).json()["detail"]["code"] == "consent_required"
    ch = client.post("/studio/characters", headers=h, json={"name": "Me", "identity_profile_id": prof,
                                                           "attest_own_likeness": True}).json()
    assert ch["consent"]["active"] and ch["usable"]
    enable(db, shot_provider="veo", provider_usd_per_second={"veo": 0.01})
    pid2 = project(client, h)
    plan(client, h, pid2, character_ids=[ch["id"]])
    r = render(client, h, pid2, 24)
    assert r.json()["detail"]["code"] == "capability_unsupported" and r.json()["detail"]["missing"] == [
        "CHARACTER_REFERENCE"]


def test_consent_revocation_blocks_render_and_dispatch(client, db, storage):
    enable(db)
    h, _ = signup(client)
    grant(client, db, h)
    prof = ready_profile(client, h, storage)
    ch = client.post("/studio/characters", headers=h, json={"name": "Me", "identity_profile_id": prof,
                                                           "attest_own_likeness": True}).json()
    pid = project(client, h)
    plan(client, h, pid, character_ids=[ch["id"]])
    assert render(client, h, pid, 24).status_code == 202
    before = client.get("/credits", headers=h).json()["balance"]
    r = client.delete(f"/studio/characters/{ch['id']}/consents", headers=h).json()
    assert r["consent"]["active"] is False and r["usable"] is False
    # already-queued shots are refused at dispatch: failed (final) and released
    c = client.post("/internal/worker/claim", json={"worker_id": "w", "models": ["mock_t2v"]}, headers=WORKER_H)
    assert c.status_code == 204
    failed = db.execute(select(GenerationJob).where(GenerationJob.error_code == "consent_revoked")).scalars().all()
    assert len(failed) == 1 and failed[0].refunded and failed[0].status == JobStatus.failed
    assert client.get("/credits", headers=h).json()["balance"] == before + 8
    assert client.get(f"/studio/projects/{pid}", headers=h).json()["status"] == "failed"
    pid2 = project(client, h)
    plan(client, h, pid2, character_ids=[ch["id"]])
    assert render(client, h, pid2, 24).json()["detail"]["code"] == "consent_required"
    # explicit re-consent restores usability
    client.post(f"/studio/characters/{ch['id']}/consents?attest_own_likeness=true", headers=h)
    assert client.get("/studio/characters", headers=h).json()["items"][0]["usable"] is True


def _tone(path: Path) -> bytes:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=330:d=20", "-c:a", "libmp3lame",
           str(path)]
    subprocess.run(cmd, check=True)  # noqa: S603
    return path.read_bytes()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_e2e_studio_with_worker(client, db, storage, monkeypatch, tmp_path):
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
    adapters = {n: FACTORIES[n]() for n in ("mock_t2v", "studio_assembler")}
    wc = TestClient(app)
    wc.headers["Authorization"] = "Bearer test-worker-token"
    api = ApiClient.__new__(ApiClient)
    api.worker_id, api.http = "studio-worker", wc

    def drain():
        n = 0
        while (payload := api.claim(list(adapters), "test", "cpu")) is not None:
            wd = Path(tempfile.mkdtemp())
            try:
                runner.process(api, adapters, payload, wd)
            finally:
                shutil.rmtree(wd, ignore_errors=True)
            n += 1
        return n

    enable(db)
    h, _ = signup(client)
    grant(client, db, h)
    a = client.post("/audio-assets", headers=h, json={"mime": "audio/mpeg", "size_bytes": 100, "rights_basis": "own",
                                                       "attest_rights": True}).json()
    storage.put(a["fields"]["key"], _tone(tmp_path / "m.mp3"), "audio/mpeg")
    assert client.post(f"/audio-assets/{a['id']}/complete", headers=h).json()["status"] == "ready"
    pid = project(client, h)
    v1 = plan(client, h, pid, music_asset_id=a["id"])
    assert render(client, h, pid, 24).status_code == 202
    assert drain() == 4  # 3 shots, then the assembly queued automatically
    p = client.get(f"/studio/projects/{pid}", headers=h).json()
    assert p["status"] == "ready" and [s["status"] for s in p["shots"]] == ["ready"] * 3, p
    out = p["output"]
    assert out["duration_ms"] >= 11500 and out["watermarked"] is True and out["captions_url"]
    vid = tmp_path / "film.mp4"
    vid.write_bytes(storage.objects[out["video_url"].split("memory://get/", 1)[1].split("?", 1)[0]][0])
    assert has_audio(vid)  # licensed music bed mixed in
    vtt = storage.objects[out["captions_url"].split("memory://get/", 1)[1].split("?", 1)[0]][0].decode()
    assert vtt.startswith("WEBVTT") and "00:00:04.000 --> 00:00:08.000" in vtt
    shot_jobs = db.execute(select(GenerationJob).where(GenerationJob.kind == JobKind.studio_shot)).scalars().all()
    assert {j.billing_state for j in shot_jobs} == {"settled"}

    # caption-only edit with burn-in: 0 credits, only a new assembly runs
    sb = json.loads(json.dumps(v1["storyboard"]))
    sb["scenes"][0]["shots"][2]["caption"] = "Gün doğumu"
    sb["captions"]["burn_in"] = True
    e = client.post(f"/studio/projects/{pid}/versions", headers=h, json={"storyboard": sb}).json()["estimate"]
    assert e["credits"] == 0
    bal = client.get("/credits", headers=h).json()["balance"]
    assert render(client, h, pid, 0).status_code == 202
    assert drain() == 1
    p2 = client.get(f"/studio/projects/{pid}", headers=h).json()
    assert p2["status"] == "ready" and p2["output"]["version_id"] == e["version_id"]
    assert client.get("/credits", headers=h).json()["balance"] == bal
    assert len(db.execute(select(StudioShotRender)).scalars().all()) == 3
