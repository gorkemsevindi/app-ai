import assert from 'node:assert/strict';
import { test } from 'node:test';
import { applyAll } from '../src/engine.ts';
import { fitInside, layerState } from '../src/preview.ts';
import { newProject, PRESETS } from '../src/schema.ts';

test('preview state follows keyframes, fades, speed and reverse like the renderer', () => {
  const p = applyAll(newProject('p', 'video', 't', PRESETS.landscape_1080), [
    { type: 'add_asset', asset: { id: 'a', kind: 'video', uri: 'asset:x', duration_ms: 20000, width: 640, height: 360 } },
    { type: 'add_track', track_id: 'v', kind: 'video' },
    { type: 'add_clip', clip: { id: 'c', track_id: 'v', asset_id: 'a', start_ms: 1000, duration_ms: 4000, in_ms: 2000 } },
    { type: 'set_keyframe', clip_id: 'c', prop: 'x', t_ms: 0, value: 0 },
    { type: 'set_keyframe', clip_id: 'c', prop: 'x', t_ms: 2000, value: 100 },
    { type: 'set_transition', clip_id: 'c', transition: 'fade', ms: 1000 },
    { type: 'set_clip', clip_id: 'c', speed: 2 },
  ]).project;
  const c = p.clips.c;
  assert.equal(c.duration_ms, 2000);
  assert.equal(layerState(p, c, 500).visible, false);
  const s = layerState(p, c, 2000);
  assert.equal(s.x, 50);
  assert.equal(s.opacity, 1);
  assert.equal(s.source_ms, 4000);
  assert.equal(layerState(p, c, 1500).opacity, 0.5);
  const r = applyAll(p, [{ type: 'set_clip', clip_id: 'c', reverse: true }]).project;
  assert.equal(layerState(r, r.clips.c, 1000).source_ms, 6000);
  assert.deepEqual(fitInside(PRESETS.vertical_1080, 640, 360), { width: 1080, height: 607.5 });
});
