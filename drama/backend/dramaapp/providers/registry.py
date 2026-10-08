"""Provider selection. Unknown/unconfigured providers raise ProviderUnavailable with the missing item —
the API surfaces this as `provider.unavailable` instead of silently falling back to a mock."""

import os

from ..config import get_settings
from .base import ProviderUnavailable

EXTERNAL_REQUIREMENTS = {
    # provider id: (env var / contract needed, capability)
    "anthropic": ("ANTHROPIC_API_KEY", "llm"),
    "elevenlabs": ("ELEVENLABS_API_KEY", "tts"),
    "google_gemini": ("GEMINI_API_KEY", "video+image (Veo 3.1, Nano Banana)"),
    "sync_labs": ("SYNC_API_KEY", "lipsync"),
    "google_lyria": ("GOOGLE_CLOUD_PROJECT + Vertex AI billing", "music"),
    "deepgram": ("DEEPGRAM_API_KEY", "asr"),
}


def llm():
    p = get_settings().llm_provider
    if p == "local_template":
        from . import llm_local

        class _Local:
            id = "local_template"

            def write_bible(self, inp):
                from .base import CallInfo
                return llm_local.generate(inp), CallInfo(provider="local_template", model="beat-bank-v1",
                                                         is_mock=True)
        return _Local()
    if p == "anthropic":
        from .llm_anthropic import AnthropicWriter
        return AnthropicWriter()
    raise ProviderUnavailable(p, EXTERNAL_REQUIREMENTS.get(p, ("adapter not implemented",))[0])


def tts():
    p = get_settings().tts_provider
    if p == "espeak_local":
        from .tts_espeak import EspeakTTS
        return EspeakTTS()
    need = EXTERNAL_REQUIREMENTS.get(p, ("adapter not implemented",))[0]
    if p in EXTERNAL_REQUIREMENTS and os.getenv(need.split()[0]):
        raise ProviderUnavailable(p, "adapter implementation pending commercial contract (see docs/KNOWN_GAPS.md)")
    raise ProviderUnavailable(p, need)


def music():
    p = get_settings().music_provider
    if p == "procedural_local":
        from .music_local import ProceduralMusic
        return ProceduralMusic()
    raise ProviderUnavailable(p, EXTERNAL_REQUIREMENTS.get(p, ("adapter not implemented",))[0])


def google_media():
    """Realistic route provider (Nano Banana + Veo). Tests replace this function with a fake."""
    from .google_media import GoogleMedia
    return GoogleMedia()


def status() -> list[dict]:
    """What is configured right now — powers /admin/providers and the setup report."""
    s = get_settings()
    rows = []
    for cap, chosen in [("llm", s.llm_provider), ("tts", s.tts_provider), ("music", s.music_provider),
                        ("video", s.video_provider), ("lipsync", s.lipsync_provider)]:
        rows.append({"capability": cap, "selected": chosen, "local": chosen.endswith("_local") or chosen == "local_template"})
    for pid, (need, cap) in EXTERNAL_REQUIREMENTS.items():
        rows.append({"capability": cap, "candidate": pid, "requires": need,
                     "credential_present": bool(os.getenv(need.split()[0]))})
    return rows
