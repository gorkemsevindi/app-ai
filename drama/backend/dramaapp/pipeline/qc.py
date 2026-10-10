"""Automated QC (spec §4): duration, black frames, missing audio, loudness, subtitle drift, identity
consistency, language mismatch, safety and consent flags. Output feeds the manual publish gate."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np

from ..providers.tts_espeak import read_wav, rms_envelope


def probe(mp4: Path) -> dict:
    r = subprocess.run([shutil.which("ffprobe") or "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json",
                        str(mp4)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def black_seconds(mp4: Path) -> float:
    r = subprocess.run(["ffmpeg", "-i", str(mp4), "-vf", "blackdetect=d=0.4:pix_th=0.08", "-an", "-f", "null", "-"],
                       capture_output=True, text=True)
    return round(sum(float(x) for x in re.findall(r"black_duration:([\d.]+)", r.stderr)), 2)


def integrated_loudness(mp4: Path) -> float | None:
    r = subprocess.run(["ffmpeg", "-nostats", "-i", str(mp4), "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    return float(m[-1]) if m else None


def subtitle_drift(dialogue_wav: Path, cues: list[dict]) -> float:
    """Max |caption start - detected speech onset| in seconds."""
    x, sr = read_wav(dialogue_wav)
    env = rms_envelope(x, sr)
    thr = max(1e-4, float(env.max()) * 0.06)
    worst = 0.0
    for c in cues:
        s = max(0, int((c["words"][0]["start"] - 0.3) * 100))
        e = min(len(env), int((c["start"] + c["duration"]) * 100))
        idx = np.nonzero(env[s:e] > thr)[0]
        if len(idx):
            onset = (s + idx[0]) / 100
            worst = max(worst, abs(onset - c["words"][0]["start"]))
    return round(float(worst), 3)


def run_qc(*, final_mp4: Path, dialogue_wav: Path, cues: list[dict], target_s: float, script_lang: str,
           voice_langs: set[str], identity: dict, flags: list[str]) -> dict:
    info = probe(final_mp4)
    dur = float(info["format"]["duration"])
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    black = float(black_seconds(final_mp4))
    lufs = integrated_loudness(final_mp4) if has_audio else None
    drift = subtitle_drift(dialogue_wav, cues) if cues else 0.0
    checks = [
        {"id": "duration", "ok": 0.75 * target_s <= dur <= 1.4 * target_s, "value": round(dur, 2),
         "expected": f"{0.75 * target_s:.0f}-{1.4 * target_s:.0f}s"},
        {"id": "aspect_9_16", "ok": abs(int(v["width"]) / int(v["height"]) - 9 / 16) < 0.01,
         "value": f"{v['width']}x{v['height']}"},
        {"id": "black_frames", "ok": black <= 1.5, "value": black, "expected": "<=1.5s (fades allowed)"},
        {"id": "audio_present", "ok": has_audio},
        {"id": "loudness", "ok": lufs is not None and -16.5 <= lufs <= -11.5, "value": lufs, "expected": "-14 LUFS ±2.5"},
        {"id": "subtitle_drift", "ok": drift <= 0.15, "value": drift, "expected": "<=0.15s"},
        {"id": "identity_locked", "ok": all(v_["locked_hash"] == v_["render_hash"] for v_ in identity.values()),
         "value": {k: v_["render_hash"][:10] for k, v_ in identity.items()}},
        {"id": "language_match", "ok": voice_langs <= {script_lang}, "value": sorted(voice_langs)},
        {"id": "safety_consent", "ok": not flags, "value": flags},
    ]
    return {"passed": all(c["ok"] for c in checks), "checks": checks, "duration_s": round(dur, 2)}
