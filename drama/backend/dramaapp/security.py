import hashlib
import hmac
import time
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from .config import get_settings


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode()[:72], hashed.encode())
    except ValueError:
        return False


def create_access_token(user_id: str, role: str) -> str:
    s = get_settings()
    exp = datetime.now(UTC) + timedelta(minutes=s.access_token_minutes)
    return jwt.encode({"sub": user_id, "role": role, "exp": exp}, s.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])


# ------------------------------------------------------------------ signed media URLs (spec §2)
def sign_media(path: str, ttl_s: int = 3600, now: int | None = None) -> str:
    exp = (now or int(time.time())) + ttl_s
    sig = hmac.new(get_settings().media_signing_secret.encode(), f"{path}:{exp}".encode(), hashlib.sha256)
    return f"exp={exp}&sig={sig.hexdigest()[:32]}"


def verify_media(path: str, exp: int, sig: str) -> bool:
    if exp < int(time.time()):
        return False
    good = hmac.new(get_settings().media_signing_secret.encode(), f"{path}:{exp}".encode(), hashlib.sha256)
    return hmac.compare_digest(good.hexdigest()[:32], sig)


def hmac_hex(secret: str, payload: bytes) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
