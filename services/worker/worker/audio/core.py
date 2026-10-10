"""Audio + audio-visual signal primitives (spec §27). Model-free and licence-free; model-based providers
(Demucs separation, pyannote diarization, lip-sync networks) plug in behind interfaces in `providers.py`.

Conventions: audio is mono 16 kHz float32 in [-1, 1]; envelopes use a 10 ms hop; per-video signals are
resampled to one value per video frame; boxes are person boxes (x1, y1, x2, y2) per frame index."""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

import cv2
import numpy as np

SR = 16000
HOP_S = 0.01
# Mouth band inside a *person* box, as fractions of box height/width. Person detectors return full-body
# boxes, so the mouth sits in the head's lower half. Tunable per detector via remote config.
MOUTH_BAND = (0.12, 0.24, 0.25, 0.75)  # (y0, y1, x0, x1)
HEAD_BAND = (0.0, 0.30, 0.15, 0.85)


def has_audio(video: Path) -> bool:
    if shutil.which("ffprobe") is None:
        return False
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                        "-of", "json", str(video)], capture_output=True, text=True)
    try:
        return bool(json.loads(r.stdout or "{}").get("streams"))
    except json.JSONDecodeError:
        return False


def extract_wav(media: Path, out: Path, sr: int = SR) -> Path | None:
    """Decode the first audio stream to mono 16-bit PCM. Returns None when there is no audio."""
    if not has_audio(media):
        return None
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", str(sr),
                    "-c:a", "pcm_s16le", str(out)], check=True, capture_output=True)
    return out


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def envelope(x: np.ndarray, sr: int, hop_s: float = HOP_S) -> np.ndarray:
    hop = max(1, int(sr * hop_s))
    n = len(x) // hop
    if n == 0:
        return np.zeros(0, np.float32)
    return np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1)).astype(np.float32)


def voice_segments(env: np.ndarray, hop_s: float = HOP_S, rel_thr: float = 0.15, abs_thr: float = 0.004,
                   min_len_s: float = 0.25, min_gap_s: float = 0.25) -> list[tuple[float, float]]:
    """Energy-based activity segments with hysteresis (gaps shorter than min_gap merge). This is a VAD,
    not a vocal detector: without source separation, music also counts as activity."""
    if env.size == 0 or float(env.max()) < abs_thr:
        return []
    thr = max(abs_thr, float(env.max()) * rel_thr)
    on = env > thr
    segs: list[list[int]] = []
    for i, v in enumerate(on):
        if v:
            if segs and i - segs[-1][1] <= int(min_gap_s / hop_s):
                segs[-1][1] = i
            else:
                segs.append([i, i])
    return [(a * hop_s, (b + 1) * hop_s) for a, b in segs if (b + 1 - a) * hop_s >= min_len_s]


def to_frames(env: np.ndarray, hop_s: float, fps: float, n_frames: int) -> np.ndarray:
    """Average-pool a 10 ms envelope onto video frames."""
    out = np.zeros(n_frames, np.float32)
    for f in range(n_frames):
        a, b = int(f / fps / hop_s), int((f + 1) / fps / hop_s)
        if a < env.size:
            out[f] = float(env[a:max(a + 1, min(b, env.size))].mean())
    return out


def articulation(env_frames: np.ndarray) -> np.ndarray:
    """Mouth *motion* tracks changes in loudness (syllable on/offsets), not loudness itself."""
    d = np.abs(np.diff(env_frames, prepend=env_frames[:1]))
    return np.convolve(d, np.ones(2) / 2, mode="same").astype(np.float32)


def _band(box, band) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    h, w = y2 - y1, x2 - x1
    return int(x1 + band[2] * w), int(y1 + band[0] * h), int(x1 + band[3] * w), int(y1 + band[1] * h)


def region_motion(video: Path, boxes: dict[int, dict[int, tuple]], n_frames: int, band=MOUTH_BAND
                  ) -> dict[int, np.ndarray]:
    """Per-track mean absolute temporal change inside a band of the person box (NaN when absent)."""
    out = {tid: np.full(n_frames, np.nan, np.float32) for tid in boxes}
    prev: dict[int, np.ndarray] = {}
    cap = cv2.VideoCapture(str(video))
    for f in range(n_frames):
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        H, W = gray.shape
        for tid, bx in boxes.items():
            b = bx.get(f)
            if b is None:
                prev.pop(tid, None)
                continue
            x0, y0, x1, y1 = _band(b, band)
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue
            patch = cv2.resize(gray[y0:y1, x0:x1], (32, 16), interpolation=cv2.INTER_AREA).astype(np.float32)
            if tid in prev:
                out[tid][f] = float(np.abs(patch - prev[tid]).mean())
            prev[tid] = patch
    cap.release()
    return out


def corr(a: np.ndarray, b: np.ndarray) -> float:
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 4:
        return 0.0
    x, y = a[m] - a[m].mean(), b[m] - b[m].mean()
    den = float(np.sqrt((x * x).sum() * (y * y).sum()))
    return float((x * y).sum() / den) if den > 1e-9 else 0.0


def best_lag(mouth: np.ndarray, audio: np.ndarray, max_lag: int) -> tuple[int, float]:
    """Lag (frames) maximising correlation; positive = video lags behind audio."""
    best = (0, -1.0)
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            c = corr(mouth[lag:], audio[: len(audio) - lag] if lag else audio)
        else:
            c = corr(mouth[:lag], audio[-lag:])
        if c > best[1] + 1e-6 or (abs(c - best[1]) <= 1e-6 and abs(lag) < abs(best[0])):
            best = (lag, c)
    return best
