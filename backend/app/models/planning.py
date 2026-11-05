import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IdMixin, TimestampMixin

__all__ = ["MealPlan", "CookedBatch", "MealConsumption", "ShoppingListItem", "Absence", "Regimen", "DoseLog", "MEAL_SLOTS",
           "ABSENCE_REASONS"]

MEAL_SLOTS = ("breakfast", "lunch", "dinner", "snack")


class MealPlan(IdMixin, TimestampMixin, Base):
    __tablename__ = "meal_plan"
    __table_args__ = (Index("ix_meal_plan_date", "household_id", "date"),)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"))
    date: Mapped[date] = mapped_column(Date)
    slot: Mapped[str] = mapped_column(String(16), default="dinner")
    recipe_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recipe_version.id", ondelete="SET NULL"))
    food_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("food.id", ondelete="SET NULL"))
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("product.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(200))
    servings: Mapped[float] = mapped_column(Float, default=4)
    participant_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="planned")  # planned | cooked | skipped
    cooked_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cooked_batch.id", ondelete="SET NULL", use_alter=True, name="fk_meal_plan_cooked_batch")
    )


class CookedBatch(IdMixin, TimestampMixin, Base):
    """What was actually cooked: the real scale, ingredients used and what happened to the food."""

    __tablename__ = "cooked_batch"
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    recipe_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recipe_version.id", ondelete="SET NULL"))
    meal_plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("meal_plan.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(200))
    cooked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    servings_made: Mapped[float] = mapped_column(Float)
    scale_factor: Mapped[float] = mapped_column(Float, default=1)
    total_cooked_weight_g: Mapped[float | None] = mapped_column(Float)
    leftover_servings: Mapped[float] = mapped_column(Float, default=0)
    waste_servings: Mapped[float] = mapped_column(Float, default=0)
    leftover_lot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pantry_lot.id", ondelete="SET NULL"))
    ingredients_used: Mapped[list] = mapped_column(JSONB, default=list)  # snapshot incl. lots deducted
    nutrition_total: Mapped[dict] = mapped_column(JSONB, default=dict)  # snapshot at cook time
    nutrition_per_serving: Mapped[dict] = mapped_column(JSONB, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id", ondelete="SET NULL"))


class MealConsumption(IdMixin, TimestampMixin, Base):
    """What one person ate. `nutrition` is a frozen snapshot so history never shifts under later edits."""

    __tablename__ = "meal_consumption"
    __table_args__ = (Index("ix_consumption_person_time", "person_id", "consumed_at"),)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person_profile.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(String(80), unique=True)  # stable id for downstream dedupe
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/London")
    meal_slot: Mapped[str | None] = mapped_column(String(16))
    cooked_batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cooked_batch.id", ondelete="SET NULL"))
    recipe_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recipe_version.id", ondelete="SET NULL"))
    food_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("food.id", ondelete="SET NULL"))
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("product.id", ondelete="SET NULL"))
    description: Mapped[str] = mapped_column(String(200))
    amount: Mapped[float] = mapped_column(Float)
    amount_unit: Mapped[str] = mapped_column(String(16))  # serving | g | ml | each
    amount_grams: Mapped[float | None] = mapped_column(Float)
    nutrition: Mapped[dict] = mapped_column(JSONB, default=dict)  # key -> value|null
    nutrition_units: Mapped[dict] = mapped_column(JSONB, default=dict)
    provenance: Mapped[dict] = mapped_column(JSONB, default=dict)  # sources, completeness, estimate flags
    confidence: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id", ondelete="SET NULL"))


class ShoppingListItem(IdMixin, TimestampMixin, Base):
    __tablename__ = "shopping_list_item"
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    food_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("food.id", ondelete="SET NULL"))
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("product.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(16))
    base_quantity: Mapped[float | None] = mapped_column(Float)
    base_unit: Mapped[str | None] = mapped_column(String(8))
    origin: Mapped[str] = mapped_column(String(16), default="manual")  # manual | plan | low_stock | forecast
    reason: Mapped[str | None] = mapped_column(Text)  # human-readable why
    source_plan_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    aisle: Mapped[str | None] = mapped_column(String(60))
    shop: Mapped[str | None] = mapped_column(String(60))
    checked: Mapped[bool] = mapped_column(Boolean, default=False)
    snoozed_until: Mapped[date | None] = mapped_column(Date)
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False)
    user_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    is_pet_food: Mapped[bool] = mapped_column(Boolean, default=False)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


ABSENCE_REASONS = ("social", "work", "study", "travel", "other")


class Absence(IdMixin, TimestampMixin, Base):
    """A person away for some meals between two dates (inclusive). `slots` empty = away for the whole day.

    With `weekdays` set (0 = Monday … 6 = Sunday) it repeats weekly on those days from `start_date`, until `end_date`
    or indefinitely when `end_date` is null (e.g. "evening class every Tuesday").
    """

    __tablename__ = "absence"
    __table_args__ = (Index("ix_absence_range", "household_id", "start_date", "end_date"),)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"))
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person_profile.id", ondelete="CASCADE"), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    slots: Mapped[list[str]] = mapped_column(ARRAY(String(16)), default=list)  # e.g. ["dinner"]; [] = all meals
    weekdays: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list, server_default="{}")
    reason: Mapped[str] = mapped_column(String(16), default="other")
    note: Mapped[str | None] = mapped_column(String(200))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id", ondelete="SET NULL"))

    def covers(self, d: date, slot: str) -> bool:
        if d < self.start_date or (self.end_date is not None and d > self.end_date):
            return False
        if self.weekdays and d.weekday() not in self.weekdays:
            return False
        return not self.slots or slot in self.slots

    def covers_day(self, d: date) -> bool:
        return self.covers(d, self.slots[0] if self.slots else "dinner")


class Regimen(IdMixin, TimestampMixin, Base):
    """A person's routine for a supplement or medicine, as told to the household (label, GP, pharmacist).

    `times_per_day` drives the "due today" checklist; it is a reminder of what was entered, not dosing advice.
    """

    __tablename__ = "regimen"
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person_profile.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"))
    dose_amount: Mapped[float] = mapped_column(Float, default=1)
    dose_unit: Mapped[str] = mapped_column(String(16))
    times_per_day: Mapped[int] = mapped_column(default=1)
    as_needed: Mapped[bool] = mapped_column(Boolean, default=False)  # e.g. pain relief: no "due" reminders
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    instructions: Mapped[str | None] = mapped_column(Text)  # as written by the prescriber / on the label
    source: Mapped[str | None] = mapped_column(String(120))  # e.g. "GP, 3 Nov", "Label", "Health visitor"
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class DoseLog(IdMixin, TimestampMixin, Base):
    """A dose actually given/taken. Supplements also get a MealConsumption snapshot so they appear in intake."""

    __tablename__ = "dose_log"
    __table_args__ = (Index("ix_dose_person_time", "person_id", "given_at"),)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person_profile.id", ondelete="CASCADE"))
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("product.id", ondelete="SET NULL"))
    regimen_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("regimen.id", ondelete="SET NULL"))
    consumption_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("meal_consumption.id", ondelete="SET NULL"))
    description: Mapped[str] = mapped_column(String(200))
    amount: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(16))
    given_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    given_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id", ondelete="SET NULL"))
    warnings_acknowledged: Mapped[list] = mapped_column(JSONB, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
