"""Versioned timeline JSON (Master Spec V7 §5): separate tracks for video shots, dialogue, subtitles, characters,
camera, scenes and audio, with the immutable storyboard version it came from. Import goes through the same
validation as any edit and becomes a new version (non-destructive); the base version must match."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from ..errors import ApiError
from ..models import StudioProject, User
from . import studio

SCHEMA = "v7-timeline-1"


def export(db: Session, project: StudioProject, version_id: uuid.UUID | None = None) -> dict:
    v = studio.get_version(db, project, version_id)
    sb = studio.Storyboard.model_validate(v.storyboard)
    hashes = studio.shot_hashes(db, v.storyboard)
    renders = {s.key: r for s, r in studio._version_renders(db, project, v)}
    scenes = sb.scene_of()
    video, dialogue, subs, camera, t = [], [], [], [], 0
    names = {c.key: c.name for c in sb.characters}
    for shot in sb.shots():
        r = renders.get(shot.key)
        video.append({"shot": shot.key, "scene": scenes[shot.key].key, "start_ms": t,
                      "end_ms": t + shot.duration_s * 1000, "hash": hashes[shot.key],
                      "status": r.status if r else "new", "transition": shot.transition})
        camera.append({"shot": shot.key, "start_ms": t, "camera": shot.camera})
        for i, d in enumerate(shot.dialogue):
            start = t + int(d.start_s * 1000)
            nxt = shot.dialogue[i + 1].start_s if i + 1 < len(shot.dialogue) else shot.duration_s
            end = t + int((d.end_s if d.end_s is not None else nxt) * 1000)
            dialogue.append({"id": d.id, "shot": shot.key, "character": d.character, "start_ms": start,
                             "end_ms": end, "text": d.text, "exact": d.exact, "locked": d.locked,
                             "emotion": d.emotion, "delivery": d.delivery, "language": d.language or sb.language})
            subs.append({"start_ms": start, "end_ms": end,
                         "text": (f"{names.get(d.character, d.character)}: " if d.character else "") + d.text})
        t += shot.duration_s * 1000
    return {"schema": SCHEMA, "project_id": str(project.id), "version_id": str(v.id), "version": v.version,
            "duration_ms": t,
            "tracks": {"video": video, "dialogue": dialogue, "subtitles": subs, "camera": camera,
                       "characters": [c.model_dump(mode="json") for c in sb.characters],
                       "scenes": [{"key": sc.key, "title": sc.title, "scene_type": sc.scene_type,
                                   "location": sc.location, "visual_style": sc.visual_style} for sc in sb.scenes],
                       "audio": [{"type": "music", **sb.audio.model_dump(mode="json")}]},
            "storyboard": v.storyboard}


def import_(db: Session, user: User, project: StudioProject, data: dict):
    if (data or {}).get("schema") != SCHEMA:
        raise ApiError(422, "bad_timeline", f"expected schema {SCHEMA}")
    base = studio.get_version(db, project, None)
    if data.get("version_id") != str(base.id):
        raise ApiError(409, "stale_timeline", "the project changed since this timeline was exported")
    cfg = studio.require_enabled(db)
    sb = studio.validate_storyboard(db, user, data.get("storyboard") or {}, cfg, project)
    return studio.add_version(db, user, project, sb, "edit", {"timeline_import": SCHEMA}, base.director, base.id)
