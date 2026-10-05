import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import current_user
from ..errors import ApiError, not_found
from ..models import AssetStatus, IdentityAsset, IdentityProfile, ProfileStatus, User
from ..schemas import AssetOut, ProfileCreateIn, ProfileOut, UploadSignIn, UploadSignOut
from ..services.generation import audit
from ..services.storage import PHOTO_MIMES, VIDEO_MIMES, get_storage, mime_compatible, sniff_mime

router = APIRouter(tags=["identity"])

CONSENT_VERSION = "likeness-v1"
MAX_PROFILES_PER_USER = 3


def _own_profile(db: Session, user: User, profile_id: uuid.UUID, lock: bool = False) -> IdentityProfile:
    q = select(IdentityProfile).where(IdentityProfile.id == profile_id)
    if lock:
        q = q.with_for_update()
    p = db.execute(q).scalar_one_or_none()
    if p is None or p.user_id != user.id or p.deleted_at is not None:
        raise not_found("identity profile")
    return p


def _out(db: Session, p: IdentityProfile) -> ProfileOut:
    assets = [a for a in p.assets if a.deleted_at is None]
    out = ProfileOut.model_validate(p, from_attributes=True)
    out.assets = [AssetOut.model_validate(a) for a in assets]
    first = next((a for a in assets if a.status == AssetStatus.accepted and a.kind == "photo"), None)
    out.thumbnail_url = get_storage().presign_get(first.storage_key) if first else None
    return out


@router.post("/identity-profiles", response_model=ProfileOut, status_code=201)
def create_profile(body: ProfileCreateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not body.consent_own_likeness:
        raise ApiError(422, "consent_required", "you must confirm you are the person in the photos")
    n = db.execute(select(func.count()).select_from(IdentityProfile).where(
        IdentityProfile.user_id == user.id, IdentityProfile.deleted_at.is_(None))).scalar_one()
    if n >= MAX_PROFILES_PER_USER:
        raise ApiError(409, "profile_limit", "profile limit reached")
    p = IdentityProfile(user_id=user.id, name=body.name, consent_at=datetime.now(UTC),
                        consent_version=CONSENT_VERSION)
    db.add(p)
    db.flush()
    audit(db, user.id, "identity.create", "identity_profile", str(p.id), {"consent_version": CONSENT_VERSION})
    db.commit()
    db.refresh(p)
    return _out(db, p)


@router.get("/identity-profiles", response_model=list[ProfileOut])
def list_profiles(user: User = Depends(current_user), db: Session = Depends(get_db)):
    ps = db.execute(select(IdentityProfile).where(IdentityProfile.user_id == user.id,
                                                  IdentityProfile.deleted_at.is_(None))
                    .order_by(IdentityProfile.created_at)).scalars().all()
    return [_out(db, p) for p in ps]


@router.get("/identity-profiles/{profile_id}", response_model=ProfileOut)
def get_profile(profile_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _out(db, _own_profile(db, user, profile_id))


@router.post("/uploads/sign", response_model=UploadSignOut)
def sign_upload(body: UploadSignIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = get_settings()
    p = _own_profile(db, user, body.profile_id, lock=True)
    if body.mime in PHOTO_MIMES:
        kind, limit = "photo", s.max_photo_bytes
    elif body.mime in VIDEO_MIMES:
        kind, limit = "video", s.max_video_bytes
    else:
        raise ApiError(415, "unsupported_media_type", "use JPEG, PNG, WEBP, HEIC, MP4 or MOV")
    if body.size_bytes > limit:
        raise ApiError(413, "file_too_large", f"max {limit // (1024 * 1024)} MB")
    count = db.execute(select(func.count()).select_from(IdentityAsset).where(
        IdentityAsset.profile_id == p.id, IdentityAsset.deleted_at.is_(None),
        IdentityAsset.status != AssetStatus.rejected)).scalar_one()
    if count >= s.max_assets_per_profile:
        raise ApiError(409, "asset_limit", f"max {s.max_assets_per_profile} files per profile")
    asset_id = uuid.uuid4()
    # Server-chosen key: clients can never pick or overwrite another user's object.
    key = f"users/{user.id}/identity/{p.id}/{asset_id}"
    db.add(IdentityAsset(id=asset_id, profile_id=p.id, user_id=user.id, kind=kind, storage_key=key,
                         mime=body.mime, declared_size=body.size_bytes))
    db.commit()
    post = get_storage().presign_upload(key, body.mime, limit)
    return UploadSignOut(asset_id=asset_id, upload_url=post.url, fields=post.fields, expires_in=post.expires_in)


@router.post("/identity-profiles/{profile_id}/assets/{asset_id}/complete", response_model=ProfileOut)
def complete_upload(profile_id: uuid.UUID, asset_id: uuid.UUID, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    p = _own_profile(db, user, profile_id, lock=True)
    a = db.get(IdentityAsset, asset_id)
    if a is None or a.profile_id != p.id or a.deleted_at is not None:
        raise not_found("asset")
    if a.status == AssetStatus.pending_upload:
        storage = get_storage()
        head = storage.head(a.storage_key)
        if head is None:
            raise ApiError(409, "upload_missing", "file not uploaded yet")
        sniffed = sniff_mime(storage.read_head_bytes(a.storage_key, 32))
        a.size_bytes = head["size"]
        if not mime_compatible(a.mime, sniffed):
            a.status, a.rejection_reason = AssetStatus.rejected, "file content does not match its type"
            storage.delete(a.storage_key)
        else:
            # Face/quality checks (single face, sharpness, lighting, no minors) run on the GPU
            # preprocessing step; here we accept structurally valid media.
            a.status = AssetStatus.accepted
        _refresh_profile_status(db, p)
        db.commit()
    db.refresh(p)
    return _out(db, p)


def _refresh_profile_status(db: Session, p: IdentityProfile) -> None:
    s = get_settings()
    accepted = db.execute(select(IdentityAsset).where(IdentityAsset.profile_id == p.id,
                                                      IdentityAsset.status == AssetStatus.accepted,
                                                      IdentityAsset.deleted_at.is_(None))).scalars().all()
    photos = sum(1 for a in accepted if a.kind == "photo")
    videos = sum(1 for a in accepted if a.kind == "video")
    ready = photos >= s.min_photos_per_profile or videos >= 1
    p.quality_report = {**(p.quality_report or {}), "photos": photos, "videos": videos,
                        "min_photos": s.min_photos_per_profile}
    if p.status in (ProfileStatus.draft, ProfileStatus.ready):
        p.status = ProfileStatus.ready if ready else ProfileStatus.draft


@router.delete("/identity-profiles/{profile_id}/assets/{asset_id}", status_code=204)
def delete_asset(profile_id: uuid.UUID, asset_id: uuid.UUID, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    p = _own_profile(db, user, profile_id, lock=True)
    a = db.get(IdentityAsset, asset_id)
    if a is None or a.profile_id != p.id or a.deleted_at is not None:
        raise not_found("asset")
    get_storage().delete(a.storage_key)
    a.status, a.deleted_at = AssetStatus.deleted, datetime.now(UTC)
    _refresh_profile_status(db, p)
    db.commit()


@router.delete("/identity-profiles/{profile_id}", status_code=204)
def delete_profile(profile_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Hard-deletes biometric source media immediately; the row is kept (soft-deleted) only so
    past generation jobs keep referential integrity."""
    p = _own_profile(db, user, profile_id, lock=True)
    get_storage().delete_prefix(f"users/{user.id}/identity/{p.id}/")
    now = datetime.now(UTC)
    for a in p.assets:
        a.status, a.deleted_at = AssetStatus.deleted, now
    p.status, p.deleted_at, p.quality_report = ProfileStatus.deleted, now, {}
    audit(db, user.id, "identity.delete", "identity_profile", str(p.id))
    db.commit()
