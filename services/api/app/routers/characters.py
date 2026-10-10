"""V6 Creator Character Identity API: creator studio, identity build/lock, registry + @resolution, project cast,
casting director, marketplace listings, licences (quote/grant) and moderation."""

import uuid

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import admin_user, client_ip, current_user
from ..errors import ApiError, not_found
from ..models import Character, CharacterLicenseGrant, User
from ..services import casting, character_market, characters, ratelimit, studio
from ..services.generation import audit

router = APIRouter(tags=["characters"])


def _own(db: Session, user: User, cid: uuid.UUID) -> Character:
    return characters.get_own(db, user, cid)


# ---------------------------------------------------------------- creator studio

class CreateIn(BaseModel):
    handle: str = Field(min_length=2, max_length=32)
    description: str = Field(min_length=10, max_length=2000)
    spec: dict = Field(default_factory=dict)  # structured controls (CharacterSpec fields)
    rights: dict
    origin: str = "synthetic"
    identity_profile_id: uuid.UUID | None = None
    attest_own_likeness: bool = False


@router.post("/characters", status_code=201)
def create(body: CreateIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ratelimit.hit("character_create", str(user.id), 20)
    ch = characters.create(db, user, body.handle.lower(), body.description, body.spec, body.rights, body.origin,
                           body.identity_profile_id, body.attest_own_likeness)
    audit(db, user.id, "character.created", "character", str(ch.id), {"origin": ch.origin}, ip=client_ip(request))
    db.commit()
    return characters.character_out(db, ch)


@router.get("/characters")
def my_characters(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Character).where(Character.creator_id == user.id, Character.deleted_at.is_(None))
                      .order_by(Character.created_at.desc())).scalars().all()
    return {"items": [characters.character_out(db, c, include_private=False) for c in rows]}


@router.get("/characters/search")
def search(q: str | None = Query(default=None, max_length=40), language: str | None = None,
           aesthetic: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    characters.require_enabled(db)
    return {"items": characters.search(db, user, q, language, aesthetic),
            "note": "search results are not licences"}


@router.get("/characters/resolve")
def resolve(mention: str = Query(max_length=70), project_id: uuid.UUID | None = None,
            user: User = Depends(current_user), db: Session = Depends(get_db)):
    characters.require_enabled(db)
    if project_id is not None:
        studio.get_project(db, user, project_id)
    return characters.resolve(db, user, mention, project_id)


@router.get("/characters/licenses")
def my_licenses(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(CharacterLicenseGrant).where(CharacterLicenseGrant.licensee_id == user.id)
                      .order_by(CharacterLicenseGrant.created_at.desc())).scalars().all()
    return {"items": [_grant_out(g) for g in rows]}


@router.get("/characters/{character_id}")
def get(character_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ch = db.get(Character, character_id)
    if ch is None or ch.deleted_at is not None:
        raise not_found("character")
    if ch.creator_id == user.id:
        return characters.character_out(db, ch)
    if not character_market.listing_live(db, ch):  # private/unlisted-without-listing: invisible to others
        raise not_found("character")
    # public card only: no reference assets, no package, no embeddings (rendering access != asset access)
    out = characters.character_out(db, ch, include_private=False)
    for k in ("current_version", "locked_version", "moderation_status"):
        out.pop(k, None)
    return {**out, "certification": character_market.certification(db, ch)}


class PayIn(BaseModel):
    confirmed_credits: int | None = None
    count: int = Field(default=4, ge=1, le=12)


def _idem(key: str | None) -> str:
    if not key or not 8 <= len(key) <= 80:
        raise ApiError(422, "idempotency_key_required", "send an Idempotency-Key header (8-80 chars)")
    return key


@router.get("/characters/{character_id}/quote")
def image_quote(character_id: uuid.UUID, kind: str = Query(pattern=r"^(preview|build|repair)$"), count: int = 4,
                user: User = Depends(current_user), db: Session = Depends(get_db)):
    ch = _own(db, user, character_id)
    cfg = characters.require_enabled(db)
    if kind == "preview":
        return characters.image_quote(db, "seed_preview", count)
    if kind == "build":
        return characters.image_quote(db, "view", len([k for k in cfg["views"] if k in characters.VIEWS]))
    v = characters.current_version(db, ch)
    bad = [a for a in characters._live_views(db, v) if a.status in ("failed", "rejected")]
    return characters.image_quote(db, "view", len(bad))


@router.post("/characters/{character_id}/preview", status_code=202)
def preview(character_id: uuid.UUID, body: PayIn, request: Request, user: User = Depends(current_user),
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
            db: Session = Depends(get_db)):
    ratelimit.hit("character_jobs", str(user.id), 30)
    ch = _own(db, user, character_id)
    out = characters.start_previews(db, user, ch, body.count, body.confirmed_credits, _idem(idempotency_key))
    audit(db, user.id, "character.previews", "character", str(ch.id), {"credits": out["credits"]},
          ip=client_ip(request))
    db.commit()
    return out


class MasterIn(BaseModel):
    asset_id: uuid.UUID


@router.post("/characters/{character_id}/approve-master")
def approve_master(character_id: uuid.UUID, body: MasterIn, request: Request, user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    ch = _own(db, user, character_id)
    out = characters.approve_master(db, user, ch, body.asset_id)
    audit(db, user.id, "character.master_approved", "character", str(ch.id), out, ip=client_ip(request))
    db.commit()
    return out


@router.post("/characters/{character_id}/identity-build", status_code=202)
def identity_build(character_id: uuid.UUID, body: PayIn, request: Request, user: User = Depends(current_user),
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                   db: Session = Depends(get_db)):
    ratelimit.hit("character_jobs", str(user.id), 30)
    ch = _own(db, user, character_id)
    out = characters.start_build(db, user, ch, body.confirmed_credits, _idem(idempotency_key))
    audit(db, user.id, "character.build", "character", str(ch.id), {"credits": out["credits"]}, ip=client_ip(request))
    db.commit()
    return out


@router.post("/characters/{character_id}/repair", status_code=202)
def repair(character_id: uuid.UUID, body: PayIn, user: User = Depends(current_user),
           idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
           db: Session = Depends(get_db)):
    ratelimit.hit("character_jobs", str(user.id), 30)
    ch = _own(db, user, character_id)
    out = characters.repair(db, user, ch, body.confirmed_credits, _idem(idempotency_key))
    db.commit()
    return out


@router.get("/characters/{character_id}/jobs")
def jobs(character_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..models import CharacterAsset, GenerationJob

    ch = _own(db, user, character_id)
    rows = db.execute(select(GenerationJob, CharacterAsset).join(
        CharacterAsset, CharacterAsset.job_id == GenerationJob.id).where(CharacterAsset.character_id == ch.id)
        .order_by(GenerationJob.created_at)).all()
    return {"items": [{"job_id": str(j.id), "asset_id": str(a.id), "view": a.view_key, "kind": a.kind,
                       "status": j.status.value, "progress": j.progress, "error_code": j.error_code,
                       "credits": j.credit_cost, "refunded": j.refunded} for j, a in rows]}


class ReviewAssetIn(BaseModel):
    decision: str = Field(pattern=r"^(approved|rejected)$")


@router.post("/characters/{character_id}/assets/{asset_id}/review")
def review_asset(character_id: uuid.UUID, asset_id: uuid.UUID, body: ReviewAssetIn,
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    ch = _own(db, user, character_id)
    a = characters.review_asset(db, user, ch, asset_id, body.decision)
    db.commit()
    return {"id": str(a.id), "status": a.status, "creator_review": a.creator_review}


@router.post("/characters/{character_id}/validate")
def validate(character_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ch = _own(db, user, character_id)
    v = characters.current_version(db, ch)
    if v.status not in ("built", "locked"):
        raise ApiError(409, "identity_not_built", "build the multi-view identity first")
    rep = characters.validate(db, ch, v)
    db.commit()
    return characters.report_out(rep)


@router.post("/characters/{character_id}/lock")
def lock(character_id: uuid.UUID, request: Request, user: User = Depends(current_user),
         db: Session = Depends(get_db)):
    ch = _own(db, user, character_id)
    v = characters.lock(db, user, ch)
    audit(db, user.id, "character.locked", "character", str(ch.id),
          {"version": f"{v.major}.{v.minor}", "checksum": v.package_checksum}, ip=client_ip(request))
    db.commit()
    return characters.character_out(db, ch)


@router.get("/characters/{character_id}/package")
def package(character_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """IdentityPackageManifest — owner only (internal identity package; no embeddings exist to export)."""
    ch = _own(db, user, character_id)
    from ..models import CharacterIdentityVersion

    v = db.get(CharacterIdentityVersion, ch.locked_version_id) if ch.locked_version_id else None
    if v is None:
        raise ApiError(409, "identity_not_locked", "lock the identity first")
    return {"manifest": v.package, "checksum": v.package_checksum}


class VersionIn(BaseModel):
    change: str = Field(pattern=r"^(minor|major)$")
    spec: dict = Field(default_factory=dict)
    description: str | None = Field(default=None, max_length=2000)


@router.post("/characters/{character_id}/versions", status_code=201)
def new_version(character_id: uuid.UUID, body: VersionIn, request: Request, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    ch = _own(db, user, character_id)
    v = characters.new_version(db, user, ch, body.change, body.spec, body.description)
    audit(db, user.id, f"character.version_{body.change}", "character", str(ch.id),
          {"version": f"{v.major}.{v.minor}"}, ip=client_ip(request))
    db.commit()
    return characters.character_out(db, ch)


class VoiceIn(BaseModel):
    kind: str = "licensed_tts"
    provider: str = Field(min_length=2, max_length=60)
    voice_id: str = Field(min_length=1, max_length=120)
    languages: list[str] = Field(default_factory=list, max_length=10)
    territories: list[str] = Field(default_factory=lambda: ["*"], max_length=50)
    allowed_use: list[str] = Field(default_factory=lambda: ["studio"], max_length=5)
    style: dict = Field(default_factory=dict)


@router.post("/characters/{character_id}/voice", status_code=201)
def voice(character_id: uuid.UUID, body: VoiceIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ch = _own(db, user, character_id)
    vp = characters.set_voice(db, user, ch, body.kind, body.provider, body.voice_id, body.languages,
                              body.territories, body.allowed_use, body.style)
    db.commit()
    return {"id": str(vp.id), "version": vp.version, "kind": vp.kind, "provider": vp.provider,
            "voice_id": vp.voice_id, "synthesis": "not enabled (no TTS provider integrated yet)"}


@router.post("/characters/{character_id}/revoke-likeness")
def revoke_likeness(character_id: uuid.UUID, request: Request, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    ch = _own(db, user, character_id)
    n = characters.revoke_likeness(db, user, ch)
    audit(db, user.id, "character.consent_revoked", "character", str(ch.id), {"receipts": n}, ip=client_ip(request))
    db.commit()
    return {"revoked": n}


@router.delete("/characters/{character_id}")
def delete(character_id: uuid.UUID, request: Request, user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    """Deletes the character for future use: listing removed, grants revoked, reference images deleted.
    Settled royalties and ledger rows stay (accounting retention)."""
    from datetime import UTC, datetime

    from ..models import CharacterAsset
    from ..services.storage import get_storage

    ch = _own(db, user, character_id)
    n = character_market.takedown(db, user, ch, "deleted by the creator")
    ch.status, ch.deleted_at = "deleted", datetime.now(UTC)
    st = get_storage()
    for a in db.execute(select(CharacterAsset).where(CharacterAsset.character_id == ch.id)).scalars():
        if a.storage_key:
            st.delete(a.storage_key)
            a.storage_key = None
    audit(db, user.id, "character.deleted", "character", str(ch.id), {"grants_revoked": n}, ip=client_ip(request))
    db.commit()
    return {"id": str(ch.id), "status": ch.status}


# ---------------------------------------------------------------- project cast + casting director

class CastIn(BaseModel):
    character_id: uuid.UUID | None = None
    mention: str | None = Field(default=None, max_length=70)
    alias: str | None = Field(default=None, max_length=32)
    lock_mode: str = "standard"
    identity_version_id: uuid.UUID | None = None
    allowed_variants: list[str] = Field(default_factory=list, max_length=12)


@router.post("/studio/projects/{project_id}/cast", status_code=201)
def add_cast(project_id: uuid.UUID, body: CastIn, request: Request, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    p = studio.get_project(db, user, project_id)
    m = casting.add(db, user, p, body.character_id, body.mention, body.alias, body.lock_mode,
                    body.identity_version_id, body.allowed_variants)
    audit(db, user.id, "cast.added", "studio_project", str(p.id),
          {"character_id": str(m.character_id), "identity_version_id": str(m.identity_version_id),
           "lock_mode": m.lock_mode}, ip=client_ip(request))
    db.commit()
    return casting.member_out(db, m)


@router.get("/studio/projects/{project_id}/cast")
def list_cast(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = studio.get_project(db, user, project_id)
    return {"items": [casting.member_out(db, m) for m in casting.members(db, p)]}


@router.delete("/studio/projects/{project_id}/cast/{member_id}")
def remove_cast(project_id: uuid.UUID, member_id: uuid.UUID, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    p = studio.get_project(db, user, project_id)
    casting.remove(db, p, member_id)
    db.commit()
    return {"removed": str(member_id)}


class ScriptIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/studio/projects/{project_id}/resolve-script")
def resolve_script(project_id: uuid.UUID, body: ScriptIn, user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    characters.require_enabled(db)
    p = studio.get_project(db, user, project_id)
    return casting.resolve_script(db, user, p, body.text)


class CastingIn(BaseModel):
    roles: list[casting.Role] = Field(min_length=1, max_length=8)
    per_role: int = Field(default=3, ge=1, le=10)


@router.post("/studio/projects/{project_id}/casting")
def casting_director(project_id: uuid.UUID, body: CastingIn, user: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    p = studio.get_project(db, user, project_id)
    return casting.suggest(db, user, p, body.roles, body.per_role)


@router.get("/studio/projects/{project_id}/identity-report")
def identity_report(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = studio.get_project(db, user, project_id)
    return {"items": studio.shot_identity(db, p),
            "note": "identity consistency is measured per shot, not guaranteed; proxy metrics are never 'verified'"}


# ---------------------------------------------------------------- marketplace + licences

class ListingIn(BaseModel):
    visibility: str = "public"
    terms: dict = Field(default_factory=dict)


@router.post("/characters/{character_id}/listing", status_code=201)
def publish(character_id: uuid.UUID, body: ListingIn, request: Request, user: User = Depends(current_user),
            db: Session = Depends(get_db)):
    ch = _own(db, user, character_id)
    lst = character_market.publish(db, user, ch, body.visibility, body.terms)
    audit(db, user.id, "character.listing_submitted", "character", str(ch.id),
          {"terms_version": lst.terms_version, "visibility": lst.visibility}, ip=client_ip(request))
    db.commit()
    return _listing_out(lst)


@router.delete("/characters/{character_id}/listing")
def unpublish(character_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ch = _own(db, user, character_id)
    lst = character_market.unpublish(db, user, ch)
    db.commit()
    return _listing_out(lst)


def _listing_out(lst) -> dict:
    return {"id": str(lst.id), "character_id": str(lst.character_id), "status": lst.status,
            "visibility": lst.visibility, "terms": lst.terms, "terms_version": lst.terms_version,
            "certification": lst.certification, "review_note": lst.review_note}


class ReportIn(BaseModel):
    reason: str = Field(pattern=r"^(impersonation|lookalike|ip_infringement|minor|sexual|other)$")
    details: str | None = Field(default=None, max_length=1000)


@router.post("/characters/{character_id}/report", status_code=201)
def report(character_id: uuid.UUID, body: ReportIn, user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    ratelimit.hit("character_report", str(user.id), 20)
    ch = db.get(Character, character_id)
    if ch is None or ch.deleted_at is not None:
        raise not_found("character")
    r = character_market.report(db, user, ch, body.reason, body.details)
    db.commit()
    return {"id": str(r.id), "status": r.status}


class LicenseQuoteIn(BaseModel):
    character_id: uuid.UUID
    seconds: int = Field(default=8, ge=1, le=600)
    scenes: int = Field(default=1, ge=1, le=100)


@router.post("/licenses/quote")
def license_quote(body: LicenseQuoteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ch = db.get(Character, body.character_id)
    if ch is None:
        raise not_found("character")
    return character_market.quote(db, user, ch, body.seconds, body.scenes)


class GrantIn(BaseModel):
    character_id: uuid.UUID
    accept_terms: bool = False
    terms_version: int | None = None


@router.post("/licenses/grant", status_code=201)
def license_grant(body: GrantIn, request: Request, user: User = Depends(current_user),
                  idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                  db: Session = Depends(get_db)):
    ratelimit.hit("character_license", str(user.id), 20)
    ch = db.get(Character, body.character_id)
    if ch is None:
        raise not_found("character")
    g = character_market.grant(db, user, ch, body.accept_terms, body.terms_version, _idem(idempotency_key))
    audit(db, user.id, "character.licensed", "character", str(ch.id),
          {"grant_id": str(g.id), "terms_version": g.terms_version}, ip=client_ip(request))
    db.commit()
    return _grant_out(g)


def _grant_out(g: CharacterLicenseGrant) -> dict:
    return {"id": str(g.id), "character_id": str(g.character_id), "status": g.status, "terms": g.terms_snapshot,
            "terms_version": g.terms_version, "starts_at": g.starts_at.isoformat(), "ends_at": g.ends_at.isoformat(),
            "revoke_reason": g.revoke_reason}


@router.get("/creator/characters/analytics")
def creator_analytics(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return character_market.analytics(db, user)


# ---------------------------------------------------------------- admin moderation

class AdminReviewIn(BaseModel):
    decision: str = Field(pattern=r"^(approve|reject|pause)$")
    note: str = Field(min_length=3, max_length=300)


@router.post("/admin/characters/{character_id}/review")
def admin_review(character_id: uuid.UUID, body: AdminReviewIn, request: Request, admin: User = Depends(admin_user),
                 db: Session = Depends(get_db)):
    ch = db.get(Character, character_id)
    if ch is None:
        raise not_found("character")
    lst = character_market.review(db, admin, ch, body.decision, body.note)
    audit(db, admin.id, f"character.listing_{body.decision}", "character", str(ch.id), {"note": body.note},
          ip=client_ip(request))
    db.commit()
    return _listing_out(lst)


class TakedownIn(BaseModel):
    note: str = Field(min_length=3, max_length=300)


@router.post("/admin/characters/{character_id}/takedown")
def admin_takedown(character_id: uuid.UUID, body: TakedownIn, request: Request, admin: User = Depends(admin_user),
                   db: Session = Depends(get_db)):
    ch = db.get(Character, character_id)
    if ch is None:
        raise not_found("character")
    n = character_market.takedown(db, admin, ch, body.note)
    audit(db, admin.id, "character.takedown", "character", str(ch.id), {"note": body.note, "grants_revoked": n},
          ip=client_ip(request))
    db.commit()
    return {"id": str(ch.id), "status": ch.status, "grants_revoked": n}
