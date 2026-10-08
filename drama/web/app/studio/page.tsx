"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";

const GENRES = ["drama", "romance", "thriller", "revenge", "comedy", "mystery"];

export default function Studio() {
  const { me, ready, refresh } = useAuth();
  const { t, lang } = useI18n();
  const router = useRouter();
  const [projects, setProjects] = useState<any[] | null>(null);
  const [f, setF] = useState({ title: "", genre: "drama", logline: "", language: lang, episode_count: 3,
    episode_duration_s: 60, character_count: 3, audience_rating: "13+", visual_style: "cinematic" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (ready && !me) router.push("/login?next=/studio");
    if (me && me.role !== "viewer") api("/studio/projects").then(setProjects);
  }, [me, ready, router]);

  if (me?.role === "viewer") {
    return (
      <main className="page">
        <h1>{t("studio")}</h1>
        <button className="primary" onClick={async () => { const r = await api("/me/become-creator", { body: {} });
          localStorage.setItem("drama.token", r.access_token); await refresh(); }}>{t("asCreator")}</button>
      </main>
    );
  }

  const create = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(null);
    try {
      const p = await api("/studio/projects", { body: { ...f, title: f.title || null } });
      router.push(`/studio/${p.id}`);
    } catch (e: any) { setErr(e.message); setBusy(false); }
  };

  return (
    <main className="page">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>{t("studio")}</h1>
        {me && <span className="badge">{me.credits} {t("credits")}</span>}
      </div>
      <form className="card" onSubmit={create} style={{ display: "grid", gap: 12 }}>
        <h3>✨ {t("newProject")}</h3>
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))" }}>
          <label>{t("genre")}<select value={f.genre} onChange={(e) => setF({ ...f, genre: e.target.value })}>{GENRES.map((g) => <option key={g}>{g}</option>)}</select></label>
          <label>{t("language")}<select value={f.language} onChange={(e) => setF({ ...f, language: e.target.value as any })}><option value="tr">Türkçe</option><option value="en">English</option></select></label>
          <label>{t("episodesCount")}<input type="number" min={1} max={60} value={f.episode_count} onChange={(e) => setF({ ...f, episode_count: +e.target.value })} /></label>
          <label>{t("duration")}<input type="number" min={20} max={180} value={f.episode_duration_s} onChange={(e) => setF({ ...f, episode_duration_s: +e.target.value })} /></label>
          <label>{t("characters")}<input type="number" min={2} max={4} value={f.character_count} onChange={(e) => setF({ ...f, character_count: +e.target.value })} /></label>
          <label>Rating<select value={f.audience_rating} onChange={(e) => setF({ ...f, audience_rating: e.target.value })}>{["7+", "13+", "16+", "18+"].map((r) => <option key={r}>{r}</option>)}</select></label>
        </div>
        <label>Title<input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="(auto)" /></label>
        <label>{t("logline")}<textarea rows={2} value={f.logline} onChange={(e) => setF({ ...f, logline: e.target.value })} /></label>
        <button className="primary" disabled={busy}>{busy ? "…" : t("create")}</button>
        {err && <p className="err">{err}</p>}
      </form>
      <h2>{t("episodes")}</h2>
      <div className="grid">
        {projects?.map((p) => (
          <Link key={p.id} href={`/studio/${p.id}`} className="card">
            {p.cover_url && <img src={p.cover_url} alt="" style={{ width: "100%", aspectRatio: "9/16", objectFit: "cover", borderRadius: 10 }} />}
            <b>{p.title}</b>
            <div className="row small muted"><span className="badge">{p.genre}</span><span className="badge">{p.status}</span>{p.episodes} ep</div>
          </Link>
        ))}
        {projects?.length === 0 && <p className="muted">{t("empty")}</p>}
      </div>
    </main>
  );
}
