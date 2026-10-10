import uuid

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import current_user
from ..models import GenerationJob, SourceVideo, User
from ..schemas import AudioOptionsIn
from ..services import lipsync, ratelimit
from ..services import multiperson as mp
from ..services.generation import audit
from ..services.storage import get_storage
from .generations import to_out

router = APIRouter(tags=["multi-person"])


class VideoCreateIn(BaseModel):
    mime: str
    size_bytes: int = Field(gt=0)
    owns_rights: bool = Field(description="I own this video or have the rights to use it")
    people_consented: bool = Field(description="Everyone whose face will be replaced agreed to it")


class Assignment(BaseModel):
    track_id: int = Field(ge=1)
    profile_id: uuid.UUID


class QuoteIn(BaseModel):
    assignments: list[Assignment]
    resolution: str = "720x1280"
    preview: bool = False
    audio: AudioOptionsIn | None = None


class MultiGenIn(QuoteIn):
    source_video_id: uuid.UUID


def _video_out(v: SourceVideo) -> dict:
    st = get_storage()
    return {
        "id": str(v.id), "status": v.status.value, "rejection_reason": v.rejection_reason,
        "duration_ms": v.duration_ms, "width": v.width, "height": v.height,
        "persons": [{"track_id": p.track_id, "label": f"Person {p.track_id}", "selectable": p.selectable,
                     "flags": p.flags, "coverage": round(p.coverage, 3),
                     "first_ms": int(p.first_frame / v.fps * 1000) if v.fps else None,
                     "last_ms": int(p.last_frame / v.fps * 1000) if v.fps else None,
                     "thumbnail_url": st.presign_get(p.thumbnail_key) if p.thumbnail_key else None}
                    for p in v.persons],
    }


def _is_replay(db: Session, user: User, key: str) -> bool:
    return db.execute(select(GenerationJob.id).where(GenerationJob.user_id == user.id,
                                                     GenerationJob.idempotency_key == key)).first() is not None


@router.get("/multi-person/config")
def mp_config(db: Session = Depends(get_db)):
    return mp.public_config(db)


@router.post("/source-videos", status_code=201)
def create_video(body: VideoCreateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ratelimit.hit("source-video", str(user.id), 10)
    v = mp.create_video(db, user, body.mime, body.size_bytes, body.owns_rights, body.people_consented)
    audit(db, user.id, "source_video.attest", "source_video", str(v.id),
          {"attestation_version": v.attestation_version})
    db.commit()
    post = get_storage().presign_upload(v.storage_key, v.mime, mp.config(db)["max_upload_mb"] * 1024 * 1024)
    return {"id": str(v.id), "upload_url": post.url, "fields": post.fields, "expires_in": post.expires_in}


@router.post("/source-videos/{video_id}/complete")
def complete_video(video_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = mp.get_own_video(db, user, video_id, lock=True)
    mp.complete_upload(db, user, v)
    db.commit()
    db.refresh(v)
    return _video_out(v)


@router.get("/source-videos/{video_id}")
def get_video(video_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = mp.get_own_video(db, user, video_id)
    out = _video_out(v)
    if v.analysis_job_id:
        j = db.get(GenerationJob, v.analysis_job_id)
        out["analysis_progress"] = j.progress if j else None
    return out


@router.delete("/source-videos/{video_id}", status_code=204)
def delete_video(video_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = mp.get_own_video(db, user, video_id, lock=True)
    mp.delete_video(db, v)
    audit(db, user.id, "source_video.delete", "source_video", str(v.id))
    db.commit()


@router.post("/source-videos/{video_id}/quote")
def quote(video_id: uuid.UUID, body: QuoteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    cfg = mp.require_enabled(db)
    v = mp.get_own_video(db, user, video_id)
    pairs = mp.validate_assignments(db, user, v, cfg, [(a.track_id, a.profile_id) for a in body.assignments])
    ap = mp.audio_plan(db, user, v, pairs, body.audio.to_options() if body.audio else None)
    base = mp.quote(cfg, len(pairs), v.duration_ms or 0, body.resolution, body.preview)
    total = base + (0 if body.preview else ap.extra_credits)
    lipsync.enforce_economics(db, total, ap.est_cost_usd)
    return {"credits": total, "breakdown": {"replacement": base, "lip_sync": 0 if body.preview else ap.extra_credits},
            "persons": len(pairs), "duration_ms": v.duration_ms, "resolution": body.resolution,
            "preview": body.preview, "speaker_mapping": ap.spec.get("speaker_mapping", [])}


@router.post("/generations/multi", status_code=201)
def create_multi(body: MultiGenIn, response: Response, user: User = Depends(current_user),
                 idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=80),
                 db: Session = Depends(get_db)):
    if not _is_replay(db, user, idempotency_key):  # network retries of the same request aren't throttled
        ratelimit.hit("generate", str(user.id), get_settings().rl_generation_per_min)
    job, created = mp.create_replace_job(db, user, body.source_video_id,
                                         [(a.track_id, a.profile_id) for a in body.assignments],
                                         body.resolution, body.preview, idempotency_key,
                                         audio=body.audio.to_options() if body.audio else None)
    db.commit()
    if not created:
        response.status_code = 200
    out = to_out(db, job).model_dump()
    out["kind"] = job.kind.value
    return out


@router.get("/source-videos")
def list_videos(user: User = Depends(current_user), db: Session = Depends(get_db)):
    vs = db.execute(select(SourceVideo).where(SourceVideo.user_id == user.id, SourceVideo.deleted_at.is_(None))
                    .order_by(SourceVideo.created_at.desc()).limit(20)).scalars()
    return [_video_out(v) for v in vs]



@router.get("/source-videos/{video_id}/speakers")
def video_speakers(video_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Who speaks when: suggestions + confidence per segment, for manual correction before lip-sync."""
    v = mp.get_own_video(db, user, video_id)
    return {**lipsync.speakers(v), "min_confidence": lipsync.config(db)[1]["min_mapping_confidence"]}


class AudioCreateIn(BaseModel):
    mime: str
    size_bytes: int = Field(gt=0)
    rights_basis: str = Field(pattern=r"^(own|licensed)$")
    attest_rights: bool = Field(description="I own this audio or hold a licence that covers this use")


@router.post("/audio-assets", status_code=201)
def create_audio(body: AudioCreateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ratelimit.hit("audio", str(user.id), 10)
    a = lipsync.create_audio(db, user, body.mime, body.size_bytes, body.rights_basis, body.attest_rights)
    audit(db, user.id, "audio.attest", "audio_asset", str(a.id), {"basis": body.rights_basis})
    db.commit()
    _, cfg = lipsync.config(db)
    post = get_storage().presign_upload(a.storage_key, a.mime, int(cfg["custom_audio_max_mb"]) * 1024 * 1024)
    return {"id": str(a.id), "upload_url": post.url, "fields": post.fields, "expires_in": post.expires_in}


@router.post("/audio-assets/{audio_id}/complete")
def complete_audio(audio_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = lipsync.complete_audio(db, lipsync.get_own_audio(db, user, audio_id))
    db.commit()
    return {"id": str(a.id), "status": a.status}


@router.delete("/audio-assets/{audio_id}", status_code=204)
def delete_audio(audio_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    lipsync.delete_audio(db, lipsync.get_own_audio(db, user, audio_id))
    audit(db, user.id, "audio.delete", "audio_asset", str(audio_id))
    db.commit()
