import { NextResponse } from 'next/server';
import { apiBase, authed, csrfOk, demoResponse, relay } from '@/lib/server/bff';

// Only these API areas are reachable through the BFF (no admin, no internal worker routes).
const ALLOWED = new Set(['editor', 'me', 'productions', 'characters', 'studio', 'credits']);
const FORWARD = ['content-type', 'idempotency-key'];

async function handle(req: Request, ctx: { params: Promise<{ path: string[] }> }) {
  if (!apiBase()) return demoResponse();
  const { path } = await ctx.params;
  if (!path.length || !ALLOWED.has(path[0]) || path.some((s) => s === '..' || s.includes('/'))) {
    return NextResponse.json({ code: 'not_found', message: 'unknown endpoint' }, { status: 404 });
  }
  if (req.method !== 'GET' && !csrfOk(req)) {
    return NextResponse.json({ code: 'csrf', message: 'cross-site request refused' }, { status: 403 });
  }
  const url = new URL(req.url);
  const headers: Record<string, string> = {};
  for (const h of FORWARD) {
    const v = req.headers.get(h);
    if (v) headers[h] = v;
  }
  const body = req.method === 'GET' || req.method === 'HEAD' ? undefined : await req.text();
  const r = await authed(`/${path.map(encodeURIComponent).join('/')}${url.search}`, { method: req.method, body, headers });
  return relay(r);
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
