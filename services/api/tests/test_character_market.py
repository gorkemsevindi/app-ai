"""V6 Phase C: AI Casting Director, Character Marketplace (listings, rights review, licences/grants) and Creator
Royalty (usage priced in the quote, settled exactly once, waterfall, clawback, takedown, reports)."""

import uuid

from sqlalchemy import select

from app.models import (
    Character,
    CharacterAsset,
    CharacterUsageEvent,
    CreatorEarning,
    GenerationJob,
    JobKind,
    JobStatus,
)
from app.services import character_market, characters, creators

from .conftest import WORKER_H, signup
from .test_characters import DESC, SPEC, bal, enable_chars, fund, locked_character, pay, uid
from .test_creators import admin, creator, policy
from .test_creators import enable as enable_flag
from .test_studio import enable as enable_studio
from .test_studio import project

APPROVE = {"decision": "approve", "note": "rights + quality ok"}
TERMS = {"commercial_use": True, "allowed_categories": ["entertainment", "social"], "duration_days": 30,
         "prohibited_contexts": ["alcohol"], "price": {"per_scene_credits": 5, "per_second_credits": 1}}


def base(db):
    enable_chars(db)
    enable_flag(db)  # creator marketplace (creator profiles)


def market(db):
    enable_flag(db, "character_marketplace", {"legal_review_ref": "LEGAL-2026-10"})


def listed(client, db, drain, ah, handle_c, handle="mert", spec=None, terms=None):
    """A creator (profile @handle_c) with a locked, approved public character."""
    h = creator(client, db, handle_c)
    fund(client, db, h)
    ch = locked_character(client, db, h, drain, handle, spec)
    r = client.post(f"/characters/{ch['id']}/listing", headers=h, json={"visibility": "public",
                                                                       "terms": terms or TERMS})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "pending_review"
    r = client.post(f"/admin/characters/{ch['id']}/review", headers=ah, json=APPROVE)
    assert r.json().get("status") == "active", r.text
    return h, ch


def license_(client, h, cid):
    q = client.post("/licenses/quote", headers=h, json={"character_id": cid}).json()
    r = pay(client, h, "/licenses/grant", {"character_id": cid, "accept_terms": True,
                                           "terms_version": q["terms_version"]})
    assert r.status_code == 201, r.text
    return r.json()


def test_listing_gate_review_search_disambiguation_and_grants(client, db, drain):
    base(db)
    ah = admin(client, db)
    a = creator(client, db, "studio")
    fund(client, db, a)
    ch = locked_character(client, db, a, drain)
    assert client.post(f"/characters/{ch['id']}/listing", headers=a, json={"terms": TERMS}).status_code == 403
    enable_flag(db, "character_marketplace", {})  # flag alone (no legal review) is not enough
    assert client.post(f"/characters/{ch['id']}/listing", headers=a, json={"terms": TERMS}).status_code == 403
    market(db)
    r = client.post(f"/characters/{ch['id']}/listing", headers=a,
                    json={"terms": {**TERMS, "commercial_use": False, "advertising": True}})
    assert r.json()["detail"]["code"] == "bad_terms"
    r = client.post(f"/characters/{ch['id']}/listing", headers=a, json={"terms": TERMS}).json()
    assert r["status"] == "pending_review" and set(r["terms"]["prohibited_contexts"]) >= {"adult", "political",
                                                                                          "alcohol"}
    assert r["terms"]["sublicensing"] is False and "cr/scene" in r["terms"]["summary"]
    assert r["certification"]["badge"] == "consistency_tested" and r["certification"]["verified"] is False
    u, _ = signup(client)
    assert client.get("/characters/search", headers=u).json()["items"] == []  # pending review: not discoverable
    client.post(f"/admin/characters/{ch['id']}/review", headers=ah, json=APPROVE)
    _, ch_b = listed(client, db, drain, ah, "other")  # a second creator's public "mert"
    items = client.get("/characters/search", headers=u).json()
    assert {x["handle"] for x in items["items"]} == {"@studio/mert", "@other/mert"}
    assert items["note"] == "search results are not licences"
    r = client.get("/characters/resolve?mention=@mert", headers=u).json()
    assert r["status"] == "ambiguous" and len(r["candidates"]) == 2  # same name, two creators: user decides
    r = client.get("/characters/resolve?mention=@studio/mert", headers=u).json()
    assert r["status"] == "needs_cast" and r["character_id"] == ch["id"]
    enable_studio(db)
    pid = project(client, u)
    r = client.post(f"/studio/projects/{pid}/cast", headers=u, json={"mention": "@studio/mert"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "license_required"  # searchable != licensed
    # public card: no reference assets, no package (rendering access is not asset access)
    card = client.get(f"/characters/{ch['id']}", headers=u).json()
    assert "assets" not in card and "locked_version" not in card and card["certification"]["badge"]
    assert client.get(f"/characters/{ch['id']}/package", headers=u).status_code == 404
    q = client.post("/licenses/quote", headers=u, json={"character_id": ch["id"], "seconds": 6, "scenes": 2}).json()
    assert q["example"]["license_credits"] == 2 * 5 + 6 and q["territory_ok"] and not q["own"]
    r = pay(client, u, "/licenses/grant", {"character_id": ch["id"], "accept_terms": False})
    assert r.json()["detail"]["code"] == "accept_terms_required"
    key = uuid.uuid4().hex
    g1 = pay(client, u, "/licenses/grant", {"character_id": ch["id"], "accept_terms": True, "terms_version": 1},
             key).json()
    g2 = pay(client, u, "/licenses/grant", {"character_id": ch["id"], "accept_terms": True, "terms_version": 1},
             key).json()
    assert g1["id"] == g2["id"] and g1["terms"]["price"]["per_scene_credits"] == 5  # idempotent, frozen terms
    assert pay(client, a, "/licenses/grant", {"character_id": ch["id"], "accept_terms": True, "terms_version": 1}
               ).json()["detail"]["code"] == "own_character"
    m = client.post(f"/studio/projects/{pid}/cast", headers=u, json={"mention": "@studio/mert"})
    assert m.status_code == 201 and m.json()["license"]["grant_id"] == g1["id"]
    assert client.post(f"/studio/projects/{pid}/cast", headers=u,
                       json={"character_id": ch["id"], "alias": "mert2",
                             "identity_version_id": ch["locked_version"]["id"]}).json()["detail"]["code"] == \
        "version_not_licensed"
    # new terms version: existing grant keeps its snapshot
    client.post(f"/characters/{ch['id']}/listing", headers=a,
                json={"terms": {**TERMS, "price": {"per_scene_credits": 50}}})
    assert db.execute(select(Character).where(Character.id == uuid.UUID(ch["id"]))).scalar_one()
    assert client.get("/characters/licenses", headers=u).json()["items"][0]["terms"]["price"]["per_scene_credits"] \
        == 5


def test_royalties_settle_exactly_once_with_waterfall_and_clawback(client, db, drain):
    base(db)
    enable_studio(db)
    market(db)
    ah = admin(client, db)
    policy(client, ah, character_share_rate=0.5, referral_rate=0.1)
    a, ch = listed(client, db, drain, ah, "studio")
    u, _ = signup(client)
    fund(client, db, u, 1000)
    license_(client, u, ch["id"])
    pid = project(client, u)
    client.post(f"/studio/projects/{pid}/cast", headers=u, json={"mention": "@studio/mert"})
    r = client.post(f"/studio/projects/{pid}/storyboard", headers=u, json={
        "brief": "@mert wakes up at dawn in Istanbul. @mert dances on a rooftop as the sun rises.",
        "target_duration_s": 12})
    assert r.status_code == 201, r.text
    est = r.json()["estimate"]
    shot = est["shots"][0]
    assert shot["license_credits"] == 5 + shot["duration_s"] * 1
    assert shot["credits"] == 2 * shot["duration_s"] + shot["license_credits"]  # compute + licence in the quote
    before = bal(client, u)
    assert client.post(f"/studio/projects/{pid}/render", headers={**u, "Idempotency-Key": uuid.uuid4().hex},
                       json={"confirmed_credits": est["credits"]}).status_code == 202
    assert drain() == 3
    assert bal(client, u) == before - est["credits"]
    p = client.get(f"/studio/projects/{pid}", headers=u).json()
    assert p["status"] == "ready"
    asm = db.execute(select(GenerationJob).where(GenerationJob.kind == JobKind.studio_assemble)).scalar_one()
    assert asm.spec["captions"][-1]["text"] == "Licensed AI actor: @studio/mert"  # attribution term honoured
    usage = db.execute(select(CharacterUsageEvent)).scalars().all()
    assert len(usage) == 2 and {x.status for x in usage} == {"settled"}
    roy = db.execute(select(CreatorEarning).where(CreatorEarning.kind == "character_royalty")).scalars().all()
    jobs = db.execute(select(GenerationJob).where(GenerationJob.kind == JobKind.studio_shot)).scalars().all()
    # only the *paid* share of each job's licence credits earns (the 30 promo signup credits are spent first)
    expected = {}
    for jb in jobs:
        paid = sum(creators._paid_credits_by_lot(db, jb.id).values())
        lic = jb.spec["character_usage"][0]["license_credits"]
        if paid:
            expected[jb.id] = round(paid * lic / jb.credit_cost * 0.01 * 1_000_000)
    assert expected and {e.job_id: e.gross_basis_micros for e in roy} == expected
    assert all(e.amount_micros == round(e.gross_basis_micros * 0.5) and e.creator_id == uid(client, a) for e in roy)
    j = db.get(GenerationJob, next(iter(expected)))
    e = next(x for x in roy if x.job_id == j.id)
    for _ in range(2):  # concurrent / repeated settlement callbacks never double-pay
        character_market.on_job_settled(db, j)
        db.commit()
    rows = db.execute(select(CreatorEarning).where(CreatorEarning.kind == "character_royalty")).scalars().all()
    assert len(rows) == len(roy)
    an = client.get("/creator/characters/analytics", headers=a).json()
    assert an["characters"][ch["id"]]["settled_uses"] == 2 and an["royalty_micros"] == sum(x.amount_micros for x in roy)
    # support refund of one shot: the royalty is clawed back (append-only negative row)
    assert client.post(f"/admin/jobs/{j.id}/refund", headers=ah, json={"reason": "customer complaint"}
                       ).status_code == 200
    claw = db.execute(select(CreatorEarning).where(CreatorEarning.kind == "clawback")).scalars().all()
    assert [c.amount_micros for c in claw] == [-e.amount_micros]
    assert client.get("/creator/characters/analytics", headers=a).json()["royalty_micros"] == \
        sum(x.amount_micros for x in roy) - e.amount_micros


def test_waterfall_caps_shares_and_promo_credits_earn_nothing(client, db, drain):
    base(db)
    enable_studio(db)
    market(db)
    ah = admin(client, db)
    policy(client, ah, character_share_rate=1.0, referral_rate=1.0, creator_share_rate=0.5)
    _, ch = listed(client, db, drain, ah, "studio", terms={**TERMS, "price": {"per_scene_credits": 100}})
    u, _ = signup(client)
    fund(client, db, u, 1000)
    license_(client, u, ch["id"])
    pid = project(client, u)
    client.post(f"/studio/projects/{pid}/cast", headers=u, json={"mention": "@studio/mert"})
    est = client.post(f"/studio/projects/{pid}/storyboard", headers=u, json={
        "brief": "@mert dances on a rooftop at sunrise.", "target_duration_s": 4}).json()["estimate"]
    client.post(f"/studio/projects/{pid}/render", headers={**u, "Idempotency-Key": uuid.uuid4().hex},
                json={"confirmed_credits": est["credits"]})
    drain()
    job = db.execute(select(GenerationJob).where(GenerationJob.kind == JobKind.studio_shot)).scalar_one()
    rows = db.execute(select(CreatorEarning).where(CreatorEarning.job_id == job.id)).scalars().all()
    gross = round(job.credit_cost * 0.01 * 1_000_000)
    assert sum(r.amount_micros for r in rows) <= gross  # never more than collected net revenue
    # promo-only payer: usage is recorded but no royalty accrues
    p, _ = signup(client)  # 30 promo signup credits only
    license_(client, p, ch["id"])
    pid2 = project(client, p)
    client.post(f"/studio/projects/{pid2}/cast", headers=p, json={"mention": "@studio/mert"})
    est2 = client.post(f"/studio/projects/{pid2}/storyboard", headers=p, json={
        "brief": "@mert waves.", "target_duration_s": 4}).json()["estimate"]
    if est2["credits"] <= bal(client, p):
        client.post(f"/studio/projects/{pid2}/render", headers={**p, "Idempotency-Key": uuid.uuid4().hex},
                    json={"confirmed_credits": est2["credits"]})
        drain()
    else:
        r = client.post(f"/studio/projects/{pid2}/render", headers={**p, "Idempotency-Key": uuid.uuid4().hex},
                        json={"confirmed_credits": est2["credits"]})
        assert r.status_code == 402  # can't afford: nothing reserved, nothing recorded
    payer_rows = db.execute(select(CreatorEarning).where(CreatorEarning.payer_id == uid(client, p))).scalars().all()
    assert payer_rows == []


def test_unpublish_keeps_grants_takedown_revokes_and_blocks_dispatch(client, db, storage, drain):
    base(db)
    enable_studio(db)
    market(db)
    ah = admin(client, db)
    a, ch = listed(client, db, drain, ah, "studio")
    u, _ = signup(client)
    fund(client, db, u, 1000)
    license_(client, u, ch["id"])
    pid = project(client, u)
    client.post(f"/studio/projects/{pid}/cast", headers=u, json={"mention": "@studio/mert"})
    plan = client.post(f"/studio/projects/{pid}/storyboard", headers=u, json={
        "brief": "@mert dances on a rooftop at sunrise.", "target_duration_s": 4}).json()
    # owner unpublishes: no new licences, existing grant runs to term end
    client.delete(f"/characters/{ch['id']}/listing", headers=a)
    late, _ = signup(client)
    assert client.post("/licenses/quote", headers=late, json={"character_id": ch["id"]}).status_code == 404
    e = client.post(f"/studio/projects/{pid}/estimate", headers=u, json={}).json()
    assert e["blocked"] == []
    before = bal(client, u)
    assert client.post(f"/studio/projects/{pid}/render", headers={**u, "Idempotency-Key": uuid.uuid4().hex},
                       json={"confirmed_credits": plan["estimate"]["credits"]}).status_code == 202
    # rights takedown while the shot is queued: grant revoked, dispatch refused, credits + usage released
    r = client.post(f"/admin/characters/{ch['id']}/takedown", headers=ah, json={"note": "copyright claim"}).json()
    assert r["status"] == "suspended" and r["grants_revoked"] == 1
    assert client.post("/internal/worker/claim", headers=WORKER_H,
                       json={"worker_id": "w1", "models": ["mock_t2v"]}).status_code == 204
    job = db.execute(select(GenerationJob).where(GenerationJob.kind == JobKind.studio_shot)).scalar_one()
    assert job.status == JobStatus.failed and job.error_code == "character_unavailable" and job.refunded
    assert db.execute(select(CharacterUsageEvent)).scalar_one().status == "released"
    assert bal(client, u) == before
    e = client.post(f"/studio/projects/{pid}/estimate", headers=u, json={}).json()
    assert e["blocked"][0]["reason"] == "character_unavailable"
    assert client.get("/characters/licenses", headers=u).json()["items"][0]["status"] == "revoked"
    assert db.execute(select(CreatorEarning).where(CreatorEarning.kind == "character_royalty")).first() is None


def test_reports_pause_listing_prohibited_context_and_cross_tenant(client, db, drain):
    base(db)
    enable_studio(db)
    market(db)
    ah = admin(client, db)
    a, ch = listed(client, db, drain, ah, "studio")
    u, _ = signup(client)
    fund(client, db, u, 1000)
    license_(client, u, ch["id"])
    pid = project(client, u)
    m = client.post(f"/studio/projects/{pid}/cast", headers=u, json={"mention": "@studio/mert"}).json()
    # licence terms prohibit alcohol: the storyboard is rejected
    r = client.post(f"/studio/projects/{pid}/storyboard", headers=u, json={
        "brief": "@mert drinks a cold beer at a rooftop bar.", "target_duration_s": 4})
    assert r.status_code == 502 and r.json()["detail"]["reason"] == "license_terms_violation"  # V4 contract
    # another user can't reference this project's cast member in their storyboard (cross-tenant)
    x, _ = signup(client)
    xp = project(client, x)
    sb = {"title": "t", "characters": [{"key": "m", "name": "M", "cast_member_id": m["id"]}],
          "scenes": [{"key": "s1", "shots": [{"key": "a", "duration_s": 4, "prompt": "a dancer on a roof",
                                              "characters": ["m"]}]}]}
    r = client.post(f"/studio/projects/{xp}/versions", headers=x, json={"storyboard": sb})
    assert r.json()["detail"]["code"] == "bad_storyboard"
    # repeat-abuse control: enough open reports pause the listing pending human review
    for _ in range(3):
        rr, _ = signup(client)
        assert client.post(f"/characters/{ch['id']}/report", headers=rr,
                           json={"reason": "lookalike", "details": "resembles someone"}).status_code == 201
    row = db.execute(select(Character).where(Character.id == uuid.UUID(ch["id"]))).scalar_one()
    db.refresh(row)
    assert row.moderation_status == "flagged"
    assert character_market.get_listing(db, row).status == "paused"
    assert client.get("/characters/search", headers=x).json()["items"] == []


def test_casting_director_suggests_but_never_casts(client, db, drain):
    base(db)
    enable_studio(db)
    market(db)
    ah = admin(client, db)
    listed(client, db, drain, ah, "studio")
    spec2 = {**SPEC, "display_name": "Ayla", "aesthetic": "photorealistic", "age_appearance": "senior",
             "appearance": {"face": "silver hair, kind eyes, wrinkles"},
             "personality": {"archetype": "wise grandmother storyteller", "traits": ["warm"]},
             "languages": ["en"]}
    listed(client, db, drain, ah, "elders", "ayla", spec2)
    u, _ = signup(client)
    pid = project(client, u)
    r = client.post(f"/studio/projects/{pid}/casting", headers=u, json={"roles": [
        {"role": "grandmother", "description": "wise storyteller with silver hair", "age_appearance": "senior"},
        {"role": "dancer", "description": "playful street dancer with copper hair", "languages": ["tr"]}]}).json()
    assert r["director"] == "rule_based" and "not AI" in r["label"]
    assert r["roles"][0]["candidates"][0]["handle"] == "@elders/ayla"
    assert r["roles"][1]["candidates"][0]["handle"] == "@studio/mert"
    assert all(c["license_status"] == "license_required" for role in r["roles"] for c in role["candidates"])
    assert client.get(f"/studio/projects/{pid}/cast", headers=u).json()["items"] == []  # suggestions only


def test_lookalike_master_is_flagged_and_blocks_listing(client, db, drain):
    base(db)
    market(db)
    ah = admin(client, db)
    _, ch = listed(client, db, drain, ah, "studio")
    master = db.execute(select(CharacterAsset).where(CharacterAsset.character_id == uuid.UUID(ch["id"]),
                                                     CharacterAsset.creator_review == "approved",
                                                     CharacterAsset.kind == "seed_preview")).scalar_one()
    b = creator(client, db, "copycat")
    fund(client, db, b)
    cid = client.post("/characters", headers=b, json={"handle": "mert", "description": DESC,
                                                      "spec": SPEC, "rights": {
                                                          "original_creation": True, "no_real_person_likeness": True,
                                                          "no_third_party_ip": True, "adult_appearance": True,
                                                          "accept_terms": True}}).json()["id"]
    pay(client, b, f"/characters/{cid}/preview", {"count": 1, "confirmed_credits": 2})
    drain()
    mine = db.execute(select(CharacterAsset).where(CharacterAsset.character_id == uuid.UUID(cid))).scalar_one()
    mine.qc = {**mine.qc, "dhash": master.qc["dhash"]}  # perceptually identical master
    db.commit()
    r = client.post(f"/characters/{cid}/approve-master", headers=b, json={"asset_id": str(mine.id)}).json()
    assert {f["type"] for f in r["flags"]} == {"lookalike", "near_duplicate_spec"}
    db.expire_all()
    assert db.get(Character, uuid.UUID(cid)).moderation_status == "flagged"
    assert characters.config(db)[1]["qc"]["lookalike_hamming_max"] == 4
