"use client";

import { useEffect, useState } from "react";
import { api, money } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export default function Creator() {
  const { t } = useI18n();
  const [e, setE] = useState<any>(null);
  const [a, setA] = useState<any[]>([]);
  const [payouts, setPayouts] = useState<any[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const load = () => {
    api("/creator/earnings").then(setE).catch((x) => setErr(x.message));
    api("/creator/analytics").then(setA);
    api("/creator/payouts").then(setPayouts);
  };
  useEffect(load, []);
  if (!e) return <main className="page">{err ? <p className="err">{err}</p> : "…"}</main>;
  const pct = (x: number | null) => (x == null ? "—" : `${Math.round(x * 100)}%`);
  return (
    <main className="page">
      <h1>{t("earnings")}</h1>
      <div className="grid">
        <div className="card"><div className="muted small">{t("pending")}</div><h2 style={{ margin: 0 }}>{money(e.pending_minor, e.currency)}</h2><div className="muted small">hold {e.hold_days}d</div></div>
        <div className="card"><div className="muted small">{t("available")}</div><h2 style={{ margin: 0 }}>{money(e.available_minor, e.currency)}</h2>
          <button className="primary" style={{ marginTop: 8 }} disabled={e.available_minor < e.min_payout_minor}
            onClick={() => api("/creator/payouts", { body: { amount_minor: e.available_minor } }).then(load).catch((x) => setErr(x.message))}>{t("requestPayout")}</button></div>
        <div className="card"><div className="muted small">Credits</div><h2 style={{ margin: 0 }}>{e.credits_balance}</h2><div className="muted small">provider cost ${e.provider_cost_usd}</div></div>
      </div>
      <p className="muted small">{e.definition} Share: {e.share_bps / 100}%.</p>
      {err && <p className="err">{err}</p>}
      <h2>Analytics</h2>
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead><tr><th>Series</th><th>Views</th><th>Qualified</th><th>Watch time</th><th>Completion</th><th>Progression</th><th>Paywall conv.</th><th>Purchases</th><th>Refunds</th><th>Net distributable</th><th>Your share</th></tr></thead>
          <tbody>
            {a.map((s) => (
              <tr key={s.series_id}>
                <td>{s.title}<div className="muted small">{s.status}</div></td><td>{s.views}</td><td>{s.qualified_views}</td><td>{Math.round(s.watch_time_s / 60)}m</td>
                <td>{pct(s.completion_rate)}</td><td>{pct(s.episode_progression_rate)}</td><td>{pct(s.paywall_conversion)}</td>
                <td>{s.purchases}</td><td>{s.refunds}</td><td>{money(s.net_distributable_minor)}</td><td>{money(s.creator_share_minor)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2>Payouts</h2>
      <table><tbody>{payouts.map((p) => <tr key={p.id}><td>{money(p.amount_minor, p.currency)}</td><td>{p.status}</td><td className="muted">{p.requested_at}</td></tr>)}</tbody></table>
    </main>
  );
}
