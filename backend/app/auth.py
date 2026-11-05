"""Authentication: Argon2id passwords, server-side sessions (cookie + CSRF token) and scoped API tokens."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import ApiToken, AuditEvent, PersonProfile, RateLimitHit, User, UserSession, utcnow

SESSION_COOKIE = "larder_session"
CSRF_HEADER = "x-csrf-token"
TOKEN_PREFIX = "lrd_"

ALL_SCOPES = {
    "recipes:read": "Read recipes and versions",
    "recipes:write": "Create and change recipes",
    "pantry:read": "Read pantry stock",
    "pantry:write": "Change pantry stock",
    "receipts:write": "Upload and apply receipts",
    "plan:read": "Read meal plans and the shopping list",
    "plan:write": "Change meal plans and the shopping list",
    "intake:read": "Read intake for the listed adult profiles",
    "intake:read:children": "Also read intake for listed child profiles",
    "intake:write": "Record meals eaten",
    "export:read": "Download a full household export",
}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

_hasher = PasswordHasher()  # Argon2id with library defaults (time_cost=3, memory_cost=64 MiB)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hash_: str, password: str) -> bool:
    try:
        return _hasher.verify(hash_, password)
    except (VerificationError, InvalidHashError):
        return False


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def validate_password_strength(password: str) -> None:
    if len(password) < 10:
        raise HTTPException(422, "Use at least 10 characters for the password.")
    if password.lower() in {"password123", "letmein123", "qwertyuiop"}:
        raise HTTPException(422, "That password is too common.")


@dataclass
class Principal:
    household_id: uuid.UUID
    user: User | None = None
    token: ApiToken | None = None
    scopes: set[str] = field(default_factory=set)
    session: UserSession | None = None

    @property
    def is_user(self) -> bool:
        return self.user is not None

    @property
    def user_id(self) -> uuid.UUID | None:
        return self.user.id if self.user else None

    @property
    def token_id(self) -> uuid.UUID | None:
        return self.token.id if self.token else None

    def has(self, scope: str) -> bool:
        return self.is_user or scope in self.scopes

    def can_read_person_intake(self, person: PersonProfile) -> bool:
        if self.is_user:
            return True
        if "intake:read" not in self.scopes or self.token is None:
            return False
        if person.id not in (self.token.person_ids or []):
            return False
        if person.kind == "child" and "intake:read:children" not in self.scopes:
            return False
        return True


def create_session(db: Session, user: User, user_agent: str | None) -> tuple[str, UserSession]:
    raw = secrets.token_urlsafe(32)
    s = UserSession(
        user_id=user.id, token_hash=sha256(raw), csrf_token=secrets.token_urlsafe(24),
        expires_at=utcnow() + timedelta(days=get_settings().session_days), user_agent=(user_agent or "")[:300],
        last_seen_at=utcnow(),
    )
    db.add(s)
    user.last_login_at = utcnow()
    db.flush()
    return raw, s


def generate_api_token() -> tuple[str, str, str]:
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    raw = f"{TOKEN_PREFIX}{prefix}_{secret}"
    return raw, prefix, sha256(raw)


def _resolve(request: Request, db: Session) -> Principal | None:
    authz = request.headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        raw = authz[7:].strip()
        tok = db.scalar(select(ApiToken).where(ApiToken.token_hash == sha256(raw)))
        if tok is None or tok.revoked_at is not None or (tok.expires_at and tok.expires_at < utcnow()):
            raise HTTPException(401, "Invalid or revoked token", headers={"WWW-Authenticate": "Bearer"})
        if tok.last_used_at is None or (utcnow() - tok.last_used_at) > timedelta(minutes=5):
            tok.last_used_at = utcnow()
            db.commit()
        return Principal(household_id=tok.household_id, token=tok, scopes=set(tok.scopes or []))
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        s = db.scalar(select(UserSession).where(UserSession.token_hash == sha256(raw)))
        if s and s.revoked_at is None and s.expires_at > utcnow() and s.user.is_active:
            if s.last_seen_at is None or (utcnow() - s.last_seen_at) > timedelta(minutes=10):
                s.last_seen_at = utcnow()
                db.commit()
            return Principal(household_id=s.user.household_id, user=s.user, session=s, scopes=set(ALL_SCOPES))
    return None


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    p = _resolve(request, db)
    if p is None:
        raise HTTPException(401, "Sign in required")
    # CSRF: cookie-authenticated mutations must echo the session's CSRF token in a header.
    if p.session is not None and request.method not in SAFE_METHODS:
        if not secrets.compare_digest(request.headers.get(CSRF_HEADER, ""), p.session.csrf_token):
            raise HTTPException(403, "Missing or invalid CSRF token")
    request.state.principal = p
    return p


def optional_principal(request: Request, db: Session) -> Principal | None:
    try:
        return _resolve(request, db)
    except HTTPException:
        return None


def require(scope: str):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if not p.has(scope):
            raise HTTPException(403, f"This token lacks the '{scope}' scope")
        return p

    return dep


def require_user(p: Principal = Depends(current_principal)) -> Principal:
    if not p.is_user:
        raise HTTPException(403, "This action needs a signed-in adult, not an API token")
    return p


def require_admin(p: Principal = Depends(require_user)) -> Principal:
    if p.user.role != "admin":
        raise HTTPException(403, "Administrator only")
    return p


def rate_limit(db: Session, bucket: str, per_minute: int) -> None:
    """Simple DB-backed sliding window; adequate for a two-user household and survives restarts."""
    since = utcnow() - timedelta(minutes=1)
    db.execute(delete(RateLimitHit).where(RateLimitHit.at < utcnow() - timedelta(hours=1)))
    n = db.scalar(select(func.count()).select_from(RateLimitHit).where(RateLimitHit.bucket == bucket, RateLimitHit.at >= since))
    if n >= per_minute:
        db.commit()
        raise HTTPException(429, "Too many requests. Please wait a minute and try again.")
    db.add(RateLimitHit(bucket=bucket))
    db.commit()


def audit(db: Session, p: Principal | None, action: str, entity_type: str | None = None, entity_id=None,
          details: dict | None = None, household_id: uuid.UUID | None = None) -> None:
    db.add(AuditEvent(
        household_id=household_id or (p.household_id if p else None),
        actor_user_id=p.user_id if p else None,
        actor_token_id=p.token_id if p else None,
        action=action, entity_type=entity_type, entity_id=str(entity_id) if entity_id else None,
        details=details or {}, at=utcnow(),
    ))


def now() -> datetime:
    return utcnow()
