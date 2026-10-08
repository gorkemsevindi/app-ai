"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

const dict = {
  tr: {
    forYou: "Sana Özel", trending: "Trend", following: "Takip", new: "Yeni", studio: "Stüdyo", earnings: "Kazanç",
    admin: "Yönetim", login: "Giriş", signup: "Kayıt ol", logout: "Çıkış", watch: "İzle", episodes: "Bölümler",
    free: "Ücretsiz", locked: "Kilitli", unlock: "Kilidi aç", follow: "Takip et", unfollow: "Takibi bırak",
    aiLabel: "Yapay zekâ ile üretildi", nextEp: "Sonraki bölüm", buyEpisode: "Bu bölüm", buyBundle: "5 bölüm paketi",
    buySeason: "Tüm sezon", sandboxNote: "Sandbox satın alma: gerçek ödeme alınmaz.", newProject: "Yeni dizi",
    genre: "Tür", logline: "Ana fikir", language: "Dil", episodesCount: "Bölüm sayısı", duration: "Süre (sn)",
    characters: "Karakterler", create: "Oluştur", estimate: "Maliyet tahmini", render: "Render", preview: "Önizleme",
    final: "Final", submit: "Yayına gönder", lock: "Kimliği kilitle", unlockId: "Kilidi kaldır", save: "Kaydet",
    script: "Senaryo", credits: "kredi", status: "Durum", comments: "Yorumlar", send: "Gönder", report: "Bildir",
    email: "E-posta", password: "Şifre", name: "Ad", asCreator: "İçerik üreticisi olarak katıl",
    pending: "Bekleyen", available: "Çekilebilir", requestPayout: "Ödeme talep et", empty: "Henüz içerik yok.",
    referenceSheet: "Mimik kütüphanesi", mockLabel: "YEREL ÖNİZLEME MOTORU — üretken video sağlayıcısı değil",
    realPortrait: "Gerçekçi portre üret", routeRealistic: "Gerçekçi (Veo)", route2d: "2D taslak (ücretsiz)",
    realisticMissing: "Gerçekçi video için eksik", routeHelp: "🎬 Gerçekçi: fotogerçekçi oyuncular, doğal konuşma ve lip-sync (Google Veo 3.1 + Nano Banana; API anahtarı gerekir). ✏️ 2D taslak: ücretsiz yerel önizleme, senaryo ve zamanlama kontrolü için.",
  },
  en: {
    forYou: "For You", trending: "Trending", following: "Following", new: "New", studio: "Studio", earnings: "Earnings",
    admin: "Admin", login: "Log in", signup: "Sign up", logout: "Log out", watch: "Watch", episodes: "Episodes",
    free: "Free", locked: "Locked", unlock: "Unlock", follow: "Follow", unfollow: "Unfollow",
    aiLabel: "AI-generated", nextEp: "Next episode", buyEpisode: "This episode", buyBundle: "5-episode bundle",
    buySeason: "Full season", sandboxNote: "Sandbox purchase: no real payment is taken.", newProject: "New series",
    genre: "Genre", logline: "Logline", language: "Language", episodesCount: "Episodes", duration: "Duration (s)",
    characters: "Characters", create: "Create", estimate: "Cost estimate", render: "Render", preview: "Preview",
    final: "Final", submit: "Submit for review", lock: "Lock identity", unlockId: "Unlock", save: "Save",
    script: "Script", credits: "credits", status: "Status", comments: "Comments", send: "Send", report: "Report",
    email: "Email", password: "Password", name: "Name", asCreator: "Join as a creator",
    pending: "Pending", available: "Available", requestPayout: "Request payout", empty: "Nothing here yet.",
    referenceSheet: "Expression library", mockLabel: "LOCAL PREVIEW ENGINE — not a generative video provider",
    realPortrait: "Generate realistic portrait", routeRealistic: "Realistic (Veo)", route2d: "2D draft (free)",
    realisticMissing: "Missing for realistic video", routeHelp: "🎬 Realistic: photoreal actors, natural speech and lip-sync (Google Veo 3.1 + Nano Banana; needs an API key). ✏️ 2D draft: free local preview for checking script and timing.",
  },
};
export type Lang = keyof typeof dict;
type Key = keyof (typeof dict)["tr"];

const Ctx = createContext<{ lang: Lang; t: (k: Key) => string; setLang: (l: Lang) => void }>({
  lang: "tr", t: (k) => dict.tr[k], setLang: () => {},
});

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>("tr");
  useEffect(() => {
    try {
      const saved = localStorage.getItem("drama.lang") as Lang | null;
      if (saved && saved in dict) setLangState(saved);
      else if (navigator.language.startsWith("en")) setLangState("en");
    } catch { /* default */ }
  }, []);
  const setLang = (l: Lang) => {
    setLangState(l);
    try { localStorage.setItem("drama.lang", l); } catch { /* ignore */ }
    document.documentElement.lang = l;
  };
  return <Ctx.Provider value={{ lang, setLang, t: (k) => dict[lang][k] }}>{children}</Ctx.Provider>;
}

export const useI18n = () => useContext(Ctx);
