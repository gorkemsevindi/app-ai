"""Internal API for GPU workers. Workers authenticate with a worker token and never hold
database or long-lived storage credentials: they get per-job signed URLs. This lets us run
workers on any GPU provider (RunPod, Lambda, cloud, owned) behind the same contract."""

import uuid

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import worker_auth
from ..models import JobStatus
from ..services import generation as gen

router = APIRouter(prefix="/internal/worker", tags=["internal"], dependencies=[Depends(worker_auth)])


class ClaimIn(BaseModel):
    worker_id: str = Field(max_length=120)
    models: list[str]
    gpu_provider: str | None = None
    gpu_type: str | None = None


class HeartbeatIn(BaseModel):
    worker_id: str
    attempt: int
    status: JobStatus | None = None
    progress: float | None = None


class CompleteIn(BaseModel):
    worker_id: str
    attempt: int
    output: dict = {}
    moderation: dict = {}
    metrics: dict = {}


class FailIn(BaseModel):
    worker_id: str
    attempt: int
    error_code: str = Field(max_length=60)
    message: str = ""
    retryable: bool = True
    metrics: dict = {}


@router.post("/claim")
def claim(body: ClaimIn, response: Response, db: Session = Depends(get_db)):
    job = gen.claim(db, body.worker_id, body.models)
    if job is None:
        db.commit()  # persist reaped leases
        response.status_code = 204
        return None
    payload = gen.build_worker_payload(db, job)
    db.commit()
    return payload


@router.post("/jobs/{job_id}/heartbeat")
def heartbeat(job_id: uuid.UUID, body: HeartbeatIn, db: Session = Depends(get_db)):
    job = gen.heartbeat(db, job_id, body.worker_id, body.attempt, body.status, body.progress)
    db.commit()
    return {"cancel": job.cancel_requested, "lease_expires_at": job.lease_expires_at}


@router.post("/jobs/{job_id}/complete")
def complete(job_id: uuid.UUID, body: CompleteIn, db: Session = Depends(get_db)):
    job = gen.complete(db, job_id, body.worker_id, body.attempt, body.output, body.moderation, body.metrics)
    db.commit()
    return {"status": job.status.value}


@router.post("/jobs/{job_id}/fail")
def fail(job_id: uuid.UUID, body: FailIn, db: Session = Depends(get_db)):
    job = gen.fail(db, job_id, body.worker_id, body.attempt, body.error_code, body.message, body.retryable,
                   body.metrics)
    db.commit()
    return {"status": job.status.value}


@router.post("/reap")
def reap(db: Session = Depends(get_db)):
    n = gen.reap_expired_leases(db, limit=500)
    db.commit()
    return {"reaped": n}
