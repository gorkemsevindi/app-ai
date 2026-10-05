"""Sign in with Apple / Google: verify the identity token signature against the provider JWKS,
plus issuer, audience and expiry. The verified `sub` is the stable account key."""

from dataclasses import dataclass
from functools import lru_cache

import jwt

from ..config import get_settings
from ..errors import ApiError

PROVIDERS = {
    "apple": {"jwks": "https://appleid.apple.com/auth/keys", "issuers": ["https://appleid.apple.com"]},
    "google": {"jwks": "https://www.googleapis.com/oauth2/v3/certs",
               "issuers": ["https://accounts.google.com", "accounts.google.com"]},
}


@dataclass
class SocialIdentity:
    provider: str
    subject: str
    email: str | None
    email_verified: bool


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)


def verify_id_token(provider: str, token: str, nonce: str | None = None) -> SocialIdentity:
    if provider not in PROVIDERS:
        raise ApiError(400, "unsupported_provider")
    s = get_settings()
    audiences = s.apple_client_ids if provider == "apple" else s.google_client_ids
    if not audiences:
        raise ApiError(503, "provider_not_configured", f"{provider} sign-in is not configured")
    cfg = PROVIDERS[provider]
    try:
        key = _jwks_client(cfg["jwks"]).get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256", "ES256"], audience=audiences,
                            options={"require": ["exp", "iat", "sub", "iss"]})
    except jwt.PyJWTError as e:
        raise ApiError(401, "invalid_identity_token", str(e)) from None
    if claims["iss"] not in cfg["issuers"]:
        raise ApiError(401, "invalid_identity_token", "bad issuer")
    if nonce is not None and claims.get("nonce") != nonce:
        raise ApiError(401, "invalid_identity_token", "nonce mismatch")
    ev = claims.get("email_verified")
    return SocialIdentity(provider, str(claims["sub"]), claims.get("email"), ev in (True, "true"))
