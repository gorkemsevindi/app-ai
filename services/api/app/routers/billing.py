"""Store purchases + store notifications (V4 Stage A2). Behind the `billing` flag; the catalog maps store
product ids to credit grants server-side, prices come from the stores."""

import hmac
import logging
import uuid

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import admin_user, client_ip, current_user
from ..errors import ApiError, not_found
from ..models import User, WebhookEvent
from ..schemas import PurchaseVerifyIn
from ..services import billing, credits, ratelimit
from ..services.generation import audit

log = logging.getLogger("billing")
router = APIRouter(tags=["billing"])


@router.get("/purchases/products")
def products(db: Session = Depends(get_db)):
    """Store product ids the app should load from StoreKit / Play Billing (no prices here)."""
    enabled, cfg = billing.config(db)
    if not enabled:
        return {"enabled": False, "products": []}
    return {"enabled": True, "products": [
        {"product_id": pid, "kind": p.get("kind"), "credits": p.get("credits") or p.get("credits_per_period"),
         "platforms": p.get("platforms", ["ios", "android"])}
        for pid, p in (cfg.get("products") or {}).items()]}


@router.post("/purchases/verify")
def verify_purchase(body: PurchaseVerifyIn, request: Request, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    ratelimit.hit("purchase", str(user.id), 20)
    p = billing.product(db, body.product_id)
    v = billing.verifier(body.platform)
    txn = v.verify(body.product_id, body.receipt, p["kind"])
    if txn.product_id != body.product_id:
        raise ApiError(422, "product_mismatch", "receipt is for a different product")
    out = billing.apply_txn(db, user, txn)
    audit(db, user.id, "purchase.verified", "purchase", txn.transaction_id,
          {"provider": txn.provider, "product_id": txn.product_id, "status": out["status"],
           "granted": out["granted"], "environment": txn.environment}, ip=client_ip(request))
    db.commit()
    if txn.provider == "google" and out["status"] in ("granted", "active", "grace"):
        try:  # after commit: never consume/acknowledge something we did not durably grant
            v.finalize(txn)
        except Exception:  # noqa: BLE001 - Play retries; RTDN + next verify re-attempt
            log.warning("play finalize failed for %s", txn.transaction_id, exc_info=True)
    return {**out, "transaction_id": txn.transaction_id, "balance": credits.available(db, user.id)}


def _inbox(db: Session, provider: str, event_id: str, event_type: str, payload: dict) -> WebhookEvent | None:
    """New event, or a stored one that failed earlier (store retry), else None (duplicate/replay)."""
    ev = billing.store_event(db, provider, event_id, event_type, payload)
    if ev is not None:
        return ev
    old = db.execute(select(WebhookEvent).where(WebhookEvent.provider == provider,
                                                WebhookEvent.event_id == event_id)).scalar_one()
    return old if old.status in ("pending", "failed") else None


class AppleNotificationIn(BaseModel):
    signedPayload: str  # noqa: N815 - Apple's field name


@router.post("/webhooks/apple")
def apple_notification(body: AppleNotificationIn, db: Session = Depends(get_db)):
    v = billing.verifier("ios")
    meta, txn, renewal = v.notification(body.signedPayload)  # 400 if the signature/chain is invalid
    ev = _inbox(db, "apple", str(meta["id"]), str(meta["type"]), {"signedPayload": body.signedPayload})
    if ev is None:
        return {"ok": True, "duplicate": True}
    billing.process_apple(db, ev, meta, txn, renewal)
    db.commit()
    return {"ok": True, "status": ev.status}


@router.post("/webhooks/google")
async def google_notification(request: Request, token: str = Query(""), db: Session = Depends(get_db)):
    s = get_settings()
    if not s.google_rtdn_token:
        raise ApiError(503, "billing_not_configured", "Play notifications are not configured")
    if not hmac.compare_digest(token, s.google_rtdn_token):
        raise ApiError(401, "unauthorized", "bad token")
    message_id, data = billing.decode_pubsub(await request.json())
    if not message_id or data.get("packageName") not in (None, s.google_package_name):
        raise ApiError(400, "bad_notification", "unexpected notification")
    kind = next((k for k in data if k.endswith("Notification")), "unknown")
    ev = _inbox(db, "google", message_id, kind, data)
    if ev is None:
        return {"ok": True, "duplicate": True}
    if kind == "testNotification":
        ev.status = "ignored"
    else:
        billing.process_google(db, ev, data, billing.verifier("android"))
    db.commit()
    return {"ok": True, "status": ev.status}


@router.post("/admin/webhooks/{event_id}/replay")
def replay(event_id: uuid.UUID, request: Request, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    """Re-run a failed notification (e.g. it arrived before the app called /purchases/verify)."""
    ev = db.get(WebhookEvent, event_id)
    if ev is None:
        raise not_found("event")
    if ev.provider == "apple":
        meta, txn, renewal = billing.verifier("ios").notification(ev.payload["signedPayload"])
        billing.process_apple(db, ev, meta, txn, renewal)
    else:
        billing.process_google(db, ev, ev.payload, billing.verifier("android"))
    audit(db, admin.id, "billing.webhook_replay", "webhook_event", str(ev.id), {"status": ev.status},
          ip=client_ip(request))
    db.commit()
    return {"id": str(ev.id), "status": ev.status, "error": ev.error}
