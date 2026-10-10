"use client";

import { useEffect, useState } from "react";
import { api, ApiError, money } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import Link from "next/link";

type Product = { product_id: string; type: string; price_minor: number; currency: string; episodes: string[] };

/** Unlock sheet. Web uses the sandbox store today; production web would use Stripe, iOS/Android native IAP. */
export function Paywall({ episodeId, onUnlocked, onClose }: { episodeId: string; onUnlocked: () => void; onClose: () => void }) {
  const { me } = useAuth();
  const { t } = useI18n();
  const [products, setProducts] = useState<Product[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api(`/episodes/${episodeId}/offer`).then((o) => { if (o.unlocked) onUnlocked(); else setProducts(o.products); })
      .catch((e) => setErr(e.message));
  }, [episodeId, onUnlocked]);

  const buy = async (p: Product) => {
    setBusy(p.product_id); setErr(null);
    try {
      const chk = await api("/purchases/sandbox/checkout", { body: { product_id: p.product_id } });
      await api("/purchases/verify", { body: { store: "sandbox", ...chk } });
      onUnlocked();
    } catch (e) { setErr(e instanceof ApiError ? e.message : String(e)); }
    setBusy(null);
  };
  const label = (type: string) => type === "episode" ? t("buyEpisode") : type === "bundle5" ? t("buyBundle") : t("buySeason");

  return (
    <div className="overlay" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <h3>🔒 {t("unlock")}</h3>
        {!me ? (
          <p><Link href={`/login?next=/watch/${episodeId}`} className="chip on">{t("login")}</Link></p>
        ) : (
          products.map((p) => (
            <div className="offer" key={p.product_id}>
              <div>
                <div>{label(p.type)}</div>
                <div className="muted small">{p.episodes.length} {t("episodes").toLowerCase()}</div>
              </div>
              <button className="primary" disabled={!!busy} onClick={() => buy(p)}>
                {busy === p.product_id ? "…" : money(p.price_minor, p.currency)}
              </button>
            </div>
          ))
        )}
        <p className="muted small">{t("sandboxNote")}</p>
        {err && <p className="err">{err}</p>}
      </div>
    </div>
  );
}
