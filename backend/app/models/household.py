import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, IdMixin, TimestampMixin

__all__ = ["Household", "User", "UserSession", "ApiToken", "PersonProfile", "PetProfile"]


class Household(IdMixin, TimestampMixin, Base):
    __tablename__ = "household"
    name: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/London")
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    locale: Mapped[str] = mapped_column(String(16), default="en-GB")


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "user_account"
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="member")  # admin | member
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(IdMixin, TimestampMixin, Base):
    """Server-side session. Only a SHA-256 hash of the cookie value is stored."""

    __tablename__ = "user_session"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user: Mapped[User] = relationship()


class ApiToken(IdMixin, TimestampMixin, Base):
    """Scoped bearer token for other apps (e.g. an activity tracker).

    `person_ids` restricts which human profiles' intake the token may read. Child profiles additionally
    require the explicit `intake:read:children` scope.
    """

    __tablename__ = "api_token"
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(16), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    person_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rotated_from_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class PersonProfile(IdMixin, TimestampMixin, Base):
    """A human who eats. Toddlers are profiles, not login accounts."""

    __tablename__ = "person_profile"
    __table_args__ = (UniqueConstraint("household_id", "name"),)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(16), default="adult")  # adult | child
    birth_month: Mapped[date | None] = mapped_column(Date)  # optional, first of month; used only for age-appropriate flags
    allergies: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)
    dislikes: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)
    dietary_preferences: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)
    default_portion: Mapped[float] = mapped_column(default=1.0)  # servings, used only as a planning default
    notes: Mapped[str | None] = mapped_column(Text)
    colour: Mapped[str | None] = mapped_column(String(16))


class PetProfile(IdMixin, TimestampMixin, Base):
    """A pet. Its food lives in separate inventory and never enters human nutrition."""

    __tablename__ = "pet_profile"
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    species: Mapped[str] = mapped_column(String(40), default="dog")
    feeding_plan: Mapped[str | None] = mapped_column(Text)  # as recorded by the household, e.g. vet-approved plan
    feeding_plan_source: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
