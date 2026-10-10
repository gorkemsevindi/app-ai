"""AI Studio shot providers: mock clip, Veo adapter against a fake google-genai client (no network)."""

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from worker.adapters.base import AdapterError, Cancelled
from worker.studio.assemble import write_vtt
from worker.studio.shots import MockT2V, VeoShot, compose_prompt

SPEC = {"resolution": "720x1280", "content_hash": "a1b2c3" + "0" * 58,
        "shot": {"duration_s": 4, "prompt": "a dancer on a rooftop at sunrise"},
        "inputs": {"prompt": "a dancer on a rooftop at sunrise", "camera": "slow dolly in", "style": "cinematic",
                   "characters": [{"key": "c1", "name": "Ece", "description": "street dancer in a red jacket"}]}}


class FakeVeo:
    def __init__(self, polls=2, filtered=False, error=None):
        self.polls, self.filtered, self.error, self.calls = polls, filtered, error, []
        self.models = SimpleNamespace(generate_videos=self._gen)
        self.operations = SimpleNamespace(get=self._get)
        self.files = SimpleNamespace(download=lambda file: b"VIDEO-BYTES")

    def _op(self, done):
        vids = [] if self.filtered else [SimpleNamespace(video=SimpleNamespace(video_bytes=None, uri="u"))]
        resp = SimpleNamespace(generated_videos=vids, rai_media_filtered_count=1 if self.filtered else 0)
        return SimpleNamespace(done=done, error=self.error if done else None, response=resp if done else None,
                               result=None)

    def _gen(self, model, prompt, config):
        self.calls.append((model, prompt, config))
        return self._op(self.polls == 0)

    def _get(self, op):
        self.polls -= 1
        return self._op(self.polls <= 0)


def test_compose_prompt_includes_style_camera_and_characters():
    p = compose_prompt(SPEC)
    assert p.startswith("cinematic a dancer") and "Camera: slow dolly in." in p and "Ece: street dancer" in p


def test_veo_adapter_polls_downloads_and_maps_config(tmp_path):
    fake = FakeVeo(polls=2)
    out, info = VeoShot(client=fake, model="veo-test", poll_s=0).render(SPEC, {}, tmp_path, lambda *a: None,
                                                                         threading.Event())
    assert out.read_bytes() == b"VIDEO-BYTES" and info["model"] == "veo-test"
    model, prompt, cfg = fake.calls[0]
    assert model == "veo-test" and cfg.aspect_ratio == "9:16" and cfg.duration_seconds == 4
    assert cfg.number_of_videos == 1 and not cfg.reference_images  # references not sent (unverified capability)


def test_veo_adapter_safety_filter_timeout_cancel_and_config(tmp_path, monkeypatch):
    with pytest.raises(AdapterError) as e:
        VeoShot(client=FakeVeo(polls=0, filtered=True), model="m", poll_s=0).render(
            SPEC, {}, tmp_path, lambda *a: None, threading.Event())
    assert e.value.code == "provider_blocked" and not e.value.retryable
    with pytest.raises(AdapterError) as e:
        VeoShot(client=FakeVeo(polls=0), model="m").render({**SPEC, "resolution": "720x720"}, {}, tmp_path,
                                                           lambda *a: None, threading.Event())
    assert e.value.code == "capability_unsupported"
    with pytest.raises(AdapterError) as e:
        VeoShot(client=FakeVeo(polls=10**6), model="m", poll_s=0, timeout_s=0.0).render(
            SPEC, {}, tmp_path, lambda *a: None, threading.Event())
    assert e.value.code == "provider_timeout" and e.value.retryable
    ev = threading.Event()
    ev.set()
    with pytest.raises(Cancelled):
        VeoShot(client=FakeVeo(polls=5), model="m", poll_s=0).render(SPEC, {}, tmp_path, lambda *a: None, ev)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("VEO_MODEL", raising=False)
    with pytest.raises(AdapterError) as e:
        VeoShot().render(SPEC, {}, tmp_path, lambda *a: None, threading.Event())
    assert e.value.code == "provider_not_configured" and not e.value.retryable


def test_mock_t2v_uses_reference_and_refuses_production(tmp_path, monkeypatch):
    import cv2
    import numpy as np

    ref = tmp_path / "ref.jpg"
    cv2.imwrite(str(ref), np.full((64, 64, 3), 200, np.uint8))
    out, info = MockT2V().render(SPEC, {"c1": [ref]}, tmp_path, lambda *a: None, threading.Event())
    assert out.exists() and info["mock"] is True and info["used_references"] is True
    monkeypatch.setenv("WORKER_ENV", "production")
    with pytest.raises(RuntimeError):
        MockT2V()


def test_vtt_format(tmp_path):
    p = write_vtt([{"start_ms": 0, "end_ms": 4000, "text": "Merhaba --> dünya"},
                   {"start_ms": 3_723_004, "end_ms": 3_724_000, "text": "x"}], Path(tmp_path) / "c.vtt")
    t = p.read_text()
    assert t.startswith("WEBVTT\n") and "00:00:00.000 --> 00:00:04.000\nMerhaba → dünya" in t
    assert "01:02:03.004 --> 01:02:04.000" in t
