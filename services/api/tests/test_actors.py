"""V4 Stage D: licensed AI actor marketplace — legal gate, own-likeness listings, moderation, territory,
confirmed licence purchase, separate actor earnings, prohibited contexts, checks at dispatch and publication,
attribution, withdrawal with pro-rata refunds and clawback."""

import uuid

from sqlalchemy import select

from app.models import (
    ActorLicense,
    CreatorEarning,
    GenerationJob,
    JobKind,
    JobStatus,
    LedgerReason,
    LicenseUsageEvent,
    StudioProject,
    StudioShotRender,
)
from app.services import credits, studio

from .conftest import WORKER_H, ready_profile
from .test_creators import admin, buy, creator, enable, policy, uid
from .test_studio import enable as enable_studio
from .test_studio import plan, project, render

TERMS = {"duration_days": 30, "price_credits": 100, "prohibited_contexts": ["alcohol"], "territories": ["*"]}


def setup_market(client, db, legal=True):
    enable(db)
    enable(db, "actor_marketplace", {"legal_review_ref": "LR-2026-001"} if legal else {})
    enable_studio(db)


def listed_actor(client, db, storage, ah, terms=None):
    oh = creator(client, db)
    prof = ready_profile(client, oh, storage)
    r = client.post("/actors/listings", headers=oh, json={"identity_profile_id": prof, "display_name": "Deniz",
                                                          "terms": terms or TERMS, "attest_own_likeness": True,
                                                          "accept_licensing_terms": True})
    assert r.status_code == 201, r.text
    lid = r.json()["id"]
    assert client.post(f"/admin/actors/{lid}/review", headers=ah, json={"status": "active", "note": "id ok"}
                       ).status_code == 200
    return oh, lid


def licensed(client, db, storage, ah, terms=None):
    oh, lid = listed_actor(client, db, storage, ah, terms)
    h = creator(client, db)  # licensee
    buy(db, uid(client, h), 300)
    q = client.post(f"/actors/{lid}/license-quote", headers=h).json()
    r = client.post(f"/actors/{lid}/license", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                    json={"confirmed_credits": q["price_credits"]})
    assert r.status_code == 201, r.text
    return oh, lid, h, r.json()


def test_legal_gate_and_listing_rules(client, db, storage):
    setup_market(client, db, legal=False)
    ah = admin(client, db)
    oh = creator(client, db)
    prof = ready_profile(client, oh, storage)
    body = {"identity_profile_id": prof, "display_name": "Deniz", "terms": TERMS, "attest_own_likeness": True,
            "accept_licensing_terms": True}
    assert client.post("/actors/listings", headers=oh, json=body).status_code == 403  # flag on, no legal review
    setup_market(client, db)
    assert client.post("/actors/listings", headers=oh, json={**body, "attest_own_likeness": False}
                       ).json()["detail"]["code"] == "consent_required"
    other = creator(client, db)
    assert client.post("/actors/listings", headers=other, json=body).status_code == 404  # not their face
    assert client.post("/actors/listings", headers=oh, json={**body, "terms": {**TERMS, "revocable": False}}
                       ).json()["detail"]["code"] == "bad_terms"
    r = client.post("/actors/listings", headers=oh, json=body).json()
    assert r["status"] == "pending_review" and r["prohibited_contexts"] == ["adult", "alcohol", "political"]
    assert client.get("/actors").json()["items"] == []  # not live before moderation
    client.post(f"/admin/actors/{r['id']}/review", headers=ah, json={"status": "active", "note": "verified"})
    assert [x["id"] for x in client.get("/actors").json()["items"]] == [r["id"]]


def test_purchase_confirm_territory_and_actor_earnings(client, db, storage):
    setup_market(client, db)
    ah = admin(client, db)
    policy(client, ah, actor_share_rate=0.7)
    oh, lid = listed_actor(client, db, storage, ah)
    assert client.post(f"/actors/{lid}/license", headers={**oh, "Idempotency-Key": uuid.uuid4().hex},
                       json={"confirmed_credits": 100}).json()["detail"]["code"] == "own_listing"
    h = creator(client, db)
    buy(db, uid(client, h), 300)
    key = uuid.uuid4().hex
    r = client.post(f"/actors/{lid}/license", headers={**h, "Idempotency-Key": key}, json={})
    assert r.json()["detail"]["code"] == "confirmation_required"
    lic = client.post(f"/actors/{lid}/license", headers={**h, "Idempotency-Key": key},
                      json={"confirmed_credits": 100}).json()
    again = client.post(f"/actors/{lid}/license", headers={**h, "Idempotency-Key": key},
                        json={"confirmed_credits": 100}).json()
    assert again["id"] == lic["id"] and client.get("/credits", headers=h).json()["balance"] == 330 - 100
    e = db.execute(select(CreatorEarning).where(CreatorEarning.kind == "actor_license")).scalar_one()
    # 100 credits: 30 promo (not revenue) + 70 purchased -> 70 * $0.01 * 70%
    assert e.creator_id == uid(client, oh) and e.amount_micros == 490_000 and e.license_id is not None
    # territory-limited actor
    _, lid2 = listed_actor(client, db, storage, ah, {**TERMS, "territories": ["TR"]})
    assert client.post(f"/actors/{lid2}/license", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                       json={"confirmed_credits": 100}).json()["detail"]["code"] == "territory_not_licensed"


def test_studio_use_contexts_dispatch_publish_and_withdrawal(client, db, storage):
    setup_market(client, db)
    ah = admin(client, db)
    policy(client, ah, actor_share_rate=0.5)
    oh, lid, h, lic = licensed(client, db, storage, ah)
    ch = client.post("/studio/characters", headers=h, json={"name": "Deniz", "actor_license_id": lic["id"]}).json()
    assert ch["usable"] is True and ch["actor_license_id"] == lic["id"]
    # someone else can't use this licence
    h3 = creator(client, db)
    assert client.post("/studio/characters", headers=h3, json={"name": "x", "actor_license_id": lic["id"]}
                       ).json()["detail"]["reason"] == "license_missing"
    pid = project(client, h)
    r = client.post(f"/studio/projects/{pid}/storyboard", headers=h, json={
        "brief": "Deniz opens a cold beer on the beach at sunset.", "character_ids": [ch["id"]]})
    assert r.status_code == 502 and r.json()["detail"]["reason"] == "license_terms_violation"
    plan(client, h, pid, character_ids=[ch["id"]])
    assert render(client, h, pid, 24).status_code == 202
    # publication check + attribution: simulate rendered shots, then build the assembly
    p = db.get(StudioProject, uuid.UUID(pid))
    for r_ in db.execute(select(StudioShotRender).where(StudioShotRender.project_id == p.id)).scalars():
        r_.status, r_.video_key = "ready", f"k/{r_.id}.mp4"
    for j in db.execute(select(GenerationJob).where(GenerationJob.kind == JobKind.studio_shot)).scalars():
        j.status = JobStatus.completed
    asm = studio.maybe_assemble(db, p)
    db.commit()
    assert asm.spec["captions"][-1]["text"] == "Licensed AI actor: Deniz"
    # the actor withdraws consent: licence ends, pro-rata refund, earning clawed back, usage refused
    before = client.get("/credits", headers=h).json()["balance"]
    w = client.delete(f"/actors/listings/{lid}", headers=oh).json()
    assert w["licences_ended"] == 1
    assert client.get("/credits", headers=h).json()["balance"] >= before + 99  # ~all 30 days unused
    claws = db.execute(select(CreatorEarning).where(CreatorEarning.kind == "clawback")).scalars().all()
    assert len(claws) == 1 and claws[0].amount_micros < 0
    assert db.get(ActorLicense, uuid.UUID(lic["id"])).status == "revoked"
    assert client.get("/studio/characters", headers=h).json()["items"][0]["usable"] is False
    c = client.post("/internal/worker/claim", json={"worker_id": "w", "models": ["studio_assembler"]},
                    headers=WORKER_H)
    assert c.status_code == 204
    ev = db.execute(select(LicenseUsageEvent).order_by(LicenseUsageEvent.id.desc())).scalars().first()
    assert ev.stage == "publish" and ev.allowed is False and ev.reason == "license_revoked"
    db.expire_all()
    assert db.get(GenerationJob, asm.id).error_code == "license_invalid"
    assert client.get("/actors").json()["items"] == []


def test_moderation_takedown_ends_licences(client, db, storage):
    setup_market(client, db)
    ah = admin(client, db)
    _, lid, h, lic = licensed(client, db, storage, ah)
    r = client.post(f"/admin/actors/{lid}/review", headers=ah, json={"status": "removed", "note": "impersonation"})
    assert r.json()["status"] == "removed"
    assert db.get(ActorLicense, uuid.UUID(lic["id"])).status == "revoked"
    assert client.post(f"/admin/actors/{lid}/review", headers=ah, json={"status": "active", "note": "oops"}
                       ).json()["detail"]["code"] == "listing_removed"
    assert credits.reconcile_user(db, uid(client, h))["consistent"]
    assert LedgerReason.license_fee.value == "license_fee"
