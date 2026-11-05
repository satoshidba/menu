import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base

__all__ = ["Base", "IdMixin", "TimestampMixin", "utcnow", "PROVENANCE_VALUES", "REVIEW_STATUSES"]

# Every imported or derived field records where it came from.
PROVENANCE_VALUES = ("manual", "label", "cofid", "open_food_facts", "open_pet_food_facts", "receipt", "ai", "url", "calculated")
REVIEW_STATUSES = ("unreviewed", "reviewed", "rejected")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now(), index=True
    )
