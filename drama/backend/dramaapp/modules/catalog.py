"""Viewer platform (spec §2): feed, series detail, playback with server-side entitlement checks and
signed URLs, watch events (qualified views), follows, comments, reports, media serving."""

import math
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, optional_user
from ..errors import AppError
from ..models import (
    Asset,
    Comment,
    CreatorProfile,
    Episode,
    Follow,
    ModerationCase,
    Report,
    Series,
    User,
    WatchEvent,
    now,
)
from ..security import sign_media, verify_media
from ..storage import local_path, signed_url
from .commerce import episode_index, has_access, ordered_episodes, pricing_for
from .moderation_rules import check_text

router = APIRouter(tags=["catalog"])

AGE_MIN = {"7+": 0, "13+": 13, "16+": 16, "18+": 18}


def _url(db: Session, aid: str | None) -> str | None:
    a = db.get(Asset, aid) if aid else None
    return signed_url(a.storage_key) if a else None


def _available(series: Series, user: User | None, country: str | None) -> tuple[bool, str]:
    if series.status != "published":
        return False, "unpublished"
    if series.rights_expire_at and series.rights_expire_at < now():
        return False, "rights_expired"
    if country and country.upper() in [c.upper() for c in (series.regions_blocked or [])]:
        return False, "region_blocked"
    need = AGE_MIN.get(series.age_rating, 0)
    if need >= 16:
        if not user or not user.birth_year or now().year - user.birth_year < need:
            return False, "age_gate"
    return True, "ok"


def series_card(db: Session, s: Series) -> dict:
    cp = db.get(CreatorProfile, s.creator_id)
    eps = ordered_episodes(db, s.id)
    return {"id": s.id, "slug": s.slug, "title": s.title, "logline": s.logline, "genre": s.genre,
            "language": s.language, "age_rating": s.age_rating, "creator": cp.handle if cp else None,
            "cover_url": _url(db, s.cover_asset_id), "episode_count": len(eps),
            "free_episodes": pricing_for(s)["free_episodes"], "ai_label": "AI-generated",
            "first_episode_id": eps[0].id if eps else None}


# ------------------------------------------------------------------------------------------ ranking
def series_stats(db: Session, series_id: str, since=None) -> dict:
    q = select(WatchEvent).join(Episode).where(Episode.series_id == series_id)
    if since:
        q = q.where(WatchEvent.created_at >= since)
    evs = db.scalars(q).all()
    viewers = {(e.user_id or e.anon_id, e.episode_id) for e in evs}
    completed = {(e.user_id or e.anon_id, e.episode_id) for e in evs if e.completed}
    qualified = {(e.user_id or e.anon_id, e.episode_id) for e in evs if e.qualified}
    eps = [e.id for e in ordered_episodes(db, series_id)]
    by_viewer: dict = {}
    for v, ep in viewers:
        by_viewer.setdefault(v, set()).add(ep)
    progressed = sum(1 for s in by_viewer.values() if len(s) >= 2)
    return {"views": len(viewers), "qualified_views": len(qualified), "completions": len(completed),
            "watch_time_s": round(sum(e.watched_s for e in evs), 1), "viewers": len(by_viewer),
            "viewers_progressed": progressed, "episodes": len(eps)}


def score(db: Session, s: Series) -> float:
    """Transparent heuristic v1 (spec §2): completion, next-episode conversion, engagement, freshness,
    with Bayesian priors so new series aren't buried. Abuse adjustment = only qualified, deduped events."""
    st = series_stats(db, s.id)
    completion = (st["completions"] + 2) / (st["views"] + 4)
    progression = (st["viewers_progressed"] + 1) / (st["viewers"] + 3)
    follows = db.scalar(select(func.count()).select_from(Follow).where(Follow.series_id == s.id)) or 0
    engagement = math.log1p(follows + st["qualified_views"] * 0.2) / 5
    age_days = max(0.0, (now() - (s.published_at or s.created_at)).total_seconds() / 86400) if s.published_at else 30
    freshness = math.exp(-age_days / 7)
    return round(0.35 * completion + 0.25 * progression + 0.15 * min(1, engagement) + 0.25 * freshness, 4)


@router.get("/feed")
def feed(tab: str = Query("for_you", pattern="^(for_you|trending|following|new)$"), genre: str | None = None,
         q: str | None = None, user: User | None = Depends(optional_user),
         x_country: str | None = Header(None), db: Session = Depends(get_db)):
    stmt = select(Series).where(Series.status == "published")
    if genre:
        stmt = stmt.where(Series.genre == genre)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Series.title).like(like), func.lower(Series.logline).like(like)))
    if tab == "following":
        if not user:
            return {"items": []}
        stmt = stmt.join(Follow, Follow.series_id == Series.id).where(Follow.user_id == user.id)
    rows = [s for s in db.scalars(stmt) if _available(s, user, x_country)[0]]
    if tab == "trending":
        since = now() - timedelta(hours=48)
        keyed = [(series_stats(db, s.id, since)["qualified_views"], s) for s in rows]
    elif tab == "new":
        keyed = [((s.published_at or s.created_at).timestamp(), s) for s in rows]
    else:
        keyed = [(score(db, s), s) for s in rows]
    keyed.sort(key=lambda x: x[0], reverse=True)
    items = []
    for sc, s in keyed[:50]:
        card = series_card(db, s)
        resume = _resume_point(db, user, s.id) if user else None
        ep_id = (resume or {}).get("episode_id") or card["first_episode_id"]
        ep = db.get(Episode, ep_id) if ep_id else None
        items.append({**card, "rank_score": sc, "resume": resume,
                      "play": {"episode_id": ep_id, "number": ep.number if ep else None,
                               "thumbnail_url": _url(db, ep.thumbnail_asset_id) if ep else None}})
    return {"tab": tab, "items": items}


def _resume_point(db: Session, user: User, series_id: str) -> dict | None:
    ev = db.scalar(select(WatchEvent).join(Episode).where(WatchEvent.user_id == user.id, Episode.series_id == series_id)
                   .order_by(WatchEvent.created_at.desc()))
    if not ev:
        return None
    ep = db.get(Episode, ev.episode_id)
    if ev.completed:
        eps = ordered_episodes(db, series_id)
        idx = next((i for i, e in enumerate(eps) if e.id == ep.id), None)
        if idx is not None and idx + 1 < len(eps):
            return {"episode_id": eps[idx + 1].id, "position_s": 0}
    return {"episode_id": ep.id, "position_s": ev.position_s}


@router.get("/series/{key}")
def series_detail(key: str, user: User | None = Depends(optional_user), x_country: str | None = Header(None),
                  db: Session = Depends(get_db)):
    s = db.get(Series, key) or db.scalar(select(Series).where(Series.slug == key))
    if not s:
        raise AppError("not_found", "Series not found", 404)
    ok, why = _available(s, user, x_country)
    if not ok:
        raise AppError(f"availability.{why}", "Series not available", 451 if why == "region_blocked" else 403)
    eps = ordered_episodes(db, s.id)
    out = []
    for i, ep in enumerate(eps, 1):
        acc, reason = has_access(db, user, ep)
        out.append({"id": ep.id, "number": ep.number, "index": i, "title": ep.title, "synopsis": ep.synopsis,
                    "duration_s": ep.duration_s, "thumbnail_url": _url(db, ep.thumbnail_asset_id),
                    "unlocked": acc, "access": reason})
    following = bool(user and db.scalar(select(Follow).where(Follow.user_id == user.id, Follow.series_id == s.id)))
    return {**series_card(db, s), "episodes": out, "following": following,
            "pricing": pricing_for(s), "canonical_url": f"/s/{s.slug}",
            "og": {"title": s.title, "description": s.logline, "image": _url(db, s.cover_asset_id)},
            "resume": _resume_point(db, user, s.id) if user else None}


@router.get("/series/{sid}/episodes")
def series_episodes(sid: str, user: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    return series_detail(sid, user, None, db)["episodes"]


@router.get("/episodes/{eid}/playback")
def playback(eid: str, user: User | None = Depends(optional_user), x_country: str | None = Header(None),
             db: Session = Depends(get_db)):
    ep = db.get(Episode, eid)
    if not ep or ep.status != "published":
        raise AppError("not_found", "Episode not found", 404)
    s = db.get(Series, ep.series_id)
    ok, why = _available(s, user, x_country)
    if not ok:
        raise AppError(f"availability.{why}", "Not available", 403)
    acc, reason = has_access(db, user, ep)
    if not acc:
        raise AppError("paywall.locked", "Unlock this episode to watch", 402, episode_id=eid)
    hls = db.get(Asset, ep.hls_asset_id)
    eps = ordered_episodes(db, s.id)
    idx = next(i for i, e in enumerate(eps) if e.id == ep.id)
    nxt = eps[idx + 1] if idx + 1 < len(eps) else None
    return {"episode_id": ep.id, "access": reason, "series_id": s.id, "title": ep.title, "number": ep.number,
            "index": episode_index(db, ep),
            "hls_url": signed_url(f"{hls.storage_key}/master.m3u8", ttl_s=3 * 3600) if hls else None,
            "mp4_url": _url(db, ep.video_asset_id), "captions_url": _url(db, ep.captions_asset_id),
            "poster_url": _url(db, ep.thumbnail_asset_id), "duration_s": ep.duration_s,
            "ai_disclosure": {"label": "AI-generated", "models": ep.provenance.get("models", [])},
            "next_episode_id": nxt.id if nxt else None}


@router.get("/media/{key:path}")
def media(key: str, exp: int, sig: str, p: str | None = None):
    """Serve signed media. HLS playlists are rewritten so every segment carries the directory signature."""
    signed_path = p or key
    if p and not key.startswith(p):
        raise AppError("media.forbidden", "Bad signature scope", 403)
    if not verify_media(signed_path, exp, sig):
        raise AppError("media.forbidden", "Expired or invalid signature", 403)
    path = local_path(key)
    if key.endswith("master.m3u8") and not p:
        prefix = key.rsplit("/", 1)[0] + "/"
        q = sign_media(prefix, ttl_s=max(60, exp - int(now().timestamp())))
        return _rewrite_playlist(path, f"{q}&p={prefix}")
    if key.endswith(".m3u8"):
        return _rewrite_playlist(path, f"exp={exp}&sig={sig}&p={p}")
    if not path.is_file():
        raise AppError("not_found", "Media not found", 404)
    return FileResponse(path)


def _rewrite_playlist(path: Path, query: str) -> PlainTextResponse:
    if not path.is_file():
        raise AppError("not_found", "Media not found", 404)
    lines = [ln if (ln.startswith("#") or not ln.strip()) else f"{ln}?{query}"
             for ln in path.read_text().splitlines()]
    return PlainTextResponse("\n".join(lines) + "\n", media_type="application/vnd.apple.mpegurl")


# ------------------------------------------------------------------------------------------ watch events
class WatchIn(BaseModel):
    event_id: str = Field(min_length=8, max_length=64)
    episode_id: str
    position_s: float = Field(ge=0)
    watched_s: float = Field(ge=0, le=7200)
    completed: bool = False
    anon_id: str | None = None


@router.post("/watch-events")
def watch_event(body: WatchIn, user: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    if db.scalar(select(WatchEvent).where(WatchEvent.event_id == body.event_id)):
        return {"ok": True, "duplicate": True}
    ep = db.get(Episode, body.episode_id)
    if not ep or ep.status != "published":
        raise AppError("not_found", "Episode not found", 404)
    if not user and not body.anon_id:
        raise AppError("watch.identity_required", "anon_id required for signed-out viewers", 400)
    acc, _ = has_access(db, user, ep)
    dur = ep.duration_s or 60
    watched = min(body.watched_s, dur * 1.1)
    # qualified view: ≥50% or ≥30 s, entitled, first qualified view per viewer/episode/day (anti-inflation)
    qualifies = acc and watched >= min(30, dur * 0.5)
    if qualifies:
        who = WatchEvent.user_id == user.id if user else WatchEvent.anon_id == body.anon_id
        dup = db.scalar(select(WatchEvent).where(who, WatchEvent.episode_id == ep.id, WatchEvent.qualified.is_(True),
                                                 WatchEvent.created_at >= now() - timedelta(days=1)))
        qualifies = dup is None
    db.add(WatchEvent(event_id=body.event_id, user_id=user.id if user else None,
                      anon_id=None if user else body.anon_id, episode_id=ep.id, position_s=body.position_s,
                      watched_s=watched, completed=body.completed, qualified=qualifies))
    db.commit()
    return {"ok": True, "qualified": qualifies}


@router.get("/me/history")
def history(user: User = Depends(current_user), db: Session = Depends(get_db)):
    evs = db.scalars(select(WatchEvent).where(WatchEvent.user_id == user.id).order_by(WatchEvent.created_at.desc())
                     .limit(200)).all()
    seen, out = set(), []
    for e in evs:
        if e.episode_id in seen:
            continue
        seen.add(e.episode_id)
        ep = db.get(Episode, e.episode_id)
        out.append({"episode_id": ep.id, "series_id": ep.series_id, "title": ep.title, "position_s": e.position_s,
                    "completed": e.completed, "at": e.created_at})
    return out


# ------------------------------------------------------------------------------------------ social
@router.post("/series/{sid}/follow")
def follow(sid: str, on: bool = True, user: User = Depends(current_user), db: Session = Depends(get_db)):
    f = db.scalar(select(Follow).where(Follow.user_id == user.id, Follow.series_id == sid))
    if on and not f:
        if not db.get(Series, sid):
            raise AppError("not_found", "Series not found", 404)
        db.add(Follow(user_id=user.id, series_id=sid))
    elif not on and f:
        db.delete(f)
    db.commit()
    return {"following": on}


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=1000)


@router.get("/episodes/{eid}/comments")
def list_comments(eid: str, db: Session = Depends(get_db)):
    rows = db.scalars(select(Comment).where(Comment.episode_id == eid, Comment.hidden.is_(False))
                      .order_by(Comment.created_at.desc()).limit(100))
    return [{"id": c.id, "user": db.get(User, c.user_id).display_name, "body": c.body, "at": c.created_at} for c in rows]


@router.post("/episodes/{eid}/comments", status_code=201)
def add_comment(eid: str, body: CommentIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not db.get(Episode, eid):
        raise AppError("not_found", "Episode not found", 404)
    mod = check_text(body.body)
    c = Comment(user_id=user.id, episode_id=eid, body=body.body, hidden=not mod["ok"])
    db.add(c)
    db.flush()
    if mod["review"] or not mod["ok"]:
        db.add(ModerationCase(target_type="comment", target_id=c.id, source="auto", priority=40,
                              notes=",".join(mod["blocked"] + mod["review"])))
    db.commit()
    return {"id": c.id, "hidden": c.hidden}


class ReportIn(BaseModel):
    target_type: str = Field(pattern="^(series|episode|comment|user|character)$")
    target_id: str
    reason: str = Field(pattern="^(impersonation|nonconsensual|minor_safety|ip_infringement|hate|violence|spam|other)$")
    details: str = Field("", max_length=2000)


@router.post("/reports", status_code=201)
def report(body: ReportIn, user: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    urgent = body.reason in ("nonconsensual", "minor_safety", "impersonation")
    case = db.scalar(select(ModerationCase).where(ModerationCase.target_id == body.target_id,
                                                  ModerationCase.status == "open"))
    if not case:
        case = ModerationCase(target_type=body.target_type, target_id=body.target_id, source="report",
                              priority=5 if urgent else 50)
        db.add(case)
        db.flush()
    elif urgent:
        case.priority = min(case.priority, 5)
    r = Report(reporter_id=user.id if user else None, target_type=body.target_type, target_id=body.target_id,
               reason=body.reason, details=body.details, case_id=case.id)
    db.add(r)
    db.commit()
    return {"id": r.id, "case_id": case.id}
