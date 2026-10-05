import os
import uuid

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_DATABASE_URL", "postgresql+psycopg://app:app@localhost:5432/app_test")
os.environ.setdefault("APP_WORKER_TOKENS", '["test-worker-token"]')
os.environ.setdefault("APP_REDIS_URL", "redis://localhost:6390/0")  # unreachable -> local limiter fallback

import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from alembic import command  # noqa: E402
from app.db import get_engine, session_factory  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Template, TemplateVersion, User, UserRole  # noqa: E402
from app.services import ratelimit  # noqa: E402
from app.services.storage import MemoryStorage, set_storage  # noqa: E402

API_DIR = os.path.dirname(os.path.dirname(__file__))
WORKER_H = {"Authorization": "Bearer test-worker-token"}
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64


@pytest.fixture(scope="session", autouse=True)
def migrated():
    cfg = Config(os.path.join(API_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(API_DIR, "alembic"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def clean_db():
    with get_engine().begin() as c:
        tables = c.execute(text("select tablename from pg_tables where schemaname='public' "
                                "and tablename <> 'alembic_version'")).scalars().all()
        # TRUNCATE bypasses row-level triggers, so the append-only ledger can be reset in tests.
        c.execute(text("TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY CASCADE"))
    ratelimit.reset_local()
    yield


@pytest.fixture
def storage():
    st = MemoryStorage()
    set_storage(st)
    return st


@pytest.fixture
def client(storage):
    return TestClient(app)


@pytest.fixture
def db():
    s = session_factory()()
    yield s
    s.close()


def signup(client, email=None, password="correct-horse-1"):
    email = email or f"u{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/auth/signup", json={"email": email, "password": password, "age_confirmed": True,
                                          "terms_accepted": True, "country": "us"})
    assert r.status_code == 201, r.text
    tok = r.json()
    return {"Authorization": f"Bearer {tok['access_token']}"}, tok


def make_admin(db, headers, client):
    uid = client.get("/me", headers=headers).json()["id"]
    u = db.get(User, uuid.UUID(uid))
    u.role = UserRole.admin
    db.commit()


def make_template(db, cost=10, active=True, preferred="dreamid_v", fallback="wan22_animate_14b", **kw):
    t = Template(slug=f"t-{uuid.uuid4().hex[:8]}", title="Test", category="trending", credit_cost=cost,
                 is_active=False, **kw)
    db.add(t)
    db.flush()
    v = TemplateVersion(template_id=t.id, version=1, prompt_recipe="a person dancing {user_text}",
                        preferred_model=preferred, fallback_model=fallback)
    db.add(v)
    db.flush()
    t.current_version_id = v.id
    t.is_active = active
    db.commit()
    return t


def ready_profile(client, headers, storage, n=5):
    r = client.post("/identity-profiles", json={"name": "Me", "consent_own_likeness": True}, headers=headers)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    for _ in range(n):
        s = client.post("/uploads/sign", json={"profile_id": pid, "mime": "image/jpeg", "size_bytes": 1000},
                        headers=headers).json()
        storage.put(s["fields"]["key"], JPEG, "image/jpeg")
        r = client.post(f"/identity-profiles/{pid}/assets/{s['asset_id']}/complete", headers=headers)
        assert r.status_code == 200, r.text
    assert r.json()["status"] == "ready"
    return pid
