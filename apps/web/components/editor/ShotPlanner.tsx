'use client';

import { useMemo, useState } from 'react';
import type { Command } from '@shared/engine.ts';
import type { Project, SetActor, SetCamera, ShotGraph, ShotPlan, Vec3 } from '@shared/schema.ts';
import { uid } from '@/lib/useEditor';

const EMPTY: ShotGraph = { location: 'Yeni mekân', actors: [], cameras: [], lights: [], shots: [] };
const SIZE = 12;          // metres shown across the plan
const PX = 40;            // px per metre
const SENSOR_W = 36;      // full-frame sensor width (mm) for the horizontal field of view

export const hfov = (lens_mm: number) => (2 * Math.atan(SENSOR_W / (2 * lens_mm)) * 180) / Math.PI;

/** Project an actor into a camera's frame: horizontal position (-1..1) and apparent size, or null when outside. */
export function projectToCamera(cam: SetCamera, p: Vec3): { u: number; size: number } | null {
  const yaw = Math.atan2(cam.target.z - cam.position.z, cam.target.x - cam.position.x);
  const dx = p.x - cam.position.x, dz = p.z - cam.position.z;
  const fwd = dx * Math.cos(yaw) + dz * Math.sin(yaw);
  const side = -dx * Math.sin(yaw) + dz * Math.cos(yaw);
  if (fwd <= 0.2) return null;
  const half = Math.tan((hfov(cam.lens_mm) * Math.PI) / 360);
  const u = side / (fwd * half);
  if (Math.abs(u) > 1.2) return null;
  return { u, size: Math.min(1.5, 1.8 / (fwd * half)) };
}

/** 2D top-down set planner (V8 §5 fallback for 3D): actors, cameras with lens-based field of view, lights and a
 * shot list, stored in the canonical project via `set_shot_graph`. The camera preview is a geometric projection;
 * a video provider is not guaranteed to reproduce the exact camera placement. */
export default function ShotPlanner({ project, exec }: { project: Project; exec(...c: Command[]): boolean }) {
  const g = project.shot_graph ?? EMPTY;
  const [sel, setSel] = useState<string | null>(null);
  const [drag, setDrag] = useState<{ id: string; kind: 'actor' | 'camera' | 'target' | 'light' } | null>(null);
  const [draft, setDraft] = useState<ShotGraph | null>(null);
  const cur = draft ?? g;
  const save = (next: ShotGraph) => { setDraft(null); exec({ type: 'set_shot_graph', shot_graph: next }); };
  const toPlan = (v: Vec3) => ({ x: (v.x + SIZE / 2) * PX, y: (v.z + SIZE / 2) * PX });
  const fromEvent = (e: React.PointerEvent<SVGSVGElement>): Vec3 => {
    const r = e.currentTarget.getBoundingClientRect();
    const k = (SIZE * PX) / r.width;
    const round = (n: number) => Math.round(n * 10) / 10;
    return { x: round(((e.clientX - r.left) * k) / PX - SIZE / 2), y: 0, z: round(((e.clientY - r.top) * k) / PX - SIZE / 2) };
  };

  function move(e: React.PointerEvent<SVGSVGElement>) {
    if (!drag) return;
    const p = fromEvent(e);
    const next: ShotGraph = structuredClone(cur);
    if (drag.kind === 'actor') next.actors = next.actors.map((a) => a.id === drag.id ? { ...a, position: { ...p, y: 0 } } : a);
    if (drag.kind === 'camera') next.cameras = next.cameras.map((c) => c.id === drag.id ? { ...c, position: { ...p, y: c.position.y } } : c);
    if (drag.kind === 'target') next.cameras = next.cameras.map((c) => c.id === drag.id ? { ...c, target: { ...p, y: c.target.y } } : c);
    if (drag.kind === 'light') next.lights = next.lights.map((l) => l.id === drag.id ? { ...l, position: { ...p, y: l.position.y } } : l);
    setDraft(next);
  }
  function up() { if (drag && draft) save(draft); setDrag(null); }

  const add = {
    actor: () => save({ ...cur, actors: [...cur.actors, { id: uid('act'), name: `Oyuncu ${cur.actors.length + 1}`, character_id: null, position: { x: 0, y: 0, z: 0 }, facing_deg: 0 } as SetActor] }),
    camera: () => save({ ...cur, cameras: [...cur.cameras, { id: uid('cam'), name: `Kamera ${cur.cameras.length + 1}`, position: { x: 0, y: 1.6, z: 4 }, target: { x: 0, y: 1.6, z: 0 }, lens_mm: 35 }] }),
    light: () => save({ ...cur, lights: [...cur.lights, { id: uid('lgt'), kind: 'key', position: { x: -3, y: 3, z: 2 }, intensity: 1, color: '#ffffff' }] }),
    shot: () => cur.cameras.length && save({ ...cur, shots: [...cur.shots, { id: uid('shot'), camera_id: cur.cameras[0].id, duration_ms: 4000, description: '', blocking: [] } as ShotPlan] }),
  };
  const selCam = cur.cameras.find((c) => c.id === sel) ?? cur.cameras[0];
  const preview = useMemo(() => selCam ? cur.actors.map((a) => ({ a, p: projectToCamera(selCam, a.position) })) : [], [selCam, cur.actors]);

  return (
    <div className="grid" style={{ padding: 12, gridTemplateColumns: 'minmax(0, 1fr) minmax(260px, 340px)', alignItems: 'start' }} data-testid="planner">
      <div className="grid">
        <div className="row">
          <label className="field" style={{ flex: 1 }}>Mekân
            <input className="input" defaultValue={cur.location} key={cur.location} maxLength={120}
                   onBlur={(e) => e.target.value.trim() && e.target.value !== cur.location && save({ ...cur, location: e.target.value.trim() })} />
          </label>
          <button className="btn small" onClick={add.actor}>+ Oyuncu</button>
          <button className="btn small" onClick={add.camera}>+ Kamera</button>
          <button className="btn small" onClick={add.light}>+ Işık</button>
        </div>
        <svg viewBox={`0 0 ${SIZE * PX} ${SIZE * PX}`} style={{ width: '100%', maxWidth: 560, background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8, touchAction: 'none' }}
             onPointerMove={move} onPointerUp={up} onPointerLeave={up} role="application" aria-label="Üstten sahne planı">
          {Array.from({ length: SIZE + 1 }, (_, i) => (
            <g key={i} stroke="var(--line)" strokeWidth={1}>
              <line x1={i * PX} y1={0} x2={i * PX} y2={SIZE * PX} /><line x1={0} y1={i * PX} x2={SIZE * PX} y2={i * PX} />
            </g>
          ))}
          {cur.cameras.map((c) => {
            const p = toPlan(c.position), tg = toPlan(c.target);
            const yaw = Math.atan2(tg.y - p.y, tg.x - p.x), h = (hfov(c.lens_mm) * Math.PI) / 360, L = 8 * PX;
            const a1 = { x: p.x + L * Math.cos(yaw - h), y: p.y + L * Math.sin(yaw - h) }, a2 = { x: p.x + L * Math.cos(yaw + h), y: p.y + L * Math.sin(yaw + h) };
            return (
              <g key={c.id} onClick={() => setSel(c.id)}>
                <polygon points={`${p.x},${p.y} ${a1.x},${a1.y} ${a2.x},${a2.y}`} fill={c.id === selCam?.id ? 'rgba(242,179,61,.18)' : 'rgba(122,167,255,.10)'} />
                <line x1={p.x} y1={p.y} x2={tg.x} y2={tg.y} stroke="var(--accent-2)" strokeDasharray="4 4" />
                <rect x={p.x - 9} y={p.y - 7} width={18} height={14} rx={3} fill="var(--accent-2)" style={{ cursor: 'move' }}
                      onPointerDown={() => setDrag({ id: c.id, kind: 'camera' })}><title>{c.name}</title></rect>
                <circle cx={tg.x} cy={tg.y} r={5} fill="none" stroke="var(--accent-2)" style={{ cursor: 'crosshair' }} onPointerDown={() => setDrag({ id: c.id, kind: 'target' })} />
                <text x={p.x + 12} y={p.y - 10} fontSize={11} fill="var(--text)">{c.name} {c.lens_mm}mm</text>
              </g>
            );
          })}
          {cur.lights.map((l) => { const p = toPlan(l.position); return (
            <g key={l.id} onPointerDown={() => setDrag({ id: l.id, kind: 'light' })} style={{ cursor: 'move' }}>
              <circle cx={p.x} cy={p.y} r={8} fill={l.color} stroke="#000" /><text x={p.x + 10} y={p.y + 4} fontSize={10} fill="var(--muted)">{l.kind}</text>
            </g>); })}
          {cur.actors.map((a) => { const p = toPlan(a.position); return (
            <g key={a.id} onPointerDown={() => setDrag({ id: a.id, kind: 'actor' })} style={{ cursor: 'move' }}>
              <circle cx={p.x} cy={p.y} r={11} fill="var(--accent)" /><text x={p.x} y={p.y + 26} fontSize={11} textAnchor="middle" fill="var(--text)">{a.name}</text>
            </g>); })}
        </svg>
        <p className="muted" style={{ fontSize: 12, margin: 0 }}>1 kare = 1 m. Kamerayı (sarı kare) ve hedefini (halka) sürükle. Bu bir 2D üstten plan ve geometrik
          kamera önizlemesidir; 3D sahne görünümü ve video sağlayıcısının kamera konumunu birebir koruması garanti değildir.</p>
      </div>
      <div className="grid">
        {selCam && (
          <div className="card grid">
            <div className="row"><strong style={{ flex: 1 }}>{selCam.name} görünümü</strong>
              <label className="field">Lens
                <select className="input" value={selCam.lens_mm} onChange={(e) => save({ ...cur, cameras: cur.cameras.map((c) => c.id === selCam.id ? { ...c, lens_mm: Number(e.target.value) } : c) })}>
                  {[14, 18, 24, 35, 50, 85, 135, 200].map((l) => <option key={l} value={l}>{l} mm</option>)}
                </select>
              </label>
            </div>
            <div aria-label="Kamera önizlemesi" role="img" style={{ position: 'relative', aspectRatio: '16 / 9', background: '#0a0b0e', borderRadius: 6, overflow: 'hidden' }}>
              {preview.map(({ a, p }) => p && (
                <div key={a.id} style={{ position: 'absolute', bottom: '12%', left: `${50 + p.u * 50}%`, transform: 'translateX(-50%)',
                                         width: `${p.size * 18}%`, height: `${p.size * 70}%`, background: 'var(--accent)', borderRadius: '40% 40% 8px 8px', opacity: .85 }}>
                  <span style={{ position: 'absolute', top: -18, left: '50%', transform: 'translateX(-50%)', fontSize: 11, whiteSpace: 'nowrap' }}>{a.name}</span>
                </div>
              ))}
            </div>
            <span className="muted" style={{ fontSize: 12 }}>Yatay görüş açısı ≈ {hfov(selCam.lens_mm).toFixed(0)}°</span>
          </div>
        )}
        <div className="card grid">
          <div className="row"><strong style={{ flex: 1 }}>Çekim listesi</strong><button className="btn small" disabled={!cur.cameras.length} onClick={add.shot}>+ Çekim</button></div>
          {cur.shots.map((s, i) => (
            <div key={s.id} className="grid" style={{ borderTop: '1px solid var(--line)', paddingTop: 8 }}>
              <div className="row">
                <strong>#{i + 1}</strong>
                <select className="input" style={{ flex: 1 }} aria-label="Kamera" value={s.camera_id} onChange={(e) => save({ ...cur, shots: cur.shots.map((x) => x.id === s.id ? { ...x, camera_id: e.target.value } : x) })}>
                  {cur.cameras.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
                <input className="input" style={{ width: 80 }} type="number" min={1} max={60} aria-label="Süre (sn)" defaultValue={s.duration_ms / 1000}
                       onBlur={(e) => { const v = Math.round(Number(e.target.value) * 1000); if (v >= 1000 && v <= 60000 && v !== s.duration_ms) save({ ...cur, shots: cur.shots.map((x) => x.id === s.id ? { ...x, duration_ms: v } : x) }); }} />
                <button className="btn small ghost" aria-label="Çekimi sil" onClick={() => save({ ...cur, shots: cur.shots.filter((x) => x.id !== s.id) })}>×</button>
              </div>
              <input className="input" placeholder="Açıklama (ör. yakın plan, karakter kapıya yürür)" defaultValue={s.description} maxLength={300}
                     onBlur={(e) => e.target.value !== s.description && save({ ...cur, shots: cur.shots.map((x) => x.id === s.id ? { ...x, description: e.target.value } : x) })} />
            </div>
          ))}
          {!cur.shots.length && <span className="muted" style={{ fontSize: 13 }}>Önce bir kamera ekle, sonra çekim planla.</span>}
        </div>
      </div>
    </div>
  );
}
