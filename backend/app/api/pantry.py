"""Pantry: grouped stock view, scan-to-draft, lots and the stock-event ledger."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Principal, audit, require
from ..db import get_db
from ..models import Barcode, Food, PantryLot, Product, StockEvent, utcnow
from ..services import off
from ..services.prices import record_purchase
from ..services.stock import EventInput, create_lot, record_event, relevant_date
from ..services.units import canonical_unit, uk_display
from .catalog import ProductIn, create_product, find_or_create_food, food_out, product_out
from .common import get_owned, iso, sid

router = APIRouter(prefix="/api/v1/pantry", tags=["pantry"])


def lot_out(lot: PantryLot) -> dict:
    rd = relevant_date(lot)
    return {
        "id": sid(lot.id), "product_id": sid(lot.product_id), "food_id": sid(lot.food_id), "location": lot.location,
        "quantity_base": round(lot.quantity_base, 3), "base_unit": lot.base_unit,
        "display": uk_display(lot.quantity_base, lot.base_unit),
        "initial_quantity_base": lot.initial_quantity_base, "entered_quantity": lot.entered_quantity,
        "entered_unit": lot.entered_unit, "conversion_factor": lot.conversion_factor, "reserved_base": lot.reserved_base,
        "opened": lot.opened, "purchased_on": iso(lot.purchased_on), "best_before": iso(lot.best_before),
        "use_by": iso(lot.use_by), "date_label": lot.date_label, "relevant_date": iso(rd),
        "relevant_date_kind": ("use_by" if lot.use_by and rd == lot.use_by else "best_before" if rd else None),
        "store": lot.store, "price_paid_pence": lot.price_paid_pence, "status": lot.status, "is_pet_food": lot.is_pet_food,
        "notes": lot.notes, "row_version": lot.row_version, "updated_at": iso(lot.updated_at),
    }


def event_out(e: StockEvent) -> dict:
    return {"id": sid(e.id), "lot_id": sid(e.lot_id), "type": e.type, "delta_base": e.delta_base,
            "entered_quantity": e.entered_quantity, "entered_unit": e.entered_unit, "conversion_factor": e.conversion_factor,
            "resulting_quantity_base": e.resulting_quantity_base, "from_location": e.from_location,
            "to_location": e.to_location, "reason": e.reason, "negative_override": e.negative_override,
            "override_reason": e.override_reason, "cooked_batch_id": sid(e.cooked_batch_id),
            "receipt_line_id": sid(e.receipt_line_id), "occurred_at": iso(e.occurred_at), "created_at": iso(e.created_at)}


def _threshold(product: Product | None, food: Food | None) -> float | None:
    if product and product.min_stock_base is not None:
        return product.min_stock_base
    if food and food.min_stock_base is not None:
        return food.min_stock_base
    return None


@router.get("")
def pantry(location: str | None = None, q: str | None = None, pet: bool = False, include_empty: bool = False,
           p: Principal = Depends(require("pantry:read")), db: Session = Depends(get_db)):
    """Stock grouped by product (or by food for unbranded items), earliest relevant date first."""
    stmt = select(PantryLot).where(PantryLot.household_id == p.household_id, PantryLot.is_pet_food.is_(pet))
    if not include_empty:
        stmt = stmt.where(PantryLot.status == "active")
    if location:
        stmt = stmt.where(PantryLot.location == location)
    lots = list(db.scalars(stmt))
    groups: dict[str, dict] = {}
    today = date.today()
    for lot in lots:
        key = f"p:{lot.product_id}" if lot.product_id else f"f:{lot.food_id}"
        g = groups.get(key)
        if g is None:
            prod = lot.product
            food = lot.food or (prod.food if prod else None)
            name = prod.name if prod else food.name
            if q and q.lower() not in name.lower() and not (prod and prod.brand and q.lower() in prod.brand.lower()):
                continue
            g = groups[key] = {
                "key": key, "product": product_out(prod) if prod else None, "food": food_out(food) if food else None,
                "name": name, "brand": prod.brand if prod else None, "base_unit": lot.base_unit, "quantity_base": 0.0,
                "lots": [], "locations": set(), "earliest_date": None, "earliest_date_kind": None,
                "threshold_base": _threshold(prod, food), "category": (prod.category if prod else None) or (food.category if food else None),
            }
        if q and key not in groups:
            continue
        g["lots"].append(lot_out(lot))
        g["locations"].add(lot.location)
        if lot.base_unit == g["base_unit"]:
            g["quantity_base"] += lot.quantity_base
        rd = relevant_date(lot)
        if rd and (g["earliest_date"] is None or rd < g["earliest_date"]):
            g["earliest_date"] = rd
            g["earliest_date_kind"] = "use_by" if lot.use_by == rd else "best_before"
    out = []
    for g in groups.values():
        g["locations"] = sorted(g["locations"])
        g["display"] = uk_display(g["quantity_base"], g["base_unit"])
        g["low_stock"] = g["threshold_base"] is not None and g["quantity_base"] <= g["threshold_base"]
        ed = g["earliest_date"]
        g["days_to_date"] = (ed - today).days if ed else None
        g["earliest_date"] = iso(ed)
        g["lots"].sort(key=lambda x: (x["relevant_date"] or "9999", x["purchased_on"] or ""))
        out.append(g)
    out.sort(key=lambda g: (g["earliest_date"] or "9999-12-31", g["name"].lower()))
    return {"items": out, "count": len(out), "as_of": utcnow().isoformat()}


@router.get("/expiring")
def expiring(days: int = Query(5, ge=0, le=60), p: Principal = Depends(require("pantry:read")), db: Session = Depends(get_db)):
    until = date.today() + timedelta(days=days)
    lots = db.scalars(select(PantryLot).where(PantryLot.household_id == p.household_id, PantryLot.status == "active",
                                              ((PantryLot.use_by <= until) | (PantryLot.best_before <= until))))
    out = []
    for lot in lots:
        prod, food = lot.product, lot.food
        out.append({**lot_out(lot), "name": prod.name if prod else food.name, "brand": prod.brand if prod else None,
                    "days_left": (relevant_date(lot) - date.today()).days})
    return sorted(out, key=lambda x: x["relevant_date"])


class ScanIn(BaseModel):
    barcode: str
    pet: bool = False


@router.post("/scan")
def scan(body: ScanIn, p: Principal = Depends(require("pantry:read")), db: Session = Depends(get_db)):
    """Returns a *draft* stock item. Nothing is recorded as purchased until the user confirms via POST /pantry/lots."""
    code = body.barcode.strip()
    known = db.scalar(select(Barcode).where(Barcode.household_id == p.household_id, Barcode.code == code))
    if known:
        prod = known.product
        active = db.scalars(select(PantryLot).where(PantryLot.product_id == prod.id, PantryLot.status == "active"))
        return {"status": "known", "barcode": code, "product": product_out(prod, db, True),
                "existing_lots": [lot_out(x) for x in active],
                "suggested": {"quantity": 1, "unit": "pack", "location": _usual_location(db, prod.id)}}
    res = off.lookup(db, code, pet=body.pet)
    if res["found"]:
        d = res["draft"]
        return {"status": "draft", "barcode": code, "draft": d, "source": res["source"],
                "warning": res.get("error"), "review_note": "Check the name, size and allergens against the pack."}
    return {"status": "unknown", "barcode": code, "message": res.get("error") or "We don't know this barcode yet.",
            "quick_add_fields": ["name", "package size"], "lookup_error": res.get("error")}


def _usual_location(db: Session, product_id) -> str:
    last = db.scalar(select(PantryLot.location).where(PantryLot.product_id == product_id).order_by(PantryLot.created_at.desc()))
    return last or "cupboard"


class LotIn(BaseModel):
    product_id: uuid.UUID | None = None
    food_id: uuid.UUID | None = None
    food_name: str | None = Field(None, description="Unbarcoded produce/bulk: name of a generic food (created if new)")
    new_product: ProductIn | None = None
    quantity: float = Field(gt=0)
    unit: str = "pack"
    location: str = Field("cupboard", pattern="^(cupboard|fridge|freezer|other)$")
    event_type: str = Field("purchase", pattern="^(purchase|add)$")
    purchased_on: date | None = None
    best_before: date | None = None
    use_by: date | None = None
    date_label: str | None = None
    store: str | None = None
    price_paid_pence: int | None = Field(None, ge=0)
    shelf_price_pence: int | None = Field(None, ge=0)
    promotion: str | None = None
    package_count: int | None = Field(None, ge=1)
    opened: bool = False
    notes: str | None = None
    confirm_duplicate: bool = False


@router.post("/lots", status_code=201)
def add_lot(body: LotIn, p: Principal = Depends(require("pantry:write")), db: Session = Depends(get_db)):
    product = food = None
    if body.product_id:
        product = get_owned(db, Product, body.product_id, p.household_id, "Product")
    elif body.new_product:
        product = create_product(db, p, body.new_product)
    elif body.food_id:
        food = get_owned(db, Food, body.food_id, p.household_id, "Food")
    elif body.food_name:
        food = find_or_create_food(db, p.household_id, body.food_name, canonical_unit(body.unit))
    else:
        raise HTTPException(422, "Choose a product or food")
    unit = canonical_unit(body.unit) or body.unit
    if unit not in ("pack",) and canonical_unit(unit) is None:
        raise HTTPException(422, f"Unknown unit '{body.unit}'")

    # Duplicate detection: same item + same dates + same quantity entered in the last 15 minutes.
    if not body.confirm_duplicate:
        recent = db.scalar(select(PantryLot).where(
            PantryLot.household_id == p.household_id,
            (PantryLot.product_id == product.id) if product else (PantryLot.food_id == food.id),
            PantryLot.entered_quantity == body.quantity, PantryLot.best_before.is_not_distinct_from(body.best_before),
            PantryLot.use_by.is_not_distinct_from(body.use_by), PantryLot.created_at > utcnow() - timedelta(minutes=15)))
        if recent:
            raise HTTPException(409, {"code": "possible_duplicate", "message": "This looks like an item you just added. Add it again?",
                                      "existing_lot": lot_out(recent)})

    purchase = None
    occurred = datetime.combine(body.purchased_on, datetime.min.time()).astimezone() if body.purchased_on else utcnow()
    lot = create_lot(db, p.household_id, product=product, food=food, quantity=body.quantity, unit=unit,
                     location=body.location, event_type=body.event_type, purchased_on=body.purchased_on or (date.today() if body.event_type == "purchase" else None),
                     best_before=body.best_before, use_by=body.use_by, date_label=body.date_label, store=body.store,
                     price_paid_pence=body.price_paid_pence, package_count=body.package_count, notes=body.notes,
                     opened=body.opened, actor_user_id=p.user_id, occurred_at=occurred)
    if body.event_type == "purchase" and (body.price_paid_pence is not None or body.shelf_price_pence is not None):
        purchase = record_purchase(db, p.household_id, product_id=lot.product_id, food_id=lot.food_id or (product.food_id if product else None),
                                   store=body.store, purchased_at=occurred, package_count=body.package_count or (body.quantity if unit == "pack" else 1),
                                   quantity_base=lot.initial_quantity_base, base_unit=lot.base_unit,
                                   shelf_price_pence=body.shelf_price_pence, paid_price_pence=body.price_paid_pence,
                                   promotion=body.promotion, source="manual", is_pet_food=lot.is_pet_food)
        lot.purchase_id = purchase.id
    audit(db, p, "pantry.lot_added", "pantry_lot", lot.id, {"event": body.event_type})
    db.commit()
    return {"lot": lot_out(lot), "purchase_id": sid(purchase.id) if purchase else None}


@router.get("/lots/{lot_id}")
def get_lot(lot_id: uuid.UUID, p: Principal = Depends(require("pantry:read")), db: Session = Depends(get_db)):
    lot = get_owned(db, PantryLot, lot_id, p.household_id, "Lot")
    events = db.scalars(select(StockEvent).where(StockEvent.lot_id == lot.id).order_by(StockEvent.created_at))
    return {"lot": lot_out(lot), "product": product_out(lot.product) if lot.product else None,
            "food": food_out(lot.food) if lot.food else None, "events": [event_out(e) for e in events]}


class LotPatch(BaseModel):
    best_before: date | None = None
    use_by: date | None = None
    date_label: str | None = None
    store: str | None = None
    notes: str | None = None
    row_version: int


@router.patch("/lots/{lot_id}")
def patch_lot(lot_id: uuid.UUID, body: LotPatch, p: Principal = Depends(require("pantry:write")), db: Session = Depends(get_db)):
    """Edit label details (dates, store, notes). Quantity and location only change through events."""
    lot = db.scalar(select(PantryLot).where(PantryLot.id == lot_id, PantryLot.household_id == p.household_id).with_for_update())
    if lot is None:
        raise HTTPException(404, "Lot not found")
    if lot.row_version != body.row_version:
        raise HTTPException(409, {"code": "conflict", "message": "This item changed on another device. Review the latest values.",
                                  "current": lot_out(lot)})
    for k, v in body.model_dump(exclude_unset=True, exclude={"row_version"}).items():
        setattr(lot, k, v)
    lot.row_version += 1
    audit(db, p, "pantry.lot_edited", "pantry_lot", lot.id, body.model_dump(exclude_unset=True, mode="json"))
    db.commit()
    return lot_out(lot)


class EventIn(BaseModel):
    lot_id: uuid.UUID
    type: str = Field(pattern="^(consumption|waste|correction|transfer|return|expiration|open|add)$")
    quantity: float | None = Field(None, gt=0)
    unit: str | None = None
    new_quantity: float | None = Field(None, ge=0)
    to_location: str | None = Field(None, pattern="^(cupboard|fridge|freezer|other)$")
    reason: str | None = None
    negative_override: bool = False
    override_reason: str | None = None
    idempotency_key: str | None = Field(None, max_length=120)
    occurred_at: datetime | None = None


@router.post("/events", status_code=201)
def post_event(body: EventIn, p: Principal = Depends(require("pantry:write")), db: Session = Depends(get_db)):
    unit = canonical_unit(body.unit) if body.unit else None
    if body.unit and unit is None and body.unit != "pack":
        raise HTTPException(422, f"Unknown unit '{body.unit}'")
    ev = record_event(db, body.lot_id, p.household_id, EventInput(
        type=body.type, quantity=body.quantity, unit=unit or body.unit, new_quantity=body.new_quantity,
        to_location=body.to_location, reason=body.reason, negative_override=body.negative_override,
        override_reason=body.override_reason, idempotency_key=body.idempotency_key, occurred_at=body.occurred_at,
    ), actor_user_id=p.user_id)
    db.commit()
    lot = db.get(PantryLot, body.lot_id)
    return {"event": event_out(ev), "lot": lot_out(lot)}


@router.get("/events")
def list_events(lot_id: uuid.UUID | None = None, since: datetime | None = None, limit: int = Query(100, le=1000),
                p: Principal = Depends(require("pantry:read")), db: Session = Depends(get_db)):
    q = select(StockEvent).where(StockEvent.household_id == p.household_id)
    if lot_id:
        q = q.where(StockEvent.lot_id == lot_id)
    if since:
        q = q.where(StockEvent.created_at > since)
    return [event_out(e) for e in db.scalars(q.order_by(StockEvent.created_at.desc()).limit(limit))]


@router.get("/consistency")
def consistency(p: Principal = Depends(require("pantry:read")), db: Session = Depends(get_db)):
    """Verifies that every lot's projected quantity equals the sum of its ledger events."""
    from sqlalchemy import func

    rows = db.execute(select(PantryLot.id, PantryLot.quantity_base, func.coalesce(func.sum(StockEvent.delta_base), 0))
                      .join(StockEvent, StockEvent.lot_id == PantryLot.id, isouter=True)
                      .where(PantryLot.household_id == p.household_id).group_by(PantryLot.id)).all()
    bad = [{"lot_id": str(r[0]), "projection": r[1], "ledger": r[2]} for r in rows if abs(r[1] - r[2]) > 1e-6]
    return {"lots_checked": len(rows), "mismatches": bad}
