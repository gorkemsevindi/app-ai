"""Creative intelligence (Master Spec V5, Phase C): intent parsing, versioned prompt optimization, creative modes,
candidate plans, variation seeds, similarity audits and diversity tracking.

Non-memorization contract (V5 §1):
- What is "learned" is *production technique* (composition, camera, lighting, pacing): a fixed, versioned library
  below. No other user's prompts, plots, characters or outputs are ever read to plan a new video.
- Intent precedence: the user's explicit text and constraints > selected template / authorized references >
  selected creative mode > adaptive suggestions. Faithful mode adds nothing; templates are never "optimized".
- The original prompt is never overwritten: each shot keeps `prompt`; `optimized_prompt` is derived from
  (prompt, mode, strategy version, composition, seed) and stored next to it.
- Similarity is measured only against content lawfully available for comparison: the same user's other
  projects and the public licensed template catalog. A near-duplicate produces a warning and a revised-plan
  offer, never a rejection; template remixes are intentional reuse and exempt.
- Popularity never feeds planning: rankings/views/shares are not inputs here."""

from __future__ import annotations

import hashlib
import math
import re
import secrets
import uuid
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FeatureFlag, StudioProject, StudioProjectVersion, Template

STRATEGY = "pv1"  # bump when the technique library or the optimizer changes (pixels change -> hashes change)
MODES = ("faithful", "balanced", "experimental")
CANDIDATES = {"faithful": 1, "balanced": 2, "experimental": 3}

# Production technique only (no story content). Each composition is a camera grammar for a sequence of shots.
COMPOSITIONS: dict[str, dict] = {
    "classic": {"camera": ["wide establishing shot", "medium shot", "medium close-up", "close-up"],
                "lighting": "soft key light, natural contrast", "pacing": "steady"},
    "dynamic": {"camera": ["handheld tracking shot", "low-angle push-in", "whip pan into medium shot",
                           "fast dolly-out reveal"], "lighting": "high contrast, rim light", "pacing": "fast"},
    "intimate": {"camera": ["close-up, shallow depth of field", "over-the-shoulder shot", "slow push-in",
                            "macro detail shot"], "lighting": "warm practical lights", "pacing": "slow"},
    "documentary": {"camera": ["static tripod medium shot", "observational handheld", "wide natural framing",
                               "eye-level close-up"], "lighting": "available natural light", "pacing": "measured"},
    "aerial": {"camera": ["high aerial establishing shot", "descending crane shot", "top-down shot",
                          "orbiting medium shot"], "lighting": "golden hour", "pacing": "flowing"},
    "symmetry": {"camera": ["centered symmetrical wide shot", "straight-on medium shot", "centered close-up",
                            "tracking shot along the axis"], "lighting": "even, saturated palette",
                 "pacing": "deliberate"},
}
GENRES = {
    "dance": r"\b(danc\w*|dans\w*|choreograph\w*|koreograf\w*)",
    "comedy": r"\b(funny|comedy|komik|joke|şaka|saka|prank)",
    "dialogue": r"\b(says|talks|conversation|dialog\w*|konuş\w*|konus\w*|söyle\w*)",
    "music": r"\b(song|sing\w*|music|şarkı|sarki|müzik|muzik|concert|konser)",
    "ads": r"\b(product|brand|advert\w*|reklam|ürün|urun|sale|indirim)",
    "history": r"\b(histor\w*|tarih\w*|ancient|antik|medieval|ottoman|osmanlı)",
    "travel": r"\b(travel|city|beach|bridge|istanbul|seyahat|şehir|sehir|plaj|köprü|kopru)",
}
DEFAULT_THRESHOLDS = {"default": 0.82, "dance": 0.78, "music": 0.8, "ads": 0.85}


def config(db: Session) -> dict:
    f = db.get(FeatureFlag, "creative")
    v = (f.value or {}) if f else {}
    return {"thresholds": {**DEFAULT_THRESHOLDS, **v.get("thresholds", {})},
            "weights": {"adherence": 0.4, "feasibility": 0.2, "novelty": 0.2, "cost": 0.1, "safety": 0.1,
                        **v.get("weights", {})}}


# ---------------------------------------------------------------- intent

def genre_of(text: str) -> str:
    low = text.lower()
    hits = [(g, len(re.findall(p, low))) for g, p in GENRES.items()]
    g, n = max(hits, key=lambda x: x[1])
    return g if n else "general"


def parse_intent(brief_text: str, mode: str, target_duration_s: int, aspect_ratio: str, n_characters: int) -> dict:
    """Immutable record of what the user asked for. Quoted phrases are hard requirements."""
    return {"schema": 1, "original_sha256": hashlib.sha256(brief_text.encode()).hexdigest(),
            "genre": genre_of(brief_text), "mode": mode,
            "required_phrases": [q.strip() for q in re.findall(r"[\"“”]([^\"“”]{2,120})[\"“”]", brief_text)],
            "constraints": {"target_duration_s": target_duration_s, "aspect_ratio": aspect_ratio,
                            "characters": n_characters},
            "precedence": ["explicit_instructions", "template_or_references", "creative_mode", "suggestions"]}


# ---------------------------------------------------------------- prompt optimization (versioned, deterministic)

def optimize(prompt: str, mode: str, composition: str, seed: int, index: int) -> str:
    """Original prompt first and intact; technique hints appended by mode. Faithful = unchanged."""
    if mode == "faithful":
        return prompt
    comp = COMPOSITIONS.get(composition, COMPOSITIONS["classic"])
    parts = [prompt.rstrip(". "), f"Lighting: {comp['lighting']}", f"Pacing: {comp['pacing']}"]
    if mode == "experimental":
        alt = list(COMPOSITIONS)[(seed + index) % len(COMPOSITIONS)]
        parts.append(f"Visual variation: {COMPOSITIONS[alt]['lighting']}")
    return (". ".join(parts) + ".")[:1500]


def apply_creative(sb: dict) -> dict:
    """(Re)derive optimized prompts and camera grammar for a storyboard that carries a creative mode.
    Called on every validation so edits to `prompt` never leave a stale optimized prompt."""
    mode = sb.get("creative_mode")
    if mode is None:
        return sb  # legacy / manual storyboards: untouched (their render hashes stay stable)
    comp = sb.get("composition") or "classic"
    seed = int(sb.get("seed") or 0)
    sb["prompt_strategy"] = STRATEGY
    shots = [s for sc in sb["scenes"] for s in sc["shots"]]
    for i, s in enumerate(shots):
        if s.get("derive") and s["derive"].get("kind") == "trim":
            s["optimized_prompt"] = None
            continue
        s["optimized_prompt"] = optimize(s["prompt"], mode, comp, seed, i)
    return sb


# ---------------------------------------------------------------- similarity + novelty

def _shingles(text: str, n: int = 3) -> set[str]:
    t = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()
    return {t[i:i + n] for i in range(max(1, len(t) - n + 1))}


def similarity(a: str, b: str) -> float:
    sa, sb_ = _shingles(a), _shingles(b)
    if not sa or not sb_:
        return 0.0
    return round(len(sa & sb_) / len(sa | sb_), 4)


def storyboard_text(sb: dict) -> str:
    return " ".join(s["prompt"] for sc in sb["scenes"] for s in sc["shots"])


def comparison_corpus(db: Session, user_id: uuid.UUID, exclude_project: uuid.UUID | None) -> list[tuple[str, str, str]]:
    """(kind, id, text) the plan may be compared with: the user's own other projects + the public catalog."""
    out: list[tuple[str, str, str]] = []
    own = db.execute(select(StudioProjectVersion).join(StudioProject, StudioProject.id ==
                                                       StudioProjectVersion.project_id)
                     .where(StudioProject.user_id == user_id, StudioProject.id != exclude_project,
                            StudioProject.deleted_at.is_(None))
                     .order_by(StudioProjectVersion.created_at.desc()).limit(50)).scalars()
    out += [("own_project", str(v.project_id), storyboard_text(v.storyboard)) for v in own]
    for t in db.execute(select(Template).where(Template.visibility == "public",
                                               Template.moderation_status == "approved")).scalars():
        out.append(("template", str(t.id), f"{t.title} {t.description}"))
    return out


def audit_similarity(db: Session, user_id: uuid.UUID, project_id: uuid.UUID | None, sb: dict, genre: str,
                     intentional: bool = False) -> dict:
    from ..models import SimilarityAudit

    cfg = config(db)
    text = storyboard_text(sb)
    best = ("", "", 0.0)
    for kind, ref, other in comparison_corpus(db, user_id, project_id):
        s = similarity(text, other)
        if s > best[2]:
            best = (kind, ref, s)
    threshold = float(cfg["thresholds"].get(genre, cfg["thresholds"]["default"]))
    decision = "intentional_reuse" if intentional else ("near_duplicate" if best[2] >= threshold else "ok")
    a = SimilarityAudit(user_id=user_id, project_id=project_id, subject="storyboard", category=genre,
                        method="char3_jaccard", threshold=threshold, top_kind=best[0] or None,
                        top_ref=best[1] or None, top_score=best[2], decision=decision)
    db.add(a)
    db.flush()
    return {"decision": decision, "score": best[2], "threshold": threshold, "matched": best[0] or None,
            "method": "char3_jaccard", "audit_id": str(a.id)}


# ---------------------------------------------------------------- candidates + scoring

def new_seed() -> int:
    return secrets.randbelow(2**31 - 1)


def pick_compositions(mode: str, seed: int, n: int, avoid: set[str] | None = None) -> list[str]:
    names = [c for c in COMPOSITIONS if c not in (avoid or set())] or list(COMPOSITIONS)
    if mode == "faithful":
        return ["classic"]
    start = seed % len(names)
    return [names[(start + i) % len(names)] for i in range(n)]


def score_candidate(sb: dict, intent: dict, credits_: int, budget: int | None, feasible: bool, novelty: float,
                    weights: dict) -> dict:
    text = " ".join([storyboard_text(sb)] + [s.get("caption") or "" for sc in sb["scenes"] for s in sc["shots"]])
    req = intent["required_phrases"]
    adherence = (sum(1 for p in req if p.lower() in text.lower()) / len(req)) if req else 1.0
    total_s = sum(s["duration_s"] for sc in sb["scenes"] for s in sc["shots"])
    tgt = intent["constraints"]["target_duration_s"]
    adherence = adherence * (1 - min(0.5, abs(total_s - tgt) / max(tgt, 1) / 2))
    cost = 1.0 if budget is None else max(0.0, 1 - max(0, credits_ - budget) / max(budget, 1))
    parts = {"adherence": round(adherence, 4), "feasibility": 1.0 if feasible else 0.0, "safety": 1.0,
             "cost": round(cost, 4), "novelty": round(novelty, 4)}
    parts["total"] = round(sum(parts[k] * float(weights[k]) for k in weights), 4)
    return parts


def entropy(counter: Counter) -> float:
    n = sum(counter.values())
    if not n:
        return 0.0
    return round(-sum(c / n * math.log2(c / n) for c in counter.values()), 4)


def register_strategy(db: Session) -> None:
    """Record the strategy version in use (idempotent) so admins can see / retire versions."""
    import json

    from ..models import PromptStrategy

    cfg = {"compositions": COMPOSITIONS, "modes": list(MODES)}
    sha = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()
    if db.get(PromptStrategy, STRATEGY) is None:
        db.add(PromptStrategy(key=STRATEGY, description="technique library v1 (composition, lighting, pacing)",
                              config_sha256=sha, config=cfg, status="active"))
        db.flush()
