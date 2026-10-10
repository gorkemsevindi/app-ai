"""AI Studio shot providers (V4 Stage B). Same adapter idea as the rest of the worker: the API routes a shot to
a provider by name (`studio.shot_provider` remote config) and this module renders one clip.

- `mock_t2v`: dev/test only. Draws a labelled synthetic clip ("MOCK"), proving queue/ledger/assembly end to end.
- `veo`: Google Veo through the official google-genai SDK. Needs GEMINI_API_KEY and VEO_MODEL (a model id that
  ops verified for this account). Reference images are not sent: that capability is not verified yet."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from ..adapters.base import AdapterError, Cancelled


def compose_prompt(spec: dict) -> str:
    inp = spec["inputs"]
    parts = [inp.get("style") or "", inp["prompt"]]
    if inp.get("camera"):
        parts.append(f"Camera: {inp['camera']}.")
    for c in inp.get("characters", []):
        if c.get("description"):
            parts.append(f"{c['name']}: {c['description']}.")
    return " ".join(p.strip() for p in parts if p and p.strip())[:2000]


class MockT2V:
    name = "mock_t2v"

    def __init__(self) -> None:
        if os.environ.get("WORKER_ENV", "dev") == "production":
            raise RuntimeError("mock_t2v is not allowed in production")

    def healthcheck(self) -> dict:
        return {"ok": True, "gpu": False}

    def render(self, spec: dict, refs: dict[str, list[Path]], workdir: Path, progress, cancel: threading.Event
               ) -> tuple[Path, dict]:
        w, h = (int(x) for x in spec["resolution"].split("x"))
        dur, fps = int(spec["shot"]["duration_s"]), 24
        seed = int(spec["content_hash"][:6], 16)
        base = np.array([(seed >> 16) & 255, (seed >> 8) & 255, seed & 255], np.uint8) // 2 + 40
        ref_img = None
        for paths in refs.values():
            for p in paths:
                img = cv2.imread(str(p))
                if img is not None:
                    ref_img = cv2.resize(img, (w // 4, w // 4))
                    break
        out = workdir / "shot_raw.mp4"
        wr = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        text = spec["shot"]["prompt"][:38]
        for f in range(dur * fps):
            if cancel.is_set():
                wr.release()
                raise Cancelled()
            fr = np.empty((h, w, 3), np.uint8)
            fr[:] = base
            x = int((f / (dur * fps)) * (w - 80)) + 40
            cv2.circle(fr, (x, h // 2), w // 10, (230, 230, 230), -1)
            cv2.putText(fr, "MOCK", (16, 48), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
            cv2.putText(fr, text, (16, h - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            if ref_img is not None:
                fr[70:70 + ref_img.shape[0], 16:16 + ref_img.shape[1]] = ref_img
            wr.write(fr)
            if f % fps == 0:
                progress(0.1 + 0.7 * f / (dur * fps))
        wr.release()
        return out, {"provider": self.name, "mock": True, "used_references": ref_img is not None}


class VeoShot:
    """google-genai: models.generate_videos -> poll operations.get -> files.download."""

    name = "veo"
    ASPECTS = {"720x1280": "9:16", "1280x720": "16:9"}

    def __init__(self, client=None, model: str | None = None, poll_s: float = 10.0, timeout_s: float = 900.0):
        self.model = model or os.environ.get("VEO_MODEL")
        self._client, self.poll_s, self.timeout_s = client, poll_s, timeout_s

    def _c(self):
        if self._client is None:
            key = os.environ.get("GEMINI_API_KEY")
            if not key or not self.model:
                raise AdapterError("provider_not_configured", "Veo needs GEMINI_API_KEY and VEO_MODEL",
                                   retryable=False)
            from google import genai

            self._client = genai.Client(api_key=key)
        return self._client

    def healthcheck(self) -> dict:
        return {"ok": bool(self._client or (os.environ.get("GEMINI_API_KEY") and self.model)), "gpu": False}

    def render(self, spec: dict, refs: dict[str, list[Path]], workdir: Path, progress, cancel: threading.Event
               ) -> tuple[Path, dict]:
        from google.genai import types

        aspect = self.ASPECTS.get(spec["resolution"])
        if aspect is None:
            raise AdapterError("capability_unsupported", f"{spec['resolution']} is not supported", retryable=False)
        if not self.model:
            raise AdapterError("provider_not_configured", "VEO_MODEL is not set", retryable=False)
        c = self._c()
        op = c.models.generate_videos(
            model=self.model, prompt=compose_prompt(spec),
            config=types.GenerateVideosConfig(aspect_ratio=aspect, number_of_videos=1,
                                              duration_seconds=int(spec["shot"]["duration_s"])))
        t0 = time.time()
        while not op.done:
            if cancel.is_set():
                raise Cancelled()
            if time.time() - t0 > self.timeout_s:
                raise AdapterError("provider_timeout", "Veo did not finish in time", retryable=True)
            progress(min(0.8, 0.1 + (time.time() - t0) / self.timeout_s))
            time.sleep(self.poll_s)
            op = c.operations.get(op)
        if op.error:
            raise AdapterError("provider_error", str(op.error)[:300], retryable=True)
        resp = op.response or op.result
        if resp is None or not resp.generated_videos:
            if resp is not None and resp.rai_media_filtered_count:
                raise AdapterError("provider_blocked", "the provider's safety filter blocked this shot",
                                   retryable=False)
            raise AdapterError("provider_empty", "no video returned", retryable=True)
        video = resp.generated_videos[0].video
        data = video.video_bytes or c.files.download(file=video)
        out = workdir / "shot_raw.mp4"
        out.write_bytes(data)
        return out, {"provider": self.name, "model": self.model, "provider_seconds": round(time.time() - t0, 1)}


SHOT_PROVIDERS = {"mock_t2v": MockT2V, "veo": VeoShot}
