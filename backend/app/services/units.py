"""Unit parsing and conversion.

Rules (see docs/DECISIONS.md, ADR-007):
* Mass normalises to grams, volume to millilitres, counts to "each".
* Volume <-> mass needs an ingredient-specific factor (density or a per-unit gram weight). Without one we
  return `needs_confirmation=True` instead of guessing.
* A count ("1 onion", "2 cloves") is never silently converted to grams. Only a confirmed per-item weight
  on the food (each_weight_confirmed) or an explicit per-unit conversion allows it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction

MASS = {"mg": 0.001, "g": 1.0, "kg": 1000.0, "oz": 28.349523125, "lb": 453.59237}
VOLUME = {
    "ml": 1.0,
    "cl": 10.0,
    "dl": 100.0,
    "l": 1000.0,
    "tsp": 5.0,
    "tbsp": 15.0,
    "dsp": 10.0,
    "cup": 236.5882365,  # US customary cup; flagged for review when it determines a weight
    "floz": 28.4130625,  # UK fluid ounce
    "pint": 568.26125,  # UK pint
}
COUNT_UNITS = {
    "each", "clove", "slice", "tin", "can", "pack", "packet", "bunch", "pinch", "handful", "sprig", "stick",
    "sheet", "jar", "bag", "bottle", "head", "fillet", "piece", "rasher", "leaf", "pot", "punnet", "box", "sachet",
    "cube", "knob", "dash", "drop", "stalk", "ball", "bulb", "loaf",
}
# Units where a quantity is not meaningful for stock arithmetic (e.g. "a pinch of salt").
NON_STOCK_UNITS = {"pinch", "dash", "drop", "handful", "knob"}

ALIASES = {
    "g": "g", "gr": "g", "gram": "g", "grams": "g", "gramme": "g", "grammes": "g",
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg", "kilogram": "kg", "kilograms": "kg",
    "mg": "mg", "milligram": "mg", "milligrams": "mg",
    "oz": "oz", "ounce": "oz", "ounces": "oz",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "ml": "ml", "mls": "ml", "millilitre": "ml", "millilitres": "ml", "milliliter": "ml", "milliliters": "ml",
    "cl": "cl", "dl": "dl",
    "l": "l", "litre": "l", "litres": "l", "liter": "l", "liters": "l", "ltr": "l",
    "tsp": "tsp", "tsps": "tsp", "teaspoon": "tsp", "teaspoons": "tsp", "t": "tsp",
    "tbsp": "tbsp", "tbsps": "tbsp", "tbs": "tbsp", "tbl": "tbsp", "tablespoon": "tbsp", "tablespoons": "tbsp",
    "dsp": "dsp", "dessertspoon": "dsp", "dessertspoons": "dsp",
    "cup": "cup", "cups": "cup", "c": "cup",
    "floz": "floz", "fl oz": "floz", "fl. oz": "floz", "fluid ounce": "floz", "fluid ounces": "floz",
    "pint": "pint", "pints": "pint", "pt": "pint",
    "each": "each", "ea": "each", "x": "each", "whole": "each", "item": "each", "items": "each",
}
for _u in COUNT_UNITS:
    ALIASES.setdefault(_u, _u)
    ALIASES.setdefault(_u + "s", _u)
    ALIASES.setdefault(_u + "es", _u)
ALIASES.update({"leaves": "leaf", "loaves": "loaf", "pinches": "pinch", "bunches": "bunch", "dashes": "dash",
                "punnets": "punnet", "boxes": "box", "knobs": "knob", "cans": "can", "tins": "tin"})

UNICODE_FRACTIONS = {
    "½": "1/2", "⅓": "1/3", "⅔": "2/3", "¼": "1/4", "¾": "3/4", "⅕": "1/5", "⅖": "2/5", "⅗": "3/5",
    "⅘": "4/5", "⅙": "1/6", "⅚": "5/6", "⅛": "1/8", "⅜": "3/8", "⅝": "5/8", "⅞": "7/8",
}


def canonical_unit(token: str | None) -> str | None:
    if token is None:
        return None
    t = token.strip().lower().rstrip(".")
    t = re.sub(r"\s+", " ", t)
    return ALIASES.get(t)


def unit_kind(unit: str | None) -> str | None:
    if unit is None:
        return None
    if unit in MASS:
        return "mass"
    if unit in VOLUME:
        return "volume"
    if unit in COUNT_UNITS:
        return "count"
    return None


def base_unit_for(unit: str | None) -> str | None:
    return {"mass": "g", "volume": "ml", "count": "each"}.get(unit_kind(unit) or "")


@dataclass
class Conversion:
    quantity: float | None
    unit: str | None
    factor: float | None  # target units per source unit
    note: str | None = None
    needs_confirmation: bool = False


@dataclass
class FoodFactors:
    """Ingredient-specific conversion data, taken from a Food row."""

    density_g_per_ml: float | None = None
    each_weight_g: float | None = None
    each_weight_confirmed: bool = False
    # e.g. {"cup": 125.0, "tin": 400.0, "clove": 5.0} grams per unit; explicitly entered/confirmed
    unit_grams: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_food(cls, food) -> "FoodFactors":
        if food is None:
            return cls()
        grams = {}
        for k, v in (food.conversions or {}).items():
            g = v.get("g") if isinstance(v, dict) else v
            if g:
                grams[k] = float(g)
        return cls(food.density_g_per_ml, food.each_weight_g, bool(food.each_weight_confirmed), grams)


def convert(quantity: float, unit: str, target: str, factors: FoodFactors | None = None) -> Conversion:
    """Convert `quantity unit` into `target` unit (g, ml, each, or any mass/volume unit)."""
    factors = factors or FoodFactors()
    src_kind, dst_kind = unit_kind(unit), unit_kind(target)
    if src_kind is None or dst_kind is None:
        return Conversion(None, target, None, f"Unknown unit '{unit}'", True)
    if unit == target:
        return Conversion(quantity, target, 1.0)
    # Explicit per-unit gram factors win (e.g. 1 cup of flour = 125 g, 1 tin = 400 g)
    if unit in factors.unit_grams:
        grams_per = factors.unit_grams[unit]
        if dst_kind == "mass":
            f = grams_per / MASS[target]
            return Conversion(quantity * f, target, f, f"1 {unit} = {grams_per:g} g (ingredient-specific)")
        if dst_kind == "volume" and factors.density_g_per_ml:
            f = grams_per / factors.density_g_per_ml / VOLUME[target]
            return Conversion(quantity * f, target, f, f"1 {unit} = {grams_per:g} g, density {factors.density_g_per_ml:g} g/ml")
    if src_kind == dst_kind == "mass":
        f = MASS[unit] / MASS[target]
        return Conversion(quantity * f, target, f)
    if src_kind == dst_kind == "volume":
        f = VOLUME[unit] / VOLUME[target]
        note = "US cup (236.6 ml) assumed" if unit == "cup" else None
        return Conversion(quantity * f, target, f, note, unit == "cup")
    if src_kind == "volume" and dst_kind == "mass":
        if factors.density_g_per_ml:
            f = VOLUME[unit] * factors.density_g_per_ml / MASS[target]
            return Conversion(quantity * f, target, f, f"density {factors.density_g_per_ml:g} g/ml")
        return Conversion(None, target, None, f"Needs a weight for 1 {unit} of this ingredient", True)
    if src_kind == "mass" and dst_kind == "volume":
        if factors.density_g_per_ml:
            f = MASS[unit] / factors.density_g_per_ml / VOLUME[target]
            return Conversion(quantity * f, target, f, f"density {factors.density_g_per_ml:g} g/ml")
        return Conversion(None, target, None, "Needs a density to convert weight to volume", True)
    if src_kind == "count" and dst_kind == "count":
        if unit == target or target == "each":
            return Conversion(quantity, target, 1.0, None if unit in ("each", target) else f"counted as {unit}")
        return Conversion(None, target, None, f"Cannot convert {unit} to {target}", True)
    if src_kind == "count" and dst_kind == "mass":
        if unit == "each" and factors.each_weight_g and factors.each_weight_confirmed:
            f = factors.each_weight_g / MASS[target]
            return Conversion(quantity * f, target, f, f"1 each = {factors.each_weight_g:g} g (confirmed)")
        return Conversion(None, target, None, f"A count ({unit}) is not converted to weight without a confirmed weight", True)
    if src_kind == "mass" and dst_kind == "count":
        if target == "each" and factors.each_weight_g and factors.each_weight_confirmed:
            f = MASS[unit] / factors.each_weight_g
            return Conversion(quantity * f, target, f, f"1 each = {factors.each_weight_g:g} g (confirmed)")
        return Conversion(None, target, None, "Weight cannot be converted to a count without a confirmed weight", True)
    if src_kind == "count" and dst_kind == "volume":
        return Conversion(None, target, None, f"A count ({unit}) is not converted to volume", True)
    if src_kind == "volume" and dst_kind == "count":
        return Conversion(None, target, None, "Volume cannot be converted to a count", True)
    return Conversion(None, target, None, "Unsupported conversion", True)


def to_base(quantity: float, unit: str, factors: FoodFactors | None = None, prefer: str | None = None) -> Conversion:
    """Normalise to g / ml / each. `prefer` lets the caller ask for the pantry's base unit."""
    if prefer:
        c = convert(quantity, unit, prefer, factors)
        if c.quantity is not None:
            return c
    base = base_unit_for(unit)
    if base is None:
        return Conversion(None, None, None, f"Unknown unit '{unit}'", True)
    return convert(quantity, unit, base, factors)


def to_grams(quantity: float, unit: str, factors: FoodFactors | None = None) -> Conversion:
    return convert(quantity, unit, "g", factors)


# ---------------------------------------------------------------------------------------------------------------
# Quantity / ingredient-line parsing
# ---------------------------------------------------------------------------------------------------------------

_NUM = r"(?:\d+\s+\d+/\d+|\d+/\d+|\d+(?:[.,]\d+)?)"
_QTY_RE = re.compile(rf"^\s*(?P<q1>{_NUM})(?:\s*(?:-|–|to)\s*(?P<q2>{_NUM}))?\s*", re.I)
_WORD_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "half": 0.5,
                 "a half": 0.5, "dozen": 12, "a dozen": 12}


def parse_number(text: str) -> float:
    t = text.strip().replace(",", ".")
    if " " in t:
        whole, frac = t.split(None, 1)
        return float(whole) + float(Fraction(frac))
    if "/" in t:
        return float(Fraction(t))
    return float(t)


def replace_unicode_fractions(text: str) -> str:
    def rep(m: re.Match) -> str:
        pre = m.group(1)
        frac = UNICODE_FRACTIONS[m.group(2)]
        return f"{pre} {frac}" if pre else frac

    return re.sub(r"(\d)?\s*([" + "".join(UNICODE_FRACTIONS) + "])", rep, text)


@dataclass
class ParsedIngredient:
    original_text: str
    name: str
    quantity: float | None = None
    quantity_max: float | None = None
    unit: str | None = None
    preparation: str | None = None
    note: str | None = None
    optional: bool = False
    unresolved: bool = False
    confidence: float = 0.9


_UNIT_TOKENS = sorted(ALIASES.keys(), key=len, reverse=True)
_UNIT_RE = re.compile(r"^(?P<u>" + "|".join(re.escape(u) for u in _UNIT_TOKENS) + r")\.?(?=\s|$|\)|,)\s*(?:of\s+)?", re.I)
_TO_TASTE = re.compile(r"\b(to taste|as needed|to serve|for serving|for garnish|optional)\b", re.I)


def parse_ingredient_line(line: str) -> ParsedIngredient:
    """Deterministic parser for lines such as "1 ½ cups (190 g) plain flour, sifted".

    Missing amounts are left as None and marked unresolved; nothing is guessed.
    """
    original = re.sub(r"\s+", " ", line).strip()
    text = replace_unicode_fractions(original)
    text = re.sub(r"^[-•*·▢□]\s*", "", text)
    optional = bool(re.search(r"\(optional\)|,\s*optional\b|^optional:", text, re.I))
    text = re.sub(r"\(optional\)|,\s*optional\b|^optional:\s*", "", text, flags=re.I).strip()

    qty = qmax = None
    unit = None
    m = _QTY_RE.match(text)
    if m:
        qty = parse_number(m.group("q1"))
        qmax = parse_number(m.group("q2")) if m.group("q2") else None
        text = text[m.end():]
        # handle "1 x 400g tin" -> treat as count of tins with note
        mx = re.match(r"^x\s*(" + _NUM + r")\s*(g|kg|ml|l)\b\s*", text, re.I)
        if mx:
            pack = f"{mx.group(1)}{mx.group(2)}"
            text = text[mx.end():]
            mu = _UNIT_RE.match(text)
            unit = canonical_unit(mu.group("u")) if mu else "each"
            if mu:
                text = text[mu.end():]
            text = f"{text} ({pack} pack)"
        else:
            # glued unit, e.g. "500g"
            mu = _UNIT_RE.match(text)
            if mu:
                unit = canonical_unit(mu.group("u"))
                text = text[mu.end():]
    else:
        lower = text.lower()
        for word, val in sorted(_WORD_NUMBERS.items(), key=lambda kv: -len(kv[0])):
            if lower.startswith(word + " "):
                rest = text[len(word) + 1:]
                mu = _UNIT_RE.match(rest)
                vague = re.match(r"^(little|few|bit|splash|drizzle|good|generous|couple|small amount)\b", rest, re.I)
                if not vague:
                    qty = float(val)
                    text = rest
                    if mu:
                        unit = canonical_unit(mu.group("u"))
                        text = rest[mu.end():]
                break

    note = None
    # Strip a parenthetical alternative measure, e.g. "(7 oz)" or "(190 g)"
    paren = re.match(r"^\(([^)]*)\)\s*", text)
    if paren:
        note = paren.group(1).strip()
        text = text[paren.end():]
        # if the main quantity was a cup/US measure and the parenthetical has metric weight, prefer the metric
        alt = re.match(rf"^({_NUM})\s*(g|kg|ml|l)\b", replace_unicode_fractions(note), re.I)
        if alt and unit in ("cup", "oz", "lb", "floz", "pint", None):
            note = f"source: {qty:g} {unit or ''}".strip() if qty is not None else note
            qty = parse_number(alt.group(1))
            qmax = None
            unit = canonical_unit(alt.group(2))

    text = text.strip().strip(",").strip()
    prep = None
    if "," in text:
        name_part, prep = text.split(",", 1)
        prep = prep.strip() or None
    else:
        name_part = text
    name = re.sub(r"\s+", " ", name_part).strip()
    name = re.sub(r"^of\s+", "", name, flags=re.I)

    if qty is not None and unit is None:
        unit = "each"
    unresolved = qty is None
    if unresolved and _TO_TASTE.search(original):
        # "salt, to taste" is legitimately unquantified; still unresolved for stock/nutrition purposes
        pass
    confidence = 0.95 if (qty is not None and unit and unit != "each") else 0.8 if qty is not None else 0.5
    return ParsedIngredient(
        original_text=original, name=name or original, quantity=qty, quantity_max=qmax, unit=unit,
        preparation=prep, note=note, optional=optional, unresolved=unresolved, confidence=confidence,
    )


def parse_quantity_text(text: str) -> tuple[float | None, str | None]:
    """Parse a package size like '500g', '1.5 L', '6 x 330ml' (returns total)."""
    if not text:
        return None, None
    t = replace_unicode_fractions(text.lower()).replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(kg|g|ml|cl|l)\b", t)
    if m:
        return float(m.group(1)) * float(m.group(2)), canonical_unit(m.group(3))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|g|mg|ml|cl|l|litre|litres)\b", t)
    if m:
        return float(m.group(1)), canonical_unit(m.group(2))
    return None, None


# ---------------------------------------------------------------------------------------------------------------
# UK display helpers
# ---------------------------------------------------------------------------------------------------------------

def uk_display(quantity: float | None, unit: str | None) -> str:
    """Human-friendly UK metric display, using tsp/tbsp for small volumes where practical."""
    if quantity is None:
        return "—"
    if unit == "ml" and quantity < 60:
        tbsp = quantity / 15
        if abs(tbsp - round(tbsp * 2) / 2) < 0.05 and tbsp >= 1:
            return f"{_fmt(round(tbsp * 2) / 2)} tbsp"
        tsp = quantity / 5
        if abs(tsp - round(tsp * 4) / 4) < 0.05 and tsp >= 0.25:
            return f"{_fmt(round(tsp * 4) / 4)} tsp"
    if unit == "g" and quantity >= 1000:
        return f"{_dec(quantity / 1000)} kg"
    if unit == "ml" and quantity >= 1000:
        return f"{_dec(quantity / 1000)} L"
    if unit == "l":
        return f"{_dec(quantity)} L"
    if unit in ("g", "ml"):
        return f"{_dec(quantity, 0 if quantity >= 10 else 1)} {unit}"
    if unit == "each":
        return _fmt(quantity)
    return f"{_fmt(quantity)} {unit or ''}".strip()


def _dec(x: float, places: int = 2) -> str:
    """Decimal display for metric weights/volumes (no vulgar fractions)."""
    return f"{x:.{places}f}".rstrip("0").rstrip(".") if places else str(int(round(x)))


def _fmt(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    for d, s in ((0.25, "¼"), (0.5, "½"), (0.75, "¾")):
        if abs(x - int(x) - d) < 1e-9:
            return (str(int(x)) if int(x) else "") + s
    return f"{x:.2f}".rstrip("0").rstrip(".")


def normalise_metric(quantity: float | None, unit: str | None, factors: FoodFactors | None = None) -> Conversion:
    """Convert imperial/US recipe units into UK metric for storage, keeping tsp/tbsp as they are."""
    if quantity is None or unit is None:
        return Conversion(quantity, unit, None)
    if unit in ("oz", "lb", "kg", "mg"):
        c = convert(quantity, unit, "g", factors)
        c.quantity = _round_sensible(c.quantity)
        return c
    if unit in ("floz", "pint", "cl", "dl"):
        c = convert(quantity, unit, "ml", factors)
        c.quantity = _round_sensible(c.quantity)
        return c
    if unit == "cup":
        g = convert(quantity, unit, "g", factors)
        if g.quantity is not None and not g.needs_confirmation:
            g.quantity = _round_sensible(g.quantity)
            return g
        ml = convert(quantity, unit, "ml", factors)
        ml.quantity = _round_sensible(ml.quantity)
        ml.needs_confirmation = True
        ml.note = "US cup converted by volume; confirm the weight for this ingredient"
        return ml
    if unit == "l":
        return Conversion(quantity * 1000, "ml", 1000)
    return Conversion(quantity, unit, 1.0)


def _round_sensible(x: float | None) -> float | None:
    if x is None:
        return None
    if x >= 100:
        return float(round(x / 5) * 5)
    if x >= 10:
        return float(round(x))
    return round(x, 1)


_F_RE = re.compile(r"(\d{3})\s*°?\s*(?:degrees\s*)?F\b", re.I)


def normalise_temperatures(text: str) -> str:
    """Rewrite '350°F' as '180°C (350°F)'. Celsius values are left untouched."""

    def rep(m: re.Match) -> str:
        f = int(m.group(1))
        c = round((f - 32) * 5 / 9 / 5) * 5
        return f"{c}°C ({f}°F)"

    if re.search(r"°\s*C|degrees C\b", text) and _F_RE.search(text):
        return text  # both given; keep source wording
    return _F_RE.sub(rep, text)
