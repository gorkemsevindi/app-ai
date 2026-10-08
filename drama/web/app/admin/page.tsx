"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function Admin() {
  const [queue, setQueue] = useState<any[]>([]);
  const [cases, setCases] = useState<any[]>([]);
  const [providers, setProviders] = useState<any[]>([]);
  const [tb, setTb] = useState<any>(null);
  const [payouts, setPayouts] = useState<any[]>([]);
  const load = () => {
    api("/admin/review-queue").then(setQueue);
    api("/admin/moderation").then(setCases);
    api("/admin/providers").then(setProviders);
    api("/admin/ledger/trial-balance").then(setTb);
    api("/admin/payouts").then(setPayouts);
  };
  useEffect(load, []);
  return (
    <main className="page">
      <h1>Admin</h1>
      <h2>Publish review ({queue.length})</h2>
      {queue.map((q) => (
        <div key={q.episode_id} className="card" style={{ marginBottom: 8 }}>
          <b>{q.title}</b> <span className={`badge ${q.qc?.passed ? "ok" : "bad"}`}>QC</span>
          <div className="small muted">{(q.provenance?.models || []).join(" · ")}</div>
          {q.provenance?.mock_components?.length > 0 && <div className="small mock">mock: {q.provenance.mock_components.join(", ")}</div>}
          <div className="row" style={{ marginTop: 8 }}>
            <button className="primary" onClick={() => api(`/admin/episodes/${q.episode_id}/decision`, { body: { approve: true } }).then(load)}>Approve & publish</button>
            <button onClick={() => api(`/admin/episodes/${q.episode_id}/decision`, { body: { approve: false } }).then(load)}>Reject</button>
          </div>
        </div>
      ))}
      <h2>Moderation ({cases.length})</h2>
      <table><tbody>
        {cases.map((c) => (
          <tr key={c.id}>
            <td><span className="badge">P{c.priority}</span></td><td>{c.target_type}:{c.target_id.slice(0, 8)}<div className="muted small">{c.source} {c.notes} {c.reports.map((r: any) => r.reason).join(",")}</div></td>
            <td className="row">
              {c.target_type === "rights_grant"
                ? <><button onClick={() => api(`/admin/rights/grants/${c.target_id}/review?approve=true`, { body: {} }).then(() => api(`/admin/moderation/${c.id}`, { body: { action: "dismiss", notes: "grant approved" } })).then(load)}>Approve grant</button>
                    <button onClick={() => api(`/admin/rights/grants/${c.target_id}/review?approve=false`, { body: {} }).then(() => api(`/admin/moderation/${c.id}`, { body: { action: "dismiss", notes: "grant rejected" } })).then(load)}>Reject</button></>
                : <><button onClick={() => api(`/admin/moderation/${c.id}`, { body: { action: "takedown" } }).then(load)}>Takedown</button>
                    <button onClick={() => api(`/admin/moderation/${c.id}`, { body: { action: "dismiss" } }).then(load)}>Dismiss</button></>}
            </td>
          </tr>
        ))}
      </tbody></table>
      <h2>Payouts</h2>
      <table><tbody>{payouts.map((p) => <tr key={p.id}><td>{p.creator_id.slice(0, 8)}</td><td>{p.amount_minor / 100} {p.currency}</td><td>{p.status}</td>
        <td>{p.status === "requested" && <button onClick={() => api(`/admin/payouts/${p.id}/settle`, { body: {} }).then(load)}>Settle (sandbox)</button>}</td></tr>)}</tbody></table>
      <h2>Ledger</h2>
      {tb && <p>Trial balance: <span className={`badge ${tb.balanced ? "ok" : "bad"}`}>{tb.balanced ? "balanced" : "UNBALANCED"}</span> <span className="muted small">{JSON.stringify(tb.per_currency_sum)}</span>
        <button style={{ marginLeft: 8 }} onClick={() => api("/admin/ledger/release-holds", { body: {} }).then(load)}>Release matured holds</button></p>}
      <h2>Providers</h2>
      <table><tbody>{providers.map((p, i) => <tr key={i}><td>{p.capability}</td><td>{p.selected || p.candidate}</td><td className="muted small">{p.requires || (p.local ? "local" : "")}</td>
        <td>{p.credential_present === undefined ? "" : p.credential_present ? <span className="badge ok">key set</span> : <span className="badge warn">missing</span>}</td></tr>)}</tbody></table>
    </main>
  );
}
