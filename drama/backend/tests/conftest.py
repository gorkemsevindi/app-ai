import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="drama-test-"))
os.environ.update({
    "DRAMA_DATABASE_URL": os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_TMP / 'test.db'}"),
    "DRAMA_STORAGE_DIR": str(_TMP / "storage"),
    "DRAMA_INLINE_WORKER": "0",
    "DRAMA_ENV": "test",
    "DRAMA_LLM_PROVIDER": "local_template",
})

from fastapi.testclient import TestClient  # noqa: E402

from dramaapp.db import Base, create_all, engine, session_factory  # noqa: E402
from dramaapp.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.drop_all(engine())
    create_all()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    s = session_factory()()
    yield s
    s.close()


_n = [0]


def signup(client, *, creator=False, birth_year=1990, role=None, db=None):
    _n[0] += 1
    r = client.post("/auth/signup", json={"email": f"u{_n[0]}@example.com", "password": "password123",
                                          "display_name": f"User {_n[0]}", "as_creator": creator,
                                          "birth_year": birth_year})
    assert r.status_code == 201, r.text
    data = r.json()
    if role and db is not None:
        from dramaapp.models import User
        from dramaapp.security import create_access_token
        u = db.get(User, data["user"]["id"])
        u.role = role
        db.commit()
        data["access_token"] = create_access_token(u.id, role)
    return {"Authorization": f"Bearer {data['access_token']}"}, data["user"]
