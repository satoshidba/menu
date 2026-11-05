import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, IdMixin, TimestampMixin

__all__ = ["Recipe", "RecipeVersion", "RecipeIngredient", "RecipeStep", "RecipeMedia"]


class Recipe(IdMixin, TimestampMixin, Base):
    __tablename__ = "recipe"
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    # Denormalised from the current version for fast listing/search.
    title: Mapped[str] = mapped_column(String(200))
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recipe_version.id", ondelete="SET NULL", use_alter=True, name="fk_recipe_current_version")
    )
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    favourite: Mapped[bool] = mapped_column(Boolean, default=False)
    # Media is not versioned content: the hero image lives on the recipe and can change at any time.
    hero_media_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recipe_media.id", ondelete="SET NULL", use_alter=True, name="fk_recipe_hero_media")
    )
    versions: Mapped[list["RecipeVersion"]] = relationship(
        back_populates="recipe", foreign_keys="RecipeVersion.recipe_id", order_by="RecipeVersion.version_number",
        cascade="all, delete-orphan", passive_deletes=True,
    )


class RecipeVersion(IdMixin, TimestampMixin, Base):
    """Immutable once `status == 'published'` (enforced by a database trigger)."""

    __tablename__ = "recipe_version"
    __table_args__ = (UniqueConstraint("recipe_id", "version_number"),)
    recipe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recipe.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(120), default="Original")
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | published
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recipe_version.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    cuisine: Mapped[str | None] = mapped_column(String(60))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    prep_minutes: Mapped[int | None] = mapped_column(Integer)
    cook_minutes: Mapped[int | None] = mapped_column(Integer)
    total_minutes: Mapped[int | None] = mapped_column(Integer)
    yield_text: Mapped[str | None] = mapped_column(String(120))
    default_servings: Mapped[float] = mapped_column(Float, default=4)
    notes: Mapped[str | None] = mapped_column(Text)
    allergens: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    # Source / provenance
    source_url: Mapped[str | None] = mapped_column(String(1000))
    source_name: Mapped[str | None] = mapped_column(String(200))
    source_author: Mapped[str | None] = mapped_column(String(200))
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    original_text: Mapped[str | None] = mapped_column(Text)
    original_values: Mapped[dict] = mapped_column(JSONB, default=dict)
    provenance: Mapped[str] = mapped_column(String(32), default="manual")
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    unresolved_fields: Mapped[list[str]] = mapped_column(ARRAY(String(80)), default=list)
    # Change tracking relative to parent
    change_request: Mapped[str | None] = mapped_column(Text)
    change_summary: Mapped[str | None] = mapped_column(Text)
    diff: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id", ondelete="SET NULL"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    recipe: Mapped[Recipe] = relationship(back_populates="versions", foreign_keys=[recipe_id])
    ingredients: Mapped[list["RecipeIngredient"]] = relationship(
        back_populates="version", order_by="RecipeIngredient.position", cascade="all, delete-orphan",
        passive_deletes=True,
    )
    steps: Mapped[list["RecipeStep"]] = relationship(
        back_populates="version", order_by="RecipeStep.position", cascade="all, delete-orphan",
        passive_deletes=True,
    )


class RecipeIngredient(IdMixin, Base):
    __tablename__ = "recipe_ingredient"
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recipe_version.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(80))
    original_text: Mapped[str | None] = mapped_column(Text)  # source wording, preserved verbatim
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[float | None] = mapped_column(Float)  # null = unresolved, never guessed
    quantity_max: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(16))  # canonical unit: g, kg, ml, l, tsp, tbsp, each, ...
    base_quantity: Mapped[float | None] = mapped_column(Float)  # normalised to g/ml/each where possible
    base_unit: Mapped[str | None] = mapped_column(String(8))
    conversion_note: Mapped[str | None] = mapped_column(String(300))
    preparation: Mapped[str | None] = mapped_column(String(200))
    optional: Mapped[bool] = mapped_column(Boolean, default=False)
    food_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("food.id", ondelete="SET NULL"), index=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("product.id", ondelete="SET NULL"))
    unresolved: Mapped[bool] = mapped_column(Boolean, default=False)
    proposed: Mapped[bool] = mapped_column(Boolean, default=False)  # filled in by AI rather than the source
    provenance: Mapped[str] = mapped_column(String(32), default="manual")
    confidence: Mapped[float | None] = mapped_column(Float)
    version: Mapped[RecipeVersion] = relationship(back_populates="ingredients")


class RecipeStep(IdMixin, Base):
    __tablename__ = "recipe_step"
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recipe_version.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    original_text: Mapped[str | None] = mapped_column(Text)
    timer_seconds: Mapped[int | None] = mapped_column(Integer)
    proposed: Mapped[bool] = mapped_column(Boolean, default=False)
    provenance: Mapped[str] = mapped_column(String(32), default="manual")
    version: Mapped[RecipeVersion] = relationship(back_populates="steps")


class RecipeMedia(IdMixin, TimestampMixin, Base):
    """Images belong to the recipe and are shared across its versions."""

    __tablename__ = "recipe_media"
    recipe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recipe.id", ondelete="CASCADE", use_alter=True,
                                                            name="fk_media_recipe"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="photo")  # photo | step | illustration
    # Step images attach to a step position within a version; copied forward when a new version keeps the step.
    version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("recipe_version.id", ondelete="CASCADE"), index=True)
    step_position: Mapped[int | None] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(String(300))
    mime: Mapped[str] = mapped_column(String(40), default="image/jpeg")
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    focal_x: Mapped[float] = mapped_column(Float, default=0.5)  # crop focus, 0..1
    focal_y: Mapped[float] = mapped_column(Float, default=0.5)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    attribution: Mapped[str | None] = mapped_column(String(300))
    is_ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    provenance: Mapped[str] = mapped_column(String(32), default="manual")
    alt_text: Mapped[str | None] = mapped_column(String(300))
