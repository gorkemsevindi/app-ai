"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Paywall } from "@/components/Paywall";
import { Player } from "@/components/Player";
import { anonId, api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";

export default function Watch() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { me } = useAuth();
  const { t } = useI18n();
  const [pb, setPb] = useState<any>(null);
  const [locked, setLocked] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [comments, setComments] = useState<any[]>([]);
  const [text, setText] = useState("");

  const load = useCallback(() => {
    setLocked(false); setErr(null);
    api(`/episodes/${id}/playback`).then(setPb).catch((e) => {
      if (e instanceof ApiError && e.status === 402) setLocked(true); else setErr(e.message);
    });
    api(`/episodes/${id}/comments`).then(setComments).catch(() => {});
  }, [id]);
  useEffect(load, [load]);

  const report = useCallback((position: number, watched: number) => {
    const ended = pb?.duration_s ? position >= pb.duration_s - 1 : false;
    api("/watch-events", { body: { event_id: crypto.randomUUID(), episode_id: id, position_s: position,
      watched_s: watched, completed: ended, anon_id: me ? undefined : anonId() } }).catch(() => {});
  }, [id, me, pb]);

  const post = async () => {
    if (!text.trim()) return;
    await api(`/episodes/${id}/comments`, { body: { body: text } }).catch((e) => setErr(e.message));
    setText(""); load();
  };

  return (
    <main className="page">
      {pb && (
        <>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <Link href={`/s/${pb.series_id}`} className="muted small">← {t("episodes")}</Link>
            <span className="badge warn">{t("aiLabel")}</span>
          </div>
          <h1>{pb.index}. {pb.title}</h1>
          <div className="player-wrap">
            <Player src={pb.hls_url || pb.mp4_url} captions={pb.captions_url} poster={pb.poster_url} autoPlay
              onProgress={report} onEnded={() => pb.next_episode_id && router.push(`/watch/${pb.next_episode_id}`)} />
          </div>
          <div className="row" style={{ justifyContent: "center", marginTop: 12 }}>
            {pb.next_episode_id && <Link className="chip on" href={`/watch/${pb.next_episode_id}`}>{t("nextEp")} →</Link>}
            <button className="ghost small" onClick={() => api("/reports", { body: { target_type: "episode", target_id: id, reason: "other" } })}>⚑ {t("report")}</button>
          </div>
          <h2>{t("comments")}</h2>
          {me && (
            <div className="row">
              <input value={text} onChange={(e) => setText(e.target.value)} maxLength={1000} style={{ flex: 1 }} />
              <button onClick={post}>{t("send")}</button>
            </div>
          )}
          {comments.map((c) => <p key={c.id}><b>{c.user}</b> <span className="muted">{c.body}</span></p>)}
        </>
      )}
      {locked && <Paywall episodeId={id} onUnlocked={load} onClose={() => router.back()} />}
      {err && <p className="err">{err}</p>}
    </main>
  );
}
