import os

import pytest

# Configure before importing the app: tests never read a developer's .env secrets.
os.environ.setdefault("SEBASTIAN_API_KEY", "test-key")
os.environ.setdefault("SEBASTIAN_SESSION_SECRET", "test-secret")
os.environ.setdefault("SEBASTIAN_TIMEZONE", "Europe/Berlin")
os.environ.setdefault("SEBASTIAN_QUIET_HOURS", "none")
from argon2 import PasswordHasher  # noqa: E402

os.environ.setdefault("SEBASTIAN_UI_PASSWORD_HASH", PasswordHasher().hash("hunter2"))

from sebastian.db import Database
from sebastian.migrate import upgrade_to_head


@pytest.fixture
def db(tmp_path):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    upgrade_to_head(url)  # exercises the real migrations
    return Database(url)


@pytest.fixture
def session(db):
    gen = db.session()
    s = next(gen)
    yield s
    s.close()


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient

    from sebastian.app import create_app

    app = create_app(f"sqlite:///{tmp_path / 'api.db'}")
    with TestClient(app, headers={"Authorization": "Bearer test-key"}) as c:
        # FastAPI builds route models lazily; do it before tests freeze `datetime`.
        c.get("/openapi.json")
        yield c


@pytest.fixture
def ui(tmp_path):
    """Browser-like client (cookies, no bearer header) against a fresh app."""
    from fastapi.testclient import TestClient

    from sebastian.app import create_app

    app = create_app(f"sqlite:///{tmp_path / 'ui.db'}")
    with TestClient(app, follow_redirects=False) as c:
        c.get("/openapi.json", headers={"Authorization": "Bearer test-key"})
        yield c
