"""In-app account deletion (App Store 5.1.1(v) / Google Play account-deletion policy).

Deletion is immediate for access + biometric media and asynchronous-safe for the rest:
1. user marked `deleting`, sessions revoked, in-flight jobs cancelled (no refund needed: account closes);
2. all objects under users/{id}/ deleted from storage;
3. PII scrubbed from the user row; identity rows soft-deleted.
Financial records (ledger, purchases) are retained in pseudonymized form for tax/fraud
obligations (see docs/SECURITY.md#retention). Backups age out per retention policy."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import client_ip, current_user
from ..models import (
    TERMINAL_STATUSES,
    AnalyticsEvent,
    AssetStatus,
    AuthSession,
    GenerationJob,
    GenerationOutput,
    IdentityAsset,
    IdentityProfile,
    JobStatus,
    ProfileStatus,
    SourceVideo,
    SourceVideoStatus,
    User,
    UserStatus,
)
from ..services.generation import audit
from ..services.storage import get_storage, user_prefix

router = APIRouter(tags=["account"])


def delete_user_data(db: Session, user: User, actor_id=None, ip: str | None = None) -> dict:
    now = datetime.now(UTC)
    user = db.execute(select(User).where(User.id == user.id).with_for_update()).scalar_one()
    user.status = UserStatus.deleting
    db.execute(update(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
               .values(revoked_at=now))
    for job in db.execute(select(GenerationJob).where(
            GenerationJob.user_id == user.id,
            GenerationJob.status.notin_([s.value for s in TERMINAL_STATUSES])).with_for_update()).scalars():
        job.status, job.finished_at, job.cancel_requested = JobStatus.cancelled, now, True
        job.lease_owner = job.lease_expires_at = None
    db.flush()

    removed = get_storage().delete_prefix(user_prefix(user.id))

    db.execute(update(IdentityAsset).where(IdentityAsset.user_id == user.id)
               .values(status=AssetStatus.deleted, deleted_at=now))
    db.execute(update(IdentityProfile).where(IdentityProfile.user_id == user.id)
               .values(status=ProfileStatus.deleted, deleted_at=now, quality_report={}, name="deleted"))
    db.execute(update(SourceVideo).where(SourceVideo.user_id == user.id)
               .values(status=SourceVideoStatus.deleted, deleted_at=now, analysis={}))
    from ..models import AudioAsset, ConsentReceipt, ShareLink, StudioCharacter, StudioProject
    db.execute(update(AudioAsset).where(AudioAsset.user_id == user.id).values(status="deleted", deleted_at=now))
    db.execute(update(ShareLink).where(ShareLink.owner_id == user.id, ShareLink.revoked_at.is_(None))
               .values(revoked_at=now))
    db.execute(update(ConsentReceipt).where(ConsentReceipt.user_id == user.id, ConsentReceipt.revoked_at.is_(None))
               .values(revoked_at=now))
    db.execute(update(StudioCharacter).where(StudioCharacter.user_id == user.id)
               .values(deleted_at=now, identity_profile_id=None, description="", traits={}))
    db.execute(update(StudioProject).where(StudioProject.user_id == user.id).values(deleted_at=now))
    job_ids = select(GenerationJob.id).where(GenerationJob.user_id == user.id)
    db.execute(update(GenerationOutput).where(GenerationOutput.job_id.in_(job_ids)).values(deleted_at=now))
    db.execute(update(GenerationJob).where(GenerationJob.user_id == user.id).values(user_text=None))
    db.execute(update(AnalyticsEvent).where(AnalyticsEvent.user_id == user.id).values(user_id=None, device_id=None))

    user.email = None
    user.password_hash = None
    user.provider_subject = None
    user.display_name = None
    user.acquisition_source = None
    user.status = UserStatus.deleted
    user.deleted_at = now
    audit(db, actor_id or user.id, "account.delete", "user", str(user.id), {"objects_removed": removed}, ip=ip)
    return {"objects_removed": removed}


@router.delete("/account", status_code=202)
def delete_account(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    result = delete_user_data(db, user, ip=client_ip(request))
    db.commit()
    return {"status": "deleted", **result}
