/** Typed client for the V8 editor API (used by the web BFF on the server and by the mobile app). */

import type { Command } from './engine.ts';
import type { Manifest } from './manifest.ts';
import type { Canvas, Project, ProjectType } from './schema.ts';

export interface EditorProjectOut {
  id: string; type: ProjectType; title: string; revision: number; project: Project; updated_at: string;
}
export interface CommandsOut { revision: number; project: Project; applied: number; replay?: boolean; rebased?: number }
export interface EditorAssetUpload { asset_id: string; upload: { url: string; method: 'PUT'; headers: Record<string, string> } }
export interface RenderOut { job_id: string; status: string; credits: number; format: string }

export type Fetcher = (path: string, init?: { method?: string; body?: unknown; headers?: Record<string, string> }) => Promise<unknown>;

export function editorApi(f: Fetcher) {
  return {
    list: () => f('/editor/projects') as Promise<{ items: EditorProjectOut[] }>,
    create: (body: { type: ProjectType; title: string; canvas?: Canvas; preset?: string; template?: string }) =>
      f('/editor/projects', { method: 'POST', body }) as Promise<EditorProjectOut>,
    get: (id: string) => f(`/editor/projects/${id}`) as Promise<EditorProjectOut>,
    commands: (id: string, base_revision: number, commands: Command[], idempotency_key: string) =>
      f(`/editor/projects/${id}/commands`, { method: 'POST', body: { base_revision, commands },
                                             headers: { 'Idempotency-Key': idempotency_key } }) as Promise<CommandsOut>,
    revisions: (id: string) => f(`/editor/projects/${id}/revisions`) as Promise<{ items: { revision: number; created_at: string; commands: number }[] }>,
    restore: (id: string, revision: number) => f(`/editor/projects/${id}/restore`, { method: 'POST', body: { revision } }) as Promise<CommandsOut>,
    manifest: (id: string) => f(`/editor/projects/${id}/manifest`) as Promise<Manifest>,
    uploadAsset: (body: { kind: string; mime: string; size_bytes: number; name: string }) =>
      f('/editor/assets', { method: 'POST', body }) as Promise<EditorAssetUpload>,
    completeAsset: (id: string, meta: { duration_ms?: number; width?: number; height?: number }) =>
      f(`/editor/assets/${id}/complete`, { method: 'POST', body: meta }) as Promise<{ id: string; status: string; uri: string }>,
    assets: () => f('/editor/assets') as Promise<{ items: { id: string; kind: string; name: string; uri: string; url: string | null; status: string }[] }>,
    render: (id: string, format: string, confirmed_credits: number | null, key: string) =>
      f(`/editor/projects/${id}/render`, { method: 'POST', body: { format, confirmed_credits }, headers: { 'Idempotency-Key': key } }) as Promise<RenderOut>,
    exports: (id: string) => f(`/editor/projects/${id}/exports`) as Promise<{ items: { job_id: string; format: string; status: string; url: string | null }[] }>,
  };
}
