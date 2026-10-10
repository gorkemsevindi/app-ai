"""Identity QC (Master Spec V6 §V6.3/§V6.5). Measured signals only — no metric proves identity.

- `face_similarity`: cosine of SFace (Apache-2.0) embeddings from YuNet (MIT) detections, only when the ONNX
  models are configured (MP_YUNET_ONNX / MP_SFACE_ONNX) and a face is found. Embeddings are transient (never
  stored or uploaded).
- `proxy_similarity`: colour-histogram + structure correlation (views) or multi-scale template matching
  (video frames vs the canonical portrait). Labelled `proxy`; the API never calls a proxy result verified.
- `dhash`: 64-bit perceptual hash of a master for lookalike screening across creators."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np


@lru_cache(maxsize=1)
def face_analyzer():
    y, s = os.environ.get("MP_YUNET_ONNX"), os.environ.get("MP_SFACE_ONNX")
    if not (y and s and Path(y).exists() and Path(s).exists()):
        return None
    from ..multiperson.components import OpenCVFaceAnalyzer

    return OpenCVFaceAnalyzer(y, s)


def _read(p: Path) -> np.ndarray:
    img = cv2.imread(str(p))
    if img is None:
        raise ValueError(f"unreadable image {p.name}")
    return img


def dhash(img: np.ndarray) -> str:
    g = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (9, 8), interpolation=cv2.INTER_AREA)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def _hist_corr(a: np.ndarray, b: np.ndarray) -> float:
    ha = cv2.calcHist([cv2.cvtColor(a, cv2.COLOR_BGR2HSV)], [0, 1], None, [30, 32], [0, 180, 0, 256])
    hb = cv2.calcHist([cv2.cvtColor(b, cv2.COLOR_BGR2HSV)], [0, 1], None, [30, 32], [0, 180, 0, 256])
    cv2.normalize(ha, ha)
    cv2.normalize(hb, hb)
    return float(max(0.0, cv2.compareHist(ha, hb, cv2.HISTCMP_CORREL)))


def _ncc(a: np.ndarray, b: np.ndarray, size: int = 64) -> float:
    ga = cv2.resize(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), (size, size)).astype(np.float32)
    gb = cv2.resize(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), (size, size)).astype(np.float32)
    ga, gb = ga - ga.mean(), gb - gb.mean()
    d = float(np.linalg.norm(ga) * np.linalg.norm(gb))
    return float((ga * gb).sum() / d) if d else 0.0


def compare_view(view: Path, master: Path, face_meaningful: bool) -> dict:
    a, m = _read(view), _read(master)
    out: dict = {"hist": round(_hist_corr(a, m), 4)}
    if face_meaningful:
        out["structure"] = round((_ncc(a, m) + 1) / 2, 4)
        # heuristic weights: structure (grayscale, lighting-robust) dominates; colour shifts under lighting changes
        out["proxy_similarity"] = round(0.3 * out["hist"] + 0.7 * out["structure"], 4)
        fa = face_analyzer()
        if fa is not None:
            ea, em = fa.embed_image(a), fa.embed_image(m)
            out["face_detected"] = ea is not None
            if ea is not None and em is not None:
                out["face_similarity"] = round(float(np.dot(ea, em)), 4)
    else:  # side/rear/body views: face recognition is not meaningful -> appearance proxy only
        out["proxy_similarity"] = out["hist"]
        out["face_meaningful"] = False
    return out


def _frames(video: Path, n: int = 8) -> list[np.ndarray]:
    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    out = []
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int((i + 0.5) * total / n))
        ok, fr = cap.read()
        if ok:
            out.append(fr)
    cap.release()
    return out


def shot_identity(video: Path, refs: dict[str, list[Path]]) -> dict:
    """Per character key: how well sampled frames match the canonical reference (first ref image)."""
    frames = _frames(video)
    fa = face_analyzer()
    out: dict = {}
    for key, paths in refs.items():
        if not paths or not frames:
            out[key] = {"method": "none", "score": None, "frames": len(frames)}
            continue
        ref = _read(paths[0])
        if fa is not None:
            er = fa.embed_image(ref)
            sims = []
            for fr in frames:
                e = fa.embed_image(fr)
                if e is not None and er is not None:
                    sims.append(float(np.dot(e, er)))
            if sims:
                out[key] = {"method": "face_embedding", "score": round(float(np.median(sims)), 4),
                            "frames": len(frames), "detected_ratio": round(len(sims) / len(frames), 3),
                            "min": round(min(sims), 4), "max": round(max(sims), 4)}
                continue
        g_ref = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)
        scores = []
        for fr in frames:
            g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
            best = -1.0
            for frac in (0.15, 0.25, 0.35, 0.5):
                tw = int(g.shape[1] * frac)
                if tw < 16 or tw >= min(g.shape):
                    continue
                t = cv2.resize(g_ref, (tw, tw))
                best = max(best, float(cv2.minMaxLoc(cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED))[1]))
            scores.append(max(0.0, best))
        out[key] = {"method": "proxy", "score": round(float(np.median(scores)), 4), "frames": len(frames),
                    "min": round(min(scores), 4), "max": round(max(scores), 4)}
    return out
