"""V4 Stage E: model registry + adaptive routing (price, capability, kill switch, health, quality floor),
benchmark harness with a real worker and blind review, scheduled tasks with advisory locks and run log."""

import shutil
import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app import scheduler
from app.db import get_engine
from app.models import (
    CreditLedger,
    FeatureFlag,
    GenerationJob,
    GenerationOutput,
    JobKind,
    LedgerReason,
    ModelRun,
    ScheduledRun,
    StudioProject,
)
from app.services import credits

from .conftest import signup
from .test_creators import admin, uid
from .test_studio import enable as enable_studio
from .test_studio import plan, project

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "worker"))


def flag(db, key, value, enabled=True):
    f = db.get(FeatureFlag, key) or FeatureFlag(key=key)
    f.enabled, f.value = enabled, value
    db.add(f)
    db.commit()


PROVIDERS = {
    "cheap": {"capabilities": ["TEXT_TO_VIDEO"], "usd_per_second": 0.02},
    "pricey": {"capabilities": ["TEXT_TO_VIDEO", "CHARACTER_REFERENCE"], "usd_per_second": 0.10, "quality": 0.9},
    "nopriced": {"capabilities": ["TEXT_TO_VIDEO"]},
}


def runs(db, model, n, failed):
    job = db.execute(select(GenerationJob.id)).scalars().first()
    for i in range(n):
        db.add(ModelRun(job_id=job, attempt=i + 1, worker_id="w", model=model,
                        status="failed" if i < failed else "succeeded", started_at=datetime.now(UTC),
                        finished_at=datetime.now(UTC)))
    db.commit()


def test_routing_static_then_adaptive_rules(client, db):
    enable_studio(db)
    ah = admin(client, db)
    r = client.get("/admin/routing/preview", headers=ah).json()
    assert r["adaptive"] is False and r["provider"] == "mock_t2v"  # backward compatible
    flag(db, "routing", {"providers": PROVIDERS, "min_runs_for_health": 4, "max_failure_rate": 0.5})
    r = client.get("/admin/routing/preview", headers=ah).json()
    assert r["provider"] == "mock_t2v" and r["fallback"] == "cheap"  # mock costs $0 in dev config
    reasons = {c["provider"]: c["reasons"] for c in r["candidates"]}
    assert reasons["nopriced"] == ["unpriced"]
    flag(db, "model_disabled:mock_t2v", {})  # kill switch
    r = client.get("/admin/routing/preview?capabilities=TEXT_TO_VIDEO", headers=ah).json()
    assert r["provider"] == "cheap" and r["fallback"] == "pricey"
    assert client.get("/admin/routing/preview?capabilities=CHARACTER_REFERENCE", headers=ah).json()["provider"] \
        == "pricey"
    assert client.get("/admin/routing/preview?tier=premium", headers=ah).json()["provider"] == "pricey"
    # a provider whose recent failure rate is too high is suspended automatically
    h, _ = signup(client)
    pid = project(client, h)
    plan(client, h, pid)
    from app.models import JobStatus

    db.add(GenerationJob(id=uuid.uuid4(), user_id=uid(client, h), kind=JobKind.studio_shot, status=JobStatus.failed,
                         queue_class="free", idempotency_key=uuid.uuid4().hex, credit_cost=0, spec={},
                         preferred_model="cheap"))
    db.commit()
    runs(db, "cheap", 4, failed=3)
    r = client.get("/admin/routing/preview", headers=ah).json()
    cheap = next(c for c in r["candidates"] if c["provider"] == "cheap")
    assert cheap["reasons"] == ["unhealthy"] and cheap["failure_rate"] == 0.75 and r["provider"] == "pricey"
    # quality floor
    flag(db, "routing", {"providers": PROVIDERS, "min_quality": 0.95})
    flag(db, "model_disabled:mock_t2v", {}, enabled=False)
    r = client.get("/admin/routing/preview", headers=ah).json()
    assert "below_quality_floor" in next(c for c in r["candidates"] if c["provider"] == "pricey")["reasons"]
    # the studio estimate follows the router; nothing eligible -> render refused with a clear code
    flag(db, "model_disabled:mock_t2v", {})
    flag(db, "routing", {"providers": {"cheap": {"capabilities": []}}})
    est = client.post(f"/studio/projects/{pid}/estimate", headers=h, json={}).json()
    assert est["provider"] is None and any("No video provider" in x for x in est["limitations"])
    r = client.post(f"/studio/projects/{pid}/render", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                    json={"confirmed_credits": est["credits"]})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "no_eligible_provider"
    m = client.get("/admin/models", headers=ah).json()
    assert m["adaptive_routing"] is True and any(p["kill_switch"] for p in m["providers"])


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_benchmark_run_with_worker_and_blind_review(client, db, storage, monkeypatch):
    from fastapi.testclient import TestClient
    from worker import runner
    from worker.client import ApiClient
    from worker.registry import FACTORIES

    from app.main import app

    monkeypatch.setattr(runner, "download", lambda url, dest, max_bytes=0: (dest.write_bytes(
        storage.objects[url.split("memory://get/", 1)[1].split("?", 1)[0]][0]), dest)[1])
    monkeypatch.setattr(runner, "upload", lambda url, path, mime: storage.put(
        url.split("memory://put/", 1)[1], Path(path).read_bytes(), mime))
    adapters = {"mock_t2v": FACTORIES["mock_t2v"]()}
    wc = TestClient(app)
    wc.headers["Authorization"] = "Bearer test-worker-token"
    api = ApiClient.__new__(ApiClient)
    api.worker_id, api.http = "bench", wc

    enable_studio(db)
    ah = admin(client, db)
    cases = [{"key": "d1", "category": "dance", "prompt": "a dancer spins on a rooftop", "duration_s": 2},
             {"key": "c1", "category": "comedy", "prompt": "a cat slips on a banana peel", "duration_s": 2,
              "aspect_ratio": "16:9"}]
    s = client.post("/admin/benchmarks/sets", headers=ah, json={"name": "core", "rights_note": "internal prompts only",
                                                               "cases": cases}).json()
    assert s["version"] == 1
    flag(db, "studio", {"provider_capabilities": {"other_t2v": ["TEXT_TO_VIDEO"]},
                        "provider_usd_per_second": {"other_t2v": 0.05}})
    run = client.post("/admin/benchmarks/runs", headers=ah, json={"set_id": s["id"],
                                                                  "providers": ["mock_t2v", "other_t2v"]}).json()
    assert client.post("/admin/benchmarks/runs", headers=ah, json={"set_id": s["id"], "providers": ["nope"]}
                       ).json()["detail"]["code"] == "unknown_provider"
    n = 0
    while (payload := api.claim(["mock_t2v"], "test", "cpu")) is not None:
        wd = Path(tempfile.mkdtemp())
        try:
            runner.process(api, adapters, payload, wd)
        finally:
            shutil.rmtree(wd, ignore_errors=True)
        n += 1
    assert n == 2
    rep = client.get(f"/admin/benchmarks/runs/{run['id']}", headers=ah).json()
    assert rep["providers"]["mock_t2v"]["completed"] == 2 and rep["providers"]["mock_t2v"]["success_rate"] == 1.0
    assert rep["providers"]["other_t2v"]["pending"] == 2  # no worker for it: reported, not invented
    pairs = {(r["provider"], r["category"]) for r in rep["by_category"]}
    assert pairs >= {("mock_t2v", "dance"), ("mock_t2v", "comedy")}
    q = client.get(f"/admin/benchmarks/runs/{run['id']}/review-queue", headers=ah).json()["items"]
    assert len(q) == 2 and all("provider" not in x for x in q)  # blind
    for x in q:
        client.post(f"/admin/benchmarks/results/{x['result_id']}/review", headers=ah,
                    json={"adherence": 5, "quality": 3})
    assert client.post(f"/admin/benchmarks/results/{q[0]['result_id']}/review", headers=ah,
                       json={"adherence": 1, "quality": 1}).json()["detail"]["code"] == "already_reviewed"
    rep = client.get(f"/admin/benchmarks/runs/{run['id']}", headers=ah).json()
    assert rep["providers"]["mock_t2v"]["review_score"] == 0.75
    m = {p["provider"]: p for p in client.get("/admin/models", headers=ah).json()["providers"]}
    assert m["mock_t2v"]["quality"] == 0.75 and m["mock_t2v"]["quality_source"] == "benchmark"
    # benchmark jobs never touch user credits
    assert db.execute(select(CreditLedger).where(CreditLedger.reason == LedgerReason.generation_debit)).first() is None


def test_scheduler_tasks_lock_and_cron(client, db, storage, monkeypatch):
    h, _ = signup(client)
    u = uid(client, h)
    credits.apply(db, u, 40, LedgerReason.promo, f"promo:{uuid.uuid4()}",
                  expires_at=datetime.now(UTC) - timedelta(minutes=1))
    db.commit()
    r = scheduler.run_task("credit_expiry")
    assert r.status == "succeeded" and r.result == {"users": 1}
    assert credits.reconcile_user(db, u)["consistent"] and credits.balance(db, u) == 30
    # retention: outputs of studio projects deleted long ago are purged from storage
    p = StudioProject(user_id=u, title="old", deleted_at=datetime.now(UTC) - timedelta(days=30))
    db.add(p)
    db.flush()
    from app.models import JobStatus

    j = GenerationJob(id=uuid.uuid4(), user_id=u, kind=JobKind.studio_shot, status=JobStatus.completed,
                      queue_class="free", idempotency_key=uuid.uuid4().hex, credit_cost=0, spec={},
                      studio_project_id=p.id, preferred_model="mock_t2v")
    db.add(j)
    db.flush()
    storage.put("outputs/x/video.mp4", b"v", "video/mp4")
    db.add(GenerationOutput(job_id=j.id, video_key="outputs/x/video.mp4", width=1, height=1, duration_ms=1,
                            size_bytes=1, codec="h264", provenance={}))
    db.commit()
    assert scheduler.run_task("retention").result == {"outputs_purged": 1}
    assert storage.head("outputs/x/video.mp4") is None
    assert scheduler.run_task("settlements").result == {"skipped": True}
    assert scheduler.run_task("play_finalize_retry").result == {"skipped": True}
    assert scheduler.run_task("webhook_retry").status == "succeeded"
    # a second scheduler can't run the same task concurrently
    with get_engine().begin() as c:
        c.execute(text("select pg_advisory_xact_lock(:k)"), {"k": scheduler._lock_key("template_metrics")})
        assert scheduler.run_task("template_metrics").status == "skipped"
    assert scheduler.run_task("template_metrics").status == "succeeded"
    # cron endpoint: not configured -> 503, wrong token -> 401, ok -> runs
    from app.config import get_settings

    assert client.post("/internal/cron/credit_expiry").status_code == 503
    monkeypatch.setenv("APP_CRON_TOKEN", "cron-secret")
    get_settings.cache_clear()
    try:
        assert client.post("/internal/cron/credit_expiry", headers={"X-Cron-Token": "nope"}).status_code == 401
        assert client.post("/internal/cron/bogus", headers={"X-Cron-Token": "cron-secret"}).status_code == 404
        assert client.post("/internal/cron/credit_expiry", headers={"X-Cron-Token": "cron-secret"}
                           ).json()["status"] == "succeeded"
    finally:
        monkeypatch.delenv("APP_CRON_TOKEN")
        get_settings.cache_clear()
    assert scheduler.main(["credit_expiry"]) == 0
    ah = admin(client, db)
    items = client.get("/admin/scheduler/runs?task=credit_expiry", headers=ah).json()["items"]
    assert len(items) >= 3 and db.execute(select(ScheduledRun)).first() is not None
