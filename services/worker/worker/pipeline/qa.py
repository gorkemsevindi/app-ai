"""Output QA + moderation scoring. The worker produces *scores*; the API owns thresholds/policy.

Production requires a real scorer (configure OUTPUT_SCORER). Candidates must be commercially
licensed — see docs/research.md (e.g. a self-hosted NSFW classifier with permissive weights, or a
moderation API). Identity-similarity must not use InsightFace/ArcFace non-commercial weights."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class OutputScorer(Protocol):
    def score(self, video: Path) -> dict[str, float]: ...


class NullScorer:
    """Dev only. Reports `required=False` so the API knows no scores were produced."""

    def score(self, video: Path) -> dict[str, float]:
        return {}


def get_scorer() -> tuple[OutputScorer, bool]:
    name = os.environ.get("OUTPUT_SCORER", "null")
    required = os.environ.get("WORKER_ENV", "dev") == "production"
    if name == "null":
        return NullScorer(), required
    raise RuntimeError(f"unknown OUTPUT_SCORER {name}")


def moderation_report(video: Path) -> dict:
    scorer, required = get_scorer()
    return {"scores": scorer.score(video), "required": required}
