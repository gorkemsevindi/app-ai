"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Player } from "@/components/Player";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

type Item = {
  id: string; slug: string; title: string; logline: string; genre: string; creator: string; episode_count: number;
  free_episodes: number; cover_url: string | null; play: { episode_id: string; number: number; thumbnail_url: string | null };
  resume: { episode_id: string; position_s: number } | null;
};
const TABS = ["for_you", "trending", "following", "new"] as const;
const GENRES = ["drama", "romance", "thriller", "revenge", "comedy", "mystery"];

function Slide({ item, active }: { item: Item; active: boolean }) {
  const { t } = useI18n();
  const [pb, setPb] = useState<any>(null);
  useEffect(() => {
    if (active && !pb && item.play.episode_id) api(`/episodes/${item.play.episode_id}/playback`).then(setPb).catch(() => setPb({ locked: true }));
  }, [active, pb, item.play.episode_id]);
  return (
    <section className="slide">
      {pb?.hls_url && active ? (
        <Player src={pb.hls_url} captions={pb.captions_url} poster={pb.poster_url} autoPlay muted startAt={item.resume?.position_s} />
      ) : (
        item.cover_url && <img src={item.cover_url} alt="" style={{ height: "100%", aspectRatio: "9/16", objectFit: "cover" }} />
      )}
      <span className="ai-label">{t("aiLabel")}</span>
      <div className="meta">
        <div className="row small muted"><span className="badge">{item.genre}</span>@{item.creator} · {item.episode_count} {t("episodes").toLowerCase()} · {item.free_episodes} {t("free").toLowerCase()}</div>
        <h2 style={{ margin: "6px 0" }}>{item.title}</h2>
        <p className="muted" style={{ margin: "0 0 10px" }}>{item.logline}</p>
        <div className="row">
          <Link className="chip on" href={`/watch/${item.resume?.episode_id || item.play.episode_id}`}>▶ {t("watch")} · {item.play.number}</Link>
          <Link className="chip" href={`/s/${item.slug}`}>{t("episodes")}</Link>
        </div>
      </div>
    </section>
  );
}

export default function Feed() {
  const { t } = useI18n();
  const [tab, setTab] = useState<(typeof TABS)[number]>("for_you");
  const [genre, setGenre] = useState<string | null>(null);
  const [items, setItems] = useState<Item[] | null>(null);
  const [active, setActive] = useState(0);
  const feedRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setItems(null);
    api(`/feed?tab=${tab}${genre ? `&genre=${genre}` : ""}`).then((r) => setItems(r.items)).catch(() => setItems([]));
  }, [tab, genre]);

  const onScroll = () => {
    const el = feedRef.current;
    if (el) setActive(Math.round(el.scrollTop / el.clientHeight));
  };
  const label = { for_you: t("forYou"), trending: t("trending"), following: t("following"), new: t("new") };

  return (
    <div className="feed" ref={feedRef} onScroll={onScroll}>
      <div className="slide" style={{ position: "fixed", inset: 0, height: 0, zIndex: 10, background: "none" }}>
        <div className="tabs">
          {TABS.map((k) => <button key={k} className={`chip ${tab === k ? "on" : ""}`} onClick={() => setTab(k)}>{label[k]}</button>)}
        </div>
        <div className="tabs" style={{ top: 96 }}>
          {GENRES.map((g) => <button key={g} className={`chip ${genre === g ? "on" : ""}`} onClick={() => setGenre(genre === g ? null : g)}>{g}</button>)}
        </div>
      </div>
      {items === null && <section className="slide"><p className="muted" style={{ alignSelf: "center" }}>…</p></section>}
      {items?.length === 0 && <section className="slide"><p className="muted" style={{ alignSelf: "center" }}>{t("empty")}</p></section>}
      {items?.map((it, i) => <Slide key={it.id} item={it} active={i === active} />)}
    </div>
  );
}
