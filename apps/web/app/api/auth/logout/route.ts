import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';
import { apiBase, clearTokens, csrfOk, RT, upstream } from '@/lib/server/bff';

export async function POST(req: Request) {
  if (!csrfOk(req)) return NextResponse.json({ code: 'csrf' }, { status: 403 });
  const rt = (await cookies()).get(RT)?.value;
  if (apiBase() && rt) {
    await upstream('/auth/logout', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                     body: JSON.stringify({ refresh_token: rt }) }).catch(() => null);
  }
  await clearTokens();
  return NextResponse.json({ ok: true });
}
