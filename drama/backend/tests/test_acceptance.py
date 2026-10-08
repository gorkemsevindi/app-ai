"""Spec §11 acceptance criteria, executed against the real API and the real local pipeline."""

import json
from datetime import timedelta

from sqlalchemy import func, select

from dramaapp.config import get_settings
from dramaapp.models import (
    Episode,
    GenerationJob,
    LedgerTransaction,
    RevenueAllocation,
    RightsGrant,
    now,
)
from dramaapp.pipeline import orchestrator as orch
from dramaapp.security import hmac_hex
from dramaapp.seed import seed
from dramaapp.storage import local_path
from dramaapp.worker import run_once

from .conftest import signup


def _project(client, h, n=3, lang="tr"):
    r = client.post("/studio/projects", headers=h, json={"genre": "drama", "language": lang, "episode_count": n,
                                                         "character_count": 3, "seed": 11})
    assert r.status_code == 201, r.text
    return r.json()


def test_creator_makes_three_coherent_episodes(client, db):
    """3 × ~60 s episodes, consistent (locked) characters, speech, timed captions, QC passed, cost reported."""
    h, _ = signup(client, creator=True)
    p = _project(client, h)
    for ch in p["characters"]:
        assert client.post(f"/studio/characters/{ch['id']}/lock", headers=h).json()["locked"]
    hashes = {}
    for e in p["episodes"]:
        est = client.post(f"/studio/episodes/{e['id']}/estimate", headers=h, json={"quality": "preview"}).json()
        assert est["credits"] > 0 and est["shadow_estimates_usd"]["premium_route"]["total_usd"] > 0
        r = client.post("/studio/generations", headers=h, json={"episode_id": e["id"], "quality": "preview"})
        assert r.status_code == 202, r.text
        assert run_once("test")
        job = client.get(f"/studio/jobs/{r.json()['id']}", headers=h).json()
        assert job["status"] == "succeeded", (job["error_code"], job["error_detail"],
                                              [(s["name"], s["status"]) for s in job["steps"]])
        ep = db.get(Episode, e["id"])
        db.refresh(ep)
        checks = {c["id"]: c for c in ep.qc_report["checks"]}
        assert all(c["ok"] for c in checks.values()), checks
        assert 45 <= ep.duration_s <= 84
        assert checks["subtitle_drift"]["value"] <= 0.15
        for k, v in ep.provenance["characters"].items():
            hashes.setdefault(k, set()).add(v["dna_hash"])
        vtt = local_path(f"episodes/{ep.id}/{job['id']}/captions.vtt").read_text()
        script = client.get(f"/studio/episodes/{ep.id}/script", headers=h).json()["content"]
        n_lines = sum(len(sc["lines"]) for sc in script["scenes"])
        assert vtt.count("-->") >= n_lines and "<v " in vtt
        assert "tts:espeak_local/espeak-ng@1.51" in ep.provenance["models"]
        assert job["spent_credits"] == job["estimate_credits"]
    assert all(len(v) == 1 for v in hashes.values()), "character identity drifted between episodes"
    cr = client.get("/creator/earnings", headers=h).json()
    assert cr["credits_balance"] < get_settings().signup_bonus_credits


def test_viewer_five_free_then_buys_episode_six_and_allocation_posted_once(client, db):
    info = seed(fixture=True, episodes=6)
    sid = info["series_id"]
    vh, viewer = signup(client)
    detail = client.get(f"/series/{sid}", headers=vh).json()
    eps = detail["episodes"]
    assert [e["unlocked"] for e in eps] == [True] * 5 + [False]
    for e in eps[:5]:
        pb = client.get(f"/episodes/{e['id']}/playback", headers=vh)
        assert pb.status_code == 200 and pb.json()["hls_url"]
        r = client.post("/watch-events", headers=vh, json={"event_id": f"ev-{e['id']}", "episode_id": e["id"],
                                                           "position_s": 12, "watched_s": 12, "completed": True})
        assert r.json()["qualified"]
    sixth = eps[5]["id"]
    assert client.get(f"/episodes/{sixth}/playback", headers=vh).status_code == 402
    # signed HLS: master -> variant -> segment all reachable with rewritten signatures
    master = client.get(client.get(f"/episodes/{eps[0]['id']}/playback", headers=vh).json()["hls_url"]
                        .replace(get_settings().public_base_url, ""))
    assert master.status_code == 200 and "sig=" in master.text
    offer = client.get(f"/episodes/{sixth}/offer", headers=vh).json()
    prod = next(p for p in offer["products"] if p["type"] == "episode")
    chk = client.post("/purchases/sandbox/checkout", headers=vh, json={"product_id": prod["product_id"]}).json()
    v1 = client.post("/purchases/verify", headers=vh, json={"store": "sandbox", **chk})
    v2 = client.post("/purchases/verify", headers=vh, json={"store": "sandbox", **chk})  # replay
    assert v1.status_code == 200 and v1.json()["id"] == v2.json()["id"]
    assert client.get(f"/episodes/{sixth}/playback", headers=vh).status_code == 200
    pid = v1.json()["id"]
    assert db.scalar(select(func.count()).select_from(RevenueAllocation).where(RevenueAllocation.purchase_id == pid)) == 1
    alloc = db.scalar(select(RevenueAllocation).where(RevenueAllocation.purchase_id == pid))
    assert alloc.net_minor == alloc.gross_minor - alloc.tax_minor - alloc.store_fee_minor
    assert alloc.creator_minor == alloc.net_minor * 6000 // 10000
    tampered = {**chk, "receipt": {**chk["receipt"], "price_minor": 1}}
    assert client.post("/purchases/verify", headers=vh, json={"store": "sandbox", **tampered}).status_code == 400
    assert client.post("/purchases/verify", headers=vh,
                       json={"store": "apple", "receipt": {}}).json()["error"]["code"] == "store.not_configured"
    # refund via signed store notification (dedup by notification id) revokes access and reverses allocation
    body = json.dumps({"notification_id": "n-1", "type": "REFUND", "transaction_id": chk["receipt"]["transaction_id"]})
    sig = hmac_hex(get_settings().sandbox_store_secret, body.encode())
    for _ in range(2):
        assert client.post("/webhooks/sandbox-store", content=body, headers={"x-signature": sig}).status_code == 200
    assert client.get(f"/episodes/{sixth}/playback", headers=vh).status_code == 402
    assert db.scalar(select(func.count()).select_from(LedgerTransaction)
                     .where(LedgerTransaction.idempotency_key == f"refund:{pid}")) == 1
    ah = {"Authorization": f"Bearer {client.post('/auth/login', json={'email': 'admin@demo.example.com', 'password': 'admin-demo-pass'}).json()['access_token']}"}
    assert client.get("/admin/ledger/trial-balance", headers=ah).json()["balanced"]


def test_creator_payout_flow(client, db):
    info = seed(fixture=True, episodes=6)
    ch = {"Authorization": f"Bearer {client.post('/auth/login', json={'email': 'creator@demo.example.com', 'password': 'creator-demo-pass'}).json()['access_token']}"}
    ah = {"Authorization": f"Bearer {client.post('/auth/login', json={'email': 'admin@demo.example.com', 'password': 'admin-demo-pass'}).json()['access_token']}"}
    sixth = client.get(f"/series/{info['series_id']}").json()["episodes"][5]["id"]
    for _ in range(10):  # 10 viewers buy the season → enough for the minimum payout
        vh, _u = signup(client)
        prod = next(p for p in client.get(f"/episodes/{sixth}/offer", headers=vh).json()["products"]
                    if p["type"] == "season")
        chk = client.post("/purchases/sandbox/checkout", headers=vh, json={"product_id": prod["product_id"]}).json()
        assert client.post("/purchases/verify", headers=vh, json={"store": "sandbox", **chk}).status_code == 200
    e = client.get("/creator/earnings", headers=ch).json()
    assert e["pending_minor"] > 0 and e["available_minor"] == 0
    for a in db.scalars(select(RevenueAllocation)):
        a.available_at = now() - timedelta(minutes=1)
    db.commit()
    client.post("/admin/ledger/release-holds", headers=ah)
    e = client.get("/creator/earnings", headers=ch).json()
    assert e["available_minor"] >= get_settings().min_payout_minor
    r = client.post("/creator/payouts", headers=ch, json={"amount_minor": get_settings().min_payout_minor})
    assert r.json()["error"]["code"] == "payout.kyc_required"
    me = client.get("/me", headers=ch).json()
    client.post(f"/admin/creators/{me['id']}/kyc", headers=ah, json={"kyc_status": "verified", "tax_form_status": "verified"})
    r = client.post("/creator/payouts", headers=ch, json={"amount_minor": get_settings().min_payout_minor})
    assert r.status_code == 201, r.text
    s = client.post(f"/admin/payouts/{r.json()['id']}/settle", headers=ah).json()
    assert s["status"] == "settled"
    assert client.get("/admin/ledger/trial-balance", headers=ah).json()["balanced"]


def test_unauthorized_face_swap_and_likeness_blocked_then_revocation_propagates(client, db):
    h, user = signup(client, creator=True)
    p = _project(client, h, n=1)
    cid = p["characters"][0]["id"]
    r = client.post("/studio/face-swap", headers=h, json={"episode_id": p["episodes"][0]["id"], "character_id": cid,
                                                          "source_asset_id": "x"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "rights.consent_required"
    g = client.post("/rights/grants", headers=h, json={"subject_name": "Me", "grant_type": "self",
                                                       "consent_accepted": True}).json()
    r = client.post(f"/studio/characters/{cid}/likeness", headers=h, json={"rights_grant_id": g["id"]})
    assert r.status_code == 403  # still pending human review
    assert client.post("/rights/grants", headers=h, json={"subject_name": "Famous Actor", "grant_type": "licensed_actor",
                                                          "consent_accepted": True}).status_code == 400
    ah, _ = signup(client, role="admin", db=db)
    client.post(f"/admin/rights/grants/{g['id']}/review?approve=true", headers=ah)
    assert client.post(f"/studio/characters/{cid}/likeness", headers=h,
                       json={"rights_grant_id": g["id"]}).status_code == 200
    r = client.post("/studio/face-swap", headers=h, json={"episode_id": p["episodes"][0]["id"], "character_id": cid,
                                                          "source_asset_id": "x"})
    assert r.json()["error"]["code"] == "provider.unavailable"  # consent ok, provider honestly not contracted
    client.post(f"/rights/grants/{g['id']}/revoke", headers=h)
    assert db.get(RightsGrant, g["id"]).revoked_at is not None
    r = client.post("/studio/generations", headers=h, json={"episode_id": p["episodes"][0]["id"]})
    assert r.json()["error"]["code"] == "rights.character_blocked"


def test_interrupted_job_resumes_without_double_billing(client, db):
    h, user = signup(client, creator=True)
    p = _project(client, h, n=1, lang="en")
    eid = p["episodes"][0]["id"]
    jid = client.post("/studio/generations", headers=h, json={"episode_id": eid}).json()["id"]
    job = orch.claim_next(db, "worker-a")
    assert job.id == jid
    orch.run_job(db, job, stop_after="performance")  # worker "crashes" after the expensive step
    db.refresh(job)
    assert job.status == "running" and job.spent_credits > 0
    spent = job.spent_credits
    assert orch.claim_next(db, "worker-b") is None  # lease still held
    job.lease_expires_at = now() - timedelta(seconds=1)  # lease expires
    db.commit()
    job2 = orch.claim_next(db, "worker-b")
    assert job2 and job2.id == jid and job2.lease_owner == "worker-b"
    orch.run_job(db, job2)
    db.refresh(job2)
    assert job2.status == "succeeded"
    perf = next(s for s in job2.steps if s.name == "performance")
    assert perf.attempts == 1  # not re-rendered
    assert job2.spent_credits == spent  # nothing re-billed
    n = db.scalar(select(func.count()).select_from(LedgerTransaction)
                  .where(LedgerTransaction.idempotency_key.like(f"job:{jid}:step:%")))
    assert n == 2


def test_failed_job_refunds_and_retry_cap(client, db):
    h, user = signup(client, creator=True)
    p = _project(client, h, n=1, lang="en")
    jid = client.post("/studio/generations", headers=h, json={"episode_id": p["episodes"][0]["id"]}).json()["id"]
    bal0 = client.get("/me", headers=h).json()["credits"]

    def boom(step):
        if step == "mix":
            raise RuntimeError("simulated provider outage")
    for _ in range(get_settings().max_render_retries + 1):
        job = orch.claim_next(db, "w")
        orch.run_job(db, job, fail_hook=boom)
    job = db.get(GenerationJob, jid)
    db.refresh(job)
    assert job.status == "failed" and job.error_code == "step.exception"
    assert client.get("/me", headers=h).json()["credits"] == bal0  # refunded


def test_spend_caps_enforced(client, db):
    h, user = signup(client, creator=True)
    p = _project(client, h, n=1)
    eid = p["episodes"][0]["id"]
    r = client.post("/studio/generations", headers=h, json={"episode_id": eid, "max_spend_credits": 1})
    assert r.json()["error"]["code"] == "credits.max_spend_too_low"
    s = get_settings()
    old = s.daily_spend_cap_credits
    s.daily_spend_cap_credits = 3
    try:
        r = client.post("/studio/generations", headers=h, json={"episode_id": eid})
        assert r.status_code == 429 and r.json()["error"]["code"] == "credits.daily_cap"
    finally:
        s.daily_spend_cap_credits = old
    vh, _ = signup(client)  # viewers can't use the studio
    assert client.post("/studio/projects", headers=vh, json={}).status_code == 403


def test_publish_gate_and_takedown(client, db):
    h, user = signup(client, creator=True)
    p = _project(client, h, n=1)
    eid = p["episodes"][0]["id"]
    assert client.post(f"/studio/episodes/{eid}/submit", headers=h).json()["error"]["code"] == "episode.not_rendered"
    from dramaapp.seed import _fixture_media
    ep = db.get(Episode, eid)
    _fixture_media(db, ep, user["id"])
    db.commit()
    assert client.post(f"/studio/episodes/{eid}/submit", headers=h).json()["status"] == "in_review"
    assert client.get(f"/episodes/{eid}/playback").status_code == 404  # not public until approved
    ah, _ = signup(client, role="admin", db=db)
    assert client.post(f"/admin/episodes/{eid}/decision", headers=ah, json={"approve": True}).json()["status"] == "published"
    assert client.get(f"/episodes/{eid}/playback").status_code == 200
    rep = client.post("/reports", json={"target_type": "episode", "target_id": eid, "reason": "impersonation"}).json()
    q = client.get("/admin/moderation", headers=ah).json()
    assert q[0]["priority"] <= 5
    client.post(f"/admin/moderation/{rep['case_id']}", headers=ah, json={"action": "takedown"})
    assert client.get(f"/episodes/{eid}/playback").status_code == 404


def test_feed_tabs_and_search(client, db):
    info = seed(fixture=True, episodes=6)
    vh, _ = signup(client)
    for tab in ("for_you", "trending", "following", "new"):
        r = client.get(f"/feed?tab={tab}", headers=vh)
        assert r.status_code == 200, r.text
    items = client.get("/feed").json()["items"]
    assert any(i["id"] == info["series_id"] for i in items)
    assert all("rank_score" in i for i in items)
    client.post(f"/series/{info['series_id']}/follow?on=true", headers=vh)
    assert [i["id"] for i in client.get("/feed?tab=following", headers=vh).json()["items"]] == [info["series_id"]]
    title = next(i["title"] for i in items if i["id"] == info["series_id"])
    assert client.get(f"/feed?q={title[:4]}").json()["items"]
