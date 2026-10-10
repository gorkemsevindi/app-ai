"""V7 AI Cinema & Short Drama Factory API (adapted to the existing router style: `/productions`, `/life-stories`).
Episodes are Studio projects; Studio endpoints keep working on them."""

import uuid

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import client_ip, current_user
from ..errors import ApiError
from ..models import ContinuityFinding, Production, ProductionEvent, User
from ..services import budget, continuity, dialogue, lifestory, productions, ratelimit, studio, timeline
from ..services.generation import audit

router = APIRouter(tags=["productions"])

ENTRY = [
    {"kind": "series", "title_tr": "Kendi Dizini Üret", "title_en": "Create Your Own Series"},
    {"kind": "film", "title_tr": "Kendi Filmini Üret", "title_en": "Make Your Own Movie"},
    {"kind": "stars", "title_tr": "Kendi Yıldızlarını Yarat", "title_en": "Create Your Own Stars"},
    {"kind": "life_story", "title_tr": "Kendi Hayatını, Kendi Hikâyeni Anlat", "title_en": "Your Life. Your Story."},
]


def _idem(key: str | None) -> str:
    if not key or not 8 <= len(key) <= 80:
        raise ApiError(422, "idempotency_key_required", "send an Idempotency-Key header (8-80 chars)")
    return key


@router.get("/productions/entry")
def entry(db: Session = Depends(get_db)):
    ok, cfg = budget.config(db)
    return {"question_tr": "Hangi hikâyeyi anlatmak istersin?", "question_en": "Which story do you want to tell?",
            "slogan": "YOUR STORY. YOUR STARS. YOUR CINEMA.", "cards": ENTRY, "enabled": ok,
            "formats": cfg["formats"], "styles": list(studio.VISUAL_STYLES), "profiles": list(budget.PROFILES)}


@router.post("/productions", status_code=201)
def create(body: productions.ProductionIn, request: Request, user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    ratelimit.hit("production_create", str(user.id), 20)
    p = productions.create(db, user, body)
    audit(db, user.id, "production.created", "production", str(p.id), {"kind": p.kind, "format": p.format,
                                                                        "rating": p.content_rating},
          ip=client_ip(request))
    db.commit()
    return productions.production_out(db, p)


@router.get("/productions")
def my_productions(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Production).where(Production.owner_id == user.id, Production.deleted_at.is_(None))
                      .order_by(Production.created_at.desc())).scalars().all()
    return {"items": [{"id": str(p.id), "kind": p.kind, "title": p.title, "status": p.status, "format": p.format}
                      for p in rows]}


@router.get("/productions/{production_id}")
def get(production_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return productions.production_out(db, productions.get(db, user, production_id))


@router.delete("/productions/{production_id}", status_code=204)
def delete(production_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from datetime import UTC, datetime

    p = productions.get(db, user, production_id)
    for ep in productions.episodes(db, p):
        productions.cancel(db, user, p, ep)
        productions.project_of(db, ep).deleted_at = datetime.now(UTC)
    p.deleted_at = datetime.now(UTC)
    db.commit()


class CastBody(BaseModel):
    cast: list[productions.CastIn] = Field(max_length=20)


@router.put("/productions/{production_id}/cast")
def set_cast(production_id: uuid.UUID, body: CastBody, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    p = productions.get(db, user, production_id)
    productions.set_cast(db, user, p, [c.model_dump() for c in body.cast])
    for ep in productions.episodes(db, p):
        productions._sync_cast(db, user, p, productions.project_of(db, ep))
    db.commit()
    return {"cast": p.cast}


@router.put("/productions/{production_id}/bible")
def put_bible(production_id: uuid.UUID, body: dict, user: User = Depends(current_user),
              db: Session = Depends(get_db)):
    p = productions.get(db, user, production_id)
    for text in [body.get("world", ""), *[c.get("bio", "") for c in body.get("characters", [])]]:
        productions._fiction(text, p.content_rating, "dialogue")
    b = continuity.save_bible(db, p.id, user.id, body)
    db.commit()
    return {"version": b.version, "bible": b.data}


@router.get("/productions/{production_id}/bible")
def get_bible(production_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = productions.get(db, user, production_id)
    b = continuity.latest_bible(db, p.id)
    return {"version": b.version if b else 0, "bible": b.data if b else continuity.Bible().model_dump()}


def _ep(db, user, production_id, number, season):
    p = productions.get(db, user, production_id)
    return p, productions.get_episode(db, p, number, season)


class PlanIn(BaseModel):
    script: str | None = Field(default=None, max_length=200_000)
    brief: str | None = Field(default=None, max_length=4000)


@router.post("/productions/{production_id}/episodes/{number}/plan", status_code=201)
def plan(production_id: uuid.UUID, number: int, body: PlanIn, season: int = 1, user: User = Depends(current_user),
         db: Session = Depends(get_db)):
    ratelimit.hit("production_plan", str(user.id), 60)
    p, ep = _ep(db, user, production_id, number, season)
    v, info = productions.plan_episode(db, user, p, ep, body.script, body.brief)
    est = studio.estimate(db, user, productions.project_of(db, ep), v)
    db.commit()
    return {"version_id": str(v.id), "version": v.version, "storyboard": v.storyboard, **info,
            "estimate": {k: est[k] for k in ("credits", "new_shots", "reused_shots", "provider",
                                             "missing_capabilities", "blocked", "limitations")}}


@router.get("/productions/{production_id}/episodes/{number}")
def episode(production_id: uuid.UUID, number: int, season: int = 1, user: User = Depends(current_user),
            db: Session = Depends(get_db)):
    p, ep = _ep(db, user, production_id, number, season)
    project = productions.project_of(db, ep)
    out = studio.project_out(db, project)
    findings = db.execute(select(ContinuityFinding).where(ContinuityFinding.episode_id == ep.id,
                                                          ContinuityFinding.studio_version_id ==
                                                          project.current_version_id)).scalars().all()
    return {"episode": {"id": str(ep.id), "number": ep.number, "season": ep.season, "title": ep.title,
                        "target_duration_s": ep.target_duration_s, "events": ep.events,
                        "plan_report": ep.plan_report, "pilot_keys": ep.pilot_keys},
            "status": productions.status(db, ep), "project": out,
            "continuity": [{"severity": f.severity, "code": f.code, "message": f.message, "details": f.details}
                           for f in findings]}


@router.get("/productions/{production_id}/episodes/{number}/continuity")
def check_continuity(production_id: uuid.UUID, number: int, season: int = 1, user: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    p, ep = _ep(db, user, production_id, number, season)
    sb = continuity._current_storyboard(db, ep)
    return {"findings": continuity.validate_episode(db, p.id, ep.branch_id, ep, sb),
            "state_before": continuity.state_before(db, p.id, ep.branch_id, ep)}


class EventsIn(BaseModel):
    events: list[dict] = Field(max_length=100)


@router.put("/productions/{production_id}/episodes/{number}/events")
def set_events(production_id: uuid.UUID, number: int, body: EventsIn, season: int = 1,
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, ep = _ep(db, user, production_id, number, season)
    out = productions.set_events(db, p, ep, body.events)
    db.commit()
    return out


@router.post("/productions/{production_id}/episodes/{number}/events/impact")
def events_impact(production_id: uuid.UUID, number: int, body: EventsIn, season: int = 1,
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, ep = _ep(db, user, production_id, number, season)
    return {"impact": continuity.impact(db, p.id, ep.branch_id, ep, continuity.parse_events(body.events))}


@router.post("/productions/{production_id}/episodes/{number}/approve")
def approve_episode(production_id: uuid.UUID, number: int, season: int = 1, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    p, ep = _ep(db, user, production_id, number, season)
    out = productions.approve_episode(db, p, ep)
    db.commit()
    return out


class BranchIn(BaseModel):
    from_episode: int = Field(ge=1)
    season: int = Field(default=1, ge=1)
    reason: str = Field(min_length=3, max_length=300)
    events: list[dict] | None = None


@router.post("/productions/{production_id}/branches", status_code=201)
def branch(production_id: uuid.UUID, body: BranchIn, user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    p = productions.get(db, user, production_id)
    br = productions.branch(db, user, p, body.from_episode, body.season, body.reason, body.events)
    db.commit()
    return {"branch_id": str(br.id), "fork_episode": br.fork_episode, "production": productions.production_out(db, p)}


class EstimateIn(BaseModel):
    episodes: list[int] | None = None  # episode numbers of the active branch (season 1 unless "s:e")
    profile: str | None = None
    cap_credits: int | None = Field(default=None, ge=0)
    cap_minor: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    purpose: str = Field(default="full", pattern=r"^(full|pilot)$")


@router.post("/productions/{production_id}/estimate")
def estimate(production_id: uuid.UUID, body: EstimateIn, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    p = productions.get(db, user, production_id)
    eps = productions.episodes(db, p)
    if body.episodes:
        eps = [e for e in eps if e.number in body.episodes]
    e = budget.estimate(db, user, p, eps, body.profile, body.cap_credits, body.cap_minor, body.currency,
                        body.purpose)
    db.commit()
    return budget.estimate_out(e)


class ApproveIn(BaseModel):
    estimate_id: uuid.UUID
    cap_credits: int | None = Field(default=None, ge=0)


@router.post("/productions/{production_id}/approve", status_code=201)
def approve(production_id: uuid.UUID, body: ApproveIn, request: Request, user: User = Depends(current_user),
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
            db: Session = Depends(get_db)):
    p = productions.get(db, user, production_id)
    a = budget.approve(db, user, p, body.estimate_id, body.cap_credits, _idem(idempotency_key))
    productions.emit(db, p.id, "budget.approved", {"cap_credits": a.cap_credits, "authorization_id": str(a.id)})
    audit(db, user.id, "production.budget_approved", "production", str(p.id), {"cap_credits": a.cap_credits},
          ip=client_ip(request))
    db.commit()
    return {"id": str(a.id), "cap_credits": a.cap_credits, "currency": a.currency, "cap_minor": a.cap_minor,
            "status": a.status}


@router.get("/productions/{production_id}/budget")
def budget_report(production_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return budget.report(db, productions.get(db, user, production_id))


@router.post("/productions/{production_id}/episodes/{number}/animatic", status_code=202)
def animatic(production_id: uuid.UUID, number: int, season: int = 1, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    p, ep = _ep(db, user, production_id, number, season)
    job = productions.animatic(db, user, p, ep)
    db.commit()
    return {"job_id": str(job.id), "credits": 0, "label": job.spec.get("label")}


class RenderIn(BaseModel):
    confirmed_credits: int | None = None
    shots: list[str] | None = Field(default=None, max_length=40)


@router.post("/productions/{production_id}/episodes/{number}/pilot", status_code=202)
def pilot(production_id: uuid.UUID, number: int, body: RenderIn, season: int = 1,
          user: User = Depends(current_user),
          idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
          db: Session = Depends(get_db)):
    p, ep = _ep(db, user, production_id, number, season)
    out = productions.render(db, user, p, ep, "pilot", body.confirmed_credits, _idem(idempotency_key), body.shots)
    db.commit()
    return out


@router.post("/productions/{production_id}/episodes/{number}/render", status_code=202)
def render(production_id: uuid.UUID, number: int, body: RenderIn, season: int = 1,
           user: User = Depends(current_user),
           idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
           db: Session = Depends(get_db)):
    """Full (or resumed) render: unchanged/already rendered shots are reused at no cost."""
    p, ep = _ep(db, user, production_id, number, season)
    out = productions.render(db, user, p, ep, "full", body.confirmed_credits, _idem(idempotency_key))
    db.commit()
    return out


@router.post("/productions/{production_id}/episodes/{number}/cancel")
def cancel(production_id: uuid.UUID, number: int, season: int = 1, user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    p, ep = _ep(db, user, production_id, number, season)
    out = productions.cancel(db, user, p, ep)
    db.commit()
    return out


@router.post("/productions/{production_id}/episodes/{number}/export")
def export(production_id: uuid.UUID, number: int, season: int = 1, user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    p, ep = _ep(db, user, production_id, number, season)
    out = productions.export(db, p, ep)
    db.commit()
    return out


@router.get("/productions/{production_id}/publish-check")
def publish_check(production_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return productions.publish_check(db, productions.get(db, user, production_id))


@router.put("/productions/{production_id}/people-confirmed")
def people_confirmed(production_id: uuid.UUID, body: dict, user: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    """The user confirms everyone named in the story consented (or was anonymised). Required before sharing."""
    p = productions.get(db, user, production_id)
    if body.get("confirm") is not True:
        raise ApiError(422, "confirmation_required", "confirm the people/consent check explicitly")
    p.people_confirmed = True
    db.commit()
    return {"people_confirmed": True}


@router.get("/productions/{production_id}/events")
def events(production_id: uuid.UUID, after: int = 0, user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    p = productions.get(db, user, production_id)
    rows = db.execute(select(ProductionEvent).where(ProductionEvent.production_id == p.id,
                                                    ProductionEvent.id > after)
                      .order_by(ProductionEvent.id).limit(500)).scalars().all()
    return {"items": [{"id": e.id, "type": e.type, "payload": e.payload, "created_at": e.created_at.isoformat(),
                       "delivered": e.delivered_at is not None} for e in rows]}


# ---------------------------------------------------------------- dialogue editor, range edit, timeline, undo


@router.get("/productions/{production_id}/episodes/{number}/dialogue")
def list_lines(production_id: uuid.UUID, number: int, season: int = 1, user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    p, ep = _ep(db, user, production_id, number, season)
    return {"items": dialogue.lines(db, productions.project_of(db, ep)), "content_rating": p.content_rating}


@router.patch("/productions/{production_id}/episodes/{number}/dialogue/{line_id}")
def edit_line(production_id: uuid.UUID, number: int, line_id: str, body: dialogue.LinePatch, season: int = 1,
              apply: bool = Query(default=False), unlock: bool = Query(default=False),
              user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, ep = _ep(db, user, production_id, number, season)
    out = dialogue.edit_line(db, user, productions.project_of(db, ep), line_id, body, unlock, apply, p, ep)
    db.commit()
    return out


class VariantIn(BaseModel):
    text: str = Field(min_length=1, max_length=600)


@router.post("/productions/{production_id}/episodes/{number}/dialogue/{line_id}/variant", status_code=201)
def line_variant(production_id: uuid.UUID, number: int, line_id: str, body: VariantIn, season: int = 1,
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, ep = _ep(db, user, production_id, number, season)
    v = dialogue.variant(db, user, productions.project_of(db, ep), line_id, body.text)
    db.commit()
    return {"version_id": str(v.id), "version": v.version, "current": False}


@router.get("/productions/{production_id}/episodes/{number}/dialogue/{line_id}/history")
def line_history(production_id: uuid.UUID, number: int, line_id: str, season: int = 1,
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, ep = _ep(db, user, production_id, number, season)
    return {"items": dialogue.history(db, productions.project_of(db, ep), line_id)}


class RangeIn(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=1)
    change: dialogue.RangeChange


@router.post("/productions/{production_id}/episodes/{number}/range-edit", status_code=201)
def range_edit(production_id: uuid.UUID, number: int, body: RangeIn, season: int = 1,
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..services import studio_edits

    p, ep = _ep(db, user, production_id, number, season)
    e, plan_ = dialogue.range_edit(db, user, productions.project_of(db, ep), body.start_ms, body.end_ms, body.change)
    db.commit()
    return {"edit": studio_edits.edit_out(e), "plan": plan_,
            "apply": f"/studio/projects/{ep.studio_project_id}/edits/{e.id}/apply"}


@router.get("/productions/{production_id}/episodes/{number}/timeline")
def get_timeline(production_id: uuid.UUID, number: int, season: int = 1, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    p, ep = _ep(db, user, production_id, number, season)
    return timeline.export(db, productions.project_of(db, ep))


@router.post("/productions/{production_id}/episodes/{number}/timeline", status_code=201)
def import_timeline(production_id: uuid.UUID, number: int, body: dict, season: int = 1,
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, ep = _ep(db, user, production_id, number, season)
    v = timeline.import_(db, user, productions.project_of(db, ep), body)
    db.commit()
    return {"version_id": str(v.id), "version": v.version}


# ---------------------------------------------------------------- life story

@router.post("/life-stories", status_code=201)
def life_start(user: User = Depends(current_user), db: Session = Depends(get_db)):
    budget.require_enabled(db)
    s = lifestory.start(db, user)
    db.commit()
    return {"id": str(s.id), "questions": lifestory.QUESTIONS, "private": True}


def _ls(db, user, sid):
    return lifestory.get(db, user, sid)


@router.get("/life-stories/{sid}")
def life_get(sid: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = _ls(db, user, sid)
    return {"id": str(s.id), "answers": s.answers, "chronology": s.chronology, "privacy": s.privacy,
            "approved": s.approved_at is not None, "production_id": str(s.production_id) if s.production_id else None}


class AnswerIn(BaseModel):
    key: str
    text: str = Field(min_length=1, max_length=4000)


@router.put("/life-stories/{sid}/answers")
def life_answer(sid: uuid.UUID, body: AnswerIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = _ls(db, user, sid)
    lifestory.answer(db, s, body.key, body.text)
    db.commit()
    return {"answers": s.answers}


class ChronoIn(BaseModel):
    events: list[dict] | None = Field(default=None, max_length=60)  # omit to draft from the answers


@router.post("/life-stories/{sid}/chronology")
def life_chronology(sid: uuid.UUID, body: ChronoIn, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    s = _ls(db, user, sid)
    lifestory.set_chronology(db, s, body.events if body.events is not None else lifestory.draft_chronology(s))
    db.commit()
    return {"chronology": s.chronology, "privacy": s.privacy}


@router.post("/life-stories/{sid}/anonymise")
def life_anonymise(sid: uuid.UUID, body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = _ls(db, user, sid)
    lifestory.anonymise(s, {str(k): str(v)[:60] for k, v in (body.get("mapping") or {}).items()})
    db.commit()
    return {"chronology": s.chronology, "privacy": s.privacy}


class LifeApproveIn(BaseModel):
    confirm_privacy: bool = False
    title: str = Field(min_length=1, max_length=120)
    format: str = Field(default="short_film", pattern=r"^(micro|short_series|standard_episode|long_episode|"
                                                       r"short_film|feature)$")
    episode_duration_s: int = Field(default=600, ge=30, le=10800)
    visual_style: str = "cinematic"
    content_rating: str = Field(default="general", pattern=r"^(general|teen|mature)$")


@router.post("/life-stories/{sid}/approve", status_code=201)
def life_approve(sid: uuid.UUID, body: LifeApproveIn, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    s = _ls(db, user, sid)
    lifestory.approve(s, body.confirm_privacy)
    p = productions.create(db, user, productions.ProductionIn(
        kind="life_story", title=body.title, format=body.format, episode_duration_s=body.episode_duration_s,
        visual_style=body.visual_style, content_rating=body.content_rating))
    s.production_id = p.id
    ep = productions.episodes(db, p)[0]
    ep.script = lifestory.screenplay_text(s)  # suggestion: the user reviews it, then plans the episode
    db.commit()
    return {"production": productions.production_out(db, p), "suggested_script": ep.script}
