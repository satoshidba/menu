"""Shopping list = planned meals' requirements minus usable pantry stock (use-by and reservations respected)."""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Food, MealPlan, PantryLot, Product, RecipeVersion, ShoppingListItem
from .stock import factors_for, usable_lots
from .units import convert, uk_display


def generate(db: Session, household_id: uuid.UUID, start: date | None = None, end: date | None = None) -> dict:
    start = start or date.today()
    end = end or start + timedelta(days=7)
    plans = list(db.scalars(select(MealPlan).where(MealPlan.household_id == household_id, MealPlan.status == "planned",
                                                   MealPlan.date >= start, MealPlan.date <= end).order_by(MealPlan.date)))
    # Aggregate needs per food in the food's pantry base unit, earliest-needed first.
    needs: dict[uuid.UUID, dict] = {}
    unquantified: list[dict] = []
    for mp in plans:
        if not mp.recipe_version_id:
            continue
        v = db.get(RecipeVersion, mp.recipe_version_id)
        f = mp.servings / (v.default_servings or 1)
        for ing in v.ingredients:
            if ing.optional:
                continue
            if not ing.food_id:
                unquantified.append({"name": ing.name, "meal": mp.title, "reason": "Not linked to a food"})
                continue
            food = db.get(Food, ing.food_id)
            product = db.get(Product, ing.product_id) if ing.product_id else None
            if ing.quantity is None:
                n = needs.setdefault(food.id, {"food": food, "base_unit": None, "need": 0.0, "plans": [], "earliest": mp.date, "unquantified": True})
                n["plans"].append(mp)
                continue
            lots = usable_lots(db, household_id, food_id=food.id)
            unit = lots[0].base_unit if lots else (food.default_unit or ing.base_unit)
            c = convert(ing.quantity * f, ing.unit, unit, factors_for(db, product, food))
            if c.quantity is None:
                unquantified.append({"name": ing.name, "meal": mp.title, "reason": c.note})
                n = needs.setdefault(food.id, {"food": food, "base_unit": None, "need": 0.0, "plans": [], "earliest": mp.date, "unquantified": True})
                n["plans"].append(mp)
                continue
            n = needs.setdefault(food.id, {"food": food, "base_unit": unit, "need": 0.0, "plans": [], "earliest": mp.date})
            if n.get("base_unit") is None:
                n["base_unit"] = unit
            if n["base_unit"] != unit:
                unquantified.append({"name": ing.name, "meal": mp.title, "reason": f"Mixed units ({unit} vs {n['base_unit']})"})
                continue
            n["need"] += c.quantity
            n["plans"].append(mp)

    wanted: dict[uuid.UUID, dict] = {}
    for fid, n in needs.items():
        food = n["food"]
        lots = usable_lots(db, household_id, food_id=fid, on=n["earliest"])
        have = sum(max(0.0, l.quantity_base - (l.reserved_base or 0)) for l in lots if l.base_unit == n["base_unit"])
        if n.get("unquantified") and not n["need"]:
            if not lots:
                wanted[fid] = {"food": food, "quantity": None, "unit": None, "reason": "Needed for " + _meals(n["plans"]) + " (amount not specified)",
                               "plans": n["plans"]}
            continue
        missing = n["need"] - have
        if missing > 1e-6:
            wanted[fid] = {"food": food, "quantity": round(missing, 3), "unit": n["base_unit"], "plans": n["plans"],
                           "reason": f"Need {uk_display(n['need'], n['base_unit'])} for {_meals(n['plans'])}; "
                                     f"{uk_display(have, n['base_unit'])} usable in stock"}

    # Low-stock thresholds (manual minimums)
    low: dict[uuid.UUID, dict] = {}
    for food in db.scalars(select(Food).where(Food.household_id == household_id, Food.min_stock_base.is_not(None))):
        lots = usable_lots(db, household_id, food_id=food.id, include_pet=food.is_pet_food)
        have = sum(l.quantity_base for l in lots)
        if have <= food.min_stock_base and food.id not in wanted:
            low[food.id] = {"food": food, "reason": f"Low stock: {uk_display(have, food.default_unit)} left "
                                                    f"(minimum {uk_display(food.min_stock_base, food.default_unit)})"}

    existing = list(db.scalars(select(ShoppingListItem).where(ShoppingListItem.household_id == household_id,
                                                              ShoppingListItem.origin.in_(("plan", "low_stock")))))
    by_key = {(i.origin, i.food_id): i for i in existing}
    touched = set()
    for fid, w in wanted.items():
        item = by_key.get(("plan", fid))
        if item is None:
            item = ShoppingListItem(household_id=household_id, food_id=fid, name=w["food"].name, origin="plan")
            db.add(item)
        touched.add(("plan", fid))
        item.reason = w["reason"]
        item.aisle = w["food"].category
        item.source_plan_ids = [mp.id for mp in w["plans"]]
        if not item.user_edited:
            item.quantity, item.unit = w["quantity"], w["unit"]
            item.base_quantity, item.base_unit = w["quantity"], w["unit"]
        item.details = {"required_base": w["quantity"], "base_unit": w["unit"]}
    for fid, l in low.items():
        item = by_key.get(("low_stock", fid))
        if item is None:
            item = ShoppingListItem(household_id=household_id, food_id=fid, name=l["food"].name, origin="low_stock",
                                    is_pet_food=l["food"].is_pet_food)
            db.add(item)
        touched.add(("low_stock", fid))
        item.reason, item.aisle = l["reason"], l["food"].category
    # Remove generated rows that are no longer needed (unless the user ticked/edited them).
    for key, item in by_key.items():
        if key not in touched and not item.checked and not item.user_edited:
            db.delete(item)
    db.flush()
    return {"from": start.isoformat(), "to": end.isoformat(), "planned_meals": len(plans), "unresolved": unquantified}


def _meals(plans: list[MealPlan]) -> str:
    titles = list(dict.fromkeys(p.title for p in plans))
    return ", ".join(titles[:3]) + (f" and {len(titles) - 3} more" if len(titles) > 3 else "")
