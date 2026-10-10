"""Character marketplace, licences and royalties (Master Spec V6, §V6.8–V6.9).

- Off unless the `character_marketplace` flag is on **and** a legal review reference is recorded
  (`legal_review_ref`), same gate as the V4 actor marketplace.
- A creator lists a *locked* original character after the rights declaration, a validation report that is not
  failing, and moderation review. Real-person characters are not listable here (V4 actor marketplace instead).
- Licence objects are explicit and versioned; a grant freezes the terms (`terms_snapshot`) and has its own id.
- Usage is priced per scene (shot) and/or per second and added to the render quote. Usage events are reserved with
  the shot job, settled exactly once with it (royalty accrues then) and released when the job is refunded.
- Waterfall per job: collected net revenue (paid credits × usd_per_paid_credit) → character royalties
  (contractual, `character_share_rate` of each character's licence portion) → template/referral shares, capped so
  the total never exceeds collected net revenue → platform. Promo credits create no royalties.
- Takedown (rights/abuse) revokes grants immediately; an owner unpublishing stops new grants while existing grants
  run to their end date. Previously rendered outputs are retained per `rendered_projects: retain`."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import ApiError, not_found
from ..models import (
    Character,
    CharacterIdentityVersion,
    CharacterLicenseGrant,
    CharacterListing,
    CharacterUsageEvent,
    FeatureFlag,
    GenerationJob,
    Report,
    User,
)
from . import characters, creators

FLAG = "character_marketplace"
ALWAYS_PROHIBITED = {"adult", "political"}
CONTEXTS = ("adult", "political", "alcohol", "gambling", "violence", "religion", "medical", "financial_advice")


def _now() -> datetime:
    return datetime.now(UTC)


class Price(BaseModel):
    per_scene_credits: int = Field(default=0, ge=0, le=10000)
    per_second_credits: int = Field(default=0, ge=0, le=1000)


class Modification(BaseModel):
    wardrobe_variants: bool = True
    voice_change: bool = False


class LicenseTerms(BaseModel):
    personal_use: bool = True
    commercial_use: bool = False
    advertising: bool = False
    allowed_categories: list[Literal["entertainment", "education", "social", "music", "advertising",
                                     "corporate"]] = Field(default_factory=lambda: ["entertainment", "social"],
                                                           min_length=1)
    prohibited_contexts: list[Literal["adult", "political", "alcohol", "gambling", "violence", "religion",
                                      "medical", "financial_advice"]] = Field(default_factory=list)
    modification: Modification = Field(default_factory=Modification)
    sublicensing: Literal[False] = False
    exclusivity: Literal["non_exclusive"] = "non_exclusive"
    territories: list[str] = Field(default_factory=lambda: ["*"], min_length=1, max_length=50)
    duration_days: int = Field(default=30, ge=1, le=365)
    revocation_policy: Literal["takedown_immediate_unpublish_at_term_end"] = "takedown_immediate_unpublish_at_term_end"
    rendered_projects: Literal["retain"] = "retain"
    attribution_required: bool = True
    price: Price = Field(default_factory=Price)


def config(db: Session) -> tuple[bool, dict]:
    f = db.get(FeatureFlag, FLAG)
    v = {"max_open_reports_before_pause": 3, **((f.value or {}) if f else {})}
    return bool(f and f.enabled and v.get("legal_review_ref")), v


def require_enabled(db: Session) -> dict:
    ok, cfg = config(db)
    if not ok:
        raise ApiError(403, "feature_disabled", "the character marketplace is not available")
    return cfg


def summary(t: LicenseTerms) -> str:
    use = "commercial" if t.commercial_use else "personal"
    p = t.price
    price = " + ".join(x for x in [p.per_scene_credits and f"{p.per_scene_credits} cr/scene",
                                   p.per_second_credits and f"{p.per_second_credits} cr/s"] if x) or "free"
    return f"{use} use, {', '.join(t.allowed_categories)}; {t.duration_days} days; {price}; no sublicensing"


def certification(db: Session, ch: Character) -> dict | None:
    v = db.get(CharacterIdentityVersion, ch.locked_version_id) if ch.locked_version_id else None
    if v is None:
        return None
    p = v.package or {}
    verdict, method = p.get("validation_verdict"), p.get("validation_method")
    return {"badge": "consistency_tested" if verdict in ("pass", "warn") else None, "verdict": verdict,
            "method": method, "verified": verdict == "pass" and method == "face_embedding",
            "note": "measured on the identity sheet; not a guarantee of on-screen consistency"}


def get_listing(db: Session, ch: Character) -> CharacterListing | None:
    return db.execute(select(CharacterListing).where(CharacterListing.character_id == ch.id)).scalar_one_or_none()


def listing_live(db: Session, ch: Character) -> bool:
    lst = get_listing(db, ch)
    ok, _ = config(db)
    return (ok and lst is not None and lst.status == "active" and ch.status in ("public", "unlisted")
            and ch.locked_version_id is not None and ch.moderation_status not in ("flagged", "removed")
            and characters.likeness_ok(db, ch))


def publish(db: Session, user: User, ch: Character, visibility: str, terms: dict) -> CharacterListing:
    require_enabled(db)
    creators.require_profile(db, user)
    if ch.origin != "synthetic":
        raise ApiError(409, "real_person_not_listable", "real-person likeness is licensed via the AI actor marketplace")
    if ch.locked_version_id is None:
        raise ApiError(409, "identity_not_locked", "lock the identity before publishing")
    if ch.moderation_status in ("flagged", "removed") or ch.status == "suspended":
        raise ApiError(409, "under_review", "this character is under moderation review")
    cert = certification(db, ch)
    if cert is None or cert["verdict"] == "fail":
        raise ApiError(409, "quality_check_required", "the identity validation must not be failing")
    if visibility not in ("public", "unlisted"):
        raise ApiError(422, "bad_visibility", "visibility must be public or unlisted")
    try:
        t = LicenseTerms.model_validate(terms or {})
    except ValidationError as e:
        raise ApiError(422, "bad_terms", "invalid licence terms",
                       {"errors": [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()[:10]]}) from e
    t.prohibited_contexts = sorted(set(t.prohibited_contexts) | ALWAYS_PROHIBITED)
    t.territories = sorted({x.upper() if x != "*" else "*" for x in t.territories})
    if t.advertising and not t.commercial_use:
        raise ApiError(422, "bad_terms", "advertising requires commercial use")
    body = {**t.model_dump(), "summary": summary(t)}
    lst = get_listing(db, ch)
    if lst is None:
        lst = CharacterListing(character_id=ch.id, owner_id=user.id, identity_version_id=ch.locked_version_id,
                               visibility=visibility, terms=body, terms_version=1, certification=cert)
        db.add(lst)
    else:  # new terms version; existing grants keep their frozen snapshot
        lst.terms, lst.terms_version = body, lst.terms_version + 1
        lst.visibility, lst.identity_version_id, lst.certification = visibility, ch.locked_version_id, cert
    lst.status = "pending_review"  # every publication / change is reviewed (originality, rights, quality)
    db.flush()
    return lst


def review(db: Session, admin: User, ch: Character, decision: str, note: str) -> CharacterListing:
    lst = get_listing(db, ch)
    if lst is None:
        raise not_found("listing")
    if decision == "approve":
        if ch.moderation_status == "flagged":
            ch.moderation_status = "cleared"  # reviewer checked the lookalike / report flag
        lst.status = "active"
        ch.status = lst.visibility
    elif decision == "reject":
        lst.status = "rejected"
    elif decision == "pause":
        lst.status = "paused"
    else:
        raise ApiError(422, "bad_decision", "approve, reject or pause")
    lst.review_note = note
    db.flush()
    return lst


def unpublish(db: Session, user: User, ch: Character) -> CharacterListing:
    lst = get_listing(db, ch)
    if lst is None or ch.creator_id != user.id:
        raise not_found("listing")
    lst.status = "removed"
    ch.status = "private"
    return lst  # existing grants continue until their end date (contract), no new grants


def takedown(db: Session, admin: User, ch: Character, note: str) -> int:
    """Rights/abuse takedown: character suspended, listing removed, all grants revoked now."""
    ch.status, ch.moderation_status, ch.moderation_note = "suspended", "removed", note[:300]
    lst = get_listing(db, ch)
    if lst is not None:
        lst.status = "removed"
    n = 0
    for g in db.execute(select(CharacterLicenseGrant).where(CharacterLicenseGrant.character_id == ch.id,
                                                            CharacterLicenseGrant.status == "active")).scalars():
        g.status, g.revoked_at, g.revoke_reason = "revoked", _now(), f"takedown: {note}"[:300]
        n += 1
    db.flush()
    return n


def report(db: Session, user: User, ch: Character, reason: str, details: str | None) -> Report:
    r = Report(reporter_id=user.id, character_id=ch.id, reason=reason, details=details)
    db.add(r)
    db.flush()
    _, cfg = config(db)
    n = db.execute(select(func.count()).select_from(Report).where(Report.character_id == ch.id,
                                                                  Report.status == "open")).scalar_one()
    lst = get_listing(db, ch)
    if n >= int(cfg["max_open_reports_before_pause"]) and lst is not None and lst.status == "active":
        lst.status = "paused"  # repeat-abuse control: pause until a human reviews
        ch.moderation_status = "flagged"
    return r


# ---------------------------------------------------------------- quotes + grants

def quote(db: Session, user: User, ch: Character, seconds: int = 8, scenes: int = 1) -> dict:
    require_enabled(db)
    if not listing_live(db, ch):
        raise not_found("character")
    lst = get_listing(db, ch)
    t = LicenseTerms.model_validate({k: v for k, v in lst.terms.items() if k != "summary"})
    territory_ok = "*" in t.territories or (user.country or "").upper() in t.territories
    return {"character_id": str(ch.id), "handle": characters.public_handle(db, ch), "listing_id": str(lst.id),
            "terms": lst.terms, "terms_version": lst.terms_version, "territory_ok": territory_ok,
            "own": ch.creator_id == user.id, "certification": lst.certification,
            "example": {"scenes": scenes, "seconds": seconds,
                        "license_credits": usage_credits(lst.terms, seconds, scenes),
                        "note": "licence credits are added to the render quote; generation compute is separate"},
            "grant_ends_at": (_now() + timedelta(days=t.duration_days)).isoformat()}


def usage_credits(terms: dict, seconds: int, scenes: int = 1) -> int:
    p = (terms or {}).get("price") or {}
    return int(p.get("per_scene_credits", 0)) * scenes + int(p.get("per_second_credits", 0)) * seconds


def grant(db: Session, user: User, ch: Character, accept_terms: bool, terms_version: int | None, idem: str
          ) -> CharacterLicenseGrant:
    key = f"cgrant:{user.id}:{idem}"
    existing = db.execute(select(CharacterLicenseGrant).where(CharacterLicenseGrant.idempotency_key == key)
                          ).scalar_one_or_none()
    if existing is not None:
        return existing
    q = quote(db, user, ch)
    if q["own"]:
        raise ApiError(409, "own_character", "your own characters need no licence")
    if not q["territory_ok"]:
        raise ApiError(403, "territory_not_licensed", "this character is not licensed in your country")
    if not accept_terms or terms_version != q["terms_version"]:
        raise ApiError(409, "accept_terms_required", "accept the current licence terms",
                       {"terms_version": q["terms_version"]})
    lst = get_listing(db, ch)
    now = _now()
    g = CharacterLicenseGrant(listing_id=lst.id, character_id=ch.id, licensee_id=user.id, terms_snapshot=lst.terms,
                              terms_version=lst.terms_version, status="active", starts_at=now,
                              ends_at=now + timedelta(days=int(lst.terms["duration_days"])), idempotency_key=key)
    db.add(g)
    db.flush()
    return g


def active_grant(db: Session, user_id: uuid.UUID, character_id: uuid.UUID) -> CharacterLicenseGrant | None:
    ch = db.get(Character, character_id)
    if ch is None or not listing_live(db, ch) and not _grant_survives_unpublish(db, ch):
        return None
    rows = db.execute(select(CharacterLicenseGrant).where(
        CharacterLicenseGrant.licensee_id == user_id, CharacterLicenseGrant.character_id == character_id,
        CharacterLicenseGrant.status == "active", CharacterLicenseGrant.ends_at > _now())
        .order_by(CharacterLicenseGrant.ends_at.desc())).scalars().all()
    return rows[0] if rows else None


def _grant_survives_unpublish(db: Session, ch: Character) -> bool:
    """Owner unpublished (not a takedown): grants already issued run to term end."""
    lst = get_listing(db, ch)
    return (lst is not None and lst.status == "removed" and ch.status == "private"
            and ch.moderation_status != "removed" and ch.locked_version_id is not None
            and characters.likeness_ok(db, ch))


def check_contexts(terms: dict, texts: list[str]) -> list[str]:
    from .actors import CONTEXT_KEYWORDS

    blob = " ".join(t for t in texts if t).lower()
    return [c for c in terms.get("prohibited_contexts", [])
            if c in CONTEXT_KEYWORDS and re.search(CONTEXT_KEYWORDS[c], blob)]


# ---------------------------------------------------------------- usage + royalties

def reserve_usage(db: Session, job: GenerationJob, usage: list[dict]) -> None:
    for u in usage:
        db.add(CharacterUsageEvent(job_id=job.id, character_id=uuid.UUID(u["character_id"]),
                                   owner_id=uuid.UUID(u["owner_id"]), user_id=job.user_id,
                                   grant_id=uuid.UUID(u["grant_id"]) if u.get("grant_id") else None,
                                   identity_version_id=uuid.UUID(u["identity_version_id"]), seconds=u["seconds"],
                                   license_credits=u["license_credits"], status="reserved"))
    db.flush()


def on_job_settled(db: Session, job: GenerationJob) -> list:
    """Exactly once per job: usage events -> settled, royalty accrues on the paid share of the licence credits."""
    rows = db.execute(select(CharacterUsageEvent).where(CharacterUsageEvent.job_id == job.id,
                                                        CharacterUsageEvent.status == "reserved")
                      .with_for_update()).scalars().all()
    if not rows:
        return []
    policy = creators.active_policy(db)
    cfg = creators.PolicyConfig.model_validate(policy.config) if policy else None
    paid = sum(creators._paid_credits_by_lot(db, job.id).values())
    out = []
    for u in rows:
        u.status = "settled"
        if cfg is None or cfg.character_share_rate <= 0 or not u.license_credits or not job.credit_cost:
            continue
        if u.owner_id == job.user_id or not creators._eligible(db, u.owner_id):
            continue
        paid_part = paid * u.license_credits / job.credit_cost
        gross = int(round(paid_part * cfg.usd_per_paid_credit * creators.MICROS))
        if gross <= 0:
            continue
        e = creators._accrue(db, u.owner_id, "character_royalty", round(gross * cfg.character_share_rate), gross,
                             policy, f"croyalty:{job.id}:{u.character_id}", cfg, job_id=job.id,
                             license_id=u.grant_id, payer_id=job.user_id)
        out.append(e)
    db.flush()
    return out


def on_job_released(db: Session, job: GenerationJob) -> None:
    for u in db.execute(select(CharacterUsageEvent).where(CharacterUsageEvent.job_id == job.id,
                                                          CharacterUsageEvent.status == "reserved")).scalars():
        u.status = "released"
    db.flush()


def analytics(db: Session, owner: User) -> dict:
    rows = db.execute(select(CharacterUsageEvent.character_id, CharacterUsageEvent.status, func.count(),
                             func.coalesce(func.sum(CharacterUsageEvent.seconds), 0),
                             func.coalesce(func.sum(CharacterUsageEvent.license_credits), 0))
                      .where(CharacterUsageEvent.owner_id == owner.id)
                      .group_by(CharacterUsageEvent.character_id, CharacterUsageEvent.status)).all()
    by: dict = {}
    for cid, status, n, secs, cr in rows:
        d = by.setdefault(str(cid), {"settled_uses": 0, "seconds": 0, "license_credits": 0, "released_uses": 0})
        if status == "settled":
            d["settled_uses"] += n
            d["seconds"] += int(secs)
            d["license_credits"] += int(cr)
        elif status == "released":
            d["released_uses"] += n
    from ..models import CreatorEarning

    roy = db.execute(select(func.coalesce(func.sum(CreatorEarning.amount_micros), 0)).where(
        CreatorEarning.creator_id == owner.id,
        CreatorEarning.kind.in_(("character_royalty",)))).scalar_one()
    claws = db.execute(select(func.coalesce(func.sum(CreatorEarning.amount_micros), 0)).where(
        CreatorEarning.creator_id == owner.id, CreatorEarning.kind == "clawback",
        CreatorEarning.idempotency_key.like("claw:croyalty:%"))).scalar_one()
    grants = db.execute(select(func.count()).select_from(CharacterLicenseGrant).join(
        Character, Character.id == CharacterLicenseGrant.character_id).where(Character.creator_id == owner.id)
    ).scalar_one()
    return {"characters": by, "grants": int(grants), "royalty_micros": int(roy) + int(claws), "currency": "USD"}
