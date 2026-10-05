import json
import threading
from pathlib import Path

import cv2
import numpy as np
import pytest

from worker.multiperson.components import ColorBlobDetector, HistFaceAnalyzer, NoSafetyClassifier, TintReplacer
from worker.multiperson.compositing import box_mask, composite_frame, effective_masks
from worker.multiperson.pipeline import analyze, qa_gate, replace
from worker.multiperson.tracking import Detection, OnlineTracker, stitch, summarize

W, H, FPS, N = 360, 640, 16, 80
RED, BLUE, GREEN = (40, 40, 220), (220, 60, 40), (40, 200, 40)


def _person(frame, x, y, color, w=60, h=150):
    cv2.rectangle(frame, (int(x), int(y)), (int(x + w), int(y + h)), color, -1)
    cv2.rectangle(frame, (int(x + 15), int(y + 10)), (int(x + w - 15), int(y + 35)), (230, 230, 230), -1)  # "face"


def synth_video(path: Path) -> Path:
    """Red walks left->right passing IN FRONT of blue (occlusion); green exits at f30 and
    re-enters at f50 on the other side (re-entry)."""
    wr = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    for f in range(N):
        frame = np.full((H, W, 3), 70, np.uint8)
        _person(frame, 150, 220, BLUE)                       # blue: static, further back (bottom 370)
        if f < 30:
            _person(frame, 20, 60, GREEN, h=120)             # green: top-left, back
        elif f >= 50:
            _person(frame, 260, 60, GREEN, h=120)
        _person(frame, 10 + f * 3.5, 260, RED, h=170)        # red: in front (bottom 430)
        wr.write(frame)
    wr.release()
    return path


# ---------------------------------------------------------------- tracking unit tests
def test_tracker_keeps_id_through_short_gap_and_fast_motion():
    tr = OnlineTracker(max_misses=10)
    for f in range(20):
        if 8 <= f < 12:  # missed detections (motion blur)
            tr.update(f, [])
            continue
        tr.update(f, [Detection(f, (10 + f * 12, 50, 70 + f * 12, 200))])
    ts = tr.tracklets()
    assert len(ts) == 1 and len(ts[0].dets) == 16


def test_stitch_merges_reentry_but_never_coexisting_tracks():
    a, b = np.array([1.0, 0, 0]), np.array([0, 1.0, 0])
    tr = OnlineTracker(max_misses=2)
    for f in range(10):
        tr.update(f, [Detection(f, (0, 0, 50, 100), face_embedding=a),
                      Detection(f, (200, 0, 250, 100), face_embedding=b)])
    for f in range(10, 15):
        tr.update(f, [Detection(f, (200, 0, 250, 100), face_embedding=b)])
    for f in range(15, 25):  # person A re-enters far away
        tr.update(f, [Detection(f, (400, 0, 450, 100), face_embedding=a),
                      Detection(f, (200, 0, 250, 100), face_embedding=b)])
    groups = stitch(tr.tracklets())
    assert len(groups) == 2
    persons = summarize(groups, n_frames=25)
    a_person = next(p for p in persons if p["id_merges"] == 1)
    assert "reentry" in a_person["flags"] and len(a_person["segments"]) == 2


def test_effective_masks_respect_depth_order():
    boxes = {1: (50, 50, 150, 300), 2: (100, 80, 200, 350)}  # 2 is lower => in front
    masks = {k: box_mask((400, 300), b, ellipse=False) for k, b in boxes.items()}
    eff = effective_masks(masks, boxes)
    overlap = (masks[1] > 0) & (masks[2] > 0)
    assert overlap.any() and not (eff[1][overlap] > 0).any() and (eff[2][overlap] > 0).all()


def test_composite_leaves_outside_untouched():
    rng = np.random.default_rng(0)
    orig = rng.integers(0, 255, (200, 120, 3), dtype=np.uint8)
    boxes = {1: (20, 20, 80, 150)}
    masks = {1: box_mask((200, 120), boxes[1])}
    rep = np.full_like(orig, 255)
    out = composite_frame(orig, {1: rep}, masks, boxes, color_match=False)
    outside = masks[1] == 0
    assert np.array_equal(out[outside], orig[outside])
    assert out[masks[1] > 0].mean() > orig[masks[1] > 0].mean()


# ---------------------------------------------------------------- pipeline integration
@pytest.fixture
def workdir(tmp_path):
    return tmp_path


def test_analysis_finds_three_people_with_stable_ids(workdir):
    video = synth_video(workdir / "src.mp4")
    res = analyze(video, ColorBlobDetector(), HistFaceAnalyzer(), NoSafetyClassifier(), workdir,
                  lambda *_: None, threading.Event(), max_seconds=15, min_face_px=10)
    persons = res["persons"]
    assert len(persons) == 3, [(p["first_frame"], p["last_frame"], p["flags"]) for p in persons]
    green = next(p for p in persons if len(p["segments"]) == 2)
    assert green["first_frame"] == 0 and green["last_frame"] == N - 1 and "reentry" in green["flags"]
    assert res["duration_ms"] == int(N / FPS * 1000)
    tracks = json.loads(Path(res["_tracks_path"]).read_text())
    assert set(tracks["persons"]) == {"1", "2", "3"}
    for t in res["_thumbnails"].values():
        assert Path(t).exists()


def test_replace_two_people_occlusion_safe(workdir):
    video = synth_video(workdir / "src.mp4")
    res = analyze(video, ColorBlobDetector(), HistFaceAnalyzer(), NoSafetyClassifier(), workdir,
                  lambda *_: None, threading.Event(), max_seconds=15, min_face_px=10)
    tracks = json.loads(Path(res["_tracks_path"]).read_text())
    by_first_x = {}
    for tid, boxes in tracks["persons"].items():
        b0 = boxes[min(boxes, key=int)]
        by_first_x[tid] = b0
    blue = next(t for t, b in by_first_x.items() if 140 <= b[0] <= 160)
    red = next(t for t, b in by_first_x.items() if b[0] < 20 and b[1] > 200)
    ref = workdir / "ref.jpg"
    cv2.imwrite(str(ref), np.full((100, 100, 3), 128, np.uint8))
    assigns = [{"track_id": 1, "worker_track_id": int(blue)}, {"track_id": 2, "worker_track_id": int(red)}]
    out, qa = replace(video, tracks, assigns, {"1": [ref], "2": [ref]}, TintReplacer(), workdir,
                      lambda *_: None, threading.Event(), max_seconds=15, model_res=(240, 416))
    assert qa["persons_replaced"] == 2
    assert qa["outside_mask_mae"] < 1.5, qa
    assert qa_gate(qa) == []
    cap_s, cap_o = cv2.VideoCapture(str(video)), cv2.VideoCapture(str(out))
    for _ in range(40):  # frame 40: red overlaps blue
        _, fs = cap_s.read()
        _, fo = cap_o.read()
    # untouched person (green, off-screen at f40) region and background are unchanged
    assert np.abs(fs[0:50].astype(int) - fo[0:50].astype(int)).mean() < 3
    # replaced region changed (hue shift)
    assert np.abs(fs[300:400, 160:200].astype(int) - fo[300:400, 160:200].astype(int)).mean() > 20
