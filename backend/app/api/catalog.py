"""Foods, products, barcodes, nutrition records and CoFID reference data."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..auth import Principal, audit, require
from ..config import get_settings
from ..db import get_db
from ..models import Barcode, CofidFood, Food, NutritionRecord, Product, utcnow
from ..services import off
from ..services.media import store_image
from ..services.nutrition import NUTRIENTS, clean_values
from ..services.nutrition_db import best_record, record_dict
from ..services.safe_fetch import FetchError, fetch
from ..services.units import FoodFactors, canonical_unit, convert, unit_kind
from .common import get_owned, iso, sid

router = APIRouter(prefix="/api/v1", tags=["catalog"])


def food_out(f: Food, db: Session | None = None, with_nutrition: bool = False) -> dict:
    out = {"id": sid(f.id), "name": f.name, "aliases": f.aliases or [], "category": f.category, "default_unit": f.default_unit,
           "density_g_per_ml": f.density_g_per_ml, "each_weight_g": f.each_weight_g,
           "each_weight_confirmed": f.each_weight_confirmed, "conversions": f.conversions or {},
           "allergens": f.allergens or [], "cofid_code": f.cofid_code, "is_pet_food": f.is_pet_food,
           "min_stock_base": f.min_stock_base, "notes": f.notes, "updated_at": iso(f.updated_at)}
    if with_nutrition and db is not None:
        out["nutrition"] = record_dict(best_record(db, food_id=f.id))
    return out


def product_out(p: Product, db: Session | None = None, with_nutrition: bool = False) -> dict:
    out = {"id": sid(p.id), "food_id": sid(p.food_id), "food_name": p.food.name if p.food else None, "name": p.name,
           "brand": p.brand, "package_quantity": p.package_quantity, "package_unit": p.package_unit,
           "package_base_quantity": p.package_base_quantity, "base_unit": p.base_unit,
           "image_url": f"/api/v1/media/{p.image_path}" if p.image_path else None, "image_source": p.image_source,
           "ingredients_text": p.ingredients_text, "allergens": p.allergens or [], "traces": p.traces or [],
           "category": p.category, "is_pet_food": p.is_pet_food, "pet_profile_id": sid(p.pet_profile_id),
           "provenance": p.provenance, "review_status": p.review_status, "min_stock_base": p.min_stock_base,
           "barcodes": [b.code for b in p.barcodes], "updated_at": iso(p.updated_at)}
    if with_nutrition and db is not None:
        out["nutrition"] = record_dict(best_record(db, product_id=p.id)) if not p.is_pet_food else None
    return out


# ---- foods ---------------------------------------------------------------------------------------------------------

class FoodIn(BaseModel):
    name: str = Field(max_length=160)
    aliases: list[str] = []
    category: str | None = None
    default_unit: str = Field("g", pattern="^(g|ml|each)$")
    density_g_per_ml: float | None = Field(None, gt=0, lt=5)
    each_weight_g: float | None = Field(None, gt=0)
    each_weight_confirmed: bool = False
    conversions: dict = {}
    allergens: list[str] = []
    cofid_code: str | None = None
    is_pet_food: bool = False
    min_stock_base: float | None = Field(None, ge=0)
    notes: str | None = None


@router.get("/foods")
def list_foods(q: str | None = None, include_pet: bool = False, limit: int = Query(50, le=500),
               p: Principal = Depends(require("recipes:read")), db: Session = Depends(get_db)):
    stmt = select(Food).where(Food.household_id == p.household_id)
    if not include_pet:
        stmt = stmt.where(Food.is_pet_food.is_(False))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Food.name).like(like), func.array_to_string(Food.aliases, " ").ilike(like)))
        stmt = stmt.order_by(func.similarity(Food.name, q).desc())
    else:
        stmt = stmt.order_by(Food.name)
    return [food_out(f) for f in db.scalars(stmt.limit(limit))]


def find_or_create_food(db: Session, household_id: uuid.UUID, name: str, unit: str | None = None) -> Food:
    name = name.strip()[:160]
    f = db.scalar(select(Food).where(Food.household_id == household_id, func.lower(Food.name) == name.lower()))
    if f is None:
        f = db.scalar(select(Food).where(Food.household_id == household_id, func.lower(func.array_to_string(Food.aliases, "|")).contains(name.lower())))
    if f is None:
        kind = unit_kind(unit)
        f = Food(household_id=household_id, name=name, default_unit={"mass": "g", "volume": "ml", "count": "each"}.get(kind or "", "g"))
        db.add(f)
        db.flush()
    return f


@router.post("/foods", status_code=201)
def create_food(body: FoodIn, p: Principal = Depends(require("pantry:write")), db: Session = Depends(get_db)):
    if db.scalar(select(Food).where(Food.household_id == p.household_id, func.lower(Food.name) == body.name.lower())):
        raise HTTPException(409, "A food with that name already exists")
    f = Food(household_id=p.household_id, **body.model_dump())
    db.add(f)
    db.flush()
    if body.cofid_code:
        _map_cofid(db, p, f, body.cofid_code)
    db.commit()
    return food_out(f, db, True)


@router.get("/foods/{food_id}")
def get_food(food_id: uuid.UUID, p: Principal = Depends(require("recipes:read")), db: Session = Depends(get_db)):
    return food_out(get_owned(db, Food, food_id, p.household_id, "Food"), db, True)


@router.patch("/foods/{food_id}")
def update_food(food_id: uuid.UUID, body: dict, p: Principal = Depends(require("pantry:write")), db: Session = Depends(get_db)):
    f = get_owned(db, Food, food_id, p.household_id, "Food")
    data = FoodIn(**{**food_out(f), **body}).model_dump()
    old_code = f.cofid_code
    for k, v in data.items():
        setattr(f, k, v)
    if data.get("cofid_code") and data["cofid_code"] != old_code:
        _map_cofid(db, p, f, data["cofid_code"])
    db.commit()
    return food_out(f, db, True)


@router.get("/foods/{food_id}/convert")
def convert_for_food(food_id: uuid.UUID, quantity: float, unit: str, to: str,
                     p: Principal = Depends(require("recipes:read")), db: Session = Depends(get_db)):
    f = get_owned(db, Food, food_id, p.household_id, "Food")
    u, t = canonical_unit(unit), canonical_unit(to)
    if not u or not t:
        raise HTTPException(422, "Unknown unit")
    c = convert(quantity, u, t, FoodFactors.from_food(f))
    return {"quantity": c.quantity, "unit": c.unit, "factor": c.factor, "note": c.note, "needs_confirmation": c.needs_confirmation}


# ---- CoFID ---------------------------------------------------------------------------------------------------------

@router.get("/nutrition/cofid")
def search_cofid(q: str, limit: int = Query(20, le=100), p: Principal = Depends(require("recipes:read")), db: Session = Depends(get_db)):
    stmt = (select(CofidFood).where(CofidFood.name.ilike(f"%{q}%") | (func.similarity(CofidFood.name, q) > 0.2))
            .order_by(func.similarity(CofidFood.name, q).desc()).limit(limit))
    return [{"code": c.code, "name": c.name, "description": c.description, "dataset_version": c.dataset_version,
             "nutrients": c.nutrients, "flags": c.flags} for c in db.scalars(stmt)]


def _map_cofid(db: Session, p: Principal, food: Food, code: str) -> NutritionRecord:
    ref = db.scalar(select(CofidFood).where(CofidFood.code == code).order_by(CofidFood.dataset_version.desc()))
    if ref is None:
        raise HTTPException(404, f"CoFID code {code} not found; import the dataset first")
    for old in db.scalars(select(NutritionRecord).where(NutritionRecord.food_id == food.id, NutritionRecord.source == "cofid",
                                                        NutritionRecord.is_current.is_(True))):
        old.is_current = False
    food.cofid_code = code
    rec = NutritionRecord(household_id=p.household_id, food_id=food.id, source="cofid", source_ref=code,
                          source_version=ref.dataset_version, retrieved_at=utcnow(), basis="100g",
                          nutrients=dict(ref.nutrients), flags=dict(ref.flags or {}), verified=False, confidence=0.8,
                          notes=f"CoFID: {ref.name}")
    db.add(rec)
    audit(db, p, "food.cofid_mapped", "food", food.id, {"code": code})
    return rec


class CofidMapIn(BaseModel):
    code: str


@router.post("/foods/{food_id}/cofid")
def map_cofid(food_id: uuid.UUID, body: CofidMapIn, p: Principal = Depends(require("pantry:write")), db: Session = Depends(get_db)):
    f = get_owned(db, Food, food_id, p.household_id, "Food")
    _map_cofid(db, p, f, body.code)
    db.commit()
    return food_out(f, db, True)


# ---- nutrition records ---------------------------------------------------------------------------------------------

class NutritionIn(BaseModel):
    food_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    source: str = Field("label", pattern="^(label|manual|open_food_facts|ai)$")
    basis: str = Field("100g", pattern="^(100g|100ml)$")
    serving_size_g: float | None = None
    nutrients: dict[str, float | None]
    verified: bool = False
    source_ref: str | None = None
    notes: str | None = None


def save_nutrition(db: Session, household_id: uuid.UUID, body: NutritionIn) -> NutritionRecord:
    unknown = set(body.nutrients) - set(NUTRIENTS)
    if unknown:
        raise HTTPException(422, f"Unknown nutrient keys: {', '.join(sorted(unknown))}")
    if bool(body.food_id) == bool(body.product_id):
        raise HTTPException(422, "Give exactly one of food_id or product_id")
    if body.product_id:
        prod = get_owned(db, Product, body.product_id, household_id, "Product")
        if prod.is_pet_food:
            raise HTTPException(422, "Pet food does not take human nutrition records")
    else:
        get_owned(db, Food, body.food_id, household_id, "Food")
    # Supersede earlier records from the same source for the same target
    q = select(NutritionRecord).where(NutritionRecord.source == body.source, NutritionRecord.is_current.is_(True))
    q = q.where(NutritionRecord.product_id == body.product_id) if body.product_id else q.where(NutritionRecord.food_id == body.food_id)
    for old in db.scalars(q):
        old.is_current = False
    values, derived = clean_values(body.nutrients)
    rec = NutritionRecord(household_id=household_id, food_id=body.food_id, product_id=body.product_id, source=body.source,
                          source_ref=body.source_ref, retrieved_at=utcnow(), basis=body.basis,
                          serving_size_g=body.serving_size_g,
                          nutrients={k: body.nutrients.get(k) for k in NUTRIENTS if k in body.nutrients},
                          flags=derived, verified=body.verified and body.source in ("label", "manual"),
                          confidence=0.95 if body.verified else 0.6, notes=body.notes)
    db.add(rec)
    return rec


@router.post("/nutrition/records", status_code=201)
def create_nutrition_record(body: NutritionIn, p: Principal = Depends(require("pantry:write")), db: Session = Depends(get_db)):
    rec = save_nutrition(db, p.household_id, body)
    audit(db, p, "nutrition.recorded", "nutrition_record", None, {"source": body.source, "verified": body.verified})
    db.commit()
    return record_dict(rec)


@router.get("/nutrition/foods/{food_id}")
def food_nutrition(food_id: uuid.UUID, p: Principal = Depends(require("recipes:read")), db: Session = Depends(get_db)):
    """Nutrition for a food *or* product id, with provenance. Unknown values are null, never zero."""
    f = db.get(Food, food_id)
    if f is not None and f.household_id == p.household_id:
        recs = db.scalars(select(NutritionRecord).where(NutritionRecord.food_id == f.id).order_by(NutritionRecord.retrieved_at.desc()))
        return {"food": food_out(f), "best": record_dict(best_record(db, food_id=f.id)), "records": [record_dict(r) for r in recs],
                "units": {k: v[1] for k, v in NUTRIENTS.items()}}
    prod = get_owned(db, Product, food_id, p.household_id, "Food or product")
    if prod.is_pet_food:
        return {"product": product_out(prod), "best": None, "records": [], "note": "Pet food has no human nutrition."}
    recs = db.scalars(select(NutritionRecord).where(NutritionRecord.product_id == prod.id).order_by(NutritionRecord.retrieved_at.desc()))
    return {"product": product_out(prod), "best": record_dict(best_record(db, product_id=prod.id)),
            "records": [record_dict(r) for r in recs], "units": {k: v[1] for k, v in NUTRIENTS.items()}}


# ---- products & barcodes -------------------------------------------------------------------------------------------

class ProductIn(BaseModel):
    name: str = Field(max_length=200)
    brand: str | None = None
    food_id: uuid.UUID | None = None
    food_name: str | None = Field(None, description="Create/link a generic food by name if food_id is not given")
    package_quantity: float | None = Field(None, gt=0)
    package_unit: str | None = None
    ingredients_text: str | None = None
    allergens: list[str] = []
    traces: list[str] = []
    category: str | None = None
    is_pet_food: bool = False
    pet_profile_id: uuid.UUID | None = None
    barcode: str | None = None
    image_url: str | None = Field(None, description="Remote image to copy (e.g. from a barcode lookup)")
    image_source: str | None = None
    provenance: str = "manual"
    nutrition: dict | None = Field(None, description="{basis, nutrients{...}, verified}")
    min_stock_base: float | None = None


def _apply_package(prod: Product, quantity: float | None, unit: str | None) -> None:
    prod.package_quantity, prod.package_unit = quantity, unit
    cu = canonical_unit(unit) if unit else None
    kind = unit_kind(cu)
    if quantity and kind in ("mass", "volume"):
        base = "g" if kind == "mass" else "ml"
        prod.base_unit = base
        prod.package_base_quantity = convert(quantity, cu, base).quantity
    elif quantity and kind == "count":
        prod.base_unit = "each"
        prod.package_base_quantity = quantity
    else:
        prod.package_base_quantity = None
        prod.base_unit = prod.base_unit or "each"


def create_product(db: Session, p: Principal, body: ProductIn) -> Product:
    food = None
    if body.food_id:
        food = get_owned(db, Food, body.food_id, p.household_id, "Food")
    elif body.food_name:
        food = find_or_create_food(db, p.household_id, body.food_name, canonical_unit(body.package_unit or ""))
        if body.is_pet_food:
            food.is_pet_food = True
    prod = Product(household_id=p.household_id, food_id=food.id if food else None, name=body.name, brand=body.brand,
                   ingredients_text=body.ingredients_text, allergens=body.allergens, traces=body.traces,
                   category=body.category or (food.category if food else None), is_pet_food=body.is_pet_food,
                   pet_profile_id=body.pet_profile_id, provenance=body.provenance, image_source=body.image_source,
                   review_status="reviewed", min_stock_base=body.min_stock_base)
    _apply_package(prod, body.package_quantity, body.package_unit)
    if food and prod.base_unit == "each" and food.default_unit != "each" and not body.package_quantity:
        prod.base_unit = food.default_unit if food.default_unit != "each" else "each"
    db.add(prod)
    db.flush()
    if body.barcode:
        if not off.valid_barcode(body.barcode):
            raise HTTPException(422, "Barcode should be 6–14 digits")
        existing = db.scalar(select(Barcode).where(Barcode.household_id == p.household_id, Barcode.code == body.barcode))
        if existing:
            raise HTTPException(409, f"Barcode already belongs to '{existing.product.name}'")
        db.add(Barcode(household_id=p.household_id, product_id=prod.id, code=body.barcode))
    if body.image_url:
        try:
            got = fetch(body.image_url, accept="image/*", max_bytes=get_settings().max_fetch_mb * 1024 * 1024)
            if got.status == 200:
                prod.image_path, _, _ = store_image(got.body, f"products/{p.household_id}", max_side=800)
                prod.image_source = prod.image_source or body.image_url
        except (FetchError, HTTPException):
            pass  # image is optional; the user can add a photo later
    if body.nutrition and not body.is_pet_food and body.nutrition.get("nutrients"):
        save_nutrition(db, p.household_id, NutritionIn(
            product_id=prod.id, source=body.nutrition.get("source") or ("label" if body.provenance == "manual" else body.provenance),
            basis=body.nutrition.get("basis", "100g"), nutrients=body.nutrition["nutrients"],
            verified=bool(body.nutrition.get("verified")), source_ref=body.barcode))
    return prod


@router.post("/products", status_code=201)
def post_product(body: ProductIn, p: Principal = Depends(require("pantry:write")), db: Session = Depends(get_db)):
    prod = create_product(db, p, body)
    audit(db, p, "product.created", "product", prod.id, {"provenance": body.provenance})
    db.commit()
    db.refresh(prod)
    return product_out(prod, db, True)


@router.get("/products")
def list_products(q: str | None = None, food_id: uuid.UUID | None = None, pet: bool | None = None,
                  limit: int = Query(50, le=500), p: Principal = Depends(require("pantry:read")), db: Session = Depends(get_db)):
    stmt = select(Product).where(Product.household_id == p.household_id)
    if food_id:
        stmt = stmt.where(Product.food_id == food_id)
    if pet is not None:
        stmt = stmt.where(Product.is_pet_food.is_(pet))
    if q:
        stmt = stmt.where(or_(Product.name.ilike(f"%{q}%"), Product.brand.ilike(f"%{q}%"),
                              func.similarity(Product.name, q) > 0.25)).order_by(func.similarity(Product.name, q).desc())
    else:
        stmt = stmt.order_by(Product.name)
    return [product_out(x) for x in db.scalars(stmt.limit(limit))]


@router.get("/products/{product_id}")
def get_product(product_id: uuid.UUID, p: Principal = Depends(require("pantry:read")), db: Session = Depends(get_db)):
    return product_out(get_owned(db, Product, product_id, p.household_id, "Product"), db, True)


@router.patch("/products/{product_id}")
def update_product(product_id: uuid.UUID, body: dict, p: Principal = Depends(require("pantry:write")), db: Session = Depends(get_db)):
    prod = get_owned(db, Product, product_id, p.household_id, "Product")
    simple = {"name", "brand", "ingredients_text", "allergens", "traces", "category", "min_stock_base", "pet_profile_id"}
    for k in simple & body.keys():
        setattr(prod, k, body[k])
    if "food_id" in body:
        prod.food_id = get_owned(db, Food, body["food_id"], p.household_id, "Food").id if body["food_id"] else None
    if "package_quantity" in body or "package_unit" in body:
        _apply_package(prod, body.get("package_quantity", prod.package_quantity), body.get("package_unit", prod.package_unit))
    if body.get("barcode"):
        if not db.scalar(select(Barcode).where(Barcode.household_id == p.household_id, Barcode.code == body["barcode"])):
            db.add(Barcode(household_id=p.household_id, product_id=prod.id, code=body["barcode"]))
    prod.review_status = "reviewed"
    db.commit()
    db.refresh(prod)
    return product_out(prod, db, True)


@router.post("/products/{product_id}/photo")
async def product_photo(product_id: uuid.UUID, file: UploadFile = File(...), p: Principal = Depends(require("pantry:write")),
                        db: Session = Depends(get_db)):
    prod = get_owned(db, Product, product_id, p.household_id, "Product")
    data = await file.read(get_settings().max_upload_mb * 1024 * 1024 + 1)
    if len(data) > get_settings().max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "File is too large")
    prod.image_path, _, _ = store_image(data, f"products/{p.household_id}", max_side=1200)
    prod.image_source = "Household photo"
    db.commit()
    return product_out(prod)


@router.get("/barcodes/{code}")
def barcode_lookup(code: str, pet: bool = False, p: Principal = Depends(require("pantry:read")), db: Session = Depends(get_db)):
    """Known product in this household first; otherwise an unconfirmed draft from Open (Pet) Food Facts."""
    known = db.scalar(select(Barcode).where(Barcode.household_id == p.household_id, Barcode.code == code))
    if known:
        return {"status": "known", "product": product_out(known.product, db, True)}
    res = off.lookup(db, code, pet=pet)
    if res["found"]:
        return {"status": "draft", "draft": res["draft"], "source": res["source"], "warning": res.get("error")}
    return {"status": "unknown", "barcode": code, "message": res.get("error") or "Not found. Add it with a name and size.",
            "lookup_error": res.get("error")}
