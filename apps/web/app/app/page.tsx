'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { useStudio } from '@/components/Shell';
import { errorMessage, type ProjectRow } from '@/lib/client';
import { deleteProject, listProjects, TYPE_LABEL } from '@/lib/projects';

const TILES = [
  { href: '/app/films', t: 'Film', d: 'Fikirden filme' },
  { href: '/app/films', t: 'Dizi', d: 'Bölüm bölüm üretim' },
  { href: '/app/new?type=video', t: 'Video Düzenle', d: 'Zaman çizelgesi editörü' },
  { href: '/app/new?type=photo', t: 'Fotoğraf / Tasarım', d: 'Afiş, kapak, sosyal' },
  { href: '/app/characters', t: 'Dijital Oyuncu', d: 'Karakterlerin ve kadron' },
  { href: '/app/films?lifestory=1', t: 'Hayat Hikâyem', d: 'Kendi hikâyenden film' },
  { href: '/app/new?tab=templates', t: 'Şablonlar', d: 'Hazır başlangıçlar' },
  { href: '/app/assets', t: 'Varlıklar', d: 'Video, görsel, ses' },
];

export default function Dashboard() {
  const { mode } = useStudio();
  const [items, setItems] = useState<ProjectRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = () => listProjects(mode).then(setItems).catch((e) => setErr(errorMessage(e)));
  useEffect(() => { load(); }, [mode]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <main className="container grid">
      <h1 style={{ margin: 0 }}>Stüdyo</h1>
      <section aria-labelledby="start" className="grid">
        <h2 id="start" style={{ margin: 0, fontSize: 18 }}>Ne üretmek istersin?</h2>
        <div className="tiles">
          {TILES.map((t) => (
            <Link key={t.t} href={t.href} className="tile"><strong>{t.t}</strong><span className="muted">{t.d}</span></Link>
          ))}
        </div>
      </section>
      <section aria-labelledby="recent" className="grid">
        <div className="row"><h2 id="recent" style={{ margin: 0, fontSize: 18 }}>Son projeler</h2>
          <div className="spacer" /><Link className="btn small primary" href="/app/new">Yeni proje</Link></div>
        {err && <p role="alert" className="error">{err}</p>}
        {!items && !err && <p className="muted">Yükleniyor…</p>}
        {items?.length === 0 && <p className="muted">Henüz proje yok. Yukarıdan bir başlangıç seç.</p>}
        <ul className="list" data-testid="projects">
          {items?.map((p) => (
            <li key={p.id}>
              <span className="badge">{TYPE_LABEL[p.type] ?? p.type}</span>
              <Link href={`/app/editor/${p.id}`} style={{ fontWeight: 600 }}>{p.title}</Link>
              <span className="muted" style={{ fontSize: 12 }}>r{p.revision} · {new Date(p.updated_at).toLocaleString('tr-TR')}</span>
              <div className="spacer" />
              <button className="btn small ghost" aria-label={`${p.title} projesini sil`}
                      onClick={async () => { if (confirm('Proje silinsin mi?')) { await deleteProject(mode, p.id); load(); } }}>Sil</button>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
