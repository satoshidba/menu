"""Shopping list from plans minus usable stock (acceptance 4) and cooking/intake (acceptance 5)."""
import pytest

from tests.conftest import people


def _pasta_recipe(client, cofid=True):
    food = client.post("/api/v1/foods", json={"name": "Dried pasta", "cofid_code": "11-716" if cofid else None, "category": "Pasta, rice & grains"}).json()
    spinach = client.post("/api/v1/foods", json={"name": "Spinach", "cofid_code": "13-572" if cofid else None}).json()
    content = {"title": "Pasta with spinach", "default_servings": 4,
               "ingredients": [{"name": "Dried pasta", "quantity": 500, "unit": "g", "food_id": food["id"]},
                               {"name": "Spinach", "quantity": 100, "unit": "g", "food_id": spinach["id"]},
                               {"name": "Mystery sauce", "quantity": 2, "unit": "tbsp"}],
               "steps": [{"text": "Boil pasta."}, {"text": "Stir in spinach."}]}
    r = client.post("/api/v1/recipes", json={"content": content})
    assert r.status_code == 201, r.text
    return r.json(), food, spinach


def _list_qty(client, name):
    for g in client.get("/api/v1/shopping-list").json()["groups"]:
        for i in g["items"]:
            if i["name"] == name and i["origin"] == "plan":
                return i["quantity"], i["unit"], i["reason"]
    return None


def test_shopping_list_subtracts_usable_stock(client, cofid):
    recipe, pasta, _ = _pasta_recipe(client)
    lot = client.post("/api/v1/pantry/lots", json={"food_id": pasta["id"], "quantity": 200, "unit": "g"}).json()["lot"]
    plan = client.post("/api/v1/plan", json={"date": "2026-11-06", "recipe_id": recipe["id"], "servings": 4}).json()
    req = {r["name"]: r for r in plan["requirements"]}
    assert req["Dried pasta"]["need_base"] == 500 and req["Dried pasta"]["missing_base"] == 300
    from datetime import date
    if date.today() > date(2026, 11, 6):
        pytest.skip("plan date in the past")
    qty, unit, reason = _list_qty(client, "Dried pasta")
    assert (qty, unit) == (300, "g") and "200 g usable" in reason
    # Adjust planned servings -> requirement updates (6 servings = 750 g - 200 g = 550 g)
    client.patch(f"/api/v1/plan/{plan['id']}", json={"servings": 6})
    assert _list_qty(client, "Dried pasta")[:2] == (550, "g")
    # Adjust the pantry -> requirement updates
    client.post("/api/v1/pantry/events", json={"lot_id": lot["id"], "type": "add", "quantity": 300, "unit": "g"})
    assert _list_qty(client, "Dried pasta")[:2] == (250, "g")
    # Stock whose use-by is before the meal does not count
    client.post("/api/v1/pantry/lots", json={"food_id": pasta["id"], "quantity": 1000, "unit": "g", "use_by": "2026-11-01"})
    assert _list_qty(client, "Dried pasta")[:2] == (250, "g")
    # Enough stock -> item disappears
    client.post("/api/v1/pantry/lots", json={"food_id": pasta["id"], "quantity": 1000, "unit": "g"})
    assert _list_qty(client, "Dried pasta") is None
    # User choices survive regeneration
    client.patch(f"/api/v1/plan/{plan['id']}", json={"servings": 40})
    item = next(i for g in client.get("/api/v1/shopping-list").json()["groups"] for i in g["items"] if i["name"] == "Dried pasta")
    client.patch(f"/api/v1/shopping-list/items/{item['id']}", json={"quantity": 2, "unit": "kg"})
    assert _list_qty(client, "Dried pasta")[:2] == (2, "kg")


def test_cook_batch_portions_leftovers_and_intake(client, cofid):
    recipe, pasta, spinach = _pasta_recipe(client)
    client.post("/api/v1/pantry/lots", json={"food_id": pasta["id"], "quantity": 1000, "unit": "g", "best_before": "2027-01-01"})
    sp = client.post("/api/v1/pantry/lots", json={"food_id": spinach["id"], "quantity": 150, "unit": "g", "use_by": "2026-11-08"}).json()["lot"]
    ppl = people(client)
    vid = recipe["version"]["id"]
    body = {"recipe_version_id": vid, "servings_made": 4, "meal_slot": "dinner", "cooked_at": "2026-11-05T18:30:00+00:00",
            "portions": [{"person_id": ppl["Adult A"]["id"], "servings": 1.5}, {"person_id": ppl["Adult B"]["id"], "servings": 1},
                         {"person_id": ppl["Toddler One"]["id"], "servings": 0.25}, {"person_id": ppl["Toddler Two"]["id"], "servings": 0.5}],
            "leftover_servings": 0.5, "waste_servings": 0.25, "idempotency_key": "cook-1"}
    r = client.post("/api/v1/meals/cook", json=body)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["unaccounted_servings"] == 0
    used = {u["name"]: u for u in out["batch"]["ingredients_used"]}
    assert used["Mystery sauce"]["shortfall"] == "Not in the pantry"
    # Pantry deductions
    pantry = {g["name"]: g for g in client.get("/api/v1/pantry").json()["items"]}
    assert pantry["Dried pasta"]["quantity_base"] == 500
    assert pantry["Spinach"]["quantity_base"] == 50
    assert pantry["Leftovers: Pasta with spinach"]["quantity_base"] == 0.5
    assert client.get(f"/api/v1/pantry/lots/{sp['id']}").json()["events"][-1]["cooked_batch_id"] == out["batch"]["id"]
    # Replaying the same cook does nothing
    assert client.post("/api/v1/meals/cook", json=body).json()["replayed"] is True
    assert {g["name"]: g for g in client.get("/api/v1/pantry").json()["items"]}["Dried pasta"]["quantity_base"] == 500

    # Per-person intake reflects different portions; unknown nutrients stay unknown
    per = out["batch"]["nutrition_per_serving"]["nutrients"]
    kcal_per = per["energy_kcal"]["value"]
    # 500 g pasta (343 kcal/100g) + 100 g spinach (CoFID 13-572) over 4 servings; the sauce is unknown -> partial
    assert per["energy_kcal"]["status"] == "partial" and "Mystery sauce" in per["energy_kcal"]["missing_from"]
    cons = {c["person_id"]: c for c in out["consumptions"]}
    a, b = cons[ppl["Adult A"]["id"]], cons[ppl["Adult B"]["id"]]
    t1 = cons[ppl["Toddler One"]["id"]]
    assert a["nutrition"]["energy_kcal"] == pytest.approx(kcal_per * 1.5, rel=1e-3)
    assert b["nutrition"]["energy_kcal"] == pytest.approx(kcal_per, rel=1e-3)
    assert t1["nutrition"]["energy_kcal"] == pytest.approx(kcal_per * 0.25, rel=1e-3)
    assert a["provenance"]["status"]["energy_kcal"] == "partial" and a["provenance"]["complete"] is False
    assert a["nutrition"]["fibre_g"] is None or a["provenance"]["status"]["fibre_g"] != "complete"

    intake = client.get(f"/api/v1/people/{ppl['Adult A']['id']}/intake?from=2026-11-05&to=2026-11-05").json()
    assert intake["days"][0]["nutrients"]["energy_kcal"]["value"] == pytest.approx(kcal_per * 1.5, rel=1e-3)
    assert intake["days"][0]["nutrients"]["energy_kcal"]["status"] == "partial"

    # Eating the leftovers later uses the batch snapshot and the leftover lot
    r = client.post("/api/v1/meals/consumption", json={"person_id": ppl["Adult B"]["id"], "cooked_batch_id": out["batch"]["id"],
                                                        "amount": 0.5, "amount_unit": "serving", "deduct_from_stock": True,
                                                        "consumed_at": "2026-11-06T12:00:00+00:00"})
    assert r.json()["nutrition"]["energy_kcal"] == pytest.approx(kcal_per * 0.5, rel=1e-3)
    assert "Leftovers: Pasta with spinach" not in {g["name"] for g in client.get("/api/v1/pantry").json()["items"]}

    # Historical snapshot is preserved when the recipe later changes
    content = client.get(f"/api/v1/recipes/{recipe['id']}/versions/{vid}/content").json()
    content["ingredients"][0]["quantity"] = 1000
    client.post(f"/api/v1/recipes/{recipe['id']}/versions", json={"content": content, "label": "Double pasta"})
    again = client.get(f"/api/v1/people/{ppl['Adult A']['id']}/intake?from=2026-11-05&to=2026-11-05").json()
    assert again["entries"][0]["nutrition"]["energy_kcal"] == a["nutrition"]["energy_kcal"]


def test_portions_cannot_exceed_batch(client):
    recipe, _, _ = _pasta_recipe(client, cofid=False)
    adult = people(client)["Adult A"]["id"]
    r = client.post("/api/v1/meals/cook", json={"recipe_version_id": recipe["version"]["id"], "servings_made": 2,
                                                 "portions": [{"person_id": adult, "servings": 2}], "leftover_servings": 1})
    assert r.status_code == 422


def test_pet_food_never_counts_as_intake(client):
    r = client.post("/api/v1/products", json={"name": "Kibble", "is_pet_food": True, "food_name": "Dog food", "package_quantity": 2, "package_unit": "kg"})
    adult = people(client)["Adult A"]["id"]
    resp = client.post("/api/v1/meals/consumption", json={"person_id": adult, "product_id": r.json()["id"], "amount": 50, "amount_unit": "g"})
    assert resp.status_code == 422
    # Pet stock is kept separately from the human pantry view
    client.post("/api/v1/pantry/lots", json={"product_id": r.json()["id"], "quantity": 1, "unit": "pack", "location": "other"})
    assert client.get("/api/v1/pantry").json()["count"] == 0
    assert client.get("/api/v1/pantry?pet=true").json()["count"] == 1
