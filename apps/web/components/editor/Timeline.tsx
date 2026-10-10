'use client';

import { useRef, useState } from 'react';
import type { Command } from '@shared/engine.ts';
import type { Project } from '@shared/schema.ts';
import { durationMs } from '@shared/schema.ts';

interface Props {
  project: Project; t: number; zoom: number; selected: string | null;
  onSeek(t: number): void; onSelect(id: string | null): void; exec(...c: Command[]): boolean;
}
type Drag = { id: string; mode: 'move' | 'l' | 'r'; sx: number; dms: number; track: string };

const SNAP_PX = 8;
const TRACK_LABEL: Record<string, string> = { video: 'Video', audio: 'Ses', text: 'Metin', overlay: 'Katman', effect: 'Efekt' };

/** Multi-track timeline: move (also across compatible tracks), trim both edges, snapping to clip edges, the
 * playhead and 0. Every gesture becomes one engine command, so undo/redo and sync are exact. */
export default function Timeline({ project, t, zoom, selected, onSeek, onSelect, exec }: Props) {
  const [drag, setDrag] = useState<Drag | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const done = useRef<Drag | null>(null);
  const total = Math.max(durationMs(project) + 5000, 15000);
  const px = (ms: number) => ms * zoom;

  const edges = (skip: string) => {
    const out = [0, t];
    for (const c of Object.values(project.clips)) if (c.id !== skip) out.push(c.start_ms, c.start_ms + c.duration_ms);
    return out;
  };
  const snap = (ms: number, skip: string) => {
    let best = ms, dist = SNAP_PX / zoom;
    for (const e of edges(skip)) if (Math.abs(e - ms) < dist) { best = e; dist = Math.abs(e - ms); }
    return Math.max(0, Math.round(best));
  };

  function onMove(e: React.PointerEvent) {
    if (!drag) return;
    const el = document.elementFromPoint(e.clientX, e.clientY)?.closest('[data-track]') as HTMLElement | null;
    setDrag({ ...drag, dms: Math.round((e.clientX - drag.sx) / zoom), track: el?.dataset.track ?? drag.track });
  }
  function onUp() {
    // pointerup and pointerleave can both fire before React re-renders: handle each gesture exactly once
    if (!drag || done.current === drag) return;
    done.current = drag;
    const c = project.clips[drag.id];
    setDrag(null);
    if (!c) return;
    if (drag.mode === 'move') {
      const start = snap(c.start_ms + drag.dms, c.id);
      const end = snap(c.start_ms + drag.dms + c.duration_ms, c.id) - c.duration_ms;
      const to = Math.abs(end - (c.start_ms + drag.dms)) < Math.abs(start - (c.start_ms + drag.dms)) ? Math.max(0, end) : start;
      if (to !== c.start_ms || drag.track !== c.track_id) {
        exec({ type: 'move_clip', clip_id: c.id, start_ms: to, ...(drag.track !== c.track_id ? { track_id: drag.track } : {}) });
      }
    } else if (drag.mode === 'l') {
      const delta = snap(c.start_ms + drag.dms, c.id) - c.start_ms;
      if (delta) exec({ type: 'trim_clip', clip_id: c.id, side: 'start', delta_ms: Math.min(delta, c.duration_ms - 100) });
    } else {
      const end = snap(c.start_ms + c.duration_ms + drag.dms, c.id);
      const delta = Math.max(end - (c.start_ms + c.duration_ms), 100 - c.duration_ms);
      if (delta) exec({ type: 'trim_clip', clip_id: c.id, side: 'end', delta_ms: delta });
    }
  }

  const seekFrom = (e: React.PointerEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    onSeek(Math.max(0, Math.round((e.clientX - r.left) / zoom)));
  };
  const ticks = [];
  const step = zoom > 0.15 ? 1000 : zoom > 0.04 ? 5000 : 15000;
  for (let ms = 0; ms <= total; ms += step) ticks.push(ms);

  return (
    <div className="timeline" ref={ref} onPointerMove={onMove} onPointerUp={onUp} onPointerLeave={onUp} data-testid="timeline">
      <div style={{ display: 'flex' }}>
        <div className="tl-head" style={{ background: 'var(--panel-2)' }} />
        <div className="tl-ruler" style={{ width: px(total), position: 'relative' }} onPointerDown={seekFrom}
             role="slider" aria-label="Oynatma konumu" aria-valuemin={0} aria-valuemax={total} aria-valuenow={t} tabIndex={0}
             onKeyDown={(e) => { if (e.key === 'ArrowRight') onSeek(t + 1000); if (e.key === 'ArrowLeft') onSeek(Math.max(0, t - 1000)); }}>
          {ticks.map((ms) => (
            <span key={ms} style={{ position: 'absolute', left: px(ms), fontSize: 10, color: 'var(--muted)', paddingLeft: 3, borderLeft: '1px solid var(--line)', height: '100%' }}>
              {Math.floor(ms / 60000)}:{String(Math.floor(ms / 1000) % 60).padStart(2, '0')}
            </span>
          ))}
          <div className="tl-playhead" style={{ left: px(t), height: 2000 }} />
        </div>
      </div>
      {[...project.tracks].reverse().map((tr) => (
        <div key={tr.id} className="tl-track">
          <div className="tl-head">
            <strong>{tr.name || TRACK_LABEL[tr.kind]}</strong>
            <div className="row" style={{ gap: 2 }}>
              <button className="btn small ghost" title={tr.hidden ? 'Göster' : 'Gizle'} aria-pressed={tr.hidden}
                      onClick={() => exec({ type: 'set_track', track_id: tr.id, hidden: !tr.hidden })}>{tr.hidden ? '◌' : '◉'}</button>
              <button className="btn small ghost" title={tr.muted ? 'Sesi aç' : 'Sessize al'} aria-pressed={tr.muted}
                      onClick={() => exec({ type: 'set_track', track_id: tr.id, muted: !tr.muted })}>{tr.muted ? '🔇' : '🔊'}</button>
              <button className="btn small ghost" title={tr.locked ? 'Kilidi aç' : 'Kilitle'} aria-pressed={tr.locked}
                      onClick={() => exec({ type: 'set_track', track_id: tr.id, locked: !tr.locked })}>{tr.locked ? '🔒' : '🔓'}</button>
            </div>
          </div>
          <div className="tl-lane" data-track={tr.id} style={{ width: px(total), background: drag && drag.track === tr.id ? 'rgba(255,255,255,.03)' : undefined }}>
            {tr.clip_ids.map((id) => {
              const c = project.clips[id];
              const d = drag?.id === id ? drag : null;
              let left = px(c.start_ms), width = Math.max(4, px(c.duration_ms));
              if (d?.mode === 'move') left += d.dms * zoom;
              if (d?.mode === 'l') { left += d.dms * zoom; width -= d.dms * zoom; }
              if (d?.mode === 'r') width += d.dms * zoom;
              const label = c.text?.content ?? (c.asset_id ? project.assets[c.asset_id]?.name : c.shape ? 'Şekil' : c.name);
              const start = (mode: Drag['mode']) => (e: React.PointerEvent) => {
                e.stopPropagation();
                onSelect(id);
                if (!c.locked && !tr.locked) setDrag({ id, mode, sx: e.clientX, dms: 0, track: tr.id });
              };
              return (
                <div key={id} className={`tl-clip ${tr.kind}${selected === id ? ' selected' : ''}`} data-clip={id}
                     style={{ left, width, opacity: d ? 0.8 : 1 }} onPointerDown={start('move')} title={label}
                     tabIndex={0} role="button" aria-label={`Klip ${label}`} aria-pressed={selected === id}
                     onKeyDown={(e) => { if (e.key === 'Enter') onSelect(id); }}>
                  <span className="h l" onPointerDown={start('l')} aria-hidden />
                  {c.locked ? '🔒 ' : ''}{label}
                  <span className="h r" onPointerDown={start('r')} aria-hidden />
                </div>
              );
            })}
          </div>
        </div>
      ))}
      {!project.tracks.length && <p className="muted" style={{ padding: 12 }}>Soldan medya, metin veya şekil ekle.</p>}
    </div>
  );
}
