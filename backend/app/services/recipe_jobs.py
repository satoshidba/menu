"""Background handlers that turn a URL / text / photos / prompt into a *draft* for human review."""
from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import audit
from ..config import get_settings
from ..models import Food, ImportJob, PantryLot, PersonProfile, Recipe, RecipeVersion
from . import ai
from .jobs import JobFailure, handler
from .media import abs_media, store_image
from .recipe_extract import ExtractedRecipe, extract_from_html, extract_from_text, structure_ingredients
from .recipes import RecipeContent, build_version, content_from_version, match_food, suggest_foods
from .safe_fetch import FetchError, fetch
from .units import FoodFactors, canonical_unit, convert, normalise_metric

log = logging.getLogger("larder.recipes")
MAX_STEP_IMAGES = 12


def _audit_ai(db: Session, job: ImportJob, usage: list) -> None:
    for u in usage:
        audit(db, None, "ai.call", "import_job", job.id, u, household_id=job.household_id)


def _draft_ingredient(db: Session, household_id, ing: dict) -> dict:
    """Attach food matches and UK-metric normalisation (ingredient-independent conversions only)."""
    food = match_food(db, household_id, ing["name"]) if ing.get("name") else None
    unit = canonical_unit(ing.get("unit")) if ing.get("unit") else None
    q = ing.get("quantity")
    note = ing.get("note")
    needs_confirmation = False
    if q is not None and unit:
        if unit in ("oz", "lb", "floz", "pint"):
            c = normalise_metric(q, unit)
            note = "; ".join(x for x in [note, f"converted from {q:g} {unit}"] if x)
            q, unit = c.quantity, c.unit
        elif unit == "cup":
            factors = FoodFactors.from_food(food)
            c = convert(q, "cup", "g", factors)
            if c.quantity is not None:
                note = "; ".join(x for x in [note, f"{q:g} cup → {c.quantity:g} g using this ingredient's cup weight"] if x)
                q, unit = round(c.quantity), "g"
            else:
                needs_confirmation = True
                note = "; ".join(x for x in [note, "Cup measure: confirm the weight in grams for this ingredient"] if x)
    return {**ing, "quantity": q, "unit": unit or ing.get("unit"), "note": note, "needs_confirmation": needs_confirmation,
            "unresolved": q is None, "food_id": str(food.id) if food else ing.get("food_id"),
            "food_name": food.name if food else None,
            "food_suggestions": [] if food else suggest_foods(db, household_id, ing.get("name", ""))}


def _stage_images(job: ImportJob, er: ExtractedRecipe) -> tuple[list[dict], list[str]]:
    """Download hero and step images that the page exposes. Failures leave a placeholder, never a fake image."""
    images, warnings = [], []
    candidates = [("hero", None, u) for u in er.image_urls[:1]]
    candidates += [("step", i, s["image"]) for i, s in enumerate(er.steps) if s.get("image")][:MAX_STEP_IMAGES]
    for role, idx, url in candidates:
        try:
            got = fetch(url, accept="image/avif,image/webp,image/png,image/jpeg,image/*")
            if got.status != 200 or not got.content_type.startswith("image/"):
                raise FetchError(f"status {got.status}")
            rel, w, h = store_image(got.body, f"staging/{job.id}")
            images.append({"staging_path": rel, "url": f"/api/v1/media/{rel}", "source_url": url, "role": role,
                           "step_index": idx, "width": w, "height": h})
        except Exception as e:  # noqa: BLE001 - any failure means "no image", shown as a placeholder
            warnings.append(f"Couldn't copy an image from the source ({'hero' if role == 'hero' else f'step {idx + 1}'}): "
                            f"{e if isinstance(e, FetchError) else 'not available'}. You can upload your own photo.")
    return images, warnings


def _content_from_extracted(er: ExtractedRecipe, url: str | None) -> dict:
    return {
        "title": er.title or "", "description": er.description, "cuisine": er.cuisine, "tags": er.tags,
        "prep_minutes": er.prep_minutes, "cook_minutes": er.cook_minutes, "total_minutes": er.total_minutes,
        "yield_text": er.yield_text, "default_servings": er.servings or 4,
        "ingredients": structure_ingredients(er.ingredients),
        "steps": [{"text": s["text"], "original_text": s["text"], "section": s.get("section")} for s in er.steps],
        "source_url": url, "source_name": er.site_name, "source_author": er.author,
        "original_values": {"ingredients": er.ingredients, "steps": [s["text"] for s in er.steps],
                            "yield": er.yield_text, "nutrition": er.nutrition_source},
        "provenance": "url" if url else "manual",
    }


def _content_from_ai(d: ai.AIRecipeDraft, *, provenance: str, url: str | None = None) -> dict:
    return {
        "title": d.title, "description": d.description, "cuisine": d.cuisine, "tags": d.tags,
        "prep_minutes": d.prep_minutes, "cook_minutes": d.cook_minutes, "yield_text": d.yield_text,
        "default_servings": d.servings or 4,
        "ingredients": [{"position": i, "original_text": x.original_text, "name": x.name, "quantity": x.quantity,
                         "unit": x.unit, "preparation": x.preparation, "optional": x.optional, "proposed": x.proposed,
                         "provenance": "ai", "confidence": d.confidence} for i, x in enumerate(d.ingredients)],
        "steps": [{"text": s.text, "original_text": None if s.proposed else s.text, "proposed": s.proposed, "provenance": "ai",
                   "timer_seconds": int(s.timer_minutes * 60) if s.timer_minutes else None} for s in d.steps],
        "source_url": url, "provenance": provenance, "extraction_confidence": d.confidence,
        "unresolved_fields": d.unresolved_fields, "ai_possible_allergens": d.possible_allergens, "ai_notes": d.notes,
    }


def _finish_draft(db: Session, job: ImportJob, content: dict, *, method: str, warnings: list[str], images: list[dict],
                  ai_used: bool) -> dict:
    hid = job.household_id
    content["ingredients"] = [_draft_ingredient(db, hid, i) for i in content.get("ingredients", [])]
    unresolved = list(dict.fromkeys(content.get("unresolved_fields") or []))
    if not content.get("title"):
        unresolved.append("title")
    if not content["ingredients"]:
        unresolved.append("ingredients")
    if not content.get("steps"):
        unresolved.append("method")
    missing_amounts = [i["original_text"] or i["name"] for i in content["ingredients"] if i["unresolved"] and not i.get("optional")]
    if missing_amounts:
        unresolved.append("ingredient amounts")
    if not any(img["role"] == "hero" for img in images):
        unresolved.append("image")
    content["unresolved_fields"] = list(dict.fromkeys(unresolved))
    confidence = content.get("extraction_confidence")
    if confidence is None:
        confidence = {"json-ld": 0.9, "microdata": 0.75, "heuristic": 0.5, "text": 0.6}.get(method, 0.4)
        if missing_amounts:
            confidence -= 0.1
    content["extraction_confidence"] = round(max(0.05, min(confidence, 1.0)), 2)
    return {"draft": content, "images": images, "method": method, "ai_used": ai_used, "warnings": warnings,
            "missing_amounts": missing_amounts}


@handler("recipe_import")
def recipe_import(db: Session, job: ImportJob) -> dict:
    mode = job.input.get("mode")
    usage: list = []
    warnings: list[str] = []
    if mode == "url":
        url = job.input["url"]
        try:
            page = fetch(url)
        except FetchError as e:
            raise JobFailure(str(e)) from e
        if page.status >= 400:
            raise JobFailure(f"The page returned an error ({page.status}). Try pasting the recipe text instead.")
        if "html" not in page.content_type and "text" not in page.content_type:
            raise JobFailure("That link isn't a web page. Upload photos or paste the text instead.")
        html = page.body.decode("utf-8", errors="replace")
        er = extract_from_html(html, page.url)
        method = er.method
        content = _content_from_extracted(er, page.url)
        ai_used = False
        if not er.complete:
            if ai.available():
                try:
                    d = ai.structure_recipe(er.page_text, url=page.url, partial={k: content[k] for k in ("title",) if content.get(k)}, usage=usage)
                    content = {**_content_from_ai(d, provenance="url", url=page.url),
                               "source_name": er.site_name, "source_author": er.author,
                               "original_values": content["original_values"]}
                    ai_used, method = True, f"{method}+ai"
                except (ai.AIError, ai.AIUnavailable) as e:
                    warnings.append(f"AI help wasn't available ({e}); showing what could be read directly.")
            else:
                warnings.append("The page has no structured recipe data and no AI provider is configured; "
                                "fill in the missing parts below.")
        images, img_warn = _stage_images(job, er)
        warnings += img_warn
        _audit_ai(db, job, usage)
        return _finish_draft(db, job, content, method=method, warnings=warnings, images=images, ai_used=ai_used)

    if mode == "text":
        text = job.input["text"][:60000]
        er = extract_from_text(text)
        content = _content_from_extracted(er, None)
        content["original_text"] = text
        content["provenance"] = "manual"
        method, ai_used = "text", False
        if not er.complete and ai.available():
            try:
                d = ai.structure_recipe(text, usage=usage)
                content = {**_content_from_ai(d, provenance="ai"), "original_text": text}
                method, ai_used = "text+ai", True
            except (ai.AIError, ai.AIUnavailable) as e:
                warnings.append(f"AI help wasn't available ({e}).")
        elif not er.complete:
            warnings.append("Couldn't find clear Ingredients and Method sections; check the fields below.")
        _audit_ai(db, job, usage)
        return _finish_draft(db, job, content, method=method, warnings=warnings, images=[], ai_used=ai_used)

    if mode == "photos":
        paths = job.input.get("staging_paths", [])
        images = [{"staging_path": p, "url": f"/api/v1/media/{p}", "source_url": None, "role": "hero" if i == 0 else "page",
                   "step_index": None} for i, p in enumerate(paths)]
        text = ""
        try:
            import pytesseract
            from PIL import Image

            for p in paths:
                text += pytesseract.image_to_string(Image.open(abs_media(p)), lang="eng", timeout=60) + "\n"
        except Exception:  # noqa: BLE001 - OCR is optional
            warnings.append("Text recognition isn't available on this server.")
        content, method, ai_used = None, "ocr", False
        if ai.available():
            try:
                blobs = [(abs_media(p).read_bytes(), "image/jpeg") for p in paths[:6]]
                d = ai.structure_recipe(text or "(see photos)", images=blobs, usage=usage)
                content, method, ai_used = _content_from_ai(d, provenance="ai"), "photo+ai", True
            except (ai.AIError, ai.AIUnavailable) as e:
                warnings.append(f"AI help wasn't available ({e}).")
        if content is None:
            er = extract_from_text(text)
            content = _content_from_extracted(er, None)
        content["original_text"] = text or None
        _audit_ai(db, job, usage)
        return _finish_draft(db, job, content, method=method, warnings=warnings, images=images, ai_used=ai_used)

    if mode == "vibe":
        if not ai.available():
            raise JobFailure("Recipe ideas need an AI provider. Ask the administrator to add one, or write the recipe by hand.")
        foods = []
        for fid in job.input.get("food_ids", []):
            f = db.get(Food, fid)
            if f and f.household_id == job.household_id and not f.is_pet_food:
                foods.append(f.name)
        if not foods:
            lots = db.scalars(select(PantryLot).where(PantryLot.household_id == job.household_id, PantryLot.status == "active",
                                                      PantryLot.is_pet_food.is_(False)).limit(60))
            foods = sorted({(l.product.name if l.product else l.food.name) for l in lots})
        people = _people_context(db, job.household_id)
        try:
            d = ai.vibe_recipe(job.input["prompt"], foods, people, usage=usage)
        except (ai.AIError, ai.AIUnavailable) as e:
            raise JobFailure(str(e)) from e
        content = _content_from_ai(d, provenance="ai")
        for i in content["ingredients"]:
            i["proposed"] = True
        for s in content["steps"]:
            s["proposed"] = True
        _audit_ai(db, job, usage)
        result = _finish_draft(db, job, content, method="ai-idea", warnings=["This is an AI suggestion. Check every amount and step."],
                               images=[], ai_used=True)
        result["draft"]["unresolved_fields"] = [x for x in result["draft"]["unresolved_fields"] if x != "image"]
        return result

    raise JobFailure(f"Unknown import mode {mode}")


def _people_context(db: Session, household_id) -> list[dict]:
    return [{"name": p.name, "kind": p.kind, "allergies": p.allergies, "dislikes": p.dislikes,
             "preferences": p.dietary_preferences} for p in db.scalars(select(PersonProfile).where(PersonProfile.household_id == household_id))]


@handler("recipe_change")
def recipe_change(db: Session, job: ImportJob) -> dict:
    """Create a *draft* version holding the proposed change set (with a structured diff) for review."""
    recipe = db.get(Recipe, job.input["recipe_id"])
    base = db.get(RecipeVersion, job.input["base_version_id"])
    if recipe is None or base is None or base.recipe_id != recipe.id or recipe.household_id != job.household_id:
        raise JobFailure("Recipe not found")
    instruction = (job.input.get("instruction") or "").strip()
    servings = job.input.get("servings")
    usage: list = []
    content = content_from_version(base)
    label, summary, review_notes, issues = None, None, [], []
    if instruction and ai.available():
        pantry = sorted({(l.product.name if l.product else l.food.name) for l in db.scalars(
            select(PantryLot).where(PantryLot.household_id == job.household_id, PantryLot.status == "active",
                                    PantryLot.is_pet_food.is_(False)).limit(80))})
        try:
            cs = ai.propose_change(content, instruction, pantry, _people_context(db, job.household_id), usage=usage)
        except (ai.AIError, ai.AIUnavailable) as e:
            raise JobFailure(str(e)) from e
        old_ings = content["ingredients"]
        new_ings = []
        for x in cs.ingredients:
            src = old_ings[x.from_position] if x.from_position is not None and 0 <= x.from_position < len(old_ings) else None
            same = src and src["name"] == x.name and src["quantity"] == x.quantity and (src["unit"] or None) == (canonical_unit(x.unit) if x.unit else None)
            new_ings.append({**(src or {}), "name": x.name, "quantity": x.quantity, "unit": x.unit, "preparation": x.preparation,
                             "optional": x.optional, "original_text": src["original_text"] if same else x.original_text,
                             "food_id": src.get("food_id") if (src and src["name"] == x.name) else None,
                             "product_id": src.get("product_id") if (src and src["name"] == x.name) else None,
                             "proposed": not same, "provenance": src["provenance"] if same else "ai"})
        content["ingredients"] = new_ings
        old_steps = content["steps"]
        content["steps"] = [{**(old_steps[s.from_position] if s.from_position is not None and 0 <= s.from_position < len(old_steps) else {}),
                             "text": s.text, "proposed": s.proposed or s.from_position is None,
                             "timer_seconds": int(s.timer_minutes * 60) if s.timer_minutes else None} for s in cs.steps]
        if cs.servings:
            content["default_servings"] = cs.servings
        label, summary = cs.label, cs.summary
        review_notes, issues = cs.substitutions_needing_review, cs.possible_issues
    elif servings:
        f = float(servings) / (base.default_servings or 1)
        for i in content["ingredients"]:
            if i["quantity"] is not None:
                i["quantity"] = round(i["quantity"] * f, 2)
        content["default_servings"] = float(servings)
        label, summary = f"Serves {float(servings):g}", f"Scaled from {base.default_servings:g} to {float(servings):g} servings."
    elif instruction:
        raise JobFailure("Changing a recipe from a description needs an AI provider. You can still edit it by hand.")
    else:
        raise JobFailure("Say what to change, or choose a number of servings.")
    for i in content["ingredients"]:
        i.pop("from_position", None)
    draft = build_version(db, recipe, RecipeContent(**content), household_id=job.household_id, label=label or "Changed",
                          parent=base, user_id=job.created_by_id, status="draft", change_request=instruction or None,
                          change_summary=summary)
    draft.diff = {**draft.diff, "review_notes": review_notes, "possible_issues": issues}
    _audit_ai(db, job, usage)
    db.commit()
    return {"draft_version_id": str(draft.id), "recipe_id": str(recipe.id), "label": draft.label, "summary": summary,
            "diff": draft.diff, "ai_used": bool(usage)}
