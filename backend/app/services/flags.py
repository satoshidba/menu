"""Possible-allergen and toddler-preparation flags.

These are *prompts for a human to check the label*, never assurances. Keyword matching is deliberately broad.
"""
from __future__ import annotations

import re

NHS_FOODS_TO_AVOID = "https://www.nhs.uk/baby/weaning-and-feeding/foods-to-avoid-giving-babies-and-young-children/"
FSA_ALLERGENS = "https://www.food.gov.uk/safety-hygiene/food-allergy-and-intolerance"

# The 14 UK major allergens -> indicative keywords
ALLERGEN_KEYWORDS = {
    "celery": r"celery|celeriac",
    "gluten": r"wheat|flour|pasta|spaghetti|farfalle|penne|noodle|bread|breadcrumb|couscous|barley|rye|spelt|semolina|"
              r"bulgur|seitan|stock cube|soy sauce|oats|porridge|tortilla|pitta|naan|cracker|biscuit",
    "crustaceans": r"prawn|shrimp|crab|lobster|crayfish|langoustine",
    "eggs": r"\beggs?\b|mayonnaise|meringue|egg noodle",
    "fish": r"\bfish\b|salmon|tuna|cod|haddock|anchov|sardine|mackerel|fish sauce|worcestershire",
    "lupin": r"lupin",
    "milk": r"milk|butter|cheese|cheddar|parmesan|mozzarella|cream|yogh?urt|ghee|cr[eè]me fra[iî]che|paneer|ricotta",
    "molluscs": r"mussel|oyster|squid|clam|scallop|octopus|calamari|snail",
    "mustard": r"mustard",
    "tree nuts": r"almond|hazelnut|walnut|cashew|pecan|pistachio|macadamia|brazil nut|pine nut",
    "peanuts": r"peanut|groundnut|satay",
    "sesame": r"sesame|tahini|hummus|houmous",
    "soya": r"\bsoy|soya|tofu|edamame|miso|tempeh",
    "sulphites": r"\bwine\b|vinegar|dried apricot|sulphite|sulfite",
}

# Practical preparation notes for young children (adults decide; links to NHS guidance)
TODDLER_KEYWORDS = [
    (r"\bsalt\b|stock cube|stock pot|soy sauce|bacon|ham|chorizo|salami|olives|anchov|feta|halloumi|gravy granules",
     "Salty ingredient: the NHS advises keeping salt low for young children."),
    (r"whole nuts?|peanuts?\b|almonds?\b|cashews?\b|walnuts?\b", "Whole nuts are a choking risk for under-5s; grind or use nut butter."),
    (r"grapes?|cherry tomato|blueberr|olives", "Small round foods: cut lengthways/quarter for young children."),
    (r"chilli|chili|cayenne|hot sauce|sriracha|jalape", "Spicy: consider a milder portion for the toddlers."),
    (r"honey", "Honey is not suitable for babies under 12 months."),
    (r"raw egg|runny|soft-boiled|mayonnaise", "Runny or raw egg: use eggs with the British Lion mark for young children."),
    (r"\bsugar\b|syrup|caster|icing", "Added sugar: the NHS advises limiting sugar for young children."),
    (r"swordfish|marlin|shark", "Swordfish, marlin and shark are not recommended for children."),
    (r"rice drink|rice milk", "Rice drinks are not recommended for children under 5."),
    (r"popcorn|marshmallow|hard sweet", "Choking risk for young children."),
]


def possible_allergens(texts: list[str], declared: list[str] | None = None) -> list[dict]:
    """Return [{allergen, matched: [ingredient...], basis: 'keyword'|'label'}]."""
    hits: dict[str, dict] = {}
    for a in declared or []:
        key = a.lower().replace("en:", "")
        hits.setdefault(key, {"allergen": key, "matched": [], "basis": "label"})
    for t in texts:
        low = t.lower()
        for allergen, pattern in ALLERGEN_KEYWORDS.items():
            if re.search(pattern, low):
                h = hits.setdefault(allergen, {"allergen": allergen, "matched": [], "basis": "keyword"})
                if t not in h["matched"]:
                    h["matched"].append(t)
    return sorted(hits.values(), key=lambda h: h["allergen"])


def person_conflicts(people: list, ingredient_names: list[str], allergens: list[dict]) -> list[dict]:
    """Flag allergies/dislikes/preferences that *might* conflict. Never asserts safety."""
    out = []
    names_low = [n.lower() for n in ingredient_names]
    present = {a["allergen"] for a in allergens}
    meat = re.compile(r"chicken|beef|pork|lamb|bacon|ham|turkey|mince|sausage|chorizo|duck|venison|gelatine|anchov|fish|salmon|tuna|prawn")
    for p in people:
        for allergy in p.allergies or []:
            a = allergy.lower()
            matched = [n for n in names_low if a in n]
            if any(a in x or x in a for x in present) or matched:
                out.append({"person": p.name, "person_id": str(p.id), "type": "allergy", "item": allergy,
                            "message": f"Possible {allergy} — check every label before serving {p.name}."})
        for d in p.dislikes or []:
            if any(d.lower() in n for n in names_low):
                out.append({"person": p.name, "person_id": str(p.id), "type": "dislike", "item": d,
                            "message": f"{p.name} dislikes {d}."})
        prefs = [x.lower() for x in (p.dietary_preferences or [])]
        if ("vegetarian" in prefs or "vegan" in prefs) and any(meat.search(n) for n in names_low):
            out.append({"person": p.name, "person_id": str(p.id), "type": "preference", "item": "vegetarian",
                        "message": f"Contains meat or fish; {p.name} prefers vegetarian."})
    return out


def toddler_flags(texts: list[str]) -> list[dict]:
    out = []
    for pattern, message in TODDLER_KEYWORDS:
        matched = [t for t in texts if re.search(pattern, t.lower())]
        if matched:
            out.append({"message": message, "matched": matched, "source": NHS_FOODS_TO_AVOID})
    return out
