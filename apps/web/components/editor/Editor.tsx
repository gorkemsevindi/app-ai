'use client';

import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { Command } from '@shared/engine.ts';
import type { Asset, AssetKind, Project, TrackKind } from '@shared/schema.ts';
import { durationMs } from '@shared/schema.ts';
import { useStudio } from '@/components/Shell';
import { errorMessage } from '@/lib/client';
import { listAssets, probe, uploadAsset, kindOf, type EditorAssetOut } from '@/lib/upload';
import { uid, useEditor } from '@/lib/useEditor';
import ExportDialog from './ExportDialog';
import Inspector from './Inspector';
import ShotPlanner from './ShotPlanner';
import Stage from './Stage';
import Timeline from './Timeline';

const SAVE_LABEL = { saved: 'Kaydedildi', pending: 'Kaydedilecek…', saving: 'Kaydediliyor…', offline: 'Çevrimdışı — yerelde tutuluyor',
                     conflict: 'Çakışma — bazı düzenlemeler uygulanamadı', error: 'Sunucu düzenlemeyi reddetti' } as const;

export default function Editor({ id }: { id: string }) {
  const { mode } = useStudio();
  const { ctl, exec, loadError, toast, setToast, urls, refreshAssets } = useEditor(id, mode);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [zoom, setZoom] = useState(0.08);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<'edit' | 'plan'>('edit');
  const [exporting, setExporting] = useState(false);

  const project = ctl?.session.project;
  const isPhoto = project?.type === 'photo';
  const total = project ? durationMs(project) : 0;

  // playback clock
  const raf = useRef(0);
  useEffect(() => {
    if (!playing) return;
    let last = performance.now();
    const tick = (now: number) => {
      setT((x) => {
        const n = x + (now - last);
        if (n >= total) { setPlaying(false); return total; }
        return n;
      });
      last = now;
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [playing, total]);

  const sel = project && selected ? project.clips[selected] ?? null : null;
  useEffect(() => { if (selected && project && !project.clips[selected]) setSelected(null); }, [project, selected]);

  const split = useCallback(() => {
    if (!sel) return;
    const at = Math.round(t);
    exec({ type: 'split_clip', clip_id: sel.id, at_ms: at, new_clip_id: uid('clip') });
  }, [sel, t, exec]);
  const remove = useCallback((ripple: boolean) => { if (sel) exec({ type: 'remove_clip', clip_id: sel.id, ripple }); }, [sel, exec]);

  // keyboard shortcuts
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || !ctl) return;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === 'z') { e.preventDefault(); if (e.shiftKey) ctl.redo(); else ctl.undo(); }
      else if (mod && e.key.toLowerCase() === 'y') { e.preventDefault(); ctl.redo(); }
      else if (mod && e.key.toLowerCase() === 's') { e.preventDefault(); ctl.flush(); }
      else if (e.key === ' ' && !isPhoto) { e.preventDefault(); setPlaying((p) => !p); }
      else if (e.key.toLowerCase() === 's' && !isPhoto) split();
      else if (e.key === 'Delete' || e.key === 'Backspace') remove(e.shiftKey);
      else if (e.key === 'ArrowRight' && !isPhoto) setT((x) => x + 1000 / (project?.canvas.fps ?? 30));
      else if (e.key === 'ArrowLeft' && !isPhoto) setT((x) => Math.max(0, x - 1000 / (project?.canvas.fps ?? 30)));
      else if (e.key === 'Escape') setSelected(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [ctl, isPhoto, split, remove, project]);

  if (loadError) return <main className="container"><p role="alert" className="error">{loadError}</p><Link href="/app">Panele dön</Link></main>;
  if (!ctl || !project) return <p className="container muted" aria-busy="true">Proje açılıyor…</p>;

  return (
    <div className="editor">
      <div className="editor-bar">
        <Link href="/app" className="btn small ghost" aria-label="Panele dön">←</Link>
        <strong style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{project.title}</strong>
        <span className={`save-state ${ctl.state === 'saved' ? 'ok' : ctl.state === 'conflict' || ctl.state === 'error' ? 'error' : 'muted'}`} role="status" data-testid="save-state">
          {SAVE_LABEL[ctl.state]}{ctl.state === 'saved' ? ` · r${ctl.session.revision}` : ''}
        </span>
        {ctl.state === 'conflict' && <button className="btn small" onClick={() => ctl.dismissConflict()}>Tamam ({ctl.dropped.length})</button>}
        <div className="spacer" />
        <div className="row" role="tablist" aria-label="Görünüm">
          <button role="tab" aria-selected={tab === 'edit'} className={`btn small${tab === 'edit' ? ' primary' : ''}`} onClick={() => setTab('edit')}>Düzenle</button>
          <button role="tab" aria-selected={tab === 'plan'} className={`btn small${tab === 'plan' ? ' primary' : ''}`} onClick={() => setTab('plan')}>Sahne planı</button>
        </div>
        <button className="btn small" onClick={() => ctl.undo()} disabled={!ctl.session.canUndo()} aria-label="Geri al">↶</button>
        <button className="btn small" onClick={() => ctl.redo()} disabled={!ctl.session.canRedo()} aria-label="Yinele">↷</button>
        <button className="btn small primary" onClick={() => setExporting(true)}>Dışa aktar</button>
      </div>
      {toast && (
        <div role="alert" className="banner down" style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
          {toast}<button className="btn small ghost" onClick={() => setToast(null)} aria-label="Kapat">×</button>
        </div>
      )}
      {tab === 'plan' ? <ShotPlanner project={project} exec={exec} /> : (
        <>
          <div className="editor-main">
            <aside className="editor-side" aria-label="Ekle">
              <AddPanel project={project} t={t} exec={exec} mode={mode} onAdded={(id) => setSelected(id)} refreshUrls={refreshAssets} setToast={setToast} />
            </aside>
            <div style={{ display: 'grid', gridTemplateRows: '1fr auto', minHeight: 0 }}>
              <Stage project={project} t={t} playing={playing} selected={selected} urls={urls} onSelect={setSelected} exec={exec} />
              {!isPhoto && (
                <div className="row" style={{ padding: '6px 12px', borderTop: '1px solid var(--line)', background: 'var(--panel)' }}>
                  <button className="btn small" onClick={() => { if (t >= total) setT(0); setPlaying(!playing); }} aria-label={playing ? 'Durdur' : 'Oynat'}>{playing ? '❚❚' : '▶'}</button>
                  <span style={{ fontVariantNumeric: 'tabular-nums', fontSize: 13 }}>{fmt(t)} / {fmt(total)}</span>
                  <button className="btn small" onClick={split} disabled={!sel} title="Oynatma başında böl (S)">Böl</button>
                  <button className="btn small" onClick={() => remove(false)} disabled={!sel}>Sil</button>
                  <button className="btn small" onClick={() => remove(true)} disabled={!sel} title="Sil ve boşluğu kapat (Shift+Del)">Boşluksuz sil</button>
                  <div className="spacer" />
                  <label className="row" style={{ fontSize: 12 }}>Yakınlaştır
                    <input type="range" min={0.01} max={0.5} step={0.01} value={zoom} onChange={(e) => setZoom(Number(e.target.value))} aria-label="Zaman çizelgesi yakınlaştırma" />
                  </label>
                </div>
              )}
            </div>
            <aside className="editor-side right" aria-label="Özellikler">
              <Inspector project={project} clip={sel} t={t} exec={exec} />
              {isPhoto && <Layers project={project} selected={selected} onSelect={setSelected} exec={exec} />}
            </aside>
          </div>
          {!isPhoto && <Timeline project={project} t={t} zoom={zoom} selected={selected} onSeek={(x) => { setPlaying(false); setT(x); }} onSelect={setSelected} exec={exec} />}
        </>
      )}
      {exporting && <ExportDialog id={id} mode={mode} isPhoto={isPhoto} pending={ctl.session.outbox.length > 0}
                                  onClose={() => setExporting(false)} flush={() => ctl.flush()} />}
    </div>
  );
}

const fmt = (ms: number) => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}.${String(Math.floor(ms % 1000 / 100))}`;

function trackFor(p: Project, kind: TrackKind, start: number, dur: number, photo: boolean): { id: string; cmds: Command[] } {
  if (!photo) {
    for (const tr of [...p.tracks].reverse()) {
      if (tr.kind !== kind || tr.locked) continue;
      const free = tr.clip_ids.every((id) => { const c = p.clips[id]; return start >= c.start_ms + c.duration_ms || start + dur <= c.start_ms; });
      if (free) return { id: tr.id, cmds: [] };
    }
  }
  const id = uid('trk');
  const names: Record<TrackKind, string> = { video: 'Video', audio: 'Ses', text: 'Metin', overlay: 'Katman', effect: 'Efekt' };
  return { id, cmds: [{ type: 'add_track', track_id: id, kind, name: `${names[kind]} ${p.tracks.filter((x) => x.kind === kind).length + 1}` }] };
}

function trackEnd(p: Project, kind: TrackKind): number {
  const tr = p.tracks.find((x) => x.kind === kind);
  return tr ? Math.max(0, ...tr.clip_ids.map((id) => p.clips[id].start_ms + p.clips[id].duration_ms)) : 0;
}

function AddPanel({ project, t, exec, mode, onAdded, refreshUrls, setToast }: {
  project: Project; t: number; exec(...c: Command[]): boolean; mode: string; onAdded(id: string): void;
  refreshUrls(): Promise<void>; setToast(s: string | null): void;
}) {
  const photo = project.type === 'photo';
  const [assets, setAssets] = useState<EditorAssetOut[]>([]);
  const [progress, setProgress] = useState<number | null>(null);
  const file = useRef<HTMLInputElement>(null);
  useEffect(() => { if (mode !== 'demo') listAssets().then(setAssets).catch(() => {}); }, [mode]);

  const start = photo ? 0 : Math.round(t);
  function addText() {
    const dur = photo ? 0 : 3000;
    const tr = trackFor(project, 'text', start, dur, photo);
    const id = uid('txt');
    if (exec(...tr.cmds, { type: 'add_clip', clip: { id, track_id: tr.id, start_ms: start, duration_ms: dur, name: 'Metin',
      text: { content: 'Yeni metin', size: Math.round(project.canvas.height / 20), color: '#ffffff', align: 'center', weight: 'bold', font: 'Inter' } } })) onAdded(id);
  }
  function addShape(type: 'rect' | 'ellipse') {
    const dur = photo ? 0 : 3000;
    const tr = trackFor(project, 'overlay', start, dur, photo);
    const id = uid('shp');
    const w = Math.round(project.canvas.width / 3);
    if (exec(...tr.cmds, { type: 'add_clip', clip: { id, track_id: tr.id, start_ms: start, duration_ms: dur, name: 'Şekil',
      shape: { type, width: w, height: type === 'rect' ? Math.round(w / 2) : w, fill: '#e8453c' } } })) onAdded(id);
  }
  function addMedia(a: Asset) {
    const cmds: Command[] = project.assets[a.id] ? [] : [{ type: 'add_asset', asset: a }];
    const kind: TrackKind = a.kind === 'audio' ? 'audio' : photo || a.kind === 'image' && project.tracks.some((x) => x.kind === 'video' && x.clip_ids.length) ? 'overlay' : 'video';
    const dur = photo ? 0 : a.kind === 'image' ? 5000 : Math.max(100, a.duration_ms ?? 5000);
    const at = photo ? 0 : kind === 'video' ? trackEnd(project, 'video') : start;
    const tr = trackFor(project, kind, at, dur, photo);
    const id = uid('clip');
    if (exec(...cmds, ...tr.cmds, { type: 'add_clip', clip: { id, track_id: tr.id, asset_id: a.id, start_ms: at, duration_ms: dur, name: a.name } })) onAdded(id);
  }
  const fromOut = (x: EditorAssetOut): Asset => ({ id: x.id, kind: x.kind, uri: x.uri, name: x.name.slice(0, 120),
    ...(x.meta.duration_ms ? { duration_ms: x.meta.duration_ms } : {}), ...(x.meta.width ? { width: x.meta.width, height: x.meta.height } : {}) });

  async function onFile(f: File | undefined) {
    if (!f) return;
    const kind = kindOf(f);
    if (!kind || kind === 'font') { setToast('Bu dosya türü editöre eklenemiyor.'); return; }
    try {
      if (mode === 'demo') {
        const meta = await probe(f, kind as AssetKind);
        addMedia({ id: uid('loc'), kind, uri: URL.createObjectURL(f), name: f.name.slice(0, 120), ...meta });
        setToast('DEMO: dosya yalnızca bu sekmede önizleme için eklendi; sayfa yenilenince görünmez.');
        return;
      }
      setProgress(0);
      const a = await uploadAsset(f, setProgress);
      setAssets((x) => [a, ...x]);
      await refreshUrls();
      addMedia(fromOut(a));
    } catch (e) { setToast(errorMessage(e)); }
    finally { setProgress(null); }
  }

  return (
    <>
      <div className="grid">
        <strong>Ekle</strong>
        <button className="btn" onClick={addText}>T Metin</button>
        <div className="row"><button className="btn" style={{ flex: 1 }} onClick={() => addShape('rect')}>▭ Dikdörtgen</button>
          <button className="btn" style={{ flex: 1 }} onClick={() => addShape('ellipse')}>◯ Elips</button></div>
        <input ref={file} type="file" hidden accept="video/*,image/*,audio/*" onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = ''; }} data-testid="editor-file" />
        <button className="btn" onClick={() => file.current?.click()} disabled={progress !== null}>{progress !== null ? `Yükleniyor %${progress}` : '⇪ Medya yükle'}</button>
      </div>
      {mode !== 'demo' && (
        <div className="grid">
          <strong>Varlıklarım</strong>
          {assets.filter((a) => a.status === 'ready' && a.kind !== 'font').map((a) => (
            <button key={a.id} className="btn small" style={{ justifyContent: 'flex-start' }} onClick={() => addMedia(fromOut(a))} title="Projeye ekle">
              {a.kind === 'video' ? '🎬' : a.kind === 'audio' ? '🎵' : '🖼'} {a.name.slice(0, 24)}
            </button>
          ))}
          {!assets.length && <span className="muted" style={{ fontSize: 12 }}>Henüz yüklenmiş dosya yok.</span>}
        </div>
      )}
      <div className="grid">
        <strong>Yapay zekâ araçları</strong>
        <span className="muted" style={{ fontSize: 12 }}>Arka plan silme, nesne silme, büyütme, konuşmadan altyazı, seslendirme ve dudak senkronu
          gerçek sağlayıcı bağlanana kadar kapalıdır.</span>
      </div>
    </>
  );
}

function Layers({ project, selected, onSelect, exec }: { project: Project; selected: string | null; onSelect(id: string): void; exec(...c: Command[]): boolean }) {
  const n = project.tracks.length;
  return (
    <div className="grid" data-testid="layers">
      <strong>Katmanlar</strong>
      {[...project.tracks].reverse().map((tr, ri) => {
        const i = n - 1 - ri;
        return tr.clip_ids.map((id) => {
          const c = project.clips[id];
          return (
            <div key={id} className="row" style={{ gap: 4, padding: 4, borderRadius: 6, background: selected === id ? 'var(--panel-2)' : undefined }}>
              <button className="btn small ghost" style={{ flex: 1, justifyContent: 'flex-start' }} onClick={() => onSelect(id)}>
                {c.text ? `T ${c.text.content.slice(0, 18)}` : c.shape ? '▭ Şekil' : `🖼 ${c.name.slice(0, 18)}`}
              </button>
              <button className="btn small ghost" aria-label="Öne getir" disabled={i === n - 1} onClick={() => exec({ type: 'reorder_track', track_id: tr.id, index: i + 1 })}>↑</button>
              <button className="btn small ghost" aria-label="Arkaya gönder" disabled={i === 0} onClick={() => exec({ type: 'reorder_track', track_id: tr.id, index: i - 1 })}>↓</button>
              <button className="btn small ghost" aria-label={tr.hidden ? 'Göster' : 'Gizle'} onClick={() => exec({ type: 'set_track', track_id: tr.id, hidden: !tr.hidden })}>{tr.hidden ? '◌' : '◉'}</button>
            </div>
          );
        });
      })}
    </div>
  );
}
