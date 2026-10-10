'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useState } from 'react';
import type { ProjectType } from '@shared/schema.ts';
import { PRESETS } from '@shared/schema.ts';
import { TEMPLATES } from '@shared/templates.ts';
import { useStudio } from '@/components/Shell';
import { errorMessage } from '@/lib/client';
import { createProject } from '@/lib/projects';

const PRESET_LABEL: Record<string, string> = {
  vertical_1080: 'Dikey 9:16 (1080×1920)', landscape_1080: 'Yatay 16:9 (1920×1080)', square_1080: 'Kare 1:1 (1080×1080)',
  poster_a4: 'Afiş A4 (1240×1754)', thumbnail: 'Kapak görseli (1280×720)',
};
const TEMPLATE_LABEL: Record<string, string> = { movie_poster: 'Film afişi', episode_thumbnail: 'Bölüm kapağı', vertical_story: 'Dikey hikâye' };

function Wizard() {
  const q = useSearchParams();
  const router = useRouter();
  const { mode } = useStudio();
  const [type, setType] = useState<ProjectType>((q.get('type') as ProjectType) || 'video');
  const [title, setTitle] = useState('Yeni proje');
  const [preset, setPreset] = useState(type === 'photo' ? 'poster_a4' : 'vertical_1080');
  const [template, setTemplate] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function go() {
    setBusy(true);
    setErr(null);
    try {
      const p = await createProject(mode, type, title.trim() || 'Yeni proje', template ? null : preset, template);
      router.push(`/app/editor/${p.id}`);
    } catch (e) {
      setErr(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <main className="container grid" style={{ maxWidth: 760 }}>
      <h1 style={{ margin: 0 }}>Yeni proje</h1>
      <div className="card grid">
        <label className="field">Proje adı
          <input className="input" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <fieldset className="row" style={{ border: 0, padding: 0 }}>
          <legend className="muted" style={{ fontSize: 13 }}>Tür</legend>
          {(['video', 'photo', 'social'] as ProjectType[]).map((t) => (
            <label key={t} className="row"><input type="radio" name="type" checked={type === t && !template}
              onChange={() => { setType(t); setTemplate(null); setPreset(t === 'photo' ? 'poster_a4' : 'vertical_1080'); }} />
              {t === 'video' ? 'Video' : t === 'photo' ? 'Fotoğraf / Tasarım' : 'Sosyal medya'}</label>
          ))}
        </fieldset>
        <label className="field">Boyut
          <select className="input" value={preset} disabled={!!template} onChange={(e) => setPreset(e.target.value)}>
            {Object.keys(PRESETS).map((k) => <option key={k} value={k}>{PRESET_LABEL[k] ?? k}</option>)}
          </select>
        </label>
      </div>
      <section className="grid" aria-labelledby="tpl">
        <h2 id="tpl" style={{ margin: 0, fontSize: 18 }}>Ya da bir şablonla başla</h2>
        <div className="tiles">
          {TEMPLATES.map((t) => (
            <button key={t.key} className="tile" aria-pressed={template === t.key}
                    style={{ textAlign: 'left', cursor: 'pointer', borderColor: template === t.key ? 'var(--accent)' : undefined }}
                    onClick={() => { setTemplate(template === t.key ? null : t.key); setType(t.type); }}>
              <strong>{TEMPLATE_LABEL[t.key] ?? t.title}</strong>
              <span className="muted">{t.canvas.width}×{t.canvas.height}</span>
            </button>
          ))}
        </div>
      </section>
      {err && <p role="alert" className="error">{err}</p>}
      <div><button className="btn primary" onClick={go} disabled={busy}>{busy ? 'Oluşturuluyor…' : 'Oluştur ve aç'}</button></div>
    </main>
  );
}

export default function Page() { return <Suspense><Wizard /></Suspense>; }
