"""Insights: spending, usage and waste, price trends and run-out forecasts from recorded history."""
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from app.models import Food, Household, Product
from app.services.insights import forecasts
from app.services.prices import record_purchase
from app.services.stock import EventInput, create_lot, record_event


def _at(days_ago: int) -> datetime:
    return datetime.combine(date.today() - timedelta(days=days_ago), time(12), timezone.utc)


def _setup(client, db):
    hid = db.scalar(select(Household.id))
    milk = Food(household_id=hid, name="Milk", default_unit="ml", category="Dairy")
    bread = Food(household_id=hid, name="Bread", default_unit="each", category="Bakery")
    saffron = Food(household_id=hid, name="Saffron", default_unit="g")
    quinoa = Food(household_id=hid, name="Quinoa", default_unit="g")
    pasta = Food(household_id=hid, name="Dried pasta", default_unit="g")
    db.add_all([milk, bread, saffron, quinoa, pasta])
    db.flush()
    return hid, milk, bread, saffron, quinoa, pasta


def _buy(db, hid, food, qty, unit, days_ago, pence, store="Tesco"):
    lot = create_lot(db, hid, product=None, food=food, quantity=qty, unit=unit, location="fridge", price_paid_pence=pence,
                     purchased_on=date.today() - timedelta(days=days_ago), occurred_at=_at(days_ago), store=store)
    record_purchase(db, hid, food_id=food.id, store=store, retailer=store, purchased_at=_at(days_ago), quantity_base=lot.initial_quantity_base,
                    base_unit=lot.base_unit, paid_price_pence=pence)
    return lot


def _milk_history(db, hid, milk):
    """4 L every fortnight; 500 ml used every other day; the last 500 ml of the first bottle went off."""
    a = _buy(db, hid, milk, 4000, "ml", 28, 200)
    for d in range(27, 14, -2):
        record_event(db, a.id, hid, EventInput(type="consumption", quantity=500, unit="ml", occurred_at=_at(d)))
    record_event(db, a.id, hid, EventInput(type="waste", quantity=500, unit="ml", reason="Went off", occurred_at=_at(15)))
    b = _buy(db, hid, milk, 4000, "ml", 14, 200)
    for d in range(13, 0, -2):
        record_event(db, b.id, hid, EventInput(type="consumption", quantity=500, unit="ml", occurred_at=_at(d)))
    db.commit()


def test_usage_forecast_waste_and_spending(client, db):
    hid, milk, *_ = _setup(client, db)
    _milk_history(db, hid, milk)
    data = client.get("/api/v1/insights?weeks=8").json()

    # spending: two 4 L purchases at £2.00 each, both within the window
    sp = data["spending"]
    assert sp["total_pence"] == 400 and sp["by_store"][0] == {"store": "Tesco", "pence": 400}
    assert sum(w["food"] for w in sp["weeks"]) == 400 and len(sp["weeks"]) == 8

    # usage and waste: 7 L used, 500 ml thrown away, valued at the price paid per ml
    u = data["usage"]
    assert u["used_value_pence"] == 350 and u["wasted_value_pence"] == 25
    assert u["waste_share"] == round(25 / 375, 3)
    assert u["top_wasted"][0]["name"] == "Milk" and u["top_wasted"][0]["quantity_display"] == "500 ml"
    assert u["top_used"][0]["events"] == 14

    # forecast from usage: about 268 ml a day with 500 ml left, so it runs out in about 2 days
    fc = {f["name"]: f for f in data["forecast"]["forecasts"]}["Milk"]
    assert fc["method"] == "usage" and fc["stock_display"] == "500 ml"
    assert 1 <= fc["days_left"] <= 3
    assert fc["earliest"] <= fc["run_out"] <= fc["latest"]
    assert fc["confidence"] == "medium"  # only 4 weeks of history
    assert "a week" in fc["basis"] and "in 4 weeks" in fc["basis"]

    # it reaches the shopping list with its basis, and a dismissal sticks
    items = [i for g in client.get("/api/v1/shopping-list").json()["groups"] for i in g["items"]]
    milk_item = next(i for i in items if i["name"] == "Milk")
    assert milk_item["origin"] == "forecast" and "medium confidence" in milk_item["reason"]
    client.delete(f"/api/v1/shopping-list/items/{milk_item['id']}")
    items = [i for g in client.get("/api/v1/shopping-list").json()["groups"] for i in g["items"]]
    assert not any(i["name"] == "Milk" for i in items)


def test_not_enough_history_means_no_forecast(client, db):
    hid, milk, *_ = _setup(client, db)
    lot = _buy(db, hid, milk, 2000, "ml", 5, 120)
    for d in (4, 2):
        record_event(db, lot.id, hid, EventInput(type="consumption", quantity=500, unit="ml", occurred_at=_at(d)))
    db.commit()
    out = forecasts(db, hid)
    assert out["forecasts"] == [] and out["learning"] == ["Milk"]


def test_purchase_rhythm_forecast(client, db):
    hid, _, bread, saffron, quinoa, _ = _setup(client, db)
    for d in (28, 21, 14, 7):  # weekly loaf, never logged as used
        record_purchase(db, hid, food_id=bread.id, purchased_at=_at(d), quantity_base=1, base_unit="each", paid_price_pence=140)
    record_purchase(db, hid, food_id=saffron.id, purchased_at=_at(3), quantity_base=1, base_unit="g", paid_price_pence=300)
    for d in (170, 160, 150):  # stopped buying it
        record_purchase(db, hid, food_id=quinoa.id, purchased_at=_at(d), quantity_base=500, base_unit="g", paid_price_pence=250)
    db.commit()
    out = forecasts(db, hid)
    names = [f["name"] for f in out["forecasts"]]
    assert names == ["Bread"]
    fc = out["forecasts"][0]
    assert fc["method"] == "purchases" and fc["run_out"] == date.today().isoformat()
    assert "usually every 7 days" in fc["basis"] and fc["confidence"] == "low"  # only 4 purchases
    assert "Saffron" in out["learning"]
    # bread isn't in stock, so it goes on the list
    items = [i for g in client.get("/api/v1/shopping-list").json()["groups"] for i in g["items"]]
    assert any(i["name"] == "Bread" and i["origin"] == "forecast" for i in items)


def test_purchase_rhythm_ignored_when_stocked(client, db):
    hid, _, bread, *_ = _setup(client, db)
    for d in (28, 21, 14, 7):
        record_purchase(db, hid, food_id=bread.id, purchased_at=_at(d), quantity_base=1, base_unit="each", paid_price_pence=140)
    create_lot(db, hid, product=None, food=bread, quantity=2, unit="each", location="freezer")
    db.commit()
    assert forecasts(db, hid)["forecasts"][0]["stock_covers_usual_buy"] is True
    items = [i for g in client.get("/api/v1/shopping-list").json()["groups"] for i in g["items"]]
    assert not any(i["name"] == "Bread" for i in items)


def test_price_trends_per_unit_and_store(client, db):
    hid, *_, pasta = _setup(client, db)
    for d, store, paid in ((70, "Tesco", 95), (45, "Sainsbury's", 80), (25, "Lidl", 75)):
        record_purchase(db, hid, food_id=pasta.id, store=store, retailer=store, purchased_at=_at(d), quantity_base=500,
                        base_unit="g", paid_price_pence=paid)
    vit = Product(household_id=hid, name="Vitamin D drops", kind="supplement", base_unit="ml")
    db.add(vit)
    db.flush()
    for d in (60, 10):
        record_purchase(db, hid, product_id=vit.id, purchased_at=_at(d), quantity_base=10, base_unit="ml", paid_price_pence=500 + d)
    db.commit()
    trends = client.get("/api/v1/insights/prices").json()
    assert [t["name"] for t in trends] == ["Dried pasta"]  # health products are left out
    t = trends[0]
    assert t["standard_unit"] == "kg" and t["latest"] == 150.0 and t["earlier_median"] == 175.0
    assert t["change_pct"] == round((150 - 175) / 175 * 100, 1)
    assert t["lowest"]["store"] == "Lidl" and [s["store"] for s in t["stores"]] == ["Lidl", "Sainsbury's", "Tesco"]
    # spending puts the vitamins in their own category
    sp = client.get("/api/v1/insights?weeks=12").json()["spending"]
    assert sum(w["health"] for w in sp["weeks"]) == 560 + 510 and sum(w["food"] for w in sp["weeks"]) == 80 + 75 + 95


def test_insights_need_pantry_scope(client, anon):
    tok = client.post("/api/v1/tokens", json={"name": "x", "scopes": ["plan:read"]}).json()["token"]
    assert anon.get("/api/v1/insights", headers={"Authorization": f"Bearer {tok}"}).status_code == 403
    tok = client.post("/api/v1/tokens", json={"name": "y", "scopes": ["pantry:read"]}).json()["token"]
    r = anon.get("/api/v1/insights/forecasts", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["forecasts"] == []
