"""Creator Studio API (spec §3): prompt-to-series wizard, Character DNA, scripts, cost preflight,
generation jobs, submission for review."""

import re
import tempfile
import time
import unicodedata
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import creator_user
from ..errors import AppError
from ..models import (
    AuditEvent,
    Character,
    CharacterVersion,
    Episode,
    GenerationJob,
    OutboxEvent,
    ProviderCall,
    RightsGrant,
    ScriptVersion,
    Season,
    Series,
    User,
    now,
)
from ..pipeline import orchestrator as orch
from ..providers import registry
from ..providers.base import ProviderUnavailable
from ..storage import put_file, signed_url
from ..story import Look, Scene, VoiceHint, WizardInput
from .moderation_rules import check_text
from .rights import active_grant_for

router = APIRouter(prefix="/studio", tags=["studio"])


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.translate(str.maketrans("ıİ", "iI"))).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80] or "series"


def _own_series(db: Session, sid: str, user: User) -> Series:
    s = db.get(Series, sid)
    if not s or (s.creator_id != user.id and user.role != "admin"):
        raise AppError("not_found", "Project not found", 404)
    return s


def _own_episode(db: Session, eid: str, user: User) -> Episode:
    ep = db.get(Episode, eid)
    if not ep:
        raise AppError("not_found", "Episode not found", 404)
    _own_series(db, ep.series_id, user)
    return ep


def _provider_error(e: ProviderUnavailable) -> AppError:
    return AppError("provider.unavailable", str(e), 503, provider=e.provider, missing=e.missing)


# ------------------------------------------------------------------------------------------ projects
@router.post("/projects", status_code=201)
def create_project(body: WizardInput, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    mod = check_text(f"{body.title or ''}\n{body.logline}")
    if not mod["ok"]:
        raise AppError("moderation.blocked", "Prompt violates content policy", 422, categories=mod["blocked"])
    try:
        writer = registry.llm()
        t0 = time.time()
        bible, info = writer.write_bible(body)
    except ProviderUnavailable as e:
        raise _provider_error(e) from e
    slug, i = slugify(bible.title), 1
    while db.scalar(select(Series).where(Series.slug == slug)):
        i += 1
        slug = f"{slugify(bible.title)}-{i}"
    series = Series(creator_id=user.id, slug=slug, title=bible.title, logline=bible.logline, genre=bible.genre,
                    language=bible.language, age_rating=body.audience_rating, visual_style=body.visual_style,
                    bible=bible.model_dump(exclude={"episodes", "characters"}),
                    settings={"wizard": body.model_dump(), "writer": info.provider, "writer_is_mock": info.is_mock},
                    ai_disclosure={"synthetic_media": True, "label": "AI-generated"})
    db.add(series)
    db.flush()
    season = Season(series_id=series.id, number=1, title=bible.season_arc[:150])
    db.add(season)
    db.flush()
    for c in bible.characters:
        ch = Character(series_id=series.id, owner_id=user.id, name=c.name, role=c.role, personality=c.personality,
                       dna={"key": c.key, "look": c.look.model_dump(), "want": c.want, "secret": c.secret,
                            "relationships": c.relationships, "wardrobe": [c.look.outfit, c.look.accent],
                            "scene_memory": []},
                       voice=c.voice.model_dump())
        db.add(ch)
        db.flush()
        db.add(CharacterVersion(character_id=ch.id, version=1, snapshot=_char_snapshot(ch)))
    for e in bible.episodes:
        _add_episode(db, series, season, e.number, e.title, e.synopsis, body.episode_duration_s,
                     {"scenes": [s.model_dump() for s in e.scenes], "cliffhanger": e.cliffhanger},
                     user.id, info.provider)
    db.add(ProviderCall(capability="llm", provider=info.provider, model=info.model, model_version=info.model_version,
                        is_mock=info.is_mock, units=info.units, cost_usd_micros=info.cost_usd_micros,
                        latency_ms=int((time.time() - t0) * 1000)))
    db.add(AuditEvent(actor_id=user.id, action="studio.project_created", target_type="series", target_id=series.id))
    db.commit()
    return project_detail(db, series)


def _add_episode(db, series, season, number, title, synopsis, dur, content, author_id, source) -> Episode:
    ep = Episode(series_id=series.id, season_id=season.id, number=number, title=title, synopsis=synopsis,
                 target_duration_s=dur, current_script_version=1)
    db.add(ep)
    db.flush()
    db.add(ScriptVersion(episode_id=ep.id, version=1, author_id=author_id, source=source, content=content))
    return ep


@router.get("/projects")
def list_projects(user: User = Depends(creator_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Series).where(Series.creator_id == user.id).order_by(Series.created_at.desc())).all()
    return [{"id": s.id, "title": s.title, "genre": s.genre, "status": s.status, "language": s.language,
             "episodes": sum(len(se.episodes) for se in s.seasons),
             "cover_url": _asset_url(db, s.cover_asset_id)} for s in rows]


def _asset_url(db: Session, aid: str | None) -> str | None:
    from ..models import Asset
    a = db.get(Asset, aid) if aid else None
    return signed_url(a.storage_key) if a else None


def _char_snapshot(ch: Character) -> dict:
    return {"name": ch.name, "role": ch.role, "personality": ch.personality, "dna": ch.dna, "voice": ch.voice,
            "likeness_source": ch.likeness_source}


def char_out(db: Session, ch: Character) -> dict:
    from ..models import Asset
    ref = db.scalar(select(Asset).where(Asset.character_id == ch.id, Asset.kind == "character_ref")
                    .order_by(Asset.created_at.desc()))
    from ..pipeline.realistic import reference_asset
    photo = reference_asset(db, ch.id, orch.dna_hash(ch.dna.get("look", {}), ch.voice))
    return {"id": ch.id, "key": ch.dna.get("key"), "name": ch.name,
            "reference_photo_url": signed_url(photo.storage_key) if photo else None, "role": ch.role, "personality": ch.personality,
            "look": ch.dna.get("look"), "voice": ch.voice, "version": ch.version, "locked": ch.locked,
            "likeness_source": ch.likeness_source, "blocked_reason": ch.blocked_reason,
            "dna_hash": orch.dna_hash(ch.dna.get("look", {}), ch.voice),
            "reference_sheet_url": signed_url(ref.storage_key) if ref else None}


def project_detail(db: Session, s: Series) -> dict:
    chars = db.scalars(select(Character).where(Character.series_id == s.id)).all()
    eps = []
    for se in s.seasons:
        for ep in se.episodes:
            job = db.scalar(select(GenerationJob).where(GenerationJob.episode_id == ep.id)
                            .order_by(GenerationJob.created_at.desc()))
            eps.append({"id": ep.id, "season": se.number, "number": ep.number, "title": ep.title,
                        "synopsis": ep.synopsis, "status": ep.status, "duration_s": ep.duration_s,
                        "script_version": ep.current_script_version, "qc_passed": (ep.qc_report or {}).get("passed"),
                        "thumbnail_url": _asset_url(db, ep.thumbnail_asset_id),
                        "latest_job": {"id": job.id, "status": job.status, "quality": job.quality,
                                       "progress": job.output.get("progress")} if job else None})
    return {"id": s.id, "slug": s.slug, "title": s.title, "logline": s.logline, "genre": s.genre,
            "language": s.language, "age_rating": s.age_rating, "status": s.status, "bible": s.bible,
            "settings": s.settings, "pricing": s.pricing, "characters": [char_out(db, c) for c in chars],
            "episodes": eps}


@router.get("/projects/{sid}")
def get_project(sid: str, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    return project_detail(db, _own_series(db, sid, user))


class ProjectPatch(BaseModel):
    title: str | None = Field(None, max_length=160)
    logline: str | None = None
    age_rating: str | None = Field(None, pattern=r"^(7\+|13\+|16\+|18\+)$")
    pricing: dict | None = None  # {"free_episodes":5,"episode_price_minor":99,...}
    regions_blocked: list[str] | None = None


@router.patch("/projects/{sid}")
def patch_project(sid: str, body: ProjectPatch, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    s = _own_series(db, sid, user)
    data = body.model_dump(exclude_none=True)
    if "pricing" in data:
        from .commerce import validate_pricing
        data["pricing"] = validate_pricing(data["pricing"])
    for k, v in data.items():
        setattr(s, k, v)
    db.commit()
    return project_detail(db, s)


@router.post("/projects/{sid}/episodes", status_code=201)
def add_episode(sid: str, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    """Write the next episode with the same writer, keeping cast and story bible."""
    s = _own_series(db, sid, user)
    season = s.seasons[-1]
    n = sum(len(se.episodes) for se in s.seasons) + 1
    wiz = WizardInput(**{**s.settings["wizard"], "episode_count": n})
    try:
        bible, info = registry.llm().write_bible(wiz)
    except ProviderUnavailable as e:
        raise _provider_error(e) from e
    e = bible.episodes[-1]
    keys = {c.dna.get("key") for c in db.scalars(select(Character).where(Character.series_id == s.id))}
    if not {ln.speaker for sc in e.scenes for ln in sc.lines} <= keys:
        raise AppError("studio.cast_mismatch", "Writer returned unknown characters; edit the script manually", 409)
    ep = _add_episode(db, s, season, len(season.episodes) + 1, e.title, e.synopsis, wiz.episode_duration_s,
                      {"scenes": [sc.model_dump() for sc in e.scenes], "cliffhanger": e.cliffhanger}, user.id,
                      info.provider)
    db.commit()
    return {"id": ep.id, "number": ep.number, "title": ep.title}


# ------------------------------------------------------------------------------------------ characters
class CharacterPatch(BaseModel):
    name: str | None = Field(None, max_length=80)
    personality: str | None = None
    look: Look | None = None
    voice: VoiceHint | None = None


@router.patch("/characters/{cid}")
def patch_character(cid: str, body: CharacterPatch, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    ch = db.get(Character, cid)
    if not ch:
        raise AppError("not_found", "Character not found", 404)
    _own_series(db, ch.series_id, user)
    if ch.locked:
        raise AppError("character.locked", "Character identity is locked; unlock to edit (creates a new version)", 409)
    if body.name is not None:
        ch.name = body.name
    if body.personality is not None:
        ch.personality = body.personality
    if body.look is not None:
        ch.dna = {**ch.dna, "look": body.look.model_dump()}
    if body.voice is not None:
        ch.voice = body.voice.model_dump()
    ch.version += 1
    db.add(CharacterVersion(character_id=ch.id, version=ch.version, snapshot=_char_snapshot(ch)))
    db.commit()
    return char_out(db, ch)


@router.post("/characters/{cid}/lock")
def lock_character(cid: str, locked: bool = True, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    ch = db.get(Character, cid)
    if not ch:
        raise AppError("not_found", "Character not found", 404)
    _own_series(db, ch.series_id, user)
    ch.locked = locked
    db.add(AuditEvent(actor_id=user.id, action=f"character.{'locked' if locked else 'unlocked'}",
                      target_type="character", target_id=ch.id, data={"version": ch.version}))
    db.commit()
    return char_out(db, ch)


@router.get("/characters/{cid}/versions")
def character_versions(cid: str, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    ch = db.get(Character, cid)
    if not ch:
        raise AppError("not_found", "Character not found", 404)
    _own_series(db, ch.series_id, user)
    rows = db.scalars(select(CharacterVersion).where(CharacterVersion.character_id == cid)
                      .order_by(CharacterVersion.version)).all()
    return [{"version": r.version, "snapshot": r.snapshot, "created_at": r.created_at} for r in rows]


@router.post("/characters/{cid}/reference-sheet")
def reference_sheet(cid: str, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    """Expression library: neutral + 6 emotions, rendered from the locked DNA (character reference)."""
    from PIL import Image, ImageDraw

    from ..pipeline.performer import render_bust
    ch = db.get(Character, cid)
    if not ch:
        raise AppError("not_found", "Character not found", 404)
    _own_series(db, ch.series_id, user)
    labels = ["neutral", "happy", "sad", "angry", "fear", "surprise", "tender"]
    sheet = Image.new("RGB", (4 * 260, 2 * 380), (24, 24, 32))
    d = ImageDraw.Draw(sheet)
    for i, emo in enumerate(labels):
        st = {"w": {} if emo == "neutral" else {emo: 0.95}, "viseme": "rest", "open": 0, "blink": 0,
              "gaze": (0, 0), "tilt": 0, "breath": 0, "tear_phase": 0.4}
        b = render_bust(ch.dna["look"], st, 340)
        x, y = (i % 4) * 260 + 10, (i // 4) * 380 + 10
        sheet.paste(b, (x, y), b)
        d.text((x + 8, y + 345), emo, fill=(230, 230, 230))
    d.text((3 * 260 + 20, 380 + 30), f"{ch.name}\nv{ch.version}\n{ch.role}", fill=(255, 255, 255))
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sheet.png"
        sheet.save(p)
        a = put_file(db, p, f"characters/{ch.id}/v{ch.version}/sheet.png", "character_ref", user.id,
                     {"version": ch.version, "provider": "portrait_local", "synthetic": True}, character_id=ch.id)
    db.commit()
    return {"asset_id": a.id, "url": signed_url(a.storage_key)}


class LikenessIn(BaseModel):
    rights_grant_id: str


@router.post("/characters/{cid}/likeness")
def attach_likeness(cid: str, body: LikenessIn, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    """Bind a real person's likeness to a character. Blocked unless an approved, unrevoked grant exists."""
    ch = db.get(Character, cid)
    if not ch:
        raise AppError("not_found", "Character not found", 404)
    _own_series(db, ch.series_id, user)
    g = db.get(RightsGrant, body.rights_grant_id)
    ch_probe = Character(owner_id=ch.owner_id, rights_grant_id=g.id if g else None)
    if not g or not active_grant_for(db, ch_probe):
        db.add(AuditEvent(actor_id=user.id, action="rights.likeness_blocked", target_type="character", target_id=cid,
                          data={"grant": body.rights_grant_id}))
        db.commit()
        raise AppError("rights.consent_required", "An approved, unrevoked consent grant for this person is required",
                       403)
    if g.scope.get("series_id") and g.scope["series_id"] != ch.series_id:
        raise AppError("rights.out_of_scope", "Grant does not cover this series", 403)
    ch.likeness_source, ch.rights_grant_id, ch.blocked_reason = "real_person", g.id, None
    ch.version += 1
    db.add(CharacterVersion(character_id=ch.id, version=ch.version, snapshot=_char_snapshot(ch)))
    db.add(AuditEvent(actor_id=user.id, action="rights.likeness_attached", target_type="character", target_id=cid,
                      data={"grant": g.id}))
    db.commit()
    return char_out(db, ch)


class FaceSwapIn(BaseModel):
    episode_id: str
    character_id: str
    source_asset_id: str


@router.post("/face-swap")
def face_swap(body: FaceSwapIn, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    """Consent-gated face replacement. Consent is checked first; then the provider (none contracted yet)."""
    ch = db.get(Character, body.character_id)
    if not ch:
        raise AppError("not_found", "Character not found", 404)
    _own_series(db, ch.series_id, user)
    if not active_grant_for(db, ch):
        db.add(AuditEvent(actor_id=user.id, action="rights.face_swap_blocked", target_type="character",
                          target_id=ch.id))
        db.commit()
        raise AppError("rights.consent_required", "Face replacement requires an approved consent grant", 403)
    raise AppError("provider.unavailable", "No face-replacement provider is contracted yet", 503,
                   provider="face_swap", missing="commercial provider + legal review")


# ------------------------------------------------------------------------------------------ scripts
@router.get("/episodes/{eid}/script")
def get_script(eid: str, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    ep = _own_episode(db, eid, user)
    sv = db.scalar(select(ScriptVersion).where(ScriptVersion.episode_id == eid,
                                               ScriptVersion.version == ep.current_script_version))
    return {"episode_id": eid, "version": sv.version, "source": sv.source, "content": sv.content}


class ScriptIn(BaseModel):
    scenes: list[Scene]
    cliffhanger: str = ""


@router.put("/episodes/{eid}/script")
def put_script(eid: str, body: ScriptIn, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    ep = _own_episode(db, eid, user)
    if ep.status in ("published", "in_review"):
        raise AppError("episode.immutable", "Published/in-review episodes cannot be edited", 409)
    mod = check_text("\n".join(ln.text for sc in body.scenes for ln in sc.lines))
    if not mod["ok"]:
        raise AppError("moderation.blocked", "Script violates content policy", 422, categories=mod["blocked"])
    keys = {c.dna.get("key") for c in db.scalars(select(Character).where(Character.series_id == ep.series_id))}
    bad = {ln.speaker for sc in body.scenes for ln in sc.lines} - keys
    if bad:
        raise AppError("script.unknown_character", f"Unknown speakers: {sorted(bad)}", 422)
    ep.current_script_version += 1
    db.add(ScriptVersion(episode_id=ep.id, version=ep.current_script_version, author_id=user.id, source="manual",
                         content={"scenes": [s.model_dump() for s in body.scenes], "cliffhanger": body.cliffhanger}))
    if ep.status in ("rendered", "rejected"):
        ep.status = "draft"
    db.commit()
    return {"version": ep.current_script_version}


@router.get("/episodes/{eid}/script/versions")
def script_versions(eid: str, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    _own_episode(db, eid, user)
    rows = db.scalars(select(ScriptVersion).where(ScriptVersion.episode_id == eid).order_by(ScriptVersion.version))
    return [{"version": r.version, "source": r.source, "created_at": r.created_at} for r in rows]


@router.post("/episodes/{eid}/script/revert/{version}")
def revert_script(eid: str, version: int, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    _own_episode(db, eid, user)
    old = db.scalar(select(ScriptVersion).where(ScriptVersion.episode_id == eid, ScriptVersion.version == version))
    if not old:
        raise AppError("not_found", "Version not found", 404)
    return put_script(eid, ScriptIn(**old.content), user, db)


# ------------------------------------------------------------------------------------------ generation
class EstimateIn(BaseModel):
    quality: str = Field("preview", pattern="^(preview|final)$")
    route: str = Field("preview_2d", pattern="^(preview_2d|realistic)$")


@router.post("/episodes/{eid}/estimate")
def estimate(eid: str, body: EstimateIn, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    ep = _own_episode(db, eid, user)
    try:
        spec = orch.build_spec(db, ep)
    except orch.StepError as e:
        raise AppError(e.code, e.detail, 422) from e
    from ..pipeline import realistic
    from . import ledger
    est = realistic.estimate(spec, body.quality, db) if body.route == "realistic" else orch.estimate(spec, body.quality)
    if body.route == "preview_2d":
        est["shadow_route_note"] = "2D preview engine; choose route=realistic for photoreal actors"
    return {**est, "balance": ledger.credit_balance(db, user.id),
            "realistic_available": _realistic_available()}


def _realistic_available() -> dict:
    try:
        orch.registry.google_media()
        return {"available": True}
    except ProviderUnavailable as e:
        return {"available": False, "missing": e.missing}


class GenerationIn(BaseModel):
    episode_id: str
    quality: str = Field("preview", pattern="^(preview|final)$")
    max_spend_credits: int | None = Field(None, ge=1)
    burn_captions: bool = True
    route: str = Field("preview_2d", pattern="^(preview_2d|realistic)$")


def job_out(job: GenerationJob) -> dict:
    return {"id": job.id, "episode_id": job.episode_id, "status": job.status, "quality": job.quality,
            "route": job.params.get("route", "preview_2d"),
            "estimate_credits": job.estimate_credits, "spent_credits": job.spent_credits,
            "max_spend_credits": job.max_spend_credits, "attempts": job.attempts, "error_code": job.error_code,
            "error_detail": job.error_detail, "output": job.output, "created_at": job.created_at,
            "finished_at": job.finished_at,
            "steps": [{"name": s.name, "status": s.status, "attempts": s.attempts, "cost_credits": s.cost_credits,
                       "error_code": s.error_code, "manifest": {k: v for k, v in s.manifest.items() if k != "outputs"}}
                      for s in job.steps]}


@router.post("/generations", status_code=202)
def create_generation(body: GenerationIn, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    ep = _own_episode(db, body.episode_id, user)
    if ep.status in ("published", "in_review", "approved"):
        raise AppError("episode.immutable", "Published or in-review episodes cannot be re-rendered", 409)
    active = db.scalar(select(GenerationJob).where(GenerationJob.episode_id == ep.id,
                                                   GenerationJob.status.in_(["queued", "running"])))
    if active:
        raise AppError("generation.in_progress", "A render is already running for this episode", 409, job_id=active.id)
    try:
        orch.registry.tts()
        orch.registry.music()
    except ProviderUnavailable as e:
        raise _provider_error(e) from e
    try:
        job = orch.create_job(db, owner_id=user.id, ep=ep, quality=body.quality, max_spend=body.max_spend_credits,
                              route=body.route)
    except orch.StepError as e:
        raise AppError(e.code, e.detail, 422) from e
    except ProviderUnavailable as e:
        raise _provider_error(e) from e
    job.params = {**job.params, "burn_captions": body.burn_captions}
    db.commit()
    from ..worker import kick
    kick()
    return job_out(job)


@router.get("/jobs/{jid}")
def get_job(jid: str, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    job = db.get(GenerationJob, jid)
    if not job or (job.owner_id != user.id and user.role != "admin"):
        raise AppError("not_found", "Job not found", 404)
    return job_out(job)


@router.post("/jobs/{jid}/cancel")
def cancel_job(jid: str, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    job = db.get(GenerationJob, jid)
    if not job or job.owner_id != user.id:
        raise AppError("not_found", "Job not found", 404)
    if job.status in ("queued", "running"):
        job.status = "cancelled"
        if job.episode_id:
            ep = db.get(Episode, job.episode_id)
            if ep.status == "rendering":
                ep.status = "draft"
        db.commit()
    return job_out(job)


@router.post("/jobs/{jid}/resume")
def resume_job(jid: str, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    """Resume a failed/cancelled job from its first unfinished step (completed steps are not re-billed)."""
    job = db.get(GenerationJob, jid)
    if not job or job.owner_id != user.id:
        raise AppError("not_found", "Job not found", 404)
    if job.status not in ("failed", "cancelled"):
        raise AppError("job.not_resumable", f"Job is {job.status}", 409)
    if job.output.get("refunded_credits"):
        raise AppError("job.refunded", "Job was refunded after failure; start a new generation", 409)
    job.status, job.error_code, job.error_detail, job.attempts = "queued", None, None, 0
    db.commit()
    from ..worker import kick
    kick()
    return job_out(job)


@router.post("/episodes/{eid}/submit")
def submit_episode(eid: str, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    ep = _own_episode(db, eid, user)
    if ep.status != "rendered" or not ep.video_asset_id:
        raise AppError("episode.not_rendered", "Render the episode before submitting", 409)
    flags = [c["id"] for c in ep.qc_report.get("checks", []) if not c["ok"]]
    ep.status = "in_review"
    db.add(OutboxEvent(type="EpisodeSubmitted", dedup_key=f"EpisodeSubmitted:{ep.id}:{now().timestamp()}",
                       payload={"episode_id": ep.id, "qc_failed": flags}))
    if not get_settings().manual_publish_gate and not flags and not ep.provenance.get("mock_components"):
        from .admin import publish_episode
        publish_episode(db, ep, actor_id=user.id)
    db.commit()
    return {"status": ep.status, "qc_failed": flags}


@router.get("/episodes/{eid}/preview")
def preview(eid: str, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    """Draft preview for the owner (before review/publish). Signed, short-lived URLs."""
    from ..models import Asset
    ep = _own_episode(db, eid, user)
    hls = db.get(Asset, ep.hls_asset_id) if ep.hls_asset_id else None
    return {"episode_id": ep.id, "status": ep.status, "qc": ep.qc_report, "provenance": ep.provenance,
            "duration_s": ep.duration_s,
            "hls_url": signed_url(f"{hls.storage_key}/master.m3u8", ttl_s=3600) if hls else None,
            "mp4_url": _asset_url(db, ep.video_asset_id), "captions_url": _asset_url(db, ep.captions_asset_id),
            "poster_url": _asset_url(db, ep.thumbnail_asset_id)}


@router.post("/characters/{cid}/reference-photo")
def reference_photo(cid: str, user: User = Depends(creator_user), db: Session = Depends(get_db)):
    """Photoreal reference portrait (Nano Banana) for the current DNA. Becomes the identity lock for the
    realistic route; regenerated only when the DNA changes. Billed in credits."""
    from ..pipeline import realistic
    from ..providers.google_media import ProviderError
    from ..providers.pricing import micros_to_credits
    from . import ledger
    ch = db.get(Character, cid)
    if not ch:
        raise AppError("not_found", "Character not found", 404)
    _own_series(db, ch.series_id, user)
    h = orch.dna_hash(ch.dna.get("look", {}), ch.voice)
    existing = realistic.reference_asset(db, ch.id, h)
    if existing:
        return {"url": signed_url(existing.storage_key), "reused": True}
    try:
        media = orch.registry.google_media()
    except ProviderUnavailable as e:
        raise _provider_error(e) from e
    cost = micros_to_credits(realistic.image_price_micros(get_settings().image_model))
    if ledger.credit_balance(db, user.id) < cost:
        raise AppError("credits.insufficient", "Not enough credits", 402, needed=cost)
    out = realistic.cache_dir("refs") / f"{h}.png"
    spec_char = {"name": ch.name, "look": ch.dna.get("look", {}), "voice": ch.voice}
    if not out.exists():
        try:
            info = media.image(
                f"Photorealistic head-and-shoulders portrait photograph of {realistic.describe(spec_char)}. Facing the "
                "camera, neutral expression, soft studio key light, plain dark grey background, sharp focus, natural "
                "skin texture. Fictional person, not a celebrity. No text.", [], out, seed=int(h[:6], 16))
        except ProviderError as e:
            raise AppError(e.code, e.detail, 502) from e
        ledger.consume_credits(db, user.id, micros_to_credits(info.cost_usd_micros), key=f"refphoto:{ch.id}:{h}",
                               memo="reference portrait")
        db.add(ProviderCall(capability="image", provider=info.provider, model=info.model, units=info.units,
                            cost_usd_micros=info.cost_usd_micros, seed=info.seed))
    a = put_file(db, out, f"characters/{ch.id}/photo-{h[:12]}.png", "character_ref_photo", user.id,
                 {"dna_hash": h, "provider": "google_gemini", "synthetic": True}, character_id=ch.id)
    db.commit()
    return {"url": signed_url(a.storage_key), "reused": False}
