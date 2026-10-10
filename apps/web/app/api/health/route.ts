import { NextResponse } from 'next/server';
import { apiBase } from '@/lib/server/bff';

export async function GET() {
  const base = apiBase();
  let api: 'not_configured' | 'ok' | 'unreachable' = 'not_configured';
  if (base) {
    try {
      api = (await fetch(`${base}/healthz`, { cache: 'no-store', signal: AbortSignal.timeout(3000) })).ok ? 'ok' : 'unreachable';
    } catch {
      api = 'unreachable';
    }
  }
  return NextResponse.json({ web: 'ok', api });
}
