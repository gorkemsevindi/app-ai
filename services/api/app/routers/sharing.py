"""Share links + deep-link landing + referral attribution + template reports (V4 Stage A3)."""

import html
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import client_ip, current_user
from ..errors import not_found
from ..models import User
from ..schemas import ReportIn
from ..services import ratelimit, sharing

router = APIRouter(tags=["sharing"])


class ShareLinkIn(BaseModel):
    template_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    campaign: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,64}$")


@router.post("/share-links", status_code=201)
def create_share_link(body: ShareLinkIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ratelimit.hit("share", str(user.id), 30)
    link = sharing.create_link(db, user, body.template_id, body.job_id, body.campaign)
    db.commit()
    return {**sharing.urls(sharing.sign(link.code)), "template_id": str(link.template_id)}


@router.get("/share-links")
def my_links(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"items": sharing.link_stats(db, user)}


@router.get("/share-links/{token}")
def resolve_link(token: str, request: Request, source: str = "app", platform: str | None = None,
                 db: Session = Depends(get_db)):
    ip = client_ip(request)
    ratelimit.hit("share_resolve", sharing.ip_hash(ip), 120)
    out = sharing.resolve(db, token, ip, source, platform)
    db.commit()
    return out


@router.post("/share-links/{token}/attribute")
def attribute(token: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ratelimit.hit("attribute", str(user.id), 10)
    out = sharing.attribute(db, user, token)
    db.commit()
    return out


@router.post("/templates/{template_id}/report", status_code=201)
def report_template(template_id: uuid.UUID, body: ReportIn, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    ratelimit.hit("report", str(user.id), 20)
    r = sharing.report_template(db, user, template_id, body.reason, body.details)
    db.commit()
    return {"id": str(r.id), "status": r.status}


# ---------------------------------------------------------------- public web: landing + app association

@router.get("/t/{token}", response_class=HTMLResponse, include_in_schema=False)
def landing(token: str, request: Request, db: Session = Depends(get_db)):
    """Web fallback for a share link (app not installed / desktop). Universal/App Links open the app instead."""
    s = get_settings()
    try:
        out = sharing.resolve(db, token, client_ip(request), "web", "web")
        db.commit()
    except Exception:  # noqa: BLE001 - unknown/forged token -> neutral page, no oracle
        out = {"status": "unavailable", "template": None}
    t = out.get("template") or {}
    title = html.escape(t.get("title") or "This template is no longer available")
    img = html.escape(t.get("thumbnail_url") or "")
    app_url = html.escape(sharing.urls(token)["app_url"])
    play = None
    if s.play_store_url:  # Play Install Referrer carries the token through a fresh install (Android)
        play = f"{s.play_store_url}{'&' if '?' in s.play_store_url else '?'}referrer={quote(token)}"
    links = "".join(f'<p><a href="{html.escape(u)}">{label}</a></p>' for u, label in
                    ((s.app_store_url, "App Store"), (play, "Google Play")) if u)
    body = (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            f"<title>{title}</title><meta property='og:title' content='{title}'>"
            + (f"<meta property='og:image' content='{img}'>" if img else "")
            + "<meta name='robots' content='noindex'></head><body style='font-family:sans-serif;padding:16px'>"
            f"<h1>{title}</h1>" + (f"<img src='{img}' alt='' style='max-width:100%'>" if img else "")
            + (f"<p><a href='{app_url}'>Try this template</a></p>{links}" if t else "") + "</body></html>")
    return HTMLResponse(body, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex",
                                       "Content-Security-Policy": "default-src 'none'; img-src https: data:; "
                                                                  "style-src 'unsafe-inline'"})


@router.get("/.well-known/apple-app-site-association", include_in_schema=False)
def aasa():
    ids = get_settings().ios_app_ids
    if not ids:
        raise not_found("file")
    return JSONResponse({"applinks": {"details": [{"appIDs": ids, "components": [{"/": "/t/*"}]}]}})


@router.get("/.well-known/assetlinks.json", include_in_schema=False)
def assetlinks():
    s = get_settings()
    if not s.android_sha256_fingerprints:
        raise not_found("file")
    return JSONResponse([{"relation": ["delegate_permission/common.handle_all_urls"],
                          "target": {"namespace": "android_app", "package_name": s.google_package_name,
                                     "sha256_cert_fingerprints": s.android_sha256_fingerprints}}])


