"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Player } from "@/components/Player";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";

const EMOTIONS = ["neutral", "happy", "sad", "angry", "fear", "surprise", "tender"];
const HAIR = ["short", "long", "bun", "curly", "bald", "bob"];

function CharacterCard({ c, onChange }: { c: any; onChange: () => void }) {
  const { t } = useI18n();
  const [look, setLook] = useState(c.look);
  const [voice, setVoice] = useState(c.voice);
  const [err, setErr] = useState<string | null>(null);
  const [sheet, setSheet] = useState<string | null>(c.reference_sheet_url);
  const [photo, setPhoto] = useState<string | null>(c.reference_photo_url);
  const [busy, setBusy] = useState(false);
  const makePhoto = async () => {
    setBusy(true); setErr(null);
    try { setPhoto((await api(`/studio/characters/${c.id}/reference-photo`, { body: {} })).url); } catch (e: any) { setErr(`${e.code}: ${e.message}`); }
    setBusy(false);
  };
  const save = async () => {
    setErr(null);
    try { await api(`/studio/characters/${c.id}`, { method: "PATCH", body: { look, voice } }); onChange(); } catch (e: any) { setErr(e.message); }
  };
  const color = (k: string) => (
    <label key={k}>{k}<input className="swatch" type="color" disabled={c.locked} value={look[k]} onChange={(e) => setLook({ ...look, [k]: e.target.value })} /></label>
  );
  return (
    <div className="card" style={{ display: "grid", gap: 8 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>{c.name}</b>
        <span className="badge">{c.role} · v{c.version}</span>
      </div>
      <p className="muted small" style={{ margin: 0 }}>{c.personality}</p>
      {c.blocked_reason && <span className="badge bad">{c.blocked_reason}</span>}
      <div className="grid" style={{ gridTemplateColumns: "repeat(3, 1fr)", gap: 6 }}>
        {["skin", "hair", "eyes", "outfit", "accent"].map(color)}
        <label>hair<select disabled={c.locked} value={look.hair_style} onChange={(e) => setLook({ ...look, hair_style: e.target.value })}>{HAIR.map((h) => <option key={h}>{h}</option>)}</select></label>
      </div>
      <div className="row small">
        <label style={{ display: "flex", gap: 4 }}><input type="checkbox" style={{ width: "auto" }} disabled={c.locked} checked={look.glasses} onChange={(e) => setLook({ ...look, glasses: e.target.checked })} />glasses</label>
        <label style={{ display: "flex", gap: 4 }}><input type="checkbox" style={{ width: "auto" }} disabled={c.locked} checked={look.beard} onChange={(e) => setLook({ ...look, beard: e.target.checked })} />beard</label>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "repeat(3, 1fr)", gap: 6 }}>
        <label>voice<select disabled={c.locked} value={voice.gender} onChange={(e) => setVoice({ ...voice, gender: e.target.value })}><option>female</option><option>male</option><option>neutral</option></select></label>
        <label>pitch<input type="number" min={0} max={99} disabled={c.locked} value={voice.pitch} onChange={(e) => setVoice({ ...voice, pitch: +e.target.value })} /></label>
        <label>wpm<input type="number" min={110} max={220} disabled={c.locked} value={voice.rate} onChange={(e) => setVoice({ ...voice, rate: +e.target.value })} /></label>
      </div>
      <div className="row">
        {!c.locked && <button onClick={save}>{t("save")}</button>}
        <button className="ghost" onClick={() => api(`/studio/characters/${c.id}/lock?locked=${!c.locked}`, { body: {} }).then(onChange)}>
          {c.locked ? `🔓 ${t("unlockId")}` : `🔒 ${t("lock")}`}
        </button>
        <button className="ghost" onClick={() => api(`/studio/characters/${c.id}/reference-sheet`, { body: {} }).then((r) => setSheet(r.url))}>{t("referenceSheet")}</button>
      </div>
      <button className="primary" disabled={busy} onClick={makePhoto}>{busy ? "…" : "📷 " + t("realPortrait")}</button>
      {photo && <img src={photo} alt={c.name} style={{ width: "100%", aspectRatio: "9/16", objectFit: "cover", borderRadius: 10 }} />}
      {sheet && <img src={sheet} alt="" style={{ width: "100%", borderRadius: 8 }} />}
      <span className="muted small">DNA {c.dna_hash.slice(0, 10)}</span>
      {err && <p className="err">{err}</p>}
    </div>
  );
}

function ScriptEditor({ ep, characters, onSaved }: { ep: any; characters: any[]; onSaved: () => void }) {
  const { t } = useI18n();
  const [script, setScript] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api(`/studio/episodes/${ep.id}/script`).then((s) => setScript(s)); }, [ep.id, ep.script_version]);
  if (!script) return null;
  const upd = (si: number, li: number, patch: any) => {
    const c = structuredClone(script.content);
    Object.assign(c.scenes[si].lines[li], patch);
    setScript({ ...script, content: c });
  };
  const save = async () => {
    setErr(null);
    try { await api(`/studio/episodes/${ep.id}/script`, { method: "PUT", body: script.content }); onSaved(); } catch (e: any) { setErr(e.message); }
  };
  return (
    <div style={{ display: "grid", gap: 8 }}>
      {script.content.scenes.map((sc: any, si: number) => (
        <div key={si} className="card" style={{ background: "var(--surface-2)" }}>
          <div className="small muted">#{si + 1} · {sc.location} · {sc.mood} · {sc.camera}</div>
          {sc.lines.map((ln: any, li: number) => (
            <div key={li} className="row" style={{ marginTop: 6, flexWrap: "nowrap" }}>
              <select style={{ width: 110 }} value={ln.speaker} onChange={(e) => upd(si, li, { speaker: e.target.value })}>
                {characters.map((c) => <option key={c.key} value={c.key}>{c.name}</option>)}
              </select>
              <select style={{ width: 105 }} value={ln.emotion} onChange={(e) => upd(si, li, { emotion: e.target.value })}>
                {EMOTIONS.map((x) => <option key={x}>{x}</option>)}
              </select>
              <input value={ln.text} onChange={(e) => upd(si, li, { text: e.target.value })} />
            </div>
          ))}
        </div>
      ))}
      <div className="row"><button onClick={save}>{t("save")} ({t("script")} v{script.version} → v{script.version + 1})</button>{err && <span className="err">{err}</span>}</div>
    </div>
  );
}

function EpisodeRow({ ep, characters, reload }: { ep: any; characters: any[]; reload: () => void }) {
  const { t } = useI18n();
  const { refresh } = useAuth();
  const [open, setOpen] = useState(false);
  const [est, setEst] = useState<any>(null);
  const [job, setJob] = useState<any>(null);
  const [preview, setPreview] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!ep.latest_job) return;
    let alive = true;
    const poll = async () => {
      const j = await api(`/studio/jobs/${ep.latest_job.id}`);
      if (!alive) return;
      setJob(j);
      if (["queued", "running"].includes(j.status)) setTimeout(poll, 2000);
      else if (ep.status === "rendering") { reload(); refresh(); }
    };
    poll();
    return () => { alive = false; };
  }, [ep.latest_job?.id, ep.status, reload, refresh]);

  const [route, setRoute] = useState<"realistic" | "preview_2d">("realistic");
  const render = async (quality: string) => {
    setErr(null);
    try { await api("/studio/generations", { body: { episode_id: ep.id, quality, route } }); reload(); } catch (e: any) { setErr(`${e.code}: ${e.message}`); }
  };
  const act = (fn: () => Promise<any>) => fn().then(reload).catch((e) => setErr(e.message));
  const editable = !["published", "in_review", "approved", "rendering"].includes(ep.status);

  return (
    <div className="card" style={{ display: "grid", gap: 10 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>{ep.number}. {ep.title}</b>
        <div className="row">
          <span className="badge">{ep.status}</span>
          {ep.qc_passed !== null && ep.qc_passed !== undefined && <span className={`badge ${ep.qc_passed ? "ok" : "bad"}`}>QC {ep.qc_passed ? "✓" : "✗"}</span>}
          {ep.duration_s && <span className="badge">{Math.round(ep.duration_s)}s</span>}
        </div>
      </div>
      <p className="muted small" style={{ margin: 0 }}>{ep.synopsis}</p>
      <div className="row">
        <button className="ghost" onClick={() => setOpen(!open)}>✎ {t("script")}</button>
        <select style={{ width: "auto" }} value={route} onChange={(e) => setRoute(e.target.value as any)}>
          <option value="realistic">🎬 {t("routeRealistic")}</option>
          <option value="preview_2d">✏️ {t("route2d")}</option>
        </select>
        <button className="ghost" onClick={() => api(`/studio/episodes/${ep.id}/estimate`, { body: { quality: "preview", route } }).then(setEst).catch((e) => setErr(e.message))}>{t("estimate")}</button>
        <button className="primary" disabled={!editable} onClick={() => render("preview")}>{t("render")} · {t("preview")}</button>
        <button disabled={!editable} onClick={() => render("final")}>{t("render")} · {t("final")}</button>
        {ep.status === "rendered" && <button className="ghost" onClick={() => api(`/studio/episodes/${ep.id}/preview`).then(setPreview)}>▶ {t("preview")}</button>}
        {ep.status === "rendered" && <button className="primary" onClick={() => act(() => api(`/studio/episodes/${ep.id}/submit`, { body: {} }))}>{t("submit")}</button>}
        {job && ["queued", "running"].includes(job.status) && <button className="ghost" onClick={() => act(() => api(`/studio/jobs/${job.id}/cancel`, { body: {} }))}>✕</button>}
        {job && ["failed", "cancelled"].includes(job.status) && !job.output?.refunded_credits && <button className="ghost" onClick={() => act(() => api(`/studio/jobs/${job.id}/resume`, { body: {} }))}>↻ resume</button>}
      </div>
      {est && (
        <div className="small muted">
          ≈{est.est_seconds}s · {est.dialogue_words} words{est.shots ? ` · ${est.shots} shots` : ""} · <b>{est.credits} {t("credits")}</b> · balance {est.balance}
          <br />{est.route}
          {est.provider_cost_usd && <><br />Provider cost ≈ ${est.provider_cost_usd.total} (video ${est.provider_cost_usd.video} + images ${est.provider_cost_usd.images})</>}
          {est.shadow_estimates_usd && <><br />Realistic premium ≈ ${est.shadow_estimates_usd.premium_route.total_usd}</>}
          {est.realistic_available && !est.realistic_available.available && <><br /><span className="err">{t("realisticMissing")}: {est.realistic_available.missing}</span></>}
        </div>
      )}
      {job && (
        <div>
          <div className="steps">{job.steps.map((s: any) => <span key={s.name} className={`step ${s.status}`}>{s.name}</span>)}</div>
          <div className="small muted">job {job.status} · {job.spent_credits}/{job.max_spend_credits} {t("credits")} {job.output?.progress?.pct != null && `· ${job.output.progress.pct}%`}</div>
          {job.error_code && <p className="err">{job.error_code}: {job.error_detail}</p>}
        </div>
      )}
      {preview?.hls_url && (
        <div style={{ maxWidth: 320 }}>
          <Player src={preview.hls_url} captions={preview.captions_url} poster={preview.poster_url} />
          <table className="small">
            <tbody>{preview.qc.checks?.map((c: any) => <tr key={c.id}><td>{c.ok ? "✓" : "✗"} {c.id}</td><td className="muted">{JSON.stringify(c.value)}</td></tr>)}</tbody>
          </table>
          {preview.provenance?.models && <p className="small muted">{preview.provenance.models.join(" · ")}</p>}
        </div>
      )}
      {open && <ScriptEditor ep={ep} characters={characters} onSaved={reload} />}
      {err && <p className="err">{err}</p>}
    </div>
  );
}

export default function Project() {
  const { id } = useParams<{ id: string }>();
  const { t } = useI18n();
  const [p, setP] = useState<any>(null);
  const reload = useCallback(() => { api(`/studio/projects/${id}`).then(setP); }, [id]);
  useEffect(reload, [reload]);
  if (!p) return <main className="page muted">…</main>;
  return (
    <main className="page">
      <div className="row small muted"><span className="badge">{p.genre}</span><span className="badge">{p.language}</span><span className="badge">{p.status}</span>
        {p.settings?.writer_is_mock && <span className="badge warn">writer: {p.settings.writer} (template, not LLM)</span>}</div>
      <h1>{p.title}</h1>
      <p className="muted">{p.logline}</p>
      <p className="small muted">{p.bible?.season_arc}</p>
      <div className="mock">{t("routeHelp")}</div>
      <h2>{t("characters")}</h2>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}>
        {p.characters.map((c: any) => <CharacterCard key={`${c.id}-${c.version}-${c.locked}`} c={c} onChange={reload} />)}
      </div>
      <h2>{t("episodes")}</h2>
      <div style={{ display: "grid", gap: 12 }}>
        {p.episodes.map((e: any) => <EpisodeRow key={e.id} ep={e} characters={p.characters} reload={reload} />)}
      </div>
      <button style={{ marginTop: 12 }} onClick={() => api(`/studio/projects/${id}/episodes`, { body: {} }).then(reload)}>+ {t("episodes")}</button>
    </main>
  );
}
