"""Recipe versions, diffs, scaling and pantry requirements."""
from __future__ import annotations

import re
import uuid
from datetime import date

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (Food, PersonProfile, Product, Recipe, RecipeIngredient, RecipeMedia, RecipeStep, RecipeVersion,
                      utcnow)
from .flags import possible_allergens, person_conflicts, toddler_flags
from .stock import factors_for, usable_lots
from .units import FoodFactors, canonical_unit, convert, normalise_metric, uk_display, unit_kind


class IngredientIn(BaseModel):
    original_text: str | None = None
    name: str = Field(min_length=1, max_length=200)
    quantity: float | None = Field(None, ge=0)
    quantity_max: float | None = Field(None, ge=0)
    unit: str | None = None
    preparation: str | None = None
    section: str | None = None
    optional: bool = False
    food_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    proposed: bool = False
    provenance: str = "manual"
    confidence: float | None = None
    from_position: int | None = None
    note: str | None = None


class StepIn(BaseModel):
    text: str = Field(min_length=1)
    original_text: str | None = None
    timer_seconds: int | None = Field(None, ge=0)
    proposed: bool = False
    provenance: str = "manual"
    from_position: int | None = None
    media_ids: list[uuid.UUID] = []


class RecipeContent(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    cuisine: str | None = None
    tags: list[str] = []
    prep_minutes: int | None = Field(None, ge=0)
    cook_minutes: int | None = Field(None, ge=0)
    total_minutes: int | None = Field(None, ge=0)
    yield_text: str | None = None
    default_servings: float = Field(4, gt=0, le=100)
    notes: str | None = None
    ingredients: list[IngredientIn] = []
    steps: list[StepIn] = []
    # provenance
    source_url: str | None = None
    source_name: str | None = None
    source_author: str | None = None
    original_text: str | None = None
    original_values: dict = {}
    provenance: str = "manual"
    extraction_confidence: float | None = None
    unresolved_fields: list[str] = []


# ---- ingredient linking ----------------------------------------------------------------------------------------------

def _singular(name: str) -> str:
    n = name.lower().strip()
    n = re.sub(r"\b(fresh|large|small|medium|ripe|finely|roughly|chopped|sliced|diced|minced)\b", "", n).strip()
    n = re.sub(r"\s+", " ", n)
    for suf, rep in (("ies", "y"), ("oes", "o"), ("ves", "f"), ("s", "")):
        if n.endswith(suf) and len(n) > 3:
            return n[: -len(suf)] + rep
    return n


def match_food(db: Session, household_id: uuid.UUID, name: str) -> Food | None:
    """Exact (case/plural-insensitive) name or alias match only. Fuzzy suggestions are offered in the UI instead."""
    target = _singular(name)
    foods = db.scalars(select(Food).where(Food.household_id == household_id, Food.is_pet_food.is_(False)))
    for f in foods:
        if _singular(f.name) == target or any(_singular(a) == target for a in f.aliases or []):
            return f
    return None


def suggest_foods(db: Session, household_id: uuid.UUID, name: str, limit: int = 5) -> list[dict]:
    rows = db.execute(select(Food.id, Food.name, func.similarity(Food.name, name).label("s"))
                      .where(Food.household_id == household_id, Food.is_pet_food.is_(False),
                             func.similarity(Food.name, name) > 0.15)
                      .order_by(func.similarity(Food.name, name).desc()).limit(limit)).all()
    return [{"id": str(r[0]), "name": r[1], "score": round(r[2], 2)} for r in rows]


def ensure_food(db: Session, household_id: uuid.UUID, ing: IngredientIn) -> Food:
    f = match_food(db, household_id, ing.name)
    if f:
        return f
    kind = unit_kind(canonical_unit(ing.unit) if ing.unit else None)
    name = re.sub(r"\s+", " ", ing.name).strip()[:160]
    name = name[0].upper() + name[1:]
    f = Food(household_id=household_id, name=name, default_unit={"mass": "g", "volume": "ml"}.get(kind or "", "each" if kind == "count" else "g"))
    db.add(f)
    db.flush()
    return f


# ---- create versions -------------------------------------------------------------------------------------------------

def _normalise_ingredient(db: Session, ing: IngredientIn, food: Food | None) -> dict:
    unit = canonical_unit(ing.unit) if ing.unit else None
    if ing.unit and unit is None:
        unit = ing.unit.lower()[:16]
    qty = ing.quantity
    note = ing.note
    factors = FoodFactors.from_food(food)
    # Store imperial/US measures as UK metric, keeping the original wording in original_text.
    if qty is not None and unit in ("oz", "lb", "floz", "pint", "cup", "kg", "l", "cl", "dl", "mg"):
        c = normalise_metric(qty, unit, factors)
        if c.quantity is not None:
            if unit not in ("kg", "l"):
                note = "; ".join(x for x in [note, c.note or f"converted from {qty:g} {unit}"] if x)
            qty, unit = c.quantity, c.unit
    base_q = base_u = None
    conv_note = note
    if qty is not None and unit:
        target = food.default_unit if food else None
        c = convert(qty, unit, target, factors) if target else None
        if c is None or c.quantity is None:
            from .units import to_base
            c = to_base(qty, unit, factors)
        base_q, base_u = c.quantity, c.unit if c.quantity is not None else None
        if c.note and c.quantity is None:
            conv_note = "; ".join(x for x in [conv_note, c.note] if x)
    return {"quantity": qty, "unit": unit, "base_quantity": base_q, "base_unit": base_u, "conversion_note": conv_note}


def build_version(db: Session, recipe: Recipe, content: RecipeContent, *, household_id: uuid.UUID, label: str,
                  parent: RecipeVersion | None, user_id=None, status: str = "published", change_request: str | None = None,
                  change_summary: str | None = None, link_foods: bool = True) -> RecipeVersion:
    n = (db.scalar(select(func.max(RecipeVersion.version_number)).where(RecipeVersion.recipe_id == recipe.id)) or 0) + 1
    v = RecipeVersion(
        recipe_id=recipe.id, version_number=n, label=label[:120], status="draft", parent_version_id=parent.id if parent else None,
        title=content.title, description=content.description, cuisine=content.cuisine, tags=content.tags[:20],
        prep_minutes=content.prep_minutes, cook_minutes=content.cook_minutes,
        total_minutes=content.total_minutes or ((content.prep_minutes or 0) + (content.cook_minutes or 0) or None),
        yield_text=content.yield_text, default_servings=content.default_servings, notes=content.notes,
        source_url=content.source_url, source_name=content.source_name, source_author=content.source_author,
        imported_at=utcnow() if content.source_url or content.provenance in ("url", "ai") else None,
        original_text=content.original_text, original_values=content.original_values, provenance=content.provenance,
        extraction_confidence=content.extraction_confidence, unresolved_fields=content.unresolved_fields,
        change_request=change_request, change_summary=change_summary, created_by_id=user_id,
    )
    db.add(v)
    db.flush()
    names = []
    for i, ing in enumerate(content.ingredients):
        food = None
        if ing.food_id:
            food = db.get(Food, ing.food_id)
            if food is None or food.household_id != household_id:
                raise HTTPException(422, f"Unknown food for ingredient '{ing.name}'")
            if food.is_pet_food:
                raise HTTPException(422, "Pet food cannot be used in a human recipe")
        elif link_foods:
            food = ensure_food(db, household_id, ing)
        product = None
        if ing.product_id:
            product = db.get(Product, ing.product_id)
            if product is None or product.household_id != household_id:
                raise HTTPException(422, f"Unknown product for ingredient '{ing.name}'")
            if product.is_pet_food:
                raise HTTPException(422, "Pet food cannot be used in a human recipe")
            if product.kind == "medicine":
                raise HTTPException(422, "Medicines can't be recipe ingredients")
        norm = _normalise_ingredient(db, ing, food)
        db.add(RecipeIngredient(
            version_id=v.id, position=i, section=ing.section, original_text=ing.original_text or ing.name, name=ing.name,
            quantity=norm["quantity"], quantity_max=ing.quantity_max, unit=norm["unit"], base_quantity=norm["base_quantity"],
            base_unit=norm["base_unit"], conversion_note=norm["conversion_note"], preparation=ing.preparation,
            optional=ing.optional, food_id=food.id if food else None, product_id=product.id if product else None,
            unresolved=norm["quantity"] is None, proposed=ing.proposed, provenance=ing.provenance, confidence=ing.confidence,
        ))
        names.append(ing.name)
    for i, st in enumerate(content.steps):
        db.add(RecipeStep(version_id=v.id, position=i, text=st.text, original_text=st.original_text,
                          timer_seconds=st.timer_seconds, proposed=st.proposed, provenance=st.provenance))
        for mid in st.media_ids:
            m = db.get(RecipeMedia, mid)
            if m and m.recipe_id == recipe.id:
                if m.version_id is None or m.version_id == v.id:
                    m.version_id, m.step_position, m.kind = v.id, i, "step"
                else:
                    db.add(RecipeMedia(recipe_id=recipe.id, household_id=household_id, kind="step", path=m.path, mime=m.mime,
                                       width=m.width, height=m.height, source_url=m.source_url, attribution=m.attribution,
                                       is_ai_generated=m.is_ai_generated, provenance=m.provenance, alt_text=m.alt_text,
                                       version_id=v.id, step_position=i))
    db.flush()
    db.refresh(v)
    declared = []
    for ing in v.ingredients:
        if ing.product_id:
            declared += db.get(Product, ing.product_id).allergens or []
        if ing.food_id:
            declared += db.get(Food, ing.food_id).allergens or []
    v.allergens = sorted({a["allergen"] for a in possible_allergens(names, declared)})
    if parent is not None:
        v.diff = compute_diff(db, parent, v)
    if status == "published":
        v.status = "published"
        v.published_at = utcnow()
    db.flush()
    return v


def content_from_version(v: RecipeVersion) -> dict:
    return {
        "title": v.title, "description": v.description, "cuisine": v.cuisine, "tags": v.tags or [],
        "prep_minutes": v.prep_minutes, "cook_minutes": v.cook_minutes, "total_minutes": v.total_minutes,
        "yield_text": v.yield_text, "default_servings": v.default_servings, "notes": v.notes,
        "source_url": v.source_url, "source_name": v.source_name, "source_author": v.source_author,
        "original_text": v.original_text, "original_values": v.original_values or {}, "provenance": v.provenance,
        "extraction_confidence": v.extraction_confidence, "unresolved_fields": v.unresolved_fields or [],
        "ingredients": [{"original_text": i.original_text, "name": i.name, "quantity": i.quantity, "quantity_max": i.quantity_max,
                         "unit": i.unit, "preparation": i.preparation, "section": i.section, "optional": i.optional,
                         "food_id": str(i.food_id) if i.food_id else None, "product_id": str(i.product_id) if i.product_id else None,
                         "proposed": i.proposed, "provenance": i.provenance, "confidence": i.confidence,
                         "from_position": i.position} for i in v.ingredients],
        "steps": [{"text": s.text, "original_text": s.original_text, "timer_seconds": s.timer_seconds, "proposed": s.proposed,
                   "provenance": s.provenance, "from_position": s.position} for s in v.steps],
    }


# ---- diff ------------------------------------------------------------------------------------------------------------

_ING_FIELDS = ("name", "quantity", "unit", "preparation", "optional", "food_id", "product_id")


def _ing_tuple(i) -> dict:
    return {k: (str(getattr(i, k)) if k.endswith("_id") and getattr(i, k) else getattr(i, k)) for k in _ING_FIELDS}


def compute_diff(db: Session, before: RecipeVersion, after: RecipeVersion) -> dict:
    """Structured before/after diff for ingredients, steps, servings, allergens and nutrition."""
    from .nutrition_db import recipe_nutrition

    b_ings = {i.position: i for i in before.ingredients}
    used = set()
    ing_changes = []
    for a in after.ingredients:
        match = None
        for pos, b in b_ings.items():
            if pos in used:
                continue
            if _singular(b.name) == _singular(a.name):
                match = b
                break
        if match is None:
            ing_changes.append({"change": "added", "after": _ing_tuple(a), "after_display": _display(a)})
            continue
        used.add(match.position)
        bt, at = _ing_tuple(match), _ing_tuple(a)
        changed = [k for k in _ING_FIELDS if bt[k] != at[k]]
        ing_changes.append({"change": "modified" if changed else "unchanged", "fields": changed, "before": bt, "after": at,
                            "before_display": _display(match), "after_display": _display(a)})
    for pos, b in b_ings.items():
        if pos not in used:
            ing_changes.append({"change": "removed", "before": _ing_tuple(b), "before_display": _display(b)})

    b_steps = [s.text for s in before.steps]
    a_steps = [s.text for s in after.steps]
    step_changes = []
    for i, t in enumerate(a_steps):
        if t in b_steps:
            step_changes.append({"change": "unchanged", "position": i, "text": t})
        else:
            step_changes.append({"change": "added" if i >= len(b_steps) else "modified", "position": i, "text": t,
                                 "before": b_steps[i] if i < len(b_steps) else None})
    for i, t in enumerate(b_steps):
        if t not in a_steps and i >= len(a_steps):
            step_changes.append({"change": "removed", "position": i, "before": t})

    nb = recipe_nutrition(db, before)["per_serving"]["nutrients"]
    na = recipe_nutrition(db, after)["per_serving"]["nutrients"]
    nutrition = {k: {"before": nb[k]["value"], "after": na[k]["value"], "unit": na[k]["unit"],
                     "before_status": nb[k]["status"], "after_status": na[k]["status"]} for k in ("energy_kcal", "protein_g", "fat_g", "saturates_g", "carbohydrate_g", "sugars_g", "fibre_g", "salt_g")}
    return {
        "from_version_id": str(before.id), "from_version_number": before.version_number,
        "servings": {"before": before.default_servings, "after": after.default_servings},
        "ingredients": ing_changes, "steps": step_changes,
        "allergens": {"before": before.allergens or [], "after": after.allergens or [],
                      "added": sorted(set(after.allergens or []) - set(before.allergens or [])),
                      "removed": sorted(set(before.allergens or []) - set(after.allergens or []))},
        "nutrition_per_serving": nutrition,
    }


def _display(i) -> str:
    q = "" if i.quantity is None else uk_display(i.quantity, i.unit) + " "
    if i.unit and i.unit not in ("g", "ml", "each", "l", "tsp", "tbsp") and i.quantity is not None:
        q = f"{i.quantity:g} {i.unit} "
    return f"{q}{i.name}".strip()


# ---- scaling & requirements --------------------------------------------------------------------------------------------

def scaled_ingredients(v: RecipeVersion, servings: float) -> list[dict]:
    """Scale for a meal without mutating the saved version."""
    f = servings / (v.default_servings or 1)
    out = []
    for i in v.ingredients:
        q = i.quantity * f if i.quantity is not None else None
        qm = i.quantity_max * f if i.quantity_max is not None else None
        count_like = unit_kind(i.unit) == "count"
        out.append({
            "position": i.position, "name": i.name, "original_text": i.original_text, "quantity": None if q is None else round(q, 2),
            "quantity_max": qm, "unit": i.unit, "display": (f"{_round_count(q)} {i.unit if i.unit != 'each' else ''}".strip()
                                                             if count_like and q is not None else uk_display(q, i.unit) if i.unit in ("g", "ml", "l", "tsp", "tbsp") else (f"{q:g} {i.unit or ''}".strip() if q is not None else "")),
            "preparation": i.preparation, "optional": i.optional, "unresolved": i.unresolved, "food_id": str(i.food_id) if i.food_id else None,
            "product_id": str(i.product_id) if i.product_id else None, "base_quantity": i.base_quantity * f if i.base_quantity is not None else None,
            "base_unit": i.base_unit, "proposed": i.proposed, "conversion_note": i.conversion_note, "section": i.section,
        })
    return out


def _round_count(q: float) -> str:
    r = round(q * 4) / 4
    from .units import _fmt
    return _fmt(r)


def requirements(db: Session, household_id: uuid.UUID, v: RecipeVersion, servings: float, on: date | None = None) -> list[dict]:
    """What the recipe needs vs usable stock (use-by respected), per ingredient."""
    f = servings / (v.default_servings or 1)
    out = []
    for i in v.ingredients:
        row = {"position": i.position, "name": i.name, "food_id": str(i.food_id) if i.food_id else None, "optional": i.optional}
        if i.quantity is None:
            out.append({**row, "status": "unquantified", "need_base": None, "base_unit": None, "have_base": None, "missing_base": None})
            continue
        food = db.get(Food, i.food_id) if i.food_id else None
        product = db.get(Product, i.product_id) if i.product_id else None
        lots = usable_lots(db, household_id, food_id=i.food_id, product_id=i.product_id, on=on) if (i.food_id or i.product_id) else []
        unit = lots[0].base_unit if lots else (i.base_unit or (food.default_unit if food else None))
        need = None
        if unit:
            c = convert(i.quantity * f, i.unit, unit, factors_for(db, product, food))
            need = c.quantity
        have = sum(l.quantity_base - (l.reserved_base or 0) for l in lots if l.base_unit == unit) if unit else 0.0
        if need is None:
            status = "unconvertible" if lots else "missing"
            out.append({**row, "status": status, "need_base": None, "base_unit": unit, "have_base": have or None,
                        "missing_base": None, "need_display": f"{i.quantity * f:g} {i.unit}"})
            continue
        missing = max(0.0, need - have)
        status = "ok" if missing <= 1e-6 else ("short" if have > 0 else "missing")
        out.append({**row, "status": status, "need_base": round(need, 3), "base_unit": unit, "have_base": round(have, 3),
                    "missing_base": round(missing, 3), "need_display": uk_display(need, unit),
                    "missing_display": uk_display(missing, unit) if missing else None,
                    "earliest_date": min(((l.use_by or l.best_before) for l in lots if (l.use_by or l.best_before)), default=None)})
    return out


def review_flags(db: Session, household_id: uuid.UUID, v: RecipeVersion, participant_ids: list | None = None) -> dict:
    names = [f"{i.name} {i.preparation or ''}".strip() for i in v.ingredients]
    declared = []
    labels = []
    for i in v.ingredients:
        if i.product_id:
            pr = db.get(Product, i.product_id)
            declared += pr.allergens or []
            labels.append({"ingredient": i.name, "product": pr.name, "allergens": pr.allergens or [], "traces": pr.traces or [],
                           "ingredients_text": pr.ingredients_text})
    allergens = possible_allergens(names, declared)
    q = select(PersonProfile).where(PersonProfile.household_id == household_id)
    people = list(db.scalars(q))
    if participant_ids:
        people = [p for p in people if p.id in participant_ids or str(p.id) in participant_ids]
    has_children = any(p.kind == "child" for p in people)
    return {
        "possible_allergens": allergens,
        "person_conflicts": person_conflicts(people, names, allergens),
        "toddler": toddler_flags(names + [s.text for s in v.steps]) if has_children else [],
        "labels": labels,
        "disclaimer": "These flags are prompts to check labels, not a guarantee that a recipe is free from any allergen.",
    }
