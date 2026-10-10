"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Paywall } from "@/components/Paywall";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";

export default function SeriesView({ slug }: { slug: string }) {
  const { me } = useAuth();
  const { t } = useI18n();
  const [s, setS] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pay, setPay] = useState<string | null>(null);
  const load = useCallback(() => { api(`/series/${slug}`).then(setS).catch((e) => setErr(e.message)); }, [slug]);
  useEffect(load, [load, me]);

  if (err) return <main className="page"><p className="err">{err}</p></main>;
  if (!s) return <main className="page muted">…</main>;
  return (
    <main className="page">
      <div className="row" style={{ alignItems: "flex-start", gap: 20 }}>
        {s.cover_url && <img src={s.cover_url} alt="" style={{ width: 160, aspectRatio: "9/16", objectFit: "cover", borderRadius: 12 }} />}
        <div style={{ flex: 1, minWidth: 220 }}>
          <div className="row small muted"><span className="badge">{s.genre}</span><span className="badge">{s.age_rating}</span><span className="badge warn">{t("aiLabel")}</span></div>
          <h1>{s.title}</h1>
          <p className="muted">{s.logline}</p>
          <p className="small muted">@{s.creator} · {s.free_episodes} {t("free").toLowerCase()}</p>
          <div className="row">
            {s.episodes[0] && <Link className="chip on" href={`/watch/${s.resume?.episode_id || s.episodes[0].id}`}>▶ {t("watch")}</Link>}
            {me && (
              <button className="chip" onClick={() => api(`/series/${s.id}/follow?on=${!s.following}`, { body: {} }).then(load)}>
                {s.following ? t("unfollow") : t("follow")}
              </button>
            )}
          </div>
        </div>
      </div>
      <h2>{t("episodes")}</h2>
      <div className="grid">
        {s.episodes.map((e: any) => (
          <div key={e.id} className="card" style={{ cursor: "pointer" }} onClick={() => e.unlocked ? (location.href = `/watch/${e.id}`) : setPay(e.id)}>
            {e.thumbnail_url && <img src={e.thumbnail_url} alt="" style={{ width: "100%", aspectRatio: "9/16", objectFit: "cover", borderRadius: 10 }} />}
            <div className="row" style={{ justifyContent: "space-between", marginTop: 8 }}>
              <b>{e.index}. {e.title}</b>
              <span className={`badge ${e.unlocked ? "ok" : ""}`}>{e.unlocked ? (e.access === "free" ? t("free") : "✓") : "🔒"}</span>
            </div>
            <p className="muted small">{e.synopsis}</p>
          </div>
        ))}
      </div>
      {pay && <Paywall episodeId={pay} onUnlocked={() => { setPay(null); load(); }} onClose={() => setPay(null)} />}
    </main>
  );
}
