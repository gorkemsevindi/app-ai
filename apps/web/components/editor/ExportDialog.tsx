'use client';

import { useEffect, useRef, useState } from 'react';
import { api, errorMessage, type Mode } from '@/lib/client';

interface Quote { format: string; quality: string; credits: number; duration_ms: number }
interface Exp { job_id: string; format: string; quality: string; status: string; progress: number | null; credits: number; error: string | null; url: string | null; revision: number }

const STATUS: Record<string, string> = { queued: 'Sırada', running: 'İşleniyor', completed: 'Hazır', failed: 'Başarısız', cancelled: 'İptal' };

/** Export: price quote → explicit confirmation (credits are reserved, settled on success, released on failure). */
export default function ExportDialog({ id, mode, isPhoto, pending, onClose, flush }: {
  id: string; mode: Mode; isPhoto: boolean; pending: boolean; onClose(): void; flush(): Promise<void>;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [format, setFormat] = useState(isPhoto ? 'png' : 'mp4');
  const [quality, setQuality] = useState('720p');
  const [quote, setQuote] = useState<Quote | null>(null);
  const [items, setItems] = useState<Exp[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const key = useRef(crypto.randomUUID());

  useEffect(() => { ref.current?.showModal(); }, []);
  const load = () => mode !== 'demo' && api<{ items: Exp[] }>(`/editor/projects/${id}/exports`).then((r) => setItems(r.items)).catch(() => {});
  useEffect(() => {
    load();
    const iv = setInterval(load, 3000);
    return () => clearInterval(iv);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setQuote(null); key.current = crypto.randomUUID(); }, [format, quality]);

  async function getQuote() {
    setErr(null);
    setBusy(true);
    try {
      await flush();  // export what the user sees: save first
      setQuote(await api<Quote>(`/editor/projects/${id}/render/quote`, { method: 'POST', body: { format, quality } }));
    } catch (e) { setErr(errorMessage(e)); }
    setBusy(false);
  }
  async function confirm() {
    if (!quote) return;
    setBusy(true);
    setErr(null);
    try {
      await api(`/editor/projects/${id}/render`, { method: 'POST', body: { format, quality, confirmed_credits: quote.credits },
                                                   headers: { 'Idempotency-Key': key.current } });
      setQuote(null);
      key.current = crypto.randomUUID();
      load();
    } catch (e) { setErr(errorMessage(e)); }
    setBusy(false);
  }

  return (
    <dialog ref={ref} onClose={onClose} aria-labelledby="exp-title">
      <div className="grid">
        <div className="row"><h2 id="exp-title" style={{ margin: 0, flex: 1 }}>Dışa aktar</h2><button className="btn small ghost" onClick={() => ref.current?.close()} aria-label="Kapat">×</button></div>
        {mode === 'demo' ? <p className="muted">DEMO modunda sunucu tarafı dışa aktarma kapalıdır. Altyazı dosyasını aşağıdan indirebilirsin.</p> : (
          <>
            <div className="row">
              <label className="field" style={{ flex: 1 }}>Biçim
                <select className="input" value={format} onChange={(e) => setFormat(e.target.value)}>
                  {!isPhoto && <><option value="mp4">MP4 (H.264)</option><option value="hevc">MP4 (HEVC)</option><option value="webm">WebM (VP9)</option></>}
                  <option value="png">PNG</option><option value="jpeg">JPEG</option><option value="webp">WebP</option>
                </select>
              </label>
              <label className="field" style={{ flex: 1 }}>Kalite
                <select className="input" value={quality} onChange={(e) => setQuality(e.target.value)}>
                  <option value="720p">720p</option><option value="1080p">1080p</option><option value="2160p">4K</option>
                </select>
              </label>
            </div>
            {pending && <p className="muted" style={{ fontSize: 13 }}>Kaydedilmemiş değişiklikler önce kaydedilecek.</p>}
            {!quote ? <button className="btn" onClick={getQuote} disabled={busy}>Fiyatı göster</button> : (
              <div className="card grid">
                <p style={{ margin: 0 }}>Bu dışa aktarma <strong>{quote.credits} kredi</strong>{quote.duration_ms ? ` (${(quote.duration_ms / 1000).toFixed(1)} sn)` : ''} tutar.
                  Kredi işlem başlarken ayrılır; başarısız olursa iade edilir.</p>
                <button className="btn primary" onClick={confirm} disabled={busy}>Onayla ve başlat</button>
              </div>
            )}
          </>
        )}
        {err && <p role="alert" className="error" style={{ margin: 0 }}>{err}</p>}
        {!isPhoto && (
          <div className="row" style={{ fontSize: 13 }}>
            <span className="muted">Altyazı:</span>
            <a href={`/api/proxy/editor/projects/${id}/subtitles.srt`} download>SRT</a>
            <a href={`/api/proxy/editor/projects/${id}/subtitles.vtt`} download>VTT</a>
          </div>
        )}
        {items.length > 0 && (
          <table className="simple" data-testid="exports">
            <thead><tr><th>Biçim</th><th>Durum</th><th>Kredi</th><th /></tr></thead>
            <tbody>{items.map((x) => (
              <tr key={x.job_id}>
                <td>{x.format.toUpperCase()} {x.quality}</td>
                <td>{STATUS[x.status] ?? x.status}{x.status === 'running' && x.progress != null ? ` %${Math.round(x.progress * (x.progress <= 1 ? 100 : 1))}` : ''}{x.error ? ` (${x.error})` : ''}</td>
                <td>{x.credits}</td>
                <td>{x.url && <a href={x.url} target="_blank" rel="noreferrer">İndir</a>}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </div>
    </dialog>
  );
}
