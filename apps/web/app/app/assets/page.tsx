'use client';

import { useEffect, useRef, useState } from 'react';
import { useStudio } from '@/components/Shell';
import { errorMessage } from '@/lib/client';
import { listAssets, uploadAsset, type EditorAssetOut } from '@/lib/upload';

const KIND: Record<string, string> = { video: 'Video', image: 'Görsel', audio: 'Ses', font: 'Yazı tipi' };
const mb = (n: number) => `${(n / 1024 / 1024).toFixed(1)} MB`;

export default function Assets() {
  const { mode } = useStudio();
  const [items, setItems] = useState<EditorAssetOut[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const load = () => listAssets().then(setItems).catch((e) => setErr(errorMessage(e)));
  useEffect(() => { if (mode !== 'demo') load(); }, [mode]); // eslint-disable-line react-hooks/exhaustive-deps

  async function onFiles(files: FileList | null) {
    if (!files) return;
    setErr(null);
    for (const f of Array.from(files)) {
      try {
        setProgress(0);
        await uploadAsset(f, setProgress);
      } catch (e) {
        setErr(`${f.name}: ${errorMessage(e)}`);
      }
    }
    setProgress(null);
    load();
  }

  return (
    <main className="container grid">
      <div className="row"><h1 style={{ margin: 0 }}>Varlıklar</h1><div className="spacer" />
        <input ref={input} type="file" multiple hidden accept="video/*,image/*,audio/*,.ttf,.otf,.woff,.woff2"
               onChange={(e) => onFiles(e.target.files)} data-testid="asset-input" />
        <button className="btn primary" disabled={mode === 'demo' || progress !== null} onClick={() => input.current?.click()}>Dosya yükle</button>
      </div>
      {mode === 'demo' && <p className="muted">DEMO modunda sunucuya yükleme yapılmaz. Editörde yerel dosyayı yalnızca önizleme için ekleyebilirsin.</p>}
      {progress !== null && <p role="status">Yükleniyor… %{progress}</p>}
      {err && <p role="alert" className="error">{err}</p>}
      <div onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); if (mode !== 'demo') onFiles(e.dataTransfer.files); }}
           className="card muted" style={{ borderStyle: 'dashed', textAlign: 'center' }}>Dosyaları buraya sürükle</div>
      <ul className="list" data-testid="assets">
        {items.map((a) => (
          <li key={a.id}>
            {a.kind === 'image' && a.url ? <img src={a.url} alt="" width={48} height={48} style={{ objectFit: 'cover', borderRadius: 6 }} /> : <span className="badge">{KIND[a.kind]}</span>}
            <strong>{a.name}</strong>
            <span className="muted" style={{ fontSize: 12 }}>{mb(a.size_bytes)}{a.meta.duration_ms ? ` · ${(a.meta.duration_ms / 1000).toFixed(1)} sn` : ''}{a.meta.width ? ` · ${a.meta.width}×${a.meta.height}` : ''}</span>
            <div className="spacer" />
            <span className={`badge ${a.status === 'ready' ? 'ok' : 'warn'}`}>{a.status === 'ready' ? 'Hazır' : 'Bekliyor'}</span>
          </li>
        ))}
      </ul>
    </main>
  );
}
