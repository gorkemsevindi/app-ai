"""V5 Phase E: governance gate for fine-tuned / self-hosted models — licence, data rights, no user content,
third-party outputs only under contract, model card, blind-reviewed non-regressing evaluation, two-person approval,
deploy into the router, retire."""

import uuid

from app.models import BenchmarkCase, BenchmarkResult, BenchmarkRun, BenchmarkSet
from app.services import router

from .conftest import signup
from .test_creators import admin
from .test_studio import enable as enable_studio

CARD = {"intended_use": "short vertical clips", "limitations": "no faces of minors", "training_data":
        "licensed stock dataset DS-1", "evaluation": "benchmark core v1", "ethical_considerations": "consent-only"}
GOOD = {"provider_name": "ours_t2v", "base_model": "open-weights-video-1", "license": "Apache-2.0",
        "attestations": {"license_verified": True, "commercial_use_allowed": True, "license_evidence": "LICENSE@v1",
                         "dataset_ref": "DS-1 contract 2026-03", "dataset_source": "licensed_external",
                         "dataset_rights_cleared": True},
        "model_card": CARD, "capabilities": ["text_to_video"], "usd_per_second": 0.005}


def reviewed_run(db, admin_id, provider, scores):
    s = BenchmarkSet(name=f"s{uuid.uuid4().hex[:6]}", rights_note="internal prompts", created_by=admin_id)
    db.add(s)
    db.flush()
    run = BenchmarkRun(set_id=s.id, providers=[provider], project_id=uuid.uuid4(), created_by=admin_id)
    db.add(run)
    db.flush()
    from datetime import UTC, datetime

    for i, (a, q) in enumerate(scores):
        c = BenchmarkCase(set_id=s.id, key=f"c{i}", category="dance", prompt="a dancer", duration_s=4)
        db.add(c)
        db.flush()
        db.add(BenchmarkResult(run_id=run.id, case_id=c.id, provider=provider, job_id=uuid.uuid4(), adherence=a,
                               quality=q, reviewer_id=admin_id, reviewed_at=datetime.now(UTC)))
    db.commit()
    return run.id


def test_registration_gates(client, db):
    ah = admin(client, db)
    bad = {**GOOD, "attestations": {**GOOD["attestations"], "commercial_use_allowed": False,
                                    "includes_user_content": True, "includes_third_party_model_outputs": True},
           "model_card": {**CARD, "limitations": ""}}
    r = client.post("/admin/model-registry", headers=ah, json=bad).json()["detail"]
    assert r["code"] == "governance_failed" and set(r["problems"]) == {
        "license_not_commercial", "user_content_not_permitted", "third_party_outputs_without_contract",
        "model_card_incomplete:limitations"}
    assert client.post("/admin/model-registry", headers=ah, json={"provider_name": "x"}).json()["detail"]["code"] \
        == "bad_registration"
    assert client.post("/admin/model-registry", headers=signup(client)[0], json=GOOD).status_code == 403


def test_evaluation_two_person_approval_deploy_retire(client, db):
    enable_studio(db)
    a1 = admin(client, db)
    a2 = admin(client, db)
    from .test_creators import uid

    m = client.post("/admin/model-registry", headers=a1, json=GOOD).json()
    assert m["status"] == "registered" and m["capabilities"] == ["TEXT_TO_VIDEO"]
    d = lambda h, action: client.post(f"/admin/model-registry/{m['id']}/decision", headers=h,  # noqa: E731
                                      json={"action": action, "note": "review ok"})
    assert d(a1, "approve").json()["detail"]["code"] == "evaluation_required"
    assert d(a1, "deploy").json()["detail"]["code"] == "bad_transition"  # never deploys itself
    # an existing provider is clearly better -> regression -> not passed
    reviewed_run(db, uid(client, a1), "mock_t2v", [(5, 5)] * 3)
    weak = reviewed_run(db, uid(client, a1), "ours_t2v", [(2, 2)] * 3)
    ev = client.post(f"/admin/model-registry/{m['id']}/evaluation", headers=a1,
                     json={"benchmark_run_id": str(weak), "min_reviews": 3}).json()["evaluation"]
    assert ev["passed"] is False and ev["baseline_quality"] == 1.0
    good = reviewed_run(db, uid(client, a1), "ours_t2v", [(5, 5), (5, 4), (5, 5)])
    ev = client.post(f"/admin/model-registry/{m['id']}/evaluation", headers=a1,
                     json={"benchmark_run_id": str(good), "min_reviews": 3, "max_quality_regression": 0.1}
                     ).json()["evaluation"]
    assert ev["passed"] is True
    assert d(a1, "approve").json()["status"] == "registered"  # one approval is not enough
    assert d(a1, "approve").json()["detail"]["code"] == "already_approved"
    assert d(a2, "approve").json()["status"] == "approved"
    assert "ours_t2v" not in router.registry(db)  # approved is not deployed
    assert d(a1, "deploy").json()["status"] == "deployed"
    reg = router.registry(db)
    assert reg["ours_t2v"]["capabilities"] == ["TEXT_TO_VIDEO"] and reg["ours_t2v"]["usd_per_second"] == 0.005
    models = {p["provider"]: p for p in client.get("/admin/models", headers=a1).json()["providers"]}
    assert "ours_t2v" in models
    assert d(a2, "retire").json()["status"] == "retired"
    assert "ours_t2v" not in router.registry(db)
