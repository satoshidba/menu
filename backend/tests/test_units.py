"""Unit parsing and conversion: metric normalisation, ingredient-specific factors, counts never become grams."""
import pytest

from app.services.units import (FoodFactors, convert, normalise_metric, normalise_temperatures, parse_ingredient_line,
                                parse_quantity_text, to_base, uk_display)


def test_mass_and_volume_conversions():
    assert convert(1.5, "kg", "g").quantity == 1500
    assert convert(250, "ml", "l").quantity == 0.25
    assert convert(2, "tbsp", "ml").quantity == 30
    assert convert(3, "tsp", "tbsp").quantity == pytest.approx(1)
    assert round(convert(8, "oz", "g").quantity, 1) == 226.8


def test_count_is_never_silently_converted_to_grams():
    c = convert(1, "each", "g")
    assert c.quantity is None and c.needs_confirmation
    c = convert(2, "clove", "g")
    assert c.quantity is None and c.needs_confirmation
    # an unconfirmed per-item weight is not enough
    assert convert(1, "each", "g", FoodFactors(each_weight_g=150, each_weight_confirmed=False)).quantity is None
    # a confirmed weight is
    assert convert(2, "each", "g", FoodFactors(each_weight_g=150, each_weight_confirmed=True)).quantity == 300


def test_cup_to_weight_needs_ingredient_factor():
    c = convert(1, "cup", "g")
    assert c.quantity is None and c.needs_confirmation
    assert convert(2, "cup", "g", FoodFactors(unit_grams={"cup": 125})).quantity == 250
    assert convert(1, "cup", "g", FoodFactors(density_g_per_ml=1.0)).quantity == pytest.approx(236.6, 0.01)
    n = normalise_metric(1, "cup")
    assert n.unit == "ml" and n.needs_confirmation


def test_volume_to_mass_with_density():
    assert convert(100, "ml", "g", FoodFactors(density_g_per_ml=0.91)).quantity == pytest.approx(91)
    assert convert(100, "ml", "g").quantity is None


def test_to_base_prefers_pantry_unit():
    assert to_base(2, "tbsp", prefer="ml").quantity == 30
    assert to_base(500, "g").unit == "g"
    assert to_base(3, "each").unit == "each"


@pytest.mark.parametrize("line,qty,unit,name,prep", [
    ("500g dried pasta", 500, "g", "dried pasta", None),
    ("1 ½ cups plain flour, sifted", 1.5, "cup", "plain flour", "sifted"),
    ("2 tbsp olive oil", 2, "tbsp", "olive oil", None),
    ("1 onion, finely chopped", 1, "each", "onion", "finely chopped"),
    ("2 cloves garlic, crushed", 2, "clove", "garlic", "crushed"),
    ("¼ tsp chilli flakes", 0.25, "tsp", "chilli flakes", None),
    ("200 g (7 oz) spinach", 200, "g", "spinach", None),
    ("1 cup (190 g) caster sugar", 190, "g", "caster sugar", None),
    ("a pinch of salt", 1, "pinch", "salt", None),
])
def test_parse_ingredient_lines(line, qty, unit, name, prep):
    p = parse_ingredient_line(line)
    assert p.quantity == pytest.approx(qty)
    assert p.unit == unit
    assert p.name.lower() == name
    assert p.preparation == prep
    assert p.original_text == line


def test_missing_amount_is_unresolved_not_guessed():
    p = parse_ingredient_line("Salt and pepper, to taste")
    assert p.quantity is None and p.unresolved
    p = parse_ingredient_line("Fresh basil leaves")
    assert p.quantity is None and p.unresolved


def test_ranges_and_optional():
    p = parse_ingredient_line("1-2 red chillies (optional)")
    assert p.quantity == 1 and p.quantity_max == 2 and p.optional


def test_package_sizes():
    assert parse_quantity_text("500g") == (500, "g")
    assert parse_quantity_text("2.27 L") == (2.27, "l")
    assert parse_quantity_text("6 x 330ml") == (1980, "ml")


def test_temperatures_and_display():
    assert normalise_temperatures("Bake at 350°F for 20 minutes") == "Bake at 175°C (350°F) for 20 minutes"
    assert normalise_temperatures("Heat to 200°C") == "Heat to 200°C"
    assert uk_display(15, "ml") == "1 tbsp"
    assert uk_display(2.5, "ml") == "½ tsp"
    assert uk_display(1500, "g") == "1.5 kg"
    assert uk_display(None, "g") == "—"
