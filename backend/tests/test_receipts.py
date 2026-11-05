"""Receipt parsing, review, idempotent apply and duplicate protection (acceptance test 3)."""
import io
import shutil

import pytest

from app.services.receipt_parse import parse_receipt_text

RECEIPT = """Sainsbury's Supermarkets Ltd
Islington Local
SS SEMI SKIMMED MILK 2.27L          £1.65
FARFALLE PASTA 500G                 £1.90
2 x £0.95
NECTAR PRICE SAVING                -£0.40
BANANAS LOOSE                       £0.42
0.540 kg @ £0.78/kg
CHOPPD TOMATOS 4OOG                 £0.55
CARRIER BAG                         £0.10
SUBTOTAL                            £4.22
TOTAL                               £4.22
NECTAR ECOUPON                     -£0.50
VISA CONTACTLESS                    £3.72
05/11/2026 18:32  Receipt No 1234-5678
"""


def test_parser_reads_promotions_weights_and_totals():
    r = parse_receipt_text(RECEIPT)
    assert r.retailer == "Sainsbury's" and r.total_pence == 422 and r.receipt_number == "1234-5678"
    kinds = [(ln.description, ln.kind, ln.line_total_pence) for ln in r.lines]
    assert ("NECTAR PRICE SAVING", "discount", -40) in kinds
    pasta = next(ln for ln in r.lines if ln.description.startswith("FARFALLE"))
    assert pasta.quantity == 2 and pasta.unit_price_pence == 95
    bananas = next(ln for ln in r.lines if ln.description.startswith("BANANAS"))
    assert bananas.weight_kg == pytest.approx(0.54)
    assert r.basket_adjustments == [{"raw_text": "NECTAR ECOUPON                     -£0.50", "description": "NECTAR ECOUPON", "amount_pence": -50}]
    assert r.warnings == [] and r.confidence >= 0.9


def test_parser_tolerates_ocr_spacing():
    r = parse_receipt_text(RECEIPT.replace("-£0.40", "- £0.40").replace("£1.90", "£1,90"))
    disc = next(ln for ln in r.lines if ln.kind == "discount")
    assert disc.line_total_pence == -40 and r.total_pence == 422 and r.confidence >= 0.9


def _upload(client, text=RECEIPT, **extra):
    r = client.post("/api/v1/receipts/import", files={"file": ("receipt.txt", io.BytesIO(text.encode()), "text/plain")}, data=extra)
    assert r.status_code == 202, r.text
    return r.json()


def _setup_products(client):
    pasta = client.post("/api/v1/products", json={"name": "Farfalle", "food_name": "Dried pasta", "package_quantity": 500, "package_unit": "g"}).json()
    milk = client.post("/api/v1/products", json={"name": "Semi skimmed milk 2.27L", "food_name": "Semi-skimmed milk",
                                                  "package_quantity": 2272, "package_unit": "ml"}).json()
    return pasta, milk


def test_review_correct_apply_once(client):
    pasta, milk = _setup_products(client)
    up = _upload(client)
    assert up["duplicate"] is False and up["job"]["status"] == "succeeded"
    rid = up["receipt"]["id"]
    rev = client.get(f"/api/v1/receipts/{rid}/review").json()
    assert rev["status"] == "review" and rev["reconciliation"]["balanced"]
    assert rev["reconciliation"]["basket_adjustments_pence"] == -50
    lines = {ln["description"]: ln for ln in rev["lines"]}
    # Apply is blocked while lines are unresolved
    r = client.post(f"/api/v1/receipts/{rid}/apply", json={})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "unresolved_lines"

    # Correct one OCR line ("CHOPPD TOMATOS 4OOG" -> proper wording) and create its product inline
    tom = lines["CHOPPD TOMATOS 4OOG"]
    r = client.patch(f"/api/v1/receipts/{rid}/lines/{tom['id']}", json={
        "description": "Chopped tomatoes 400g",
        "new_product": {"name": "Chopped tomatoes", "food_name": "Chopped tomatoes (tinned)", "package_quantity": 400, "package_unit": "g"}})
    assert r.status_code == 200, r.text
    fixed = next(ln for ln in r.json()["lines"] if ln["id"] == tom["id"])
    assert fixed["corrected"] and fixed["raw_text"].startswith("CHOPPD TOMATOS") and fixed["match_status"] == "confirmed"

    client.patch(f"/api/v1/receipts/{rid}/lines/{lines['FARFALLE PASTA 500G']['id']}", json={"product_id": pasta["id"]})
    client.patch(f"/api/v1/receipts/{rid}/lines/{lines['SS SEMI SKIMMED MILK 2.27L']['id']}", json={"product_id": milk["id"], "location": "fridge"})
    client.patch(f"/api/v1/receipts/{rid}/lines/{lines['BANANAS LOOSE']['id']}", json={"food_name": "Bananas"})

    r = client.post(f"/api/v1/receipts/{rid}/apply", json={"idempotency_key": "apply-1"})
    assert r.status_code == 200, r.text
    assert r.json()["purchases"] == 4 and r.json()["lots"] == 4
    # Applying again is a no-op
    again = client.post(f"/api/v1/receipts/{rid}/apply", json={"idempotency_key": "apply-1"}).json()
    assert again["status"] == "already_applied"

    pantry = {g["name"]: g for g in client.get("/api/v1/pantry").json()["items"]}
    assert pantry["Farfalle"]["quantity_base"] == 1000  # 2 packs x 500 g
    assert pantry["Bananas"]["quantity_base"] == 540
    # Actual paid price: £1.90 shelf - £0.40 Nectar saving = £1.50 for 1 kg
    hist = client.get(f"/api/v1/prices/history?productId={pasta['id']}").json()
    paid = [s for s in hist["series"] if s["kind"] == "paid"]
    shelf = [s for s in hist["series"] if s["kind"] == "shelf"]
    assert [s["price_pence"] for s in paid] == [150] and [s["price_pence"] for s in shelf] == [190]
    assert paid[0]["unit_price_pence"] == pytest.approx(150) and paid[0]["promotion"] == "NECTAR PRICE SAVING"

    # Re-importing the identical file warns and does not create anything
    dup = _upload(client)
    assert dup["duplicate"] is True and dup["receipt"]["id"] == rid
    # A different photo/scan of the same receipt (same retailer/date/total/number) is flagged and cannot double count
    other = _upload(client, RECEIPT.replace("Islington Local", "Islington Local  "))
    assert other["duplicate"] is False
    rid2 = other["receipt"]["id"]
    rev2 = client.get(f"/api/v1/receipts/{rid2}/review").json()
    assert rev2["duplicate_of_id"] == rid
    for ln in rev2["lines"]:
        if ln["kind"] == "item":
            client.patch(f"/api/v1/receipts/{rid2}/lines/{ln['id']}", json={"match_status": "skip"})
    r = client.post(f"/api/v1/receipts/{rid2}/apply", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "duplicate"
    assert client.get("/api/v1/pantry").json()["count"] == 4


def test_learned_matches_and_file_deletion(client):
    pasta, milk = _setup_products(client)
    rid = _upload(client)["receipt"]["id"]
    rev = client.get(f"/api/v1/receipts/{rid}/review").json()
    for ln in rev["lines"]:
        if ln["kind"] != "item":
            continue
        if "FARFALLE" in ln["description"]:
            client.patch(f"/api/v1/receipts/{rid}/lines/{ln['id']}", json={"product_id": pasta["id"]})
        else:
            client.patch(f"/api/v1/receipts/{rid}/lines/{ln['id']}", json={"match_status": "skip"})
    assert client.post(f"/api/v1/receipts/{rid}/apply", json={}).status_code == 200
    # Original can be deleted while history stays
    r = client.delete(f"/api/v1/receipts/{rid}/file")
    assert r.json()["has_file"] is False
    assert client.get(f"/api/v1/receipts/{rid}/file").status_code == 404
    # Next week's receipt: the same wording is suggested from history
    rid2 = _upload(client, RECEIPT.replace("05/11/2026", "12/11/2026").replace("1234-5678", "2222-3333"))["receipt"]["id"]
    line = next(ln for ln in client.get(f"/api/v1/receipts/{rid2}/review").json()["lines"] if "FARFALLE" in ln["description"])
    assert line["match_status"] == "suggested" and line["product"]["id"] == pasta["id"]


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_photo_receipt_ocr(client):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (900, 900), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSansMono.ttf", 26)
    except OSError:
        font = ImageFont.load_default()
    for i, line in enumerate(RECEIPT.splitlines()):
        d.text((30, 30 + i * 36), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)
    r = client.post("/api/v1/receipts/import", files={"file": ("photo.jpg", io.BytesIO(buf.getvalue()), "image/jpeg")})
    assert r.status_code == 202
    rev = client.get(f"/api/v1/receipts/{r.json()['receipt']['id']}/review").json()
    assert rev["retailer"] == "Sainsbury's" and len([ln for ln in rev["lines"] if ln["kind"] == "item"]) >= 3
