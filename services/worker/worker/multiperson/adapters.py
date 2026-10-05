"""Worker-side adapters for multi-person jobs. They plug into the same registry / ModelRouter as the
single-person adapters; the API routes jobs by model name (`analysis_model`, `preferred_model`,
`fallback_model` in the `multi_person` remote config)."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from ..adapters.base import AdapterError, Capabilities
from . import components as C
from .pipeline import analyze, qa_gate, replace


class AnalysisAdapter:
    def __init__(self, name: str, detector_factory, faces_factory, safety_factory):
        self.name = name
        self._df, self._ff, self._sf = detector_factory, faces_factory, safety_factory
        self._components = None

    def _load(self):
        if self._components is None:
            self._components = (self._df(), self._ff(), self._sf())
        return self._components

    def capabilities(self) -> Capabilities:
        return Capabilities(model=self.name, tasks=["person_analysis"], max_duration_s=60, resolutions=["any"],
                            min_vram_gb=0 if self.name.startswith("mock") else 8, max_identities=0,
                            license="Apache-2.0/MIT components")

    def healthcheck(self) -> dict:
        try:
            self._load()
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": repr(e)}

    def run(self, payload: dict, source: Path, workdir: Path, progress, cancel: threading.Event) -> dict:
        det, faces, _ = self._load()
        safety = self._sf()  # re-evaluated each job: fails closed in production if unconfigured
        spec = payload.get("spec", {})
        return analyze(source, det, faces, safety, workdir, progress, cancel,
                       max_seconds=float(spec.get("max_duration_s", 15)) + 5,
                       min_face_px=int(spec.get("min_face_px", 64)))

    def cancel(self) -> None:
        pass


class MultiReplaceAdapter:
    def __init__(self, name: str, replacer_factory, faces_factory=None, min_identity: float | None = None):
        self.name = name
        self._rf, self._ff = replacer_factory, faces_factory
        self.min_identity = min_identity

    def capabilities(self) -> Capabilities:
        return Capabilities(model=self.name, tasks=["multi_person_replace"], max_duration_s=20,
                            resolutions=["480x832", "720x1280"],
                            min_vram_gb=0 if self.name.startswith("mock") else 16, max_identities=16)

    def healthcheck(self) -> dict:
        try:
            r = self._rf()
            inner = getattr(r, "adapter", None)
            return inner.healthcheck() if inner is not None else {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": repr(e)}

    def run(self, payload: dict, source: Path, tracks: dict, identities: dict[str, list[Path]], workdir: Path,
            progress, cancel: threading.Event) -> tuple[Path, dict]:
        spec = payload["spec"]
        w, h = (int(x) for x in spec.get("resolution", "720x1280").split("x"))
        model_res = (480, 832) if (spec.get("preview") or w <= 480) else (w, h)
        faces = self._ff() if self._ff else None
        t0 = time.time()
        out, qa = replace(source, tracks, spec["assignments"], identities, self._rf(), workdir, progress, cancel,
                          max_seconds=float(spec.get("max_seconds", 15)), model_res=model_res, faces=faces,
                          seed=int(payload.get("attempt", 1)) * 1000)
        qa["gpu_seconds"] = time.time() - t0
        problems = qa_gate(qa, self.min_identity)
        if problems:
            # Retryable: next attempt gets a new seed and, after FALLBACK_AFTER_S, may route to the fallback model.
            raise AdapterError("qa_failed", ",".join(problems), retryable=True)
        return out, qa

    def cancel(self) -> None:
        pass


def _faces_prod():
    return C.OpenCVFaceAnalyzer()


def mp_analyzer() -> AnalysisAdapter:
    return AnalysisAdapter("mp_analyzer", C.OnnxPersonDetector, _faces_prod, C.safety_classifier_from_env)


def dreamid_v_mp() -> MultiReplaceAdapter:
    from ..adapters.command import dreamid_v

    return MultiReplaceAdapter("dreamid_v_mp", lambda: C.CommandReplacer(dreamid_v()), _faces_prod,
                               min_identity=float(os.environ.get("MP_MIN_IDENTITY_SIM", "0.35")))


def wan22_animate_mp() -> MultiReplaceAdapter:
    from ..adapters.command import wan22_animate_14b

    return MultiReplaceAdapter("wan22_animate_mp", lambda: C.CommandReplacer(wan22_animate_14b()), _faces_prod,
                               min_identity=float(os.environ.get("MP_MIN_IDENTITY_SIM", "0.35")))


def mock_mp_analyzer() -> AnalysisAdapter:
    if os.environ.get("WORKER_ENV", "dev") == "production":
        raise RuntimeError("mock adapters are not allowed in production")
    return AnalysisAdapter("mock_mp_analyzer", C.ColorBlobDetector, C.HistFaceAnalyzer, C.NoSafetyClassifier)


def mock_mp() -> MultiReplaceAdapter:
    if os.environ.get("WORKER_ENV", "dev") == "production":
        raise RuntimeError("mock adapters are not allowed in production")
    return MultiReplaceAdapter("mock_mp", C.TintReplacer)


def load_tracks(path: Path) -> dict:
    return json.loads(path.read_text())
