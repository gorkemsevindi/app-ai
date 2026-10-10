"""V4 Stage A3: signed share links, click capture, attribution rules, landing page, template reports."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import Attribution, FeatureFlag, GenerationJob, JobStatus, ReferralClick, Template, User
from app.services import sharing

from .conftest import make_template, ready_profile, signup
from .test_generation import _gen


def enable(db, key, value=None):
    f = db.get(FeatureFlag, key) or FeatureFlag(key=key)
    f.enabled, f.value = True, value or {}
    db.add(f)
    db.commit()


def _uid(client, h):
    return uuid.UUID(client.get("/me", headers=h).json()["id"])


def test_share_link_is_signed_flagged_and_reused(client, db):
    h, _ = signup(client)
    t = make_template(db)
    assert client.post("/share-links", headers=h, json={"template_id": str(t.id)}).status_code == 403
    enable(db, "sharing")
    a = client.post("/share-links", headers=h, json={"template_id": str(t.id)}).json()
    b = client.post("/share-links", headers=h, json={"template_id": str(t.id)}).json()
    assert a["token"] == b["token"] and a["url"].endswith(f"/t/{a['token']}") and a["app_url"].startswith("aivideo://")
    c = client.post("/share-links", headers=h, json={"template_id": str(t.id), "campaign": "tiktok"}).json()
    assert c["token"] != a["token"]
    # forged signature / guessed code -> 404 without touching the link
    code = a["token"].split(".")[0]
    assert client.get(f"/share-links/{code}.AAAAAAAAAAAAAAAA").status_code == 404
    assert client.get("/share-links/nope").status_code == 404
    # unknown template / takedown -> not shareable
    assert client.post("/share-links", headers=h, json={"template_id": str(uuid.uuid4())}).status_code == 404


def test_job_share_only_for_own_completed_jobs(client, db, storage):
    enable(db, "sharing")
    h, _ = signup(client)
    pid = ready_profile(client, h, storage)
    t = make_template(db, cost=10)
    jid = _gen(client, h, t.id, pid).json()["id"]
    r = client.post("/share-links", headers=h, json={"job_id": jid})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_shareable"
    job = db.get(GenerationJob, uuid.UUID(jid))
    job.status = JobStatus.completed
    db.commit()
    r = client.post("/share-links", headers=h, json={"job_id": jid})
    assert r.status_code == 201 and r.json()["template_id"] == str(t.id)
    h2, _ = signup(client)
    assert client.post("/share-links", headers=h2, json={"job_id": jid}).status_code == 404  # IDOR


def test_resolve_records_deduplicated_clicks_and_hides_taken_down_templates(client, db):
    enable(db, "sharing")
    h, _ = signup(client)
    t = make_template(db)
    tok = client.post("/share-links", headers=h, json={"template_id": str(t.id)}).json()["token"]
    r = client.get(f"/share-links/{tok}?source=web&platform=ios").json()
    assert r["status"] == "ok" and r["template"]["id"] == str(t.id)
    client.get(f"/share-links/{tok}")
    client.get(f"/share-links/{tok}", headers={"cf-connecting-ip": "203.0.113.9"})
    clicks = db.execute(select(ReferralClick)).scalars().all()
    assert len(clicks) == 2  # same IP within the window counts once
    assert all(len(c.ip_hash) == 64 and "203.0.113" not in c.ip_hash for c in clicks)
    t2 = db.get(Template, t.id)
    t2.moderation_status = "rejected"
    db.commit()
    assert client.get(f"/share-links/{tok}").json() == {"status": "unavailable", "template": None}
    stats = client.get("/share-links", headers=h).json()["items"]
    assert stats[0]["clicks"] == 2 and stats[0]["signups"] == 0


def test_attribution_rules(client, db):
    enable(db, "sharing")
    owner_h, _ = signup(client)
    t = make_template(db)
    tok = client.post("/share-links", headers=owner_h, json={"template_id": str(t.id)}).json()["token"]
    new_h, _ = signup(client)
    assert client.post(f"/share-links/{tok}/attribute", headers=new_h).json()["reason"] == "disabled"
    enable(db, "referrals")
    assert client.post(f"/share-links/{tok}/attribute", headers=new_h).json()["reason"] == "no_recent_click"
    client.get(f"/share-links/{tok}")
    # self-referral never counts
    assert client.post(f"/share-links/{tok}/attribute", headers=owner_h).json()["reason"] == "self_referral"
    r = client.post(f"/share-links/{tok}/attribute", headers=new_h).json()
    assert r["attributed"] is True
    a = db.execute(select(Attribution)).scalar_one()
    assert a.referrer_id == _uid(client, owner_h) and a.template_id == t.id and a.policy["attribution_days"] == 30
    # first touch wins: a second link can't steal the user
    other_h, _ = signup(client)
    tok2 = client.post("/share-links", headers=other_h, json={"template_id": str(t.id)}).json()["token"]
    client.get(f"/share-links/{tok2}", headers={"cf-connecting-ip": "198.51.100.1"})
    assert client.post(f"/share-links/{tok2}/attribute", headers=new_h).json()["reason"] == "already_attributed"
    # existing (old) users are not "acquired" by a link
    old_h, _ = signup(client)
    u = db.get(User, _uid(client, old_h))
    u.created_at = datetime.now(UTC) - timedelta(days=10)
    db.commit()
    assert client.post(f"/share-links/{tok}/attribute", headers=old_h).json()["reason"] == "not_new_user"


def test_landing_page_escapes_and_well_known_files(client, db, monkeypatch):
    enable(db, "sharing")
    h, _ = signup(client)
    t = make_template(db)
    t.title = "<script>alert(1)</script>"
    db.commit()
    tok = client.post("/share-links", headers=h, json={"template_id": str(t.id)}).json()["token"]
    r = client.get(f"/t/{tok}")
    assert r.status_code == 200 and "<script>alert" not in r.text and "&lt;script&gt;" in r.text
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert "no longer available" in client.get("/t/forged.token").text
    assert client.get("/.well-known/apple-app-site-association").status_code == 404
    from app.config import get_settings

    monkeypatch.setenv("APP_IOS_APP_IDS", '["ABCDE12345.com.example.aivideo"]')
    get_settings.cache_clear()
    try:
        j = client.get("/.well-known/apple-app-site-association").json()
        assert j["applinks"]["details"][0]["appIDs"] == ["ABCDE12345.com.example.aivideo"]
    finally:
        monkeypatch.delenv("APP_IOS_APP_IDS")
        get_settings.cache_clear()


def test_template_reports_move_to_review(client, db):
    t = make_template(db)
    hs = [signup(client)[0] for _ in range(5)]
    for i, h in enumerate(hs):
        r = client.post(f"/templates/{t.id}/report", headers=h, json={"reason": "copyright"})
        assert r.status_code == 201
        client.post(f"/templates/{t.id}/report", headers=h, json={"reason": "copyright"})  # dup ignored
        db.expire_all()
        assert db.get(Template, t.id).moderation_status == ("review" if i == 4 else "approved")
    # a minor-safety report goes to review immediately
    t2 = make_template(db)
    client.post(f"/templates/{t2.id}/report", headers=hs[0], json={"reason": "minor_safety"})
    db.expire_all()
    assert db.get(Template, t2.id).moderation_status == "review"
    assert client.get(f"/templates/{t2.id}").status_code == 404  # out of the app until an admin decides


def test_sign_roundtrip():
    assert sharing.unsign(sharing.sign("abc")) == "abc"
