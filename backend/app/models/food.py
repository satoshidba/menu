import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, IdMixin, TimestampMixin, utcnow

__all__ = ["Food", "Product", "Barcode", "NutritionRecord", "CofidFood", "ExternalProductCache"]


class Food(IdMixin, TimestampMixin, Base):
    """A generic ingredient concept (e.g. "dried pasta"). Products are branded packages of a food."""

    __tablename__ = "food"
    __table_args__ = (UniqueConstraint("household_id", "name"),)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String(160)), default=list)
    category: Mapped[str | None] = mapped_column(String(60))  # used for aisle grouping
    default_unit: Mapped[str] = mapped_column(String(8), default="g")  # g | ml | each
    density_g_per_ml: Mapped[float | None] = mapped_column(Float)
    each_weight_g: Mapped[float | None] = mapped_column(Float)  # only used when explicitly confirmed
    each_weight_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Ingredient-specific volume-to-weight factors, e.g. {"cup": {"g": 125, "source": "manual"}}
    conversions: Mapped[dict] = mapped_column(JSONB, default=dict)
    allergens: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    cofid_code: Mapped[str | None] = mapped_column(String(16))  # explicit, human-reviewed mapping
    is_pet_food: Mapped[bool] = mapped_column(Boolean, default=False)
    min_stock_base: Mapped[float | None] = mapped_column(Float)  # manual low-stock threshold in base unit
    notes: Mapped[str | None] = mapped_column(Text)


class Product(IdMixin, TimestampMixin, Base):
    """A specific branded package. Label data wins over community data once verified."""

    __tablename__ = "product"
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    food_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("food.id", ondelete="SET NULL"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    brand: Mapped[str | None] = mapped_column(String(120))
    package_quantity: Mapped[float | None] = mapped_column(Float)  # as printed, e.g. 500
    package_unit: Mapped[str | None] = mapped_column(String(12))  # as printed, e.g. g
    package_base_quantity: Mapped[float | None] = mapped_column(Float)  # normalised, in base_unit
    base_unit: Mapped[str] = mapped_column(String(8), default="g")  # g | ml | each
    image_path: Mapped[str | None] = mapped_column(String(300))
    image_source: Mapped[str | None] = mapped_column(String(300))
    ingredients_text: Mapped[str | None] = mapped_column(Text)
    allergens: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    traces: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    category: Mapped[str | None] = mapped_column(String(60))
    is_pet_food: Mapped[bool] = mapped_column(Boolean, default=False)
    pet_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pet_profile.id", ondelete="SET NULL"))
    provenance: Mapped[str] = mapped_column(String(32), default="manual")
    review_status: Mapped[str] = mapped_column(String(16), default="reviewed")
    min_stock_base: Mapped[float | None] = mapped_column(Float)
    # food | pet_food | supplement | medicine. Supplements count towards nutrition per dose; medicines never do.
    kind: Mapped[str] = mapped_column(String(16), default="food", server_default="food", index=True)
    # Label / prescription directions as entered by the household. Larder never calculates doses.
    dose_amount: Mapped[float | None] = mapped_column(Float)
    dose_unit: Mapped[str | None] = mapped_column(String(16))  # tablet, capsule, ml, drop, scoop, g, sachet, gummy
    label_min_interval_hours: Mapped[float | None] = mapped_column(Float)
    label_max_doses_24h: Mapped[int | None] = mapped_column()
    active_ingredients: Mapped[str | None] = mapped_column(String(300))
    label_directions: Mapped[str | None] = mapped_column(Text)
    barcodes: Mapped[list["Barcode"]] = relationship(back_populates="product", cascade="all, delete-orphan")
    food: Mapped[Food | None] = relationship()


class Barcode(IdMixin, TimestampMixin, Base):
    __tablename__ = "barcode"
    __table_args__ = (UniqueConstraint("household_id", "code"),)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(32))
    symbology: Mapped[str | None] = mapped_column(String(16))
    product: Mapped[Product] = relationship(back_populates="barcodes")


class NutritionRecord(IdMixin, TimestampMixin, Base):
    """Nutrient values for a food or product, with provenance.

    `nutrients` maps nutrient key -> number or null. A missing key or null means *unknown*, never zero.
    `flags` maps nutrient key -> "trace" | "estimate" | "derived".
    """

    __tablename__ = "nutrition_record"
    __table_args__ = (Index("ix_nutrition_target", "food_id", "product_id", "is_current"),)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    food_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("food.id", ondelete="CASCADE"))
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(32))  # label | manual | cofid | open_food_facts | ai
    source_ref: Mapped[str | None] = mapped_column(String(300))  # CoFID code, OFF barcode, label photo path
    source_version: Mapped[str | None] = mapped_column(String(60))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    basis: Mapped[str] = mapped_column(String(8), default="100g")  # 100g | 100ml | dose (one dose_unit of a supplement)
    serving_size_g: Mapped[float | None] = mapped_column(Float)
    nutrients: Mapped[dict] = mapped_column(JSONB, default=dict)
    flags: Mapped[dict] = mapped_column(JSONB, default=dict)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text)


class CofidFood(Base):
    """Read-only reference rows from the UK CoFID dataset (Crown copyright, Open Government Licence v3.0).

    Kept in its own table to respect the source licence and allow re-import by dataset version.
    """

    __tablename__ = "cofid_food"
    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    dataset_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    group_code: Mapped[str | None] = mapped_column(String(16))
    nutrients: Mapped[dict] = mapped_column(JSONB, default=dict)
    flags: Mapped[dict] = mapped_column(JSONB, default=dict)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExternalProductCache(IdMixin, Base):
    """Cached community product lookups (Open Food Facts / Open Pet Food Facts, ODbL)."""

    __tablename__ = "external_product_cache"
    __table_args__ = (UniqueConstraint("source", "code"),)
    source: Mapped[str] = mapped_column(String(32))
    code: Mapped[str] = mapped_column(String(32))
    found: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
