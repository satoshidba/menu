import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IdMixin, TimestampMixin, utcnow

__all__ = ["ImportJob", "AuditEvent", "IdempotencyRecord", "RateLimitHit"]


class ImportJob(IdMixin, TimestampMixin, Base):
    """Background job (Postgres-backed queue). `input`/`result` never contain secrets or receipt text."""

    __tablename__ = "import_job"
    household_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued|running|succeeded|failed
    input: Mapped[dict] = mapped_column(JSONB, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(80))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id", ondelete="SET NULL"))


class AuditEvent(IdMixin, Base):
    __tablename__ = "audit_event"
    household_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id", ondelete="SET NULL"))
    actor_token_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("api_token.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class IdempotencyRecord(IdMixin, Base):
    """Stores the response of a mutation keyed by the client-supplied Idempotency-Key header."""

    __tablename__ = "idempotency_record"
    __table_args__ = (UniqueConstraint("household_id", "key"),)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(120))
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(300))
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RateLimitHit(IdMixin, Base):
    __tablename__ = "rate_limit_hit"
    bucket: Mapped[str] = mapped_column(String(120), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
