'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { useStudio } from '@/components/Shell';
import { api, errorMessage } from '@/lib/client';

interface Row { id: string; kind: string; title: string; status: string; format: string }
interface Entry { enabled: boolean; formats: Record<string, [number, number]> }
const KIND: Record<string, string> = { series: 'Dizi', film: 'Film', stars: 'Yıldızlarım', life_story: 'Hayat hikâyem' };
const FORMAT: Record<string, string> = { micro: 'Mikro bölüm', short_series: 'Kısa dizi', standard_episode: 'Standart bölüm',
  long_episode: 'Uzun bölüm (10–30 dk)', short_film: 'Kısa film', feature: 'Uzun metraj' };

export default function Films() {
  const { mode } = useStudio();
  const router = useRouter();
  const [items, setItems] = useState<Row[] | null>(null);
  const [entry, setEntry] = useState<Entry | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [f, setF] = useState({ kind: 'series', title: '', logline: '', format: 'long_episode', minutes: 12, episodes: 3, content_rating: 'general' });

  useEffect(() => {
    if (mode === 'demo') return;
    api<{ items: Row[] }>('/productions').then((r) => setItems(r.items)).catch((e) => setErr(errorMessage(e)));
    api<Entry>('/productions/entry').then(setEntry).catch(() => {});
  }, [mode]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      const p = await api<{ id: string }>('/productions', { method: 'POST', body: {
        kind: f.kind, title: f.title || 'Adsız yapım', logline: f.logline, format: f.format, content_rating: f.content_rating,
        episodes_per_season: f.kind === 'series' ? f.episodes : 1, episode_duration_s: Math.round(f.minutes * 60) } });
      router.push(`/app/films/${p.id}`);
    } catch (x) { setErr(errorMessage(x)); }
  }

  if (mode === 'demo') return <main className="container"><h1>Film & Dizi Fabrikası</h1><p className="muted">Yapımlar sunucuda tutulur; DEMO modunda kapalıdır.</p></main>;
  const range = entry?.formats?.[f.format];
  return (
    <main className="container grid">
      <h1 style={{ margin: 0 }}>Film & Dizi Fabrikası</h1>
      <p className="muted" style={{ margin: 0 }}>Hangi hikâyeyi anlatmak istersin?</p>
      <form className="card grid" onSubmit={create} aria-label="Yeni yapım">
        <div className="row">
          <label className="field" style={{ flex: 1 }}>Tür
            <select className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
              {Object.entries(KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
          <label className="field" style={{ flex: 2 }}>Başlık<input className="input" value={f.title} maxLength={120} onChange={(e) => setF({ ...f, title: e.target.value })} /></label>
        </div>
        <label className="field">Tek cümlelik hikâye<textarea className="input" rows={2} maxLength={600} value={f.logline} onChange={(e) => setF({ ...f, logline: e.target.value })} /></label>
        <div className="row">
          <label className="field" style={{ flex: 2 }}>Biçim
            <select className="input" value={f.format} onChange={(e) => setF({ ...f, format: e.target.value })}>
              {Object.keys(entry?.formats ?? FORMAT).map((k) => <option key={k} value={k}>{FORMAT[k] ?? k}</option>)}
            </select>
          </label>
          <label className="field" style={{ flex: 1 }}>Bölüm süresi (dk)
            <input className="input" type="number" min={range ? range[0] / 60 : 0.5} max={range ? range[1] / 60 : 180} step={0.5} value={f.minutes} onChange={(e) => setF({ ...f, minutes: Number(e.target.value) })} />
          </label>
          {f.kind === 'series' && <label className="field" style={{ flex: 1 }}>Bölüm sayısı<input className="input" type="number" min={1} max={50} value={f.episodes} onChange={(e) => setF({ ...f, episodes: Number(e.target.value) })} /></label>}
          <label className="field" style={{ flex: 1 }}>İçerik
            <select className="input" value={f.content_rating} onChange={(e) => setF({ ...f, content_rating: e.target.value })}>
              <option value="general">Genel</option><option value="teen">13+</option><option value="mature">Yetişkin (kurgu)</option>
            </select>
          </label>
        </div>
        {range && <span className="muted" style={{ fontSize: 12 }}>Bu biçim için süre: {range[0] / 60}–{range[1] / 60} dk</span>}
        {entry && !entry.enabled && <p className="error" style={{ margin: 0 }}>Yapım oluşturma bu hesapta henüz açık değil.</p>}
        <div><button className="btn primary">Yapımı oluştur</button></div>
      </form>
      {err && <p role="alert" className="error">{err}</p>}
      <ul className="list">
        {items?.map((p) => (
          <li key={p.id}><span className="badge">{KIND[p.kind] ?? p.kind}</span><Link href={`/app/films/${p.id}`} style={{ fontWeight: 600 }}>{p.title}</Link>
            <span className="muted" style={{ fontSize: 12 }}>{FORMAT[p.format] ?? p.format} · {p.status}</span></li>
        ))}
        {items?.length === 0 && <li className="muted">Henüz yapım yok.</li>}
      </ul>
    </main>
  );
}
