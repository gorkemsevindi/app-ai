"""Anthropic Claude story writer (structured output validated against StoryBible).

Requires ANTHROPIC_API_KEY (or another credential the SDK resolves). Not exercised in CI without a key;
see docs/KNOWN_GAPS.md. Pricing is looked up from pricing.PRICES at call time."""

import os

from ..config import get_settings
from ..story import StoryBible, WizardInput
from .base import CallInfo, ProviderUnavailable
from .pricing import llm_cost_micros

SYSTEM = """You are the head writer of a vertical (9:16) serialized micro-drama studio.
Write original fiction only: never use real people's names, likenesses or existing IP.
Each episode must play in about {dur} seconds: roughly {words} spoken words across 3 short scenes,
open with a hook in the first line and end on a cliffhanger. Dialogue must be natural, speakable and in
the requested language ({lang}). Respect the audience rating {rating}. Every line has an emotion from the
allowed set so the performance engine can drive facial expressions. Character keys are lowercase ascii."""


class AnthropicWriter:
    id = "anthropic"

    def __init__(self) -> None:
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise ProviderUnavailable("anthropic", "python package 'anthropic'") from e
        if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
            raise ProviderUnavailable("anthropic", "ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic()
        self.model = get_settings().anthropic_model

    def write_bible(self, inp: WizardInput) -> tuple[StoryBible, CallInfo]:
        words = int(inp.episode_duration_s * 2.0)
        prompt = (
            f"Create a story bible and full scripts.\nGenre: {inp.genre}\nLogline: {inp.logline or '(invent one)'}\n"
            f"Title: {inp.title or '(invent one)'}\nEpisodes: {inp.episode_count}\nCharacters: {inp.character_count}\n"
            f"Visual style: {inp.visual_style}\nLanguage: {inp.language}"
        )
        resp = self.client.messages.parse(
            model=self.model,
            max_tokens=32000,
            system=SYSTEM.format(dur=inp.episode_duration_s, words=words, lang=inp.language,
                                 rating=inp.audience_rating),
            messages=[{"role": "user", "content": prompt}],
            output_format=StoryBible,
        )
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            raise ProviderUnavailable("anthropic", f"usable output (stop_reason={resp.stop_reason})")
        u = resp.usage
        info = CallInfo(provider="anthropic", model=self.model, model_version=resp.model,
                        units={"input_tokens": u.input_tokens, "output_tokens": u.output_tokens},
                        cost_usd_micros=llm_cost_micros(self.model, u.input_tokens, u.output_tokens))
        return resp.parsed_output, info
