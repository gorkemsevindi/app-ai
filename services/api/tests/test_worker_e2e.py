"""Integration: real API + real worker loop (mock adapter, real ffmpeg encode) wired through the
internal worker HTTP contract. Covers upload -> generate -> result end to end without a GPU."""

import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "worker"))

from worker import runner  # noqa: E402
from worker.adapters.mock import MockAdapter  # noqa: E402
from worker.client import ApiClient  # noqa: E402
from worker.pipeline.encode import probe  # noqa: E402

from .conftest import make_template, ready_profile, signup  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")


def _api(client):
    api = ApiClient.__new__(ApiClient)
    api.worker_id = "e2e-worker"
    client.headers["Authorization"] = "Bearer test-worker-token"
    api.http = client
    return api


def _wire_storage(monkeypatch, storage):
    def fake_download(url, dest, max_bytes=0):
        key = url.split("memory://get/", 1)[1].split("?", 1)[0]
        dest.write_bytes(storage.objects[key][0])
        return dest

    def fake_upload(url, path, mime):
        storage.put(url.split("memory://put/", 1)[1], path.read_bytes(), mime)

    monkeypatch.setattr(runner, "download", fake_download)
    monkeypatch.setattr(runner, "upload", fake_upload)


def _run_one(client, storage, params=None):
    from fastapi.testclient import TestClient

    from app.main import app

    worker_http = TestClient(app)
    api = _api(worker_http)
    payload = api.claim(["mock"], "test", "cpu")
    assert payload is not None
    if params:
        payload["params"].update(params)
    wd = Path(tempfile.mkdtemp())
    try:
        runner.process(api, {"mock": MockAdapter()}, payload, wd)
    finally:
        shutil.rmtree(wd, ignore_errors=True)
    return payload


def test_upload_generate_result(client, db, storage, monkeypatch):
    _wire_storage(monkeypatch, storage)
    h, _ = signup(client)
    pid = ready_profile(client, h, storage)
    t = make_template(db, cost=10, preferred="mock", fallback=None, duration_s=2)
    jid = client.post("/generations", json={"template_id": str(t.id), "profile_id": pid},
                      headers={**h, "Idempotency-Key": uuid.uuid4().hex}).json()["id"]
    payload = _run_one(client, storage)
    g = client.get(f"/generations/{jid}", headers=h).json()
    assert g["status"] == "completed", g
    assert g["output"]["width"] == 720 and g["output"]["height"] == 1280
    data, mime = storage.objects[payload["upload"]["video"]["key"]]
    assert mime == "video/mp4" and len(data) > 1000
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(data)
        f.flush()
        info = probe(Path(f.name))
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["codec_name"], v["width"], v["height"], v["pix_fmt"]) == ("h264", 720, 1280, "yuv420p")
    assert payload["upload"]["thumbnail"]["key"] in storage.objects


def test_adapter_failure_is_retried_then_refunded(client, db, storage, monkeypatch):
    _wire_storage(monkeypatch, storage)
    h, _ = signup(client)
    pid = ready_profile(client, h, storage)
    t = make_template(db, cost=10, preferred="mock", fallback=None, duration_s=2)
    jid = client.post("/generations", json={"template_id": str(t.id), "profile_id": pid},
                      headers={**h, "Idempotency-Key": uuid.uuid4().hex}).json()["id"]
    for _ in range(3):
        _run_one(client, storage, params={"mock_fail": "oom"})
    g = client.get(f"/generations/{jid}", headers=h).json()
    assert g["status"] == "failed" and g["refunded"] and g["error_code"] == "oom"
    assert client.get("/credits", headers=h).json()["balance"] == 30
