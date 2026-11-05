"""Scheduled backups (pg_dump custom format + media + receipt originals), optional encryption, and restore.

Archive layout (tar.gz):  manifest.json, db.dump, files/media/..., files/receipts/...
Encrypted archives are `LARDERENC1` + salt(16) + nonce(12) + AES-256-GCM(ciphertext), key = scrypt(passphrase).
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, unquote

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from ..config import get_settings

MAGIC = b"LARDERENC1"


def _pg_env() -> tuple[dict, list[str]]:
    """libpq connection args from the SQLAlchemy URL, passing the password via env (never on the command line)."""
    url = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
    parts = urlsplit(url)
    env = dict(os.environ)
    if parts.password:
        env["PGPASSWORD"] = unquote(parts.password)
    args = []
    from urllib.parse import parse_qs
    q = parse_qs(parts.query)
    host = parts.hostname or (q.get("host", [None])[0])
    port = parts.port or (q.get("port", [None])[0])
    if host:
        args += ["-h", host]
    if port:
        args += ["-p", str(port)]
    if parts.username:
        args += ["-U", unquote(parts.username)]
    dbname = parts.path.lstrip("/")
    return env, args + ["-d", dbname]


def _key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode())


def encrypt(data: bytes, passphrase: str) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    return MAGIC + salt + nonce + AESGCM(_key(passphrase, salt)).encrypt(nonce, data, MAGIC)


def decrypt(blob: bytes, passphrase: str) -> bytes:
    if not blob.startswith(MAGIC):
        return blob
    salt, nonce, ct = blob[10:26], blob[26:38], blob[38:]
    return AESGCM(_key(passphrase, salt)).decrypt(nonce, ct, MAGIC)


def create_backup(passphrase: str | None = None, label: str = "scheduled") -> Path:
    s = get_settings()
    passphrase = passphrase if passphrase is not None else s.backup_passphrase
    s.backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "db.dump"
        env, args = _pg_env()
        res = subprocess.run(["pg_dump", "-Fc", "--no-owner", "--no-privileges", "-f", str(dump), *args],
                             env=env, capture_output=True, timeout=1800)
        if res.returncode != 0:
            raise RuntimeError("pg_dump failed: " + res.stderr.decode(errors="replace")[-500:])
        from sqlalchemy import text

        from ..db import engine

        with engine().connect() as c:
            head = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
        manifest = {"app": "larder", "format": 1, "created_at": stamp, "label": label, "alembic_revision": head,
                    "encrypted": bool(passphrase)}
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            info = tarfile.TarInfo("manifest.json")
            mb = json.dumps(manifest, indent=1).encode()
            info.size = len(mb)
            tar.addfile(info, io.BytesIO(mb))
            tar.add(dump, arcname="db.dump")
            for name, d in (("media", s.media_dir), ("receipts", s.receipts_dir)):
                if d.exists():
                    tar.add(d, arcname=f"files/{name}", filter=lambda ti: None if "/staging/" in ti.name or ti.name.endswith("/staging") else ti)
        data = buf.getvalue()
    out = s.backups_dir / f"larder-{stamp}{'.enc' if passphrase else ''}.tar.gz"
    out.write_bytes(encrypt(data, passphrase) if passphrase else data)
    os.chmod(out, 0o600)
    prune()
    return out


def prune() -> None:
    s = get_settings()
    files = sorted(s.backups_dir.glob("larder-*.tar.gz"))
    for f in files[: max(0, len(files) - s.backup_keep)]:
        f.unlink(missing_ok=True)


def list_backups() -> list[dict]:
    s = get_settings()
    if not s.backups_dir.exists():
        return []
    return [{"name": f.name, "size": f.stat().st_size, "encrypted": ".enc." in f.name,
             "created_at": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat()}
            for f in sorted(s.backups_dir.glob("larder-*.tar.gz"), reverse=True)]


def _safe_members(tar: tarfile.TarFile):
    for m in tar.getmembers():
        p = Path(m.name)
        if p.is_absolute() or ".." in p.parts or m.issym() or m.islnk() or m.isdev():
            raise RuntimeError(f"Unsafe path in backup: {m.name}")
        yield m


def restore_backup(path: Path, passphrase: str | None = None) -> dict:
    """Replace the database and files with a backup's contents. Run with the app stopped (see docs/OPERATIONS.md)."""
    s = get_settings()
    blob = path.read_bytes()
    if blob.startswith(MAGIC):
        if not (passphrase or s.backup_passphrase):
            raise RuntimeError("This backup is encrypted; provide the passphrase")
        blob = decrypt(blob, passphrase or s.backup_passphrase)
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            tar.extractall(tmp, members=list(_safe_members(tar)))
        manifest = json.loads((Path(tmp) / "manifest.json").read_text())
        env, args = _pg_env()
        res = subprocess.run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges", "--single-transaction",
                              *args, str(Path(tmp) / "db.dump")], env=env, capture_output=True, timeout=3600)
        if res.returncode != 0:
            raise RuntimeError("pg_restore failed: " + res.stderr.decode(errors="replace")[-800:])
        for name, d in (("media", s.media_dir), ("receipts", s.receipts_dir)):
            src = Path(tmp) / "files" / name
            if d.exists():
                shutil.rmtree(d)
            if src.exists():
                shutil.copytree(src, d)
            else:
                d.mkdir(parents=True, exist_ok=True)
    from ..db import reset_engine
    reset_engine()
    return manifest
