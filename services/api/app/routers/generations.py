import uuid

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import current_user
from ..errors import ApiError
from ..models import GenerationJob, GenerationOutput, JobKind, JobStatus, Report, Template, User
from ..schemas import GenerationIn, GenerationOut, OutputOut, ReportIn
from ..services import generation as gen
from ..services import ratelimit
from ..services.storage import get_storage

router = APIRouter(prefix="/generations", tags=["generations"])


def to_out(db: Session, job: GenerationJob) -> GenerationOut:
    out = GenerationOut(
        id=job.id, status=job.status.value, progress=round(job.progress, 3), template_id=job.template_id,
        kind=job.kind.value,
        queue_class=job.queue_class, credit_cost=job.credit_cost, refunded=job.refunded,
        error_code=job.error_code, error_message=job.error_message, created_at=job.created_at,
        finished_at=job.finished_at,
    )
    if job.status == JobStatus.queued:
        out.queue_position = db.execute(select(func.count()).select_from(GenerationJob).where(
            GenerationJob.status == JobStatus.queued, GenerationJob.queue_class == job.queue_class,
            GenerationJob.created_at < job.created_at)).scalar_one() + 1
    tpl = db.get(Template, job.template_id) if job.template_id else None
    est = tpl.est_seconds if tpl else int(job.spec.get("est_seconds", 300))
    if job.status not in (JobStatus.completed, JobStatus.failed, JobStatus.cancelled):
        out.est_seconds_remaining = int(est * (1 - job.progress)) + 20 * (out.queue_position or 0)
    if job.status == JobStatus.completed:
        o = db.execute(select(GenerationOutput).where(GenerationOutput.job_id == job.id,
                                                      GenerationOutput.deleted_at.is_(None))).scalar_one_or_none()
        if o:
            st = get_storage()
            out.output = OutputOut(video_url=st.presign_get(o.video_key),
                                   thumbnail_url=st.presign_get(o.thumbnail_key) if o.thumbnail_key else None,
                                   width=o.width, height=o.height, duration_ms=o.duration_ms,
                                   watermarked=o.watermarked)
    return out


@router.post("", response_model=GenerationOut, status_code=201)
def create(body: GenerationIn, response: Response, user: User = Depends(current_user),
           idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=80),
           db: Session = Depends(get_db)):
    replay = db.execute(select(GenerationJob.id).where(GenerationJob.user_id == user.id,
                                                       GenerationJob.idempotency_key == idempotency_key)).first()
    if replay is None:  # network retries of the same request aren't throttled
        ratelimit.hit("generate", str(user.id), get_settings().rl_generation_per_min)
    res = gen.create_job(db, user, body.template_id, body.profile_id, body.text, idempotency_key)
    db.commit()
    if not res.created:
        response.status_code = 200
    return to_out(db, res.job)


@router.get("", response_model=list[GenerationOut])
def list_mine(limit: int = 30, user: User = Depends(current_user), db: Session = Depends(get_db)):
    jobs = db.execute(select(GenerationJob).where(GenerationJob.user_id == user.id,
                                                  GenerationJob.kind != JobKind.analysis)
                      .order_by(GenerationJob.created_at.desc()).limit(min(limit, 100))).scalars().all()
    return [to_out(db, j) for j in jobs]


@router.get("/{job_id}", response_model=GenerationOut)
def get_one(job_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return to_out(db, gen.get_user_job(db, user, job_id))


@router.post("/{job_id}/cancel", response_model=GenerationOut)
def cancel(job_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = gen.cancel_by_user(db, user, job_id)
    db.commit()
    return to_out(db, job)


@router.post("/{job_id}/report", status_code=201)
def report(job_id: uuid.UUID, body: ReportIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = gen.get_user_job(db, user, job_id)
    dup = db.execute(select(Report).where(Report.job_id == job.id, Report.reporter_id == user.id,
                                          Report.status == "open")).scalar_one_or_none()
    if dup:
        return {"id": str(dup.id), "status": dup.status}
    r = Report(reporter_id=user.id, job_id=job.id, template_id=job.template_id, reason=body.reason,
               details=body.details)
    db.add(r)
    db.commit()
    return {"id": str(r.id), "status": r.status}


@router.post("/{job_id}/retry", response_model=GenerationOut, status_code=201)
def retry(job_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """One-tap retry of a failed job: a new job with a deterministic idempotency key."""
    old = gen.get_user_job(db, user, job_id)
    if old.status != JobStatus.failed:
        raise ApiError(409, "not_failed", "only failed generations can be retried")
    if old.kind == JobKind.multi_replace:
        from ..services import multiperson as mp

        assigns = [(a["track_id"], uuid.UUID(a["profile_id"])) for a in old.spec.get("assignments", [])]
        job, _ = mp.create_replace_job(db, user, old.source_video_id, assigns,
                                       old.spec.get("resolution", "720x1280"), bool(old.spec.get("preview")),
                                       f"retry:{old.id}")
        db.commit()
        return to_out(db, job)
    if old.kind != JobKind.template:
        raise ApiError(409, "not_retryable", "this job can't be retried")
    res = gen.create_job(db, user, old.template_id, old.profile_id, old.user_text, f"retry:{old.id}")
    db.commit()
    return to_out(db, res.job)
