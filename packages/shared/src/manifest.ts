/** Render manifest `rm1`: the flattened, renderer-facing view of a canonical project (V8 §6). Web preview,
 * mobile preview and the server renderer consume the same manifest; the server computes it with its port. */

import type { Clip, Project } from './schema.ts';
import { durationMs } from './schema.ts';

export interface ManifestLayer {
  clip_id: string; z: number; kind: 'video' | 'image' | 'text' | 'shape';
  asset: { id: string; kind: string; uri: string } | null;
  start_ms: number; end_ms: number; in_ms: number; speed: number; reverse: boolean;
  transform: Clip['transform']; keyframes: Clip['keyframes']; text: Clip['text']; shape: Clip['shape'];
  transition_in: Clip['transition_in']; effects: Clip['effects'];
}
export interface ManifestAudio {
  clip_id: string; asset: { id: string; uri: string }; start_ms: number; end_ms: number; in_ms: number; speed: number;
  volume: number; fade_in_ms: number; fade_out_ms: number; keyframes: Clip['keyframes'];
}
export interface Subtitle { start_ms: number; end_ms: number; text: string }
export interface Manifest {
  schema: 'rm1'; project_id: string; type: Project['type']; canvas: Project['canvas']; duration_ms: number;
  layers: ManifestLayer[]; audio: ManifestAudio[]; subtitles: Subtitle[];
}

export function renderManifest(p: Project): Manifest {
  const layers: ManifestLayer[] = [];
  const audio: ManifestAudio[] = [];
  const subtitles: Subtitle[] = [];
  p.tracks.forEach((t, z) => {
    for (const id of t.clip_ids) {
      const c = p.clips[id];
      const a = c.asset_id ? p.assets[c.asset_id] : null;
      const end = c.start_ms + c.duration_ms;
      if ((t.kind === 'audio' || (t.kind === 'video' && a?.kind === 'video')) && a && !t.muted && !c.audio.muted) {
        audio.push({ clip_id: c.id, asset: { id: a.id, uri: a.uri }, start_ms: c.start_ms, end_ms: end, in_ms: c.in_ms,
                     speed: c.speed, volume: c.audio.volume, fade_in_ms: c.audio.fade_in_ms,
                     fade_out_ms: c.audio.fade_out_ms, keyframes: c.keyframes.volume ? { volume: c.keyframes.volume } : {} });
      }
      if (t.kind === 'audio' || t.hidden) continue;
      if (t.kind === 'text' && c.text) subtitles.push({ start_ms: c.start_ms, end_ms: end, text: c.text.content });
      const kind = c.text ? 'text' : c.shape ? 'shape' : a?.kind === 'video' ? 'video' : 'image';
      layers.push({ clip_id: c.id, z, kind, asset: a ? { id: a.id, kind: a.kind, uri: a.uri } : null,
                    start_ms: c.start_ms, end_ms: end, in_ms: c.in_ms, speed: c.speed, reverse: c.reverse,
                    transform: c.transform, keyframes: c.keyframes, text: c.text, shape: c.shape,
                    transition_in: c.transition_in, effects: c.effects });
    }
  });
  subtitles.sort((a, b) => a.start_ms - b.start_ms || a.end_ms - b.end_ms);
  return { schema: 'rm1', project_id: p.project_id, type: p.type, canvas: p.canvas, duration_ms: durationMs(p),
           layers, audio, subtitles };
}

const pad = (n: number, w = 2) => String(n).padStart(w, '0');
function ts(ms: number, sep: string): string {
  const h = Math.floor(ms / 3_600_000), m = Math.floor((ms % 3_600_000) / 60_000), s = Math.floor((ms % 60_000) / 1000);
  return `${pad(h)}:${pad(m)}:${pad(s)}${sep}${pad(ms % 1000, 3)}`;
}
export function toSrt(subs: Subtitle[]): string {
  return subs.map((s, i) => `${i + 1}\n${ts(s.start_ms, ',')} --> ${ts(s.end_ms, ',')}\n${s.text}\n`).join('\n');
}
export function toVtt(subs: Subtitle[]): string {
  return 'WEBVTT\n\n' + subs.map((s) => `${ts(s.start_ms, '.')} --> ${ts(s.end_ms, '.')}\n${s.text}\n`).join('\n');
}

/** Value of an animated property at local time t (same interpolation as the renderer). */
export function valueAt(c: Clip, prop: keyof Clip['keyframes'], t: number, base: number): number {
  const ks = c.keyframes[prop];
  if (!ks?.length) return base;
  if (t <= ks[0].t_ms) return ks[0].value;
  for (let i = 0; i < ks.length - 1; i++) {
    const a = ks[i], b = ks[i + 1];
    if (t >= a.t_ms && t <= b.t_ms) {
      // the easing of the keyframe being moved towards shapes the segment ("ease into this value")
      if (b.easing === 'hold' || b.t_ms === a.t_ms) return t >= b.t_ms ? b.value : a.value;
      let u = (t - a.t_ms) / (b.t_ms - a.t_ms);
      if (b.easing === 'ease_in') u = u * u;
      else if (b.easing === 'ease_out') u = 1 - (1 - u) * (1 - u);
      else if (b.easing === 'ease_in_out') u = u < 0.5 ? 2 * u * u : 1 - 2 * (1 - u) * (1 - u);
      return a.value + (b.value - a.value) * u;
    }
  }
  return ks[ks.length - 1].value;
}
