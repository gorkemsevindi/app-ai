"""Multi-person tracking with persistent IDs.

Two layers:
1. Online association (frame to frame): IoU + appearance cost with a constant-velocity prediction
   (OC-SORT-style "observation-centric" recovery), so short occlusions and fast motion keep the ID.
   In production the per-frame boxes/masks come from SAM 2.1 propagation seeded by the detector;
   this module is model-agnostic and only consumes detections.
2. Offline stitching: tracklets that end and later restart (exit/re-entry, long occlusion, profile
   turn where the face embedder lost the face) are merged when appearance agrees and they never
   co-exist in time. The result is one stable `track_id` per physical person for the whole clip.

Pure numpy so it is unit-testable without a GPU."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

Box = tuple[float, float, float, float]  # x1, y1, x2, y2 (pixels)


@dataclass
class Detection:
    frame: int
    box: Box
    score: float = 1.0
    face_box: Box | None = None
    face_embedding: np.ndarray | None = None   # L2-normalized (e.g. SFace 128-d)
    body_embedding: np.ndarray | None = None   # L2-normalized appearance (colour hist / re-ID net)
    flags: tuple[str, ...] = ()                # e.g. ("minor_suspected",) from an attribute classifier


@dataclass
class Tracklet:
    id: int
    dets: list[Detection] = field(default_factory=list)
    misses: int = 0
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(4))

    @property
    def first(self) -> int:
        return self.dets[0].frame

    @property
    def last(self) -> int:
        return self.dets[-1].frame

    def predict(self, frame: int) -> np.ndarray:
        last = np.array(self.dets[-1].box, dtype=float)
        return last + self.velocity * (frame - self.last)

    def add(self, d: Detection) -> None:
        if self.dets:
            dt = max(1, d.frame - self.last)
            new_v = (np.array(d.box) - np.array(self.dets[-1].box)) / dt
            self.velocity = 0.6 * self.velocity + 0.4 * new_v
        self.dets.append(d)
        self.misses = 0

    def appearance(self, kind: str = "face") -> np.ndarray | None:
        embs = [getattr(d, f"{kind}_embedding") for d in self.dets if getattr(d, f"{kind}_embedding") is not None]
        if not embs:
            return None
        m = np.mean(np.stack(embs), axis=0)
        n = np.linalg.norm(m)
        return m / n if n > 0 else None


def iou(a: np.ndarray | Box, b: np.ndarray | Box) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


def _cos(a: np.ndarray | None, b: np.ndarray | None) -> float | None:
    if a is None or b is None:
        return None
    return float(np.dot(a, b))


def _hungarian_greedy(cost: np.ndarray, max_cost: float) -> list[tuple[int, int]]:
    """Greedy min-cost matching (adequate for <= ~20 people; swap for scipy's LSA if needed)."""
    pairs = []
    if cost.size == 0:
        return pairs
    used_r, used_c = set(), set()
    for idx in np.argsort(cost, axis=None):
        r, c = np.unravel_index(idx, cost.shape)
        if cost[r, c] > max_cost:
            break
        if r in used_r or c in used_c:
            continue
        used_r.add(r)
        used_c.add(c)
        pairs.append((int(r), int(c)))
    return pairs


class OnlineTracker:
    def __init__(self, max_misses: int = 30, iou_weight: float = 0.6, max_cost: float = 0.8):
        self.max_misses = max_misses
        self.iou_weight = iou_weight
        self.max_cost = max_cost
        self.active: list[Tracklet] = []
        self.finished: list[Tracklet] = []
        self._next = 1

    def update(self, frame: int, dets: list[Detection]) -> None:
        cost = np.ones((len(self.active), len(dets)))
        for i, t in enumerate(self.active):
            pred = t.predict(frame)
            t_face, t_body = t.appearance("face"), t.appearance("body")
            for j, d in enumerate(dets):
                ov = iou(pred, d.box)
                app = _cos(t_face, d.face_embedding)
                if app is None:
                    app = _cos(t_body, d.body_embedding)
                app_cost = 0.5 if app is None else (1 - app)
                c = self.iou_weight * (1 - ov) + (1 - self.iou_weight) * app_cost
                if ov == 0 and (app is None or app < 0.5):
                    c = 1.0  # no spatial or appearance evidence
                cost[i, j] = c
        matched_t, matched_d = set(), set()
        for i, j in _hungarian_greedy(cost, self.max_cost):
            self.active[i].add(dets[j])
            matched_t.add(i)
            matched_d.add(j)
        for i, t in enumerate(self.active):
            if i not in matched_t:
                t.misses += 1
        for j, d in enumerate(dets):
            if j not in matched_d:
                t = Tracklet(id=self._next)
                self._next += 1
                t.add(d)
                self.active.append(t)
        still = []
        for t in self.active:
            (self.finished if t.misses > self.max_misses else still).append(t)
        self.active = still

    def tracklets(self) -> list[Tracklet]:
        return sorted(self.finished + self.active, key=lambda t: t.id)


def _overlaps(a: Tracklet, b: Tracklet) -> bool:
    fa = {d.frame for d in a.dets}
    return any(d.frame in fa for d in b.dets)


def stitch(tracklets: list[Tracklet], min_sim: float = 0.55, max_gap: int | None = None) -> list[list[Tracklet]]:
    """Agglomerative merge of tracklets into identities. Two groups merge only if no member pair
    co-exists in any frame (a person can't be in two places) and their appearance similarity is
    high enough. Returns groups ordered by first appearance."""
    groups: list[list[Tracklet]] = [[t] for t in tracklets if t.dets]

    def group_emb(g: list[Tracklet], kind: str) -> np.ndarray | None:
        embs = [e for t in g if (e := t.appearance(kind)) is not None]
        if not embs:
            return None
        m = np.mean(np.stack(embs), axis=0)
        return m / (np.linalg.norm(m) or 1)

    while True:
        best, best_pair = min_sim, None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                gi, gj = groups[i], groups[j]
                if any(_overlaps(a, b) for a in gi for b in gj):
                    continue
                if max_gap is not None:
                    gap = min(abs(b.first - a.last) for a in gi for b in gj)
                    if gap > max_gap:
                        continue
                s = _cos(group_emb(gi, "face"), group_emb(gj, "face"))
                if s is None:
                    s = _cos(group_emb(gi, "body"), group_emb(gj, "body"))
                    s = None if s is None else s - 0.1  # body appearance is weaker evidence
                if s is not None and s > best:
                    best, best_pair = s, (i, j)
        if best_pair is None:
            break
        i, j = best_pair
        groups[i] = sorted(groups[i] + groups[j], key=lambda t: t.first)
        del groups[j]
    return sorted(groups, key=lambda g: min(t.first for t in g))


def summarize(groups: list[list[Tracklet]], n_frames: int, min_face_px: int = 64) -> list[dict]:
    """Per-person summary sent to the API (and later used to drive replacement)."""
    out = []
    for idx, g in enumerate(groups, start=1):
        dets = sorted((d for t in g for d in t.dets), key=lambda d: d.frame)
        frames = [d.frame for d in dets]
        faces = [d for d in dets if d.face_box is not None]
        face_px = [min(f.face_box[2] - f.face_box[0], f.face_box[3] - f.face_box[1]) for f in faces]
        segments, start, prev = [], frames[0], frames[0]
        for f in frames[1:]:
            if f - prev > 1:
                segments.append([start, prev])
                start = f
            prev = f
        segments.append([start, prev])
        flags = set()
        span = frames[-1] - frames[0] + 1
        if len(segments) > 1:
            flags.add("reentry")  # left visibility (exit or full occlusion) and came back
        if len(set(frames)) / span < 0.8:
            flags.add("heavy_occlusion")
        if any("minor_suspected" in d.flags for d in dets):
            flags.add("minor_suspected")
        if faces and len(faces) / len(dets) < 0.3:
            flags.add("face_not_visible")
        out.append({
            "worker_track_id": idx,
            "first_frame": frames[0], "last_frame": frames[-1],
            "coverage": round(len(set(frames)) / max(1, n_frames), 4),
            "face_visible_ratio": round(len(faces) / max(1, len(dets)), 4),
            "median_face_px": int(np.median(face_px)) if face_px else 0,
            "segments": segments, "id_merges": len(g) - 1, "flags": sorted(flags),
            "boxes": {str(d.frame): [round(v, 1) for v in d.box] for d in dets},
        })
    return out
