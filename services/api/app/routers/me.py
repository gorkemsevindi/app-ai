from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import AnalyticsEvent, CreditLedger, FeatureFlag, User
from ..schemas import AnalyticsIn, CreditsOut, MeOut, MePatch
from ..services import credits
from .auth import needs_consent

router = APIRouter(tags=["me"])

ANALYTICS_EVENT_ALLOWLIST = {
    "app_open", "onboarding_step", "identity_started", "identity_created", "template_view", "generate_tap",
    "generation_completed_seen", "share_tap", "share_completed", "download", "paywall_view", "purchase_start",
    "purchase_success", "purchase_cancel", "restore_tap", "report_tap",
}


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    out = MeOut.model_validate(user)
    out.credits = credits.balance(db, user.id)
    out.needs_consent = needs_consent(user)
    return out


@router.patch("/me", response_model=MeOut)
def patch_me(body: MePatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    user = db.merge(user)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(user, k, v.upper() if k == "country" else v)
    db.commit()
    return me(user, db)


@router.get("/credits", response_model=CreditsOut)
def get_credits(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(CreditLedger).where(CreditLedger.user_id == user.id)
                      .order_by(CreditLedger.id.desc()).limit(50)).scalars().all()
    return CreditsOut(balance=credits.balance(db, user.id), history=[
        {"delta": r.delta, "reason": r.reason.value, "balance_after": r.balance_after,
         "created_at": r.created_at.isoformat()} for r in rows])


@router.get("/config")
def remote_config(db: Session = Depends(get_db)):
    """Public remote config (feature flags marked public). No auth so it works pre-login."""
    flags = db.execute(select(FeatureFlag).where(FeatureFlag.public.is_(True))).scalars()
    return {f.key: {"enabled": f.enabled, "value": f.value} for f in flags}


@router.post("/events", status_code=202)
def track(events: list[AnalyticsIn], user: User = Depends(current_user), db: Session = Depends(get_db)):
    """First-party raw event store (we own the schema; can be exported to PostHog/warehouse)."""
    for e in events[:50]:
        if e.name in ANALYTICS_EVENT_ALLOWLIST:
            db.add(AnalyticsEvent(user_id=user.id, device_id=e.device_id, name=e.name,
                                  props={k: v for k, v in list(e.props.items())[:20]}))
    db.commit()
    return {"accepted": True}
