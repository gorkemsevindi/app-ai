"""V6: character image provider (mock), identity QC (proxy metrics, dhash), shot identity measurement, Gemini
adapter fail-closed without configuration."""

import threading
from pathlib import Path

import cv2
import numpy as np
import pytest

from worker.adapters.base import AdapterError
from worker.characters.images import GeminiImage, MockImage
from worker.characters.qc import compare_view, dhash, shot_identity

NOP = lambda *a, **k: None  # noqa: E731


def _gen(m, tmp, spec, refs=()):
    d = tmp / spec.get("view_key", "p") / str(spec["seed"])
    d.mkdir(parents=True, exist_ok=True)
    return m.generate({"resolution": "512x512", **spec}, list(refs), d, NOP, threading.Event())[0]


def test_mock_views_are_conditioned_on_the_master(tmp_path):
    m = MockImage()
    master = _gen(m, tmp_path, {"asset_kind": "seed_preview", "seed": 7})
    other = _gen(m, tmp_path, {"asset_kind": "seed_preview", "seed": 99})
    assert dhash(cv2.imread(str(master))) != dhash(cv2.imread(str(other)))
    for view, face in (("expr_smile", True), ("yaw45_left", True), ("light_cool", True), ("rear", False)):
        v = _gen(m, tmp_path, {"asset_kind": "view", "view_key": view, "seed": 7}, [master])
        q = compare_view(v, master, face)
        assert q["proxy_similarity"] >= 0.6, (view, q)
        assert ("face_meaningful" in q) is (not face)
        assert "face_similarity" not in q  # no embedder configured -> never claimed
    # a different person is measurably less similar than the same person's expression view
    smile = compare_view(_gen(m, tmp_path, {"asset_kind": "view", "view_key": "expr_smile", "seed": 7}, [master]),
                         master, True)["proxy_similarity"]
    assert compare_view(other, master, True)["proxy_similarity"] < smile
    with pytest.raises(AdapterError):
        _gen(m, tmp_path, {"asset_kind": "view", "view_key": "rear", "seed": 1})  # no master reference


def test_shot_identity_proxy_finds_the_reference(tmp_path):
    m = MockImage()
    ref = _gen(m, tmp_path, {"asset_kind": "seed_preview", "seed": 3})
    stranger = _gen(m, tmp_path, {"asset_kind": "seed_preview", "seed": 4})
    w, h = 360, 640
    vid = tmp_path / "shot.mp4"
    wr = cv2.VideoWriter(str(vid), cv2.VideoWriter_fourcc(*"mp4v"), 12, (w, h))
    tile = cv2.resize(cv2.imread(str(ref)), (w // 4, w // 4))
    for i in range(24):
        fr = np.full((h, w, 3), (90, 60, 30), np.uint8)
        fr[70:70 + tile.shape[0], 16 + i:16 + i + tile.shape[1]] = tile
        wr.write(fr)
    wr.release()
    out = shot_identity(vid, {"mert": [ref], "ece": [stranger], "none": []})
    assert out["mert"]["method"] == "proxy" and out["mert"]["score"] > 0.8
    assert out["ece"]["score"] < out["mert"]["score"]
    assert out["none"] == {"method": "none", "score": None, "frames": 8}


def test_gemini_image_fails_closed_without_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv("CHARACTER_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    g = GeminiImage()
    assert g.healthcheck()["ok"] is False
    with pytest.raises(AdapterError) as e:
        g.generate({"prompt": "x", "resolution": "512x512"}, [], tmp_path, NOP, threading.Event())
    assert e.value.code == "provider_not_configured" and e.value.retryable is False


def test_gemini_image_uses_reference_parts_and_returns_png(tmp_path):
    ok, png = cv2.imencode(".png", np.full((8, 8, 3), 200, np.uint8))
    calls = {}

    class Part:
        def __init__(self, data):
            self.inline_data = type("D", (), {"data": data})()

    class Models:
        def generate_content(self, model, contents, config):
            calls.update(model=model, n=len(contents))
            content = type("C", (), {"parts": [Part(png.tobytes())]})()
            return type("R", (), {"candidates": [type("Cand", (), {"content": content})()]})()

    client = type("Client", (), {"models": Models()})()
    ref = tmp_path / "ref.png"
    cv2.imwrite(str(ref), np.zeros((8, 8, 3), np.uint8))
    out, info = GeminiImage(client=client, model="verified-image-model").generate(
        {"prompt": "same person, left profile", "resolution": "512x512"}, [ref], tmp_path, NOP, threading.Event())
    assert calls == {"model": "verified-image-model", "n": 2} and Path(out).exists()
    assert info["provider"] == "gemini_image" and "mock" not in info
