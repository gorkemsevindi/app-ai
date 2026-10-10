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


# ---------------------------------------------------------------- V7 fiction policy (drama dialogue)

RATINGS = ("general", "teen", "mature")
# Profanity / slang (EN + TR, ASCII-folded like `normalize`): allowed in fiction, but it sets the age rating.
_PROFANITY = [re.compile(p) for p in (
    r"\bfuck", r"\bshit", r"\bbitch", r"\basshole", r"\bbastard", r"\bdamn", r"\bcunt", r"\bdick(head)?\b",
    r"\bmotherfuck", r"\bsiktir", r"\bsikeyim", r"\bamk\b", r"\bamina", r"\borospu", r"\bpic\b", r"\byarrak",
    r"\bkahpe", r"\bserefsiz", r"\bgerizekal", r"\bsalak\b", r"\bdangalak")]
_CHILD_SEXUAL = [re.compile(r"\bloli"), re.compile(r"\bshota"), re.compile(r"\bchild ?porn"), re.compile(r"\bcp\b")]
_SELF_HARM_INCITE = [re.compile(r"\bkill yourself\b"), re.compile(r"\bkys\b"), re.compile(r"\bgo die\b")]
# what each sensitive category needs at minimum (fiction)
_NEEDS = {"profanity": "teen", "violence": "general", "hate_harassment": "mature", "sexual": "mature",
          "self_harm_incitement": "mature"}


def _hits(t: str, cat: str) -> bool:
    return any(p.search(t) for p in _COMPILED.get(cat, []))


def check_fiction(text: str | None, rating: str, kind: str = "dialogue") -> Decision:
    """Fiction policy (Master Spec V7 §4): in a fictional production, dialogue may contain slang, swearing,
    anger, threats between characters and adult language — the *rating* records it. Never allowed, at any
    rating: sexual content involving minors, impersonation/fraud patterns, and sexual *visuals* (video providers
    and app stores). Returned `reasons` are the content flags that drove the rating."""
    if not text:
        return Decision(True)
    t = normalize(text)
    if any(p.search(t) for p in _CHILD_SEXUAL) or (_hits(t, "minors") and _hits(t, "sexual")):
        return Decision(False, "minors_sexual", ["never_allowed"])
    if _hits(t, "impersonation_fraud"):
        return Decision(False, "impersonation_fraud", ["never_allowed"])
    if kind == "visual" and _hits(t, "sexual"):
        return Decision(False, "sexual_visual", ["never_allowed"])
    flags = []
    if any(p.search(t) for p in _PROFANITY):
        flags.append("profanity")
    for cat in ("violence", "hate_harassment", "sexual"):
        if _hits(t, cat):
            flags.append(cat)
    if any(p.search(t) for p in _SELF_HARM_INCITE):
        flags.append("self_harm_incitement")
    level = RATINGS.index(rating) if rating in RATINGS else 0
    need = max((RATINGS.index(_NEEDS[f]) for f in flags), default=0)
    if need > level:
        return Decision(False, f"rating_required:{RATINGS[need]}", flags)
    return Decision(True, None, flags)
