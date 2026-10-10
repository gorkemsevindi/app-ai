/** Command engine (Master Spec V8 §6, ADR-2). Pure and deterministic: `apply(project, command)` returns the new
 * project and the inverse commands (undo = apply the inverse; redo = apply the command again). The server runs a
 * byte-for-byte port of this file (services/api/app/services/editor_engine.py); shared fixtures prove parity.
 * Rules: integer milliseconds, half-up rounding, no clips overlapping on a track (except photo layers),
 * locked tracks/clips refuse edits, every value range-checked. */

import type {
  Asset, Canvas, Clip, CharacterRef, DialogueRef, Easing, Keyframe, KeyframeProp, Project, ShotGraph, Track, TrackKind,
} from './schema.ts';
import { DEFAULT_AUDIO, DEFAULT_TRANSFORM } from './schema.ts';

export class EngineError extends Error {
  code: string;
  constructor(code: string, message?: string) { super(message ?? code); this.code = code; }
}

export type Command = { type: string; [k: string]: unknown };
export interface Result { project: Project; inverse: Command[] }

const KINDS: TrackKind[] = ['video', 'audio', 'text', 'overlay', 'effect'];
const PROPS: KeyframeProp[] = ['x', 'y', 'scale', 'rotation', 'opacity', 'volume'];
const EASINGS: Easing[] = ['linear', 'ease_in', 'ease_out', 'ease_in_out', 'hold'];
const RANGES: Record<string, [number, number]> = {
  x: [-100000, 100000], y: [-100000, 100000], scale: [0.01, 20], rotation: [-360, 360], opacity: [0, 1],
  volume: [0, 4], speed: [0.1, 8],
};

export function roundHalfUp(x: number): number { return Math.floor(x + 0.5); }
const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v));

function fail(code: string, msg?: string): never { throw new EngineError(code, msg); }
function int(v: unknown, name: string, min = 0, max = 86_400_000): number {
  if (typeof v !== 'number' || !Number.isInteger(v) || v < min || v > max) fail('bad_value', `${name} must be an integer in [${min}, ${max}]`);
  return v as number;
}
function num(v: unknown, name: string): number {
  const r = RANGES[name] ?? [-1e9, 1e9];
  if (typeof v !== 'number' || !Number.isFinite(v) || v < r[0] || v > r[1]) fail('bad_value', `${name} out of range`);
  return v as number;
}
function str(v: unknown, name: string, max = 200): string {
  if (typeof v !== 'string' || v.length === 0 || v.length > max) fail('bad_value', `${name} must be a string`);
  return v as string;
}

function track(p: Project, id: unknown): Track {
  const t = p.tracks.find((x) => x.id === id);
  if (!t) fail('not_found', `track ${String(id)}`);
  return t;
}
function clip(p: Project, id: unknown): Clip {
  const c = p.clips[id as string];
  if (!c) fail('not_found', `clip ${String(id)}`);
  return c;
}
function editable(p: Project, c: Clip): void {
  if (c.locked || track(p, c.track_id).locked) fail('locked', 'this layer is locked');
}

function sortTrack(p: Project, t: Track): void {
  t.clip_ids.sort((a, b) => (p.clips[a].start_ms - p.clips[b].start_ms) || (a < b ? -1 : a > b ? 1 : 0));
}
function checkOverlap(p: Project, c: Clip): void {
  if (p.type === 'photo') return;
  for (const id of track(p, c.track_id).clip_ids) {
    if (id === c.id) continue;
    const o = p.clips[id];
    if (c.start_ms < o.start_ms + o.duration_ms && o.start_ms < c.start_ms + c.duration_ms) fail('overlap', `overlaps ${id}`);
  }
}
function checkDuration(p: Project, d: number): void {
  if (p.type !== 'photo' && d <= 0) fail('bad_value', 'duration must be positive');
}

// ---------------------------------------------------------------- primitives (also used as inverses)

function putClip(p: Project, c: Clip): void {
  const prev = p.clips[c.id];
  if (prev && prev.track_id !== c.track_id) {
    const old = track(p, prev.track_id);
    old.clip_ids = old.clip_ids.filter((x) => x !== c.id);
  }
  p.clips[c.id] = clone(c);
  const t = track(p, c.track_id);
  if (!t.clip_ids.includes(c.id)) t.clip_ids.push(c.id);
  sortTrack(p, t);
}
function delClip(p: Project, id: string): void {
  const c = clip(p, id);
  const t = track(p, c.track_id);
  t.clip_ids = t.clip_ids.filter((x) => x !== id);
  delete p.clips[id];
  p.dialogue_refs = p.dialogue_refs.filter((r) => r.clip_id !== id);
}
const snapClip = (c: Clip): Command => ({ type: 'put_clip', clip: clone(c) });

function primitive(p: Project, cmd: Command): boolean {
  switch (cmd.type) {
    case 'put_clip': putClip(p, cmd.clip as Clip); return true;
    case 'del_clip': delClip(p, cmd.clip_id as string); return true;
    case 'put_track': {
      const t = clone(cmd.track as Track);
      p.tracks = p.tracks.filter((x) => x.id !== t.id);
      p.tracks.splice(Math.min(cmd.index as number, p.tracks.length), 0, t);
      return true;
    }
    case 'del_track': p.tracks = p.tracks.filter((x) => x.id !== cmd.track_id); return true;
    case 'put_asset': p.assets[(cmd.asset as Asset).id] = clone(cmd.asset as Asset); return true;
    case 'del_asset': delete p.assets[cmd.asset_id as string]; return true;
    case 'put_canvas': p.canvas = clone(cmd.canvas as Canvas); return true;
    case 'put_title': p.title = cmd.title as string; return true;
    case 'put_refs':
      p.dialogue_refs = clone(cmd.dialogue_refs as DialogueRef[]);
      p.character_refs = clone(cmd.character_refs as CharacterRef[]);
      return true;
    case 'put_shot_graph': p.shot_graph = clone(cmd.shot_graph as ShotGraph | null); return true;
    default: return false;
  }
}

// ---------------------------------------------------------------- commands

function newClip(p: Project, raw: Record<string, unknown>): Clip {
  const id = str(raw.id, 'id', 80);
  if (p.clips[id]) fail('exists', `clip ${id} exists`);
  const t = track(p, raw.track_id);
  const asset = (raw.asset_id ?? null) as string | null;
  if (asset !== null && !p.assets[asset]) fail('not_found', `asset ${asset}`);
  if ((t.kind === 'video' || t.kind === 'audio') && asset === null) fail('bad_value', `${t.kind} clips need an asset`);
  if (t.kind === 'audio' && p.assets[asset as string].kind !== 'audio' && p.assets[asset as string].kind !== 'video') {
    fail('bad_value', 'audio tracks take audio');
  }
  const c: Clip = {
    id, track_id: t.id, asset_id: asset, start_ms: int(raw.start_ms ?? 0, 'start_ms'),
    duration_ms: int(raw.duration_ms ?? 0, 'duration_ms'), in_ms: int(raw.in_ms ?? 0, 'in_ms'),
    speed: 1, reverse: false, transform: { ...DEFAULT_TRANSFORM, ...((raw.transform as object) ?? {}) },
    text: (raw.text ?? null) as Clip['text'], shape: (raw.shape ?? null) as Clip['shape'],
    audio: { ...DEFAULT_AUDIO }, keyframes: {}, transition_in: { type: 'none', ms: 0 }, effects: [],
    group_id: null, locked: false, name: typeof raw.name === 'string' ? raw.name.slice(0, 80) : id,
  };
  if (t.kind === 'text' && !c.text) fail('bad_value', 'text clips need text');
  if (c.text) {
    const defaults = { font: 'Inter', size: 64, color: '#ffffff', align: 'center' as const, weight: 'bold' as const };
    c.text = Object.assign(defaults, c.text, { content: str(c.text.content, 'text', 2000) });
  }
  for (const k of ['x', 'y', 'scale', 'rotation', 'opacity'] as const) num(c.transform[k], k);
  checkDuration(p, c.duration_ms);
  return c;
}

export function apply(project: Project, cmd: Command): Result {
  const p = clone(project);
  const inv: Command[] = [];
  if (primitive(p, cmd)) return { project: p, inverse: [] };
  switch (cmd.type) {
    case 'set_title': {
      inv.push({ type: 'put_title', title: p.title });
      p.title = str(cmd.title, 'title', 120);
      break;
    }
    case 'set_canvas': {
      inv.push({ type: 'put_canvas', canvas: clone(p.canvas) });
      const c = { ...p.canvas };
      if (cmd.width !== undefined) c.width = int(cmd.width, 'width', 16, 7680);
      if (cmd.height !== undefined) c.height = int(cmd.height, 'height', 16, 7680);
      if (cmd.fps !== undefined) c.fps = int(cmd.fps, 'fps', 1, 120);
      if (cmd.background !== undefined) c.background = str(cmd.background, 'background', 20);
      p.canvas = c;
      break;
    }
    case 'add_asset': {
      const a = cmd.asset as Asset;
      str(a?.id, 'asset.id', 80);
      if (!['video', 'image', 'audio', 'font'].includes(a.kind)) fail('bad_value', 'asset kind');
      str(a.uri, 'asset.uri', 500);
      inv.push(p.assets[a.id] ? { type: 'put_asset', asset: clone(p.assets[a.id]) } : { type: 'del_asset', asset_id: a.id });
      p.assets[a.id] = { id: a.id, kind: a.kind, uri: a.uri, name: (a.name ?? a.id).slice(0, 120),
                         ...(a.duration_ms !== undefined ? { duration_ms: int(a.duration_ms, 'duration_ms') } : {}),
                         ...(a.width !== undefined ? { width: int(a.width, 'width', 1, 16384) } : {}),
                         ...(a.height !== undefined ? { height: int(a.height, 'height', 1, 16384) } : {}),
                         ...(a.license ? { license: String(a.license).slice(0, 200) } : {}) };
      break;
    }
    case 'remove_asset': {
      const id = cmd.asset_id as string;
      if (!p.assets[id]) fail('not_found', `asset ${id}`);
      if (Object.values(p.clips).some((c) => c.asset_id === id)) fail('in_use', 'the asset is used by a clip');
      inv.push({ type: 'put_asset', asset: clone(p.assets[id]) });
      delete p.assets[id];
      break;
    }
    case 'add_track': {
      const id = str(cmd.track_id, 'track_id', 80);
      if (p.tracks.some((t) => t.id === id)) fail('exists', `track ${id} exists`);
      const kind = cmd.kind as TrackKind;
      if (!KINDS.includes(kind)) fail('bad_value', 'track kind');
      const index = cmd.index === undefined ? p.tracks.length : int(cmd.index, 'index', 0, p.tracks.length);
      p.tracks.splice(index, 0, { id, kind, name: typeof cmd.name === 'string' ? cmd.name.slice(0, 60) : kind,
                                  clip_ids: [], muted: false, hidden: false, locked: false });
      inv.push({ type: 'del_track', track_id: id });
      break;
    }
    case 'remove_track': {
      const t = track(p, cmd.track_id);
      if (t.clip_ids.length) fail('not_empty', 'remove the clips first');
      inv.push({ type: 'put_track', track: clone(t), index: p.tracks.indexOf(t) });
      p.tracks = p.tracks.filter((x) => x.id !== t.id);
      break;
    }
    case 'reorder_track': {
      const t = track(p, cmd.track_id);
      const from = p.tracks.indexOf(t);
      const to = int(cmd.index, 'index', 0, p.tracks.length - 1);
      inv.push({ type: 'reorder_track', track_id: t.id, index: from });
      p.tracks.splice(from, 1);
      p.tracks.splice(to, 0, t);
      break;
    }
    case 'set_track': {
      const t = track(p, cmd.track_id);
      inv.push({ type: 'put_track', track: clone(t), index: p.tracks.indexOf(t) });
      for (const k of ['muted', 'hidden', 'locked'] as const) if (typeof cmd[k] === 'boolean') t[k] = cmd[k] as boolean;
      if (typeof cmd.name === 'string') t.name = cmd.name.slice(0, 60);
      break;
    }
    case 'add_clip': {
      const c = newClip(p, cmd.clip as Record<string, unknown>);
      if (track(p, c.track_id).locked) fail('locked', 'this track is locked');
      p.clips[c.id] = c;
      track(p, c.track_id).clip_ids.push(c.id);
      checkOverlap(p, c);
      sortTrack(p, track(p, c.track_id));
      inv.push({ type: 'del_clip', clip_id: c.id });
      break;
    }
    case 'remove_clip': {
      const c = clip(p, cmd.clip_id);
      editable(p, c);
      inv.push(snapClip(c));
      if (p.dialogue_refs.some((x) => x.clip_id === c.id)) {
        inv.push({ type: 'put_refs', dialogue_refs: clone(p.dialogue_refs), character_refs: clone(p.character_refs) });
      }
      const t = track(p, c.track_id);
      const later = cmd.ripple === true ? t.clip_ids.map((id) => p.clips[id]).filter((o) => o.start_ms >= c.start_ms + c.duration_ms) : [];
      delClip(p, c.id);
      for (const o of later) {
        if (o.locked) fail('locked', 'a later clip is locked');
        inv.push(snapClip(o));
        o.start_ms -= c.duration_ms;
      }
      sortTrack(p, t);
      break;
    }
    case 'move_clip': {
      const c = clip(p, cmd.clip_id);
      editable(p, c);
      inv.push(snapClip(c));
      const to = cmd.track_id !== undefined ? track(p, cmd.track_id) : track(p, c.track_id);
      if (to.locked) fail('locked', 'the target track is locked');
      const from = track(p, c.track_id);
      const visual = (k: TrackKind) => k === 'video' || k === 'overlay';
      if (to.kind !== from.kind && !(visual(to.kind) && visual(from.kind))) {
        fail('bad_value', `can't move a ${from.kind} clip to a ${to.kind} track`);
      }
      const moved = { ...c, start_ms: int(cmd.start_ms, 'start_ms'), track_id: to.id };
      putClip(p, moved);
      checkOverlap(p, p.clips[c.id]);
      break;
    }
    case 'trim_clip': {
      const c = clip(p, cmd.clip_id);
      editable(p, c);
      inv.push(snapClip(c));
      const delta = int(cmd.delta_ms, 'delta_ms', -86_400_000);
      if (cmd.side === 'start') {
        const src = roundHalfUp(delta * c.speed);
        if (c.in_ms + src < 0) fail('bad_value', 'trim before the start of the media');
        c.start_ms += delta;
        c.in_ms += src;
        c.duration_ms -= delta;
        if (c.start_ms < 0) fail('bad_value', 'start before 0');
      } else if (cmd.side === 'end') {
        c.duration_ms += delta;
      } else fail('bad_value', 'side must be start or end');
      checkDuration(p, c.duration_ms);
      if (c.duration_ms < 0) fail('bad_value', 'negative duration');
      const a = c.asset_id ? p.assets[c.asset_id] : null;
      if (a?.duration_ms !== undefined && a.kind !== 'image' && c.in_ms + roundHalfUp(c.duration_ms * c.speed) > a.duration_ms) {
        fail('bad_value', 'trim beyond the end of the media');
      }
      sortTrack(p, track(p, c.track_id));
      checkOverlap(p, c);
      break;
    }
    case 'split_clip': {
      const c = clip(p, cmd.clip_id);
      editable(p, c);
      const at = int(cmd.at_ms, 'at_ms');
      if (at <= c.start_ms || at >= c.start_ms + c.duration_ms) fail('bad_value', 'split point must be inside the clip');
      const nid = str(cmd.new_clip_id, 'new_clip_id', 80);
      if (p.clips[nid]) fail('exists', `clip ${nid} exists`);
      inv.push({ type: 'del_clip', clip_id: nid }, snapClip(c));
      const first = at - c.start_ms;
      const right: Clip = { ...clone(c), id: nid, start_ms: at, duration_ms: c.duration_ms - first,
                            in_ms: c.in_ms + roundHalfUp(first * c.speed), transition_in: { type: 'none', ms: 0 },
                            keyframes: shiftKeyframes(c.keyframes, first, c.duration_ms - first), name: `${c.name} (2)` };
      c.keyframes = shiftKeyframes(c.keyframes, 0, first);
      c.duration_ms = first;
      putClip(p, c);
      putClip(p, right);
      break;
    }
    case 'slip_clip': {
      const c = clip(p, cmd.clip_id);
      editable(p, c);
      inv.push(snapClip(c));
      c.in_ms = int(cmd.in_ms, 'in_ms');
      const a = c.asset_id ? p.assets[c.asset_id] : null;
      if (a?.duration_ms !== undefined && c.in_ms + roundHalfUp(c.duration_ms * c.speed) > a.duration_ms) fail('bad_value', 'slip beyond the media');
      break;
    }
    case 'set_clip': {
      const c = clip(p, cmd.clip_id);
      if (!(c.locked && Object.keys(cmd).every((k) => k === 'type' || k === 'clip_id' || k === 'locked'))) editable(p, c);
      inv.push(snapClip(c));
      if (cmd.transform) {
        const t = { ...c.transform, ...(cmd.transform as object) };
        for (const k of ['x', 'y', 'scale', 'rotation', 'opacity'] as const) num(t[k], k);
        c.transform = t;
      }
      if (cmd.text) {
        if (!c.text) fail('bad_value', 'not a text clip');
        c.text = { ...c.text, ...(cmd.text as object) };
        str(c.text.content, 'text', 2000);
        int(c.text.size, 'size', 4, 1000);
      }
      if (cmd.shape) {
        if (!c.shape) fail('bad_value', 'not a shape clip');
        c.shape = { ...c.shape, ...(cmd.shape as object) };
      }
      if (cmd.audio) {
        const a = { ...c.audio, ...(cmd.audio as object) };
        num(a.volume, 'volume');
        int(a.fade_in_ms, 'fade_in_ms');
        int(a.fade_out_ms, 'fade_out_ms');
        c.audio = a;
      }
      if (cmd.speed !== undefined) {
        const s = num(cmd.speed, 'speed');
        c.duration_ms = roundHalfUp((c.duration_ms * c.speed) / s);
        c.speed = s;
        checkDuration(p, c.duration_ms);
        checkOverlap(p, c);
      }
      if (typeof cmd.reverse === 'boolean') c.reverse = cmd.reverse;
      if (typeof cmd.locked === 'boolean') c.locked = cmd.locked;
      if (typeof cmd.name === 'string') c.name = cmd.name.slice(0, 80);
      if (Array.isArray(cmd.effects)) c.effects = clone(cmd.effects as Clip['effects']).slice(0, 20);
      break;
    }
    case 'set_keyframe': {
      const c = clip(p, cmd.clip_id);
      editable(p, c);
      const prop = cmd.prop as KeyframeProp;
      if (!PROPS.includes(prop)) fail('bad_value', 'keyframe property');
      const easing = (cmd.easing ?? 'linear') as Easing;
      if (!EASINGS.includes(easing)) fail('bad_value', 'easing');
      const t = int(cmd.t_ms, 't_ms', 0, c.duration_ms);
      inv.push(snapClip(c));
      const kf: Keyframe = { t_ms: t, value: num(cmd.value, prop), easing };
      const list = (c.keyframes[prop] ?? []).filter((k) => k.t_ms !== t);
      list.push(kf);
      list.sort((a, b) => a.t_ms - b.t_ms);
      c.keyframes = { ...c.keyframes, [prop]: list };
      break;
    }
    case 'remove_keyframe': {
      const c = clip(p, cmd.clip_id);
      editable(p, c);
      const prop = cmd.prop as KeyframeProp;
      const list = c.keyframes[prop] ?? [];
      if (!list.some((k) => k.t_ms === cmd.t_ms)) fail('not_found', 'keyframe');
      inv.push(snapClip(c));
      const rest = list.filter((k) => k.t_ms !== cmd.t_ms);
      const next = { ...c.keyframes };
      if (rest.length) next[prop] = rest; else delete next[prop];
      c.keyframes = next;
      break;
    }
    case 'set_transition': {
      const c = clip(p, cmd.clip_id);
      editable(p, c);
      if (!['none', 'fade', 'dissolve'].includes(cmd.transition as string)) fail('bad_value', 'transition');
      inv.push(snapClip(c));
      c.transition_in = { type: cmd.transition as 'none', ms: int(cmd.ms ?? 0, 'ms', 0, Math.max(0, c.duration_ms)) };
      break;
    }
    case 'group': {
      const gid = str(cmd.group_id, 'group_id', 80);
      const ids = cmd.clip_ids as string[];
      if (!Array.isArray(ids) || ids.length < 2) fail('bad_value', 'group needs 2+ clips');
      for (const id of ids) { const c = clip(p, id); inv.push(snapClip(c)); c.group_id = gid; }
      break;
    }
    case 'ungroup': {
      for (const c of Object.values(p.clips)) if (c.group_id === cmd.group_id) { inv.push(snapClip(c)); c.group_id = null; }
      break;
    }
    case 'link_dialogue': {
      const r = cmd.ref as DialogueRef;
      clip(p, r?.clip_id);
      inv.push({ type: 'put_refs', dialogue_refs: clone(p.dialogue_refs), character_refs: clone(p.character_refs) });
      p.dialogue_refs = [...p.dialogue_refs.filter((x) => x.clip_id !== r.clip_id),
        { clip_id: r.clip_id, production_id: str(r.production_id, 'production_id', 80), episode: int(r.episode, 'episode', 1, 10000),
          line_id: str(r.line_id, 'line_id', 40) }];
      break;
    }
    case 'set_character_refs': {
      inv.push({ type: 'put_refs', dialogue_refs: clone(p.dialogue_refs), character_refs: clone(p.character_refs) });
      p.character_refs = clone((cmd.refs as CharacterRef[]) ?? []).slice(0, 50);
      break;
    }
    case 'set_shot_graph': {
      inv.push({ type: 'put_shot_graph', shot_graph: clone(p.shot_graph) });
      p.shot_graph = validateShotGraph(cmd.shot_graph);
      break;
    }
    default:
      fail('unknown_command', String(cmd.type));
  }
  return { project: p, inverse: inv.reverse() };
}

function shiftKeyframes(k: Clip['keyframes'], from: number, len: number): Clip['keyframes'] {
  const out: Clip['keyframes'] = {};
  for (const prop of PROPS) {
    const list = (k[prop] ?? []).filter((x) => x.t_ms >= from && x.t_ms <= from + len).map((x) => ({ ...x, t_ms: x.t_ms - from }));
    if (list.length) out[prop] = list;
  }
  return out;
}

function validateShotGraph(raw: unknown): ShotGraph | null {
  if (raw === null) return null;
  const g = raw as ShotGraph;
  str(g?.location, 'location', 120);
  const ids = new Set<string>();
  for (const list of [g.actors, g.cameras, g.lights, g.shots]) {
    if (!Array.isArray(list) || list.length > 50) fail('bad_value', 'shot graph lists');
    for (const x of list) { str(x.id, 'id', 40); if (ids.has(x.id)) fail('bad_value', `duplicate id ${x.id}`); ids.add(x.id); }
  }
  for (const c of g.cameras) if (typeof c.lens_mm !== 'number' || c.lens_mm < 8 || c.lens_mm > 300) fail('bad_value', 'lens_mm');
  const cams = new Set(g.cameras.map((c) => c.id));
  for (const s of g.shots) {
    if (!cams.has(s.camera_id)) fail('bad_value', `shot ${s.id} uses an unknown camera`);
    int(s.duration_ms, 'duration_ms', 1000, 60000);
  }
  return clone(g);
}

/** Apply a list of commands; on error nothing changes (the caller keeps the original). */
export function applyAll(project: Project, cmds: Command[]): Result {
  let p = project;
  const inverse: Command[] = [];
  for (const c of cmds) {
    const r = apply(p, c);
    p = r.project;
    inverse.unshift(...r.inverse);
  }
  return { project: p, inverse };
}

/** Ids of the clips/tracks/fields a command touches (server-side conflict detection, ADR-3). */
export function touches(cmd: Command): string[] {
  const out: string[] = [];
  for (const k of ['clip_id', 'track_id', 'asset_id', 'new_clip_id', 'group_id']) if (typeof cmd[k] === 'string') out.push(`${k.replace('new_', '')}:${cmd[k]}`);
  const c = cmd.clip as { id?: string } | undefined;
  if (c?.id) out.push(`clip_id:${c.id}`);
  if (Array.isArray(cmd.clip_ids)) for (const id of cmd.clip_ids as string[]) out.push(`clip_id:${id}`);
  if (['set_canvas', 'put_canvas'].includes(cmd.type)) out.push('canvas');
  if (['set_title', 'put_title'].includes(cmd.type)) out.push('title');
  if (['set_shot_graph', 'put_shot_graph'].includes(cmd.type)) out.push('shot_graph');
  if (['link_dialogue', 'set_character_refs', 'put_refs'].includes(cmd.type)) out.push('refs');
  if (['add_track', 'remove_track', 'reorder_track', 'put_track', 'del_track'].includes(cmd.type)) out.push('track_order');
  return out;
}
