/** Built-in, original starter templates (no third-party IP). Each is a list of commands on an empty project, so
 * templates go through the same engine and validation as any edit. */

import type { Command } from './engine.ts';
import type { Canvas } from './schema.ts';
import { PRESETS } from './schema.ts';

export interface Template { key: string; title: string; type: 'video' | 'photo' | 'social'; canvas: Canvas; commands: Command[] }

const text = (id: string, track: string, content: string, y: number, size: number, start = 0, duration = 0): Command => ({
  type: 'add_clip', clip: { id, track_id: track, start_ms: start, duration_ms: duration, transform: { y },
                           text: { content, size, color: '#ffffff', weight: 'bold', align: 'center', font: 'Inter' } },
});

export const TEMPLATES: Template[] = [
  { key: 'movie_poster', title: 'Movie poster', type: 'photo', canvas: { ...PRESETS.poster_a4, background: '#0e0e12' },
    commands: [
      { type: 'add_track', track_id: 'bg', kind: 'overlay', name: 'Background' },
      { type: 'add_clip', clip: { id: 'band', track_id: 'bg', shape: { type: 'rect', width: 1240, height: 420, fill: '#e8453c' }, transform: { y: 520 } } },
      { type: 'add_track', track_id: 'title', kind: 'text', name: 'Title' },
      text('t1', 'title', 'YOUR STORY', -500, 120),
      { type: 'add_track', track_id: 'sub', kind: 'text', name: 'Tagline' },
      text('t2', 'sub', 'Coming soon', -340, 56),
    ] },
  { key: 'episode_thumbnail', title: 'Episode thumbnail', type: 'photo', canvas: PRESETS.thumbnail,
    commands: [
      { type: 'add_track', track_id: 'title', kind: 'text', name: 'Title' },
      text('t1', 'title', 'EPISODE 1', -120, 110),
      { type: 'add_track', track_id: 'sub', kind: 'text', name: 'Subtitle' },
      text('t2', 'sub', 'The beginning', 60, 60),
    ] },
  { key: 'vertical_story', title: 'Vertical story', type: 'social', canvas: PRESETS.vertical_1080,
    commands: [
      { type: 'add_track', track_id: 'v1', kind: 'video', name: 'Video' },
      { type: 'add_track', track_id: 'cap', kind: 'text', name: 'Captions' },
      text('c1', 'cap', 'Your caption here', 700, 64, 0, 3000),
    ] },
];
