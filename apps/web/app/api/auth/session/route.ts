import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';
import { apiBase, AT, authed, RT } from '@/lib/server/bff';

export async function GET() {
  if (!apiBase()) return NextResponse.json({ mode: 'demo', user: null });
  const jar = await cookies();
  if (!jar.get(AT) && !jar.get(RT)) return NextResponse.json({ mode: 'live', user: null });
  try {
    const r = await authed('/me');
    if (!r.ok) return NextResponse.json({ mode: 'live', user: null });
    const me = (await r.json()) as Record<string, unknown>;
    return NextResponse.json({ mode: 'live', user: { id: me.id, email: me.email, plan: me.plan,
                                                     credits: me.credits ?? null } });
  } catch {
    return NextResponse.json({ mode: 'unreachable', user: null });
  }
}
