"""Independent provider interfaces for spec §27 capabilities. Every real model sits behind an explicit
licence/configuration gate and fails closed with `provider_not_configured` instead of silently degrading.

Capabilities: AUDIO_SEPARATION, ACTIVE_SPEAKER_DETECTION (built-in AV correlation, see stage.py),
SPEECH_LIP_SYNC, SINGING_LIP_SYNC, MULTI_SPEAKER_LIP_SYNC, EXPRESSION_TRANSFER, FACIAL_ANIMATION.

Licence notes (verified against upstream repos 2026-10-10, docs/V3_AUDIT_AND_PLAN.md §5):
- LatentSync: Apache-2.0 code, but face alignment uses InsightFace landmarks (non-commercial weights) ->
  requires LATENTSYNC_LICENSE_CLEARED=1 after the landmark model is replaced.
- MuseTalk: MIT code; dependent models (whisper, sd-vae, dwpose, S3FD) carry their own licences ->
  requires MUSETALK_LICENSE_CLEARED=1.
- Demucs: MIT. LivePortrait: MIT code, weight/detector licences unverified -> gated.
- sync.so: commercial API; not wired until a contract + network egress exist."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from ..adapters.base import AdapterError
from .core import MOUTH_BAND, SR, _band, envelope, read_wav, to_frames

SPEECH_LIP_SYNC = "SPEECH_LIP_SYNC"
SINGING_LIP_SYNC = "SINGING_LIP_SYNC"
MULTI_SPEAKER_LIP_SYNC = "MULTI_SPEAKER_LIP_SYNC"
EXPRESSION_TRANSFER = "EXPRESSION_TRANSFER"
AUDIO_SEPARATION = "AUDIO_SEPARATION"


def _not_production(name: str) -> None:
    if os.environ.get("WORKER_ENV", "dev") == "production":
        raise RuntimeError(f"{name} is a development mock and is not allowed in production")


def _licence_gate(name: str, env: str) -> None:
    if os.environ.get(env) != "1":
        raise AdapterError("provider_not_configured",
                           f"{name} is disabled until its licence blockers are cleared ({env}=1)", retryable=False)


# ---------------------------------------------------------------- separation

class SeparationProvider(Protocol):
    name: str

    def vocals(self, wav: Path, workdir: Path) -> tuple[Path, bool]: ...  # (path, actually_separated)


class NoSeparation:
    """Pass-through: activity segments then include music. Mapping confidence is reduced accordingly."""

    name = "none"

    def vocals(self, wav: Path, workdir: Path) -> tuple[Path, bool]:
        return wav, False


class DemucsSeparation:
    """Demucs (MIT) two-stem vocal separation via its CLI. Validate on the target image before enabling."""

    name = "demucs"

    def vocals(self, wav: Path, workdir: Path) -> tuple[Path, bool]:
        out = workdir / "sep"
        cmd = os.environ.get("DEMUCS_CMD", "demucs --two-stems=vocals -o {out} {wav}")
        r = subprocess.run(shlex.split(cmd.format(out=out, wav=wav)), capture_output=True, text=True,
                           timeout=int(os.environ.get("DEMUCS_TIMEOUT_S", "600")))
        hits = list(out.rglob("vocals.wav"))
        if r.returncode != 0 or not hits:
            raise AdapterError("separation_failed", (r.stderr or "")[-300:], retryable=True)
        mono = workdir / "vocals16k.wav"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(hits[0]), "-ac", "1", "-ar", str(SR),
                        str(mono)], check=True, capture_output=True)
        return mono, True


def separation_from_env() -> SeparationProvider:
    return DemucsSeparation() if os.environ.get("AUDIO_SEPARATION", "none") == "demucs" else NoSeparation()


# ---------------------------------------------------------------- lip-sync

class LipSyncProvider(Protocol):
    name: str
    capabilities: set[str]

    def apply(self, video: Path, track_boxes: dict[int, tuple], audio_wav: Path, segments: list[tuple[float, float]],
              mode: str, preserve: dict, fps: float, workdir: Path) -> Path: ...


class MockLipSync:
    """DEVELOPMENT MOCK. Darkens the mouth band in proportion to the audio envelope inside the mapped
    segments, so QC/sync measurements can be exercised end-to-end without a GPU. Not a lip-sync model."""

    name = "mock_lipsync"
    capabilities = {SPEECH_LIP_SYNC, SINGING_LIP_SYNC, MULTI_SPEAKER_LIP_SYNC}

    def __init__(self) -> None:
        _not_production(self.name)

    def apply(self, video, track_boxes, audio_wav, segments, mode, preserve, fps, workdir):
        x, sr = read_wav(audio_wav)
        cap = cv2.VideoCapture(str(video))
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        env = to_frames(envelope(x, sr), 0.01, fps, n)
        env = env / (float(env.max()) or 1.0)
        out = workdir / f"lipsync_{abs(hash(str(video))) % 10_000}.mp4"
        wr = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
        for f in range(n):
            ok, frame = cap.read()
            if not ok:
                break
            t = f / fps
            b = track_boxes.get(f)
            if b is not None and any(a <= t < e for a, e in segments):
                x0, y0, x1, y1 = _band(b, MOUTH_BAND)
                x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
                if x1 > x0 and y1 > y0:
                    open_px = int((y1 - y0) * min(1.0, env[f] * 1.2))
                    if open_px > 0:
                        cy = (y0 + y1) // 2
                        frame[max(y0, cy - open_px // 2):min(y1, cy + open_px // 2 + 1), x0:x1] = (25, 20, 60)
            wr.write(frame)
        cap.release()
        wr.release()
        return out


class CommandLipSync:
    """Self-hosted lip-sync network run as a subprocess on a face-centred crop, pasted back with a feathered
    mask (same isolation model as the identity adapters). `{video}` `{audio}` `{out}` `{repo}` `{ckpt}`
    `{python}` placeholders; the CLI must be validated on a GPU node (docs/MODEL_SETUP.md)."""

    def __init__(self, name: str, capabilities: set[str], licence_env: str, cmd_env: str, default_cmd: str):
        self.name, self.capabilities = name, capabilities
        _licence_gate(name, licence_env)
        self.repo = Path(os.environ.get(f"{name.upper()}_REPO", f"/models/{name}/repo"))
        self.ckpt = Path(os.environ.get(f"{name.upper()}_CKPT", f"/models/{name}/ckpt"))
        self.python = os.environ.get(f"{name.upper()}_PYTHON", str(self.repo / ".venv/bin/python"))
        self.cmd = os.environ.get(cmd_env, default_cmd)

    def apply(self, video, track_boxes, audio_wav, segments, mode, preserve, fps, workdir):
        if mode == "singing" and SINGING_LIP_SYNC not in self.capabilities:
            raise AdapterError("capability_unsupported", f"{self.name} has no singing lip-sync", retryable=False)
        if not self.repo.exists():
            raise AdapterError("provider_not_configured", f"{self.name} repo missing at {self.repo}", retryable=False)
        from ..multiperson.compositing import box_mask, feather
        from ..multiperson.pipeline import _crop_window

        cap = cv2.VideoCapture(str(video))
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        left, top, cw, ch = _crop_window(track_boxes, W, H)
        crop = workdir / f"{self.name}_crop.mp4"
        wr = cv2.VideoWriter(str(crop), cv2.VideoWriter_fourcc(*"mp4v"), fps, (cw - cw % 2, ch - ch % 2))
        frames = []
        for _ in range(n):
            ok, fr = cap.read()
            if not ok:
                break
            frames.append(fr)
            wr.write(fr[top:top + ch - ch % 2, left:left + cw - cw % 2])
        cap.release()
        wr.release()
        synced = workdir / f"{self.name}_out.mp4"
        cmd = self.cmd.format(python=self.python, repo=self.repo, ckpt=self.ckpt, video=crop, audio=audio_wav,
                              out=synced)
        r = subprocess.run(shlex.split(cmd), cwd=self.repo, capture_output=True, text=True,
                           timeout=int(os.environ.get("LIPSYNC_TIMEOUT_S", "1800")))
        if r.returncode != 0 or not synced.exists():
            raise AdapterError("lipsync_failed", (r.stderr or "")[-400:], retryable=True)
        out = workdir / f"{self.name}_composited.mp4"
        rc = cv2.VideoCapture(str(synced))
        wr = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
        for f, fr in enumerate(frames):
            ok, sf = rc.read()
            t = f / fps
            b = track_boxes.get(f)
            if ok and b is not None and any(a <= t < e for a, e in segments):
                full = fr.copy()
                full[top:top + sf.shape[0], left:left + sf.shape[1]] = sf
                m = feather(box_mask((H, W), b))[..., None]  # 0..1 alpha
                fr = (full * m + fr * (1 - m)).astype(np.uint8)
            wr.write(fr)
        rc.release()
        wr.release()
        return out


class NotConfiguredLipSync:
    """Commercial API placeholder (e.g. sync.so). Fails closed until contract, key and egress exist."""

    capabilities: set[str] = set()

    def __init__(self, name: str):
        self.name = name

    def apply(self, *a, **k):
        raise AdapterError("provider_not_configured", f"{self.name} lip-sync is not configured", retryable=False)


def latentsync() -> CommandLipSync:
    return CommandLipSync(
        "latentsync", {SPEECH_LIP_SYNC, MULTI_SPEAKER_LIP_SYNC}, "LATENTSYNC_LICENSE_CLEARED", "LATENTSYNC_CMD",
        "{python} -m scripts.inference --unet_config_path configs/unet/stage2.yaml "
        "--inference_ckpt_path {ckpt}/latentsync_unet.pt --video_path {video} --audio_path {audio} "
        "--video_out_path {out}")


def musetalk() -> CommandLipSync:
    return CommandLipSync(
        "musetalk", {SPEECH_LIP_SYNC, SINGING_LIP_SYNC, MULTI_SPEAKER_LIP_SYNC}, "MUSETALK_LICENSE_CLEARED",
        "MUSETALK_CMD", "{python} -m scripts.realtime_inference --video {video} --audio {audio} --out {out}")


LIPSYNC_FACTORIES = {
    "mock_lipsync": MockLipSync,
    "latentsync": latentsync,
    "musetalk": musetalk,
    "sync_so": lambda: NotConfiguredLipSync("sync_so"),
}


def lipsync_provider(name: str) -> LipSyncProvider:
    f = LIPSYNC_FACTORIES.get(name)
    if f is None:
        raise AdapterError("provider_not_configured", f"unknown lip-sync provider {name}", retryable=False)
    return f()


# ---------------------------------------------------------------- expression / facial animation

class ExpressionTransfer(Protocol):
    name: str

    def apply(self, source: Path, rendered: Path, track_boxes: dict[int, tuple], preserve: dict, workdir: Path
              ) -> Path: ...


class PassThroughExpression:
    """Identity replacement is driven frame-by-frame by the source clip, so head pose, gaze and expression
    already follow the source; QC measures it (`motion_preservation`). A dedicated model (e.g. LivePortrait,
    gated until its weight licences are verified) can re-impose them when a provider drifts."""

    name = "passthrough"

    def apply(self, source, rendered, track_boxes, preserve, workdir):
        return rendered


def expression_provider(name: str) -> ExpressionTransfer:
    if name in ("", "passthrough", None):
        return PassThroughExpression()
    if name == "liveportrait":
        _licence_gate("liveportrait", "LIVEPORTRAIT_LICENSE_CLEARED")
        raise AdapterError("provider_not_configured", "liveportrait adapter not validated yet", retryable=False)
    raise AdapterError("provider_not_configured", f"unknown expression provider {name}", retryable=False)
