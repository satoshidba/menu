"""Database-aware nutrition: choose the best record for an item and compute recipe/portion values."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Food, NutritionRecord, Product, RecipeIngredient, RecipeVersion
from .nutrition import Contribution, NutritionAccumulator, clean_values, scale
from .units import FoodFactors, convert

# Lower number wins. Verified label beats everything; community data is last.
SOURCE_PRIORITY = {"label": 0, "manual": 1, "cofid": 2, "open_food_facts": 3, "ai": 4}


def _rank(r: NutritionRecord) -> tuple:
    return (0 if r.verified else 1, SOURCE_PRIORITY.get(r.source, 9), -(r.retrieved_at.timestamp() if r.retrieved_at else 0))


def best_record(db: Session, *, product_id: uuid.UUID | None = None, food_id: uuid.UUID | None = None) -> NutritionRecord | None:
    """Product-specific data first, then the generic food's data."""
    if product_id:
        recs = list(db.scalars(select(NutritionRecord).where(NutritionRecord.product_id == product_id, NutritionRecord.is_current.is_(True))))
        if recs:
            return sorted(recs, key=_rank)[0]
        p = db.get(Product, product_id)
        if p and p.is_pet_food:
            return None
        food_id = food_id or (p.food_id if p else None)
    if food_id:
        recs = list(db.scalars(select(NutritionRecord).where(NutritionRecord.food_id == food_id, NutritionRecord.is_current.is_(True))))
        if recs:
            return sorted(recs, key=_rank)[0]
    return None


def record_dict(r: NutritionRecord | None) -> dict | None:
    if r is None:
        return None
    values, derived = clean_values(r.nutrients or {})
    flags = {**derived, **(r.flags or {})}
    return {
        "id": str(r.id), "source": r.source, "source_ref": r.source_ref, "source_version": r.source_version,
        "retrieved_at": r.retrieved_at.isoformat() if r.retrieved_at else None, "basis": r.basis,
        "serving_size_g": r.serving_size_g, "verified": r.verified, "confidence": r.confidence,
        "values": values, "flags": flags,
        "missing": [k for k, v in values.items() if v is None],
        "is_estimate": not r.verified,
    }


def amount_on_basis(quantity: float, unit: str, basis: str, factors: FoodFactors) -> tuple[float | None, str | None]:
    """Express `quantity unit` in the record's basis unit (g for 100g, ml for 100ml)."""
    target = "ml" if basis == "100ml" else "g"
    c = convert(quantity, unit, target, factors)
    if c.quantity is None and target == "g":
        c2 = convert(quantity, unit, "ml", factors)
        if c2.quantity is not None and factors.density_g_per_ml:
            return c2.quantity * factors.density_g_per_ml, None
    return c.quantity, c.note if c.quantity is None else None


def item_contribution(db: Session, *, label: str, quantity: float | None, unit: str | None,
                      food: Food | None = None, product: Product | None = None) -> Contribution:
    if product is not None and product.is_pet_food or (food is not None and food.is_pet_food):
        return Contribution(label, None, reason="Pet food is excluded from human nutrition")
    if product is not None and product.kind == "medicine":
        return Contribution(label, None, reason="Medicines are not counted as nutrition")
    if quantity is None or unit is None:
        return Contribution(label, None, reason="Amount not specified")
    rec = best_record(db, product_id=product.id if product else None, food_id=food.id if food else (product.food_id if product else None))
    if rec is None:
        return Contribution(label, None, reason="No nutrition data linked")
    f_food = food or (db.get(Food, product.food_id) if product and product.food_id else None)
    factors = FoodFactors.from_food(f_food)
    if product is not None and product.package_base_quantity and product.base_unit in ("g", "ml"):
        for u in ("pack", "packet", "tin", "can", "jar", "bag", "bottle", "pot"):
            factors.unit_grams.setdefault(u, product.package_base_quantity)
    if rec.basis == "dose":
        doses = doses_from(quantity, unit, product)
        if doses is None:
            return Contribution(label, None, reason=f"Record supplements in doses ({product.dose_unit if product else 'dose'})")
        values, _ = clean_values(rec.nutrients or {})
        return Contribution(label, {k: (None if v is None else v * doses) for k, v in values.items()}, estimated=not rec.verified,
                            reason=f"{rec.source} per dose{' (verified)' if rec.verified else ''}")
    amount, why = amount_on_basis(quantity, unit, rec.basis, factors)
    if amount is None:
        return Contribution(label, None, reason=why or "Cannot convert amount to weight")
    values, _ = clean_values(rec.nutrients or {})
    return Contribution(label, scale(values, amount), estimated=not rec.verified,
                        reason=f"{rec.source}{' (verified)' if rec.verified else ''}")


def doses_from(quantity: float, unit: str, product: Product | None) -> float | None:
    """How many label doses `quantity unit` is. One dose = product.dose_amount × product.dose_unit."""
    if unit == "dose":
        return quantity
    if product is None or not product.dose_unit:
        return None
    per = product.dose_amount or 1.0
    if unit == product.dose_unit:
        return quantity / per
    from .units import convert
    c = convert(quantity, unit, product.dose_unit)
    return c.quantity / per if c.quantity is not None else None


def recipe_nutrition(db: Session, version: RecipeVersion, servings: float | None = None) -> dict:
    """Total and per-serving nutrition for a recipe version, scaled to `servings` (defaults to the recipe's)."""
    base_servings = version.default_servings or 1
    target = servings or base_servings
    factor = target / base_servings
    acc = NutritionAccumulator()
    for ing in version.ingredients:
        if ing.optional:
            continue
        food = db.get(Food, ing.food_id) if ing.food_id else None
        product = db.get(Product, ing.product_id) if ing.product_id else None
        q = ing.quantity * factor if ing.quantity is not None else None
        acc.add(item_contribution(db, label=ing.name, quantity=q, unit=ing.unit, food=food, product=product))
    total = acc.result()
    per = acc.result(divisor=target)
    return {
        "recipe_version_id": str(version.id), "servings": target, "scale_factor": factor,
        "total": total, "per_serving": per,
        "basis_note": "Calculated from raw ingredient quantities; cooking losses are not modelled unless a yield is recorded.",
    }


def ingredient_nutrition_status(db: Session, ing: RecipeIngredient) -> dict:
    food = db.get(Food, ing.food_id) if ing.food_id else None
    product = db.get(Product, ing.product_id) if ing.product_id else None
    c = item_contribution(db, label=ing.name, quantity=ing.quantity, unit=ing.unit, food=food, product=product)
    return {"known": c.values is not None, "reason": c.reason}
