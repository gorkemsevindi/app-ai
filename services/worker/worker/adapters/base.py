"""Provider-agnostic model adapter contract (spec §4). Every model — self-hosted or a remote API
fallback — implements this interface, so swapping models never touches the API, queue or app."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class AdapterError(Exception):
    def __init__(self, code: str, message: str = "", retryable: bool = True):
        super().__init__(message or code)
        self.code, self.retryable = code, retryable


class Cancelled(AdapterError):
    def __init__(self) -> None:
        super().__init__("cancelled", "cancelled by user", retryable=False)


@dataclass
class Capabilities:
    model: str
    tasks: list[str]                      # e.g. ["face_swap_v2v", "identity_i2v", "multi_person_replace"]
    max_duration_s: float
    resolutions: list[str]                # e.g. ["480x832", "720x1280"]
    min_vram_gb: int
    max_identities: int = 1
    license: str = ""


@dataclass
class GenerationRequest:
    job_id: str
    task: str
    prompt: str
    negative_prompt: str
    duration_s: float
    width: int
    height: int
    identity_images: list[Path]
    workdir: Path
    params: dict[str, Any] = field(default_factory=dict)
    source_video: Path | None = None      # template clip / user-uploaded driving video
    seed: int | None = None


@dataclass
class GenerationResult:
    video_path: Path                      # raw model output (any codec/size); encoder normalizes
    fps: float
    gpu_seconds: float
    metrics: dict[str, Any] = field(default_factory=dict)


class ProgressFn(Protocol):
    def __call__(self, fraction: float, stage: str | None = None) -> None: ...


class ModelAdapter(Protocol):
    name: str

    def capabilities(self) -> Capabilities: ...
    def healthcheck(self) -> dict[str, Any]: ...
    def estimate(self, req: GenerationRequest) -> dict[str, float]: ...  # {"gpu_seconds", "vram_gb"}
    def generate(self, req: GenerationRequest, progress: ProgressFn, cancel: threading.Event) -> GenerationResult: ...
    def cancel(self) -> None: ...
