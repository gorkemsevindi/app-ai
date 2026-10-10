"""Licensed AI actor marketplace API (V4 Stage D). Off unless the flag is on AND a legal review is recorded."""

import uuid

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import admin_user, client_ip, current_user
from ..errors import ApiError, not_found
from ..models import ActorLicense, ActorListing, User
from ..services import actors, ratelimit
from ..services.generation import audit

router = APIRouter(tags=["actors"])


class ListingIn(BaseModel):
    identity_profile_id: uuid.UUID
    display_name: str = Field(min_length=1, max_length=60)
    terms: dict
    attest_own_likeness: bool = False
    accept_licensing_terms: bool = False


@router.post("/actors/listings", status_code=201)
def create_listing(body: ListingIn, request: Request, user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    lst = actors.create_listing(db, user, body.identity_profile_id, body.display_name, body.terms,
                                body.attest_own_likeness, body.accept_licensing_terms)
    audit(db, user.id, "actor.listing_created", "actor_listing", str(lst.id),
          {"terms": lst.terms, "consent_receipt_id": str(lst.consent_receipt_id)}, ip=client_ip(request))
    db.commit()
    return actors.listing_card(db, lst)


@router.delete("/actors/listings/{listing_id}")
def withdraw_listing(listing_id: uuid.UUID, request: Request, user: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    """The person withdraws consent: listing removed, all licences end (pro-rata refunds, clawback)."""
    lst = db.get(ActorListing, listing_id)
    if lst is None or lst.owner_id != user.id:
        raise not_found("actor")
    n = actors.revoke_listing(db, user, lst, "consent withdrawn by the actor")
    audit(db, user.id, "actor.consent_withdrawn", "actor_listing", str(lst.id), {"licences_ended": n},
          ip=client_ip(request))
    db.commit()
    return {"id": str(lst.id), "status": lst.status, "licences_ended": n}


@router.get("/actors")
def browse(db: Session = Depends(get_db)):
    actors.require_enabled(db)
    rows = db.execute(select(ActorListing).where(ActorListing.status == "active")
                      .order_by(ActorListing.created_at.desc()).limit(100)).scalars().all()
    return {"items": [actors.listing_card(db, x) for x in rows if actors.listing_live(db, x)]}


@router.post("/actors/{listing_id}/license-quote")
def license_quote(listing_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return actors.quote(db, user, actors.get_listing(db, listing_id))


class LicenseIn(BaseModel):
    confirmed_credits: int | None = None


@router.post("/actors/{listing_id}/license", status_code=201)
def buy_license(listing_id: uuid.UUID, body: LicenseIn, request: Request, user: User = Depends(current_user),
                idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=80),
                db: Session = Depends(get_db)):
    ratelimit.hit("actor_license", str(user.id), 10)
    lic = actors.purchase(db, user, actors.get_listing(db, listing_id), body.confirmed_credits, idempotency_key)
    audit(db, user.id, "actor.licensed", "actor_license", str(lic.id),
          {"listing_id": str(listing_id), "price": lic.price_credits, "terms_version": lic.terms_version},
          ip=client_ip(request))
    db.commit()
    return _license_out(lic)


def _license_out(lic: ActorLicense) -> dict:
    return {"id": str(lic.id), "listing_id": str(lic.listing_id), "status": lic.status,
            "price_credits": lic.price_credits, "terms": lic.terms_snapshot, "terms_version": lic.terms_version,
            "starts_at": lic.starts_at.isoformat(), "ends_at": lic.ends_at.isoformat()}


@router.get("/actors/licenses")
def my_licenses(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(ActorLicense).where(ActorLicense.licensee_id == user.id)
                      .order_by(ActorLicense.created_at.desc())).scalars().all()
    return {"items": [_license_out(x) for x in rows]}


class ReviewIn(BaseModel):
    status: str = Field(pattern=r"^(active|paused|removed)$")
    note: str = Field(min_length=3, max_length=300)


@router.post("/admin/actors/{listing_id}/review")
def review(listing_id: uuid.UUID, body: ReviewIn, request: Request, admin: User = Depends(admin_user),
           db: Session = Depends(get_db)):
    """Moderation / takedown (impersonation claims, terms abuse). Removal ends licences like a withdrawal."""
    lst = db.get(ActorListing, listing_id)
    if lst is None:
        raise not_found("actor")
    if body.status == "active" and lst.status == "removed":
        raise ApiError(409, "listing_removed", "removed listings need a new consent and listing")
    if body.status == "removed":
        owner = db.get(User, lst.owner_id)
        actors.revoke_listing(db, owner, lst, f"removed by moderation: {body.note}")
    lst.status, lst.moderation_note = body.status, body.note
    audit(db, admin.id, f"actor.{body.status}", "actor_listing", str(lst.id), {"note": body.note},
          ip=client_ip(request))
    db.commit()
    return actors.listing_card(db, lst)
