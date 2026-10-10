"""AI Studio API (V4 Stage B). Endpoint names follow the existing conventions (no /v1 prefix)."""

import uuid

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import client_ip, current_user
from ..models import StudioCharacter, StudioProject, StudioProjectVersion, User
from ..services import ratelimit, studio
from ..services.generation import audit

router = APIRouter(prefix="/studio", tags=["studio"])


class ProjectIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    aspect_ratio: str = Field(default="9:16", pattern=r"^(9:16|16:9|1:1)$")
    language: str = Field(default="en", max_length=8)
    budget_credits: int | None = Field(default=None, ge=1)


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    studio.require_enabled(db)
    studio._check_text(body.title)
    p = StudioProject(user_id=user.id, title=body.title, aspect_ratio=body.aspect_ratio, language=body.language,
                      budget_credits=body.budget_credits, status="draft")
    db.add(p)
    db.commit()
    return studio.project_out(db, p)


@router.get("/projects")
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(StudioProject).where(StudioProject.user_id == user.id,
                                                  StudioProject.deleted_at.is_(None))
                      .order_by(StudioProject.updated_at.desc()).limit(100)).scalars().all()
    return {"items": [{"id": str(p.id), "title": p.title, "status": p.status, "aspect_ratio": p.aspect_ratio,
                       "updated_at": p.updated_at.isoformat()} for p in rows]}


@router.get("/projects/{project_id}")
def get_project(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return studio.project_out(db, studio.get_project(db, user, project_id))


class BudgetIn(BaseModel):
    budget_credits: int | None = Field(default=None, ge=1)


@router.patch("/projects/{project_id}")
def set_budget(project_id: uuid.UUID, body: BudgetIn, user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    p = studio.get_project(db, user, project_id)
    p.budget_credits = body.budget_credits
    db.commit()
    return studio.project_out(db, p)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from datetime import UTC, datetime

    p = studio.get_project(db, user, project_id)
    p.deleted_at = datetime.now(UTC)
    db.commit()


@router.post("/projects/{project_id}/storyboard", status_code=201)
def plan(project_id: uuid.UUID, body: studio.Brief, user: User = Depends(current_user),
         db: Session = Depends(get_db)):
    """Director: brief -> storyboard (new immutable version) + estimate. Nothing is rendered or charged."""
    ratelimit.hit("studio_plan", str(user.id), 10)
    p = studio.get_project(db, user, project_id)
    v = studio.plan_storyboard(db, user, p, body)
    est = studio.estimate(db, user, p, v)
    db.commit()
    return {"version_id": str(v.id), "version": v.version, "storyboard": v.storyboard, "director": v.director,
            "creative": _creative_out(v.creative), "estimate": est}


def _creative_out(c: dict) -> dict:
    """What the user sees about the plan: mode, why this candidate, similarity warning (never a rejection)."""
    sim = c.get("similarity") or {}
    return {"mode": c.get("mode"), "strategy": c.get("strategy"), "composition": c.get("composition"),
            "seed": c.get("seed"), "intent": c.get("intent"), "candidates": c.get("candidates", []),
            "similarity": sim, "near_duplicate": sim.get("decision") == "near_duplicate",
            "suggestion": "This plan is close to one you (or the catalog) already have. Try a variation or "
                          "the Experimental mode." if sim.get("decision") == "near_duplicate" else None}


class VariationsIn(BaseModel):
    count: int = Field(default=2, ge=1, le=4)
    creative_mode: str | None = Field(default=None, pattern=r"^(faithful|balanced|experimental)$")


@router.post("/projects/{project_id}/variations", status_code=201)
def variations(project_id: uuid.UUID, body: VariationsIn, user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    ratelimit.hit("studio_plan", str(user.id), 10)
    p = studio.get_project(db, user, project_id)
    vs = studio.variations(db, user, p, body.count, body.creative_mode)
    out = [{"version_id": str(v.id), "version": v.version, "storyboard": v.storyboard,
            "creative": _creative_out(v.creative), "estimate": studio.estimate(db, user, p, v)} for v in vs]
    db.commit()
    return {"items": out, "current_version_id": str(p.current_version_id)}


class VersionIn(BaseModel):
    storyboard: dict
    parent_version_id: uuid.UUID | None = None


@router.post("/projects/{project_id}/versions", status_code=201)
def save_version(project_id: uuid.UUID, body: VersionIn, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    """Manual edit (timeline/storyboard UI): validated like director output, saved as a new version."""
    cfg = studio.require_enabled(db)
    p = studio.get_project(db, user, project_id)
    parent = studio.get_version(db, p, body.parent_version_id) if body.parent_version_id else None
    sb = studio.validate_storyboard(db, user, body.storyboard, cfg, p)
    v = studio.add_version(db, user, p, sb, "edit", {}, parent.director if parent else {},
                           parent.id if parent else p.current_version_id)
    est = studio.estimate(db, user, p, v)
    db.commit()
    return {"version_id": str(v.id), "version": v.version, "estimate": est}


@router.get("/projects/{project_id}/versions")
def list_versions(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = studio.get_project(db, user, project_id)
    rows = db.execute(select(StudioProjectVersion).where(StudioProjectVersion.project_id == p.id)
                      .order_by(StudioProjectVersion.version.desc())).scalars().all()
    return {"items": [{"id": str(v.id), "version": v.version, "source": v.source,
                       "parent_version_id": str(v.parent_version_id) if v.parent_version_id else None,
                       "created_at": v.created_at.isoformat(), "title": v.storyboard.get("title")} for v in rows]}


@router.post("/projects/{project_id}/revisions/{version_id}/restore", status_code=201)
def restore(project_id: uuid.UUID, version_id: uuid.UUID, user: User = Depends(current_user),
            db: Session = Depends(get_db)):
    cfg = studio.require_enabled(db)
    p = studio.get_project(db, user, project_id)
    old = studio.get_version(db, p, version_id)
    sb = studio.validate_storyboard(db, user, old.storyboard, cfg, p)  # consent/assets re-checked on restore
    v = studio.add_version(db, user, p, sb, "restore", old.brief, old.director, old.id)
    est = studio.estimate(db, user, p, v)
    db.commit()
    return {"version_id": str(v.id), "version": v.version, "estimate": est}


class EstimateIn(BaseModel):
    version_id: uuid.UUID | None = None


@router.post("/projects/{project_id}/estimate")
def estimate(project_id: uuid.UUID, body: EstimateIn, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    p = studio.get_project(db, user, project_id)
    return studio.estimate(db, user, p, studio.get_version(db, p, body.version_id))


class RenderIn(BaseModel):
    version_id: uuid.UUID | None = None
    confirmed_credits: int | None = None


@router.post("/projects/{project_id}/render", status_code=202)
def render(project_id: uuid.UUID, body: RenderIn, request: Request, user: User = Depends(current_user),
           idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=80),
           db: Session = Depends(get_db)):
    ratelimit.hit("generate", str(user.id), 6)
    studio.get_project(db, user, project_id)
    # serialize concurrent renders of one project (double tap / two devices)
    p = db.execute(select(StudioProject).where(StudioProject.id == project_id).with_for_update()).scalar_one()
    out = studio.render(db, user, p, body.version_id, body.confirmed_credits, idempotency_key)
    audit(db, user.id, "studio.render", "studio_project", str(p.id),
          {"version_id": out["version_id"], "credits": out["credits"], "jobs": len(out["jobs"])},
          ip=client_ip(request))
    db.commit()
    return out


@router.get("/projects/{project_id}/timeline")
def timeline(project_id: uuid.UUID, version_id: uuid.UUID | None = None, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    p = studio.get_project(db, user, project_id)
    v = studio.get_version(db, p, version_id)
    sb = studio.Storyboard.model_validate(v.storyboard)
    t, clips, captions = 0, [], []
    for sc in sb.scenes:
        for s in sc.shots:
            clips.append({"scene": sc.key, "shot": s.key, "start_ms": t, "end_ms": t + s.duration_s * 1000,
                          "transition": s.transition})
            for d in s.dialogue:
                captions.append({"shot": s.key, "start_ms": t + int(d.start_s * 1000), "text": d.text,
                                 "character": d.character})
            t += s.duration_s * 1000
    return {"version_id": str(v.id), "duration_ms": t, "aspect_ratio": sb.aspect_ratio,
            "tracks": {"video": clips, "captions": captions,
                       "music": [{"asset_id": str(sb.audio.music_asset_id), "start_ms": 0, "end_ms": t,
                                  "volume": sb.audio.music_volume}] if sb.audio.music_asset_id else []}}


# ---------------------------------------------------------------- characters

class CharacterIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=500)
    traits: dict = {}
    identity_profile_id: uuid.UUID | None = None
    attest_own_likeness: bool = False
    project_id: uuid.UUID | None = None
    actor_license_id: uuid.UUID | None = None


def _char_out(db: Session, ch: StudioCharacter) -> dict:
    c = studio.active_consent(db, ch)
    return {"id": str(ch.id), "name": ch.name, "description": ch.description, "traits": ch.traits,
            "project_id": str(ch.project_id) if ch.project_id else None,
            "has_likeness": ch.identity_profile_id is not None,
            "actor_license_id": str(ch.actor_license_id) if ch.actor_license_id else None,
            "consent": {"active": c is not None, "granted_at": c.granted_at.isoformat() if c else None},
            "usable": studio.character_usable(db, ch)}


@router.post("/characters", status_code=201)
def create_character(body: CharacterIn, request: Request, user: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    ch = studio.create_character(db, user, body.name, body.description, body.traits, body.identity_profile_id,
                                 body.attest_own_likeness, body.project_id, body.actor_license_id)
    if ch.identity_profile_id:
        audit(db, user.id, "studio.consent_granted", "studio_character", str(ch.id), {}, ip=client_ip(request))
    db.commit()
    return _char_out(db, ch)


@router.get("/characters")
def list_characters(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(StudioCharacter).where(StudioCharacter.user_id == user.id,
                                                    StudioCharacter.deleted_at.is_(None))).scalars().all()
    return {"items": [_char_out(db, c) for c in rows]}


@router.post("/characters/{character_id}/consents", status_code=201)
def grant(character_id: uuid.UUID, request: Request, attest_own_likeness: bool = False,
          user: User = Depends(current_user), db: Session = Depends(get_db)):
    ch = studio.get_character(db, user, character_id)
    if ch.identity_profile_id is None:
        return _char_out(db, ch)
    if not attest_own_likeness:
        from ..errors import ApiError

        raise ApiError(422, "consent_required", "confirm this is your own likeness and you allow its use")
    if studio.active_consent(db, ch) is None:
        studio.grant_consent(db, user, ch)
        audit(db, user.id, "studio.consent_granted", "studio_character", str(ch.id), {}, ip=client_ip(request))
    db.commit()
    return _char_out(db, ch)


@router.delete("/characters/{character_id}/consents")
def revoke(character_id: uuid.UUID, request: Request, user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    """Withdraw consent: no new renders with this likeness (also blocked at dispatch for queued shots)."""
    ch = studio.get_character(db, user, character_id)
    n = studio.revoke_consent(db, user, ch)
    audit(db, user.id, "studio.consent_revoked", "studio_character", str(ch.id), {"receipts": n},
          ip=client_ip(request))
    db.commit()
    return _char_out(db, ch)




# ---------------------------------------------------------------- Stage C: editing (chat + timeline)

class EditIn(BaseModel):
    instruction: str | None = Field(default=None, min_length=2, max_length=1000)
    ops: list[dict] | None = Field(default=None, max_length=20)
    base_version_id: uuid.UUID | None = None
    source: str = Field(default="chat", pattern=r"^(chat|timeline)$")
    auto_apply: bool = False  # timeline buttons: apply right away (still validated + versioned)


@router.post("/projects/{project_id}/edits", status_code=201)
def propose_edit(project_id: uuid.UUID, body: EditIn, request: Request, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    from ..services import studio_edits

    ratelimit.hit("studio_edit", str(user.id), 30)
    p = studio.get_project(db, user, project_id)
    e = studio_edits.propose(db, user, p, body.base_version_id, body.instruction, body.ops, body.source)
    if body.auto_apply and e.status == "proposed":
        v = studio_edits.apply(db, user, p, e)
        audit(db, user.id, "studio.edit_applied", "studio_project", str(p.id),
              {"edit_id": str(e.id), "version": v.version, "ops": [o["op"] for o in e.ops]}, ip=client_ip(request))
    db.commit()
    return studio_edits.edit_out(e)


@router.get("/projects/{project_id}/edits")
def list_edits(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..models import StudioEditOperation
    from ..services import studio_edits

    p = studio.get_project(db, user, project_id)
    rows = db.execute(select(StudioEditOperation).where(StudioEditOperation.project_id == p.id)
                      .order_by(StudioEditOperation.created_at.desc()).limit(50)).scalars().all()
    return {"items": [studio_edits.edit_out(e) for e in rows]}


@router.post("/projects/{project_id}/edits/{edit_id}/apply")
def apply_edit(project_id: uuid.UUID, edit_id: uuid.UUID, request: Request, user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    from ..services import studio_edits

    p = studio.get_project(db, user, project_id)
    p = db.execute(select(StudioProject).where(StudioProject.id == p.id).with_for_update()).scalar_one()
    e = studio_edits.get_edit(db, p, edit_id)
    v = studio_edits.apply(db, user, p, e)
    audit(db, user.id, "studio.edit_applied", "studio_project", str(p.id),
          {"edit_id": str(e.id), "version": v.version, "ops": [o["op"] for o in e.ops]}, ip=client_ip(request))
    est = studio.estimate(db, user, p, v)
    db.commit()
    return {**studio_edits.edit_out(e), "version": v.version, "estimate": est}


@router.post("/projects/{project_id}/edits/{edit_id}/reject")
def reject_edit(project_id: uuid.UUID, edit_id: uuid.UUID, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    from ..errors import ApiError
    from ..services import studio_edits

    p = studio.get_project(db, user, project_id)
    e = studio_edits.get_edit(db, p, edit_id)
    if e.status == "applied":
        raise ApiError(409, "edit_not_applicable", "applied edits are undone with /undo")
    e.status = "rejected"
    db.commit()
    return studio_edits.edit_out(e)


@router.post("/projects/{project_id}/undo", status_code=201)
def undo(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..services import studio_edits

    p = studio.get_project(db, user, project_id)
    v = studio_edits.undo(db, user, p)
    est = studio.estimate(db, user, p, v)
    db.commit()
    return {"version_id": str(v.id), "version": v.version, "estimate": est}


@router.post("/projects/{project_id}/redo", status_code=201)
def redo(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..services import studio_edits

    p = studio.get_project(db, user, project_id)
    v = studio_edits.redo(db, user, p)
    est = studio.estimate(db, user, p, v)
    db.commit()
    return {"version_id": str(v.id), "version": v.version, "estimate": est}


class ExtendIn(BaseModel):
    direction: str = Field(default="end", pattern=r"^(end|start)$")
    seconds: int = Field(ge=1, le=20)
    prompt: str | None = Field(default=None, min_length=5, max_length=1500)


@router.post("/projects/{project_id}/shots/{shot_key}/extend", status_code=201)
def extend_shot(project_id: uuid.UUID, shot_key: str, body: ExtendIn, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    """Timeline shortcut: propose an extension (or prepend) of a rendered shot, with its incremental price."""
    from ..services import studio_edits

    p = studio.get_project(db, user, project_id)
    op = {"op": "extend_shot", "shot": shot_key, "direction": body.direction, "seconds": body.seconds}
    if body.prompt:
        op["prompt"] = body.prompt
    e = studio_edits.propose(db, user, p, None, None, [op], "timeline")
    db.commit()
    return studio_edits.edit_out(e)


@router.post("/projects/{project_id}/shots/{shot_key}/inpaint")
def inpaint_shot(project_id: uuid.UUID, shot_key: str, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    """Video inpainting is offered only when a provider supports it; none does yet -> safe rejection."""
    from ..errors import ApiError

    studio.get_project(db, user, project_id)
    _, cfg = studio.config(db)
    if "VIDEO_INPAINT" not in set(cfg["provider_capabilities"].get(cfg["shot_provider"], [])):
        raise ApiError(422, "capability_unsupported", "the video provider can't inpaint",
                       {"missing": ["VIDEO_INPAINT"]})
    raise ApiError(501, "not_implemented", "inpainting is not implemented yet")
