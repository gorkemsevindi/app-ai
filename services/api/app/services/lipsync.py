"""Audio options for replacement/remix jobs (spec §27/§28): audio_mode, lip-sync, speaker mapping, motion
preservation, pricing surcharge and the per-job cost ceiling / margin floor.

Speaker → person mapping: the worker's analysis proposes a visible speaker per activity segment with a
confidence. Only replaced persons need lip-sync when the original soundtrack is kept (untouched people
still match it). Low-confidence segments that would affect a replaced person are never guessed: the API
answers 409 `speaker_mapping_required` with the evidence so the client can ask the user."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..errors import ApiError, not_found
from ..models import AudioAsset, FeatureFlag, SourceVideo, User
from .storage import get_storage

FLAG = "lip_sync"
AUDIO_ATTESTATION = "audio-rights-v1"
AUDIO_MIMES = {"audio/mpeg", "audio/mp4", "audio/x-m4a", "audio/wav", "audio/x-wav"}

DEFAULTS: dict = {
    "provider": "sync_so",                # disabled until contracted; tests/dev set mock_lipsync
    "expression_provider": "passthrough",
    "credits_per_second": 3,
    "premium_multiplier": 2.0,
    "max_offset_ms": 120,
    "min_sync_score": 0.25,
    "min_mapping_confidence": 0.5,
    "provider_usd_per_second": {"mock_lipsync": 0.0, "latentsync": 0.01, "musetalk": 0.01, "sync_so": 0.083},
    "gpu_usd_per_person_second": 0.004,
    "credit_usd": 0.01,
    "max_job_cost_usd": 5.0,
    "margin_floor_usd": None,
    "custom_audio_max_mb": 20,
}


def config(db: Session) -> tuple[bool, dict]:
    f = db.get(FeatureFlag, FLAG)
    v = {**DEFAULTS, **((f.value or {}) if f else {})}
    v["provider_usd_per_second"] = {**DEFAULTS["provider_usd_per_second"],
                                    **((f.value or {}).get("provider_usd_per_second", {}) if f else {})}
    return bool(f and f.enabled), v


def audio_analysis_enabled(db: Session) -> bool:
    return config(db)[0]


@dataclass
class AudioOptions:
    audio_mode: str = "original"            # original|custom|none
    audio_asset_id: uuid.UUID | None = None
    lip_sync: bool = False
    mode: str = "auto"                      # speech|singing|auto
    speaker_mapping: list[dict] | None = None  # [{start_ms,end_ms,track_id|slot_id}]
    preserve: dict = field(default_factory=lambda: {"head_motion": True, "expression": True, "eye_motion": True})
    quality: str = "standard"


@dataclass
class AudioPlan:
    spec: dict
    extra_credits: int
    est_cost_usd: float


def speakers(video: SourceVideo) -> dict:
    """Analysis audio timeline expressed in API track ids (Person N), for UI and validation."""
    a = (video.analysis or {}).get("audio")
    if not a:
        return {"available": False, "has_audio": None, "segments": []}
    w2t = {p.stats.get("worker_track_id"): p.track_id for p in video.persons}
    segs = []
    for s in a.get("segments", []):
        scores = {str(w2t[int(k)]): v for k, v in (s.get("track_scores") or {}).items() if int(k) in w2t}
        sug = w2t.get(s.get("suggested_worker_track"))
        segs.append({"start_ms": s["start_ms"], "end_ms": s["end_ms"], "suggested_track_id": sug,
                     "confidence": s.get("confidence", 0.0), "track_scores": scores})
    return {"available": True, "has_audio": a.get("has_audio"), "separated": a.get("separated", False),
            "segments": segs}


def plan(db: Session, user: User, video: SourceVideo, assigned: dict[int, int], opts: AudioOptions | None,
         n_persons: int, template_mapping: list[dict] | None = None, slot_to_track: dict[str, int] | None = None
         ) -> AudioPlan:
    """assigned: API track_id -> worker_track_id for the persons being replaced in this job."""
    opts = opts or AudioOptions()
    enabled, cfg = config(db)
    if opts.audio_mode not in ("original", "custom", "none"):
        raise ApiError(422, "bad_audio_mode", "audio_mode must be original, custom or none")
    spec: dict = {"audio_mode": opts.audio_mode, "preserve": opts.preserve, "quality": opts.quality}
    if opts.audio_mode == "custom":
        a = db.get(AudioAsset, opts.audio_asset_id) if opts.audio_asset_id else None
        if a is None or a.user_id != user.id or a.deleted_at is not None:
            raise not_found("audio")
        if a.status != "ready":
            raise ApiError(409, "audio_not_ready", "audio upload is not finished")
        spec["audio_asset_id"] = str(a.id)
    duration_s = (video.duration_ms or 0) / 1000
    gpu_usd = float(cfg["gpu_usd_per_person_second"]) * n_persons * duration_s
    if not opts.lip_sync:
        return AudioPlan(spec, 0, round(gpu_usd, 4))
    if not enabled:
        raise ApiError(403, "feature_disabled", "lip-sync is not available yet")
    if opts.audio_mode == "none":
        raise ApiError(422, "lip_sync_needs_audio", "lip-sync needs the original or a custom soundtrack")
    if opts.mode not in ("speech", "singing", "auto"):
        raise ApiError(422, "bad_lip_sync_mode", "speech, singing or auto")

    def resolve_track(item: dict) -> int:
        if item.get("slot_id") is not None:
            t = (slot_to_track or {}).get(str(item["slot_id"]))
            if t is None:
                raise ApiError(422, "unknown_slot", f"slot {item['slot_id']} not found")
            return t
        return int(item.get("track_id", 0))

    mapping: list[dict] = []
    if opts.speaker_mapping is not None or template_mapping:
        for item in (opts.speaker_mapping if opts.speaker_mapping is not None else template_mapping or []):
            s, e = int(item["start_ms"]), int(item["end_ms"])
            if s < 0 or e <= s or (video.duration_ms and e > video.duration_ms + 500):
                raise ApiError(422, "bad_speaker_segment", "segments must lie inside the clip")
            t = resolve_track(item)
            if t not in assigned:
                if opts.speaker_mapping is None:
                    continue  # curated template line for a slot this user kept original
                raise ApiError(422, "speaker_not_replaced", f"person {t} is not being replaced in this video")
            mapping.append({"start_ms": s, "end_ms": e, "track_id": t, "worker_track_id": assigned[t],
                            "source": "manual" if opts.speaker_mapping is not None else "template"})
    else:
        if opts.audio_mode == "custom":
            raise ApiError(409, "speaker_mapping_required", "tell us who sings/speaks each part of your audio",
                           {"segments": []})
        timeline = speakers(video)
        if not timeline["available"]:
            raise ApiError(409, "audio_analysis_missing", "re-analyse the video with lip-sync enabled")
        min_conf = float(cfg["min_mapping_confidence"])
        unresolved = []
        for seg in timeline["segments"]:
            sug, conf = seg["suggested_track_id"], float(seg["confidence"])
            touches_replaced = any(int(t) in assigned and sc > 0 for t, sc in seg["track_scores"].items())
            if sug in assigned and conf >= min_conf:
                mapping.append({"start_ms": seg["start_ms"], "end_ms": seg["end_ms"], "track_id": sug,
                                "worker_track_id": assigned[sug], "source": "auto", "confidence": conf})
            elif touches_replaced and (conf < min_conf or sug in assigned):
                unresolved.append(seg)
        if unresolved:
            raise ApiError(409, "speaker_mapping_required", "confirm who is speaking in these parts",
                           {"segments": unresolved})
    seconds = sum((m["end_ms"] - m["start_ms"]) / 1000 for m in mapping)
    mult = float(cfg["premium_multiplier"]) if opts.quality == "premium" else 1.0
    extra = int(math.ceil(float(cfg["credits_per_second"]) * seconds * mult))
    provider = cfg["provider"]
    cost = gpu_usd + float(cfg["provider_usd_per_second"].get(provider, 0.1)) * seconds
    spec["lip_sync"] = {"enabled": True, "mode": opts.mode, "provider": provider,
                        "expression_provider": cfg["expression_provider"],
                        "max_offset_ms": int(cfg["max_offset_ms"]), "min_score": float(cfg["min_sync_score"])}
    spec["speaker_mapping"] = mapping
    return AudioPlan(spec, extra, round(cost, 4))


def enforce_economics(db: Session, total_credits: int, est_cost_usd: float) -> None:
    """Spec §28: per-job cost ceiling and optional margin floor, checked before any debit."""
    _, cfg = config(db)
    if est_cost_usd > float(cfg["max_job_cost_usd"]):
        raise ApiError(422, "cost_ceiling_exceeded", "this combination is too expensive to render right now",
                       {"est_cost_usd": est_cost_usd})
    floor = cfg.get("margin_floor_usd")
    if floor is not None and total_credits * float(cfg["credit_usd"]) - est_cost_usd < float(floor):
        raise ApiError(422, "requote_required", "pricing for this video needs to be updated",
                       {"est_cost_usd": est_cost_usd})


# ---------------------------------------------------------------- custom audio (licensed/owned uploads)

def create_audio(db: Session, user: User, mime: str, size_bytes: int, rights_basis: str, attested: bool
                 ) -> AudioAsset:
    enabled, cfg = config(db)
    studio_flag = db.get(FeatureFlag, "studio")
    if not enabled and not (studio_flag and studio_flag.enabled):  # lip-sync soundtracks or AI Studio music
        raise ApiError(403, "feature_disabled", "custom audio is not available yet")
    if not attested or rights_basis not in ("own", "licensed"):
        raise ApiError(422, "attestation_required", "confirm you own or licensed this audio")
    if mime not in AUDIO_MIMES:
        raise ApiError(415, "unsupported_media_type", "use MP3, M4A or WAV")
    if size_bytes > int(cfg["custom_audio_max_mb"]) * 1024 * 1024:
        raise ApiError(413, "file_too_large", f"max {cfg['custom_audio_max_mb']} MB")
    aid = uuid.uuid4()
    a = AudioAsset(id=aid, user_id=user.id, storage_key=f"users/{user.id}/audio/{aid}/original", mime=mime,
                   declared_size=size_bytes, status="pending_upload", rights_basis=rights_basis,
                   rights_attested_at=datetime.now(UTC), attestation_version=AUDIO_ATTESTATION)
    db.add(a)
    db.flush()
    return a


def _sniff_audio(head: bytes) -> bool:
    return (head[:4] == b"RIFF" and head[8:12] == b"WAVE") or head[:3] == b"ID3" or \
        (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0) or head[4:8] == b"ftyp"


def complete_audio(db: Session, a: AudioAsset) -> AudioAsset:
    if a.status != "pending_upload":
        return a
    st = get_storage()
    h = st.head(a.storage_key)
    if h is None:
        raise ApiError(409, "upload_missing", "file not uploaded yet")
    a.size_bytes = h["size"]
    _, cfg = config(db)
    if h["size"] > int(cfg["custom_audio_max_mb"]) * 1024 * 1024 or not _sniff_audio(
            st.read_head_bytes(a.storage_key, 16)):
        st.delete(a.storage_key)
        a.status = "rejected"
        return a
    a.status = "ready"
    return a


def get_own_audio(db: Session, user: User, audio_id: uuid.UUID) -> AudioAsset:
    a = db.get(AudioAsset, audio_id)
    if a is None or a.user_id != user.id or a.deleted_at is not None:
        raise not_found("audio")
    return a


def delete_audio(db: Session, a: AudioAsset) -> None:
    get_storage().delete(a.storage_key)
    a.status, a.deleted_at = "deleted", datetime.now(UTC)
