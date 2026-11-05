"""Price observations and history based on the household's actual purchases."""
from __future__ import annotations

import statistics
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Offer, PriceObservation, Purchase, utcnow


def standard_unit(base_unit: str | None) -> str:
    return {"g": "kg", "ml": "L"}.get(base_unit or "", "item")


def unit_price(price_pence: int, quantity_base: float | None, base_unit: str | None, package_count: float = 1) -> float | None:
    """Pence per kg / L / item."""
    if base_unit in ("g", "ml"):
        if not quantity_base:
            return None
        return price_pence / (quantity_base / 1000.0)
    count = quantity_base or package_count
    return price_pence / count if count else None


def observe(db: Session, household_id: uuid.UUID, *, kind: str, price_pence: int, product_id=None, food_id=None,
            purchase_id=None, offer_id=None, store=None, retailer=None, observed_at: datetime | None = None,
            package_count: float = 1, quantity_base: float | None = None, base_unit: str | None = None,
            promotion: str | None = None, source: str = "manual", confidence: float = 1.0) -> PriceObservation:
    obs = PriceObservation(
        household_id=household_id, product_id=product_id, food_id=food_id, purchase_id=purchase_id, offer_id=offer_id,
        kind=kind, store=store, retailer=retailer, observed_at=observed_at or utcnow(), price_pence=price_pence,
        package_count=package_count, quantity_base=quantity_base, base_unit=base_unit,
        standard_unit=standard_unit(base_unit), unit_price_pence=unit_price(price_pence, quantity_base, base_unit, package_count),
        promotion=promotion, source=source, confidence=confidence,
    )
    db.add(obs)
    return obs


def record_purchase(db: Session, household_id: uuid.UUID, *, product_id=None, food_id=None, store=None, retailer=None,
                    purchased_at: datetime, package_count: float = 1, quantity_base: float | None = None,
                    base_unit: str | None = None, shelf_price_pence: int | None = None, paid_price_pence: int | None = None,
                    promotion: str | None = None, source: str = "manual", receipt_id=None, receipt_line_id=None,
                    is_pet_food: bool = False, confidence: float = 1.0) -> Purchase:
    pur = Purchase(household_id=household_id, receipt_id=receipt_id, receipt_line_id=receipt_line_id, product_id=product_id,
                   food_id=food_id, store=store, retailer=retailer, purchased_at=purchased_at, package_count=package_count,
                   quantity_base=quantity_base, base_unit=base_unit, shelf_price_pence=shelf_price_pence,
                   paid_price_pence=paid_price_pence, source=source, is_pet_food=is_pet_food)
    db.add(pur)
    db.flush()
    common = dict(product_id=product_id, food_id=food_id, purchase_id=pur.id, store=store, retailer=retailer,
                  observed_at=purchased_at, package_count=package_count, quantity_base=quantity_base, base_unit=base_unit,
                  source=source, confidence=confidence)
    if paid_price_pence is not None:
        observe(db, household_id, kind="paid", price_pence=paid_price_pence, promotion=promotion, **common)
    if shelf_price_pence is not None and shelf_price_pence != paid_price_pence:
        observe(db, household_id, kind="shelf", price_pence=shelf_price_pence, **common)
    return pur


def offer_is_current(o: Offer, today=None) -> bool:
    """An offer is shown as current only with a source, capture time, store/region and an expiry that hasn't passed."""
    today = today or utcnow().date()
    return bool(o.source and o.captured_at and o.store_region and o.valid_to and o.valid_to >= today
                and (o.valid_from is None or o.valid_from <= today))


def history(db: Session, household_id: uuid.UUID, *, product_id=None, food_id=None) -> dict:
    q = select(PriceObservation).where(PriceObservation.household_id == household_id)
    if product_id:
        q = q.where(PriceObservation.product_id == product_id)
    elif food_id:
        q = q.where(PriceObservation.food_id == food_id)
    obs = list(db.scalars(q.order_by(PriceObservation.observed_at)))
    paid = [o for o in obs if o.kind == "paid" and o.unit_price_pence is not None]
    # Group by store + pack size; never collapse different sizes/stores into one figure.
    groups: dict[tuple, list[PriceObservation]] = {}
    for o in paid:
        groups.setdefault((o.store or o.retailer or "Unknown", o.quantity_base, o.base_unit), []).append(o)
    summary = None
    if paid:
        ups = [o.unit_price_pence for o in paid]
        last = paid[-1]
        summary = {
            "standard_unit": last.standard_unit, "usually_paid": round(statistics.median(ups), 1),
            "lowest": round(min(ups), 1), "highest": round(max(ups), 1), "observations": len(paid),
            "last_purchased": {"at": last.observed_at.isoformat(), "price_pence": last.price_pence,
                               "unit_price_pence": round(last.unit_price_pence, 1), "store": last.store or last.retailer,
                               "promotion": last.promotion},
            "basis": "Your recorded purchases (effective price paid after line discounts)",
        }
    offers_q = select(Offer).where(Offer.household_id == household_id)
    if product_id:
        offers_q = offers_q.where(Offer.product_id == product_id)
    elif food_id:
        offers_q = offers_q.where(Offer.food_id == food_id)
    return {
        "summary": summary,
        "series": [{"at": o.observed_at.isoformat(), "kind": o.kind, "price_pence": o.price_pence,
                    "unit_price_pence": o.unit_price_pence, "standard_unit": o.standard_unit, "store": o.store or o.retailer,
                    "quantity_base": o.quantity_base, "base_unit": o.base_unit, "package_count": o.package_count,
                    "promotion": o.promotion, "source": o.source, "confidence": o.confidence} for o in obs],
        "groups": [{"store": k[0], "pack_quantity_base": k[1], "base_unit": k[2], "count": len(v),
                    "median_unit_price_pence": round(statistics.median([o.unit_price_pence for o in v]), 1)}
                   for k, v in groups.items()],
        "offers": [{"id": str(o.id), "retailer": o.retailer, "store_region": o.store_region, "description": o.description,
                    "price_pence": o.price_pence, "source": o.source, "source_url": o.source_url,
                    "captured_at": o.captured_at.isoformat(), "valid_to": o.valid_to.isoformat() if o.valid_to else None,
                    "current": offer_is_current(o)} for o in db.scalars(offers_q)],
    }
