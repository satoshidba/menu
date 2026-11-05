"""Test fixtures. Uses a real PostgreSQL database (LARDER_TEST_DATABASE_URL) migrated with Alembic."""
import os
import shutil
import tempfile
from pathlib import Path

import pytest

TEST_DB = os.environ.get("LARDER_TEST_DATABASE_URL", "postgresql+psycopg://postgres@/larder_test?host=/var/run/postgresql&port=5433")
DATA_DIR = Path(tempfile.mkdtemp(prefix="larder-test-"))
os.environ.update({
    "LARDER_DATABASE_URL": TEST_DB, "LARDER_DATA_DIR": str(DATA_DIR), "LARDER_COOKIE_SECURE": "false",
    "LARDER_INLINE_JOBS": "true", "LARDER_ANTHROPIC_API_KEY": "", "LARDER_OFF_ENABLED": "false",
    "LARDER_IMPORT_RATE_PER_MINUTE": "1000", "LARDER_LOGIN_RATE_PER_MINUTE": "1000", "LARDER_BACKUP_PASSPHRASE": "",
})

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import Base, SessionLocal, engine, reset_engine  # noqa: E402

PASSWORD = "correct horse battery"


@pytest.fixture(scope="session", autouse=True)
def migrated():
    get_settings.cache_clear()
    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "migrations"))
    with engine().begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    command.upgrade(cfg, "head")
    yield
    reset_engine()
    shutil.rmtree(DATA_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_db(migrated):
    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine().begin() as c:
        c.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    for d in ("media", "receipts", "backups"):
        shutil.rmtree(DATA_DIR / d, ignore_errors=True)
    yield


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


def _client() -> TestClient:
    from app.main import app
    return TestClient(app)


def login(c: TestClient, email: str, password: str = PASSWORD) -> TestClient:
    r = c.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    c.headers["x-csrf-token"] = r.json()["csrf_token"]
    return c


@pytest.fixture
def client() -> TestClient:
    """Signed-in administrator of a fresh household with two adults, two toddlers and a dog."""
    c = _client()
    r = c.post("/api/v1/setup", json={"household_name": "Test", "email": "a@example.com", "display_name": "Adult A", "password": PASSWORD})
    assert r.status_code == 201, r.text
    c.headers["x-csrf-token"] = r.json()["csrf_token"]
    assert c.post("/api/v1/users", json={"email": "b@example.com", "display_name": "Adult B", "password": PASSWORD}).status_code == 201
    for name in ("Toddler One", "Toddler Two"):
        assert c.post("/api/v1/people", json={"name": name, "kind": "child"}).status_code == 201
    assert c.post("/api/v1/pets", json={"name": "Dog", "species": "dog"}).status_code == 201
    return c


@pytest.fixture
def anon() -> TestClient:
    return _client()


def people(c: TestClient) -> dict:
    return {p["name"]: p for p in c.get("/api/v1/people").json()}


@pytest.fixture
def cofid(client, db):
    from app.services.cofid import load_sample
    load_sample(db)
    return True
