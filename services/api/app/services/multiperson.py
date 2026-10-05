"""Multi-Person Viral Video Replacement (docs/MULTI_PERSON.md). Reuses the generation queue,
lease/retry/refund machinery and model router; adds source videos, person tracks and assignments.
All limits and prices come from the `multi_person` remote-config flag."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import ApiError, not_found
from ..models import (
    TERMINAL_STATUSES,
    FeatureFlag,
    GenerationJob,
    IdentityProfile,
    JobAssignment,
    JobKind,
    JobStatus,
    LedgerReason,
    ModerationAction,
    ProfileStatus,
    SourceVideo,
    SourceVideoStatus,
    User,
    VideoPerson,
)
from . import credits
from .storage import VIDEO_MIMES, get_storage, mime_compatible, sniff_mime

ATTESTATION_VERSION = "video-rights-v1"
FLAG_KEY = "multi_person"
HARD_MAX_PERSONS = 16  # schema/architecture ceiling; the product limit is remote config

DEFAULTS: dict = {
    "max_persons": 4,
    "max_duration_s": 15,
    "min_duration_s": 2,
    "max_upload_mb": 100,
    "resolutions": {"480x832": 1.0, "720x1280": 1.6},
    "pricing": {"base": 10, "per_person_second": 2, "preview": 5},
    "preview_seconds": 2,
    "analysis_model": "mp_analyzer",
    "preferred_model": "dreamid_v_mp",
    "fallback_model": "wan22_animate_mp",
    "force_watermark": True,
    "min_face_px": 64,
    "min_coverage": 0.15,
}

# Analysis flags that reject the whole video (safety) vs. only disable one person.
VIDEO_BLOCK_FLAGS = {"minor_suspected": "minors", "nsfw_source": "sexual", "graphic_violence": "violence"}
PERSON_DISABLE_FLAGS = {"too_small", "face_not_visible", "low_coverage", "minor_suspected"}


@dataclass
class MPConfig:
    enabled: bool
    values: dict

    def __getitem__(self, k: str):
        return self.values[k]


def config(db: Session) -> MPConfig:
    f = db.get(FeatureFlag, FLAG_KEY)
    values = {**DEFAULTS, **((f.value or {}) if f else {})}
    values["max_persons"] = max(1, min(int(values["max_persons"]), HARD_MAX_PERSONS))
    return MPConfig(enabled=bool(f and f.enabled), values=values)


def require_enabled(db: Session) -> MPConfig:
    cfg = config(db)
    if not cfg.enabled:
        raise ApiError(403, "feature_disabled", "multi-person videos are not available yet")
    return cfg


def public_config(db: Session) -> dict:
    cfg = config(db)
    v = cfg.values
    return {"enabled": cfg.enabled, "max_persons": v["max_persons"], "max_duration_s": v["max_duration_s"],
            "min_duration_s": v["min_duration_s"], "resolutions": list(v["resolutions"]), "pricing": v["pricing"],
            "max_upload_mb": v["max_upload_mb"]}


# ---------------------------------------------------------------- pricing

def quote(cfg: MPConfig, persons: int, duration_ms: int, resolution: str, preview: bool) -> int:
    if resolution not in cfg["resolutions"]:
        raise ApiError(422, "bad_resolution", f"allowed: {list(cfg['resolutions'])}")
    p = cfg["pricing"]
    secs = min(math.ceil(duration_ms / 1000), cfg["max_duration_s"])
    if preview:
        return int(p.get("preview", 5))
    raw = p["base"] + p["per_person_second"] * persons * secs
    return int(math.ceil(raw * float(cfg["resolutions"][resolution])))


# ---------------------------------------------------------------- source videos

def get_own_video(db: Session, user: User, video_id: uuid.UUID, lock: bool = False) -> SourceVideo:
    q = select(SourceVideo).where(SourceVideo.id == video_id)
    if lock:
        q = q.with_for_update()
    v = db.execute(q).scalar_one_or_none()
    if v is None or v.user_id != user.id or v.deleted_at is not None:
        raise not_found("video")
    return v


def create_video(db: Session, user: User, mime: str, size_bytes: int, owns_rights: bool,
                 people_consented: bool) -> SourceVideo:
    cfg = require_enabled(db)
    if not (owns_rights and people_consented):
        raise ApiError(422, "attestation_required",
                       "confirm you have the rights to this video and everyone being replaced agreed")
    if mime not in VIDEO_MIMES:
        raise ApiError(415, "unsupported_media_type", "use MP4 or MOV")
    if size_bytes > cfg["max_upload_mb"] * 1024 * 1024:
        raise ApiError(413, "file_too_large", f"max {cfg['max_upload_mb']} MB")
    vid = uuid.uuid4()
    v = SourceVideo(id=vid, user_id=user.id, storage_key=f"users/{user.id}/source_videos/{vid}/original",
                    mime=mime, declared_size=size_bytes, rights_attested_at=datetime.now(UTC),
                    attestation_version=ATTESTATION_VERSION)
    db.add(v)
    db.flush()
    return v


def complete_upload(db: Session, user: User, video: SourceVideo) -> SourceVideo:
    """Validate the uploaded bytes and enqueue the (free) analysis job."""
    cfg = require_enabled(db)
    if video.status != SourceVideoStatus.pending_upload:
        return video
    st = get_storage()
    head = st.head(video.storage_key)
    if head is None:
        raise ApiError(409, "upload_missing", "file not uploaded yet")
    video.size_bytes = head["size"]
    if head["size"] > cfg["max_upload_mb"] * 1024 * 1024 or not mime_compatible(
            video.mime, sniff_mime(st.read_head_bytes(video.storage_key, 32))):
        st.delete(video.storage_key)
        video.status, video.rejection_reason = SourceVideoStatus.rejected, "invalid video file"
        return video
    job = GenerationJob(
        id=uuid.uuid4(), user_id=user.id, kind=JobKind.analysis, source_video_id=video.id,
        status=JobStatus.queued, queue_class="paid_high",  # cheap + interactive: never starve analysis
        idempotency_key=f"analysis:{video.id}", credit_cost=0, max_attempts=get_settings().job_max_attempts,
        preferred_model=cfg["analysis_model"], fallback_model=None, watermark=False,
        spec={"max_persons_detect": cfg["max_persons"] * 2, "max_duration_s": cfg["max_duration_s"],
              "min_face_px": cfg["min_face_px"]},
    )
    db.add(job)
    video.status, video.analysis_job_id = SourceVideoStatus.analyzing, job.id
    return video


def apply_analysis(db: Session, job: GenerationJob, output: dict) -> None:
    """Persist worker analysis: persistent person tracks + safety flags."""
    video = db.execute(select(SourceVideo).where(SourceVideo.id == job.source_video_id).with_for_update()
                       ).scalar_one_or_none()
    if video is None or video.deleted_at is not None:
        return
    cfg = config(db)
    a = output.get("analysis", {})
    video.duration_ms, video.width, video.height = a.get("duration_ms"), a.get("width"), a.get("height")
    video.fps = a.get("fps")
    video.analysis = {"flags": a.get("flags", []), "models": a.get("models", {}), "timing": a.get("timing", {}),
                      "scene_cuts": a.get("scene_cuts", [])}
    for flag, category in VIDEO_BLOCK_FLAGS.items():
        if flag in a.get("flags", []):
            video.status, video.rejection_reason = SourceVideoStatus.rejected, "video not allowed"
            db.add(ModerationAction(target_user_id=video.user_id, source="auto_input", action="block_input",
                                    category=category, note=f"source video {video.id}: {flag}"))
            get_storage().delete(video.storage_key)
            return
    dur_s = (video.duration_ms or 0) / 1000
    if dur_s < cfg["min_duration_s"] or dur_s > cfg["max_duration_s"] + 0.5:
        video.status = SourceVideoStatus.rejected
        video.rejection_reason = f"video must be {cfg['min_duration_s']}-{cfg['max_duration_s']} seconds"
        return
    db.query(VideoPerson).filter(VideoPerson.source_video_id == video.id).delete()
    persons = sorted(a.get("persons", []), key=lambda p: -float(p.get("coverage", 0)))
    if not persons:
        video.status, video.rejection_reason = SourceVideoStatus.rejected, "no people found in this video"
        return
    for idx, p in enumerate(persons[:HARD_MAX_PERSONS], start=1):
        flags = list(p.get("flags", []))
        if int(p.get("median_face_px", 0)) < cfg["min_face_px"]:
            flags.append("too_small")
        if float(p.get("coverage", 0)) < cfg["min_coverage"]:
            flags.append("low_coverage")
        db.add(VideoPerson(
            source_video_id=video.id, track_id=idx, first_frame=int(p["first_frame"]), last_frame=int(p["last_frame"]),
            coverage=float(p.get("coverage", 0)), face_visible_ratio=float(p.get("face_visible_ratio", 0)),
            median_face_px=int(p.get("median_face_px", 0)), thumbnail_key=p.get("thumbnail_key"),
            selectable=not (set(flags) & PERSON_DISABLE_FLAGS), flags=sorted(set(flags)),
            stats={"worker_track_id": p.get("worker_track_id"), "segments": p.get("segments", []),
                   "id_merges": p.get("id_merges", 0)},
        ))
    video.status = SourceVideoStatus.ready


def mark_analysis_failed(db: Session, job: GenerationJob) -> None:
    v = db.get(SourceVideo, job.source_video_id)
    if v and v.status == SourceVideoStatus.analyzing:
        v.status, v.rejection_reason = SourceVideoStatus.failed, "we couldn't analyze this video"


def delete_video(db: Session, video: SourceVideo) -> None:
    st = get_storage()
    st.delete_prefix(f"users/{video.user_id}/source_videos/{video.id}/")
    video.status, video.deleted_at = SourceVideoStatus.deleted, datetime.now(UTC)


# ---------------------------------------------------------------- replacement jobs

def validate_assignments(db: Session, user: User, video: SourceVideo, cfg: MPConfig,
                         assignments: list[tuple[int, uuid.UUID]]) -> list[tuple[VideoPerson, IdentityProfile]]:
    if video.status != SourceVideoStatus.ready:
        raise ApiError(409, "video_not_ready", "video is not ready")
    if not assignments:
        raise ApiError(422, "no_assignments", "pick at least one person")
    if len(assignments) > cfg["max_persons"]:
        raise ApiError(422, "too_many_persons", f"up to {cfg['max_persons']} people")
    tracks = [t for t, _ in assignments]
    if len(set(tracks)) != len(tracks):
        raise ApiError(422, "duplicate_person", "each person can be assigned once")
    persons = {p.track_id: p for p in video.persons}
    out = []
    for track_id, profile_id in assignments:
        person = persons.get(track_id)
        if person is None:
            raise ApiError(422, "unknown_person", f"person {track_id} not found")
        if not person.selectable:
            raise ApiError(422, "person_not_selectable", f"person {track_id} can't be replaced",
                           {"flags": person.flags})
        prof = db.get(IdentityProfile, profile_id)
        # Only the requester's own, consented, ready profiles. Same 404 for foreign ids (no IDOR oracle).
        if prof is None or prof.user_id != user.id or prof.deleted_at is not None:
            raise not_found("identity profile")
        if prof.status != ProfileStatus.ready:
            raise ApiError(409, "profile_not_ready", "identity profile is not ready")
        out.append((person, prof))
    return out


def create_replace_job(db: Session, user: User, video_id: uuid.UUID, assignments: list[tuple[int, uuid.UUID]],
                       resolution: str, preview: bool, idempotency_key: str) -> tuple[GenerationJob, bool]:
    from .generation import queue_class_for

    cfg = require_enabled(db)
    existing = db.execute(select(GenerationJob).where(GenerationJob.user_id == user.id,
                                                      GenerationJob.idempotency_key == idempotency_key)
                          ).scalar_one_or_none()
    if existing:
        if existing.source_video_id != video_id or existing.kind != JobKind.multi_replace:
            raise ApiError(409, "idempotency_conflict", "idempotency key reused with different parameters")
        return existing, False
    video = get_own_video(db, user, video_id)
    pairs = validate_assignments(db, user, video, cfg, assignments)
    cost = quote(cfg, len(pairs), video.duration_ms or 0, resolution, preview)

    credits.lock_user(db, user.id)
    active = db.execute(select(func.count()).select_from(GenerationJob).where(
        GenerationJob.user_id == user.id, GenerationJob.kind != JobKind.analysis,
        GenerationJob.status.notin_([s.value for s in TERMINAL_STATUSES]))).scalar_one()
    if active >= get_settings().max_active_jobs_per_user:
        raise ApiError(429, "too_many_active_jobs", "wait for your current videos to finish")

    qc = queue_class_for(user)
    single_fast_path = len(pairs) == 1 and not (set(pairs[0][0].flags) & {"heavy_occlusion", "reentry"})
    job = GenerationJob(
        id=uuid.uuid4(), user_id=user.id, kind=JobKind.multi_replace, source_video_id=video.id,
        status=JobStatus.queued, queue_class=qc, idempotency_key=idempotency_key, credit_cost=cost,
        max_attempts=get_settings().job_max_attempts,
        preferred_model=cfg["preferred_model"], fallback_model=cfg["fallback_model"],
        watermark=bool(cfg["force_watermark"]) or qc == "free",
        spec={"resolution": "480x832" if preview else resolution, "preview": preview,
              "max_seconds": cfg["preview_seconds"] if preview else cfg["max_duration_s"],
              "fast_path": single_fast_path,
              "assignments": [{"track_id": p.track_id, "profile_id": str(prof.id),
                               "first_frame": p.first_frame, "last_frame": p.last_frame,
                               "worker_track_id": p.stats.get("worker_track_id")} for p, prof in pairs]},
    )
    db.add(job)
    db.flush()
    for p, prof in pairs:
        db.add(JobAssignment(job_id=job.id, track_id=p.track_id, profile_id=prof.id))
    if cost:
        credits.apply(db, user.id, -cost, LedgerReason.generation_debit, f"gen:{job.id}",
                      ref_type="generation_job", ref_id=str(job.id))
    return job, True


def build_payload(db: Session, job: GenerationJob) -> dict:
    from ..models import AssetStatus, IdentityAsset
    from .generation import output_key

    st = get_storage()
    video = db.get(SourceVideo, job.source_video_id)
    if video is None or video.deleted_at is not None:
        raise ApiError(410, "source_gone", "source video deleted")
    base = {
        "job_id": str(job.id), "attempt": job.attempts, "model": job.model_used, "kind": job.kind.value,
        "capability": "person_analysis" if job.kind == JobKind.analysis else "multi_person_replace",
        "lease_s": get_settings().job_lease_s, "spec": job.spec, "watermark": job.watermark,
        "source_video_url": st.presign_get(video.storage_key, 3600),
        "prompt": "", "negative_prompt": "", "params": {}, "duration_s": (video.duration_ms or 0) / 1000,
        "identity_assets": [],
    }
    prefix = f"users/{video.user_id}/source_videos/{video.id}/"
    if job.kind == JobKind.analysis:
        n = job.spec.get("max_persons_detect", 8)
        base["upload"] = {
            "thumbnails": {str(i): {"key": f"{prefix}person_{i}.jpg",
                                    "url": st.presign_put(f"{prefix}person_{i}.jpg", "image/jpeg")}
                           for i in range(1, n + 1)},
            "tracks": {"key": f"{prefix}tracks.json",
                       "url": st.presign_put(f"{prefix}tracks.json", "application/json")},
        }
        return base
    base["tracks_url"] = st.presign_get(f"{prefix}tracks.json", 3600)
    identities = {}
    for a in job.spec.get("assignments", []):
        assets = db.execute(select(IdentityAsset).where(IdentityAsset.profile_id == uuid.UUID(a["profile_id"]),
                                                        IdentityAsset.status == AssetStatus.accepted)
                            .order_by(IdentityAsset.created_at)).scalars().all()
        identities[str(a["track_id"])] = [{"id": str(x.id), "kind": x.kind, "url": st.presign_get(x.storage_key, 3600)}
                                          for x in assets]
    base["identities"] = identities
    base["params"] = {"resolution": job.spec.get("resolution", "720x1280")}
    base["upload"] = {
        "video": {"key": output_key(job, "video.mp4"),
                  "url": st.presign_put(output_key(job, "video.mp4"), "video/mp4")},
        "thumbnail": {"key": output_key(job, "thumb.jpg"),
                      "url": st.presign_put(output_key(job, "thumb.jpg"), "image/jpeg")},
    }
    return base
