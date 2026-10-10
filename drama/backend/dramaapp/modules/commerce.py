"""Viewer monetisation (spec §2, §5): per-series pricing, paywall, store purchase verification,
entitlements, refunds, revenue allocation to creators.

Stores: `sandbox` is a fully working signed-receipt emulator for development and acceptance tests.
`apple`, `google`, `stripe` adapters are declared but refuse to run until credentials exist
(App Store Server API key, Play Developer API service account, Stripe secret + webhook secret).
"""

import json
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import current_user, optional_user
from ..errors import AppError
from ..models import (
    Entitlement,
    Episode,
    OutboxEvent,
    ProcessedEvent,
    Purchase,
    RevenueAllocation,
    Season,
    Series,
    User,
    now,
)
from ..security import hmac_hex
from . import ledger

router = APIRouter(tags=["commerce"])

STORE_REQUIREMENTS = {
    "apple": "App Store Server API key (Issuer ID, Key ID, .p8) + bundle id + signed notification v2 endpoint",
    "google": "Play Developer API service account + RTDN Pub/Sub topic",
    "stripe": "STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET (web only; mobile digital unlocks must use IAP)",
}


# ------------------------------------------------------------------------------------------ pricing
def validate_pricing(p: dict) -> dict:
    allowed = {"free_episodes": (0, 100), "episode_price_minor": (0, 10_000), "bundle5_price_minor": (0, 50_000),
               "season_price_minor": (0, 100_000)}
    out = {}
    for k, v in p.items():
        if k not in allowed or not isinstance(v, int) or not allowed[k][0] <= v <= allowed[k][1]:
            raise AppError("pricing.invalid", f"Invalid pricing field {k}", 422)
        out[k] = v
    return out


def pricing_for(series: Series) -> dict:
    s = get_settings()
    p = {"free_episodes": s.default_free_episodes, "episode_price_minor": s.default_episode_price_minor,
         "bundle5_price_minor": s.default_bundle5_price_minor, "season_price_minor": s.default_season_price_minor,
         "currency": s.currency}
    p.update(series.pricing or {})
    return p


def ordered_episodes(db: Session, series_id: str, published_only: bool = True) -> list[Episode]:
    q = (select(Episode).join(Season).where(Episode.series_id == series_id)
         .order_by(Season.number, Episode.number))
    if published_only:
        q = q.where(Episode.status == "published")
    return list(db.scalars(q))


def episode_index(db: Session, ep: Episode) -> int:
    """1-based position in the series' published order (drives the 'first N free' rule)."""
    eps = ordered_episodes(db, ep.series_id, published_only=ep.status == "published")
    return next((i for i, e in enumerate(eps, 1) if e.id == ep.id), len(eps) + 1)


def has_access(db: Session, user: User | None, ep: Episode) -> tuple[bool, str]:
    series = db.get(Series, ep.series_id)
    if user and (user.id == series.creator_id or user.role == "admin"):
        return True, "owner"
    if episode_index(db, ep) <= pricing_for(series)["free_episodes"]:
        return True, "free"
    if not user:
        return False, "locked"
    ent = db.scalar(select(Entitlement).where(Entitlement.user_id == user.id, Entitlement.episode_id == ep.id,
                                              Entitlement.revoked_at.is_(None)))
    if ent:
        return True, ent.source
    sub = db.scalar(select(Purchase).where(Purchase.user_id == user.id, Purchase.product_type == "subscription",
                                           Purchase.status == "verified"))
    if sub and sub.raw.get("expires_at", "") > now().isoformat():
        return True, "subscription"
    return False, "locked"


def products_for(db: Session, ep: Episode) -> list[dict]:
    series = db.get(Series, ep.series_id)
    p = pricing_for(series)
    eps = ordered_episodes(db, series.id)
    idx = next((i for i, e in enumerate(eps) if e.id == ep.id), 0)
    paid_from_here = [e for e in eps[idx: idx + 5] if eps.index(e) + 1 > p["free_episodes"]]
    season_paid = [e for e in eps if e.season_id == ep.season_id and eps.index(e) + 1 > p["free_episodes"]]
    out = [{"product_id": f"ep:{ep.id}", "type": "episode", "price_minor": p["episode_price_minor"],
            "currency": p["currency"], "episodes": [ep.id]}]
    if len(paid_from_here) > 1:
        out.append({"product_id": f"b5:{ep.id}", "type": "bundle5", "price_minor": p["bundle5_price_minor"],
                    "currency": p["currency"], "episodes": [e.id for e in paid_from_here]})
    if season_paid:
        out.append({"product_id": f"season:{ep.season_id}", "type": "season", "price_minor": p["season_price_minor"],
                    "currency": p["currency"], "episodes": [e.id for e in season_paid]})
    return out


def resolve_product(db: Session, product_id: str) -> dict:
    kind, _, ref = product_id.partition(":")
    if kind in ("ep", "b5"):
        ep = db.get(Episode, ref)
        if not ep or ep.status != "published":
            raise AppError("product.unknown", "Unknown product", 404)
        prods = {p["product_id"]: p for p in products_for(db, ep)}
        if product_id not in prods:
            raise AppError("product.unknown", "Product not offered", 404)
        return {**prods[product_id], "series_id": ep.series_id, "anchor_episode": ep.id}
    if kind == "season":
        season = db.get(Season, ref)
        eps = [e for e in ordered_episodes(db, season.series_id) if e.season_id == season.id] if season else []
        if not eps:
            raise AppError("product.unknown", "Unknown product", 404)
        prods = {p["product_id"]: p for p in products_for(db, eps[0])}
        return {**prods[product_id], "series_id": season.series_id, "anchor_episode": eps[0].id}
    if kind == "credits":
        packs = {"500": 499, "1200": 999, "3000": 1999}  # commercial decision: confirm packs/prices
        if ref not in packs:
            raise AppError("product.unknown", "Unknown credit pack", 404)
        return {"product_id": product_id, "type": "credits", "credits": int(ref), "price_minor": packs[ref],
                "currency": get_settings().currency, "episodes": []}
    if kind == "sub" and ref == "monthly":
        return {"product_id": product_id, "type": "subscription", "price_minor": 799,
                "currency": get_settings().currency, "episodes": []}
    raise AppError("product.unknown", "Unknown product", 404)


@router.get("/episodes/{eid}/offer")
def offer(eid: str, user: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    ep = db.get(Episode, eid)
    if not ep or ep.status != "published":
        raise AppError("not_found", "Episode not found", 404)
    ok, why = has_access(db, user, ep)
    return {"episode_id": eid, "unlocked": ok, "reason": why, "products": [] if ok else products_for(db, ep),
            "pricing": pricing_for(db.get(Series, ep.series_id))}


# ------------------------------------------------------------------------------------------ sandbox store
class SandboxCheckoutIn(BaseModel):
    product_id: str


@router.post("/purchases/sandbox/checkout")
def sandbox_checkout(body: SandboxCheckoutIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Emulates a store sheet: returns a signed receipt like a real store would. Disabled in production."""
    if get_settings().env == "production":
        raise AppError("store.sandbox_disabled", "Sandbox store disabled in production", 403)
    prod = resolve_product(db, body.product_id)
    receipt = {"transaction_id": f"sbx_{secrets.token_hex(8)}", "product_id": body.product_id, "user_id": user.id,
               "price_minor": prod["price_minor"], "currency": prod["currency"], "purchased_at": now().isoformat()}
    payload = json.dumps(receipt, sort_keys=True)
    return {"receipt": receipt, "signature": hmac_hex(get_settings().sandbox_store_secret, payload.encode())}


class VerifyIn(BaseModel):
    store: str = Field(pattern="^(sandbox|apple|google|stripe)$")
    receipt: dict
    signature: str = ""


@router.post("/purchases/verify")
def verify_purchase(body: VerifyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.store != "sandbox":
        raise AppError("store.not_configured", f"{body.store} verification is not configured", 501,
                       requires=STORE_REQUIREMENTS[body.store])
    s = get_settings()
    payload = json.dumps(body.receipt, sort_keys=True).encode()
    if not secrets.compare_digest(hmac_hex(s.sandbox_store_secret, payload), body.signature):
        raise AppError("purchase.invalid_signature", "Receipt signature invalid", 400)
    r = body.receipt
    if r.get("user_id") != user.id:
        raise AppError("purchase.wrong_user", "Receipt belongs to another account", 403)
    prod = resolve_product(db, r["product_id"])
    if prod["price_minor"] != r["price_minor"] or prod["currency"] != r["currency"]:
        raise AppError("purchase.price_mismatch", "Receipt price does not match catalog", 400)
    existing = db.scalar(select(Purchase).where(Purchase.store == "sandbox",
                                                Purchase.store_transaction_id == r["transaction_id"]))
    if existing:
        return purchase_out(db, existing)  # idempotent replay
    return purchase_out(db, record_purchase(db, user, "sandbox", r["transaction_id"], prod, r))


def record_purchase(db: Session, user: User, store: str, txid: str, prod: dict, raw: dict) -> Purchase:
    s = get_settings()
    pur = Purchase(user_id=user.id, store=store, store_transaction_id=txid, product_type=prod["type"],
                   series_id=prod.get("series_id"), episode_id=prod.get("anchor_episode"),
                   gross_minor=prod["price_minor"], currency=prod["currency"], raw=dict(raw))
    if prod["type"] == "subscription":
        pur.raw["expires_at"] = (now() + timedelta(days=30)).isoformat()
    db.add(pur)
    db.flush()
    for eid in prod["episodes"]:
        ent = db.scalar(select(Entitlement).where(Entitlement.user_id == user.id, Entitlement.episode_id == eid))
        if ent:
            ent.revoked_at, ent.purchase_id, ent.source = None, pur.id, "purchase"
        else:
            db.add(Entitlement(user_id=user.id, series_id=prod["series_id"], episode_id=eid, purchase_id=pur.id,
                               source="purchase"))
        db.add(OutboxEvent(type="EntitlementGranted", dedup_key=f"EntitlementGranted:{pur.id}:{eid}",
                           payload={"user_id": user.id, "episode_id": eid}))
    gross = pur.gross_minor
    cur = pur.currency
    fee_bps = s.store_fee_bps.get(store, 3000)
    tax = gross * s.indirect_tax_bps // (10_000 + s.indirect_tax_bps)
    fee = (gross - tax) * fee_bps // 10_000
    net = gross - tax - fee
    entries: list = [(f"cash:{store}", cur, gross - fee), ("expense:store_fees", cur, fee), ("liability:tax", cur, -tax)]
    if prod["type"] in ("episode", "bundle5", "season"):
        series = db.get(Series, prod["series_id"])
        creator = net * s.creator_share_bps // 10_000
        platform = net - creator
        available_at = now() + timedelta(days=s.earnings_hold_days)
        entries += [(f"liability:creator:{series.creator_id}:pending", cur, -creator, available_at),
                    ("revenue:platform:series", cur, -(platform + fee))]
        db.add(RevenueAllocation(purchase_id=pur.id, creator_id=series.creator_id, gross_minor=gross, tax_minor=tax,
                                 store_fee_minor=fee, net_minor=net, creator_minor=creator, platform_minor=platform,
                                 currency=cur, share_bps=s.creator_share_bps, available_at=available_at))
        db.add(OutboxEvent(type="RevenueAllocated", dedup_key=f"RevenueAllocated:{pur.id}",
                           payload={"purchase_id": pur.id, "creator_id": series.creator_id, "creator_minor": creator}))
    elif prod["type"] == "credits":
        entries += [("revenue:platform:credits", cur, -(net + fee))]
        ledger.grant_credits(db, user.id, prod["credits"], key=f"credits_purchase:{pur.id}", memo="credit pack")
    else:  # subscription: held in a pool, distributed by qualified watch time (policy pending approval)
        entries += [("liability:subscription_pool", cur, -(net + fee))]
    ledger.post(db, idempotency_key=f"purchase:{pur.id}", kind="purchase", entries=entries,
                memo=f"{store} {prod['product_id']}", ref={"purchase_id": pur.id})
    db.add(OutboxEvent(type="PurchaseVerified", dedup_key=f"PurchaseVerified:{pur.id}",
                       payload={"purchase_id": pur.id, "product": prod["product_id"]}))
    db.commit()
    return pur


def purchase_out(db: Session, p: Purchase) -> dict:
    ents = db.scalars(select(Entitlement).where(Entitlement.purchase_id == p.id)).all()
    return {"id": p.id, "store": p.store, "product_type": p.product_type, "status": p.status,
            "gross_minor": p.gross_minor, "currency": p.currency,
            "entitlements": [e.episode_id for e in ents if not e.revoked_at]}


@router.get("/entitlements")
def entitlements(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Entitlement).where(Entitlement.user_id == user.id, Entitlement.revoked_at.is_(None)))
    return [{"episode_id": e.episode_id, "series_id": e.series_id, "source": e.source} for e in rows]


# ------------------------------------------------------------------------------------------ refunds / store notifications
class SandboxNotificationIn(BaseModel):
    notification_id: str
    type: str = Field(pattern="^(REFUND|CHARGEBACK)$")
    transaction_id: str


@router.post("/webhooks/sandbox-store")
async def sandbox_notification(request: Request, db: Session = Depends(get_db)):
    """Signed server-to-server notification (shape mirrors App Store Server Notifications v2 / Play RTDN)."""
    raw = await request.body()
    sig = request.headers.get("x-signature", "")
    if not secrets.compare_digest(hmac_hex(get_settings().sandbox_store_secret, raw), sig):
        raise AppError("webhook.invalid_signature", "Bad signature", 400)
    body = SandboxNotificationIn(**json.loads(raw))
    if db.get(ProcessedEvent, f"sandbox:{body.notification_id}"):
        return {"ok": True, "duplicate": True}
    db.add(ProcessedEvent(id=f"sandbox:{body.notification_id}", source="sandbox_store"))
    pur = db.scalar(select(Purchase).where(Purchase.store == "sandbox",
                                           Purchase.store_transaction_id == body.transaction_id))
    if not pur:
        db.commit()
        raise AppError("purchase.not_found", "Unknown transaction", 404)
    refund_purchase(db, pur, reason=body.type)
    return {"ok": True}


def refund_purchase(db: Session, pur: Purchase, reason: str) -> None:
    from ..models import LedgerTransaction
    if pur.status == "refunded":
        return
    txn = db.scalar(select(LedgerTransaction).where(LedgerTransaction.idempotency_key == f"purchase:{pur.id}"))
    ledger.reverse(db, txn, idempotency_key=f"refund:{pur.id}", memo=reason)
    alloc = db.scalar(select(RevenueAllocation).where(RevenueAllocation.purchase_id == pur.id))
    if alloc:
        rel = db.scalar(select(LedgerTransaction).where(LedgerTransaction.idempotency_key == f"release:{alloc.id}"))
        if rel:  # earnings already released -> claw back from available balance
            ledger.reverse(db, rel, idempotency_key=f"refund-release:{alloc.id}", memo="clawback")
        alloc.reversed = True
    for e in db.scalars(select(Entitlement).where(Entitlement.purchase_id == pur.id)):
        e.revoked_at = now()
        db.add(OutboxEvent(type="EntitlementRevoked", dedup_key=f"EntitlementRevoked:{pur.id}:{e.episode_id}",
                           payload={"user_id": e.user_id, "episode_id": e.episode_id}))
    pur.status = "refunded"
    db.commit()


def release_matured(db: Session) -> int:
    """Move creator earnings past the hold window from pending to available (run daily)."""
    n = 0
    for a in db.scalars(select(RevenueAllocation).where(RevenueAllocation.available_at <= now(),
                                                        RevenueAllocation.reversed.is_(False))):
        _, created = ledger.post(db, idempotency_key=f"release:{a.id}", kind="earnings.release",
                                 entries=[(f"liability:creator:{a.creator_id}:pending", a.currency, a.creator_minor),
                                          (f"liability:creator:{a.creator_id}:available", a.currency, -a.creator_minor)])
        n += int(created)
    db.commit()
    return n
