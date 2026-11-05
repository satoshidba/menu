"""Stock-event arithmetic, lots, ledger invariants (acceptance test 2)."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def _product(client, barcode="5000000000017"):
    r = client.post("/api/v1/products", json={"name": "Farfalle", "brand": "Store brand", "food_name": "Dried pasta",
                                               "package_quantity": 500, "package_unit": "g", "barcode": barcode})
    assert r.status_code == 201, r.text
    return r.json()


def test_scan_known_two_lots_prices_and_earliest_date(client):
    prod = _product(client)
    scan = client.post("/api/v1/pantry/scan", json={"barcode": "5000000000017"}).json()
    assert scan["status"] == "known" and scan["product"]["id"] == prod["id"]
    # Scanning alone records nothing
    assert client.get("/api/v1/pantry").json()["count"] == 0
    a = client.post("/api/v1/pantry/lots", json={"product_id": prod["id"], "quantity": 1, "unit": "pack", "purchased_on": "2026-10-01",
                                                  "best_before": "2027-06-01", "store": "Tesco", "price_paid_pence": 95})
    b = client.post("/api/v1/pantry/lots", json={"product_id": prod["id"], "quantity": 2, "unit": "pack", "purchased_on": "2026-10-20",
                                                  "best_before": "2027-03-01", "store": "Lidl", "price_paid_pence": 150})
    assert a.status_code == 201 and b.status_code == 201, (a.text, b.text)
    items = client.get("/api/v1/pantry").json()["items"]
    assert len(items) == 1
    g = items[0]
    assert g["quantity_base"] == 1500 and g["display"] == "1.5 kg"
    assert g["earliest_date"] == "2027-03-01" and len(g["lots"]) == 2
    hist = client.get(f"/api/v1/prices/history?productId={prod['id']}").json()
    paid = [s for s in hist["series"] if s["kind"] == "paid"]
    assert sorted(s["price_pence"] for s in paid) == [95, 150]
    assert sorted(round(s["unit_price_pence"]) for s in paid) == [150, 190]  # £1.50/kg and £1.90/kg
    assert hist["summary"]["lowest"] == 150 and hist["summary"]["standard_unit"] == "kg"


def test_unknown_barcode_quick_add(client):
    scan = client.post("/api/v1/pantry/scan", json={"barcode": "9999999999994"}).json()
    assert scan["status"] == "unknown"
    r = client.post("/api/v1/pantry/lots", json={"new_product": {"name": "Oat drink", "package_quantity": 1, "package_unit": "l",
                                                                  "barcode": "9999999999994", "food_name": "Oat drink"},
                                                  "quantity": 1, "unit": "pack", "location": "fridge"})
    assert r.status_code == 201, r.text
    assert r.json()["lot"]["quantity_base"] == 1000 and r.json()["lot"]["base_unit"] == "ml"
    assert client.post("/api/v1/pantry/scan", json={"barcode": "9999999999994"}).json()["status"] == "known"


def test_loose_produce_and_event_arithmetic(client):
    r = client.post("/api/v1/pantry/lots", json={"food_name": "Spinach", "quantity": 250, "unit": "g", "location": "fridge",
                                                  "use_by": "2026-11-07"})
    lot = r.json()["lot"]
    ev = lambda **b: client.post("/api/v1/pantry/events", json={"lot_id": lot["id"], **b})  # noqa: E731
    assert ev(type="consumption", quantity=100, unit="g").json()["lot"]["quantity_base"] == 150
    assert ev(type="waste", quantity=0.05, unit="kg", reason="wilted").json()["lot"]["quantity_base"] == 100
    # negative stock refused without override
    r = ev(type="consumption", quantity=150, unit="g")
    assert r.status_code == 409
    # override needs a reason
    assert ev(type="consumption", quantity=150, unit="g", negative_override=True).status_code == 422
    r = ev(type="consumption", quantity=150, unit="g", negative_override=True, override_reason="Used more than recorded; will recount")
    assert r.status_code == 201 and r.json()["lot"]["quantity_base"] == -50
    # correction to a counted value keeps history
    r = ev(type="correction", new_quantity=40, unit="g", reason="Stocktake")
    assert r.json()["lot"]["quantity_base"] == 40 and r.json()["event"]["delta_base"] == 90
    r = ev(type="transfer", to_location="freezer")
    assert r.json()["lot"]["location"] == "freezer" and r.json()["lot"]["quantity_base"] == 40
    detail = client.get(f"/api/v1/pantry/lots/{lot['id']}").json()
    assert [e["type"] for e in detail["events"]] == ["purchase", "consumption", "waste", "consumption", "correction", "transfer"]
    assert sum(e["delta_base"] for e in detail["events"]) == pytest.approx(40)
    assert client.get("/api/v1/pantry/consistency").json()["mismatches"] == []


def test_idempotent_event(client):
    lot = client.post("/api/v1/pantry/lots", json={"food_name": "Rice", "quantity": 1000, "unit": "g"}).json()["lot"]
    body = {"lot_id": lot["id"], "type": "consumption", "quantity": 100, "unit": "g", "idempotency_key": "k1"}
    client.post("/api/v1/pantry/events", json=body)
    client.post("/api/v1/pantry/events", json=body)
    assert client.get(f"/api/v1/pantry/lots/{lot['id']}").json()["lot"]["quantity_base"] == 900
    # HTTP-level Idempotency-Key replays the stored response
    body2 = {"lot_id": lot["id"], "type": "consumption", "quantity": 50, "unit": "g"}
    r1 = client.post("/api/v1/pantry/events", json=body2, headers={"Idempotency-Key": "http-1"})
    r2 = client.post("/api/v1/pantry/events", json=body2, headers={"Idempotency-Key": "http-1"})
    assert r1.json() == r2.json() and r2.headers.get("idempotent-replay") == "true"
    assert client.get(f"/api/v1/pantry/lots/{lot['id']}").json()["lot"]["quantity_base"] == 850


def test_duplicate_lot_warning(client):
    body = {"food_name": "Butter", "quantity": 250, "unit": "g", "best_before": "2026-12-01"}
    assert client.post("/api/v1/pantry/lots", json=body).status_code == 201
    r = client.post("/api/v1/pantry/lots", json=body)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "possible_duplicate"
    assert client.post("/api/v1/pantry/lots", json={**body, "confirm_duplicate": True}).status_code == 201


def test_ledger_is_append_only(client, db):
    lot = client.post("/api/v1/pantry/lots", json={"food_name": "Flour", "quantity": 1, "unit": "kg"}).json()["lot"]
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE stock_event SET delta_base = 5 WHERE lot_id = :l"), {"l": lot["id"]})
        db.commit()
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(text("DELETE FROM stock_event WHERE lot_id = :l"), {"l": lot["id"]})
        db.commit()
    db.rollback()


def test_label_edit_conflict(client):
    lot = client.post("/api/v1/pantry/lots", json={"food_name": "Yogurt", "quantity": 500, "unit": "g"}).json()["lot"]
    assert client.patch(f"/api/v1/pantry/lots/{lot['id']}", json={"use_by": "2026-11-10", "row_version": lot["row_version"]}).status_code == 200
    r = client.patch(f"/api/v1/pantry/lots/{lot['id']}", json={"use_by": "2026-11-11", "row_version": lot["row_version"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "conflict"


def test_use_by_food_takes_earliest_first(client):
    a = client.post("/api/v1/pantry/lots", json={"food_name": "Milk", "quantity": 1, "unit": "l", "use_by": "2026-11-20"}).json()["lot"]
    b = client.post("/api/v1/pantry/lots", json={"food_name": "Milk", "quantity": 1, "unit": "l", "use_by": "2026-11-10"}).json()["lot"]
    r = client.post("/api/v1/pantry/use", json={"food_id": a["food_id"], "quantity": 1200, "unit": "ml"})
    assert r.status_code == 201, r.text
    assert [d["lot_id"] for d in r.json()["deducted"]] == [b["id"], a["id"]] and r.json()["shortfall_base"] == 0
    r = client.post("/api/v1/pantry/use", json={"food_id": a["food_id"], "quantity": 1, "unit": "l", "type": "waste", "reason": "sour"})
    assert r.json()["shortfall_base"] == 200 and "below zero" in r.json()["message"]
    assert client.get("/api/v1/pantry/consistency").json()["mismatches"] == []


def test_stocktake_by_location(client):
    a = client.post("/api/v1/pantry/lots", json={"food_name": "Yogurt", "quantity": 500, "unit": "g", "location": "fridge"}).json()["lot"]
    b = client.post("/api/v1/pantry/lots", json={"food_name": "Cheddar", "quantity": 250, "unit": "g", "location": "fridge"}).json()["lot"]
    c = client.post("/api/v1/pantry/lots", json={"food_name": "Butter", "quantity": 250, "unit": "g", "location": "fridge"}).json()["lot"]
    client.post("/api/v1/pantry/lots", json={"food_name": "Rice", "quantity": 1, "unit": "kg", "location": "cupboard"})
    assert {x["location"] for x in client.get("/api/v1/today").json()["stocktake_due"]} == {"fridge", "cupboard"}
    sheet = client.get("/api/v1/pantry/stocktake?location=fridge").json()
    assert {x["name"] for x in sheet["lots"]} == {"Yogurt", "Cheddar", "Butter"}
    body = {"location": "fridge", "counts": [{"lot_id": a["id"], "counted_quantity": 300}, {"lot_id": b["id"], "counted_quantity": 250}],
            "not_found": [c["id"]], "idempotency_key": "st-1"}
    r = client.post("/api/v1/pantry/stocktake", json=body).json()
    assert len(r["changed"]) == 1 and r["unchanged"] == 1 and r["not_found"] == [c["id"]]
    # replay is harmless
    client.post("/api/v1/pantry/stocktake", json=body)
    pantry = {g["name"]: g for g in client.get("/api/v1/pantry?location=fridge").json()["items"]}
    assert pantry["Yogurt"]["quantity_base"] == 300 and "Butter" not in pantry
    events = client.get(f"/api/v1/pantry/lots/{a['id']}").json()["events"]
    assert [e["type"] for e in events] == ["purchase", "correction"] and events[-1]["reason"].startswith("Stocktake (fridge")
    assert [x["location"] for x in client.get("/api/v1/today").json()["stocktake_due"]] == ["cupboard"]
    assert client.get("/api/v1/pantry/stocktake?location=fridge").json()["last_counted"]["fridge"]
    assert client.get("/api/v1/pantry/consistency").json()["mismatches"] == []


def test_merge_duplicate_foods(client):
    keep = client.post("/api/v1/pantry/lots", json={"food_name": "Spinach", "quantity": 200, "unit": "g"}).json()["lot"]
    dup = client.post("/api/v1/pantry/lots", json={"food_name": "Baby spinach", "quantity": 100, "unit": "g"}).json()["lot"]
    recipe = client.post("/api/v1/recipes", json={"content": {"title": "Salad", "ingredients": [{"name": "Baby spinach", "quantity": 150, "unit": "g"}],
                                                              "steps": [{"text": "Toss."}]}}).json()
    dups = client.get("/api/v1/foods/duplicates").json()
    pair = next(d for d in dups if {d["keep"]["name"], d["merge"]["name"]} == {"Spinach", "Baby spinach"})
    src, dst = (pair["merge"]["id"], pair["keep"]["id"])
    if pair["keep"]["name"] != "Spinach":
        src, dst = dst, src
    r = client.post(f"/api/v1/foods/{src}/merge", json={"into_food_id": dst})
    assert r.status_code == 200, r.text
    assert "Baby spinach" in r.json()["kept"]["aliases"] and r.json()["moved"]["pantry_lot"] == 1
    # one pantry line now, recipe (published, immutable) still reads "Baby spinach" but is linked to Spinach and in stock
    pantry = client.get("/api/v1/pantry").json()["items"]
    assert [g["name"] for g in pantry] == ["Spinach"] and pantry[0]["quantity_base"] == 300
    v = client.get(f"/api/v1/recipes/{recipe['id']}").json()["version"]
    assert v["ingredients"][0]["name"] == "Baby spinach" and v["ingredients"][0]["food_id"] == dst
    review = client.get(f"/api/v1/recipes/{recipe['id']}/versions/{v['id']}/review").json()
    assert review["requirements"][0]["status"] == "ok"
    # future recipes with the alias link straight to the kept food
    assert client.get(f"/api/v1/foods/{src}").status_code == 404
    _ = keep, dup
