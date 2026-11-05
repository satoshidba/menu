"""Today dashboard, prices, media serving, admin (jobs, backups, export/import, audit), health."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..auth import Principal, audit, current_principal, require, require_admin, require_user
from ..config import get_settings
from ..db import get_db
from ..models import (AuditEvent, Food, ImportJob, MealPlan, PantryLot, PersonProfile, Product, Recipe, RecipeVersion,
                      User, utcnow)
from ..services import ai, backup, export, prices
from ..services.jobs import job_out
from ..services.media import abs_media
from ..services.stock import relevant_date
from ..services.units import uk_display
from .common import get_owned, iso, sid
from .pantry import not_health
from .planning import plan_out

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/health", include_in_schema=False)
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/today")
def today(p: Principal = Depends(require("pantry:read")), db: Session = Depends(get_db)):
    """Home: next planned meal, use-soon items, low stock and quick suggestions."""
    t = date.today()
    nxt = db.scalar(select(MealPlan).where(MealPlan.household_id == p.household_id, MealPlan.status == "planned",
                                           MealPlan.date >= t).order_by(MealPlan.date, MealPlan.slot))
    soon_cut = t + timedelta(days=3)
    lots = list(db.scalars(select(PantryLot).where(PantryLot.household_id == p.household_id, PantryLot.status == "active",
                                                   PantryLot.is_pet_food.is_(False), not_health(),
                                                   (PantryLot.use_by <= soon_cut) | (PantryLot.best_before <= soon_cut))))
    use_soon = []
    for lot in sorted(lots, key=lambda l: relevant_date(l)):
        rd = relevant_date(lot)
        use_soon.append({"lot_id": sid(lot.id), "name": lot.product.name if lot.product else lot.food.name,
                         "display": uk_display(lot.quantity_base, lot.base_unit), "location": lot.location,
                         "date": iso(rd), "date_kind": "use_by" if lot.use_by == rd else "best_before",
                         "date_label": lot.date_label, "days_left": (rd - t).days})
    # low stock: aggregate per product/food with a threshold
    low = []
    for model in (Product, Food):
        q = select(model).where(model.household_id == p.household_id, model.min_stock_base.is_not(None))
        if model is Product:
            q = q.where(Product.kind.not_in(("supplement", "medicine")))
        for item in db.scalars(q):
            col = PantryLot.product_id if model is Product else PantryLot.food_id
            have = db.scalar(select(func.coalesce(func.sum(PantryLot.quantity_base), 0)).where(col == item.id, PantryLot.status == "active"))
            if have <= item.min_stock_base:
                unit = item.base_unit if model is Product else item.default_unit
                low.append({"id": sid(item.id), "kind": model.__name__.lower(), "name": item.name, "have": uk_display(have, unit),
                            "minimum": uk_display(item.min_stock_base, unit)})
    from .recipes import list_recipes
    ideas = list_recipes(q=None, tag=None, max_minutes=None, cook_with_what_we_have=True, include_archived=False, limit=4, p=p, db=db)
    week_end = t + timedelta(days=6)
    planned = db.scalar(select(func.count()).select_from(MealPlan).where(MealPlan.household_id == p.household_id,
                                                                       MealPlan.date >= t, MealPlan.date <= week_end))
    from ..services.absences import absences_between
    from .planning import _names, absence_out
    names = _names(db, p.household_id)
    away_today = [{**absence_out(a, names), "date": iso(d)} for d in (t, t + timedelta(days=1))
                  for a in absences_between(db, p.household_id, d, d) if a.covers_day(d)]
    return {"date": iso(t), "next_meal": plan_out(db, nxt, with_requirements=True) if nxt else None, "use_soon": use_soon[:12],
            "away": away_today,
            "health": _health_summary(db, p),
            "stocktake_due": _stocktake_due(db, p),
            "low_stock": low[:12], "ideas": ideas, "planned_this_week": planned,
            "counts": {"recipes": db.scalar(select(func.count()).select_from(Recipe).where(Recipe.household_id == p.household_id)),
                       "pantry_lots": db.scalar(select(func.count()).select_from(PantryLot).where(PantryLot.household_id == p.household_id, PantryLot.status == "active"))}}


def _stocktake_due(db: Session, p: Principal) -> list[dict]:
    """Locations with stock that haven't been counted for 30 days (or ever)."""
    from .pantry import _last_counted
    last = _last_counted(db, p.household_id)
    locs = {r[0] for r in db.execute(select(PantryLot.location).where(PantryLot.household_id == p.household_id, PantryLot.status == "active",
                                                                      not_health()).distinct())}
    out = []
    for loc in sorted(locs):
        when = last.get(loc)
        days = (utcnow() - datetime.fromisoformat(when)).days if when else None
        if days is None or days >= 30:
            out.append({"location": loc, "days_since": days})
    return out


def _health_summary(db: Session, p: Principal) -> dict | None:
    if not p.has("health:read"):
        return None
    from ..services.health import due_today
    due = due_today(db, p.household_id, lambda person: p.can_read_person_intake(person, "health:read"))
    return {"due": [d for d in due if d["remaining_today"] > 0], "given_today": sum(d["given_today"] for d in due)}


@router.get("/prices/history")
def price_history(productId: uuid.UUID | None = None, foodId: uuid.UUID | None = None, p: Principal = Depends(require("pantry:read")),
                  db: Session = Depends(get_db)):
    if not (productId or foodId):
        raise HTTPException(422, "Give productId or foodId")
    if productId:
        get_owned(db, Product, productId, p.household_id, "Product")
    if foodId:
        get_owned(db, Food, foodId, p.household_id, "Food")
    return prices.history(db, p.household_id, product_id=productId, food_id=foodId)


@router.get("/media/{path:path}", include_in_schema=False)
def media(path: str, request: Request, p: Principal = Depends(current_principal)):
    """Household media is private: served only to signed-in users or tokens of the same household."""
    parts = path.split("/")
    if len(parts) < 2:
        raise HTTPException(404)
    f = abs_media(path)
    if not f.is_file():
        raise HTTPException(404)
    # media paths are namespaced by recipe/product/staging id; check ownership for products (household in path)
    if parts[0] == "products" and parts[1] != str(p.household_id):
        raise HTTPException(404)
    if parts[0] == "recipes":
        from ..db import SessionLocal
        db = SessionLocal()
        try:
            r = db.get(Recipe, uuid.UUID(parts[1]))
            if r is None or r.household_id != p.household_id:
                raise HTTPException(404)
        except ValueError as e:
            raise HTTPException(404) from e
        finally:
            db.close()
    return FileResponse(f, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=604800, immutable"})


# ---- admin -------------------------------------------------------------------------------------------------------------

@router.get("/admin/status")
def admin_status(p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    s = get_settings()
    failed = db.scalar(select(func.count()).select_from(ImportJob).where(ImportJob.status == "failed",
                                                                         ImportJob.created_at > utcnow() - timedelta(days=7)))
    usage = db.execute(select(func.count(), func.coalesce(func.sum(AuditEvent.details["est_cost_usd"].as_float()), 0))
                       .where(AuditEvent.action == "ai.call", AuditEvent.at > utcnow() - timedelta(days=30))).first()
    return {"ai": {"enabled": s.ai_enabled, "model": s.ai_model if s.ai_enabled else None, "calls_30d": usage[0],
                   "est_cost_usd_30d": round(usage[1] or 0, 4)},
            "barcode_lookup": s.off_enabled, "backups": {"enabled": s.backup_enabled, "encrypted": bool(s.backup_passphrase),
                                                         "keep": s.backup_keep, "latest": (backup.list_backups() or [None])[0]},
            "failed_jobs_7d": failed, "public_url": s.public_url}


@router.get("/admin/jobs")
def admin_jobs(status: str | None = None, limit: int = Query(50, le=200), p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    q = select(ImportJob).where((ImportJob.household_id == p.household_id) | ImportJob.household_id.is_(None))
    if status:
        q = q.where(ImportJob.status == status)
    return [{**job_out(j), "result": None} for j in db.scalars(q.order_by(ImportJob.created_at.desc()).limit(limit))]


@router.post("/admin/jobs/{job_id}/retry")
def retry_job(job_id: uuid.UUID, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    j = db.get(ImportJob, job_id)
    if j is None or (j.household_id not in (None, p.household_id)):
        raise HTTPException(404, "Job not found")
    if j.status != "failed":
        raise HTTPException(409, "Only failed jobs can be retried")
    j.status, j.error, j.run_after, j.max_attempts = "queued", None, utcnow(), j.attempts + 1
    audit(db, p, "job.retried", "import_job", j.id)
    db.commit()
    if get_settings().inline_jobs:
        from ..services.jobs import run_job
        run_job(j.id)
        db.refresh(j)
    return job_out(j)


@router.get("/admin/backups")
def admin_backups(p: Principal = Depends(require_admin)):
    return backup.list_backups()


@router.post("/admin/backups", status_code=201)
def admin_backup_now(p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    from ..services.jobs import enqueue
    job = enqueue(db, "backup", {"label": "manual"}, household_id=p.household_id, user_id=p.user_id, max_attempts=1)
    audit(db, p, "backup.requested", "import_job", job.id)
    db.commit()
    return job_out(job)


@router.get("/admin/backups/{name}")
def admin_backup_download(name: str, p: Principal = Depends(require_admin)):
    if "/" in name or ".." in name or not name.startswith("larder-"):
        raise HTTPException(404)
    f = get_settings().backups_dir / name
    if not f.is_file():
        raise HTTPException(404)
    return FileResponse(f, media_type="application/gzip", filename=name)


@router.get("/admin/audit")
def admin_audit(limit: int = Query(100, le=500), action: str | None = None, p: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    q = select(AuditEvent).where(AuditEvent.household_id == p.household_id)
    if action:
        q = q.where(AuditEvent.action == action)
    return [{"id": sid(a.id), "action": a.action, "entity_type": a.entity_type, "entity_id": a.entity_id, "details": a.details,
             "actor_user_id": sid(a.actor_user_id), "actor_token_id": sid(a.actor_token_id), "at": iso(a.at)}
            for a in db.scalars(q.order_by(AuditEvent.at.desc()).limit(limit))]


@router.get("/export")
def household_export(include_files: bool = True, p: Principal = Depends(require("export:read")), db: Session = Depends(get_db)):
    """Full household export: JSON + CSV per table, plus media and receipt originals (zip)."""
    if not p.is_user and "export:read" not in p.scopes:
        raise HTTPException(403)
    blob = export.build_export(db, include_files)
    audit(db, p, "household.exported", "household", p.household_id, {"include_files": include_files})
    db.commit()
    name = f"larder-export-{date.today().isoformat()}.zip"
    return Response(blob, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/setup/restore", status_code=201)
async def restore_from_export(file: UploadFile = File(...), admin_email: str = Form(...), admin_password: str = Form(...),
                              db: Session = Depends(get_db)):
    """Restore a household export into a fresh instance (only before setup)."""
    if db.scalar(select(func.count()).select_from(User)):
        raise HTTPException(409, "This instance already has users")
    blob = await file.read(1024 * 1024 * 1024)
    try:
        return export.import_export(db, blob, admin_email, admin_password)
    except RuntimeError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/status/features")
def features(p: Principal = Depends(require_user)):
    s = get_settings()
    return {"ai": ai.available(), "barcode_lookup": s.off_enabled, "ocr": _has_tesseract()}


def _has_tesseract() -> bool:
    import shutil
    return shutil.which("tesseract") is not None


@router.get("/people/summary")
def people_summary(p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    return {"adults": db.scalar(select(func.count()).select_from(PersonProfile).where(PersonProfile.household_id == p.household_id, PersonProfile.kind == "adult")),
            "children": db.scalar(select(func.count()).select_from(PersonProfile).where(PersonProfile.household_id == p.household_id, PersonProfile.kind == "child"))}


_ = RecipeVersion
