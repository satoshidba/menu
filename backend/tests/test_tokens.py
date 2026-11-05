"""Scoped API tokens and the intake export (acceptance test 6)."""
import csv
import io

from tests.conftest import people


def _token(client, scopes, person_ids, name="Activity app"):
    r = client.post("/api/v1/tokens", json={"name": name, "scopes": scopes, "person_ids": person_ids})
    assert r.status_code == 201, r.text
    return r.json()


def _log(client, person_id, kcal_food):
    r = client.post("/api/v1/meals/consumption", json={"person_id": person_id, "food_id": kcal_food, "amount": 150, "amount_unit": "g",
                                                        "consumed_at": "2026-11-03T12:30:00+00:00", "meal_slot": "lunch"})
    assert r.status_code == 201, r.text
    return r.json()


def _food(client):
    f = client.post("/api/v1/foods", json={"name": "Porridge oats"}).json()
    client.post("/api/v1/nutrition/records", json={"food_id": f["id"], "source": "label", "verified": True,
                                                   "nutrients": {"energy_kcal": 375, "protein_g": 11, "fat_g": 8, "salt_g": None}})
    return f["id"]


def test_read_only_token_exports_one_adult(client, anon):
    ppl = people(client)
    adult, other, child = ppl["Adult A"]["id"], ppl["Adult B"]["id"], ppl["Toddler One"]["id"]
    food = _food(client)
    entry = _log(client, adult, food)
    _log(client, other, food)
    _log(client, child, food)
    tok = _token(client, ["intake:read"], [adult])
    h = {"Authorization": f"Bearer {tok['token']}"}

    r = anon.get(f"/api/v1/people/{adult}/intake/export?format=json&from=2026-11-01&to=2026-11-05", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["timezone"] == "Europe/London" and body["units"]["energy_kcal"] == "kcal"
    rec = body["records"][0]
    assert rec["external_id"] == entry["external_id"] and rec["external_id"].startswith("lrd-mc-")
    assert rec["energy_kcal"] == 562.5 and rec["nutrients"]["salt_g"]["value"] is None  # unknown stays unknown
    assert rec["nutrients"]["protein_g"]["unit"] == "g" and rec["consumed_at"].startswith("2026-11-03T12:30")
    assert rec["source"] == "item" and "confidence" in rec

    c = anon.get(f"/api/v1/people/{adult}/intake/export?format=csv&from=2026-11-01&to=2026-11-05", headers=h)
    assert c.status_code == 200 and c.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(c.text)))
    assert rows[0]["external_id"] == entry["external_id"] and rows[0]["energy_kcal"] == "562.5" and rows[0]["salt_g"] == ""
    assert rows[0]["salt_g_status"] == "unknown"

    # Stable IDs across exports
    again = anon.get(f"/api/v1/people/{adult}/intake/export?format=json&from=2026-11-01&to=2026-11-05", headers=h).json()
    assert again["records"][0]["external_id"] == rec["external_id"]

    # Cannot read the other adult or a toddler, cannot write, cannot read recipes or pantry
    assert anon.get(f"/api/v1/people/{other}/intake/export", headers=h).status_code == 404
    assert anon.get(f"/api/v1/people/{child}/intake", headers=h).status_code == 404
    assert anon.post("/api/v1/meals/consumption", headers=h, json={"person_id": adult, "food_id": food, "amount": 1, "amount_unit": "g"}).status_code == 403
    assert anon.get("/api/v1/pantry", headers=h).status_code == 403
    assert anon.get("/api/v1/recipes", headers=h).status_code == 403
    assert anon.post("/api/v1/tokens", headers=h, json={"name": "x", "scopes": ["intake:read"]}).status_code == 403
    # Only the permitted profile is visible
    assert [p["id"] for p in anon.get("/api/v1/people", headers=h).json()] == [adult]


def test_child_intake_needs_explicit_scope(client, anon):
    child = people(client)["Toddler One"]["id"]
    food = _food(client)
    _log(client, child, food)
    t1 = _token(client, ["intake:read"], [child], "no child scope")
    assert anon.get(f"/api/v1/people/{child}/intake", headers={"Authorization": f"Bearer {t1['token']}"}).status_code == 404
    t2 = _token(client, ["intake:read", "intake:read:children"], [child], "with child scope")
    assert anon.get(f"/api/v1/people/{child}/intake", headers={"Authorization": f"Bearer {t2['token']}"}).status_code == 200


def test_incremental_sync_and_tombstones(client, anon):
    adult = people(client)["Adult A"]["id"]
    food = _food(client)
    e1 = _log(client, adult, food)
    tok = _token(client, ["intake:read"], [adult])
    h = {"Authorization": f"Bearer {tok['token']}"}
    first = anon.get(f"/api/v1/people/{adult}/intake/export?updated_since=2000-01-01T00:00:00Z", headers=h).json()
    assert len(first["records"]) == 1
    cursor = first["cursor"]
    assert anon.get(f"/api/v1/people/{adult}/intake/export", params={"updated_since": cursor}, headers=h).json()["records"] == []
    client.delete(f"/api/v1/meals/consumption/{e1['id']}")
    changes = anon.get(f"/api/v1/people/{adult}/intake/export", params={"updated_since": cursor}, headers=h).json()["records"]
    assert len(changes) == 1 and changes[0]["deleted"] is True and changes[0]["external_id"] == e1["external_id"]


def test_revoke_and_rotate(client, anon):
    adult = people(client)["Adult A"]["id"]
    tok = _token(client, ["intake:read"], [adult])
    h = {"Authorization": f"Bearer {tok['token']}"}
    assert anon.get(f"/api/v1/people/{adult}/intake", headers=h).status_code == 200
    new = client.post(f"/api/v1/tokens/{tok['id']}/rotate").json()
    assert anon.get(f"/api/v1/people/{adult}/intake", headers=h).status_code == 401
    h2 = {"Authorization": f"Bearer {new['token']}"}
    assert anon.get(f"/api/v1/people/{adult}/intake", headers=h2).status_code == 200
    client.delete(f"/api/v1/tokens/{new['id']}")
    assert anon.get(f"/api/v1/people/{adult}/intake", headers=h2).status_code == 401
    # tokens are never listed with their secret
    assert all("token" not in t for t in client.get("/api/v1/tokens").json())


def test_session_csrf_and_member_limits(client, anon):
    # cookie session without CSRF header is refused for mutations
    del client.headers["x-csrf-token"]
    assert client.post("/api/v1/people", json={"name": "X"}).status_code == 403
    # the second adult (member) cannot mint tokens
    from tests.conftest import login
    b = login(anon, "b@example.com")
    assert b.post("/api/v1/tokens", json={"name": "x", "scopes": ["intake:read"]}).status_code == 403
    assert b.get("/api/v1/pantry").status_code == 200
    assert anon.post("/api/v1/auth/login", json={"email": "b@example.com", "password": "wrong password"}).status_code == 401
