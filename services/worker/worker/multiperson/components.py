"""Pluggable per-stage components. Each has a production implementation with a commercially usable
licence (see docs/research-multiperson.md) and a deterministic dev/test implementation."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from ..adapters.base import AdapterError
from .tracking import Detection


# ---------------------------------------------------------------- interfaces
class PersonDetector(Protocol):
    def detect(self, frame: np.ndarray, frame_idx: int) -> list[Detection]: ...


class FaceAnalyzer(Protocol):
    def analyze(self, frame: np.ndarray, box) -> tuple[tuple, np.ndarray] | None: ...
    def embed_image(self, image: np.ndarray) -> np.ndarray | None: ...


class SafetyClassifier(Protocol):
    def flags(self, frame: np.ndarray, det: Detection) -> tuple[str, ...]: ...


class PersonReplacer(Protocol):
    name: str

    def replace(self, crop_video: Path, mask_video: Path, identity_images: list[Path], seed: int,
                workdir: Path) -> Path: ...


def body_embedding(frame: np.ndarray, box) -> np.ndarray | None:
    """Licence-free appearance cue: HSV colour histogram of the torso region."""
    x1, y1, x2, y2 = (int(v) for v in box)
    h = y2 - y1
    crop = frame[max(0, y1 + h // 5): max(0, y1 + 3 * h // 5), max(0, x1): max(0, x2)]
    if crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256]).flatten()
    n = np.linalg.norm(hist)
    return hist / n if n > 0 else None


# ---------------------------------------------------------------- production implementations
class OnnxPersonDetector:
    """RT-DETRv2 / D-FINE / YOLOX exported to ONNX (Apache-2.0), run with onnxruntime-gpu.
    Expected output: boxes [N,4] (xyxy, input scale), scores [N], labels [N] (COCO person = 0)."""

    def __init__(self, model_path: str | None = None, input_size: int = 640, conf: float = 0.45):
        import onnxruntime as ort  # noqa: PLC0415

        path = model_path or os.environ["MP_DETECTOR_ONNX"]
        self.sess = ort.InferenceSession(path, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
        self.size, self.conf = input_size, conf

    def detect(self, frame: np.ndarray, frame_idx: int) -> list[Detection]:
        h, w = frame.shape[:2]
        img = cv2.resize(frame, (self.size, self.size))[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255
        names = [i.name for i in self.sess.get_inputs()]
        feeds = {names[0]: img}
        if len(names) > 1:
            feeds[names[1]] = np.array([[self.size, self.size]], dtype=np.int64)
        boxes, scores, labels = self.sess.run(None, feeds)[:3]
        sx, sy = w / self.size, h / self.size
        out = []
        for b, s, lab in zip(np.squeeze(boxes, 0) if boxes.ndim == 3 else boxes,
                             np.ravel(scores), np.ravel(labels), strict=False):
            if int(lab) == 0 and float(s) >= self.conf:
                out.append(Detection(frame_idx, (b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy), float(s)))
        return out


class OpenCVFaceAnalyzer:
    """YuNet (MIT) face detection + SFace (Apache-2.0) 128-d embedding, both from OpenCV Zoo.
    NOT InsightFace/ArcFace (non-commercial weights)."""

    def __init__(self, yunet: str | None = None, sface: str | None = None):
        self.det = cv2.FaceDetectorYN.create(yunet or os.environ["MP_YUNET_ONNX"], "", (320, 320), 0.7)
        self.rec = cv2.FaceRecognizerSF.create(sface or os.environ["MP_SFACE_ONNX"], "")

    def _faces(self, img: np.ndarray):
        self.det.setInputSize((img.shape[1], img.shape[0]))
        _, faces = self.det.detect(img)
        return faces if faces is not None else []

    def analyze(self, frame, box):
        x1, y1, x2, y2 = (max(0, int(v)) for v in box)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        faces = self._faces(crop)
        if len(faces) == 0:
            return None
        f = max(faces, key=lambda r: r[2] * r[3])
        emb = self.rec.feature(self.rec.alignCrop(crop, f)).flatten()
        emb = emb / (np.linalg.norm(emb) or 1)
        fx, fy, fw, fh = f[:4]
        return (x1 + fx, y1 + fy, x1 + fx + fw, y1 + fy + fh), emb

    def embed_image(self, image):
        faces = self._faces(image)
        if len(faces) == 0:
            return None
        f = max(faces, key=lambda r: r[2] * r[3])
        emb = self.rec.feature(self.rec.alignCrop(image, f)).flatten()
        return emb / (np.linalg.norm(emb) or 1)


class CommandReplacer:
    """Runs a single-identity video replacer (DreamID-V / Wan-Animate) on one person's crop clip."""

    def __init__(self, adapter):
        self.adapter = adapter
        self.name = adapter.name

    def replace(self, crop_video, mask_video, identity_images, seed, workdir):
        import threading

        from ..adapters.base import GenerationRequest

        cap = cv2.VideoCapture(str(crop_video))
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        n, fps = cap.get(cv2.CAP_PROP_FRAME_COUNT), cap.get(cv2.CAP_PROP_FPS) or 16
        cap.release()
        req = GenerationRequest(job_id=workdir.name, task="face_swap_v2v", prompt="", negative_prompt="",
                                duration_s=n / fps, width=w, height=h, identity_images=identity_images,
                                workdir=workdir, source_video=crop_video, seed=seed,
                                params={"fps": int(fps), "mask_video": str(mask_video)})
        res = self.adapter.generate(req, lambda *_: None, threading.Event())
        return res.video_path


# ---------------------------------------------------------------- dev / test implementations
class ColorBlobDetector:
    """Test-only: synthetic videos draw each 'person' as a saturated rectangle of a distinct hue."""

    def detect(self, frame, frame_idx):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        sat = (hsv[..., 1] > 120) & (hsv[..., 2] > 60)
        dets = []
        for lo in range(0, 180, 30):  # separate hue bins so touching people stay separate blobs
            hue = ((hsv[..., 0] + 15) % 180 >= lo) & ((hsv[..., 0] + 15) % 180 < lo + 30)
            mask = (sat & hue).astype(np.uint8)
            n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
            for i in range(1, n):
                x, y, w, h, area = stats[i]
                if area > 400:
                    dets.append(Detection(frame_idx, (float(x), float(y), float(x + w), float(y + h))))
        return dets


class HistFaceAnalyzer:
    """Test-only stand-in: 'face' = top quarter of the box, embedding = colour histogram."""

    def analyze(self, frame, box):
        x1, y1, x2, y2 = box
        face = (x1, y1, x2, y1 + (y2 - y1) / 4)
        emb = body_embedding(frame, box)
        return (face, emb) if emb is not None else None

    def embed_image(self, image):
        h, w = image.shape[:2]
        return body_embedding(image, (0, 0, w, h))


class NoSafetyClassifier:
    def flags(self, frame, det):
        return ()


class TintReplacer:
    """Dev/test-only replacer: shifts hue inside the mask so compositing/QA can be verified."""

    name = "mock_mp"

    def replace(self, crop_video, mask_video, identity_images, seed, workdir):
        out = workdir / f"replaced_{seed}.mp4"
        cap, mcap = cv2.VideoCapture(str(crop_video)), cv2.VideoCapture(str(mask_video))
        fps = cap.get(cv2.CAP_PROP_FPS) or 16
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        wr = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        while True:
            ok, f = cap.read()
            okm, m = mcap.read()
            if not ok:
                break
            hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
            hsv[..., 0] = (hsv[..., 0].astype(int) + 60) % 180
            g = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
            if okm:
                sel = m[..., 0] > 127
                f[sel] = g[sel]
            wr.write(f)
        cap.release()
        mcap.release()
        wr.release()
        return out


def safety_classifier_from_env() -> SafetyClassifier:
    name = os.environ.get("MP_SAFETY_CLASSIFIER", "none")
    if name == "none":
        if os.environ.get("WORKER_ENV", "dev") == "production":
            # Fail closed: never analyze real footage in production without the minor/NSFW screen.
            raise AdapterError("safety_unavailable", "age/NSFW classifier not configured", retryable=False)
        return NoSafetyClassifier()
    raise AdapterError("safety_unavailable", f"unknown classifier {name}", retryable=False)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def transcode_h264(src: Path, dst: Path) -> Path:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-preset", "veryfast", str(dst)], check=True)
    return dst
