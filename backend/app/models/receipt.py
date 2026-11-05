import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, IdMixin, TimestampMixin

__all__ = ["Receipt", "ReceiptLine", "Purchase", "PriceObservation", "Offer"]


class Receipt(IdMixin, TimestampMixin, Base):
    __tablename__ = "receipt"
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="uploaded")  # uploaded|parsing|review|applied|failed
    source_kind: Mapped[str] = mapped_column(String(16), default="photo")  # photo | pdf | screenshot | text | email
    retailer: Mapped[str | None] = mapped_column(String(60))
    store_name: Mapped[str | None] = mapped_column(String(120))
    purchased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_number: Mapped[str | None] = mapped_column(String(80))
    subtotal_pence: Mapped[int | None] = mapped_column(Integer)
    total_pence: Mapped[int | None] = mapped_column(Integer)  # printed total
    payment_total_pence: Mapped[int | None] = mapped_column(Integer)  # after vouchers/points
    basket_adjustments: Mapped[list] = mapped_column(JSONB, default=list)  # unallocated coupons etc.
    file_path: Mapped[str | None] = mapped_column(String(300))  # original; deletable independently
    file_mime: Mapped[str | None] = mapped_column(String(60))
    file_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    file_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extracted_text: Mapped[str | None] = mapped_column(Text)
    parser: Mapped[str | None] = mapped_column(String(40))
    parse_confidence: Mapped[float | None] = mapped_column(Float)
    parse_warnings: Mapped[list] = mapped_column(JSONB, default=list)
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("receipt.id", ondelete="SET NULL"))
    fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)  # retailer+date+total+number
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id", ondelete="SET NULL"))
    apply_idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id", ondelete="SET NULL"))
    lines: Mapped[list["ReceiptLine"]] = relationship(
        back_populates="receipt", order_by="ReceiptLine.position", cascade="all, delete-orphan"
    )


class ReceiptLine(IdMixin, TimestampMixin, Base):
    __tablename__ = "receipt_line"
    receipt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("receipt.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    raw_text: Mapped[str] = mapped_column(Text)  # exact retailer wording, never edited
    description: Mapped[str] = mapped_column(String(200))  # reviewed description
    kind: Mapped[str] = mapped_column(String(16), default="item")  # item|discount|return|bag|deposit|other|ignore
    quantity: Mapped[float] = mapped_column(Float, default=1)
    weight_kg: Mapped[float | None] = mapped_column(Float)
    unit_price_pence: Mapped[int | None] = mapped_column(Integer)
    line_total_pence: Mapped[int | None] = mapped_column(Integer)  # shelf price x qty
    discount_pence: Mapped[int] = mapped_column(Integer, default=0)  # allocated line discount (positive number)
    discount_description: Mapped[str | None] = mapped_column(String(200))
    applies_to_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("receipt_line.id", ondelete="SET NULL"))
    corrected: Mapped[bool] = mapped_column(Boolean, default=False)
    original_parsed: Mapped[dict] = mapped_column(JSONB, default=dict)
    confidence: Mapped[float | None] = mapped_column(Float)
    match_status: Mapped[str] = mapped_column(String(16), default="unmatched")  # unmatched|suggested|confirmed|skip
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("product.id", ondelete="SET NULL"))
    food_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("food.id", ondelete="SET NULL"))
    is_substitution: Mapped[bool] = mapped_column(Boolean, default=False)
    substituted_for: Mapped[str | None] = mapped_column(String(200))
    create_lot: Mapped[bool] = mapped_column(Boolean, default=True)
    location: Mapped[str] = mapped_column(String(16), default="cupboard")
    best_before: Mapped[date | None] = mapped_column(Date)
    use_by: Mapped[date | None] = mapped_column(Date)
    receipt: Mapped[Receipt] = relationship(back_populates="lines")


class Purchase(IdMixin, TimestampMixin, Base):
    __tablename__ = "purchase"
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    receipt_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("receipt.id", ondelete="SET NULL"), index=True)
    receipt_line_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("receipt_line.id", ondelete="SET NULL"), unique=True
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("product.id", ondelete="SET NULL"), index=True)
    food_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("food.id", ondelete="SET NULL"), index=True)
    store: Mapped[str | None] = mapped_column(String(120))
    retailer: Mapped[str | None] = mapped_column(String(60))
    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    package_count: Mapped[float] = mapped_column(Float, default=1)
    quantity_base: Mapped[float | None] = mapped_column(Float)
    base_unit: Mapped[str | None] = mapped_column(String(8))
    shelf_price_pence: Mapped[int | None] = mapped_column(Integer)
    paid_price_pence: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual | receipt
    is_return: Mapped[bool] = mapped_column(Boolean, default=False)
    is_pet_food: Mapped[bool] = mapped_column(Boolean, default=False)


class PriceObservation(IdMixin, TimestampMixin, Base):
    """One observed price. Distinct pack sizes and stores are never collapsed."""

    __tablename__ = "price_observation"
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    food_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("food.id", ondelete="SET NULL"), index=True)
    purchase_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase.id", ondelete="CASCADE"))
    offer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("offer.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))  # paid | shelf | offer
    store: Mapped[str | None] = mapped_column(String(120))
    retailer: Mapped[str | None] = mapped_column(String(60))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    price_pence: Mapped[int] = mapped_column(Integer)  # for the whole quantity below
    package_count: Mapped[float] = mapped_column(Float, default=1)
    quantity_base: Mapped[float | None] = mapped_column(Float)  # total base units this price bought
    base_unit: Mapped[str | None] = mapped_column(String(8))
    standard_unit: Mapped[str | None] = mapped_column(String(8))  # kg | L | item
    unit_price_pence: Mapped[float | None] = mapped_column(Float)  # pence per standard unit
    promotion: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(16))  # receipt | manual | offer
    confidence: Mapped[float] = mapped_column(Float, default=1.0)


class Offer(IdMixin, TimestampMixin, Base):
    """A manually saved or externally sourced offer. Only 'current' when source, capture time, store and expiry exist."""

    __tablename__ = "offer"
    __table_args__ = (UniqueConstraint("household_id", "source", "external_ref"),)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"))
    food_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("food.id", ondelete="SET NULL"))
    retailer: Mapped[str] = mapped_column(String(60))
    store_region: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(300))
    price_pence: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(40), default="manual")
    source_url: Mapped[str | None] = mapped_column(String(1000))
    external_ref: Mapped[str | None] = mapped_column(String(120))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
