'use client';

import { useRouter } from 'next/navigation';
import { use, useEffect, useState } from 'react';
import { useStudio } from '@/components/Shell';
import { api, errorMessage } from '@/lib/client';
import { createProject } from '@/lib/projects';

interface Ep { id: string; season: number; number: number; title: string; target_duration_s: number; status: string }
interface Prod {
  id: string; kind: string; title: string; logline: string; format: string; aspect_ratio: string; content_rating: string;
  status: string; episodes: Ep[];
  budget: { cap_credits: number | null; committed_credits: number; settled_credits: number; reserved_credits: number; refunded_credits: number; remaining_credits: number | null };
}

export default function Production({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { mode } = useStudio();
  const router = useRouter();
  const [p, setP] = useState<Prod | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<Prod>(`/productions/${id}`).then(setP).catch((e) => setErr(errorMessage(e))); }, [id]);

  async function openCut(e: Ep) {
    try {
      const preset = p?.aspect_ratio === '9:16' ? 'vertical_1080' : p?.aspect_ratio === '1:1' ? 'square_1080' : 'landscape_1080';
      const proj = await createProject(mode, 'episode', `${p?.title} · S${e.season}B${e.number} kurgu`.slice(0, 120), preset, null);
      router.push(`/app/editor/${proj.id}`);
    } catch (x) { setErr(errorMessage(x)); }
  }

  if (err) return <main className="container"><p role="alert" className="error">{err}</p></main>;
  if (!p) return <p className="container muted">Yükleniyor…</p>;
  const b = p.budget;
  return (
    <main className="container grid">
      <h1 style={{ margin: 0 }}>{p.title}</h1>
      {p.logline && <p className="muted" style={{ margin: 0 }}>{p.logline}</p>}
      <div className="row"><span className="badge">{p.format}</span><span className="badge">{p.aspect_ratio}</span><span className="badge">{p.content_rating}</span><span className="badge">{p.status}</span></div>
      <section className="card grid" aria-labelledby="budget">
        <h2 id="budget" style={{ margin: 0, fontSize: 18 }}>Bütçe</h2>
        <table className="simple"><tbody>
          <tr><th>Tavan</th><td>{b.cap_credits ?? 'belirlenmedi'}</td></tr>
          <tr><th>Taahhüt edilen</th><td>{b.committed_credits}</td></tr>
          <tr><th>Harcanan</th><td>{b.settled_credits}</td></tr>
          <tr><th>Ayrılmış</th><td>{b.reserved_credits}</td></tr>
          <tr><th>İade</th><td>{b.refunded_credits}</td></tr>
          <tr><th>Kalan</th><td>{b.remaining_credits ?? '—'}</td></tr>
        </tbody></table>
      </section>
      <section className="grid" aria-labelledby="eps">
        <h2 id="eps" style={{ margin: 0, fontSize: 18 }}>Bölümler</h2>
        <ul className="list">
          {p.episodes.map((e) => (
            <li key={e.id}><strong>S{e.season} · B{e.number}</strong> {e.title}
              <span className="muted" style={{ fontSize: 12 }}>{Math.round(e.target_duration_s / 60)} dk · {e.status}</span>
              <div className="spacer" /><button className="btn small" onClick={() => openCut(e)}>Kurgu projesi aç</button></li>
          ))}
        </ul>
        <p className="muted" style={{ fontSize: 12 }}>Senaryo, diyalog düzenleme, süreklilik ve bölüm üretimi mobil uygulamadaki Film Fabrikası ekranlarında ve
          aynı API’de mevcuttur; web’de bu sürümde özet, bütçe ve kurgu projesi açma sunulur.</p>
      </section>
    </main>
  );
}
