// Sync controller against a simulated authoritative server (the shared engine): disconnects, lost responses,
// conflicts and offline drafts must never lose or double-apply an edit (V8 definition of done).
import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { Command } from '../../../../packages/shared/src/engine.ts';
import { applyAll, touches } from '../../../../packages/shared/src/engine.ts';
import { newProject, PRESETS, type Project } from '../../../../packages/shared/src/schema.ts';
import { EditorSession } from '../../../../packages/shared/src/session.ts';
import { SyncController, TransportError, type DraftStore, type Transport } from '../sync.ts';

class FakeServer {
  revision = 0;
  project: Project;
  keys = new Map<string, number>();
  log: Command[][] = [];
  failNext: 'network' | 'lost_response' | null = null;
  constructor(p: Project) { this.project = p; }
  transport(): Transport {
    return {
      commands: async (base, cmds, key) => {
        if (this.failNext === 'network') { this.failNext = null; throw new TypeError('fetch failed'); }
        if (this.keys.has(key)) return { revision: this.revision, project: this.project };
        if (base !== this.revision) {
          const theirs = new Set(this.log.slice(base).flat().flatMap(touches));
          if (cmds.flatMap(touches).some((t) => theirs.has(t))) {
            throw new TransportError(409, 'revision_conflict', 'conflict', { revision: this.revision, project: this.project });
          }
        }
        this.project = applyAll(this.project, cmds).project;
        this.revision += 1;
        this.log.push(cmds);
        this.keys.set(key, this.revision);
        if (this.failNext === 'lost_response') { this.failNext = null; throw new TypeError('connection reset'); }
        return { revision: this.revision, project: this.project };
      },
      get: async () => ({ revision: this.revision, project: this.project }),
    };
  }
}

function memDrafts(): DraftStore & { m: Map<string, unknown> } {
  const m = new Map<string, unknown>();
  return { m, load: (id) => (m.get(id) as never) ?? null, save: (id, d) => { m.set(id, structuredClone(d)); }, clear: (id) => { m.delete(id); } };
}

const base = () => applyAll(newProject('p1', 'video', 'T', PRESETS.vertical_1080), [
  { type: 'add_track', track_id: 'txt', kind: 'text' },
  { type: 'add_track', track_id: 'txt2', kind: 'text' },
]).project;
const text = (id: string, track = 'txt', start = 0): Command => ({ type: 'add_clip', clip: { id, track_id: track, start_ms: start, duration_ms: 1000, text: { content: id } } });

test('autosave sends a batch and acknowledges the server revision', async () => {
  const srv = new FakeServer(base());
  const s = new SyncController(new EditorSession(srv.project, 0, 'a'), srv.transport(), memDrafts());
  s.exec(text('a'));
  s.exec(text('b', 'txt', 2000));
  await s.flush();
  assert.equal(s.state, 'saved');
  assert.equal(srv.revision, 1);
  assert.deepEqual(s.session.project, srv.project);
});

test('network failure keeps the edit and its key; a lost response is not applied twice', async () => {
  const srv = new FakeServer(base());
  const drafts = memDrafts();
  const s = new SyncController(new EditorSession(srv.project, 0, 'b'), srv.transport(), drafts);
  s.exec(text('a'));
  srv.failNext = 'network';
  await s.flush();
  assert.equal(s.state, 'offline');
  assert.equal(srv.revision, 0);
  assert.ok(drafts.m.has('p1'), 'offline draft persisted');
  srv.failNext = 'lost_response';  // applied on the server, response never arrives
  await s.flush();
  assert.equal(s.state, 'offline');
  await s.flush();                  // retry with the same idempotency key => replay, not a second apply
  assert.equal(s.state, 'saved');
  assert.equal(srv.revision, 1);
  assert.equal(Object.keys(srv.project.clips).length, 1);
  assert.ok(!drafts.m.has('p1'), 'draft cleared once saved');
});

test('a closed tab: the draft is replayed by the next session', async () => {
  const srv = new FakeServer(base());
  const drafts = memDrafts();
  const s1 = new SyncController(new EditorSession(srv.project, 0, 'c'), srv.transport(), drafts);
  s1.exec(text('a'));
  srv.failNext = 'network';
  await s1.flush();
  const s2 = new SyncController(new EditorSession(srv.project, srv.revision, 'd'), srv.transport(), drafts);
  assert.equal(s2.restoreDraft(), 1);
  await s2.flush();
  assert.equal(s2.state, 'saved');
  assert.ok(srv.project.clips.a);
});

test('concurrent edits on different items rebase; edits on the same item conflict and are reported', async () => {
  const srv = new FakeServer(base());
  const web = new SyncController(new EditorSession(srv.project, 0, 'w'), srv.transport(), memDrafts());
  const phone = new SyncController(new EditorSession(srv.project, 0, 'p'), srv.transport(), memDrafts());
  web.exec(text('a'));
  phone.exec(text('b', 'txt2'));
  await web.flush();
  await phone.flush();  // server-side rebase (disjoint)
  assert.equal(phone.state, 'saved');
  assert.ok(srv.project.clips.a && srv.project.clips.b);

  web.exec({ type: 'set_clip', clip_id: 'a', text: { content: 'web' } });
  phone.session.rebase(srv.project, srv.revision);
  phone.exec({ type: 'remove_clip', clip_id: 'a' });
  await phone.flush();
  await web.flush();      // conflict: clip a is gone -> the web edit is dropped and reported, nothing crashes
  assert.equal(web.state, 'conflict');
  assert.equal(web.dropped.length, 1);
  assert.deepEqual(web.session.project, srv.project);
});
