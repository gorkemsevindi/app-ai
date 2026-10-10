"""Google Gemini API media provider: Nano Banana (images) + Veo 3.1 (video with native speech audio).

This is the realistic route. It needs a Gemini API key with billing enabled (GEMINI_API_KEY).
- Images: `models.generate_content` with reference images in `contents` and `image_config`
  (aspect ratio 9:16).
- Video: `models.generate_videos` (image→video from a keyframe), a long-running operation polled via
  `operations.get`, then the file is downloaded.
Model ids are configurable and validated against the account's model list.
"""

import os
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings
from .base import CallInfo, ProviderUnavailable
from .pricing import image_price_micros, veo_price_micros_per_s


def api_key() -> str | None:
    return get_settings().gemini_api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


class ProviderError(Exception):
    """A provider call ran but failed (safety filter, quota, bad request). Retryable unless noted."""

    def __init__(self, code: str, detail: str, retryable: bool = True):
        self.code, self.detail, self.retryable = code, detail, retryable
        super().__init__(f"{code}: {detail}")


@dataclass
class VideoResult:
    path: Path
    seconds: int
    info: CallInfo


class GoogleMedia:
    id = "google_gemini"

    def __init__(self, client=None):
        if client is None:
            key = api_key()
            if not key:
                raise ProviderUnavailable("google_gemini", "GEMINI_API_KEY (Google AI Studio key, billing enabled)")
            try:
                from google import genai
            except ImportError as e:  # pragma: no cover
                raise ProviderUnavailable("google_gemini", "python package 'google-genai'") from e
            client = genai.Client(api_key=key)
        self.client = client
        self.s = get_settings()
        self._models: set[str] | None = None

    # ------------------------------------------------------------------ model validation
    def check_model(self, model: str) -> None:
        if self._models is None:
            try:
                self._models = {m.name.removeprefix("models/") for m in self.client.models.list()}
            except Exception as e:  # noqa: BLE001
                raise ProviderError("provider.auth", f"Could not list Gemini models: {e}", retryable=False) from e
        if model not in self._models:
            related = sorted(m for m in self._models if any(k in m for k in ("veo", "image", "imagen")))
            raise ProviderError("provider.model_not_found",
                                f"Model '{model}' is not available for this key. Available media models: {related}",
                                retryable=False)

    # ------------------------------------------------------------------ images
    def image(self, prompt: str, refs: list[Path], out: Path, seed: int | None = None) -> CallInfo:
        from google.genai import types

        model = self.s.image_model
        self.check_model(model)
        parts: list = [types.Part.from_bytes(data=p.read_bytes(), mime_type=_mime(p)) for p in refs]
        parts.append(prompt)
        try:
            resp = self.client.models.generate_content(
                model=model, contents=parts,
                config=types.GenerateContentConfig(response_modalities=["IMAGE"], seed=seed,
                                                   image_config=types.ImageConfig(aspect_ratio="9:16")))
        except Exception as e:  # noqa: BLE001
            raise _wrap(e) from e
        for cand in resp.candidates or []:
            for part in (cand.content.parts if cand.content else []) or []:
                if part.inline_data and part.inline_data.data:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(part.inline_data.data)
                    return CallInfo(provider=self.id, model=model, model_version=resp.model_version or "",
                                    units={"images": 1, "refs": len(refs)}, cost_usd_micros=image_price_micros(model),
                                    seed=seed)
        reason = getattr(resp.prompt_feedback, "block_reason", None) if resp.prompt_feedback else None
        raise ProviderError("provider.no_image", f"No image returned (block_reason={reason})", retryable=reason is None)

    # ------------------------------------------------------------------ video
    def video(self, prompt: str, keyframe: Path, out: Path, *, seconds: int, quality: str, seed: int | None = None,
              audio: bool = True, negative: str = "") -> VideoResult:
        from google.genai import types

        model = self.s.video_model_final if quality == "final" else self.s.video_model_preview
        self.check_model(model)
        resolution = self.s.video_resolution_final if quality == "final" else self.s.video_resolution_preview
        try:
            op = self.client.models.generate_videos(
                model=model,
                source=types.GenerateVideosSource(
                    prompt=prompt, image=types.Image(image_bytes=keyframe.read_bytes(), mime_type=_mime(keyframe))),
                config=types.GenerateVideosConfig(
                    aspect_ratio="9:16", duration_seconds=seconds, resolution=resolution, generate_audio=audio,
                    number_of_videos=1, seed=seed, negative_prompt=negative or None,
                    person_generation="allow_adult"))
            deadline = time.time() + self.s.veo_timeout_seconds
            while not op.done:
                if time.time() > deadline:
                    raise ProviderError("provider.timeout", f"Veo operation {op.name} exceeded timeout")
                time.sleep(self.s.veo_poll_seconds)
                op = self.client.operations.get(op)
        except ProviderError:
            raise
        except Exception as e:  # noqa: BLE001
            raise _wrap(e) from e
        if op.error:
            raise ProviderError("provider.video_failed", str(op.error))
        res = op.response or op.result
        vids = (res.generated_videos if res else None) or []
        if not vids:
            reasons = getattr(res, "rai_media_filtered_reasons", None) if res else None
            raise ProviderError("provider.safety_filtered", f"Veo returned no video (filtered: {reasons})",
                                retryable=False)
        v = vids[0].video
        data = v.video_bytes
        if not data:
            data = self.client.files.download(file=v) or v.video_bytes
        if not data:
            raise ProviderError("provider.download_failed", "Generated video could not be downloaded")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return VideoResult(path=out, seconds=seconds, info=CallInfo(
            provider=self.id, model=model, units={"seconds": seconds, "resolution": resolution, "audio": audio},
            cost_usd_micros=veo_price_micros_per_s(model, audio) * seconds, seed=seed))


def _mime(p: Path) -> str:
    return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(
        p.suffix.lower(), "image/png")


def _wrap(e: Exception) -> ProviderError:
    code = getattr(e, "code", None)
    msg = str(e)[:800]
    if code in (401, 403):
        return ProviderError("provider.auth", msg, retryable=False)
    if code == 429:
        return ProviderError("provider.rate_limited", msg)
    if code == 400:
        return ProviderError("provider.bad_request", msg, retryable=False)
    return ProviderError("provider.error", msg)
