"""Provider adapter contracts. Pipeline code talks only to these interfaces; concrete providers are
chosen by config (DRAMA_*_PROVIDER) so commercial routing decisions never require code changes."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..story import StoryBible, WizardInput


class ProviderUnavailable(Exception):
    """Raised when a provider is selected but not configured (missing key / contract). Never mocked silently."""

    def __init__(self, provider: str, missing: str):
        self.provider, self.missing = provider, missing
        super().__init__(f"Provider '{provider}' is not configured: missing {missing}")


@dataclass
class CallInfo:
    provider: str
    model: str
    model_version: str = ""
    is_mock: bool = False
    units: dict = field(default_factory=dict)
    cost_usd_micros: int = 0
    seed: int | None = None


@dataclass
class WordTiming:
    word: str
    start: float
    end: float


@dataclass
class VisemeKey:
    t: float  # seconds from clip start
    viseme: str  # rest|AA|EE|OO|UU|MBP|FV|L|S


@dataclass
class SpeechClip:
    wav: Path
    duration: float
    words: list[WordTiming]
    visemes: list[VisemeKey]
    envelope: list[float]  # RMS per 1/100 s (drives jaw opening)
    info: CallInfo


class LLMProvider(Protocol):
    id: str

    def write_bible(self, inp: WizardInput) -> tuple[StoryBible, CallInfo]: ...


class TTSProvider(Protocol):
    id: str

    def synthesize(self, text: str, *, lang: str, voice: dict, emotion: str, intensity: float,
                   out: Path) -> SpeechClip: ...


class MusicProvider(Protocol):
    id: str

    def compose(self, *, mood: str, genre: str, duration: float, seed: int, out: Path) -> CallInfo: ...
