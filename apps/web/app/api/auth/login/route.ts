import { NextResponse } from 'next/server';
import { apiBase, csrfOk, demoResponse, setTokens, upstream, type Tokens } from '@/lib/server/bff';

export async function POST(req: Request) {
  if (!apiBase()) return demoResponse();
  if (!csrfOk(req)) return NextResponse.json({ code: 'csrf' }, { status: 403 });
  const { email, password } = (await req.json().catch(() => ({}))) as { email?: string; password?: string };
  const r = await upstream('/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                            body: JSON.stringify({ email, password, device_id: 'web' }) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) return NextResponse.json(data, { status: r.status });
  await setTokens(data as Tokens);
  return NextResponse.json({ ok: true, needs_consent: (data as Tokens).needs_consent ?? false });
}
