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


def _next_weekday(wd: int, after: date) -> date:
    d = after + timedelta(days=1)
    while d.weekday() != wd:
        d += timedelta(days=1)
    return d


def test_weekly_recurring_absence(client):
    a = people(client)["Adult A"]["id"]
    today = date.today()
    tue = _next_weekday(1, today)
    r = client.post("/api/v1/absences", json={"person_id": a, "start_date": today.isoformat(), "weekdays": [1], "slots": ["dinner"],
                                               "reason": "study", "note": "Evening class"})
    assert r.status_code == 201, r.text
    ab = r.json()["absence"]
    assert ab["end_date"] is None and ab["repeats"] == "every Tue"
    week = client.get(f"/api/v1/plan?from={tue.isoformat()}&to={(tue + timedelta(days=6)).isoformat()}").json()
    assert list(week["away_by_date"]) == [tue.isoformat()]
    # Tuesday dinner excludes Adult A; Wednesday dinner and Tuesday lunch don't
    tue_dinner = client.post("/api/v1/plan", json={"date": tue.isoformat(), "slot": "dinner", "title": "Soup"}).json()
    wed_dinner = client.post("/api/v1/plan", json={"date": (tue + timedelta(days=1)).isoformat(), "slot": "dinner", "title": "Soup"}).json()
    tue_lunch = client.post("/api/v1/plan", json={"date": tue.isoformat(), "slot": "lunch", "title": "Soup"}).json()
    assert a not in tue_dinner["participant_ids"] and a in wed_dinner["participant_ids"] and a in tue_lunch["participant_ids"]
    # also applies weeks later
    later = tue + timedelta(days=21)
    assert client.get(f"/api/v1/plan?from={later.isoformat()}&to={later.isoformat()}").json()["away_by_date"][later.isoformat()][0]["note"] == "Evening class"
    assert client.post("/api/v1/absences", json={"person_id": a, "start_date": today.isoformat(), "weekdays": [7]}).status_code == 422


def test_calendar_feed(client, anon):
    ppl = people(client)
    today = date.today()
    client.post("/api/v1/plan", json={"date": (today + timedelta(days=1)).isoformat(), "slot": "dinner", "title": "Fish pie, with peas; and mash"})
    client.post("/api/v1/absences", json={"person_id": ppl["Adult B"]["id"], "start_date": today.isoformat(), "weekdays": [0, 3],
                                          "slots": ["dinner"], "reason": "work"})
    client.post("/api/v1/absences", json={"person_id": ppl["Adult A"]["id"], "start_date": (today + timedelta(days=3)).isoformat(),
                                          "end_date": (today + timedelta(days=4)).isoformat(), "reason": "travel", "note": "Leeds"})
    # wrong scope refused
    other = client.post("/api/v1/tokens", json={"name": "x", "scopes": ["plan:read"]}).json()["token"]
    assert anon.get(f"/api/v1/calendar.ics?token={other}").status_code == 401
    tok = client.post("/api/v1/tokens", json={"name": "Calendar", "scopes": ["calendar:read"]}).json()
    r = anon.get(f"/api/v1/calendar.ics?token={tok['token']}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    ics = r.text
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert "SUMMARY:Dinner: Fish pie\\, with peas\; and mash" in ics
    assert "RRULE:FREQ=WEEKLY;BYDAY=MO,TH" in ics and "Adult B out (work): dinner" in ics
    assert "Adult A out (away): all day — Leeds" in ics and "DTSTART;VALUE=DATE:" in ics
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))
    # the feed token can't do anything else
    assert anon.get("/api/v1/plan", headers={"Authorization": f"Bearer {tok['token']}"}).status_code == 403
    client.delete(f"/api/v1/tokens/{tok['id']}")
    assert anon.get(f"/api/v1/calendar.ics?token={tok['token']}").status_code == 401


def test_tokens_redacted_from_logs():
    import logging

    from app.main import RedactTokens
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s"', ("1.2.3.4", "GET", "/api/v1/calendar.ics?token=lrd_ab12_cdEF-gh"), None)
    RedactTokens().filter(rec)
    assert "lrd_ab12" not in rec.getMessage() and "lrd_[redacted]" in rec.getMessage()
