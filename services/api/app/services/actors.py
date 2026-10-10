"""Licensed AI actor marketplace (V4 Stage D, spec V4 §8).

- Off unless the `actor_marketplace` flag is on **and** a legal review reference is recorded in its config
  (`legal_review_ref`): the flag alone cannot launch it.
- A creator can only list *their own* consented identity profile, with a consent receipt and explicit,
  versioned licence terms (uses, territory, duration, commercial use, prohibited contexts, price, revocation,
  attribution). Listings go live after moderation.
- A licence snapshots the terms. Authorization is checked when the character is used, again at shot dispatch
  and again at publication (assembly); each check is logged in `license_usage_events`.
- Revocation by the owner (or consent withdrawal) ends all licences: unused time is refunded pro rata to the
  licensee and the owner's earning is clawed back in the same proportion.
- Actor earnings are a separate ledger kind (`actor_license`), never mixed with template share or referrals."""

from __future__ import annotations

import math
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import ApiError, not_found
from ..models import (
    ActorLicense,
    ActorListing,
    ConsentReceipt,
    CreatorEarning,
    FeatureFlag,
    IdentityProfile,
    LedgerReason,
    LicenseUsageEvent,
    ProfileStatus,
    User,
)
from . import creators, credits

FLAG = "actor_marketplace"
ALWAYS_PROHIBITED = {"adult", "political"}
CONTEXT_KEYWORDS = {
    "adult": r"\b(nude|naked|sexy|lingerie|erotic|porn|çıplak|ciplak|seksi|erotik)\b",
    "political": r"\b(election|vote|campaign|president|party|seçim|secim|oy ver|parti|cumhurbaşkanı|politik)\b",
    "alcohol": r"\b(beer|wine|vodka|whisky|alcohol|bira|şarap|sarap|rakı|raki|alkol)\b",
    "gambling": r"\b(casino|bet|betting|poker|kumar|bahis|rulet)\b",
    "violence": r"\b(gun|shoot|kill|blood|weapon|silah|öldür|oldur|kan)\b",
    "religion": r"\b(church|mosque|prayer|cami|kilise|dua)\b",
    "medical": r"\b(medicine|cure|doctor|ilaç|ilac|tedavi|doktor)\b",
    "financial_advice": r"\b(invest|crypto|stock tip|yatırım|yatirim|kripto|borsa)\b",
}


def _now() -> datetime:
    return datetime.now(UTC)


class Terms(BaseModel):
    permitted_uses: list[Literal["studio"]] = Field(default_factory=lambda: ["studio"], min_length=1)
    territories: list[str] = Field(default_factory=lambda: ["*"], min_length=1, max_length=50)
    duration_days: int = Field(ge=1, le=365)
    commercial_use: bool = False
    prohibited_contexts: list[Literal["adult", "political", "alcohol", "gambling", "violence", "religion",
                                      "medical", "financial_advice"]] = Field(default_factory=list)
    price_credits: int = Field(ge=1, le=100000)
    attribution_required: bool = True
    revocable: Literal[True] = True  # the person can always withdraw consent


def require_enabled(db: Session) -> dict:
    f = db.get(FeatureFlag, FLAG)
    cfg = (f.value or {}) if f else {}
    if not (f and f.enabled and cfg.get("legal_review_ref")):
        raise ApiError(403, "feature_disabled", "the AI actor marketplace is not available")
    return cfg


def _consent_active(db: Session, receipt_id: uuid.UUID) -> bool:
    r = db.get(ConsentReceipt, receipt_id)
    return r is not None and r.revoked_at is None


def create_listing(db: Session, user: User, identity_profile_id: uuid.UUID, display_name: str, terms: dict,
                   attest_own_likeness: bool, accept_licensing_terms: bool) -> ActorListing:
    require_enabled(db)
    creators.require_profile(db, user)
    if not (attest_own_likeness and accept_licensing_terms):
        raise ApiError(422, "consent_required", "confirm it is your own likeness and accept the licensing terms")
    prof = db.get(IdentityProfile, identity_profile_id)
    if prof is None or prof.user_id != user.id or prof.status != ProfileStatus.ready:
        raise not_found("identity profile")  # only your own consented, verified profile (no impersonation)
    try:
        t = Terms.model_validate(terms)
    except ValidationError as e:
        raise ApiError(422, "bad_terms", "invalid licence terms",
                       {"errors": [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()[:10]]}) from e
    t.prohibited_contexts = sorted(set(t.prohibited_contexts) | ALWAYS_PROHIBITED)
    t.territories = sorted({x.upper() if x != "*" else "*" for x in t.territories})
    from . import moderation

    if not moderation.check_text(display_name).allowed:
        raise ApiError(422, "content_blocked", "this text is not allowed")
    lid = uuid.uuid4()
    receipt = ConsentReceipt(user_id=user.id, subject_type="actor_listing", subject_id=lid,
                             scope={"likeness": True, "licensing": True, "terms": t.model_dump()},
                             terms_version="actor-licensing-v1",
                             statement="I am the person in this profile and license my likeness under these terms.")
    db.add(receipt)
    db.flush()
    listing = ActorListing(id=lid, owner_id=user.id, identity_profile_id=prof.id, consent_receipt_id=receipt.id,
                           display_name=display_name, terms=t.model_dump(), terms_version=1, status="pending_review")
    db.add(listing)
    db.flush()
    return listing


def get_listing(db: Session, listing_id: uuid.UUID) -> ActorListing:
    lst = db.get(ActorListing, listing_id)
    if lst is None or lst.status == "removed":
        raise not_found("actor")
    return lst


def listing_live(db: Session, lst: ActorListing) -> bool:
    prof = db.get(IdentityProfile, lst.identity_profile_id)
    return (lst.status == "active" and _consent_active(db, lst.consent_receipt_id) and prof is not None
            and prof.status == ProfileStatus.ready)


def quote(db: Session, user: User, lst: ActorListing) -> dict:
    require_enabled(db)
    if not listing_live(db, lst):
        raise not_found("actor")
    t = Terms.model_validate(lst.terms)
    territory_ok = "*" in t.territories or (user.country or "").upper() in t.territories
    return {"listing_id": str(lst.id), "price_credits": t.price_credits, "terms": t.model_dump(),
            "terms_version": lst.terms_version, "territory_ok": territory_ok,
            "ends_at": (_now() + timedelta(days=t.duration_days)).isoformat()}


def purchase(db: Session, user: User, lst: ActorListing, confirmed_credits: int | None, idem: str) -> ActorLicense:
    require_enabled(db)
    key = f"license:{user.id}:{idem}"
    existing = db.execute(select(ActorLicense).where(ActorLicense.idempotency_key == key)).scalar_one_or_none()
    if existing is not None:
        return existing
    q = quote(db, user, lst)
    if lst.owner_id == user.id:
        raise ApiError(409, "own_listing", "you can't license your own likeness")
    if not q["territory_ok"]:
        raise ApiError(403, "territory_not_licensed", "this actor is not licensed in your country")
    if confirmed_credits != q["price_credits"]:
        raise ApiError(409, "confirmation_required", "confirm the licence price", {"credits": q["price_credits"]})
    now = _now()
    lic = ActorLicense(listing_id=lst.id, licensee_id=user.id, terms_snapshot=q["terms"],
                       terms_version=lst.terms_version, price_credits=q["price_credits"], status="active",
                       starts_at=now, ends_at=now + timedelta(days=q["terms"]["duration_days"]), idempotency_key=key)
    db.add(lic)
    db.flush()
    credits.apply(db, user.id, -lic.price_credits, LedgerReason.license_fee, f"license:{lic.id}",
                  ref_type="actor_license", ref_id=str(lic.id))
    _accrue_actor(db, lic, lst)
    return lic


def _accrue_actor(db: Session, lic: ActorLicense, lst: ActorListing) -> None:
    from sqlalchemy import func

    from ..models import CreditAllocation, CreditLedger, CreditLot

    policy = creators.active_policy(db)
    if policy is None:
        return
    cfg = creators.PolicyConfig.model_validate(policy.config)
    paid = db.execute(select(func.coalesce(func.sum(-CreditAllocation.amount), 0))
                      .join(CreditLedger, CreditLedger.id == CreditAllocation.ledger_id)
                      .join(CreditLot, CreditLot.id == CreditAllocation.lot_id)
                      .where(CreditLedger.idempotency_key == f"license:{lic.id}",
                             CreditLot.bucket.in_(creators.PAID_BUCKETS))).scalar_one()
    gross = int(round(int(paid) * cfg.usd_per_paid_credit * creators.MICROS))
    if gross > 0 and cfg.actor_share_rate > 0 and creators._eligible(db, lst.owner_id):
        creators._accrue(db, lst.owner_id, "actor_license", round(gross * cfg.actor_share_rate), gross, policy,
                         f"actor:{lic.id}", cfg, license_id=lic.id, payer_id=lic.licensee_id)


def license_usable(db: Session, license_id: uuid.UUID, user_id: uuid.UUID, use: str = "studio") -> str | None:
    """None if usable, else the reason."""
    lic = db.get(ActorLicense, license_id)
    if lic is None or lic.licensee_id != user_id:
        return "license_missing"
    if lic.status != "active":
        return f"license_{lic.status}"
    if lic.ends_at <= _now():
        return "license_expired"
    if use not in lic.terms_snapshot.get("permitted_uses", []):
        return "use_not_permitted"
    lst = db.get(ActorListing, lic.listing_id)
    if lst is None or not listing_live(db, lst):
        return "listing_unavailable"
    return None


def log_usage(db: Session, license_id: uuid.UUID, job_id: uuid.UUID | None, stage: str, reason: str | None) -> None:
    db.add(LicenseUsageEvent(license_id=license_id, job_id=job_id, stage=stage, allowed=reason is None,
                             reason=reason))
    db.flush()


def check_contexts(lic: ActorLicense, texts: list[str]) -> list[str]:
    """Prohibited-context violations in the storyboard text for this licence (keyword screen, conservative)."""
    blob = " ".join(t for t in texts if t).lower()
    return [c for c in lic.terms_snapshot.get("prohibited_contexts", [])
            if c in CONTEXT_KEYWORDS and re.search(CONTEXT_KEYWORDS[c], blob)]


def revoke_listing(db: Session, owner: User, lst: ActorListing, why: str) -> int:
    """Owner withdraws: listing removed, consent revoked, licences end with a pro-rata refund + clawback."""
    if lst.owner_id != owner.id:
        raise not_found("actor")
    lst.status = "removed"
    r = db.get(ConsentReceipt, lst.consent_receipt_id)
    if r is not None and r.revoked_at is None:
        r.revoked_at = _now()
    n = 0
    for lic in db.execute(select(ActorLicense).where(ActorLicense.listing_id == lst.id,
                                                     ActorLicense.status == "active")).scalars():
        end_license(db, lic, why)
        n += 1
    return n


def end_license(db: Session, lic: ActorLicense, why: str) -> None:
    now = _now()
    total = (lic.ends_at - lic.starts_at).total_seconds()
    remaining = max(0.0, (lic.ends_at - now).total_seconds()) / total if total > 0 else 0.0
    lic.status, lic.revoked_at = "revoked", now
    refund = int(math.floor(lic.price_credits * remaining))
    if refund > 0:
        credits.apply(db, lic.licensee_id, refund, LedgerReason.refund, f"license_refund:{lic.id}",
                      ref_type="actor_license", ref_id=str(lic.id), note=why[:200], bucket="adjustment")
    earning = db.execute(select(CreatorEarning).where(CreatorEarning.idempotency_key == f"actor:{lic.id}")
                         ).scalar_one_or_none()
    if earning is not None and remaining > 0:
        creators._claw(db, earning, remaining, f"claw:actor:{lic.id}", f"licence revoked: {why}", None)


def listing_card(db: Session, lst: ActorListing) -> dict:
    prof = creators.get_profile(db, lst.owner_id)
    t = lst.terms
    return {"id": str(lst.id), "display_name": lst.display_name, "owner_handle": prof.handle if prof else None,
            "status": lst.status, "price_credits": t["price_credits"], "duration_days": t["duration_days"],
            "commercial_use": t["commercial_use"], "territories": t["territories"],
            "prohibited_contexts": t["prohibited_contexts"], "attribution_required": t["attribution_required"],
            "terms_version": lst.terms_version}
