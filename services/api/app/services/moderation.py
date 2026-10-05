"""Input moderation (text the user types + template safety tags) and the output-moderation
hook. Text rules here are a first, deterministic layer; production adds a classifier
provider behind `OutputModerator` / `TextClassifier` (see SECURITY.md)."""

import re
import unicodedata
from dataclasses import dataclass, field

BLOCKED_TEMPLATE_TAGS = {"sexual", "nudity", "minor", "violence_graphic", "political_figure", "celebrity"}

# Category -> patterns. Matching is done on a normalized, leetspeak-folded string.
_RULES: dict[str, list[str]] = {
    "sexual": [r"\bnud(e|ity)\b", r"\bnaked\b", r"\bnsfw\b", r"\bporn", r"\bsex(y|ual)?\b", r"\bstrip(ping|tease)\b",
               r"\blingerie\b", r"\bundress"],
    "minors": [r"\bchild(ren)?\b", r"\bkid(s)?\b", r"\bminor(s)?\b", r"\bteen(ager)?s?\b", r"\bunderage\b",
               r"\bschool ?girl\b", r"\bloli"],
    "impersonation_fraud": [r"\bpresident\b", r"\bprime minister\b", r"\bceo of\b", r"\bbank\b.*\bverify",
                            r"\bfake (id|passport|news)\b", r"\bimpersonat"],
    "violence": [r"\bkill(ing)?\b", r"\bshoot(ing)?\b", r"\bbehead", r"\bgore\b", r"\bblood(y)?\b", r"\bsuicide\b",
                 r"\bself[- ]?harm\b"],
    "hate_harassment": [r"\bnazi\b", r"\bkkk\b", r"\bgenocide\b", r"\bterroris"],
}
_COMPILED = {cat: [re.compile(p) for p in pats] for cat, pats in _RULES.items()}
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})


@dataclass
class Decision:
    allowed: bool
    category: str | None = None
    reasons: list[str] = field(default_factory=list)


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    t = t.translate(_LEET)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def check_text(text: str | None) -> Decision:
    if not text:
        return Decision(True)
    t = normalize(text)
    for cat, pats in _COMPILED.items():
        for p in pats:
            if p.search(t):
                return Decision(False, cat, [p.pattern])
    return Decision(True)


def check_template_tags(tags: list[str]) -> Decision:
    bad = sorted(set(tags) & BLOCKED_TEMPLATE_TAGS)
    return Decision(not bad, bad[0] if bad else None, bad)


def check_output(worker_report: dict) -> Decision:
    """Output moderation decision. Workers run frame-level classifiers (NSFW, minor-presence,
    face-count) and send scores; the API owns the policy thresholds so they can change without
    redeploying GPU images."""
    scores = worker_report.get("scores", {}) if worker_report else {}
    if scores.get("nsfw", 0.0) >= 0.6:
        return Decision(False, "sexual", ["nsfw_score"])
    if scores.get("minor_presence", 0.0) >= 0.5:
        return Decision(False, "minors", ["minor_presence"])
    if scores.get("gore", 0.0) >= 0.7:
        return Decision(False, "violence", ["gore_score"])
    if worker_report.get("required") and not scores:
        return Decision(False, "moderation_unavailable", ["no_scores"])
    return Decision(True)
