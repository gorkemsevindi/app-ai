"use client";

import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";

export function Nav() {
  const { me, signOut } = useAuth();
  const { t, lang, setLang } = useI18n();
  return (
    <nav className="nav">
      <Link href="/" className="brand">SAH<span>NE</span></Link>
      {me && me.role !== "viewer" && <Link href="/studio">{t("studio")}</Link>}
      {me && me.role !== "viewer" && <Link href="/creator" className="hide-sm">{t("earnings")}</Link>}
      {me?.role === "admin" && <Link href="/admin" className="hide-sm">{t("admin")}</Link>}
      <button className="ghost" style={{ padding: "4px 8px" }} onClick={() => setLang(lang === "tr" ? "en" : "tr")}>
        {lang === "tr" ? "EN" : "TR"}
      </button>
      {me ? (
        <button className="ghost" style={{ padding: "4px 10px" }} onClick={signOut}>{t("logout")}</button>
      ) : (
        <Link href="/login" className="chip">{t("login")}</Link>
      )}
    </nav>
  );
}
