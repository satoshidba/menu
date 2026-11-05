"""Medicines, vitamins and supplements: cabinet, regimens, dose log."""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Principal, audit, require
from ..db import get_db
from ..models import DoseLog, Household, MealConsumption, PersonProfile, Product, Regimen, utcnow
from ..services import health
from ..services.nutrition_db import best_record, record_dict
from ..services.stock import create_lot
from ..services.units import canonical_unit
from .catalog import NutritionIn, _apply_package, save_nutrition
from .common import get_owned, iso, sid

router = APIRouter(prefix="/api/v1/health", tags=["medicines & vitamins"])
KIND = "^(supplement|medicine)$"


def amount(q: float, p: Product) -> str:
    """Exact amounts for medicines (never "2 tsp" for 10 ml); counted items use the pack's unit, e.g. "14 tablets"."""
    if p.base_unit == "each":
        unit = p.package_unit or p.dose_unit or "each"
        return f"{q:g} {unit}{'s' if q != 1 and unit not in ('each',) and not unit.endswith('s') else ''}"
    if p.base_unit == "g" and q >= 1000:
        return f"{q / 1000:g} kg"
    if p.base_unit == "ml" and q >= 1000:
        return f"{q / 1000:g} L"
    return f"{q:g} {p.base_unit}"


def item_out(db: Session, row: dict) -> dict:
    p: Product = row["product"]
    nut = record_dict(best_record(db, product_id=p.id)) if p.kind == "supplement" else None
    return {
        "product_id": sid(p.id), "kind": p.kind, "name": p.name, "brand": p.brand,
        "pack": f"{p.package_quantity:g} {p.package_unit}" if p.package_quantity else None,
        "dose": health.qty_unit(p.dose_amount or 1, p.dose_unit) if p.dose_unit else None, "dose_amount": p.dose_amount, "dose_unit": p.dose_unit,
        "label_min_interval_hours": p.label_min_interval_hours, "label_max_doses_24h": p.label_max_doses_24h,
        "active_ingredients": p.active_ingredients, "label_directions": p.label_directions, "allergens": p.allergens or [],
        "stock": amount(row["total"], p) if row["lots"] else None, "stock_base": row["total"], "base_unit": p.base_unit,
        "earliest_expiry": iso(row["earliest_expiry"]), "days_to_expiry": row["days_to_expiry"], "low_stock": row["low_stock"],
        "lots": [{"lot_id": sid(l.id), "amount": amount(l.quantity_base, p), "expiry": iso(l.use_by or l.best_before),
                  "location": l.location, "opened": l.opened} for l in row["lots"]],
        "nutrition_per_dose": {k: v for k, v in (nut or {}).get("values", {}).items() if v is not None} if nut else None,
        "min_stock_base": p.min_stock_base,
    }


def regimen_out(db: Session, r: Regimen) -> dict:
    p = db.get(Product, r.product_id)
    person = db.get(PersonProfile, r.person_id)
    return {"regimen_id": sid(r.id), "person_id": sid(r.person_id), "person": person.name if person else None, "product_id": sid(r.product_id),
            "item": f"{p.brand + ' ' if p and p.brand else ''}{p.name if p else ''}", "kind": p.kind if p else None,
            "dose_amount": r.dose_amount, "dose_unit": r.dose_unit, "times_per_day": r.times_per_day, "as_needed": r.as_needed,
            "start_date": iso(r.start_date), "end_date": iso(r.end_date), "instructions": r.instructions, "source": r.source, "active": r.active}


def dose_out(d: DoseLog, tz: str, names: dict | None = None) -> dict:
    return {"dose_id": sid(d.id), "person_id": sid(d.person_id), "person": (names or {}).get(d.person_id), "product_id": sid(d.product_id),
            "item": d.description, "amount": d.amount, "unit": d.unit, "given_at": iso(d.given_at),
            "local_time": d.given_at.astimezone(ZoneInfo(tz)).strftime("%a %d %b %H:%M"), "regimen_id": sid(d.regimen_id),
            "warnings_acknowledged": d.warnings_acknowledged, "notes": d.notes, "counts_in_nutrition": d.consumption_id is not None}


def _readable_people(db: Session, p: Principal) -> dict:
    people = db.scalars(select(PersonProfile).where(PersonProfile.household_id == p.household_id))
    return {x.id: x for x in people if p.can_read_person_intake(x, "health:read")}


# ---- cabinet ---------------------------------------------------------------------------------------------------------------

@router.get("/cabinet")
def get_cabinet(kind: str | None = Query(None, pattern=KIND), p: Principal = Depends(require("health:read")), db: Session = Depends(get_db)):
    """Medicines and supplements with stock, expiry (earliest first) and the label directions you recorded."""
    return {"items": [item_out(db, r) for r in health.cabinet(db, p.household_id, kind)], "advice": health.ADVICE}


class ItemIn(BaseModel):
    kind: str = Field(pattern=KIND)
    name: str = Field(max_length=200)
    brand: str | None = None
    pack_quantity: float | None = Field(None, gt=0, description="e.g. 16 (tablets) or 100 (ml)")
    pack_unit: str | None = Field(None, description="tablet, capsule, ml, g, gummy, sachet…")
    dose_amount: float | None = Field(None, gt=0)
    dose_unit: str | None = None
    label_min_interval_hours: float | None = Field(None, gt=0, le=168)
    label_max_doses_24h: int | None = Field(None, ge=1, le=24)
    active_ingredients: str | None = Field(None, max_length=300)
    label_directions: str | None = None
    allergens: list[str] = []
    barcode: str | None = None
    nutrition_per_dose: dict[str, float | None] | None = Field(None, description="Supplements only, e.g. {'vitamin_d_ug': 10} or {'protein_g': 24}")
    nutrition_verified: bool = True
    min_stock_base: float | None = Field(None, ge=0)
    # optional first stock
    stock_quantity: float | None = Field(None, gt=0, description="How many packs (or units) you have now")
    stock_unit: str = "pack"
    expiry: date | None = None
    location: str = Field("other", pattern="^(cupboard|fridge|freezer|other)$")


def _unit(u: str | None) -> str | None:
    return (canonical_unit(u) or u.lower()) if u else None


@router.post("/items", status_code=201)
def create_item(body: ItemIn, p: Principal = Depends(require("health:write")), db: Session = Depends(get_db)):
    if body.kind == "medicine" and body.nutrition_per_dose:
        raise HTTPException(422, "Medicines don't take nutrition values")
    prod = Product(household_id=p.household_id, kind=body.kind, name=body.name, brand=body.brand, dose_amount=body.dose_amount,
                   dose_unit=_unit(body.dose_unit), label_min_interval_hours=body.label_min_interval_hours,
                   label_max_doses_24h=body.label_max_doses_24h, active_ingredients=body.active_ingredients,
                   label_directions=body.label_directions, allergens=body.allergens, category="Medicines & vitamins",
                   provenance="label", review_status="reviewed", min_stock_base=body.min_stock_base)
    _apply_package(prod, body.pack_quantity, _unit(body.pack_unit))
    db.add(prod)
    db.flush()
    if body.barcode:
        from ..models import Barcode
        db.add(Barcode(household_id=p.household_id, product_id=prod.id, code=body.barcode))
    if body.nutrition_per_dose:
        save_nutrition(db, p.household_id, NutritionIn(product_id=prod.id, source="label", basis="dose", nutrients=body.nutrition_per_dose,
                                                      verified=body.nutrition_verified))
    if body.stock_quantity:
        create_lot(db, p.household_id, product=prod, food=None, quantity=body.stock_quantity, unit=_unit(body.stock_unit) or "pack",
                   location=body.location, event_type="add", use_by=body.expiry, date_label="Expiry" if body.expiry else None,
                   actor_user_id=p.user_id)
    audit(db, p, "health.item_created", "product", prod.id, {"kind": body.kind})
    db.commit()
    return item_out(db, next(r for r in health.cabinet(db, p.household_id) if r["product"].id == prod.id))


@router.patch("/items/{product_id}")
def update_item(product_id: uuid.UUID, body: dict, p: Principal = Depends(require("health:write")), db: Session = Depends(get_db)):
    prod = get_owned(db, Product, product_id, p.household_id, "Item")
    if prod.kind not in health.HEALTH_KINDS:
        raise HTTPException(404, "Item not found")
    for k in ("name", "brand", "dose_amount", "label_min_interval_hours", "label_max_doses_24h", "active_ingredients", "label_directions",
              "allergens", "min_stock_base"):
        if k in body:
            setattr(prod, k, body[k])
    if "dose_unit" in body:
        prod.dose_unit = _unit(body["dose_unit"])
    if body.get("nutrition_per_dose") is not None:
        if prod.kind == "medicine":
            raise HTTPException(422, "Medicines don't take nutrition values")
        save_nutrition(db, p.household_id, NutritionIn(product_id=prod.id, source="label", basis="dose", nutrients=body["nutrition_per_dose"],
                                                      verified=bool(body.get("nutrition_verified", True))))
    db.commit()
    return item_out(db, next(r for r in health.cabinet(db, p.household_id) if r["product"].id == prod.id))


# ---- regimens -----------------------------------------------------------------------------------------------------------------

class RegimenIn(BaseModel):
    person_id: uuid.UUID
    product_id: uuid.UUID
    dose_amount: float | None = Field(None, gt=0)
    dose_unit: str | None = None
    times_per_day: int = Field(1, ge=1, le=12)
    as_needed: bool = False
    start_date: date | None = None
    end_date: date | None = None
    instructions: str | None = None
    source: str | None = Field(None, max_length=120)


@router.get("/regimens")
def list_regimens(person_id: uuid.UUID | None = None, include_ended: bool = False, p: Principal = Depends(require("health:read")),
                  db: Session = Depends(get_db)):
    readable = _readable_people(db, p)
    q = select(Regimen).where(Regimen.household_id == p.household_id)
    if person_id:
        q = q.where(Regimen.person_id == person_id)
    if not include_ended:
        q = q.where(Regimen.active.is_(True), (Regimen.end_date.is_(None)) | (Regimen.end_date >= date.today()))
    return [regimen_out(db, r) for r in db.scalars(q.order_by(Regimen.start_date)) if r.person_id in readable]


@router.post("/regimens", status_code=201)
def create_regimen(body: RegimenIn, p: Principal = Depends(require("health:write")), db: Session = Depends(get_db)):
    person = get_owned(db, PersonProfile, body.person_id, p.household_id, "Person")
    prod = get_owned(db, Product, body.product_id, p.household_id, "Item")
    if prod.kind not in health.HEALTH_KINDS:
        raise HTTPException(422, "Choose a medicine or supplement")
    if body.end_date and body.start_date and body.end_date < body.start_date:
        raise HTTPException(422, "The end date is before the start date")
    r = Regimen(household_id=p.household_id, person_id=person.id, product_id=prod.id, dose_amount=body.dose_amount or prod.dose_amount or 1,
                dose_unit=_unit(body.dose_unit) or prod.dose_unit or "dose", times_per_day=body.times_per_day, as_needed=body.as_needed,
                start_date=body.start_date or date.today(), end_date=body.end_date, instructions=body.instructions, source=body.source)
    db.add(r)
    audit(db, p, "health.regimen_created", "regimen", None, {"kind": prod.kind})
    db.commit()
    return regimen_out(db, r)


@router.patch("/regimens/{regimen_id}")
def update_regimen(regimen_id: uuid.UUID, body: dict, p: Principal = Depends(require("health:write")), db: Session = Depends(get_db)):
    r = get_owned(db, Regimen, regimen_id, p.household_id, "Regimen")
    for k in ("dose_amount", "times_per_day", "as_needed", "instructions", "source", "active"):
        if k in body:
            setattr(r, k, body[k])
    for k in ("start_date", "end_date"):
        if k in body:
            setattr(r, k, date.fromisoformat(body[k]) if body[k] else None)
    if "dose_unit" in body:
        r.dose_unit = _unit(body["dose_unit"])
    db.commit()
    return regimen_out(db, r)


@router.delete("/regimens/{regimen_id}")
def stop_regimen(regimen_id: uuid.UUID, p: Principal = Depends(require("health:write")), db: Session = Depends(get_db)):
    """Stop a regimen (kept for the record; doses already given are unaffected)."""
    r = get_owned(db, Regimen, regimen_id, p.household_id, "Regimen")
    r.active = False
    r.end_date = r.end_date if r.end_date and r.end_date < date.today() else date.today()
    db.commit()
    return regimen_out(db, r)


# ---- doses ---------------------------------------------------------------------------------------------------------------------

class DoseIn(BaseModel):
    person_id: uuid.UUID
    product_id: uuid.UUID | None = None
    regimen_id: uuid.UUID | None = None
    amount: float | None = Field(None, gt=0)
    unit: str | None = None
    given_at: datetime | None = None
    acknowledge_warnings: bool = Field(False, description="Required to record a dose that conflicts with the entered directions")
    deduct_from_stock: bool = True
    notes: str | None = None


@router.get("/doses/check")
def check_dose(person_id: uuid.UUID, product_id: uuid.UUID, p: Principal = Depends(require("health:read")), db: Session = Depends(get_db)):
    """Preview what logging a dose now would flag, and when the next dose is allowed by the entered directions."""
    person = get_owned(db, PersonProfile, person_id, p.household_id, "Person")
    prod = get_owned(db, Product, product_id, p.household_id, "Item")
    tz = db.get(Household, p.household_id).timezone
    na = health.next_allowed(db, person, prod)
    return {"warnings": health.dose_warnings(db, person, prod, utcnow(), tz), "next_allowed_at": iso(na), "advice": health.ADVICE}


@router.post("/doses", status_code=201)
def give_dose(body: DoseIn, p: Principal = Depends(require("health:write")), db: Session = Depends(get_db)):
    """Record a dose. If it conflicts with the directions you entered (too soon, too many in 24 hours, expired), the API
    answers 409 with the reasons; send again with acknowledge_warnings=true to record it anyway."""
    person = get_owned(db, PersonProfile, body.person_id, p.household_id, "Person")
    regimen = get_owned(db, Regimen, body.regimen_id, p.household_id, "Regimen") if body.regimen_id else None
    prod_id = body.product_id or (regimen.product_id if regimen else None)
    if not prod_id:
        raise HTTPException(422, "Choose an item or a regimen")
    prod = get_owned(db, Product, prod_id, p.household_id, "Item")
    dose, warnings, consumption = health.log_dose(db, p.household_id, person=person, product=prod, amount=body.amount, unit=_unit(body.unit),
                                                  given_at=body.given_at, regimen=regimen, acknowledge_warnings=body.acknowledge_warnings,
                                                  deduct_from_stock=body.deduct_from_stock, notes=body.notes, user_id=p.user_id)
    audit(db, p, "health.dose_logged", "dose_log", dose.id, {"kind": prod.kind, "acknowledged": [w["code"] for w in dose.warnings_acknowledged]})
    db.commit()
    tz = db.get(Household, p.household_id).timezone
    return {"dose": dose_out(dose, tz, {person.id: person.name}), "notes": warnings, "nutrition_counted": consumption}


@router.get("/doses")
def list_doses(person_id: uuid.UUID | None = None, from_: date | None = Query(None, alias="from"), to: date | None = None,
               p: Principal = Depends(require("health:read")), db: Session = Depends(get_db)):
    tz = db.get(Household, p.household_id).timezone
    zone = ZoneInfo(tz)
    to = to or date.today()
    from_ = from_ or to - timedelta(days=6)
    start, end = datetime.combine(from_, time.min, zone), datetime.combine(to + timedelta(days=1), time.min, zone)
    readable = _readable_people(db, p)
    if person_id and person_id not in readable:
        raise HTTPException(404, "Person not found")
    names = {k: v.name for k, v in readable.items()}
    out = []
    for pid in ([person_id] if person_id else list(readable)):
        out += [dose_out(d, tz, names) for d in health.doses_between(db, pid, start, end)]
    out.sort(key=lambda d: d["given_at"], reverse=True)
    return {"from": iso(from_), "to": iso(to), "doses": out}


@router.delete("/doses/{dose_id}")
def delete_dose(dose_id: uuid.UUID, p: Principal = Depends(require("health:write")), db: Session = Depends(get_db)):
    """Remove a dose logged by mistake (its nutrition entry is removed too). Stock is not put back automatically."""
    d = get_owned(db, DoseLog, dose_id, p.household_id, "Dose")
    d.deleted_at = utcnow()
    if d.consumption_id:
        c = db.get(MealConsumption, d.consumption_id)
        if c:
            c.deleted_at = utcnow()
    db.commit()
    return {"ok": True}


@router.get("/today")
def health_today(p: Principal = Depends(require("health:read")), db: Session = Depends(get_db)):
    """Doses due today per regimen, plus cabinet items expiring within 30 days or running low."""
    readable = _readable_people(db, p)
    due = health.due_today(db, p.household_id, lambda person: person.id in readable)
    cab = health.cabinet(db, p.household_id)
    attention = [{"item": r["product"].name, "kind": r["product"].kind, "expiry": iso(r["earliest_expiry"]), "days_to_expiry": r["days_to_expiry"],
                  "low_stock": r["low_stock"], "stock": amount(r["total"], r["product"]) if r["lots"] else "none"}
                 for r in cab if (r["days_to_expiry"] is not None and r["days_to_expiry"] <= 30) or r["low_stock"]]
    return {"due": due, "attention": attention, "advice": health.ADVICE}
