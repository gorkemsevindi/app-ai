"""Template system V3 (spec §3, §16, §26): variable person slots, source-clip ingestion, publish/moderation
gate, multi-slot remix jobs, server-side ranking and aggregated metrics.

Everything is additive: classic single-identity templates keep working through `generation.create_job`.
Remix jobs reuse the multi-person pipeline (`multiperson.enqueue_replace`) on the template's analysed clip.
Prices, ranking weights and limits come from remote config (`template_remix`, `ranking` flags)."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import ApiError, not_found
from ..models import (
    TERMINAL_STATUSES,
    AnalyticsEvent,
    FeatureFlag,
    GenerationJob,
    IdentityProfile,
    JobKind,
    JobStatus,
    ProfileStatus,
    Report,
    SourceVideo,
    SourceVideoStatus,
    Template,
    TemplateMetricDaily,
    TemplatePersonSlot,
    TemplateVersion,
    User,
    VideoPerson,
)
from . import credits, lipsync, multiperson
from .storage import VIDEO_MIMES, get_storage

REMIX_FLAG = "template_remix"
RANKING_FLAG = "ranking"
TEMPLATE_ATTESTATION = "template-rights-v1"
RIGHTS_BASES = {"first_party", "licensed", "creator_owned", "public_domain"}
VISIBILITIES = {"draft", "private", "unlisted", "public", "blocked"}

REMIX_DEFAULTS: dict = {
    "pricing": {"per_extra_slot": 15, "preview": 5},
    "resolutions": {"480x832": 1.0, "720x1280": 1.5},
    "require_all_slots": False,
    "max_slots": 8,
    "confirm_above_credits": 100,  # spec §2.2: require explicit confirmation when the price is material
}
RANKING_DEFAULTS: dict = {
    "weights": {"recent_successful_uses": 1.0, "share_rate": 2.0, "completion_rate": 1.5, "paid_conversion": 1.0,
                "quality": 0.5, "report_rate": 4.0, "failure_rate": 2.0},
    "window_days": 14, "half_life_days": 7.0, "max_per_creator": 3, "new_template_boost_days": 3,
    "report_block_rate": 0.2,
}


def _flag(db: Session, key: str, defaults: dict) -> tuple[bool, dict]:
    f = db.get(FeatureFlag, key)
    merged = {**defaults, **((f.value or {}) if f else {})}
    for k in ("pricing", "weights"):
        if k in defaults:
            merged[k] = {**defaults[k], **((f.value or {}).get(k, {}) if f else {})}
    return bool(f and f.enabled), merged


def remix_config(db: Session) -> tuple[bool, dict]:
    return _flag(db, REMIX_FLAG, REMIX_DEFAULTS)


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- visibility / lookup

def is_listed(t: Template) -> bool:
    return (t.is_active and t.deleted_at is None and t.current_version_id is not None
            and t.visibility == "public" and t.moderation_status == "approved")


def is_usable(t: Template) -> bool:
    """Listed templates plus unlisted ones (reachable by deep link only)."""
    return (t.is_active and t.deleted_at is None and t.current_version_id is not None
            and t.visibility in ("public", "unlisted") and t.moderation_status == "approved")


def current_version(db: Session, t: Template) -> TemplateVersion:
    v = db.get(TemplateVersion, t.current_version_id) if t.current_version_id else None
    if v is None:
        raise not_found("template")
    return v


def slots_of(db: Session, v: TemplateVersion) -> list[TemplatePersonSlot]:
    return list(db.execute(select(TemplatePersonSlot).where(TemplatePersonSlot.template_version_id == v.id)
                           .order_by(TemplatePersonSlot.position)).scalars())


def is_remix(v: TemplateVersion, slots: list[TemplatePersonSlot]) -> bool:
    return v.source_video_id is not None and bool(slots)


# ---------------------------------------------------------------- pricing

def quote(db: Session, t: Template, v: TemplateVersion, n_slots: int, resolution: str, preview: bool) -> dict:
    _, cfg = remix_config(db)
    rule = {**cfg["pricing"], **(v.credit_rule or {})}
    res_mult = {**cfg["resolutions"], **rule.get("resolutions", {})}
    if resolution not in res_mult:
        raise ApiError(422, "bad_resolution", f"allowed: {sorted(res_mult)}")
    if preview:
        credits_ = int(rule.get("preview", 5))
        breakdown = {"preview": credits_}
    else:
        base = int(rule.get("base", t.credit_cost))
        extra = int(rule["per_extra_slot"]) * max(0, n_slots - 1)
        credits_ = int(math.ceil((base + extra) * float(res_mult[resolution])))
        breakdown = {"base": base, "extra_slots": extra, "resolution_multiplier": float(res_mult[resolution])}
    return {"credits": credits_, "breakdown": breakdown,
            "confirm_required": credits_ >= int(cfg["confirm_above_credits"])}


# ---------------------------------------------------------------- remix jobs

@dataclass
class SlotAssignment:
    slot_id: str
    profile_id: uuid.UUID


def _validate_slots(db: Session, user: User, v: TemplateVersion, slots: list[TemplatePersonSlot],
                    assignments: list[SlotAssignment], cfg: dict) -> list[tuple[VideoPerson, IdentityProfile]]:
    if not assignments:
        raise ApiError(422, "no_assignments", "add at least one photo set")
    by_slot = {s.slot_id: s for s in slots}
    ids = [a.slot_id for a in assignments]
    if len(set(ids)) != len(ids):
        raise ApiError(422, "duplicate_slot", "each slot can be filled once")
    missing_required = [s.slot_id for s in slots if s.required and s.slot_id not in ids]
    if missing_required or (cfg["require_all_slots"] and len(ids) != len(slots)):
        raise ApiError(422, "slots_missing", "fill all required slots", {"slots": missing_required or
                                                                          [s.slot_id for s in slots]})
    video = db.get(SourceVideo, v.source_video_id)
    if video is None or video.status != SourceVideoStatus.ready or video.deleted_at is not None:
        raise ApiError(409, "template_unavailable", "this template is being updated, try again later")
    persons = {p.track_id: p for p in video.persons}
    out = []
    for a in assignments:
        slot = by_slot.get(a.slot_id)
        if slot is None:
            raise ApiError(422, "unknown_slot", f"slot {a.slot_id} not found")
        person = persons.get(slot.track_id)
        if person is None or not person.selectable:
            raise ApiError(409, "template_unavailable", "this template is being updated, try again later")
        prof = db.get(IdentityProfile, a.profile_id)
        if prof is None or prof.user_id != user.id or prof.deleted_at is not None:
            raise not_found("identity profile")  # same 404 for foreign ids: no IDOR oracle
        if prof.status != ProfileStatus.ready:
            raise ApiError(409, "profile_not_ready", "identity profile is not ready")
        out.append((person, prof))
    return out


def remix_audio_plan(db: Session, user: User, v: TemplateVersion, slots: list[TemplatePersonSlot], pairs: list,
                     audio: lipsync.AudioOptions | None) -> lipsync.AudioPlan:
    video = db.get(SourceVideo, v.source_video_id)
    assigned = {p.track_id: p.stats.get("worker_track_id") for p, _ in pairs}
    return lipsync.plan(db, user, video, assigned, audio, len(pairs),
                        template_mapping=(v.config or {}).get("speaker_mapping"),
                        slot_to_track={s.slot_id: s.track_id for s in slots})


def create_remix_job(db: Session, user: User, template_id: uuid.UUID, assignments: list[SlotAssignment],
                     resolution: str, preview: bool, idempotency_key: str,
                     audio: lipsync.AudioOptions | None = None) -> tuple[GenerationJob, bool]:
    enabled, cfg = remix_config(db)
    if not enabled:
        raise ApiError(403, "feature_disabled", "template remix is not available yet")
    existing = db.execute(select(GenerationJob).where(GenerationJob.user_id == user.id,
                                                      GenerationJob.idempotency_key == idempotency_key)
                          ).scalar_one_or_none()
    if existing:
        if existing.template_id != template_id or existing.kind != JobKind.multi_replace:
            raise ApiError(409, "idempotency_conflict", "idempotency key reused with different parameters")
        return existing, False
    t = db.get(Template, template_id)
    if t is None or not is_usable(t):
        raise not_found("template")
    if t.pro_only:
        from .generation import queue_class_for
        if queue_class_for(user) != "paid_high":
            raise ApiError(402, "pro_required", "this template requires Pro")
    v = current_version(db, t)
    slots = slots_of(db, v)
    if not is_remix(v, slots):
        raise ApiError(422, "not_a_remix_template", "use the single-photo flow for this template")
    pairs = _validate_slots(db, user, v, slots, assignments, cfg)
    q = quote(db, t, v, len(pairs), resolution, preview)
    # Templates default to keeping their (licensed) soundtrack; lip-sync uses the curated slot timeline.
    ap = remix_audio_plan(db, user, v, slots, pairs, audio)
    if not preview and ap.extra_credits:
        q = {**q, "credits": q["credits"] + ap.extra_credits,
             "breakdown": {**q["breakdown"], "lip_sync": ap.extra_credits}}
    lipsync.enforce_economics(db, q["credits"], ap.est_cost_usd)

    credits.lock_user(db, user.id)
    active = db.execute(select(func.count()).select_from(GenerationJob).where(
        GenerationJob.user_id == user.id, GenerationJob.kind != JobKind.analysis,
        GenerationJob.status.notin_([s.value for s in TERMINAL_STATUSES]))).scalar_one()
    if active >= get_settings().max_active_jobs_per_user:
        raise ApiError(429, "too_many_active_jobs", "wait for your current videos to finish")
    video = db.get(SourceVideo, v.source_video_id)
    mp_cfg = multiperson.config(db)
    slot_by_track = {s.track_id: s.slot_id for s in slots}
    job = multiperson.enqueue_replace(
        db, user, video, pairs, q["credits"], idempotency_key, resolution=resolution, preview=preview,
        max_seconds=mp_cfg["preview_seconds"] if preview else (t.duration_s or mp_cfg["max_duration_s"]),
        preferred_model=v.preferred_model, fallback_model=v.fallback_model,
        force_watermark=bool(v.config.get("force_watermark", False)),
        template_id=t.id, template_version_id=v.id,
        extra_spec={"template_slots": [{"slot_id": slot_by_track[p.track_id], "track_id": p.track_id}
                                       for p, _ in pairs],
                    "template_config": v.config or {}, "quote": q, "audio": ap.spec,
                    "est_cost_usd": ap.est_cost_usd})
    return job, True


# ---------------------------------------------------------------- ingestion (admin console)

def attach_source_video(db: Session, admin: User, t: Template, mime: str, size_bytes: int, rights: dict
                        ) -> SourceVideo:
    """Step 1 of ingestion: rights evidence first, then an upload slot for the canonical clip."""
    basis = rights.get("basis")
    if basis not in RIGHTS_BASES:
        raise ApiError(422, "rights_required", f"rights basis must be one of {sorted(RIGHTS_BASES)}")
    if basis in ("licensed", "creator_owned") and not rights.get("evidence"):
        raise ApiError(422, "rights_evidence_required", "attach licence/release evidence references")
    if mime not in VIDEO_MIMES:
        raise ApiError(415, "unsupported_media_type", "use MP4 or MOV")
    t.commercial_rights = {**rights, "attested_by": str(admin.id), "attested_at": now().isoformat(),
                           "attestation_version": TEMPLATE_ATTESTATION}
    vid = uuid.uuid4()
    v = SourceVideo(id=vid, user_id=admin.id, storage_key=f"users/{admin.id}/source_videos/{vid}/original",
                    mime=mime, declared_size=size_bytes, rights_attested_at=now(),
                    attestation_version=TEMPLATE_ATTESTATION,
                    analysis={"template_id": str(t.id)})
    db.add(v)
    db.flush()
    return v


def complete_source_upload(db: Session, admin: User, video: SourceVideo) -> SourceVideo:
    # Ingestion runs even while the consumer multi-person feature is disabled.
    return multiperson.complete_upload(db, admin, video, cfg=multiperson.config(db))


def create_version(db: Session, admin: User, t: Template, source_video_id: uuid.UUID, slots: list[dict],
                   prompt_recipe: str, preferred_model: str, fallback_model: str | None, config: dict,
                   credit_rule: dict) -> TemplateVersion:
    """Step 2: define slots on the analysed clip. Creates a new immutable (unpublished) version."""
    _, cfg = remix_config(db)
    video = db.get(SourceVideo, source_video_id)
    if video is None or video.deleted_at is not None or video.analysis.get("template_id") != str(t.id):
        raise not_found("source video")
    if video.status != SourceVideoStatus.ready:
        raise ApiError(409, "video_not_ready", "source clip analysis is not finished or was rejected",
                       {"status": video.status.value, "reason": video.rejection_reason})
    if not slots or len(slots) > cfg["max_slots"]:
        raise ApiError(422, "bad_slots", f"define 1..{cfg['max_slots']} slots")
    persons = {p.track_id: p for p in video.persons}
    seen_slots, seen_tracks = set(), set()
    for s in slots:
        p = persons.get(int(s["track_id"]))
        if p is None or not p.selectable:
            raise ApiError(422, "track_not_selectable", f"track {s['track_id']} cannot be a slot",
                           {"flags": p.flags if p else ["missing"]})
        if s["slot_id"] in seen_slots or p.track_id in seen_tracks:
            raise ApiError(422, "duplicate_slot", "slot ids and tracks must be unique")
        seen_slots.add(s["slot_id"])
        seen_tracks.add(p.track_id)
    for seg in (config or {}).get("speaker_mapping", []):
        if seg.get("slot_id") not in seen_slots or int(seg.get("end_ms", 0)) <= int(seg.get("start_ms", -1)):
            raise ApiError(422, "bad_speaker_mapping", "speaker_mapping entries need a defined slot_id and a range")
    prev = db.execute(select(func.coalesce(func.max(TemplateVersion.version), 0))
                      .where(TemplateVersion.template_id == t.id)).scalar_one()
    v = TemplateVersion(template_id=t.id, version=int(prev) + 1, prompt_recipe=prompt_recipe,
                        model_capability="multi_person_replace", preferred_model=preferred_model,
                        fallback_model=fallback_model, created_by=admin.id, source_video_id=video.id,
                        config=config, credit_rule=credit_rule,
                        params={"source_clip_key": video.storage_key})
    db.add(v)
    db.flush()
    for i, s in enumerate(slots):
        db.add(TemplatePersonSlot(template_version_id=v.id, slot_id=s["slot_id"], position=i,
                                  label=s.get("label") or f"Person {i + 1}", track_id=int(s["track_id"]),
                                  required=bool(s.get("required", False)), requirements=s.get("requirements", {})))
    db.flush()
    return v


def publish(db: Session, admin: User, t: Template, version_id: uuid.UUID, visibility: str) -> Template:
    """Step 3: moderation gate + publish. Published versions are immutable; older jobs keep their
    template_version_id so outputs stay traceable (spec §3)."""
    v = db.get(TemplateVersion, version_id)
    if v is None or v.template_id != t.id:
        raise not_found("template version")
    if visibility not in ("public", "unlisted", "private"):
        raise ApiError(422, "bad_visibility", "public, unlisted or private")
    if v.source_video_id is not None:
        if not t.commercial_rights.get("basis"):
            raise ApiError(422, "rights_required", "record commercial rights before publishing")
        video = db.get(SourceVideo, v.source_video_id)
        if video is None or video.status != SourceVideoStatus.ready:
            raise ApiError(409, "video_not_ready", "source clip is not ready")
        if not slots_of(db, v):
            raise ApiError(422, "bad_slots", "a remix template needs at least one slot")
    if t.moderation_status == "rejected":
        raise ApiError(409, "template_rejected", "rejected templates must be re-reviewed first")
    v.published_at = v.published_at or now()
    t.current_version_id = v.id
    t.moderation_status = "approved"
    t.visibility = visibility
    t.is_active = True
    return t


# ---------------------------------------------------------------- metrics (aggregated separately)

def refresh_metrics(db: Session, day: date) -> int:
    """Aggregate one UTC day into template_metrics_daily (idempotent upsert). Run daily + for today."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    end = start + timedelta(days=1)
    rows: dict[uuid.UUID, dict] = {}

    def row(tid):
        return rows.setdefault(tid, {"opens": 0, "shares": 0, "generations": 0, "successes": 0, "failures": 0,
                                     "paid_uses": 0, "reports": 0, "credits_charged": 0, "cost_usd": 0.0})

    jobs = db.execute(select(GenerationJob.template_id, GenerationJob.status, GenerationJob.queue_class,
                             GenerationJob.credit_cost, GenerationJob.refunded)
                      .where(GenerationJob.template_id.isnot(None), GenerationJob.created_at >= start,
                             GenerationJob.created_at < end)).all()
    for tid, st, qc, cost, refunded in jobs:
        r = row(tid)
        r["generations"] += 1
        if st == JobStatus.completed:
            r["successes"] += 1
            if qc != "free":
                r["paid_uses"] += 1
        elif st == JobStatus.failed:
            r["failures"] += 1
        if not refunded:
            r["credits_charged"] += int(cost or 0)
    from ..models import ModelRun
    for tid, usd in db.execute(select(GenerationJob.template_id, func.coalesce(func.sum(ModelRun.est_cost_usd), 0.0))
                               .join(ModelRun, ModelRun.job_id == GenerationJob.id)
                               .where(GenerationJob.template_id.isnot(None), ModelRun.started_at >= start,
                                      ModelRun.started_at < end).group_by(GenerationJob.template_id)):
        row(tid)["cost_usd"] += float(usd)
    for tid, n in db.execute(select(Report.template_id, func.count()).where(
            Report.template_id.isnot(None), Report.created_at >= start, Report.created_at < end)
            .group_by(Report.template_id)):
        row(tid)["reports"] += n
    for name, props in db.execute(select(AnalyticsEvent.name, AnalyticsEvent.props).where(
            AnalyticsEvent.name.in_(["template_open", "template_share"]), AnalyticsEvent.occurred_at >= start,
            AnalyticsEvent.occurred_at < end)):
        try:
            tid = uuid.UUID(str((props or {}).get("template_id")))
        except ValueError:
            continue
        if db.get(Template, tid) is None:
            continue
        row(tid)["opens" if name == "template_open" else "shares"] += 1
    for tid, vals in rows.items():
        stmt = insert(TemplateMetricDaily).values(template_id=tid, day=day, **vals)
        db.execute(stmt.on_conflict_do_update(index_elements=["template_id", "day"], set_=vals))
    return len(rows)


# ---------------------------------------------------------------- ranking / feed

def _metrics_window(db: Session, ids: list[uuid.UUID], days: int, half_life: float) -> dict[uuid.UUID, dict]:
    today = now().date()
    out: dict[uuid.UUID, dict] = {i: {"uses": 0.0, "succ": 0, "fail": 0, "gen": 0, "paid": 0, "opens": 0,
                                       "shares": 0, "reports": 0} for i in ids}
    if not ids:
        return out
    for m in db.execute(select(TemplateMetricDaily).where(TemplateMetricDaily.template_id.in_(ids),
                                                           TemplateMetricDaily.day >= today - timedelta(days=days))
                        ).scalars():
        age = (today - m.day).days
        decay = 0.5 ** (age / half_life)
        o = out[m.template_id]
        o["uses"] += m.successes * decay
        o["succ"] += m.successes
        o["fail"] += m.failures
        o["gen"] += m.generations
        o["paid"] += m.paid_uses
        o["opens"] += m.opens
        o["shares"] += m.shares
        o["reports"] += m.reports
    return out


def score(m: dict, w: dict, quality: float, age_days: float, boost_days: float) -> tuple[float, float]:
    """Explainable score (spec §16). Returns (score, report_rate). Rates use Laplace smoothing so new
    templates are neither buried nor over-promoted by a handful of events."""
    completion = (m["succ"] + 1) / (m["gen"] + 2)
    failure = (m["fail"] + 0.5) / (m["gen"] + 5)
    share_rate = (m["shares"] + 0.5) / (m["opens"] + m["succ"] + 5)
    report_rate = m["reports"] / max(5, m["opens"] + m["gen"])
    paid_conv = (m["paid"] + 0.5) / (m["succ"] + 5)
    s = (w["recent_successful_uses"] * math.log1p(m["uses"]) + w["share_rate"] * share_rate
         + w["completion_rate"] * completion + w["paid_conversion"] * paid_conv + w["quality"] * quality
         - w["report_rate"] * report_rate - w["failure_rate"] * failure)
    if age_days < boost_days:
        s += 0.5 * (1 - age_days / boost_days)  # cold-start exposure for brand-new templates
    return s, report_rate


def feed(db: Session, category: str | None, limit: int, locale: str | None = None) -> list[dict]:
    _, rc = _flag(db, RANKING_FLAG, RANKING_DEFAULTS)
    q = select(Template).where(Template.is_active.is_(True), Template.deleted_at.is_(None),
                               Template.current_version_id.isnot(None), Template.visibility == "public",
                               Template.moderation_status == "approved")
    if category and category not in ("trending", "new", "multi_person"):
        q = q.where(Template.category == category)
    templates = list(db.execute(q).scalars())
    if locale:
        lang = locale.split("-")[0]
        templates = [t for t in templates if not t.locale_tags or lang in t.locale_tags]
    versions = {v.id: v for v in db.execute(select(TemplateVersion).where(
        TemplateVersion.id.in_([t.current_version_id for t in templates]))).scalars()} if templates else {}
    slot_counts = dict(db.execute(select(TemplatePersonSlot.template_version_id, func.count())
                                  .where(TemplatePersonSlot.template_version_id.in_(list(versions)))
                                  .group_by(TemplatePersonSlot.template_version_id)).all()) if versions else {}
    if category == "multi_person":
        templates = [t for t in templates if slot_counts.get(t.current_version_id, 0) >= 2]
    metrics = _metrics_window(db, [t.id for t in templates], int(rc["window_days"]), float(rc["half_life_days"]))
    scored = []
    for t in templates:
        age = (now() - t.created_at).total_seconds() / 86400 if t.created_at else 999
        quality = float((versions[t.current_version_id].config or {}).get("quality_score", 0.5))
        sc, report_rate = score(metrics[t.id], rc["weights"], quality, age, float(rc["new_template_boost_days"]))
        if report_rate >= float(rc["report_block_rate"]):
            continue  # safety penalty: heavily reported templates drop out until reviewed
        if category == "new":
            sc = -age
        scored.append((sc, t))
    scored.sort(key=lambda x: (-x[0], x[1].sort_order))
    per_creator: dict = {}
    cards = []
    for _, t in scored:
        key = t.creator_id or "official"
        if per_creator.get(key, 0) >= int(rc["max_per_creator"]) and t.creator_id is not None:
            continue  # creator diversity (official/first-party templates are not capped)
        per_creator[key] = per_creator.get(key, 0) + 1
        cards.append(card(db, t, versions[t.current_version_id], slot_counts.get(t.current_version_id, 0),
                          metrics[t.id]))
        if len(cards) >= limit:
            break
    return cards


def card(db: Session, t: Template, v: TemplateVersion, n_slots: int, m: dict | None = None) -> dict:
    uses = m["succ"] if m else 0
    creator = None
    if t.creator_id:
        u = db.get(User, t.creator_id)
        creator = {"id": str(t.creator_id), "name": u.display_name if u else None}
    min_price = quote(db, t, v, 1, "720x1280", False)["credits"] if n_slots else t.credit_cost
    return {
        "id": str(t.id), "slug": t.slug, "title": t.title, "category": t.category,
        "thumbnail_url": t.thumbnail_url, "preview_url": t.preview_url, "duration_s": t.duration_s,
        "creator": creator or {"id": None, "name": "Official"},
        "use_count": uses, "est_credits": min_price, "person_slots": max(1, n_slots),
        "est_seconds": t.est_seconds, "pro_only": t.pro_only,
        "safety_badge": "verified_rights" if t.commercial_rights.get("basis") else "curated",
        "mode": "remix" if n_slots else "single",
    }


def slot_out(db: Session, v: TemplateVersion) -> list[dict]:
    st = get_storage()
    video = db.get(SourceVideo, v.source_video_id) if v.source_video_id else None
    persons = {p.track_id: p for p in (video.persons if video else [])}
    out = []
    for s in slots_of(db, v):
        p = persons.get(s.track_id)
        out.append({"slot_id": s.slot_id, "label": s.label, "required": s.required,
                    "thumbnail_url": st.presign_get(p.thumbnail_key) if p and p.thumbnail_key else None,
                    "requirements": s.requirements})
    return out
