"""Backup + restore round trip (database and media) and household export/import (acceptance 7, restore path)."""
import io
import os
import shutil

import pytest
from PIL import Image

from app.config import get_settings

pytestmark = pytest.mark.skipif(shutil.which("pg_dump") is None, reason="pg_dump not available")


def _photo():
    buf = io.BytesIO()
    Image.new("RGB", (80, 60), "orange").save(buf, "JPEG")
    return buf.getvalue()


def _make_data(client):
    r = client.post("/api/v1/recipes", json={"content": {"title": "Backup soup", "ingredients": [{"name": "Leeks", "quantity": 2, "unit": "each"}],
                                                         "steps": [{"text": "Cook."}]}}).json()
    m = client.post(f"/api/v1/recipes/{r['id']}/media", files={"file": ("p.jpg", _photo(), "image/jpeg")}).json()
    client.post("/api/v1/pantry/lots", json={"food_name": "Leeks", "quantity": 3, "unit": "each"})
    return r, m


@pytest.mark.parametrize("passphrase", ["", "a long backup passphrase"])
def test_backup_and_restore_round_trip(client, passphrase):
    from app.services.backup import create_backup, restore_backup

    r, m = _make_data(client)
    path = create_backup(passphrase or None, label="test")
    assert path.exists() and (".enc." in path.name) == bool(passphrase)
    if passphrase:
        assert path.read_bytes().startswith(b"LARDERENC1")
    # wreck the data: delete recipe and its media file
    client.delete(f"/api/v1/recipes/{r['id']}")
    assert client.get(f"/api/v1/recipes/{r['id']}").status_code == 404
    manifest = restore_backup(path, passphrase or None)
    assert manifest["alembic_revision"] == "0002"
    # sessions were restored too; log in again to be safe
    from tests.conftest import PASSWORD, _client, login
    c = login(_client(), "a@example.com", PASSWORD)
    got = c.get(f"/api/v1/recipes/{r['id']}").json()
    assert got["title"] == "Backup soup"
    assert c.get(got["hero"]["url"]).status_code == 200
    assert c.get("/api/v1/pantry").json()["items"][0]["quantity_base"] == 3


def test_wrong_passphrase_fails(client):
    from app.services.backup import create_backup, restore_backup

    path = create_backup("right passphrase", label="test")
    with pytest.raises(Exception):
        restore_backup(path, "wrong passphrase")


def test_export_and_import_into_fresh_instance(client, db):
    from sqlalchemy import text

    from app.db import Base, engine
    from app.services.export import build_export, import_export

    r, _ = _make_data(client)
    v2 = client.get(f"/api/v1/recipes/{r['id']}/versions/{r['version']['id']}/content").json()
    v2["ingredients"][0]["quantity"] = 3
    client.post(f"/api/v1/recipes/{r['id']}/versions", json={"content": v2, "label": "More leeks"})
    resp = client.get("/api/v1/export")
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/zip"
    blob = build_export(db)
    import zipfile
    names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
    assert "data.json" in names and "csv/recipe.csv" in names and any(n.startswith("media/recipes/") for n in names)
    assert b"password_hash" not in zipfile.ZipFile(io.BytesIO(blob)).read("data.json")

    db.close()
    names_sql = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine().begin() as c:
        c.execute(text(f"TRUNCATE {names_sql} RESTART IDENTITY CASCADE"))
    shutil.rmtree(get_settings().media_dir, ignore_errors=True)
    from app.db import SessionLocal
    s = SessionLocal()
    out = import_export(s, blob, "a@example.com", "a new strong password")
    s.close()
    assert out["imported"]["recipe_version"] == 2
    from tests.conftest import _client, login
    c = login(_client(), "a@example.com", "a new strong password")
    got = c.get(f"/api/v1/recipes/{r['id']}").json()
    assert len(got["versions"]) == 2 and got["hero"] is not None and c.get(got["hero"]["url"]).status_code == 200
    _ = os
