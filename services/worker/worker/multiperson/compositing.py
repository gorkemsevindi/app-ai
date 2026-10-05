"""Occlusion-aware compositing of per-person replacement passes back into the original frames.

- Depth order per frame: a person whose box bottom edge is lower in the frame is assumed closer to
  the camera (ground-plane heuristic); production refines with SAM mask overlap ordering.
- Effective mask of person k = mask_k minus the masks of everyone in front of k, so a replaced
  person never paints over someone standing in front of them.
- Feathered alpha (distance transform) hides seams; colour is matched to the original inside a
  ring around the mask so lighting stays consistent with the scene.
- Pixels outside the union of effective masks are copied from the source untouched (QA verifies)."""

from __future__ import annotations

import cv2
import numpy as np


def box_mask(shape: tuple[int, int], box, ellipse: bool = True) -> np.ndarray:
    h, w = shape
    m = np.zeros((h, w), np.uint8)
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return m
    if ellipse:
        cv2.ellipse(m, ((x1 + x2) // 2, (y1 + y2) // 2), ((x2 - x1) // 2, (y2 - y1) // 2), 0, 0, 360, 255, -1)
    else:
        m[y1:y2, x1:x2] = 255
    return m


def depth_order(boxes: dict[int, tuple]) -> list[int]:
    """Back-to-front track ids for one frame."""
    return sorted(boxes, key=lambda k: boxes[k][3])


def effective_masks(masks: dict[int, np.ndarray], boxes: dict[int, tuple]) -> dict[int, np.ndarray]:
    order = depth_order(boxes)
    out = {}
    for i, k in enumerate(order):
        m = masks[k].copy()
        for front in order[i + 1:]:
            m[masks[front] > 0] = 0
        out[k] = m
    return out


def feather(mask: np.ndarray, radius: int = 6) -> np.ndarray:
    if radius <= 0:
        return (mask > 0).astype(np.float32)
    binary = (mask > 0).astype(np.uint8)
    dist = cv2.distanceTransform(binary, cv2.DIST_L2, 5)
    return np.clip(dist / float(radius), 0.0, 1.0).astype(np.float32)


def lab_stats(img: np.ndarray, mask: np.ndarray) -> np.ndarray | None:
    sel = mask > 0
    if sel.sum() < 50:
        return None
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)[sel]
    return np.stack([lab.mean(0), lab.std(0) + 1e-6])  # (2, 3)


def apply_color_transform(img: np.ndarray, src_stats: np.ndarray, ref_stats: np.ndarray) -> np.ndarray:
    """Constant (clip-level) Reinhard transfer. Using one transform for the whole clip instead of
    per-frame statistics is what keeps the replaced person's colour from pumping when someone
    walks past (per-frame stats change with the surroundings -> visible flicker)."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab = (lab - src_stats[0]) * (ref_stats[1] / src_stats[1]) + ref_stats[0]
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def match_color(src_patch: np.ndarray, ref_patch: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Reinhard colour transfer in LAB, statistics computed only inside `mask`."""
    sel = mask > 0
    if sel.sum() < 50:
        return src_patch
    s = cv2.cvtColor(src_patch, cv2.COLOR_BGR2LAB).astype(np.float32)
    r = cv2.cvtColor(ref_patch, cv2.COLOR_BGR2LAB).astype(np.float32)
    for c in range(3):
        sm, ss = s[..., c][sel].mean(), s[..., c][sel].std() + 1e-6
        rm, rs = r[..., c][sel].mean(), r[..., c][sel].std() + 1e-6
        s[..., c] = (s[..., c] - sm) * (rs / ss) + rm
    return cv2.cvtColor(np.clip(s, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def composite_frame(original: np.ndarray, replaced: dict[int, np.ndarray], masks: dict[int, np.ndarray],
                    boxes: dict[int, tuple], feather_px: int = 6, color_match: bool = False,
                    color_transforms: dict[int, tuple[np.ndarray, np.ndarray]] | None = None) -> np.ndarray:
    """`replaced[k]` is a full-frame image whose pixels inside person k's region are the new person."""
    eff = effective_masks(masks, boxes)
    out = original.astype(np.float32)
    for k in depth_order(boxes):
        if k not in replaced:
            continue
        rep = replaced[k]
        if color_transforms and k in color_transforms:
            rep = apply_color_transform(rep, *color_transforms[k])
        elif color_match:
            ring = cv2.dilate(eff[k], np.ones((15, 15), np.uint8)) - eff[k]
            ref_mask = ring if ring.sum() > 0 else eff[k]
            rep = match_color(rep, original, ref_mask) if ref_mask.sum() else rep
        a = feather(eff[k], feather_px)[..., None]
        out = out * (1 - a) + rep.astype(np.float32) * a
    return np.clip(out, 0, 255).astype(np.uint8)


def outside_mask_change(original: np.ndarray, result: np.ndarray, union_mask: np.ndarray, dilate: int = 4) -> float:
    """Mean absolute change outside the (slightly dilated) union of replaced masks. Should be ~0."""
    m = cv2.dilate((union_mask > 0).astype(np.uint8), np.ones((2 * dilate + 1, 2 * dilate + 1), np.uint8))
    outside = m == 0
    if outside.sum() == 0:
        return 0.0
    return float(np.abs(original.astype(np.int16) - result.astype(np.int16))[outside].mean())
