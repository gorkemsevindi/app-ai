"""Operations API (V4 Stage E): model registry + routing preview, benchmark harness with blind review,
scheduled task trigger and run log."""

import hmac
import uuid

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import admin_user, client_ip, staff_user
from ..errors import ApiError
from ..models import BenchmarkSet, ScheduledRun, User
from ..services import benchmarks
from ..services import router as routing
from ..services.generation import audit

router = APIRouter(tags=["ops"])


@router.get("/admin/models")
def models(_: User = Depends(staff_user), db: Session = Depends(get_db)):
    enabled, cfg = routing.config(db)
    reg = routing.registry(db)
    hl = routing.health(db, list(reg), int(cfg["failure_window_hours"]))
    dis = routing.disabled_models(db)
    return {"adaptive_routing": enabled, "policy": {k: cfg[k] for k in cfg if k != "providers"},
            "providers": [{"provider": n, **info, **hl[n], "kill_switch": n in dis} for n, info in reg.items()]}


@router.get("/admin/routing/preview")
def preview(capabilities: str = "TEXT_TO_VIDEO", tier: str = "standard", _: User = Depends(staff_user),
            db: Session = Depends(get_db)):
    needed = {c.strip().upper() for c in capabilities.split(",") if c.strip()}
    return routing.choose(db, needed, tier)


class SetIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9_-]{3,80}$")
    rights_note: str = Field(min_length=10, max_length=300, description="who owns / consented to the material")
    cases: list[benchmarks.CaseIn] = Field(min_length=1, max_length=200)


@router.post("/admin/benchmarks/sets", status_code=201)
def create_set(body: SetIn, request: Request, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    s = benchmarks.create_set(db, admin, body.name, body.rights_note, body.cases)
    audit(db, admin.id, "benchmark.set_created", "benchmark_set", str(s.id), {"version": s.version,
                                                                              "cases": len(body.cases)},
          ip=client_ip(request))
    db.commit()
    return {"id": str(s.id), "name": s.name, "version": s.version, "cases": len(body.cases)}


@router.get("/admin/benchmarks/sets")
def list_sets(_: User = Depends(staff_user), db: Session = Depends(get_db)):
    rows = db.execute(select(BenchmarkSet).order_by(BenchmarkSet.created_at.desc())).scalars().all()
    return {"items": [{"id": str(s.id), "name": s.name, "version": s.version, "frozen": s.frozen} for s in rows]}


class RunIn(BaseModel):
    set_id: uuid.UUID
    providers: list[str] = Field(min_length=1, max_length=10)


@router.post("/admin/benchmarks/runs", status_code=201)
def start_run(body: RunIn, request: Request, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    run = benchmarks.start_run(db, admin, body.set_id, body.providers)
    audit(db, admin.id, "benchmark.run_started", "benchmark_run", str(run.id), {"providers": body.providers},
          ip=client_ip(request))
    db.commit()
    return {"id": str(run.id)}


@router.get("/admin/benchmarks/runs/{run_id}")
def run_report(run_id: uuid.UUID, _: User = Depends(staff_user), db: Session = Depends(get_db)):
    return benchmarks.report(db, run_id)


@router.get("/admin/benchmarks/runs/{run_id}/review-queue")
def review_queue(run_id: uuid.UUID, _: User = Depends(staff_user), db: Session = Depends(get_db)):
    return {"items": benchmarks.review_queue(db, run_id)}


class ReviewIn(BaseModel):
    adherence: int = Field(ge=1, le=5)
    quality: int = Field(ge=1, le=5)
    notes: str | None = Field(default=None, max_length=300)


@router.post("/admin/benchmarks/results/{result_id}/review")
def review(result_id: uuid.UUID, body: ReviewIn, reviewer: User = Depends(staff_user), db: Session = Depends(get_db)):
    benchmarks.review(db, reviewer, result_id, body.adherence, body.quality, body.notes)
    db.commit()
    return {"ok": True}


@router.post("/internal/cron/{task}")
def cron(task: str, x_cron_token: str = Header(default=""), db: Session = Depends(get_db)):
    from .. import scheduler

    token = get_settings().cron_token
    if not token:
        raise ApiError(503, "cron_not_configured", "set APP_CRON_TOKEN")
    if not hmac.compare_digest(x_cron_token, token):
        raise ApiError(401, "unauthorized", "bad cron token")
    if task not in scheduler.TASKS:
        raise ApiError(404, "not_found", "unknown task")
    r = scheduler.run_task(task)
    return {"task": task, "status": r.status, "result": r.result, "error": r.error}


@router.get("/admin/scheduler/runs")
def scheduler_runs(task: str | None = None, _: User = Depends(staff_user), db: Session = Depends(get_db)):
    q = select(ScheduledRun).order_by(ScheduledRun.id.desc()).limit(100)
    if task:
        q = q.where(ScheduledRun.task == task)
    return {"items": [{"task": r.task, "status": r.status, "result": r.result, "error": r.error,
                       "started_at": r.started_at.isoformat(),
                       "finished_at": r.finished_at.isoformat() if r.finished_at else None}
                      for r in db.execute(q).scalars()]}


# ---------------------------------------------------------------- V5 Phase D: learning policies

class PolicyIn(BaseModel):
    kind: str = "routing"
    config: dict = {}


@router.post("/admin/learning/policies", status_code=201)
def create_policy(body: PolicyIn, request: Request, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    from ..services import policies

    p = policies.create(db, admin, body.kind, body.config)
    audit(db, admin.id, "learning_policy.created", "learning_policy", str(p.id), {"version": p.version},
          ip=client_ip(request))
    db.commit()
    return _policy_out(p)


def _policy_out(p) -> dict:
    return {"id": str(p.id), "kind": p.kind, "version": p.version, "status": p.status, "rollout_pct": p.rollout_pct,
            "config": p.config, "evaluation": p.evaluation, "history": p.history}


@router.get("/admin/learning/policies")
def list_policies(_: User = Depends(staff_user), db: Session = Depends(get_db)):
    from ..models import LearningPolicyVersion

    rows = db.execute(select(LearningPolicyVersion).order_by(LearningPolicyVersion.created_at.desc())).scalars()
    return {"items": [_policy_out(p) for p in rows]}


class TransitionIn(BaseModel):
    to: str = Field(pattern=r"^(shadow|ab|active|rolled_back|retired)$")
    rollout_pct: int | None = Field(default=None, ge=0, le=100)
    note: str = Field(min_length=3, max_length=300)


@router.post("/admin/learning/policies/{policy_id}/transition")
def transition_policy(policy_id: uuid.UUID, body: TransitionIn, request: Request, admin: User = Depends(admin_user),
                      db: Session = Depends(get_db)):
    from ..services import policies

    p = policies.get(db, policy_id)
    policies.transition(db, admin, p, body.to, body.rollout_pct, body.note)
    audit(db, admin.id, f"learning_policy.{body.to}", "learning_policy", str(p.id),
          {"version": p.version, "rollout_pct": p.rollout_pct, "note": body.note}, ip=client_ip(request))
    db.commit()
    return _policy_out(p)


@router.get("/admin/learning/policies/{policy_id}/evaluation")
def policy_evaluation(policy_id: uuid.UUID, _: User = Depends(staff_user), db: Session = Depends(get_db)):
    from ..services import policies

    return policies.evaluate(db, policies.get(db, policy_id))


# ---------------------------------------------------------------- V5 Phase E: model registry governance

def _model_out(m) -> dict:
    return {"id": str(m.id), "provider_name": m.provider_name, "version": m.version, "base_model": m.base_model,
            "license": m.license, "status": m.status, "capabilities": m.capabilities,
            "usd_per_second": m.usd_per_second, "evaluation": m.evaluation, "approvals": m.approvals,
            "attestations": m.attestations, "model_card": m.model_card}


@router.post("/admin/model-registry", status_code=201)
def register_model(body: dict, request: Request, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    from ..services import model_registry

    m = model_registry.register(db, admin, body)
    audit(db, admin.id, "model_registry.registered", "model_registry", str(m.id),
          {"provider": m.provider_name, "version": m.version, "license": m.license}, ip=client_ip(request))
    db.commit()
    return _model_out(m)


class ModelEvalIn(BaseModel):
    benchmark_run_id: uuid.UUID
    min_reviews: int = Field(default=10, ge=1)
    max_quality_regression: float = Field(default=0.05, ge=0, le=1)


@router.post("/admin/model-registry/{model_id}/evaluation")
def evaluate_model(model_id: uuid.UUID, body: ModelEvalIn, admin: User = Depends(admin_user),
                   db: Session = Depends(get_db)):
    from ..services import model_registry

    m = model_registry.get(db, model_id)
    m.eval_run_id = body.benchmark_run_id
    m.evaluation = model_registry.evaluate(db, m, body.benchmark_run_id, body.min_reviews,
                                           body.max_quality_regression)
    db.commit()
    return _model_out(m)


class ModelDecisionIn(BaseModel):
    action: str = Field(pattern=r"^(approve|deploy|reject|retire)$")
    note: str = Field(min_length=3, max_length=300)


@router.post("/admin/model-registry/{model_id}/decision")
def model_decision(model_id: uuid.UUID, body: ModelDecisionIn, request: Request, admin: User = Depends(admin_user),
                   db: Session = Depends(get_db)):
    from ..services import model_registry

    m = model_registry.get(db, model_id)
    if body.action == "approve":
        model_registry.approve(db, admin, m, body.note)
    else:
        model_registry.set_status(m, {"deploy": "deployed", "reject": "rejected", "retire": "retired"}[body.action])
    audit(db, admin.id, f"model_registry.{body.action}", "model_registry", str(m.id),
          {"status": m.status, "note": body.note}, ip=client_ip(request))
    db.commit()
    return _model_out(m)
