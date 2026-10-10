"""Store billing (V4 Stage A2): server-side verification of App Store / Google Play purchases, credit grants,
subscription periods, refunds/revocations and signed store notifications.

Rules:
- the client never says how many credits it bought: grants come from the server-side product catalog
  (remote config `billing.value.products`), keyed by store product id; prices live in the stores;
- every grant is idempotent per store transaction (`purchase:{provider}:{txn}`, `subgrant:...`), so a client
  retry, a webhook duplicate or an out-of-order notification can never grant twice;
- a store transaction is bound to one account (unique provider+transaction, plus appAccountToken /
  obfuscatedExternalAccountId when the app sets it);
- notifications are untrusted signals: Apple payloads are verified JWS (official App Store Server Library,
  chain to Apple roots + OIDs + OCSP), Google RTDN payloads only name a token and the state is re-read
  from the Play Developer API;
- refunds/revocations reverse the matching grant from its own lot first (purchase_reversal, clamped)."""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import ApiError
from ..models import CreditLedger, FeatureFlag, LedgerReason, Purchase, Subscription, User, WebhookEvent
from . import credits

FLAG = "billing"
ACTIVE_STATES = {"active", "grace"}


def _now() -> datetime:
    return datetime.now(UTC)


def _ms(v) -> datetime | None:
    return datetime.fromtimestamp(int(v) / 1000, UTC) if v else None


def _iso(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None


@dataclass
class StoreTxn:
    provider: str                   # apple|google
    transaction_id: str             # Apple transactionId / Google orderId (unique per charge)
    original_id: str                # Apple originalTransactionId / Google purchaseToken (stable per subscription)
    product_id: str
    store_kind: str                 # consumable|subscription (as the store reports it)
    environment: str                # production|sandbox
    state: str                      # purchased|pending|active|grace|on_hold|paused|canceled|expired|revoked
    expires_at: datetime | None = None
    account_token: str | None = None
    quantity: int = 1
    auto_renew: bool | None = None
    acknowledged: bool = True
    raw: dict = field(default_factory=dict)


class Verifier(Protocol):
    def verify(self, product_id: str, receipt: str, kind: str) -> StoreTxn: ...


def config(db: Session) -> tuple[bool, dict]:
    f = db.get(FeatureFlag, FLAG)
    return bool(f and f.enabled), dict((f.value or {}) if f else {})


def product(db: Session, product_id: str) -> dict:
    enabled, cfg = config(db)
    if not enabled:
        raise ApiError(403, "feature_disabled", "purchases are not available yet")
    p = (cfg.get("products") or {}).get(product_id)
    if not p or p.get("kind") not in ("consumable", "subscription"):
        raise ApiError(422, "unknown_product", "this product is not sold here")
    return p


# ---------------------------------------------------------------- Apple

class AppleVerifier:
    def __init__(self, roots: list[bytes], bundle_id: str, app_apple_id: int | None, online_checks: bool):
        from appstoreserverlibrary.models.Environment import Environment
        from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier

        self._verifiers = [(env, SignedDataVerifier(roots, online_checks, env, bundle_id, app_apple_id))
                           for env in (Environment.PRODUCTION, Environment.SANDBOX)]

    def _decode(self, fn: str, payload: str):
        from appstoreserverlibrary.signed_data_verifier import VerificationException

        last = None
        for env, v in self._verifiers:  # production first, then sandbox (App Review / TestFlight)
            try:
                return env, getattr(v, fn)(payload)
            except VerificationException as e:
                last = e
        raise ApiError(400, "invalid_receipt", "store signature could not be verified",
                       {"reason": str(getattr(last, "status", last))})

    @staticmethod
    def _txn(env, t) -> StoreTxn:
        kind = "subscription" if t.rawType == "Auto-Renewable Subscription" else "consumable"
        expires = _ms(t.expiresDate)
        if t.revocationDate:
            state = "revoked"
        elif kind == "subscription":
            state = "active" if expires and expires > _now() else "expired"
        else:
            state = "purchased"
        return StoreTxn(provider="apple", transaction_id=str(t.transactionId),
                        original_id=str(t.originalTransactionId or t.transactionId), product_id=t.productId,
                        store_kind=kind, environment=str(env.value).lower(), state=state, expires_at=expires,
                        account_token=str(t.appAccountToken) if t.appAccountToken else None,
                        quantity=int(t.quantity or 1),
                        raw={"type": t.rawType, "purchase_date": t.purchaseDate, "storefront": t.storefront,
                             "price": t.price, "currency": t.currency, "revocation_reason": t.rawRevocationReason})

    def verify(self, product_id: str, receipt: str, kind: str) -> StoreTxn:
        env, t = self._decode("verify_and_decode_signed_transaction", receipt)
        return self._txn(env, t)

    def notification(self, signed_payload: str) -> tuple[dict, StoreTxn | None, dict]:
        env, n = self._decode("verify_and_decode_notification", signed_payload)
        txn, renewal = None, {}
        data = n.data
        if data and data.signedTransactionInfo:
            _, t = self._decode("verify_and_decode_signed_transaction", data.signedTransactionInfo)
            txn = self._txn(env, t)
        if data and data.signedRenewalInfo:
            _, r = self._decode("verify_and_decode_renewal_info", data.signedRenewalInfo)
            renewal = {"auto_renew": r.rawAutoRenewStatus == 1,
                       "grace_expires_at": _ms(getattr(r, "gracePeriodExpiresDate", None))}
        meta = {"id": n.notificationUUID, "type": n.rawNotificationType, "subtype": n.rawSubtype}
        return meta, txn, renewal


# ---------------------------------------------------------------- Google

class GoogleVerifier:
    BASE = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications"
    SCOPE = "https://www.googleapis.com/auth/androidpublisher"

    def __init__(self, service_account: dict, package: str, http: httpx.Client | None = None):
        self.sa, self.package = service_account, package
        self.http = http or httpx.Client(timeout=15)
        self._token: tuple[str, float] | None = None

    def _auth(self) -> dict:
        if self._token is None or self._token[1] < time.time() + 60:
            now = int(time.time())
            assertion = jwt.encode({"iss": self.sa["client_email"], "scope": self.SCOPE,
                                    "aud": self.sa.get("token_uri", "https://oauth2.googleapis.com/token"),
                                    "iat": now, "exp": now + 3600}, self.sa["private_key"], algorithm="RS256")
            r = self.http.post(self.sa.get("token_uri", "https://oauth2.googleapis.com/token"),
                               data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                     "assertion": assertion})
            r.raise_for_status()
            body = r.json()
            self._token = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
        return {"Authorization": f"Bearer {self._token[0]}"}

    def _get(self, path: str) -> dict:
        r = self.http.get(f"{self.BASE}/{self.package}/purchases/{path}", headers=self._auth())
        if r.status_code in (400, 404, 410):
            raise ApiError(400, "invalid_receipt", "purchase token not recognised by Google Play")
        r.raise_for_status()
        return r.json()

    def _post(self, path: str) -> None:
        r = self.http.post(f"{self.BASE}/{self.package}/purchases/{path}", headers=self._auth())
        r.raise_for_status()

    def verify(self, product_id: str, receipt: str, kind: str) -> StoreTxn:
        if kind == "subscription":
            return self.subscription(receipt)
        p = self._get(f"products/{product_id}/tokens/{receipt}")
        state = {0: "purchased", 1: "revoked", 2: "pending"}.get(int(p.get("purchaseState", 0)), "pending")
        return StoreTxn(provider="google", transaction_id=p.get("orderId") or _tok(receipt), original_id=receipt,
                        product_id=p.get("productId") or product_id, store_kind="consumable",
                        environment="sandbox" if p.get("purchaseType") == 0 else "production", state=state,
                        account_token=p.get("obfuscatedExternalAccountId"), quantity=int(p.get("quantity") or 1),
                        acknowledged=int(p.get("acknowledgementState", 0)) == 1,
                        raw={"consumption_state": p.get("consumptionState"), "region": p.get("regionCode")})

    def subscription(self, token: str) -> StoreTxn:
        s = self._get(f"subscriptionsv2/tokens/{token}")
        items = s.get("lineItems") or [{}]
        item = max(items, key=lambda i: i.get("expiryTime") or "")
        state = {"SUBSCRIPTION_STATE_ACTIVE": "active", "SUBSCRIPTION_STATE_IN_GRACE_PERIOD": "grace",
                 "SUBSCRIPTION_STATE_ON_HOLD": "on_hold", "SUBSCRIPTION_STATE_PAUSED": "paused",
                 "SUBSCRIPTION_STATE_CANCELED": "canceled", "SUBSCRIPTION_STATE_EXPIRED": "expired",
                 }.get(s.get("subscriptionState", ""), "pending")
        expires = _iso(item.get("expiryTime"))
        if state == "canceled" and expires and expires > _now():
            state = "active"  # cancelled = auto-renew off; entitled until the period ends
        return StoreTxn(provider="google", transaction_id=item.get("latestSuccessfulOrderId") or
                        s.get("latestOrderId") or _tok(token), original_id=token,
                        product_id=item.get("productId", ""), store_kind="subscription",
                        environment="sandbox" if s.get("testPurchase") is not None else "production",
                        state=state, expires_at=expires,
                        account_token=(s.get("externalAccountIdentifiers") or {}).get("obfuscatedExternalAccountId"),
                        auto_renew=bool((item.get("autoRenewingPlan") or {}).get("autoRenewEnabled", False)),
                        acknowledged=s.get("acknowledgementState") == "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED",
                        raw={"linked_purchase_token": s.get("linkedPurchaseToken"), "region": s.get("regionCode")})

    def finalize(self, txn: StoreTxn) -> None:
        """Acknowledge (Play refunds unacknowledged purchases after 3 days) and consume consumables."""
        if txn.store_kind == "subscription":
            if not txn.acknowledged:
                self._post(f"subscriptions/{txn.product_id}/tokens/{txn.original_id}:acknowledge")
        else:
            self._post(f"products/{txn.product_id}/tokens/{txn.original_id}:consume")


def _tok(token: str) -> str:
    return "tok_" + hashlib.sha256(token.encode()).hexdigest()[:40]


# ---------------------------------------------------------------- verifier registry

_override: dict[str, object] = {}


def set_verifier(platform: str, v: object | None) -> None:
    """Tests / alternative backends. None removes the override."""
    if v is None:
        _override.pop(platform, None)
    else:
        _override[platform] = v


def verifier(platform: str):
    if platform in _override:
        return _override[platform]
    s = get_settings()
    if platform == "ios":
        if not s.apple_root_cert_paths:
            raise ApiError(503, "billing_not_configured", "App Store verification is not configured")
        roots = [open(p, "rb").read() for p in s.apple_root_cert_paths]  # noqa: SIM115
        online = s.is_prod_like if s.apple_online_checks is None else s.apple_online_checks
        v = AppleVerifier(roots, s.apple_bundle_id, s.apple_app_apple_id, online)
    elif platform == "android":
        if not s.google_service_account_file:
            raise ApiError(503, "billing_not_configured", "Google Play verification is not configured")
        with open(s.google_service_account_file) as f:
            v = GoogleVerifier(json.load(f), s.google_package_name)
    else:
        raise ApiError(422, "bad_platform", "ios or android")
    _override[platform] = v
    return v


# ---------------------------------------------------------------- applying store state

def _bind_user(db: Session, user: User, txn: StoreTxn) -> None:
    if txn.account_token and txn.account_token.replace("-", "").lower() != user.id.hex:
        raise ApiError(409, "purchase_other_account", "this purchase belongs to another account")
    owner = db.execute(select(Purchase.user_id).where(Purchase.provider == txn.provider,
                                                      Purchase.transaction_id == txn.transaction_id)).scalar()
    if owner is None and txn.store_kind == "subscription":
        owner = db.execute(select(Subscription.user_id).where(Subscription.provider == txn.provider,
                                                              Subscription.original_transaction_id == txn.original_id)
                           ).scalar()
    if owner is not None and owner != user.id:
        raise ApiError(409, "purchase_other_account", "this purchase belongs to another account")


def recompute_plan(db: Session, user: User) -> None:
    subs = db.execute(select(Subscription).where(Subscription.user_id == user.id)).scalars().all()
    live = [s.current_period_end for s in subs if s.status in ACTIVE_STATES and s.current_period_end
            and s.current_period_end > _now()]
    user.plan = "pro" if live else "free"
    user.plan_expires_at = max(live) if live else None


def apply_txn(db: Session, user: User, txn: StoreTxn) -> dict:
    """Make our state match a verified store transaction. Idempotent; safe for retries and replays."""
    p = product(db, txn.product_id)
    if p["kind"] != txn.store_kind:
        raise ApiError(422, "unknown_product", "product type mismatch")
    _bind_user(db, user, txn)
    credits.lock_user(db, user.id)
    pur = db.execute(select(Purchase).where(Purchase.provider == txn.provider,
                                            Purchase.transaction_id == txn.transaction_id)).scalar_one_or_none()
    if pur is None:
        pur = Purchase(user_id=user.id, provider=txn.provider, product_id=txn.product_id,
                       transaction_id=txn.transaction_id, kind=txn.store_kind, status="verified",
                       environment=txn.environment, raw={**txn.raw, "original_id": txn.original_id})
        db.add(pur)
        db.flush()
    if txn.state == "revoked":
        reverse(db, txn.provider, txn.transaction_id, "revoked")
        if txn.store_kind == "subscription":
            _upsert_sub(db, user, txn, status="revoked")
            recompute_plan(db, user)
        return {"status": "revoked", "granted": 0}
    if txn.state == "pending":
        return {"status": "pending", "granted": 0}

    granted = 0
    if txn.store_kind == "consumable":
        amount = int(p["credits"]) * max(1, txn.quantity)
        # sandbox (App Review / TestFlight / Play test) purchases are not revenue: never "paid" credits
        bucket = "purchased" if txn.environment == "production" else "promo"
        e = credits.apply(db, user.id, amount, LedgerReason.purchase, f"purchase:{txn.provider}:{txn.transaction_id}",
                          ref_type="purchase", ref_id=txn.transaction_id, bucket=bucket)
        granted = e.delta
        status = "granted"
    else:
        sub = _upsert_sub(db, user, txn, status=txn.state)
        status = txn.state
        if txn.state in ACTIVE_STATES and txn.expires_at and txn.expires_at > _now():
            expires = txn.expires_at if p.get("credits_expire_with_period", True) else None
            e = credits.apply(db, user.id, int(p.get("credits_per_period", 0)) or 0, LedgerReason.subscription_grant,
                              f"subgrant:{txn.provider}:{txn.transaction_id}", ref_type="subscription",
                              ref_id=txn.transaction_id,
                              bucket="subscription" if txn.environment == "production" else "promo",
                              expires_at=expires) \
                if int(p.get("credits_per_period", 0)) > 0 else None
            granted = e.delta if e is not None else 0
            sub.last_granted_period = txn.transaction_id
        recompute_plan(db, user)
    return {"status": status, "granted": granted, "environment": txn.environment, "plan": user.plan,
            "plan_expires_at": user.plan_expires_at.isoformat() if user.plan_expires_at else None}


def _upsert_sub(db: Session, user: User, txn: StoreTxn, status: str) -> Subscription:
    sub = db.execute(select(Subscription).where(Subscription.provider == txn.provider,
                                                Subscription.original_transaction_id == txn.original_id)
                     ).scalar_one_or_none()
    if sub is None:
        sub = Subscription(user_id=user.id, provider=txn.provider, product_id=txn.product_id,
                           original_transaction_id=txn.original_id, status=status, environment=txn.environment)
        db.add(sub)
    sub.product_id, sub.status = txn.product_id, status
    # out-of-order notifications: the period end only moves forward
    if txn.expires_at and (sub.current_period_end is None or txn.expires_at > sub.current_period_end):
        sub.current_period_end = txn.expires_at
    if txn.auto_renew is not None:
        sub.auto_renew = txn.auto_renew
    db.flush()
    return sub


def reverse(db: Session, provider: str, transaction_id: str, why: str) -> int:
    """Refund / revoke / chargeback: take back what that transaction granted (clamped at 0)."""
    pur = db.execute(select(Purchase).where(Purchase.provider == provider,
                                            Purchase.transaction_id == transaction_id)).scalar_one_or_none()
    if pur is None:
        return 0
    pur.status = "refunded" if why == "refund" else "revoked"
    taken = 0
    for key in (f"purchase:{provider}:{transaction_id}", f"subgrant:{provider}:{transaction_id}"):
        g = db.execute(select(CreditLedger).where(CreditLedger.idempotency_key == key)).scalar_one_or_none()
        if g is not None and g.delta > 0:
            e = credits.apply(db, pur.user_id, -g.delta, LedgerReason.purchase_reversal, f"reversal:{key}",
                              ref_type="purchase", ref_id=transaction_id, note=why, allow_negative=True,
                              prefer_source_key=key)
            taken += -e.delta
            from . import creators

            creators.on_purchase_reversed(db, key, why)  # earnings paid by these credits are clawed back
    return taken


# ---------------------------------------------------------------- notifications (durable inbox)

def store_event(db: Session, provider: str, event_id: str, event_type: str, payload: dict) -> WebhookEvent | None:
    """Insert into the inbox; None if this event was already received (replay/duplicate)."""
    try:
        with db.begin_nested():
            ev = WebhookEvent(provider=provider, event_id=event_id, event_type=event_type[:80], payload=payload)
            db.add(ev)
            db.flush()
        return ev
    except IntegrityError:
        return None


def _user_for(db: Session, txn: StoreTxn) -> User | None:
    uid = db.execute(select(Purchase.user_id).where(Purchase.provider == txn.provider,
                                                    Purchase.transaction_id == txn.transaction_id)).scalar()
    if uid is None:
        uid = db.execute(select(Subscription.user_id).where(Subscription.provider == txn.provider,
                                                            Subscription.original_transaction_id == txn.original_id)
                         ).scalar()
    if uid is None and txn.raw.get("linked_purchase_token"):
        uid = db.execute(select(Subscription.user_id).where(
            Subscription.provider == txn.provider,
            Subscription.original_transaction_id == txn.raw["linked_purchase_token"])).scalar()
    if uid is None and txn.account_token:
        try:
            uid = uuid.UUID(txn.account_token)
        except ValueError:
            uid = None
    return db.get(User, uid) if uid else None


def _finish(ev: WebhookEvent, status: str, error: str | None = None) -> None:
    ev.status, ev.error, ev.processed_at, ev.attempts = status, error, _now(), ev.attempts + 1


def process_apple(db: Session, ev: WebhookEvent, meta: dict, txn: StoreTxn | None, renewal: dict) -> None:
    t = meta["type"]
    if txn is None:
        return _finish(ev, "processed", "no transaction")
    user = _user_for(db, txn)
    if user is None:
        return _finish(ev, "failed", "unknown_user")
    if t in ("REFUND", "REVOKE"):
        reverse(db, "apple", txn.transaction_id, "refund" if t == "REFUND" else "revoked")
        if txn.store_kind == "subscription":
            _upsert_sub(db, user, txn, status="refunded" if t == "REFUND" else "revoked")
            recompute_plan(db, user)
    elif t in ("SUBSCRIBED", "DID_RENEW", "OFFER_REDEEMED", "ONE_TIME_CHARGE"):
        apply_txn(db, user, txn)
    elif t == "DID_FAIL_TO_RENEW":
        sub = _upsert_sub(db, user, txn, status="grace" if meta.get("subtype") == "GRACE_PERIOD" else "billing_retry")
        if renewal.get("grace_expires_at"):
            sub.current_period_end = max(sub.current_period_end or renewal["grace_expires_at"],
                                         renewal["grace_expires_at"])
        recompute_plan(db, user)
    elif t in ("EXPIRED", "GRACE_PERIOD_EXPIRED"):
        _upsert_sub(db, user, txn, status="expired")
        recompute_plan(db, user)
    elif t == "DID_CHANGE_RENEWAL_STATUS":
        sub = _upsert_sub(db, user, txn, status=txn.state)
        sub.auto_renew = meta.get("subtype") == "AUTO_RENEW_ENABLED"
    else:
        return _finish(ev, "ignored")
    _finish(ev, "processed")


def process_google(db: Session, ev: WebhookEvent, data: dict, v: GoogleVerifier) -> None:
    if "voidedPurchaseNotification" in data:
        n = data["voidedPurchaseNotification"]
        taken = reverse(db, "google", n.get("orderId") or _tok(n.get("purchaseToken", "")), "refund")
        if n.get("productType") == 1:  # subscription: re-read and downgrade if needed
            txn = v.subscription(n["purchaseToken"])
            user = _user_for(db, txn)
            if user is not None:
                _upsert_sub(db, user, txn, status="refunded")
                recompute_plan(db, user)
        return _finish(ev, "processed", f"reversed={taken}")
    if "subscriptionNotification" in data:
        txn = v.subscription(data["subscriptionNotification"]["purchaseToken"])
    elif "oneTimeProductNotification" in data:
        n = data["oneTimeProductNotification"]
        txn = v.verify(n["sku"], n["purchaseToken"], "consumable")
    else:
        return _finish(ev, "ignored")
    user = _user_for(db, txn)
    if user is None:
        return _finish(ev, "failed", "unknown_user")
    try:
        apply_txn(db, user, txn)
    except ApiError as e:
        return _finish(ev, "failed", e.code)
    _finish(ev, "processed")


def decode_pubsub(body: dict) -> tuple[str, dict]:
    msg = body.get("message") or {}
    data = json.loads(base64.b64decode(msg.get("data") or b"e30=").decode() or "{}")
    return str(msg.get("messageId") or msg.get("message_id") or ""), data


# ---------------------------------------------------------------- retries (scheduled tasks)

def replay_event(db: Session, ev: WebhookEvent) -> None:
    if ev.provider == "apple":
        meta, txn, renewal = verifier("ios").notification(ev.payload["signedPayload"])
        process_apple(db, ev, meta, txn, renewal)
    else:
        process_google(db, ev, ev.payload, verifier("android"))


def mark_finalize_pending(db: Session, txn: StoreTxn) -> None:
    pur = db.execute(select(Purchase).where(Purchase.provider == txn.provider,
                                            Purchase.transaction_id == txn.transaction_id)).scalar_one_or_none()
    if pur is not None:
        pur.raw = {**(pur.raw or {}), "finalize_pending": True, "product_id": txn.product_id,
                   "store_kind": txn.store_kind, "original_id": txn.original_id,
                   "acknowledged": txn.acknowledged}


def retry_finalize(db: Session, limit: int = 100) -> dict:
    """Play refunds purchases that are never acknowledged (3 days): retry until it succeeds."""
    rows = db.execute(select(Purchase).where(Purchase.provider == "google",
                                             Purchase.raw["finalize_pending"].as_boolean().is_(True))
                      .limit(limit)).scalars().all()
    ok = failed = 0
    for pur in rows:
        txn = StoreTxn(provider="google", transaction_id=pur.transaction_id, original_id=pur.raw["original_id"],
                       product_id=pur.raw["product_id"], store_kind=pur.raw["store_kind"],
                       environment=pur.environment, state="purchased",
                       acknowledged=bool(pur.raw.get("acknowledged", False)))
        try:
            verifier("android").finalize(txn)
            pur.raw = {**pur.raw, "finalize_pending": False}
            ok += 1
        except Exception:  # noqa: BLE001 - keep pending, try next run
            failed += 1
    return {"finalized": ok, "still_pending": failed}


def retry_failed_events(db: Session, max_attempts: int = 5, limit: int = 100) -> dict:
    rows = db.execute(select(WebhookEvent).where(WebhookEvent.status == "failed",
                                                 WebhookEvent.attempts < max_attempts)
                      .order_by(WebhookEvent.received_at).limit(limit)).scalars().all()
    done = 0
    for ev in rows:
        try:
            replay_event(db, ev)
        except ApiError as e:
            _finish(ev, "failed", e.code)
        done += ev.status == "processed"
    return {"retried": len(rows), "processed": done}
