"""Dev/test-only adapter: renders a synthetic clip with ffmpeg so the full pipeline (queue, lease,
upload, encode, moderation, refund) can be exercised without a GPU. Refuses to run in production."""

from __future__ import annotations

import os
import subprocess
import threading
import time

from .base import AdapterError, Capabilities, GenerationRequest, GenerationResult, ProgressFn


class MockAdapter:
    name = "mock"

    def __init__(self) -> None:
        if os.environ.get("WORKER_ENV", "dev") == "production":
            raise RuntimeError("mock adapter is not allowed in production")
        self._proc: subprocess.Popen | None = None

    def capabilities(self) -> Capabilities:
        return Capabilities(model=self.name, tasks=["face_swap_v2v", "identity_i2v", "multi_person_replace"],
                            max_duration_s=20, resolutions=["720x1280"], min_vram_gb=0, max_identities=8,
                            license="internal")

    def healthcheck(self) -> dict:
        return {"ok": True, "gpu": False}

    def estimate(self, req: GenerationRequest) -> dict[str, float]:
        return {"gpu_seconds": 0.0, "vram_gb": 0.0}

    def generate(self, req: GenerationRequest, progress: ProgressFn, cancel: threading.Event) -> GenerationResult:
        out = req.workdir / "raw.mp4"
        t0 = time.time()
        if req.params.get("mock_fail"):
            raise AdapterError(str(req.params["mock_fail"]), "forced failure", retryable=True)
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
               "-i", f"testsrc2=size={req.width}x{req.height}:rate=24:duration={req.duration_s}",
               "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(out)]
        self._proc = subprocess.Popen(cmd)  # noqa: S603
        while self._proc.poll() is None:
            if cancel.is_set():
                self._proc.kill()
                from .base import Cancelled

                raise Cancelled()
            progress(0.5, "generating")
            time.sleep(0.05)
        if self._proc.returncode != 0:
            raise AdapterError("render_failed", "ffmpeg failed")
        return GenerationResult(video_path=out, fps=24, gpu_seconds=0.0, metrics={"wall_s": time.time() - t0})

    def cancel(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.kill()
