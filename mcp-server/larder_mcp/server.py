"""Larder MCP server.

Transports:
  stdio            `larder-mcp` with LARDER_URL and LARDER_TOKEN set (Claude Desktop / Claude Code on your computer)
  streamable HTTP  `larder-mcp --transport http` behind Caddy at https://<site>/mcp; each client sends its own
                   `Authorization: Bearer lrd_…` token, which is forwarded to the Larder API.

The server is a thin, stateless client of the Larder REST API. It never sees more than the token allows.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import uuid
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from .client import LarderClient, verify_setting

INSTRUCTIONS = """\
Larder is a UK household's food app: recipes, pantry stock (lots with use-by/best-before dates), meal plans,
who's out for which meals, the shopping list, receipts and per-person nutrition. Two adults, two toddlers, one dog.

How to behave:
- Use UK English, metric units (g, kg, ml, L, tsp, tbsp) and £. Dates are ISO (YYYY-MM-DD) in tool calls.
- Read before you write: check the pantry, plan and people before suggesting or changing things.
- Imports and AI changes produce DRAFTS. Show the user what will be saved (and any unresolved amounts) and get their
  go-ahead before save_recipe, accept_recipe_change, apply_receipt or anything that removes data.
- Never say a recipe is allergen-safe or safe for someone. You may point out *possible* allergens and say to check
  the labels. Don't give medical, weight-loss or calorie-target advice, especially for the children.
- Use-by and best-before are label wording. Don't decide whether food is safe to eat.
- Pet food is separate inventory and never counts as human food or nutrition. Don't give pet feeding advice.
- Nutrition values are estimates unless from verified labels. "partial" means a lower bound; unknown is not zero.
- When someone is out (see get_week_plan absences), plan for the people at home.
Items, recipes and people can usually be given by name; ids are returned by the read tools.
"""

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
REMOVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)
FETCH = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)

mcp = MCPServer(name="larder", title="Larder", version="1.0.0", instructions=INSTRUCTIONS,
                description="Household recipes, pantry, meal planning, shopping, receipts and nutrition")

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
MEALS = Literal["breakfast", "lunch", "dinner", "snack"]


# ---- plumbing ----------------------------------------------------------------------------------------------------------

def client(ctx: Context | None) -> LarderClient:
    """HTTP transport: use the caller's bearer token. stdio: use LARDER_TOKEN from the environment."""
    token = None
    headers = None
    if ctx is not None:
        try:
            headers = ctx.headers
        except (ValueError, AttributeError):
            headers = None
    if headers:
        auth = headers.get("authorization") or headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        base = os.environ.get("LARDER_API_URL") or os.environ.get("LARDER_URL", "http://web:8000")
    else:
        base = os.environ.get("LARDER_URL") or os.environ.get("LARDER_API_URL", "http://127.0.0.1:8000")
    token = token or os.environ.get("LARDER_TOKEN", "")
    return LarderClient(base, token, verify=verify_setting())


def is_id(s: str | None) -> bool:
    return bool(s and UUID_RE.match(s.strip()))


async def resolve_person(c: LarderClient, who: str) -> dict:
    people = await c.get("/people")
    for p in people:
        if who == p["id"] or who.strip().lower() == p["name"].lower():
            return p
    matches = [p for p in people if who.strip().lower() in p["name"].lower()]
    if len(matches) == 1:
        return matches[0]
    raise ToolError(f"No single person matches '{who}'. People: {', '.join(p['name'] for p in people) or 'none visible to this token'}")


async def resolve_recipe(c: LarderClient, recipe: str) -> dict:
    if is_id(recipe):
        return await c.get(f"/recipes/{recipe}")
    found = await c.get("/recipes", q=recipe, limit=10)
    exact = [r for r in found if r["title"].lower() == recipe.strip().lower()]
    pick = exact[0] if exact else (found[0] if len(found) == 1 else None)
    if pick is None:
        if not found:
            raise ToolError(f"No recipe matches '{recipe}'.")
        raise ToolError(f"'{recipe}' matches several recipes: " + "; ".join(f"{r['title']} (id {r['id']})" for r in found[:8]))
    return await c.get(f"/recipes/{pick['id']}")


async def resolve_item(c: LarderClient, item: str) -> dict:
    """Food or product by id or name -> {'food_id'|'product_id': id, 'name': ...}."""
    if is_id(item):
        try:
            p = await c.get(f"/products/{item}")
            return {"product_id": p["id"], "name": p["name"]}
        except ToolError:
            f = await c.get(f"/foods/{item}")
            return {"food_id": f["id"], "name": f["name"]}
    pantry = await c.get("/pantry", q=item)
    names = [(g, g["name"].lower()) for g in pantry["items"]]
    exact = [g for g, n in names if n == item.strip().lower()] or [g for g, n in names if item.strip().lower() in n]
    if len(exact) >= 1:
        g = exact[0]
        if g.get("product"):
            return {"product_id": g["product"]["id"], "name": g["name"]}
        return {"food_id": g["food"]["id"], "name": g["name"]}
    foods = await c.get("/foods", q=item, limit=5)
    exact_f = [f for f in foods if f["name"].lower() == item.strip().lower()]
    if exact_f or len(foods) == 1:
        f = (exact_f or foods)[0]
        return {"food_id": f["id"], "name": f["name"]}
    raise ToolError(f"Couldn't find '{item}'. Candidates: {', '.join(f['name'] for f in foods) or 'none'}")


async def wait_job(c: LarderClient, job: dict, timeout: float = 110.0) -> dict:
    waited, delay = 0.0, 0.8
    while job["status"] not in ("succeeded", "failed") and waited < timeout:
        await asyncio.sleep(delay)
        waited += delay
        delay = min(delay * 1.4, 3.0)
        job = await c.get(f"/jobs/{job['id']}")
    if job["status"] == "failed":
        raise ToolError(job.get("error") or "The import failed")
    return job


def compact_group(g: dict) -> dict:
    return {
        "name": g["name"], "brand": g.get("brand"), "amount": g["display"], "quantity_base": round(g["quantity_base"], 2),
        "base_unit": g["base_unit"], "locations": g["locations"], "earliest_date": g["earliest_date"],
        "earliest_date_kind": g["earliest_date_kind"], "days_to_date": g["days_to_date"], "low_stock": g["low_stock"],
        "food_id": (g.get("food") or {}).get("id"), "product_id": (g.get("product") or {}).get("id"),
        "lots": [{"lot_id": l["id"], "amount": l["display"], "location": l["location"], "use_by": l["use_by"],
                  "best_before": l["best_before"], "label": l["date_label"], "opened": l["opened"]} for l in g["lots"]],
    }


def compact_recipe(r: dict, review: dict | None = None) -> dict:
    v = r["version"]
    out = {
        "recipe_id": r["id"], "title": v["title"], "version_id": v["id"], "version": v["label"],
        "versions": [{"id": x["id"], "label": x["label"], "status": x["status"]} for x in r["versions"]],
        "servings": v["servings"], "default_servings": v["default_servings"], "prep_minutes": v["prep_minutes"],
        "cook_minutes": v["cook_minutes"], "tags": v["tags"], "description": v["description"],
        "ingredients": [{"amount": i["display"] if not i["unresolved"] else None, "name": i["name"], "preparation": i["preparation"],
                         "optional": i["optional"], "unresolved": i["unresolved"], "proposed": i["proposed"], "food_id": i["food_id"]}
                        for i in v["ingredients"]],
        "steps": [{"n": s["position"] + 1, "text": s["text"], "timer_minutes": round(s["timer_seconds"] / 60) if s["timer_seconds"] else None}
                  for s in v["steps"]],
        "possible_allergens": v["allergens"], "source": {"url": v["source_url"], "name": v["source_name"], "author": v["source_author"]},
        "unresolved_fields": v["unresolved_fields"],
    }
    if review:
        out["stock"] = [{"name": x["name"], "status": x["status"], "need": x.get("need_display"), "missing": x.get("missing_display")}
                        for x in review["requirements"]]
        f = review["flags"]
        out["check_before_serving"] = {
            "possible_allergens": [a["allergen"] for a in f["possible_allergens"]],
            "people": [x["message"] for x in f["person_conflicts"]],
            "toddlers": [x["message"] for x in f["toddler"]],
            "note": f["disclaimer"],
        }
    return out


def nutrition_brief(n: dict) -> dict:
    return {k: {"value": None if d["value"] is None else round(d["value"], 1), "unit": d["unit"], "status": d["status"]}
            for k, d in n["nutrients"].items() if k in ("energy_kcal", "protein_g", "carbohydrate_g", "sugars_g", "fat_g",
                                                         "saturates_g", "fibre_g", "salt_g")}


# ---- read tools ----------------------------------------------------------------------------------------------------------

@mcp.tool(annotations=READ)
async def get_today(ctx: Context) -> dict:
    """Today's overview: next planned meal (with missing ingredients), items to use soon, low stock, who's out today or
    tomorrow, and recipes that use what's in stock."""
    c = client(ctx)
    t = await c.get("/today")
    nm = t["next_meal"]
    return {
        "date": t["date"],
        "next_meal": nm and {"id": nm["id"], "date": nm["date"], "meal": nm["slot"], "title": nm["title"], "servings": nm["servings"],
                             "recipe_id": nm["recipe_id"], "missing": [f"{x['name']} ({x.get('missing_display') or x.get('need_display') or '?'})"
                                                                       for x in nm.get("requirements", []) if x["status"] in ("missing", "short")]},
        "use_soon": t["use_soon"], "low_stock": t["low_stock"], "away": t.get("away", []),
        "ideas": [{"recipe_id": r["id"], "title": r["title"], "reasons": r["match"]["reasons"], "missing": r["match"]["missing"]} for r in t["ideas"]],
    }


@mcp.tool(annotations=READ)
async def find_recipes(ctx: Context, query: str | None = None, cook_with_what_we_have: bool = False,
                       max_minutes: int | None = None, tag: str | None = None, limit: int = 20) -> list[dict]:
    """Search the household's recipes. With cook_with_what_we_have=true, results are ranked by how many ingredients are
    in stock and whether they use items that need using soon, with reasons and missing items."""
    c = client(ctx)
    rows = await c.get("/recipes", q=query, cook_with_what_we_have=str(cook_with_what_we_have).lower(), max_minutes=max_minutes, tag=tag, limit=limit)
    return [{"recipe_id": r["id"], "title": r["title"], "minutes": r["total_minutes"], "serves": r["default_servings"], "tags": r["tags"],
             "version": r["version_label"], "possible_allergens": r["allergens"], "have": f"{r['match']['have']}/{r['match']['needed']}",
             "missing": r["match"]["missing"], "uses_soon": r["match"]["use_soon"]} for r in rows]


@mcp.tool(annotations=READ)
async def get_recipe(ctx: Context, recipe: Annotated[str, Field(description="Recipe id or title")],
                     servings: Annotated[float | None, Field(description="Scale amounts for this many servings (view only)")] = None,
                     version_id: str | None = None) -> dict:
    """A recipe with scaled ingredients, method, what's in stock or missing, and things to check before serving
    (possible allergens, household allergies/dislikes/preferences, toddler preparation notes). Scaling never changes
    the saved recipe."""
    c = client(ctx)
    r = await resolve_recipe(c, recipe)
    if servings or version_id:
        r = await c.get(f"/recipes/{r['id']}", servings=servings, version_id=version_id)
    v = r["version"]
    review = await c.get(f"/recipes/{r['id']}/versions/{v['id']}/review", servings=servings or v["default_servings"])
    return compact_recipe(r, review)


@mcp.tool(annotations=READ)
async def get_recipe_nutrition(ctx: Context, recipe: Annotated[str, Field(description="Recipe id or title")],
                               servings: float | None = None) -> dict:
    """Estimated nutrition for a recipe: per serving and total. Status 'partial' means some ingredients have no data,
    so the value is a lower bound; 'unknown' is not zero."""
    c = client(ctx)
    r = await resolve_recipe(c, recipe)
    n = await c.get(f"/nutrition/recipes/{r['version']['id']}", servings=servings)
    missing = sorted({m for d in n["per_serving"]["nutrients"].values() for m in d["missing_from"]})
    return {"title": r["title"], "servings": n["servings"], "per_serving": nutrition_brief(n["per_serving"]),
            "total": nutrition_brief(n["total"]), "ingredients_without_data": missing, "note": n["basis_note"]}


@mcp.tool(annotations=READ)
async def get_pantry(ctx: Context, location: Literal["cupboard", "fridge", "freezer", "other"] | None = None,
                     search: str | None = None, pet_food: bool = False) -> dict:
    """What's in stock, grouped by item, earliest relevant date first. Each item lists its lots (with lot_id, location
    and use-by/best-before). pet_food=true shows the separate pet-food inventory."""
    c = client(ctx)
    p = await c.get("/pantry", location=location, q=search, pet=str(pet_food).lower())
    return {"count": p["count"], "items": [compact_group(g) for g in p["items"]]}


@mcp.tool(annotations=READ)
async def get_use_soon(ctx: Context, days: int = 5) -> list[dict]:
    """Lots whose use-by or best-before date falls within the next `days` days (label wording; not a safety judgement)."""
    c = client(ctx)
    rows = await c.get("/pantry/expiring", days=days)
    return [{"lot_id": r["id"], "name": r["name"], "amount": r["display"], "location": r["location"], "date": r["relevant_date"],
             "date_kind": r["relevant_date_kind"], "days_left": r["days_left"]} for r in rows]


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
async def lookup_barcode(ctx: Context, barcode: str, pet_food: bool = False) -> dict:
    """Look up a barcode: a product the household already has, or a draft from Open Food Facts (community data, may be
    wrong; confirm with the user). Nothing is added to the pantry."""
    c = client(ctx)
    return await c.post("/pantry/scan", {"barcode": barcode, "pet": pet_food})


@mcp.tool(annotations=READ)
async def get_week_plan(ctx: Context, start_date: Annotated[str | None, Field(description="YYYY-MM-DD; defaults to this Monday")] = None,
                        end_date: str | None = None) -> dict:
    """Planned meals and absences (who's out for which meals) for a date range. Meals list `away_conflicts` when someone
    planned to eat is marked out."""
    c = client(ctx)
    p = await c.get("/plan", **{"from": start_date, "to": end_date})
    people = {x["id"]: x["name"] for x in await c.get("/people")}
    return {
        "from": p["from"], "to": p["to"],
        "meals": [{"meal_id": m["id"], "date": m["date"], "meal": m["slot"], "title": m["title"], "servings": m["servings"],
                   "recipe_id": m["recipe_id"], "status": m["status"], "eating": [people.get(x, x) for x in m["participant_ids"]],
                   "away_conflicts": [f"{a['person_name']} ({a['reason']})" for a in m["away_conflicts"]]} for m in p["meals"]],
        "absences": [{"absence_id": a["id"], "person": a["person_name"], "from": a["start_date"], "to": a["end_date"],
                      "meals": "all day" if a["all_day"] else a["slots"], "reason": a["reason"], "note": a["note"]} for a in p["absences"]],
    }


@mcp.tool(annotations=READ)
async def get_shopping_list(ctx: Context, group_by: Literal["aisle", "shop"] = "aisle") -> dict:
    """The shopping list: planned meals' needs minus usable pantry stock (refreshed on read), low-stock reminders and
    manual items, each with the reason it's there."""
    c = client(ctx)
    s = await c.get("/shopping-list", group_by=group_by)
    return {"groups": [{"group": g["name"], "items": [{"item_id": i["id"], "name": i["product_name"] or i["name"], "amount": i["display"],
                                                        "why": i["reason"], "got": i["checked"], "origin": i["origin"], "shop": i["shop"]}
                                                       for i in g["items"]]} for g in s["groups"]],
            "not_included": (s.get("generated") or {}).get("unresolved", [])}


@mcp.tool(annotations=READ)
async def get_price_history(ctx: Context, item: Annotated[str, Field(description="Product or food name or id")]) -> dict:
    """Prices the household actually paid for an item: usually paid, lowest, highest and last, per kg / L / item, with
    shop and pack size kept separate. External offers are listed with whether they are current."""
    c = client(ctx)
    it = await resolve_item(c, item)
    h = await c.get("/prices/history", productId=it.get("product_id"), foodId=it.get("food_id"))
    return {"item": it["name"], "summary": h["summary"], "by_shop_and_pack": h["groups"], "recent": h["series"][-10:], "offers": h["offers"]}


@mcp.tool(annotations=READ)
async def list_people(ctx: Context) -> list[dict]:
    """Household members (adults and children) with allergies, dislikes, dietary preferences and usual portion."""
    c = client(ctx)
    return [{"person_id": p["id"], "name": p["name"], "kind": p["kind"], "allergies": p["allergies"], "dislikes": p["dislikes"],
             "preferences": p["dietary_preferences"], "usual_portion": p["default_portion"]} for p in await c.get("/people")]


@mcp.tool(annotations=READ)
async def get_intake(ctx: Context, person: Annotated[str, Field(description="Name or id")], from_date: str | None = None,
                     to_date: str | None = None) -> dict:
    """What a person ate, per day, with estimated nutrients (status 'partial' = lower bound, 'unknown' = no data).
    Needs the intake:read scope for that person (and intake:read:children for a child)."""
    c = client(ctx)
    p = await resolve_person(c, person)
    d = await c.get(f"/people/{p['id']}/intake", **{"from": from_date, "to": to_date})
    return {"person": d["person"], "from": d["from"], "to": d["to"],
            "days": [{"date": x["date"], "entries": x["entries"], **{k: v for k, v in x["nutrients"].items()
                                                                     if k in ("energy_kcal", "protein_g", "carbohydrate_g", "sugars_g", "fat_g", "saturates_g", "fibre_g", "salt_g")}}
                     for x in d["days"]],
            "entries": [{"id": e["id"], "when": e["consumed_at"], "what": e["description"], "amount": f"{e['amount']} {e['amount_unit']}",
                         "kcal": e["nutrition"].get("energy_kcal")} for e in d["entries"]], "note": d["note"]}


@mcp.tool(annotations=READ)
async def search_foods(ctx: Context, query: str) -> dict:
    """Find generic foods and branded products by name (ids for other tools)."""
    c = client(ctx)
    foods = await c.get("/foods", q=query, limit=10)
    products = await c.get("/products", q=query, limit=10)
    return {"foods": [{"food_id": f["id"], "name": f["name"], "unit": f["default_unit"], "category": f["category"]} for f in foods],
            "products": [{"product_id": p["id"], "name": p["name"], "brand": p["brand"], "pack": f"{p['package_quantity'] or ''} {p['package_unit'] or ''}".strip(),
                          "food": p["food_name"], "pet_food": p["is_pet_food"]} for p in products]}


@mcp.tool(annotations=READ)
async def list_receipts(ctx: Context) -> list[dict]:
    """Uploaded receipts with their status (review = waiting for the user; applied = added to the pantry)."""
    c = client(ctx)
    return [{"receipt_id": r["id"], "shop": r["retailer"], "date": r["purchased_at"], "total_pence": r["total_pence"], "status": r["status"],
             "lines": r["line_count"], "possible_duplicate_of": r["duplicate_of_id"]} for r in await c.get("/receipts")]


@mcp.tool(annotations=READ)
async def get_receipt(ctx: Context, receipt_id: str) -> dict:
    """A receipt's review: each line with the shop's wording, price paid after line discounts, match status and
    suggested product, the reconciliation against the printed total, and what still blocks applying it."""
    c = client(ctx)
    r = await c.get(f"/receipts/{receipt_id}/review")
    return {"receipt_id": r["id"], "status": r["status"], "shop": r["retailer"], "date": r["purchased_at"], "warnings": r["warnings"],
            "duplicate_of": r.get("duplicate_of"), "reconciliation": r["reconciliation"], "blocking": r["blocking"],
            "basket_adjustments": r["basket_adjustments"],
            "lines": [{"line_id": l["id"], "kind": l["kind"], "text": l["raw_text"], "description": l["description"], "qty": l["quantity"],
                       "weight_kg": l["weight_kg"], "line_pence": l["line_total_pence"], "paid_pence": l["paid_pence"],
                       "promotions": l["promotions"], "match": l["match_status"],
                       "product": (l["product"] or {}).get("name"), "food": (l["food"] or {}).get("name"), "goes_to": l["location"]}
                      for l in r["lines"] if l["kind"] != "ignore"]}


# ---- recipes: drafts, versions ---------------------------------------------------------------------------------------------

class IngredientDraft(BaseModel):
    name: str
    quantity: float | None = Field(None, description="Leave null if the source gives no amount; never guess")
    unit: str | None = Field(None, description="g, kg, ml, l, tsp, tbsp, cup, each, clove, tin, pinch, …")
    preparation: str | None = None
    optional: bool = False
    original_text: str | None = Field(None, description="The line exactly as in the source")
    proposed: bool = Field(False, description="True if this item or amount is your suggestion, not from a source")


class StepDraft(BaseModel):
    text: str
    timer_minutes: float | None = None
    proposed: bool = False


class RecipeDraft(BaseModel):
    title: str
    servings: float = 4
    description: str | None = None
    tags: list[str] = []
    prep_minutes: int | None = None
    cook_minutes: int | None = None
    ingredients: list[IngredientDraft]
    steps: list[StepDraft]
    notes: str | None = None
    source_url: str | None = None
    source_name: str | None = None
    source_author: str | None = None
    image_staging_paths: list[str] = Field([], description="From import_recipe's images; the first becomes the dish photo")


def _draft_from_import(result: dict) -> dict:
    d = result["draft"]
    return {
        "title": d.get("title"), "servings": d.get("default_servings"), "description": d.get("description"), "tags": d.get("tags", []),
        "prep_minutes": d.get("prep_minutes"), "cook_minutes": d.get("cook_minutes"), "source_url": d.get("source_url"),
        "source_name": d.get("source_name"), "source_author": d.get("source_author"),
        "ingredients": [{"name": i["name"], "quantity": i["quantity"], "unit": i["unit"], "preparation": i.get("preparation"),
                         "optional": i.get("optional", False), "original_text": i.get("original_text"), "proposed": i.get("proposed", False),
                         **({"needs_weight_confirmation": True} if i.get("needs_confirmation") else {})} for i in d.get("ingredients", [])],
        "steps": [{"text": s["text"], "timer_minutes": round(s["timer_seconds"] / 60) if s.get("timer_seconds") else None,
                   "proposed": s.get("proposed", False)} for s in d.get("steps", [])],
        "image_staging_paths": [im["staging_path"] for im in sorted(result.get("images", []), key=lambda i: i["role"] != "hero")],
    }


@mcp.tool(annotations=FETCH)
async def import_recipe(ctx: Context, url: str | None = None, text: str | None = None) -> dict:
    """Read a recipe from a web page (structured data first; AI only fills gaps, if configured) or pasted text and
    return a DRAFT. Nothing is saved. Show the draft to the user, especially `unresolved` and `missing_amounts`, then
    call save_recipe with the (possibly edited) draft."""
    if not (url or text):
        raise ToolError("Give a url or some recipe text")
    c = client(ctx)
    job = await c.post("/recipes/import", {"url": url} if url else {"text": text})
    job = await wait_job(c, job)
    res = job["result"]
    return {"draft": _draft_from_import(res), "unresolved": res["draft"].get("unresolved_fields", []),
            "missing_amounts": res.get("missing_amounts", []), "read_by": res["method"], "ai_used": res["ai_used"],
            "warnings": res["warnings"], "next": "Review with the user, then save_recipe(draft)."}


@mcp.tool(annotations=FETCH)
async def suggest_recipe(ctx: Context, request: Annotated[str, Field(description="What the household fancies")],
                         use_items: Annotated[list[str], Field(description="Pantry foods to use (names)")] = []) -> dict:
    """Ask Larder's AI for an original recipe idea using what's in stock (needs an AI provider on the server).
    Everything comes back marked proposed. Returns a DRAFT; nothing is saved."""
    c = client(ctx)
    ids = []
    for name in use_items:
        it = await resolve_item(c, name)
        if it.get("food_id"):
            ids.append(it["food_id"])
    job = await wait_job(c, await c.post("/recipes/vibe", {"prompt": request, "food_ids": ids}))
    res = job["result"]
    return {"draft": _draft_from_import(res), "warnings": res["warnings"], "next": "Review with the user, then save_recipe(draft)."}


@mcp.tool(annotations=WRITE)
async def save_recipe(ctx: Context, draft: RecipeDraft) -> dict:
    """Save a reviewed recipe as version 1 ("Original"). Ingredients are linked to pantry foods by name. Only call this
    after the user has seen the draft."""
    c = client(ctx)
    content = {
        "title": draft.title, "description": draft.description, "tags": draft.tags, "prep_minutes": draft.prep_minutes,
        "cook_minutes": draft.cook_minutes, "default_servings": draft.servings, "notes": draft.notes,
        "source_url": draft.source_url, "source_name": draft.source_name, "source_author": draft.source_author,
        "provenance": "url" if draft.source_url else ("ai" if any(i.proposed for i in draft.ingredients) else "manual"),
        "ingredients": [{"name": i.name, "quantity": i.quantity, "unit": i.unit, "preparation": i.preparation, "optional": i.optional,
                         "original_text": i.original_text, "proposed": i.proposed, "provenance": "ai" if i.proposed else "manual"} for i in draft.ingredients],
        "steps": [{"text": s.text, "timer_seconds": int(s.timer_minutes * 60) if s.timer_minutes else None, "proposed": s.proposed,
                   "provenance": "ai" if s.proposed else "manual"} for s in draft.steps],
    }
    media = [{"staging_path": p, "role": "hero" if k == 0 else "photo", "source_url": draft.source_url} for k, p in enumerate(draft.image_staging_paths)]
    r = await c.post("/recipes", {"content": content, "media": media})
    return {"recipe_id": r["id"], "title": r["title"], "version_id": r["version"]["id"], "saved": True}


@mcp.tool(annotations=FETCH)
async def propose_recipe_change(ctx: Context, recipe: Annotated[str, Field(description="Recipe id or title")],
                                instruction: Annotated[str | None, Field(description="e.g. 'make this vegetarian', 'less spicy for the toddlers'")] = None,
                                servings: Annotated[float | None, Field(description="Rescale to this many servings instead")] = None) -> dict:
    """Propose a change to a recipe. Creates a DRAFT version with a before/after diff of ingredients, method, possible
    allergens and nutrition per serving; nothing changes until accept_recipe_change. Described changes need the
    server's AI provider; rescaling doesn't."""
    if not (instruction or servings):
        raise ToolError("Give an instruction or a number of servings")
    c = client(ctx)
    r = await resolve_recipe(c, recipe)
    job = await wait_job(c, await c.post(f"/recipes/{r['id']}/propose-change", {"instruction": instruction, "servings": servings}))
    res = job["result"]
    d = res["diff"]
    return {
        "recipe_id": res["recipe_id"], "draft_version_id": res["draft_version_id"], "label": res["label"], "summary": res["summary"],
        "ingredient_changes": [{"change": x["change"], "before": x.get("before_display"), "after": x.get("after_display")}
                               for x in d["ingredients"] if x["change"] != "unchanged"],
        "step_changes": [{"step": x["position"] + 1, "change": x["change"], "text": x.get("text") or x.get("before")} for x in d["steps"] if x["change"] != "unchanged"],
        "servings": d["servings"], "possible_allergens": d["allergens"], "nutrition_per_serving": d["nutrition_per_serving"],
        "check_yourself": (d.get("review_notes") or []) + (d.get("possible_issues") or []),
        "next": "Show the user; then accept_recipe_change (new named version by default) or discard_recipe_change.",
    }


@mcp.tool(annotations=WRITE)
async def accept_recipe_change(ctx: Context, recipe_id: str, draft_version_id: str, label: str | None = None,
                               replace_current: Annotated[bool, Field(description="True = take over the current version's name (old one stays in history). Default keeps both as named versions.")] = False) -> dict:
    """Publish a reviewed draft from propose_recipe_change as a new immutable version and make it current."""
    c = client(ctx)
    r = await c.post(f"/recipes/{recipe_id}/versions", {"draft_version_id": draft_version_id, "label": label, "mode": "overwrite" if replace_current else "new"})
    return {"recipe_id": r["id"], "current_version": r["version"]["label"], "version_id": r["version"]["id"],
            "versions": [x["label"] for x in r["versions"] if x["status"] == "published"]}


@mcp.tool(annotations=REMOVE)
async def discard_recipe_change(ctx: Context, recipe_id: str, draft_version_id: str) -> dict:
    """Throw away a draft change (published versions are never affected)."""
    c = client(ctx)
    await c.delete(f"/recipes/{recipe_id}/drafts/{draft_version_id}")
    return {"discarded": draft_version_id}


# ---- pantry -----------------------------------------------------------------------------------------------------------------

@mcp.tool(annotations=WRITE)
async def add_to_pantry(ctx: Context,
                        item: Annotated[str, Field(description="Existing product/food name or id, or a new loose item name (e.g. 'Carrots')")],
                        quantity: float,
                        unit: Annotated[str, Field(description="pack, each, g, kg, ml, l")] = "pack",
                        location: Literal["cupboard", "fridge", "freezer", "other"] = "cupboard",
                        use_by: str | None = None, best_before: str | None = None,
                        bought: Annotated[bool, Field(description="True if purchased (records a purchase when a price is given)")] = True,
                        store: str | None = None, price_paid_gbp: float | None = None,
                        barcode: Annotated[str | None, Field(description="Adds a known barcode's product")] = None,
                        new_product_pack_size: Annotated[str | None, Field(description="For a new branded product, e.g. '500 g'")] = None,
                        confirm_duplicate: bool = False) -> dict:
    """Add stock to the pantry as a new lot. Use only for things the user says they have or bought."""
    c = client(ctx)
    body: dict[str, Any] = {"quantity": quantity, "unit": unit, "location": location, "use_by": use_by, "best_before": best_before,
                            "store": store, "price_paid_pence": round(price_paid_gbp * 100) if price_paid_gbp is not None else None,
                            "event_type": "purchase" if bought else "add", "confirm_duplicate": confirm_duplicate}
    if barcode:
        scan = await c.post("/pantry/scan", {"barcode": barcode})
        if scan["status"] != "known":
            raise ToolError(f"Barcode {barcode} isn't a saved product yet ({scan['status']}). Use lookup_barcode, confirm details with the user, "
                            "then add it with new_product_pack_size.")
        body["product_id"] = scan["product"]["id"]
    elif new_product_pack_size:
        m = re.match(r"^\s*([\d.]+)\s*([a-zA-Z]+)\s*$", new_product_pack_size)
        if not m:
            raise ToolError("Pack size should look like '500 g' or '1 l'")
        body["new_product"] = {"name": item, "package_quantity": float(m.group(1)), "package_unit": m.group(2), "food_name": item}
    else:
        try:
            it = await resolve_item(c, item)
            body.update({k: v for k, v in it.items() if k in ("food_id", "product_id")})
        except ToolError:
            body["food_name"] = item
            if unit == "pack":
                body["unit"] = "each"
    r = await c.post("/pantry/lots", body)
    lot = r["lot"]
    return {"lot_id": lot["id"], "added": lot["display"], "location": lot["location"], "use_by": lot["use_by"], "best_before": lot["best_before"]}


@mcp.tool(annotations=WRITE)
async def use_from_pantry(ctx: Context, item: Annotated[str, Field(description="Food or product name or id")], quantity: float,
                          unit: Annotated[str, Field(description="g, kg, ml, l, each, pack, tbsp, …")],
                          wasted: Annotated[bool, Field(description="True if thrown away rather than eaten/used")] = False,
                          reason: str | None = None) -> dict:
    """Record using (or throwing away) an amount of an item. Takes from the earliest-dated lots first and never takes
    stock below zero; any shortfall is reported."""
    c = client(ctx)
    it = await resolve_item(c, item)
    r = await c.post("/pantry/use", {**{k: v for k, v in it.items() if k in ("food_id", "product_id")}, "quantity": quantity, "unit": unit,
                                     "type": "waste" if wasted else "consumption", "reason": reason, "idempotency_key": f"mcp-{uuid.uuid4()}"})
    return {"item": it["name"], "taken": [f"{d['quantity_base']:g} {d['base_unit']} from lot {d['lot_id']}" for d in r["deducted"]],
            "shortfall": r["message"]}


@mcp.tool(annotations=WRITE)
async def adjust_stock(ctx: Context, lot_id: str, action: Literal["move", "correct_count", "mark_opened"],
                       to_location: Literal["cupboard", "fridge", "freezer", "other"] | None = None,
                       counted_quantity: float | None = None, unit: str | None = None,
                       reason: Annotated[str | None, Field(description="Required for correct_count")] = None) -> dict:
    """Move a lot, correct its counted amount (keeps history), or mark it opened. Lot ids come from get_pantry."""
    c = client(ctx)
    if action == "move":
        body = {"type": "transfer", "to_location": to_location}
    elif action == "correct_count":
        body = {"type": "correction", "new_quantity": counted_quantity, "unit": unit, "reason": reason}
    else:
        body = {"type": "open"}
    r = await c.post("/pantry/events", {"lot_id": lot_id, **body})
    return {"lot_id": lot_id, "now": r["lot"]["display"], "location": r["lot"]["location"], "event": r["event"]["type"]}


# ---- planning, absences, shopping -------------------------------------------------------------------------------------------

@mcp.tool(annotations=WRITE)
async def plan_meal(ctx: Context, date: Annotated[str, Field(description="YYYY-MM-DD")], meal: MEALS = "dinner",
                    recipe: Annotated[str | None, Field(description="Recipe id or title")] = None,
                    title: Annotated[str | None, Field(description="Name for a meal without a recipe, e.g. 'Takeaway'")] = None,
                    servings: Annotated[float | None, Field(description="Defaults to the usual portions of the people eating")] = None,
                    people: Annotated[list[str] | None, Field(description="Names; defaults to everyone not marked away")] = None) -> dict:
    """Add a meal to the plan. The shopping list updates automatically."""
    c = client(ctx)
    body: dict[str, Any] = {"date": date, "slot": meal, "servings": servings, "title": title}
    if recipe:
        body["recipe_id"] = (await resolve_recipe(c, recipe))["id"]
    if people is not None:
        body["participant_ids"] = [(await resolve_person(c, p))["id"] for p in people]
    m = await c.post("/plan", body)
    return {"meal_id": m["id"], "date": m["date"], "meal": m["slot"], "title": m["title"], "servings": m["servings"],
            "missing": [f"{x['name']} ({x.get('missing_display') or '?'})" for x in m.get("requirements", []) if x["status"] in ("missing", "short")]}


@mcp.tool(annotations=WRITE)
async def update_planned_meal(ctx: Context, meal_id: str, date: str | None = None, meal: MEALS | None = None,
                              servings: float | None = None, people: list[str] | None = None,
                              status: Literal["planned", "skipped"] | None = None) -> dict:
    """Change a planned meal's day, meal, servings, who's eating or mark it skipped."""
    c = client(ctx)
    body: dict[str, Any] = {k: v for k, v in {"date": date, "slot": meal, "servings": servings, "status": status}.items() if v is not None}
    if people is not None:
        body["participant_ids"] = [(await resolve_person(c, p))["id"] for p in people]
    m = await c.patch(f"/plan/{meal_id}", body)
    return {"meal_id": m["id"], "date": m["date"], "meal": m["slot"], "title": m["title"], "servings": m["servings"], "status": m["status"]}


@mcp.tool(annotations=REMOVE)
async def remove_planned_meal(ctx: Context, meal_id: str) -> dict:
    """Remove a meal from the plan."""
    c = client(ctx)
    await c.delete(f"/plan/{meal_id}")
    return {"removed": meal_id}


@mcp.tool(annotations=WRITE)
async def plan_without_absent(ctx: Context, meal_id: str) -> dict:
    """For a planned meal that includes someone marked away, remove them and reduce servings by their usual portion."""
    c = client(ctx)
    r = await c.post(f"/plan/{meal_id}/apply-absences")
    return {"changed": r["changed"], "removed": r["removed"], "servings": r["meal"]["servings"]}


@mcp.tool(annotations=WRITE)
async def mark_away(ctx: Context, person: str, start_date: str, end_date: str | None = None,
                    meals: Annotated[list[MEALS] | None, Field(description="Meals missed; omit for all day")] = None,
                    reason: Literal["social", "work", "study", "travel", "other"] = "other", note: str | None = None) -> dict:
    """Mark someone as out (e.g. socialising, work, study) for meals between two dates. Returns planned meals that
    still include them; ask the user, then use plan_without_absent for each."""
    c = client(ctx)
    p = await resolve_person(c, person)
    r = await c.post("/absences", {"person_id": p["id"], "start_date": start_date, "end_date": end_date, "slots": meals or [],
                                   "reason": reason, "note": note})
    a = r["absence"]
    return {"absence_id": a["id"], "person": a["person_name"], "from": a["start_date"], "to": a["end_date"],
            "meals": "all day" if a["all_day"] else a["slots"], "affected_planned_meals": r["affected_meals"]}


@mcp.tool(annotations=REMOVE)
async def remove_away(ctx: Context, absence_id: str) -> dict:
    """Delete an absence (planned meals are not changed)."""
    c = client(ctx)
    await c.delete(f"/absences/{absence_id}")
    return {"removed": absence_id}


@mcp.tool(annotations=WRITE)
async def add_to_shopping_list(ctx: Context, name: str, quantity: float | None = None, unit: str | None = None, shop: str | None = None) -> dict:
    """Add an item to the shopping list by hand."""
    c = client(ctx)
    body: dict[str, Any] = {"name": name, "quantity": quantity, "unit": unit, "shop": shop}
    try:
        it = await resolve_item(c, name)
        body.update({k: v for k, v in it.items() if k in ("food_id", "product_id")})
    except ToolError:
        pass
    i = await c.post("/shopping-list/items", body)
    return {"item_id": i["id"], "name": i["name"], "amount": i["display"]}


@mcp.tool(annotations=WRITE)
async def update_shopping_item(ctx: Context, item_id: str, got: bool | None = None, quantity: float | None = None, unit: str | None = None,
                               shop: str | None = None, snooze_days: int | None = None, dismiss: bool | None = None) -> dict:
    """Tick off, change, snooze or dismiss a shopping-list item (ids from get_shopping_list)."""
    c = client(ctx)
    body: dict[str, Any] = {k: v for k, v in {"checked": got, "quantity": quantity, "unit": unit, "shop": shop, "dismissed": dismiss}.items() if v is not None}
    if snooze_days:
        body["snoozed_until"] = (date.today() + timedelta(days=snooze_days)).isoformat()
    i = await c.patch(f"/shopping-list/items/{item_id}", body)
    return {"item_id": i["id"], "name": i["name"], "amount": i["display"], "got": i["checked"], "snoozed_until": i["snoozed_until"]}


# ---- eating & cooking -----------------------------------------------------------------------------------------------------

@mcp.tool(annotations=WRITE)
async def log_eaten(ctx: Context, person: str, food: Annotated[str, Field(description="Food or product name or id")], amount: float,
                    unit: Annotated[str, Field(description="g, ml, each, tbsp, …")] = "g", meal: MEALS | None = None,
                    when: Annotated[str | None, Field(description="ISO date-time; defaults to now")] = None,
                    take_from_pantry: bool = False) -> dict:
    """Record that a person ate something (not pet food). Nutrition is snapshotted; unknown values stay unknown."""
    c = client(ctx)
    p = await resolve_person(c, person)
    it = await resolve_item(c, food)
    e = await c.post("/meals/consumption", {"person_id": p["id"], **{k: v for k, v in it.items() if k in ("food_id", "product_id")},
                                            "amount": amount, "amount_unit": unit, "meal_slot": meal, "consumed_at": when,
                                            "deduct_from_stock": take_from_pantry})
    return {"entry_id": e["id"], "person": p["name"], "what": e["description"], "kcal": e["nutrition"].get("energy_kcal"),
            "complete": e["provenance"].get("complete")}


@mcp.tool(annotations=WRITE)
async def record_cooked_meal(ctx: Context, recipe: Annotated[str, Field(description="Recipe id or title")], servings_made: float,
                             portions: Annotated[dict[str, float], Field(description="Servings each person actually ate, e.g. {'Alex': 1.5, 'Mia': 0.25}")],
                             leftover_servings: float = 0, wasted_servings: float = 0,
                             leftovers_go_in: Literal["fridge", "freezer"] = "fridge",
                             planned_meal_id: str | None = None, when: str | None = None) -> dict:
    """Record a cooked batch: takes the scaled ingredients from the pantry (earliest dates first, never below zero),
    logs each person's actual portion (never split evenly for them) and stores leftovers. Ask the user for portions
    rather than assuming them."""
    c = client(ctx)
    r = await resolve_recipe(c, recipe)
    ps = [{"person_id": (await resolve_person(c, name))["id"], "servings": s} for name, s in portions.items()]
    out = await c.post("/meals/cook", {"recipe_version_id": r["version"]["id"], "servings_made": servings_made, "portions": ps,
                                       "leftover_servings": leftover_servings, "waste_servings": wasted_servings,
                                       "leftover_location": leftovers_go_in, "meal_plan_id": planned_meal_id, "cooked_at": when,
                                       "idempotency_key": f"mcp-{uuid.uuid4()}"})
    b = out["batch"]
    names = {x["id"]: x["name"] for x in await c.get("/people")}
    return {"batch_id": b["id"], "title": b["title"], "servings_made": b["servings_made"],
            "pantry": [{"item": u["name"], "taken": sum(d["quantity_base"] for d in u.get("deducted", [])), "unit": u.get("base_unit"),
                        "shortfall": u.get("shortfall")} for u in b["ingredients_used"] if not u.get("skip")],
            "eaten": [{"person": names.get(x["person_id"], x["person_id"]), "servings": x["amount"], "kcal": x["nutrition"].get("energy_kcal"),
                       "status": x["provenance"]["status"].get("energy_kcal")} for x in out["consumptions"]],
            "unassigned_servings": out["unaccounted_servings"]}


# ---- receipts ------------------------------------------------------------------------------------------------------------------

@mcp.tool(annotations=WRITE)
async def import_receipt_text(ctx: Context, text: Annotated[str, Field(description="Receipt text, e.g. from an e-receipt e-mail")]) -> dict:
    """Upload a receipt as text and read it. Nothing is added to the pantry until apply_receipt. Exact re-uploads are
    refused as duplicates."""
    c = client(ctx)
    r = await c.request("POST", "/receipts/import", data={"text": text})
    if r.get("duplicate"):
        return {"duplicate": True, "message": r["message"], "receipt_id": r["receipt"]["id"]}
    if r.get("job"):
        await wait_job(c, r["job"])
    return await get_receipt(ctx, r["receipt"]["id"])


@mcp.tool(annotations=WRITE)
async def match_receipt_line(ctx: Context, receipt_id: str, line_id: str,
                             product: Annotated[str | None, Field(description="Existing product name or id")] = None,
                             loose_food: Annotated[str | None, Field(description="Generic food name for loose/weighed items")] = None,
                             not_for_pantry: bool = False, confirm_suggestion: bool = False,
                             description: str | None = None, quantity: float | None = None, line_price_gbp: float | None = None,
                             weight_kg: float | None = None, location: Literal["cupboard", "fridge", "freezer", "other"] | None = None,
                             use_by: str | None = None) -> dict:
    """Resolve or correct one receipt line: match it to a product or loose food, accept the suggested match, mark it
    'not for the pantry', or fix a misread description/quantity/price. The shop's original wording is kept."""
    c = client(ctx)
    body: dict[str, Any] = {k: v for k, v in {"description": description, "quantity": quantity, "weight_kg": weight_kg, "location": location,
                                              "use_by": use_by}.items() if v is not None}
    if line_price_gbp is not None:
        body["line_total_pence"] = round(line_price_gbp * 100)
    if not_for_pantry:
        body["match_status"] = "skip"
    elif product:
        it = await resolve_item(c, product)
        if "product_id" not in it:
            raise ToolError(f"'{product}' is a generic food, not a product; use loose_food instead or create the product in the app")
        body["product_id"] = it["product_id"]
    elif loose_food:
        body["food_name"] = loose_food
    elif confirm_suggestion:
        line = next((l for l in (await c.get(f"/receipts/{receipt_id}/review"))["lines"] if l["id"] == line_id), None)
        if not line or not (line["product"] or line["food"]):
            raise ToolError("That line has no suggestion to confirm")
        body["product_id" if line["product"] else "food_id"] = (line["product"] or line["food"])["id"]
    await c.patch(f"/receipts/{receipt_id}/lines/{line_id}", body)
    return await get_receipt(ctx, receipt_id)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False))
async def apply_receipt(ctx: Context, receipt_id: str,
                        confirm: Annotated[bool, Field(description="Must be true: the user has reviewed the lines and agreed")],
                        not_a_duplicate: Annotated[bool, Field(description="Only if the user confirms a flagged duplicate is a different shop")] = False) -> dict:
    """Add a reviewed receipt's items to the pantry and record the prices paid, in one go. Safe to retry (applies once)."""
    if not confirm:
        raise ToolError("Show the user the reviewed lines and totals first, then call again with confirm=true.")
    c = client(ctx)
    r = await c.post(f"/receipts/{receipt_id}/apply", {"idempotency_key": f"mcp-apply-{receipt_id}", "confirm_not_duplicate": not_a_duplicate})
    return {k: v for k, v in r.items() if k != "receipt"}


# ---- resources & prompts -------------------------------------------------------------------------------------------------

@mcp.resource("larder://{view}", name="larder-view", title="Larder views",
              description="Live JSON views: larder://today, larder://pantry, larder://shopping-list, larder://week-plan",
              mime_type="application/json")
async def view_resource(view: str, ctx: Context) -> dict:
    views = {"today": get_today, "pantry": get_pantry, "shopping-list": get_shopping_list, "week-plan": get_week_plan}
    if view not in views:
        raise ToolError(f"Unknown view '{view}'. Try: {', '.join(views)}")
    return await views[view](ctx)


@mcp.prompt(title="Plan the week")
def plan_week(notes: str = "") -> str:
    """Plan dinners for the coming week around what's in stock and who's out."""
    return ("Plan dinners for the next seven days. First call get_today, get_week_plan, list_people and get_pantry. "
            "Prefer recipes that use items needing to be used soon (find_recipes with cook_with_what_we_have=true), respect "
            "allergies, dislikes and preferences, keep salt modest for the toddlers, and plan only for the people at home on "
            "each day. Propose the plan to me as a short table before calling plan_meal, then show the resulting shopping "
            "list with get_shopping_list." + (f"\nAlso: {notes}" if notes else ""))


@mcp.prompt(title="Use it up")
def use_it_up() -> str:
    """Suggest meals that use food close to its date."""
    return ("Call get_use_soon and find_recipes(cook_with_what_we_have=true). Suggest two or three meals for the next few "
            "days that use those items, say what's missing for each, and ask before adding anything to the plan.")


@mcp.prompt(title="After the shop")
def after_the_shop(receipt_text: str) -> str:
    """Turn a pasted e-receipt into pantry stock with review."""
    return ("Import this receipt with import_receipt_text. For each unresolved line, propose a match (existing product, "
            "loose food, or not for the pantry) and show me the list with prices paid before applying. Only call "
            "apply_receipt with confirm=true after I agree.\n\n" + receipt_text)


# ---- entry point ---------------------------------------------------------------------------------------------------------

def http_app():
    """Streamable HTTP app with a bearer-token gate; the token itself is validated by the Larder API on each call."""
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.responses import JSONResponse

    hosts = [h.strip() for h in os.environ.get("LARDER_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    security = TransportSecuritySettings(enable_dns_rebinding_protection=bool(hosts), allowed_hosts=hosts,
                                         allowed_origins=[f"https://{h}" for h in hosts])
    inner = mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True, transport_security=security)

    async def app(scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").startswith("/mcp"):
            auth = dict(scope.get("headers") or {}).get(b"authorization", b"").decode()
            if not auth.lower().startswith("bearer lrd_"):
                resp = JSONResponse({"error": "invalid_token", "error_description": "Send a Larder API token: Authorization: Bearer lrd_…"},
                                    status_code=401, headers={"WWW-Authenticate": 'Bearer realm="larder"'})
                await resp(scope, receive, send)
                return
        if scope["type"] == "http" and scope.get("path") == "/healthz":
            await JSONResponse({"status": "ok"})(scope, receive, send)
            return
        await inner(scope, receive, send)

    return app


def main() -> None:
    ap = argparse.ArgumentParser(prog="larder-mcp", description="MCP server for Larder")
    ap.add_argument("--transport", choices=["stdio", "http"], default=os.environ.get("LARDER_MCP_TRANSPORT", "stdio"))
    ap.add_argument("--host", default=os.environ.get("LARDER_MCP_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("LARDER_MCP_PORT", "8001")))
    args = ap.parse_args()
    if args.transport == "stdio":
        if not os.environ.get("LARDER_TOKEN"):
            raise SystemExit("Set LARDER_URL (e.g. https://larder.home.arpa) and LARDER_TOKEN (Settings → Connected apps).")
        mcp.run("stdio")
    else:
        import uvicorn
        uvicorn.run(http_app(), host=args.host, port=args.port, log_level="info", proxy_headers=True, forwarded_allow_ips="*")



if __name__ == "__main__":
    main()
