import type { Metadata } from "next";
import SeriesView from "./view";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/** Server-rendered OG/canonical metadata for shareable series landing pages (spec §6). */
export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  try {
    const r = await fetch(`${API}/series/${slug}`, { cache: "no-store" });
    if (!r.ok) return { title: "Sahne" };
    const s = await r.json();
    return {
      title: `${s.title} — Sahne`,
      description: s.logline,
      alternates: { canonical: `/s/${s.slug}` },
      openGraph: { title: s.title, description: s.logline, images: s.og?.image ? [s.og.image] : [], type: "video.tv_show" },
      other: { "al:ios:url": `sahne://s/${s.slug}`, "al:android:url": `sahne://s/${s.slug}` },
    };
  } catch {
    return { title: "Sahne" };
  }
}

export default async function Page({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <SeriesView slug={slug} />;
}
