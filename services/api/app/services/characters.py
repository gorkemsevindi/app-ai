"""Creator Character Identity (Master Spec V6, §V6.1–V6.7).

A character is a persistent digital actor with an immutable UUID and a creator-scoped handle
(`@<creator>/<handle>`). Its *core identity* lives in versioned, lockable identity versions; *performance*
(wardrobe variants, expression, scene, dialogue) varies without touching it.

Flow (V6.2): create (description + structured controls → CharacterSpec, original prompt kept; rights
declaration) → seed previews (reroll allowed) → approve master → identity-conditioned multi-view build
→ QC (measured metrics + creator review per asset; repair regenerates only failed/rejected assets)
→ validation report → lock (identity package with checksums) → cast into Studio projects.

Honesty rules:
- Identity consistency is *measured*, never guaranteed. With a face embedder (SFace) on the worker the method is
  `face_embedding`; otherwise only `proxy` metrics exist and nothing is ever labelled verified.
- Side/rear/body views are flagged as views where face recognition is not meaningful.
- Images are generated per asset as queue jobs: reserved before dispatch, settled once, refunded on failure.
- Synthetic actors are the default; a real-person reference is only the creator's own consented identity profile.
- Characters must appear adult; minors, real-person impersonation and third-party IP are declared against and
  screened (keyword screen = conservative first line, human review via reports/takedown)."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import ApiError, not_found
from ..models import (
    Character,
    CharacterAsset,
    CharacterIdentityVersion,
    CharacterListing,
    CharacterQualityReport,
    CharacterRightsClaim,
    CharacterVoiceProfile,
    ConsentReceipt,
    CreatorProfile,
    FeatureFlag,
    GenerationJob,
    IdentityProfile,
    JobKind,
    JobStatus,
    ProfileStatus,
    ProjectCastMember,
    User,
)
from . import credits, moderation

FLAG = "characters"
SPEC_SCHEMA = "cs1"
PACKAGE_SCHEMA = "ipm1"
RIGHTS_TERMS = "character-rights-v1"
HANDLE_RE = r"^[a-z0-9_]{2,32}$"

# V6.3 multi-angle sheet. face=True: face recognition is meaningful for that view.
VIEWS: dict[str, dict] = {
    "front_neutral": {"face": True, "prompt": "front view, neutral expression, head and shoulders"},
    "yaw45_left": {"face": True, "prompt": "three-quarter view turned 45 degrees to the left"},
    "yaw45_right": {"face": True, "prompt": "three-quarter view turned 45 degrees to the right"},
    "profile_left": {"face": False, "prompt": "left profile, 90 degrees"},
    "profile_right": {"face": False, "prompt": "right profile, 90 degrees"},
    "rear": {"face": False, "prompt": "rear view, back of the head and shoulders"},
    "expr_smile": {"face": True, "prompt": "front view, warm smile"},
    "expr_anger": {"face": True, "prompt": "front view, angry expression"},
    "expr_sadness": {"face": True, "prompt": "front view, sad expression"},
    "mouth_aa": {"face": True, "prompt": "front view, speaking, open mouth shape 'aa'"},
    "mouth_oo": {"face": True, "prompt": "front view, speaking, rounded mouth shape 'oo'"},
    "mouth_mm": {"face": True, "prompt": "front view, speaking, closed lips shape 'mm'"},
    "closeup": {"face": True, "prompt": "extreme close-up of the face"},
    "fullbody": {"face": False, "prompt": "full body, standing, neutral pose, baseline wardrobe"},
    "pose_walking": {"face": False, "prompt": "full body, walking"},
    "light_warm": {"face": True, "prompt": "front view, warm golden-hour lighting"},
    "light_cool": {"face": True, "prompt": "front view, cool overcast lighting"},
}
IMMUTABLE_FIELDS = ("aesthetic", "age_appearance", "appearance")

DEFAULTS: dict = {
    "image_provider": "mock_image",          # worker adapter; production: a verified, licensed image provider
    "provider_usd_per_image": {"mock_image": 0.0},  # ops price real providers from current docs
    "credits_per_image": {"preview": 2, "view": 2},
    "max_previews": 6,
    "views": list(VIEWS),
    "image_resolution": "768x768",
    "max_characters_per_user": 50,
    "max_job_cost_usd": 1.0,
    "qc": {"face_similarity_min": 0.363, "proxy_similarity_min": 0.55, "lookalike_hamming_max": 4,
           "duplicate_spec_similarity": 0.9},
    "lock": {"strong_credit_multiplier": 1.5, "strict_retry_budget": 2, "max_reference_images": 3},
    "tts_voices": {},                        # {"provider": ["voice_id", ...]} licensed voices only
    "blocked_terms": [],                     # ops-maintained names/IP terms (real people, franchises)
}
# Conservative screens (first line only; reports + takedown + human review are the real control).
MINOR_RE = r"\b(child|kid|kids|teen|teenager|minor|schoolgirl|schoolboy|underage|baby|toddler|çocuk|cocuk|" \
           r"ergen|bebek|küçük kız|kucuk kiz|liseli|\d{1,2}\s*(yo|year[- ]old|yaşında|yasinda))\b"
LOOKALIKE_RE = r"\b(looks like|look like|lookalike|look-alike|doppelg[aä]nger|celebrity|famous|benzeyen|benzesin|" \
               r"ünlü|unlu)\b"


def _now() -> datetime:
    return datetime.now(UTC)


def config(db: Session) -> tuple[bool, dict]:
    f = db.get(FeatureFlag, FLAG)
    raw = (f.value or {}) if f else {}
    v = {**DEFAULTS, **raw}
    for k in ("qc", "lock", "credits_per_image", "provider_usd_per_image"):
        v[k] = {**DEFAULTS[k], **(raw.get(k) or {})}
    return bool(f and f.enabled), v


def require_enabled(db: Session) -> dict:
    enabled, cfg = config(db)
    if not enabled:
        raise ApiError(403, "feature_disabled", "character creation is not available yet")
    return cfg


# ---------------------------------------------------------------- CharacterSpec (cs1)

class Appearance(BaseModel):
    face: str = Field(default="", max_length=400)
    hair: str = Field(default="", max_length=200)
    eyes: str = Field(default="", max_length=120)
    skin: str = Field(default="", max_length=120)
    body: str = Field(default="", max_length=200)
    distinctive_features: str = Field(default="", max_length=300)


class Personality(BaseModel):
    """Fictional character bible — separate from identity; never derived from demographic categories."""

    archetype: str = Field(default="", max_length=80)
    traits: list[str] = Field(default_factory=list, max_length=12)
    speech_style: str = Field(default="", max_length=200)
    mannerisms: str = Field(default="", max_length=200)
    gait: str = Field(default="", max_length=120)
    expression_palette: list[str] = Field(default_factory=list, max_length=12)
    bio: str = Field(default="", max_length=1000)


class VoicePrefs(BaseModel):
    accent: str = Field(default="", max_length=60)
    timbre: str = Field(default="", max_length=60)
    pronunciation: str = Field(default="", max_length=200)


class Variant(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    description: str = Field(min_length=2, max_length=300)


class CharacterSpec(BaseModel):
    schema_: Literal["cs1"] = Field(default="cs1", alias="schema")
    display_name: str = Field(min_length=1, max_length=60)
    aesthetic: Literal["photorealistic", "stylized", "animation"] = "photorealistic"
    age_appearance: Literal["young_adult", "adult", "middle_aged", "senior"] = "adult"  # adults only
    appearance: Appearance = Field(default_factory=Appearance)
    wardrobe_baseline: str = Field(default="", max_length=300)
    personality: Personality = Field(default_factory=Personality)
    voice: VoicePrefs = Field(default_factory=VoicePrefs)
    languages: list[str] = Field(default_factory=lambda: ["en"], min_length=1, max_length=10)
    tags: list[str] = Field(default_factory=list, max_length=20)
    variants: list[Variant] = Field(default_factory=list, max_length=12)

    model_config = {"populate_by_name": True}

    def texts(self) -> list[str]:
        a, p = self.appearance, self.personality
        return [self.display_name, a.face, a.hair, a.eyes, a.skin, a.body, a.distinctive_features,
                self.wardrobe_baseline, p.archetype, p.speech_style, p.mannerisms, p.gait, p.bio,
                self.voice.accent, self.voice.timbre, self.voice.pronunciation, *p.traits, *p.expression_palette,
                *self.tags, *[v.description for v in self.variants]]

    def dump(self) -> dict:
        return self.model_dump(mode="json", by_alias=True)


def normalize_spec(description: str, structured: dict) -> CharacterSpec:
    """Structured controls win; the free description fills the face/identity text when no field is given.
    (Rule-based normalizer; the original prompt is stored separately and never rewritten.)"""
    data = json.loads(json.dumps(structured or {}))
    app = data.setdefault("appearance", {})
    if not any(str(v).strip() for v in app.values()):
        app["face"] = description[:400]
    data.setdefault("personality", {}).setdefault("bio", description[:1000])
    data["languages"] = sorted({str(x).lower()[:8] for x in data.get("languages") or ["en"]})
    try:
        return CharacterSpec.model_validate(data)
    except ValidationError as e:
        raise ApiError(422, "bad_character_spec", "the character specification is invalid",
                       {"errors": [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()[:10]]}) from e


def screen_texts(db: Session, texts: list[str]) -> None:
    _, cfg = config(db)
    blob = " ".join(t for t in texts if t)
    low = blob.lower()
    if re.search(MINOR_RE, low):  # specific reasons first, then the general moderation screen
        raise ApiError(422, "minor_not_allowed", "characters must be adults")
    if re.search(LOOKALIKE_RE, low):
        raise ApiError(422, "impersonation_not_allowed",
                       "describe an original character, not a real or famous person's likeness")
    d = moderation.check_text(blob)
    if not d.allowed:
        raise ApiError(422, "content_blocked", "this text is not allowed", {"category": d.category})
    for term in cfg["blocked_terms"]:
        if term and re.search(r"\b" + re.escape(str(term).lower()) + r"\b", low):
            raise ApiError(422, "rights_conflict", "this description names a protected person or work")


def view_prompt(spec: dict, view_key: str | None) -> str:
    s = CharacterSpec.model_validate(spec)
    a = s.appearance
    parts = [f"Original fictional {s.aesthetic} character, {s.age_appearance.replace('_', ' ')}.",
             a.face, a.hair and f"Hair: {a.hair}.", a.eyes and f"Eyes: {a.eyes}.", a.skin and f"Skin: {a.skin}.",
             a.body and f"Body: {a.body}.", a.distinctive_features and f"Distinctive: {a.distinctive_features}.",
             s.wardrobe_baseline and f"Wardrobe: {s.wardrobe_baseline}."]
    if view_key:
        parts.append(f"Same person as the reference image. {VIEWS[view_key]['prompt']}. Plain studio background.")
    else:
        parts.append("Canonical front-facing portrait, neutral expression, even lighting, plain background.")
    return " ".join(p.strip() for p in parts if p and p.strip())[:1800]


# ---------------------------------------------------------------- registry

def creator_handle(db: Session, user_id: uuid.UUID) -> str | None:
    p = db.get(CreatorProfile, user_id)
    return p.handle if p else None


def public_handle(db: Session, ch: Character) -> str:
    ch_handle = creator_handle(db, ch.creator_id)
    return f"@{ch_handle}/{ch.handle}" if ch_handle else f"@{ch.handle}"


def get_own(db: Session, user: User, character_id: uuid.UUID) -> Character:
    ch = db.get(Character, character_id)
    if ch is None or ch.creator_id != user.id or ch.deleted_at is not None:
        raise not_found("character")
    return ch


def current_version(db: Session, ch: Character) -> CharacterIdentityVersion:
    return db.get(CharacterIdentityVersion, ch.current_version_id)


class RightsIn(BaseModel):
    original_creation: bool = False
    no_real_person_likeness: bool = False  # synthetic: not based on any real person
    no_third_party_ip: bool = False
    adult_appearance: bool = False
    accept_terms: bool = False


def create(db: Session, user: User, handle: str, description: str, structured: dict, rights: dict,
           origin: str = "synthetic", identity_profile_id: uuid.UUID | None = None,
           attest_own_likeness: bool = False) -> Character:
    cfg = require_enabled(db)
    if not re.match(HANDLE_RE, handle or ""):
        raise ApiError(422, "bad_handle", "handle: 2-32 lowercase letters, digits or _")
    n = db.execute(select(func.count()).select_from(Character).where(Character.creator_id == user.id,
                                                                     Character.deleted_at.is_(None))).scalar_one()
    if n >= int(cfg["max_characters_per_user"]):
        raise ApiError(429, "character_limit", "character limit reached")
    r = RightsIn.model_validate(rights or {})
    if origin not in ("synthetic", "real_person"):
        raise ApiError(422, "bad_origin", "origin must be synthetic or real_person")
    need = [r.original_creation, r.no_third_party_ip, r.adult_appearance, r.accept_terms]
    if origin == "synthetic":
        need.append(r.no_real_person_likeness)
    if not all(need):
        raise ApiError(422, "rights_declaration_required",
                       "confirm the character is original, adult, free of third-party IP and accept the terms")
    spec = normalize_spec(description, structured)
    screen_texts(db, [description, handle, *spec.texts()])
    prof = None
    if origin == "real_person":  # only your own consented, verified likeness (never someone else's)
        prof = db.get(IdentityProfile, identity_profile_id) if identity_profile_id else None
        if prof is None or prof.user_id != user.id or prof.status != ProfileStatus.ready:
            raise not_found("identity profile")
        if not attest_own_likeness:
            raise ApiError(422, "consent_required", "confirm this is your own likeness")
    ch = Character(creator_id=user.id, handle=handle, display_name=spec.display_name, status="draft", origin=origin,
                   identity_profile_id=prof.id if prof else None)
    try:
        with db.begin_nested():
            db.add(ch)
            db.flush()
    except IntegrityError as e:
        raise ApiError(409, "handle_taken", "you already have a character with this handle") from e
    receipt = None
    if prof is not None:
        receipt = ConsentReceipt(user_id=user.id, subject_type="character", subject_id=ch.id,
                                 scope={"likeness": True, "voice": False, "identity_profile_id": str(prof.id)},
                                 terms_version=RIGHTS_TERMS,
                                 statement="I am the person in this identity profile and allow this character "
                                           "to use my likeness.")
        db.add(receipt)
        db.flush()
    db.add(CharacterRightsClaim(character_id=ch.id, user_id=user.id, origin=origin, declaration=r.model_dump(),
                                terms_version=RIGHTS_TERMS, consent_receipt_id=receipt.id if receipt else None))
    v = CharacterIdentityVersion(character_id=ch.id, major=1, minor=0, change_kind="initial",
                                 original_prompt=description, spec=spec.dump(), status="draft", created_by=user.id)
    db.add(v)
    db.flush()
    ch.current_version_id = v.id
    db.flush()
    return ch


def likeness_ok(db: Session, ch: Character) -> bool:
    """Real-person characters stay usable only while the owner's consent and profile are intact."""
    if ch.origin != "real_person":
        return True
    prof = db.get(IdentityProfile, ch.identity_profile_id) if ch.identity_profile_id else None
    if prof is None or prof.status != ProfileStatus.ready:
        return False
    return db.execute(select(ConsentReceipt.id).where(
        ConsentReceipt.subject_type == "character", ConsentReceipt.subject_id == ch.id,
        ConsentReceipt.revoked_at.is_(None))).first() is not None


def revoke_likeness(db: Session, user: User, ch: Character) -> int:
    rows = db.execute(select(ConsentReceipt).where(
        ConsentReceipt.subject_type == "character", ConsentReceipt.subject_id == ch.id,
        ConsentReceipt.revoked_at.is_(None))).scalars().all()
    for r in rows:
        r.revoked_at = _now()
    return len(rows)


# ---------------------------------------------------------------- image jobs (previews, views, repairs)

def image_quote(db: Session, kind: str, n: int) -> dict:
    cfg = require_enabled(db)
    provider = cfg["image_provider"]
    usd = cfg["provider_usd_per_image"].get(provider)
    per = int(cfg["credits_per_image"]["preview" if kind == "seed_preview" else "view"])
    return {"provider": provider, "images": n, "credits_per_image": per, "credits": per * n,
            "est_cost_usd": round(usd * n, 4) if usd is not None else None}


def _job_key(*parts: str) -> str:
    return "ch:" + hashlib.sha256(":".join(parts).encode()).hexdigest()[:48]


def _enqueue(db: Session, user: User, ch: Character, v: CharacterIdentityVersion, items: list[dict], kind: str,
             confirmed_credits: int | None, idem: str) -> dict:
    """items: [{view_key, references: [asset ids], seed?}] -> one asset + one reserved job each (all or nothing)."""
    cfg = require_enabled(db)
    keys = [_job_key(str(ch.id), idem, it["view_key"]) for it in items]
    replay = db.execute(select(GenerationJob).where(GenerationJob.user_id == user.id,
                                                    GenerationJob.idempotency_key.in_(keys))).scalars().all()
    if replay:
        return {"jobs": [str(j.id) for j in replay], "credits": sum(j.credit_cost for j in replay), "replay": True}
    q = image_quote(db, kind, len(items))
    if q["est_cost_usd"] is None:
        raise ApiError(422, "pricing_not_configured", "the image provider has no configured price")
    if q["images"] and q["est_cost_usd"] / q["images"] > float(cfg["max_job_cost_usd"]):
        raise ApiError(422, "cost_ceiling_exceeded", "image generation is too expensive right now")
    if confirmed_credits != q["credits"]:
        raise ApiError(409, "confirmation_required", "confirm the price first", {"credits": q["credits"], **q})
    from . import router as model_router

    if q["provider"] in model_router.disabled_models(db):
        raise ApiError(503, "provider_disabled", "the image provider is switched off")
    s = get_settings()
    qc = "paid_high" if user.plan == "pro" else "free"
    jobs = []
    for it, key in zip(items, keys, strict=True):
        a = CharacterAsset(character_id=ch.id, identity_version_id=v.id, kind=kind, view_key=it["view_key"],
                           status="pending", reference_asset_ids=[str(x) for x in it.get("references", [])],
                           seed=it.get("seed", secrets.randbelow(2**31 - 1)), provider=q["provider"])
        db.add(a)
        db.flush()
        spec = {"character_id": str(ch.id), "identity_version_id": str(v.id), "asset_id": str(a.id),
                "asset_kind": kind, "view_key": it["view_key"], "seed": a.seed,
                "prompt": view_prompt(v.spec, None if kind == "seed_preview" else it["view_key"]),
                "references": a.reference_asset_ids, "resolution": cfg["image_resolution"],
                "face_meaningful": kind == "seed_preview" or VIEWS.get(it["view_key"], {}).get("face", False),
                "creative_mode": "character", "prompt_strategy": SPEC_SCHEMA}
        job = GenerationJob(id=uuid.uuid4(), user_id=user.id, kind=JobKind.character_asset, status=JobStatus.queued,
                            queue_class=qc, preferred_model=q["provider"], idempotency_key=key,
                            credit_cost=q["credits_per_image"], max_attempts=s.job_max_attempts, watermark=False,
                            est_cost_usd=round(q["est_cost_usd"] / q["images"], 5) if q["images"] else 0.0,
                            spec=spec)
        db.add(job)
        db.flush()
        a.job_id = job.id
        credits.reserve(db, job)  # 402 rolls back everything
        jobs.append(job)
    return {"jobs": [str(j.id) for j in jobs], "credits": q["credits"], "assets": len(jobs)}


def start_previews(db: Session, user: User, ch: Character, count: int, confirmed_credits: int | None,
                   idem: str) -> dict:
    cfg = require_enabled(db)
    v = current_version(db, ch)
    if v.status != "draft":
        raise ApiError(409, "identity_approved", "the master identity is already approved for this version")
    if not 1 <= count <= int(cfg["max_previews"]):
        raise ApiError(422, "bad_count", f"1-{cfg['max_previews']} previews")
    if not likeness_ok(db, ch):
        raise ApiError(409, "consent_required", "the likeness consent is missing or revoked")
    # deterministic keys per request: replays map to the same jobs
    items = [{"view_key": f"preview_{hashlib.sha256(f'{idem}:{i}'.encode()).hexdigest()[:8]}"} for i in range(count)]
    return _enqueue(db, user, ch, v, items, "seed_preview", confirmed_credits, idem)


def approve_master(db: Session, user: User, ch: Character, asset_id: uuid.UUID) -> dict:
    require_enabled(db)
    v = current_version(db, ch)
    if v.status != "draft":
        raise ApiError(409, "identity_approved", "the master identity is already approved")
    a = db.get(CharacterAsset, asset_id)
    if a is None or a.identity_version_id != v.id or a.kind != "seed_preview" or a.status not in ("ready", "approved"):
        raise not_found("preview")
    v.master_asset_id, v.status = a.id, "master_approved"
    a.status, a.creator_review = "approved", "approved"
    flags = lookalike_check(db, ch, a)
    flags += diversity_check(db, ch, v)
    return {"identity_version_id": str(v.id), "master_asset_id": str(a.id), "flags": flags}


def lookalike_check(db: Session, ch: Character, master: CharacterAsset) -> list[dict]:
    """V6.8: near-identical master to another creator's public character -> flagged for review (perceptual hash)."""
    _, cfg = config(db)
    mine = master.qc.get("dhash")
    if not mine:
        return []
    others = db.execute(select(CharacterAsset, Character).join(
        CharacterIdentityVersion, CharacterIdentityVersion.master_asset_id == CharacterAsset.id).join(
        Character, Character.id == CharacterAsset.character_id).where(
        Character.creator_id != ch.creator_id, Character.status.in_(("public", "unlisted")))).all()
    hits = []
    for a, other in others:
        h = a.qc.get("dhash")
        if h and bin(int(h, 16) ^ int(mine, 16)).count("1") <= int(cfg["qc"]["lookalike_hamming_max"]):
            hits.append({"type": "lookalike", "character_id": str(other.id)})
    if hits:
        ch.moderation_status = "flagged"
        ch.moderation_note = "possible lookalike of another creator's character"
    return hits


def diversity_check(db: Session, ch: Character, v: CharacterIdentityVersion) -> list[dict]:
    """V6.12: new original characters should not converge on the same face/wardrobe/personality. Compares the
    spec text with the creator's own and public characters (lexical, V5 similarity) — a warning, not a block."""
    from . import creative

    _, cfg = config(db)
    mine = " ".join(CharacterSpec.model_validate(v.spec).texts())
    rows = db.execute(select(CharacterIdentityVersion.spec, Character.id).join(
        Character, Character.id == CharacterIdentityVersion.character_id).where(
        Character.id != ch.id, Character.deleted_at.is_(None),
        (Character.creator_id == ch.creator_id) | Character.status.in_(("public",)))).all()
    out = []
    for spec, cid in rows:
        s = creative.similarity(mine, " ".join(CharacterSpec.model_validate(spec).texts()))
        if s >= float(cfg["qc"]["duplicate_spec_similarity"]):
            out.append({"type": "near_duplicate_spec", "character_id": str(cid), "similarity": round(s, 3)})
    return out


def start_build(db: Session, user: User, ch: Character, confirmed_credits: int | None, idem: str) -> dict:
    cfg = require_enabled(db)
    v = current_version(db, ch)
    if v.status != "master_approved":
        raise ApiError(409, "master_required", "approve a master identity first")
    if db.execute(select(CharacterAsset.id).where(CharacterAsset.identity_version_id == v.id,
                                                  CharacterAsset.kind == "view")).first():
        raise ApiError(409, "already_built", "views exist; use repair for failed or rejected views")
    if not likeness_ok(db, ch):
        raise ApiError(409, "consent_required", "the likeness consent is missing or revoked")
    items = [{"view_key": k, "references": [v.master_asset_id]} for k in cfg["views"] if k in VIEWS]
    return _enqueue(db, user, ch, v, items, "view", confirmed_credits, idem)


def repair(db: Session, user: User, ch: Character, confirmed_credits: int | None, idem: str) -> dict:
    """Regenerate only failed / creator-rejected views (V6.3); approved and pending views are untouched."""
    require_enabled(db)
    v = current_version(db, ch)
    if v.status not in ("master_approved", "built"):
        raise ApiError(409, "not_repairable", "only an unlocked, built identity can be repaired")
    bad = [a for a in _live_views(db, v) if a.status in ("failed", "rejected")]
    if not bad:
        raise ApiError(409, "nothing_to_repair", "no failed or rejected views")
    out = _enqueue(db, user, ch, v, [{"view_key": a.view_key, "references": [v.master_asset_id]} for a in bad],
                   "view", confirmed_credits, idem)
    if not out.get("replay"):
        new = {a.view_key: a for a in db.execute(select(CharacterAsset).where(
            CharacterAsset.identity_version_id == v.id, CharacterAsset.kind == "view",
            CharacterAsset.status == "pending")).scalars()}
        for a in bad:
            if a.view_key in new:
                a.superseded_by = new[a.view_key].id
        v.status = "master_approved"
    return out


def _live_views(db: Session, v: CharacterIdentityVersion) -> list[CharacterAsset]:
    return db.execute(select(CharacterAsset).where(CharacterAsset.identity_version_id == v.id,
                                                   CharacterAsset.kind == "view",
                                                   CharacterAsset.superseded_by.is_(None))
                      .order_by(CharacterAsset.created_at)).scalars().all()


# ---------------------------------------------------------------- worker completion hooks

def on_asset_completed(db: Session, job: GenerationJob, output: dict) -> str | None:
    """Returns an error code if the image is missing (caller requeues/fails), else None."""
    from .storage import get_storage

    a = db.get(CharacterAsset, uuid.UUID(job.spec["asset_id"]))
    key = asset_key(job)
    head = get_storage().head(key)
    if a is None or head is None or head["size"] <= 0:
        return "output_missing"
    a.status, a.storage_key = "ready", key
    a.sha256 = str(output.get("sha256") or "")[:64] or None
    a.width, a.height = output.get("width"), output.get("height")
    a.model = (output.get("model") or job.model_used)
    a.params = {"prompt_strategy": SPEC_SCHEMA, "resolution": job.spec.get("resolution"),
                "mock": bool(output.get("mock"))}
    a.qc = {k: v for k, v in (output.get("qc") or {}).items() if isinstance(v, (int, float, str, bool, type(None)))}
    db.flush()
    _maybe_built(db, a)
    return None


def on_asset_failed(db: Session, job: GenerationJob) -> None:
    a = db.get(CharacterAsset, uuid.UUID(job.spec["asset_id"]))
    if a is not None and a.status == "pending":
        a.status = "failed"
        db.flush()
        _maybe_built(db, a)


def _maybe_built(db: Session, a: CharacterAsset) -> None:
    v = db.get(CharacterIdentityVersion, a.identity_version_id)
    if a.kind != "view" or v.status != "master_approved":
        return
    views = _live_views(db, v)
    if views and all(x.status != "pending" for x in views):
        v.status = "built"
        validate(db, db.get(Character, v.character_id), v)


def asset_key(job: GenerationJob) -> str:
    return f"characters/{job.spec['character_id']}/{job.spec['identity_version_id']}/{job.spec['asset_id']}.png"


def review_asset(db: Session, user: User, ch: Character, asset_id: uuid.UUID, decision: str) -> CharacterAsset:
    v = current_version(db, ch)
    a = db.get(CharacterAsset, asset_id)
    if a is None or a.identity_version_id != v.id or a.kind != "view" or a.superseded_by is not None:
        raise not_found("asset")
    if v.status == "locked":
        raise ApiError(409, "identity_locked", "a locked identity can't be changed; create a new version")
    if a.status not in ("ready", "approved", "rejected"):
        raise ApiError(409, "asset_not_ready", "this view is not ready")
    a.creator_review = decision
    a.status = "approved" if decision == "approved" else "rejected"
    return a


# ---------------------------------------------------------------- validation (IdentityValidationReport)

def validate(db: Session, ch: Character, v: CharacterIdentityVersion) -> CharacterQualityReport:
    _, cfg = config(db)
    th = cfg["qc"]
    views = _live_views(db, v)
    rows, failed, method_face, method_proxy = [], [], 0, 0
    for a in views:
        face_ok = VIEWS.get(a.view_key, {}).get("face", False)
        q = a.qc or {}
        row = {"view": a.view_key, "asset_id": str(a.id), "status": a.status, "creator_review": a.creator_review,
               "face_meaningful": face_ok}
        if a.status in ("failed", "rejected"):
            row["result"] = a.status
            failed.append(a.view_key)
        elif face_ok and q.get("face_similarity") is not None:
            method_face += 1
            row.update(metric="face_similarity", value=q["face_similarity"],
                       result="pass" if q["face_similarity"] >= th["face_similarity_min"] else "fail")
        elif q.get("proxy_similarity") is not None:
            method_proxy += 1
            row.update(metric="proxy_similarity", value=q["proxy_similarity"],
                       result="pass" if q["proxy_similarity"] >= th["proxy_similarity_min"] else "fail")
            if face_ok:
                row["note"] = "face embedder unavailable: proxy only"
        else:
            row["result"] = "not_measured"
        if not face_ok:
            row["note"] = "face recognition not meaningful for this view; body/appearance proxy only"
        if row.get("result") == "fail":
            failed.append(a.view_key)
        rows.append(row)
    required = [k for k in cfg["views"] if k in VIEWS]
    missing = sorted(set(required) - {a.view_key for a in views if a.status in ("ready", "approved")})
    reviewed = all(a.creator_review == "approved" for a in views) and bool(views)
    method = "face_embedding" if method_face else ("proxy" if method_proxy else "none")
    if failed or missing:
        verdict = "fail"
    elif method != "face_embedding" or not reviewed:
        verdict = "warn"  # never 'pass' on proxies alone or without the creator's human review
    else:
        verdict = "pass"
    rep = CharacterQualityReport(character_id=ch.id, identity_version_id=v.id, scope="build", method=method,
                                 metrics={"views": rows, "failed": sorted(set(failed)), "missing": missing,
                                          "creator_reviewed_all": reviewed,
                                          "note": "measured target, not a guarantee of identity"},
                                 thresholds=th, verdict=verdict)
    db.add(rep)
    db.flush()
    return rep


def latest_report(db: Session, v: CharacterIdentityVersion) -> CharacterQualityReport | None:
    return db.execute(select(CharacterQualityReport).where(
        CharacterQualityReport.identity_version_id == v.id, CharacterQualityReport.scope == "build")
        .order_by(CharacterQualityReport.created_at.desc())).scalars().first()


def report_out(r: CharacterQualityReport | None) -> dict | None:
    if r is None:
        return None
    return {"id": str(r.id), "verdict": r.verdict, "method": r.method, "metrics": r.metrics,
            "thresholds": r.thresholds, "verified": r.verdict == "pass" and r.method == "face_embedding",
            "created_at": r.created_at.isoformat() if r.created_at else None}


# ---------------------------------------------------------------- lock + package + versions

def lock(db: Session, user: User, ch: Character) -> CharacterIdentityVersion:
    require_enabled(db)
    v = current_version(db, ch)
    if v.status == "locked":
        return v
    if v.status != "built":
        raise ApiError(409, "identity_not_built", "build and review the multi-view identity first")
    rep = validate(db, ch, v)
    if rep.verdict == "fail":
        raise ApiError(409, "identity_validation_failed", "fix failed or missing views before locking",
                       {"report": report_out(rep)})
    if not rep.metrics["creator_reviewed_all"]:
        raise ApiError(409, "creator_review_required", "approve every view before locking (manual QC)")
    if not likeness_ok(db, ch):
        raise ApiError(409, "consent_required", "the likeness consent is missing or revoked")
    v.package = build_package(db, ch, v, rep)
    v.package_checksum = _checksum(v.package)
    v.status, v.locked_at = "locked", _now()
    ch.locked_version_id = v.id
    if ch.status == "draft":
        ch.status = "private"
    db.flush()
    return v


def _checksum(obj: dict) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def build_package(db: Session, ch: Character, v: CharacterIdentityVersion, rep: CharacterQualityReport) -> dict:
    """IdentityPackageManifest (internal). No biometric embeddings are stored or exported."""
    master = db.get(CharacterAsset, v.master_asset_id)
    views = [a for a in _live_views(db, v) if a.status == "approved"]
    claim = db.execute(select(CharacterRightsClaim).where(CharacterRightsClaim.character_id == ch.id)
                       .order_by(CharacterRightsClaim.created_at.desc())).scalars().first()
    voice = active_voice(db, ch)
    spec = CharacterSpec.model_validate(v.spec)

    def ref(a: CharacterAsset) -> dict:
        return {"asset_id": str(a.id), "view": a.view_key, "sha256": a.sha256, "storage_key": a.storage_key,
                "face_meaningful": VIEWS.get(a.view_key, {}).get("face", True), "qc": a.qc,
                "provenance": {"provider": a.provider, "model": a.model, "seed": a.seed,
                               "references": a.reference_asset_ids, "job_id": str(a.job_id) if a.job_id else None}}

    return {"schema": PACKAGE_SCHEMA, "character_uuid": str(ch.id),
            "identity_version": {"id": str(v.id), "major": v.major, "minor": v.minor},
            "canonical": {"portrait": ref(master),
                          "fullbody": next((ref(a) for a in views if a.view_key == "fullbody"), None)},
            "multiview": [ref(a) for a in views if not a.view_key.startswith(("expr_", "mouth_"))],
            "expression_sheet": [ref(a) for a in views if a.view_key.startswith(("expr_", "mouth_"))],
            "body_wardrobe_baseline": spec.wardrobe_baseline, "variants": [x.model_dump() for x in spec.variants],
            "voice_profile_id": str(voice.id) if voice else None, "pronunciation": spec.voice.pronunciation,
            "bio": spec.personality.bio,
            "rights": {"origin": ch.origin, "claim_id": str(claim.id) if claim else None,
                       "consent_receipt_id": str(claim.consent_receipt_id) if claim and claim.consent_receipt_id
                       else None},
            "embeddings": {"stored": False, "note": "biometric embeddings are computed transiently for QC only"},
            "validation_report_id": str(rep.id), "validation_verdict": rep.verdict, "validation_method": rep.method,
            "checksums": {str(a.id): a.sha256 for a in [master, *views]}}


def reference_keys(v: CharacterIdentityVersion, limit: int) -> list[str]:
    """Conditioning images for rendering: canonical portrait, full body, then 3/4 views."""
    p = v.package or {}
    order = [(p.get("canonical") or {}).get("portrait"), (p.get("canonical") or {}).get("fullbody")]
    order += [x for x in p.get("multiview", []) if x["view"] in ("yaw45_left", "yaw45_right", "front_neutral")]
    keys = [x["storage_key"] for x in order if x and x.get("storage_key")]
    return list(dict.fromkeys(keys))[:limit]


def new_version(db: Session, user: User, ch: Character, change: str, spec_patch: dict, description: str | None
                ) -> CharacterIdentityVersion:
    """minor = cosmetic (wardrobe/variants/personality/voice prefs): inherits the locked identity package.
    major = immutable traits change: a new draft identity that must go through master + build + lock again.
    Existing project casts keep the version they froze."""
    require_enabled(db)
    base = db.get(CharacterIdentityVersion, ch.locked_version_id) if ch.locked_version_id else None
    if base is None or current_version(db, ch).status != "locked":
        raise ApiError(409, "lock_required", "lock the current identity before creating a new version")
    merged = {**base.spec, **(spec_patch or {})}
    spec = normalize_spec(description or base.original_prompt, merged)
    screen_texts(db, [description or "", *spec.texts()])
    touched = [f for f in IMMUTABLE_FIELDS if json.dumps(spec.dump().get(f), sort_keys=True)
               != json.dumps(base.spec.get(f), sort_keys=True)]
    if change == "minor" and touched:
        raise ApiError(422, "immutable_trait_change",
                       "these traits define the identity: create a major version (or a new character)",
                       {"fields": touched})
    if change not in ("minor", "major"):
        raise ApiError(422, "bad_change", "change must be minor or major")
    if change == "minor":
        minor = db.execute(select(func.max(CharacterIdentityVersion.minor)).where(
            CharacterIdentityVersion.character_id == ch.id,
            CharacterIdentityVersion.major == base.major)).scalar_one()
        v = CharacterIdentityVersion(character_id=ch.id, major=base.major, minor=minor + 1, parent_version_id=base.id,
                                     change_kind="minor", original_prompt=base.original_prompt, spec=spec.dump(),
                                     status="locked", master_asset_id=base.master_asset_id, created_by=user.id,
                                     locked_at=_now())
        pkg = dict(base.package)
        pkg.update({"identity_version": {"major": base.major, "minor": minor + 1},
                    "body_wardrobe_baseline": spec.wardrobe_baseline,
                    "variants": [x.model_dump() for x in spec.variants], "inherits": str(base.id)})
        db.add(v)
        db.flush()
        pkg["identity_version"]["id"] = str(v.id)
        v.package, v.package_checksum = pkg, _checksum(pkg)
        ch.current_version_id = ch.locked_version_id = v.id
        ch.display_name = spec.display_name
        db.flush()
        return v
    major = db.execute(select(func.max(CharacterIdentityVersion.major)).where(
        CharacterIdentityVersion.character_id == ch.id)).scalar_one()
    v = CharacterIdentityVersion(character_id=ch.id, major=major + 1, minor=0, parent_version_id=base.id,
                                 change_kind="major", original_prompt=description or base.original_prompt,
                                 spec=spec.dump(), status="draft", created_by=user.id)
    db.add(v)
    db.flush()
    ch.current_version_id = v.id  # the previous locked version stays usable until the new one is locked
    return v


# ---------------------------------------------------------------- voice (V6.7)

def active_voice(db: Session, ch: Character) -> CharacterVoiceProfile | None:
    return db.execute(select(CharacterVoiceProfile).where(CharacterVoiceProfile.character_id == ch.id,
                                                          CharacterVoiceProfile.status == "active")
                      .order_by(CharacterVoiceProfile.version.desc())).scalars().first()


def set_voice(db: Session, user: User, ch: Character, kind: str, provider: str, voice_id: str, languages: list[str],
              territories: list[str], allowed_use: list[str], style: dict) -> CharacterVoiceProfile:
    cfg = require_enabled(db)
    if kind == "consented_likeness":
        raise ApiError(403, "voice_cloning_disabled",
                       "voice likeness cloning is not available (separate consent and abuse workflow required)")
    if kind != "licensed_tts":
        raise ApiError(422, "bad_voice_kind", "kind must be licensed_tts")
    if voice_id not in (cfg["tts_voices"].get(provider) or []):
        raise ApiError(422, "voice_not_licensed", "this voice is not in the licensed voice catalogue")
    screen_texts(db, [json.dumps(style or {}, ensure_ascii=False)])
    n = db.execute(select(func.coalesce(func.max(CharacterVoiceProfile.version), 0)).where(
        CharacterVoiceProfile.character_id == ch.id)).scalar_one()
    for old in db.execute(select(CharacterVoiceProfile).where(CharacterVoiceProfile.character_id == ch.id,
                                                              CharacterVoiceProfile.status == "active")).scalars():
        old.status = "superseded"
    vp = CharacterVoiceProfile(character_id=ch.id, version=n + 1, kind=kind, provider=provider, voice_id=voice_id,
                               languages=languages or [], territories=territories or ["*"],
                               allowed_use=allowed_use or ["studio"], style=style or {})
    db.add(vp)
    db.flush()
    return vp


# ---------------------------------------------------------------- resolution (@handle) + usability

def usable_by(db: Session, user_id: uuid.UUID, ch: Character) -> tuple[bool, str | None, uuid.UUID | None]:
    """(ok, reason, grant_id). Own locked characters are usable; others need a live listing AND an active grant.
    Searchability never implies a licence."""
    if ch.deleted_at is not None or ch.status in ("suspended", "deleted") or ch.moderation_status == "removed":
        return False, "character_unavailable", None
    if ch.locked_version_id is None:
        return False, "identity_not_locked", None
    if not likeness_ok(db, ch):
        return False, "consent_revoked", None
    if ch.creator_id == user_id:
        return True, None, None
    from . import character_market

    grant = character_market.active_grant(db, user_id, ch.id)
    if grant is None:
        return False, "license_required", None
    return True, None, grant.id


def _card(db: Session, ch: Character, viewer_id: uuid.UUID | None = None) -> dict:
    lst = db.execute(select(CharacterListing).where(CharacterListing.character_id == ch.id)).scalar_one_or_none()
    return {"id": str(ch.id), "handle": public_handle(db, ch), "display_name": ch.display_name,
            "owner": creator_handle(db, ch.creator_id), "own": viewer_id == ch.creator_id,
            "status": ch.status, "locked": ch.locked_version_id is not None,
            "listing": {"status": lst.status, "price": (lst.terms or {}).get("price"),
                        "license_summary": (lst.terms or {}).get("summary")} if lst else None}


MENTION_RE = r"@([a-z0-9_]{2,32})(?:/([a-z0-9_]{2,32}))?"


def find_mentions(text: str) -> list[str]:
    seen = []
    for m in re.finditer(MENTION_RE, (text or "").lower()):
        tok = m.group(0)
        if tok not in seen:
            seen.append(tok)
    return seen


def resolve(db: Session, user: User, mention: str, project_id: uuid.UUID | None = None) -> dict:
    """Never resolves an ambiguous name silently (V6.4). Order: project cast alias → exact @creator/handle →
    own characters → public characters (handle or display name). Returns status bound|needs_cast|ambiguous|
    not_found with candidates."""
    m = re.fullmatch(MENTION_RE, mention.strip().lower())
    if m is None:
        raise ApiError(422, "bad_mention", "mentions look like @name or @creator/name")
    a, b = m.group(1), m.group(2)
    cast = []
    if project_id is not None:
        cast = db.execute(select(ProjectCastMember).where(ProjectCastMember.project_id == project_id,
                                                          ProjectCastMember.removed_at.is_(None))).scalars().all()
    if b is None:
        hit = [c for c in cast if c.alias == a]
        if len(hit) == 1:
            return {"mention": mention, "status": "bound", "cast_member_id": str(hit[0].id),
                    "character_id": str(hit[0].character_id), "candidates": []}
    if b is not None:
        prof = db.execute(select(CreatorProfile).where(CreatorProfile.handle == a)).scalar_one_or_none()
        rows = db.execute(select(Character).where(Character.creator_id == prof.user_id, Character.handle == b,
                                                  Character.deleted_at.is_(None))).scalars().all() if prof else []
        rows = [c for c in rows if c.creator_id == user.id or c.status in ("public", "unlisted")]
    else:
        own = db.execute(select(Character).where(Character.creator_id == user.id, Character.handle == a,
                                                 Character.deleted_at.is_(None))).scalars().all()
        pub = db.execute(select(Character).where(
            Character.creator_id != user.id, Character.status == "public", Character.deleted_at.is_(None),
            (Character.handle == a) | (func.lower(Character.display_name) == a))).scalars().all()
        rows = own + pub
    cands = [_card(db, c, user.id) for c in rows]
    if not rows:
        return {"mention": mention, "status": "not_found", "candidates": []}
    in_cast = [c for c in cast if c.character_id in {r.id for r in rows}]
    if len(rows) == 1 and len(in_cast) == 1:
        return {"mention": mention, "status": "bound", "cast_member_id": str(in_cast[0].id),
                "character_id": str(rows[0].id), "candidates": cands}
    if len(rows) == 1:
        return {"mention": mention, "status": "needs_cast", "character_id": str(rows[0].id), "candidates": cands}
    return {"mention": mention, "status": "ambiguous", "candidates": cands}


def search(db: Session, user: User, q: str | None, language: str | None, aesthetic: str | None, limit: int = 30
           ) -> list[dict]:
    """Discovery: public characters with a live listing, plus your own. A result is not a licence."""
    from . import character_market

    stmt = select(Character).where(Character.deleted_at.is_(None),
                                   (Character.creator_id == user.id) | (Character.status == "public"))
    if q:
        like = f"%{q.lower()[:40]}%"
        stmt = stmt.where(func.lower(Character.display_name).like(like) | Character.handle.like(like))
    out = []
    for ch in db.execute(stmt.order_by(Character.created_at.desc()).limit(200)).scalars():
        if ch.creator_id != user.id and not character_market.listing_live(db, ch):
            continue
        v = db.get(CharacterIdentityVersion, ch.locked_version_id or ch.current_version_id)
        spec = v.spec if v else {}
        if language and language.lower() not in spec.get("languages", []):
            continue
        if aesthetic and spec.get("aesthetic") != aesthetic:
            continue
        out.append({**_card(db, ch, user.id), "languages": spec.get("languages", []),
                    "aesthetic": spec.get("aesthetic"), "age_appearance": spec.get("age_appearance"),
                    "archetype": (spec.get("personality") or {}).get("archetype"), "tags": spec.get("tags", []),
                    "certification": character_market.certification(db, ch)})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- output

def character_out(db: Session, ch: Character, include_private: bool = True) -> dict:
    from .storage import get_storage

    v = current_version(db, ch)
    locked = db.get(CharacterIdentityVersion, ch.locked_version_id) if ch.locked_version_id else None
    out = {**_card(db, ch, ch.creator_id), "origin": ch.origin, "moderation_status": ch.moderation_status,
           "current_version": {"id": str(v.id), "version": f"{v.major}.{v.minor}", "status": v.status,
                               "spec": v.spec, "original_prompt": v.original_prompt,
                               "master_asset_id": str(v.master_asset_id) if v.master_asset_id else None},
           "locked_version": {"id": str(locked.id), "version": f"{locked.major}.{locked.minor}",
                              "package_checksum": locked.package_checksum} if locked else None,
           "voice": None, "report": report_out(latest_report(db, v))}
    vp = active_voice(db, ch)
    if vp:
        out["voice"] = {"id": str(vp.id), "version": vp.version, "kind": vp.kind, "provider": vp.provider,
                        "voice_id": vp.voice_id, "languages": vp.languages}
    if include_private:  # owner only: short-lived signed URLs to the private reference images
        st = get_storage()
        assets = db.execute(select(CharacterAsset).where(CharacterAsset.identity_version_id == v.id,
                                                         CharacterAsset.superseded_by.is_(None))
                            .order_by(CharacterAsset.created_at)).scalars().all()
        out["assets"] = [{"id": str(a.id), "kind": a.kind, "view": a.view_key, "status": a.status,
                          "creator_review": a.creator_review, "qc": a.qc, "sha256": a.sha256,
                          "face_meaningful": VIEWS.get(a.view_key, {}).get("face", True),
                          "url": st.presign_get(a.storage_key, 600) if a.storage_key else None} for a in assets]
    return out


# ---------------------------------------------------------------- worker payload

def build_payload(db: Session, job: GenerationJob) -> dict:
    from ..models import AssetStatus, IdentityAsset
    from .storage import get_storage
    from .studio import DispatchRefused

    st = get_storage()
    ch = db.get(Character, uuid.UUID(job.spec["character_id"]))
    if ch is None or ch.deleted_at is not None or ch.status == "suspended" or not likeness_ok(db, ch):
        raise DispatchRefused("character_unavailable")  # re-checked at dispatch
    refs = []
    for rid in job.spec.get("references") or []:
        ra = db.get(CharacterAsset, uuid.UUID(rid))
        if ra is not None and ra.storage_key:
            refs.append(st.presign_get(ra.storage_key, 3600))
    if ch.origin == "real_person" and not refs:  # the owner's own consented photos condition the previews
        assets = db.execute(select(IdentityAsset).where(
            IdentityAsset.profile_id == ch.identity_profile_id, IdentityAsset.status == AssetStatus.accepted,
            IdentityAsset.kind == "photo").order_by(IdentityAsset.created_at).limit(3)).scalars().all()
        refs = [st.presign_get(a.storage_key, 3600) for a in assets]
    key = asset_key(job)
    return {"job_id": str(job.id), "attempt": job.attempts, "model": job.model_used, "kind": job.kind.value,
            "lease_s": get_settings().job_lease_s, "spec": job.spec, "reference_images": refs,
            "upload": {"image": {"key": key, "url": st.presign_put(key, "image/png")}}}
