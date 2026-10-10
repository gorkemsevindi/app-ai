import { NextResponse } from 'next/server';
import { apiBase, csrfOk, demoResponse, setTokens, upstream, type Tokens } from '@/lib/server/bff';

export async function POST(req: Request) {
  if (!apiBase()) return demoResponse();
  if (!csrfOk(req)) return NextResponse.json({ code: 'csrf' }, { status: 403 });
  const b = (await req.json().catch(() => ({}))) as Record<string, unknown>;
  const r = await upstream('/auth/signup', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: b.email, password: b.password, age_confirmed: b.age_confirmed === true,
                           terms_accepted: b.terms_accepted === true, locale: 'tr', acquisition_source: 'web',
                           device_id: 'web' }),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) return NextResponse.json(data, { status: r.status });
  await setTokens(data as Tokens);
  return NextResponse.json({ ok: true }, { status: 201 });
}
