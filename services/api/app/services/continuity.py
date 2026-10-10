"""Story Continuity / Series Memory (Master Spec V7 §6).

- Project Bible: versioned (world, era/time, locations, characters with bios/goals/relationships/wardrobe,
  props, mysteries with reveal episodes, style rules). Characters link to V6 character UUIDs via the cast alias.
- Episodes declare story events (death, injury, recovery, reveal, relationship, wardrobe, location, item_gain,
  item_loss). The canonical world state before episode N on a branch = bible + events of every earlier episode
  of that branch lineage. Approving an episode stores an immutable snapshot (state + deltas).
- Validator: checks an episode storyboard against the state *before* it — dead characters on screen or
  speaking, unrevealed mysteries leaking, unknown speakers, locations outside the bible, injuries/wardrobe to keep,
  relationship changes — and reports findings by severity. Rule-based and explainable; it never edits content.
- Branches: changing a past event ("don't let her die in episode 4") forks a new branch from that episode;
  earlier episodes are shared, the old branch is untouched. Impact preview lists later episodes that would get new
  findings before anything is changed."""

from __future__ import annotations

import copy
import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import ApiError
from ..models import (
    ContinuityFinding,
    EpisodeSnapshot,
    ProductionEpisode,
    StoryBible,
    StoryBranch,
    StudioProjectVersion,
)


class BibleCharacter(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    name: str = Field(min_length=1, max_length=60)
    character_id: uuid.UUID | None = None  # V6 persistent character UUID (when cast)
    bio: str = Field(default="", max_length=1000)
    goals: str = Field(default="", max_length=400)
    relationships: dict[str, str] = Field(default_factory=dict)
    wardrobe: str = Field(default="", max_length=300)
    status: Literal["alive", "dead", "missing"] = "alive"


class Mystery(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    keywords: list[str] = Field(min_length=1, max_length=10)
    reveal_episode: int = Field(ge=1)


class Bible(BaseModel):
    world: str = Field(default="", max_length=2000)
    era: str = Field(default="", max_length=120)
    locations: list[str] = Field(default_factory=list, max_length=100)
    characters: list[BibleCharacter] = Field(default_factory=list, max_length=60)
    props: list[str] = Field(default_factory=list, max_length=100)
    mysteries: list[Mystery] = Field(default_factory=list, max_length=30)
    style_rules: list[str] = Field(default_factory=list, max_length=30)


class Event(BaseModel):
    type: Literal["death", "injury", "recovery", "reveal", "relationship", "wardrobe", "location", "item_gain",
                  "item_loss", "missing", "return"]
    character: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,40}$")
    target: str | None = Field(default=None, max_length=60)  # other character / mystery / item / place
    value: str | None = Field(default=None, max_length=200)
    scene: str | None = Field(default=None, max_length=40)


def parse_bible(data: dict) -> Bible:
    try:
        return Bible.model_validate(data or {})
    except ValidationError as e:
        raise ApiError(422, "bad_bible", "the story bible is invalid",
                       {"errors": [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()[:10]]}) from e


def parse_events(raw: list) -> list[dict]:
    try:
        return [Event.model_validate(x).model_dump(exclude_none=True) for x in raw or []]
    except ValidationError as e:
        raise ApiError(422, "bad_events", "invalid story events",
                       {"errors": [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()[:10]]}) from e


def latest_bible(db: Session, production_id: uuid.UUID) -> StoryBible | None:
    return db.execute(select(StoryBible).where(StoryBible.production_id == production_id)
                      .order_by(StoryBible.version.desc())).scalars().first()


def save_bible(db: Session, production_id: uuid.UUID, user_id: uuid.UUID, data: dict) -> StoryBible:
    b = parse_bible(data)
    n = db.execute(select(func.coalesce(func.max(StoryBible.version), 0))
                   .where(StoryBible.production_id == production_id)).scalar_one()
    row = StoryBible(production_id=production_id, version=n + 1, data=b.model_dump(mode="json"), created_by=user_id)
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------- branches + lineage

def lineage(db: Session, branch_id: uuid.UUID) -> list[tuple[StoryBranch, int | None]]:
    """[(branch, episodes below this number come from it; None = all)] from the given branch to the root."""
    out, cur, limit = [], db.get(StoryBranch, branch_id), None
    while cur is not None:
        out.append((cur, limit))
        limit = cur.fork_episode if limit is None else min(limit, cur.fork_episode or limit)
        cur = db.get(StoryBranch, cur.parent_branch_id) if cur.parent_branch_id else None
    return out


def episodes_on(db: Session, branch_id: uuid.UUID) -> list[ProductionEpisode]:
    """Episodes visible on a branch: its own, plus the parent's episodes before the fork (recursively)."""
    seen: dict[tuple[int, int], ProductionEpisode] = {}
    for br, below in lineage(db, branch_id):
        for ep in db.execute(select(ProductionEpisode).where(ProductionEpisode.branch_id == br.id)).scalars():
            k = (ep.season, ep.number)
            if k in seen:
                continue
            if br.id != branch_id and below is not None and ep.number >= below:
                continue
            seen[k] = ep
    return sorted(seen.values(), key=lambda e: (e.season, e.number))


# ---------------------------------------------------------------- world state

def initial_state(bible: Bible) -> dict:
    return {"characters": {c.key: {"name": c.name, "status": c.status, "injuries": [], "wardrobe": c.wardrobe,
                                   "location": None, "relationships": dict(c.relationships), "items": []}
                           for c in bible.characters},
            "revealed": [], "locations": list(bible.locations), "history": []}


def apply_events(state: dict, events: list[dict], episode: int) -> dict:
    st = copy.deepcopy(state)
    for e in events:
        ch = st["characters"].setdefault(e.get("character") or "_", {
            "name": e.get("character"), "status": "alive", "injuries": [], "wardrobe": "", "location": None,
            "relationships": {}, "items": []}) if e.get("character") else None
        t = e["type"]
        if t == "death" and ch is not None:
            ch["status"] = "dead"
        elif t == "missing" and ch is not None:
            ch["status"] = "missing"
        elif t == "return" and ch is not None:
            ch["status"] = "alive"
        elif t == "injury" and ch is not None:
            ch["injuries"].append(e.get("value") or "injury")
        elif t == "recovery" and ch is not None:
            ch["injuries"] = []
        elif t == "wardrobe" and ch is not None:
            ch["wardrobe"] = e.get("value") or ""
        elif t == "location" and ch is not None:
            ch["location"] = e.get("value")
        elif t == "relationship" and ch is not None and e.get("target"):
            ch["relationships"][e["target"]] = e.get("value") or ""
        elif t == "item_gain" and ch is not None and e.get("value"):
            ch["items"].append(e["value"])
        elif t == "item_loss" and ch is not None and e.get("value") in ch["items"]:
            ch["items"].remove(e["value"])
        elif t == "reveal" and e.get("target"):
            st["revealed"].append(e["target"])
        st["history"].append({"episode": episode, **e})
    return st


def state_before(db: Session, production_id: uuid.UUID, branch_id: uuid.UUID, episode: ProductionEpisode,
                 bible: Bible | None = None) -> dict:
    if bible is None:
        row = latest_bible(db, production_id)
        bible = parse_bible(row.data if row else {})
    st = initial_state(bible)
    for ep in episodes_on(db, branch_id):
        if (ep.season, ep.number) >= (episode.season, episode.number):
            break
        st = apply_events(st, ep.events or [], ep.number)
    return st


# ---------------------------------------------------------------- validator

def validate_episode(db: Session, production_id: uuid.UUID, branch_id: uuid.UUID, episode: ProductionEpisode,
                     storyboard: dict, events: list[dict] | None = None, bible: Bible | None = None) -> list[dict]:
    if bible is None:
        row = latest_bible(db, production_id)
        bible = parse_bible(row.data if row else {})
    st = state_before(db, production_id, branch_id, episode, bible)
    events = episode.events if events is None else events
    known = {c.key for c in bible.characters}
    findings: list[dict] = []

    def add(sev, code, msg, **details):
        findings.append({"severity": sev, "code": code, "message": msg, "details": details})

    died_here = {e.get("character"): e.get("scene") for e in events if e["type"] == "death"}
    scene_order = [sc["key"] for sc in storyboard.get("scenes", [])]
    for sc in storyboard.get("scenes", []):
        present = set(sc.get("characters_present") or [])
        for sh in sc.get("shots", []):
            present |= set(sh.get("characters") or [])
            speakers = {d.get("character") for d in sh.get("dialogue", []) if d.get("character")}
            for k in sorted(present | speakers):
                c = st["characters"].get(k)
                if c and c["status"] == "dead":
                    add("error", "dead_character_appears",
                        f"{c['name']} died earlier in the story but appears in {sh['key']}", character=k,
                        shot=sh["key"], scene=sc["key"])
                if k in died_here and died_here[k] and died_here[k] in scene_order and \
                        scene_order.index(sc["key"]) > scene_order.index(died_here[k]):
                    add("error", "appears_after_death", f"{k} appears after dying in scene {died_here[k]}",
                        character=k, shot=sh["key"])
            for k in sorted(speakers):
                if known and k not in known:
                    add("warning", "unknown_speaker", f"{k} speaks but is not in the story bible", character=k,
                        shot=sh["key"])
        if sc.get("location") and bible.locations and sc["location"] not in bible.locations:
            add("info", "new_location", f"scene {sc['key']} uses a location that is not in the bible",
                location=sc["location"], scene=sc["key"])
        for k in sorted(present):
            c = st["characters"].get(k)
            if c and c["injuries"]:
                add("info", "carry_injury", f"{c['name']} is still injured ({', '.join(c['injuries'])})",
                    character=k, scene=sc["key"])
    text = " ".join([sh.get("prompt", "") + " " + " ".join(d.get("text", "") for d in sh.get("dialogue", []))
                     for sc in storyboard.get("scenes", []) for sh in sc.get("shots", [])]).lower()
    for m in bible.mysteries:
        revealing = any(e["type"] == "reveal" and e.get("target") == m.key for e in events)
        if m.key not in st["revealed"] and episode.number < m.reveal_episode and not revealing and \
                any(re.search(r"\b" + re.escape(kw.lower()) + r"\b", text) for kw in m.keywords):
            add("warning", "mystery_leak", f"the mystery '{m.key}' is planned for episode {m.reveal_episode}",
                mystery=m.key)
    for e in events:
        if e.get("character") and known and e["character"] not in known:
            add("warning", "event_unknown_character", f"event for unknown character {e['character']}", event=e)
        c = st["characters"].get(e.get("character") or "")
        if e["type"] == "death" and c and c["status"] == "dead":
            add("error", "dies_twice", f"{c['name']} is already dead", character=e["character"])
    sev = {"error": 0, "warning": 1, "info": 2}
    return sorted(findings, key=lambda f: sev[f["severity"]])


def record(db: Session, episode: ProductionEpisode, version_id: uuid.UUID | None, findings: list[dict]) -> None:
    for f in findings:
        db.add(ContinuityFinding(episode_id=episode.id, studio_version_id=version_id, severity=f["severity"],
                                 code=f["code"], message=f["message"][:400], details=f["details"]))
    db.flush()


def snapshot(db: Session, production_id: uuid.UUID, branch_id: uuid.UUID, episode: ProductionEpisode,
             version_id: uuid.UUID | None) -> EpisodeSnapshot:
    row = latest_bible(db, production_id)
    bible = parse_bible(row.data if row else {})
    st = apply_events(state_before(db, production_id, branch_id, episode, bible), episode.events or [],
                      episode.number)
    snap = EpisodeSnapshot(episode_id=episode.id, branch_id=branch_id, number=episode.number,
                           studio_version_id=version_id, bible_version=row.version if row else None,
                           events=list(episode.events or []), state=st)
    db.add(snap)
    db.flush()
    return snap


def impact(db: Session, production_id: uuid.UUID, branch_id: uuid.UUID, episode: ProductionEpisode,
           new_events: list[dict]) -> list[dict]:
    """Which later episodes on this branch get new findings if this episode's events change (nothing is saved)."""
    out = []
    later = [e for e in episodes_on(db, branch_id) if (e.season, e.number) > (episode.season, episode.number)]
    original = episode.events
    try:
        with db.no_autoflush:  # the hypothetical events are never written
            for ep in later:
                sb = _current_storyboard(db, ep)
                episode.events = original
                before = validate_episode(db, production_id, branch_id, ep, sb)
                episode.events = new_events
                after = validate_episode(db, production_id, branch_id, ep, sb)
                bk = {(f["code"], str(f["details"])) for f in before}
                ak = {(f["code"], str(f["details"])) for f in after}
                new = [f for f in after if (f["code"], str(f["details"])) not in bk]
                resolved = [f for f in before if (f["code"], str(f["details"])) not in ak]
                if new or resolved:
                    out.append({"episode": ep.number, "season": ep.season, "new_findings": new,
                                "resolved_findings": resolved})
    finally:
        episode.events = original
    return out


def _current_storyboard(db: Session, ep: ProductionEpisode) -> dict:
    from ..models import StudioProject

    p = db.get(StudioProject, ep.studio_project_id)
    v = db.get(StudioProjectVersion, p.current_version_id) if p and p.current_version_id else None
    return v.storyboard if v else {"scenes": []}
