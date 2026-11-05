"""Receipt pipeline: extract text -> parse -> suggest matches -> human review -> idempotent apply."""
from __future__ import annotations

import email
import email.policy
import logging
import subprocess
import tempfile
import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import audit
from ..models import Food, ImportJob, Product, Purchase, Receipt, ReceiptLine, utcnow
from . import ai
from .jobs import JobFailure, handler
from .media import receipt_abs
from .prices import record_purchase
from .receipt_parse import ParsedReceipt, fingerprint, parse_receipt_text
from .recipe_extract import plain_text
from .stock import create_lot

log = logging.getLogger("larder.receipts")


# ---- text extraction (runs in the worker; untrusted input, bounded by timeouts) ----------------------------------------

def pdf_text(path: Path) -> str:
    try:
        out = subprocess.run(["pdftotext", "-layout", "-l", "20", str(path), "-"], capture_output=True, timeout=60, check=False)
        return out.stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return ""


def image_text(path: Path) -> str:
    try:
        import pytesseract
        from PIL import Image, ImageOps

        with Image.open(path) as im:
            g = ImageOps.grayscale(ImageOps.exif_transpose(im))
            if g.width < 1400:
                g = g.resize((g.width * 2, g.height * 2))
            return pytesseract.image_to_string(g, lang="eng", config="--psm 4", timeout=90)
    except Exception as e:  # noqa: BLE001 - OCR missing or failed
        log.warning("OCR unavailable: %s", type(e).__name__)
        return ""


def email_text(data: bytes) -> tuple[str, bytes | None]:
    """Return (text, first PDF attachment) from a forwarded e-receipt."""
    msg = email.message_from_bytes(data, policy=email.policy.default)
    text, pdf = "", None
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == "application/pdf" and pdf is None:
            pdf = part.get_payload(decode=True)
        elif ctype == "text/plain" and not text:
            text = part.get_content()
        elif ctype == "text/html" and not text:
            text = plain_text(part.get_content())
    return text, pdf


def extract_text(receipt: Receipt) -> str:
    if receipt.file_path is None:
        return receipt.extracted_text or ""
    path = receipt_abs(receipt.file_path)
    mime = receipt.file_mime or ""
    if mime == "application/pdf":
        return pdf_text(path)
    if mime.startswith("image/"):
        return image_text(path)
    data = path.read_bytes()
    if mime == "message/rfc822":
        text, pdf = email_text(data)
        if pdf:
            with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
                tmp.write(pdf)
                tmp.flush()
                return pdf_text(Path(tmp.name)) or text
        return text
    if mime == "text/html":
        return plain_text(data.decode("utf-8", errors="replace"))
    return data.decode("utf-8", errors="replace")


# ---- matching ----------------------------------------------------------------------------------------------------------

def suggest_match(db: Session, household_id: uuid.UUID, retailer: str | None, description: str) -> tuple[str, uuid.UUID | None, uuid.UUID | None, float]:
    """Learned mapping from earlier confirmed lines first, then name similarity. Returns (status, product, food, conf)."""
    prev = db.scalar(select(ReceiptLine).join(Receipt).where(
        Receipt.household_id == household_id, Receipt.status == "applied", ReceiptLine.match_status == "confirmed",
        func.lower(ReceiptLine.description) == description.lower(), Receipt.retailer.is_not_distinct_from(retailer))
        .order_by(ReceiptLine.created_at.desc()))
    if prev and (prev.product_id or prev.food_id):
        return "suggested", prev.product_id, prev.food_id, 0.9
    row = db.execute(select(Product.id, Product.food_id, func.similarity(Product.name, description).label("s"))
                     .where(Product.household_id == household_id).order_by(func.similarity(Product.name, description).desc()).limit(1)).first()
    if row and row[2] >= 0.45:
        return "suggested", row[0], row[1], round(float(row[2]), 2)
    frow = db.execute(select(Food.id, func.similarity(Food.name, description).label("s"))
                      .where(Food.household_id == household_id, Food.is_pet_food.is_(False))
                      .order_by(func.similarity(Food.name, description).desc()).limit(1)).first()
    if frow and frow[1] >= 0.4:
        return "suggested", None, frow[0], round(float(frow[1]), 2)
    return "unmatched", None, None, 0.0


def _apply_parsed(db: Session, r: Receipt, parsed: ParsedReceipt, parser: str) -> None:
    r.retailer = r.retailer or parsed.retailer
    r.purchased_at = r.purchased_at or parsed.purchased_at
    r.receipt_number = r.receipt_number or parsed.receipt_number
    r.subtotal_pence, r.total_pence = parsed.subtotal_pence, parsed.total_pence
    r.payment_total_pence = parsed.payment_total_pence
    r.basket_adjustments = parsed.basket_adjustments
    r.parse_confidence, r.parse_warnings, r.parser = parsed.confidence, parsed.warnings, parser
    for old in list(r.lines):
        db.delete(old)
    db.flush()
    created: list[ReceiptLine] = []
    for i, pl in enumerate(parsed.lines):
        line = ReceiptLine(receipt_id=r.id, position=i, raw_text=pl.raw_text, description=pl.description[:200], kind=pl.kind,
                           quantity=pl.quantity, weight_kg=pl.weight_kg, unit_price_pence=pl.unit_price_pence,
                           line_total_pence=pl.line_total_pence, confidence=pl.confidence,
                           original_parsed={"description": pl.description, "quantity": pl.quantity, "line_total_pence": pl.line_total_pence,
                                            "unit_price_pence": pl.unit_price_pence, "weight_kg": pl.weight_kg, "kind": pl.kind},
                           create_lot=pl.kind == "item", match_status="skip" if pl.kind in ("bag", "deposit", "other") else "unmatched")
        if pl.kind == "item":
            st, pid, fid, conf = suggest_match(db, r.household_id, r.retailer, pl.description)
            line.match_status, line.product_id, line.food_id = st, pid, fid
            if pid:
                prod = db.get(Product, pid)
                line.location = _usual_location(db, prod)
        db.add(line)
        created.append(line)
    db.flush()
    for pl, line in zip(parsed.lines, created):
        if pl.kind == "discount" and pl.applies_to_index is not None and pl.applies_to_index < len(created):
            line.applies_to_line_id = created[pl.applies_to_index].id


def _usual_location(db: Session, prod: Product | None) -> str:
    from ..models import PantryLot

    if prod is None:
        return "cupboard"
    loc = db.scalar(select(PantryLot.location).where(PantryLot.product_id == prod.id).order_by(PantryLot.created_at.desc()))
    return loc or "cupboard"


def detect_duplicate(db: Session, r: Receipt) -> Receipt | None:
    q = select(Receipt).where(Receipt.household_id == r.household_id, Receipt.id != r.id)
    if r.file_sha256:
        d = db.scalar(q.where(Receipt.file_sha256 == r.file_sha256))
        if d:
            return d
    if r.fingerprint:
        d = db.scalar(q.where(Receipt.fingerprint == r.fingerprint))
        if d:
            return d
    if r.retailer and r.purchased_at and r.total_pence is not None:
        cands = db.scalars(q.where(Receipt.retailer == r.retailer, Receipt.total_pence == r.total_pence))
        for c in cands:
            if c.purchased_at and c.purchased_at.date() == r.purchased_at.date():
                return c
    return None


@handler("receipt_parse")
def receipt_parse(db: Session, job: ImportJob) -> dict:
    r = db.get(Receipt, job.input["receipt_id"])
    if r is None:
        raise JobFailure("Receipt not found")
    usage: list = []
    text = extract_text(r)
    r.extracted_text = text or r.extracted_text
    parsed = parse_receipt_text(text or "")
    parser = "rules"
    if (parsed.confidence < 0.7 or not parsed.lines) and ai.available():
        try:
            file = None
            if r.file_path and (r.file_mime or "").startswith(("image/", "application/pdf")):
                file = (receipt_abs(r.file_path).read_bytes(), r.file_mime if r.file_mime != "image/heic" else "image/jpeg")
            ar = ai.parse_receipt(text, file, usage=usage)
            parsed = _from_ai(ar, parsed)
            parser = "rules+ai"
        except (ai.AIError, ai.AIUnavailable) as e:
            parsed.warnings.append(f"AI reading wasn't available ({e}).")
        for u in usage:
            audit(db, None, "ai.call", "receipt", r.id, u, household_id=r.household_id)
    if not parsed.lines:
        parsed.warnings.append("No item lines could be read. Add lines by hand or try a clearer photo.")
    _apply_parsed(db, r, parsed, parser)
    r.fingerprint = fingerprint(r.retailer, r.purchased_at, r.total_pence, r.receipt_number)
    dup = detect_duplicate(db, r)
    r.duplicate_of_id = dup.id if dup else None
    r.status = "review"
    db.commit()
    return {"receipt_id": str(r.id), "lines": len(parsed.lines), "confidence": parsed.confidence, "duplicate_of": str(dup.id) if dup else None}


def _from_ai(ar, fallback: ParsedReceipt) -> ParsedReceipt:
    from .receipt_parse import ParsedLine

    out = ParsedReceipt(retailer=ar.retailer or fallback.retailer, receipt_number=ar.receipt_number or fallback.receipt_number,
                        total_pence=ar.total_pence if ar.total_pence is not None else fallback.total_pence,
                        payment_total_pence=ar.payment_total_pence or fallback.payment_total_pence,
                        purchased_at=fallback.purchased_at, warnings=list(fallback.warnings), confidence=ar.confidence)
    if ar.purchased_at:
        try:
            out.purchased_at = datetime.fromisoformat(ar.purchased_at)
        except ValueError:
            pass
    for ln in ar.lines:
        out.lines.append(ParsedLine(raw_text=ln.raw_text, description=ln.description, kind=ln.kind, quantity=ln.quantity,
                                    weight_kg=ln.weight_kg, unit_price_pence=ln.unit_price_pence, line_total_pence=ln.line_total_pence,
                                    applies_to_index=ln.applies_to_index, confidence=min(ar.confidence, 0.8)))
    out.basket_adjustments = [{"raw_text": b.raw_text, "description": b.description, "amount_pence": b.line_total_pence or 0}
                              for b in ar.basket_adjustments]
    out.warnings.append("Lines were read with AI help; check each one.")
    return out


# ---- review & apply --------------------------------------------------------------------------------------------------

def line_effective(r: Receipt) -> dict[uuid.UUID, dict]:
    """Shelf total, allocated discounts and effective paid price per item line."""
    eff = {}
    for ln in r.lines:
        if ln.kind == "item":
            eff[ln.id] = {"shelf": ln.line_total_pence or 0, "discount": ln.discount_pence or 0, "promotions": []}
    for ln in r.lines:
        if ln.kind == "discount" and ln.applies_to_line_id in eff:
            eff[ln.applies_to_line_id]["discount"] += abs(ln.line_total_pence or 0)
            eff[ln.applies_to_line_id]["promotions"].append(ln.description)
    for v in eff.values():
        v["paid"] = v["shelf"] - v["discount"]
    return eff


def reconciliation(r: Receipt) -> dict:
    lines_total = sum((ln.line_total_pence or 0) for ln in r.lines if ln.kind in ("item", "discount", "bag", "deposit", "return", "other"))
    unallocated_discounts = sum(abs(ln.line_total_pence or 0) for ln in r.lines if ln.kind == "discount" and ln.applies_to_line_id is None)
    basket = sum(b.get("amount_pence", 0) for b in r.basket_adjustments or [])
    unmatched = None if r.total_pence is None else r.total_pence - lines_total
    return {"lines_total_pence": lines_total, "printed_total_pence": r.total_pence, "unmatched_pence": unmatched,
            "unallocated_discounts_pence": unallocated_discounts, "basket_adjustments_pence": basket,
            "payment_total_pence": r.payment_total_pence,
            "balanced": unmatched == 0 if unmatched is not None else False}


def blocking_issues(r: Receipt) -> list[dict]:
    issues = []
    for ln in r.lines:
        if ln.kind == "item" and ln.create_lot and ln.match_status not in ("confirmed", "skip"):
            issues.append({"line_id": str(ln.id), "position": ln.position, "issue": "unmatched",
                           "message": f"Match “{ln.description}” to a product or food, or mark it as not for the pantry."})
        if ln.kind == "item" and ln.line_total_pence is None:
            issues.append({"line_id": str(ln.id), "position": ln.position, "issue": "no_price", "message": f"Add the price for “{ln.description}”."})
    return issues


def apply_receipt(db: Session, r: Receipt, *, user_id=None, idempotency_key: str | None = None, confirm_not_duplicate: bool = False) -> dict:
    """Create purchases, price observations and pantry lots in ONE transaction. Safe to retry."""
    r = db.scalar(select(Receipt).where(Receipt.id == r.id).with_for_update())
    if r.status == "applied":
        return {"status": "already_applied", "receipt_id": str(r.id), "applied_at": r.applied_at.isoformat(),
                "purchases": db.scalar(select(func.count()).select_from(Purchase).where(Purchase.receipt_id == r.id))}
    if r.status != "review":
        raise HTTPException(409, "This receipt isn't ready to apply yet")
    if r.duplicate_of_id and not confirm_not_duplicate:
        dup = db.get(Receipt, r.duplicate_of_id)
        raise HTTPException(409, {"code": "duplicate", "message": "This looks like a receipt you've already imported. "
                                  "Applying it again would double count. Confirm only if it is a different shop.",
                                  "duplicate_of": str(r.duplicate_of_id), "duplicate_status": dup.status if dup else None})
    issues = blocking_issues(r)
    if issues:
        raise HTTPException(422, {"code": "unresolved_lines", "message": "Some lines still need attention.", "issues": issues})
    if not r.purchased_at:
        raise HTTPException(422, {"code": "no_date", "message": "Add the purchase date before applying."})
    eff = line_effective(r)
    created = {"purchases": 0, "lots": 0, "price_observations": 0, "returns": 0}
    store = r.store_name or r.retailer
    for ln in r.lines:
        if ln.kind == "item" and ln.match_status == "confirmed":
            prod = db.get(Product, ln.product_id) if ln.product_id else None
            food = db.get(Food, ln.food_id) if ln.food_id else (db.get(Food, prod.food_id) if prod and prod.food_id else None)
            e = eff[ln.id]
            qty_base, base_unit = _line_quantity(prod, food, ln)
            pur = record_purchase(db, r.household_id, product_id=prod.id if prod else None, food_id=food.id if food else None,
                                  store=store, retailer=r.retailer, purchased_at=r.purchased_at, package_count=ln.quantity,
                                  quantity_base=qty_base, base_unit=base_unit, shelf_price_pence=e["shelf"], paid_price_pence=e["paid"],
                                  promotion="; ".join(e["promotions"]) or None, source="receipt", receipt_id=r.id,
                                  receipt_line_id=ln.id, is_pet_food=bool(prod and prod.is_pet_food), confidence=ln.confidence or 0.9)
            created["purchases"] += 1
            created["price_observations"] += 1 + (1 if e["discount"] else 0)
            if ln.create_lot:
                if ln.weight_kg:
                    qty, unit = ln.weight_kg * 1000, "g"
                elif prod is not None and prod.package_base_quantity:
                    qty, unit = ln.quantity, "pack"
                elif prod is not None:
                    qty, unit = ln.quantity, prod.base_unit if prod.base_unit == "each" else "each"
                else:
                    qty, unit = ln.quantity, food.default_unit if food and food.default_unit == "each" else "each"
                    if food and food.default_unit != "each":
                        raise HTTPException(422, {"code": "no_size", "message": f"“{ln.description}”: give a weight or link a product with a pack size."})
                lot = create_lot(db, r.household_id, product=prod, food=None if prod else food, quantity=qty, unit=unit,
                                 location=ln.location, event_type="purchase", purchased_on=r.purchased_at.date(),
                                 best_before=ln.best_before, use_by=ln.use_by, store=store, price_paid_pence=e["paid"],
                                 package_count=int(ln.quantity) if float(ln.quantity).is_integer() else None, purchase_id=pur.id,
                                 actor_user_id=user_id, receipt_line_id=ln.id, idempotency_key=f"receipt-line:{ln.id}",
                                 occurred_at=r.purchased_at)
                created["lots"] += 1
                _ = lot
        elif ln.kind == "return" and ln.match_status == "confirmed":
            record_purchase(db, r.household_id, product_id=ln.product_id, food_id=ln.food_id, store=store, retailer=r.retailer,
                            purchased_at=r.purchased_at, package_count=-abs(ln.quantity), paid_price_pence=ln.line_total_pence,
                            source="receipt", receipt_id=r.id, receipt_line_id=ln.id)
            created["returns"] += 1
    r.status, r.applied_at, r.applied_by_id = "applied", utcnow(), user_id
    r.apply_idempotency_key = idempotency_key
    return {"status": "applied", "receipt_id": str(r.id), **created}


def _line_quantity(prod: Product | None, food: Food | None, ln: ReceiptLine) -> tuple[float | None, str | None]:
    if ln.weight_kg:
        return ln.weight_kg * 1000, "g"
    if prod and prod.package_base_quantity:
        return prod.package_base_quantity * ln.quantity, prod.base_unit
    return ln.quantity, "each"


def today() -> date:
    return date.today()
