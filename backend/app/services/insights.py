"""Household insights derived from what is already recorded: spending, usage and waste, unit-price trends, and
run-out forecasts.

Everything comes from purchases, price observations and the stock ledger. Each figure carries its basis, coverage
notes say what's missing (unpriced purchases, waste without a known cost), and forecasts appear only after enough
history, as a date range with a confidence label rather than a single confident date.
"""
from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Food, Household, PantryLot, PriceObservation, Product, Purchase, ShoppingListItem, StockEvent
from .units import uk_display

HEALTH = ("supplement", "medicine")
OUT_TYPES = ("consumption", "waste", "expiration")
WASTE_TYPES = ("waste", "expiration")

# Forecast thresholds: below these there isn't enough history to say anything useful.
USAGE_LOOKBACK_DAYS = 56
USAGE_MIN_DAYS = 14
USAGE_MIN_EVENTS = 4
PURCHASE_LOOKBACK_DAYS = 180
PURCHASE_MIN = 3


def amount(q: float, unit: str | None) -> str:
    """Counts read as whole (or half) items; weights and volumes use the usual UK display."""
    if unit == "each":
        n = round(q * 2) / 2
        return f"{n:g}"
    return uk_display(q, unit)


def short_date(d: date) -> str:
    return f"{d:%a} {d.day} {d:%b}"


def _today(db: Session, household_id: uuid.UUID) -> tuple[date, ZoneInfo]:
    h = db.get(Household, household_id)
    tz = ZoneInfo(h.timezone if h and h.timezone else "Europe/London")
    return datetime.now(tz).date(), tz


def _week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _health_product_ids(db: Session, household_id: uuid.UUID) -> set[uuid.UUID]:
    return set(db.scalars(select(Product.id).where(Product.household_id == household_id, Product.kind.in_(HEALTH))))


def _names(db: Session, household_id: uuid.UUID) -> tuple[dict, dict]:
    foods = {f.id: f for f in db.scalars(select(Food).where(Food.household_id == household_id))}
    products = {p.id: p for p in db.scalars(select(Product).where(Product.household_id == household_id))}
    return foods, products


def _label(foods: dict, products: dict, food_id, product_id) -> str:
    if food_id and food_id in foods:
        return foods[food_id].name
    p = products.get(product_id)
    return f"{p.brand + ' ' if p and p.brand else ''}{p.name}" if p else "Unknown item"


# ---------------------------------------------------------------- spending
def spending(db: Session, household_id: uuid.UUID, weeks: int = 12) -> dict:
    today, tz = _today(db, household_id)
    first_week = _week(today) - timedelta(weeks=weeks - 1)
    prior_start = first_week - timedelta(weeks=weeks)
    rows = db.execute(select(Purchase, Product.kind).outerjoin(Product, Product.id == Purchase.product_id)
                      .where(Purchase.household_id == household_id,
                             Purchase.purchased_at >= datetime.combine(prior_start, time.min, tz))).all()
    series = {first_week + timedelta(weeks=i): {"food": 0, "pet": 0, "health": 0} for i in range(weeks)}
    stores: dict[str, int] = defaultdict(int)
    prior_total, unpriced, first_seen = 0, 0, None
    for pur, kind in rows:
        d = pur.purchased_at.astimezone(tz).date()
        if pur.paid_price_pence is None:
            unpriced += d >= first_week
            continue
        amount = -abs(pur.paid_price_pence) if pur.is_return else pur.paid_price_pence
        if d < first_week:
            prior_total += amount
            continue
        cat = "health" if kind in HEALTH else "pet" if (pur.is_pet_food or kind == "pet_food") else "food"
        wk = _week(d)
        if wk in series:
            series[wk][cat] += amount
        stores[pur.retailer or pur.store or "Not recorded"] += amount
        first_seen = min(first_seen, d) if first_seen else d
    total = sum(sum(v.values()) for v in series.values())
    weeks_with_data = len([w for w in series if first_seen and w >= _week(first_seen)]) or 0
    return {
        "weeks": [{"week": w.isoformat(), **v, "total": sum(v.values())} for w, v in series.items()],
        "total_pence": total,
        "weeks_with_records": weeks_with_data,
        "average_week_pence": round(total / weeks_with_data) if weeks_with_data else None,
        "previous_period_pence": prior_total if prior_total else None,
        "by_store": sorted(({"store": k, "pence": v} for k, v in stores.items()), key=lambda x: -x["pence"]),
        "unpriced_purchases": unpriced,
        "basis": "Prices paid on applied receipts and items added with a price, after line discounts. Basket-level "
                 "vouchers and points aren't included.",
    }


# ---------------------------------------------------------------- usage and waste
def usage_and_waste(db: Session, household_id: uuid.UUID, weeks: int = 12) -> dict:
    today, tz = _today(db, household_id)
    first_week = _week(today) - timedelta(weeks=weeks - 1)
    since = datetime.combine(first_week, time.min, tz)
    health = _health_product_ids(db, household_id)
    foods, products = _names(db, household_id)
    rows = db.execute(select(StockEvent, PantryLot).join(PantryLot, PantryLot.id == StockEvent.lot_id)
                      .where(StockEvent.household_id == household_id, StockEvent.occurred_at >= since,
                             StockEvent.type.in_(OUT_TYPES))).all()
    items: dict[tuple, dict] = {}
    weekly = {first_week + timedelta(weeks=i): 0.0 for i in range(weeks)}
    used_value = wasted_value = 0.0
    unpriced_waste = 0
    for ev, lot in rows:
        if lot.product_id in health:
            continue
        qty = -ev.delta_base
        if qty <= 0:
            continue
        value = qty / lot.initial_quantity_base * lot.price_paid_pence if lot.price_paid_pence and lot.initial_quantity_base else None
        key = (lot.food_id or lot.product_id, lot.base_unit)
        it = items.setdefault(key, {"name": _label(foods, products, lot.food_id, lot.product_id), "food_id": lot.food_id,
                                    "base_unit": lot.base_unit, "used": 0.0, "wasted": 0.0, "used_value": 0.0,
                                    "wasted_value": 0.0, "used_events": 0, "waste_events": 0, "is_pet_food": lot.is_pet_food})
        if ev.type in WASTE_TYPES:
            it["wasted"] += qty
            it["waste_events"] += 1
            if value is None:
                unpriced_waste += 1
            else:
                it["wasted_value"] += value
                wasted_value += value
                weekly[_week(ev.occurred_at.astimezone(tz).date())] = weekly.get(_week(ev.occurred_at.astimezone(tz).date()), 0) + value
        else:
            it["used"] += qty
            it["used_events"] += 1
            if value is not None:
                it["used_value"] += value
                used_value += value

    def out(it: dict, which: str) -> dict:
        return {"name": it["name"], "food_id": str(it["food_id"]) if it["food_id"] else None, "is_pet_food": it["is_pet_food"],
                "quantity_display": uk_display(it[which], it["base_unit"]), "events": it[f"{'used' if which == 'used' else 'waste'}_events"],
                "value_pence": round(it[f"{which}_value"]) if it[f"{which}_value"] else None}

    wasted = sorted((i for i in items.values() if i["wasted"]), key=lambda i: (-i["wasted_value"], -i["waste_events"]))
    used = sorted((i for i in items.values() if i["used"]), key=lambda i: (-i["used_events"], -i["used_value"]))
    priced_total = used_value + wasted_value
    return {
        "used_value_pence": round(used_value),
        "wasted_value_pence": round(wasted_value),
        "waste_share": round(wasted_value / priced_total, 3) if priced_total else None,
        "waste_weeks": [{"week": w.isoformat(), "pence": round(v)} for w, v in sorted(weekly.items()) if w >= first_week],
        "top_wasted": [out(i, "wasted") for i in wasted[:8]],
        "top_used": [out(i, "used") for i in used[:10]],
        "unpriced_waste_events": unpriced_waste,
        "basis": "From the pantry history: what was used, thrown away or past its date. Value is the share of the price "
                 "paid for that pack, so items added without a price aren't counted in the money figures.",
    }


# ---------------------------------------------------------------- price trends
def price_trends(db: Session, household_id: uuid.UUID, days: int = 365, limit: int = 12) -> list[dict]:
    today, tz = _today(db, household_id)
    foods, products = _names(db, household_id)
    obs = db.scalars(select(PriceObservation).where(
        PriceObservation.household_id == household_id, PriceObservation.kind == "paid",
        PriceObservation.unit_price_pence.is_not(None),
        PriceObservation.observed_at >= datetime.combine(today - timedelta(days=days), time.min, tz))
        .order_by(PriceObservation.observed_at))
    groups: dict[tuple, list[PriceObservation]] = defaultdict(list)
    for o in obs:
        if o.product_id and products.get(o.product_id) and products[o.product_id].kind in HEALTH:
            continue
        fid = o.food_id or (products[o.product_id].food_id if o.product_id in products else None)
        groups[(fid or o.product_id, o.standard_unit)].append(o)
    out = []
    for (key, std), points in groups.items():
        if len(points) < 2 or (points[-1].observed_at - points[0].observed_at).days < 14:
            continue
        earlier = statistics.median(p.unit_price_pence for p in points[:-1])
        latest = points[-1]
        by_store: dict[str, list[float]] = defaultdict(list)
        for p in points:
            by_store[p.retailer or p.store or "Not recorded"].append(p.unit_price_pence)
        stores = sorted(({"store": s, "median": round(statistics.median(v), 1), "count": len(v)} for s, v in by_store.items()),
                        key=lambda x: x["median"])
        low = min(points, key=lambda p: p.unit_price_pence)
        out.append({
            "name": _label(foods, products, key if key in foods else None, key if key in products else None),
            "food_id": str(key) if key in foods else None, "standard_unit": std,
            "points": [{"date": p.observed_at.astimezone(tz).date().isoformat(), "unit_price": round(p.unit_price_pence, 1),
                        "store": p.retailer or p.store, "promotion": p.promotion} for p in points],
            "latest": round(latest.unit_price_pence, 1), "earlier_median": round(earlier, 1),
            "change_pct": round((latest.unit_price_pence - earlier) / earlier * 100, 1) if earlier else None,
            "lowest": {"unit_price": round(low.unit_price_pence, 1), "store": low.retailer or low.store,
                       "date": low.observed_at.astimezone(tz).date().isoformat()},
            "stores": stores,
        })
    out.sort(key=lambda x: (-len(x["points"]), -abs(x["change_pct"] or 0)))
    return out[:limit]


# ---------------------------------------------------------------- forecasts
def forecasts(db: Session, household_id: uuid.UUID) -> dict:
    """When each regularly-used food is likely to run out.

    Usage method: what left the pantry (used, thrown away, past its date) over the last 8 weeks gives a daily rate;
    week-to-week variation widens the range. Needs 2+ weeks of history and 4+ events.
    Purchase method (when usage isn't logged): the typical gap between purchases, from 3+ purchases in 6 months.
    """
    today, tz = _today(db, household_id)
    health = _health_product_ids(db, household_id)
    foods, _ = _names(db, household_id)
    lookback = datetime.combine(today - timedelta(days=USAGE_LOOKBACK_DAYS), time.min, tz)

    stock: dict[tuple, float] = defaultdict(float)
    for lot in db.scalars(select(PantryLot).where(PantryLot.household_id == household_id, PantryLot.status == "active",
                                                  PantryLot.food_id.is_not(None))):
        if lot.product_id not in health:
            stock[(lot.food_id, lot.base_unit)] += max(0.0, lot.quantity_base)

    first_seen = dict(db.execute(select(PantryLot.food_id, func.min(StockEvent.occurred_at))
                                 .join(PantryLot, PantryLot.id == StockEvent.lot_id)
                                 .where(StockEvent.household_id == household_id, PantryLot.food_id.is_not(None))
                                 .group_by(PantryLot.food_id)).all())
    out_events: dict[tuple, list[tuple[date, float]]] = defaultdict(list)
    for ev, lot in db.execute(select(StockEvent, PantryLot).join(PantryLot, PantryLot.id == StockEvent.lot_id)
                              .where(StockEvent.household_id == household_id, StockEvent.occurred_at >= lookback,
                                     StockEvent.type.in_(OUT_TYPES), PantryLot.food_id.is_not(None))):
        if lot.product_id not in health and ev.delta_base < 0:
            out_events[(lot.food_id, lot.base_unit)].append((ev.occurred_at.astimezone(tz).date(), -ev.delta_base))

    on_list = set(db.scalars(select(ShoppingListItem.food_id).where(ShoppingListItem.household_id == household_id,
                                                                    ShoppingListItem.checked.is_(False),
                                                                    ShoppingListItem.dismissed.is_(False))))
    results, learning, done = [], [], set()
    for key, evs in out_events.items():
        fid, unit = key
        seen = first_seen.get(fid)
        start = max(today - timedelta(days=USAGE_LOOKBACK_DAYS), seen.astimezone(tz).date() if seen else today)
        observed = (today - start).days
        if observed < USAGE_MIN_DAYS or len(evs) < USAGE_MIN_EVENTS:
            learning.append(foods[fid].name)
            continue
        last_used = max(d for d, _ in evs)
        if (today - last_used).days > 21:  # not used for three weeks: probably stopped buying it
            continue
        total = sum(q for _, q in evs)
        nweeks = max(2, observed // 7)
        weekly = [0.0] * nweeks
        for d, q in evs:
            i = (today - d).days // 7
            if i < nweeks:
                weekly[i] += q
        mean = statistics.fmean(weekly)
        cv = min(1.0, statistics.pstdev(weekly) / mean) if mean else 1.0
        rate = total / observed
        have = stock.get(key, 0.0)
        mid = have / rate
        early, late = have / (rate * (1 + cv)), have / (rate * max(0.3, 1 - cv))
        conf = "high" if nweeks >= 6 and cv <= 0.35 else "low" if nweeks < 3 or cv > 0.75 else "medium"
        results.append(_forecast(foods[fid], unit, "usage", have, today, mid, early, late, conf, fid in on_list,
                                 f"Got through {amount(total, unit)} in {nweeks} weeks (about {amount(rate * 7, unit)} a week, "
                                 f"including any thrown away); {amount(have, unit)} left", per_week=rate * 7))
        done.add(fid)

    purchases: dict[uuid.UUID, list[Purchase]] = defaultdict(list)
    for pur in db.scalars(select(Purchase).where(
            Purchase.household_id == household_id, Purchase.is_return.is_(False), Purchase.food_id.is_not(None),
            Purchase.purchased_at >= datetime.combine(today - timedelta(days=PURCHASE_LOOKBACK_DAYS), time.min, tz))
            .order_by(Purchase.purchased_at)):
        if pur.product_id not in health:
            purchases[pur.food_id].append(pur)
    for fid, purs in purchases.items():
        if fid in done or fid not in foods:
            continue
        days = sorted({p.purchased_at.astimezone(tz).date() for p in purs})
        if len(days) < PURCHASE_MIN:
            if fid not in [f for f, _ in out_events]:
                learning.append(foods[fid].name)
            continue
        gaps = [(b - a).days for a, b in zip(days, days[1:])]
        typical = statistics.median(gaps)
        if len(gaps) >= 4:
            q = statistics.quantiles(gaps, n=4)
            lo, hi = q[0], q[2]
        else:
            lo, hi = min(gaps), max(gaps)
        last = days[-1]
        if (today - last).days > hi + typical:  # stopped buying it; don't nag
            continue
        spread = (hi - lo) / typical if typical else 1
        conf = "medium" if len(days) >= 5 and spread <= 0.5 else "low"
        unit = purs[-1].base_unit or foods[fid].default_unit or "each"
        have = stock.get((fid, unit), 0.0)
        since = (today - last).days
        sizes = [p.quantity_base for p in purs if p.quantity_base and p.base_unit == unit]
        usual = statistics.median(sizes) if sizes else None
        fc = _forecast(foods[fid], unit, "purchases", have, today, typical - since, lo - since, hi - since, conf,
                       fid in on_list, f"Bought {len(days)} times since {short_date(days[0])}, usually every "
                       f"{typical:g} days; last bought {short_date(last)}"
                       + (f"; {amount(have, unit)} in stock" if have else ""))
        # A purchase rhythm says nothing about a cupboard that's already full; don't push it onto the list
        fc["stock_covers_usual_buy"] = bool(usual and have >= usual)
        results.append(fc)
    results.sort(key=lambda r: (r["earliest"], r["name"]))
    return {"forecasts": results, "learning": sorted(set(learning))[:12],
            "basis": "Usage forecasts need 2 weeks and 4 recorded uses within the last 8 weeks; purchase-based ones need "
                     "3 purchases in 6 months. Ranges widen when use varies from week to week."}


def _forecast(food: Food, unit: str, method: str, have: float, today: date, mid: float, early: float, late: float, conf: str,
              on_list: bool, basis: str, per_week: float | None = None) -> dict:
    def day(n: float) -> date:
        return today + timedelta(days=max(0, round(n)))
    return {"food_id": str(food.id), "name": food.name, "is_pet_food": food.is_pet_food, "base_unit": unit, "method": method,
            "stock_display": amount(have, unit), "per_week_display": amount(per_week, unit) if per_week else None,
            "run_out": day(mid).isoformat(), "earliest": day(early).isoformat(), "latest": day(late).isoformat(),
            "days_left": max(0, round(mid)), "out_now": have <= 0 and method == "usage", "confidence": conf,
            "on_shopping_list": on_list, "basis": basis, "category": food.category, "stock_covers_usual_buy": False}


def overview(db: Session, household_id: uuid.UUID, weeks: int = 12) -> dict:
    today, _ = _today(db, household_id)
    return {"as_of": today.isoformat(), "weeks": weeks, "spending": spending(db, household_id, weeks),
            "usage": usage_and_waste(db, household_id, weeks), "price_trends": price_trends(db, household_id),
            "forecast": forecasts(db, household_id)}
