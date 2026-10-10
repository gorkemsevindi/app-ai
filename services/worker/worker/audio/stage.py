"""Audio timeline + lip-sync stages (spec §27 pipeline):

analysis:  audio extraction → (optional) vocal separation → activity segments → per-track mouth motion →
           audio-visual active-speaker scores → speaker→track suggestions with confidence (ambiguous ones
           are flagged for manual assignment; diarization alone never decides who is visible).
replace:   identity replacement (existing) → per-track lip-sync on mapped segments → expression/head-motion
           preservation → QC: sync offset (ms), lip-sync score, motion preservation."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .core import (
    HEAD_BAND,
    HOP_S,
    articulation,
    best_lag,
    corr,
    envelope,
    extract_wav,
    read_wav,
    region_motion,
    to_frames,
    voice_segments,
)
from .providers import SeparationProvider, expression_provider, lipsync_provider


def interp_tracks(tracks: dict, n: int, fps: float) -> dict[int, dict[int, tuple]]:
    from ..multiperson.pipeline import _interp_boxes

    return {int(k): _interp_boxes(v, n, int(fps * 0.75)) for k, v in tracks["persons"].items()}


def analyze_audio(video: Path, tracks: dict, n_frames: int, fps: float, workdir: Path,
                  separation: SeparationProvider) -> dict:
    wav = extract_wav(video, workdir / "audio.wav")
    if wav is None:
        return {"has_audio": False, "segments": [], "separation": separation.name}
    vocals, separated = separation.vocals(wav, workdir)
    x, sr = read_wav(vocals)
    env = envelope(x, sr)
    segs = voice_segments(env)
    boxes = interp_tracks(tracks, n_frames, fps)
    motion = region_motion(video, boxes, n_frames)
    art = articulation(to_frames(env, HOP_S, fps, n_frames))
    out = []
    for a, b in segs:
        fa, fb = int(a * fps), min(n_frames, int(b * fps) + 1)
        scores: dict[int, float] = {}
        mean_motion = {t: float(np.nanmean(m[fa:fb])) if np.isfinite(m[fa:fb]).any() else 0.0
                       for t, m in motion.items()}
        top_motion = max(mean_motion.values() or [0.0]) or 1.0
        for t, m in motion.items():
            seg = m[fa:fb]
            if np.isfinite(seg).sum() < max(4, (fb - fa) // 3):
                continue  # not visible enough in this segment
            c = max(0.0, corr(seg, art[fa:fb]))
            scores[t] = round(0.7 * c + 0.3 * mean_motion[t] / top_motion, 4)
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        best_t, best = ranked[0] if ranked else (None, 0.0)
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        conf = 0.0 if best_t is None else float(np.clip((best - second) / max(best, 1e-6), 0, 1)
                                                * np.clip(best / 0.5, 0, 1))
        if not separated:
            conf *= 0.85  # music may be mistaken for voice without separation
        out.append({"start_ms": int(a * 1000), "end_ms": int(b * 1000), "track_scores":
                    {str(k): v for k, v in scores.items()}, "suggested_worker_track": best_t,
                    "confidence": round(conf, 3)})
    return {"has_audio": True, "separation": separation.name, "separated": separated,
            "diarization": "turns_from_activity", "asd": "av_correlation_v1", "segments": out}


def lipsync_and_qc(composited: Path, source: Path, audio_wav: Path, mapping: list[dict], tracks: dict,
                   n_frames: int, fps: float, lip_cfg: dict, workdir: Path) -> tuple[Path, dict]:
    """mapping: [{start_ms, end_ms, worker_track_id}] (only replaced persons). Returns the new video + QC."""
    boxes = interp_tracks(tracks, n_frames, fps)
    by_track: dict[int, list[tuple[float, float]]] = {}
    for m in mapping:
        by_track.setdefault(int(m["worker_track_id"]), []).append((m["start_ms"] / 1000, m["end_ms"] / 1000))
    provider = lipsync_provider(lip_cfg.get("provider", "mock_lipsync"))
    mode = lip_cfg.get("mode", "speech")
    preserve = lip_cfg.get("preserve", {})
    video = composited
    for tid, segs in by_track.items():
        if tid in boxes:
            video = provider.apply(video, boxes[tid], audio_wav, segs, mode, preserve, fps, workdir)
    video = expression_provider(lip_cfg.get("expression_provider", "passthrough")).apply(
        source, video, boxes, preserve, workdir)
    return video, qc(video, source, audio_wav, by_track, boxes, n_frames, fps, provider.name)


def qc(video: Path, source: Path, audio_wav: Path, by_track: dict[int, list[tuple[float, float]]],
       boxes: dict[int, dict[int, tuple]], n_frames: int, fps: float, provider: str) -> dict:
    x, sr = read_wav(audio_wav)
    art = articulation(to_frames(envelope(x, sr), HOP_S, fps, n_frames))
    mouth = region_motion(video, {t: boxes[t] for t in by_track if t in boxes}, n_frames)
    head_out = region_motion(video, {t: boxes[t] for t in by_track if t in boxes}, n_frames, band=HEAD_BAND)
    head_src = region_motion(source, {t: boxes[t] for t in by_track if t in boxes}, n_frames, band=HEAD_BAND)
    per = {}
    max_lag = int(round(0.3 * fps))
    for t, segs in by_track.items():
        if t not in mouth:
            continue
        sel = np.zeros(n_frames, bool)
        for a, b in segs:
            sel[int(a * fps):min(n_frames, int(b * fps) + 1)] = True
        m = np.where(sel, mouth[t], np.nan)
        au = np.where(sel, art, np.nan)
        lag, score = best_lag(np.nan_to_num(m, nan=0.0), np.nan_to_num(au, nan=0.0), max_lag)
        per[str(t)] = {"sync_offset_ms": int(round(lag / fps * 1000)), "lip_sync_score": round(float(score), 3),
                       "motion_preservation": round(corr(head_src[t], head_out[t]), 3)}
    worst_off = max((abs(v["sync_offset_ms"]) for v in per.values()), default=0)
    worst_score = min((v["lip_sync_score"] for v in per.values()), default=1.0)
    return {"lip_sync": {"provider": provider, "tracks": per, "max_abs_offset_ms": worst_off,
                         "min_score": worst_score}}


def lipsync_problems(qa: dict, max_offset_ms: int, min_score: float) -> list[str]:
    ls = qa.get("lip_sync")
    if not ls:
        return []
    problems = []
    if ls["max_abs_offset_ms"] > max_offset_ms:
        problems.append("lip_sync_offset")
    if ls["min_score"] < min_score:
        problems.append("lip_sync_low")
    return problems
