from fastapi import Depends, Header
from sqlalchemy.orm import Session

from .db import get_db
from .errors import AppError
from .models import User
from .security import decode_token


def _user_from_header(authorization: str | None, db: Session) -> User | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    try:
        claims = decode_token(authorization.split(" ", 1)[1])
    except Exception as e:  # noqa: BLE001 - any decode failure is an auth failure
        raise AppError("auth.invalid_token", "Invalid or expired token", 401) from e
    user = db.get(User, claims["sub"])
    if not user or user.deleted_at:
        raise AppError("auth.invalid_token", "Unknown user", 401)
    return user


def optional_user(authorization: str | None = Header(None), db: Session = Depends(get_db)) -> User | None:
    return _user_from_header(authorization, db)


def current_user(authorization: str | None = Header(None), db: Session = Depends(get_db)) -> User:
    user = _user_from_header(authorization, db)
    if not user:
        raise AppError("auth.required", "Authentication required", 401)
    return user


def creator_user(user: User = Depends(current_user)) -> User:
    if user.role not in ("creator", "admin"):
        raise AppError("auth.creator_required", "Creator account required", 403)
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise AppError("auth.admin_required", "Admin role required", 403)
    return user
