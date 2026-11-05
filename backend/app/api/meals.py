"""Cooking a batch, recording what each person ate, and per-person intake with JSON/CSV export."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Principal, audit, require
from ..db import get_db
from ..models import (CookedBatch, Food, Household, MealConsumption, MealPlan, PantryLot, PersonProfile, Product, Recipe,
                      RecipeVersion, utcnow)
from ..services.nutrition import CORE_NUTRIENTS, NUTRIENTS, NutritionAccumulator, scale_summary, snapshot_values
from ..services.nutrition_db import item_contribution
from ..services.stock import EventInput, consume_fefo, create_lot, factors_for, record_event, usable_lots
from ..services.units import canonical_unit, convert
from .common import get_owned, iso, sid

router = APIRouter(prefix="/api/v1", tags=["meals & intake"])


def consumption_out(c: MealConsumption) -> dict:
    return {
        "id": sid(c.id), "external_id": c.external_id, "person_id": sid(c.person_id), "consumed_at": iso(c.consumed_at),
        "timezone": c.timezone, "meal_slot": c.meal_slot, "cooked_batch_id": sid(c.cooked_batch_id),
        "recipe_version_id": sid(c.recipe_version_id), "food_id": sid(c.food_id), "product_id": sid(c.product_id),
        "description": c.description, "amount": c.amount, "amount_unit": c.amount_unit, "amount_grams": c.amount_grams,
        "nutrition": c.nutrition, "nutrition_units": c.nutrition_units, "provenance": c.provenance, "confidence": c.confidence,
        "notes": c.notes, "deleted": c.deleted_at is not None, "updated_at": iso(c.updated_at), "created_at": iso(c.created_at),
    }


def batch_out(b: CookedBatch) -> dict:
    return {"id": sid(b.id), "title": b.title, "recipe_version_id": sid(b.recipe_version_id), "meal_plan_id": sid(b.meal_plan_id),
            "cooked_at": iso(b.cooked_at), "servings_made": b.servings_made, "scale_factor": b.scale_factor,
            "leftover_servings": b.leftover_servings, "waste_servings": b.waste_servings, "leftover_lot_id": sid(b.leftover_lot_id),
            "ingredients_used": b.ingredients_used, "nutrition_total": b.nutrition_total,
            "nutrition_per_serving": b.nutrition_per_serving, "notes": b.notes}


def _new_external_id() -> str:
    return f"lrd-mc-{uuid.uuid4()}"


def _snapshot(summary: dict, factor: float, sources: dict) -> tuple[dict, dict, dict, float]:
    s = scale_summary(summary, factor)
    vals, units, status = snapshot_values(s)
    missing = sorted({m for d in s["nutrients"].values() for m in d.get("missing_from", [])})
    estimated = any(d.get("estimated") for d in s["nutrients"].values())
    complete = all(status[k] == "complete" for k in CORE_NUTRIENTS)
    prov = {**sources, "status": status, "complete": complete, "estimated": estimated, "missing_from": missing,
            "note": "Unknown values are null, not zero. 'partial' totals exclude items with unknown values."}
    known = sum(1 for k in CORE_NUTRIENTS if status[k] == "complete")
    confidence = round((known / len(CORE_NUTRIENTS)) * (0.7 if estimated else 1.0), 2)
    return vals, units, prov, confidence


# ---- cooking ---------------------------------------------------------------------------------------------------------

class DeductionIn(BaseModel):
    ingredient_position: int | None = None
    name: str | None = None
    food_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    quantity: float | None = Field(None, ge=0)
    unit: str | None = None
    skip: bool = False


class PortionIn(BaseModel):
    person_id: uuid.UUID
    servings: float = Field(ge=0, le=20)
    notes: str | None = None


class CookIn(BaseModel):
    recipe_version_id: uuid.UUID
    meal_plan_id: uuid.UUID | None = None
    servings_made: float = Field(gt=0, le=200)
    cooked_at: datetime | None = None
    meal_slot: str | None = None
    deductions: list[DeductionIn] | None = Field(None, description="Actual ingredients used; defaults to the scaled recipe")
    portions: list[PortionIn] = []
    leftover_servings: float = Field(0, ge=0)
    leftover_location: str = Field("fridge", pattern="^(cupboard|fridge|freezer|other)$")
    leftover_use_by: date | None = None
    waste_servings: float = Field(0, ge=0)
    notes: str | None = None
    idempotency_key: str | None = Field(None, max_length=120)


@router.post("/meals/cook", status_code=201)
def cook(body: CookIn, p: Principal = Depends(require("intake:write")), db: Session = Depends(get_db)):
    """Record a cooked batch: deduct pantry stock, snapshot nutrition, log each person's actual portion and leftovers."""
    if body.idempotency_key:
        existing = db.scalar(select(CookedBatch).where(CookedBatch.idempotency_key == body.idempotency_key))
        if existing:
            return {"batch": batch_out(existing), "replayed": True}
    v = db.get(RecipeVersion, body.recipe_version_id)
    if v is None or db.get(Recipe, v.recipe_id).household_id != p.household_id or v.status != "published":
        raise HTTPException(404, "Recipe version not found")
    eaten = sum(x.servings for x in body.portions)
    accounted = eaten + body.leftover_servings + body.waste_servings
    if accounted > body.servings_made + 1e-6:
        raise HTTPException(422, f"Portions, leftovers and waste add up to {accounted:g} servings, "
                                 f"but only {body.servings_made:g} were made.")
    factor = body.servings_made / (v.default_servings or 1)
    tzname = db.get(Household, p.household_id).timezone
    cooked_at = body.cooked_at or utcnow()

    # Actual ingredients (default: scaled recipe with linked food/product)
    used = []
    by_pos = {i.position: i for i in v.ingredients}
    if body.deductions is None:
        for i in v.ingredients:
            used.append({"position": i.position, "name": i.name, "food_id": i.food_id, "product_id": i.product_id,
                         "quantity": i.quantity * factor if i.quantity is not None else None, "unit": i.unit,
                         "optional": i.optional, "skip": i.optional})
    else:
        for d in body.deductions:
            src = by_pos.get(d.ingredient_position) if d.ingredient_position is not None else None
            used.append({"position": d.ingredient_position, "name": d.name or (src.name if src else "Ingredient"),
                         "food_id": d.food_id or (src.food_id if src else None),
                         "product_id": d.product_id or (src.product_id if src else None),
                         "quantity": d.quantity if d.quantity is not None else (src.quantity * factor if src and src.quantity is not None else None),
                         "unit": canonical_unit(d.unit) if d.unit else (src.unit if src else None),
                         "optional": src.optional if src else False, "skip": d.skip})

    batch = CookedBatch(household_id=p.household_id, recipe_version_id=v.id, meal_plan_id=body.meal_plan_id, title=v.title,
                        cooked_at=cooked_at, servings_made=body.servings_made, scale_factor=factor,
                        leftover_servings=body.leftover_servings, waste_servings=body.waste_servings, notes=body.notes,
                        idempotency_key=body.idempotency_key, created_by_id=p.user_id)
    db.add(batch)
    db.flush()

    acc = NutritionAccumulator()
    used_out = []
    for u in used:
        food = db.get(Food, u["food_id"]) if u["food_id"] else None
        product = db.get(Product, u["product_id"]) if u["product_id"] else None
        if (food and food.household_id != p.household_id) or (product and product.household_id != p.household_id):
            raise HTTPException(422, "Unknown ingredient item")
        if (product and product.is_pet_food) or (food and food.is_pet_food):
            raise HTTPException(422, "Pet food can't be used in human cooking")
        row = {k: (str(v_) if isinstance(v_, uuid.UUID) else v_) for k, v_ in u.items()}
        if u["skip"]:
            row["deducted"] = []
            used_out.append(row)
            continue
        acc.add(item_contribution(db, label=u["name"], quantity=u["quantity"], unit=u["unit"], food=food, product=product))
        deducted, shortfall = [], None
        if u["quantity"] and u["unit"] and (food or product):
            # Prefer the chosen product's lots; otherwise any lot of the generic food (including its products).
            target = {"product_id": product.id} if product else {}
            lots = usable_lots(db, p.household_id, **target) if product else []
            if not lots:
                fid = food.id if food else (product.food_id if product else None)
                target = {"food_id": fid} if fid else {}
                lots = usable_lots(db, p.household_id, **target) if fid else []
            if lots:
                lots_unit = lots[0].base_unit
                c = convert(u["quantity"], u["unit"], lots_unit, factors_for(db, product, food))
                if c.quantity is not None:
                    deducted, short = consume_fefo(db, p.household_id, **target, quantity_base=c.quantity, base_unit=lots_unit,
                                                   reason=f"Cooked: {v.title}", cooked_batch_id=batch.id, actor_user_id=p.user_id,
                                                   key_prefix=f"cook:{batch.id}:{u['position']}")
                    shortfall = round(short, 3) if short > 1e-6 else None
                else:
                    shortfall = f"Couldn't convert {u['quantity']:g} {u['unit']} to {lots_unit}: {c.note}"
            else:
                shortfall = "Not in the pantry"
        row.update(deducted=deducted, shortfall=shortfall, base_unit=deducted[0]["base_unit"] if deducted else None)
        used_out.append(row)

    total = acc.result()
    per = acc.result(divisor=body.servings_made)
    batch.ingredients_used = used_out
    batch.nutrition_total, batch.nutrition_per_serving = total, per

    consumptions = []
    for portion in body.portions:
        person = get_owned(db, PersonProfile, portion.person_id, p.household_id, "Person")
        if portion.servings <= 0:
            continue
        vals, units, prov, conf = _snapshot(per, portion.servings, {"source": "cooked_batch", "cooked_batch_id": str(batch.id),
                                                                   "recipe_version_id": str(v.id), "per_unit": per})
        c = MealConsumption(household_id=p.household_id, person_id=person.id, external_id=_new_external_id(), consumed_at=cooked_at,
                            timezone=tzname, meal_slot=body.meal_slot, cooked_batch_id=batch.id, recipe_version_id=v.id,
                            description=v.title, amount=portion.servings, amount_unit="serving", nutrition=vals,
                            nutrition_units=units, provenance=prov, confidence=conf, notes=portion.notes, created_by_id=p.user_id)
        db.add(c)
        consumptions.append(c)

    if body.leftover_servings > 0:
        lf_name = f"Leftovers: {v.title}"[:160]
        food = db.scalar(select(Food).where(Food.household_id == p.household_id, Food.name == lf_name))
        if food is None:
            food = Food(household_id=p.household_id, name=lf_name, default_unit="each", category="Leftovers",
                        notes="Servings of a cooked batch; nutrition comes from the batch snapshot.")
            db.add(food)
            db.flush()
        lot = create_lot(db, p.household_id, product=None, food=food, quantity=body.leftover_servings, unit="each",
                         location=body.leftover_location, event_type="add", use_by=body.leftover_use_by,
                         date_label="Cooked " + cooked_at.astimezone(ZoneInfo(tzname)).strftime("%d/%m/%Y"),
                         notes=f"cooked_batch:{batch.id}", actor_user_id=p.user_id)
        batch.leftover_lot_id = lot.id
    if body.meal_plan_id:
        mp = get_owned(db, MealPlan, body.meal_plan_id, p.household_id, "Meal plan")
        mp.status, mp.cooked_batch_id = "cooked", batch.id
    audit(db, p, "meal.cooked", "cooked_batch", batch.id, {"servings_made": body.servings_made, "portions": len(consumptions)})
    db.commit()
    unaccounted = round(body.servings_made - accounted, 3)
    return {"batch": batch_out(batch), "consumptions": [consumption_out(c) for c in consumptions],
            "unaccounted_servings": unaccounted if unaccounted > 1e-6 else 0}


@router.get("/meals/batches/{batch_id}")
def get_batch(batch_id: uuid.UUID, p: Principal = Depends(require("plan:read")), db: Session = Depends(get_db)):
    b = get_owned(db, CookedBatch, batch_id, p.household_id, "Batch")
    cons = db.scalars(select(MealConsumption).where(MealConsumption.cooked_batch_id == b.id, MealConsumption.deleted_at.is_(None)))
    return {"batch": batch_out(b), "consumptions": [consumption_out(c) for c in cons]}


# ---- consumption -------------------------------------------------------------------------------------------------------

class ConsumptionIn(BaseModel):
    person_id: uuid.UUID
    consumed_at: datetime | None = None
    meal_slot: str | None = Field(None, pattern="^(breakfast|lunch|dinner|snack)$")
    cooked_batch_id: uuid.UUID | None = None
    food_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    description: str | None = None
    amount: float = Field(gt=0)
    amount_unit: str = Field("serving", description="serving (batch) | g | ml | each | any known unit")
    deduct_from_stock: bool = Field(False, description="Also consume from the pantry (or the batch's leftovers)")
    notes: str | None = None


@router.post("/meals/consumption", status_code=201)
def record_consumption(body: ConsumptionIn, p: Principal = Depends(require("intake:write")), db: Session = Depends(get_db)):
    person = get_owned(db, PersonProfile, body.person_id, p.household_id, "Person")
    tzname = db.get(Household, p.household_id).timezone
    at = body.consumed_at or utcnow()
    c = MealConsumption(household_id=p.household_id, person_id=person.id, external_id=_new_external_id(), consumed_at=at,
                        timezone=tzname, meal_slot=body.meal_slot, amount=body.amount, notes=body.notes, created_by_id=p.user_id)
    if body.cooked_batch_id:
        b = get_owned(db, CookedBatch, body.cooked_batch_id, p.household_id, "Batch")
        if body.amount_unit != "serving":
            raise HTTPException(422, "Record leftovers from a batch in servings")
        vals, units, prov, conf = _snapshot(b.nutrition_per_serving, body.amount, {"source": "cooked_batch", "cooked_batch_id": str(b.id),
                                                                                 "recipe_version_id": sid(b.recipe_version_id),
                                                                                 "per_unit": b.nutrition_per_serving})
        c.cooked_batch_id, c.recipe_version_id, c.description, c.amount_unit = b.id, b.recipe_version_id, body.description or b.title, "serving"
        if body.deduct_from_stock and b.leftover_lot_id:
            lot = db.get(PantryLot, b.leftover_lot_id)
            if lot and lot.status == "active":
                record_event(db, lot.id, p.household_id, EventInput(type="consumption", quantity=min(body.amount, lot.quantity_base),
                                                                    unit="each", reason=f"Eaten by {person.name}"), actor_user_id=p.user_id)
    else:
        food = get_owned(db, Food, body.food_id, p.household_id, "Food") if body.food_id else None
        product = get_owned(db, Product, body.product_id, p.household_id, "Product") if body.product_id else None
        if not (food or product):
            raise HTTPException(422, "Choose a cooked batch, food or product")
        if (product and product.is_pet_food) or (food and food.is_pet_food):
            raise HTTPException(422, "Pet food can't be logged as human intake")
        if product and product.kind in ("supplement", "medicine"):
            raise HTTPException(422, "Record medicines and supplements as doses (POST /api/v1/health/doses)")
        unit = canonical_unit(body.amount_unit)
        if unit is None:
            raise HTTPException(422, f"Unknown unit '{body.amount_unit}'")
        acc = NutritionAccumulator()
        label = body.description or (product.name if product else food.name)
        acc.add(item_contribution(db, label=label, quantity=body.amount, unit=unit, food=food, product=product))
        summary = acc.result()
        unit_acc = NutritionAccumulator()
        unit_acc.add(item_contribution(db, label=label, quantity=1, unit=unit, food=food, product=product))
        vals, units, prov, conf = _snapshot(summary, 1.0, {"source": "item", "per_unit": unit_acc.result(), "unit": unit})
        c.food_id, c.product_id = (food.id if food else None), (product.id if product else None)
        c.description, c.amount_unit = label, unit
        if unit == "g":
            c.amount_grams = body.amount
        if body.deduct_from_stock:
            from ..services.stock import factors_for, usable_lots
            lots = usable_lots(db, p.household_id, food_id=food.id if food else None, product_id=product.id if product else None)
            if lots:
                cv = convert(body.amount, unit, lots[0].base_unit, factors_for(db, product, food))
                if cv.quantity:
                    consume_fefo(db, p.household_id, food_id=food.id if food else None, product_id=product.id if product else None,
                                 quantity_base=cv.quantity, base_unit=lots[0].base_unit, reason=f"Eaten by {person.name}",
                                 actor_user_id=p.user_id)
    c.nutrition, c.nutrition_units, c.provenance, c.confidence = vals, units, prov, conf
    db.add(c)
    db.commit()
    return consumption_out(c)


class ConsumptionPatch(BaseModel):
    amount: float | None = Field(None, gt=0)
    consumed_at: datetime | None = None
    meal_slot: str | None = None
    notes: str | None = None


@router.patch("/meals/consumption/{cid}")
def patch_consumption(cid: uuid.UUID, body: ConsumptionPatch, p: Principal = Depends(require("intake:write")), db: Session = Depends(get_db)):
    """Correct an entry. Nutrition is rescaled from the per-unit snapshot taken when it was recorded."""
    c = get_owned(db, MealConsumption, cid, p.household_id, "Entry")
    if c.deleted_at:
        raise HTTPException(409, "Entry was deleted")
    if body.amount is not None and body.amount != c.amount:
        per = (c.provenance or {}).get("per_unit")
        if per:
            vals, units, prov, conf = _snapshot(per, body.amount, {k: v for k, v in c.provenance.items()
                                                                 if k in ("source", "cooked_batch_id", "recipe_version_id", "per_unit", "unit")})
            c.nutrition, c.nutrition_units, c.provenance, c.confidence = vals, units, prov, conf
        c.amount = body.amount
        if c.amount_unit == "g":
            c.amount_grams = body.amount
    for k in ("consumed_at", "meal_slot", "notes"):
        if getattr(body, k) is not None:
            setattr(c, k, getattr(body, k))
    db.commit()
    return consumption_out(c)


@router.delete("/meals/consumption/{cid}")
def delete_consumption(cid: uuid.UUID, p: Principal = Depends(require("intake:write")), db: Session = Depends(get_db)):
    """Soft delete: keeps a tombstone so incremental exports can tell downstream apps to remove it."""
    c = get_owned(db, MealConsumption, cid, p.household_id, "Entry")
    c.deleted_at = utcnow()
    db.commit()
    return {"ok": True}


# ---- intake & export ------------------------------------------------------------------------------------------------

def _person_for(db: Session, p: Principal, person_id: uuid.UUID) -> PersonProfile:
    person = get_owned(db, PersonProfile, person_id, p.household_id, "Person")
    if not p.can_read_person_intake(person):
        # 404 rather than 403 so a token cannot probe which profiles exist
        raise HTTPException(404, "Person not found")
    return person


def _range(db: Session, p: Principal, from_: date | None, to: date | None) -> tuple[datetime, datetime, str]:
    tzname = db.get(Household, p.household_id).timezone
    tz = ZoneInfo(tzname)
    to = to or date.today()
    from_ = from_ or (to - timedelta(days=6))
    if from_ > to:
        raise HTTPException(422, "'from' must be on or before 'to'")
    return datetime.combine(from_, time.min, tz), datetime.combine(to + timedelta(days=1), time.min, tz), tzname


def _rows(db: Session, person: PersonProfile, start: datetime, end: datetime, updated_since: datetime | None) -> list[MealConsumption]:
    q = select(MealConsumption).where(MealConsumption.person_id == person.id)
    if updated_since:
        q = q.where(MealConsumption.updated_at > updated_since)
    else:
        q = q.where(MealConsumption.consumed_at >= start, MealConsumption.consumed_at < end, MealConsumption.deleted_at.is_(None))
    return list(db.scalars(q.order_by(MealConsumption.consumed_at, MealConsumption.id)))


@router.get("/people/{person_id}/intake")
def intake(person_id: uuid.UUID, from_: date | None = Query(None, alias="from"), to: date | None = None,
           p: Principal = Depends(require("intake:read")), db: Session = Depends(get_db)):
    person = _person_for(db, p, person_id)
    start, end, tzname = _range(db, p, from_, to)
    rows = _rows(db, person, start, end, None)
    tz = ZoneInfo(tzname)
    days: dict[str, dict] = {}
    for c in rows:
        d = c.consumed_at.astimezone(tz).date().isoformat()
        day = days.setdefault(d, {"date": d, "entries": 0, "nutrients": {k: {"value": None, "unit": NUTRIENTS[k][1], "status": "unknown",
                                                                            "known": 0, "unknown": 0} for k in NUTRIENTS}})
        day["entries"] += 1
        for k in NUTRIENTS:
            v = (c.nutrition or {}).get(k)
            st = ((c.provenance or {}).get("status") or {}).get(k, "unknown" if v is None else "complete")
            t = day["nutrients"][k]
            if v is None:
                t["unknown"] += 1
            else:
                t["value"] = round((t["value"] or 0) + v, 3)
                t["known"] += 1
                if st == "partial":
                    t["unknown"] += 1
    for day in days.values():
        for t in day["nutrients"].values():
            t["status"] = "unknown" if t["known"] == 0 else ("complete" if t["unknown"] == 0 else "partial")
    return {"person": {"id": sid(person.id), "name": person.name, "kind": person.kind}, "from": start.date().isoformat(),
            "to": (end - timedelta(days=1)).date().isoformat(), "timezone": tzname,
            "days": sorted(days.values(), key=lambda d: d["date"]), "entries": [consumption_out(c) for c in rows],
            "note": "Values are estimates unless every source was a verified label. Unknown is not zero."}


EXPORT_FIELDS = ["external_id", "person_id", "consumed_at", "timezone", "local_date", "meal_slot", "description", "amount",
                 "amount_unit", "source", "recipe_version_id", "food_id", "product_id", "confidence", "estimated", "complete",
                 "deleted", "updated_at"]


def _export_record(c: MealConsumption, tz: ZoneInfo) -> dict:
    prov = c.provenance or {}
    nutrients = {k: {"value": (c.nutrition or {}).get(k), "unit": NUTRIENTS[k][1],
                     "status": (prov.get("status") or {}).get(k, "unknown")} for k in NUTRIENTS}
    return {
        "external_id": c.external_id, "person_id": sid(c.person_id), "consumed_at": iso(c.consumed_at), "timezone": c.timezone,
        "local_date": c.consumed_at.astimezone(tz).date().isoformat(), "meal_slot": c.meal_slot, "description": c.description,
        "amount": c.amount, "amount_unit": c.amount_unit, "energy_kcal": nutrients["energy_kcal"]["value"],
        "nutrients": nutrients, "source": prov.get("source"), "recipe_version_id": sid(c.recipe_version_id),
        "food_id": sid(c.food_id), "product_id": sid(c.product_id), "confidence": c.confidence,
        "estimated": prov.get("estimated", True), "complete": prov.get("complete", False),
        "missing_from": prov.get("missing_from", []), "deleted": c.deleted_at is not None, "updated_at": iso(c.updated_at),
    }


@router.get("/people/{person_id}/intake/export")
def intake_export(person_id: uuid.UUID, format: str = Query("json", pattern="^(json|csv)$"),
                  from_: date | None = Query(None, alias="from"), to: date | None = None,
                  updated_since: datetime | None = Query(None, description="Incremental sync: changes (incl. deletions) after this time"),
                  p: Principal = Depends(require("intake:read")), db: Session = Depends(get_db)):
    """Stable, deduplicatable intake export. Poll with `updated_since` set to the previous response's `cursor`."""
    person = _person_for(db, p, person_id)
    start, end, tzname = _range(db, p, from_, to)
    tz = ZoneInfo(tzname)
    rows = _rows(db, person, start, end, updated_since)
    records = [_export_record(c, tz) for c in rows]
    cursor = max((c.updated_at for c in rows), default=updated_since or utcnow()).isoformat()
    audit(db, p, "intake.exported", "person", person.id, {"format": format, "count": len(records), "incremental": bool(updated_since)})
    db.commit()
    if format == "json":
        return {"schema": "larder.intake.v1", "person": {"id": sid(person.id), "name": person.name, "kind": person.kind},
                "from": start.date().isoformat(), "to": (end - timedelta(days=1)).date().isoformat(), "timezone": tzname,
                "generated_at": utcnow().isoformat(), "cursor": cursor, "units": {k: v[1] for k, v in NUTRIENTS.items()},
                "records": records}
    buf = io.StringIO()
    cols = EXPORT_FIELDS + [f"{k}" for k in NUTRIENTS] + [f"{k}_status" for k in NUTRIENTS] + ["missing_from"]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in records:
        row = {k: r.get(k) for k in EXPORT_FIELDS}
        for k in NUTRIENTS:
            row[k] = r["nutrients"][k]["value"]
            row[f"{k}_status"] = r["nutrients"][k]["status"]
        row["missing_from"] = "; ".join(r["missing_from"])
        w.writerow(row)
    # Units travel in the header comment-free: column names carry the unit suffix (e.g. energy_kcal, salt_g, sodium_mg).
    filename = f"intake-{person.name.lower().replace(' ', '-')}-{start.date()}-{(end - timedelta(days=1)).date()}.csv"
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "X-Larder-Cursor": cursor})
