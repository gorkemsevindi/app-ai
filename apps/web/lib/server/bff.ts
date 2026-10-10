/** Backend-for-frontend helpers (server only). The browser never sees API tokens: they live in httpOnly cookies
 * and are attached here when requests are forwarded to the FastAPI service (V8 ADR-5). */

import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';

export const AT = 'av_at';
export const RT = 'av_rt';
const REFRESH_MAX_AGE = 30 * 24 * 3600;

export function apiBase(): string | null {
  const v = process.env.API_BASE_URL?.trim();
  return v ? v.replace(/\/+$/, '') : null;
}

export interface Tokens { access_token: string; refresh_token: string; expires_in: number; needs_consent?: boolean }

export async function setTokens(t: Tokens): Promise<void> {
  const jar = await cookies();
  const secure = process.env.NODE_ENV === 'production';
  const base = { httpOnly: true, secure, sameSite: 'lax' as const, path: '/' };
  jar.set(AT, t.access_token, { ...base, maxAge: Math.max(60, t.expires_in - 30) });
  jar.set(RT, t.refresh_token, { ...base, maxAge: REFRESH_MAX_AGE });
}

export async function clearTokens(): Promise<void> {
  const jar = await cookies();
  jar.delete(AT);
  jar.delete(RT);
}

export async function upstream(path: string, init: RequestInit = {}, token?: string | null): Promise<Response> {
  const base = apiBase();
  if (!base) throw new Error('API_BASE_URL is not configured');
  const headers = new Headers(init.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  headers.set('X-Client', 'web');
  return fetch(`${base}${path}`, { ...init, headers, cache: 'no-store', redirect: 'manual' });
}

/** Refresh the session with the rotating refresh token; returns the new access token or null. */
export async function refresh(): Promise<string | null> {
  const jar = await cookies();
  const rt = jar.get(RT)?.value;
  if (!rt) return null;
  const r = await upstream('/auth/refresh', { method: 'POST', body: JSON.stringify({ refresh_token: rt }),
                                              headers: { 'Content-Type': 'application/json' } });
  if (!r.ok) {
    await clearTokens();
    return null;
  }
  const t = (await r.json()) as Tokens;
  await setTokens(t);
  return t.access_token;
}

/** Call the API as the signed-in user, refreshing once on 401. */
export async function authed(path: string, init: RequestInit = {}): Promise<Response> {
  const jar = await cookies();
  let token = jar.get(AT)?.value ?? null;
  if (!token) token = await refresh();
  if (!token) return NextResponse.json({ code: 'unauthenticated', message: 'sign in first' }, { status: 401 });
  let r = await upstream(path, init, token);
  if (r.status === 401) {
    token = await refresh();
    if (!token) return NextResponse.json({ code: 'unauthenticated', message: 'sign in first' }, { status: 401 });
    r = await upstream(path, init, token);
  }
  return r;
}

/** CSRF defence for state-changing BFF calls: same-origin Origin header plus a custom header that a cross-site
 * form cannot send. Cookies are also SameSite=Lax. */
export function csrfOk(req: Request): boolean {
  if (req.headers.get('x-av-csrf') !== '1') return false;
  const origin = req.headers.get('origin');
  if (!origin) return false;
  const host = req.headers.get('x-forwarded-host') ?? req.headers.get('host');
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

export function demoResponse(): NextResponse {
  return NextResponse.json({ code: 'demo_mode', message: 'the API is not configured for this deployment' },
                           { status: 503 });
}

/** Copy an upstream response (status, JSON/text body, content type) back to the browser. */
export async function relay(r: Response): Promise<NextResponse> {
  const body = r.status === 204 ? null : await r.arrayBuffer();
  const headers = new Headers();
  const ct = r.headers.get('content-type');
  if (ct) headers.set('content-type', ct);
  headers.set('cache-control', 'no-store');
  return new NextResponse(body, { status: r.status, headers });
}
