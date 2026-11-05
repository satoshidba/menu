"""Postgres-backed job queue (SELECT ... FOR UPDATE SKIP LOCKED). No Redis needed.

Job inputs/results never contain credentials; receipt text stays on the receipt row, not in job payloads or logs.
"""
from __future__ import annotations

import logging
import os
import socket
import traceback
import uuid
from datetime import timedelta
from typing import Callable

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal
from ..models import ImportJob, utcnow

log = logging.getLogger("larder.jobs")
HANDLERS: dict[str, Callable[[Session, ImportJob], dict]] = {}
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


class JobFailure(Exception):
    """A user-facing failure that should not be retried."""


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn

    return deco


def enqueue(db: Session, kind: str, input: dict, *, household_id=None, user_id=None, max_attempts: int = 2) -> ImportJob:
    job = ImportJob(kind=kind, input=input, household_id=household_id, created_by_id=user_id, max_attempts=max_attempts)
    db.add(job)
    db.commit()
    if get_settings().inline_jobs:
        run_job(job.id)
        db.refresh(job)
    return job


def job_out(j: ImportJob) -> dict:
    return {"id": str(j.id), "kind": j.kind, "status": j.status, "progress": j.progress, "result": j.result,
            "error": j.error, "attempts": j.attempts, "created_at": j.created_at.isoformat() if j.created_at else None,
            "finished_at": j.finished_at.isoformat() if j.finished_at else None}


def run_job(job_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        job = db.scalar(select(ImportJob).where(ImportJob.id == job_id).with_for_update(skip_locked=True))
        if job is None or job.status not in ("queued",):
            return
        _execute(db, job)
    finally:
        db.close()


def claim_next(db: Session) -> ImportJob | None:
    job = db.scalar(select(ImportJob).where(ImportJob.status == "queued", ImportJob.run_after <= utcnow())
                    .order_by(ImportJob.created_at).limit(1).with_for_update(skip_locked=True))
    return job


def _execute(db: Session, job: ImportJob) -> None:
    fn = HANDLERS.get(job.kind)
    job.status, job.locked_at, job.locked_by = "running", utcnow(), WORKER_ID
    job.attempts += 1
    db.commit()
    try:
        if fn is None:
            raise JobFailure(f"No handler for job kind '{job.kind}'")
        result = fn(db, job)
        job.result = result or {}
        job.status, job.progress, job.error = "succeeded", 100, None
    except JobFailure as e:
        db.rollback()
        job = db.get(ImportJob, job.id)
        job.status, job.error = "failed", str(e)
    except Exception as e:  # unexpected: retry with backoff
        db.rollback()
        job = db.get(ImportJob, job.id)
        log.error("job %s (%s) failed: %s", job.id, job.kind, type(e).__name__)
        log.debug("%s", traceback.format_exc())
        job.error = f"{type(e).__name__}: {str(e)[:300]}"
        if job.attempts < job.max_attempts:
            job.status, job.run_after = "queued", utcnow() + timedelta(seconds=30 * job.attempts)
        else:
            job.status = "failed"
    job.finished_at = utcnow() if job.status in ("succeeded", "failed") else None
    job.locked_at = job.locked_by = None
    db.commit()


def work_once() -> bool:
    db = SessionLocal()
    try:
        job = claim_next(db)
        if job is None:
            db.rollback()
            return False
        _execute(db, job)
        return True
    finally:
        db.close()


def requeue_stuck(db: Session, minutes: int = 15) -> int:
    res = db.execute(text("UPDATE import_job SET status='queued', locked_at=NULL, locked_by=NULL "
                          "WHERE status='running' AND locked_at < now() - make_interval(mins => :m)"), {"m": minutes})
    db.commit()
    return res.rowcount or 0
