"""procedural_local: deterministic score synthesis (original by construction → royalty-cleared).

Generates a mood-dependent chord progression with pad, pulse bass and optional soft percussion. Also
renders location ambience beds (room tone, rain, city, ...)."""

import wave
from pathlib import Path

import numpy as np

from .base import CallInfo

SR = 44100
# mood -> (bpm, root midi, mode intervals for i..vii, progression degrees, percussion)
MOODS = {
    "tense": (84, 50, [0, 2, 3, 5, 7, 8, 10], [0, 5, 3, 4], True),
    "mysterious": (72, 48, [0, 2, 3, 5, 7, 8, 11], [0, 1, 5, 4], False),
    "dramatic": (90, 45, [0, 2, 3, 5, 7, 8, 10], [0, 5, 2, 6], True),
    "sad": (66, 52, [0, 2, 3, 5, 7, 8, 10], [0, 3, 5, 4], False),
    "warm": (92, 53, [0, 2, 4, 5, 7, 9, 11], [0, 4, 5, 3], True),
    "romantic": (76, 55, [0, 2, 4, 5, 7, 9, 11], [0, 5, 3, 4], False),
    "playful": (112, 57, [0, 2, 4, 5, 7, 9, 11], [0, 3, 4, 0], True),
}


def midi_hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def write_wav(path: Path, x: np.ndarray, sr: int = SR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    x = np.clip(x, -1, 1)
    stereo = x.ndim == 2
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2 if stereo else 1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


def _env(n: int, a: float, r: float) -> np.ndarray:
    e = np.ones(n, dtype=np.float32)
    na, nr = int(a * SR), int(r * SR)
    if na:
        e[:na] = np.linspace(0, 1, na)
    if nr:
        e[-nr:] *= np.linspace(1, 0, nr)
    return e


class ProceduralMusic:
    id = "procedural_local"

    def compose(self, *, mood: str, genre: str, duration: float, seed: int, out: Path) -> CallInfo:
        bpm, root, scale, prog, perc = MOODS.get(mood, MOODS["tense"])
        rng = np.random.default_rng(seed)
        n = int(duration * SR)
        mix = np.zeros((n, 2), dtype=np.float32)
        bar = 4 * 60 / bpm
        t_bar = np.arange(int(bar * SR)) / SR
        for b in range(int(duration / bar) + 1):
            deg = prog[b % len(prog)]
            chord = [root + scale[(deg + k) % 7] + 12 * ((deg + k) // 7) for k in (0, 2, 4)]
            pad = sum(np.sin(2 * np.pi * midi_hz(m + 12) * t_bar + rng.uniform(0, 6)) *
                      (1 + 0.003 * np.sin(2 * np.pi * 5 * t_bar)) for m in chord) / 3
            pad *= _env(len(t_bar), 0.4, 0.5) * 0.22
            bass = np.sin(2 * np.pi * midi_hz(chord[0] - 12) * t_bar)
            pulse = (np.sin(2 * np.pi * (bpm / 60) * t_bar) > 0.2).astype(np.float32)
            bass *= pulse * 0.16
            seg = pad + bass
            if perc:
                beat = int(60 / bpm * SR)
                for k in range(4):
                    s = k * beat
                    ln = min(int(0.12 * SR), len(seg) - s)
                    if ln > 0:
                        seg[s:s + ln] += rng.normal(0, 1, ln) * np.exp(-np.linspace(0, 9, ln)) * (0.10 if k % 2 else 0.05)
            s0 = int(b * bar * SR)
            if s0 >= n:
                break
            ln = min(len(seg), n - s0)
            mix[s0:s0 + ln, 0] += seg[:ln] * 0.9
            mix[s0:s0 + ln, 1] += seg[:ln] * 1.0
        mix *= _env(n, 1.0, 2.0)[:, None]
        write_wav(out, mix)
        return CallInfo(provider=self.id, model="procedural-score", model_version="1", units={"seconds": duration},
                        seed=seed)

    def ambience(self, *, kind: str, duration: float, seed: int, out: Path) -> None:
        rng = np.random.default_rng(seed)
        n = int(duration * SR)
        white = rng.normal(0, 1, n).astype(np.float32)
        if kind == "rain":
            x = np.convolve(white, np.ones(3) / 3, mode="same") * 0.08
        elif kind in ("city", "night"):
            x = np.cumsum(white)
            x = (x - np.convolve(x, np.ones(800) / 800, mode="same")) * 0.004
        elif kind == "nature":
            x = np.convolve(white, np.ones(40) / 40, mode="same") * 0.05
        else:  # room / office / cafe: low room tone
            x = np.convolve(white, np.ones(200) / 200, mode="same") * 0.06
        write_wav(out, x * _env(n, 0.5, 0.5))
