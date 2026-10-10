'use client';

import { useEffect, useState } from 'react';
import { useStudio } from '@/components/Shell';
import { api, errorMessage } from '@/lib/client';

interface Ch { id: string; handle: string; display_name: string | null; status: string; locked: boolean; moderation_status: string;
  current_version: { version: string; status: string } | null; locked_version: { version: string } | null }

export default function Characters() {
  const { mode } = useStudio();
  const [items, setItems] = useState<Ch[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (mode !== 'demo') api<{ items: Ch[] }>('/characters').then((r) => setItems(r.items)).catch((e) => setErr(errorMessage(e))); }, [mode]);
  return (
    <main className="container grid">
      <h1 style={{ margin: 0 }}>Dijital oyuncular</h1>
      <p className="muted" style={{ margin: 0 }}>Karakter oluşturma, çok açılı kimlik ve kilitleme mobil uygulamada; burada karakterlerini ve kilit durumlarını görürsün.
        Gerçek kişi benzerliği yalnızca doğrulanmış rıza ile kullanılabilir.</p>
      {mode === 'demo' && <p className="muted">DEMO modunda kapalıdır.</p>}
      {err && <p role="alert" className="error">{err}</p>}
      <ul className="list">
        {items?.map((c) => (
          <li key={c.id}><strong>@{c.handle}</strong>{c.display_name && <span>{c.display_name}</span>}
            <span className={`badge ${c.locked ? 'ok' : ''}`}>{c.locked ? `Kilitli v${c.locked_version?.version}` : 'Kilitsiz'}</span>
            <span className="badge">{c.moderation_status}</span>
            {c.current_version && <span className="muted" style={{ fontSize: 12 }}>güncel v{c.current_version.version} ({c.current_version.status})</span>}
          </li>
        ))}
        {items?.length === 0 && <li className="muted">Henüz karakter yok.</li>}
      </ul>
    </main>
  );
}
