from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import current_user
from ..errors import AppError
from ..models import AuditEvent, CreatorProfile, User, now
from ..security import create_access_token, hash_password, verify_password
from . import ledger

router = APIRouter(tags=["auth"])


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=2, max_length=80)
    locale: str = Field("tr", pattern="^(tr|en)$")
    birth_year: int | None = Field(None, ge=1900, le=2026)
    as_creator: bool = False


class LoginIn(BaseModel):
    email: EmailStr
    password: str


def me_out(db: Session, u: User) -> dict:
    cp = db.get(CreatorProfile, u.id)
    return {"id": u.id, "email": u.email, "display_name": u.display_name, "role": u.role, "locale": u.locale,
            "credits": ledger.credit_balance(db, u.id),
            "creator": {"handle": cp.handle, "kyc_status": cp.kyc_status} if cp else None}


def make_creator(db: Session, u: User) -> None:
    if db.get(CreatorProfile, u.id):
        return
    base = "".join(ch for ch in u.display_name.lower() if ch.isalnum())[:24] or "creator"
    handle, i = base, 1
    while db.scalar(select(CreatorProfile).where(CreatorProfile.handle == handle)):
        i += 1
        handle = f"{base}{i}"
    db.add(CreatorProfile(user_id=u.id, handle=handle))
    if u.role == "viewer":
        u.role = "creator"
    db.flush()
    ledger.grant_credits(db, u.id, get_settings().signup_bonus_credits, key=f"signup_bonus:{u.id}",
                         memo="creator free trial credits")


@router.post("/auth/signup", status_code=201)
def signup(body: SignupIn, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise AppError("auth.email_taken", "Email already registered", 409)
    u = User(email=body.email.lower(), password_hash=hash_password(body.password), display_name=body.display_name,
             locale=body.locale, birth_year=body.birth_year)
    db.add(u)
    db.flush()
    if body.as_creator:
        make_creator(db, u)
    db.add(AuditEvent(actor_id=u.id, action="auth.signup", target_type="user", target_id=u.id))
    db.commit()
    return {"access_token": create_access_token(u.id, u.role), "user": me_out(db, u)}


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(User.email == body.email.lower()))
    if not u or u.deleted_at or not verify_password(body.password, u.password_hash):
        raise AppError("auth.invalid_credentials", "Wrong email or password", 401)
    return {"access_token": create_access_token(u.id, u.role), "user": me_out(db, u)}


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return me_out(db, user)


@router.post("/me/become-creator")
def become_creator(user: User = Depends(current_user), db: Session = Depends(get_db)):
    make_creator(db, user)
    db.commit()
    return {"access_token": create_access_token(user.id, user.role), "user": me_out(db, user)}


@router.post("/me/delete")
def delete_account(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """GDPR/KVKK erasure request: account is deactivated immediately; PII scrubbed. Financial ledger rows
    are retained (legal obligation) but keyed by opaque id only."""
    user.deleted_at = now()
    user.email = f"deleted-{user.id}@invalid"
    user.display_name = "deleted"
    user.password_hash = "!"
    db.add(AuditEvent(actor_id=user.id, action="account.deleted", target_type="user", target_id=user.id))
    db.commit()
    return {"ok": True}
