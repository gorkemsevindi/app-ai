import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from 'react';

import { api, type Me } from './api';
import { claimPendingAttribution } from './referral';
import { tokenStore } from './tokenStore';

type AuthState = {
  me: Me | null;
  loading: boolean;
  refreshMe: () => Promise<void>;
  signUp: (b: { email: string; password: string; country?: string; ageConfirmed: boolean; termsAccepted: boolean }) => Promise<void>;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
};

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshMe = useCallback(async () => {
    try {
      setMe(await api<Me>('/me'));
      claimPendingAttribution().catch(() => {});
    } catch {
      setMe(null);
    }
  }, []);

  useEffect(() => {
    (async () => {
      if (await tokenStore.getAccess()) await refreshMe();
      setLoading(false);
    })();
  }, [refreshMe]);

  const value = useMemo<AuthState>(() => ({
    me,
    loading,
    refreshMe,
    async signUp(b) {
      const t = await api<{ access_token: string; refresh_token: string }>('/auth/signup', {
        auth: false,
        body: { email: b.email, password: b.password, country: b.country, age_confirmed: b.ageConfirmed,
                terms_accepted: b.termsAccepted },
      });
      await tokenStore.set(t.access_token, t.refresh_token);
      await refreshMe();
    },
    async signIn(email, password) {
      const t = await api<{ access_token: string; refresh_token: string }>('/auth/login', {
        auth: false, body: { email, password },
      });
      await tokenStore.set(t.access_token, t.refresh_token);
      await refreshMe();
    },
    async signOut() {
      const refresh = await tokenStore.getRefresh();
      if (refresh) await api('/auth/logout', { auth: false, body: { refresh_token: refresh } }).catch(() => {});
      await tokenStore.clear();
      setMe(null);
    },
  }), [me, loading, refreshMe]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error('useAuth outside AuthProvider');
  return v;
}
