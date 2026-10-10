"use client";

import Hls from "hls.js";
import { useEffect, useRef } from "react";

type Props = {
  src: string;
  captions?: string | null;
  poster?: string | null;
  autoPlay?: boolean;
  muted?: boolean;
  startAt?: number;
  onProgress?: (position: number, watched: number) => void;
  onEnded?: () => void;
  className?: string;
};

/** Adaptive HLS player (hls.js, native HLS on Safari) with VTT captions and watch-time reporting. */
export function Player({ src, captions, poster, autoPlay, muted, startAt, onProgress, onEnded, className }: Props) {
  const ref = useRef<HTMLVideoElement>(null);
  const watched = useRef(0);
  const last = useRef<number | null>(null);

  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    watched.current = 0;
    let hls: Hls | null = null;
    if (src.includes(".m3u8") && !v.canPlayType("application/vnd.apple.mpegurl") && Hls.isSupported()) {
      hls = new Hls({ capLevelToPlayerSize: true });
      hls.loadSource(src);
      hls.attachMedia(v);
    } else {
      v.src = src;
    }
    const onLoaded = () => { if (startAt) v.currentTime = startAt; if (autoPlay) v.play().catch(() => {}); };
    v.addEventListener("loadedmetadata", onLoaded);
    return () => { v.removeEventListener("loadedmetadata", onLoaded); hls?.destroy(); };
  }, [src, autoPlay, startAt]);

  useEffect(() => {
    const v = ref.current;
    if (!v || !onProgress) return;
    const tick = () => {
      if (!v.paused) {
        const now = v.currentTime;
        if (last.current !== null && now > last.current && now - last.current < 2) watched.current += now - last.current;
        last.current = now;
      }
    };
    const iv = setInterval(tick, 500);
    const report = setInterval(() => onProgress(v.currentTime, watched.current), 10000);
    const onPause = () => onProgress(v.currentTime, watched.current);
    v.addEventListener("pause", onPause);
    return () => { clearInterval(iv); clearInterval(report); v.removeEventListener("pause", onPause); };
  }, [onProgress]);

  return (
    <video
      ref={ref}
      className={className}
      poster={poster || undefined}
      playsInline
      muted={muted}
      controls
      crossOrigin="anonymous"
      onEnded={() => { onProgress?.(ref.current?.currentTime || 0, watched.current); onEnded?.(); }}
    >
      {captions && <track kind="captions" src={captions} srcLang="tr" label="Altyazı" />}
    </video>
  );
}
