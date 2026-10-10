"""Exact Dialogue Mode + line editor + selected-range editing (Master Spec V7 §4/§5).

- Each line is a persistent object inside the immutable storyboard versions: `id`, speaker, timing, text,
  language, voice, emotion, delivery, intensity, pronunciation, `exact`, `locked`, `rev`. History = the line's
  value in every version; undo/redo = Studio undo/redo.
- `exact` text is stored and subtitled byte-for-byte: never smoothed, paraphrased or "scripted up". The fiction
  policy decides what a production's rating allows (slang/swearing need teen/mature); a few things are never
  allowed. No TTS provider is integrated, so spoken audio is reported as not available instead of silently
  falling back to a provider that could alter the words.
- Every change is first an *impact analysis*: subtitles only / voice / voice + lip-sync / shot regeneration /
  later-scene story impact (continuity findings), with credits — then applied as a new version.
- Range edits (`01:12–01:17`) map the range to the shots it touches; providers can't regenerate an exact
  sub-range, so whole shots are regenerated and neighbouring transitions re-assembled — stated, not hidden."""

from __future__ import annotations

import copy
import uuid

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import ApiError, not_found
from ..models import ModelRun, StudioProject, StudioProjectVersion, User
from . import budget, studio


class LinePatch(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=600)
    character: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,40}$")
    language: str | None = Field(default=None, max_length=8)
    emotion: str | None = Field(default=None, max_length=40)
    delivery: str | None = Field(default=None, pattern=r"^(normal|shout|whisper|cry|sarcastic|laugh|angry|calm|mock)$")
    intensity: float | None = Field(default=None, ge=0, le=1)
    pronunciation: str | None = Field(default=None, max_length=200)
    voice_id: str | None = Field(default=None, max_length=120)
    locked: bool | None = None
    exact: bool | None = None


def lines(db: Session, project: StudioProject) -> list[dict]:
    v = studio.get_version(db, project, None)
    out = []
    for lid, x in studio.dialogue_lines(v.storyboard).items():
        out.append({"id": lid, "shot": x["shot"], "scene": x["scene"], **x["line"]})
    return out


def _apply_patch(storyboard: dict, line_id: str, patch: LinePatch, unlock: bool) -> tuple[dict, dict, dict]:
    sb = copy.deepcopy(storyboard)
    for sc in sb.get("scenes", []):
        for sh in sc.get("shots", []):
            for d in sh.get("dialogue", []):
                if d.get("id") != line_id:
                    continue
                before = dict(d)
                changes = patch.model_dump(exclude_none=True)
                if d.get("locked") and not unlock and set(changes) - {"locked"}:
                    raise ApiError(409, "dialogue_locked", "this line is locked; unlock it explicitly first",
                                   {"line_id": line_id})
                d.update(changes)
                if "text" in changes or "character" in changes:
                    d["rev"] = int(d.get("rev", 0)) + 1
                return sb, before, d
    raise not_found("dialogue line")


def impact(db: Session, user: User, project: StudioProject, before_sb: dict, after_sb: dict, line_before: dict,
           line_after: dict) -> dict:
    """What a line change touches, before anything is charged."""
    _, bcfg = budget.config(db)
    costs = bcfg["costs_usd"]
    before = studio.estimate_storyboard(db, user, project, before_sb)
    after = studio.estimate_storyboard(db, user, project, after_sb)
    changed_text = line_before.get("text") != line_after.get("text") or \
        line_before.get("character") != line_after.get("character")
    perf = any(line_before.get(k) != line_after.get(k) for k in
               ("emotion", "delivery", "intensity", "pronunciation", "voice_id", "language"))
    levels = []
    if changed_text:
        levels.append("subtitles")
    if changed_text or perf:
        levels.append("voice" if costs["tts_per_1k_chars"] is not None else "voice_not_available")
        levels.append("voice_and_lip_sync" if costs["lip_sync_per_second"] is not None
                      else "lip_sync_not_available")
    if after["new_shots"] > before["new_shots"]:
        levels.append("shot_regeneration")
    return {"levels": levels, "credits_delta": after["credits"] - before["credits"],
            "new_shots": after["new_shots"], "reused_shots": after["reused_shots"],
            "notes": ["Dialogue is delivered as exact subtitles; no voice provider is integrated yet."]
            if costs["tts_per_1k_chars"] is None else []}


def edit_line(db: Session, user: User, project: StudioProject, line_id: str, patch: LinePatch, unlock: bool,
              apply: bool, prod=None, ep=None) -> dict:
    cfg = studio.require_enabled(db)
    base = studio.get_version(db, project, None)
    new_sb, before, after = _apply_patch(base.storyboard, line_id, patch, unlock)
    sb = studio.validate_storyboard(db, user, new_sb, cfg, project)  # fiction policy + limits
    new_sb = sb.model_dump(mode="json")
    imp = impact(db, user, project, base.storyboard, new_sb, before, after)
    story = []
    if prod is not None and ep is not None:  # later-scene / later-episode story impact
        from . import continuity

        now = {(f["code"], str(f["details"])) for f in
               continuity.validate_episode(db, prod.id, ep.branch_id, ep, base.storyboard)}
        story = [f for f in continuity.validate_episode(db, prod.id, ep.branch_id, ep, new_sb)
                 if (f["code"], str(f["details"])) not in now]
        if story:
            imp["levels"].append("story_review")
    out = {"line_before": before, "line_after": after, "impact": imp, "story_findings": story, "applied": False}
    if apply:
        v = studio.add_version(db, user, project, studio.Storyboard.model_validate(new_sb), "dialogue_edit",
                               {"line_id": line_id}, base.director, base.id)
        out.update(applied=True, version_id=str(v.id), version=v.version)
    return out


def variant(db: Session, user: User, project: StudioProject, line_id: str, text: str) -> StudioProjectVersion:
    """A/B variant: a parallel (non-current) version with the alternative line; pick one with restore."""
    cfg = studio.require_enabled(db)
    base = studio.get_version(db, project, None)
    new_sb, before, _ = _apply_patch(base.storyboard, line_id, LinePatch(text=text), unlock=True)
    sb = studio.validate_storyboard(db, user, new_sb, cfg, project)
    return studio.add_version(db, user, project, sb, "dialogue_variant", {"line_id": line_id, "variant_of": before},
                              base.director, base.id, make_current=False)


def history(db: Session, project: StudioProject, line_id: str) -> list[dict]:
    rows = db.execute(select(StudioProjectVersion).where(StudioProjectVersion.project_id == project.id)
                      .order_by(StudioProjectVersion.version)).scalars().all()
    out, last = [], None
    for v in rows:
        x = studio.dialogue_lines(v.storyboard).get(line_id)
        if x is None:
            continue
        sig = (x["line"].get("text"), x["line"].get("character"), x["line"].get("emotion"),
               x["line"].get("delivery"), x["line"].get("locked"))
        if sig != last:
            out.append({"version": v.version, "version_id": str(v.id), "source": v.source, **x["line"]})
            last = sig
    return out


# ---------------------------------------------------------------- selected range edit

class RangeChange(BaseModel):
    prompt: str | None = Field(default=None, min_length=5, max_length=1500)
    camera: str | None = Field(default=None, max_length=200)
    caption: str | None = Field(default=None, max_length=300)
    expression: str | None = Field(default=None, max_length=120)  # facial expression / acting note
    action: str | None = Field(default=None, max_length=300)  # actor movement


def shots_in_range(storyboard: dict, start_ms: int, end_ms: int) -> list[dict]:
    if end_ms <= start_ms:
        raise ApiError(422, "bad_range", "end must be after start")
    out, t = [], 0
    for sc in storyboard.get("scenes", []):
        for sh in sc.get("shots", []):
            s0, s1 = t, t + int(sh["duration_s"]) * 1000
            if s0 < end_ms and s1 > start_ms:
                out.append({"shot": sh["key"], "scene": sc["key"], "start_ms": s0, "end_ms": s1,
                            "overlap_ms": min(s1, end_ms) - max(s0, start_ms)})
            t = s1
    if not out:
        raise ApiError(422, "bad_range", "the range is outside the episode")
    return out


def range_edit(db: Session, user: User, project: StudioProject, start_ms: int, end_ms: int, change: RangeChange):
    """-> a proposed Studio edit (diff + cost) for the shots in the range, plus the edit plan shown to the user."""
    from . import studio_edits

    base = studio.get_version(db, project, None)
    hit = shots_in_range(base.storyboard, start_ms, end_ms)
    by_key = {s["key"]: s for sc in base.storyboard["scenes"] for s in sc["shots"]}
    ops = []
    for h in hit:
        sh = by_key[h["shot"]]
        prompt = change.prompt or sh["prompt"]
        notes = [x for x in (change.expression and f"Expression: {change.expression}.",
                             change.action and f"Action: {change.action}.") if x]
        if notes:
            prompt = (prompt + " " + " ".join(notes))[:1500]
        op = {"op": "set_shot", "shot": h["shot"]}
        if prompt != sh["prompt"]:
            op["prompt"] = prompt
        if change.camera is not None:
            op["camera"] = change.camera
        if change.caption is not None:
            op["caption"] = change.caption
        if len(op) > 2:
            ops.append(op)
    if not ops:
        raise ApiError(422, "nothing_to_change", "describe what should change in the range")
    e = studio_edits.propose(db, user, project, base.id, None, ops, "timeline_range")
    cost = (e.preview or {}).get("cost") or {}
    avg = db.execute(select(ModelRun.finished_at, ModelRun.started_at).where(
        ModelRun.status == "succeeded").order_by(ModelRun.started_at.desc()).limit(50)).all()
    secs = [(f - s).total_seconds() for f, s in avg if f and s]
    per = sum(secs) / len(secs) if secs else None
    plan = {"range_ms": [start_ms, end_ms], "changed_clips": hit, "credits": cost.get("delta_credits"),
            "new_shots": cost.get("new_shots"),
            "estimated_seconds": round(per * len(hit), 1) if per else None,
            "risks": ["The video provider can't regenerate only part of a shot: the whole shot "
                      f"({sum(h['end_ms'] - h['start_ms'] for h in hit) / 1000:.0f} s) is regenerated.",
                      "Neighbouring transitions are re-assembled; unchanged shots are reused (no charge).",
                      "Lip-sync is not available; dialogue stays as exact subtitles."]}
    return e, plan


def get_project_for(db: Session, user: User, project_id: uuid.UUID) -> StudioProject:
    return studio.get_project(db, user, project_id)
