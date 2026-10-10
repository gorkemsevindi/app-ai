"""Generation job lifecycle: create (debit) -> durable queue -> lease/heartbeat -> complete | fail
(refund). The queue lives in PostgreSQL (`SELECT ... FOR UPDATE SKIP LOCKED`), so a Redis or
worker loss can never lose a paid job or double-charge it. See docs/adr/0002-durable-queue.md."""

import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import ApiError, not_found
from ..models import (
    TERMINAL_STATUSES,
    AssetStatus,
    AuditLog,
    FeatureFlag,
    GenerationJob,
    GenerationOutput,
    IdentityAsset,
    IdentityProfile,
    JobKind,
    JobStatus,
    ModelRun,
    ModerationAction,
    ProfileStatus,
    Template,
    TemplateVersion,
    User,
)
from . import credits, learning, moderation
from .storage import get_storage

_LEARNING_LISTENER = learning._capture_terminal_jobs  # importing learning registers its job-outcome flush hook

ACTIVE_STATUSES = {JobStatus.preprocessing, JobStatus.generating, JobStatus.postprocessing, JobStatus.moderation}

ALLOWED_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.queued: {JobStatus.preprocessing, JobStatus.cancelled, JobStatus.failed},
    JobStatus.preprocessing: {JobStatus.generating, JobStatus.failed, JobStatus.cancelled, JobStatus.queued},
    JobStatus.generating: {JobStatus.postprocessing, JobStatus.failed, JobStatus.cancelled, JobStatus.queued},
    JobStatus.postprocessing: {JobStatus.moderation, JobStatus.failed, JobStatus.cancelled, JobStatus.queued},
    JobStatus.moderation: {JobStatus.completed, JobStatus.failed, JobStatus.queued},
    JobStatus.completed: set(),
    JobStatus.failed: set(),
    JobStatus.cancelled: set(),
}

_PIPELINE_ORDER = [JobStatus.queued, JobStatus.preprocessing, JobStatus.generating, JobStatus.postprocessing,
                   JobStatus.moderation]

FALLBACK_AFTER_S = 120  # a job may go to its fallback model once it has waited this long


def now() -> datetime:
    return datetime.now(UTC)


def transition(job: GenerationJob, to: JobStatus) -> None:
    if to == job.status:
        return
    if to not in ALLOWED_TRANSITIONS[job.status]:
        raise ApiError(409, "invalid_transition", f"{job.status.value} -> {to.value}")
    job.status = to
    if to in TERMINAL_STATUSES:
        job.finished_at = now()
        job.lease_owner = None
        job.lease_expires_at = None


def queue_class_for(user: User) -> str:
    if user.plan == "pro" and (user.plan_expires_at is None or user.plan_expires_at > now()):
        return "paid_high"
    return "free"


def _refund(db: Session, job: GenerationJob, why: str) -> None:
    credits.release(db, job, why)
    if job.kind == JobKind.studio_shot and (job.spec or {}).get("character_usage"):
        from . import character_market

        character_market.on_job_released(db, job)  # no royalty for refunded usage


def _disabled_models(db: Session) -> set[str]:
    flags = db.execute(select(FeatureFlag).where(FeatureFlag.key.like("model_disabled:%"),
                                                 FeatureFlag.enabled.is_(True))).scalars()
    return {f.key.split(":", 1)[1] for f in flags}


# ---------------------------------------------------------------- create

@dataclass
class CreateResult:
    job: GenerationJob
    created: bool


def create_job(db: Session, user: User, template_id: uuid.UUID, profile_id: uuid.UUID | None,
               user_text: str | None, idempotency_key: str) -> CreateResult:
    s = get_settings()
    existing = db.execute(select(GenerationJob).where(GenerationJob.user_id == user.id,
                                                      GenerationJob.idempotency_key == idempotency_key)
                          ).scalar_one_or_none()
    if existing:
        if existing.template_id != template_id or existing.profile_id != profile_id:
            raise ApiError(409, "idempotency_conflict", "idempotency key reused with different parameters")
        return CreateResult(existing, False)

    tpl = db.get(Template, template_id)
    if tpl is None or not tpl.is_active or tpl.deleted_at is not None or tpl.current_version_id is None:
        raise not_found("template")
    if tpl.pro_only and queue_class_for(user) != "paid_high":
        raise ApiError(402, "pro_required", "this template requires Pro")
    ver = db.get(TemplateVersion, tpl.current_version_id)
    assert ver is not None
    tag_decision = moderation.check_template_tags(tpl.safety_tags)
    if not tag_decision.allowed:
        raise not_found("template")

    if user_text and not tpl.accepts_text:
        user_text = None
    text_decision = moderation.check_text(user_text)
    if not text_decision.allowed:
        db.add(ModerationAction(target_user_id=user.id, source="auto_input", action="block_input",
                                category=text_decision.category, note="generation text blocked"))
        db.commit()
        # Blocked before any debit -> no credit consumed.
        raise ApiError(422, "content_blocked", "this text is not allowed", {"category": text_decision.category})

    if profile_id is not None:
        profile = db.get(IdentityProfile, profile_id)
        if profile is None or profile.user_id != user.id or profile.deleted_at is not None:
            raise not_found("identity profile")
        if profile.status != ProfileStatus.ready:
            raise ApiError(409, "profile_not_ready", "identity profile is not ready")
    else:
        raise ApiError(422, "profile_required", "an identity profile is required")

    # Serialize per user: concurrency cap + debit happen under the same row lock.
    credits.lock_user(db, user.id)
    active = db.execute(select(func.count()).select_from(GenerationJob).where(
        GenerationJob.user_id == user.id, GenerationJob.kind != JobKind.analysis,
        GenerationJob.status.notin_([s.value for s in TERMINAL_STATUSES]))).scalar_one()
    if active >= s.max_active_jobs_per_user:
        raise ApiError(429, "too_many_active_jobs", "wait for your current videos to finish")
    depth = db.execute(select(func.count()).select_from(GenerationJob).where(
        GenerationJob.status == JobStatus.queued)).scalar_one()
    if depth >= s.global_queue_hard_limit:
        raise ApiError(503, "queue_full", "we're very busy right now, please try again in a few minutes")

    qc = queue_class_for(user)
    job = GenerationJob(
        id=uuid.uuid4(), user_id=user.id, profile_id=profile_id, template_id=tpl.id,
        template_version_id=tpl.current_version_id, status=JobStatus.queued, queue_class=qc,
        kind=JobKind.template, preferred_model=ver.preferred_model, fallback_model=ver.fallback_model,
        user_text=user_text, idempotency_key=idempotency_key, credit_cost=tpl.credit_cost,
        max_attempts=s.job_max_attempts, watermark=(qc == "free"),
    )
    db.add(job)
    db.flush()
    credits.reserve(db, job)
    return CreateResult(job, True)


def get_user_job(db: Session, user: User, job_id: uuid.UUID) -> GenerationJob:
    job = db.get(GenerationJob, job_id)
    if job is None or job.user_id != user.id:
        raise not_found("generation")
    return job


def cancel_by_user(db: Session, user: User, job_id: uuid.UUID) -> GenerationJob:
    job = get_user_job(db, user, job_id)
    job = db.execute(select(GenerationJob).where(GenerationJob.id == job.id).with_for_update()).scalar_one()
    if job.status in TERMINAL_STATUSES:
        return job
    if job.status == JobStatus.queued:
        transition(job, JobStatus.cancelled)
        _refund(db, job, "cancelled_by_user")
    elif job.status == JobStatus.moderation:
        raise ApiError(409, "too_late_to_cancel", "the video is almost ready")
    else:
        job.cancel_requested = True  # worker sees it on next heartbeat
    return job


# ---------------------------------------------------------------- worker side

def _weighted_class_order() -> list[str]:
    weights = dict(get_settings().queue_weights)
    order: list[str] = []
    while weights:
        total = sum(weights.values())
        r = random.uniform(0, total)
        acc = 0.0
        for k, w in list(weights.items()):
            acc += w
            if r <= acc:
                order.append(k)
                del weights[k]
                break
    return order


def spend_today(db: Session) -> float:
    start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    return float(db.execute(select(func.coalesce(func.sum(ModelRun.est_cost_usd), 0.0))
                            .where(ModelRun.started_at >= start)).scalar_one())


def reap_expired_leases(db: Session, limit: int = 100) -> int:
    expired = db.execute(
        select(GenerationJob).where(GenerationJob.status.in_([s.value for s in ACTIVE_STATUSES]),
                                    GenerationJob.lease_expires_at < now())
        .limit(limit).with_for_update(skip_locked=True)).scalars().all()
    for job in expired:
        run = db.execute(select(ModelRun).where(ModelRun.job_id == job.id, ModelRun.attempt == job.attempts)
                         ).scalar_one_or_none()
        if run and run.status == "running":
            run.status, run.finished_at, run.error = "lost", now(), "lease expired"
        _requeue_or_fail(db, job, "worker_lost", "worker stopped responding")
    return len(expired)


def _on_terminal_failure(db: Session, job: GenerationJob) -> None:
    if job.kind == JobKind.analysis and job.source_video_id:
        from . import multiperson

        multiperson.mark_analysis_failed(db, job)
    if job.studio_project_id is not None:
        from . import studio

        studio.on_job_failed(db, job)
    if job.kind == JobKind.character_asset:
        from . import characters

        characters.on_asset_failed(db, job)


def refuse_dispatch(db: Session, job: GenerationJob, code: str, message: str) -> None:
    """A claimed job whose authorization no longer holds (e.g. consent revoked): fail final + release credits."""
    _close_run(db, job, "failed", {}, error=code)
    job.error_code, job.error_message = code, message
    transition(job, JobStatus.failed)
    _refund(db, job, code)
    _on_terminal_failure(db, job)


def _requeue_or_fail(db: Session, job: GenerationJob, code: str, message: str) -> None:
    if job.cancel_requested:
        transition(job, JobStatus.cancelled)
        _refund(db, job, "cancelled_by_user")
        return
    if job.attempts < job.max_attempts:
        transition(job, JobStatus.queued)
        job.lease_owner, job.lease_expires_at, job.progress = None, None, 0.0
        job.error_code, job.error_message = code, message
    else:
        job.error_code, job.error_message = code, message
        transition(job, JobStatus.failed)
        _refund(db, job, code)
        _on_terminal_failure(db, job)


def claim(db: Session, worker_id: str, models: list[str]) -> GenerationJob | None:
    s = get_settings()
    reap_expired_leases(db)
    usable = set(models) - _disabled_models(db)
    if not usable:
        return None
    spent = spend_today(db)
    classes = _weighted_class_order()
    if spent >= s.gpu_daily_budget_usd * 1.5:
        return None  # hard ceiling: stop all GPU spend, jobs wait (and are visible as queued)
    if spent >= s.gpu_daily_budget_usd:
        classes = [c for c in classes if c != "free"]  # soft ceiling: only paying users

    disabled = _disabled_models(db)
    fallback_cutoff = now() - timedelta(seconds=FALLBACK_AFTER_S)
    model_ok = or_(
        GenerationJob.preferred_model.in_(usable),
        and_(GenerationJob.fallback_model.in_(usable),
             or_(GenerationJob.preferred_model.in_(disabled or {"__none__"}),
                 GenerationJob.created_at < fallback_cutoff)),
    )
    for qc in classes:
        job = db.execute(
            select(GenerationJob)
            .where(GenerationJob.status == JobStatus.queued, GenerationJob.queue_class == qc, model_ok)
            .order_by(GenerationJob.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if job is None:
            continue
        model = job.preferred_model if job.preferred_model in usable else job.fallback_model
        transition(job, JobStatus.preprocessing)
        job.attempts += 1
        job.lease_owner = worker_id
        job.lease_expires_at = now() + timedelta(seconds=s.job_lease_s)
        job.model_used = model
        job.started_at = job.started_at or now()
        db.add(ModelRun(job_id=job.id, attempt=job.attempts, worker_id=worker_id, model=model))
        db.flush()
        return job
    return None


def _locked_leased_job(db: Session, job_id: uuid.UUID, worker_id: str, attempt: int) -> GenerationJob:
    job = db.execute(select(GenerationJob).where(GenerationJob.id == job_id).with_for_update()).scalar_one_or_none()
    if job is None:
        raise not_found("job")
    if job.lease_owner != worker_id or job.attempts != attempt or job.status not in ACTIVE_STATUSES:
        # Stale worker (lease expired and job was re-queued / finished): it must drop the work.
        raise ApiError(409, "lease_lost", "lease no longer held")
    return job


def heartbeat(db: Session, job_id: uuid.UUID, worker_id: str, attempt: int, status: JobStatus | None,
              progress: float | None) -> GenerationJob:
    job = _locked_leased_job(db, job_id, worker_id, attempt)
    if status and status != job.status:
        if status not in (JobStatus.generating, JobStatus.postprocessing):
            raise ApiError(422, "bad_status", "workers may only report generating/postprocessing")
        transition(job, status)
    if progress is not None:
        job.progress = max(job.progress, min(1.0, max(0.0, progress)))
    job.lease_expires_at = now() + timedelta(seconds=get_settings().job_lease_s)
    return job


def complete(db: Session, job_id: uuid.UUID, worker_id: str, attempt: int, output: dict,
             moderation_report: dict, metrics: dict) -> GenerationJob:
    job = _locked_leased_job(db, job_id, worker_id, attempt)
    _close_run(db, job, "succeeded", metrics)
    # Workers may skip intermediate heartbeats; walk the linear path so every hop is validated.
    for step in (JobStatus.generating, JobStatus.postprocessing, JobStatus.moderation):
        if _PIPELINE_ORDER.index(job.status) < _PIPELINE_ORDER.index(step):
            transition(job, step)

    if job.kind == JobKind.analysis:
        from . import multiperson

        multiperson.apply_analysis(db, job, output)
        job.progress = 1.0
        transition(job, JobStatus.completed)
        return job

    if job.kind == JobKind.character_asset:
        return _complete_character_asset(db, job, output, moderation_report)
    if job.kind == JobKind.editor_render:
        return _complete_editor_render(db, job, output, moderation_report)

    storage = get_storage()
    video_key = output_key(job, "video.mp4")
    head = storage.head(video_key)
    if head is None or head["size"] <= 0:
        _requeue_or_fail(db, job, "output_missing", "worker reported success but no video was stored")
        return job

    decision = moderation.check_output(moderation_report)
    if not decision.allowed:
        db.add(ModerationAction(job_id=job.id, target_user_id=job.user_id, source="auto_output",
                                action="block_output", category=decision.category, note=",".join(decision.reasons)))
        storage.delete(video_key)
        job.error_code, job.error_message = "output_blocked", "the result did not pass our safety checks"
        transition(job, JobStatus.failed)
        _refund(db, job, "output_blocked")
        return job

    if job.kind == JobKind.studio_shot and (job.spec or {}).get("identity_gate"):
        from . import studio

        code = studio.identity_gate(db, job, metrics)
        if code:  # strict character lock: not delivered, refunded; bounded retry or explicit failure
            storage.delete(video_key)
            job.error_code, job.error_message = code, "the character identity check did not pass"
            transition(job, JobStatus.failed)
            _refund(db, job, code)
            studio.on_identity_failed(db, job)
            return job

    thumb_key = output_key(job, "thumb.jpg")
    db.add(GenerationOutput(
        job_id=job.id, video_key=video_key,
        thumbnail_key=thumb_key if storage.head(thumb_key) else None,
        width=int(output.get("width", 720)), height=int(output.get("height", 1280)),
        duration_ms=int(output.get("duration_ms", 5000)), size_bytes=int(head["size"]),
        codec=str(output.get("codec", "h264")), watermarked=job.watermark,
        provenance={"ai_generated": True, "model": job.model_used, "job_id": str(job.id),
                    "template_id": str(job.template_id) if job.template_id else None,
                    "template_version_id": str(job.template_version_id) if job.template_version_id else None,
                    "source_video_id": str(job.source_video_id) if job.source_video_id else None,
                    "c2pa": bool(output.get("c2pa")), "generated_at": now().isoformat()},
    ))
    job.progress = 1.0
    job.error_code = job.error_message = None
    transition(job, JobStatus.completed)
    credits.settle(db, job)
    if job.billing_state == "settled":
        from . import character_market, creators

        character_market.on_job_settled(db, job)  # V6 waterfall: contractual royalties first
        creators.on_job_settled(db, job)
    if job.studio_project_id is not None:
        from . import studio

        studio.on_job_completed(db, job, video_key, int(output.get("duration_ms", 5000)))
    return job


def _complete_character_asset(db: Session, job: GenerationJob, output: dict, report: dict) -> GenerationJob:
    from . import characters

    decision = moderation.check_output(report)
    if not decision.allowed:
        db.add(ModerationAction(job_id=job.id, target_user_id=job.user_id, source="auto_output",
                                action="block_output", category=decision.category, note=",".join(decision.reasons)))
        get_storage().delete(characters.asset_key(job))
        job.error_code, job.error_message = "output_blocked", "the image did not pass our safety checks"
        transition(job, JobStatus.failed)
        _refund(db, job, "output_blocked")
        characters.on_asset_failed(db, job)
        return job
    err = characters.on_asset_completed(db, job, output)
    if err:
        _requeue_or_fail(db, job, err, "worker reported success but no image was stored")
        return job
    job.progress = 1.0
    job.error_code = job.error_message = None
    transition(job, JobStatus.completed)
    credits.settle(db, job)
    return job


def _complete_editor_render(db: Session, job: GenerationJob, output: dict, report: dict) -> GenerationJob:
    from . import editor

    key = editor.output_key(job)
    head = get_storage().head(key)
    if head is None or head["size"] <= 0:
        _requeue_or_fail(db, job, "output_missing", "worker reported success but no file was stored")
        return job
    decision = moderation.check_output(report)
    if not decision.allowed:
        db.add(ModerationAction(job_id=job.id, target_user_id=job.user_id, source="auto_output",
                                action="block_output", category=decision.category, note=",".join(decision.reasons)))
        get_storage().delete(key)
        job.error_code, job.error_message = "output_blocked", "the export did not pass our safety checks"
        transition(job, JobStatus.failed)
        _refund(db, job, "output_blocked")
        return job
    job.progress = 1.0
    job.error_code = job.error_message = None
    transition(job, JobStatus.completed)
    credits.settle(db, job)
    return job


def fail(db: Session, job_id: uuid.UUID, worker_id: str, attempt: int, code: str, message: str,
         retryable: bool, metrics: dict | None = None) -> GenerationJob:
    job = _locked_leased_job(db, job_id, worker_id, attempt)
    _close_run(db, job, "failed", metrics or {}, error=f"{code}: {message}"[:500])
    if code == "cancelled" and job.cancel_requested:
        transition(job, JobStatus.cancelled)
        _refund(db, job, "cancelled_by_user")
    elif retryable:
        _requeue_or_fail(db, job, code, message[:500])
    else:
        job.error_code, job.error_message = code, message[:500]
        transition(job, JobStatus.failed)
        _refund(db, job, code)
        _on_terminal_failure(db, job)
    return job


def _close_run(db: Session, job: GenerationJob, status: str, metrics: dict, error: str | None = None) -> None:
    run = db.execute(select(ModelRun).where(ModelRun.job_id == job.id, ModelRun.attempt == job.attempts)
                     ).scalar_one_or_none()
    if run is None:
        return
    run.status, run.finished_at, run.error = status, now(), error
    run.gpu_seconds = metrics.get("gpu_seconds")
    run.est_cost_usd = metrics.get("est_cost_usd")
    run.gpu_provider = metrics.get("gpu_provider")
    run.gpu_type = metrics.get("gpu_type")
    run.metrics = {k: v for k, v in metrics.items()
                   if k not in {"gpu_seconds", "est_cost_usd", "gpu_provider", "gpu_type"}}


def output_key(job: GenerationJob, name: str) -> str:
    return f"users/{job.user_id}/outputs/{job.id}/{name}"


def compile_prompt(ver: TemplateVersion, user_text: str | None) -> str:
    """Template recipes may contain {user_text}. User text is already moderated; braces are
    stripped so it cannot inject further placeholders."""
    safe = (user_text or "").replace("{", "").replace("}", "").strip()
    recipe = ver.prompt_recipe
    if "{user_text}" in recipe:
        return recipe.replace("{user_text}", safe)
    return recipe if not safe else f"{recipe} {safe}"


def build_worker_payload(db: Session, job: GenerationJob) -> dict:
    if job.kind in (JobKind.studio_shot, JobKind.studio_assemble):
        from . import studio

        return studio.build_payload(db, job)
    if job.kind == JobKind.character_asset:
        from . import characters

        return characters.build_payload(db, job)
    if job.kind == JobKind.editor_render:
        from . import editor

        return editor.build_payload(db, job)
    if job.kind != JobKind.template:
        from . import multiperson

        return multiperson.build_payload(db, job)
    ver = db.get(TemplateVersion, job.template_version_id)
    tpl = db.get(Template, job.template_id)
    assert ver is not None and tpl is not None
    storage = get_storage()
    assets = db.execute(select(IdentityAsset).where(IdentityAsset.profile_id == job.profile_id,
                                                    IdentityAsset.status == AssetStatus.accepted)
                        .order_by(IdentityAsset.created_at)).scalars().all()
    src_key = (ver.params or {}).get("source_clip_key")
    return {
        "source_video_url": storage.presign_get(src_key, 3600) if src_key else None,
        "job_id": str(job.id),
        "attempt": job.attempts,
        "model": job.model_used,
        "capability": ver.model_capability,
        "prompt": compile_prompt(ver, job.user_text),
        "negative_prompt": ver.negative_prompt,
        "params": ver.params,
        "duration_s": tpl.duration_s,
        "aspect_ratio": tpl.aspect_ratio,
        "watermark": job.watermark,
        "lease_s": get_settings().job_lease_s,
        "identity_assets": [{"id": str(a.id), "kind": a.kind, "mime": a.mime,
                             "url": storage.presign_get(a.storage_key, 3600)} for a in assets],
        "upload": {
            "video": {"key": output_key(job, "video.mp4"),
                      "url": storage.presign_put(output_key(job, "video.mp4"), "video/mp4")},
            "thumbnail": {"key": output_key(job, "thumb.jpg"),
                          "url": storage.presign_put(output_key(job, "thumb.jpg"), "image/jpeg")},
        },
    }


def audit(db: Session, actor_id, action: str, target_type: str | None = None, target_id: str | None = None,
          data: dict | None = None, ip: str | None = None) -> None:
    db.add(AuditLog(actor_id=actor_id, action=action, target_type=target_type, target_id=target_id,
                    data=data or {}, ip=ip))
