"""Multi-person analysis and replacement orchestration (docs/MULTI_PERSON.md §2)."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..adapters.base import AdapterError, Cancelled
from .components import FaceAnalyzer, PersonDetector, PersonReplacer, SafetyClassifier, body_embedding
from .compositing import box_mask, composite_frame, outside_mask_change
from .tracking import OnlineTracker, stitch, summarize

Progress = Callable[[float, str | None], None]


@dataclass
class VideoInfo:
    fps: float
    n_frames: int
    width: int
    height: int

    @property
    def duration_ms(self) -> int:
        return int(self.n_frames / (self.fps or 1) * 1000)


def probe(path: Path) -> VideoInfo:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise AdapterError("bad_video", "cannot decode video", retryable=False)
    info = VideoInfo(cap.get(cv2.CAP_PROP_FPS) or 24.0, int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
                     int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    cap.release()
    return info


def scene_cuts(path: Path, threshold: float = 0.45) -> list[int]:
    """Hard cuts break tracking assumptions; we report them (tracks are not stitched across a cut
    by motion, only by appearance)."""
    cap, prev, cuts, i = cv2.VideoCapture(str(path)), None, [], 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        h = cv2.calcHist([cv2.cvtColor(f, cv2.COLOR_BGR2HSV)], [0, 1], None, [16, 8], [0, 180, 0, 256])
        cv2.normalize(h, h)
        if prev is not None and cv2.compareHist(prev, h, cv2.HISTCMP_BHATTACHARYYA) > threshold:
            cuts.append(i)
        prev, i = h, i + 1
    cap.release()
    return cuts


# ---------------------------------------------------------------- analysis
def analyze(video: Path, detector: PersonDetector, faces: FaceAnalyzer, safety: SafetyClassifier,
            workdir: Path, progress: Progress, cancel: threading.Event, max_seconds: float,
            min_face_px: int = 64, stride: int = 1) -> dict:
    t0 = time.time()
    info = probe(video)
    if info.n_frames <= 0:
        raise AdapterError("bad_video", "empty video", retryable=False)
    max_frames = int(min(info.n_frames, (max_seconds + 0.5) * info.fps))
    tracker = OnlineTracker(max_misses=int(info.fps * 2))
    cap = cv2.VideoCapture(str(video))
    frames_for_thumbs: dict[int, np.ndarray] = {}
    video_flags: set[str] = set()
    i = 0
    while i < max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        if cancel.is_set():
            raise Cancelled()
        if i % stride == 0:
            dets = detector.detect(frame, i)
            for d in dets:
                fa = faces.analyze(frame, d.box)
                if fa is not None:
                    d.face_box, d.face_embedding = fa
                d.body_embedding = body_embedding(frame, d.box)
                d.flags = tuple(safety.flags(frame, d))
                video_flags.update(f for f in d.flags if f in {"nsfw_source", "graphic_violence"})
            tracker.update(i, dets)
            if i % max(1, int(info.fps / 2)) == 0:
                frames_for_thumbs[i] = frame
        i += 1
        if i % 10 == 0:
            progress(0.1 + 0.8 * i / max_frames, "generating")
    cap.release()
    groups = stitch(tracker.tracklets(), max_gap=None)
    persons = summarize(groups, n_frames=i, min_face_px=min_face_px)
    if any("minor_suspected" in p["flags"] for p in persons):
        video_flags.add("minor_suspected")

    # Thumbnail per person: the sampled frame where their box is largest.
    for p in persons:
        best, best_area = None, 0.0
        for f_idx, b in p["boxes"].items():
            fi = int(f_idx)
            if fi in frames_for_thumbs:
                area = (b[2] - b[0]) * (b[3] - b[1])
                if area > best_area:
                    best, best_area = (fi, b), area
        if best:
            fi, b = best
            x1, y1, x2, y2 = (max(0, int(v)) for v in b)
            thumb = frames_for_thumbs[fi][y1:y2, x1:x2]
            path = workdir / f"person_{p['worker_track_id']}.jpg"
            cv2.imwrite(str(path), thumb)
            p["thumbnail_path"] = str(path)

    tracks_path = workdir / "tracks.json"
    tracks_path.write_text(json.dumps({"fps": info.fps, "width": info.width, "height": info.height,
                                       "persons": {str(p["worker_track_id"]): p["boxes"] for p in persons}}))
    return {
        "duration_ms": int(i / info.fps * 1000), "width": info.width, "height": info.height, "fps": info.fps,
        "flags": sorted(video_flags), "scene_cuts": scene_cuts(video),
        "persons": [{k: v for k, v in p.items() if k not in ("boxes", "thumbnail_path")} for p in persons],
        "_thumbnails": {str(p["worker_track_id"]): p.get("thumbnail_path") for p in persons},
        "_tracks_path": str(tracks_path),
        "timing": {"analysis_s": round(time.time() - t0, 2)},
    }


# ---------------------------------------------------------------- replacement
def _interp_boxes(boxes: dict[str, list], n_frames: int, max_hold: int) -> dict[int, tuple]:
    """Fill short gaps (missed detections, motion blur) by linear interpolation; long gaps stay
    empty (person fully occluded or off-screen -> nothing is rendered for them)."""
    known = sorted((int(k), tuple(v)) for k, v in boxes.items())
    out: dict[int, tuple] = {}
    for (f0, b0), (f1, b1) in zip(known, known[1:], strict=False):
        out[f0] = b0
        if 1 < f1 - f0 <= max_hold:
            for f in range(f0 + 1, f1):
                t = (f - f0) / (f1 - f0)
                out[f] = tuple(a + (b - a) * t for a, b in zip(b0, b1, strict=True))
    if known:
        out[known[-1][0]] = known[-1][1]
    return {f: b for f, b in out.items() if f < n_frames}


def _crop_window(boxes: dict[int, tuple], w: int, h: int, aspect: float = 9 / 16, margin: float = 0.25):
    xs1, ys1, xs2, ys2 = zip(*boxes.values(), strict=True)
    x1, y1, x2, y2 = min(xs1), min(ys1), max(xs2), max(ys2)
    bw, bh = (x2 - x1) * (1 + margin), (y2 - y1) * (1 + margin)
    if bw / bh > aspect:
        bh = bw / aspect
    else:
        bw = bh * aspect
    bw, bh = min(bw, w), min(bh, h)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    left = int(np.clip(cx - bw / 2, 0, w - bw))
    top = int(np.clip(cy - bh / 2, 0, h - bh))
    return left, top, int(bw) // 2 * 2, int(bh) // 2 * 2


def replace(video: Path, tracks: dict, assignments: list[dict], identities: dict[str, list[Path]],
            replacer: PersonReplacer, workdir: Path, progress: Progress, cancel: threading.Event,
            max_seconds: float, model_res: tuple[int, int] = (480, 832), faces: FaceAnalyzer | None = None,
            seed: int = 1234) -> tuple[Path, dict]:
    info = probe(video)
    n = int(min(info.n_frames, (max_seconds + 0.5) * info.fps))
    W, H = info.width, info.height
    max_hold = int(info.fps * 0.75)
    per_track_boxes = {a["worker_track_id"]: _interp_boxes(tracks["persons"][str(a["worker_track_id"])], n, max_hold)
                       for a in assignments}
    windows, rep_paths = {}, {}
    # 1) one masked replacement pass per assigned person (cost ~ linear in persons)
    for idx, a in enumerate(assignments):
        if cancel.is_set():
            raise Cancelled()
        tid = a["worker_track_id"]
        boxes = per_track_boxes[tid]
        if not boxes:
            continue
        left, top, cw, ch = _crop_window(boxes, W, H)
        windows[tid] = (left, top, cw, ch)
        crop_p, mask_p = workdir / f"crop_{tid}.mp4", workdir / f"mask_{tid}.mp4"
        cap = cv2.VideoCapture(str(video))
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        cw_r, mw_r = (cv2.VideoWriter(str(p), fourcc, info.fps, model_res) for p in (crop_p, mask_p))
        for f in range(n):
            ok, frame = cap.read()
            if not ok:
                break
            crop = cv2.resize(frame[top:top + ch, left:left + cw], model_res)
            m = np.zeros((H, W), np.uint8)
            if f in boxes:
                m = box_mask((H, W), boxes[f])
            mcrop = cv2.resize(m[top:top + ch, left:left + cw], model_res, interpolation=cv2.INTER_NEAREST)
            cw_r.write(crop)
            mw_r.write(cv2.cvtColor(mcrop, cv2.COLOR_GRAY2BGR))
        cap.release()
        cw_r.release()
        mw_r.release()
        # Same seed + same identity for the whole clip (also across exits/re-entries) => stable face.
        rep_paths[tid] = replacer.replace(crop_p, mask_p, identities[str(a["track_id"])], seed + tid, workdir)
        progress(0.1 + 0.7 * (idx + 1) / len(assignments), "generating")

    all_tracks = {int(k): _interp_boxes(v, n, max_hold) for k, v in tracks["persons"].items()}
    color_transforms = _clip_color_transforms(video, rep_paths, windows, all_tracks, n, W, H)

    # 2) occlusion-aware composite, streaming frame by frame
    progress(0.85, "postprocessing")
    out_path = workdir / "composited.mp4"
    cap = cv2.VideoCapture(str(video))
    reps = {tid: cv2.VideoCapture(str(p)) for tid, p in rep_paths.items()}
    wr = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), info.fps, (W, H))
    outside, flicker_excess = [], []
    prev_out, prev_src, prev_core = None, None, None
    for f in range(n):
        ok, frame = cap.read()
        if not ok:
            break
        boxes = {tid: b[f] for tid, b in all_tracks.items() if f in b}
        masks = {tid: box_mask((H, W), b) for tid, b in boxes.items()}
        replaced = {}
        for tid, rc in reps.items():
            okr, rf = rc.read()
            if not okr or tid not in boxes:
                continue
            left, top, cw, ch = windows[tid]
            full = frame.copy()
            full[top:top + ch, left:left + cw] = cv2.resize(rf, (cw, ch))
            replaced[tid] = full
        result = composite_frame(frame, replaced, masks, boxes, color_transforms=color_transforms) \
            if replaced else frame
        union = np.zeros((H, W), np.uint8)
        for tid in replaced:
            union |= masks[tid]
        outside.append(outside_mask_change(frame, result, union))
        core = cv2.erode(union, np.ones((9, 9), np.uint8))  # ignore feathered/moving mask borders
        if prev_out is not None and core.any() and prev_core is not None:
            sel = (core > 0) & (prev_core > 0)
            if sel.any():
                flicker_excess.append(_warp_excess(prev_src, frame, prev_out, result, sel))
        prev_out, prev_src, prev_core = result, frame, core
        wr.write(result)
    cap.release()
    wr.release()
    for rc in reps.values():
        rc.release()

    qa = {
        "outside_mask_mae": round(float(np.mean(outside)) if outside else 0.0, 4),
        # Mean per-pixel temporal change inside replaced regions beyond the source's own change
        # (0-255 scale). p95 catches short flicker bursts that a mean hides.
        "flicker_excess": round(float(np.mean(flicker_excess)) if flicker_excess else 0.0, 4),
        "flicker_excess_p95": round(float(np.percentile(flicker_excess, 95)) if flicker_excess else 0.0, 4),
        "persons_replaced": len(rep_paths),
    }
    if faces is not None:
        qa["identity_similarity"] = _identity_similarity(out_path, per_track_boxes, identities, assignments, faces)
    return out_path, qa


def _clip_color_transforms(video, rep_paths, windows, all_tracks, n, W, H, step: int = 4) -> dict:
    """One colour transform per replaced person, from statistics pooled over the clip, mapping the
    replacer's output toward the original person's lighting."""
    from .compositing import box_mask

    acc: dict[int, dict[str, list]] = {t: {"src": [], "ref": []} for t in rep_paths}
    cap = cv2.VideoCapture(str(video))
    reps = {t: cv2.VideoCapture(str(p)) for t, p in rep_paths.items()}
    for f in range(n):
        ok, frame = cap.read()
        if not ok:
            break
        for t, rc in reps.items():
            okr, rf = rc.read()
            if not okr or f % step or f not in all_tracks.get(t, {}):
                continue
            left, top, cw, ch = windows[t]
            m = box_mask((H, W), all_tracks[t][f])[top:top + ch, left:left + cw] > 0
            if m.sum() < 50:
                continue
            acc[t]["src"].append(cv2.cvtColor(cv2.resize(rf, (cw, ch)), cv2.COLOR_BGR2LAB)[m].astype(np.float32))
            acc[t]["ref"].append(cv2.cvtColor(frame[top:top + ch, left:left + cw], cv2.COLOR_BGR2LAB)[m]
                                 .astype(np.float32))
    cap.release()
    for rc in reps.values():
        rc.release()
    out = {}
    for t, a in acc.items():
        if a["src"]:
            s, r = np.concatenate(a["src"]), np.concatenate(a["ref"])
            # Only correct global lighting (L channel), keep the new person's own colours (a/b).
            src_stats = np.stack([s.mean(0), s.std(0) + 1e-6])
            ref_stats = src_stats.copy()
            ref_stats[:, 0] = [r[:, 0].mean(), r[:, 0].std() + 1e-6]
            out[t] = (src_stats, ref_stats)
    return out


def _warp_excess(prev_src, src, prev_out, out, sel) -> float:
    """Motion-compensated flicker: warp previous frames along the *source* optical flow and compare
    the residual of the output with the residual of the source. Production uses RAFT (BSD-3);
    Farneback (OpenCV) here keeps the worker light."""
    g0, g1 = cv2.cvtColor(prev_src, cv2.COLOR_BGR2GRAY), cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)
    flow = cv2.calcOpticalFlowFarneback(g1, g0, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    h, w = g0.shape
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    mx, my = gx + flow[..., 0], gy + flow[..., 1]
    warp_src = cv2.remap(prev_src, mx, my, cv2.INTER_LINEAR)
    warp_out = cv2.remap(prev_out, mx, my, cv2.INTER_LINEAR)
    e_src = np.abs(src.astype(np.int16) - warp_src.astype(np.int16)).mean(-1)[sel]
    e_out = np.abs(out.astype(np.int16) - warp_out.astype(np.int16)).mean(-1)[sel]
    return float(np.maximum(0, e_out - e_src).mean())


def _identity_similarity(video: Path, per_track_boxes, identities, assignments, faces: FaceAnalyzer) -> dict:
    refs = {}
    for a in assignments:
        embs = [e for p in identities[str(a["track_id"])] if (img := cv2.imread(str(p))) is not None
                and (e := faces.embed_image(img)) is not None]
        if embs:
            m = np.mean(embs, axis=0)
            refs[a["worker_track_id"]] = m / (np.linalg.norm(m) or 1)
    sims: dict[int, list[float]] = {k: [] for k in refs}
    cap, f = cv2.VideoCapture(str(video)), 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if f % 4 == 0:
            for tid, ref in refs.items():
                b = per_track_boxes[tid].get(f)
                if b is not None and (r := faces.analyze(frame, b)) is not None:
                    sims[tid].append(float(np.dot(ref, r[1])))
        f += 1
    cap.release()
    return {str(k): round(float(np.median(v)), 4) if v else None for k, v in sims.items()}


def qa_gate(qa: dict, min_identity: float | None = None, max_outside: float = 1.5,
            max_flicker: float = 6.0) -> list[str]:
    problems = []
    if qa["outside_mask_mae"] > max_outside:
        problems.append("outside_mask_changed")
    if qa["flicker_excess_p95"] > max_flicker:
        problems.append("temporal_flicker")
    if min_identity is not None:
        for tid, s in (qa.get("identity_similarity") or {}).items():
            if s is None or s < min_identity:
                problems.append(f"identity_low:{tid}")
    return problems
