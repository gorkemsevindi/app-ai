"""Provider benchmark harness (V4 Stage E / V5 evaluation gates).

- A benchmark set is a fixed list of rights-cleared prompts with categories (dance, comedy, dialogue, music,
  ads, history, multi_person, ...). It is frozen on its first run, so later runs are comparable.
- A run renders every case on every chosen provider as ordinary studio shot jobs (same queue, leases,
  moderation, cost telemetry) owned by the admin and charged 0 credits.
- Results report per provider and per category: success rate, p50/p95 latency, cost per success and the
  worker's QA metrics. Human review is blind: reviewers see the video and the prompt, never the provider.
- `provider_quality` (mean blind review score, 0..1) feeds the adaptive router's quality floor."""

from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import ApiError, not_found
from ..models import (
    BenchmarkCase,
    BenchmarkResult,
    BenchmarkRun,
    BenchmarkSet,
    GenerationJob,
    GenerationOutput,
    JobKind,
    JobStatus,
    ModelRun,
    StudioProject,
    User,
)


class CaseIn(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    category: str = Field(pattern=r"^[a-z_]{2,40}$")
    prompt: str = Field(min_length=5, max_length=1500)
    duration_s: int = Field(ge=1, le=20)
    aspect_ratio: str = Field(default="9:16", pattern=r"^(9:16|16:9|1:1)$")


def create_set(db: Session, admin: User, name: str, rights_note: str, cases: list[CaseIn]) -> BenchmarkSet:
    from . import moderation

    if len({c.key for c in cases}) != len(cases):
        raise ApiError(422, "bad_benchmark", "case keys must be unique")
    for c in cases:
        if not moderation.check_text(c.prompt).allowed:
            raise ApiError(422, "content_blocked", f"case {c.key} is not allowed")
    n = db.execute(select(func.coalesce(func.max(BenchmarkSet.version), 0)).where(BenchmarkSet.name == name)
                   ).scalar_one()
    s = BenchmarkSet(name=name, version=n + 1, rights_note=rights_note, created_by=admin.id)
    db.add(s)
    db.flush()
    for c in cases:
        db.add(BenchmarkCase(set_id=s.id, **c.model_dump()))
    db.flush()
    return s


def start_run(db: Session, admin: User, set_id: uuid.UUID, providers: list[str]) -> BenchmarkRun:
    from . import router, studio

    bset = db.get(BenchmarkSet, set_id)
    if bset is None:
        raise not_found("benchmark set")
    reg = router.registry(db)
    unknown = [p for p in providers if p not in reg]
    if unknown:
        raise ApiError(422, "unknown_provider", f"not in the registry: {unknown}")
    bset.frozen = True
    cases = db.execute(select(BenchmarkCase).where(BenchmarkCase.set_id == bset.id)
                       .order_by(BenchmarkCase.key)).scalars().all()
    proj = StudioProject(user_id=admin.id, title=f"benchmark {bset.name} v{bset.version}"[:120], status="rendering")
    db.add(proj)
    db.flush()
    run = BenchmarkRun(set_id=bset.id, providers=providers, project_id=proj.id, created_by=admin.id)
    db.add(run)
    db.flush()
    for p in providers:
        for c in cases:
            h = hashlib.sha256(f"{run.id}:{p}:{c.id}".encode()).hexdigest()
            shot = {"key": c.key, "duration_s": c.duration_s, "prompt": c.prompt, "camera": "", "characters": [],
                    "dialogue": [], "caption": None, "transition": "cut"}
            job = GenerationJob(
                id=uuid.uuid4(), user_id=admin.id, kind=JobKind.studio_shot, status=JobStatus.queued,
                queue_class="free", studio_project_id=proj.id, preferred_model=p, fallback_model=None,
                idempotency_key=f"bm:{h[:48]}", credit_cost=0, max_attempts=1, watermark=False,
                spec={"shot": shot, "content_hash": h, "benchmark_run_id": str(run.id),
                      "resolution": studio.RESOLUTIONS[c.aspect_ratio],
                      "inputs": {"prompt": c.prompt, "camera": "", "duration_s": c.duration_s, "style": "",
                                 "aspect_ratio": c.aspect_ratio, "quality": "standard", "provider": p,
                                 "characters": []}})
            db.add(job)
            db.flush()
            db.add(BenchmarkResult(run_id=run.id, case_id=c.id, provider=p, job_id=job.id))
    db.flush()
    return run


def _pct(v: list[float], q: float) -> float | None:
    if not v:
        return None
    v = sorted(v)
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 2)


def _score(r: BenchmarkResult) -> float | None:
    if r.adherence is None or r.quality is None:
        return None
    return round(((r.adherence + r.quality) / 2 - 1) / 4, 4)  # 1..5 -> 0..1


def report(db: Session, run_id: uuid.UUID) -> dict:
    run = db.get(BenchmarkRun, run_id)
    if run is None:
        raise not_found("benchmark run")
    rows = db.execute(select(BenchmarkResult, BenchmarkCase, GenerationJob)
                      .join(BenchmarkCase, BenchmarkCase.id == BenchmarkResult.case_id)
                      .join(GenerationJob, GenerationJob.id == BenchmarkResult.job_id)
                      .where(BenchmarkResult.run_id == run.id)).all()
    job_ids = [j.id for _, _, j in rows]
    costs = dict(db.execute(select(ModelRun.job_id, func.coalesce(func.sum(ModelRun.est_cost_usd), 0.0))
                            .where(ModelRun.job_id.in_(job_ids)).group_by(ModelRun.job_id)).all()) if job_ids else {}
    qa = {r.job_id: (r.metrics or {}).get("qa") for r in db.execute(
        select(ModelRun).where(ModelRun.job_id.in_(job_ids))).scalars()} if job_ids else {}

    def agg(items):
        done = [j for _, _, j in items if j.status == JobStatus.completed]
        terminal = [j for _, _, j in items if j.status in (JobStatus.completed, JobStatus.failed)]
        lat = [(j.finished_at - j.created_at).total_seconds() for j in done if j.finished_at]
        cost = sum(float(costs.get(j.id, 0.0)) for _, _, j in items)
        scores = [s for s in (_score(r) for r, _, _ in items) if s is not None]
        return {"cases": len(items), "completed": len(done), "failed": len(terminal) - len(done),
                "pending": len(items) - len(terminal),
                "success_rate": round(len(done) / len(terminal), 4) if terminal else None,
                "p50_latency_s": _pct(lat, 0.5), "p95_latency_s": _pct(lat, 0.95),
                "cost_usd": round(cost, 4), "cost_per_success_usd": round(cost / len(done), 4) if done else None,
                "review_score": round(sum(scores) / len(scores), 4) if scores else None, "reviewed": len(scores)}

    by_p, by_pc = defaultdict(list), defaultdict(list)
    for item in rows:
        by_p[item[0].provider].append(item)
        by_pc[(item[0].provider, item[1].category)].append(item)
    return {"run_id": str(run.id), "providers": {p: agg(v) for p, v in sorted(by_p.items())},
            "by_category": [{"provider": p, "category": c, **agg(v)} for (p, c), v in sorted(by_pc.items())],
            "qa_samples": {str(k): v for k, v in qa.items() if v}}


def review_queue(db: Session, run_id: uuid.UUID, limit: int = 20) -> list[dict]:
    """Unreviewed finished results, in a provider-blind order (hash of the result id)."""
    from .storage import get_storage

    rows = db.execute(select(BenchmarkResult, BenchmarkCase, GenerationOutput)
                      .join(BenchmarkCase, BenchmarkCase.id == BenchmarkResult.case_id)
                      .join(GenerationOutput, GenerationOutput.job_id == BenchmarkResult.job_id)
                      .where(BenchmarkResult.run_id == run_id, BenchmarkResult.reviewed_at.is_(None))).all()
    rows.sort(key=lambda r: hashlib.sha256(str(r[0].id).encode()).hexdigest())
    st = get_storage()
    return [{"result_id": str(r.id), "category": c.category, "prompt": c.prompt,
             "video_url": st.presign_get(o.video_key)} for r, c, o in rows[:limit]]


def review(db: Session, reviewer: User, result_id: uuid.UUID, adherence: int, quality: int, notes: str | None) -> None:
    r = db.get(BenchmarkResult, result_id)
    if r is None:
        raise not_found("result")
    if r.reviewed_at is not None:
        raise ApiError(409, "already_reviewed", "this result was already reviewed")
    r.adherence, r.quality, r.notes = adherence, quality, notes
    r.reviewer_id, r.reviewed_at = reviewer.id, datetime.now(UTC)


def provider_quality(db: Session, days: int = 90) -> dict[str, float]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = db.execute(select(BenchmarkResult.provider, BenchmarkResult.adherence, BenchmarkResult.quality)
                      .where(BenchmarkResult.reviewed_at >= since)).all()
    acc: dict[str, list[float]] = defaultdict(list)
    for p, a, q in rows:
        if a is not None and q is not None:
            acc[p].append(((a + q) / 2 - 1) / 4)
    return {p: round(sum(v) / len(v), 4) for p, v in acc.items() if v}
