"""Rights & consent (spec §3 face swap, §10). Real-person likeness requires a recorded, reviewed,
unrevoked RightsGrant. Revocation propagates: characters are blocked and published episodes using them
are taken down pending review."""

import hashlib
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import admin_user, current_user
from ..errors import AppError
from ..models import AuditEvent, Character, Episode, ModerationCase, OutboxEvent, RightsGrant, User, now

router = APIRouter(tags=["rights"])

CONSENT_TEXT_V1 = (
    "I confirm I am the person shown (or hold a written licence from them), I authorise this creator to use "
    "my face and/or voice to create AI-generated fictional content on this platform within the scope below, "
    "I understand content will be labelled as AI-generated, and I can revoke this permission at any time."
)


def active_grant_for(db: Session, ch: Character) -> RightsGrant | None:
    if not ch.rights_grant_id:
        return None
    g = db.get(RightsGrant, ch.rights_grant_id)
    if not g or g.review_status != "approved" or g.revoked_at or (g.expires_at and g.expires_at < now()):
        return None
    if g.grantee_id != ch.owner_id:
        return None
    return g


class GrantIn(BaseModel):
    subject_name: str = Field(min_length=2, max_length=120)
    grant_type: str = Field(pattern="^(self|licensed_actor|authorized_upload)$")
    face: bool = True
    voice: bool = False
    series_id: str | None = None
    consent_text_version: str = "v1"
    consent_accepted: bool
    evidence_asset_ids: list[str] = Field(default_factory=list)  # signed release, ID selfie video, ...
    expires_at: datetime | None = None


@router.get("/rights/consent-text")
def consent_text():
    return {"version": "v1", "text": CONSENT_TEXT_V1}


@router.post("/rights/grants", status_code=201)
def create_grant(body: GrantIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not body.consent_accepted:
        raise AppError("rights.consent_not_accepted", "Consent must be explicitly accepted", 400)
    if body.grant_type != "self" and not body.evidence_asset_ids:
        raise AppError("rights.evidence_required", "Licensed/authorised likeness needs evidence (signed release)", 400)
    g = RightsGrant(grantee_id=user.id, subject_name=body.subject_name, grant_type=body.grant_type,
                    subject_user_id=user.id if body.grant_type == "self" else None,
                    scope={"face": body.face, "voice": body.voice, "series_id": body.series_id},
                    evidence={"consent_text_version": body.consent_text_version,
                              "consent_text_sha256": hashlib.sha256(CONSENT_TEXT_V1.encode()).hexdigest(),
                              "asset_ids": body.evidence_asset_ids, "accepted_at": now().isoformat()},
                    expires_at=body.expires_at)
    db.add(g)
    db.flush()
    # every real-likeness grant gets human review (spec: manual review for sensitive use)
    db.add(ModerationCase(target_type="rights_grant", target_id=g.id, source="rights", priority=20))
    db.add(AuditEvent(actor_id=user.id, action="rights.grant_created", target_type="rights_grant", target_id=g.id))
    db.commit()
    return _out(g)


@router.get("/rights/grants")
def my_grants(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [_out(g) for g in db.scalars(select(RightsGrant).where(RightsGrant.grantee_id == user.id))]


@router.post("/rights/grants/{gid}/revoke")
def revoke(gid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    g = db.get(RightsGrant, gid)
    if not g or (g.grantee_id != user.id and g.subject_user_id != user.id and user.role != "admin"):
        raise AppError("not_found", "Grant not found", 404)
    return _out(revoke_grant(db, g, user.id))


def revoke_grant(db: Session, g: RightsGrant, actor_id: str) -> RightsGrant:
    if g.revoked_at:
        return g
    g.revoked_at = now()
    chars = db.scalars(select(Character).where(Character.rights_grant_id == g.id)).all()
    taken_down = []
    for ch in chars:
        ch.blocked_reason = "rights_revoked"
        eps = db.scalars(select(Episode).where(Episode.series_id == ch.series_id,
                                               Episode.status.in_(["published", "approved", "in_review"]))).all()
        for ep in eps:
            if ch.id in [c.get("id") for c in (ep.provenance or {}).get("characters", {}).values()]:
                ep.status = "taken_down"
                taken_down.append(ep.id)
                db.add(ModerationCase(target_type="episode", target_id=ep.id, source="rights", priority=10,
                                      notes="likeness rights revoked"))
    db.add(OutboxEvent(type="RightsRevoked", dedup_key=f"RightsRevoked:{g.id}",
                       payload={"grant_id": g.id, "episodes_taken_down": taken_down}))
    db.add(AuditEvent(actor_id=actor_id, action="rights.revoked", target_type="rights_grant", target_id=g.id,
                      data={"episodes_taken_down": taken_down}))
    db.commit()
    return g


@router.post("/admin/rights/grants/{gid}/review")
def review(gid: str, approve: bool, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    g = db.get(RightsGrant, gid)
    if not g:
        raise AppError("not_found", "Grant not found", 404)
    g.review_status, g.reviewed_by = ("approved" if approve else "rejected"), admin.id
    db.add(AuditEvent(actor_id=admin.id, action=f"rights.{g.review_status}", target_type="rights_grant", target_id=g.id))
    db.commit()
    return _out(g)


def _out(g: RightsGrant) -> dict:
    return {"id": g.id, "subject_name": g.subject_name, "grant_type": g.grant_type, "scope": g.scope,
            "review_status": g.review_status, "revoked_at": g.revoked_at, "expires_at": g.expires_at}
