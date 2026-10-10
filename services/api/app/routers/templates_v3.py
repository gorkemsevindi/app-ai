"""Template V3 endpoints: ranked feed, estimate, multi-slot remix, and the admin ingestion console API."""

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import admin_user, client_ip, current_user
from ..errors import ApiError, not_found
from ..models import GenerationJob, SourceVideo, Template, TemplateVersion, User
from ..services import credits, ratelimit
from ..services import multiperson as mp
from ..services import templates_v3 as tv3
from ..services.generation import audit
from ..services.storage import get_storage
from .generations import to_out

router = APIRouter(tags=["templates-v3"])


# ---------------------------------------------------------------- consumer

@router.get("/feed")
def get_feed(category: str | None = None, locale: str | None = None, limit: int = 30,
             db: Session = Depends(get_db)):
    """Server-ranked discovery feed (weights live in the `ranking` remote config, never in clients)."""
    return {"category": category or "trending", "items": tv3.feed(db, category, max(1, min(limit, 60)), locale)}


class EstimateIn(BaseModel):
    template_id: uuid.UUID
    slots: list[str] = Field(default_factory=list, description="slot ids the user intends to fill")
    resolution: str = "720x1280"
    preview: bool = False


@router.post("/generations/estimate")
def estimate(body: EstimateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = db.get(Template, body.template_id)
    if t is None or not tv3.is_usable(t):
        raise not_found("template")
    v = tv3.current_version(db, t)
    slots = tv3.slots_of(db, v)
    if tv3.is_remix(v, slots):
        known = {s.slot_id for s in slots}
        n = len([s for s in body.slots if s in known]) or 1
        q = tv3.quote(db, t, v, n, body.resolution, body.preview)
    else:
        n = 1
        q = {"credits": t.credit_cost, "breakdown": {"base": t.credit_cost}, "confirm_required": False}
    bal = credits.balance(db, user.id)
    return {**q, "template_id": str(t.id), "mode": "remix" if slots else "single", "persons": n,
            "est_seconds": t.est_seconds, "balance": bal, "sufficient": bal >= q["credits"]}


class SlotAssignmentIn(BaseModel):
    slot_id: str = Field(min_length=1, max_length=32)
    profile_id: uuid.UUID


class RemixIn(BaseModel):
    template_id: uuid.UUID
    assignments: list[SlotAssignmentIn] = Field(min_length=1, max_length=16)
    resolution: str = "720x1280"
    preview: bool = False
    confirmed_credits: int | None = Field(default=None, description="price the user saw and accepted")


@router.post("/generations/remix", status_code=201)
def remix(body: RemixIn, response: Response, user: User = Depends(current_user),
          idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=80),
          db: Session = Depends(get_db)):
    replay = db.execute(select(GenerationJob.id).where(GenerationJob.user_id == user.id,
                                                       GenerationJob.idempotency_key == idempotency_key)).first()
    if replay is None:
        ratelimit.hit("generate", str(user.id), get_settings().rl_generation_per_min)
        t = db.get(Template, body.template_id)
        if t is not None and tv3.is_usable(t):
            q = tv3.quote(db, t, tv3.current_version(db, t), len(body.assignments), body.resolution, body.preview)
            if q["confirm_required"] and body.confirmed_credits != q["credits"]:
                raise ApiError(409, "confirmation_required", "confirm the price before generating",
                               {"credits": q["credits"]})
    job, created = tv3.create_remix_job(
        db, user, body.template_id, [tv3.SlotAssignment(a.slot_id, a.profile_id) for a in body.assignments],
        body.resolution, body.preview, idempotency_key)
    db.commit()
    if not created:
        response.status_code = 200
    out = to_out(db, job).model_dump()
    out["kind"] = job.kind.value
    out["template_version_id"] = str(job.template_version_id) if job.template_version_id else None
    return out


# ---------------------------------------------------------------- admin ingestion console

class SourceVideoIn(BaseModel):
    mime: str
    size_bytes: int = Field(gt=0)
    rights: dict = Field(description='{"basis": "first_party|licensed|creator_owned|public_domain", '
                                     '"evidence": ["contract-123"], "notes": "..."}')


@router.post("/admin/templates/{template_id}/source-video", status_code=201)
def admin_source_video(template_id: uuid.UUID, body: SourceVideoIn, request: Request,
                       admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    t = db.get(Template, template_id)
    if t is None or t.deleted_at is not None:
        raise not_found("template")
    v = tv3.attach_source_video(db, admin, t, body.mime, body.size_bytes, body.rights)
    audit(db, admin.id, "template.rights_attested", "template", str(t.id),
          {"basis": body.rights.get("basis"), "source_video_id": str(v.id)}, ip=client_ip(request))
    db.commit()
    post = get_storage().presign_upload(v.storage_key, v.mime, mp.config(db)["max_upload_mb"] * 1024 * 1024)
    return {"source_video_id": str(v.id), "upload_url": post.url, "fields": post.fields,
            "expires_in": post.expires_in}


@router.post("/admin/templates/{template_id}/source-video/{video_id}/complete")
def admin_complete_source(template_id: uuid.UUID, video_id: uuid.UUID, admin: User = Depends(admin_user),
                          db: Session = Depends(get_db)):
    v = db.execute(select(SourceVideo).where(SourceVideo.id == video_id).with_for_update()).scalar_one_or_none()
    if v is None or v.analysis.get("template_id") != str(template_id):
        raise not_found("source video")
    tv3.complete_source_upload(db, admin, v)
    db.commit()
    return admin_source_status(template_id, video_id, admin, db)


@router.get("/admin/templates/{template_id}/source-video/{video_id}")
def admin_source_status(template_id: uuid.UUID, video_id: uuid.UUID, admin: User = Depends(admin_user),
                        db: Session = Depends(get_db)):
    v = db.get(SourceVideo, video_id)
    if v is None or v.analysis.get("template_id") != str(template_id):
        raise not_found("source video")
    st = get_storage()
    return {"id": str(v.id), "status": v.status.value, "rejection_reason": v.rejection_reason,
            "duration_ms": v.duration_ms, "flags": v.analysis.get("flags", []),
            "tracks": [{"track_id": p.track_id, "selectable": p.selectable, "flags": p.flags,
                        "coverage": round(p.coverage, 3), "median_face_px": p.median_face_px,
                        "thumbnail_url": st.presign_get(p.thumbnail_key) if p.thumbnail_key else None}
                       for p in v.persons]}


class SlotIn(BaseModel):
    slot_id: str = Field(pattern=r"^[a-z0-9_-]{1,32}$")
    track_id: int = Field(ge=1)
    label: str | None = Field(default=None, max_length=60)
    required: bool = False
    requirements: dict = {}


class VersionV3In(BaseModel):
    source_video_id: uuid.UUID
    slots: list[SlotIn] = Field(min_length=1)
    prompt_recipe: str = Field(default="replace the selected people, keep motion, camera, lighting", min_length=10)
    preferred_model: str = "dreamid_v_mp"
    fallback_model: str | None = "wan22_animate_mp"
    config: dict = {}
    credit_rule: dict = {}


@router.post("/admin/templates/{template_id}/versions-v3", status_code=201)
def admin_create_version(template_id: uuid.UUID, body: VersionV3In, request: Request,
                         admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    t = db.get(Template, template_id)
    if t is None or t.deleted_at is not None:
        raise not_found("template")
    v = tv3.create_version(db, admin, t, body.source_video_id, [s.model_dump() for s in body.slots],
                           body.prompt_recipe, body.preferred_model, body.fallback_model, body.config,
                           body.credit_rule)
    audit(db, admin.id, "template.version_created", "template", str(t.id),
          {"version": v.version, "slots": [s.slot_id for s in body.slots]}, ip=client_ip(request))
    db.commit()
    return {"id": str(v.id), "version": v.version, "slots": tv3.slot_out(db, v)}


class PublishIn(BaseModel):
    version_id: uuid.UUID
    visibility: str = "public"


@router.post("/admin/templates/{template_id}/publish")
def admin_publish(template_id: uuid.UUID, body: PublishIn, request: Request, admin: User = Depends(admin_user),
                  db: Session = Depends(get_db)):
    t = db.get(Template, template_id)
    if t is None or t.deleted_at is not None:
        raise not_found("template")
    tv3.publish(db, admin, t, body.version_id, body.visibility)
    audit(db, admin.id, "template.published", "template", str(t.id),
          {"version_id": str(body.version_id), "visibility": body.visibility}, ip=client_ip(request))
    db.commit()
    v = db.get(TemplateVersion, body.version_id)
    return {"id": str(t.id), "visibility": t.visibility, "moderation_status": t.moderation_status,
            "current_version": v.version}


class ModerationIn(BaseModel):
    status: str = Field(pattern=r"^(approved|rejected|review)$")
    visibility: str | None = Field(default=None, pattern=r"^(draft|private|unlisted|public|blocked)$")
    note: str = Field(min_length=3, max_length=300)


@router.post("/admin/templates/{template_id}/moderation")
def admin_moderate(template_id: uuid.UUID, body: ModerationIn, request: Request, admin: User = Depends(admin_user),
                   db: Session = Depends(get_db)):
    """Takedown / review: rejected or blocked templates disappear from feed, detail and new remixes at once;
    jobs already created keep their version id for traceability."""
    t = db.get(Template, template_id)
    if t is None:
        raise not_found("template")
    t.moderation_status = body.status
    if body.visibility:
        t.visibility = body.visibility
    audit(db, admin.id, "template.moderation", "template", str(t.id), body.model_dump(), ip=client_ip(request))
    db.commit()
    return {"id": str(t.id), "moderation_status": t.moderation_status, "visibility": t.visibility}


@router.post("/admin/templates/metrics/refresh")
def admin_refresh_metrics(day: date | None = None, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    n = tv3.refresh_metrics(db, day or datetime.now(UTC).date())
    db.commit()
    return {"templates": n}
