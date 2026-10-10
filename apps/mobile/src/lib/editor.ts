/** Mobile side of the V8 editor: the same engine, session and sync controller as the web app (packages/shared),
 * an API transport, and an offline draft store on the device file system. */

import { File, Paths } from 'expo-file-system';
import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { AppState } from 'react-native';

import type { Command } from '@shared/engine.ts';
import { EngineError } from '@shared/engine.ts';
import type { Project } from '@shared/schema.ts';
import { EditorSession } from '@shared/session.ts';
import { SyncController, TransportError, type DraftStore, type Transport } from '@shared/sync.ts';

import { api, ApiError, newIdempotencyKey } from './api';

export interface ProjectOut { id: string; type: Project['type']; title: string; revision: number; project: Project; updated_at: string }

const draftFile = (id: string) => new File(Paths.document, `editor-draft-${id.replace(/[^a-zA-Z0-9-]/g, '')}.json`);

export const fileDrafts: DraftStore = {
  load(id) {
    try {
      const f = draftFile(id);
      return f.exists ? JSON.parse(f.textSync()) : null;
    } catch { return null; }
  },
  save(id, d) {
    try {
      const f = draftFile(id);
      if (!f.exists) f.create();
      f.write(JSON.stringify(d));
    } catch { /* best effort: the session still holds the outbox */ }
  },
  clear(id) {
    try { const f = draftFile(id); if (f.exists) f.delete(); } catch { /* ignore */ }
  },
};

const asTransport = (e: unknown): never => {
  if (e instanceof ApiError) throw new TransportError(e.status, e.code, e.message, e.extra);
  throw new TransportError(0, 'network', e instanceof Error ? e.message : 'network error');
};

export function apiTransport(id: string): Transport {
  return {
    commands: async (base_revision, commands, key) => {
      try {
        return await api(`/editor/projects/${id}/commands`, { body: { base_revision, commands }, idempotencyKey: key });
      } catch (e) { return asTransport(e); }
    },
    get: async () => {
      try {
        const o = await api<ProjectOut>(`/editor/projects/${id}`);
        return { revision: o.revision, project: o.project };
      } catch (e) { return asTransport(e); }
    },
  };
}

export function useMobileEditor(id: string) {
  const ctl = useRef<SyncController | null>(null);
  const [version, bump] = useReducer((x: number) => x + 1, 0);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const t = apiTransport(id);
    t.get().then((cur) => {
      if (!alive) return;
      const c = new SyncController(new EditorSession(cur.project, cur.revision, `m-${newIdempotencyKey().slice(0, 8)}`), t, fileDrafts, bump);
      ctl.current = c;
      if (c.restoreDraft() > 0) c.flush();
      bump();
    }).catch((e) => setError(e instanceof Error ? e.message : String(e)));
    return () => { alive = false; };
  }, [id]);

  useEffect(() => {
    const c = ctl.current;
    if (!c || c.state !== 'pending') return;
    const h = setTimeout(() => c.flush(), 800);
    return () => clearTimeout(h);
  }, [version]);
  useEffect(() => {
    // retry when the app returns to the foreground and periodically while offline
    const sub = AppState.addEventListener('change', (s) => { if (s === 'active' && ctl.current?.session.outbox.length) ctl.current.flush(); });
    const iv = setInterval(() => { if (ctl.current?.state === 'offline') ctl.current.flush(); }, 10_000);
    return () => { sub.remove(); clearInterval(iv); };
  }, []);

  const exec = useCallback((...cmds: Command[]) => {
    try {
      ctl.current?.exec(...cmds);
      return true;
    } catch (e) {
      setNotice(e instanceof EngineError ? e.code : String(e));
      return false;
    }
  }, []);

  return { ctl: ctl.current, version, exec, error, notice, setNotice };
}

export const uid = (prefix: string) => `${prefix}_${newIdempotencyKey().replace(/-/g, '').slice(0, 10)}`;

/** Direct upload of a picked file: presigned PUT, then the API verifies the object. */
export async function uploadPicked(file: { uri: string; mimeType: string; name: string; size: number;
                                           duration_ms?: number; width?: number; height?: number },
                                   kind: 'video' | 'image' | 'audio') {
  const up = await api<{ asset_id: string; uri: string; upload: { url: string; headers: Record<string, string> } }>(
    '/editor/assets', { body: { kind, mime: file.mimeType, size_bytes: file.size, name: file.name } });
  const blob = await (await fetch(file.uri)).blob();
  const r = await fetch(up.upload.url, { method: 'PUT', headers: up.upload.headers, body: blob });
  if (!r.ok) throw new Error(`upload failed (${r.status})`);
  const meta: Record<string, number> = {};
  for (const k of ['duration_ms', 'width', 'height'] as const) if (file[k]) meta[k] = Math.round(file[k] as number);
  return api<{ id: string; kind: string; name: string; uri: string; url: string | null; meta: Record<string, number> }>(
    `/editor/assets/${up.asset_id}/complete`, { body: meta });
}
