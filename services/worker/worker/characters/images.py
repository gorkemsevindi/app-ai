"""Character image providers (V6.3). The API sends one image per job: a seed preview (text-only) or an
identity-conditioned view (reference = the approved master).

- `mock_image`: dev/test only. Draws a synthetic portrait from the seed; views are derived from the master image
  (turns, expressions, mouth shapes, lighting, close-up, body), so identity QC has something real to measure.
  Labelled MOCK, refuses to run in production.
- `gemini_image`: Gemini image generation through the official google-genai SDK, reference images passed as
  image parts. NOT verified in this repository's environment: needs GEMINI_API_KEY + CHARACTER_IMAGE_MODEL (a model
  id ops verified for reference-conditioned character consistency) and remains disabled until configured."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from ..adapters.base import AdapterError, Cancelled


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(int(seed) % (2**32))


class MockImage:
    name = "mock_image"

    def __init__(self) -> None:
        if os.environ.get("WORKER_ENV", "dev") == "production":
            raise RuntimeError("mock_image is not allowed in production")

    def healthcheck(self) -> dict:
        return {"ok": True, "gpu": False}

    def _portrait(self, seed: int, size: int) -> np.ndarray:
        r = _rng(seed)
        bg = r.integers(150, 230, 3)
        img = np.empty((size, size, 3), np.uint8)
        img[:] = bg
        skin = tuple(int(x) for x in r.integers([90, 120, 150], [150, 180, 230]))
        hair = tuple(int(x) for x in r.integers(10, 120, 3))
        cx = size // 2 + int(size * r.uniform(-0.1, 0.1))
        cy = int(size * r.uniform(0.45, 0.58))
        for i in range(0, size, max(8, size // int(r.integers(6, 24)))):  # background pattern varies by seed
            cv2.line(img, (i, 0), (int(i * r.uniform(0.2, 1.8)), size), tuple(int(x) for x in bg // 1.2), 2)
        fw, fh = int(size * r.uniform(0.2, 0.26)), int(size * r.uniform(0.27, 0.33))
        cv2.ellipse(img, (cx, cy - fh // 2), (int(fw * 1.15), int(fh * 0.75)), 0, 180, 360, hair, -1)
        cv2.ellipse(img, (cx, cy), (fw, fh), 0, 0, 360, skin, -1)
        ey, ex = cy - fh // 5, int(fw * 0.45)
        eye = tuple(int(x) for x in r.integers(20, 160, 3))
        for dx in (-ex, ex):
            cv2.circle(img, (cx + dx, ey), max(4, size // 60), (255, 255, 255), -1)
            cv2.circle(img, (cx + dx, ey), max(2, size // 120), eye, -1)
        cv2.line(img, (cx, ey + fh // 8), (cx - fw // 10, cy + fh // 6), (60, 60, 90), 2)
        self._mouth(img, cx, cy + fh // 2, fw, "neutral")
        if r.random() > 0.5:  # distinctive feature: a mole
            cv2.circle(img, (cx + fw // 2, cy + fh // 4), max(2, size // 150), (40, 40, 60), -1)
        cv2.rectangle(img, (int(cx - fw * 1.4), cy + fh), (int(cx + fw * 1.4), size), hair[::-1], -1)
        return img

    @staticmethod
    def _mouth(img, cx, my, fw, shape) -> None:
        col = (60, 40, 150)
        if shape == "smile":
            cv2.ellipse(img, (cx, my - 8), (fw // 3, fw // 6), 0, 0, 180, col, 3)
        elif shape == "anger":
            cv2.line(img, (cx - fw // 3, my), (cx + fw // 3, my - 6), col, 4)
        elif shape == "sadness":
            cv2.ellipse(img, (cx, my + 8), (fw // 3, fw // 6), 0, 180, 360, col, 3)
        elif shape == "aa":
            cv2.ellipse(img, (cx, my), (fw // 5, fw // 4), 0, 0, 360, col, -1)
        elif shape == "oo":
            cv2.circle(img, (cx, my), fw // 7, col, -1)
        elif shape == "mm":
            cv2.line(img, (cx - fw // 4, my), (cx + fw // 4, my), col, 5)
        else:
            cv2.line(img, (cx - fw // 4, my), (cx + fw // 4, my), col, 3)

    def _view(self, master: np.ndarray, view: str, seed: int) -> np.ndarray:
        h, w = master.shape[:2]
        img = master.copy()
        if view.startswith("yaw45_") or view.startswith("profile_"):
            k = 0.82 if view.startswith("yaw45_") else 0.6
            sq = cv2.resize(master, (int(w * k), h))
            img = cv2.GaussianBlur(master, (0, 0), 25)  # same backdrop, softened
            x0 = (w - sq.shape[1]) // 2 + (int(w * 0.06) if view.endswith("right") else -int(w * 0.06))
            x0 = max(0, min(w - sq.shape[1], x0))
            img[:, x0:x0 + sq.shape[1]] = sq
            if view.endswith("left"):
                img = cv2.flip(img, 1)
        elif view == "rear":
            hair = tuple(int(x) for x in master[int(h * 0.3), w // 2])
            cv2.ellipse(img, (w // 2, int(h * 0.48)), (int(w * 0.25), int(h * 0.33)), 0, 0, 360, hair, -1)
        elif view.startswith("expr_") or view.startswith("mouth_"):
            shape = view.split("_", 1)[1]
            cy, fw = int(h * 0.52), int(w * 0.23)
            cv2.rectangle(img, (w // 2 - fw // 2, int(cy + h * 0.1)), (w // 2 + fw // 2, int(cy + h * 0.2)),
                          tuple(int(x) for x in master[cy, w // 2]), -1)
            self._mouth(img, w // 2, int(cy + h * 0.15), fw, shape)
        elif view == "closeup":
            c = master[int(h * 0.1):int(h * 0.9), int(w * 0.1):int(w * 0.9)]
            img = cv2.resize(c, (w, h))
        elif view in ("fullbody", "pose_walking"):
            img = cv2.GaussianBlur(master, (0, 0), 25)
            head = cv2.resize(master, (w // 3, h // 3))
            img[0:h // 3, w // 3:w // 3 + w // 3] = head
            body = tuple(int(x) for x in master[h - 5, w // 2])
            cv2.rectangle(img, (int(w * 0.38), h // 3), (int(w * 0.62), int(h * 0.7)), body, -1)
            legs = (int(w * 0.04), 0) if view == "pose_walking" else (0, 0)
            cv2.line(img, (int(w * 0.45), int(h * 0.7)), (int(w * 0.43) - legs[0], h - 10), (40, 40, 40), 12)
            cv2.line(img, (int(w * 0.55), int(h * 0.7)), (int(w * 0.57) + legs[0], h - 10), (40, 40, 40), 12)
        elif view == "light_warm":
            img = cv2.addWeighted(img, 0.85, np.full_like(img, (40, 120, 230)), 0.15, 0)
        elif view == "light_cool":
            img = cv2.addWeighted(img, 0.85, np.full_like(img, (220, 160, 90)), 0.15, 0)
        return img

    def generate(self, spec: dict, refs: list[Path], workdir: Path, progress, cancel: threading.Event
                 ) -> tuple[Path, dict]:
        if cancel.is_set():
            raise Cancelled()
        w, h = (int(x) for x in spec["resolution"].split("x"))
        if spec["asset_kind"] == "seed_preview":
            img = self._portrait(int(spec["seed"]), min(w, h))
        else:
            if not refs:
                raise AdapterError("reference_missing", "views need the master reference", retryable=False)
            master = cv2.imread(str(refs[0]))
            if master is None:
                raise AdapterError("reference_unreadable", "the master reference could not be read", retryable=False)
            img = self._view(cv2.resize(master, (min(w, h), min(w, h))), spec["view_key"], int(spec["seed"]))
        cv2.putText(img, "MOCK", (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        progress(0.8)
        out = workdir / "image.png"
        cv2.imwrite(str(out), img)
        return out, {"provider": self.name, "model": "mock_image", "mock": True}


class GeminiImage:
    """google-genai `models.generate_content` with response image parts; references as inline image parts."""

    name = "gemini_image"

    def __init__(self, client=None, model: str | None = None, retries: int = 1):
        self.model = model or os.environ.get("CHARACTER_IMAGE_MODEL")
        self._client, self.retries = client, retries

    def _c(self):
        if self._client is None:
            key = os.environ.get("GEMINI_API_KEY")
            if not key or not self.model:
                raise AdapterError("provider_not_configured", "needs GEMINI_API_KEY and CHARACTER_IMAGE_MODEL",
                                   retryable=False)
            from google import genai

            self._client = genai.Client(api_key=key)
        return self._client

    def healthcheck(self) -> dict:
        return {"ok": bool(self._client or (os.environ.get("GEMINI_API_KEY") and self.model)), "gpu": False}

    def generate(self, spec: dict, refs: list[Path], workdir: Path, progress, cancel: threading.Event
                 ) -> tuple[Path, dict]:
        from google.genai import types

        if not self.model:
            raise AdapterError("provider_not_configured", "CHARACTER_IMAGE_MODEL is not set", retryable=False)
        parts = [types.Part.from_bytes(data=p.read_bytes(), mime_type="image/png") for p in refs]
        t0 = time.time()
        if cancel.is_set():
            raise Cancelled()
        resp = self._c().models.generate_content(
            model=self.model, contents=[spec["prompt"], *parts],
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]))
        data = None
        for cand in resp.candidates or []:
            for part in (cand.content.parts if cand.content else []) or []:
                if part.inline_data is not None and part.inline_data.data:
                    data = part.inline_data.data
                    break
            if data:
                break
        if not data:
            fb = getattr(resp, "prompt_feedback", None)
            if fb is not None and getattr(fb, "block_reason", None):
                raise AdapterError("provider_blocked", "the provider's safety filter blocked this image",
                                   retryable=False)
            raise AdapterError("provider_empty", "no image returned", retryable=True)
        raw = workdir / "image_raw.bin"
        raw.write_bytes(data)
        img = cv2.imread(str(raw))
        if img is None:
            raise AdapterError("provider_bad_output", "the provider returned an unreadable image", retryable=True)
        out = workdir / "image.png"
        cv2.imwrite(str(out), img)
        progress(0.8)
        return out, {"provider": self.name, "model": self.model, "provider_seconds": round(time.time() - t0, 1)}


IMAGE_PROVIDERS = {"mock_image": MockImage, "gemini_image": GeminiImage}
