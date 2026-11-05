"""Meal plan (week view) and shopping list."""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Principal, require
from ..db import get_db
from ..auth import audit
from ..models import (ABSENCE_REASONS, Absence, Food, MealPlan, PersonProfile, Product, Recipe, RecipeMedia, RecipeVersion,
                      ShoppingListItem)
from ..services import shopping
from ..services.absences import (absences_between, apply_to_meal, meal_conflicts, present_people, suggested_servings)
from ..services.recipes import requirements
from ..services.units import canonical_unit, uk_display
from .common import get_owned, iso, sid

router = APIRouter(prefix="/api/v1", tags=["planning"])


DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def absence_out(a: Absence, names: dict | None = None) -> dict:
    repeats = None
    if a.weekdays:
        repeats = "every " + ", ".join(DAY_NAMES[d] for d in sorted(a.weekdays)) + (f" until {a.end_date:%d %b}" if a.end_date else "")
    return {"id": sid(a.id), "person_id": sid(a.person_id), "person_name": (names or {}).get(a.person_id), "start_date": iso(a.start_date),
            "end_date": iso(a.end_date), "slots": a.slots or [], "all_day": not a.slots, "reason": a.reason, "note": a.note,
            "weekdays": sorted(a.weekdays or []), "repeats": repeats}


def away_by_date(absences: list, start: date, end: date, names: dict) -> dict:
    out: dict[str, list] = {}
    d = start
    while d <= end:
        for a in absences:
            if a.covers_day(d):
                out.setdefault(d.isoformat(), []).append(absence_out(a, names))
        d += timedelta(days=1)
    return out


def _names(db: Session, household_id) -> dict:
    return {p.id: p.name for p in db.scalars(select(PersonProfile).where(PersonProfile.household_id == household_id))}


def plan_out(db: Session, mp: MealPlan, with_requirements: bool = False, absences: list | None = None, names: dict | None = None) -> dict:
    conflicts = meal_conflicts(db, mp, absences)
    names = names if names is not None else (_names(db, mp.household_id) if conflicts else {})
    out = {"id": sid(mp.id), "date": iso(mp.date), "slot": mp.slot, "title": mp.title, "servings": mp.servings,
           "recipe_version_id": sid(mp.recipe_version_id), "food_id": sid(mp.food_id), "product_id": sid(mp.product_id),
           "participant_ids": [sid(x) for x in mp.participant_ids or []], "notes": mp.notes, "status": mp.status,
           "cooked_batch_id": sid(mp.cooked_batch_id), "recipe_id": None, "hero": None,
           "away_conflicts": [absence_out(a, names) for a in conflicts] if mp.status == "planned" else []}
    if mp.recipe_version_id:
        v = db.get(RecipeVersion, mp.recipe_version_id)
        if v:
            r = db.get(Recipe, v.recipe_id)
            out["recipe_id"] = sid(r.id)
            out["version_label"] = v.label
            out["total_minutes"] = v.total_minutes
            if r.hero_media_id:
                m = db.get(RecipeMedia, r.hero_media_id)
                out["hero"] = {"url": f"/api/v1/media/{m.path}", "focal_x": m.focal_x, "focal_y": m.focal_y} if m else None
            if with_requirements:
                out["requirements"] = requirements(db, mp.household_id, v, mp.servings, on=mp.date)
    return out


@router.get("/plan")
def get_plan(from_: date | None = Query(None, alias="from"), to: date | None = None, p: Principal = Depends(require("plan:read")),
             db: Session = Depends(get_db)):
    start = from_ or date.today() - timedelta(days=date.today().weekday())
    end = to or start + timedelta(days=6)
    rows = db.scalars(select(MealPlan).where(MealPlan.household_id == p.household_id, MealPlan.date >= start, MealPlan.date <= end)
                      .order_by(MealPlan.date, MealPlan.slot))
    absences = absences_between(db, p.household_id, start, end)
    names = _names(db, p.household_id)
    return {"from": iso(start), "to": iso(end), "meals": [plan_out(db, m, absences=absences, names=names) for m in rows],
            "absences": [absence_out(a, names) for a in absences], "away_by_date": away_by_date(absences, start, end, names)}


class PlanIn(BaseModel):
    date: date
    slot: str = Field("dinner", pattern="^(breakfast|lunch|dinner|snack)$")
    recipe_version_id: uuid.UUID | None = None
    recipe_id: uuid.UUID | None = None
    food_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    title: str | None = None
    servings: float | None = Field(None, gt=0, le=100, description="Defaults to the usual portions of the people at home")
    participant_ids: list[uuid.UUID] | None = Field(None, description="Defaults to everyone not marked away for this meal")
    notes: str | None = None


def _resolve(db: Session, p: Principal, body: PlanIn) -> dict:
    data = body.model_dump(exclude={"recipe_id"})
    if body.recipe_id and not body.recipe_version_id:
        r = get_owned(db, Recipe, body.recipe_id, p.household_id, "Recipe")
        data["recipe_version_id"] = r.current_version_id
    if data["recipe_version_id"]:
        v = db.get(RecipeVersion, data["recipe_version_id"])
        if v is None or db.get(Recipe, v.recipe_id).household_id != p.household_id or v.status != "published":
            raise HTTPException(422, "Unknown recipe version")
        data["title"] = data["title"] or v.title
    elif body.food_id:
        data["title"] = data["title"] or get_owned(db, Food, body.food_id, p.household_id, "Food").name
    elif body.product_id:
        data["title"] = data["title"] or get_owned(db, Product, body.product_id, p.household_id, "Product").name
    if not data["title"]:
        raise HTTPException(422, "Choose a recipe or give the meal a name")
    if body.participant_ids is None:
        present = present_people(db, p.household_id, body.date, body.slot)
        data["participant_ids"] = [x.id for x in present]
    else:
        present = [get_owned(db, PersonProfile, pid, p.household_id, "Person") for pid in body.participant_ids]
    if data["servings"] is None:
        data["servings"] = suggested_servings(present) if present else 1
    return data


@router.post("/plan", status_code=201)
def add_plan(body: PlanIn, p: Principal = Depends(require("plan:write")), db: Session = Depends(get_db)):
    mp = MealPlan(household_id=p.household_id, **_resolve(db, p, body))
    db.add(mp)
    db.flush()
    shopping.generate(db, p.household_id)
    db.commit()
    return plan_out(db, mp, with_requirements=True)


@router.post("/plan/{plan_id}/apply-absences")
def apply_absences(plan_id: uuid.UUID, p: Principal = Depends(require("plan:write")), db: Session = Depends(get_db)):
    """Plan a meal without the people who are away: removes them and reduces servings by their usual portions."""
    mp = get_owned(db, MealPlan, plan_id, p.household_id, "Meal")
    result = apply_to_meal(db, mp)
    if result["changed"]:
        shopping.generate(db, p.household_id)
    db.commit()
    return {**result, "meal": plan_out(db, mp, with_requirements=True)}


# ---- absences (who's out) -------------------------------------------------------------------------------------------

class AbsenceIn(BaseModel):
    person_id: uuid.UUID
    start_date: date
    end_date: date | None = None
    slots: list[str] = Field([], description="Meals missed, e.g. ['dinner']; empty = all day")
    weekdays: list[int] = Field([], description="Repeat weekly on these days (0 = Monday … 6 = Sunday); end_date may then be empty")
    reason: str = Field("other", description="social | work | study | travel | other")
    note: str | None = Field(None, max_length=200)


def _check_absence(db: Session, p: Principal, body: AbsenceIn) -> dict:
    get_owned(db, PersonProfile, body.person_id, p.household_id, "Person")
    if any(d < 0 or d > 6 for d in body.weekdays):
        raise HTTPException(422, "Weekdays run from 0 (Monday) to 6 (Sunday)")
    end = body.end_date if body.weekdays else (body.end_date or body.start_date)
    if end is not None and end < body.start_date:
        raise HTTPException(422, "The end date is before the start date")
    if not body.weekdays and (end - body.start_date).days > 366:
        raise HTTPException(422, "One-off absences can be up to a year long; use a weekly repeat for regular commitments")
    bad = set(body.slots) - {"breakfast", "lunch", "dinner", "snack"}
    if bad:
        raise HTTPException(422, f"Unknown meal: {', '.join(sorted(bad))}")
    if body.reason not in ABSENCE_REASONS:
        raise HTTPException(422, f"Reason should be one of: {', '.join(ABSENCE_REASONS)}")
    return {**body.model_dump(), "end_date": end, "weekdays": sorted(set(body.weekdays)),
            "slots": sorted(set(body.slots), key=["breakfast", "lunch", "dinner", "snack"].index)}


def _affected(db: Session, household_id, a: Absence) -> list[dict]:
    last = a.end_date or a.start_date + timedelta(days=120)
    rows = db.scalars(select(MealPlan).where(MealPlan.household_id == household_id, MealPlan.status == "planned",
                                             MealPlan.date >= a.start_date, MealPlan.date <= last))
    return [{"id": sid(m.id), "date": iso(m.date), "slot": m.slot, "title": m.title, "servings": m.servings}
            for m in rows if a.covers(m.date, m.slot) and a.person_id in (m.participant_ids or [])]


@router.get("/absences")
def list_absences(from_: date | None = Query(None, alias="from"), to: date | None = None, person_id: uuid.UUID | None = None,
                  p: Principal = Depends(require("plan:read")), db: Session = Depends(get_db)):
    start = from_ or date.today()
    end = to or start + timedelta(days=60)
    rows = [a for a in absences_between(db, p.household_id, start, end) if person_id is None or a.person_id == person_id]
    names = _names(db, p.household_id)
    return [absence_out(a, names) for a in rows]


@router.post("/absences", status_code=201)
def create_absence(body: AbsenceIn, p: Principal = Depends(require("plan:write")), db: Session = Depends(get_db)):
    """Mark someone away. The response lists planned meals that include them, so they can be updated."""
    a = Absence(household_id=p.household_id, created_by_id=p.user_id, **_check_absence(db, p, body))
    db.add(a)
    db.flush()
    audit(db, p, "absence.created", "absence", a.id, {"reason": a.reason})
    db.commit()
    return {"absence": absence_out(a, _names(db, p.household_id)), "affected_meals": _affected(db, p.household_id, a)}


@router.patch("/absences/{absence_id}")
def update_absence(absence_id: uuid.UUID, body: AbsenceIn, p: Principal = Depends(require("plan:write")), db: Session = Depends(get_db)):
    a = get_owned(db, Absence, absence_id, p.household_id, "Absence")
    for k, v in _check_absence(db, p, body).items():
        setattr(a, k, v)
    db.commit()
    return {"absence": absence_out(a, _names(db, p.household_id)), "affected_meals": _affected(db, p.household_id, a)}


@router.delete("/absences/{absence_id}")
def delete_absence(absence_id: uuid.UUID, p: Principal = Depends(require("plan:write")), db: Session = Depends(get_db)):
    a = get_owned(db, Absence, absence_id, p.household_id, "Absence")
    db.delete(a)
    db.commit()
    return {"ok": True}


@router.get("/plan/{plan_id}")
def get_plan_item(plan_id: uuid.UUID, p: Principal = Depends(require("plan:read")), db: Session = Depends(get_db)):
    return plan_out(db, get_owned(db, MealPlan, plan_id, p.household_id, "Meal"), with_requirements=True)


@router.patch("/plan/{plan_id}")
def update_plan(plan_id: uuid.UUID, body: dict, p: Principal = Depends(require("plan:write")), db: Session = Depends(get_db)):
    mp = get_owned(db, MealPlan, plan_id, p.household_id, "Meal")
    current = {"date": mp.date, "slot": mp.slot, "recipe_version_id": mp.recipe_version_id, "food_id": mp.food_id,
               "product_id": mp.product_id, "title": mp.title, "servings": mp.servings, "participant_ids": mp.participant_ids,
               "notes": mp.notes}
    if "recipe_version_id" in body or "recipe_id" in body:
        current["title"] = None
    data = _resolve(db, p, PlanIn(**{**current, **body}))
    for k, v in data.items():
        setattr(mp, k, v)
    if body.get("status") in ("planned", "skipped"):
        mp.status = body["status"]
    shopping.generate(db, p.household_id)
    db.commit()
    return plan_out(db, mp, with_requirements=True)


@router.delete("/plan/{plan_id}")
def delete_plan(plan_id: uuid.UUID, p: Principal = Depends(require("plan:write")), db: Session = Depends(get_db)):
    mp = get_owned(db, MealPlan, plan_id, p.household_id, "Meal")
    db.delete(mp)
    db.flush()
    shopping.generate(db, p.household_id)
    db.commit()
    return {"ok": True}


# ---- shopping list -----------------------------------------------------------------------------------------------------

def item_out(db: Session, i: ShoppingListItem) -> dict:
    prod = db.get(Product, i.product_id) if i.product_id else None
    return {"id": sid(i.id), "name": i.name, "food_id": sid(i.food_id), "product_id": sid(i.product_id),
            "product_name": f"{prod.brand + ' ' if prod and prod.brand else ''}{prod.name}" if prod else None,
            "quantity": i.quantity, "unit": i.unit, "display": uk_display(i.quantity, i.unit) if i.quantity is not None else None,
            "origin": i.origin, "reason": i.reason, "aisle": i.aisle or "Other", "shop": i.shop, "checked": i.checked,
            "snoozed_until": iso(i.snoozed_until), "dismissed": i.dismissed, "user_edited": i.user_edited,
            "is_pet_food": i.is_pet_food, "source_plan_ids": [sid(x) for x in i.source_plan_ids or []], "updated_at": iso(i.updated_at)}


@router.get("/shopping-list")
def get_list(refresh: bool = True, group_by: str = Query("aisle", pattern="^(aisle|shop)$"),
             p: Principal = Depends(require("plan:read")), db: Session = Depends(get_db)):
    info = None
    if refresh and p.has("plan:write"):
        info = shopping.generate(db, p.household_id)
        db.commit()
    today = date.today()
    items = [i for i in db.scalars(select(ShoppingListItem).where(ShoppingListItem.household_id == p.household_id)
                                   .order_by(ShoppingListItem.checked, ShoppingListItem.aisle, ShoppingListItem.name))
             if not i.dismissed and not (i.snoozed_until and i.snoozed_until > today)]
    groups: dict[str, list] = {}
    for i in items:
        key = (i.shop or "Any shop") if group_by == "shop" else (i.aisle or "Other")
        groups.setdefault(key, []).append(item_out(db, i))
    return {"groups": [{"name": k, "items": v} for k, v in sorted(groups.items())], "count": len(items),
            "generated": info}


@router.post("/shopping-list/generate")
def generate_list(body: dict | None = None, p: Principal = Depends(require("plan:write")), db: Session = Depends(get_db)):
    body = body or {}
    start = date.fromisoformat(body["from"]) if body.get("from") else None
    end = date.fromisoformat(body["to"]) if body.get("to") else None
    info = shopping.generate(db, p.household_id, start, end)
    db.commit()
    return {**info, **get_list(refresh=False, group_by="aisle", p=p, db=db)}


class ItemIn(BaseModel):
    name: str = Field(max_length=200)
    quantity: float | None = Field(None, gt=0)
    unit: str | None = None
    food_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    shop: str | None = None
    aisle: str | None = None


@router.post("/shopping-list/items", status_code=201)
def add_item(body: ItemIn, p: Principal = Depends(require("plan:write")), db: Session = Depends(get_db)):
    food = get_owned(db, Food, body.food_id, p.household_id, "Food") if body.food_id else None
    prod = get_owned(db, Product, body.product_id, p.household_id, "Product") if body.product_id else None
    i = ShoppingListItem(household_id=p.household_id, name=body.name, quantity=body.quantity,
                         unit=canonical_unit(body.unit) if body.unit else None, food_id=food.id if food else (prod.food_id if prod else None),
                         product_id=prod.id if prod else None, shop=body.shop,
                         aisle=body.aisle or (food.category if food else prod.category if prod else None), origin="manual",
                         reason="Added by hand", is_pet_food=bool(prod and prod.is_pet_food), user_edited=True)
    db.add(i)
    db.commit()
    return item_out(db, i)


class ItemPatch(BaseModel):
    checked: bool | None = None
    quantity: float | None = Field(None, gt=0)
    unit: str | None = None
    product_id: uuid.UUID | None = None
    shop: str | None = None
    aisle: str | None = None
    snoozed_until: date | None = None
    dismissed: bool | None = None
    name: str | None = None


@router.patch("/shopping-list/items/{item_id}")
def patch_item(item_id: uuid.UUID, body: ItemPatch, p: Principal = Depends(require("plan:write")), db: Session = Depends(get_db)):
    i = get_owned(db, ShoppingListItem, item_id, p.household_id, "Item")
    data = body.model_dump(exclude_unset=True)
    if "product_id" in data and data["product_id"]:
        prod = get_owned(db, Product, data["product_id"], p.household_id, "Product")
        if i.food_id and prod.food_id and prod.food_id != i.food_id:
            raise HTTPException(422, "That product is for a different food")
    if "unit" in data and data["unit"]:
        data["unit"] = canonical_unit(data["unit"]) or data["unit"]
    for k, v in data.items():
        setattr(i, k, v)
    if {"quantity", "unit", "name"} & data.keys():
        i.user_edited = True
    db.commit()
    return item_out(db, i)


@router.delete("/shopping-list/items/{item_id}")
def delete_item(item_id: uuid.UUID, p: Principal = Depends(require("plan:write")), db: Session = Depends(get_db)):
    i = get_owned(db, ShoppingListItem, item_id, p.household_id, "Item")
    if i.origin != "manual":
        i.dismissed = True  # generated rows would come back; remember the dismissal instead
    else:
        db.delete(i)
    db.commit()
    return {"ok": True}


@router.post("/shopping-list/clear-checked")
def clear_checked(p: Principal = Depends(require("plan:write")), db: Session = Depends(get_db)):
    n = 0
    for i in db.scalars(select(ShoppingListItem).where(ShoppingListItem.household_id == p.household_id, ShoppingListItem.checked.is_(True))):
        db.delete(i)
        n += 1
    db.commit()
    return {"removed": n}
