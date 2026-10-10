"""Audio / lip-sync stage tests on synthetic audio-visual clips (real ffmpeg + OpenCV signal processing).

Clip: two people; person 1 "speaks" (mouth opens with the audio envelope) in 0.3–1.3 s, person 2 in
1.7–2.7 s. `lag_frames` delays the mouth relative to the audio to test sync-offset QC."""

import shutil
import subprocess
import wave
from pathlib import Path

import cv2
import numpy as np
import pytest

from worker.adapters.base import AdapterError
from worker.audio.core import best_lag, envelope, extract_wav, has_audio, voice_segments
from worker.audio.providers import MockLipSync, NoSeparation, lipsync_provider
from worker.audio.stage import analyze_audio, lipsync_and_qc, lipsync_problems
from worker.pipeline.encode import encode_vertical

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")

FPS, W, H, DUR = 24, 360, 640, 3.0
SEG = {1: (0.3, 1.3), 2: (1.7, 2.7)}
BOX = {1: (40, 120, 160, 520), 2: (200, 120, 320, 520)}


def _audio(sr=16000, music=False):
    t = np.arange(int(DUR * sr)) / sr
    x = np.zeros_like(t)
    if music:  # steady instrumental tone: no syllable structure
        x = 0.3 * np.sin(2 * np.pi * 330 * t)
    else:
        for a, b in SEG.values():
            m = (t >= a) & (t < b)
            syll = 0.5 + 0.5 * np.sin(2 * np.pi * 4.0 * (t - a))  # ~4 syllables / s
            x[m] = 0.6 * syll[m] * np.sin(2 * np.pi * 180 * t[m])
    return x.astype(np.float32), sr


def _write_wav(path, x, sr):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def make_clip(tmp: Path, lag_frames=0, music=False, mouths=True) -> tuple[Path, dict]:
    x, sr = _audio(music=music)
    env = envelope(x, sr)
    n = int(DUR * FPS)
    silent = tmp / "v.mp4"
    wr = cv2.VideoWriter(str(silent), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    for f in range(n):
        fr = np.full((H, W, 3), 60, np.uint8)
        for tid, (x1, y1, x2, y2) in BOX.items():
            cv2.rectangle(fr, (x1, y1), (x2, y2), (40, 120, 200) if tid == 1 else (200, 120, 40), -1)
            cv2.rectangle(fr, (x1 + 20, y1 + 10), (x2 - 20, y1 + 110), (190, 200, 220), -1)  # face
            src_f = f - lag_frames
            t = src_f / FPS
            a, b = SEG[tid]
            if mouths and not music and a <= t < b:
                e = float(env[min(env.size - 1, int(t / 0.01))]) / float(env.max())
                hgt = int(4 + 40 * e)
                cy = y1 + int(0.18 * (y2 - y1))
                cv2.rectangle(fr, (x1 + 40, cy - hgt // 2), (x2 - 40, cy + hgt // 2), (30, 20, 70), -1)
        wr.write(fr)
    wr.release()
    wav = tmp / "a.wav"
    _write_wav(wav, x, sr)
    clip = tmp / "clip.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-i", str(wav), "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)], check=True)
    tracks = {"persons": {str(t): {str(f): list(BOX[t]) for f in range(n)} for t in BOX}}
    return clip, tracks


def test_vad_and_lag_primitives():
    x, sr = _audio()
    segs = voice_segments(envelope(x, sr))
    assert len(segs) == 2
    assert abs(segs[0][0] - 0.3) < 0.1 and abs(segs[1][1] - 2.7) < 0.1
    a = np.sin(np.linspace(0, 12, 72))
    assert best_lag(np.roll(a, 3), a, 6)[0] == 3


def test_analysis_suggests_visible_speaker_per_segment(tmp_path):
    clip, tracks = make_clip(tmp_path)
    res = analyze_audio(clip, tracks, int(DUR * FPS), FPS, tmp_path, NoSeparation())
    assert res["has_audio"] and len(res["segments"]) == 2
    s1, s2 = res["segments"]
    assert s1["suggested_worker_track"] == 1 and s2["suggested_worker_track"] == 2
    assert s1["confidence"] > 0.4 and s2["confidence"] > 0.4
    assert res["separated"] is False  # honest: no separation provider configured


def test_music_without_vocals_is_not_confidently_mapped(tmp_path):
    clip, tracks = make_clip(tmp_path, music=True)
    res = analyze_audio(clip, tracks, int(DUR * FPS), FPS, tmp_path, NoSeparation())
    # a steady instrumental bed produces at most one long segment and no confident speaker
    assert all(s["confidence"] < 0.3 for s in res["segments"])


def test_no_audio_track(tmp_path):
    silent = tmp_path / "silent.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=gray:s={W}x{H}:d=1",
                    "-c:v", "libx264", str(silent)], check=True)
    assert not has_audio(silent) and extract_wav(silent, tmp_path / "x.wav") is None
    res = analyze_audio(silent, {"persons": {}}, 24, FPS, tmp_path, NoSeparation())
    assert res == {"has_audio": False, "segments": [], "separation": "none"}


def test_sync_offset_qc_detects_late_mouth(tmp_path):
    clip, tracks = make_clip(tmp_path, lag_frames=4)  # mouth 167 ms late
    wav = extract_wav(clip, tmp_path / "a16.wav")
    # identity provider = no-op lip-sync: measure the source as-is via the QC entry point
    from worker.audio.stage import interp_tracks, qc

    boxes = interp_tracks(tracks, int(DUR * FPS), FPS)
    r = qc(clip, clip, wav, {1: [(0.3, 1.3)]}, boxes, int(DUR * FPS), FPS, "none")["lip_sync"]
    off = r["tracks"]["1"]["sync_offset_ms"]
    assert 120 <= off <= 210, r
    assert lipsync_problems({"lip_sync": r}, max_offset_ms=120, min_score=0.2) == ["lip_sync_offset"]


def test_mock_lipsync_end_to_end_is_in_sync_and_preserves_motion(tmp_path):
    clip, tracks = make_clip(tmp_path, mouths=False)  # replaced faces lost their mouth motion
    wav = extract_wav(clip, tmp_path / "a16.wav")
    mapping = [{"start_ms": 300, "end_ms": 1300, "worker_track_id": 1},
               {"start_ms": 1700, "end_ms": 2700, "worker_track_id": 2}]
    out, qa = lipsync_and_qc(clip, clip, wav, mapping, tracks, int(DUR * FPS), FPS,
                             {"provider": "mock_lipsync", "mode": "singing"}, tmp_path)
    ls = qa["lip_sync"]
    assert ls["provider"] == "mock_lipsync" and set(ls["tracks"]) == {"1", "2"}
    assert ls["max_abs_offset_ms"] <= 50 and ls["min_score"] > 0.5, ls
    assert lipsync_problems(qa, 120, 0.25) == []
    # muxing the original soundtrack keeps an audio stream in the delivered file
    enc = encode_vertical(out, tmp_path, width=360, height=640, watermark=True, audio=clip)
    assert has_audio(enc.video)
    (tmp_path / "silent").mkdir()
    enc2 = encode_vertical(out, tmp_path / "silent", width=360, height=640, audio=None)
    assert not has_audio(enc2.video)  # default behaviour unchanged: silent unless audio is requested


def test_gated_providers_fail_closed(monkeypatch):
    monkeypatch.delenv("LATENTSYNC_LICENSE_CLEARED", raising=False)
    with pytest.raises(AdapterError) as e:
        lipsync_provider("latentsync")
    assert e.value.code == "provider_not_configured" and not e.value.retryable
    with pytest.raises(AdapterError):
        lipsync_provider("sync_so").apply()
    monkeypatch.setenv("WORKER_ENV", "production")
    with pytest.raises(RuntimeError):
        MockLipSync()
