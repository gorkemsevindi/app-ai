"""V2 Trend Engine contract (spec §6/§18). The MVP does not import this module."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass
class TrendSignal:
    source: str            # licensed/permitted data source only
    key: str               # e.g. sound id, hashtag, motion pattern id
    observed_at: datetime
    volume: float
    velocity: float        # d(volume)/dt
    locale: str | None = None


@dataclass
class TemplateSuggestion:
    trend_key: str
    score: float
    category: str
    rationale: str


class TrendSource(Protocol):
    def fetch(self, since: datetime) -> list[TrendSignal]: ...


class TrendRanker(Protocol):
    def rank(self, signals: list[TrendSignal]) -> list[TemplateSuggestion]: ...
