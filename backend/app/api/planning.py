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
from ..models import Food, MealPlan, PersonProfile, Product, Recipe, RecipeMedia, RecipeVersion, ShoppingListItem
from ..services import shopping
from ..services.recipes import requirements
from ..services.units import canonical_unit, uk_display
from .common import get_owned, iso, sid

router = APIRouter(prefix="/api/v1", tags=["planning"])


def plan_out(db: Session, mp: MealPlan, with_requirements: bool = False) -> dict:
    out = {"id": sid(mp.id), "date": iso(mp.date), "slot": mp.slot, "title": mp.title, "servings": mp.servings,
           "recipe_version_id": sid(mp.recipe_version_id), "food_id": sid(mp.food_id), "product_id": sid(mp.product_id),
           "participant_ids": [sid(x) for x in mp.participant_ids or []], "notes": mp.notes, "status": mp.status,
           "cooked_batch_id": sid(mp.cooked_batch_id), "recipe_id": None, "hero": None}
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
    return {"from": iso(start), "to": iso(end), "meals": [plan_out(db, m) for m in rows]}


class PlanIn(BaseModel):
    date: date
    slot: str = Field("dinner", pattern="^(breakfast|lunch|dinner|snack)$")
    recipe_version_id: uuid.UUID | None = None
    recipe_id: uuid.UUID | None = None
    food_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    title: str | None = None
    servings: float = Field(4, gt=0, le=100)
    participant_ids: list[uuid.UUID] = []
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
    for pid in body.participant_ids:
        get_owned(db, PersonProfile, pid, p.household_id, "Person")
    return data


@router.post("/plan", status_code=201)
def add_plan(body: PlanIn, p: Principal = Depends(require("plan:write")), db: Session = Depends(get_db)):
    mp = MealPlan(household_id=p.household_id, **_resolve(db, p, body))
    db.add(mp)
    db.flush()
    shopping.generate(db, p.household_id)
    db.commit()
    return plan_out(db, mp, with_requirements=True)


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
