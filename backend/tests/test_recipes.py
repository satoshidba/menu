"""Recipe import, review, AI change proposals, versions and scaling (acceptance test 1)."""
import http.server
import io
import json
import threading

import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.services import ai

JSONLD = {
    "@context": "https://schema.org", "@graph": [
        {"@type": "WebPage", "name": "Page"},
        {"@type": "Recipe", "name": "Spinach &amp; ricotta pasta", "author": {"@type": "Person", "name": "Test Cook"},
         "image": ["/hero.png"], "recipeYield": "4 servings", "prepTime": "PT10M", "cookTime": "PT15M",
         "recipeIngredient": ["8 oz pasta", "1 cup ricotta", "200 g spinach", "2 tbsp olive oil", "Salt, to taste", "1 lemon"],
         "recipeInstructions": [
             {"@type": "HowToStep", "text": "Heat the oven to 350°F."},
             {"@type": "HowToStep", "text": "Boil the pasta for 10 minutes.", "image": "/step2.png"},
             {"@type": "HowToStep", "text": "Stir through the spinach and ricotta."}]},
    ]}
PAGE = f"""<html><head><title>Spinach pasta</title><meta property="og:site_name" content="Test Kitchen">
<script type="application/ld+json">{json.dumps(JSONLD)}</script></head>
<body><h1>Spinach pasta</h1><script>alert('x')</script></body></html>"""
NO_DATA_PAGE = "<html><head><title>My blog</title></head><body><p>Just a story about lunch.</p></body></html>"


def _png(colour):
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), colour).save(buf, "PNG")
    return buf.getvalue()


class Handler(http.server.BaseHTTPRequestHandler):
    routes = {"/recipe": ("text/html", PAGE.encode()), "/plain": ("text/html", NO_DATA_PAGE.encode()),
              "/hero.png": ("image/png", _png("red")), "/step2.png": ("image/png", _png("green"))}

    def do_GET(self):  # noqa: N802
        if self.path == "/redirect-private":
            self.send_response(302)
            self.send_header("Location", "http://10.0.0.1/secret")
            self.end_headers()
            return
        ctype, body = self.routes.get(self.path, ("text/plain", b""))
        self.send_response(200 if self.path in self.routes else 404)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def site(monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(get_settings(), "allow_private_fetch", True)
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _port_ok(monkeypatch):
    # the test server runs on a random port; allow non-standard ports only inside tests
    from app.services import safe_fetch
    real = safe_fetch.validate_url

    def patched(url):
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        if parts.hostname == "127.0.0.1":
            return parts.scheme, parts.hostname, parts.port, "127.0.0.1"
        return real(url)
    monkeypatch.setattr(safe_fetch, "validate_url", patched)


def test_url_import_review_save_change_and_version(client, cofid, site, monkeypatch):
    _port_ok(monkeypatch)
    job = client.post("/api/v1/recipes/import", json={"url": f"{site}/recipe"}).json()
    assert job["status"] == "succeeded", job
    res = job["result"]
    d = res["draft"]
    assert res["method"] == "json-ld" and not res["ai_used"]
    assert d["title"] == "Spinach & ricotta pasta" and d["source_author"] == "Test Cook" and d["source_name"] == "Test Kitchen"
    assert d["source_url"].endswith("/recipe") and d["default_servings"] == 4
    ings = {i["original_text"]: i for i in d["ingredients"]}
    # UK units: oz -> g; cup needs confirmation; missing amount is unresolved and not invented
    assert ings["8 oz pasta"]["unit"] == "g" and ings["8 oz pasta"]["quantity"] == 225
    assert ings["1 cup ricotta"]["needs_confirmation"]
    assert ings["Salt, to taste"]["quantity"] is None and ings["Salt, to taste"]["unresolved"]
    assert ings["1 lemon"]["unit"] == "each" and ings["1 lemon"]["quantity"] == 1
    assert "ingredient amounts" in d["unresolved_fields"]
    assert [s["text"] for s in d["steps"]][0] == "Heat the oven to 175°C (350°F)."
    assert {i["role"] for i in res["images"]} == {"hero", "step"}

    # Save the reviewed draft with its images
    media = [{"staging_path": i["staging_path"], "source_url": i["source_url"], "role": i["role"], "step_index": i["step_index"]} for i in res["images"]]
    content = {k: d[k] for k in ("title", "description", "default_servings", "source_url", "source_name", "source_author",
                                 "original_values", "provenance", "extraction_confidence", "unresolved_fields", "prep_minutes", "cook_minutes")}
    content["ingredients"] = [{k: i.get(k) for k in ("original_text", "name", "quantity", "unit", "preparation", "optional", "food_id")} for i in d["ingredients"]]
    content["steps"] = [{"text": s["text"], "original_text": s["original_text"]} for s in d["steps"]]
    r = client.post("/api/v1/recipes", json={"content": content, "media": media})
    assert r.status_code == 201, r.text
    recipe = r.json()
    rid, v1 = recipe["id"], recipe["version"]["id"]
    assert recipe["hero"]["attribution"].startswith("Image from 127.0.0.1")
    assert recipe["version"]["steps"][1]["media"], "step image should be attached to step 2"
    assert client.get(recipe["hero"]["url"]).status_code == 200
    salt = next(i for i in recipe["version"]["ingredients"] if i["name"].lower().startswith("salt"))
    assert salt["unresolved"] and salt["quantity"] is None

    # Ask for a substitution (AI is faked here; the real adapter returns the same schema)
    monkeypatch.setattr(ai, "available", lambda: True)

    def fake_change(recipe_json, instruction, pantry, people, usage=None):
        ings = recipe_json["ingredients"]
        new = [ai.AIIngredient(original_text=i["original_text"], name=i["name"], quantity=i["quantity"], unit=i["unit"], from_position=k)
               for k, i in enumerate(ings) if "ricotta" not in i["name"].lower()]
        new.append(ai.AIIngredient(original_text="150 g cream cheese", name="cream cheese", quantity=150, unit="g", proposed=True))
        steps = [ai.AIStep(text=s["text"].replace("ricotta", "cream cheese"), from_position=k) for k, s in enumerate(recipe_json["steps"])]
        return ai.AIChangeSet(label="With cream cheese", summary="Swapped ricotta for cream cheese.", ingredients=new, steps=steps,
                              substitutions_needing_review=["Cream cheese instead of ricotta: check the label for allergens."],
                              possible_issues=["Contains milk"])

    monkeypatch.setattr(ai, "propose_change", fake_change)
    job = client.post(f"/api/v1/recipes/{rid}/propose-change", json={"instruction": "use cream cheese instead of ricotta"}).json()
    assert job["status"] == "succeeded", job
    diff = job["result"]["diff"]
    changes = {(c["change"], (c.get("after") or c.get("before"))["name"]) for c in diff["ingredients"]}
    assert ("removed", "ricotta") in changes and ("added", "cream cheese") in changes
    assert any(s["change"] == "modified" for s in diff["steps"])
    assert "energy_kcal" in diff["nutrition_per_serving"] and diff["review_notes"]
    draft_id = job["result"]["draft_version_id"]
    # Nothing changes until it is accepted
    assert client.get(f"/api/v1/recipes/{rid}").json()["current_version_id"] == v1

    r = client.post(f"/api/v1/recipes/{rid}/versions", json={"draft_version_id": draft_id})
    assert r.status_code == 201, r.text
    after = r.json()
    assert after["current_version_id"] == draft_id and len(after["versions"]) == 2
    assert {v["label"] for v in after["versions"]} == {"Original", "With cream cheese"}
    # the original is retained unchanged
    orig = client.get(f"/api/v1/recipes/{rid}?version_id={v1}").json()["version"]
    assert any(i["name"] == "ricotta" for i in orig["ingredients"])
    # step image carried? (media is per version; original still has it)
    assert orig["steps"][1]["media"]


def test_published_versions_are_immutable(client, db):
    r = client.post("/api/v1/recipes", json={"content": {"title": "Toast", "ingredients": [{"name": "Bread", "quantity": 2, "unit": "slice"}],
                                                         "steps": [{"text": "Toast it."}]}})
    vid = r.json()["version"]["id"]
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE recipe_version SET title='Changed' WHERE id=:v"), {"v": vid})
        db.commit()
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE recipe_ingredient SET quantity=99 WHERE version_id=:v"), {"v": vid})
        db.commit()
    db.rollback()
    # Editing creates a new version instead
    content = client.get(f"/api/v1/recipes/{r.json()['id']}/versions/{vid}/content").json()
    content["ingredients"][0]["quantity"] = 4
    r2 = client.post(f"/api/v1/recipes/{r.json()['id']}/versions", json={"content": content, "label": "Hungry", "change_summary": "More toast"})
    assert r2.status_code == 201
    mod = [c for c in r2.json()["version"]["diff"]["ingredients"] if c["change"] == "modified"]
    assert mod and mod[0]["fields"] == ["quantity"]
    # overwrite keeps history but takes over the label
    content["ingredients"][0]["quantity"] = 3
    r3 = client.post(f"/api/v1/recipes/{r.json()['id']}/versions", json={"content": content, "mode": "overwrite"})
    assert r3.json()["version"]["label"] == "Hungry" and len(r3.json()["versions"]) == 3
    # deleting the recipe still works (cascade)
    assert client.delete(f"/api/v1/recipes/{r.json()['id']}").status_code == 200


def test_scaling_for_a_meal_does_not_mutate_base(client):
    r = client.post("/api/v1/recipes", json={"content": {"title": "Soup", "default_servings": 4,
                                                         "ingredients": [{"name": "Carrots", "quantity": 500, "unit": "g"},
                                                                         {"name": "Onion", "quantity": 1, "unit": "each"}],
                                                         "steps": [{"text": "Simmer."}]}}).json()
    s = client.get(f"/api/v1/recipes/{r['id']}/scaled?servings=3").json()
    assert s["ingredients"][0]["quantity"] == 375 and s["ingredients"][1]["display"] == "¾"
    again = client.get(f"/api/v1/recipes/{r['id']}").json()["version"]
    assert again["ingredients"][0]["quantity"] == 500 and again["default_servings"] == 4
    # deterministic scaling proposal without AI -> draft with diff
    job = client.post(f"/api/v1/recipes/{r['id']}/propose-change", json={"servings": 2}).json()
    assert job["status"] == "succeeded" and job["result"]["diff"]["servings"] == {"before": 4, "after": 2}


def test_text_import_and_no_ai_fallback(client, site, monkeypatch):
    _port_ok(monkeypatch)
    text_ = "Easy flapjacks\nServes 12\nIngredients\n250 g oats\n125 g butter\n100 g golden syrup\nMethod\n1. Melt the butter and syrup.\n2. Stir in the oats.\n3. Bake at 180°C for 20 minutes."
    job = client.post("/api/v1/recipes/import", json={"text": text_}).json()
    d = job["result"]["draft"]
    assert d["title"] == "Easy flapjacks" and d["default_servings"] == 12 and len(d["ingredients"]) == 3 and len(d["steps"]) == 3
    # a page with no recipe data and no AI configured -> clear message, partial draft, no invented content
    job = client.post("/api/v1/recipes/import", json={"url": f"{site}/plain"}).json()
    assert job["status"] == "succeeded"
    assert job["result"]["draft"]["ingredients"] == [] and "ingredients" in job["result"]["draft"]["unresolved_fields"]
    assert any("no AI provider" in w for w in job["result"]["warnings"])
    # vibe needs AI
    assert client.post("/api/v1/recipes/vibe", json={"prompt": "something with spinach"}).status_code == 503


def test_ssrf_protection(client, site):
    get_settings().allow_private_fetch = False
    for url in ("http://127.0.0.1/recipe", "http://localhost/", "http://10.0.0.5/", "http://169.254.169.254/latest/meta-data/",
                "file:///etc/passwd", "http://[::1]/", "http://user:pw@example.com/", "http://example.com:8080/"):
        job = client.post("/api/v1/recipes/import", json={"url": url}).json()
        assert job["status"] == "failed", url
    from app.services.safe_fetch import FetchError, validate_url
    with pytest.raises(FetchError):
        validate_url("http://0.0.0.0/")
