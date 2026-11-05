"""Nutrition maths. Unknown is never zero.

A `NutrientTotal` carries `value` plus `status`:
* complete - every contributing item had a known value
* partial  - some contributors were unknown; `value` is the sum of the known part (a lower bound)
* unknown  - no contributor had a known value; `value` is None
"""
from __future__ import annotations

from dataclasses import dataclass, field

# key -> (label, unit, required-minimum?)
NUTRIENTS: dict[str, tuple[str, str, bool]] = {
    "energy_kcal": ("Energy", "kcal", True),
    "energy_kj": ("Energy", "kJ", True),
    "protein_g": ("Protein", "g", True),
    "carbohydrate_g": ("Carbohydrate", "g", True),
    "sugars_g": ("of which sugars", "g", True),
    "fat_g": ("Fat", "g", True),
    "saturates_g": ("of which saturates", "g", True),
    "fibre_g": ("Fibre", "g", True),
    "salt_g": ("Salt", "g", True),
    "sodium_mg": ("Sodium", "mg", False),
    "calcium_mg": ("Calcium", "mg", False),
    "iron_mg": ("Iron", "mg", False),
    "potassium_mg": ("Potassium", "mg", False),
    "vitamin_c_mg": ("Vitamin C", "mg", False),
    "vitamin_d_ug": ("Vitamin D", "µg", False),
    "vitamin_a_ug": ("Vitamin A", "µg", False),
    "vitamin_b12_ug": ("Vitamin B12", "µg", False),
    "folate_ug": ("Folate / folic acid", "µg", False),
    "zinc_mg": ("Zinc", "mg", False),
    "iodine_ug": ("Iodine", "µg", False),
    "omega3_mg": ("Omega-3 (EPA+DHA)", "mg", False),
}
CORE_NUTRIENTS = [k for k, v in NUTRIENTS.items() if v[2]]
KJ_PER_KCAL = 4.184
SALT_PER_SODIUM = 2.5


def unit_of(key: str) -> str:
    return NUTRIENTS[key][1] if key in NUTRIENTS else ""


def clean_values(raw: dict) -> tuple[dict, dict]:
    """Normalise a nutrient dict: keep numbers, keep None as unknown, derive kJ/salt where the source unit is known.

    Returns (values, flags). Derivations are flagged "derived".
    """
    values: dict[str, float | None] = {}
    flags: dict[str, str] = {}
    for k in NUTRIENTS:
        v = raw.get(k)
        if v is None or v == "":
            values[k] = None
        else:
            try:
                values[k] = float(v)
            except (TypeError, ValueError):
                values[k] = None
    if values.get("energy_kj") is None and values.get("energy_kcal") is not None:
        values["energy_kj"] = round(values["energy_kcal"] * KJ_PER_KCAL, 1)
        flags["energy_kj"] = "derived"
    if values.get("energy_kcal") is None and values.get("energy_kj") is not None:
        values["energy_kcal"] = round(values["energy_kj"] / KJ_PER_KCAL, 1)
        flags["energy_kcal"] = "derived"
    # Sodium -> salt only when sodium is recorded in mg (our canonical, known unit).
    if values.get("salt_g") is None and values.get("sodium_mg") is not None:
        values["salt_g"] = round(values["sodium_mg"] * SALT_PER_SODIUM / 1000, 3)
        flags["salt_g"] = "derived"
    return values, flags


def scale(values: dict, grams_or_ml: float, basis_amount: float = 100.0) -> dict:
    """Scale per-100 values to an amount. None stays None."""
    f = grams_or_ml / basis_amount
    return {k: (None if v is None else v * f) for k, v in values.items()}


@dataclass
class NutrientTotal:
    value: float | None = None
    known: int = 0
    unknown: int = 0
    missing_from: list[str] = field(default_factory=list)
    estimated: bool = False

    @property
    def status(self) -> str:
        if self.known == 0:
            return "unknown"
        return "complete" if self.unknown == 0 else "partial"

    def as_dict(self, key: str, divisor: float = 1.0) -> dict:
        v = None if self.value is None else round(self.value / divisor, 3)
        return {
            "key": key, "label": NUTRIENTS[key][0], "unit": unit_of(key), "value": v, "status": self.status,
            "estimated": self.estimated, "missing_from": self.missing_from,
        }


@dataclass
class Contribution:
    """One item's contribution: its label, scaled nutrient values (or None if the amount/record is unknown)."""

    label: str
    values: dict | None  # None => the item's nutrition is entirely unknown (no record or no weight)
    estimated: bool = False
    reason: str | None = None


class NutritionAccumulator:
    def __init__(self, keys: list[str] | None = None):
        self.keys = keys or list(NUTRIENTS)
        self.totals = {k: NutrientTotal() for k in self.keys}
        self.items: list[dict] = []

    def add(self, c: Contribution) -> None:
        self.items.append({"label": c.label, "known": c.values is not None, "estimated": c.estimated, "reason": c.reason})
        for k in self.keys:
            t = self.totals[k]
            v = None if c.values is None else c.values.get(k)
            if v is None:
                t.unknown += 1
                if c.label not in t.missing_from:
                    t.missing_from.append(c.label)
            else:
                t.value = (t.value or 0.0) + float(v)
                t.known += 1
                t.estimated = t.estimated or c.estimated

    def result(self, divisor: float = 1.0) -> dict:
        """Serialisable summary. `divisor` = number of servings to get per-serving values."""
        return {
            "nutrients": {k: self.totals[k].as_dict(k, divisor) for k in self.keys},
            "items": self.items,
            "complete": all(self.totals[k].status == "complete" for k in CORE_NUTRIENTS if k in self.totals),
        }


def snapshot_values(summary: dict) -> tuple[dict, dict, dict]:
    """Flatten a summary into (values, units, status) dicts for storage on a consumption row."""
    vals, units, status = {}, {}, {}
    for k, d in summary["nutrients"].items():
        vals[k] = d["value"]
        units[k] = d["unit"]
        status[k] = d["status"]
    return vals, units, status


def scale_summary(summary: dict, factor: float) -> dict:
    """Scale an already-computed summary (e.g. per serving -> 0.5 serving)."""
    out = {"nutrients": {}, "items": summary.get("items", []), "complete": summary.get("complete", False)}
    for k, d in summary["nutrients"].items():
        nd = dict(d)
        nd["value"] = None if d["value"] is None else round(d["value"] * factor, 3)
        out["nutrients"][k] = nd
    return out
