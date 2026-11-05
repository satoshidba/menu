"""Open Food Facts / Open Pet Food Facts barcode lookup with a local cache.

Data is ODbL-licensed community data: it is cached in its own table, shown with attribution and treated as
unverified until a human confirms it against the label.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import ExternalProductCache, utcnow
from .units import canonical_unit, parse_quantity_text

log = logging.getLogger("larder.off")
FIELDS = ("code,product_name,product_name_en,generic_name,brands,quantity,product_quantity,product_quantity_unit,"
          "image_front_url,image_url,ingredients_text,ingredients_text_en,allergens_tags,traces_tags,nutriments,"
          "serving_size,categories_tags,last_modified_t")
CACHE_DAYS = 30
ATTRIBUTION = "Data from Open Food Facts (ODbL), community-contributed and unverified"


def valid_barcode(code: str) -> bool:
    return bool(re.fullmatch(r"\d{6,14}", code or ""))


def _lookup_remote(base: str, code: str) -> dict | None:
    s = get_settings()
    url = f"{base}/api/v2/product/{code}.json"
    try:
        r = httpx.get(url, params={"fields": FIELDS}, headers={"User-Agent": s.off_user_agent}, timeout=8.0)
    except httpx.HTTPError as e:
        log.warning("OFF lookup failed: %s", type(e).__name__)
        raise
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    if data.get("status") != 1:
        return None
    return data.get("product") or None


def lookup(db: Session, code: str, *, pet: bool = False) -> dict:
    """Return {"found": bool, "source": ..., "draft": {...} | None, "error": str | None}."""
    s = get_settings()
    source = "open_pet_food_facts" if pet else "open_food_facts"
    if not valid_barcode(code):
        return {"found": False, "source": source, "draft": None, "error": "Not a valid product barcode"}
    cached = db.scalar(select(ExternalProductCache).where(ExternalProductCache.source == source, ExternalProductCache.code == code))
    if cached and cached.fetched_at > utcnow() - timedelta(days=CACHE_DAYS):
        return {"found": cached.found, "source": source, "draft": draft_from_off(cached.payload, pet) if cached.found else None,
                "error": None, "cached": True}
    if not s.off_enabled:
        return {"found": False, "source": source, "draft": None, "error": "Online product lookup is turned off"}
    try:
        product = _lookup_remote(s.pet_off_base_url if pet else s.off_base_url, code)
    except Exception:
        if cached:
            return {"found": cached.found, "source": source, "draft": draft_from_off(cached.payload, pet) if cached.found else None,
                    "error": "Lookup service unavailable; showing cached data", "cached": True}
        return {"found": False, "source": source, "draft": None, "error": "Product lookup is unavailable right now"}
    if cached is None:
        cached = ExternalProductCache(source=source, code=code)
        db.add(cached)
    cached.found = product is not None
    cached.payload = product or {}
    cached.fetched_at = utcnow()
    db.commit()
    return {"found": cached.found, "source": source, "draft": draft_from_off(product, pet) if product else None, "error": None}


_NUTRIENT_MAP = {
    "energy-kcal_100g": "energy_kcal", "energy-kj_100g": "energy_kj", "proteins_100g": "protein_g",
    "carbohydrates_100g": "carbohydrate_g", "sugars_100g": "sugars_g", "fat_100g": "fat_g",
    "saturated-fat_100g": "saturates_g", "fiber_100g": "fibre_g", "salt_100g": "salt_g",
}


def _tags(tags) -> list[str]:
    return [t.split(":", 1)[-1].replace("-", " ") for t in (tags or []) if isinstance(t, str)]


def draft_from_off(p: dict, pet: bool = False) -> dict:
    qty, unit = None, None
    if p.get("product_quantity"):
        try:
            qty = float(p["product_quantity"])
            unit = canonical_unit(p.get("product_quantity_unit") or "g") or "g"
        except (TypeError, ValueError):
            qty = None
    if qty is None:
        qty, unit = parse_quantity_text(p.get("quantity") or "")
    nutriments = p.get("nutriments") or {}
    nutrients = {}
    for src, key in _NUTRIENT_MAP.items():
        v = nutriments.get(src)
        nutrients[key] = float(v) if isinstance(v, (int, float)) else None
    # OFF only gives sodium in g; convert to mg (known unit) when present.
    if isinstance(nutriments.get("sodium_100g"), (int, float)):
        nutrients["sodium_mg"] = float(nutriments["sodium_100g"]) * 1000
    basis = "100ml" if (unit in ("ml", "l", "cl")) else "100g"
    return {
        "barcode": p.get("code"),
        "name": p.get("product_name_en") or p.get("product_name") or p.get("generic_name") or "",
        "brand": (p.get("brands") or "").split(",")[0].strip() or None,
        "package_quantity": qty, "package_unit": unit,
        "image_url": p.get("image_front_url") or p.get("image_url"),
        "ingredients_text": p.get("ingredients_text_en") or p.get("ingredients_text"),
        "allergens": _tags(p.get("allergens_tags")), "traces": _tags(p.get("traces_tags")),
        "categories": _tags(p.get("categories_tags"))[:5],
        "is_pet_food": pet,
        "nutrition": None if pet else {"basis": basis, "nutrients": nutrients, "serving_size": p.get("serving_size")},
        "provenance": "open_pet_food_facts" if pet else "open_food_facts",
        "attribution": ATTRIBUTION.replace("Open Food Facts", "Open Pet Food Facts") if pet else ATTRIBUTION,
        "missing": [k for k, v in nutrients.items() if v is None] if not pet else [],
    }
