"""Creator marketplace API (V4 Stage D): onboarding, template submission, earnings/analytics; admin revenue
policies, settlements, risk holds, creator status. Gated by the `creator_marketplace` flag."""

import secrets
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import admin_user, client_ip, current_user, staff_user
from ..errors import ApiError, not_found
from ..models import (
    CreatorEarning,
    CreatorProfile,
    CreatorRiskHold,
    FeatureFlag,
    RevenuePolicy,
    Settlement,
    SourceVideo,
    Template,
    TemplateVersion,
    User,
)
from ..services import creators, ratelimit
from ..services import templates_v3 as tv3
from ..services.generation import audit
from ..services.storage import get_storage

router = APIRouter(tags=["creators"])
FLAG = "creator_marketplace"


def _enabled(db: Session) -> None:
    f = db.get(FeatureFlag, FLAG)
    if not (f and f.enabled):
        raise ApiError(403, "feature_disabled", "the creator marketplace is not available yet")


def _profile_out(p: CreatorProfile, private: bool = False) -> dict:
    out = {"handle": p.handle, "display_name": p.display_name, "bio": p.bio, "status": p.status}
    if private:
        out.update({"payout_country": p.payout_country, "payout_status": p.payout_status,
                    "terms_version": p.terms_version})
    return out


class ProfileIn(BaseModel):
    handle: str = Field(pattern=r"^[a-zA-Z0-9_]{3,32}$")
    display_name: str = Field(min_length=1, max_length=60)
    bio: str = Field(default="", max_length=300)
    payout_country: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    accept_terms: bool = False


@router.post("/creator/profile", status_code=201)
def become_creator(body: ProfileIn, request: Request, user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    _enabled(db)
    p = creators.create_profile(db, user, body.handle, body.display_name, body.bio, body.payout_country,
                                body.accept_terms)
    audit(db, user.id, "creator.onboarded", "creator", str(user.id), {"terms": p.terms_version},
          ip=client_ip(request))
    db.commit()
    return _profile_out(p, private=True)


@router.get("/creator/profile")
def my_profile(user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = creators.get_profile(db, user.id)
    if p is None:
        raise not_found("creator profile")
    return _profile_out(p, private=True)


@router.get("/creators/{handle}")
def public_profile(handle: str, db: Session = Depends(get_db)):
    p = db.execute(select(CreatorProfile).where(CreatorProfile.handle == handle.lower())).scalar_one_or_none()
    if p is None or p.status != "active":
        raise not_found("creator")
    ts = db.execute(select(Template).where(Template.creator_id == p.user_id)).scalars().all()
    cards = []
    for t in ts:
        if tv3.is_listed(t):
            v = tv3.current_version(db, t)
            cards.append(tv3.card(db, t, v, len(tv3.slots_of(db, v))))
    return {**_profile_out(p), "templates": cards}


# ---------------------------------------------------------------- creator templates

class CreatorTemplateIn(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(default="", max_length=1000)
    category: str = Field(pattern=r"^[a-z_]{3,40}$")


def _own_template(db: Session, user: User, template_id: uuid.UUID) -> Template:
    t = db.get(Template, template_id)
    if t is None or t.creator_id != user.id or t.deleted_at is not None:
        raise not_found("template")
    return t


@router.post("/creator/templates", status_code=201)
def create_template(body: CreatorTemplateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _enabled(db)
    p = creators.require_profile(db, user)
    ratelimit.hit("creator_template", str(user.id), 10)
    from ..services import moderation

    for text in (body.title, body.description):
        if not moderation.check_text(text).allowed:
            raise ApiError(422, "content_blocked", "this text is not allowed")
    _, cfg = tv3.remix_config(db)
    t = Template(slug=f"{p.handle}-{secrets.token_hex(3)}", title=body.title, description=body.description,
                 category=body.category, credit_cost=int(cfg["pricing"].get("base", 30)), is_active=False,
                 creator_id=user.id, visibility="draft", moderation_status="pending")
    db.add(t)
    db.commit()
    return {"id": str(t.id), "slug": t.slug, "moderation_status": t.moderation_status, "visibility": t.visibility}


@router.get("/creator/templates")
def my_templates(user: User = Depends(current_user), db: Session = Depends(get_db)):
    ts = db.execute(select(Template).where(Template.creator_id == user.id, Template.deleted_at.is_(None))
                    .order_by(Template.created_at.desc())).scalars().all()
    return {"items": [{"id": str(t.id), "title": t.title, "moderation_status": t.moderation_status,
                       "visibility": t.visibility, "submission": t.commercial_rights.get("submission")} for t in ts]}


class CreatorSourceIn(BaseModel):
    mime: str
    size_bytes: int = Field(gt=0)
    basis: str = Field(pattern=r"^(creator_owned|licensed)$")
    evidence: list[str] = Field(min_length=1, max_length=20, description="release/licence references")
    people_consented: bool = False
    notes: str = Field(default="", max_length=500)


@router.post("/creator/templates/{template_id}/source-video", status_code=201)
def source_video(template_id: uuid.UUID, body: CreatorSourceIn, request: Request, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    """Creators must own/licence the clip and confirm everyone visible agreed to its use as a remix template."""
    _enabled(db)
    creators.require_profile(db, user)
    t = _own_template(db, user, template_id)
    if not body.people_consented:
        raise ApiError(422, "consent_required", "confirm that everyone in the clip agreed to this use")
    v = tv3.attach_source_video(db, user, t, body.mime, body.size_bytes,
                                {"basis": body.basis, "evidence": body.evidence, "notes": body.notes,
                                 "people_consented": True, "submitted_by": "creator"})
    audit(db, user.id, "template.rights_attested", "template", str(t.id),
          {"basis": body.basis, "source_video_id": str(v.id), "creator": True}, ip=client_ip(request))
    db.commit()
    from ..services import multiperson as mp

    post = get_storage().presign_upload(v.storage_key, v.mime, mp.config(db)["max_upload_mb"] * 1024 * 1024)
    return {"source_video_id": str(v.id), "upload_url": post.url, "fields": post.fields,
            "expires_in": post.expires_in}


@router.post("/creator/templates/{template_id}/source-video/{video_id}/complete")
def source_complete(template_id: uuid.UUID, video_id: uuid.UUID, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    t = _own_template(db, user, template_id)
    v = db.execute(select(SourceVideo).where(SourceVideo.id == video_id).with_for_update()).scalar_one_or_none()
    if v is None or v.user_id != user.id or v.analysis.get("template_id") != str(t.id):
        raise not_found("source video")
    tv3.complete_source_upload(db, user, v)
    db.commit()
    return source_status(template_id, video_id, user, db)


@router.get("/creator/templates/{template_id}/source-video/{video_id}")
def source_status(template_id: uuid.UUID, video_id: uuid.UUID, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    t = _own_template(db, user, template_id)
    v = db.get(SourceVideo, video_id)
    if v is None or v.user_id != user.id or v.analysis.get("template_id") != str(t.id):
        raise not_found("source video")
    st = get_storage()
    return {"id": str(v.id), "status": v.status.value, "rejection_reason": v.rejection_reason,
            "flags": v.analysis.get("flags", []),
            "tracks": [{"track_id": p.track_id, "selectable": p.selectable, "flags": p.flags,
                        "thumbnail_url": st.presign_get(p.thumbnail_key) if p.thumbnail_key else None}
                       for p in v.persons]}


class CreatorVersionIn(BaseModel):
    source_video_id: uuid.UUID
    slots: list[dict] = Field(min_length=1, max_length=8)
    prompt_recipe: str = Field(default="replace the selected people, keep motion, camera, lighting", min_length=10,
                               max_length=1000)
    config: dict = {}


@router.post("/creator/templates/{template_id}/versions", status_code=201)
def create_version(template_id: uuid.UUID, body: CreatorVersionIn, user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    _enabled(db)
    creators.require_profile(db, user)
    t = _own_template(db, user, template_id)
    from ..services import moderation

    if not moderation.check_text(body.prompt_recipe).allowed:
        raise ApiError(422, "content_blocked", "this text is not allowed")
    _, cfg = tv3.remix_config(db)
    # creators don't pick models or prices: the platform's default remix route and pricing apply
    v = tv3.create_version(db, user, t, body.source_video_id, body.slots, body.prompt_recipe,
                           cfg.get("preferred_model", "dreamid_v_mp"), cfg.get("fallback_model"),
                           {k: body.config[k] for k in ("speaker_mapping",) if k in body.config}, {})
    db.commit()
    return {"id": str(v.id), "version": v.version, "slots": tv3.slot_out(db, v)}


class SubmitIn(BaseModel):
    version_id: uuid.UUID
    visibility: str = Field(default="public", pattern=r"^(public|unlisted)$")


@router.post("/creator/templates/{template_id}/submit")
def submit(template_id: uuid.UUID, body: SubmitIn, request: Request, user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    """Send a version to moderation. It goes live only when an admin publishes it."""
    _enabled(db)
    creators.require_profile(db, user)
    t = _own_template(db, user, template_id)
    v = db.get(TemplateVersion, body.version_id)
    if v is None or v.template_id != t.id:
        raise not_found("version")
    if t.moderation_status == "rejected" and t.commercial_rights.get("submission", {}).get("version_id") == str(v.id):
        raise ApiError(409, "template_rejected", "this version was rejected; create a new version")
    t.commercial_rights = {**t.commercial_rights, "submission": {
        "version_id": str(v.id), "visibility": body.visibility, "at": datetime.now(UTC).isoformat()}}
    t.moderation_status = "review"
    audit(db, user.id, "template.submitted", "template", str(t.id), {"version_id": str(v.id)},
          ip=client_ip(request))
    db.commit()
    return {"id": str(t.id), "moderation_status": t.moderation_status}


# ---------------------------------------------------------------- earnings + analytics

@router.get("/creator/earnings")
def earnings(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if creators.get_profile(db, user.id) is None:
        raise not_found("creator profile")
    rows = db.execute(select(CreatorEarning).where(CreatorEarning.creator_id == user.id)
                      .order_by(CreatorEarning.id.desc()).limit(100)).scalars().all()
    sets = db.execute(select(Settlement).where(Settlement.creator_id == user.id)
                      .order_by(Settlement.created_at.desc()).limit(24)).scalars().all()
    return {"balances": creators.balances(db, user.id),
            "events": [{"kind": e.kind, "amount_micros": e.amount_micros, "policy_version": e.policy_version,
                        "template_id": str(e.template_id) if e.template_id else None,
                        "available_at": e.available_at.isoformat(), "created_at": e.created_at.isoformat()}
                       for e in rows],
            "settlements": [{"id": str(s.id), "amount_micros": s.amount_micros, "status": s.status,
                             "period_end": s.period_end.isoformat(),
                             "paid_at": s.paid_at.isoformat() if s.paid_at else None} for s in sets]}


@router.get("/creator/analytics")
def creator_analytics(days: int = 30, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if creators.get_profile(db, user.id) is None:
        raise not_found("creator profile")
    return creators.analytics(db, user.id, max(1, min(days, 365)))


# ---------------------------------------------------------------- admin

class PolicyIn(BaseModel):
    config: dict
    effective_from: datetime | None = None


@router.post("/admin/revenue-policies", status_code=201)
def create_policy(body: PolicyIn, request: Request, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    p = creators.create_policy(db, admin, body.config, body.effective_from)
    audit(db, admin.id, "revenue_policy.created", "revenue_policy", str(p.id),
          {"version": p.version, "config": p.config}, ip=client_ip(request))
    db.commit()
    return {"id": str(p.id), "version": p.version, "config": p.config, "effective_from": p.effective_from.isoformat()}


@router.get("/admin/revenue-policies")
def list_policies(_: User = Depends(staff_user), db: Session = Depends(get_db)):
    rows = db.execute(select(RevenuePolicy).order_by(RevenuePolicy.version.desc())).scalars().all()
    active = creators.active_policy(db)
    return {"items": [{"id": str(p.id), "version": p.version, "config": p.config,
                       "effective_from": p.effective_from.isoformat(),
                       "active": active is not None and p.id == active.id} for p in rows]}


@router.post("/admin/settlements/run")
def run_settlements(request: Request, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    out = creators.run_settlements(db)
    audit(db, admin.id, "settlements.run", "settlement", None, {"created": len(out)}, ip=client_ip(request))
    db.commit()
    return {"created": [{"id": str(s.id), "creator_id": str(s.creator_id), "amount_micros": s.amount_micros,
                         "status": s.status, "risk": s.risk} for s in out]}


@router.get("/admin/settlements")
def list_settlements(status: str | None = None, _: User = Depends(staff_user), db: Session = Depends(get_db)):
    q = select(Settlement).order_by(Settlement.created_at.desc()).limit(200)
    if status:
        q = q.where(Settlement.status == status)
    return {"items": [{"id": str(s.id), "creator_id": str(s.creator_id), "amount_micros": s.amount_micros,
                       "status": s.status, "risk": s.risk, "external_ref": s.external_ref}
                      for s in db.execute(q).scalars()]}


class SettlementActionIn(BaseModel):
    external_ref: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=300)


@router.post("/admin/settlements/{settlement_id}/{action}")
def settlement_action(settlement_id: uuid.UUID, action: str, body: SettlementActionIn, request: Request,
                      admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    s = creators.get_settlement(db, settlement_id)
    creators.transition_settlement(db, admin, s, action, body.external_ref, body.note)
    audit(db, admin.id, f"settlement.{action}", "settlement", str(s.id),
          {"amount_micros": s.amount_micros, "external_ref": body.external_ref, "note": body.note},
          ip=client_ip(request))
    db.commit()
    return {"id": str(s.id), "status": s.status, "paid_at": s.paid_at.isoformat() if s.paid_at else None}


@router.get("/admin/creators/risk-holds")
def risk_holds(status: str = "open", _: User = Depends(staff_user), db: Session = Depends(get_db)):
    rows = db.execute(select(CreatorRiskHold).where(CreatorRiskHold.status == status)
                      .order_by(CreatorRiskHold.created_at)).scalars().all()
    return {"items": [{"id": str(h.id), "creator_id": str(h.creator_id), "reason": h.reason, "signals": h.signals,
                       "created_at": h.created_at.isoformat()} for h in rows]}


class ResolveIn(BaseModel):
    status: str = Field(pattern=r"^(released|confirmed_fraud)$")
    note: str = Field(min_length=3, max_length=300)


@router.post("/admin/creators/risk-holds/{hold_id}/resolve")
def resolve_hold(hold_id: uuid.UUID, body: ResolveIn, request: Request, admin: User = Depends(admin_user),
                 db: Session = Depends(get_db)):
    h = db.get(CreatorRiskHold, hold_id)
    if h is None or h.status != "open":
        raise not_found("risk hold")
    h.status, h.note, h.resolved_by, h.resolved_at = body.status, body.note, admin.id, datetime.now(UTC)
    if body.status == "confirmed_fraud":
        _set_status(db, h.creator_id, "suspended")
    audit(db, admin.id, "creator.risk_resolved", "creator", str(h.creator_id), body.model_dump(),
          ip=client_ip(request))
    db.commit()
    return {"id": str(h.id), "status": h.status}


def _set_status(db: Session, creator_id: uuid.UUID, status: str) -> None:
    p = db.get(CreatorProfile, creator_id)
    if p is None:
        raise not_found("creator")
    p.status = status
    if status == "suspended":  # out of discovery and no payouts until reviewed
        for t in db.execute(select(Template).where(Template.creator_id == creator_id,
                                                   Template.moderation_status == "approved")).scalars():
            t.moderation_status = "review"
        for s in db.execute(select(Settlement).where(Settlement.creator_id == creator_id,
                                                     Settlement.status.in_(("pending", "approved")))).scalars():
            s.status = "held"


class CreatorStatusIn(BaseModel):
    status: str = Field(pattern=r"^(active|suspended)$")
    note: str = Field(min_length=3, max_length=300)


@router.post("/admin/creators/{creator_id}/status")
def creator_status(creator_id: uuid.UUID, body: CreatorStatusIn, request: Request,
                   admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    _set_status(db, creator_id, body.status)
    audit(db, admin.id, f"creator.{body.status}", "creator", str(creator_id), {"note": body.note},
          ip=client_ip(request))
    db.commit()
    return {"creator_id": str(creator_id), "status": body.status}


class PayoutStatusIn(BaseModel):
    payout_status: str = Field(pattern=r"^(none|kyc_pending|verified|blocked)$")
    kyc_ref: str | None = Field(default=None, max_length=120)


@router.post("/admin/creators/{creator_id}/payout-status")
def payout_status(creator_id: uuid.UUID, body: PayoutStatusIn, request: Request, admin: User = Depends(admin_user),
                  db: Session = Depends(get_db)):
    """Set from the KYC/payout provider's result (manual until a provider is integrated). No KYC data stored."""
    p = db.get(CreatorProfile, creator_id)
    if p is None:
        raise not_found("creator")
    p.payout_status, p.kyc_ref = body.payout_status, body.kyc_ref
    audit(db, admin.id, "creator.payout_status", "creator", str(creator_id), {"payout_status": body.payout_status},
          ip=client_ip(request))
    db.commit()
    return {"creator_id": str(creator_id), "payout_status": p.payout_status}


@router.get("/admin/templates/review-queue")
def review_queue(_: User = Depends(staff_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Template).where(Template.moderation_status == "review")
                      .order_by(Template.updated_at)).scalars().all()
    return {"items": [{"id": str(t.id), "title": t.title, "creator_id": str(t.creator_id) if t.creator_id else None,
                       "rights": t.commercial_rights, "submission": t.commercial_rights.get("submission")}
                      for t in rows]}
