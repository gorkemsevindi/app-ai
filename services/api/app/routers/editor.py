"""V8 editor API (shared by web, iOS and Android): canonical projects, command batches with optimistic
concurrency and idempotency, revisions/restore, render manifest, assets (presigned direct upload), exports."""

import uuid

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import client_ip, current_user
from ..errors import ApiError, not_found
from ..models import EditorAsset, EditorProject, EditorRevision, User
from ..services import editor, editor_engine, ratelimit
from ..services.generation import audit

router = APIRouter(prefix="/editor", tags=["editor"])


def _idem(key: str | None) -> str:
    if not key or not 8 <= len(key) <= 100:
        raise ApiError(422, "idempotency_key_required", "send an Idempotency-Key header (8-100 chars)")
    return key


@router.get("/config")
def config(db: Session = Depends(get_db)):
    ok, cfg = editor.config(db)
    return {"enabled": ok, "presets": editor.DATA["presets"],
            "templates": [{"key": t["key"], "title": t["title"], "type": t["type"]} for t in editor.DATA["templates"]],
            "formats": [*editor.FORMATS, "srt", "vtt"], "qualities": list(cfg["credits_per_second"]),
            "ai_tools": {"text_to_video": "studio", "image_to_video": "not_available", "inpainting": "not_available",
                         "background_remove": "not_available", "object_remove": "not_available",
                         "upscale": "not_available", "speech_to_text": "not_available", "voiceover": "not_available",
                         "lip_sync": "not_available"}}


class CreateIn(BaseModel):
    type: str = Field(default="video", pattern=r"^(video|photo|social|film|episode)$")
    title: str = Field(min_length=1, max_length=120)
    canvas: dict | None = None
    preset: str | None = None
    template: str | None = None


@router.post("/projects", status_code=201)
def create(body: CreateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ratelimit.hit("editor_create", str(user.id), 60)
    p = editor.create(db, user, body.type, body.title, body.canvas, body.preset, body.template)
    db.commit()
    return editor.out(p)


@router.get("/projects")
def list_projects(limit: int = Query(default=50, ge=1, le=100), before: str | None = None,
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(EditorProject).where(EditorProject.owner_id == user.id, EditorProject.deleted_at.is_(None))
    rows = db.execute(q.order_by(EditorProject.updated_at.desc()).limit(limit)).scalars().all()
    return {"items": [{"id": str(p.id), "type": p.type, "title": p.title, "revision": p.revision,
                       "updated_at": p.updated_at.isoformat()} for p in rows]}


@router.get("/projects/{project_id}")
def get(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return editor.out(editor.get(db, user, project_id))


@router.delete("/projects/{project_id}", status_code=204)
def delete(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from datetime import UTC, datetime

    editor.get(db, user, project_id).deleted_at = datetime.now(UTC)
    db.commit()


class CommandsIn(BaseModel):
    base_revision: int = Field(ge=0)
    commands: list[dict] = Field(min_length=1, max_length=1000)


@router.post("/projects/{project_id}/commands")
def commands(project_id: uuid.UUID, body: CommandsIn, request: Request, user: User = Depends(current_user),
             idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
             x_client: str | None = Header(default=None, alias="X-Client"), db: Session = Depends(get_db)):
    ratelimit.hit("editor_commands", str(user.id), 600)
    outp = editor.commands(db, user, project_id, body.base_revision, body.commands, _idem(idempotency_key), x_client)
    db.commit()
    return outp


@router.get("/projects/{project_id}/revisions")
def revisions(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = editor.get(db, user, project_id)
    rows = db.execute(select(EditorRevision).where(EditorRevision.project_id == p.id)
                      .order_by(EditorRevision.revision.desc()).limit(200)).scalars().all()
    return {"items": [{"revision": r.revision, "base_revision": r.base_revision, "commands": len(r.commands),
                       "client": r.client, "created_at": r.created_at.isoformat()} for r in rows]}


class RestoreIn(BaseModel):
    revision: int = Field(ge=0)


@router.post("/projects/{project_id}/restore")
def restore(project_id: uuid.UUID, body: RestoreIn, user: User = Depends(current_user),
            db: Session = Depends(get_db)):
    outp = editor.restore(db, user, project_id, body.revision)
    db.commit()
    return outp


@router.get("/projects/{project_id}/manifest")
def manifest(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return editor_engine.render_manifest(editor.get(db, user, project_id).document)


@router.get("/projects/{project_id}/subtitles.{fmt}", response_class=PlainTextResponse)
def subtitles(project_id: uuid.UUID, fmt: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if fmt not in ("srt", "vtt"):
        raise not_found("format")
    m = editor_engine.render_manifest(editor.get(db, user, project_id).document)
    text = editor_engine.to_srt(m["subtitles"]) if fmt == "srt" else editor_engine.to_vtt(m["subtitles"])
    return PlainTextResponse(text, media_type="text/vtt" if fmt == "vtt" else "application/x-subrip")


class RenderIn(BaseModel):
    format: str = Field(pattern=r"^(mp4|hevc|webm|png|jpeg|webp)$")
    quality: str = Field(default="720p", pattern=r"^(720p|1080p|2160p)$")
    confirmed_credits: int | None = None


@router.post("/projects/{project_id}/render/quote")
def render_quote(project_id: uuid.UUID, body: RenderIn, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    return editor.render_quote(db, editor.get(db, user, project_id), body.format, body.quality)


@router.post("/projects/{project_id}/render", status_code=202)
def render(project_id: uuid.UUID, body: RenderIn, request: Request, user: User = Depends(current_user),
           idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), db: Session = Depends(get_db)):
    ratelimit.hit("editor_render", str(user.id), 30)
    job = editor.render(db, user, project_id, body.format, body.quality, body.confirmed_credits,
                        _idem(idempotency_key))
    audit(db, user.id, "editor.render", "editor_project", str(project_id),
          {"format": body.format, "credits": job.credit_cost}, ip=client_ip(request))
    db.commit()
    return {"job_id": str(job.id), "status": job.status.value, "credits": job.credit_cost, "format": body.format}


@router.get("/projects/{project_id}/exports")
def exports(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"items": editor.exports(db, user, project_id)}


class AssetIn(BaseModel):
    kind: str = Field(pattern=r"^(video|image|audio|font)$")
    mime: str = Field(max_length=80)
    size_bytes: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=160)


@router.post("/assets", status_code=201)
def upload(body: AssetIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ratelimit.hit("editor_upload", str(user.id), 120)
    outp = editor.upload(db, user, body.kind, body.mime, body.size_bytes, body.name)
    db.commit()
    return outp


@router.post("/assets/{asset_id}/complete")
def complete(asset_id: uuid.UUID, body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = editor.complete_asset(db, user, asset_id, body)
    db.commit()
    return editor.asset_out(a)


@router.get("/assets")
def assets(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(EditorAsset).where(EditorAsset.owner_id == user.id, EditorAsset.status != "deleted")
                      .order_by(EditorAsset.created_at.desc()).limit(200)).scalars().all()
    return {"items": [editor.asset_out(a) for a in rows]}
