'use client';
/** Browser-side API access. Everything goes through the same-origin BFF (/api/proxy); tokens stay in httpOnly
 * cookies. In DEMO mode (no API configured) the editor runs on the shared engine with a local store. */

import type { Command } from '@shared/engine.ts';
import { applyAll } from '@shared/engine.ts';
import type { Canvas, Project, ProjectType } from '@shared/schema.ts';
import { newProject, PRESETS } from '@shared/schema.ts';
import { TEMPLATES } from '@shared/templates.ts';
import type { CommandsResult, DraftStore, Transport } from './sync';
import { TransportError } from './sync';

export async function api<T = unknown>(path: string, init: { method?: string; body?: unknown; headers?: Record<string, string> } = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers ?? {}) };
  if (init.method && init.method !== 'GET') headers['x-av-csrf'] = '1';
  if (init.body !== undefined) headers['content-type'] = 'application/json';
  let r: Response;
  try {
    r = await fetch(`/api/proxy${path}`, { method: init.method ?? 'GET', headers, credentials: 'same-origin',
                                           body: init.body === undefined ? undefined : JSON.stringify(init.body) });
  } catch (e) {
    throw new TransportError(0, 'network', e instanceof Error ? e.message : 'network error');
  }
  const ct = r.headers.get('content-type') ?? '';
  const data = ct.includes('json') ? await r.json().catch(() => null) : await r.text();
  if (!r.ok) {
    const d = (data && typeof data === 'object' && 'detail' in data ? (data as { detail: unknown }).detail : data) as Record<string, unknown> | null;
    const det = d && typeof d === 'object' ? d : {};
    throw new TransportError(r.status, String(det.code ?? `http_${r.status}`), String(det.message ?? r.statusText), det);
  }
  return data as T;
}

export async function authPost(path: string, body: unknown): Promise<{ ok: boolean; status: number; data: Record<string, unknown> }> {
  const r = await fetch(path, { method: 'POST', headers: { 'content-type': 'application/json', 'x-av-csrf': '1' },
                                body: JSON.stringify(body), credentials: 'same-origin' });
  const data = (await r.json().catch(() => ({}))) as Record<string, unknown>;
  return { ok: r.ok, status: r.status, data };
}

export interface ProjectRow { id: string; type: ProjectType; title: string; revision: number; updated_at: string }
export interface ProjectOut extends ProjectRow { project: Project }

export function errorMessage(e: unknown): string {
  if (e instanceof TransportError) {
    const map: Record<string, string> = {
      network: 'Sunucuya ulaşılamadı. Değişiklikler bu cihazda saklanıyor.',
      unauthenticated: 'Oturum süresi doldu, lütfen tekrar giriş yapın.',
      feature_disabled: 'Editör bu hesapta henüz açık değil.',
      insufficient_credits: 'Kredi yetersiz.',
      confirmation_required: 'Fiyat değişti; lütfen yeniden onaylayın.',
      demo_mode: 'Bu dağıtımda API yapılandırılmamış (DEMO).',
      csrf: 'İstek güvenlik kontrolünden geçmedi.',
    };
    return map[e.code] ?? `${e.message} (${e.code})`;
  }
  return e instanceof Error ? e.message : String(e);
}

// ---------------------------------------------------------------- local drafts (offline queue)

const safe = <T,>(f: () => T, fallback: T): T => { try { return f(); } catch { return fallback; } };

export const localDrafts: DraftStore = {
  load: (id) => safe(() => JSON.parse(localStorage.getItem(`av.draft.${id}`) ?? 'null'), null),
  save: (id, d) => safe(() => localStorage.setItem(`av.draft.${id}`, JSON.stringify(d)), undefined),
  clear: (id) => safe(() => localStorage.removeItem(`av.draft.${id}`), undefined),
};

// ---------------------------------------------------------------- live transport

export function liveTransport(id: string): Transport {
  return {
    commands: (base_revision, commands, key) =>
      api<CommandsResult>(`/editor/projects/${id}/commands`, { method: 'POST', body: { base_revision, commands },
                                                                headers: { 'Idempotency-Key': key } }),
    get: async () => {
      const o = await api<ProjectOut>(`/editor/projects/${id}`);
      return { revision: o.revision, project: o.project };
    },
  };
}

// ---------------------------------------------------------------- DEMO store (no backend; this browser only)

interface DemoRec { revision: number; project: Project; updated_at: string; keys: string[] }
const demoKey = 'av.demo.projects';
function demoAll(): Record<string, DemoRec> { return safe(() => JSON.parse(localStorage.getItem(demoKey) ?? '{}'), {}); }
function demoSave(all: Record<string, DemoRec>) { safe(() => localStorage.setItem(demoKey, JSON.stringify(all)), undefined); }

export const demo = {
  list(): ProjectRow[] {
    return Object.values(demoAll()).map((r) => ({ id: r.project.project_id, type: r.project.type, title: r.project.title,
                                                  revision: r.revision, updated_at: r.updated_at }))
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  },
  create(type: ProjectType, title: string, preset: string | null, template: string | null): ProjectOut {
    const id = crypto.randomUUID();
    const t = template ? TEMPLATES.find((x) => x.key === template) : undefined;
    const canvas: Canvas = t?.canvas ?? PRESETS[preset ?? ''] ?? PRESETS.vertical_1080;
    let p = newProject(id, t ? t.type : type, title, canvas);
    if (t) p = applyAll(p, t.commands).project;
    const all = demoAll();
    all[id] = { revision: 0, project: p, updated_at: new Date().toISOString(), keys: [] };
    demoSave(all);
    return { id, type: p.type, title, revision: 0, updated_at: all[id].updated_at, project: p };
  },
  remove(id: string) { const all = demoAll(); delete all[id]; demoSave(all); },
  transport(id: string): Transport {
    return {
      async commands(base, cmds: Command[], key) {
        const all = demoAll();
        const r = all[id];
        if (!r) throw new TransportError(404, 'not_found', 'project not found');
        if (r.keys.includes(key)) return { revision: r.revision, project: r.project };
        if (base !== r.revision) throw new TransportError(409, 'revision_conflict', 'conflict', { revision: r.revision, project: r.project });
        r.project = applyAll(r.project, cmds).project;
        r.revision += 1;
        r.keys = [...r.keys.slice(-50), key];
        r.updated_at = new Date().toISOString();
        demoSave(all);
        return { revision: r.revision, project: r.project };
      },
      async get() {
        const r = demoAll()[id];
        if (!r) throw new TransportError(404, 'not_found', 'project not found');
        return { revision: r.revision, project: r.project };
      },
    };
  },
};

export type Mode = 'demo' | 'live' | 'unreachable';
export interface SessionInfo { mode: Mode; user: { id: string; email: string | null; plan: string; credits: number | null } | null }
export async function getSession(): Promise<SessionInfo> {
  try {
    const r = await fetch('/api/auth/session', { cache: 'no-store' });
    return (await r.json()) as SessionInfo;
  } catch {
    return { mode: 'unreachable', user: null };
  }
}
