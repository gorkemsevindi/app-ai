"""V4 Stage A2: server-side store verification, grants, subscriptions, refunds and store notifications.

Apple: real ES256 JWS with an x5c chain from a test PKI (root -> intermediate with Apple's intermediate OID ->
leaf with Apple's leaf OID), verified by Apple's official App Store Server Library against the test root.
Google: the Play Developer API is a mocked HTTP transport; the code under test does the real OAuth
assertion signing, request paths and response mapping."""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID, ObjectIdentifier
from sqlalchemy import select

from app.config import get_settings
from app.models import CreditLedger, FeatureFlag, LedgerReason, Purchase, Subscription, User, WebhookEvent
from app.services import billing

from .conftest import make_admin, signup

BUNDLE = "com.example.aivideo"
CATALOG = {"credits_500": {"kind": "consumable", "credits": 500},
           "pro_monthly": {"kind": "subscription", "credits_per_period": 1000}}


# ---------------------------------------------------------------- test PKI + JWS

def _cert(subject, issuer_name, pub, signer, ca, oid=None, days=30):
    """X509_STRICT (used by Apple's library) needs SKI/AKI and CA key usage."""
    now = datetime.now(UTC) - timedelta(days=1)
    b = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
         .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_name)]))
         .public_key(pub).serial_number(x509.random_serial_number())
         .not_valid_before(now).not_valid_after(now + timedelta(days=days))
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
         .add_extension(x509.SubjectKeyIdentifier.from_public_key(pub), critical=False)
         .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(signer.public_key()), critical=False))
    if ca:
        b = b.add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
    if oid:
        b = b.add_extension(x509.UnrecognizedExtension(ObjectIdentifier(oid), b"\x05\x00"), critical=False)
    return b.sign(signer, hashes.SHA256())


class ApplePKI:
    def __init__(self):
        self.root_key = ec.generate_private_key(ec.SECP256R1())
        self.int_key = ec.generate_private_key(ec.SECP256R1())
        self.leaf_key = ec.generate_private_key(ec.SECP256R1())
        self.root = _cert("Test Root", "Test Root", self.root_key.public_key(), self.root_key, True)
        self.inter = _cert("Test WWDR", "Test Root", self.int_key.public_key(), self.root_key, True,
                           "1.2.840.113635.100.6.2.1")
        self.leaf = _cert("Test Signer", "Test WWDR", self.leaf_key.public_key(), self.int_key, False,
                          "1.2.840.113635.100.6.11.1")

    def der(self, c):
        return c.public_bytes(serialization.Encoding.DER)

    def sign(self, payload: dict, key=None) -> str:
        x5c = [base64.b64encode(self.der(c)).decode() for c in (self.leaf, self.inter, self.root)]
        pem = (key or self.leaf_key).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption())
        return jwt.encode(payload, pem, algorithm="ES256", headers={"x5c": x5c})


@pytest.fixture
def apple():
    pki = ApplePKI()
    billing.set_verifier("ios", billing.AppleVerifier([pki.der(pki.root)], BUNDLE, 1234, False))
    yield pki
    billing.set_verifier("ios", None)


def _ms(dt):
    return int(dt.timestamp() * 1000)


def apple_txn(pki, user_id=None, product="credits_500", txn="1000001", otid=None, env="Production",
              expires=None, revoked=False, typ=None):
    now = datetime.now(UTC)
    p = {"transactionId": txn, "originalTransactionId": otid or txn, "bundleId": BUNDLE, "productId": product,
         "purchaseDate": _ms(now), "quantity": 1, "environment": env, "signedDate": _ms(now),
         "type": typ or ("Auto-Renewable Subscription" if expires else "Consumable"), "storefront": "TUR"}
    if user_id:
        p["appAccountToken"] = str(user_id)
    if expires:
        p["expiresDate"] = _ms(expires)
    if revoked:
        p["revocationDate"] = _ms(now)
    return pki.sign(p)


def apple_notice(pki, ntype, signed_txn, subtype=None, nid=None):
    p = {"notificationType": ntype, "notificationUUID": nid or str(uuid.uuid4()), "version": "2.0",
         "signedDate": _ms(datetime.now(UTC)),
         "data": {"bundleId": BUNDLE, "appAppleId": 1234, "environment": "Production",
                  "signedTransactionInfo": signed_txn}}
    if subtype:
        p["subtype"] = subtype
    return pki.sign(p)


def enable_billing(db, products=None):
    f = db.get(FeatureFlag, "billing") or FeatureFlag(key="billing")
    f.enabled, f.value = True, {"products": products or CATALOG}
    db.add(f)
    db.commit()


def _uid(client, h):
    return uuid.UUID(client.get("/me", headers=h).json()["id"])


def _verify(client, h, platform, product, receipt):
    return client.post("/purchases/verify", headers=h, json={
        "platform": platform, "product_id": product, "receipt": receipt, "idempotency_key": uuid.uuid4().hex})


# ---------------------------------------------------------------- Apple

def test_flag_and_catalog_gate(client, db, apple):
    h, _ = signup(client)
    assert client.get("/purchases/products").json() == {"enabled": False, "products": []}
    r = _verify(client, h, "ios", "credits_500", apple_txn(apple))
    assert r.status_code == 403
    enable_billing(db)
    ids = {p["product_id"] for p in client.get("/purchases/products").json()["products"]}
    assert ids == {"credits_500", "pro_monthly"}
    assert _verify(client, h, "ios", "credits_9999", apple_txn(apple)).json()["detail"]["code"] == "unknown_product"


def test_apple_consumable_grant_is_idempotent_and_bound_to_account(client, db, apple):
    enable_billing(db)
    h, _ = signup(client)
    uid = _uid(client, h)
    receipt = apple_txn(apple, uid, txn="2000001")
    r = _verify(client, h, "ios", "credits_500", receipt)
    assert r.status_code == 200, r.text
    assert r.json()["granted"] == 500 and r.json()["balance"] == 530
    # replaying the same receipt never grants twice
    assert _verify(client, h, "ios", "credits_500", receipt).json()["balance"] == 530
    buckets = {b["bucket"]: b["remaining"] for b in client.get("/credits", headers=h).json()["buckets"]}
    assert buckets == {"promo": 30, "purchased": 500}
    # another account cannot claim it (appAccountToken + unique transaction)
    h2, _ = signup(client)
    assert _verify(client, h2, "ios", "credits_500", receipt).json()["detail"]["code"] == "purchase_other_account"
    # receipt for a different product than claimed
    assert _verify(client, h, "ios", "pro_monthly", receipt).json()["detail"]["code"] == "product_mismatch"


def test_apple_forged_or_wrong_app_receipts_are_rejected(client, db, apple):
    enable_billing(db)
    h, _ = signup(client)
    # signed by a key that is not the certificate's key
    forged = apple.sign({"transactionId": "x", "bundleId": BUNDLE, "productId": "credits_500",
                         "environment": "Production", "signedDate": _ms(datetime.now(UTC)), "type": "Consumable"},
                        key=ec.generate_private_key(ec.SECP256R1()))
    assert _verify(client, h, "ios", "credits_500", forged).json()["detail"]["code"] == "invalid_receipt"
    # chain from an unknown root
    other = ApplePKI()
    assert _verify(client, h, "ios", "credits_500", apple_txn(other)).json()["detail"]["code"] == "invalid_receipt"
    # another app's bundle id
    wrong = apple.sign({"transactionId": "y", "originalTransactionId": "y", "bundleId": "com.other.app",
                        "productId": "credits_500", "environment": "Production", "type": "Consumable",
                        "signedDate": _ms(datetime.now(UTC))})
    assert _verify(client, h, "ios", "credits_500", wrong).json()["detail"]["code"] == "invalid_receipt"
    assert db.execute(select(Purchase)).first() is None


def test_apple_sandbox_is_accepted_and_labelled(client, db, apple):
    enable_billing(db)
    h, _ = signup(client)
    r = _verify(client, h, "ios", "credits_500", apple_txn(apple, txn="sb-1", env="Sandbox"))
    assert r.status_code == 200 and r.json()["environment"] == "sandbox"
    assert db.execute(select(Purchase)).scalar_one().environment == "sandbox"


def test_apple_subscription_lifecycle_via_notifications(client, db, apple):
    enable_billing(db)
    h, _ = signup(client)
    uid = _uid(client, h)
    p1_end = datetime.now(UTC) + timedelta(days=30)
    r = _verify(client, h, "ios", "pro_monthly", apple_txn(apple, uid, "pro_monthly", "s-1", "orig-1", expires=p1_end))
    assert r.json()["status"] == "active" and r.json()["plan"] == "pro" and r.json()["granted"] == 1000
    sub_credits = next(b for b in client.get("/credits", headers=h).json()["buckets"] if b["bucket"] == "subscription")
    assert sub_credits["next_expiry"]  # subscription credits expire with the period
    assert client.get("/me", headers=h).json()["plan"] == "pro"

    # renewal: new transaction id, grants the next period once even if delivered twice
    p2_end = p1_end + timedelta(days=30)
    renew = apple_notice(apple, "DID_RENEW", apple_txn(apple, uid, "pro_monthly", "s-2", "orig-1", expires=p2_end),
                         nid="n-renew")
    assert client.post("/webhooks/apple", json={"signedPayload": renew}).json()["status"] == "processed"
    assert client.post("/webhooks/apple", json={"signedPayload": renew}).json()["duplicate"] is True
    grants = db.execute(select(CreditLedger).where(CreditLedger.reason == LedgerReason.subscription_grant)).all()
    assert len(grants) == 2
    # an older notification arriving late never moves the period end backwards
    late = apple_notice(apple, "SUBSCRIBED", apple_txn(apple, uid, "pro_monthly", "s-1", "orig-1", expires=p1_end))
    client.post("/webhooks/apple", json={"signedPayload": late})
    db.expire_all()
    sub = db.execute(select(Subscription)).scalar_one()
    assert abs((sub.current_period_end - p2_end).total_seconds()) < 1

    # refund of period 2 takes those credits back from their own lot, then expiry downgrades
    refund = apple_notice(apple, "REFUND", apple_txn(apple, uid, "pro_monthly", "s-2", "orig-1", expires=p2_end,
                                                     revoked=True))
    client.post("/webhooks/apple", json={"signedPayload": refund})
    rev = db.execute(select(CreditLedger).where(CreditLedger.reason == LedgerReason.purchase_reversal)).scalar_one()
    assert rev.delta == -1000 and rev.idempotency_key == "reversal:subgrant:apple:s-2"
    expired = apple_notice(apple, "EXPIRED", apple_txn(apple, uid, "pro_monthly", "s-2", "orig-1",
                                                       expires=datetime.now(UTC) - timedelta(minutes=1)))
    client.post("/webhooks/apple", json={"signedPayload": expired})
    assert client.get("/me", headers=h).json()["plan"] == "free"


def test_apple_notification_before_verify_is_replayable(client, db, apple):
    enable_billing(db)
    ha, _ = signup(client)
    make_admin(db, ha, client)
    h, _ = signup(client)
    # no appAccountToken and no prior verify -> unknown user, kept as failed
    n = apple_notice(apple, "ONE_TIME_CHARGE", apple_txn(apple, None, txn="early-1"))
    assert client.post("/webhooks/apple", json={"signedPayload": n}).json()["status"] == "failed"
    ev = db.execute(select(WebhookEvent)).scalar_one()
    assert ev.error == "unknown_user"
    _verify(client, h, "ios", "credits_500", apple_txn(apple, None, txn="early-1"))
    r = client.post(f"/admin/webhooks/{ev.id}/replay", headers=ha)
    assert r.json()["status"] == "processed"
    assert client.get("/credits", headers=h).json()["balance"] == 530  # still exactly one grant


def test_invalid_apple_notification_is_rejected_and_not_stored(client, db, apple):
    other = ApplePKI()
    r = client.post("/webhooks/apple", json={"signedPayload": apple_notice(other, "DID_RENEW", "x")})
    assert r.status_code == 400 and db.execute(select(WebhookEvent)).first() is None


# ---------------------------------------------------------------- Google

class FakePlay:
    def __init__(self):
        self.products: dict[str, dict] = {}
        self.subs: dict[str, dict] = {}
        self.posts: list[str] = []
        self.tokens_issued = 0

    def handler(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            form = dict(x.split("=", 1) for x in req.content.decode().split("&"))
            claims = jwt.decode(form["assertion"], options={"verify_signature": False})
            assert claims["scope"] == billing.GoogleVerifier.SCOPE
            self.tokens_issued += 1
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        assert req.headers["Authorization"] == "Bearer at"
        path = url.split("/applications/com.example.aivideo/purchases/", 1)[1]
        if req.method == "POST":
            self.posts.append(path)
            return httpx.Response(200, json={})
        if path.startswith("subscriptionsv2/tokens/"):
            s = self.subs.get(path.rsplit("/", 1)[1])
        else:
            s = self.products.get(path.rsplit("/", 1)[1])
        return httpx.Response(200, json=s) if s else httpx.Response(404, json={})


@pytest.fixture
def play(monkeypatch):
    fake = FakePlay()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sa = {"client_email": "svc@test.iam.gserviceaccount.com", "token_uri": "https://oauth2.googleapis.com/token",
          "private_key": key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()).decode()}
    v = billing.GoogleVerifier(sa, "com.example.aivideo", httpx.Client(transport=httpx.MockTransport(fake.handler)))
    billing.set_verifier("android", v)
    monkeypatch.setenv("APP_GOOGLE_RTDN_TOKEN", "rtdn-secret")
    get_settings.cache_clear()
    yield fake
    billing.set_verifier("android", None)
    monkeypatch.delenv("APP_GOOGLE_RTDN_TOKEN")
    get_settings.cache_clear()


def _push(client, data, mid=None, token="rtdn-secret"):
    body = {"message": {"data": base64.b64encode(json.dumps(data).encode()).decode(),
                        "messageId": mid or uuid.uuid4().hex}, "subscription": "projects/x/subscriptions/y"}
    return client.post(f"/webhooks/google?token={token}", json=body)


def test_google_consumable_acknowledged_and_consumed_after_grant(client, db, play):
    enable_billing(db)
    h, _ = signup(client)
    uid = _uid(client, h)
    play.products["tok-1"] = {"purchaseState": 0, "orderId": "GPA.1", "productId": "credits_500",
                              "acknowledgementState": 0, "obfuscatedExternalAccountId": uid.hex}
    play.products["tok-pending"] = {"purchaseState": 2, "orderId": "GPA.2", "productId": "credits_500"}
    r = _verify(client, h, "android", "credits_500", "tok-1")
    assert r.status_code == 200 and r.json()["balance"] == 530, r.text
    assert play.posts == ["products/credits_500/tokens/tok-1:consume"]
    r = _verify(client, h, "android", "credits_500", "tok-pending")
    assert r.json()["status"] == "pending" and r.json()["balance"] == 530
    assert _verify(client, h, "android", "credits_500", "tok-unknown").json()["detail"]["code"] == "invalid_receipt"
    # voided purchase (refund) via RTDN reverses the grant
    _push(client, {"packageName": "com.example.aivideo",
                   "voidedPurchaseNotification": {"purchaseToken": "tok-1", "orderId": "GPA.1", "productType": 2}})
    assert client.get("/credits", headers=h).json()["balance"] == 30
    assert db.execute(select(Purchase).where(Purchase.transaction_id == "GPA.1")).scalar_one().status == "refunded"


def test_google_subscription_rtdn_refetches_state(client, db, play):
    enable_billing(db)
    h, _ = signup(client)
    uid = _uid(client, h)
    end = datetime.now(UTC) + timedelta(days=30)

    def sub(state, order, expiry):
        return {"subscriptionState": state, "acknowledgementState": "ACKNOWLEDGEMENT_STATE_PENDING",
                "externalAccountIdentifiers": {"obfuscatedExternalAccountId": uid.hex},
                "lineItems": [{"productId": "pro_monthly", "expiryTime": expiry.isoformat().replace("+00:00", "Z"),
                               "latestSuccessfulOrderId": order, "autoRenewingPlan": {"autoRenewEnabled": True}}]}

    play.subs["stok"] = sub("SUBSCRIPTION_STATE_ACTIVE", "GPA.s..0", end)
    r = _verify(client, h, "android", "pro_monthly", "stok")
    assert r.json()["plan"] == "pro" and r.json()["granted"] == 1000
    assert "subscriptions/pro_monthly/tokens/stok:acknowledge" in play.posts
    # forged token in push URL is refused
    assert _push(client, {"subscriptionNotification": {"purchaseToken": "stok"}}, token="nope").status_code == 401
    # renewal notification: content is only a pointer, state is re-read from Play
    play.subs["stok"] = sub("SUBSCRIPTION_STATE_ACTIVE", "GPA.s..1", end + timedelta(days=30))
    msg = {"packageName": "com.example.aivideo",
           "subscriptionNotification": {"notificationType": 2, "purchaseToken": "stok",
                                        "subscriptionId": "pro_monthly"}}
    assert _push(client, msg, mid="m1").json()["status"] == "processed"
    assert _push(client, msg, mid="m1").json()["duplicate"] is True
    grants = select(CreditLedger).where(CreditLedger.reason == LedgerReason.subscription_grant)
    assert len(db.execute(grants).all()) == 2
    play.subs["stok"] = sub("SUBSCRIPTION_STATE_EXPIRED", "GPA.s..1", datetime.now(UTC) - timedelta(minutes=5))
    _push(client, msg)
    db.expire_all()
    assert db.get(User, uid).plan == "free"


def test_google_not_configured_fails_closed(client, db, monkeypatch):
    enable_billing(db)
    h, _ = signup(client)
    monkeypatch.delenv("APP_GOOGLE_RTDN_TOKEN", raising=False)
    get_settings.cache_clear()
    r = _verify(client, h, "android", "credits_500", "tok")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "billing_not_configured"
    assert _push(client, {}).status_code == 503
