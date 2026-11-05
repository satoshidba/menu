"""Receipt upload, line-by-line review, apply (idempotent), original file deletion."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import Principal, audit, rate_limit, require
from ..config import get_settings
from ..db import get_db
from ..models import Food, Product, Receipt, ReceiptLine, utcnow
from ..services.jobs import enqueue, job_out
from ..services.media import RECEIPT_MIMES, receipt_abs, sniff_mime, store_receipt_file
from ..services.receipts import apply_receipt, blocking_issues, line_effective, reconciliation
from .catalog import ProductIn, create_product, find_or_create_food, product_out
from .common import get_owned, iso, sid

router = APIRouter(prefix="/api/v1/receipts", tags=["receipts"])


def line_out(db: Session, ln: ReceiptLine, eff: dict) -> dict:
    prod = db.get(Product, ln.product_id) if ln.product_id else None
    food = db.get(Food, ln.food_id) if ln.food_id else None
    e = eff.get(ln.id, {})
    return {"id": sid(ln.id), "position": ln.position, "raw_text": ln.raw_text, "description": ln.description, "kind": ln.kind,
            "quantity": ln.quantity, "weight_kg": ln.weight_kg, "unit_price_pence": ln.unit_price_pence,
            "line_total_pence": ln.line_total_pence, "discount_pence": e.get("discount", 0), "paid_pence": e.get("paid"),
            "promotions": e.get("promotions", []), "applies_to_line_id": sid(ln.applies_to_line_id), "corrected": ln.corrected,
            "original_parsed": ln.original_parsed, "confidence": ln.confidence, "match_status": ln.match_status,
            "product": {"id": sid(prod.id), "name": prod.name, "brand": prod.brand, "package_base_quantity": prod.package_base_quantity,
                        "base_unit": prod.base_unit, "image_url": f"/api/v1/media/{prod.image_path}" if prod.image_path else None} if prod else None,
            "food": {"id": sid(food.id), "name": food.name} if food else None, "is_substitution": ln.is_substitution,
            "substituted_for": ln.substituted_for, "create_lot": ln.create_lot, "location": ln.location,
            "best_before": iso(ln.best_before), "use_by": iso(ln.use_by)}


def receipt_out(db: Session, r: Receipt, full: bool = False) -> dict:
    out = {"id": sid(r.id), "status": r.status, "source_kind": r.source_kind, "retailer": r.retailer, "store_name": r.store_name,
           "purchased_at": iso(r.purchased_at), "receipt_number": r.receipt_number, "total_pence": r.total_pence,
           "payment_total_pence": r.payment_total_pence, "has_file": bool(r.file_path), "file_mime": r.file_mime,
           "file_deleted_at": iso(r.file_deleted_at), "parser": r.parser, "parse_confidence": r.parse_confidence,
           "duplicate_of_id": sid(r.duplicate_of_id), "applied_at": iso(r.applied_at), "created_at": iso(r.created_at),
           "line_count": len(r.lines)}
    if full:
        eff = line_effective(r)
        out.update(lines=[line_out(db, ln, eff) for ln in r.lines], basket_adjustments=r.basket_adjustments or [],
                   warnings=r.parse_warnings or [], reconciliation=reconciliation(r), blocking=blocking_issues(r),
                   extracted_text=r.extracted_text, subtotal_pence=r.subtotal_pence)
        if r.duplicate_of_id:
            d = db.get(Receipt, r.duplicate_of_id)
            out["duplicate_of"] = receipt_out(db, d) if d else None
    return out


@router.get("")
def list_receipts(p: Principal = Depends(require("receipts:write")), db: Session = Depends(get_db)):
    rows = db.scalars(select(Receipt).where(Receipt.household_id == p.household_id).order_by(Receipt.created_at.desc()).limit(200))
    return [receipt_out(db, r) for r in rows]


@router.post("/import", status_code=202)
async def import_receipt(file: UploadFile | None = File(None), text: str | None = Form(None), source_kind: str | None = Form(None),
                         allow_duplicate: bool = Form(False), p: Principal = Depends(require("receipts:write")),
                         db: Session = Depends(get_db)):
    """Upload a photo, screenshot, PDF, saved e-receipt (.eml/.html) or pasted text. Parsing runs in the worker."""
    rate_limit(db, f"receipt:{p.household_id}", get_settings().import_rate_per_minute)
    limit = get_settings().max_upload_mb * 1024 * 1024
    r = Receipt(household_id=p.household_id, created_by_id=p.user_id, status="parsing")
    if file is not None:
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(413, f"Files up to {get_settings().max_upload_mb} MB, please")
        mime = sniff_mime(data) or (file.content_type or "")
        if file.filename and file.filename.lower().endswith(".eml"):
            mime = "message/rfc822"
        elif file.filename and file.filename.lower().endswith((".html", ".htm")) and mime not in RECEIPT_MIMES - {"text/plain"}:
            mime = "text/html"
        elif mime not in RECEIPT_MIMES:
            mime = "text/plain" if _looks_text(data) else mime
        if mime not in RECEIPT_MIMES:
            raise HTTPException(415, "Upload a photo, screenshot, PDF, e-mail (.eml) or text receipt")
        if mime.startswith("image/") and mime != "image/heic":
            from ..services.media import reencode_image
            data, _, _ = reencode_image(data, max_side=3000, quality=90)  # validate + strip metadata
            mime = "image/jpeg"
        rel, digest = store_receipt_file(data, mime, p.household_id)
        existing = db.scalar(select(Receipt).where(Receipt.household_id == p.household_id, Receipt.file_sha256 == digest))
        if existing and not allow_duplicate:
            return {"duplicate": True, "message": "You've already uploaded this exact file. It hasn't been imported again.",
                    "receipt": receipt_out(db, existing), "job": None}
        r.file_path, r.file_mime, r.file_sha256 = rel, mime, digest
        r.source_kind = source_kind or ("pdf" if mime == "application/pdf" else "email" if mime == "message/rfc822" else "photo" if mime.startswith("image/") else "text")
    elif text:
        r.extracted_text = text[:100000]
        r.source_kind = "text"
        import hashlib
        r.file_sha256 = hashlib.sha256(text.strip().encode()).hexdigest()
        existing = db.scalar(select(Receipt).where(Receipt.household_id == p.household_id, Receipt.file_sha256 == r.file_sha256))
        if existing and not allow_duplicate:
            return {"duplicate": True, "message": "You've already pasted this exact receipt.", "receipt": receipt_out(db, existing), "job": None}
    else:
        raise HTTPException(422, "Choose a file or paste the receipt text")
    db.add(r)
    db.commit()
    audit(db, p, "receipt.uploaded", "receipt", r.id, {"kind": r.source_kind})
    db.commit()
    job = enqueue(db, "receipt_parse", {"receipt_id": str(r.id)}, household_id=p.household_id, user_id=p.user_id)
    db.refresh(r)
    return {"duplicate": False, "receipt": receipt_out(db, r), "job": job_out(job)}


def _looks_text(data: bytes) -> bool:
    try:
        data[:4000].decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


@router.get("/{receipt_id}/review")
def review(receipt_id: uuid.UUID, p: Principal = Depends(require("receipts:write")), db: Session = Depends(get_db)):
    return receipt_out(db, get_owned(db, Receipt, receipt_id, p.household_id, "Receipt"), full=True)


class ReceiptPatch(BaseModel):
    retailer: str | None = None
    store_name: str | None = None
    purchased_at: datetime | None = None
    receipt_number: str | None = None
    total_pence: int | None = None
    payment_total_pence: int | None = None


def _editable(r: Receipt) -> None:
    if r.status == "applied":
        raise HTTPException(409, "This receipt has been applied. Record a correction or return in the pantry instead.")


@router.patch("/{receipt_id}")
def patch_receipt(receipt_id: uuid.UUID, body: ReceiptPatch, p: Principal = Depends(require("receipts:write")), db: Session = Depends(get_db)):
    from ..services.receipt_parse import fingerprint
    from ..services.receipts import detect_duplicate

    r = get_owned(db, Receipt, receipt_id, p.household_id, "Receipt")
    _editable(r)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(r, k, v)
    r.fingerprint = fingerprint(r.retailer, r.purchased_at, r.total_pence, r.receipt_number)
    dup = detect_duplicate(db, r)
    r.duplicate_of_id = dup.id if dup else None
    db.commit()
    return receipt_out(db, r, full=True)


class LinePatch(BaseModel):
    description: str | None = None
    kind: str | None = Field(None, pattern="^(item|discount|return|bag|deposit|other|ignore)$")
    quantity: float | None = Field(None, gt=0)
    weight_kg: float | None = Field(None, gt=0)
    unit_price_pence: int | None = None
    line_total_pence: int | None = None
    applies_to_line_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    food_id: uuid.UUID | None = None
    food_name: str | None = None
    new_product: ProductIn | None = None
    match_status: str | None = Field(None, pattern="^(confirmed|skip|unmatched)$")
    is_substitution: bool | None = None
    substituted_for: str | None = None
    create_lot: bool | None = None
    location: str | None = Field(None, pattern="^(cupboard|fridge|freezer|other)$")
    best_before: date | None = None
    use_by: date | None = None


@router.patch("/{receipt_id}/lines/{line_id}")
def patch_line(receipt_id: uuid.UUID, line_id: uuid.UUID, body: LinePatch, p: Principal = Depends(require("receipts:write")),
               db: Session = Depends(get_db)):
    r = get_owned(db, Receipt, receipt_id, p.household_id, "Receipt")
    _editable(r)
    ln = db.get(ReceiptLine, line_id)
    if ln is None or ln.receipt_id != r.id:
        raise HTTPException(404, "Line not found")
    data = body.model_dump(exclude_unset=True)
    corrections = {"description", "kind", "quantity", "weight_kg", "unit_price_pence", "line_total_pence"} & data.keys()
    for k in corrections | {"applies_to_line_id", "is_substitution", "substituted_for", "create_lot", "location", "best_before", "use_by"}:
        if k in data:
            setattr(ln, k, data[k])
    if corrections:
        ln.corrected = True
    if body.new_product:
        prod = create_product(db, p, body.new_product)
        ln.product_id, ln.food_id, ln.match_status = prod.id, prod.food_id, "confirmed"
    elif "product_id" in data and data["product_id"]:
        prod = get_owned(db, Product, data["product_id"], p.household_id, "Product")
        ln.product_id, ln.food_id, ln.match_status = prod.id, prod.food_id, "confirmed"
    elif body.food_id or body.food_name:
        food = get_owned(db, Food, body.food_id, p.household_id, "Food") if body.food_id else find_or_create_food(db, p.household_id, body.food_name)
        ln.product_id, ln.food_id, ln.match_status = None, food.id, "confirmed"
    if body.match_status:
        ln.match_status = body.match_status
        if body.match_status == "skip":
            ln.create_lot = False
    if ln.kind in ("bag", "deposit", "other", "ignore") and ln.match_status == "unmatched":
        ln.match_status = "skip"
    db.commit()
    db.refresh(r)
    return receipt_out(db, r, full=True)


class LineIn(BaseModel):
    description: str
    kind: str = "item"
    quantity: float = 1
    line_total_pence: int


@router.post("/{receipt_id}/lines", status_code=201)
def add_line(receipt_id: uuid.UUID, body: LineIn, p: Principal = Depends(require("receipts:write")), db: Session = Depends(get_db)):
    r = get_owned(db, Receipt, receipt_id, p.household_id, "Receipt")
    _editable(r)
    pos = (db.scalar(select(func.max(ReceiptLine.position)).where(ReceiptLine.receipt_id == r.id)) or -1) + 1
    db.add(ReceiptLine(receipt_id=r.id, position=pos, raw_text="(added by hand)", description=body.description, kind=body.kind,
                       quantity=body.quantity, line_total_pence=body.line_total_pence, unit_price_pence=round(body.line_total_pence / body.quantity),
                       corrected=True, confidence=1.0, create_lot=body.kind == "item"))
    db.commit()
    db.refresh(r)
    return receipt_out(db, r, full=True)


@router.delete("/{receipt_id}/lines/{line_id}")
def delete_line(receipt_id: uuid.UUID, line_id: uuid.UUID, p: Principal = Depends(require("receipts:write")), db: Session = Depends(get_db)):
    r = get_owned(db, Receipt, receipt_id, p.household_id, "Receipt")
    _editable(r)
    ln = db.get(ReceiptLine, line_id)
    if ln is None or ln.receipt_id != r.id:
        raise HTTPException(404, "Line not found")
    ln.kind, ln.match_status, ln.create_lot = "ignore", "skip", False  # keep the raw text; just exclude it
    db.commit()
    db.refresh(r)
    return receipt_out(db, r, full=True)


class ApplyIn(BaseModel):
    idempotency_key: str | None = Field(None, max_length=120)
    confirm_not_duplicate: bool = False


@router.post("/{receipt_id}/apply")
def apply(receipt_id: uuid.UUID, body: ApplyIn | None = None, p: Principal = Depends(require("receipts:write")), db: Session = Depends(get_db)):
    body = body or ApplyIn()
    r = get_owned(db, Receipt, receipt_id, p.household_id, "Receipt")
    result = apply_receipt(db, r, user_id=p.user_id, idempotency_key=body.idempotency_key, confirm_not_duplicate=body.confirm_not_duplicate)
    if result["status"] == "applied":
        audit(db, p, "receipt.applied", "receipt", r.id, {k: v for k, v in result.items() if k != "receipt_id"})
    db.commit()
    return {**result, "receipt": receipt_out(db, db.get(Receipt, receipt_id), full=True)}


@router.get("/{receipt_id}/file")
def get_file(receipt_id: uuid.UUID, p: Principal = Depends(require("receipts:write")), db: Session = Depends(get_db)):
    r = get_owned(db, Receipt, receipt_id, p.household_id, "Receipt")
    if not r.file_path:
        raise HTTPException(404, "The original file has been deleted")
    return FileResponse(receipt_abs(r.file_path), media_type=r.file_mime or "application/octet-stream",
                        headers={"Cache-Control": "private, no-store", "Content-Disposition": "inline"})


@router.delete("/{receipt_id}/file")
def delete_file(receipt_id: uuid.UUID, p: Principal = Depends(require("receipts:write")), db: Session = Depends(get_db)):
    """Delete the original image/PDF but keep the reviewed lines, purchases and prices."""
    r = get_owned(db, Receipt, receipt_id, p.household_id, "Receipt")
    if r.file_path:
        others = db.scalar(select(func.count()).select_from(Receipt).where(Receipt.file_path == r.file_path, Receipt.id != r.id))
        if not others:
            receipt_abs(r.file_path).unlink(missing_ok=True)
        r.file_path, r.file_deleted_at = None, utcnow()
        r.extracted_text = None
    audit(db, p, "receipt.file_deleted", "receipt", r.id)
    db.commit()
    return receipt_out(db, r)


@router.delete("/{receipt_id}")
def delete_receipt(receipt_id: uuid.UUID, p: Principal = Depends(require("receipts:write")), db: Session = Depends(get_db)):
    r = get_owned(db, Receipt, receipt_id, p.household_id, "Receipt")
    if r.status == "applied":
        raise HTTPException(409, "Applied receipts are part of your purchase history. Delete the original file instead.")
    if r.file_path:
        others = db.scalar(select(func.count()).select_from(Receipt).where(Receipt.file_path == r.file_path, Receipt.id != r.id))
        if not others:
            receipt_abs(r.file_path).unlink(missing_ok=True)
    db.delete(r)
    db.commit()
    return {"ok": True}
