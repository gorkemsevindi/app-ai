"""espeak_local: offline formant TTS (eSpeak NG, GPL-3.0 binary invoked as a separate process).

Robotic quality — a development/preview voice, not a product voice. It is still a *real* pipeline stage:
it produces audio, word timings (energy-segmented forced alignment) and phoneme-derived visemes that
drive lip-sync and karaoke captions exactly like a premium TTS + aligner would.
"""

import re
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

from .base import CallInfo, ProviderUnavailable, SpeechClip, VisemeKey, WordTiming

# emotion -> (rate multiplier, pitch delta, amplitude, pitch range [used by SSML-capable premium TTS only])
PROSODY = {
    "neutral": (1.0, 0, 110, 50), "happy": (1.06, 8, 120, 70), "sad": (0.86, -10, 90, 25),
    "angry": (1.12, 4, 150, 80), "fear": (1.10, 12, 105, 75), "surprise": (1.02, 15, 125, 90),
    "tender": (0.92, -3, 95, 40),
}
LANG_VOICE = {"tr": "tr", "en": "en-us"}

_VOWEL = {"a": "AA", "A": "AA", "@": "AA", "V": "AA", "&": "AA", "3": "EE", "e": "EE", "E": "EE",
          "i": "EE", "I": "EE", "o": "OO", "O": "OO", "0": "OO", "u": "UU", "U": "UU", "y": "UU", "Y": "UU",
          "W": "UU"}
_CONS = {"m": "MBP", "b": "MBP", "p": "MBP", "f": "FV", "v": "FV", "l": "L", "w": "UU"}


def phonemes_to_visemes(ph: str) -> list[str]:
    ph = re.sub(r"[',:_%=!|]", "", ph)
    out = []
    for ch in ph:
        if ch in _VOWEL:
            out.append(_VOWEL[ch])
        elif ch in _CONS:
            out.append(_CONS[ch])
        elif ch.isalpha():
            out.append("S")
    return out or ["S"]


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr, n = w.getframerate(), w.getnframes()
        data = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
        if w.getnchannels() == 2:
            data = data.reshape(-1, 2).mean(axis=1)
    return data, sr


def rms_envelope(x: np.ndarray, sr: int, hop_s: float = 0.01) -> np.ndarray:
    hop = max(1, int(sr * hop_s))
    n = len(x) // hop
    if n == 0:
        return np.zeros(1, dtype=np.float32)
    return np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1))


def voiced_segments(env: np.ndarray, hop_s: float = 0.01, min_gap_s: float = 0.12) -> list[tuple[float, float]]:
    thr = max(1e-4, float(env.max()) * 0.06)
    voiced = env > thr
    segs, start, gap = [], None, 0
    min_gap = int(min_gap_s / hop_s)
    for i, v in enumerate(voiced):
        if v:
            if start is None:
                start = i
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= min_gap:
                segs.append((start * hop_s, (i - gap + 1) * hop_s))
                start, gap = None, 0
    if start is not None:
        segs.append((start * hop_s, (len(voiced) - gap) * hop_s))
    return segs


def align_words(words: list[str], weights: list[int], segs: list[tuple[float, float]], duration: float) -> list[WordTiming]:
    """Map words onto voiced segments. Phrase groups (split on punctuation) are matched to segments when
    counts agree; otherwise words are spread proportionally over the whole voiced span."""
    if not words:
        return []
    if not segs:
        segs = [(0.0, duration)]
    groups: list[list[int]] = [[]]
    for i, w in enumerate(words):
        groups[-1].append(i)
        if re.search(r"[,.;:!?…]$", w) and i < len(words) - 1:
            groups.append([])
    if len(groups) != len(segs):
        groups, segs = [list(range(len(words)))], [(segs[0][0], segs[-1][1])]
    out: list[WordTiming] = [None] * len(words)  # type: ignore[list-item]
    for g, (s0, s1) in zip(groups, segs, strict=True):
        total = sum(weights[i] for i in g) or 1
        t = s0
        for i in g:
            d = (s1 - s0) * weights[i] / total
            out[i] = WordTiming(word=words[i], start=round(t, 3), end=round(t + d, 3))
            t += d
    return out


class EspeakTTS:
    id = "espeak_local"

    def __init__(self) -> None:
        self.bin = shutil.which("espeak-ng") or shutil.which("espeak")
        if not self.bin:
            raise ProviderUnavailable("espeak_local", "espeak-ng binary (apt install espeak-ng)")

    def _variant(self, voice: dict) -> str:
        g = voice.get("gender", "neutral")
        p = int(voice.get("pitch", 50))
        if g == "female":
            return f"f{1 + p % 4}"
        if g == "male":
            return f"m{1 + p % 6}"
        return "m3"

    def phonemes(self, word: str, lang: str) -> str:
        r = subprocess.run([self.bin, "-q", "-x", "-v", LANG_VOICE.get(lang, lang), "--", word],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip()

    def synthesize(self, text: str, *, lang: str, voice: dict, emotion: str, intensity: float,
                   out: Path) -> SpeechClip:
        rate_m, dp, amp, _range = PROSODY.get(emotion, PROSODY["neutral"])
        k = 0.4 + 0.6 * intensity
        rate = int(voice.get("rate", 165) * (1 + (rate_m - 1) * k))
        pitch = max(0, min(99, int(voice.get("pitch", 50) + dp * k)))
        v = f"{LANG_VOICE.get(lang, lang)}+{self._variant(voice)}"
        out.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([self.bin, "-v", v, "-s", str(rate), "-p", str(pitch), "-a", str(amp), "-w", str(out), "--",
                            text], capture_output=True, text=True, timeout=60)
        if r.returncode != 0 or not out.exists():  # espeak-ng exits 0 on some errors; check the file too
            raise RuntimeError(f"espeak-ng failed: {r.stderr.strip()[:300]}")
        x, sr = read_wav(out)
        duration = len(x) / sr
        env = rms_envelope(x, sr)
        words = text.split()
        phs = [self.phonemes(re.sub(r"[^\w'-]", "", w) or w, lang) for w in words]
        vis_per_word = [phonemes_to_visemes(p) for p in phs]
        timings = align_words(words, [len(v_) for v_ in vis_per_word], voiced_segments(env), duration)
        visemes: list[VisemeKey] = []
        for wt, vs in zip(timings, vis_per_word, strict=True):
            step = (wt.end - wt.start) / len(vs)
            visemes += [VisemeKey(t=round(wt.start + i * step, 3), viseme=v_) for i, v_ in enumerate(vs)]
            visemes.append(VisemeKey(t=wt.end, viseme="rest"))
        norm = float(env.max()) or 1.0
        return SpeechClip(wav=out, duration=duration, words=timings, visemes=visemes,
                          envelope=[round(float(e) / norm, 3) for e in env],
                          info=CallInfo(provider=self.id, model="espeak-ng", model_version="1.51", is_mock=False,
                                        units={"chars": len(text)}))
