from sqlalchemy import text

from app.db import get_engine

from .conftest import JPEG, MP4, ready_profile, signup


def test_signup_login_refresh_rotation(client):
    h, tok = signup(client, "a@example.com")
    me = client.get("/me", headers=h).json()
    assert me["credits"] == 30 and me["needs_consent"] is False

    assert client.post("/auth/login", json={"email": "a@example.com", "password": "wrong-pass"}).status_code == 401
    assert client.post("/auth/signup", json={"email": "A@example.com", "password": "xxxxxxxx1",
                                             "age_confirmed": True, "terms_accepted": True}).status_code == 409

    r1 = client.post("/auth/refresh", json={"refresh_token": tok["refresh_token"]})
    assert r1.status_code == 200
    # Reusing the rotated token revokes the whole family.
    assert client.post("/auth/refresh", json={"refresh_token": tok["refresh_token"]}).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": r1.json()["refresh_token"]}).status_code == 401


def test_signup_requires_consent(client):
    r = client.post("/auth/signup", json={"email": "b@example.com", "password": "xxxxxxxx1",
                                          "age_confirmed": False, "terms_accepted": True})
    assert r.status_code == 422


def test_auth_required_and_bad_token(client):
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_login_rate_limited(client):
    signup(client, "rl@example.com")
    codes = [client.post("/auth/login", json={"email": "rl@example.com", "password": "bad-bad-bad"}).status_code
             for _ in range(12)]
    assert 429 in codes


def test_identity_profile_requires_consent(client):
    h, _ = signup(client)
    r = client.post("/identity-profiles", json={"name": "Me", "consent_own_likeness": False}, headers=h)
    assert r.status_code == 422


def test_upload_validation_and_magic_bytes(client, storage):
    h, _ = signup(client)
    pid = client.post("/identity-profiles", json={"consent_own_likeness": True}, headers=h).json()["id"]
    assert client.post("/uploads/sign", json={"profile_id": pid, "mime": "application/pdf", "size_bytes": 10},
                       headers=h).status_code == 415
    assert client.post("/uploads/sign", json={"profile_id": pid, "mime": "image/jpeg",
                                              "size_bytes": 999_999_999}, headers=h).status_code == 413
    s = client.post("/uploads/sign", json={"profile_id": pid, "mime": "image/jpeg", "size_bytes": 100},
                    headers=h).json()
    # Server picks the key, scoped to the user.
    assert s["fields"]["key"].startswith("users/")
    storage.put(s["fields"]["key"], b"<?php evil", "image/jpeg")
    r = client.post(f"/identity-profiles/{pid}/assets/{s['asset_id']}/complete", headers=h).json()
    assert r["assets"][0]["status"] == "rejected"
    assert s["fields"]["key"] not in storage.objects

    v = client.post("/uploads/sign", json={"profile_id": pid, "mime": "video/mp4", "size_bytes": 100},
                    headers=h).json()
    storage.put(v["fields"]["key"], MP4, "video/mp4")
    r = client.post(f"/identity-profiles/{pid}/assets/{v['asset_id']}/complete", headers=h).json()
    assert r["status"] == "ready"


def test_profile_idor(client, storage):
    h1, _ = signup(client)
    h2, _ = signup(client)
    pid = ready_profile(client, h1, storage)
    assert client.get(f"/identity-profiles/{pid}", headers=h2).status_code == 404
    assert client.delete(f"/identity-profiles/{pid}", headers=h2).status_code == 404
    assert client.post("/uploads/sign", json={"profile_id": pid, "mime": "image/jpeg", "size_bytes": 5},
                       headers=h2).status_code == 404


def test_delete_profile_removes_media(client, storage):
    h, _ = signup(client)
    pid = ready_profile(client, h, storage)
    assert any("/identity/" in k for k in storage.objects)
    assert client.delete(f"/identity-profiles/{pid}", headers=h).status_code == 204
    assert not any("/identity/" in k for k in storage.objects)
    assert client.get(f"/identity-profiles/{pid}", headers=h).status_code == 404


def test_ledger_is_append_only(client):
    signup(client)
    import pytest
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError), get_engine().begin() as c:
        c.execute(text("UPDATE credit_ledger SET delta = 999999"))
    with pytest.raises(DBAPIError), get_engine().begin() as c:
        c.execute(text("DELETE FROM credit_ledger"))


def test_account_deletion(client, storage):
    h, _ = signup(client, "del@example.com")
    ready_profile(client, h, storage)
    storage.put("users/other/keep", JPEG, "image/jpeg")
    r = client.delete("/account", headers=h)
    assert r.status_code == 202
    assert set(storage.objects) == {"users/other/keep"}
    assert client.get("/me", headers=h).status_code == 401
    # Email is free again (PII scrubbed).
    signup(client, "del@example.com")
