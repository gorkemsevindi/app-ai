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


def frame_at(video: Path, last: bool) -> np.ndarray | None:
    cap = cv2.VideoCapture(str(video))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if last and n > 1:
        cap.set(cv2.CAP_PROP_POS_FRAMES, n - 1)
    ok, fr = cap.read()
    if last and not ok and n > 2:  # some containers report one frame too many
        cap.set(cv2.CAP_PROP_POS_FRAMES, n - 2)
        ok, fr = cap.read()
    cap.release()
    return fr if ok else None


def seam_score(boundary: Path, out: Path, direction: str) -> float | None:
    """1.0 = the extension starts (or, for a prepend, ends) on exactly the boundary frame of the source shot."""
    a = frame_at(boundary, last=direction == "end")
    b = frame_at(out, last=direction == "start")
    if a is None or b is None:
        return None
    b = cv2.resize(b, (a.shape[1], a.shape[0]))
    return round(1.0 - float(np.mean(cv2.absdiff(a, b))) / 255.0, 4)


def compose_prompt(spec: dict) -> str:
    inp = spec["inputs"]
    # V5: a creative-mode storyboard carries a derived optimized prompt; the user's original stays in `prompt`
    parts = [inp.get("style") or "", inp.get("optimized_prompt") or inp["prompt"]]
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

    def render(self, spec: dict, refs: dict[str, list[Path]], workdir: Path, progress, cancel: threading.Event,
               boundary: Path | None = None) -> tuple[Path, dict]:
        w, h = (int(x) for x in spec["resolution"].split("x"))
        ext = spec.get("extend")
        anchor = None
        if ext and boundary is not None:  # continuity: start (or end, for a prepend) on the source boundary frame
            fr = frame_at(boundary, last=ext["direction"] == "end")
            anchor = cv2.resize(fr, (w, h)) if fr is not None else None
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
            if anchor is not None:  # cross-fade from/to the boundary frame over the first/last second
                pos = f if ext["direction"] == "end" else dur * fps - 1 - f
                alpha = max(0.0, 1.0 - pos / fps)
                fr = cv2.addWeighted(anchor, alpha, fr, 1.0 - alpha, 0)
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

    def render(self, spec: dict, refs: dict[str, list[Path]], workdir: Path, progress, cancel: threading.Event,
               boundary: Path | None = None) -> tuple[Path, dict]:
        from google.genai import types

        if spec.get("extend"):  # video extension via the API is not verified for this account yet
            raise AdapterError("capability_unsupported", "Veo extension is not enabled", retryable=False)
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
