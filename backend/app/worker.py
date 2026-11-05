"""Background worker: job queue, nightly backups, housekeeping. Run with `python -m app.worker`."""
from __future__ import annotations

import logging
import shutil
import signal
import time
from datetime import datetime, timedelta, timezone

from .config import get_settings
from .db import SessionLocal
from .models import ImportJob
from .services.jobs import enqueue, handler, requeue_stuck, work_once

log = logging.getLogger("larder.worker")
_stop = False


def register_handlers() -> None:
    from .services import receipts, recipe_jobs  # noqa: F401

    @handler("backup")
    def _backup(db, job: ImportJob) -> dict:
        from .services.backup import create_backup

        path = create_backup(label=job.input.get("label", "scheduled"))
        return {"file": path.name, "size": path.stat().st_size}


def housekeeping() -> None:
    s = get_settings()
    staging = s.media_dir / "staging"
    if staging.exists():
        cutoff = time.time() - 2 * 86400
        for d in staging.iterdir():
            if d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
    db = SessionLocal()
    try:
        n = requeue_stuck(db)
        if n:
            log.warning("requeued %d stuck jobs", n)
    finally:
        db.close()


def maybe_schedule_backup(last: datetime | None) -> datetime | None:
    s = get_settings()
    now = datetime.now(timezone.utc)
    if not s.backup_enabled or now.hour != s.backup_hour_utc:
        return last
    if last and now - last < timedelta(hours=20):
        return last
    db = SessionLocal()
    try:
        enqueue(db, "backup", {"label": "scheduled"}, max_attempts=2)
    finally:
        db.close()
    log.info("scheduled backup queued")
    return now


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    register_handlers()
    s = get_settings()
    for d in (s.media_dir, s.receipts_dir, s.backups_dir):
        d.mkdir(parents=True, exist_ok=True)

    def stop(*_):
        global _stop
        _stop = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    last_backup, last_house = None, 0.0
    log.info("worker started")
    while not _stop:
        try:
            if time.time() - last_house > 600:
                housekeeping()
                last_house = time.time()
            last_backup = maybe_schedule_backup(last_backup)
            if not work_once():
                time.sleep(1.5)
        except Exception:  # noqa: BLE001 - keep the worker alive; details are in the job row
            log.exception("worker loop error")
            time.sleep(5)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
