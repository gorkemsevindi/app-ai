import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { apply, EngineError, applyAll } from '../src/engine.ts';
import { renderManifest, toSrt, toVtt, valueAt } from '../src/manifest.ts';
import { durationMs } from '../src/schema.ts';
import { EditorSession } from '../src/session.ts';
import { SCENARIOS } from './scenarios.ts';

const fixtures = JSON.parse(readFileSync(new URL('../fixtures/engine.json', import.meta.url), 'utf8'));

test('fixtures are reproduced by the TS engine (contract with the Python port)', () => {
  assert.equal(fixtures.length, SCENARIOS.length);
  for (const f of fixtures) {
    let p = f.initial;
    try {
      for (const c of f.commands) p = apply(p, c).project;
      assert.equal(f.error, undefined, f.name);
      assert.deepEqual(p, f.expected, f.name);
      assert.deepEqual(renderManifest(p), f.manifest, f.name);
    } catch (e) {
      if (!(e instanceof EngineError)) throw e;
      assert.equal(e.code, f.error, f.name);
    }
  }
});

test('every command is exactly undoable (apply inverse -> previous state)', () => {
  for (const f of fixtures.filter((x: { error?: string }) => !x.error)) {
    let p = f.initial;
    for (const c of f.commands) {
      const r = apply(p, c);
      const back = applyAll(r.project, r.inverse).project;
      assert.deepEqual(back, p, `${f.name}: ${c.type}`);
      p = r.project;
    }
  }
});

test('manifest, subtitles and keyframe interpolation', () => {
  const f = fixtures.find((x: { name: string }) => x.name === 'text_audio_keyframes');
  const m = renderManifest(f.expected);
  assert.equal(m.duration_ms, durationMs(f.expected));
  assert.deepEqual(m.subtitles, [{ start_ms: 1000, end_ms: 4000, text: 'Merhaba dünya! Siktir git.' }]);
  assert.match(toSrt(m.subtitles), /^1\n00:00:01,000 --> 00:00:04,000\nMerhaba dünya! Siktir git\.\n/);
  assert.ok(toVtt(m.subtitles).startsWith('WEBVTT\n\n00:00:01.000 --> 00:00:04.000'));
  const t1 = f.expected.clips.t1;
  assert.equal(valueAt(t1, 'opacity', 0, 1), 0);
  assert.equal(valueAt(t1, 'opacity', 250, 1), 0.75);  // ease_out at u=0.5
  assert.equal(valueAt(t1, 'opacity', 2000, 1), 1);
  const music = m.audio.find((a) => a.clip_id === 'mu');
  assert.equal(music?.volume, 0.4);
  assert.equal(f.expected.clips.c2.duration_ms, 2000);  // speed 2 halves the duration
});

test('session: optimistic edits, undo/redo, idempotent batches, rebase after conflict', () => {
  const s0 = SCENARIOS[0];
  let p = s0.initial;
  for (const c of s0.commands) p = apply(p, c).project;
  const s = new EditorSession(p, 7, 'web');
  s.exec({ type: 'trim_clip', clip_id: 'c1', side: 'end', delta_ms: -2000 });
  s.exec({ type: 'set_title', title: 'Bir' });
  assert.equal(s.project.clips.c1.duration_ms, 6000);
  s.undo();
  assert.equal(s.project.title, 'Test');
  s.redo();
  assert.equal(s.project.title, 'Bir');
  const b1 = s.nextBatch()!;
  assert.equal(b1.idempotency_key, s.nextBatch()!.idempotency_key);  // stable until acknowledged
  assert.equal(b1.base_revision, 7);
  // the server applied it -> revision 8
  let server = p;
  for (const c of b1.commands) server = apply(server, c).project;
  s.acknowledge(b1, 8, server);
  assert.equal(s.outbox.length, 0);
  assert.deepEqual(s.project, server);
  // conflict: someone else deleted c2 while we trimmed it
  s.exec({ type: 'trim_clip', clip_id: 'c2', side: 'end', delta_ms: -500 });
  const other = apply(server, { type: 'remove_clip', clip_id: 'c2' }).project;
  const { dropped } = s.rebase(other, 9);
  assert.equal(dropped.length, 1);
  assert.equal(s.project.clips.c2, undefined);
  assert.throws(() => s.exec({ type: 'remove_clip', clip_id: 'nope' }), EngineError);
  assert.equal(s.revision, 9);
});
