"""AI Studio editing (V4 Stage C): conversational and timeline edits as typed, auditable operations.

- Chat and timeline produce the same `ops` (spec V4 §5: equivalent backend operations).
- An edit is a *proposal* first: the new storyboard, a diff and the cost delta are shown; only `apply` creates a
  new immutable version. Undo/rollback = restore an earlier version. Nothing is silently overwritten.
- Trims and splits of a shot that is already rendered become `derive: trim` shots: the assembler cuts the
  existing render, so they cost 0 credits. Extensions continue a rendered shot (provider capability required).
- Requests the providers can't do (object/background recolor, inpainting, relighting) are answered as
  `unsupported` with the missing capability, never faked. Ambiguous targets get a clarification question."""

from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import ApiError, not_found
from ..models import StudioEditOperation, StudioProject, StudioShotRender, User
from . import studio
from .studio import KEY_RE, Dialogue

# ---------------------------------------------------------------- operation schema


class SetShot(BaseModel):
    op: Literal["set_shot"]
    shot: str = Field(pattern=KEY_RE)
    prompt: str | None = Field(default=None, min_length=5, max_length=1500)
    camera: str | None = Field(default=None, max_length=200)
    caption: str | None = Field(default=None, max_length=300)
    transition: Literal["cut", "fade"] | None = None
    duration_s: int | None = Field(default=None, ge=1, le=60)


class SetDialogue(BaseModel):
    op: Literal["set_dialogue"]
    shot: str = Field(pattern=KEY_RE)
    dialogue: list[Dialogue] = Field(max_length=6)


class RemoveShot(BaseModel):
    op: Literal["remove_shot"]
    shot: str = Field(pattern=KEY_RE)


class DuplicateShot(BaseModel):
    op: Literal["duplicate_shot"]
    shot: str = Field(pattern=KEY_RE)


class MoveShot(BaseModel):
    op: Literal["move_shot"]
    shot: str = Field(pattern=KEY_RE)
    before: str | None = Field(default=None, pattern=KEY_RE, description="move before this shot; null = to the end")


class SplitShot(BaseModel):
    op: Literal["split_shot"]
    shot: str = Field(pattern=KEY_RE)
    at_s: int = Field(ge=1)


class TrimShot(BaseModel):
    op: Literal["trim_shot"]
    shot: str = Field(pattern=KEY_RE)
    start_s: int = Field(ge=0)
    end_s: int = Field(ge=1)


class ExtendShot(BaseModel):
    op: Literal["extend_shot"]
    shot: str = Field(pattern=KEY_RE)
    direction: Literal["end", "start"] = "end"
    seconds: int = Field(ge=1, le=20)
    prompt: str | None = Field(default=None, min_length=5, max_length=1500)


class SetStyle(BaseModel):
    op: Literal["set_style"]
    style: str = Field(max_length=300)


class SetAspect(BaseModel):
    op: Literal["set_aspect_ratio"]
    aspect_ratio: Literal["9:16", "16:9", "1:1"]


class SetMusic(BaseModel):
    op: Literal["set_music"]
    music_asset_id: uuid.UUID | None = None
    volume: float | None = Field(default=None, ge=0, le=1)


class SetCaptions(BaseModel):
    op: Literal["set_captions"]
    enabled: bool | None = None
    burn_in: bool | None = None


class ReplaceCharacter(BaseModel):
    op: Literal["replace_character"]
    character: str = Field(pattern=KEY_RE)
    character_id: uuid.UUID | None = None
    name: str | None = Field(default=None, max_length=60)
    description: str | None = Field(default=None, max_length=500)


class Unsupported(BaseModel):
    op: Literal["unsupported"]
    capability: Literal["OBJECT_EDIT", "SCENE_EDIT", "VIDEO_INPAINT", "RELIGHT", "VOICE_TTS", "DUBBING", "OTHER"]
    reason: str = Field(max_length=300)


Op = Annotated[SetShot | SetDialogue | RemoveShot | DuplicateShot | MoveShot | SplitShot | TrimShot | ExtendShot |
               SetStyle | SetAspect | SetMusic | SetCaptions | ReplaceCharacter | Unsupported,
               Field(discriminator="op")]
OPS = TypeAdapter(list[Op])


def parse_ops(raw: list) -> list:
    try:
        return OPS.validate_python(raw)
    except ValidationError as e:
        raise ApiError(422, "bad_edit", "edit operations are invalid",
                       {"errors": [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()[:10]]}) from e


# ---------------------------------------------------------------- applying operations

def _flat(sb: dict) -> list[tuple[dict, dict]]:
    return [(sc, sh) for sc in sb["scenes"] for sh in sc["shots"]]


def _find(sb: dict, key: str) -> tuple[dict, dict]:
    for sc, sh in _flat(sb):
        if sh["key"] == key:
            return sc, sh
    raise ApiError(422, "unknown_shot", f"there is no shot {key}", {"shot": key})


def _new_key(sb: dict, base: str, suffix: str) -> str:
    keys = {sh["key"] for _, sh in _flat(sb)} | {sc["key"] for sc in sb["scenes"]}
    stem = re.sub(r"[^a-z0-9_-]", "", base)[: 40 - len(suffix) - 3] or "sh"
    for n in range(1, 1000):
        k = f"{stem}{suffix}{n}"
        if k not in keys:
            return k
    raise ApiError(422, "bad_edit", "too many shots")


def _trim_of(sh: dict, hashes: dict[str, str], rendered: set[str], start: int, end: int) -> dict | None:
    """A trim of `sh`'s pixels if they exist (re-based onto the original render for trims of trims)."""
    d = sh.get("derive")
    if d and d.get("kind") == "trim":
        return {"kind": "trim", "from_hash": d["from_hash"], "start_s": d["start_s"] + start,
                "end_s": d["start_s"] + end}
    h = hashes.get(sh["key"])
    if h in rendered:
        return {"kind": "trim", "from_hash": h, "start_s": start, "end_s": end}
    return None


def apply_ops(storyboard: dict, ops: list, hashes: dict[str, str], rendered: set[str]) -> tuple[dict, list[str]]:
    """Pure: storyboard + ops -> new storyboard (+ notes). `hashes`: shot key -> content hash of the base version;
    `rendered`: hashes with a ready render."""
    sb = copy.deepcopy(storyboard)
    notes: list[str] = []
    for op in ops:
        if isinstance(op, Unsupported):
            continue
        if isinstance(op, SetShot):
            _, sh = _find(sb, op.shot)
            for f in ("prompt", "camera", "caption", "transition", "duration_s"):
                v = getattr(op, f)
                if v is not None:
                    sh[f] = v
            if op.prompt is not None or op.camera is not None or op.duration_s is not None:
                sh.pop("derive", None)  # new pixels: no longer a cut of an old render
        elif isinstance(op, SetDialogue):
            _, sh = _find(sb, op.shot)
            sh["dialogue"] = [d.model_dump() for d in op.dialogue]
        elif isinstance(op, RemoveShot):
            sc, sh = _find(sb, op.shot)
            sc["shots"].remove(sh)
            if not sc["shots"]:
                sb["scenes"].remove(sc)
            if not sb["scenes"]:
                raise ApiError(422, "bad_edit", "a project needs at least one shot")
        elif isinstance(op, DuplicateShot):
            sc, sh = _find(sb, op.shot)
            dup = {**copy.deepcopy(sh), "key": _new_key(sb, op.shot, "c")}
            sc["shots"].insert(sc["shots"].index(sh) + 1, dup)  # same content -> same hash -> render reused
        elif isinstance(op, MoveShot):
            sc, sh = _find(sb, op.shot)
            if op.before == op.shot:
                continue
            sc["shots"].remove(sh)
            if op.before is None:
                sb["scenes"][-1]["shots"].append(sh)
            else:
                tsc, tsh = _find(sb, op.before)
                tsc["shots"].insert(tsc["shots"].index(tsh), sh)
            if not sc["shots"]:
                sb["scenes"].remove(sc)
        elif isinstance(op, SplitShot | TrimShot):
            sc, sh = _find(sb, op.shot)
            dur = sh["duration_s"]
            if isinstance(op, SplitShot):
                if not 0 < op.at_s < dur:
                    raise ApiError(422, "bad_edit", f"split point must be inside the {dur}s shot")
                a, b = _trim_of(sh, hashes, rendered, 0, op.at_s), _trim_of(sh, hashes, rendered, op.at_s, dur)
                second = {**copy.deepcopy(sh), "key": _new_key(sb, op.shot, "b"), "duration_s": dur - op.at_s,
                          "dialogue": [{**d, "start_s": d["start_s"] - op.at_s} for d in sh.get("dialogue", [])
                                       if d["start_s"] >= op.at_s]}
                sh["dialogue"] = [d for d in sh.get("dialogue", []) if d["start_s"] < op.at_s]
                sh["duration_s"] = op.at_s
                if a and b:
                    sh["derive"], second["derive"] = a, b
                else:
                    notes.append(f"{op.shot} is not rendered yet: both halves will be rendered separately.")
                sc["shots"].insert(sc["shots"].index(sh) + 1, second)
            else:
                if not (0 <= op.start_s < op.end_s <= dur):
                    raise ApiError(422, "bad_edit", f"trim must stay inside the {dur}s shot")
                t = _trim_of(sh, hashes, rendered, op.start_s, op.end_s)
                sh["duration_s"] = op.end_s - op.start_s
                sh["dialogue"] = [{**d, "start_s": d["start_s"] - op.start_s} for d in sh.get("dialogue", [])
                                  if op.start_s <= d["start_s"] < op.end_s]
                if t:
                    sh["derive"] = t
                else:
                    notes.append(f"{op.shot} is not rendered yet: it will be rendered at the new length.")
        elif isinstance(op, ExtendShot):
            sc, sh = _find(sb, op.shot)
            if sh.get("derive"):
                raise ApiError(422, "bad_edit", "extend the original shot, not a cut or an extension of it")
            h = hashes.get(op.shot)
            if h not in rendered:
                raise ApiError(409, "source_not_rendered", f"render {op.shot} before extending it")
            ext = {"key": _new_key(sb, op.shot, "x"), "duration_s": op.seconds,
                   "prompt": op.prompt or f"Seamless continuation of the previous shot: {sh['prompt']}"[:1500],
                   "camera": sh.get("camera", ""), "characters": list(sh.get("characters", [])), "dialogue": [],
                   "caption": None, "transition": "cut",
                   "derive": {"kind": "extend", "from_hash": h, "direction": op.direction}}
            idx = sc["shots"].index(sh)
            sc["shots"].insert(idx + 1 if op.direction == "end" else idx, ext)
        elif isinstance(op, SetStyle):
            sb["style"] = op.style
            notes.append("A new style changes every shot: all shots will be rendered again.")
        elif isinstance(op, SetAspect):
            sb["aspect_ratio"] = op.aspect_ratio
            notes.append("A new aspect ratio changes every shot: all shots will be rendered again.")
        elif isinstance(op, SetMusic):
            sb.setdefault("audio", {})["music_asset_id"] = str(op.music_asset_id) if op.music_asset_id else None
            if op.volume is not None:
                sb["audio"]["music_volume"] = op.volume
        elif isinstance(op, SetCaptions):
            cap = sb.setdefault("captions", {})
            for f in ("enabled", "burn_in"):
                if getattr(op, f) is not None:
                    cap[f] = getattr(op, f)
        elif isinstance(op, ReplaceCharacter):
            ch = next((c for c in sb.get("characters", []) if c["key"] == op.character), None)
            if ch is None:
                raise ApiError(422, "bad_edit", f"there is no character {op.character}")
            if op.character_id is not None:
                ch["character_id"] = str(op.character_id)
            for f in ("name", "description"):
                if getattr(op, f) is not None:
                    ch[f] = getattr(op, f)
    return sb, notes


def diff(db: Session, before: dict, after: dict) -> dict:
    hb, ha = studio.shot_hashes(db, before), studio.shot_hashes(db, after)
    order_b = [k for k in hb]
    order_a = [k for k in ha]
    return {"added": [k for k in order_a if k not in hb], "removed": [k for k in order_b if k not in ha],
            "changed": [k for k in order_a if k in hb and hb[k] != ha[k]],
            "reordered": [k for k in order_a if k in hb] != [k for k in order_b if k in ha],
            "duration_s": {"before": sum(s["duration_s"] for _, s in _flat(before)),
                           "after": sum(s["duration_s"] for _, s in _flat(after))}}


# ---------------------------------------------------------------- editors (instruction -> ops)

_SHOT_RE = re.compile(r"(?:shot|sahne|çekim|cekim)\s*#?(\d+)|(\d+)\s*\.?\s*(?:shot|sahne|çekim|cekim)", re.I)


class RuleBasedEditor:
    """Deterministic parser for common TR/EN timeline commands. Labelled as not AI."""

    name, label = "rule_based", "rule-based editor (not AI)"

    def _shots(self, text: str, keys: list[str]) -> list[str]:
        out = []
        for m in _SHOT_RE.finditer(text):
            n = int(m.group(1) or m.group(2))
            if not 1 <= n <= len(keys):
                raise _Clarify(f"There is no shot {n}; the video has {len(keys)} shots.")
            out.append(keys[n - 1])
        if not out and re.search(r"\b(son|last)\b", text, re.I):
            out.append(keys[-1])
        return out

    def propose(self, instruction: str, storyboard: dict) -> tuple[list[dict], str | None]:
        t = instruction.strip()
        low = t.lower()
        keys = [sh["key"] for _, sh in _flat(storyboard)]
        quoted = re.findall(r"[\"“”']([^\"“”']{1,300})[\"“”']", t)
        secs = re.search(r"(\d+)\s*(?:s\b|sn\b|sec|second|saniye)", low)
        try:
            shots = self._shots(t, keys)
        except _Clarify as c:
            return [], str(c)
        if re.search(r"background|arka\s*plan|recolou?r|renk|object|nesne|lighting|ışı[kğ]|isik", low):
            cap = "RELIGHT" if re.search(r"lighting|ışı[kğ]|isik", low) else "OBJECT_EDIT"
            return [{"op": "unsupported", "capability": cap,
                     "reason": "Region and object edits need a provider with this capability; none is enabled."}], None
        if re.search(r"\b(16:9|9:16|1:1)\b", low):
            return [{"op": "set_aspect_ratio", "aspect_ratio": re.search(r"(16:9|9:16|1:1)", low).group(1)}], None
        if re.search(r"(müzi[kğ]i?|muzik|music).*(kaldır|kaldir|sil|remove|off|kapat)", low):
            return [{"op": "set_music", "music_asset_id": None}], None
        if re.search(r"(altyazı|altyazi|caption|subtitle).*(göm|gom|burn|yak)", low):
            return [{"op": "set_captions", "enabled": True, "burn_in": True}], None
        if re.search(r"(stil|style)", low) and quoted:
            return [{"op": "set_style", "style": quoted[0]}], None
        need_shot = None
        if re.search(r"\b(uzat|extend|prepend|lengthen)", low):
            need_shot = "extend"
        elif re.search(r"\b(sil|kaldır|kaldir|çıkar|cikar|remove|delete)\b", low):
            need_shot = "remove"
        elif re.search(r"\b(böl|bol|split)\b", low):
            need_shot = "split"
        elif re.search(r"(kısalt|kisalt|trim|shorten|kes)", low):
            need_shot = "trim"
        elif re.search(r"(kopyala|çoğalt|cogalt|duplicate|copy)", low):
            need_shot = "duplicate"
        elif re.search(r"(taşı|tasi|move|önüne|onune|before|sona|to the end)", low):
            need_shot = "move"
        elif re.search(r"(altyazı|altyazi|caption)", low) and quoted:
            need_shot = "caption"
        elif re.search(r"(prompt|açıklama|aciklama|görüntü|goruntu|make shot|yap)", low) and quoted:
            need_shot = "prompt"
        if need_shot is None:
            return [], ("I couldn't map this to an edit. Try e.g. 'extend shot 2 by 4 seconds', "
                        "'remove shot 3', 'move shot 3 before shot 1', 'trim shot 1 to 2 seconds', "
                        "'caption of shot 1 \"...\"', 'make it 16:9'.")
        if not shots:
            return [], "Which shot do you mean? (e.g. 'shot 2' / '2. sahne')"
        s = shots[0]
        if need_shot == "extend":
            if not secs:
                return [], "By how many seconds should I extend it?"
            start = bool(re.search(r"(başına|basina|önüne|onune|beginning|start|prepend|before it)", low))
            return [{"op": "extend_shot", "shot": s, "direction": "start" if start else "end",
                     "seconds": int(secs.group(1)), **({"prompt": quoted[0]} if quoted else {})}], None
        if need_shot == "remove":
            return [{"op": "remove_shot", "shot": x} for x in shots], None
        if need_shot == "split":
            if not secs:
                return [], "At which second should I split it?"
            return [{"op": "split_shot", "shot": s, "at_s": int(secs.group(1))}], None
        if need_shot == "trim":
            if not secs:
                return [], "To how many seconds should I shorten it?"
            return [{"op": "trim_shot", "shot": s, "start_s": 0, "end_s": int(secs.group(1))}], None
        if need_shot == "duplicate":
            return [{"op": "duplicate_shot", "shot": s}], None
        if need_shot == "move":
            if re.search(r"(sona|to the end|at the end)", low):
                return [{"op": "move_shot", "shot": s, "before": None}], None
            if len(shots) < 2:
                return [], "Where should it go? (e.g. 'before shot 1' / 'to the end')"
            return [{"op": "move_shot", "shot": s, "before": shots[1]}], None
        if need_shot == "caption":
            return [{"op": "set_shot", "shot": s, "caption": quoted[0]}], None
        return [{"op": "set_shot", "shot": s, "prompt": quoted[0]}], None


class _Clarify(Exception):
    pass


class GeminiEditor:
    """Gemini maps the instruction to the same typed ops (JSON schema); the result is validated and previewed
    like any timeline edit, so the model can never write a version directly."""

    name = "gemini"

    def __init__(self, model: str, client=None):
        self.model, self.label, self._client = model, f"AI editor ({model})", client

    def _c(self):
        if self._client is None:
            key = get_settings().gemini_api_key
            if not key:
                raise ApiError(503, "editor_not_configured", "the AI editor is not configured")
            from google import genai

            self._client = genai.Client(api_key=key)
        return self._client

    def propose(self, instruction: str, storyboard: dict) -> tuple[list[dict], str | None]:
        from google.genai import types

        schema = {"type": "object", "properties": {
            "ops": OPS.json_schema(), "clarification": {"type": ["string", "null"]}}, "required": ["ops"]}
        system = ("You edit a short-video storyboard. Translate the user's request into the given typed operations, "
                  "referring to shots and characters by their keys. If the target is ambiguous, return no ops and "
                  "a short clarification question. If the request needs a capability that no operation offers "
                  "(object recolor, background replacement, inpainting, relighting, dubbing), return an "
                  "'unsupported' op. Never invent shot keys.")
        resp = self._c().models.generate_content(
            model=self.model,
            contents=json.dumps({"instruction": instruction, "storyboard": storyboard}, ensure_ascii=False),
            config=types.GenerateContentConfig(system_instruction=system, temperature=0.2,
                                               response_mime_type="application/json", response_json_schema=schema))
        try:
            data = json.loads(resp.text or "")
            return list(data.get("ops") or []), data.get("clarification")
        except (TypeError, json.JSONDecodeError, AttributeError) as e:
            raise ApiError(502, "editor_invalid_output", "the editor returned an invalid edit") from e


_editor_override = None


def set_editor(e) -> None:  # tests
    global _editor_override
    _editor_override = e


def editor(cfg: dict):
    if _editor_override is not None:
        return _editor_override
    if cfg.get("editor", "rule_based") == "gemini":
        model = cfg.get("editor_model") or cfg.get("director_model")
        if not model:
            raise ApiError(503, "editor_not_configured", "the AI editor is not configured")
        return GeminiEditor(model)
    return RuleBasedEditor()


# ---------------------------------------------------------------- proposals

def _render_state(db: Session, project: StudioProject) -> set[str]:
    return {h for (h,) in db.execute(select(StudioShotRender.content_hash).where(
        StudioShotRender.project_id == project.id, StudioShotRender.status == "ready")).all()}


def propose(db: Session, user: User, project: StudioProject, base_version_id: uuid.UUID | None,
            instruction: str | None, raw_ops: list | None, source: str) -> StudioEditOperation:
    cfg = studio.require_enabled(db)
    base = studio.get_version(db, project, base_version_id)
    ed_info: dict = {"provider": "timeline", "label": "timeline"}
    clarification = None
    if raw_ops is None:
        if not instruction:
            raise ApiError(422, "bad_edit", "send an instruction or operations")
        studio._check_text(instruction)
        ed = editor(cfg)
        ed_info = {"provider": ed.name, "label": ed.label}
        raw_ops, clarification = ed.propose(instruction, base.storyboard)
    try:
        ops = parse_ops(raw_ops)
    except ApiError as e:
        if instruction is None:
            raise
        raise ApiError(502, "editor_invalid_output", "the editor returned invalid operations",
                       {"reason": e.code}) from e
    rec = StudioEditOperation(project_id=project.id, base_version_id=base.id, source=source,
                              instruction=instruction, editor=ed_info, ops=[o.model_dump(mode="json") for o in ops],
                              created_by=user.id, preview={})
    unsupported = [o for o in ops if isinstance(o, Unsupported)]
    if clarification and not ops:
        rec.status, rec.clarification = "needs_clarification", clarification[:500]
    elif unsupported and len(unsupported) == len(ops):
        rec.status = "unsupported"
        rec.clarification = "; ".join(f"{o.capability}: {o.reason}" for o in unsupported)[:500]
        rec.preview = {"missing_capabilities": sorted({o.capability for o in unsupported})}
    elif not ops:
        rec.status, rec.clarification = "needs_clarification", (clarification or "Nothing to change.")[:500]
    else:
        hashes = studio.shot_hashes(db, base.storyboard)
        new_sb, notes = apply_ops(base.storyboard, ops, hashes, _render_state(db, project))
        studio.check_locked_lines(base.storyboard, new_sb)  # V7: locked/exact lines are never silently changed
        sb = studio.validate_storyboard(db, user, new_sb, cfg, project)  # same checks as any edit
        new_sb = sb.model_dump(mode="json")
        before = studio.estimate_storyboard(db, user, project, base.storyboard)
        after = studio.estimate_storyboard(db, user, project, new_sb)
        rec.status = "proposed"
        rec.preview = {"storyboard": new_sb, "diff": diff(db, base.storyboard, new_sb), "notes": notes,
                       "unsupported": [o.model_dump() for o in unsupported],
                       "cost": {"render_credits_after": after["credits"], "render_credits_before": before["credits"],
                                "delta_credits": after["credits"] - before["credits"],
                                "new_shots": after["new_shots"], "reused_shots": after["reused_shots"],
                                "missing_capabilities": after["missing_capabilities"],
                                "missing_sources": after["missing_sources"]}}
    db.add(rec)
    db.flush()
    return rec


def get_edit(db: Session, project: StudioProject, edit_id: uuid.UUID) -> StudioEditOperation:
    e = db.get(StudioEditOperation, edit_id)
    if e is None or e.project_id != project.id:
        raise not_found("edit")
    return e


def apply(db: Session, user: User, project: StudioProject, e: StudioEditOperation):
    cfg = studio.require_enabled(db)
    if e.status != "proposed":
        raise ApiError(409, "edit_not_applicable", f"this edit is {e.status}")
    if project.current_version_id != e.base_version_id:
        raise ApiError(409, "stale_edit", "the project changed since this edit was proposed; propose it again")
    sb = studio.validate_storyboard(db, user, e.preview["storyboard"], cfg, project)
    base = studio.get_version(db, project, e.base_version_id)
    v = studio.add_version(db, user, project, sb, "edit", {"edit_id": str(e.id)}, base.director, base.id)
    e.status, e.result_version_id, e.applied_at = "applied", v.id, datetime.now(UTC)
    return v


def undo(db: Session, user: User, project: StudioProject):
    cfg = studio.require_enabled(db)
    cur = studio.get_version(db, project, None)
    if cur.parent_version_id is None:
        raise ApiError(409, "nothing_to_undo", "this is the first version")
    prev = studio.get_version(db, project, cur.parent_version_id)
    sb = studio.validate_storyboard(db, user, prev.storyboard, cfg, project)
    # the restored copy takes prev's place in the lineage, so undoing again keeps walking back
    return studio.add_version(db, user, project, sb, "restore", {"undo_of": str(cur.id)}, prev.director,
                              prev.parent_version_id)


def redo(db: Session, user: User, project: StudioProject):
    """V7: re-apply the version the last undo stepped back from (only right after an undo)."""
    cfg = studio.require_enabled(db)
    cur = studio.get_version(db, project, None)
    undone = (cur.brief or {}).get("undo_of") if cur.source == "restore" else None
    if not undone:
        raise ApiError(409, "nothing_to_redo", "redo is only available right after an undo")
    nxt = studio.get_version(db, project, uuid.UUID(undone))
    sb = studio.validate_storyboard(db, user, nxt.storyboard, cfg, project)
    return studio.add_version(db, user, project, sb, "redo", {"redo_of": str(nxt.id)}, nxt.director, cur.id)


def edit_out(e: StudioEditOperation) -> dict:
    return {"id": str(e.id), "status": e.status, "source": e.source, "instruction": e.instruction,
            "editor": e.editor, "ops": e.ops, "clarification": e.clarification,
            "diff": e.preview.get("diff"), "cost": e.preview.get("cost"), "notes": e.preview.get("notes", []),
            "unsupported": e.preview.get("unsupported", []),
            "missing_capabilities": e.preview.get("missing_capabilities", []),
            "base_version_id": str(e.base_version_id),
            "result_version_id": str(e.result_version_id) if e.result_version_id else None}
