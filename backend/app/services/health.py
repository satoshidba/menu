"""Vitamins, supplements and medicines: dose logging with label checks, regimens due today.

Larder records what the household enters from the label, prescription or health professional. It never calculates
or recommends doses. The checks compare a new dose with the *entered* minimum interval and maximum doses in 24 hours
and ask for confirmation; they are a memory aid, not medical advice.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DoseLog, Household, MealConsumption, PantryLot, PersonProfile, Product, Regimen, utcnow
from .nutrition import NutritionAccumulator, snapshot_values
from .nutrition_db import item_contribution
from .stock import consume_fefo, factors_for, usable_lots
from .units import convert, unit_kind

HEALTH_KINDS = ("supplement", "medicine")
ADVICE = "Larder only checks against the directions you entered. If unsure, ask a pharmacist, GP or NHS 111."


def _fmt_dt(dt: datetime, tz: str) -> str:
    return dt.astimezone(ZoneInfo(tz)).strftime("%a %d %b %H:%M")


def dose_warnings(db: Session, person: PersonProfile, product: Product, given_at: datetime, tz: str) -> list[dict]:
    out: list[dict] = []
    prior = list(db.scalars(select(DoseLog).where(DoseLog.person_id == person.id, DoseLog.product_id == product.id,
                                                  DoseLog.deleted_at.is_(None), DoseLog.given_at <= given_at,
                                                  DoseLog.given_at > given_at - timedelta(hours=24)).order_by(DoseLog.given_at.desc())))
    if product.label_min_interval_hours and prior:
        last = prior[0].given_at
        gap = (given_at - last).total_seconds() / 3600
        if gap < product.label_min_interval_hours:
            next_ok = last + timedelta(hours=product.label_min_interval_hours)
            out.append({"code": "interval", "message":
                        f"{person.name} last had {product.name} at {_fmt_dt(last, tz)} ({gap:.1f} h ago). The directions you entered "
                        f"say at least {product.label_min_interval_hours:g} hours between doses (next from {_fmt_dt(next_ok, tz)})."})
    if product.label_max_doses_24h and len(prior) + 1 > product.label_max_doses_24h:
        out.append({"code": "max_24h", "message":
                    f"This would be dose {len(prior) + 1} of {product.name} for {person.name} in 24 hours. The directions you "
                    f"entered say no more than {product.label_max_doses_24h}."})
    if product.kind == "medicine" and not (product.label_directions or product.label_min_interval_hours or product.label_max_doses_24h):
        out.append({"code": "no_directions", "message": f"No label directions are recorded for {product.name}; check the packet."})
    lots = usable_lots(db, person.household_id, product_id=product.id, include_pet=True)
    if lots and all(l.use_by and l.use_by < given_at.date() for l in lots):
        out.append({"code": "expired", "message": f"Every {product.name} in the cabinet is past its expiry date."})
    return out


def next_allowed(db: Session, person: PersonProfile, product: Product) -> datetime | None:
    last = db.scalar(select(DoseLog.given_at).where(DoseLog.person_id == person.id, DoseLog.product_id == product.id,
                                                    DoseLog.deleted_at.is_(None)).order_by(DoseLog.given_at.desc()))
    if last and product.label_min_interval_hours:
        t = last + timedelta(hours=product.label_min_interval_hours)
        return t if t > utcnow() else None
    return None


def log_dose(db: Session, household_id: uuid.UUID, *, person: PersonProfile, product: Product, amount: float | None, unit: str | None,
             given_at: datetime | None, regimen: Regimen | None, acknowledge_warnings: bool, deduct_from_stock: bool,
             notes: str | None, user_id=None) -> tuple[DoseLog, list[dict], dict | None]:
    if product.kind not in HEALTH_KINDS:
        raise HTTPException(422, "That isn't a medicine or supplement; log food from the nutrition screen")
    tz = db.get(Household, household_id).timezone
    given_at = given_at or utcnow()
    amount = amount if amount is not None else (regimen.dose_amount if regimen else product.dose_amount or 1)
    unit = unit or (regimen.dose_unit if regimen else product.dose_unit) or "dose"
    warnings = dose_warnings(db, person, product, given_at, tz)
    blocking = [w for w in warnings if w["code"] in ("interval", "max_24h", "expired")]
    if blocking and not acknowledge_warnings:
        raise HTTPException(409, {"code": "check_label", "message": "Check before giving this dose.", "warnings": warnings, "advice": ADVICE})

    dose = DoseLog(household_id=household_id, person_id=person.id, product_id=product.id, regimen_id=regimen.id if regimen else None,
                   description=f"{product.brand + ' ' if product.brand else ''}{product.name}", amount=amount, unit=unit,
                   given_at=given_at, given_by_id=user_id, warnings_acknowledged=blocking, notes=notes)
    db.add(dose)
    db.flush()

    stock_note = None
    if deduct_from_stock:
        lots = usable_lots(db, household_id, product_id=product.id, include_pet=True)
        if lots:
            base = lots[0].base_unit
            uq, u = (amount * (product.dose_amount or 1), product.dose_unit) if unit == "dose" and product.dose_unit else (amount, unit)
            if u == base:
                q = uq
            elif base == "each" and unit_kind(u) == "count":
                q = uq  # tablets, capsules, sachets… counted individually
            else:
                q = convert(uq, u, base, factors_for(db, product, None)).quantity
            if q is not None:
                _, short = consume_fefo(db, household_id, product_id=product.id, quantity_base=q, base_unit=base,
                                        reason=f"Dose for {person.name}", actor_user_id=user_id, key_prefix=f"dose:{dose.id}")
                stock_note = f"Cabinet stock short by {short:g} {base}" if short > 1e-6 else None
            else:
                stock_note = f"Stock not adjusted: can't convert {unit} to {base}. Set the pack size in {unit}s."
        else:
            stock_note = "None in the cabinet to take from."

    consumption = None
    if product.kind == "supplement":
        acc = NutritionAccumulator()
        acc.add(item_contribution(db, label=dose.description, quantity=amount, unit=unit, product=product))
        summary = acc.result()
        if any(d["value"] is not None for d in summary["nutrients"].values()):
            vals, units, status = snapshot_values(summary)
            per = NutritionAccumulator()
            per.add(item_contribution(db, label=dose.description, quantity=1, unit=unit, product=product))
            c = MealConsumption(household_id=household_id, person_id=person.id, external_id=f"lrd-mc-{uuid.uuid4()}",
                                consumed_at=given_at, timezone=tz, meal_slot="supplement", product_id=product.id,
                                description=dose.description, amount=amount, amount_unit=unit, nutrition=vals, nutrition_units=units,
                                provenance={"source": "supplement", "dose_log_id": str(dose.id), "per_unit": per.result(), "unit": unit,
                                            "status": status, "complete": False, "estimated": True,
                                            "note": "Only the nutrients on the supplement label are counted; others are unknown, not zero."},
                                confidence=0.9, created_by_id=user_id)
            db.add(c)
            db.flush()
            dose.consumption_id = c.id
            consumption = {"id": str(c.id), "nutrition": {k: v for k, v in vals.items() if v is not None}}
    return dose, warnings + ([{"code": "stock", "message": stock_note}] if stock_note else []), consumption


def due_today(db: Session, household_id: uuid.UUID, people_filter=None) -> list[dict]:
    tz = db.get(Household, household_id).timezone
    zone = ZoneInfo(tz)
    today = datetime.now(zone).date()
    start = datetime.combine(today, datetime.min.time(), zone)
    regimens = db.scalars(select(Regimen).where(Regimen.household_id == household_id, Regimen.active.is_(True),
                                                Regimen.start_date <= today,
                                                (Regimen.end_date.is_(None)) | (Regimen.end_date >= today)))
    out = []
    for r in regimens:
        person = db.get(PersonProfile, r.person_id)
        if people_filter is not None and not people_filter(person):
            continue
        product = db.get(Product, r.product_id)
        given = list(db.scalars(select(DoseLog).where(DoseLog.person_id == r.person_id, DoseLog.product_id == r.product_id,
                                                      DoseLog.deleted_at.is_(None), DoseLog.given_at >= start).order_by(DoseLog.given_at)))
        na = next_allowed(db, person, product)
        out.append({
            "regimen_id": str(r.id), "person_id": str(person.id), "person": person.name, "person_kind": person.kind,
            "product_id": str(product.id), "item": f"{product.brand + ' ' if product.brand else ''}{product.name}", "kind": product.kind,
            "dose": f"{r.dose_amount:g} {r.dose_unit}", "times_per_day": r.times_per_day, "as_needed": r.as_needed,
            "given_today": len(given), "given_times": [g.given_at.astimezone(zone).strftime("%H:%M") for g in given],
            "remaining_today": 0 if r.as_needed else max(0, r.times_per_day - len(given)),
            "next_allowed_at": na.isoformat() if na else None, "instructions": r.instructions, "source": r.source,
            "end_date": r.end_date.isoformat() if r.end_date else None,
        })
    out.sort(key=lambda x: (x["remaining_today"] == 0, x["person"], x["item"]))
    return out


def cabinet(db: Session, household_id: uuid.UUID, kind: str | None = None) -> list[dict]:
    q = select(Product).where(Product.household_id == household_id, Product.kind.in_([kind] if kind else HEALTH_KINDS))
    today = date.today()
    out = []
    for p in db.scalars(q.order_by(Product.kind, Product.name)):
        lots = list(db.scalars(select(PantryLot).where(PantryLot.product_id == p.id, PantryLot.status == "active").order_by(PantryLot.use_by)))
        total = sum(l.quantity_base for l in lots)
        earliest = min((l.use_by or l.best_before for l in lots if (l.use_by or l.best_before)), default=None)
        out.append({"product": p, "lots": lots, "total": total, "earliest_expiry": earliest,
                    "days_to_expiry": (earliest - today).days if earliest else None,
                    "low_stock": p.min_stock_base is not None and total <= p.min_stock_base})
    return out


def doses_between(db: Session, person_id, start: datetime, end: datetime) -> list[DoseLog]:
    return list(db.scalars(select(DoseLog).where(DoseLog.person_id == person_id, DoseLog.deleted_at.is_(None), DoseLog.given_at >= start,
                                                 DoseLog.given_at < end).order_by(DoseLog.given_at.desc())))
