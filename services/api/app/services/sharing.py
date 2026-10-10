"""Share links, click capture and referral attribution (V4 Stage A3).

- Tokens are server-issued `code.sig` (HMAC-SHA256): forged or enumerated tokens are rejected before any
  database read, and clients can never name the referrer themselves (spec §6: never trust client creator ids).
- Links point at a *template* ("Try this template"). A user's generated video stays private; they share the
  file itself through the OS share sheet if they want to.
- Clicks store a keyed hash of the IP (fraud signals, de-duplication), never the IP itself.
- Attribution: first touch, one per user, new users only, inside a click window; self-referral is ignored.
  All rules live in the `referrals` remote config and are snapshotted onto the attribution row.
  No money moves here: creator/referral earnings are Stage D and will read these rows."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import ApiError, not_found
from ..models import (
    Attribution,
    FeatureFlag,
    GenerationJob,
    JobStatus,
    ReferralClick,
    Report,
    ShareLink,
    Template,
    User,
)
from . import templates_v3 as tv3

SHARE_FLAG, REFERRAL_FLAG = "sharing", "referrals"
REFERRAL_DEFAULTS = {"click_window_days": 7, "attribution_days": 30, "new_user_max_age_hours": 72,
                     "click_dedupe_minutes": 60}
REPORT_DEFAULTS = {"template_review_after_reporters": 5}


def _now() -> datetime:
    return datetime.now(UTC)


def _flag(db: Session, key: str, defaults: dict | None = None) -> tuple[bool, dict]:
    f = db.get(FeatureFlag, key)
    return bool(f and f.enabled), {**(defaults or {}), **((f.value or {}) if f else {})}


def _key() -> bytes:
    s = get_settings()
    if s.share_link_secret:
        return s.share_link_secret.encode()
    return hmac.new(s.jwt_secret.encode(), b"share-links-v1", hashlib.sha256).digest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def sign(code: str) -> str:
    return f"{code}.{_b64(hmac.new(_key(), code.encode(), hashlib.sha256).digest()[:12])}"


def unsign(token: str) -> str:
    code, _, sig = token.partition(".")
    if not code or not sig or len(token) > 80 or not hmac.compare_digest(sign(code), token):
        raise not_found("link")
    return code


def urls(token: str) -> dict:
    s = get_settings()
    return {"token": token, "url": f"{s.share_base_url.rstrip('/')}/t/{token}", "app_url": f"{s.app_scheme}://t/{token}"}


def ip_hash(ip: str) -> str:
    return hmac.new(_key(), f"ip:{ip}".encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- links

def create_link(db: Session, user: User, template_id: uuid.UUID | None, job_id: uuid.UUID | None,
                campaign: str | None) -> ShareLink:
    if not _flag(db, SHARE_FLAG)[0]:
        raise ApiError(403, "feature_disabled", "sharing is not available yet")
    if job_id is not None:
        job = db.get(GenerationJob, job_id)
        if job is None or job.user_id != user.id:
            raise not_found("generation")
        if job.status != JobStatus.completed or job.template_id is None:
            raise ApiError(409, "not_shareable", "only finished template videos can be shared")
        template_id = job.template_id
    t = db.get(Template, template_id) if template_id else None
    if t is None or not tv3.is_usable(t):
        raise not_found("template")
    existing = db.execute(select(ShareLink).where(
        ShareLink.owner_id == user.id, ShareLink.template_id == t.id, ShareLink.revoked_at.is_(None),
        ShareLink.job_id.is_(None) if job_id is None else ShareLink.job_id == job_id,
        ShareLink.campaign.is_(None) if campaign is None else ShareLink.campaign == campaign)).scalars().first()
    if existing:
        return existing
    link = ShareLink(code=_b64(secrets.token_bytes(12)), owner_id=user.id, template_id=t.id, job_id=job_id,
                     campaign=campaign)
    db.add(link)
    db.flush()
    return link


def get_link(db: Session, token: str) -> ShareLink:
    code = unsign(token)
    link = db.execute(select(ShareLink).where(ShareLink.code == code)).scalar_one_or_none()
    if link is None or link.revoked_at is not None:
        raise not_found("link")
    return link


def record_click(db: Session, link: ShareLink, ip: str, source: str, platform: str | None) -> ReferralClick | None:
    """One counted click per (link, ip hash) per dedupe window; repeated hits don't inflate metrics."""
    _, cfg = _flag(db, REFERRAL_FLAG, REFERRAL_DEFAULTS)
    h = ip_hash(ip)
    since = _now() - timedelta(minutes=int(cfg["click_dedupe_minutes"]))
    recent = db.execute(select(ReferralClick).where(ReferralClick.link_id == link.id, ReferralClick.ip_hash == h,
                                                    ReferralClick.created_at >= since)).scalars().first()
    if recent:
        return recent
    c = ReferralClick(link_id=link.id, source=source if source in ("web", "app") else "web", ip_hash=h,
                      platform=platform if platform in ("ios", "android", "web") else None)
    db.add(c)
    db.flush()
    return c


def resolve(db: Session, token: str, ip: str, source: str, platform: str | None) -> dict:
    link = get_link(db, token)
    record_click(db, link, ip, source, platform)
    t = db.get(Template, link.template_id)
    if t is None or not tv3.is_usable(t):
        return {"status": "unavailable", "template": None}
    v = tv3.current_version(db, t)
    return {"status": "ok", "template": tv3.card(db, t, v, len(tv3.slots_of(db, v))), "campaign": link.campaign}


def attribute(db: Session, user: User, token: str) -> dict:
    enabled, cfg = _flag(db, REFERRAL_FLAG, REFERRAL_DEFAULTS)
    if not enabled:
        return {"attributed": False, "reason": "disabled"}
    link = get_link(db, token)
    if db.execute(select(Attribution.id).where(Attribution.user_id == user.id)).first():
        return {"attributed": False, "reason": "already_attributed"}  # first touch wins
    if link.owner_id == user.id:
        return {"attributed": False, "reason": "self_referral"}
    if user.created_at < _now() - timedelta(hours=int(cfg["new_user_max_age_hours"])):
        return {"attributed": False, "reason": "not_new_user"}
    click = db.execute(select(ReferralClick).where(
        ReferralClick.link_id == link.id,
        ReferralClick.created_at >= _now() - timedelta(days=int(cfg["click_window_days"])))
        .order_by(ReferralClick.created_at.desc())).scalars().first()
    if click is None:
        return {"attributed": False, "reason": "no_recent_click"}
    a = Attribution(user_id=user.id, link_id=link.id, click_id=click.id, referrer_id=link.owner_id,
                    template_id=link.template_id, campaign=link.campaign, model="first_touch",
                    policy={k: cfg[k] for k in REFERRAL_DEFAULTS}, attributed_at=_now(),
                    expires_at=_now() + timedelta(days=int(cfg["attribution_days"])))
    try:
        with db.begin_nested():
            db.add(a)
            db.flush()
    except IntegrityError:
        return {"attributed": False, "reason": "already_attributed"}
    return {"attributed": True, "expires_at": a.expires_at.isoformat()}


def link_stats(db: Session, user: User) -> list[dict]:
    rows = db.execute(
        select(ShareLink, func.count(func.distinct(ReferralClick.id)), func.count(func.distinct(Attribution.id)))
        .outerjoin(ReferralClick, ReferralClick.link_id == ShareLink.id)
        .outerjoin(Attribution, Attribution.link_id == ShareLink.id)
        .where(ShareLink.owner_id == user.id).group_by(ShareLink.id)
        .order_by(ShareLink.created_at.desc()).limit(100)).all()
    return [{**urls(sign(link.code)), "template_id": str(link.template_id), "campaign": link.campaign,
             "clicks": clicks, "signups": signups, "created_at": link.created_at.isoformat()}
            for link, clicks, signups in rows]


# ---------------------------------------------------------------- template reports

def report_template(db: Session, user: User, template_id: uuid.UUID, reason: str, details: str | None) -> Report:
    t = db.get(Template, template_id)
    if t is None or not tv3.is_usable(t):
        raise not_found("template")
    dup = db.execute(select(Report).where(Report.template_id == t.id, Report.job_id.is_(None),
                                          Report.reporter_id == user.id, Report.status == "open")).scalars().first()
    if dup:
        return dup
    r = Report(reporter_id=user.id, template_id=t.id, reason=reason, details=details)
    db.add(r)
    db.flush()
    _, cfg = _flag(db, "moderation", REPORT_DEFAULTS)
    reporters = db.execute(select(func.count(func.distinct(Report.reporter_id))).where(
        Report.template_id == t.id, Report.job_id.is_(None), Report.status == "open")).scalar_one()
    # Safety-critical reasons go to review immediately; others after N distinct reporters (anti-brigading).
    if reason == "minor_safety" or reporters >= int(cfg["template_review_after_reporters"]):
        t.moderation_status = "review"
    return r
