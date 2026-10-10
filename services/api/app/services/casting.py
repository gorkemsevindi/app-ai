"""Project cast, @character script resolution and the casting director (Master Spec V6, §V6.4–V6.6).

- A cast member (CastBinding) freezes character UUID + identity version (+ package checksum) + voice version +
  lock mode for one Studio project; creator updates never change existing scenes.
- Script mentions (`@mert`, `@studio/mert`) resolve against the project cast first; anything ambiguous or not yet
  cast is returned for the user to decide — never resolved silently.
- Lock modes (V6.5): standard = best effort (measured, reported); strong = more identity conditioning and a
  higher compute price (multiplier, remote config); strict = threshold gate per shot with a bounded retry budget;
  failed strict attempts are refunded, so the user never pays more than the quote, and after the budget the shot
  fails explicitly (never labelled verified).
- The casting director is rule-based (labelled as such): it ranks candidates by role fit, language, style, age
  appearance, availability/licence and cost, and only *suggests*; the user casts explicitly."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import ApiError, not_found
from ..models import (
    Character,
    CharacterIdentityVersion,
    CharacterVoiceProfile,
    ProjectCastMember,
    StudioProject,
    User,
)
from . import character_market, characters

LOCK_MODES = ("standard", "strong", "strict")


def _alias_ok(alias: str) -> bool:
    import re

    return re.fullmatch(r"[a-z0-9_]{2,32}", alias or "") is not None


def add(db: Session, user: User, project: StudioProject, character_id: uuid.UUID | None, mention: str | None,
        alias: str | None, lock_mode: str, identity_version_id: uuid.UUID | None,
        allowed_variants: list[str] | None) -> ProjectCastMember:
    characters.require_enabled(db)
    if lock_mode not in LOCK_MODES:
        raise ApiError(422, "bad_lock_mode", "lock_mode: standard, strong or strict")
    if character_id is None:
        if not mention:
            raise ApiError(422, "character_required", "give a character id or an @mention")
        r = characters.resolve(db, user, mention, project.id)
        if r["status"] == "bound":
            raise ApiError(409, "already_cast", "this character is already in the cast", r)
        if r["status"] != "needs_cast":
            raise ApiError(409, "resolution_required", "pick the exact character", r)
        character_id = uuid.UUID(r["character_id"])
    ch = db.get(Character, character_id)
    hidden = ch is not None and ch.creator_id != user.id and ch.status not in ("public", "unlisted")
    if ch is None or ch.deleted_at is not None or hidden:
        raise not_found("character")
    ok, reason, grant_id = characters.usable_by(db, user.id, ch)
    if not ok:
        detail = {"reason": reason}
        if reason == "license_required":
            detail["quote"] = f"/licenses/quote?character_id={ch.id}"
        raise ApiError(403 if reason == "license_required" else 409, reason, "this character can't be cast", detail)
    if identity_version_id is not None and ch.creator_id != user.id:
        raise ApiError(403, "version_not_licensed", "licensed characters use the listed identity version")
    if ch.creator_id != user.id:
        lst = character_market.get_listing(db, ch)
        vid = lst.identity_version_id if lst else ch.locked_version_id
    else:
        vid = identity_version_id or ch.locked_version_id
    v = db.get(CharacterIdentityVersion, vid)
    if v is None or v.character_id != ch.id or v.status != "locked":
        raise ApiError(409, "identity_not_locked", "only a locked identity version can be cast")
    variants = sorted(set(allowed_variants or []))
    known = {x["key"] for x in (v.spec.get("variants") or [])}
    if set(variants) - known:
        raise ApiError(422, "unknown_variant", "unknown wardrobe variant", {"known": sorted(known)})
    alias = (alias or ch.handle).lower()
    if not _alias_ok(alias):
        raise ApiError(422, "bad_alias", "alias: 2-32 lowercase letters, digits or _")
    taken = db.execute(select(ProjectCastMember.id).where(ProjectCastMember.project_id == project.id,
                                                          ProjectCastMember.alias == alias,
                                                          ProjectCastMember.removed_at.is_(None))).first()
    if taken:
        raise ApiError(409, "alias_taken", "another cast member uses this alias")
    voice = characters.active_voice(db, ch)
    m = ProjectCastMember(project_id=project.id, character_id=ch.id, identity_version_id=v.id,
                          voice_profile_id=voice.id if voice else None, alias=alias, lock_mode=lock_mode,
                          allowed_variants=variants, grant_id=grant_id, added_by=user.id)
    db.add(m)
    db.flush()
    return m


def get_member(db: Session, project: StudioProject, member_id: uuid.UUID) -> ProjectCastMember:
    m = db.get(ProjectCastMember, member_id)
    if m is None or m.project_id != project.id or m.removed_at is not None:
        raise not_found("cast member")
    return m


def remove(db: Session, project: StudioProject, member_id: uuid.UUID) -> None:
    from datetime import UTC, datetime

    get_member(db, project, member_id).removed_at = datetime.now(UTC)


def members(db: Session, project: StudioProject) -> list[ProjectCastMember]:
    return db.execute(select(ProjectCastMember).where(ProjectCastMember.project_id == project.id,
                                                      ProjectCastMember.removed_at.is_(None))
                      .order_by(ProjectCastMember.created_at)).scalars().all()


def member_out(db: Session, m: ProjectCastMember) -> dict:
    ch = db.get(Character, m.character_id)
    v = db.get(CharacterIdentityVersion, m.identity_version_id)
    lst = character_market.get_listing(db, ch)
    price = (lst.terms or {}).get("price") if lst and ch.creator_id != m.added_by else None
    return {"id": str(m.id), "alias": m.alias, "mention": f"@{m.alias}", "character_id": str(ch.id),
            "handle": characters.public_handle(db, ch), "display_name": ch.display_name,
            "identity_version": {"id": str(v.id), "version": f"{v.major}.{v.minor}",
                                 "package_checksum": v.package_checksum},
            "voice_profile_id": str(m.voice_profile_id) if m.voice_profile_id else None,
            "lock_mode": m.lock_mode, "allowed_variants": m.allowed_variants,
            "license": {"grant_id": str(m.grant_id), "price": price} if m.grant_id else None,
            "own": ch.creator_id == m.added_by}


def resolve_script(db: Session, user: User, project: StudioProject, text: str) -> dict:
    out = [characters.resolve(db, user, x, project.id) for x in characters.find_mentions(text)]
    return {"mentions": out, "all_bound": all(r["status"] == "bound" for r in out)}


# ---------------------------------------------------------------- studio integration helpers

def cast_input(db: Session, member_id: uuid.UUID) -> dict | None:
    """The frozen cast snapshot that becomes part of a shot's content hash."""
    m = db.get(ProjectCastMember, member_id)
    if m is None:
        return None
    v = db.get(CharacterIdentityVersion, m.identity_version_id)
    return {"member_id": str(m.id), "character_uuid": str(m.character_id), "identity_version_id": str(v.id),
            "package_checksum": v.package_checksum, "lock_mode": m.lock_mode,
            "voice_profile_id": str(m.voice_profile_id) if m.voice_profile_id else None,
            "variants": m.allowed_variants}


def cast_problem(db: Session, user_id: uuid.UUID, project_id: uuid.UUID, member_id: uuid.UUID) -> str | None:
    m = db.get(ProjectCastMember, member_id)
    if m is None or m.removed_at is not None or m.project_id != project_id:
        return "cast_member_missing"
    ch = db.get(Character, m.character_id)
    ok, reason, grant_id = characters.usable_by(db, user_id, ch)
    if not ok:
        return reason
    if m.grant_id is not None and grant_id != m.grant_id:
        m.grant_id = grant_id  # renewed licence: same character, new grant id
    return None


def pricing(db: Session, member_id: uuid.UUID, seconds: int) -> dict:
    """Licence credits for one shot (others' characters only) + owner id for royalties."""
    m = db.get(ProjectCastMember, member_id)
    ch = db.get(Character, m.character_id)
    if ch.creator_id == m.added_by or m.grant_id is None:
        return {"license_credits": 0, "owner_id": str(ch.creator_id), "grant_id": None}
    from ..models import CharacterLicenseGrant

    g = db.get(CharacterLicenseGrant, m.grant_id)
    return {"license_credits": character_market.usage_credits(g.terms_snapshot, seconds),
            "owner_id": str(ch.creator_id), "grant_id": str(g.id), "terms": g.terms_snapshot}


def display(db: Session, member_id: uuid.UUID) -> dict:
    m = db.get(ProjectCastMember, member_id)
    ch = db.get(Character, m.character_id)
    v = db.get(CharacterIdentityVersion, m.identity_version_id)
    spec = characters.CharacterSpec.model_validate(v.spec)
    desc = ", ".join(x for x in [spec.appearance.face[:200], spec.appearance.hair, spec.wardrobe_baseline] if x)
    return {"name": ch.display_name, "description": desc[:500], "alias": m.alias}


# ---------------------------------------------------------------- casting director (rule-based)

class Role(BaseModel):
    role: str = Field(min_length=2, max_length=60)
    description: str = Field(default="", max_length=500)
    age_appearance: str | None = Field(default=None, pattern=r"^(young_adult|adult|middle_aged|senior)$")
    aesthetic: str | None = Field(default=None, pattern=r"^(photorealistic|stylized|animation)$")
    languages: list[str] = Field(default_factory=list, max_length=5)
    max_credits_per_second: int | None = Field(default=None, ge=0)


def suggest(db: Session, user: User, project: StudioProject, roles: list[Role], per_role: int = 3) -> dict:
    """Ranks candidates per role; never casts, never treats a search hit as licensed."""
    from . import creative

    characters.require_enabled(db)
    pool = characters.search(db, user, None, None, None, limit=200)
    cast_ids = {str(m.character_id) for m in members(db, project)}
    out = []
    for r in roles:
        characters.screen_texts(db, [r.role, r.description])
        want = f"{r.role} {r.description}".lower()
        ranked = []
        for c in pool:
            ch = db.get(Character, uuid.UUID(c["id"]))
            v = db.get(CharacterIdentityVersion, ch.locked_version_id) if ch.locked_version_id else None
            if v is None:
                continue
            spec = characters.CharacterSpec.model_validate(v.spec)
            fit = creative.similarity(want, " ".join(spec.texts()).lower())
            reasons, score = [f"role fit {fit:.2f}"], fit
            if r.age_appearance:
                if spec.age_appearance == r.age_appearance:
                    score += 0.2
                    reasons.append("age appearance matches")
                else:
                    score -= 0.1
            if r.aesthetic:
                score += 0.15 if spec.aesthetic == r.aesthetic else -0.15
            if r.languages:
                common = set(x.lower() for x in r.languages) & set(spec.languages)
                score += 0.1 * len(common)
                if not common:
                    reasons.append("language mismatch")
            ok, reason, _ = characters.usable_by(db, user.id, ch)
            lic = "own" if ch.creator_id == user.id else ("licensed" if ok else (reason or "unavailable"))
            price = ((c.get("listing") or {}).get("price") or {}) if ch.creator_id != user.id else {}
            cps = int(price.get("per_second_credits", 0))
            if r.max_credits_per_second is not None and cps > r.max_credits_per_second:
                continue
            score -= 0.01 * cps
            cert = c.get("certification") or {}
            if cert.get("badge"):
                score += 0.05
                reasons.append("consistency tested")
            ranked.append({"character_id": c["id"], "handle": c["handle"], "display_name": c["display_name"],
                           "score": round(score, 4), "reasons": reasons, "license_status": lic,
                           "price": price or None, "certification": cert or None,
                           "already_cast": c["id"] in cast_ids})
        ranked.sort(key=lambda x: -x["score"])
        out.append({"role": r.role, "candidates": ranked[:per_role]})
    return {"director": "rule_based", "label": "rule-based casting (not AI)", "roles": out,
            "note": "suggestions only: cast explicitly; licensed characters need an active licence grant"}


def voice_of(db: Session, member_id: uuid.UUID) -> CharacterVoiceProfile | None:
    m = db.get(ProjectCastMember, member_id)
    return db.get(CharacterVoiceProfile, m.voice_profile_id) if m and m.voice_profile_id else None
