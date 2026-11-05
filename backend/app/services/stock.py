"""Stock ledger. Every quantity change is a StockEvent written in the same transaction as the lot projection."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Food, PantryLot, Product, StockEvent, utcnow
from .units import FoodFactors, base_unit_for, convert

EPS = 1e-9
NEGATIVE_TYPES = {"consumption", "waste", "expiration", "return"}


class StockError(HTTPException):
    def __init__(self, detail: str, status: int = 409):
        super().__init__(status, detail)


def factors_for(db: Session, product: Product | None, food: Food | None) -> FoodFactors:
    if food is None and product is not None and product.food_id:
        food = db.get(Food, product.food_id)
    f = FoodFactors.from_food(food)
    if product is not None and product.package_base_quantity and product.base_unit == "g":
        # a product's own pack weight lets "1 pack/tin/jar" convert for that product only
        for u in ("pack", "packet", "tin", "can", "jar", "bag", "bottle", "box", "pot", "punnet"):
            f.unit_grams.setdefault(u, product.package_base_quantity)
    return f


def lot_base_unit(product: Product | None, food: Food | None, entered_unit: str) -> str:
    if product is not None and product.base_unit:
        return product.base_unit
    if food is not None and food.default_unit:
        return food.default_unit
    return base_unit_for(entered_unit) or "each"


def normalise_for_lot(db: Session, product: Product | None, food: Food | None, quantity: float, unit: str,
                      base_unit: str) -> tuple[float, float]:
    """Return (quantity_base, factor). Packs convert via the product's package size."""
    if unit in ("pack", "packet", "tin", "can", "jar", "bag", "bottle", "box", "pot", "punnet", "each") \
            and product is not None and product.package_base_quantity and base_unit == product.base_unit \
            and product.base_unit != "each":
        f = product.package_base_quantity
        return quantity * f, f
    c = convert(quantity, unit, base_unit, factors_for(db, product, food))
    if c.quantity is None:
        raise StockError(f"Can't convert {quantity:g} {unit} to {base_unit}: {c.note}", 422)
    return c.quantity, c.factor or 1.0


def create_lot(db: Session, household_id: uuid.UUID, *, product: Product | None, food: Food | None,
               quantity: float, unit: str, location: str = "cupboard", event_type: str = "purchase",
               purchased_on: date | None = None, best_before: date | None = None, use_by: date | None = None,
               date_label: str | None = None, store: str | None = None, price_paid_pence: int | None = None,
               package_count: int | None = None, purchase_id: uuid.UUID | None = None, notes: str | None = None,
               opened: bool = False, actor_user_id=None, receipt_line_id=None, idempotency_key: str | None = None,
               occurred_at: datetime | None = None) -> PantryLot:
    if quantity <= 0:
        raise StockError("Quantity must be more than zero", 422)
    if idempotency_key:
        existing = db.scalar(select(StockEvent).where(StockEvent.idempotency_key == idempotency_key))
        if existing:
            return db.get(PantryLot, existing.lot_id)
    if food is None and product is not None and product.food_id:
        food = db.get(Food, product.food_id)
    base_unit = lot_base_unit(product, food, unit)
    qty_base, factor = normalise_for_lot(db, product, food, quantity, unit, base_unit)
    is_pet = bool((product and product.is_pet_food) or (food and food.is_pet_food))
    lot = PantryLot(
        household_id=household_id, product_id=product.id if product else None, food_id=food.id if food else None,
        location=location, entered_quantity=quantity, entered_unit=unit, base_unit=base_unit,
        conversion_factor=factor, initial_quantity_base=qty_base, quantity_base=qty_base,
        purchased_on=purchased_on, best_before=best_before, use_by=use_by, date_label=date_label, store=store,
        price_paid_pence=price_paid_pence, package_count=package_count, purchase_id=purchase_id, notes=notes,
        is_pet_food=is_pet, opened=opened, opened_at=utcnow() if opened else None,
    )
    db.add(lot)
    db.flush()
    db.add(StockEvent(
        household_id=household_id, lot_id=lot.id, type=event_type, delta_base=qty_base, entered_quantity=quantity,
        entered_unit=unit, conversion_factor=factor, resulting_quantity_base=qty_base, to_location=location,
        actor_user_id=actor_user_id, receipt_line_id=receipt_line_id, idempotency_key=idempotency_key,
        occurred_at=occurred_at or utcnow(), created_at=utcnow(), reason="Initial stock",
    ))
    return lot


@dataclass
class EventInput:
    type: str
    quantity: float | None = None  # in `unit`; sign is implied by type except for correction
    unit: str | None = None
    new_quantity: float | None = None  # for correction: the counted quantity (in `unit`)
    to_location: str | None = None
    reason: str | None = None
    negative_override: bool = False
    override_reason: str | None = None
    idempotency_key: str | None = None
    cooked_batch_id: uuid.UUID | None = None
    occurred_at: datetime | None = None


def record_event(db: Session, lot_id: uuid.UUID, household_id: uuid.UUID, ev: EventInput, actor_user_id=None) -> StockEvent:
    """Apply one event to one lot under a row lock. Rejects negative stock unless explicitly overridden."""
    if ev.idempotency_key:
        existing = db.scalar(select(StockEvent).where(StockEvent.idempotency_key == ev.idempotency_key))
        if existing:
            if existing.lot_id != lot_id:
                raise StockError("Idempotency key already used for a different lot")
            return existing
    lot = db.scalar(select(PantryLot).where(PantryLot.id == lot_id, PantryLot.household_id == household_id).with_for_update())
    if lot is None:
        raise StockError("Stock lot not found", 404)
    unit = ev.unit or lot.entered_unit
    product = db.get(Product, lot.product_id) if lot.product_id else None
    food = db.get(Food, lot.food_id) if lot.food_id else None

    def to_base(q: float) -> tuple[float, float]:
        if unit == lot.base_unit:
            return q, 1.0
        if unit == lot.entered_unit:
            return q * lot.conversion_factor, lot.conversion_factor
        return normalise_for_lot(db, product, food, q, unit, lot.base_unit)

    factor = None
    from_loc = to_loc = None
    if ev.type == "correction":
        if ev.new_quantity is None or ev.new_quantity < 0:
            raise StockError("A correction needs the counted quantity (0 or more)", 422)
        target, factor = to_base(ev.new_quantity)
        delta = target - lot.quantity_base
        if not ev.reason:
            raise StockError("Please give a reason for the correction", 422)
    elif ev.type == "transfer":
        if not ev.to_location or ev.to_location == lot.location:
            raise StockError("Choose a different location to move to", 422)
        delta = 0.0
        from_loc, to_loc = lot.location, ev.to_location
    elif ev.type == "open":
        delta = 0.0
    elif ev.type in NEGATIVE_TYPES:
        if ev.quantity is None:
            # "use it all" / expiry of whole lot
            delta = -lot.quantity_base
            factor = 1.0
        else:
            if ev.quantity <= 0:
                raise StockError("Quantity must be more than zero", 422)
            q, factor = to_base(ev.quantity)
            delta = -q
    elif ev.type in ("purchase", "add"):
        if ev.quantity is None or ev.quantity <= 0:
            raise StockError("Quantity must be more than zero", 422)
        q, factor = to_base(ev.quantity)
        delta = q
    else:
        raise StockError(f"Unknown event type {ev.type}", 422)

    resulting = lot.quantity_base + delta
    if resulting < -EPS:
        if not ev.negative_override:
            raise StockError(
                f"That would take stock below zero (on hand {lot.quantity_base:g} {lot.base_unit}). "
                "Correct the count first, or confirm an override with a reason.")
        if not ev.override_reason:
            raise StockError("A negative-stock override needs an explanation", 422)
    if abs(resulting) < EPS:
        resulting = 0.0

    event = StockEvent(
        household_id=household_id, lot_id=lot.id, type=ev.type, delta_base=delta,
        entered_quantity=ev.quantity if ev.type != "correction" else ev.new_quantity, entered_unit=unit,
        conversion_factor=factor, resulting_quantity_base=resulting, from_location=from_loc, to_location=to_loc,
        reason=ev.reason, negative_override=ev.negative_override and resulting < 0,
        override_reason=ev.override_reason if (ev.negative_override and resulting < 0) else None,
        idempotency_key=ev.idempotency_key, cooked_batch_id=ev.cooked_batch_id, actor_user_id=actor_user_id,
        occurred_at=ev.occurred_at or utcnow(), created_at=utcnow(),
    )
    db.add(event)
    lot.quantity_base = resulting
    if to_loc:
        lot.location = to_loc
    if ev.type == "open" and not lot.opened:
        lot.opened, lot.opened_at = True, utcnow()
    if ev.type in ("consumption", "waste") and not lot.opened and lot.base_unit != "each":
        lot.opened, lot.opened_at = True, utcnow()
    lot.status = "active" if resulting > EPS else "depleted"
    lot.row_version += 1
    db.flush()
    return event


def usable_lots(db: Session, household_id: uuid.UUID, *, food_id=None, product_id=None, on: date | None = None,
                include_pet: bool = False) -> list[PantryLot]:
    """Active lots for a food (including all its products) or a product, earliest relevant date first.

    A lot whose *use-by* date is before `on` is excluded from planning arithmetic (it is still shown in the
    pantry with its label wording; the app does not decide whether food is safe). Best-before does not exclude.
    """
    q = select(PantryLot).where(PantryLot.household_id == household_id, PantryLot.status == "active",
                                PantryLot.quantity_base > EPS)
    if not include_pet:
        q = q.where(PantryLot.is_pet_food.is_(False))
    if product_id:
        q = q.where(PantryLot.product_id == product_id)
    elif food_id:
        prod_ids = select(Product.id).where(Product.food_id == food_id)
        q = q.where((PantryLot.food_id == food_id) | (PantryLot.product_id.in_(prod_ids)))
    else:
        return []
    lots = list(db.scalars(q))
    if on:
        lots = [lot for lot in lots if lot.use_by is None or lot.use_by >= on]
    lots.sort(key=lambda lot: (relevant_date(lot) or date.max, lot.created_at))
    return lots


def relevant_date(lot: PantryLot) -> date | None:
    dates = [d for d in (lot.use_by, lot.best_before) if d]
    return min(dates) if dates else None


def consume_fefo(db: Session, household_id: uuid.UUID, *, food_id=None, product_id=None, quantity_base: float,
                 base_unit: str, reason: str, cooked_batch_id=None, actor_user_id=None,
                 key_prefix: str | None = None, preferred_lot_ids: list[uuid.UUID] | None = None) -> tuple[list[dict], float]:
    """Deduct from lots first-expiry-first-out. Returns (deductions, shortfall_base). Never goes negative."""
    lots = usable_lots(db, household_id, food_id=food_id, product_id=product_id)
    if preferred_lot_ids:
        lots.sort(key=lambda lot: 0 if lot.id in preferred_lot_ids else 1)
    remaining = quantity_base
    out = []
    for i, lot in enumerate(lots):
        if remaining <= EPS:
            break
        if lot.base_unit != base_unit:
            continue
        take = min(lot.quantity_base, remaining)
        if take <= EPS:
            continue
        record_event(db, lot.id, household_id, EventInput(
            type="consumption", quantity=take, unit=lot.base_unit, reason=reason, cooked_batch_id=cooked_batch_id,
            idempotency_key=f"{key_prefix}:{lot.id}" if key_prefix else None,
        ), actor_user_id=actor_user_id)
        out.append({"lot_id": str(lot.id), "quantity_base": take, "base_unit": base_unit})
        remaining -= take
    return out, max(0.0, remaining)
