"""Admin & trust-and-safety (spec §10): moderation queue, publish gate, takedowns, KYC status,
payout settlement (sandbox rail), provider status, ledger trial balance."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import admin_user
from ..errors import AppError
from ..models import (
    AuditEvent,
    Comment,
    CreatorProfile,
    Episode,
    GenerationJob,
    ModerationCase,
    OutboxEvent,
    Payout,
    Report,
    Series,
    User,
    now,
)
from ..providers import registry
from . import ledger
from .commerce import release_matured
from .creator import payout_out, settle_payout

router = APIRouter(prefix="/admin", tags=["admin"])


def publish_episode(db: Session, ep: Episode, actor_id: str) -> None:
    ep.status, ep.published_at = "published", now()
    s = db.get(Series, ep.series_id)
    if s.status != "published":
        s.status, s.published_at = "published", now()
    db.add(OutboxEvent(type="EpisodePublished", dedup_key=f"EpisodePublished:{ep.id}", payload={"episode_id": ep.id}))
    db.add(AuditEvent(actor_id=actor_id, action="episode.published", target_type="episode", target_id=ep.id))


@router.get("/review-queue")
def review_queue(admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    eps = db.scalars(select(Episode).where(Episode.status == "in_review").order_by(Episode.created_at)).all()
    return [{"episode_id": e.id, "series_id": e.series_id, "title": e.title, "qc": e.qc_report,
             "provenance": e.provenance} for e in eps]


class Decision(BaseModel):
    approve: bool
    notes: str = ""


@router.post("/episodes/{eid}/decision")
def decide_episode(eid: str, body: Decision, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    ep = db.get(Episode, eid)
    if not ep or ep.status != "in_review":
        raise AppError("episode.not_in_review", "Episode is not awaiting review", 409)
    if body.approve:
        db.add(OutboxEvent(type="EpisodeApproved", dedup_key=f"EpisodeApproved:{ep.id}", payload={"episode_id": ep.id}))
        publish_episode(db, ep, admin.id)
    else:
        ep.status = "rejected"
        db.add(AuditEvent(actor_id=admin.id, action="episode.rejected", target_type="episode", target_id=ep.id,
                          data={"notes": body.notes}))
    db.commit()
    return {"status": ep.status}


@router.get("/moderation")
def moderation_queue(status: str = "open", admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    cases = db.scalars(select(ModerationCase).where(ModerationCase.status == status)
                       .order_by(ModerationCase.priority, ModerationCase.created_at)).all()
    out = []
    for c in cases:
        reports = db.scalars(select(Report).where(Report.case_id == c.id)).all()
        out.append({"id": c.id, "target_type": c.target_type, "target_id": c.target_id, "source": c.source,
                    "priority": c.priority, "notes": c.notes, "reports": [{"reason": r.reason, "details": r.details}
                                                                          for r in reports]})
    return out


class CaseAction(BaseModel):
    action: str = Field(pattern="^(dismiss|takedown|hide_comment|restore)$")
    notes: str = ""


@router.post("/moderation/{cid}")
def act_on_case(cid: str, body: CaseAction, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    c = db.get(ModerationCase, cid)
    if not c:
        raise AppError("not_found", "Case not found", 404)
    if body.action == "takedown":
        if c.target_type == "episode":
            db.get(Episode, c.target_id).status = "taken_down"
        elif c.target_type == "series":
            s = db.get(Series, c.target_id)
            s.status = "taken_down"
            for se in s.seasons:
                for ep in se.episodes:
                    if ep.status == "published":
                        ep.status = "taken_down"  # takedown propagation
    elif body.action == "hide_comment" and c.target_type == "comment":
        db.get(Comment, c.target_id).hidden = True
    elif body.action == "restore" and c.target_type == "episode":
        db.get(Episode, c.target_id).status = "published"
    c.status = "dismissed" if body.action == "dismiss" else "actioned"
    c.decision, c.notes, c.decided_by = body.action, body.notes, admin.id
    db.add(AuditEvent(actor_id=admin.id, action=f"moderation.{body.action}", target_type=c.target_type,
                      target_id=c.target_id, data={"case": c.id}))
    db.commit()
    return {"id": c.id, "status": c.status}


class KycIn(BaseModel):
    kyc_status: str = Field(pattern="^(none|pending|verified|rejected)$")
    tax_form_status: str = Field(pattern="^(none|pending|verified|rejected)$")


@router.post("/creators/{uid}/kyc")
def set_kyc(uid: str, body: KycIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    """Manual override until a KYC vendor (e.g. Stripe Identity / Sumsub) is contracted."""
    cp = db.get(CreatorProfile, uid)
    if not cp:
        raise AppError("not_found", "Creator not found", 404)
    cp.kyc_status, cp.tax_form_status = body.kyc_status, body.tax_form_status
    db.add(AuditEvent(actor_id=admin.id, action="creator.kyc_set", target_type="creator", target_id=uid,
                      data=body.model_dump()))
    db.commit()
    return {"ok": True}


class CreditGrant(BaseModel):
    credits: int = Field(gt=0, le=1_000_000)
    reason: str = Field(min_length=3, max_length=200)


@router.post("/creators/{uid}/credits")
def grant_credits(uid: str, body: CreditGrant, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    """Manual credit grant (Originals programme, support, testing). Audited."""
    from ..models import uid as new_id
    if not db.get(User, uid):
        raise AppError("not_found", "User not found", 404)
    ledger.grant_credits(db, uid, body.credits, key=f"admin_grant:{new_id()}", memo=body.reason)
    db.add(AuditEvent(actor_id=admin.id, action="credits.granted", target_type="user", target_id=uid,
                      data=body.model_dump()))
    db.commit()
    return {"balance": ledger.credit_balance(db, uid)}


@router.get("/payouts")
def payouts(admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    return [payout_out(p) | {"creator_id": p.creator_id} for p in db.scalars(select(Payout))]


@router.post("/payouts/{pid}/settle")
def settle(pid: str, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    p = db.get(Payout, pid)
    if not p:
        raise AppError("not_found", "Payout not found", 404)
    return payout_out(settle_payout(db, p, admin.id, external_ref=f"sandbox-{pid[:8]}"))


@router.post("/ledger/release-holds")
def release_holds(admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    return {"released": release_matured(db)}


@router.get("/ledger/trial-balance")
def trial_balance(admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    tb = ledger.trial_balance(db)
    return {"per_currency_sum": tb, "balanced": all(v == 0 for v in tb.values())}


@router.get("/providers")
def providers(admin: User = Depends(admin_user)):
    return registry.status()


@router.get("/jobs")
def jobs(status: str | None = None, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    q = select(GenerationJob).order_by(GenerationJob.created_at.desc()).limit(100)
    if status:
        q = q.where(GenerationJob.status == status)
    return [{"id": j.id, "status": j.status, "owner_id": j.owner_id, "quality": j.quality, "attempts": j.attempts,
             "spent_credits": j.spent_credits, "error_code": j.error_code} for j in db.scalars(q)]


@router.get("/audit")
def audit(limit: int = 100, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(min(limit, 500)))
    return [{"actor": a.actor_id, "action": a.action, "target": f"{a.target_type}:{a.target_id}", "data": a.data,
             "at": a.created_at} for a in rows]
