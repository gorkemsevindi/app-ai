"""AI Cinema & Short Drama Factory (Master Spec V7) — productions on top of the existing Studio.

Four entry flows (series, film, "create your stars", life story) share one infrastructure: a production owns
seasons/episodes on story branches, and **every episode is a Studio project**. Rendering, content hashing and
selective re-render, typed edits/undo, V6 cast snapshots + identity lock, routing, credits and royalties are the
existing, tested code paths. This module adds the production layer: formats and long-episode limits, screenplay
planning, the fiction content policy, preview-first staging (free animatic → paid pilot → full render), budget
hard caps, episode status, cancellation, continuity, events (outbox) and export.

Nothing here claims more than exists: no TTS/lip-sync/dubbing provider is integrated, so dialogue is delivered as
exact subtitles; mock providers are labelled; a 30-minute episode is many short shot jobs plus assembly."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import ApiError, not_found
from ..models import (
    GenerationJob,
    GenerationOutput,
    JobKind,
    JobStatus,
    Production,
    ProductionEpisode,
    ProductionEvent,
    StoryBranch,
    StudioProject,
    StudioProjectVersion,
    StudioShotRender,
    User,
)
from . import budget, continuity, screenplay, studio

KINDS = ("series", "film", "stars", "life_story")
FORMATS = ("micro", "short_series", "standard_episode", "long_episode", "short_film", "feature")
RATINGS = ("general", "teen", "mature")
TERMINAL = {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}


def _now() -> datetime:
    return datetime.now(UTC)


def require_enabled(db: Session) -> dict:
    cfg = budget.require_enabled(db)
    studio.require_enabled(db)  # episodes are Studio projects
    return cfg


def emit(db: Session, production_id: uuid.UUID, type_: str, payload: dict) -> None:
    """Transactional outbox: same transaction as the change; delivered by the scheduler."""
    db.add(ProductionEvent(production_id=production_id, type=type_, payload=payload))


# ---------------------------------------------------------------- create

class CastIn(BaseModel):
    character_id: uuid.UUID
    alias: str | None = Field(default=None, pattern=r"^[a-z0-9_]{2,32}$")
    lock_mode: str = Field(default="", pattern=r"^(|standard|strong|strict)$")


class ProductionIn(BaseModel):
    kind: str = Field(pattern=r"^(series|film|stars|life_story)$")
    title: str = Field(min_length=1, max_length=120)
    logline: str = Field(default="", max_length=600)
    genre: str = Field(default="drama", max_length=40)
    audience: str = Field(default="adult", max_length=40)
    format: str = Field(pattern=r"^(micro|short_series|standard_episode|long_episode|short_film|feature)$")
    visual_style: str = Field(default="cinematic",
                              pattern=r"^(photoreal|cinematic|cartoon_2d|animation_3d|anime|stylized|mixed)$")
    language: str = Field(default="tr", max_length=8)
    aspect_ratio: str = Field(default="16:9", pattern=r"^(9:16|16:9|1:1)$")
    content_rating: str = Field(default="general", pattern=r"^(general|teen|mature)$")
    profile: str = Field(default="standard", pattern=r"^(economy|standard|cinema_pro)$")
    seasons: int = Field(default=1, ge=1, le=10)
    episodes_per_season: int = Field(default=1, ge=1, le=50)
    episode_duration_s: int = Field(ge=30, le=10800)
    cast: list[CastIn] = Field(default_factory=list, max_length=20)


def create(db: Session, user: User, body: ProductionIn) -> Production:
    cfg = require_enabled(db)
    lo, hi = cfg["formats"][body.format]
    if not lo <= body.episode_duration_s <= hi:
        raise ApiError(422, "bad_duration", f"{body.format} episodes are {lo}-{hi} seconds")
    if body.content_rating == "mature":
        if user.age_confirmed_at is None:
            raise ApiError(403, "adults_only", "mature productions are for adults")
        if not cfg["allow_mature"]:
            raise ApiError(403, "rating_not_available", "mature productions are not available in this region")
    for t in (body.title, body.logline):
        _fiction(t, body.content_rating, "visual")
    n = db.execute(select(func.count()).select_from(Production).where(Production.owner_id == user.id,
                                                                      Production.deleted_at.is_(None))).scalar_one()
    if n >= int(cfg["max_productions_per_user"]):
        raise ApiError(429, "production_limit", "production limit reached")
    max_ep = int(cfg["max_episode_seconds"])
    parts = -(-body.episode_duration_s // max_ep)  # a feature film longer than the quota becomes parts
    per_ep = -(-body.episode_duration_s // parts)
    count = body.seasons * body.episodes_per_season * parts
    if count > int(cfg["max_episodes"]):
        raise ApiError(422, "too_many_episodes", f"at most {cfg['max_episodes']} episodes")
    prod = Production(owner_id=user.id, kind=body.kind, title=body.title, logline=body.logline, genre=body.genre,
                      audience=body.audience, format=body.format, visual_style=body.visual_style,
                      language=body.language, aspect_ratio=body.aspect_ratio, content_rating=body.content_rating,
                      profile=body.profile, status="draft", settings={"parts_per_episode": parts})
    db.add(prod)
    db.flush()
    br = StoryBranch(production_id=prod.id, name="main", created_by=user.id)
    db.add(br)
    db.flush()
    prod.active_branch_id = br.id
    set_cast(db, user, prod, [c.model_dump() for c in body.cast])
    num = 0
    for s in range(1, body.seasons + 1):
        for e in range(1, body.episodes_per_season * parts + 1):
            num += 1
            title = f"{body.title} — S{s}E{e}" if body.kind == "series" else (
                f"{body.title} — Part {e}" if parts > 1 else body.title)
            _new_episode(db, user, prod, br, s, e, title[:120], per_ep)
    emit(db, prod.id, "production.created", {"kind": prod.kind, "episodes": num})
    return prod


def _fiction(text: str, rating: str, kind: str) -> None:
    from . import moderation

    d = moderation.check_fiction(text, rating, kind)
    if not d.allowed:
        raise ApiError(422, "content_blocked", "this text is not allowed in this production",
                       {"category": d.category, "flags": d.reasons})


def _limits(cfg: dict, seconds: int) -> dict:
    # the planner rounds scenes to whole shots: allow a small tolerance over the target, never over quota + 10 %
    return {"max_total_s": min(int(int(cfg["max_episode_seconds"]) * 1.1), int(seconds * 1.15) + 60),
            "max_shots": 400}


def _new_episode(db: Session, user: User, prod: Production, br: StoryBranch, season: int, number: int, title: str,
                 seconds: int, script: str | None = None, events: list | None = None) -> ProductionEpisode:
    cfg = require_enabled(db)
    project = StudioProject(user_id=user.id, title=title, aspect_ratio=prod.aspect_ratio, language=prod.language,
                            status="draft", limits=_limits(cfg, seconds),
                            content_policy={"fiction": True, "rating": prod.content_rating})
    db.add(project)
    db.flush()
    ep = ProductionEpisode(production_id=prod.id, branch_id=br.id, season=season, number=number, title=title,
                           target_duration_s=seconds, status="draft", studio_project_id=project.id, script=script,
                           events=events or [])
    db.add(ep)
    db.flush()
    project.production_episode_id = ep.id
    _sync_cast(db, user, prod, project)
    return ep


def get(db: Session, user: User, production_id: uuid.UUID) -> Production:
    p = db.get(Production, production_id)
    if p is None or p.owner_id != user.id or p.deleted_at is not None:
        raise not_found("production")
    return p


def episodes(db: Session, prod: Production, branch_id: uuid.UUID | None = None) -> list[ProductionEpisode]:
    return continuity.episodes_on(db, branch_id or prod.active_branch_id)


def get_episode(db: Session, prod: Production, number: int, season: int = 1) -> ProductionEpisode:
    for ep in episodes(db, prod):
        if ep.number == number and ep.season == season:
            return ep
    raise not_found("episode")


def project_of(db: Session, ep: ProductionEpisode) -> StudioProject:
    return db.get(StudioProject, ep.studio_project_id)


# ---------------------------------------------------------------- cast (V6 characters, unchanged data model)

def set_cast(db: Session, user: User, prod: Production, cast: list[dict]) -> None:
    from ..models import Character
    from . import characters

    out, aliases = [], set()
    for c in cast:
        ch = db.get(Character, uuid.UUID(str(c["character_id"])))
        if ch is None:
            raise not_found("character")
        ok, reason, _ = characters.usable_by(db, user.id, ch)
        if not ok:
            raise ApiError(403 if reason == "license_required" else 409, reason, "this character can't be cast",
                           {"character_id": str(ch.id)})
        alias = c.get("alias") or ch.handle
        if alias in aliases:
            raise ApiError(409, "alias_taken", "two cast members share an alias")
        aliases.add(alias)
        mode = c.get("lock_mode") or budget.config(db)[1]["profiles"][prod.profile]["lock_mode"]
        out.append({"character_id": str(ch.id), "alias": alias, "lock_mode": mode, "name": ch.display_name})
    prod.cast = out


def _sync_cast(db: Session, user: User, prod: Production, project: StudioProject) -> None:
    """Each episode project gets V6 cast snapshots (frozen identity version) for the production cast."""
    from . import casting

    have = {m.character_id: m for m in casting.members(db, project)}
    for c in prod.cast or []:
        cid = uuid.UUID(c["character_id"])
        if cid in have:
            continue
        casting.add(db, user, project, cid, None, c["alias"], c["lock_mode"], None, None)


def cast_keys(db: Session, project: StudioProject) -> tuple[dict[str, str], list[dict]]:
    """name/alias (lower) -> storyboard key, plus the CharacterRefs of the cast."""
    from . import casting

    keys, refs = {}, []
    for m in casting.members(db, project):
        d = casting.display(db, m.id)
        keys[m.alias] = m.alias
        keys[d["name"].lower()] = m.alias
        keys[screenplay.slug(d["name"])] = m.alias
        refs.append({"key": m.alias, "name": d["name"], "description": d["description"], "cast_member_id": str(m.id)})
    return keys, refs


# ---------------------------------------------------------------- planning

def plan_episode(db: Session, user: User, prod: Production, ep: ProductionEpisode, script: str | None,
                 brief: str | None) -> tuple[StudioProjectVersion, dict]:
    require_enabled(db)
    project = project_of(db, ep)
    cfg = studio.require_enabled(db)
    prof = budget.config(db)[1]["profiles"][prod.profile]
    _sync_cast(db, user, prod, project)
    if script:
        parsed = screenplay.parse(script)
        if not parsed:
            raise ApiError(422, "empty_script", "the screenplay has no scenes")
        keys, refs = cast_keys(db, project)
        try:
            frag, report = screenplay.plan(parsed, ep.target_duration_s, cfg["allowed_shot_durations"], keys,
                                           prod.visual_style)
        except ValueError as e:
            raise ApiError(422, "scene_too_long", str(e)) from e
        ref_keys = {r["key"] for r in refs}
        chars = refs + [{"key": k, "name": n[:60], "description": ""} for k, n in frag["characters"].items()
                        if k not in ref_keys]
        used = {k for sc in frag["scenes"] for sh in sc["shots"] for k in sh["characters"]} | \
            {d["character"] for sc in frag["scenes"] for sh in sc["shots"] for d in sh["dialogue"]}
        raw = {"title": ep.title, "language": prod.language, "aspect_ratio": prod.aspect_ratio,
               "style": "", "quality": prof["quality"], "visual_style": prod.visual_style,
               "characters": [c for c in chars if c["key"] in used], "scenes": frag["scenes"],
               "captions": {"enabled": True, "burn_in": False},
               "limitations": ["Planned by the rule-based screenplay planner (not AI). Dialogue is copied exactly.",
                               "Voice/lip-sync providers are not integrated: dialogue is shown as exact subtitles."]}
        sb = studio.validate_storyboard(db, user, raw, cfg, project)
        ep.script = script
        v = studio.add_version(db, user, project, sb, "screenplay", {"script": True, "report": report}, {
            "provider": "screenplay", "label": report["planner"]}, project.current_version_id)
        ep.plan_report = report
    elif brief:
        b = studio.Brief(brief=brief, title=ep.title, language=prod.language, aspect_ratio=prod.aspect_ratio,
                         target_duration_s=min(ep.target_duration_s, 180),  # V4 director: short briefs
                         quality=prof["quality"], cast_member_ids=[], creative_mode="faithful")
        v = studio.plan_storyboard(db, user, project, b)
        ep.plan_report = {"planner": v.director.get("label"), "brief": True}
    else:
        raise ApiError(422, "script_or_brief_required", "send a screenplay or a brief")
    findings = continuity.validate_episode(db, prod.id, ep.branch_id, ep, v.storyboard)
    continuity.record(db, ep, v.id, findings)
    ep.status = "planned"
    prod.status = "planning"
    emit(db, prod.id, "production.planned", {"episode": ep.number, "season": ep.season, "version": v.version,
                                             "shots": len(studio.Storyboard.model_validate(v.storyboard).shots())})
    if any(f["severity"] == "error" for f in findings):
        emit(db, prod.id, "continuity.flagged", {"episode": ep.number,
                                                 "errors": [f["code"] for f in findings if f["severity"] == "error"]})
    return v, {"findings": findings, "report": ep.plan_report}


# ---------------------------------------------------------------- previews + rendering

def animatic(db: Session, user: User, prod: Production, ep: ProductionEpisode) -> GenerationJob:
    project = project_of(db, ep)
    v = studio.get_version(db, project, None)
    job = studio.assemble_preview(db, user, project, v, "animatic")
    ep.animatic_job_id = job.id
    return job


def pilot_keys(sb: studio.Storyboard, seconds: tuple[int, int], keys: list[str] | None) -> list[str]:
    shots = sb.shots()
    if keys:
        unknown = set(keys) - {s.key for s in shots}
        if unknown:
            raise ApiError(422, "unknown_shots", "unknown shots", {"shots": sorted(unknown)})
        return list(keys)
    out, t = [], 0
    for s in shots:  # the opening sequence, 30-60 s by default
        if t >= seconds[0]:
            break
        out.append(s.key)
        t += s.duration_s
    if t > seconds[1] and len(out) > 1:
        out.pop()
    return out


def render(db: Session, user: User, prod: Production, ep: ProductionEpisode, purpose: str,
           confirmed_credits: int | None, idem: str, keys: list[str] | None = None) -> dict:
    cfg = require_enabled(db)
    project = project_of(db, ep)
    v = studio.get_version(db, project, None)
    errors = [f for f in continuity.validate_episode(db, prod.id, ep.branch_id, ep, v.storyboard)
              if f["severity"] == "error"]
    if errors:  # continuity errors must be fixed (or the story branched) before paid generation
        raise ApiError(409, "continuity_errors", "fix the continuity errors first", {"findings": errors})
    only = None
    if purpose == "pilot":
        only = pilot_keys(studio.Storyboard.model_validate(v.storyboard), tuple(cfg["pilot_seconds"]), keys)
    est = studio.estimate(db, user, project, v)
    if only is not None:
        est = studio.subset_estimate(est, only)
    if est["credits"]:
        budget.check_cap(db, prod, est["credits"])  # hard cap before any reservation
    out = studio.render(db, user, project, v.id, confirmed_credits, idem, only_keys=only, purpose=purpose)
    if purpose == "pilot":
        ep.pilot_keys = only
        ep.status = "pilot_rendering"
        _maybe_pilot_assembly(db, user, ep)
    else:
        ep.status = "generating"
    prod.status = "producing"
    emit(db, prod.id, "budget.charged" if est["credits"] else "render.requested",
         {"episode": ep.number, "purpose": purpose, "credits": est["credits"], "jobs": len(out["jobs"])})
    for j in out["jobs"]:
        emit(db, prod.id, "shot.queued", {"episode": ep.number, "job_id": j})
    return {**out, "pilot_keys": only}


def _maybe_pilot_assembly(db: Session, user: User, ep: ProductionEpisode) -> None:
    if not ep.pilot_keys or ep.pilot_job_id:
        return
    project = project_of(db, ep)
    v = studio.get_version(db, project, None)
    pairs = {s.key: r for s, r in studio._version_renders(db, project, v)}
    if all(pairs.get(k) is not None and pairs[k].status == "ready" for k in ep.pilot_keys):
        ep.pilot_job_id = studio.assemble_preview(db, user, project, v, "pilot", ep.pilot_keys).id


def on_episode_job(db: Session, project: StudioProject, job: GenerationJob, outcome: str) -> None:
    ep = db.get(ProductionEpisode, project.production_episode_id)
    if ep is None:
        return
    purpose = (job.spec or {}).get("purpose")
    if job.kind == JobKind.studio_shot:
        emit(db, ep.production_id, f"shot.{outcome}", {"episode": ep.number, "job_id": str(job.id),
                                                        "shot": (job.spec.get("shot") or {}).get("key"),
                                                        "error": job.error_code})
        if outcome == "completed" and ep.pilot_keys and not ep.pilot_job_id:
            _maybe_pilot_assembly(db, db.get(User, project.user_id), ep)
    elif outcome == "completed":
        emit(db, ep.production_id, "render.completed", {"episode": ep.number, "purpose": purpose or "full",
                                                        "job_id": str(job.id)})
    st = status(db, ep)["status"]
    if st in ("ready", "pilot_ready", "failed", "partial_failed"):  # persist milestones, not transient states
        ep.status = st


def status(db: Session, ep: ProductionEpisode) -> dict:
    """draft → planned → (estimated/approved at production level) → queued → generating → qc → ready |
    partial_failed | failed | cancelled, with per-shot progress."""
    project = project_of(db, ep)
    jobs = db.execute(select(GenerationJob).where(GenerationJob.studio_project_id == project.id)).scalars().all()
    shots = [j for j in jobs if j.kind == JobKind.studio_shot]
    latest: dict[str, GenerationJob] = {}
    for j in sorted(shots, key=lambda x: x.created_at):
        latest[j.spec.get("content_hash", str(j.id))] = j
    cur = list(latest.values())
    counts: dict[str, int] = {}
    for j in cur:
        counts[j.status.value] = counts.get(j.status.value, 0) + 1
    assemblies = [j for j in jobs if j.kind == JobKind.studio_assemble]
    phase = ep.status  # persisted milestone: draft|planned|pilot_rendering|pilot_ready|generating|ready|...|cancelled
    running = any(j.status in (JobStatus.preprocessing, JobStatus.generating) for j in cur + assemblies)
    if project.status == "ready":
        st = "ready"
    elif phase == "cancelled" and not any(j.status not in TERMINAL for j in cur):
        st = "cancelled"
    elif running:
        st = "generating"
    elif any(j.status == JobStatus.queued for j in cur + assemblies):
        st = "queued"
    elif any(j.status in (JobStatus.postprocessing, JobStatus.moderation) for j in cur + assemblies):
        st = "qc"
    elif cur and all(j.status == JobStatus.failed for j in cur):
        st = "failed"
    elif any(j.status == JobStatus.failed for j in cur):
        st = "partial_failed"
    elif project.current_version_id is None:
        st = "draft"
    elif phase in ("pilot_rendering", "pilot_ready") and cur and all(j.status == JobStatus.completed for j in cur):
        st = "pilot_ready"
    elif phase == "generating" and cur and all(j.status == JobStatus.completed for j in cur):
        st = "assembling"
    else:
        st = phase
    done = sum(1 for j in cur if j.status == JobStatus.completed)
    return {"status": st, "shots_total": len(cur), "shots_done": done, "by_status": counts,
            "progress": round(done / len(cur), 3) if cur else 0.0,
            "credits_charged": sum(j.credit_cost for j in jobs if j.billing_state == "settled"),
            "credits_reserved": sum(j.credit_cost for j in jobs if j.billing_state == "reserved"),
            "credits_refunded": sum(j.credit_cost for j in jobs if j.billing_state == "released"),
            "errors": [{"job_id": str(j.id), "shot": (j.spec.get("shot") or {}).get("key"), "error": j.error_code}
                       for j in cur if j.status == JobStatus.failed]}


def cancel(db: Session, user: User, prod: Production, ep: ProductionEpisode) -> dict:
    """User cancel: every unfinished job of the episode is cancelled; reserved credits come back."""
    from . import generation

    project = project_of(db, ep)
    jobs = db.execute(select(GenerationJob).where(GenerationJob.studio_project_id == project.id,
                                                  GenerationJob.status.notin_([s.value for s in TERMINAL]))
                      ).scalars().all()
    n = 0
    for j in jobs:
        generation.cancel_by_user(db, user, j.id)
        n += 1
    ep.status = "cancelled"
    for r in db.execute(select(StudioShotRender).where(StudioShotRender.project_id == project.id,
                                                       StudioShotRender.status == "queued")).scalars():
        r.status = "cancelled"
    if project.status == "rendering":
        project.status = "failed"
    emit(db, prod.id, "credits.adjusted", {"episode": ep.number, "cancelled_jobs": n})
    return {"cancelled_jobs": n}


# ---------------------------------------------------------------- story events, branches

def set_events(db: Session, prod: Production, ep: ProductionEpisode, events: list) -> dict:
    """Changing a past episode's events never rewrites it silently: if the episode is already approved/rendered
    (or later episodes exist), the caller must branch; otherwise events update with an impact preview."""
    parsed = continuity.parse_events(events)
    later = [e for e in episodes(db, prod) if (e.season, e.number) > (ep.season, ep.number)]
    preview = continuity.impact(db, prod.id, ep.branch_id, ep, parsed)
    from ..models import EpisodeSnapshot

    locked = ep.status == "ready" or db.execute(select(func.count()).select_from(EpisodeSnapshot).where(
        EpisodeSnapshot.episode_id == ep.id)).scalar_one() > 0
    if locked:
        raise ApiError(409, "episode_locked", "this episode is approved; create a story branch from it",
                       {"impact": preview})
    ep.events = parsed
    return {"events": parsed, "impact": preview, "later_episodes": len(later)}


def branch(db: Session, user: User, prod: Production, from_episode: int, season: int, reason: str,
           events: list | None) -> StoryBranch:
    """Fork the story at an episode: episodes from there on are copied (draft) into the new branch with their
    current storyboard; earlier ones stay shared. The previous branch is untouched."""
    src_eps = episodes(db, prod)
    target = next((e for e in src_eps if e.number == from_episode and e.season == season), None)
    if target is None:
        raise not_found("episode")
    br = StoryBranch(production_id=prod.id, parent_branch_id=prod.active_branch_id, fork_episode=from_episode,
                     name=f"branch from S{season}E{from_episode}", reason=reason[:300], created_by=user.id)
    db.add(br)
    db.flush()
    for e in src_eps:
        if (e.season, e.number) < (season, from_episode):
            continue
        new = _new_episode(db, user, prod, br, e.season, e.number, e.title, e.target_duration_s, e.script,
                           list(e.events or []))
        old_v = db.get(StudioProjectVersion, project_of(db, e).current_version_id) \
            if project_of(db, e).current_version_id else None
        if old_v is not None:
            p = project_of(db, new)
            cfg = studio.require_enabled(db)
            sb = studio.validate_storyboard(db, user, _remap_cast(db, project_of(db, e), p, old_v.storyboard), cfg, p)
            studio.add_version(db, user, p, sb, "branch", {"from_version": str(old_v.id)}, old_v.director, None)
            new.status = "planned"
        if e.id == target.id and events is not None:
            new.events = continuity.parse_events(events)
    prod.active_branch_id = br.id
    emit(db, prod.id, "story.branched", {"branch_id": str(br.id), "from_episode": from_episode, "reason": reason})
    return br


def _remap_cast(db: Session, old: StudioProject, new: StudioProject, sb: dict) -> dict:
    """Cast member ids are per project: point the copied storyboard at the new project's cast members."""
    from . import casting

    by_char = {m.character_id: m.id for m in casting.members(db, new)}
    out = {**sb, "characters": []}
    for c in sb.get("characters", []):
        c = dict(c)
        if c.get("cast_member_id"):
            from ..models import ProjectCastMember

            m = db.get(ProjectCastMember, uuid.UUID(c["cast_member_id"]))
            c["cast_member_id"] = str(by_char[m.character_id]) if m and m.character_id in by_char else None
            if c["cast_member_id"] is None:
                c.pop("cast_member_id")
        out["characters"].append(c)
    return out


def approve_episode(db: Session, prod: Production, ep: ProductionEpisode) -> dict:
    """Locks the episode's story state: immutable snapshot of the canonical world state after it."""
    project = project_of(db, ep)
    snap = continuity.snapshot(db, prod.id, ep.branch_id, ep, project.current_version_id)
    emit(db, prod.id, "episode.approved", {"episode": ep.number, "snapshot_id": str(snap.id)})
    return {"snapshot_id": str(snap.id), "state": snap.state}


# ---------------------------------------------------------------- export + publish checks

def export(db: Session, prod: Production, ep: ProductionEpisode) -> dict:
    from . import storage, timeline
    from .generation import output_key

    project = project_of(db, ep)
    if project.status != "ready" or project.output_job_id is None:
        raise ApiError(409, "episode_not_ready", "render the episode first")
    o = db.execute(select(GenerationOutput).where(GenerationOutput.job_id == project.output_job_id)).scalar_one()
    job = db.get(GenerationJob, project.output_job_id)
    st = storage.get_storage()
    cap = output_key(job, "captions.vtt")
    mock = any((r.metrics or {}).get("mock") or r.model in ("mock_t2v",) for r in _runs(db, project))
    out = {"video_url": st.presign_get(o.video_key, 900), "captions_url": st.presign_get(cap, 900) if st.head(cap)
           else None, "duration_ms": o.duration_ms, "timeline": timeline.export(db, project),
           "manifest": {"ai_generated": True, "label": "AI-generated fiction", "content_rating": prod.content_rating,
                        "production_id": str(prod.id), "episode": ep.number, "watermarked": o.watermarked,
                        "provenance": o.provenance, "test_mode": mock,
                        "note": "mock/test providers: not a production render" if mock else None}}
    emit(db, prod.id, "export.ready", {"episode": ep.number, "test_mode": mock})
    return out


def _runs(db: Session, project: StudioProject):
    from ..models import ModelRun

    return db.execute(select(ModelRun).join(GenerationJob, GenerationJob.id == ModelRun.job_id)
                      .where(GenerationJob.studio_project_id == project.id)).scalars().all()


def publish_check(db: Session, prod: Production) -> dict:
    """Publishing is off by default; this lists what blocks it. (A public production feed is not built.)"""
    blockers = ["publishing_not_available"]
    if prod.kind == "life_story" and not prod.people_confirmed:
        blockers.append("people_and_consent_not_confirmed")
    if prod.content_rating == "mature":
        blockers.append("age_gate_and_store_rating_required")
    errors = []
    for ep in episodes(db, prod):
        project = project_of(db, ep)
        if project.current_version_id:
            v = db.get(StudioProjectVersion, project.current_version_id)
            errors += [f for f in continuity.validate_episode(db, prod.id, ep.branch_id, ep, v.storyboard)
                       if f["severity"] == "error"]
    if errors:
        blockers.append("continuity_errors")
    return {"visibility": prod.visibility, "can_publish": False, "blockers": blockers}


def production_out(db: Session, prod: Production) -> dict:
    eps = episodes(db, prod)
    return {"id": str(prod.id), "kind": prod.kind, "title": prod.title, "logline": prod.logline, "genre": prod.genre,
            "format": prod.format, "visual_style": prod.visual_style, "language": prod.language,
            "aspect_ratio": prod.aspect_ratio, "content_rating": prod.content_rating, "profile": prod.profile,
            "status": prod.status, "visibility": prod.visibility, "cast": prod.cast,
            "active_branch_id": str(prod.active_branch_id) if prod.active_branch_id else None,
            "parts_per_episode": (prod.settings or {}).get("parts_per_episode", 1),
            "episodes": [{"id": str(e.id), "season": e.season, "number": e.number, "title": e.title,
                          "target_duration_s": e.target_duration_s, "status": e.status,
                          "studio_project_id": str(e.studio_project_id), "branch_id": str(e.branch_id)}
                         for e in eps],
            "budget": budget.report(db, prod)}
