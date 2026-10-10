"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, getToken, setToken } from "./api";

export type Me = {
  id: string; email: string; display_name: string; role: "viewer" | "creator" | "admin"; credits: number;
  creator: { handle: string; kyc_status: string } | null;
};

const Ctx = createContext<{ me: Me | null; ready: boolean; refresh: () => Promise<void>; signIn: (token: string) => Promise<void>; signOut: () => void }>(
  { me: null, ready: false, refresh: async () => {}, signIn: async () => {}, signOut: () => {} },
);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);
  const refresh = useCallback(async () => {
    if (!getToken()) { setMe(null); setReady(true); return; }
    try { setMe(await api<Me>("/me")); } catch { setToken(null); setMe(null); }
    setReady(true);
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  const signIn = async (token: string) => { setToken(token); await refresh(); };
  const signOut = () => { setToken(null); setMe(null); };
  return <Ctx.Provider value={{ me, ready, refresh, signIn, signOut }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
