"""AI Studio (V4 Stage B): projects -> immutable storyboard versions -> estimate -> confirmed render of shots ->
assembly with captions and licensed audio.

Principles (spec V4 §2/§3/§6/§12/§13):
- Plan -> estimate -> user confirms the exact credits -> render. Nothing expensive runs without confirmation.
- Storyboards are immutable versions. A shot is identified by the hash of everything that changes its pixels;
  a new version reuses every unchanged shot, so an edit only re-renders (and only charges) what changed.
- Every shot is a normal generation job: same queue, lease/retry, reserve/settle/release credit ledger, output
  moderation per rendered shot, provenance.
- Real likeness only through the owner's consented identity profile *and* an active consent receipt, checked
  when the render is requested and again at dispatch. Revoking consent stops future use.
- Providers and prices are remote config; a provider without a configured price cannot render (fail closed).
- The rule-based planner is labelled as such; it is not presented as an AI director."""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_serializer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import ApiError, not_found
from ..models import (
    AudioAsset,
    ConsentReceipt,
    FeatureFlag,
    GenerationJob,
    IdentityProfile,
    JobKind,
    JobStatus,
    ProfileStatus,
    StudioCharacter,
    StudioProject,
    StudioProjectVersion,
    StudioShotRender,
    User,
)
from . import credits, moderation

FLAG = "studio"
CONSENT_TERMS = "studio-likeness-v1"
DEFAULTS: dict = {
    "director": "rule_based",            # rule_based|gemini
    "director_model": None,              # required for gemini (verified model id, set by ops)
    "shot_provider": "mock_t2v",         # worker adapter name; production sets a real, licensed provider
    "fallback_provider": None,
    "provider_capabilities": {
        "mock_t2v": ["TEXT_TO_VIDEO", "CHARACTER_REFERENCE", "VIDEO_EXTEND", "VIDEO_PREPEND"],
        "veo": ["TEXT_TO_VIDEO"],        # reference images only after verification on the chosen model
    },
    "provider_usd_per_second": {"mock_t2v": 0.0},  # real providers must be priced by ops from current docs
    "allowed_shot_durations": [4, 6, 8],
    "max_shots": 12,
    "max_total_s": 60,
    "credits_per_second": {"standard": 10, "premium": 25},
    "assemble_credits": 0,
    "max_job_cost_usd": 5.0,
    "max_project_cost_usd": 30.0,
}
LOCK_RANK = ["standard", "strong", "strict"]
RESOLUTIONS = {"9:16": "720x1280", "16:9": "1280x720", "1:1": "720x720"}
KEY_RE = r"^[a-z0-9_-]{1,40}$"


def config(db: Session) -> tuple[bool, dict]:
    f = db.get(FeatureFlag, FLAG)
    v = {**DEFAULTS, **((f.value or {}) if f else {})}
    for k in ("provider_capabilities", "provider_usd_per_second", "credits_per_second"):
        v[k] = {**DEFAULTS[k], **(((f.value or {}).get(k) or {}) if f else {})}
    return bool(f and f.enabled), v


def require_enabled(db: Session) -> dict:
    enabled, cfg = config(db)
    if not enabled:
        raise ApiError(403, "feature_disabled", "AI Studio is not available yet")
    return cfg


# ---------------------------------------------------------------- storyboard schema

def _drop_unset(data: dict, fields: tuple[str, ...]) -> dict:
    """V7 fields are optional and omitted when unset, so pre-V7 storyboards serialize exactly as before."""
    return {k: v for k, v in data.items() if not (k in fields and v in (None, False, 0, [], {}))}


V7_DIALOGUE_FIELDS = ("id", "end_s", "language", "emotion", "delivery", "intensity", "pronunciation", "voice_id",
                      "locked", "exact", "rev", "variant_of")


class Dialogue(BaseModel):
    character: str | None = Field(default=None, pattern=KEY_RE)
    text: str = Field(min_length=1, max_length=600)
    start_s: float = Field(default=0.0, ge=0)
    # V7 Exact Dialogue Mode: a persistent line with performance controls. `exact` = the text is the user's
    # literal words (never smoothed, paraphrased or rewritten); `locked` = edits need an explicit unlock.
    id: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,40}$")
    end_s: float | None = Field(default=None, ge=0)
    language: str | None = Field(default=None, max_length=8)
    emotion: str | None = Field(default=None, max_length=40)
    delivery: Literal["normal", "shout", "whisper", "cry", "sarcastic", "laugh", "angry", "calm", "mock"] | None \
        = None
    intensity: float | None = Field(default=None, ge=0, le=1)
    pronunciation: str | None = Field(default=None, max_length=200)
    voice_id: str | None = Field(default=None, max_length=120)
    locked: bool = False
    exact: bool = False
    rev: int = Field(default=0, ge=0)
    variant_of: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,40}$")  # A/B variant of another line

    @model_serializer(mode="wrap")
    def _ser(self, handler):
        return _drop_unset(handler(self), V7_DIALOGUE_FIELDS)


class Derive(BaseModel):
    """A shot made from an existing render: `trim` cuts a range of it (no provider call, no credits);
    `extend` continues it after its end / before its start (provider call with VIDEO_EXTEND / VIDEO_PREPEND)."""

    kind: Literal["trim", "extend"]
    from_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    start_s: int | None = Field(default=None, ge=0)
    end_s: int | None = Field(default=None, ge=1)
    direction: Literal["end", "start"] | None = None


class Shot(BaseModel):
    key: str = Field(pattern=KEY_RE)
    duration_s: int
    prompt: str = Field(min_length=5, max_length=1500)
    camera: str = Field(default="", max_length=200)
    characters: list[str] = Field(default_factory=list, max_length=4)
    dialogue: list[Dialogue] = Field(default_factory=list, max_length=6)
    caption: str | None = Field(default=None, max_length=300)
    transition: Literal["cut", "fade"] = "cut"
    derive: Derive | None = None
    optimized_prompt: str | None = Field(default=None, max_length=1500)  # V5: derived; `prompt` is the original


VISUAL_STYLES = ("photoreal", "cinematic", "cartoon_2d", "animation_3d", "anime", "stylized", "mixed")
SCENE_TYPES = ("dialogue", "action", "landscape", "montage", "transition", "animation")
V7_SCENE_FIELDS = ("visual_style", "scene_type", "location", "time_of_day", "characters_present")


class Scene(BaseModel):
    key: str = Field(pattern=KEY_RE)
    title: str = Field(default="", max_length=120)
    shots: list[Shot] = Field(min_length=1, max_length=40)  # V7: long episodes (limits enforced per project)
    # V7: per-scene style override, scene class (capability/cost matrix) and continuity context
    visual_style: Literal["photoreal", "cinematic", "cartoon_2d", "animation_3d", "anime", "stylized", "mixed"] | None \
        = None
    scene_type: Literal["dialogue", "action", "landscape", "montage", "transition", "animation"] | None = None
    location: str | None = Field(default=None, max_length=120)
    time_of_day: str | None = Field(default=None, max_length=40)
    characters_present: list[str] = Field(default_factory=list, max_length=12)

    @model_serializer(mode="wrap")
    def _ser(self, handler):
        return _drop_unset(handler(self), V7_SCENE_FIELDS)


class CharacterRef(BaseModel):
    key: str = Field(pattern=KEY_RE)
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=500)
    character_id: uuid.UUID | None = None
    cast_member_id: uuid.UUID | None = None  # V6: frozen cast snapshot (persistent character identity)


class AudioCfg(BaseModel):
    music_asset_id: uuid.UUID | None = None
    music_volume: float = Field(default=0.25, ge=0, le=1)
    voice: Literal["none", "tts"] = "none"


class Captions(BaseModel):
    enabled: bool = True
    burn_in: bool = False


class Storyboard(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    language: str = Field(default="en", max_length=8)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    style: str = Field(default="", max_length=300)
    quality: Literal["standard", "premium"] = "standard"
    characters: list[CharacterRef] = Field(default_factory=list, max_length=8)
    scenes: list[Scene] = Field(min_length=1, max_length=80)
    audio: AudioCfg = Field(default_factory=AudioCfg)
    captions: Captions = Field(default_factory=Captions)
    limitations: list[str] = Field(default_factory=list, max_length=20)
    # V5 creative fields (None = legacy/manual storyboard: no optimization, render hashes unchanged)
    creative_mode: Literal["faithful", "balanced", "experimental"] | None = None
    composition: str | None = Field(default=None, max_length=40)
    seed: int | None = Field(default=None, ge=0)
    prompt_strategy: str | None = Field(default=None, max_length=40)
    visual_style: Literal["photoreal", "cinematic", "cartoon_2d", "animation_3d", "anime", "stylized", "mixed"] | None \
        = None  # V7 production default style (scenes may override)

    @model_serializer(mode="wrap")
    def _ser(self, handler):
        return _drop_unset(handler(self), ("visual_style",))

    def shots(self) -> list[Shot]:
        return [s for sc in self.scenes for s in sc.shots]

    def scene_of(self) -> dict[str, Scene]:
        return {s.key: sc for sc in self.scenes for s in sc.shots}


class Brief(BaseModel):
    brief: str = Field(min_length=10, max_length=4000)
    title: str | None = Field(default=None, max_length=120)
    language: str = Field(default="en", max_length=8)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    target_duration_s: int = Field(default=24, ge=4, le=180)
    quality: Literal["standard", "premium"] = "standard"
    style: str = Field(default="", max_length=300)
    character_ids: list[uuid.UUID] = Field(default_factory=list, max_length=8)
    cast_member_ids: list[uuid.UUID] = Field(default_factory=list, max_length=8)  # V6 cast (also via @mentions)
    music_asset_id: uuid.UUID | None = None
    platform: str | None = Field(default=None, max_length=30)
    creative_mode: Literal["faithful", "balanced", "experimental"] = "balanced"


# ---------------------------------------------------------------- characters + consent

def active_consent(db: Session, character: StudioCharacter) -> ConsentReceipt | None:
    return db.execute(select(ConsentReceipt).where(
        ConsentReceipt.subject_type == "studio_character", ConsentReceipt.subject_id == character.id,
        ConsentReceipt.revoked_at.is_(None)).order_by(ConsentReceipt.granted_at.desc())).scalars().first()


def create_character(db: Session, user: User, name: str, description: str, traits: dict,
                     identity_profile_id: uuid.UUID | None, attest_own_likeness: bool,
                     project_id: uuid.UUID | None, actor_license_id: uuid.UUID | None = None) -> StudioCharacter:
    require_enabled(db)
    if actor_license_id is not None:  # a licensed AI actor (Stage D): the licence is the authorization
        from . import actors

        actors.require_enabled(db)
        reason = actors.license_usable(db, actor_license_id, user.id)
        if reason:
            raise ApiError(409, "license_invalid", "this actor licence can't be used", {"reason": reason})
        from ..models import ActorLicense, ActorListing

        lst = db.get(ActorListing, db.get(ActorLicense, actor_license_id).listing_id)
        _check_text(name)
        _check_text(description)
        ch = StudioCharacter(user_id=user.id, project_id=project_id, name=name or lst.display_name,
                             description=description, traits=traits or {}, voice_permission="none",
                             identity_profile_id=lst.identity_profile_id, actor_license_id=actor_license_id)
        db.add(ch)
        db.flush()
        return ch
    for text in (name, description):
        _check_text(text)
    if project_id is not None:
        get_project(db, user, project_id)
    ch = StudioCharacter(user_id=user.id, project_id=project_id, name=name, description=description,
                         traits=traits or {}, voice_permission="none")
    if identity_profile_id is not None:
        prof = db.get(IdentityProfile, identity_profile_id)
        if prof is None or prof.user_id != user.id or prof.status != ProfileStatus.ready:
            raise not_found("identity profile")
        if not attest_own_likeness:
            raise ApiError(422, "consent_required", "confirm this is your own likeness and you allow its use")
        ch.identity_profile_id = prof.id
    db.add(ch)
    db.flush()
    if ch.identity_profile_id:
        grant_consent(db, user, ch)
    return ch


def grant_consent(db: Session, user: User, ch: StudioCharacter) -> ConsentReceipt:
    r = ConsentReceipt(user_id=user.id, subject_type="studio_character", subject_id=ch.id,
                       scope={"likeness": True, "voice": False, "identity_profile_id": str(ch.identity_profile_id)},
                       terms_version=CONSENT_TERMS,
                       statement="I am the person in this identity profile and allow its use in my AI Studio projects.")
    db.add(r)
    db.flush()
    return r


def revoke_consent(db: Session, user: User, ch: StudioCharacter) -> int:
    rows = db.execute(select(ConsentReceipt).where(
        ConsentReceipt.subject_type == "studio_character", ConsentReceipt.subject_id == ch.id,
        ConsentReceipt.revoked_at.is_(None))).scalars().all()
    for r in rows:
        r.revoked_at = datetime.now(UTC)
    return len(rows)


def get_character(db: Session, user: User, character_id: uuid.UUID) -> StudioCharacter:
    ch = db.get(StudioCharacter, character_id)
    if ch is None or ch.user_id != user.id or ch.deleted_at is not None:
        raise not_found("character")
    return ch


def character_usable(db: Session, ch: StudioCharacter) -> bool:
    """A character with a real likeness needs a ready profile and an active consent receipt."""
    if ch.deleted_at is not None:
        return False
    if ch.actor_license_id is not None:
        from . import actors

        return actors.license_usable(db, ch.actor_license_id, ch.user_id) is None
    if ch.identity_profile_id is None:
        return True
    prof = db.get(IdentityProfile, ch.identity_profile_id)
    return prof is not None and prof.status == ProfileStatus.ready and active_consent(db, ch) is not None


# ---------------------------------------------------------------- projects + versions

def get_project(db: Session, user: User, project_id: uuid.UUID) -> StudioProject:
    p = db.get(StudioProject, project_id)
    if p is None or p.user_id != user.id or p.deleted_at is not None:
        raise not_found("project")
    return p


def get_version(db: Session, project: StudioProject, version_id: uuid.UUID | None) -> StudioProjectVersion:
    vid = version_id or project.current_version_id
    v = db.get(StudioProjectVersion, vid) if vid else None
    if v is None or v.project_id != project.id:
        raise not_found("version")
    return v


def _check_text(text: str | None) -> None:
    d = moderation.check_text(text)
    if not d.allowed:
        raise ApiError(422, "content_blocked", "this text is not allowed", {"category": d.category})


def _text_checker(project: StudioProject | None):
    """V7: fictional productions use the fiction policy (slang/swearing allowed per rating); else the default."""
    pol = (project.content_policy or {}) if project is not None else {}
    if not pol.get("fiction"):
        return lambda text, kind="dialogue": _check_text(text)
    rating = pol.get("rating", "general")

    def check(text, kind="dialogue"):
        d = moderation.check_fiction(text, rating, kind)
        if not d.allowed:
            raise ApiError(422, "content_blocked", "this text is not allowed in this production",
                           {"category": d.category, "flags": d.reasons, "rating": rating})
    return check


def validate_storyboard(db: Session, user: User, raw: dict, cfg: dict, project: StudioProject | None = None
                        ) -> Storyboard:
    from . import creative

    if project is not None and project.limits:  # V7: long episodes carry their own format limits
        cfg = {**cfg, **{k: v for k, v in project.limits.items() if k in ("max_shots", "max_total_s")}}

    if isinstance(raw, dict) and raw.get("creative_mode") is not None and isinstance(raw.get("scenes"), list):
        import copy

        try:
            raw = creative.apply_creative(copy.deepcopy(raw))
        except (KeyError, TypeError):
            pass  # malformed: the schema validation below reports it
    try:
        sb = Storyboard.model_validate(raw)
    except ValidationError as e:
        raise ApiError(422, "bad_storyboard", "storyboard is invalid",
                       {"errors": [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()[:10]]}) from e
    shots = sb.shots()
    keys = [s.key for s in shots] + [sc.key for sc in sb.scenes]
    if len(set(keys)) != len(keys):
        raise ApiError(422, "bad_storyboard", "scene and shot keys must be unique")
    if len(shots) > int(cfg["max_shots"]):
        raise ApiError(422, "too_many_shots", f"at most {cfg['max_shots']} shots")
    if sum(s.duration_s for s in shots) > int(cfg["max_total_s"]):
        raise ApiError(422, "too_long", f"at most {cfg['max_total_s']} seconds in total")
    allowed = set(int(x) for x in cfg["allowed_shot_durations"])
    for s in shots:
        d = s.derive
        if d is not None and d.kind == "trim":
            if d.start_s is None or d.end_s is None or d.end_s <= d.start_s or s.duration_s != d.end_s - d.start_s:
                raise ApiError(422, "bad_storyboard", f"shot {s.key}: trim range must match its duration")
        elif s.duration_s not in allowed:
            raise ApiError(422, "bad_duration", f"shot durations must be one of {sorted(allowed)}")
        if d is not None and d.kind == "extend" and d.direction is None:
            raise ApiError(422, "bad_storyboard", f"shot {s.key}: extend needs a direction")
    char_keys = {c.key for c in sb.characters}
    for s in shots:
        if set(s.characters) - char_keys or any(d.character and d.character not in char_keys for d in s.dialogue):
            raise ApiError(422, "bad_storyboard", f"shot {s.key} references an unknown character")
        for d in s.dialogue:
            if d.start_s >= s.duration_s:
                raise ApiError(422, "bad_storyboard", f"dialogue in {s.key} starts after the shot ends")
    check = _text_checker(project)
    for text in [sb.title, sb.style] + [c.name for c in sb.characters] + [c.description for c in sb.characters] + \
            [t for s in shots for t in [s.prompt, s.camera]]:
        check(text, "visual")
    for text in [t for s in shots for t in [s.caption] + [d.text for d in s.dialogue]]:
        check(text, "dialogue")
    line_ids = [d.id for s in shots for d in s.dialogue if d.id]
    if len(line_ids) != len(set(line_ids)):
        raise ApiError(422, "bad_storyboard", "dialogue line ids must be unique")
    for s in shots:
        for d in s.dialogue:
            if d.end_s is not None and not d.start_s <= d.end_s <= s.duration_s:
                raise ApiError(422, "bad_storyboard", f"dialogue in {s.key} must end inside the shot")
    texts_all = [sb.title, sb.style] + [t for s in shots for t in [s.prompt, s.camera, s.caption] +
                                        [d.text for d in s.dialogue]]
    for c in sb.characters:
        if c.cast_member_id is not None:
            from ..models import Character, ProjectCastMember
            from . import character_market

            m = db.get(ProjectCastMember, c.cast_member_id)
            p = db.get(StudioProject, m.project_id) if m is not None else None
            if c.character_id is not None or m is None or m.removed_at is not None or p is None or \
                    p.user_id != user.id:
                raise ApiError(422, "bad_storyboard", f"character {c.key}: unknown cast member")
            if m.grant_id is not None:  # licensed character: the grant's prohibited contexts apply
                from ..models import CharacterLicenseGrant

                g = db.get(CharacterLicenseGrant, m.grant_id)
                bad = character_market.check_contexts(g.terms_snapshot, texts_all) if g else ["license_missing"]
                if bad:
                    raise ApiError(422, "license_terms_violation",
                                   "this storyboard uses the licensed character in a prohibited context",
                                   {"contexts": bad, "character": c.key})
            ch = db.get(Character, m.character_id)
            if ch is not None and ch.creator_id != user.id and m.grant_id is None:
                raise ApiError(422, "bad_storyboard", f"character {c.key}: not licensed")
        if c.character_id is not None:
            ch = get_character(db, user, c.character_id)
            if ch.actor_license_id is not None:  # licensed actor: the licence's prohibited contexts apply
                from ..models import ActorLicense
                from . import actors

                lic = db.get(ActorLicense, ch.actor_license_id)
                bad = actors.check_contexts(lic, texts_all) if lic is not None else ["license_missing"]
                if bad:
                    raise ApiError(422, "license_terms_violation",
                                   "this storyboard uses the licensed actor in a prohibited context",
                                   {"contexts": bad, "character": c.key})
    if sb.audio.music_asset_id is not None:
        a = db.get(AudioAsset, sb.audio.music_asset_id)
        if a is None or a.user_id != user.id or a.deleted_at is not None or a.status != "ready":
            raise not_found("audio")
    return sb


def dialogue_lines(storyboard: dict) -> dict[str, dict]:
    """line id -> {line, shot, scene} for every identified dialogue line."""
    out = {}
    for sc in storyboard.get("scenes", []):
        for sh in sc.get("shots", []):
            for d in sh.get("dialogue", []):
                if d.get("id"):
                    out[d["id"]] = {"line": d, "shot": sh["key"], "scene": sc["key"]}
    return out


def check_locked_lines(before: dict, after: dict) -> None:
    old, new = dialogue_lines(before), dialogue_lines(after)
    for lid, o in old.items():
        if not o["line"].get("locked"):
            continue
        n = new.get(lid)
        if n is None or n["line"].get("text") != o["line"]["text"] or \
                n["line"].get("character") != o["line"].get("character"):
            if n is not None and not n["line"].get("locked") and n["line"].get("text") == o["line"]["text"]:
                continue  # this edit only unlocks the line
            raise ApiError(409, "dialogue_locked",
                           "a locked dialogue line can't be changed or removed; unlock it first", {"line_id": lid})


def add_version(db: Session, user: User, project: StudioProject, sb: Storyboard, source: str, brief: dict,
                director: dict, parent: uuid.UUID | None, creative: dict | None = None,
                make_current: bool = True) -> StudioProjectVersion:
    if source == "edit" and parent is not None:  # V7: locked dialogue lines change only after an explicit unlock
        prev = db.get(StudioProjectVersion, parent)
        if prev is not None:
            check_locked_lines(prev.storyboard, sb.model_dump(mode="json"))
    n = db.execute(select(func.coalesce(func.max(StudioProjectVersion.version), 0))
                   .where(StudioProjectVersion.project_id == project.id)).scalar_one()
    v = StudioProjectVersion(project_id=project.id, version=n + 1, parent_version_id=parent, source=source,
                             brief=brief, storyboard=sb.model_dump(mode="json"), director=director,
                             created_by=user.id, creative=creative or {})
    db.add(v)
    db.flush()
    if not make_current:
        return v
    project.current_version_id = v.id
    project.aspect_ratio, project.language = sb.aspect_ratio, sb.language
    if project.status == "draft":
        project.status = "planned"
    return v


# ---------------------------------------------------------------- director

class CastChar:
    """A project cast member presented to the directors like a character (V6)."""

    def __init__(self, member_id: uuid.UUID, name: str, description: str, alias: str):
        self.cast_member_id, self.name, self.description, self.alias = member_id, name, description, alias


def _char_dicts(characters: list) -> list[dict]:
    out = []
    for i, c in enumerate(characters):
        if isinstance(c, CastChar):
            out.append({"key": c.alias, "name": c.name, "description": c.description,
                        "cast_member_id": str(c.cast_member_id)})
        else:
            out.append({"key": f"c{i + 1}", "name": c.name, "description": c.description, "character_id": str(c.id)})
    return out


def _shot_chars(chars: list[dict], text: str) -> list[str]:
    """@alias mentions in a sentence pick that shot's cast; otherwise everyone (legacy behaviour)."""
    low = text.lower()
    named = [c["key"] for c in chars if c.get("cast_member_id") and f"@{c['key']}" in low]
    return (named or [c["key"] for c in chars])[:4]


class RuleBasedDirector:
    """Deterministic planner (no AI): one shot per sentence of the brief. Clearly labelled to the user."""

    name, label = "rule_based", "rule-based planner (not AI)"

    def plan(self, brief: Brief, characters: list[StudioCharacter], cfg: dict, composition: str = "classic",
             seed: int = 0) -> dict:
        from .creative import COMPOSITIONS

        cams = COMPOSITIONS.get(composition, COMPOSITIONS["classic"])["camera"]
        allowed = sorted(int(x) for x in cfg["allowed_shot_durations"])
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", brief.brief) if len(s.strip()) >= 5]
        target = min(brief.target_duration_s, int(cfg["max_total_s"]))
        n = max(1, min(len(sentences) or 1, int(cfg["max_shots"]), max(1, target // allowed[0])))
        per = min(allowed, key=lambda d: abs(d - target / n))
        chars = _char_dicts(characters)
        shots = []
        for i in range(n):
            text = sentences[i] if i < len(sentences) else brief.brief[:200]
            shots.append({"key": f"sh{i + 1}", "duration_s": per,
                          "prompt": f"{brief.style + '. ' if brief.style else ''}{text}"[:1500],
                          "camera": cams[(i + seed) % len(cams)] if i else cams[0],
                          "characters": _shot_chars(chars, text), "caption": text[:300],
                          "transition": "cut" if i == 0 else "fade"})
        return {"title": brief.title or brief.brief[:60], "language": brief.language,
                "aspect_ratio": brief.aspect_ratio, "style": brief.style, "quality": brief.quality,
                "characters": chars, "scenes": [{"key": "s1", "title": "", "shots": shots}],
                "audio": {"music_asset_id": str(brief.music_asset_id) if brief.music_asset_id else None},
                "captions": {"enabled": True, "burn_in": False},
                "limitations": ["Planned by the rule-based planner, not an AI director: one shot per sentence.",
                                "Dialogue is shown as captions; speech synthesis is not enabled yet."],
                "creative_mode": brief.creative_mode, "composition": composition, "seed": seed}


class GeminiDirector:
    """Gemini via the official google-genai SDK with a JSON schema response. Needs GEMINI_API_KEY and a model id
    configured by ops (`studio.director_model`); the output is validated like any user edit."""

    name = "gemini"

    def __init__(self, model: str, client=None):
        self.model, self.label = model, f"AI director ({model})"
        self._client = client

    def _c(self):
        if self._client is None:
            key = get_settings().gemini_api_key
            if not key:
                raise ApiError(503, "director_not_configured", "the AI director is not configured")
            from google import genai

            self._client = genai.Client(api_key=key)
        return self._client

    def plan(self, brief: Brief, characters: list[StudioCharacter], cfg: dict) -> dict:
        from google.genai import types

        chars = _char_dicts(characters)
        instructions = (
            "You are a film director planning a short video. Return ONLY JSON for the storyboard schema. "
            f"Use shot durations from {cfg['allowed_shot_durations']} seconds, at most {cfg['max_shots']} shots and "
            f"{min(brief.target_duration_s, int(cfg['max_total_s']))} seconds in total. Write visual prompts in "
            "English; dialogue and captions in the requested language. Use only the given characters (by key). "
            "List honest limitations (e.g. exact likeness or lip-sync is not guaranteed). No real public figures, "
            "no sexual content, no minors in unsafe contexts.")
        user = json.dumps({"brief": brief.brief, "language": brief.language, "aspect_ratio": brief.aspect_ratio,
                           "style": brief.style, "quality": brief.quality, "platform": brief.platform,
                           "characters": chars}, ensure_ascii=False)
        resp = self._c().models.generate_content(
            model=self.model, contents=user,
            config=types.GenerateContentConfig(system_instruction=instructions, temperature=0.7,
                                               response_mime_type="application/json",
                                               response_json_schema=Storyboard.model_json_schema()))
        try:
            data = json.loads(resp.text or "")
        except (TypeError, json.JSONDecodeError) as e:
            raise ApiError(502, "director_invalid_output", "the director returned an invalid storyboard") from e
        # never trust model-provided ids/references: re-bind characters by key, keep the requested music
        by_key = {c["key"]: c for c in chars}
        data["characters"] = [by_key[c["key"]] for c in data.get("characters", []) if c.get("key") in by_key]
        data.setdefault("audio", {})["music_asset_id"] = str(brief.music_asset_id) if brief.music_asset_id else None
        data["aspect_ratio"], data["quality"] = brief.aspect_ratio, brief.quality
        return data


_director_override = None


def set_director(d) -> None:  # tests
    global _director_override
    _director_override = d


def director(cfg: dict):
    if _director_override is not None:
        return _director_override
    if cfg["director"] == "gemini":
        if not cfg.get("director_model"):
            raise ApiError(503, "director_not_configured", "the AI director is not configured")
        return GeminiDirector(cfg["director_model"])
    return RuleBasedDirector()


def plan_storyboard(db: Session, user: User, project: StudioProject, brief: Brief) -> StudioProjectVersion:
    cfg = require_enabled(db)
    _check_text(brief.brief)
    _check_text(brief.style)
    chars = [get_character(db, user, cid) for cid in brief.character_ids] + plan_cast(db, user, project, brief)
    d = director(cfg)
    sb, meta = plan_candidates(db, user, project, brief, chars, d, cfg)
    return add_version(db, user, project, sb, "director", brief.model_dump(mode="json"),
                       {"provider": d.name, "label": d.label}, project.current_version_id, creative=meta)


def plan_cast(db: Session, user: User, project: StudioProject, brief: Brief) -> list[CastChar]:
    """V6.6: @mentions in the brief must all resolve to this project's cast (never silently); explicitly
    listed cast members are added too."""
    from . import casting, characters

    ids: list[uuid.UUID] = list(brief.cast_member_ids)
    if characters.find_mentions(brief.brief):
        res = casting.resolve_script(db, user, project, brief.brief)
        if not res["all_bound"]:
            raise ApiError(409, "cast_resolution_required", "cast or disambiguate the mentioned characters first",
                           {"mentions": [m for m in res["mentions"] if m["status"] != "bound"]})
        ids += [uuid.UUID(m["cast_member_id"]) for m in res["mentions"]]
    out = []
    for mid in dict.fromkeys(ids):
        m = casting.get_member(db, project, mid)
        d = casting.display(db, m.id)
        out.append(CastChar(m.id, d["name"], d["description"], d["alias"]))
    return out


def variations(db: Session, user: User, project: StudioProject, count: int, mode: str | None
               ) -> list[StudioProjectVersion]:
    """V5 §3: user-requested variations of the same intent — distinct seeds AND composition strategies (randomness
    alone isn't creativity). The brief, required phrases and constraints are unchanged; the current version stays."""
    from . import creative

    cfg = require_enabled(db)
    base = get_version(db, project, None)
    if not base.brief:
        raise ApiError(409, "variations_need_brief", "variations need a storyboard planned from a brief")
    brief = Brief.model_validate({**base.brief, **({"creative_mode": mode} if mode else {})})
    chars = [get_character(db, user, cid) for cid in brief.character_ids] + plan_cast(db, user, project, brief)
    d = director(cfg)
    used = {base.creative.get("composition")} - {None}
    out = []
    for _ in range(count):
        sb, meta = plan_candidates(db, user, project, brief, chars, d, cfg, seed=creative.new_seed(), avoid=used)
        used.add(meta["composition"])
        out.append(add_version(db, user, project, sb, "variation", brief.model_dump(mode="json"),
                               {"provider": d.name, "label": d.label}, base.id, creative=meta, make_current=False))
    return out


def plan_candidates(db: Session, user: User, project: StudioProject, brief: Brief, chars: list, d, cfg: dict,
                    seed: int | None = None, avoid: set[str] | None = None) -> tuple[Storyboard, dict]:
    """V5 §3: generate mode-dependent candidate plans (distinct composition + seed), score adherence,
    feasibility, safety, cost and novelty, keep the best. The original brief and intent are never altered."""
    from . import creative

    ccfg = creative.config(db)
    intent = creative.parse_intent(brief.brief, brief.creative_mode, brief.target_duration_s, brief.aspect_ratio,
                                   len(chars))
    seed = creative.new_seed() if seed is None else seed
    n = creative.CANDIDATES[brief.creative_mode] if isinstance(d, RuleBasedDirector) else 1
    corpus = creative.comparison_corpus(db, user.id, project.id)
    scored = []
    for i, comp in enumerate(creative.pick_compositions(brief.creative_mode, seed, n, avoid)):
        cseed = (seed + i * 7919) % (2**31 - 1)
        raw = d.plan(brief, chars, cfg, comp, cseed) if isinstance(d, RuleBasedDirector) else d.plan(brief, chars, cfg)
        if not isinstance(d, RuleBasedDirector):
            raw.update({"creative_mode": brief.creative_mode, "composition": comp, "seed": cseed})
        try:
            sb = validate_storyboard(db, user, raw, cfg, project)
        except ApiError as e:
            if e.code == "content_blocked":
                raise
            raise ApiError(502, "director_invalid_output", "the director returned an unusable storyboard",
                           {"reason": e.code}) from e
        dump = sb.model_dump(mode="json")
        est = estimate_storyboard(db, user, project, dump)
        text = creative.storyboard_text(dump)
        novelty = 1.0 - max((creative.similarity(text, t) for _, _, t in corpus), default=0.0)
        sc = creative.score_candidate(dump, intent, est["credits"], project.budget_credits,
                                      est["provider"] is not None and not est["missing_capabilities"], novelty,
                                      ccfg["weights"])
        scored.append((sc["total"], i, sb, {"composition": comp, "seed": cseed, "scores": sc}))
    scored.sort(key=lambda x: (-x[0], x[1]))
    _, _, best, chosen = scored[0]
    sim = creative.audit_similarity(db, user.id, project.id, best.model_dump(mode="json"), intent["genre"])
    creative.register_strategy(db)
    meta = {"intent": intent, "mode": brief.creative_mode, "strategy": creative.STRATEGY,
            "composition": chosen["composition"], "seed": chosen["seed"],
            "candidates": [c for _, _, _, c in scored], "similarity": sim}
    return best, meta


# ---------------------------------------------------------------- estimate

def _shot_inputs(db: Session, sb: Storyboard, shot: Shot, provider: str) -> dict:
    by_key = {c.key: c for c in sb.characters}
    refs = []
    for k in shot.characters:
        c = by_key[k]
        ch = db.get(StudioCharacter, c.character_id) if c.character_id else None
        ref = {"key": k, "name": c.name, "description": c.description,
               "character_id": str(ch.id) if ch else None,
               "identity_profile_id": str(ch.identity_profile_id) if ch and ch.identity_profile_id else None}
        if c.cast_member_id is not None:  # V6: the frozen identity snapshot changes pixels -> hashed
            from . import casting

            ref["cast"] = casting.cast_input(db, c.cast_member_id)
        refs.append(ref)
    if shot.derive is not None and shot.derive.kind == "trim":
        # a trim's pixels are fully defined by its source render and the range
        return {"trim_of": shot.derive.from_hash, "start_s": shot.derive.start_s, "end_s": shot.derive.end_s,
                "aspect_ratio": sb.aspect_ratio, "provider": provider, "characters": refs}
    out = {"prompt": shot.prompt, "camera": shot.camera, "duration_s": shot.duration_s, "style": sb.style,
           "aspect_ratio": sb.aspect_ratio, "quality": sb.quality, "provider": provider, "characters": refs}
    scene = sb.scene_of().get(shot.key)
    vstyle = (scene.visual_style if scene is not None else None) or sb.visual_style
    if vstyle:  # V7 visual style (production default or scene override): changes pixels -> hashed
        out["visual_style"] = vstyle
    if shot.optimized_prompt is not None:  # V5: the derived prompt changes pixels -> part of the hash
        out["optimized_prompt"] = shot.optimized_prompt
        out["creative"] = {"mode": sb.creative_mode, "strategy": sb.prompt_strategy, "seed": sb.seed}
    if shot.derive is not None:  # extension: continuity depends on the source render
        out["extend"] = {"from_hash": shot.derive.from_hash, "direction": shot.derive.direction}
    return out


def shot_hash(inputs: dict) -> str:
    """Everything that changes the pixels of a shot (the provider is recorded on the job, not hashed)."""
    material = {k: v for k, v in inputs.items() if k != "provider"}
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def needed_capabilities(inputs: dict) -> set[str]:
    if "trim_of" in inputs:
        return set()  # cut from an existing render by the assembler
    caps = {"TEXT_TO_VIDEO"}
    if inputs.get("extend"):
        caps.add("VIDEO_EXTEND" if inputs["extend"]["direction"] == "end" else "VIDEO_PREPEND")
    if any(c["identity_profile_id"] or c.get("cast") for c in inputs["characters"]):
        caps.add("CHARACTER_REFERENCE")
    if inputs.get("visual_style"):  # V7 capability matrix: the provider must declare the style
        caps.add("STYLE_" + inputs["visual_style"].upper())
    return caps


def estimate(db: Session, user: User, project: StudioProject, v: StudioProjectVersion) -> dict:
    out = estimate_storyboard(db, user, project, v.storyboard)
    return {**out, "version_id": str(v.id), "version": v.version, "director": v.director}


def route_for(db: Session, sb: Storyboard, user_id: uuid.UUID | None = None) -> dict:
    """Provider for this storyboard: one provider per render keeps the look consistent across shots."""
    from . import router

    needed: set[str] = set()
    for s in sb.shots():
        needed |= needed_capabilities(_shot_inputs(db, sb, s, ""))
    return router.choose(db, needed, sb.quality, user_id)


def estimate_storyboard(db: Session, user: User, project: StudioProject, storyboard: dict) -> dict:
    cfg = require_enabled(db)
    sb = Storyboard.model_validate(storyboard)
    route = route_for(db, sb, user.id)
    provider = route["provider"]
    from . import router

    info = router.registry(db).get(provider or "", {})
    caps = set(info.get("capabilities", []))
    usd_ps = info.get("usd_per_second")
    cps = float(cfg["credits_per_second"][sb.quality])
    renders = {r.content_hash: r for r in db.execute(select(StudioShotRender).where(
        StudioShotRender.project_id == project.id)).scalars()}
    rows, new_credits, new_usd, missing, blocked, parents = [], 0, 0.0, set(), [], []
    scenes = sb.scene_of()
    type_mult = cfg.get("scene_type_credit_multiplier") or {}
    for shot in sb.shots():
        inp = _shot_inputs(db, sb, shot, provider)
        h = shot_hash(inp)
        r = renders.get(h)
        state = r.status if r is not None and r.status in ("ready", "queued") else "new"
        if shot.derive is not None:
            src = renders.get(shot.derive.from_hash)
            if src is None or src.status != "ready":
                parents.append({"shot": shot.key, "reason": "source_not_rendered"})
            if shot.derive.kind == "trim":
                state = "derived"
        credits_ = 0 if state == "derived" else int(math.ceil(cps * shot.duration_s))
        st = scenes[shot.key].scene_type
        if st and state != "derived" and float(type_mult.get(st, 1.0)) != 1.0:  # V7 scene-class cost matrix
            credits_ = int(math.ceil(credits_ * float(type_mult[st])))
        usd = round(usd_ps * shot.duration_s, 4) if usd_ps is not None else None
        lack = needed_capabilities(inp) - caps
        missing |= lack
        for c in inp["characters"]:
            ch = db.get(StudioCharacter, uuid.UUID(c["character_id"])) if c["character_id"] else None
            if ch is not None and not character_usable(db, ch):
                blocked.append({"shot": shot.key, "character": c["key"], "reason": "consent_missing"})
        cast = [c for c in inp["characters"] if c.get("cast")]
        lock = max((c["cast"]["lock_mode"] for c in cast), key=LOCK_RANK.index, default=None)
        license_rows: list[dict] = []
        if cast and state != "derived":
            from . import casting, characters

            _, ccfg = characters.config(db)
            if lock in ("strong", "strict"):  # more identity conditioning / QC: priced by remote config
                credits_ = int(math.ceil(credits_ * float(ccfg["lock"]["strong_credit_multiplier"])))
                usd = round(usd * float(ccfg["lock"]["strong_credit_multiplier"]), 4) if usd is not None else None
            for c in cast:
                problem = casting.cast_problem(db, user.id, project.id, uuid.UUID(c["cast"]["member_id"]))
                if problem:
                    blocked.append({"shot": shot.key, "character": c["key"], "reason": problem})
                    continue
                pr = casting.pricing(db, uuid.UUID(c["cast"]["member_id"]), shot.duration_s)
                license_rows.append({"key": c["key"], "character_id": c["cast"]["character_uuid"],
                                     "identity_version_id": c["cast"]["identity_version_id"],
                                     "owner_id": pr["owner_id"], "grant_id": pr["grant_id"],
                                     "license_credits": pr["license_credits"], "seconds": shot.duration_s})
            credits_ += sum(x["license_credits"] for x in license_rows)
        rows.append({"key": shot.key, "duration_s": shot.duration_s, "hash": h, "status": state,
                     "credits": credits_ if state == "new" else 0, "full_credits": credits_,
                     "est_cost_usd": usd if state == "new" else 0.0, "capabilities": sorted(needed_capabilities(inp)),
                     "lock_mode": lock, "license": license_rows,
                     "license_credits": sum(x["license_credits"] for x in license_rows)})
        if state == "new":
            new_credits += credits_
            new_usd = new_usd + usd if (usd is not None and new_usd is not None) else None
    assemble = int(cfg["assemble_credits"])
    total = new_credits + assemble
    limitations = list(sb.limitations)
    if sb.audio.voice == "tts":
        limitations.append("Speech synthesis is not enabled yet: dialogue is shown as captions.")
    if any(r["lock_mode"] for r in rows):
        limitations.append("Character identity is measured per shot, not guaranteed. Strict lock retries a failed "
                           "shot within a fixed budget (failed attempts are refunded), then fails explicitly.")
    if missing:
        limitations.append(f"The current video provider does not support: {', '.join(sorted(missing))}.")
    if provider is None:
        limitations.append("No video provider currently meets the quality, health and capability requirements.")
    return {"provider": provider, "fallback_provider": route["fallback"], "routing": route["reason"],
            "routing_policy": route.get("policy"),
            "shots": rows,
            "new_shots": sum(1 for r in rows if r["status"] == "new"),
            "reused_shots": sum(1 for r in rows if r["status"] != "new"),
            "credits": total, "assemble_credits": assemble,
            "est_cost_usd": round(new_usd, 4) if new_usd is not None else None,
            "missing_capabilities": sorted(missing), "blocked": blocked, "missing_sources": parents,
            "limitations": limitations, "budget_credits": project.budget_credits,
            "within_budget": project.budget_credits is None or total <= project.budget_credits,
            "balance": credits.available(db, user.id)}


# ---------------------------------------------------------------- render + orchestration

def _job_key(prefix: str, *parts: str) -> str:
    return f"{prefix}:{hashlib.sha256(':'.join(parts).encode()).hexdigest()[:48]}"


def render(db: Session, user: User, project: StudioProject, version_id: uuid.UUID | None,
           confirmed_credits: int | None, idempotency_key: str, only_keys: list[str] | None = None,
           purpose: str = "full") -> dict:
    """purpose=full renders every new shot and assembles; purpose=pilot (V7 preview-first) renders only
    `only_keys` — the renders are content-hashed, so the full render later reuses them at no cost."""
    cfg = require_enabled(db)
    v = get_version(db, project, version_id)
    sb0 = Storyboard.model_validate(v.storyboard)
    keys = [_job_key("st", idempotency_key, shot_hash(_shot_inputs(db, sb0, s, ""))) for s in sb0.shots()
            if only_keys is None or s.key in only_keys]
    replay = db.execute(select(GenerationJob).where(GenerationJob.user_id == user.id,
                                                    GenerationJob.idempotency_key.in_(keys))).scalars().all()
    if replay:  # same Idempotency-Key retried: return what that request created, charge nothing new
        return {"project_id": str(project.id), "version_id": str(v.id), "credits": sum(j.credit_cost for j in replay),
                "jobs": [str(j.id) for j in replay], "status": project.status, "replay": True}
    est = estimate(db, user, project, v)
    if only_keys is not None:
        est = subset_estimate(est, only_keys)
    if est["blocked"]:
        if all(b["reason"] == "consent_missing" for b in est["blocked"]):
            raise ApiError(409, "consent_required", "a character's likeness consent is missing or revoked",
                           {"blocked": est["blocked"]})
        raise ApiError(409, "cast_unavailable", "a cast character can't be used right now", {"blocked": est["blocked"]})
    if est["missing_sources"]:
        raise ApiError(409, "source_not_rendered", "trimmed or extended shots need their source shot rendered first",
                       {"shots": est["missing_sources"]})
    if est["provider"] is None:
        raise ApiError(503, "no_eligible_provider", "no video provider is available for this storyboard right now")
    if est["missing_capabilities"]:
        raise ApiError(422, "capability_unsupported", "the video provider can't do what this storyboard needs",
                       {"missing": est["missing_capabilities"]})
    if est["new_shots"] and est["est_cost_usd"] is None:
        raise ApiError(422, "pricing_not_configured", "this video provider has no configured price")
    if not est["within_budget"]:
        raise ApiError(422, "project_budget_exceeded", "this render is above the project budget",
                       {"credits": est["credits"], "budget": project.budget_credits})
    if any((r["est_cost_usd"] or 0) > float(cfg["max_job_cost_usd"]) for r in est["shots"]) or \
            (est["est_cost_usd"] or 0) > float(cfg["max_project_cost_usd"]):
        raise ApiError(422, "cost_ceiling_exceeded", "this render is too expensive right now")
    if confirmed_credits != est["credits"]:
        raise ApiError(409, "confirmation_required", "confirm the price before rendering", {"credits": est["credits"]})

    sb = Storyboard.model_validate(v.storyboard)
    provider, fallback = est["provider"], est["fallback_provider"]
    s = get_settings()
    qc = "paid_high" if user.plan == "pro" else "free"
    by_key = {r["key"]: r for r in est["shots"]}
    created = []
    for shot in sb.shots():
        row = by_key.get(shot.key)
        if row is None or row["status"] != "new":
            continue
        inp = _shot_inputs(db, sb, shot, provider)
        key = _job_key("st", idempotency_key, row["hash"])
        existing = db.execute(select(GenerationJob).where(GenerationJob.user_id == user.id,
                                                          GenerationJob.idempotency_key == key)).scalar_one_or_none()
        if existing is not None:
            continue
        spec = {"shot": shot.model_dump(mode="json"), "inputs": inp, "content_hash": row["hash"],
                "project_version_id": str(v.id), "resolution": RESOLUTIONS[sb.aspect_ratio],
                "creative_mode": sb.creative_mode or "manual", "prompt_strategy": sb.prompt_strategy or "v0",
                "seed": sb.seed, "routing": est.get("routing_policy")}
        if row["lock_mode"]:  # V6.5: identity gate per cast member; strict = bounded retry budget
            from . import characters

            _, ccfg = characters.config(db)
            spec["identity_gate"] = {"lock_mode": row["lock_mode"], "attempt": 0,
                                     "budget": int(ccfg["lock"]["strict_retry_budget"]) if row["lock_mode"] == "strict"
                                     else 0,
                                     "members": {c["key"]: c["cast"]["lock_mode"] for c in inp["characters"]
                                                 if c.get("cast")}}
            spec["character_usage"] = row["license"]
        if shot.derive is not None:  # extension: the worker needs the source render's boundary frames
            src = db.execute(select(StudioShotRender).where(StudioShotRender.project_id == project.id,
                                                            StudioShotRender.content_hash == shot.derive.from_hash)
                             ).scalar_one()
            spec["extend"] = {"direction": shot.derive.direction, "source_video_key": src.video_key}
        job = GenerationJob(
            id=uuid.uuid4(), user_id=user.id, kind=JobKind.studio_shot, status=JobStatus.queued, queue_class=qc,
            studio_project_id=project.id, preferred_model=provider, fallback_model=fallback,
            idempotency_key=key, credit_cost=row["credits"], max_attempts=s.job_max_attempts,
            watermark=False, est_cost_usd=row["est_cost_usd"], spec=spec)
        db.add(job)
        db.flush()
        r = db.execute(select(StudioShotRender).where(StudioShotRender.project_id == project.id,
                                                      StudioShotRender.content_hash == row["hash"])
                       ).scalar_one_or_none()
        if r is None:
            r = StudioShotRender(project_id=project.id, content_hash=row["hash"], shot_key=shot.key)
            db.add(r)
        r.status, r.job_id, r.video_key = "queued", job.id, None
        credits.reserve(db, job)  # 402 rolls back the whole render: all or nothing
        if spec.get("character_usage"):
            from . import character_market

            character_market.reserve_usage(db, job, spec["character_usage"])
        created.append(job)
    if purpose == "full":
        project.rendered_version_id = v.id
        project.status = "rendering"
        db.flush()
        maybe_assemble(db, project)
    db.flush()
    return {"project_id": str(project.id), "version_id": str(v.id), "credits": est["credits"],
            "jobs": [str(j.id) for j in created], "status": project.status, "purpose": purpose}


def subset_estimate(est: dict, only_keys: list[str]) -> dict:
    """The estimate restricted to some shots (pilot sequence): no assembly charge, same per-shot prices."""
    rows = [r for r in est["shots"] if r["key"] in only_keys]
    if len(rows) != len(set(only_keys)):
        raise ApiError(422, "unknown_shots", "some shots are not in this version")
    new = [r for r in rows if r["status"] == "new"]
    usd = [r["est_cost_usd"] for r in new]
    return {**est, "shots": rows, "credits": sum(r["credits"] for r in new), "new_shots": len(new),
            "reused_shots": len(rows) - len(new), "assemble_credits": 0,
            "est_cost_usd": round(sum(usd), 4) if all(u is not None for u in usd) else None,
            "blocked": [b for b in est["blocked"] if b["shot"] in only_keys],
            "missing_sources": [m for m in est["missing_sources"] if m["shot"] in only_keys],
            "within_budget": True}


def _version_renders(db: Session, project: StudioProject, v: StudioProjectVersion
                     ) -> list[tuple[Shot, StudioShotRender | None]]:
    """(shot, render that provides its pixels). Trims resolve to their source render."""
    sb = Storyboard.model_validate(v.storyboard)
    renders = {r.content_hash: r for r in db.execute(select(StudioShotRender).where(
        StudioShotRender.project_id == project.id)).scalars()}
    out = []
    for s in sb.shots():
        if s.derive is not None and s.derive.kind == "trim":
            out.append((s, renders.get(s.derive.from_hash)))
        else:
            out.append((s, renders.get(shot_hash(_shot_inputs(db, sb, s, "")))))
    return out


def shot_hashes(db: Session, storyboard: dict) -> dict[str, str]:
    sb = Storyboard.model_validate(storyboard)
    return {s.key: shot_hash(_shot_inputs(db, sb, s, "")) for s in sb.shots()}


def maybe_assemble(db: Session, project: StudioProject) -> GenerationJob | None:
    """When every shot of the target version is ready, queue the assembly job (once per version)."""
    if project.rendered_version_id is None:
        return None
    v = db.get(StudioProjectVersion, project.rendered_version_id)
    pairs = _version_renders(db, project, v)
    if any(r is None or r.status != "ready" for _, r in pairs):
        return None
    _, cfg = config(db)
    key = _job_key("sa", str(v.id))
    job = db.execute(select(GenerationJob).where(GenerationJob.user_id == project.user_id,
                                                 GenerationJob.idempotency_key == key)).scalar_one_or_none()
    if job is not None:
        return job
    sb = Storyboard.model_validate(v.storyboard)
    user = db.get(User, project.user_id)
    timeline, captions, t = [], [], 0
    for shot, r in pairs:
        trim = shot.derive.start_s if shot.derive is not None and shot.derive.kind == "trim" else 0
        timeline.append({"shot_key": shot.key, "video_key": r.video_key, "duration_ms": shot.duration_s * 1000,
                         "transition": shot.transition, "start_ms": t, "trim_start_ms": trim * 1000})
        lines = [(d.start_s, d.text) for d in shot.dialogue] or ([(0.0, shot.caption)] if shot.caption else [])
        for i, (start, text) in enumerate(lines):
            end = lines[i + 1][0] if i + 1 < len(lines) else shot.duration_s
            captions.append({"start_ms": t + int(start * 1000), "end_ms": t + int(end * 1000), "text": text})
        t += shot.duration_s * 1000
    credits_line = []
    for c in sb.characters:
        ch = db.get(StudioCharacter, c.character_id) if c.character_id else None
        if ch is not None and ch.actor_license_id is not None:
            from ..models import ActorLicense, ActorListing

            lic = db.get(ActorLicense, ch.actor_license_id)
            if lic is not None and lic.terms_snapshot.get("attribution_required"):
                credits_line.append(db.get(ActorListing, lic.listing_id).display_name)
    for c in sb.characters:
        if c.cast_member_id is None:
            continue
        from ..models import Character, CharacterLicenseGrant, ProjectCastMember

        m = db.get(ProjectCastMember, c.cast_member_id)
        g = db.get(CharacterLicenseGrant, m.grant_id) if m is not None and m.grant_id else None
        if g is not None and g.terms_snapshot.get("attribution_required"):
            from . import characters as _chars

            credits_line.append(_chars.public_handle(db, db.get(Character, m.character_id)))
    final_captions = captions if sb.captions.enabled else []
    if credits_line and t >= 2000:  # licence terms: credit the licensed AI actor (even with captions off)
        final_captions = final_captions + [{"start_ms": t - 2000, "end_ms": t,
                                            "text": "Licensed AI actor: " + ", ".join(sorted(set(credits_line)))}]
    job = GenerationJob(
        id=uuid.uuid4(), user_id=project.user_id, kind=JobKind.studio_assemble, status=JobStatus.queued,
        queue_class="paid_high" if user.plan == "pro" else "free", studio_project_id=project.id,
        preferred_model="studio_assembler", idempotency_key=key, credit_cost=int(cfg["assemble_credits"]),
        max_attempts=get_settings().job_max_attempts, watermark=user.plan != "pro", est_cost_usd=0.0,
        spec={"project_version_id": str(v.id), "timeline": timeline,
              "captions": final_captions, "burn_in": sb.captions.burn_in,
              "audio": sb.audio.model_dump(mode="json"), "resolution": RESOLUTIONS[sb.aspect_ratio],
              "title": sb.title})
    db.add(job)
    db.flush()
    credits.reserve(db, job)
    return job


def assemble_preview(db: Session, user: User, project: StudioProject, v: StudioProjectVersion, purpose: str,
                     only_keys: list[str] | None = None) -> GenerationJob:
    """V7 preview-first. `animatic`: free storyboard preview (title cards with the scene/shot text and exact
    subtitles, no AI generation; labelled preview quality). `pilot`: assembles the selected rendered shots
    (30-60 s pilot sequence). Neither changes the project's final output."""
    sb = Storyboard.model_validate(v.storyboard)
    shots = [s for s in sb.shots() if only_keys is None or s.key in only_keys]
    if not shots:
        raise ApiError(422, "unknown_shots", "no shots selected")
    renders = {}
    if purpose == "pilot":
        pairs = {s.key: r for s, r in _version_renders(db, project, v)}
        missing = [s.key for s in shots if pairs.get(s.key) is None or pairs[s.key].status != "ready"]
        if missing:
            raise ApiError(409, "shots_not_ready", "render the pilot shots first", {"shots": missing})
        renders = pairs
    scenes = sb.scene_of()
    names = {c.key: c.name for c in sb.characters}
    timeline, captions, t = [], [], 0
    for shot in shots:
        item = {"shot_key": shot.key, "duration_ms": shot.duration_s * 1000, "transition": shot.transition,
                "start_ms": t, "trim_start_ms": 0}
        if purpose == "pilot":
            r = renders[shot.key]
            trim = shot.derive.start_s if shot.derive is not None and shot.derive.kind == "trim" else 0
            item.update(video_key=r.video_key, trim_start_ms=trim * 1000)
        else:
            sc = scenes[shot.key]
            item["card"] = {"title": " · ".join(x for x in [sc.title, sc.location, sc.time_of_day] if x)[:120],
                            "text": shot.prompt[:400], "camera": shot.camera[:120]}
        timeline.append(item)
        lines = [(d.start_s, (f"{names.get(d.character, d.character)}: " if d.character else "") + d.text)
                 for d in shot.dialogue] or ([(0.0, shot.caption)] if shot.caption else [])
        for i, (start, text) in enumerate(lines):
            end = lines[i + 1][0] if i + 1 < len(lines) else shot.duration_s
            captions.append({"start_ms": t + int(start * 1000), "end_ms": t + int(end * 1000), "text": text})
        t += shot.duration_s * 1000
    key = _job_key("sp", purpose, str(v.id), ",".join(s.key for s in shots))
    job = db.execute(select(GenerationJob).where(GenerationJob.user_id == project.user_id,
                                                 GenerationJob.idempotency_key == key)).scalar_one_or_none()
    if job is not None and job.status not in (JobStatus.failed, JobStatus.cancelled):
        return job
    if job is not None:
        key = _job_key("sp", purpose, str(v.id), ",".join(s.key for s in shots), uuid.uuid4().hex)
    job = GenerationJob(
        id=uuid.uuid4(), user_id=project.user_id, kind=JobKind.studio_assemble, status=JobStatus.queued,
        queue_class="paid_high" if user.plan == "pro" else "free", studio_project_id=project.id,
        preferred_model="studio_assembler", idempotency_key=key, credit_cost=0,
        max_attempts=get_settings().job_max_attempts, watermark=True, est_cost_usd=0.0,
        spec={"project_version_id": str(v.id), "timeline": timeline, "captions": captions, "burn_in": True,
              "audio": sb.audio.model_dump(mode="json") if purpose == "pilot" else {"music_asset_id": None},
              "resolution": RESOLUTIONS[sb.aspect_ratio], "title": sb.title, "purpose": purpose,
              "label": "preview quality (storyboard cards, not AI video)" if purpose == "animatic"
              else "pilot sequence"})
    db.add(job)
    db.flush()
    return job


def on_job_completed(db: Session, job: GenerationJob, video_key: str, duration_ms: int) -> None:
    project = db.get(StudioProject, job.studio_project_id)
    if project is None:
        return
    if job.kind == JobKind.studio_shot:
        r = db.execute(select(StudioShotRender).where(StudioShotRender.project_id == project.id,
                                                      StudioShotRender.content_hash == job.spec["content_hash"])
                       ).scalar_one_or_none()
        if r is not None and r.job_id == job.id:
            r.status, r.video_key, r.duration_ms = "ready", video_key, duration_ms
            db.flush()
        maybe_assemble(db, project)
    elif job.kind == JobKind.studio_assemble and job.spec.get("purpose") in ("pilot", "animatic"):
        pass  # previews never replace the project's final output
    elif job.kind == JobKind.studio_assemble and job.spec.get("project_version_id") == str(project.rendered_version_id):
        project.status, project.output_job_id = "ready", job.id
    if project.production_episode_id is not None:  # V7: production progress, events, budget tracking
        from . import productions

        productions.on_episode_job(db, project, job, "completed")


def identity_gate(db: Session, job: GenerationJob, metrics: dict) -> str | None:
    """V6.5: record a measured identity report per cast member of a finished shot; strict lock fails the shot when
    a member is below threshold or could not be measured. Proxy passes are 'measured', never 'verified'."""
    from ..models import CharacterQualityReport
    from . import characters

    gate = job.spec.get("identity_gate")
    if not gate:
        return None
    _, ccfg = characters.config(db)
    th = ccfg["qc"]
    ident = ((metrics or {}).get("qa") or {}).get("identity") or {}
    failed = []
    for c in job.spec["inputs"]["characters"]:
        if not c.get("cast"):
            continue
        m = ident.get(c["key"]) if isinstance(ident.get(c["key"]), dict) else {}
        method, score = str(m.get("method") or "none"), m.get("score")
        thr = th["face_similarity_min"] if method == "face_embedding" else th["proxy_similarity_min"]
        if method == "none" or not isinstance(score, (int, float)):
            verdict = "insufficient"
        else:
            verdict = "pass" if score >= thr else "fail"
        db.add(CharacterQualityReport(
            character_id=uuid.UUID(c["cast"]["character_uuid"]),
            identity_version_id=uuid.UUID(c["cast"]["identity_version_id"]), scope="shot", job_id=job.id,
            method=method, verdict=verdict, thresholds={"min": thr},
            metrics={"shot_key": job.spec["shot"]["key"], "score": score, "lock_mode": c["cast"]["lock_mode"],
                     "attempt": gate.get("attempt", 0),
                     **{k: v for k, v in m.items() if k in ("frames", "detected_ratio", "min", "max")}}))
        if c["cast"]["lock_mode"] == "strict" and verdict != "pass":
            failed.append(c["key"])
    db.flush()
    return "identity_check_failed" if failed else None


def on_identity_failed(db: Session, job: GenerationJob) -> GenerationJob | None:
    """Strict lock: retry within the budget (a new reserved job; the failed attempt was refunded), otherwise the
    shot fails explicitly."""
    gate = dict(job.spec.get("identity_gate") or {})
    project = db.get(StudioProject, job.studio_project_id)
    r = db.execute(select(StudioShotRender).where(StudioShotRender.project_id == job.studio_project_id,
                                                  StudioShotRender.content_hash == job.spec["content_hash"])
                   ).scalar_one_or_none()
    if gate.get("attempt", 0) < gate.get("budget", 0):
        gate["attempt"] = gate.get("attempt", 0) + 1
        spec = {**job.spec, "identity_gate": gate}
        retry = GenerationJob(
            id=uuid.uuid4(), user_id=job.user_id, kind=JobKind.studio_shot, status=JobStatus.queued,
            queue_class=job.queue_class, studio_project_id=job.studio_project_id,
            preferred_model=job.preferred_model, fallback_model=job.fallback_model,
            idempotency_key=f"{job.idempotency_key[:70]}:r{gate['attempt']}", credit_cost=job.credit_cost,
            max_attempts=job.max_attempts, watermark=False, est_cost_usd=job.est_cost_usd, spec=spec)
        try:
            with db.begin_nested():
                db.add(retry)
                db.flush()
                credits.reserve(db, retry)
                if spec.get("character_usage"):
                    from . import character_market

                    character_market.reserve_usage(db, retry, spec["character_usage"])
        except ApiError:
            retry = None  # cannot pay for the retry: explicit failure below
        if retry is not None:
            if r is not None and r.job_id == job.id:
                r.status, r.job_id = "queued", retry.id
            return retry
    if r is not None and r.job_id == job.id:
        r.status = "identity_failed"
    if project is not None:
        project.status = "failed"
    return None


def shot_identity(db: Session, project: StudioProject) -> list[dict]:
    from ..models import CharacterQualityReport

    jobs = {j.id: j for j in db.execute(select(GenerationJob).where(
        GenerationJob.studio_project_id == project.id, GenerationJob.kind == JobKind.studio_shot)).scalars()}
    if not jobs:
        return []
    rows = db.execute(select(CharacterQualityReport).where(CharacterQualityReport.job_id.in_(list(jobs)))
                      .order_by(CharacterQualityReport.created_at)).scalars().all()
    return [{"job_id": str(r.job_id), "shot_key": r.metrics.get("shot_key"), "character_id": str(r.character_id),
             "identity_version_id": str(r.identity_version_id), "method": r.method, "verdict": r.verdict,
             "score": r.metrics.get("score"), "threshold": r.thresholds.get("min"),
             "lock_mode": r.metrics.get("lock_mode"), "attempt": r.metrics.get("attempt"),
             "label": "measured (proxy)" if r.method == "proxy" and r.verdict == "pass" else
             ("measured" if r.verdict == "pass" else r.verdict)} for r in rows]


def on_job_failed(db: Session, job: GenerationJob) -> None:
    project = db.get(StudioProject, job.studio_project_id)
    if project is None:
        return
    if job.kind == JobKind.studio_shot:
        r = db.execute(select(StudioShotRender).where(StudioShotRender.project_id == project.id,
                                                      StudioShotRender.content_hash == job.spec["content_hash"])
                       ).scalar_one_or_none()
        if r is not None and r.job_id == job.id:
            r.status = "failed"
    if job.spec.get("purpose") not in ("pilot", "animatic"):
        project.status = "failed"  # partial: re-rendering only charges the failed shots
    if project.production_episode_id is not None:
        from . import productions

        productions.on_episode_job(db, project, job, "failed")


# ---------------------------------------------------------------- worker payloads

class DispatchRefused(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def build_payload(db: Session, job: GenerationJob) -> dict:
    from ..models import AssetStatus, IdentityAsset
    from .generation import output_key
    from .storage import get_storage

    st = get_storage()
    base = {"job_id": str(job.id), "attempt": job.attempts, "model": job.model_used, "kind": job.kind.value,
            "watermark": job.watermark, "lease_s": get_settings().job_lease_s, "spec": job.spec,
            "upload": {"video": {"key": output_key(job, "video.mp4"),
                                 "url": st.presign_put(output_key(job, "video.mp4"), "video/mp4")},
                       "thumbnail": {"key": output_key(job, "thumb.jpg"),
                                     "url": st.presign_put(output_key(job, "thumb.jpg"), "image/jpeg")}}}
    if job.kind == JobKind.studio_shot:
        refs = {}
        for c in job.spec["inputs"]["characters"]:
            if c.get("cast"):  # V6: re-check licence/consent/suspension at dispatch; send identity references
                from ..models import CharacterIdentityVersion
                from . import casting, characters

                problem = casting.cast_problem(db, job.user_id, job.studio_project_id,
                                               uuid.UUID(c["cast"]["member_id"]))
                if problem:
                    raise DispatchRefused(problem)
                _, ccfg = characters.config(db)
                v = db.get(CharacterIdentityVersion, uuid.UUID(c["cast"]["identity_version_id"]))
                # rendering access is not asset access: only the worker gets short-lived URLs
                refs[c["key"]] = [st.presign_get(k, 3600) for k in
                                  characters.reference_keys(v, int(ccfg["lock"]["max_reference_images"]))]
                continue
            if not c.get("character_id"):
                continue
            ch = db.get(StudioCharacter, uuid.UUID(c["character_id"]))
            if ch is not None and ch.actor_license_id is not None:
                from . import actors

                reason = actors.license_usable(db, ch.actor_license_id, ch.user_id)
                actors.log_usage(db, ch.actor_license_id, job.id, "dispatch", reason)
                if reason:
                    raise DispatchRefused("license_invalid")
            if ch is None or not character_usable(db, ch):
                raise DispatchRefused("consent_revoked")  # checked again at dispatch (spec V4 §8)
            if ch.identity_profile_id:
                assets = db.execute(select(IdentityAsset).where(
                    IdentityAsset.profile_id == ch.identity_profile_id, IdentityAsset.status == AssetStatus.accepted,
                    IdentityAsset.kind == "photo").order_by(IdentityAsset.created_at).limit(3)).scalars().all()
                refs[c["key"]] = [st.presign_get(a.storage_key, 3600) for a in assets]
        base["reference_images"] = refs
        ext = job.spec.get("extend")
        if ext:
            base["boundary_video_url"] = st.presign_get(ext["source_video_key"], 3600)
        return base
    v = db.get(StudioProjectVersion, uuid.UUID(job.spec["project_version_id"]))
    for c in Storyboard.model_validate(v.storyboard).characters if v is not None else []:
        ch = db.get(StudioCharacter, c.character_id) if c.character_id else None
        if ch is not None and ch.actor_license_id is not None:  # licence re-checked at publication
            from . import actors

            reason = actors.license_usable(db, ch.actor_license_id, ch.user_id)
            actors.log_usage(db, ch.actor_license_id, job.id, "publish", reason)
            if reason:
                raise DispatchRefused("license_invalid")
    base["shots"] = [{**s, "url": st.presign_get(s["video_key"], 3600) if s.get("video_key") else None}
                     for s in job.spec["timeline"]]
    music_id = (job.spec.get("audio") or {}).get("music_asset_id")
    if music_id:
        a = db.get(AudioAsset, uuid.UUID(music_id))
        if a is None or a.deleted_at is not None or a.status != "ready":
            raise DispatchRefused("audio_unavailable")
        base["music_url"] = st.presign_get(a.storage_key, 3600)
    base["upload"]["captions"] = {"key": output_key(job, "captions.vtt"),
                                  "url": st.presign_put(output_key(job, "captions.vtt"), "text/vtt")}
    return base


def project_out(db: Session, project: StudioProject) -> dict:
    from ..models import GenerationOutput
    from .storage import get_storage

    v = db.get(StudioProjectVersion, project.current_version_id) if project.current_version_id else None
    out = None
    if project.output_job_id:
        o = db.execute(select(GenerationOutput).where(GenerationOutput.job_id == project.output_job_id)
                       ).scalar_one_or_none()
        if o is not None and o.deleted_at is None:
            from .generation import output_key

            st = get_storage()
            job = db.get(GenerationJob, project.output_job_id)
            cap_key = output_key(job, "captions.vtt")
            out = {"video_url": st.presign_get(o.video_key), "duration_ms": o.duration_ms,
                   "captions_url": st.presign_get(cap_key) if st.head(cap_key) else None,
                   "watermarked": o.watermarked, "version_id": str(job.spec.get("project_version_id"))}
    shots = []
    if project.rendered_version_id:
        rv = db.get(StudioProjectVersion, project.rendered_version_id)
        shots = [{"key": s.key, "status": r.status if r else "new"} for s, r in _version_renders(db, project, rv)]
    return {"id": str(project.id), "title": project.title, "status": project.status,
            "aspect_ratio": project.aspect_ratio, "language": project.language,
            "budget_credits": project.budget_credits,
            "current_version": {"id": str(v.id), "version": v.version, "source": v.source,
                                "storyboard": v.storyboard, "director": v.director} if v else None,
            "rendered_version_id": str(project.rendered_version_id) if project.rendered_version_id else None,
            "shots": shots, "output": out}
