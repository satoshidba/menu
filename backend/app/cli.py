"""Admin CLI: `python -m app.cli <command>`."""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from sqlalchemy import func, select


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("import-cofid", help="Import the CoFID dataset (xlsx path or URL); --sample loads the bundled subset")
    c.add_argument("source", nargs="?")
    c.add_argument("--sample", action="store_true")
    sub.add_parser("seed-demo", help="Load sample (non-personal) household data for trying the app")
    b = sub.add_parser("backup", help="Create a backup now")
    b.add_argument("--passphrase", default=None)
    r = sub.add_parser("restore", help="Restore a backup archive (stop the web and worker services first)")
    r.add_argument("file", help="Path inside the container, or - to read the archive from standard input")
    r.add_argument("--passphrase", default=None)
    r.add_argument("--yes", action="store_true")
    e = sub.add_parser("export", help="Write a full household export zip")
    e.add_argument("file")
    i = sub.add_parser("import", help="Import a household export into a fresh instance")
    i.add_argument("file")
    i.add_argument("--admin-email", required=True)
    pw = sub.add_parser("reset-password", help="Set a user's password")
    pw.add_argument("email")
    sub.add_parser("check", help="Verify stock projections match the ledger")
    args = ap.parse_args(argv)

    from .db import SessionLocal

    if args.cmd == "import-cofid":
        from .services import cofid

        db = SessionLocal()
        if args.sample or not args.source:
            n = cofid.load_sample(db)
        else:
            src = args.source
            if src.startswith("http"):
                from .services.safe_fetch import fetch
                import tempfile
                got = fetch(src, accept="*/*", max_bytes=50 * 1024 * 1024)
                tmp = Path(tempfile.mkstemp(suffix=".xlsx")[1])
                tmp.write_bytes(got.body)
                src = str(tmp)
            n = cofid.load(db, cofid.read_xlsx(Path(src)))
        print(f"Imported {n} CoFID foods")
    elif args.cmd == "seed-demo":
        from .seed import seed_demo

        print(json.dumps(seed_demo(), indent=1))
    elif args.cmd == "backup":
        from .services.backup import create_backup

        print(create_backup(args.passphrase, label="manual"))
    elif args.cmd == "restore":
        from .services.backup import restore_backup

        if args.file == "-":
            import tempfile
            if not args.yes:
                print("Reading the archive from stdin needs --yes (there is no terminal to confirm on).")
                return 1
            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
                tmp.write(sys.stdin.buffer.read())
            src = Path(tmp.name)
        else:
            src = Path(args.file)
            if not args.yes:
                ans = input("This replaces ALL current data with the backup. Type RESTORE to continue: ")
                if ans.strip() != "RESTORE":
                    print("Cancelled")
                    return 1
        try:
            print(json.dumps(restore_backup(src, args.passphrase), indent=1))
        finally:
            if args.file == "-":
                src.unlink(missing_ok=True)
    elif args.cmd == "export":
        from .services.export import build_export

        db = SessionLocal()
        Path(args.file).write_bytes(build_export(db))
        print(f"Wrote {args.file}")
    elif args.cmd == "import":
        from .services.export import import_export

        db = SessionLocal()
        pw1 = getpass.getpass("New password for the administrator: ")
        print(json.dumps(import_export(db, Path(args.file).read_bytes(), args.admin_email, pw1), indent=1))
    elif args.cmd == "reset-password":
        from .auth import hash_password, validate_password_strength
        from .models import User

        db = SessionLocal()
        u = db.scalar(select(User).where(func.lower(User.email) == args.email.lower()))
        if not u:
            print("No such user")
            return 1
        p1 = getpass.getpass("New password: ")
        validate_password_strength(p1)
        u.password_hash = hash_password(p1)
        db.commit()
        print("Password updated")
    elif args.cmd == "check":
        from sqlalchemy import text

        db = SessionLocal()
        bad = db.execute(text("SELECT l.id FROM pantry_lot l LEFT JOIN stock_event e ON e.lot_id=l.id GROUP BY l.id "
                              "HAVING abs(l.quantity_base - coalesce(sum(e.delta_base),0)) > 1e-6")).all()
        print(f"{len(bad)} mismatched lots")
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
