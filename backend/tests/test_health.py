"""Vitamins, protein supplements and medicines: cabinet, label checks, regimens, intake, scopes."""
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import people


def _vitamin_d(client):
    r = client.post("/api/v1/health/items", json={
        "kind": "supplement", "name": "Vitamin D drops", "brand": "Store brand", "pack_quantity": 10, "pack_unit": "ml",
        "dose_amount": 1, "dose_unit": "drop", "nutrition_per_dose": {"vitamin_d_ug": 10}, "stock_quantity": 1, "expiry": "2027-06-30"})
    assert r.status_code == 201, r.text
    return r.json()


def _paracetamol(client, max24=4, interval=4):
    r = client.post("/api/v1/health/items", json={
        "kind": "medicine", "name": "Infant paracetamol suspension", "pack_quantity": 100, "pack_unit": "ml", "dose_amount": 5, "dose_unit": "ml",
        "label_min_interval_hours": interval, "label_max_doses_24h": max24, "active_ingredients": "Paracetamol 120 mg/5 ml",
        "label_directions": "As entered from the bottle", "stock_quantity": 1, "expiry": "2026-12-31", "min_stock_base": 40})
    assert r.status_code == 201, r.text
    return r.json()


def _protein(client):
    r = client.post("/api/v1/health/items", json={
        "kind": "supplement", "name": "Whey protein", "pack_quantity": 1, "pack_unit": "kg", "dose_amount": 30, "dose_unit": "g",
        "nutrition_per_dose": {"protein_g": 24, "energy_kcal": 120, "sugars_g": 1.5, "salt_g": 0.2}, "stock_quantity": 1})
    assert r.status_code == 201, r.text
    return r.json()


def test_cabinet_is_separate_from_food(client):
    vd, para = _vitamin_d(client), _paracetamol(client)
    assert vd["stock"] == "10 ml" and vd["nutrition_per_dose"] == {"vitamin_d_ug": 10.0}
    assert para["label_max_doses_24h"] == 4 and para["nutrition_per_dose"] is None
    cab = client.get("/api/v1/health/cabinet").json()["items"]
    assert {i["name"] for i in cab} == {"Vitamin D drops", "Infant paracetamol suspension"}
    # not in the food pantry, not a recipe ingredient, not loggable as food
    assert client.get("/api/v1/pantry").json()["count"] == 0
    content = {"title": "x", "ingredients": [{"name": "p", "quantity": 5, "unit": "ml", "product_id": para["product_id"]}], "steps": [{"text": "x"}]}
    assert client.post("/api/v1/recipes", json={"content": content}).status_code == 422
    adult = people(client)["Adult A"]["id"]
    assert client.post("/api/v1/meals/consumption", json={"person_id": adult, "product_id": para["product_id"], "amount": 5, "amount_unit": "ml"}).status_code == 422
    # medicines can't get nutrition values
    assert client.post("/api/v1/nutrition/records", json={"product_id": para["product_id"], "nutrients": {"energy_kcal": 1}}).status_code == 422


def test_medicine_dose_checks_against_entered_directions(client):
    para = _paracetamol(client, max24=2, interval=4)
    toddler = people(client)["Toddler One"]["id"]
    t0 = datetime.now(timezone.utc) - timedelta(hours=10)
    give = lambda at, ack=False: client.post("/api/v1/health/doses", json={"person_id": toddler, "product_id": para["product_id"],  # noqa: E731
                                                                           "given_at": at.isoformat(), "acknowledge_warnings": ack})
    r = give(t0)
    assert r.status_code == 201, r.text
    assert r.json()["dose"]["amount"] == 5 and r.json()["dose"]["unit"] == "ml" and r.json()["nutrition_counted"] is None
    # too soon: 409 with the reason, nothing recorded
    r = give(t0 + timedelta(hours=2))
    assert r.status_code == 409 and r.json()["detail"]["warnings"][0]["code"] == "interval"
    assert "4 hours" in r.json()["detail"]["warnings"][0]["message"]
    # fine after the interval
    assert give(t0 + timedelta(hours=5)).status_code == 201
    # a third within 24 h exceeds the entered maximum of 2
    r = give(t0 + timedelta(hours=9, minutes=30))
    assert r.status_code == 409 and {w["code"] for w in r.json()["detail"]["warnings"]} == {"max_24h"}
    # recorded only with explicit acknowledgement, which is kept on the record
    r = give(t0 + timedelta(hours=9, minutes=30), ack=True)
    assert r.status_code == 201 and r.json()["dose"]["warnings_acknowledged"][0]["code"] == "max_24h"
    doses = client.get(f"/api/v1/health/doses?person_id={toddler}").json()["doses"]
    assert len(doses) == 3
    # stock came out of the bottle: 3 × 5 ml from 100 ml
    item = next(i for i in client.get("/api/v1/health/cabinet").json()["items"] if i["kind"] == "medicine")
    assert item["stock_base"] == 85
    check = client.get(f"/api/v1/health/doses/check?person_id={toddler}&product_id={para['product_id']}").json()
    assert check["next_allowed_at"] is not None and "pharmacist" in check["advice"]


def test_supplements_count_towards_intake(client):
    _vitamin_d(client)
    whey = _protein(client)
    adult = people(client)["Adult A"]["id"]
    r = client.post("/api/v1/health/doses", json={"person_id": adult, "product_id": whey["product_id"], "amount": 1.5, "unit": "dose"})
    assert r.status_code == 201, r.text
    assert r.json()["nutrition_counted"]["nutrition"]["protein_g"] == pytest.approx(36)
    # 45 g taken from the 1 kg tub
    tub = next(i for i in client.get("/api/v1/health/cabinet").json()["items"] if i["name"] == "Whey protein")
    assert tub["stock_base"] == 955
    day = datetime.now(timezone.utc).date().isoformat()
    intake = client.get(f"/api/v1/people/{adult}/intake?from={day}&to={day}").json()
    entry = intake["entries"][0]
    assert entry["meal_slot"] == "supplement" and entry["provenance"]["source"] == "supplement"
    assert entry["nutrition"]["protein_g"] == pytest.approx(36) and entry["nutrition"]["fibre_g"] is None  # unknown, not zero
    # deleting the dose removes it from intake too
    client.delete(f"/api/v1/health/doses/{r.json()['dose']['dose_id']}")
    assert client.get(f"/api/v1/people/{adult}/intake?from={day}&to={day}").json()["entries"] == []


def test_regimens_due_today_and_low_stock(client):
    vd, para = _vitamin_d(client), _paracetamol(client)
    ppl = people(client)
    mia = ppl["Toddler One"]["id"]
    reg = client.post("/api/v1/health/regimens", json={"person_id": mia, "product_id": vd["product_id"], "times_per_day": 1,
                                                       "source": "Health visitor"}).json()
    client.post("/api/v1/health/regimens", json={"person_id": mia, "product_id": para["product_id"], "as_needed": True})
    due = client.get("/api/v1/health/today").json()["due"]
    vd_due = next(d for d in due if d["item"].endswith("Vitamin D drops"))
    assert vd_due["remaining_today"] == 1 and vd_due["dose"] == "1 drop"
    from app.services.health import qty_unit
    assert qty_unit(5, "drop") == "5 drops" and qty_unit(2, "sachet") == "2 sachets" and qty_unit(5, "ml") == "5 ml"
    assert next(d for d in due if "paracetamol" in d["item"])["remaining_today"] == 0  # as needed: never "due"
    assert client.get("/api/v1/today").json()["health"]["due"][0]["regimen_id"] == reg["regimen_id"]
    r = client.post("/api/v1/health/doses", json={"person_id": mia, "regimen_id": reg["regimen_id"]})
    assert r.status_code == 201
    assert client.get("/api/v1/today").json()["health"]["due"] == []
    # paracetamol has a 40 ml minimum: give doses until it is low and it appears on the shopping list
    item = client.patch(f"/api/v1/health/items/{para['product_id']}", json={"min_stock_base": 100}).json()
    assert item["low_stock"] is True
    names = [i["name"] for g in client.get("/api/v1/shopping-list").json()["groups"] for i in g["items"]]
    assert "Infant paracetamol suspension" in names
    # stopping a regimen keeps it on record
    stopped = client.delete(f"/api/v1/health/regimens/{reg['regimen_id']}").json()
    assert stopped["active"] is False
    assert all(r["regimen_id"] != reg["regimen_id"] for r in client.get("/api/v1/health/regimens").json())


def test_health_scopes(client, anon):
    para = _paracetamol(client)
    ppl = people(client)
    child, adult = ppl["Toddler One"]["id"], ppl["Adult A"]["id"]
    client.post("/api/v1/health/doses", json={"person_id": child, "product_id": para["product_id"]})
    intake_only = client.post("/api/v1/tokens", json={"name": "a", "scopes": ["intake:read"], "person_ids": [adult, child]}).json()["token"]
    assert anon.get("/api/v1/health/cabinet", headers={"Authorization": f"Bearer {intake_only}"}).status_code == 403
    h = {"Authorization": "Bearer " + client.post("/api/v1/tokens", json={"name": "b", "scopes": ["health:read"], "person_ids": [adult, child]}).json()["token"]}
    assert anon.get("/api/v1/health/cabinet", headers=h).status_code == 200
    assert anon.get(f"/api/v1/health/doses?person_id={child}", headers=h).status_code == 404  # child needs explicit scope
    assert anon.post("/api/v1/health/doses", headers=h, json={"person_id": adult, "product_id": para["product_id"]}).status_code == 403
    h2 = {"Authorization": "Bearer " + client.post("/api/v1/tokens", json={"name": "c", "scopes": ["health:read", "intake:read:children"],
                                                                         "person_ids": [child]}).json()["token"]}
    assert len(anon.get(f"/api/v1/health/doses?person_id={child}", headers=h2).json()["doses"]) == 1
