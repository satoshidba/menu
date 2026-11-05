"""Import the UK Composition of Foods Integrated Dataset (CoFID) as a versioned reference table.

Source: Public Health England / OHID, McCance and Widdowson's CoFID 2021. Crown copyright, Open Government
Licence v3.0. Values are per 100 g edible portion. 'Tr' (trace) is stored as 0 with a "trace" flag; 'N'
(present but not determined) and blanks are unknown (None).
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..models import CofidFood, utcnow

DATASET_VERSION = "CoFID-2021"
SOURCE_URL = ("https://www.gov.uk/government/publications/composition-of-foods-integrated-dataset-cofid")
# sheet -> {CoFID column code: our key}
COLUMNS = {
    "1.3 Proximates": {"PROT": "protein_g", "FAT": "fat_g", "CHO": "carbohydrate_g", "KCALS": "energy_kcal",
                       "KJ": "energy_kj", "TOTSUG": "sugars_g", "AOACFIB": "fibre_g", "SATFOD": "saturates_g"},
    "1.4 Inorganics": {"NA": "sodium_mg", "CA": "calcium_mg", "FE": "iron_mg", "K": "potassium_mg"},
    "1.5 Vitamins": {"VITC": "vitamin_c_mg", "VITD": "vitamin_d_ug"},
}


def _value(raw) -> tuple[float | None, str | None]:
    if raw is None:
        return None, None
    s = str(raw).strip()
    if s == "" or s.upper() == "N":
        return None, None
    if s.lower() == "tr":
        return 0.0, "trace"
    try:
        return float(s), None
    except ValueError:
        return None, None


def read_xlsx(path: Path) -> dict[str, dict]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    foods: dict[str, dict] = {}
    for sheet, cols in COLUMNS.items():
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        rows = ws.iter_rows(values_only=True)
        next(rows)  # human headers
        codes = next(rows)
        next(rows)  # descriptions
        idx = {code: i for i, code in enumerate(codes) if code in cols}
        for row in rows:
            code = row[0]
            if not code:
                continue
            f = foods.setdefault(str(code), {"name": row[1], "description": row[2], "group": row[3], "nutrients": {}, "flags": {}})
            for c, i in idx.items():
                v, flag = _value(row[i] if i < len(row) else None)
                f["nutrients"][cols[c]] = v
                if flag:
                    f["flags"][cols[c]] = flag
    return foods


def load(db: Session, foods: dict[str, dict], version: str = DATASET_VERSION) -> int:
    db.execute(delete(CofidFood).where(CofidFood.dataset_version == version))
    n = 0
    for code, f in foods.items():
        db.add(CofidFood(code=code, dataset_version=version, name=str(f["name"])[:300], description=f.get("description"),
                         group_code=f.get("group"), nutrients=f["nutrients"], flags=f.get("flags", {}), imported_at=utcnow()))
        n += 1
    db.commit()
    return n


def load_sample(db: Session) -> int:
    """Load the bundled subset (real CoFID 2021 values, see seed/cofid_subset.json) for offline setups and tests."""
    path = Path(__file__).resolve().parent.parent / "seed" / "cofid_subset.json"
    data = json.loads(path.read_text())
    return load(db, data["foods"], data["dataset_version"])
