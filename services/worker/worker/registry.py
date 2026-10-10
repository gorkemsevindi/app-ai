"""Which adapters this worker process loads (WORKER_MODELS=comma list). One GPU image can carry
several adapters; the API-side router picks preferred/fallback per template."""

from __future__ import annotations

import os

from .adapters import command
from .adapters.mock import MockAdapter
from .characters.images import GeminiImage, MockImage
from .multiperson import adapters as mp
from .studio.shots import MockT2V, VeoShot


class StudioAssembler:
    """CPU-only assembly of AI Studio projects (ffmpeg)."""

    name = "studio_assembler"

    def healthcheck(self) -> dict:
        return {"ok": True, "gpu": False}

class EditorRenderer:
    """CPU-only final renders of V8 editor projects (ffmpeg)."""

    name = "editor_renderer"

    def healthcheck(self) -> dict:
        return {"ok": True, "gpu": False}


FACTORIES = {
    "mock": MockAdapter,
    "dreamid_v": command.dreamid_v,
    "wan22_animate_14b": command.wan22_animate_14b,
    "wan22_ti2v_5b": command.wan22_ti2v_5b,
    # multi-person
    "mp_analyzer": mp.mp_analyzer,
    "dreamid_v_mp": mp.dreamid_v_mp,
    "wan22_animate_mp": mp.wan22_animate_mp,
    "mock_mp_analyzer": mp.mock_mp_analyzer,
    "mock_mp": mp.mock_mp,
    # AI Studio
    "mock_t2v": MockT2V,
    "veo": VeoShot,
    "studio_assembler": StudioAssembler,
    # V8 editor exports (CPU ffmpeg)
    "editor_renderer": EditorRenderer,
    # V6 character identity images
    "mock_image": MockImage,
    "gemini_image": GeminiImage,
}


def load_adapters() -> dict:
    names = [n.strip() for n in os.environ.get("WORKER_MODELS", "mock").split(",") if n.strip()]
    unknown = set(names) - set(FACTORIES)
    if unknown:
        raise RuntimeError(f"unknown models: {unknown}")
    return {n: FACTORIES[n]() for n in names}
