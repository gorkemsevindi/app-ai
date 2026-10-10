import type { Command } from '../src/engine.ts';
import type { Project } from '../src/schema.ts';
import { newProject, PRESETS } from '../src/schema.ts';
import { TEMPLATES } from '../src/templates.ts';

export interface Scenario { name: string; initial: Project; commands: Command[]; error?: string }

const vid = (): Project => newProject('p1', 'video', 'Test', PRESETS.landscape_1080);
const base: Command[] = [
  { type: 'add_asset', asset: { id: 'a1', kind: 'video', uri: 'asset:a1', name: 'clip.mp4', duration_ms: 20000, width: 1920, height: 1080 } },
  { type: 'add_asset', asset: { id: 'm1', kind: 'audio', uri: 'asset:m1', name: 'music.mp3', duration_ms: 60000 } },
  { type: 'add_track', track_id: 'v1', kind: 'video', name: 'Main' },
  { type: 'add_track', track_id: 'au', kind: 'audio', name: 'Music' },
  { type: 'add_track', track_id: 'tx', kind: 'text', name: 'Titles' },
  { type: 'add_clip', clip: { id: 'c1', track_id: 'v1', asset_id: 'a1', start_ms: 0, duration_ms: 8000, in_ms: 1000 } },
  { type: 'add_clip', clip: { id: 'c2', track_id: 'v1', asset_id: 'a1', start_ms: 8000, duration_ms: 4000, in_ms: 10000 } },
];

export const SCENARIOS: Scenario[] = [
  { name: 'build_basic', initial: vid(), commands: base },
  { name: 'trim_split_ripple', initial: vid(), commands: [...base,
    { type: 'trim_clip', clip_id: 'c1', side: 'start', delta_ms: 500 },
    { type: 'split_clip', clip_id: 'c1', at_ms: 3000, new_clip_id: 'c1b' },
    { type: 'remove_clip', clip_id: 'c1b', ripple: true },
    { type: 'trim_clip', clip_id: 'c2', side: 'end', delta_ms: -1000 }] },
  { name: 'text_audio_keyframes', initial: vid(), commands: [...base,
    { type: 'add_clip', clip: { id: 't1', track_id: 'tx', start_ms: 1000, duration_ms: 3000, text: { content: 'Merhaba dünya! Siktir git.', size: 72 } } },
    { type: 'set_clip', clip_id: 't1', transform: { y: 400, opacity: 0.9 }, text: { color: '#ffcc00' } },
    { type: 'set_keyframe', clip_id: 't1', prop: 'opacity', t_ms: 0, value: 0 },
    { type: 'set_keyframe', clip_id: 't1', prop: 'opacity', t_ms: 500, value: 1, easing: 'ease_out' },
    { type: 'set_keyframe', clip_id: 'c1', prop: 'scale', t_ms: 0, value: 1 },
    { type: 'set_keyframe', clip_id: 'c1', prop: 'scale', t_ms: 8000, value: 1.2 },
    { type: 'add_clip', clip: { id: 'mu', track_id: 'au', asset_id: 'm1', start_ms: 0, duration_ms: 12000 } },
    { type: 'set_clip', clip_id: 'mu', audio: { volume: 0.4, fade_in_ms: 500, fade_out_ms: 1500 } },
    { type: 'set_transition', clip_id: 'c2', transition: 'fade', ms: 400 },
    { type: 'set_clip', clip_id: 'c2', speed: 2 },
    { type: 'split_clip', clip_id: 'c1', at_ms: 4000, new_clip_id: 'c1r' }] },
  { name: 'move_reorder_lock_group', initial: vid(), commands: [...base,
    { type: 'add_track', track_id: 'ov', kind: 'overlay', name: 'Overlay', index: 1 },
    { type: 'move_clip', clip_id: 'c2', track_id: 'ov', start_ms: 2000 },
    { type: 'reorder_track', track_id: 'tx', index: 0 },
    { type: 'group', group_id: 'g1', clip_ids: ['c1', 'c2'] },
    { type: 'set_track', track_id: 'au', muted: true },
    { type: 'set_clip', clip_id: 'c1', locked: true },
    { type: 'set_canvas', width: 1080, height: 1920 },
    { type: 'set_title', title: 'Yeni başlık' }] },
  { name: 'shot_graph_and_refs', initial: vid(), commands: [...base,
    { type: 'set_shot_graph', shot_graph: { location: 'Kitchen', actors: [{ id: 'a', name: 'Ayşe', character_id: null, position: { x: 0, y: 0, z: 2 }, facing_deg: 180 }],
      cameras: [{ id: 'cam1', name: 'A', position: { x: 0, y: 1.6, z: -2 }, target: { x: 0, y: 1.5, z: 2 }, lens_mm: 35 }],
      lights: [{ id: 'l1', kind: 'key', position: { x: 2, y: 3, z: 0 }, intensity: 0.8, color: '#fff2e0' }],
      shots: [{ id: 's1', camera_id: 'cam1', duration_ms: 4000, description: 'medium', blocking: [{ actor_id: 'a', to: { x: 1, y: 0, z: 2 } }] }] } },
    { type: 'link_dialogue', ref: { clip_id: 'c1', production_id: 'prod1', episode: 1, line_id: 's1-l1' } },
    { type: 'remove_clip', clip_id: 'c1' }] },
  ...TEMPLATES.map((t) => ({ name: `template_${t.key}`, initial: newProject('p1', t.type, t.title, t.canvas), commands: t.commands })),
  { name: 'err_overlap', initial: vid(), commands: [...base, { type: 'move_clip', clip_id: 'c2', start_ms: 4000 }], error: 'overlap' },
  { name: 'err_locked', initial: vid(), commands: [...base, { type: 'set_clip', clip_id: 'c1', locked: true }, { type: 'remove_clip', clip_id: 'c1' }], error: 'locked' },
  { name: 'err_trim_past_media', initial: vid(), commands: [...base, { type: 'trim_clip', clip_id: 'c2', side: 'end', delta_ms: 9000 }], error: 'bad_value' },
  { name: 'err_split_outside', initial: vid(), commands: [...base, { type: 'split_clip', clip_id: 'c1', at_ms: 9000, new_clip_id: 'x' }], error: 'bad_value' },
  { name: 'err_unknown', initial: vid(), commands: [{ type: 'explode' }], error: 'unknown_command' },
  { name: 'err_asset_in_use', initial: vid(), commands: [...base, { type: 'remove_asset', asset_id: 'a1' }], error: 'in_use' },
];
