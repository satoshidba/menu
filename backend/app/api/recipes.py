"""Recipes: list/search, import to draft, save, versions, change proposals, media, scaling, nutrition."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..auth import Principal, audit, rate_limit, require
from ..config import get_settings
from ..db import get_db
from ..models import Food, ImportJob, PantryLot, Recipe, RecipeMedia, RecipeVersion, utcnow
from ..services import ai
from ..services.jobs import enqueue, job_out
from ..services.media import delete_media, move_media, store_image
from ..services.nutrition_db import ingredient_nutrition_status, recipe_nutrition
from ..services.recipes import (RecipeContent, build_version, content_from_version, requirements, review_flags,
                                scaled_ingredients, suggest_foods)
from ..services.stock import relevant_date
from ..services.units import uk_display
from .common import get_owned, iso, sid

router = APIRouter(prefix="/api/v1", tags=["recipes"])


def media_out(m: RecipeMedia) -> dict:
    return {"id": sid(m.id), "url": f"/api/v1/media/{m.path}", "kind": m.kind, "width": m.width, "height": m.height,
            "focal_x": m.focal_x, "focal_y": m.focal_y, "source_url": m.source_url, "attribution": m.attribution,
            "is_ai_generated": m.is_ai_generated, "label": "Illustration (AI-generated)" if m.is_ai_generated else None,
            "alt_text": m.alt_text, "version_id": sid(m.version_id), "step_position": m.step_position}


def version_summary(v: RecipeVersion) -> dict:
    return {"id": sid(v.id), "version_number": v.version_number, "label": v.label, "status": v.status, "title": v.title,
            "parent_version_id": sid(v.parent_version_id), "change_summary": v.change_summary,
            "created_at": iso(v.created_at), "published_at": iso(v.published_at)}


def version_out(db: Session, v: RecipeVersion, servings: float | None = None) -> dict:
    media = list(db.scalars(select(RecipeMedia).where(RecipeMedia.version_id == v.id)))
    by_step: dict[int, list] = {}
    for m in media:
        by_step.setdefault(m.step_position, []).append(media_out(m))
    s = servings or v.default_servings
    return {
        **version_summary(v), "description": v.description, "cuisine": v.cuisine, "tags": v.tags or [],
        "prep_minutes": v.prep_minutes, "cook_minutes": v.cook_minutes, "total_minutes": v.total_minutes,
        "yield_text": v.yield_text, "default_servings": v.default_servings, "servings": s, "notes": v.notes,
        "allergens": v.allergens or [], "source_url": v.source_url, "source_name": v.source_name,
        "source_author": v.source_author, "imported_at": iso(v.imported_at), "original_text": v.original_text,
        "original_values": v.original_values or {}, "provenance": v.provenance,
        "extraction_confidence": v.extraction_confidence, "unresolved_fields": v.unresolved_fields or [],
        "change_request": v.change_request, "diff": v.diff or {},
        "ingredients": [{**x, "id": sid(v.ingredients[k].id), "nutrition_known": ingredient_nutrition_status(db, v.ingredients[k])["known"]}
                        for k, x in enumerate(scaled_ingredients(v, s))],
        "steps": [{"id": sid(st.id), "position": st.position, "text": st.text, "original_text": st.original_text,
                   "timer_seconds": st.timer_seconds, "proposed": st.proposed, "provenance": st.provenance,
                   "media": by_step.get(st.position, [])} for st in v.steps],
    }


def recipe_out(db: Session, r: Recipe, version: RecipeVersion | None = None, servings: float | None = None) -> dict:
    v = version or (db.get(RecipeVersion, r.current_version_id) if r.current_version_id else None)
    hero = db.get(RecipeMedia, r.hero_media_id) if r.hero_media_id else None
    versions = db.scalars(select(RecipeVersion).where(RecipeVersion.recipe_id == r.id).order_by(RecipeVersion.version_number))
    return {"id": sid(r.id), "title": r.title, "favourite": r.favourite, "is_archived": r.is_archived,
            "current_version_id": sid(r.current_version_id), "hero": media_out(hero) if hero else None,
            "media": [media_out(m) for m in db.scalars(select(RecipeMedia).where(RecipeMedia.recipe_id == r.id, RecipeMedia.version_id.is_(None)))],
            "versions": [version_summary(x) for x in versions],
            "version": version_out(db, v, servings) if v else None, "updated_at": iso(r.updated_at)}


# ---- list / find ---------------------------------------------------------------------------------------------------

@router.get("/recipes")
def list_recipes(q: str | None = None, tag: str | None = None, max_minutes: int | None = None,
                 cook_with_what_we_have: bool = False, include_archived: bool = False,
                 limit: int = Query(60, le=200), p: Principal = Depends(require("recipes:read")), db: Session = Depends(get_db)):
    stmt = select(Recipe).where(Recipe.household_id == p.household_id, Recipe.current_version_id.is_not(None))
    if not include_archived:
        stmt = stmt.where(Recipe.is_archived.is_(False))
    if q:
        stmt = stmt.where(or_(Recipe.title.ilike(f"%{q}%"), func.similarity(Recipe.title, q) > 0.2))
    recipes = list(db.scalars(stmt.order_by(Recipe.favourite.desc(), Recipe.updated_at.desc()).limit(500)))
    stock = _stock_index(db, p.household_id)
    out = []
    for r in recipes:
        v = db.get(RecipeVersion, r.current_version_id)
        if tag and tag.lower() not in [t.lower() for t in v.tags or []]:
            continue
        if max_minutes and v.total_minutes and v.total_minutes > max_minutes:
            continue
        needed = [i for i in v.ingredients if not i.optional and i.food_id]
        have = [i for i in needed if i.food_id in stock]
        missing = [i.name for i in needed if i.food_id not in stock]
        soon = [stock[i.food_id]["name"] for i in have if stock[i.food_id]["days"] is not None and stock[i.food_id]["days"] <= 3]
        coverage = len(have) / len(needed) if needed else 0
        score = coverage + 0.15 * len(soon)
        reasons = []
        if needed:
            reasons.append(f"You have {len(have)} of {len(needed)} ingredients")
        if soon:
            reasons.append("Uses " + ", ".join(sorted(set(soon))[:3]) + " (use soon)")
        hero = db.get(RecipeMedia, r.hero_media_id) if r.hero_media_id else None
        out.append({"id": sid(r.id), "title": r.title, "favourite": r.favourite, "hero": media_out(hero) if hero else None,
                    "version_id": sid(v.id), "version_label": v.label, "tags": v.tags or [], "total_minutes": v.total_minutes,
                    "default_servings": v.default_servings, "allergens": v.allergens or [], "source_name": v.source_name,
                    "match": {"score": round(score, 3), "have": len(have), "needed": len(needed), "missing": missing[:8],
                              "use_soon": sorted(set(soon)), "reasons": reasons}})
    if cook_with_what_we_have:
        out = [o for o in out if o["match"]["needed"]]
        out.sort(key=lambda o: (-o["match"]["score"], len(o["match"]["missing"])))
    return out[:limit]


def _stock_index(db: Session, household_id) -> dict:
    lots = db.scalars(select(PantryLot).where(PantryLot.household_id == household_id, PantryLot.status == "active",
                                              PantryLot.is_pet_food.is_(False)))
    idx: dict = {}
    today = date.today()
    for l in lots:
        fid = l.food_id or (l.product.food_id if l.product else None)
        if not fid:
            continue
        rd = relevant_date(l)
        days = (rd - today).days if rd else None
        name = l.food.name if l.food else (l.product.food.name if l.product and l.product.food else l.product.name)
        cur = idx.get(fid)
        if cur is None or (days is not None and (cur["days"] is None or days < cur["days"])):
            idx[fid] = {"name": name, "days": days}
    return idx


# ---- import / vibe -> draft ----------------------------------------------------------------------------------------

class ImportIn(BaseModel):
    url: str | None = Field(None, max_length=2000)
    text: str | None = Field(None, max_length=60000)


@router.post("/recipes/import", status_code=202)
def import_recipe(body: ImportIn, p: Principal = Depends(require("recipes:write")), db: Session = Depends(get_db)):
    """Start an import from a URL or pasted text. Poll GET /jobs/{id}; the result is a draft for review."""
    rate_limit(db, f"import:{p.household_id}", get_settings().import_rate_per_minute)
    if body.url:
        job = enqueue(db, "recipe_import", {"mode": "url", "url": body.url.strip()}, household_id=p.household_id, user_id=p.user_id)
    elif body.text:
        job = enqueue(db, "recipe_import", {"mode": "text", "text": body.text}, household_id=p.household_id, user_id=p.user_id)
    else:
        raise HTTPException(422, "Paste a link or some recipe text")
    return job_out(job)


@router.post("/recipes/import/photos", status_code=202)
async def import_recipe_photos(files: list[UploadFile] = File(...), p: Principal = Depends(require("recipes:write")),
                               db: Session = Depends(get_db)):
    rate_limit(db, f"import:{p.household_id}", get_settings().import_rate_per_minute)
    if len(files) > 8:
        raise HTTPException(422, "Up to 8 photos at a time")
    paths = []
    folder = f"staging/{uuid.uuid4().hex}"
    for f in files:
        data = await f.read(get_settings().max_upload_mb * 1024 * 1024 + 1)
        if len(data) > get_settings().max_upload_mb * 1024 * 1024:
            raise HTTPException(413, "A photo is too large")
        rel, _, _ = store_image(data, folder)
        paths.append(rel)
    job = enqueue(db, "recipe_import", {"mode": "photos", "staging_paths": paths}, household_id=p.household_id, user_id=p.user_id)
    return job_out(job)


class VibeIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=1000)
    food_ids: list[uuid.UUID] = []


@router.post("/recipes/vibe", status_code=202)
def vibe(body: VibeIn, p: Principal = Depends(require("recipes:write")), db: Session = Depends(get_db)):
    """'Describe what you want' + selected pantry items -> an AI-proposed recipe draft (all items marked proposed)."""
    if not ai.available():
        raise HTTPException(503, "Recipe ideas need an AI provider. You can still write a recipe by hand.")
    rate_limit(db, f"import:{p.household_id}", get_settings().import_rate_per_minute)
    job = enqueue(db, "recipe_import", {"mode": "vibe", "prompt": body.prompt, "food_ids": [str(x) for x in body.food_ids]},
                  household_id=p.household_id, user_id=p.user_id)
    return job_out(job)


@router.get("/jobs/{job_id}")
def get_job(job_id: uuid.UUID, p: Principal = Depends(require("recipes:read")), db: Session = Depends(get_db)):
    j = get_owned(db, ImportJob, job_id, p.household_id, "Job")
    return job_out(j)


# ---- save ------------------------------------------------------------------------------------------------------------

class MediaRef(BaseModel):
    staging_path: str
    source_url: str | None = None
    attribution: str | None = None
    role: str = Field("hero", pattern="^(hero|step|page|photo)$")
    step_index: int | None = None


class RecipeCreateIn(BaseModel):
    content: RecipeContent
    media: list[MediaRef] = []
    label: str = "Original"
    status: str = Field("published", pattern="^(published|draft)$")


def _attach_staged(db: Session, p: Principal, recipe: Recipe, version: RecipeVersion, media: list[MediaRef]) -> None:
    for m in media:
        if not m.staging_path.startswith("staging/") or ".." in m.staging_path:
            raise HTTPException(422, "Bad media reference")
        try:
            rel = move_media(m.staging_path, f"recipes/{recipe.id}")
        except FileNotFoundError:
            continue
        is_step = m.role == "step" and m.step_index is not None and m.step_index < len(version.steps)
        rm = RecipeMedia(recipe_id=recipe.id, household_id=p.household_id, kind="step" if is_step else "photo", path=rel,
                         source_url=m.source_url, attribution=m.attribution or (f"Image from {m.source_url.split('/')[2]}" if m.source_url else None),
                         provenance="url" if m.source_url else "manual", version_id=version.id if is_step else None,
                         step_position=m.step_index if is_step else None)
        db.add(rm)
        db.flush()
        if m.role == "hero" and recipe.hero_media_id is None:
            recipe.hero_media_id = rm.id


@router.post("/recipes", status_code=201)
def create_recipe(body: RecipeCreateIn, p: Principal = Depends(require("recipes:write")), db: Session = Depends(get_db)):
    """Save a reviewed recipe (manual, imported or AI-assisted) as version 1."""
    r = Recipe(household_id=p.household_id, title=body.content.title)
    db.add(r)
    db.flush()
    v = build_version(db, r, body.content, household_id=p.household_id, label=body.label, parent=None, user_id=p.user_id,
                      status=body.status)
    if body.status == "published":
        r.current_version_id = v.id
    _attach_staged(db, p, r, v, body.media)
    audit(db, p, "recipe.created", "recipe", r.id, {"provenance": body.content.provenance, "source_url": body.content.source_url})
    db.commit()
    return recipe_out(db, r, v)


@router.get("/recipes/{recipe_id}")
def get_recipe(recipe_id: uuid.UUID, version_id: uuid.UUID | None = None, servings: float | None = Query(None, gt=0, le=100),
               p: Principal = Depends(require("recipes:read")), db: Session = Depends(get_db)):
    r = get_owned(db, Recipe, recipe_id, p.household_id, "Recipe")
    v = None
    if version_id:
        v = db.get(RecipeVersion, version_id)
        if v is None or v.recipe_id != r.id:
            raise HTTPException(404, "Version not found")
    return recipe_out(db, r, v, servings)


@router.get("/recipes/{recipe_id}/versions/{version_id}/review")
def version_review(recipe_id: uuid.UUID, version_id: uuid.UUID, servings: float | None = Query(None, gt=0),
                   participants: str | None = None, p: Principal = Depends(require("recipes:read")), db: Session = Depends(get_db)):
    """Stock requirements, allergen/person flags and food-link suggestions for a version (scaled, not mutated)."""
    v = db.get(RecipeVersion, version_id)
    if v is None or v.recipe_id != recipe_id or db.get(Recipe, recipe_id).household_id != p.household_id:
        raise HTTPException(404, "Version not found")
    s = servings or v.default_servings
    pids = [uuid.UUID(x) for x in participants.split(",")] if participants else None
    return {"servings": s, "requirements": requirements(db, p.household_id, v, s), "flags": review_flags(db, p.household_id, v, pids),
            "food_suggestions": {str(i.id): suggest_foods(db, p.household_id, i.name) for i in v.ingredients if not i.food_id}}


class RecipePatch(BaseModel):
    favourite: bool | None = None
    is_archived: bool | None = None
    current_version_id: uuid.UUID | None = None
    hero_media_id: uuid.UUID | None = None


@router.patch("/recipes/{recipe_id}")
def patch_recipe(recipe_id: uuid.UUID, body: RecipePatch, p: Principal = Depends(require("recipes:write")), db: Session = Depends(get_db)):
    r = get_owned(db, Recipe, recipe_id, p.household_id, "Recipe")
    data = body.model_dump(exclude_unset=True)
    if "current_version_id" in data:
        v = db.get(RecipeVersion, data["current_version_id"])
        if v is None or v.recipe_id != r.id or v.status != "published":
            raise HTTPException(422, "Choose a published version of this recipe")
        r.current_version_id, r.title = v.id, v.title
    if "hero_media_id" in data:
        m = db.get(RecipeMedia, data["hero_media_id"]) if data["hero_media_id"] else None
        if data["hero_media_id"] and (m is None or m.recipe_id != r.id):
            raise HTTPException(422, "Unknown image")
        r.hero_media_id = m.id if m else None
    for k in ("favourite", "is_archived"):
        if k in data:
            setattr(r, k, data[k])
    db.commit()
    return recipe_out(db, r)


@router.delete("/recipes/{recipe_id}")
def delete_recipe(recipe_id: uuid.UUID, p: Principal = Depends(require("recipes:write")), db: Session = Depends(get_db)):
    r = get_owned(db, Recipe, recipe_id, p.household_id, "Recipe")
    paths = [m.path for m in db.scalars(select(RecipeMedia).where(RecipeMedia.recipe_id == r.id))]
    r.current_version_id = None
    r.hero_media_id = None
    db.flush()
    db.delete(r)
    audit(db, p, "recipe.deleted", "recipe", recipe_id)
    db.commit()
    for path in set(paths):
        delete_media(path)
    return {"ok": True}


# ---- change proposals & versions ---------------------------------------------------------------------------------

class ProposeIn(BaseModel):
    instruction: str | None = Field(None, max_length=2000)
    base_version_id: uuid.UUID | None = None
    servings: float | None = Field(None, gt=0, le=100)


@router.post("/recipes/{recipe_id}/propose-change", status_code=202)
def propose_change(recipe_id: uuid.UUID, body: ProposeIn, p: Principal = Depends(require("recipes:write")), db: Session = Depends(get_db)):
    """AI (or deterministic scaling) creates a *draft* version with a before/after diff. Nothing is published."""
    r = get_owned(db, Recipe, recipe_id, p.household_id, "Recipe")
    base_id = body.base_version_id or r.current_version_id
    rate_limit(db, f"import:{p.household_id}", get_settings().import_rate_per_minute)
    job = enqueue(db, "recipe_change", {"recipe_id": str(r.id), "base_version_id": str(base_id), "instruction": body.instruction,
                                        "servings": body.servings}, household_id=p.household_id, user_id=p.user_id, max_attempts=1)
    return job_out(job)


class VersionIn(BaseModel):
    draft_version_id: uuid.UUID | None = None
    base_version_id: uuid.UUID | None = None
    content: RecipeContent | None = None
    label: str | None = Field(None, max_length=120)
    mode: str = Field("new", pattern="^(new|overwrite)$")
    make_current: bool = True
    change_request: str | None = None
    change_summary: str | None = None


@router.post("/recipes/{recipe_id}/versions", status_code=201)
def create_version(recipe_id: uuid.UUID, body: VersionIn, p: Principal = Depends(require("recipes:write")), db: Session = Depends(get_db)):
    """Publish a reviewed draft or edited content as a new immutable version.

    mode=new (default) keeps the base as a separate named version; mode=overwrite takes over the base's label and
    becomes current. Either way, earlier versions remain in the history.
    """
    r = get_owned(db, Recipe, recipe_id, p.household_id, "Recipe")
    base = db.get(RecipeVersion, body.base_version_id or r.current_version_id)
    if base is None or base.recipe_id != r.id:
        raise HTTPException(422, "Unknown base version")
    if body.draft_version_id and body.content is None:
        d = db.get(RecipeVersion, body.draft_version_id)
        if d is None or d.recipe_id != r.id or d.status != "draft":
            raise HTTPException(422, "Draft not found or already published")
        v = d
        if body.label:
            v.label = body.label
        if body.mode == "overwrite":
            v.label = base.label
        v.status, v.published_at = "published", utcnow()
    else:
        if body.content is None:
            raise HTTPException(422, "Send the edited content or a draft id")
        label = base.label if body.mode == "overwrite" else (body.label or "Edited")
        v = build_version(db, r, body.content, household_id=p.household_id, label=label, parent=base, user_id=p.user_id,
                          change_request=body.change_request, change_summary=body.change_summary)
        if body.draft_version_id:
            _discard_draft(db, r, body.draft_version_id)
    if body.make_current or body.mode == "overwrite":
        r.current_version_id, r.title = v.id, v.title
    audit(db, p, "recipe.version_published", "recipe_version", v.id, {"mode": body.mode, "label": v.label})
    db.commit()
    return recipe_out(db, r, v)


def _discard_draft(db: Session, r: Recipe, vid: uuid.UUID) -> None:
    d = db.get(RecipeVersion, vid)
    if d and d.recipe_id == r.id and d.status == "draft":
        db.delete(d)
        db.flush()


@router.put("/recipes/{recipe_id}/drafts/{version_id}")
def update_draft(recipe_id: uuid.UUID, version_id: uuid.UUID, content: RecipeContent, p: Principal = Depends(require("recipes:write")),
                 db: Session = Depends(get_db)):
    """Replace a draft's content (user edits to an AI proposal). The diff is recomputed."""
    r = get_owned(db, Recipe, recipe_id, p.household_id, "Recipe")
    d = db.get(RecipeVersion, version_id)
    if d is None or d.recipe_id != r.id or d.status != "draft":
        raise HTTPException(404, "Draft not found")
    parent = db.get(RecipeVersion, d.parent_version_id) if d.parent_version_id else None
    label, req, summary, extra = d.label, d.change_request, d.change_summary, {k: (d.diff or {}).get(k) for k in ("review_notes", "possible_issues")}
    db.delete(d)
    db.flush()
    nd = build_version(db, r, content, household_id=p.household_id, label=label, parent=parent, user_id=p.user_id, status="draft",
                       change_request=req, change_summary=summary)
    nd.diff = {**(nd.diff or {}), **{k: v for k, v in extra.items() if v}}
    db.commit()
    return version_out(db, nd)


@router.delete("/recipes/{recipe_id}/drafts/{version_id}")
def delete_draft(recipe_id: uuid.UUID, version_id: uuid.UUID, p: Principal = Depends(require("recipes:write")), db: Session = Depends(get_db)):
    r = get_owned(db, Recipe, recipe_id, p.household_id, "Recipe")
    _discard_draft(db, r, version_id)
    db.commit()
    return {"ok": True}


@router.get("/recipes/{recipe_id}/versions/{version_id}/content")
def version_content(recipe_id: uuid.UUID, version_id: uuid.UUID, p: Principal = Depends(require("recipes:read")), db: Session = Depends(get_db)):
    """Editable content of a version (to start a manual edit)."""
    r = get_owned(db, Recipe, recipe_id, p.household_id, "Recipe")
    v = db.get(RecipeVersion, version_id)
    if v is None or v.recipe_id != r.id:
        raise HTTPException(404, "Version not found")
    return content_from_version(v)


# ---- media -----------------------------------------------------------------------------------------------------------

@router.post("/recipes/{recipe_id}/media", status_code=201)
async def upload_media(recipe_id: uuid.UUID, file: UploadFile = File(...), kind: str = Form("photo"),
                       version_id: uuid.UUID | None = Form(None), step_position: int | None = Form(None),
                       make_hero: bool = Form(False), alt_text: str | None = Form(None), is_ai_generated: bool = Form(False),
                       p: Principal = Depends(require("recipes:write")), db: Session = Depends(get_db)):
    """Household photo for the dish or a step. Media is not versioned content, so this works on published versions."""
    r = get_owned(db, Recipe, recipe_id, p.household_id, "Recipe")
    data = await file.read(get_settings().max_upload_mb * 1024 * 1024 + 1)
    if len(data) > get_settings().max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "File is too large")
    if version_id is not None:
        v = db.get(RecipeVersion, version_id)
        if v is None or v.recipe_id != r.id:
            raise HTTPException(422, "Unknown version")
    rel, w, h = store_image(data, f"recipes/{r.id}")
    m = RecipeMedia(recipe_id=r.id, household_id=p.household_id, kind="step" if step_position is not None else kind, path=rel,
                    width=w, height=h, attribution="Household photo" if not is_ai_generated else "AI-generated illustration",
                    is_ai_generated=is_ai_generated, provenance="ai" if is_ai_generated else "manual", alt_text=alt_text,
                    version_id=version_id if step_position is not None else None, step_position=step_position)
    db.add(m)
    db.flush()
    if make_hero or (r.hero_media_id is None and step_position is None):
        if is_ai_generated:
            raise HTTPException(422, "An AI illustration can't be the dish photo")
        r.hero_media_id = m.id
    db.commit()
    return media_out(m)


class MediaPatch(BaseModel):
    focal_x: float | None = Field(None, ge=0, le=1)
    focal_y: float | None = Field(None, ge=0, le=1)
    alt_text: str | None = None


@router.patch("/recipes/{recipe_id}/media/{media_id}")
def patch_media(recipe_id: uuid.UUID, media_id: uuid.UUID, body: MediaPatch, p: Principal = Depends(require("recipes:write")),
                db: Session = Depends(get_db)):
    m = db.get(RecipeMedia, media_id)
    if m is None or m.recipe_id != recipe_id or m.household_id != p.household_id:
        raise HTTPException(404, "Image not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(m, k, v)
    db.commit()
    return media_out(m)


@router.delete("/recipes/{recipe_id}/media/{media_id}")
def delete_recipe_media(recipe_id: uuid.UUID, media_id: uuid.UUID, p: Principal = Depends(require("recipes:write")),
                        db: Session = Depends(get_db)):
    m = db.get(RecipeMedia, media_id)
    if m is None or m.recipe_id != recipe_id or m.household_id != p.household_id:
        raise HTTPException(404, "Image not found")
    path = m.path
    db.delete(m)
    db.commit()
    if not db.scalar(select(func.count()).select_from(RecipeMedia).where(RecipeMedia.path == path)):
        delete_media(path)
    return {"ok": True}


# ---- nutrition ---------------------------------------------------------------------------------------------------------

@router.get("/nutrition/recipes/{version_id}")
def nutrition_for_version(version_id: uuid.UUID, servings: float | None = Query(None, gt=0, le=100),
                          p: Principal = Depends(require("recipes:read")), db: Session = Depends(get_db)):
    v = db.get(RecipeVersion, version_id)
    if v is None or db.get(Recipe, v.recipe_id).household_id != p.household_id:
        raise HTTPException(404, "Version not found")
    return recipe_nutrition(db, v, servings)


@router.get("/recipes/{recipe_id}/scaled")
def scaled(recipe_id: uuid.UUID, servings: float = Query(gt=0, le=100), version_id: uuid.UUID | None = None,
           p: Principal = Depends(require("recipes:read")), db: Session = Depends(get_db)):
    """Ingredient amounts for a planned batch. The saved base version is never changed."""
    r = get_owned(db, Recipe, recipe_id, p.household_id, "Recipe")
    v = db.get(RecipeVersion, version_id or r.current_version_id)
    if v is None or v.recipe_id != r.id:
        raise HTTPException(404, "Version not found")
    return {"version_id": sid(v.id), "servings": servings, "base_servings": v.default_servings,
            "ingredients": scaled_ingredients(v, servings)}


@router.post("/foods/{food_id}/confirm-conversion")
def confirm_conversion(food_id: uuid.UUID, body: dict, p: Principal = Depends(require("recipes:write")), db: Session = Depends(get_db)):
    """Record an ingredient-specific factor, e.g. {"unit": "cup", "grams": 125} or {"unit": "each", "grams": 150}."""
    f = get_owned(db, Food, food_id, p.household_id, "Food")
    unit, grams = body.get("unit"), body.get("grams")
    if not unit or not isinstance(grams, (int, float)) or grams <= 0:
        raise HTTPException(422, "Give a unit and a positive weight in grams")
    if unit == "each":
        f.each_weight_g, f.each_weight_confirmed = float(grams), True
    else:
        f.conversions = {**(f.conversions or {}), unit: {"g": float(grams), "source": "confirmed by household", "at": utcnow().isoformat()}}
    audit(db, p, "food.conversion_confirmed", "food", f.id, {"unit": unit, "grams": grams})
    db.commit()
    return {"id": sid(f.id), "conversions": f.conversions, "each_weight_g": f.each_weight_g, "each_weight_confirmed": f.each_weight_confirmed,
            "display": f"1 {unit} = {uk_display(float(grams), 'g')}"}
