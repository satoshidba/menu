"""End-to-end tests: a real MCP client talks to the Larder MCP server, which talks to a running Larder API.

Run against a seeded instance (see README "Developing"):
    LARDER_URL=http://127.0.0.1:8000 LARDER_TOKEN=lrd_… pytest
The token needs the agent scopes. Tests create and then clean up their own plan entries, absences and list items.
"""
import json
import os
import uuid
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.skipif(not (os.environ.get("LARDER_URL") and os.environ.get("LARDER_TOKEN")),
                                reason="needs LARDER_URL and LARDER_TOKEN for a running Larder")


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def call(client, name, /, **args):
    r = await client.call_tool(name, args)
    text = "".join(getattr(c, "text", "") for c in r.content)
    if r.is_error:
        raise AssertionError(f"{name} failed: {text}")
    return r.structured_content if r.structured_content is not None else json.loads(text)


@pytest.fixture
async def client():
    from mcp.client import Client

    from larder_mcp.server import mcp

    async with Client(mcp) as c:
        yield c


@pytest.mark.anyio
async def test_lists_tools_prompts_and_annotations(client):
    tools = {t.name: t for t in (await client.list_tools()).tools}
    assert {"get_today", "get_pantry", "plan_meal", "mark_away", "import_recipe", "apply_receipt", "record_cooked_meal"} <= set(tools)
    assert tools["get_pantry"].annotations.read_only_hint is True
    assert tools["remove_planned_meal"].annotations.destructive_hint is True
    prompts = {p.name for p in (await client.list_prompts()).prompts}
    assert {"plan_week", "use_it_up", "after_the_shop"} <= prompts
    assert "Never say a recipe is allergen-safe" in (client.instructions or "")
    res = await client.read_resource("larder://shopping-list")
    assert "groups" in json.loads(res.contents[0].text)


@pytest.mark.anyio
async def test_read_tools(client):
    today = await call(client, "get_today")
    assert "use_soon" in today and "ideas" in today
    pantry = await call(client, "get_pantry")
    assert pantry["count"] > 0 and pantry["items"][0]["lots"][0]["lot_id"]
    people = await call(client, "list_people")
    assert {p["kind"] for p in people["result"] if isinstance(people, dict) and "result" in people} or people
    recipes = await call(client, "find_recipes", cook_with_what_we_have=True)
    rows = recipes["result"] if isinstance(recipes, dict) else recipes
    first = rows[0]
    r = await call(client, "get_recipe", recipe=first["title"], servings=2)
    assert r["servings"] == 2 and r["ingredients"] and "check_before_serving" in r
    n = await call(client, "get_recipe_nutrition", recipe=first["recipe_id"])
    assert n["per_serving"]["energy_kcal"]["status"] in ("complete", "partial", "unknown")
    hist = await call(client, "get_price_history", item="Farfalle")
    assert hist["summary"]["standard_unit"] == "kg"


@pytest.mark.anyio
async def test_plan_absence_and_shopping_round_trip(client):
    d = (date.today() + timedelta(days=20)).isoformat()
    meal = await call(client, "plan_meal", date=d, meal="dinner", recipe="Red lentil and squash dal")
    try:
        away = await call(client, "mark_away", person="Sam", start_date=d, meals=["dinner"], reason="work", note="Late shift")
        assert [m["id"] for m in away["affected_planned_meals"]] == [meal["meal_id"]]
        week = await call(client, "get_week_plan", start_date=d, end_date=d)
        assert week["meals"][0]["away_conflicts"] == ["Sam (work)"]
        fixed = await call(client, "plan_without_absent", meal_id=meal["meal_id"])
        assert fixed["removed"] == ["Sam"] and fixed["servings"] == meal["servings"] - 1
        sl = await call(client, "get_shopping_list")
        assert any("Red lentil" in (i["why"] or "") for g in sl["groups"] for i in g["items"])
        await call(client, "remove_away", absence_id=away["absence_id"])
        weekly = await call(client, "mark_away", person="Alex", start_date=d, every_weekday=["Tue"], meals=["dinner"], reason="study")
        assert weekly["repeats"] == "every Tue" and weekly["to"] is None
        await call(client, "remove_away", absence_id=weekly["absence_id"])
    finally:
        await call(client, "remove_planned_meal", meal_id=meal["meal_id"])


@pytest.mark.anyio
async def test_pantry_add_and_use(client):
    name = f"Test carrots {uuid.uuid4().hex[:6]}"
    added = await call(client, "add_to_pantry", item=name, quantity=500, unit="g", location="fridge", bought=False,
                       confirm_duplicate=True)
    assert added["added"] == "500 g"
    used = await call(client, "use_from_pantry", item=name, quantity=0.2, unit="kg")
    assert used["shortfall"] is None and "200 g" in used["taken"][0]
    wasted = await call(client, "use_from_pantry", item=name, quantity=400, unit="g", wasted=True, reason="test")
    assert "below zero" in wasted["shortfall"]


@pytest.mark.anyio
async def test_receipt_needs_confirmation(client):
    pence = 100 + uuid.uuid4().int % 9000
    price = f"£{pence // 100}.{pence % 100:02d}"
    text = (f"Tesco Stores\nTEST LOOSE APPLES          {price}\nTOTAL                      {price}\nVISA {price}\n"
            f"01/02/2026 10:00 Receipt No {uuid.uuid4().hex[:10].upper()}")
    r = await call(client, "import_receipt_text", text=text)
    rid = r["receipt_id"]
    blocked = await client.call_tool("apply_receipt", {"receipt_id": rid, "confirm": False})
    assert blocked.is_error
    line = next(l for l in r["lines"] if l["kind"] == "item")
    r2 = await call(client, "match_receipt_line", receipt_id=rid, line_id=line["line_id"], not_for_pantry=True)
    assert r2["blocking"] == []
    done = await call(client, "apply_receipt", receipt_id=rid, confirm=True)
    assert done["status"] == "applied"
    again = await call(client, "apply_receipt", receipt_id=rid, confirm=True)
    assert again["status"] == "already_applied"


@pytest.mark.anyio
async def test_errors_are_readable(client):
    r = await client.call_tool("get_recipe", {"recipe": "definitely not a recipe zzz"})
    assert r.is_error and "No recipe matches" in r.content[0].text


@pytest.mark.anyio
async def test_medicines_and_vitamins(client):
    name = f"Test paracetamol {uuid.uuid4().hex[:5]}"
    item = await call(client, "add_cabinet_item", kind="medicine", name=name, pack_quantity=100, pack_unit="ml", dose_amount=5, dose_unit="ml",
                      label_min_interval_hours=4, label_max_doses_24h=4, packs_in_stock=1)
    assert item["dose"] == "5 ml"
    first = await call(client, "log_dose", person="Sam", item=name)
    assert first["recorded"] is True
    again = await call(client, "log_dose", person="Sam", item=name)
    assert again["recorded"] is False and again["needs_confirmation"] and "4 hours" in again["warnings"]
    forced = await call(client, "log_dose", person="Sam", item=name, acknowledge_warnings=True, notes="test")
    assert forced["recorded"] is True
    doses = await call(client, "list_doses", person="Sam")
    rows = doses["result"] if isinstance(doses, dict) else doses
    assert sum(1 for d in rows if d["item"] == name) == 2
    due = await call(client, "get_doses_due")
    assert "due" in due and "advice" in due
