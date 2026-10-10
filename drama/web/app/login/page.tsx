"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";

function LoginForm() {
  const { t, lang } = useI18n();
  const { signIn } = useAuth();
  const router = useRouter();
  const next = useSearchParams().get("next") || "/";
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [f, setF] = useState({ email: "", password: "", display_name: "", as_creator: false });
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setErr(null);
    try {
      const r = mode === "login"
        ? await api("/auth/login", { body: { email: f.email, password: f.password } })
        : await api("/auth/signup", { body: { ...f, locale: lang } });
      await signIn(r.access_token);
      router.push(f.as_creator ? "/studio" : next);
    } catch (e: any) { setErr(e.message); }
  };

  return (
    <main className="page" style={{ maxWidth: 420 }}>
      <div className="row">
        <button className={`chip ${mode === "login" ? "on" : ""}`} onClick={() => setMode("login")}>{t("login")}</button>
        <button className={`chip ${mode === "signup" ? "on" : ""}`} onClick={() => setMode("signup")}>{t("signup")}</button>
      </div>
      <form onSubmit={submit} style={{ display: "grid", gap: 12, marginTop: 16 }}>
        {mode === "signup" && <label>{t("name")}<input required value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} /></label>}
        <label>{t("email")}<input type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></label>
        <label>{t("password")}<input type="password" required minLength={8} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></label>
        {mode === "signup" && (
          <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input type="checkbox" style={{ width: "auto" }} checked={f.as_creator} onChange={(e) => setF({ ...f, as_creator: e.target.checked })} />
            {t("asCreator")}
          </label>
        )}
        <button className="primary">{mode === "login" ? t("login") : t("signup")}</button>
        {err && <p className="err">{err}</p>}
      </form>
    </main>
  );
}

export default function Login() {
  return <Suspense><LoginForm /></Suspense>;
}
