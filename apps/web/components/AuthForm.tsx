'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { authPost } from '@/lib/client';

const MESSAGES: Record<string, string> = {
  invalid_credentials: 'E-posta veya şifre hatalı.',
  email_taken: 'Bu e-posta ile zaten bir hesap var.',
  consent_required: 'Yaş onayı ve kullanım koşulları gerekli.',
  demo_mode: 'Bu dağıtımda sunucu bağlı değil. Stüdyoyu DEMO modunda deneyebilirsin.',
  rate_limited: 'Çok fazla deneme. Biraz sonra tekrar dene.',
  csrf: 'Güvenlik kontrolü başarısız. Sayfayı yenile.',
};

export default function AuthForm({ mode }: { mode: 'login' | 'signup' }) {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [age, setAge] = useState(false);
  const [terms, setTerms] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    const r = await authPost(`/api/auth/${mode}`, mode === 'login' ? { email, password }
      : { email, password, age_confirmed: age, terms_accepted: terms });
    setBusy(false);
    if (r.ok) { router.push('/app'); router.refresh(); return; }
    const d = (r.data.detail ?? r.data) as Record<string, unknown>;
    const code = String(d.code ?? '');
    setErr(MESSAGES[code] ?? (Array.isArray(r.data.detail) ? 'Lütfen alanları kontrol et (şifre en az 8 karakter).' : String(d.message ?? 'Hata')));
  }

  return (
    <main className="container" style={{ maxWidth: 420, paddingTop: 64 }}>
      <Link href="/" className="brand">AI Cinema <span>Studio</span></Link>
      <h1>{mode === 'login' ? 'Giriş yap' : 'Hesap oluştur'}</h1>
      <form className="card grid" onSubmit={submit} noValidate>
        <label className="field">E-posta
          <input className="input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="field">Şifre
          <input className="input" type="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                 required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {mode === 'signup' && (
          <>
            <label className="row"><input type="checkbox" checked={age} onChange={(e) => setAge(e.target.checked)} /> 18 yaşından büyüğüm</label>
            <label className="row"><input type="checkbox" checked={terms} onChange={(e) => setTerms(e.target.checked)} /> Kullanım koşullarını ve gizlilik politikasını kabul ediyorum</label>
          </>
        )}
        {err && <p role="alert" className="error" style={{ margin: 0 }}>{err}</p>}
        <button className="btn primary" disabled={busy || !email || !password || (mode === 'signup' && (!age || !terms))}>
          {busy ? 'Bekleyin…' : mode === 'login' ? 'Giriş yap' : 'Hesap oluştur'}
        </button>
      </form>
      <p className="muted">
        {mode === 'login' ? <>Hesabın yok mu? <Link href="/signup">Kayıt ol</Link></> : <>Zaten hesabın var mı? <Link href="/login">Giriş yap</Link></>}
      </p>
    </main>
  );
}
