from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import client_ip, current_user
from ..errors import ApiError
from ..models import AuthSession, LedgerReason, User, UserStatus
from ..schemas import ConsentIn, LoginIn, RefreshIn, SignupIn, SocialIn, TokenOut
from ..security import create_access_token, hash_password, new_refresh_token, sha256_hex, verify_password
from ..services import credits, ratelimit
from ..services.generation import audit
from ..services.social_auth import verify_id_token

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue(db: Session, user: User, device_id: str | None) -> TokenOut:
    s = get_settings()
    raw, h = new_refresh_token()
    db.add(AuthSession(user_id=user.id, token_hash=h, device_id=device_id,
                       expires_at=datetime.now(UTC) + timedelta(seconds=s.refresh_token_ttl_s)))
    db.commit()
    return TokenOut(access_token=create_access_token(user.id, user.role.value), refresh_token=raw,
                    expires_in=s.access_token_ttl_s, needs_consent=needs_consent(user))


def needs_consent(user: User) -> bool:
    return user.age_confirmed_at is None or user.terms_version != get_settings().terms_version


def _grant_signup_bonus(db: Session, user: User) -> None:
    n = get_settings().signup_bonus_credits
    if n > 0:
        credits.apply(db, user.id, n, LedgerReason.signup_bonus, f"signup:{user.id}")


def _apply_consent(user: User, age_confirmed: bool, terms_accepted: bool) -> None:
    if not (age_confirmed and terms_accepted):
        raise ApiError(422, "consent_required", "you must confirm your age and accept the terms")
    now = datetime.now(UTC)
    user.age_confirmed_at = now
    user.terms_accepted_at = now
    user.terms_version = get_settings().terms_version


@router.post("/signup", response_model=TokenOut, status_code=201)
def signup(body: SignupIn, request: Request, db: Session = Depends(get_db)):
    ratelimit.hit("auth", client_ip(request), get_settings().rl_auth_per_min)
    exists = db.execute(select(User).where(func.lower(User.email) == body.email.lower(),
                                           User.deleted_at.is_(None))).scalar_one_or_none()
    if exists:
        raise ApiError(409, "email_taken", "an account with this email already exists")
    user = User(email=body.email.lower(), password_hash=hash_password(body.password), auth_provider="email",
                country=(body.country or "").upper() or None, locale=body.locale,
                acquisition_source=body.acquisition_source)
    _apply_consent(user, body.age_confirmed, body.terms_accepted)
    db.add(user)
    db.flush()
    _grant_signup_bonus(db, user)
    audit(db, user.id, "auth.signup", "user", str(user.id), ip=client_ip(request))
    return _issue(db, user, body.device_id)


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    ratelimit.hit("auth", client_ip(request), get_settings().rl_auth_per_min)
    ratelimit.hit("auth-email", body.email.lower(), get_settings().rl_auth_per_min)
    user = db.execute(select(User).where(func.lower(User.email) == body.email.lower(),
                                         User.deleted_at.is_(None))).scalar_one_or_none()
    if not verify_password(body.password, user.password_hash if user else None) or user is None:
        raise ApiError(401, "invalid_credentials", "email or password is incorrect")
    if user.status == UserStatus.banned:
        raise ApiError(403, "account_banned")
    return _issue(db, user, body.device_id)


@router.post("/social", response_model=TokenOut)
def social(body: SocialIn, request: Request, db: Session = Depends(get_db)):
    ratelimit.hit("auth", client_ip(request), get_settings().rl_auth_per_min)
    ident = verify_id_token(body.provider, body.id_token, body.nonce)
    user = db.execute(select(User).where(User.auth_provider == ident.provider,
                                         User.provider_subject == ident.subject,
                                         User.deleted_at.is_(None))).scalar_one_or_none()
    if user is None:
        user = User(auth_provider=ident.provider, provider_subject=ident.subject,
                    email=ident.email.lower() if ident.email and ident.email_verified else None,
                    display_name=body.display_name, country=(body.country or "").upper() or None,
                    locale=body.locale)
        if body.age_confirmed and body.terms_accepted:
            _apply_consent(user, True, True)
        db.add(user)
        db.flush()
        _grant_signup_bonus(db, user)
        audit(db, user.id, "auth.signup", "user", str(user.id), {"provider": ident.provider})
    if user.status == UserStatus.banned:
        raise ApiError(403, "account_banned")
    return _issue(db, user, body.device_id)


@router.post("/refresh", response_model=TokenOut)
def refresh(body: RefreshIn, db: Session = Depends(get_db)):
    sess = db.execute(select(AuthSession).where(AuthSession.token_hash == sha256_hex(body.refresh_token))
                      .with_for_update()).scalar_one_or_none()
    now = datetime.now(UTC)
    if sess is None or sess.expires_at < now:
        raise ApiError(401, "invalid_refresh_token")
    if sess.revoked_at is not None:
        # Reuse of a rotated token => likely theft. Revoke the whole family for this user.
        for other in db.execute(select(AuthSession).where(AuthSession.user_id == sess.user_id,
                                                          AuthSession.revoked_at.is_(None))).scalars():
            other.revoked_at = now
        db.commit()
        raise ApiError(401, "invalid_refresh_token")
    user = db.get(User, sess.user_id)
    if user is None or user.status != UserStatus.active:
        raise ApiError(401, "invalid_refresh_token")
    sess.revoked_at = now
    return _issue(db, user, sess.device_id)


@router.post("/logout", status_code=204)
def logout(body: RefreshIn, db: Session = Depends(get_db)):
    sess = db.execute(select(AuthSession).where(AuthSession.token_hash == sha256_hex(body.refresh_token))
                      ).scalar_one_or_none()
    if sess and sess.revoked_at is None:
        sess.revoked_at = datetime.now(UTC)
        db.commit()


@router.post("/consent", status_code=204)
def consent(body: ConsentIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    user = db.merge(user)
    _apply_consent(user, body.age_confirmed, body.terms_accepted)
    if body.country:
        user.country = body.country.upper()
    audit(db, user.id, "auth.consent", "user", str(user.id), {"terms_version": user.terms_version})
    db.commit()
