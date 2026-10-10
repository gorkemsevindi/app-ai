'use client';

import { useEffect, useState } from 'react';
import type { Command } from '@shared/engine.ts';
import { layerState } from '@shared/preview.ts';
import type { Clip, KeyframeProp, Project } from '@shared/schema.ts';
import { PRESETS } from '@shared/schema.ts';

interface Props { project: Project; clip: Clip | null; t: number; exec(...c: Command[]): boolean }

/** Number input that commits on blur/Enter (one command per edit, not per keystroke). */
function Num({ label, value, step = 1, min, max, onCommit }: { label: string; value: number; step?: number; min?: number; max?: number; onCommit(v: number): void }) {
  const [v, setV] = useState(String(value));
  useEffect(() => setV(String(value)), [value]);
  const commit = () => { const n = Number(v); if (Number.isFinite(n) && n !== value) onCommit(n); else setV(String(value)); };
  return (
    <label className="field">{label}
      <input className="input" type="number" step={step} min={min} max={max} value={v} onChange={(e) => setV(e.target.value)}
             onBlur={commit} onKeyDown={(e) => { if (e.key === 'Enter') commit(); }} />
    </label>
  );
}

const KF_LABEL: Record<KeyframeProp, string> = { x: 'X', y: 'Y', scale: 'Ölçek', rotation: 'Döndürme', opacity: 'Opaklık', volume: 'Ses' };

export default function Inspector({ project, clip, t, exec }: Props) {
  if (!clip) return <CanvasPanel project={project} exec={exec} />;
  const c = clip;
  const st = layerState(project, c, t);
  const local = project.type === 'photo' ? 0 : Math.min(Math.max(0, t - c.start_ms), c.duration_ms);
  const asset = c.asset_id ? project.assets[c.asset_id] : null;
  const hasAudio = asset && (asset.kind === 'audio' || asset.kind === 'video');
  const visual = !(asset?.kind === 'audio');
  const tf = (k: 'x' | 'y' | 'scale' | 'rotation' | 'opacity') => (v: number) => {
    if (c.keyframes[k]?.length) exec({ type: 'set_keyframe', clip_id: c.id, prop: k, t_ms: local, value: v, easing: 'linear' });
    else exec({ type: 'set_clip', clip_id: c.id, transform: { [k]: v } });
  };

  return (
    <div className="grid" data-testid="inspector">
      <div className="row"><strong style={{ flex: 1 }}>{c.text ? 'Metin' : c.shape ? 'Şekil' : asset?.kind === 'audio' ? 'Ses' : asset?.kind === 'image' ? 'Görsel' : 'Video'}</strong>
        <button className="btn small" onClick={() => exec({ type: 'set_clip', clip_id: c.id, locked: !c.locked })}>{c.locked ? 'Kilidi aç' : 'Kilitle'}</button>
      </div>
      {c.text && (
        <>
          <label className="field">İçerik
            <textarea className="input" rows={3} defaultValue={c.text.content} key={`${c.id}-${c.text.content}`} maxLength={2000}
                      onBlur={(e) => e.target.value && e.target.value !== c.text?.content && exec({ type: 'set_clip', clip_id: c.id, text: { content: e.target.value } })} />
          </label>
          <div className="row">
            <Num label="Boyut" value={c.text.size} min={4} max={1000} onCommit={(v) => exec({ type: 'set_clip', clip_id: c.id, text: { size: Math.round(v) } })} />
            <label className="field">Renk<input type="color" value={c.text.color} onChange={(e) => exec({ type: 'set_clip', clip_id: c.id, text: { color: e.target.value } })} /></label>
          </div>
          <div className="row">
            <select className="input" aria-label="Hizalama" value={c.text.align} onChange={(e) => exec({ type: 'set_clip', clip_id: c.id, text: { align: e.target.value } })}>
              <option value="left">Sola</option><option value="center">Ortaya</option><option value="right">Sağa</option>
            </select>
            <select className="input" aria-label="Kalınlık" value={c.text.weight} onChange={(e) => exec({ type: 'set_clip', clip_id: c.id, text: { weight: e.target.value } })}>
              <option value="normal">Normal</option><option value="bold">Kalın</option>
            </select>
          </div>
          <label className="row"><input type="checkbox" checked={!!c.text.background}
            onChange={(e) => exec({ type: 'set_clip', clip_id: c.id, text: { background: e.target.checked ? '#000000' : '' } })} /> Arka plan kutusu</label>
        </>
      )}
      {c.shape && (
        <div className="row">
          <Num label="Genişlik" value={c.shape.width} min={1} onCommit={(v) => exec({ type: 'set_clip', clip_id: c.id, shape: { width: Math.round(v) } })} />
          <Num label="Yükseklik" value={c.shape.height} min={1} onCommit={(v) => exec({ type: 'set_clip', clip_id: c.id, shape: { height: Math.round(v) } })} />
          <label className="field">Dolgu<input type="color" value={c.shape.fill} onChange={(e) => exec({ type: 'set_clip', clip_id: c.id, shape: { fill: e.target.value } })} /></label>
        </div>
      )}
      {visual && (
        <fieldset className="grid" style={{ border: '1px solid var(--line)', borderRadius: 8 }}>
          <legend className="muted" style={{ fontSize: 12 }}>Dönüşüm{Object.keys(c.keyframes).length ? ' (anahtar karede)' : ''}</legend>
          <div className="row">
            <Num label="X" value={Math.round(st.x)} onCommit={tf('x')} />
            <Num label="Y" value={Math.round(st.y)} onCommit={tf('y')} />
          </div>
          <div className="row">
            <Num label="Ölçek" value={+st.scale.toFixed(2)} step={0.05} min={0.01} max={20} onCommit={tf('scale')} />
            <Num label="Döndürme" value={Math.round(st.rotation)} min={-360} max={360} onCommit={tf('rotation')} />
          </div>
          <Num label="Opaklık" value={+st.opacity.toFixed(2)} step={0.05} min={0} max={1} onCommit={tf('opacity')} />
        </fieldset>
      )}
      {project.type !== 'photo' && (
        <>
          <fieldset className="grid" style={{ border: '1px solid var(--line)', borderRadius: 8 }}>
            <legend className="muted" style={{ fontSize: 12 }}>Anahtar kareler (oynatma başında: {(local / 1000).toFixed(2)} sn)</legend>
            <div className="row" style={{ gap: 4 }}>
              {(['x', 'y', 'scale', 'rotation', 'opacity', ...(hasAudio ? ['volume'] : [])] as KeyframeProp[]).map((k) => (
                <button key={k} className="btn small" onClick={() => exec({ type: 'set_keyframe', clip_id: c.id, prop: k, t_ms: local, easing: 'linear',
                  value: k === 'volume' ? c.audio.volume : +(st[k as 'x'] as number).toFixed(3) })}>◆ {KF_LABEL[k]}</button>
              ))}
            </div>
            {(Object.entries(c.keyframes) as [KeyframeProp, { t_ms: number; value: number; easing: string }[]][]).map(([k, list]) => (
              <div key={k} style={{ fontSize: 12 }}>
                <strong>{KF_LABEL[k]}</strong>{' '}
                {list.map((kf) => (
                  <span key={kf.t_ms} className="badge" style={{ marginRight: 4 }}>
                    {(kf.t_ms / 1000).toFixed(2)}s={+kf.value.toFixed(2)}
                    <select aria-label="Yumuşatma" value={kf.easing} style={{ fontSize: 11, background: 'transparent', border: 0 }}
                            onChange={(e) => exec({ type: 'set_keyframe', clip_id: c.id, prop: k, t_ms: kf.t_ms, value: kf.value, easing: e.target.value })}>
                      <option value="linear">doğrusal</option><option value="ease_in">yavaş başla</option><option value="ease_out">yavaş bitir</option>
                      <option value="ease_in_out">yumuşak</option><option value="hold">sabit</option>
                    </select>
                    <button className="btn small ghost" aria-label="Anahtar kareyi sil" onClick={() => exec({ type: 'remove_keyframe', clip_id: c.id, prop: k, t_ms: kf.t_ms })}>×</button>
                  </span>
                ))}
              </div>
            ))}
          </fieldset>
          <div className="row">
            {asset && asset.kind !== 'image' && (
              <label className="field">Hız
                <select className="input" value={c.speed} onChange={(e) => exec({ type: 'set_clip', clip_id: c.id, speed: Number(e.target.value) })}>
                  {[0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4].map((s) => <option key={s} value={s}>{s}×</option>)}
                </select>
              </label>
            )}
            <label className="field">Geçiş
              <select className="input" value={c.transition_in.type}
                      onChange={(e) => exec({ type: 'set_transition', clip_id: c.id, transition: e.target.value, ms: e.target.value === 'none' ? 0 : Math.min(500, c.duration_ms) })}>
                <option value="none">Yok</option><option value="fade">Kararma</option><option value="dissolve">Çözülme</option>
              </select>
            </label>
          </div>
          {asset?.kind === 'video' && <label className="row"><input type="checkbox" checked={c.reverse} onChange={(e) => exec({ type: 'set_clip', clip_id: c.id, reverse: e.target.checked })} /> Ters oynat</label>}
          {hasAudio && (
            <fieldset className="grid" style={{ border: '1px solid var(--line)', borderRadius: 8 }}>
              <legend className="muted" style={{ fontSize: 12 }}>Ses</legend>
              <label className="field">Seviye {Math.round(c.audio.volume * 100)}%
                <input type="range" min={0} max={2} step={0.05} defaultValue={c.audio.volume} key={`${c.id}-${c.audio.volume}`}
                       onPointerUp={(e) => exec({ type: 'set_clip', clip_id: c.id, audio: { volume: Number((e.target as HTMLInputElement).value) } })}
                       onKeyUp={(e) => exec({ type: 'set_clip', clip_id: c.id, audio: { volume: Number((e.target as HTMLInputElement).value) } })} />
              </label>
              <div className="row">
                <Num label="Giriş (ms)" value={c.audio.fade_in_ms} min={0} onCommit={(v) => exec({ type: 'set_clip', clip_id: c.id, audio: { fade_in_ms: Math.round(v) } })} />
                <Num label="Çıkış (ms)" value={c.audio.fade_out_ms} min={0} onCommit={(v) => exec({ type: 'set_clip', clip_id: c.id, audio: { fade_out_ms: Math.round(v) } })} />
              </div>
              <label className="row"><input type="checkbox" checked={c.audio.muted} onChange={(e) => exec({ type: 'set_clip', clip_id: c.id, audio: { muted: e.target.checked } })} /> Sessiz</label>
            </fieldset>
          )}
          <p className="muted" style={{ fontSize: 12, margin: 0 }}>Başlangıç {(c.start_ms / 1000).toFixed(2)} sn · Süre {(c.duration_ms / 1000).toFixed(2)} sn{asset?.kind === 'video' || asset?.kind === 'audio' ? ` · Kaynak ${(c.in_ms / 1000).toFixed(2)} sn` : ''}</p>
        </>
      )}
    </div>
  );
}

function CanvasPanel({ project, exec }: { project: Project; exec(...c: Command[]): boolean }) {
  const cv = project.canvas;
  return (
    <div className="grid" data-testid="inspector">
      <strong>Proje</strong>
      <label className="field">Başlık
        <input className="input" defaultValue={project.title} key={project.title} maxLength={120}
               onBlur={(e) => e.target.value.trim() && e.target.value !== project.title && exec({ type: 'set_title', title: e.target.value.trim() })} />
      </label>
      <label className="field">Boyut
        <select className="input" value={Object.keys(PRESETS).find((k) => PRESETS[k].width === cv.width && PRESETS[k].height === cv.height) ?? ''}
                onChange={(e) => { const p = PRESETS[e.target.value]; if (p) exec({ type: 'set_canvas', width: p.width, height: p.height }); }}>
          <option value="" disabled>{cv.width}×{cv.height}</option>
          {Object.entries(PRESETS).map(([k, p]) => <option key={k} value={k}>{k} ({p.width}×{p.height})</option>)}
        </select>
      </label>
      <label className="field">Arka plan<input type="color" value={cv.background.slice(0, 7)} onChange={(e) => exec({ type: 'set_canvas', background: e.target.value })} /></label>
      <p className="muted" style={{ fontSize: 12 }}>Bir katmanı seçerek özelliklerini düzenle. Kısayollar: Boşluk oynat/durdur · S böl ·
        Del sil (Shift+Del boşluğu kapatarak) · Ctrl+Z geri · Ctrl+Shift+Z yinele · ←/→ kare kaydır.</p>
    </div>
  );
}
