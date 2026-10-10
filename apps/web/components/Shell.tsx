'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { createContext, useContext, useEffect, useState } from 'react';
import { getSession, type SessionInfo } from '@/lib/client';

const Ctx = createContext<SessionInfo>({ mode: 'demo', user: null });
export const useStudio = () => useContext(Ctx);

const NAV = [
  { href: '/app', label: 'Panel' },
  { href: '/app/new', label: 'Yeni proje' },
  { href: '/app/assets', label: 'Varlıklar' },
  { href: '/app/films', label: 'Film & Dizi' },
  { href: '/app/characters', label: 'Dijital oyuncular' },
];

export default function Shell({ children }: { children: React.ReactNode }) {
  const [s, setS] = useState<SessionInfo | null>(null);
  const path = usePathname();
  const router = useRouter();

  useEffect(() => { getSession().then(setS); }, []);
  useEffect(() => {
    if (s?.mode === 'live' && !s.user) router.replace(`/login?next=${encodeURIComponent(path)}`);
  }, [s, path, router]);

  async function logout() {
    await fetch('/api/auth/logout', { method: 'POST', headers: { 'x-av-csrf': '1' } });
    router.push('/');
  }

  if (!s || (s.mode === 'live' && !s.user)) return <p className="container muted" aria-busy="true">Yükleniyor…</p>;
  return (
    <Ctx.Provider value={s}>
      {s.mode === 'demo' && (
        <div className="banner demo" role="status">DEMO modu: bu dağıtıma sunucu bağlanmamış. Projeler yalnızca bu
          tarayıcıda saklanır; yükleme, dışa aktarma ve yapay zekâ üretimi kapalıdır.</div>
      )}
      {s.mode === 'unreachable' && (
        <div className="banner down" role="alert">Sunucuya ulaşılamıyor. Düzenlemeler bu cihazda taslak olarak tutulur ve
          bağlantı gelince gönderilir.</div>
      )}
      <header className="topbar">
        <Link href="/app" className="brand">AI Cinema <span>Studio</span></Link>
        <nav aria-label="Ana menü">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} aria-current={path === n.href ? 'page' : undefined}>{n.label}</Link>
          ))}
        </nav>
        <div className="spacer" />
        {s.user && <span className="muted" style={{ fontSize: 13 }}>{s.user.email} · {s.user.credits ?? 0} kredi</span>}
        {s.user && <button className="btn small" onClick={logout}>Çıkış</button>}
      </header>
      {children}
    </Ctx.Provider>
  );
}
