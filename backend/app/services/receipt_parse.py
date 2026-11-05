"""Deterministic UK receipt text parser. Works on OCR output, PDF text and pasted e-receipts.

It is intentionally conservative: anything it cannot classify is kept as an 'other' line with its raw text so the
review screen can show it. The AI adapter may be used as a second opinion when confidence is low.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

RETAILERS = [
    ("Sainsbury's", r"sainsbury"), ("Tesco", r"\btesco\b"), ("Lidl", r"\blidl\b"), ("Aldi", r"\baldi\b"),
    ("M&S", r"marks\s*(&|and)\s*spencer|\bm\s*&\s*s\b"), ("Asda", r"\basda\b"), ("Morrisons", r"morrisons"),
    ("Waitrose", r"waitrose"), ("Co-op", r"\bco-?op\b"), ("Iceland", r"\biceland\b"), ("Ocado", r"\bocado\b"),
]
DISCOUNT_WORDS = re.compile(
    r"(saving|discount|promo|promotion|offer|multi-?buy|clubcard price|nectar price|lidl plus|sparks|reduced|"
    r"coupon|voucher|price match|deal|any \d+ for|\d+ for £)", re.I)
BASKET_WORDS = re.compile(r"(coupon|voucher|points|staff discount|colleague discount|ecoupon|e-coupon|redeemed|reward)", re.I)
TOTAL_WORDS = re.compile(r"^\s*(total|balance due|amount due|to pay|total to pay|order total|grand total)\b", re.I)
SUBTOTAL_WORDS = re.compile(r"^\s*(sub-?total|subtotal|items? total)\b", re.I)
PAYMENT_WORDS = re.compile(
    r"^\s*(visa|mastercard|master card|debit|credit|card|contactless|cash|change|amex|apple pay|google pay|paid|"
    r"payment|tender)\b", re.I)
SKIP_WORDS = re.compile(
    r"(vat|tel[:. ]|www\.|thank you|customer copy|store|served by|till|auth code|aid:|pan seq|merchant|terminal|"
    r"please keep|returns policy|\bpoints balance\b|you saved|total savings|items sold|number of items|balance to pay)", re.I)
RETURN_WORDS = re.compile(r"\b(refund|return(ed)?)\b", re.I)
BAG_WORDS = re.compile(r"\b(carrier bag|bag for life|bag charge)\b", re.I)
DEPOSIT_WORDS = re.compile(r"\b(deposit)\b", re.I)

PRICE = r"-?\s?£?\s?\d{1,4}[.,]\d{2}-?"  # OCR often reads "- £0.40" with a space
PRICE_END = re.compile(rf"(?P<price>{PRICE})\s*(?:[A-Z*]{{1,2}})?\s*$")
QTY_AT = re.compile(r"^\s*(?P<q>\d+)\s*(?:x|@)\s*£?\s?(?P<each>\d+[.,]\d{2})\b", re.I)
WEIGHT_AT = re.compile(r"(?P<kg>\d+[.,]\d{1,3})\s*kg\s*@\s*£?\s?(?P<per>\d+[.,]\d{2})\s*/\s*kg", re.I)
LEAD_QTY = re.compile(r"^\s*(?P<q>\d{1,2})\s*(?:x\s+)?(?=[A-Za-z])")
DATE_RES = [
    re.compile(r"(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{2,4})(?:\D{1,3}(?P<H>\d{1,2}):(?P<M>\d{2}))?"),
    re.compile(r"(?P<d>\d{1,2})[-. ](?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[-. ,]+(?P<y>\d{4})"
               r"(?:\D{1,3}(?P<H>\d{1,2}):(?P<M>\d{2}))?", re.I),
]
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
RECEIPT_NO = re.compile(r"(receipt|trans(?:action)?|trn|order|invoice|ref)\s*(no\.?|number|#|id)?\s*[:#]?\s*(?P<n>[A-Z0-9-]{4,})", re.I)


def pence(text: str) -> int:
    t = text.replace("£", "").replace(" ", "").replace(",", ".")
    neg = t.startswith("-") or t.endswith("-")
    t = t.strip("-")
    value = int(round(float(t) * 100))
    return -value if neg else value


@dataclass
class ParsedLine:
    raw_text: str
    description: str
    kind: str = "item"
    quantity: float = 1
    weight_kg: float | None = None
    unit_price_pence: int | None = None
    line_total_pence: int | None = None
    applies_to_index: int | None = None
    confidence: float = 0.8


@dataclass
class ParsedReceipt:
    retailer: str | None = None
    store_name: str | None = None
    purchased_at: datetime | None = None
    receipt_number: str | None = None
    subtotal_pence: int | None = None
    total_pence: int | None = None
    payment_total_pence: int | None = None
    lines: list[ParsedLine] = field(default_factory=list)
    basket_adjustments: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.0


def detect_retailer(text: str) -> str | None:
    head = text[:1500]
    for name, pattern in RETAILERS:
        if re.search(pattern, head, re.I):
            return name
    for name, pattern in RETAILERS:
        if re.search(pattern, text, re.I):
            return name
    return None


def parse_date(text: str, tz: str = "Europe/London") -> datetime | None:
    for rx in DATE_RES:
        m = rx.search(text)
        if not m:
            continue
        try:
            d = int(m.group("d"))
            mon = MONTHS[m.group("mon")[:3].lower()] if "mon" in rx.groupindex and m.group("mon") else int(m.group("m"))
            y = int(m.group("y"))
            y = y + 2000 if y < 100 else y
            hh = int(m.group("H")) if m.group("H") else 12
            mm = int(m.group("M")) if m.group("M") else 0
            return datetime(y, mon, d, hh, mm, tzinfo=ZoneInfo(tz))
        except (ValueError, KeyError):
            continue
    return None


def parse_receipt_text(text: str, tz: str = "Europe/London") -> ParsedReceipt:
    r = ParsedReceipt()
    r.retailer = detect_retailer(text)
    r.purchased_at = parse_date(text, tz)
    m = RECEIPT_NO.search(text)
    if m:
        r.receipt_number = m.group("n")
    lines = [ln.rstrip() for ln in text.splitlines()]
    pending_qty: tuple[int, int] | None = None  # (qty, each pence) seen on a line before the item
    total_candidates: list[int] = []
    payment_seen = False
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if TOTAL_WORDS.match(line):
            pm = PRICE_END.search(line)
            if pm:
                total_candidates.append(pence(pm.group("price")))
            continue
        if SUBTOTAL_WORDS.match(line):
            pm = PRICE_END.search(line)
            if pm:
                r.subtotal_pence = pence(pm.group("price"))
            continue
        if PAYMENT_WORDS.match(line):
            pm = PRICE_END.search(line)
            if pm and not re.match(r"^\s*change\b", line, re.I):
                r.payment_total_pence = (r.payment_total_pence or 0) + abs(pence(pm.group("price")))
            payment_seen = True
            continue
        if payment_seen:
            continue  # nothing after payment is a purchased item
        qm = QTY_AT.match(line)
        if qm and not PRICE_END.search(line[qm.end():]):
            # "2 x £0.70" on its own line: belongs to the adjacent item line
            pending_qty = (int(qm.group("q")), pence(qm.group("each")))
            if r.lines and r.lines[-1].kind == "item" and r.lines[-1].line_total_pence == pending_qty[0] * pending_qty[1]:
                r.lines[-1].quantity, r.lines[-1].unit_price_pence = pending_qty
                r.lines[-1].raw_text += "\n" + raw
                pending_qty = None
            continue
        wm = WEIGHT_AT.search(line)
        if wm and not PRICE_END.search(line[wm.end():]):
            kg = float(wm.group("kg").replace(",", "."))
            if r.lines and r.lines[-1].kind == "item":
                r.lines[-1].weight_kg = kg
                r.lines[-1].unit_price_pence = pence(wm.group("per"))
                r.lines[-1].raw_text += "\n" + raw
            continue
        pm = PRICE_END.search(line)
        if not pm:
            continue
        if SKIP_WORDS.search(line) and not DISCOUNT_WORDS.search(line):
            continue
        amount = pence(pm.group("price"))
        desc = line[: pm.start()].strip(" .:-\t")
        if not desc:
            continue
        pl = ParsedLine(raw_text=raw, description=desc, line_total_pence=amount)
        if amount < 0 or DISCOUNT_WORDS.search(desc):
            if BASKET_WORDS.search(desc) and not re.search(r"nectar price|clubcard price", desc, re.I):
                r.basket_adjustments.append({"raw_text": raw, "description": desc, "amount_pence": -abs(amount)})
                continue
            pl.kind = "discount"
            pl.line_total_pence = -abs(amount)
            prev_items = [i for i, x in enumerate(r.lines) if x.kind == "item"]
            pl.applies_to_index = prev_items[-1] if prev_items else None
            pl.confidence = 0.7 if pl.applies_to_index is not None else 0.4
        elif RETURN_WORDS.search(desc):
            pl.kind = "return"
            pl.line_total_pence = -abs(amount)
        elif BAG_WORDS.search(desc):
            pl.kind = "bag"
        elif DEPOSIT_WORDS.search(desc):
            pl.kind = "deposit"
        else:
            lq = LEAD_QTY.match(desc)
            if lq and int(lq.group("q")) > 0:
                pl.quantity = int(lq.group("q"))
                pl.description = desc[lq.end():].strip()
                if pl.quantity > 1:
                    pl.unit_price_pence = round(amount / pl.quantity)
            qm2 = QTY_AT.search(desc)
            if qm2:
                pl.quantity = int(qm2.group("q"))
                pl.unit_price_pence = pence(qm2.group("each"))
                pl.description = (desc[: qm2.start()] + desc[qm2.end():]).strip()
            if pending_qty and pending_qty[0] * pending_qty[1] == amount:
                pl.quantity, pl.unit_price_pence = pending_qty
                pending_qty = None
            if pl.unit_price_pence is None:
                pl.unit_price_pence = amount if pl.quantity == 1 else round(amount / pl.quantity)
            if not re.search(r"[A-Za-z]{3,}", pl.description):
                pl.kind, pl.confidence = "other", 0.3
        r.lines.append(pl)

    if total_candidates:
        r.total_pence = total_candidates[0]
    items_sum = sum(x.line_total_pence or 0 for x in r.lines if x.kind in ("item", "discount", "bag", "deposit", "return"))
    if r.total_pence is None:
        r.warnings.append("No total found; check the printed total.")
        r.confidence = 0.4 if r.lines else 0.0
    else:
        diff = r.total_pence - items_sum
        if diff != 0:
            r.warnings.append(f"Lines add up to £{items_sum / 100:.2f} but the total is £{r.total_pence / 100:.2f} "
                              f"(difference £{diff / 100:.2f}).")
        r.confidence = 0.95 if diff == 0 else 0.7 if abs(diff) < 100 else 0.5
    if not r.retailer:
        r.warnings.append("Retailer not recognised.")
    if not r.purchased_at:
        r.warnings.append("Purchase date not found.")
    return r


def fingerprint(retailer: str | None, purchased_at: datetime | None, total_pence: int | None, receipt_number: str | None) -> str | None:
    if not (retailer and purchased_at and total_pence is not None):
        return None
    key = f"{retailer.lower()}|{purchased_at.date().isoformat()}|{total_pence}|{(receipt_number or '').upper()}"
    return hashlib.sha256(key.encode()).hexdigest()
