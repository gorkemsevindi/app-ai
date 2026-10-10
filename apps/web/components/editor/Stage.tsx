'use client';

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { Command } from '@shared/engine.ts';
import { valueAt } from '@shared/manifest.ts';
import { fitInside, layerState } from '@shared/preview.ts';
import type { Clip, Project } from '@shared/schema.ts';

interface Props {
  project: Project; t: number; playing: boolean; selected: string | null; urls: Record<string, string>;
  onSelect(id: string | null): void; exec(...c: Command[]): boolean;
}

/** WYSIWYG preview: same placement rules as the server renderer (shared/preview.ts). */
export default function Stage({ project, t, playing, selected, urls, onSelect, exec }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState({ w: 640, h: 360 });
  const [drag, setDrag] = useState<{ id: string; sx: number; sy: number; dx: number; dy: number } | null>(null);
  const done = useRef<typeof drag>(null);
  const cv = project.canvas;

  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setBox({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const s = Math.max(0.05, Math.min((box.w - 24) / cv.width, (box.h - 24) / cv.height));

  const uri = (c: Clip) => {
    const a = c.asset_id ? project.assets[c.asset_id] : null;
    return a ? (urls[a.uri] ?? (a.uri.startsWith('blob:') ? a.uri : null)) : null;
  };

  function endDrag() {
    if (!drag || done.current === drag) return;  // pointerup + pointerleave in the same frame
    done.current = drag;
    const c = project.clips[drag.id];
    const dx = Math.round(drag.dx / s), dy = Math.round(drag.dy / s);
    setDrag(null);
    if (!c || (dx === 0 && dy === 0)) return;
    const local = project.type === 'photo' ? 0 : Math.min(Math.max(0, t - c.start_ms), c.duration_ms);
    const st = layerState(project, c, t);
    const cmds: Command[] = [];
    for (const [prop, v] of [['x', st.x + dx], ['y', st.y + dy]] as const) {
      if (c.keyframes[prop]?.length) cmds.push({ type: 'set_keyframe', clip_id: c.id, prop, t_ms: local, value: Math.round(v), easing: 'linear' });
    }
    if (!cmds.length) cmds.push({ type: 'set_clip', clip_id: c.id, transform: { x: Math.round(c.transform.x + dx), y: Math.round(c.transform.y + dy) } });
    exec(...cmds);
  }

  const layers = project.tracks.flatMap((tr, z) => tr.kind === 'audio' || tr.hidden ? []
    : tr.clip_ids.map((id) => ({ c: project.clips[id], z })));

  return (
    <div className="stage-wrap" ref={wrap} onPointerDown={(e) => { if (e.target === e.currentTarget) onSelect(null); }}>
      <div className="stage" role="img" aria-label="Önizleme" data-testid="stage"
           style={{ width: cv.width * s, height: cv.height * s, background: cv.background }}
           onPointerMove={(e) => drag && setDrag({ ...drag, dx: e.clientX - drag.sx, dy: e.clientY - drag.sy })}
           onPointerUp={endDrag} onPointerLeave={endDrag}
           onPointerDown={(e) => { if (e.target === e.currentTarget) onSelect(null); }}>
        {layers.map(({ c, z }) => {
          const st = layerState(project, c, t);
          if (!st.visible) return null;
          const d = drag?.id === c.id ? drag : null;
          const x = st.x * s + (d ? d.dx : 0), y = st.y * s + (d ? d.dy : 0);
          const common = {
            key: c.id, className: `layer${selected === c.id ? ' selected' : ''}`, 'data-clip': c.id,
            onPointerDown: (e: React.PointerEvent) => {
              e.stopPropagation();
              onSelect(c.id);
              if (!c.locked) setDrag({ id: c.id, sx: e.clientX, sy: e.clientY, dx: 0, dy: 0 });
            },
          };
          if (c.text) {
            const tx = c.text;
            const size = Math.max(4, Math.trunc(tx.size * st.scale)) * s;
            const left = tx.align === 'left' ? `${40 * s + x}px` : tx.align === 'right' ? undefined : '50%';
            const right = tx.align === 'right' ? `${40 * s - x}px` : undefined;
            return (
              <div {...common} style={{ zIndex: z, left, right, top: '50%', opacity: st.opacity, fontSize: size, color: tx.color,
                fontWeight: tx.weight === 'bold' ? 700 : 400, whiteSpace: 'pre', lineHeight: 1.1, background: tx.background,
                padding: tx.background ? 12 * s : 0, cursor: c.locked ? 'default' : 'move',
                transform: `translate(${tx.align === 'center' ? `calc(-50% + ${x}px)` : '0'}, calc(-50% + ${y}px))` }}>
                {tx.content}
              </div>
            );
          }
          let w: number, h: number;
          if (c.shape) { w = c.shape.width; h = c.shape.height; }
          else {
            const a = c.asset_id ? project.assets[c.asset_id] : undefined;
            ({ width: w, height: h } = fitInside(cv, a?.width, a?.height));
          }
          const style: React.CSSProperties = {
            zIndex: z, width: w * s, height: h * s, opacity: st.opacity, cursor: c.locked ? 'default' : 'move',
            transform: `translate(-50%, -50%) translate(${x}px, ${y}px) rotate(${st.rotation}deg) scale(${st.scale})`,
          };
          if (c.shape) {
            return <div {...common} style={{ ...style, background: c.shape.fill,
              borderRadius: c.shape.type === 'ellipse' ? '50%' : (c.shape.radius ?? 0) * s }} />;
          }
          const src = uri(c);
          const kind = c.asset_id ? project.assets[c.asset_id]?.kind : null;
          if (!src) return <div {...common} style={{ ...style, display: 'grid', placeItems: 'center', background: '#333', color: '#aaa', fontSize: 12 }}>medya</div>;
          if (kind === 'video') return <VideoLayer {...common} style={style} src={src} sourceMs={st.source_ms} playing={playing} speed={c.speed} muted />;
          return <img {...common} src={src} alt={c.name} draggable={false} style={{ ...style, objectFit: 'contain' }} />;
        })}
      </div>
      <AudioBus project={project} t={t} playing={playing} urls={urls} />
    </div>
  );
}

function VideoLayer({ src, sourceMs, playing, speed, style, muted, ...rest }: {
  src: string; sourceMs: number; playing: boolean; speed: number; style: React.CSSProperties; muted: boolean;
} & Record<string, unknown>) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    v.playbackRate = speed;
    const want = sourceMs / 1000;
    if (!playing || Math.abs(v.currentTime - want) > 0.3) v.currentTime = want;
    if (playing && v.paused) v.play().catch(() => {});
    if (!playing && !v.paused) v.pause();
  }, [sourceMs, playing, speed]);
  return <video ref={ref} src={src} muted={muted} playsInline preload="auto" style={{ ...style, objectFit: 'contain' }} {...rest} />;
}

/** Audio preview (audio tracks + the sound of video clips). */
function AudioBus({ project, t, playing, urls }: { project: Project; t: number; playing: boolean; urls: Record<string, string> }) {
  const items = project.tracks.flatMap((tr) => tr.muted ? [] : tr.clip_ids.map((id) => project.clips[id]))
    .filter((c) => {
      const a = c.asset_id ? project.assets[c.asset_id] : null;
      return a && (a.kind === 'audio' || a.kind === 'video') && !c.audio.muted && t >= c.start_ms && t < c.start_ms + c.duration_ms;
    });
  return <>{items.map((c) => {
    const a = project.assets[c.asset_id as string];
    const src = urls[a.uri] ?? (a.uri.startsWith('blob:') ? a.uri : null);
    return src ? <AudioEl key={c.id} src={src} sourceMs={layerState(project, c, t).source_ms} playing={playing}
                          speed={c.speed} volume={valueAt(c, 'volume', t - c.start_ms, c.audio.volume)} /> : null;
  })}</>;
}

function AudioEl({ src, sourceMs, playing, speed, volume }: { src: string; sourceMs: number; playing: boolean; speed: number; volume: number }) {
  const ref = useRef<HTMLAudioElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.playbackRate = speed;
    el.volume = Math.max(0, Math.min(1, volume));
    const want = sourceMs / 1000;
    if (!playing || Math.abs(el.currentTime - want) > 0.3) el.currentTime = want;
    if (playing && el.paused) el.play().catch(() => {});
    if (!playing && !el.paused) el.pause();
  }, [sourceMs, playing, speed, volume]);
  return <audio ref={ref} src={src} preload="auto" />;
}
