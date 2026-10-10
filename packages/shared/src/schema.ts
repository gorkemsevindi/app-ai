/** Canonical project schema `cp1` (Master Spec V8 §6). One document for video, photo/design, social, film and
 * episode projects, edited only through commands (engine.ts) so web, iOS, Android and the server produce the same
 * JSON. Times are integer milliseconds; ids are client-generated UUIDs/strings. */

export const SCHEMA = 'cp1' as const;
export type ProjectType = 'video' | 'photo' | 'social' | 'film' | 'episode';
export type TrackKind = 'video' | 'audio' | 'text' | 'overlay' | 'effect';
export type AssetKind = 'video' | 'image' | 'audio' | 'font';
export type Easing = 'linear' | 'ease_in' | 'ease_out' | 'ease_in_out' | 'hold';
export type KeyframeProp = 'x' | 'y' | 'scale' | 'rotation' | 'opacity' | 'volume';

export interface Canvas { width: number; height: number; fps: number; background: string }
export interface Asset {
  id: string; kind: AssetKind; uri: string; name: string;
  duration_ms?: number; width?: number; height?: number; license?: string;
}
export interface Transform {
  x: number; y: number; scale: number; rotation: number; opacity: number;
  crop?: { left: number; top: number; right: number; bottom: number };
  blend?: 'normal' | 'multiply' | 'screen' | 'overlay';
}
export interface TextProps {
  content: string; font: string; size: number; color: string; align: 'left' | 'center' | 'right';
  weight: 'normal' | 'bold'; background?: string;
}
export interface ShapeProps { type: 'rect' | 'ellipse'; width: number; height: number; fill: string; radius?: number }
export interface AudioProps { volume: number; fade_in_ms: number; fade_out_ms: number; muted: boolean }
export interface Keyframe { t_ms: number; value: number; easing: Easing }
export interface Transition { type: 'none' | 'fade' | 'dissolve'; ms: number }
export interface Clip {
  id: string; track_id: string; asset_id: string | null;
  start_ms: number; duration_ms: number; in_ms: number; speed: number; reverse: boolean;
  transform: Transform; text: TextProps | null; shape: ShapeProps | null; audio: AudioProps;
  keyframes: Partial<Record<KeyframeProp, Keyframe[]>>; transition_in: Transition;
  effects: { type: string; params: Record<string, number | string> }[];
  group_id: string | null; locked: boolean; name: string;
}
export interface Track { id: string; kind: TrackKind; name: string; clip_ids: string[]; muted: boolean; hidden: boolean; locked: boolean }
export interface DialogueRef { clip_id: string; production_id: string; episode: number; line_id: string }
export interface CharacterRef { character_id: string; identity_version_id: string | null }
export interface Project {
  schema: typeof SCHEMA;
  project_id: string;
  type: ProjectType;
  title: string;
  canvas: Canvas;
  assets: Record<string, Asset>;
  tracks: Track[];          // order = z order (last is on top)
  clips: Record<string, Clip>;
  dialogue_refs: DialogueRef[];
  character_refs: CharacterRef[];
  shot_graph: ShotGraph | null;
}

/** V8 §5 interactive set / storyboard (3D with a 2D top-down fallback). Units: metres, degrees. */
export interface Vec3 { x: number; y: number; z: number }
export interface SetActor { id: string; name: string; character_id: string | null; position: Vec3; facing_deg: number }
export interface SetCamera { id: string; name: string; position: Vec3; target: Vec3; lens_mm: number }
export interface SetLight { id: string; kind: 'key' | 'fill' | 'back' | 'practical'; position: Vec3; intensity: number; color: string }
export interface ShotPlan { id: string; camera_id: string; duration_ms: number; description: string; blocking: { actor_id: string; to: Vec3 }[] }
export interface ShotGraph { location: string; actors: SetActor[]; cameras: SetCamera[]; lights: SetLight[]; shots: ShotPlan[] }

export const DEFAULT_TRANSFORM: Transform = { x: 0, y: 0, scale: 1, rotation: 0, opacity: 1 };
export const DEFAULT_AUDIO: AudioProps = { volume: 1, fade_in_ms: 0, fade_out_ms: 0, muted: false };

export const PRESETS: Record<string, Canvas> = {
  vertical_1080: { width: 1080, height: 1920, fps: 30, background: '#000000' },
  landscape_1080: { width: 1920, height: 1080, fps: 30, background: '#000000' },
  square_1080: { width: 1080, height: 1080, fps: 30, background: '#000000' },
  poster_a4: { width: 1240, height: 1754, fps: 30, background: '#ffffff' },
  thumbnail: { width: 1280, height: 720, fps: 30, background: '#111111' },
};

export function newProject(project_id: string, type: ProjectType, title: string, canvas: Canvas): Project {
  return { schema: SCHEMA, project_id, type, title, canvas: { ...canvas }, assets: {}, tracks: [], clips: {},
           dialogue_refs: [], character_refs: [], shot_graph: null };
}

export function durationMs(p: Project): number {
  let d = 0;
  for (const c of Object.values(p.clips)) d = Math.max(d, c.start_ms + c.duration_ms);
  return d;
}
