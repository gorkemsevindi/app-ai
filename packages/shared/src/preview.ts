/** Preview math shared by the web and mobile editors. It mirrors the server renderer (worker/editor/render.py):
 * transform = offset from the canvas centre; media are fitted inside the canvas, then scaled; shapes keep their own
 * pixel size; text is placed by alignment (40 px margin) and sized by `size * scale`; fades come from transition_in. */

import type { Canvas, Clip, Project } from './schema.ts';
import { valueAt } from './manifest.ts';

export interface LayerState {
  visible: boolean; x: number; y: number; scale: number; rotation: number; opacity: number;
  /** Position in the source media (ms), for video/audio. */
  source_ms: number;
}

export function isActive(p: Project, c: Clip, t: number): boolean {
  if (p.type === 'photo') return true;
  return t >= c.start_ms && t < c.start_ms + c.duration_ms;
}

export function layerState(p: Project, c: Clip, t: number): LayerState {
  const local = p.type === 'photo' ? 0 : t - c.start_ms;
  const tf = c.transform;
  let opacity = valueAt(c, 'opacity', local, tf.opacity);
  const tr = c.transition_in;
  if ((tr.type === 'fade' || tr.type === 'dissolve') && tr.ms > 0 && p.type !== 'photo') opacity *= Math.min(1, Math.max(0, local / tr.ms));
  const span = Math.round(c.duration_ms * c.speed);
  const off = Math.round(Math.max(0, local) * c.speed);
  return {
    visible: isActive(p, c, t),
    x: valueAt(c, 'x', local, tf.x), y: valueAt(c, 'y', local, tf.y), scale: valueAt(c, 'scale', local, tf.scale),
    rotation: valueAt(c, 'rotation', local, tf.rotation), opacity,
    source_ms: c.in_ms + (c.reverse ? Math.max(0, span - off) : off),
  };
}

/** Size of a media layer (before `scale`) after fitting it inside the canvas. */
export function fitInside(canvas: Canvas, w: number | undefined, h: number | undefined): { width: number; height: number } {
  if (!w || !h) return { width: canvas.width, height: canvas.height };
  const k = Math.min(canvas.width / w, canvas.height / h);
  return { width: w * k, height: h * k };
}
