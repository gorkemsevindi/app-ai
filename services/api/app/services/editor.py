"""V8 Unified Creative Studio — server side of the shared editor (web, iOS, Android).

- Canonical projects (schema cp1) change only through commands, applied by `editor_engine` (the port of the
  shared TS engine). The server is authoritative: clients send `base_revision` + an Idempotency-Key.
- Concurrency (ADR-3): if the base is behind, the batch is rebased onto the latest revision when it touches
  nothing the newer revisions touched; otherwise 409 `revision_conflict` with the current document.
- Every accepted batch is an append-only revision with a snapshot; restore creates a new revision.
- Assets are uploaded straight to private storage with a presigned PUT and referenced as `asset:<id>`. A project may
  only reference the owner's own ready assets (object-level ACL).
- Final renders run on the worker (ffmpeg) as `editor_render` jobs, priced from remote config, reserved before
  dispatch, settled once, refunded on failure. Subtitles (SRT/VTT) are produced synchronously (no compute)."""

from __future__ import annotations

import json
import math
import re
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import ApiError, not_found
from ..models import EditorAsset, EditorProject, EditorRevision, FeatureFlag, GenerationJob, JobKind, JobStatus, User
from . import credits, editor_engine, moderation

FLAG = "editor"
DEFAULTS = {"credits_per_second": {"720p": 1, "1080p": 2, "2160p": 6}, "image_credits": 1,
            "max_render_seconds": 1800, "max_upload_mb": 2048, "max_batch": 500, "max_document_kb": 4096,
            "allow_4k": False}
MIMES = {"video": ("video/mp4", "video/quicktime", "video/webm"),
         "image": ("image/png", "image/jpeg", "image/webp"),
         "audio": ("audio/mpeg", "audio/mp4", "audio/wav", "audio/x-wav", "audio/aac"),
         "font": ("font/ttf", "font/otf", "application/font-sfnt")}
FORMATS = {"mp4": ("video", "video/mp4"), "hevc": ("video", "video/mp4"), "webm": ("video", "video/webm"),
           "png": ("image", "image/png"), "jpeg": ("image", "image/jpeg"), "webp": ("image", "image/webp")}
DATA = json.loads((Path(__file__).resolve().parents[1] / "data" / "editor_templates.json").read_text())
ASSET_URI = re.compile(r"^asset:([0-9a-f-]{36})$")


def config(db: Session) -> tuple[bool, dict]:
    f = db.get(FeatureFlag, FLAG)
    raw = (f.value or {}) if f else {}
    v = {**DEFAULTS, **raw}
    v["credits_per_second"] = {**DEFAULTS["credits_per_second"], **(raw.get("credits_per_second") or {})}
    return bool(f and f.enabled), v


def require_enabled(db: Session) -> dict:
    ok, cfg = config(db)
    if not ok:
        raise ApiError(403, "feature_disabled", "the editor is not available yet")
    return cfg


def out(p: EditorProject) -> dict:
    return {"id": str(p.id), "type": p.type, "title": p.title, "revision": p.revision, "project": p.document,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None}


def get(db: Session, user: User, project_id: uuid.UUID, lock: bool = False) -> EditorProject:
    q = select(EditorProject).where(EditorProject.id == project_id)
    if lock:
        q = q.with_for_update()
    p = db.execute(q).scalar_one_or_none()
    if p is None or p.owner_id != user.id or p.deleted_at is not None:
        raise not_found("project")
    return p


def _engine_error(e: editor_engine.EngineError, index: int | None = None) -> ApiError:
    return ApiError(422, "invalid_command", str(e), {"engine_code": e.code, "index": index})


def create(db: Session, user: User, type_: str, title: str, canvas: dict | None, preset: str | None,
           template: str | None) -> EditorProject:
    require_enabled(db)
    if type_ not in ("video", "photo", "social", "film", "episode"):
        raise ApiError(422, "bad_type", "unknown project type")
    if not moderation.check_text(title).allowed:
        raise ApiError(422, "content_blocked", "this text is not allowed")
    pid = uuid.uuid4()
    cmds = []
    if template:
        t = next((x for x in DATA["templates"] if x["key"] == template), None)
        if t is None:
            raise not_found("template")
        type_, canvas, cmds = t["type"], t["canvas"], t["commands"]
    elif preset:
        canvas = DATA["presets"].get(preset)
        if canvas is None:
            raise not_found("preset")
    canvas = canvas or DATA["presets"]["vertical_1080"]
    doc = editor_engine.new_project(str(pid), type_, title[:120], canvas)
    try:
        doc, _ = editor_engine.apply(doc, {"type": "set_canvas", **{k: canvas[k] for k in
                                                                    ("width", "height", "fps", "background")}})
        doc, _ = editor_engine.apply_all(doc, cmds)
    except (editor_engine.EngineError, KeyError) as e:
        raise ApiError(422, "bad_canvas", "invalid canvas or template") from e
    p = EditorProject(id=pid, owner_id=user.id, type=type_, title=title[:120], revision=0, document=doc)
    db.add(p)
    db.flush()
    db.add(EditorRevision(project_id=p.id, revision=0, base_revision=0, commands=cmds, snapshot=doc,
                          idempotency_key=f"create:{p.id}", client="server", author_id=user.id))
    db.flush()
    return p


def _check_asset_refs(db: Session, user: User, cmds: list[dict]) -> None:
    """Object-level ACL: an add_asset may only point at the owner's own ready asset, with its true kind."""
    for c in cmds:
        if not isinstance(c, dict) or c.get("type") not in ("add_asset", "put_asset"):
            continue
        a = c.get("asset") or {}
        m = ASSET_URI.match(str(a.get("uri", "")))
        row = db.get(EditorAsset, uuid.UUID(m.group(1))) if m else None
        if row is None or row.owner_id != user.id or row.status != "ready":
            raise ApiError(403, "asset_not_allowed", "projects can only use your own uploaded assets")
        if a.get("kind") != row.kind:
            raise ApiError(422, "asset_kind_mismatch", "the asset kind does not match the upload")


def _check_text(cmds: list[dict]) -> None:
    for c in cmds:
        clip = c.get("clip") if isinstance(c, dict) else None
        text = ((clip or {}).get("text") or {}).get("content") if isinstance(clip, dict) else None
        text = text or ((c.get("text") or {}).get("content") if isinstance(c.get("text"), dict) else None)
        if text:
            d = moderation.check_fiction(text, "mature", "dialogue")  # captions follow the fiction policy
            if not d.allowed:
                raise ApiError(422, "content_blocked", "this text is not allowed", {"category": d.category})


def commands(db: Session, user: User, project_id: uuid.UUID, base_revision: int, cmds: list[dict], idem: str,
             client: str | None) -> dict:
    cfg = require_enabled(db)
    if not isinstance(cmds, list) or not cmds:
        raise ApiError(422, "no_commands", "send at least one command")
    if len(cmds) > int(cfg["max_batch"]):
        raise ApiError(413, "batch_too_large", f"at most {cfg['max_batch']} commands per batch")
    p = get(db, user, project_id, lock=True)  # serialize writers per project
    seen = db.execute(select(EditorRevision).where(EditorRevision.project_id == p.id,
                                                   EditorRevision.idempotency_key == idem)).scalar_one_or_none()
    if seen is not None:  # retried batch (autosave/offline replay): same answer, nothing applied twice
        return {"revision": p.revision, "project": p.document, "applied": len(seen.commands), "replay": True}
    if base_revision > p.revision or base_revision < 0:
        raise ApiError(409, "revision_conflict", "unknown base revision", {"revision": p.revision,
                                                                            "project": p.document})
    _check_asset_refs(db, user, cmds)
    _check_text(cmds)
    rebased = 0
    if base_revision < p.revision:
        newer = db.execute(select(EditorRevision).where(EditorRevision.project_id == p.id,
                                                        EditorRevision.revision > base_revision)).scalars().all()
        theirs = set().union(*[editor_engine.touches(c) for r in newer for c in r.commands]) if newer else set()
        mine = set().union(*[editor_engine.touches(c) for c in cmds])
        if theirs & mine or any(r.client == "restore" for r in newer):
            raise ApiError(409, "revision_conflict", "someone changed the same items; reload and retry",
                           {"revision": p.revision, "project": p.document, "overlap": sorted(theirs & mine)[:20]})
        rebased = len(newer)
    doc = p.document
    for i, c in enumerate(cmds):
        try:
            doc, _ = editor_engine.apply(doc, c)
        except editor_engine.EngineError as e:
            raise _engine_error(e, i) from e
    if len(json.dumps(doc)) > int(cfg["max_document_kb"]) * 1024:
        raise ApiError(413, "document_too_large", "the project is too large")
    rev = p.revision + 1
    try:
        with db.begin_nested():
            db.add(EditorRevision(project_id=p.id, revision=rev, base_revision=base_revision, commands=cmds,
                                  snapshot=doc, idempotency_key=idem, client=(client or "")[:20] or None,
                                  author_id=user.id))
            db.flush()
    except IntegrityError as e:
        raise ApiError(409, "revision_conflict", "concurrent write; retry", {"revision": p.revision}) from e
    p.document, p.revision, p.title = doc, rev, doc["title"]
    return {"revision": rev, "project": doc, "applied": len(cmds), "rebased": rebased}


def restore(db: Session, user: User, project_id: uuid.UUID, revision: int) -> dict:
    p = get(db, user, project_id, lock=True)
    r = db.execute(select(EditorRevision).where(EditorRevision.project_id == p.id,
                                                EditorRevision.revision == revision)).scalar_one_or_none()
    if r is None:
        raise not_found("revision")
    rev = p.revision + 1
    db.add(EditorRevision(project_id=p.id, revision=rev, base_revision=p.revision,
                          commands=[{"type": "restore", "revision": revision}], snapshot=r.snapshot,
                          idempotency_key=f"restore:{rev}", client="restore", author_id=user.id))
    p.document, p.revision, p.title = r.snapshot, rev, r.snapshot["title"]
    db.flush()
    return {"revision": rev, "project": p.document, "applied": 0}


# ---------------------------------------------------------------- assets

def upload(db: Session, user: User, kind: str, mime: str, size: int, name: str) -> dict:
    from .storage import get_storage

    cfg = require_enabled(db)
    if mime not in MIMES.get(kind, ()):
        raise ApiError(422, "bad_mime", f"{mime} is not accepted for {kind}")
    if not 0 < size <= int(cfg["max_upload_mb"]) * 1024 * 1024:
        raise ApiError(413, "file_too_large", f"at most {cfg['max_upload_mb']} MB")
    aid = uuid.uuid4()
    key = f"editor/{user.id}/{aid}"
    db.add(EditorAsset(id=aid, owner_id=user.id, kind=kind, mime=mime, name=name[:160], size_bytes=size,
                       storage_key=key, status="pending"))
    db.flush()
    return {"asset_id": str(aid), "uri": f"asset:{aid}",
            "upload": {"url": get_storage().presign_put(key, mime, 3600), "method": "PUT",
                       "headers": {"Content-Type": mime}}}


def complete_asset(db: Session, user: User, asset_id: uuid.UUID, meta: dict) -> EditorAsset:
    from .storage import get_storage

    a = db.get(EditorAsset, asset_id)
    if a is None or a.owner_id != user.id or a.status == "deleted":
        raise not_found("asset")
    head = get_storage().head(a.storage_key)
    if head is None or head["size"] <= 0:
        raise ApiError(409, "upload_missing", "the file was not uploaded")
    a.size_bytes, a.status = int(head["size"]), "ready"
    a.meta = {k: int(v) for k, v in (meta or {}).items() if k in ("duration_ms", "width", "height")
              and isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 10**9}
    return a


def asset_out(a: EditorAsset) -> dict:
    from .storage import get_storage

    return {"id": str(a.id), "kind": a.kind, "name": a.name, "uri": f"asset:{a.id}", "status": a.status,
            "mime": a.mime, "size_bytes": a.size_bytes, "meta": a.meta,
            "url": get_storage().presign_get(a.storage_key, 900) if a.status == "ready" else None}


# ---------------------------------------------------------------- render / export

def render_quote(db: Session, p: EditorProject, fmt: str, quality: str) -> dict:
    cfg = require_enabled(db)
    if fmt not in FORMATS:
        raise ApiError(422, "bad_format", f"formats: {', '.join(FORMATS)}, srt, vtt")
    kind, _ = FORMATS[fmt]
    m = editor_engine.render_manifest(p.document)
    if kind == "video":
        if m["duration_ms"] <= 0:
            raise ApiError(422, "empty_timeline", "add clips first")
        if m["duration_ms"] > int(cfg["max_render_seconds"]) * 1000:
            raise ApiError(422, "too_long", f"at most {cfg['max_render_seconds']} seconds")
        if quality == "2160p" and not cfg["allow_4k"]:
            raise ApiError(403, "plan_required", "4K export is not available on your plan")
        cps = int(cfg["credits_per_second"][quality])
        cost = math.ceil(m["duration_ms"] / 1000) * cps
    else:
        cost = int(cfg["image_credits"])
    return {"format": fmt, "quality": quality, "credits": cost, "duration_ms": m["duration_ms"]}


def render(db: Session, user: User, project_id: uuid.UUID, fmt: str, quality: str, confirmed: int | None,
           idem: str) -> GenerationJob:
    p = get(db, user, project_id)
    key = f"er:{p.id}:{idem}"[:80]
    job = db.execute(select(GenerationJob).where(GenerationJob.user_id == user.id,
                                                 GenerationJob.idempotency_key == key)).scalar_one_or_none()
    if job is not None:
        return job
    q = render_quote(db, p, fmt, quality)
    if confirmed != q["credits"]:
        raise ApiError(409, "confirmation_required", "confirm the price first", q)
    m = editor_engine.render_manifest(p.document)
    assets = {}
    for layer in m["layers"] + m["audio"]:
        a = layer.get("asset")
        if a:
            mm = ASSET_URI.match(a["uri"])
            row = db.get(EditorAsset, uuid.UUID(mm.group(1))) if mm else None
            if row is None or row.owner_id != user.id or row.status != "ready":
                raise ApiError(409, "asset_missing", "an asset of this project is missing", {"asset": a["id"]})
            assets[a["id"]] = row.storage_key
    s = get_settings()
    job = GenerationJob(id=uuid.uuid4(), user_id=user.id, kind=JobKind.editor_render, status=JobStatus.queued,
                        queue_class="paid_high" if user.plan == "pro" else "free", preferred_model="editor_renderer",
                        idempotency_key=key, credit_cost=q["credits"], max_attempts=s.job_max_attempts,
                        watermark=user.plan != "pro", est_cost_usd=0.0,
                        spec={"project_id": str(p.id), "revision": p.revision, "format": fmt, "quality": quality,
                              "manifest": m, "assets": assets})
    db.add(job)
    db.flush()
    credits.reserve(db, job)
    return job


def output_key(job: GenerationJob) -> str:
    ext = {"mp4": "mp4", "hevc": "mp4", "webm": "webm", "png": "png", "jpeg": "jpg", "webp": "webp"}
    return f"editor/{job.user_id}/renders/{job.id}.{ext[job.spec['format']]}"


def build_payload(db: Session, job: GenerationJob) -> dict:
    from .storage import get_storage

    st = get_storage()
    mime = FORMATS[job.spec["format"]][1]
    return {"job_id": str(job.id), "attempt": job.attempts, "model": job.model_used, "kind": job.kind.value,
            "lease_s": get_settings().job_lease_s, "spec": {k: v for k, v in job.spec.items() if k != "assets"},
            "watermark": job.watermark,
            "asset_urls": {aid: st.presign_get(key, 3600) for aid, key in job.spec["assets"].items()},
            "upload": {"output": {"key": output_key(job), "url": st.presign_put(output_key(job), mime),
                                  "mime": mime}}}


def exports(db: Session, user: User, project_id: uuid.UUID) -> list[dict]:
    from .storage import get_storage

    p = get(db, user, project_id)
    st = get_storage()
    rows = db.execute(select(GenerationJob).where(GenerationJob.user_id == user.id,
                                                  GenerationJob.kind == JobKind.editor_render)
                      .order_by(GenerationJob.created_at.desc()).limit(100)).scalars().all()
    return [{"job_id": str(j.id), "format": j.spec["format"], "quality": j.spec.get("quality"),
             "revision": j.spec.get("revision"), "status": j.status.value, "progress": j.progress,
             "credits": j.credit_cost, "error": j.error_code,
             "url": st.presign_get(output_key(j), 900) if j.status == JobStatus.completed else None}
            for j in rows if j.spec.get("project_id") == str(p.id)]
