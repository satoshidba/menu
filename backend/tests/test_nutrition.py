"""Nutrition scaling and unknown values: unknown is never zero."""
import pytest

from app.services.nutrition import Contribution, NutritionAccumulator, clean_values, scale


def test_scale_keeps_unknown_as_none():
    v = {"energy_kcal": 343.0, "fibre_g": None}
    s = scale(v, 250)
    assert s["energy_kcal"] == pytest.approx(857.5)
    assert s["fibre_g"] is None


def test_partial_and_unknown_status():
    acc = NutritionAccumulator(["energy_kcal", "fibre_g", "salt_g"])
    acc.add(Contribution("pasta", {"energy_kcal": 343.0, "fibre_g": None, "salt_g": 0.01}))
    acc.add(Contribution("mystery sauce", None, reason="No nutrition data linked"))
    r = acc.result()["nutrients"]
    assert r["energy_kcal"]["status"] == "partial" and r["energy_kcal"]["value"] == 343.0
    assert "mystery sauce" in r["energy_kcal"]["missing_from"]
    assert r["fibre_g"]["status"] == "unknown" and r["fibre_g"]["value"] is None


def test_complete_and_per_serving():
    acc = NutritionAccumulator(["energy_kcal"])
    acc.add(Contribution("a", {"energy_kcal": 200.0}))
    acc.add(Contribution("b", {"energy_kcal": 600.0}))
    assert acc.result(divisor=4)["nutrients"]["energy_kcal"] == {**acc.result(divisor=4)["nutrients"]["energy_kcal"], "value": 200.0, "status": "complete"}


def test_derivations_only_from_known_units():
    v, flags = clean_values({"energy_kcal": 100, "sodium_mg": 400})
    assert v["energy_kj"] == pytest.approx(418.4) and flags["energy_kj"] == "derived"
    assert v["salt_g"] == pytest.approx(1.0) and flags["salt_g"] == "derived"
    v, _ = clean_values({"energy_kcal": 100})
    assert v["salt_g"] is None


def test_recipe_nutrition_scales_with_servings_and_flags_counts(client, cofid):
    foods = {}
    for name, code in (("Dried pasta", "11-716"), ("Onion", "13-499")):
        r = client.post("/api/v1/foods", json={"name": name, "cofid_code": code, "default_unit": "g" if name == "Dried pasta" else "each"})
        assert r.status_code == 201, r.text
        foods[name] = r.json()["id"]
    content = {"title": "Plain pasta", "default_servings": 4, "ingredients": [
        {"name": "Dried pasta", "quantity": 400, "unit": "g", "food_id": foods["Dried pasta"]},
        {"name": "Onion", "quantity": 1, "unit": "each", "food_id": foods["Onion"]},
    ], "steps": [{"text": "Cook."}]}
    r = client.post("/api/v1/recipes", json={"content": content})
    assert r.status_code == 201, r.text
    vid = r.json()["version"]["id"]
    n4 = client.get(f"/api/v1/nutrition/recipes/{vid}").json()
    n2 = client.get(f"/api/v1/nutrition/recipes/{vid}?servings=2").json()
    # CoFID 11-716 dried pasta: 343 kcal/100 g. 400 g -> 1372 kcal total, onion unknown (count without confirmed weight)
    assert n4["total"]["nutrients"]["energy_kcal"]["value"] == pytest.approx(1372)
    assert n4["total"]["nutrients"]["energy_kcal"]["status"] == "partial"
    assert "Onion" in n4["total"]["nutrients"]["energy_kcal"]["missing_from"]
    assert n4["per_serving"]["nutrients"]["energy_kcal"]["value"] == pytest.approx(343)
    assert n2["total"]["nutrients"]["energy_kcal"]["value"] == pytest.approx(686)
    # CoFID reports AOAC fibre for dried pasta as not determined -> unknown, never 0
    assert n4["total"]["nutrients"]["fibre_g"]["value"] is None or n4["total"]["nutrients"]["fibre_g"]["status"] != "complete"
    # confirm an onion weight -> onion becomes known
    client.post(f"/api/v1/foods/{foods['Onion']}/confirm-conversion", json={"unit": "each", "grams": 150})
    n = client.get(f"/api/v1/nutrition/recipes/{vid}").json()
    assert n["total"]["nutrients"]["energy_kcal"]["status"] == "complete"


def test_pet_food_has_no_human_nutrition(client):
    r = client.post("/api/v1/products", json={"name": "Dog kibble", "is_pet_food": True, "package_quantity": 2, "package_unit": "kg",
                                               "food_name": "Dog food"})
    assert r.status_code == 201
    pid = r.json()["id"]
    r = client.post("/api/v1/nutrition/records", json={"product_id": pid, "nutrients": {"energy_kcal": 350}})
    assert r.status_code == 422
    content = {"title": "Not for people", "ingredients": [{"name": "kibble", "quantity": 100, "unit": "g", "product_id": pid}], "steps": [{"text": "x"}]}
    assert client.post("/api/v1/recipes", json={"content": content}).status_code == 422
