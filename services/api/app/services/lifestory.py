""""Your life. Your story." (Master Spec V7 §3/§12): an optional guided interview → chronology draft → privacy
scan (third-party names, contact data, sensitive categories) with anonymisation → fact/fiction marking →
rule-based screenplay suggestion → explicit user approval → a private life_story production.

Private by default; nothing is published. Real people named in the story need the user's confirmation of
consent before any sharing (the production's `people_confirmed` gate); their likeness/voice can only come from
V6 consented characters."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..errors import ApiError, not_found
from ..models import LifeStorySession, User

QUESTIONS = [
    {"key": "childhood", "prompt": "Where and how did you grow up?"},
    {"key": "turning_points", "prompt": "Which moments changed your life?"},
    {"key": "people", "prompt": "Who were the important people? (you can use nicknames)"},
    {"key": "places", "prompt": "Which places matter in your story?"},
    {"key": "feelings", "prompt": "What did you feel in those moments?"},
    {"key": "ending", "prompt": "How should the story end?"},
]
SENSITIVE = {"health": r"\b(cancer|disease|illness|depress\w*|kanser|hastal\w*|depresyon)\b",
             "sexuality": r"\b(gay|lesbian|bisexual|eşcinsel|escinsel)\b",
             "religion": r"\b(muslim|christian|jewish|atheist|müslüman|hristiyan|yahudi|ateist)\b",
             "criminal": r"\b(arrest\w*|prison|jail|tutukla\w*|hapis|cezaevi)\b"}
CONTACT = {"email": r"[\w.+-]+@[\w-]+\.[\w.]+", "phone": r"(\+?\d[\d\s-]{8,}\d)",
           "address": r"\b(sokak|sok\.|cadde|cad\.|street|st\.|avenue|mahalle)\b"}
NAME_RE = r"\b([A-ZÇĞİÖŞÜ][a-zçğıöşü]{2,})(?:\s+([A-ZÇĞİÖŞÜ][a-zçğıöşü]{2,}))?\b"
STOP = {"The", "And", "When", "Then", "After", "Before", "Bir", "Ve", "Ama", "Sonra", "Önce", "Benim", "Ben", "Bu",
        "Şu", "Annem", "Babam", "My", "Our", "In", "At", "On"}


class EventIn(BaseModel):
    when: str = Field(min_length=1, max_length=40)  # year, age or "childhood"
    title: str = Field(min_length=2, max_length=120)
    text: str = Field(min_length=2, max_length=2000)
    kind: str = Field(default="fact", pattern=r"^(fact|fiction|dramatized)$")


def start(db: Session, user: User) -> LifeStorySession:
    s = LifeStorySession(user_id=user.id, answers={}, chronology=[], privacy={})
    db.add(s)
    db.flush()
    return s


def get(db: Session, user: User, sid) -> LifeStorySession:
    s = db.get(LifeStorySession, sid)
    if s is None or s.user_id != user.id or s.deleted_at is not None:
        raise not_found("life story")
    return s


def answer(db: Session, s: LifeStorySession, key: str, text: str) -> None:
    if key not in {q["key"] for q in QUESTIONS}:
        raise ApiError(422, "bad_question", "unknown question")
    s.answers = {**(s.answers or {}), key: text[:4000]}


def set_chronology(db: Session, s: LifeStorySession, events: list[dict]) -> list[dict]:
    items = [EventIn.model_validate(e).model_dump() for e in events]

    def order(e):
        m = re.search(r"\d{1,4}", e["when"])
        return (int(m.group(0)) if m else 0, e["title"])
    s.chronology = sorted(items, key=order)
    s.privacy = scan(s)
    s.approved_at = None
    return s.chronology


def draft_chronology(s: LifeStorySession) -> list[dict]:
    """A starting point from the answers (the user edits it): one beat per answered question, marked fact."""
    order = [q["key"] for q in QUESTIONS]
    return [{"when": str(i + 1), "title": k.replace("_", " ").title(), "text": s.answers[k], "kind": "fact"}
            for i, k in enumerate(order) if s.answers.get(k)]


def scan(s: LifeStorySession) -> dict:
    blob = " ".join([*(s.answers or {}).values(), *[e["text"] + " " + e["title"] for e in s.chronology or []]])
    names = sorted({" ".join(x for x in m if x) for m in re.findall(NAME_RE, blob)} - STOP)
    contacts = {k: sorted(set(re.findall(p, blob))) for k, p in CONTACT.items() if re.search(p, blob, re.I)}
    sensitive = sorted(k for k, p in SENSITIVE.items() if re.search(p, blob, re.I))
    return {"possible_people": names[:50], "contact_data": contacts, "sensitive_topics": sensitive,
            "warnings": (["Other people are named: anonymise them or confirm you may tell their story."]
                         if names else []) +
                        (["Contact details found: they will be removed."] if contacts else []) +
                        (["Sensitive topics: they stay private unless you choose otherwise."] if sensitive else [])}


def anonymise(s: LifeStorySession, mapping: dict[str, str]) -> list[dict]:
    out = []
    for e in s.chronology or []:
        e = dict(e)
        for real, alias in mapping.items():
            e["text"] = re.sub(r"\b" + re.escape(real) + r"\b", alias, e["text"])
            e["title"] = re.sub(r"\b" + re.escape(real) + r"\b", alias, e["title"])
        for p in CONTACT.values():
            e["text"] = re.sub(p, "[removed]", e["text"], flags=re.I) if p != CONTACT["address"] else e["text"]
        out.append(e)
    s.chronology = out
    s.privacy = scan(s)
    return out


def screenplay_text(s: LifeStorySession) -> str:
    """Rule-based script suggestion: one scene per chronology beat; fiction/dramatized beats are labelled."""
    parts = []
    for e in s.chronology or []:
        tag = "" if e["kind"] == "fact" else f" ({e['kind'].upper()})"
        parts.append(f"# {e['when']} — {e['title']}{tag}\n{e['text']}\nNARRATOR: {e['title']}.\n")
    return "\n".join(parts)


def approve(s: LifeStorySession, confirm_privacy: bool) -> None:
    if not s.chronology:
        raise ApiError(422, "chronology_required", "add the story beats first")
    if s.privacy.get("contact_data"):
        raise ApiError(409, "contact_data_present", "remove contact details first (anonymise)")
    if not confirm_privacy:
        raise ApiError(422, "privacy_confirmation_required", "review the privacy check and confirm")
    s.approved_at = datetime.now(UTC)
