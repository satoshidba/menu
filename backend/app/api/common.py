"""Shared API helpers: household-scoped lookup, pagination, dates, idempotency middleware."""
from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import date, datetime

from fastapi import HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from ..db import SessionLocal
from ..models import IdempotencyRecord


def get_owned(db: Session, model, obj_id, household_id: uuid.UUID, what: str = "Item"):
    obj = db.get(model, obj_id)
    if obj is None or getattr(obj, "household_id", household_id) != household_id:
        raise HTTPException(404, f"{what} not found")
    return obj


def iso(v):
    if v is None:
        return None
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def sid(v):
    return str(v) if v is not None else None


class Page:
    """Opaque cursor pagination over (sort_key, id)."""

    def __init__(self, limit: int = Query(50, ge=1, le=500), cursor: str | None = Query(None)):
        self.limit = limit
        self.cursor = cursor
        self.offset = 0
        if cursor:
            try:
                self.offset = int(json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())["o"])
            except Exception as e:
                raise HTTPException(400, "Bad cursor") from e

    def wrap(self, items: list, has_more: bool) -> dict:
        nxt = None
        if has_more:
            nxt = base64.urlsafe_b64encode(json.dumps({"o": self.offset + self.limit}).encode()).decode()
        return {"items": items, "next_cursor": nxt}

    def apply(self, db: Session, stmt):
        rows = list(db.scalars(stmt.offset(self.offset).limit(self.limit + 1)))
        return rows[: self.limit], len(rows) > self.limit


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Replays the stored response for a repeated `Idempotency-Key` on a mutating /api/v1 request."""

    async def dispatch(self, request: Request, call_next):
        key = request.headers.get("idempotency-key")
        if not key or request.method in ("GET", "HEAD", "OPTIONS") or not request.url.path.startswith("/api/v1"):
            return await call_next(request)
        if len(key) > 120:
            return JSONResponse({"detail": "Idempotency-Key too long"}, 400)
        body = await request.body()
        req_hash = hashlib.sha256(request.method.encode() + request.url.path.encode() + body).hexdigest()
        from ..auth import optional_principal

        db = SessionLocal()
        try:
            p = optional_principal(request, db)
            if p is None:
                return await call_next(request)
            hid = p.household_id
            rec = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.household_id == hid, IdempotencyRecord.key == key))
            if rec:
                if rec.request_hash != req_hash:
                    return JSONResponse({"detail": "Idempotency-Key reused with a different request"}, 422)
                return JSONResponse(rec.response, rec.status_code, headers={"Idempotent-Replay": "true"})
        finally:
            db.close()

        response = await call_next(request)
        if response.status_code < 500 and response.headers.get("content-type", "").startswith("application/json"):
            chunks = [c async for c in response.body_iterator]
            raw = b"".join(chunks)
            db = SessionLocal()
            try:
                db.add(IdempotencyRecord(household_id=hid, key=key, method=request.method, path=request.url.path,
                                         request_hash=req_hash, status_code=response.status_code,
                                         response=json.loads(raw or b"null")))
                db.commit()
            except (IntegrityError, json.JSONDecodeError):
                db.rollback()
            finally:
                db.close()
            headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
            return Response(raw, response.status_code, headers=headers, media_type=response.media_type)
        return response


def parse_date_param(v: str | None) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError as e:
        raise HTTPException(422, f"Use an ISO date (YYYY-MM-DD), not '{v}'") from e
