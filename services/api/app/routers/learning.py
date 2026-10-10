"""Learning controls and feedback (Master Spec V5, Phase B)."""

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import client_ip, current_user, staff_user
from ..models import User
from ..services import learning, ratelimit
from ..services.generation import audit

router = APIRouter(tags=["learning"])


@router.get("/me/learning-consent")
def get_consent(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"policy_version": learning.POLICY_VERSION, "consents": learning.consents(db, user.id),
            "defaults": learning.PURPOSES}


class ConsentIn(BaseModel):
    technical_improvement: bool | None = None
    content_training: bool | None = None
    personalization: bool | None = None


@router.put("/me/learning-consent")
def set_consent(body: ConsentIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    out = learning.set_consents(db, user, changes)
    audit(db, user.id, "learning.consent_changed", "user", str(user.id), changes, ip=client_ip(request))
    db.commit()
    return {"policy_version": learning.POLICY_VERSION, "consents": out}


@router.delete("/me/learning-data")
def delete_learning_data(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    n = learning.delete_learning_data(db, user)
    audit(db, user.id, "learning.data_deleted", "user", str(user.id), {"events": n}, ip=client_ip(request))
    db.commit()
    return {"deleted_events": n, "consents": learning.consents(db, user.id)}


class FeedbackIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    reasons: list[str] = Field(default_factory=list, max_length=8)
    regenerated: bool = False


@router.post("/generations/{job_id}/feedback")
def feedback(job_id: uuid.UUID, body: FeedbackIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ratelimit.hit("feedback", str(user.id), 30)
    ev = learning.feedback(db, user, job_id, body.rating, body.reasons, body.regenerated)
    db.commit()
    return {"job_id": str(job_id), "rating": ev.rating, "reasons": ev.reasons}


@router.get("/admin/learning/overview")
def overview(days: int = 7, _: User = Depends(staff_user), db: Session = Depends(get_db)):
    return learning.overview(db, max(1, min(days, 90)))
