"""Absence calendar: people away for meals change planning defaults and flag conflicts."""
from datetime import date, timedelta

from tests.conftest import people


def _recipe(client):
    r = client.post("/api/v1/recipes", json={"content": {"title": "Stew", "default_servings": 4,
                                                         "ingredients": [{"name": "Carrots", "quantity": 400, "unit": "g"}],
                                                         "steps": [{"text": "Simmer."}]}})
    return r.json()


def test_absence_defaults_and_conflicts(client):
    ppl = people(client)
    b = ppl["Adult B"]["id"]
    d = (date.today() + timedelta(days=2)).isoformat()
    recipe = _recipe(client)

    # A meal planned before anyone is away includes everyone: 2 adults + 2 toddlers (0.5 each) = 3 servings
    before = client.post("/api/v1/plan", json={"date": d, "slot": "dinner", "recipe_id": recipe["id"]}).json()
    assert len(before["participant_ids"]) == 4 and before["servings"] == 3 and before["away_conflicts"] == []

    # Adult B is out for dinner that evening (work); lunch isn't affected
    r = client.post("/api/v1/absences", json={"person_id": b, "start_date": d, "slots": ["dinner"], "reason": "work", "note": "Late shift"})
    assert r.status_code == 201, r.text
    assert [m["id"] for m in r.json()["affected_meals"]] == [before["id"]]

    plan = client.get(f"/api/v1/plan?from={d}&to={d}").json()
    meal = plan["meals"][0]
    assert meal["away_conflicts"][0]["person_name"] == "Adult B" and meal["away_conflicts"][0]["reason"] == "work"
    assert plan["absences"][0]["slots"] == ["dinner"]

    # Applying removes them and cuts servings by their usual portion; the shopping list follows
    applied = client.post(f"/api/v1/plan/{before['id']}/apply-absences").json()
    assert applied["removed"] == ["Adult B"] and applied["meal"]["servings"] == 2 and b not in applied["meal"]["participant_ids"]
    assert applied["meal"]["away_conflicts"] == []

    # New meals default to the people at home
    new = client.post("/api/v1/plan", json={"date": d, "slot": "dinner", "title": "Leftovers"}).json()
    assert b not in new["participant_ids"] and new["servings"] == 2
    lunch = client.post("/api/v1/plan", json={"date": d, "slot": "lunch", "title": "Soup"}).json()
    assert b in lunch["participant_ids"] and lunch["servings"] == 3


def test_multi_day_absence_and_validation(client):
    ppl = people(client)
    a = ppl["Adult A"]["id"]
    start = date.today() + timedelta(days=5)
    r = client.post("/api/v1/absences", json={"person_id": a, "start_date": start.isoformat(),
                                               "end_date": (start + timedelta(days=2)).isoformat(), "reason": "travel"})
    absence = r.json()["absence"]
    assert absence["all_day"] and absence["person_name"] == "Adult A"
    mid = (start + timedelta(days=1)).isoformat()
    meal = client.post("/api/v1/plan", json={"date": mid, "slot": "breakfast", "title": "Toast"}).json()
    assert a not in meal["participant_ids"]
    assert len(client.get(f"/api/v1/absences?from={start.isoformat()}").json()) == 1
    # validation
    assert client.post("/api/v1/absences", json={"person_id": a, "start_date": "2026-12-05", "end_date": "2026-12-01"}).status_code == 422
    assert client.post("/api/v1/absences", json={"person_id": a, "start_date": "2026-12-05", "slots": ["supper"]}).status_code == 422
    assert client.post("/api/v1/absences", json={"person_id": a, "start_date": "2026-12-05", "reason": "holiday"}).status_code == 422
    # edit and delete
    r = client.patch(f"/api/v1/absences/{absence['id']}", json={"person_id": a, "start_date": start.isoformat(), "slots": ["dinner"], "reason": "social"})
    assert r.json()["absence"]["slots"] == ["dinner"] and r.json()["absence"]["end_date"] == start.isoformat()
    assert client.delete(f"/api/v1/absences/{absence['id']}").status_code == 200
    assert client.get(f"/api/v1/absences?from={start.isoformat()}").json() == []


def test_today_shows_who_is_out(client):
    b = people(client)["Adult B"]["id"]
    client.post("/api/v1/absences", json={"person_id": b, "start_date": date.today().isoformat(), "slots": ["dinner"], "reason": "social"})
    away = client.get("/api/v1/today").json()["away"]
    assert away[0]["person_name"] == "Adult B" and away[0]["reason"] == "social"
