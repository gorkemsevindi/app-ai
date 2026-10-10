import Constants from 'expo-constants';
import { Platform } from 'react-native';
import * as Crypto from 'expo-crypto';

import { tokenStore } from './tokenStore';

const BASE_URL: string =
  (Constants.expoConfig?.extra as { apiUrl?: string } | undefined)?.apiUrl ?? 'http://localhost:8000';

/** Stable, machine-readable error codes from the API. UI maps them to localized copy. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public extra: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

export type Json = Record<string, unknown> | unknown[] | null;

let refreshing: Promise<boolean> | null = null;

async function refreshTokens(): Promise<boolean> {
  const refresh = await tokenStore.getRefresh();
  if (!refresh) return false;
  const r = await fetch(`${BASE_URL}/auth/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refresh }),
  });
  if (!r.ok) {
    await tokenStore.clear();
    return false;
  }
  const t = await r.json();
  await tokenStore.set(t.access_token, t.refresh_token);
  return true;
}

export async function api<T = any>(
  path: string,
  opts: { method?: string; body?: Json; idempotencyKey?: string; auth?: boolean } = {},
): Promise<T> {
  const doFetch = async () => {
    const headers: Record<string, string> = { 'Content-Type': 'application/json', 'X-Client': Platform.OS };
    if (opts.auth !== false) {
      const access = await tokenStore.getAccess();
      if (access) headers.Authorization = `Bearer ${access}`;
    }
    if (opts.idempotencyKey) headers['Idempotency-Key'] = opts.idempotencyKey;
    return fetch(`${BASE_URL}${path}`, {
      method: opts.method ?? (opts.body ? 'POST' : 'GET'),
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  };
  let res = await doFetch();
  if (res.status === 401 && opts.auth !== false) {
    // Single-flight refresh so parallel requests don't burn the rotating refresh token.
    refreshing = refreshing ?? refreshTokens().finally(() => (refreshing = null));
    if (await refreshing) res = await doFetch();
  }
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = (data as any)?.detail ?? {};
    throw new ApiError(res.status, d.code ?? 'unknown_error', d.message ?? 'Request failed', d);
  }
  return data as T;
}

export const newIdempotencyKey = () => Crypto.randomUUID();

/** Direct-to-storage upload with a presigned POST (the API never proxies media bytes). */
export async function uploadPresigned(
  url: string,
  fields: Record<string, string>,
  file: { uri: string; mimeType: string; name: string },
): Promise<void> {
  const form = new FormData();
  Object.entries(fields).forEach(([k, v]) => form.append(k, v));
  form.append('file', { uri: file.uri, type: file.mimeType, name: file.name } as unknown as Blob);
  const r = await fetch(url, { method: 'POST', body: form });
  if (!r.ok && r.status !== 204) throw new ApiError(r.status, 'upload_failed', 'Upload failed');
}

// ---- typed endpoints -------------------------------------------------------------------------
export type TemplateSlot = { slot_id: string; label: string; required: boolean; thumbnail_url: string | null };
export type Template = {
  id: string; slug: string; title: string; description: string; category: string;
  thumbnail_url: string | null; preview_url: string | null; duration_s: number; credit_cost: number;
  est_seconds: number; accepts_text: boolean; pro_only: boolean;
  // V3 (optional so older API responses still type-check)
  mode?: 'single' | 'remix'; person_slots?: TemplateSlot[]; est_credits?: number | null; lip_sync_available?: boolean;
};
/** Server-ranked feed card (GET /feed). Ranking weights never reach the client. */
export type FeedItem = {
  id: string; slug: string; title: string; category: string; thumbnail_url: string | null;
  preview_url: string | null; duration_s: number; est_credits: number; person_slots: number; use_count: number;
  est_seconds: number; pro_only: boolean; safety_badge: string; mode: 'single' | 'remix';
  creator: { id: string | null; name: string | null };
};
export type Estimate = { credits: number; confirm_required: boolean; balance: number; sufficient: boolean; persons: number };
export type Generation = {
  id: string; kind: string; status: string; progress: number; queue_position: number | null;
  est_seconds_remaining: number | null; credit_cost: number; refunded: boolean; error_code: string | null;
  error_message: string | null; created_at: string;
  output: { video_url: string; thumbnail_url: string | null; width: number; height: number; watermarked: boolean } | null;
};
export type Profile = {
  id: string; name: string; status: string; thumbnail_url: string | null;
  quality_report: { photos?: number; videos?: number; min_photos?: number };
  assets: { id: string; status: string; rejection_reason: string | null }[];
};
export type Me = { id: string; email: string | null; credits: number; plan: string; needs_consent: boolean; locale: string };
export type SourceVideo = {
  id: string; status: string; rejection_reason: string | null; duration_ms: number | null;
  persons: { track_id: number; label: string; selectable: boolean; flags: string[]; thumbnail_url: string | null }[];
};
export type MultiConfig = {
  enabled: boolean; max_persons: number; max_duration_s: number; min_duration_s: number; resolutions: string[];
  max_upload_mb: number;
};

// ---- AI Studio (V4 Stage B)
export type StudioShot = {
  key: string; duration_s: number; prompt: string; camera?: string; caption?: string | null;
  dialogue?: { character?: string | null; text: string; start_s: number }[]; transition?: 'cut' | 'fade';
  derive?: { kind: 'trim' | 'extend' } | null;
};
export type Storyboard = {
  title: string; language: string; aspect_ratio: '9:16' | '16:9' | '1:1'; style: string;
  scenes: { key: string; title: string; shots: StudioShot[] }[]; limitations: string[];
  captions: { enabled: boolean; burn_in: boolean };
};
export type StudioEstimate = {
  version_id: string; version: number; credits: number; new_shots: number; reused_shots: number;
  limitations: string[]; missing_capabilities: string[]; blocked: { shot: string }[]; within_budget: boolean;
  balance: number; director?: { provider: string; label: string };
};
export type StudioProject = {
  id: string; title: string; status: 'draft' | 'planned' | 'rendering' | 'ready' | 'failed';
  aspect_ratio: string; current_version: { id: string; version: number; storyboard: Storyboard;
    director: { provider?: string; label?: string } } | null;
  shots: { key: string; status: string }[];
  output: { video_url: string; captions_url: string | null; duration_ms: number } | null;
};
export type StudioEdit = {
  id: string; status: 'proposed' | 'needs_clarification' | 'unsupported' | 'applied' | 'rejected';
  clarification: string | null; editor: { label?: string }; notes: string[];
  diff: { added: string[]; removed: string[]; changed: string[]; reordered: boolean;
          duration_s: { before: number; after: number } } | null;
  cost: { render_credits_after: number; delta_credits: number; new_shots: number; reused_shots: number } | null;
  missing_capabilities: string[];
};
