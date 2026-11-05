"""Full household export (JSON + CSV + media archive) and import into a fresh instance.

The export excludes secrets: password hashes, session tokens and API token hashes are never written.
"""
from __future__ import annotations

import csv
import io
import json
import secrets
import uuid
import zipfile
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..auth import hash_password
from ..config import get_settings
from ..db import Base
from ..models import User

EXCLUDED_TABLES = {"user_session", "api_token", "rate_limit_hit", "idempotency_record", "external_product_cache", "alembic_version"}
REDACTED_COLUMNS = {"user_account": {"password_hash"}}
FORMAT = "larder.export.v1"


def _json_default(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    raise TypeError(type(v))


def tables() -> list:
    return [t for t in Base.metadata.sorted_tables if t.name not in EXCLUDED_TABLES]


def build_export(db: Session, include_files: bool = True) -> bytes:
    s = get_settings()
    data = {}
    for t in tables():
        rows = [dict(r._mapping) for r in db.execute(select(t))]
        for r in rows:
            for c in REDACTED_COLUMNS.get(t.name, ()):
                r.pop(c, None)
        data[t.name] = rows
    head = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    manifest = {"format": FORMAT, "generated_at": datetime.utcnow().isoformat() + "Z", "alembic_revision": head,
                "counts": {k: len(v) for k, v in data.items()},
                "notes": "Passwords, sessions and API tokens are not included. CoFID rows are Crown copyright (OGL v3)."}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
        z.writestr("data.json", json.dumps(data, default=_json_default))
        for name, rows in data.items():
            if not rows:
                continue
            sio = io.StringIO()
            w = csv.DictWriter(sio, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                w.writerow({k: (json.dumps(v, default=_json_default) if isinstance(v, (dict, list)) else _json_default(v)
                                if isinstance(v, (datetime, date, uuid.UUID)) else v) for k, v in r.items()})
            z.writestr(f"csv/{name}.csv", sio.getvalue())
        if include_files:
            for prefix, d in (("media", s.media_dir), ("receipts", s.receipts_dir)):
                if d.exists():
                    for f in d.rglob("*"):
                        if f.is_file() and "staging" not in f.parts:
                            z.write(f, f"{prefix}/{f.relative_to(d)}")
    return buf.getvalue()


def _deferred_columns(table) -> set[str]:
    """FK columns that close a cycle (declared with use_alter); inserted as NULL and filled in afterwards."""
    cols = set()
    for fk in table.foreign_keys:
        if fk.constraint.use_alter and fk.parent.nullable:
            cols.add(fk.parent.name)
    return cols


def insert_order() -> list:
    """sorted_tables ignores use_alter FKs; make sure NOT NULL ones still have their parent inserted first."""
    order = tables()
    changed = True
    while changed:
        changed = False
        for t in list(order):
            for fk in t.foreign_keys:
                if fk.constraint.use_alter and not fk.parent.nullable:
                    parent = fk.column.table
                    if parent in order and order.index(parent) > order.index(t):
                        order.remove(t)
                        order.insert(order.index(parent) + 1, t)
                        changed = True
    return order


def import_export(db: Session, blob: bytes, admin_email: str, admin_password: str) -> dict:
    """Load an export into an EMPTY instance. The named admin gets the given password; other users get random ones."""
    if db.scalar(select(func.count()).select_from(User)):
        raise RuntimeError("Import only works on a fresh instance with no users")
    s = get_settings()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        manifest = json.loads(z.read("manifest.json"))
        if manifest.get("format") != FORMAT:
            raise RuntimeError("Not a Larder export")
        data = json.loads(z.read("data.json"))
        for name in z.namelist():
            p = Path(name)
            if p.is_absolute() or ".." in p.parts:
                raise RuntimeError("Unsafe path in export")
            if p.parts[0] in ("media", "receipts") and not name.endswith("/"):
                dest = (s.media_dir if p.parts[0] == "media" else s.receipts_dir) / Path(*p.parts[1:])
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(name))
    deferred: list[tuple] = []
    published_versions = []
    found_admin = False
    for t in insert_order():
        rows = data.get(t.name, [])
        if not rows:
            continue
        dcols = _deferred_columns(t)
        coltypes = {c.name: c.type for c in t.columns}
        for r in rows:
            row = {k: v for k, v in r.items() if k in coltypes}
            for k in dcols:
                if row.get(k) is not None:
                    deferred.append((t, row["id"], k, row[k]))
                    row[k] = None
            if t.name == "user_account":
                if row["email"].lower() == admin_email.lower():
                    row["password_hash"], found_admin = hash_password(admin_password), True
                    row["role"] = "admin"
                else:
                    row["password_hash"] = hash_password(secrets.token_urlsafe(24))
            if t.name == "recipe_version" and row.get("status") == "published":
                published_versions.append(row["id"])
                row["status"] = "draft"  # children must be inserted before the version becomes immutable
            db.execute(t.insert().values(**row))
    if not found_admin:
        db.rollback()
        raise RuntimeError(f"No user with email {admin_email} in the export")
    for t, rid, col, val in deferred:
        db.execute(t.update().where(t.c.id == rid).values({col: val}))
    if published_versions:
        vt = Base.metadata.tables["recipe_version"]
        db.execute(vt.update().where(vt.c.id.in_(published_versions)).values(status="published"))
    db.commit()
    return {"imported": manifest.get("counts", {}), "note": "Other users must have their passwords reset by the administrator."}
