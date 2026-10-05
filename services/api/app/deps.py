import uuid

import jwt
from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .errors import ApiError
from .models import User, UserRole, UserStatus
from .security import constant_time_in, decode_access_token


def current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(401, "unauthenticated", "missing bearer token")
    try:
        claims = decode_access_token(authorization.split(" ", 1)[1])
    except jwt.PyJWTError:
        raise ApiError(401, "invalid_token", "token invalid or expired") from None
    user = db.get(User, uuid.UUID(claims["sub"]))
    if user is None or user.status in (UserStatus.deleted, UserStatus.deleting):
        raise ApiError(401, "invalid_token", "account no longer exists")
    if user.status == UserStatus.banned:
        raise ApiError(403, "account_banned", "this account is suspended")
    return user


def require_role(*roles: UserRole):
    def dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise ApiError(403, "forbidden", "insufficient permissions")
        return user

    return dep


admin_user = require_role(UserRole.admin)
staff_user = require_role(UserRole.admin, UserRole.support)


def worker_auth(authorization: str | None = Header(default=None)) -> str:
    tokens = get_settings().worker_tokens
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(401, "unauthenticated", "worker token required")
    token = authorization.split(" ", 1)[1]
    if not tokens or not constant_time_in(token, tokens):
        raise ApiError(401, "unauthenticated", "invalid worker token")
    return token


def client_ip(request: Request) -> str:
    # Behind Cloudflare / LB the edge sets CF-Connecting-IP; origin is not directly reachable.
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")
