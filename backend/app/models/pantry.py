import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, IdMixin, TimestampMixin

__all__ = ["PantryLot", "StockEvent", "STOCK_EVENT_TYPES", "LOCATIONS"]

STOCK_EVENT_TYPES = ("purchase", "add", "consumption", "waste", "correction", "transfer", "return", "expiration", "open")
LOCATIONS = ("cupboard", "fridge", "freezer", "other")


class PantryLot(IdMixin, TimestampMixin, Base):
    """One batch of one product/food in one place with one purchase/expiry context.

    `quantity_base` is a projection maintained in the same transaction as each StockEvent; it always equals
    the sum of the lot's events' `delta_base` (verified by tests and the admin consistency check).
    """

    __tablename__ = "pantry_lot"
    __table_args__ = (
        CheckConstraint("product_id IS NOT NULL OR food_id IS NOT NULL", name="lot_has_item"),
        CheckConstraint("location IN ('cupboard','fridge','freezer','other')", name="lot_location"),
    )
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("product.id", ondelete="RESTRICT"), index=True)
    food_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("food.id", ondelete="RESTRICT"), index=True)
    location: Mapped[str] = mapped_column(String(16), default="cupboard")
    # Quantity as the user entered it, plus normalisation
    entered_quantity: Mapped[float] = mapped_column(Float)
    entered_unit: Mapped[str] = mapped_column(String(16))
    base_unit: Mapped[str] = mapped_column(String(8))  # g | ml | each
    conversion_factor: Mapped[float] = mapped_column(Float)  # base units per entered unit
    initial_quantity_base: Mapped[float] = mapped_column(Float)
    quantity_base: Mapped[float] = mapped_column(Float)  # current, projected from events
    reserved_base: Mapped[float] = mapped_column(Float, default=0)
    package_count: Mapped[int | None] = mapped_column(Integer)
    opened: Mapped[bool] = mapped_column(Boolean, default=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purchased_on: Mapped[date | None] = mapped_column(Date)
    best_before: Mapped[date | None] = mapped_column(Date)
    use_by: Mapped[date | None] = mapped_column(Date)
    date_label: Mapped[str | None] = mapped_column(String(60))  # exact wording on pack, e.g. "Display until"
    store: Mapped[str | None] = mapped_column(String(80))
    price_paid_pence: Mapped[int | None] = mapped_column(Integer)
    purchase_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase.id", ondelete="SET NULL"))
    is_pet_food: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | depleted
    notes: Mapped[str | None] = mapped_column(Text)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    product = relationship("Product")
    food = relationship("Food")


class StockEvent(IdMixin, Base):
    """Append-only ledger entry. Corrections are new events; history is never rewritten."""

    __tablename__ = "stock_event"
    __table_args__ = (
        CheckConstraint(
            "type IN ('purchase','add','consumption','waste','correction','transfer','return','expiration','open')",
            name="stock_event_type",
        ),
        CheckConstraint("NOT negative_override OR override_reason IS NOT NULL", name="override_needs_reason"),
    )
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    lot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pantry_lot.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(16))
    delta_base: Mapped[float] = mapped_column(Float)
    entered_quantity: Mapped[float | None] = mapped_column(Float)
    entered_unit: Mapped[str | None] = mapped_column(String(16))
    conversion_factor: Mapped[float | None] = mapped_column(Float)
    resulting_quantity_base: Mapped[float] = mapped_column(Float)
    from_location: Mapped[str | None] = mapped_column(String(16))
    to_location: Mapped[str | None] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)
    negative_override: Mapped[bool] = mapped_column(Boolean, default=False)
    override_reason: Mapped[str | None] = mapped_column(Text)
    cooked_batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cooked_batch.id", ondelete="SET NULL"))
    receipt_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("receipt_line.id", ondelete="SET NULL"))
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id", ondelete="SET NULL"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
