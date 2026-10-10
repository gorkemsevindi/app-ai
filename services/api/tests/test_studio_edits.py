"""V4 Stage C: conversational + timeline editing as typed operations, proposals with cost delta, apply/reject,
stale protection, undo, unsupported capabilities, and a worker e2e for split/trim (free cuts of existing
renders) and extend (boundary-frame continuation with a measured seam score)."""

import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import GenerationJob, JobKind, ModelRun
from app.services import studio_edits

from .conftest import signup
from .test_studio import enable, grant, plan, project, render

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "worker"))


def edit(client, h, pid, **body):
    r = client.post(f"/studio/projects/{pid}/edits", headers=h, json=body)
    assert r.status_code in (201, 409, 422, 502), r.text
    return r


def keys(sb):
    return [s["key"] for sc in sb["scenes"] for s in sc["shots"]]


def current(client, h, pid):
    return client.get(f"/studio/projects/{pid}", headers=h).json()["current_version"]


def test_chat_edits_clarify_unsupported_and_propose(client, db):
    enable(db)
    h, _ = signup(client)
    pid = project(client, h)
    plan(client, h, pid)
    e = edit(client, h, pid, instruction="sahneyi sil").json()
    assert e["status"] == "needs_clarification" and "Which shot" in e["clarification"]
    e = edit(client, h, pid, instruction="remove shot 9").json()
    assert e["status"] == "needs_clarification" and "3 shots" in e["clarification"]
    e = edit(client, h, pid, instruction="change the background to a blue sky").json()
    assert e["status"] == "unsupported" and e["missing_capabilities"] == ["OBJECT_EDIT"]
    e = edit(client, h, pid, instruction="make it a poem").json()
    assert e["status"] == "needs_clarification"
    # extending needs a rendered source
    r = edit(client, h, pid, instruction="2. sahneyi 4 saniye uzat")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "source_not_rendered"
    e = edit(client, h, pid, instruction="3. sahneyi 1. sahnenin önüne taşı").json()
    assert e["status"] == "proposed" and e["editor"]["label"].endswith("(not AI)")
    assert e["ops"] == [{"op": "move_shot", "shot": "sh3", "before": "sh1"}]
    assert e["diff"]["reordered"] is True and e["cost"]["delta_credits"] == 0  # nothing rendered yet: same total
    assert current(client, h, pid)["version"] == 1  # a proposal changes nothing


def test_timeline_ops_apply_reject_stale_and_undo(client, db):
    enable(db)
    h, _ = signup(client)
    pid = project(client, h)
    v1 = plan(client, h, pid)
    p1 = edit(client, h, pid, source="timeline", ops=[{"op": "remove_shot", "shot": "sh2"}]).json()
    p2 = edit(client, h, pid, source="timeline", ops=[{"op": "duplicate_shot", "shot": "sh1"}]).json()
    assert p1["diff"]["removed"] == ["sh2"] and p1["cost"]["delta_credits"] == -8
    assert p2["diff"]["added"] == ["sh1c1"] and p2["cost"]["delta_credits"] == 8
    r = client.post(f"/studio/projects/{pid}/edits/{p1['id']}/apply", headers=h).json()
    assert r["status"] == "applied" and r["version"] == 2 and keys(current(client, h, pid)["storyboard"]) == ["sh1",
                                                                                                         "sh3"]
    # p2 was proposed against v1: applying it now would silently drop p1 -> refused
    r = client.post(f"/studio/projects/{pid}/edits/{p2['id']}/apply", headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_edit"
    assert client.post(f"/studio/projects/{pid}/edits/{p2['id']}/reject", headers=h).json()["status"] == "rejected"
    assert client.post(f"/studio/projects/{pid}/edits/{p1['id']}/apply", headers=h).json()["detail"]["code"] == \
        "edit_not_applicable"
    a = edit(client, h, pid, source="timeline", auto_apply=True,
             ops=[{"op": "set_shot", "shot": "sh1", "caption": "Günaydın"},
                  {"op": "set_captions", "burn_in": True}]).json()
    assert a["status"] == "applied" and current(client, h, pid)["version"] == 3
    # undo walks back: v3 -> (copy of v2) -> (copy of v1)
    u1 = client.post(f"/studio/projects/{pid}/undo", headers=h).json()
    assert keys(current(client, h, pid)["storyboard"]) == ["sh1", "sh3"] and u1["version"] == 4
    client.post(f"/studio/projects/{pid}/undo", headers=h)
    assert current(client, h, pid)["storyboard"] == v1["storyboard"]
    assert client.post(f"/studio/projects/{pid}/undo", headers=h).json()["detail"]["code"] == "nothing_to_undo"
    edits = client.get(f"/studio/projects/{pid}/edits", headers=h).json()["items"]
    assert [x["status"] for x in edits] == ["applied", "rejected", "applied"]
    # invalid ops are rejected up front; unknown shot keys too
    assert edit(client, h, pid, ops=[{"op": "explode"}]).json()["detail"]["code"] == "bad_edit"
    assert edit(client, h, pid, ops=[{"op": "remove_shot", "shot": "nope"}]).json()["detail"]["code"] == "unknown_shot"
    assert client.post(f"/studio/projects/{pid}/shots/sh1/inpaint", headers=h).json()["detail"]["missing"] == [
        "VIDEO_INPAINT"]


class FakeGemini:
    def __init__(self, payload):
        self.payload = payload
        self.models = SimpleNamespace(generate_content=lambda model, contents, config: SimpleNamespace(
            text=self.payload if isinstance(self.payload, str) else json.dumps(self.payload)))


def test_gemini_editor_is_validated_like_timeline_ops(client, db):
    enable(db)
    h, _ = signup(client)
    pid = project(client, h)
    plan(client, h, pid)
    fake = FakeGemini({"ops": [{"op": "set_shot", "shot": "sh2", "prompt": "She runs through the rain"}]})
    studio_edits.set_editor(studio_edits.GeminiEditor("gemini-test", client=fake))
    try:
        e = edit(client, h, pid, instruction="make it rain in the second shot").json()
        assert e["status"] == "proposed" and e["diff"]["changed"] == ["sh2"] and e["editor"]["provider"] == "gemini"
        fake.payload = {"ops": [], "clarification": "Which character?"}
        assert edit(client, h, pid, instruction="make her taller").json()["clarification"] == "Which character?"
        fake.payload = {"ops": [{"op": "set_shot", "shot": "sh2", "duration_s": "long"}]}
        r = edit(client, h, pid, instruction="longer")
        assert r.status_code == 502 and r.json()["detail"]["code"] == "editor_invalid_output"
        fake.payload = "{oops"
        assert edit(client, h, pid, instruction="x y").json()["detail"]["code"] == "editor_invalid_output"
    finally:
        studio_edits.set_editor(None)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_e2e_split_trim_extend_with_worker(client, db, storage, monkeypatch):
    from fastapi.testclient import TestClient
    from worker import runner
    from worker.client import ApiClient
    from worker.registry import FACTORIES

    from app.main import app

    def fake_download(url, dest, max_bytes=0):
        dest.write_bytes(storage.objects[url.split("memory://get/", 1)[1].split("?", 1)[0]][0])
        return dest

    monkeypatch.setattr(runner, "download", fake_download)
    monkeypatch.setattr(runner, "upload", lambda url, path, mime: storage.put(
        url.split("memory://put/", 1)[1], Path(path).read_bytes(), mime))
    adapters = {n: FACTORIES[n]() for n in ("mock_t2v", "studio_assembler")}
    wc = TestClient(app)
    wc.headers["Authorization"] = "Bearer test-worker-token"
    api = ApiClient.__new__(ApiClient)
    api.worker_id, api.http = "edit-worker", wc

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
    pid = project(client, h)
    plan(client, h, pid)
    assert render(client, h, pid, 24).status_code == 202 and drain() == 4
    bal = client.get("/credits", headers=h).json()["balance"]

    # split the rendered shot 1 and trim shot 2: both are free cuts of existing renders
    e = edit(client, h, pid, instruction="split shot 1 at 2 seconds").json()
    assert e["cost"]["render_credits_after"] == 0 and e["diff"]["added"] == ["sh1b1"]
    client.post(f"/studio/projects/{pid}/edits/{e['id']}/apply", headers=h)
    e = edit(client, h, pid, instruction="3. sahneyi 3 saniyeye kısalt").json()
    assert e.get("ops", [{}])[0].get("op") == "trim_shot", e
    client.post(f"/studio/projects/{pid}/edits/{e['id']}/apply", headers=h)
    # extend the last shot by 4 s: only that new shot is charged (4 s x 2 credits)
    e = edit(client, h, pid, instruction="extend shot 4 by 4 seconds").json()
    assert e["status"] == "proposed", e
    assert e["cost"]["render_credits_after"] == 8 and e["cost"]["new_shots"] == 1, e["cost"]
    applied = client.post(f"/studio/projects/{pid}/edits/{e['id']}/apply", headers=h).json()
    sb = current(client, h, pid)["storyboard"]
    assert keys(sb) == ["sh1", "sh1b1", "sh2", "sh3", "sh3x1"]
    assert [s["duration_s"] for sc in sb["scenes"] for s in sc["shots"]] == [2, 2, 3, 4, 4]
    assert render(client, h, pid, applied["estimate"]["credits"]).status_code == 202
    assert drain() == 2  # the extension + the assembly; cuts never hit the provider
    assert client.get("/credits", headers=h).json()["balance"] == bal - 8
    p = client.get(f"/studio/projects/{pid}", headers=h).json()
    assert p["status"] == "ready" and 14500 <= p["output"]["duration_ms"] <= 15500, p
    ext_job = db.execute(select(GenerationJob).where(GenerationJob.kind == JobKind.studio_shot)
                         .order_by(GenerationJob.created_at.desc())).scalars().first()
    run = db.execute(select(ModelRun).where(ModelRun.job_id == ext_job.id)).scalar_one()
    assert run.metrics["qa"]["seam_score"] > 0.95, run.metrics  # starts on the source's last frame
    # Veo does not offer extension: the same proposal against a Veo project is refused at render time
    enable(db, shot_provider="veo", provider_usd_per_second={"veo": 0.01})
    sb2 = json.loads(json.dumps(sb))
    sb2["scenes"][0]["shots"][-1]["prompt"] = "Continuation at golden hour, wide drone shot"
    est = client.post(f"/studio/projects/{pid}/versions", headers=h, json={"storyboard": sb2}).json()["estimate"]
    assert "VIDEO_EXTEND" in est["missing_capabilities"]
