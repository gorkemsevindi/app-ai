"""Admin API (separate authz scope: role=admin, support has read-only subset). Every mutating
action writes an audit log entry. Support staff never receive identity media URLs."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import admin_user, client_ip, staff_user
from ..errors import ApiError, not_found
from ..models import (
    FeatureFlag,
    GenerationJob,
    JobStatus,
    LedgerReason,
    ModelRun,
    ModerationAction,
    Report,
    Template,
    TemplateVersion,
    User,
    UserStatus,
)
from ..services import credits
from ..services.generation import audit

router = APIRouter(prefix="/admin", tags=["admin"])


class TemplateIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]{3,80}$")
    title: str = Field(max_length=120)
    description: str = ""
    category: str
    thumbnail_url: str | None = None
    preview_url: str | None = None
    duration_s: int = Field(default=5, ge=2, le=10)
    aspect_ratio: str = "9:16"
    credit_cost: int = Field(default=10, ge=0, le=500)
    est_seconds: int = 90
    accepts_text: bool = False
    locale_tags: list[str] = []
    safety_tags: list[str] = []
    pro_only: bool = False
    sort_order: int = 100


class TemplatePatch(BaseModel):
    title: str | None = None
    description: str | None = None
    category: str | None = None
    thumbnail_url: str | None = None
    preview_url: str | None = None
    credit_cost: int | None = Field(default=None, ge=0, le=500)
    est_seconds: int | None = None
    is_active: bool | None = None
    pro_only: bool | None = None
    sort_order: int | None = None
    locale_tags: list[str] | None = None
    safety_tags: list[str] | None = None


class VersionIn(BaseModel):
    prompt_recipe: str = Field(min_length=10)
    negative_prompt: str = ""
    model_capability: str = "face_swap_v2v"
    preferred_model: str = "dreamid_v"
    fallback_model: str | None = "wan22_animate_14b"
    params: dict = {}
    activate: bool = True


class FlagIn(BaseModel):
    enabled: bool
    value: dict = {}
    description: str = ""
    public: bool = False


class GrantIn(BaseModel):
    amount: int = Field(ge=-10000, le=10000)
    reason: str = Field(min_length=3, max_length=200)
    campaign: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=80)


class ReportDecisionIn(BaseModel):
    action: str = Field(pattern=r"^(dismiss|remove_output|ban_user|warn)$")
    note: str | None = None


def _tpl_dict(t: Template) -> dict:
    return {c.name: getattr(t, c.name) for c in Template.__table__.columns}


@router.get("/dashboard")
def dashboard(_: User = Depends(staff_user), db: Session = Depends(get_db)):
    now = datetime.now(UTC)
    day = now - timedelta(days=1)
    jobs_24h = db.execute(select(GenerationJob.status, func.count()).where(GenerationJob.created_at >= day)
                          .group_by(GenerationJob.status)).all()
    by_status = {s.value: n for s, n in jobs_24h}
    done = by_status.get("completed", 0)
    failed = by_status.get("failed", 0)
    avg_secs = db.execute(select(func.avg(func.extract("epoch", GenerationJob.finished_at - GenerationJob.created_at)))
                          .where(GenerationJob.status == JobStatus.completed,
                                 GenerationJob.finished_at >= day)).scalar_one()
    cost = db.execute(select(func.coalesce(func.sum(ModelRun.est_cost_usd), 0.0), func.coalesce(
        func.sum(ModelRun.gpu_seconds), 0.0)).where(ModelRun.started_at >= day)).one()
    from ..models import AnalyticsEvent, CreditLedger

    dau = db.execute(select(func.count(func.distinct(AnalyticsEvent.user_id)))
                     .where(AnalyticsEvent.occurred_at >= day)).scalar_one()
    mau = db.execute(select(func.count(func.distinct(AnalyticsEvent.user_id)))
                     .where(AnalyticsEvent.occurred_at >= now - timedelta(days=30))).scalar_one()
    burned = db.execute(select(func.coalesce(func.sum(-CreditLedger.delta), 0)).where(
        CreditLedger.reason == LedgerReason.generation_debit, CreditLedger.created_at >= day)).scalar_one()
    return {
        "users_total": db.execute(select(func.count()).select_from(User).where(User.deleted_at.is_(None))).scalar_one(),
        "dau": dau, "mau": mau,
        "generations_24h": by_status,
        "success_rate_24h": round(done / (done + failed), 4) if done + failed else None,
        "avg_generation_seconds_24h": float(avg_secs) if avg_secs else None,
        "queue_depth": {qc: n for qc, n in db.execute(select(GenerationJob.queue_class, func.count()).where(
            GenerationJob.status == JobStatus.queued).group_by(GenerationJob.queue_class)).all()},
        "gpu_cost_usd_24h": float(cost[0]), "gpu_seconds_24h": float(cost[1]),
        "credits_burned_24h": int(burned),
        "open_reports": db.execute(
            select(func.count()).select_from(Report).where(Report.status == "open")).scalar_one(),
    }


# ---- templates
@router.get("/templates")
def list_templates(_: User = Depends(staff_user), db: Session = Depends(get_db)):
    return [_tpl_dict(t) for t in db.execute(select(Template).where(Template.deleted_at.is_(None))
                                             .order_by(Template.sort_order)).scalars()]


@router.post("/templates", status_code=201)
def create_template(body: TemplateIn, request: Request, admin: User = Depends(admin_user),
                    db: Session = Depends(get_db)):
    if db.execute(select(Template).where(Template.slug == body.slug)).scalar_one_or_none():
        raise ApiError(409, "slug_taken")
    t = Template(**body.model_dump(), is_active=False)
    db.add(t)
    db.flush()
    audit(db, admin.id, "template.create", "template", str(t.id), body.model_dump(), client_ip(request))
    db.commit()
    return _tpl_dict(t)


@router.patch("/templates/{template_id}")
def patch_template(template_id: uuid.UUID, body: TemplatePatch, request: Request,
                   admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    t = db.get(Template, template_id)
    if t is None or t.deleted_at is not None:
        raise not_found("template")
    changes = body.model_dump(exclude_none=True)
    if changes.get("is_active") and t.current_version_id is None:
        raise ApiError(409, "no_version", "add a prompt version before activating")
    for k, v in changes.items():
        setattr(t, k, v)
    audit(db, admin.id, "template.update", "template", str(t.id), changes, client_ip(request))
    db.commit()
    return _tpl_dict(t)


@router.post("/templates/{template_id}/versions", status_code=201)
def add_version(template_id: uuid.UUID, body: VersionIn, request: Request, admin: User = Depends(admin_user),
                db: Session = Depends(get_db)):
    t = db.execute(select(Template).where(Template.id == template_id).with_for_update()).scalar_one_or_none()
    if t is None or t.deleted_at is not None:
        raise not_found("template")
    n = db.execute(select(func.coalesce(func.max(TemplateVersion.version), 0))
                   .where(TemplateVersion.template_id == t.id)).scalar_one()
    v = TemplateVersion(template_id=t.id, version=n + 1, created_by=admin.id,
                        **body.model_dump(exclude={"activate"}))
    db.add(v)
    db.flush()
    if body.activate:
        t.current_version_id = v.id
    audit(db, admin.id, "template.version", "template", str(t.id), {"version": v.version}, client_ip(request))
    db.commit()
    return {"id": str(v.id), "version": v.version, "active": body.activate}


@router.get("/templates/{template_id}/versions")
def list_versions(template_id: uuid.UUID, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    vs = db.execute(select(TemplateVersion).where(TemplateVersion.template_id == template_id)
                    .order_by(TemplateVersion.version.desc())).scalars()
    out = []
    for v in vs:
        stats = db.execute(select(GenerationJob.status, func.count()).where(
            GenerationJob.template_version_id == v.id).group_by(GenerationJob.status)).all()
        out.append({c.name: getattr(v, c.name) for c in TemplateVersion.__table__.columns}
                   | {"stats": {s.value: n for s, n in stats}})
    return out


@router.delete("/templates/{template_id}", status_code=204)
def delete_template(template_id: uuid.UUID, request: Request, admin: User = Depends(admin_user),
                    db: Session = Depends(get_db)):
    t = db.get(Template, template_id)
    if t is None:
        raise not_found("template")
    t.is_active, t.deleted_at = False, datetime.now(UTC)
    audit(db, admin.id, "template.delete", "template", str(t.id), ip=client_ip(request))
    db.commit()


# ---- flags / model routing kill switch
@router.get("/flags")
def list_flags(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    return [{"key": f.key, "enabled": f.enabled, "value": f.value, "description": f.description, "public": f.public}
            for f in db.execute(select(FeatureFlag).order_by(FeatureFlag.key)).scalars()]


@router.put("/flags/{key}")
def put_flag(key: str, body: FlagIn, request: Request, admin: User = Depends(admin_user),
             db: Session = Depends(get_db)):
    f = db.get(FeatureFlag, key) or FeatureFlag(key=key)
    f.enabled, f.value, f.description, f.public = body.enabled, body.value, body.description, body.public
    db.add(f)
    audit(db, admin.id, "flag.put", "feature_flag", key, body.model_dump(), client_ip(request))
    db.commit()
    return {"key": key, **body.model_dump()}


# ---- users / credits
@router.get("/users")
def lookup_user(q: str, _: User = Depends(staff_user), db: Session = Depends(get_db)):
    stmt = select(User)
    try:
        stmt = stmt.where(User.id == uuid.UUID(q))
    except ValueError:
        stmt = stmt.where(func.lower(User.email) == q.lower())
    u = db.execute(stmt).scalar_one_or_none()
    if u is None:
        raise not_found("user")
    jobs = db.execute(select(GenerationJob).where(GenerationJob.user_id == u.id)
                      .order_by(GenerationJob.created_at.desc()).limit(20)).scalars()
    return {
        "id": str(u.id), "email": u.email, "status": u.status.value, "role": u.role.value, "plan": u.plan,
        "country": u.country, "created_at": u.created_at, "credits": credits.balance(db, u.id),
        "ledger": credits.reconcile_user(db, u.id),
        "recent_jobs": [{"id": str(j.id), "status": j.status.value, "error_code": j.error_code,
                         "created_at": j.created_at} for j in jobs],
    }


@router.post("/users/{user_id}/credits")
def grant_credits(user_id: uuid.UUID, body: GrantIn, request: Request, admin: User = Depends(admin_user),
                  db: Session = Depends(get_db)):
    if db.get(User, user_id) is None:
        raise not_found("user")
    reason = LedgerReason.promo if body.amount > 0 else LedgerReason.admin_adjust
    e = credits.apply(db, user_id, body.amount, reason, f"admin:{body.idempotency_key}", ref_type="campaign",
                      ref_id=body.campaign, actor_id=admin.id, note=body.reason)
    audit(db, admin.id, "credits.grant", "user", str(user_id), body.model_dump(), client_ip(request))
    db.commit()
    return {"balance": e.balance_after}


@router.post("/users/{user_id}/ban")
def ban(user_id: uuid.UUID, request: Request, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise not_found("user")
    u.status = UserStatus.banned
    db.add(ModerationAction(target_user_id=u.id, actor_id=admin.id, action="ban"))
    audit(db, admin.id, "user.ban", "user", str(u.id), ip=client_ip(request))
    db.commit()
    return {"status": u.status.value}


# ---- moderation queue
@router.get("/reports")
def list_reports(status: str = "open", _: User = Depends(staff_user), db: Session = Depends(get_db)):
    rs = db.execute(select(Report).where(Report.status == status).order_by(Report.created_at).limit(100)).scalars()
    return [{"id": str(r.id), "job_id": str(r.job_id) if r.job_id else None, "reason": r.reason,
             "details": r.details, "status": r.status, "created_at": r.created_at} for r in rs]


@router.post("/reports/{report_id}/decision")
def decide(report_id: uuid.UUID, body: ReportDecisionIn, request: Request, admin: User = Depends(admin_user),
           db: Session = Depends(get_db)):
    from ..models import GenerationOutput
    from ..services.storage import get_storage

    r = db.get(Report, report_id)
    if r is None:
        raise not_found("report")
    job = db.get(GenerationJob, r.job_id) if r.job_id else None
    if body.action == "remove_output" and job:
        for o in db.execute(select(GenerationOutput).where(GenerationOutput.job_id == job.id)).scalars():
            get_storage().delete(o.video_key)
            o.deleted_at = datetime.now(UTC)
    if body.action == "ban_user" and job:
        u = db.get(User, job.user_id)
        if u:
            u.status = UserStatus.banned
    r.status = "dismissed" if body.action == "dismiss" else "actioned"
    db.add(ModerationAction(report_id=r.id, job_id=r.job_id, target_user_id=job.user_id if job else None,
                            actor_id=admin.id, action=body.action, note=body.note))
    audit(db, admin.id, "report.decision", "report", str(r.id), body.model_dump(), client_ip(request))
    db.commit()
    return {"status": r.status}
