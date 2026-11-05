"""Sample household data for trying the app. Contains no personal information: all names are fictional and the
recipes were written for this project. Product names are generic, not real brands."""
from __future__ import annotations

import json
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

from .auth import hash_password
from .db import SessionLocal
from .models import (Absence, Food, Household, MealPlan, PersonProfile, PetProfile, Product, Recipe, RecipeMedia, User)
from .services import cofid
from .services.prices import record_purchase
from .services.recipes import IngredientIn, RecipeContent, StepIn, build_version
from .services.stock import create_lot

SEED_DIR = Path(__file__).resolve().parent / "seed"

RECIPES = [
    {
        "title": "Chicken and spinach pasta with tomato", "servings": 4, "prep": 10, "cook": 20, "cuisine": "Italian",
        "tags": ["weeknight", "family"], "image": "pasta.jpg",
        "description": "A quick tomato sauce with wilted spinach and pan-fried chicken. Mild enough for small children.",
        "ingredients": [("500 g dried pasta (penne or farfalle)", "Dried pasta", 500, "g"), ("2 chicken breasts, diced", "Chicken breast", 300, "g"),
                        ("1 tbsp olive oil", "Olive oil", 1, "tbsp"), ("1 onion, finely chopped", "Onion", 1, "each"),
                        ("2 cloves garlic, crushed", "Garlic", 6, "g"), ("400 g tin chopped tomatoes", "Chopped tomatoes (tinned)", 400, "g"),
                        ("100 g spinach", "Spinach", 100, "g"), ("30 g Parmesan, grated, to serve", "Parmesan", 30, "g")],
        "steps": ["Bring a large pan of water to the boil and cook the pasta for 10–12 minutes until just tender. Drain, keeping a cup of the cooking water.",
                  "Meanwhile heat the oil in a wide pan over a medium heat. Fry the chicken for 6–8 minutes until cooked through with no pink meat. Lift out onto a plate.",
                  "Add the onion to the pan and cook gently for 5 minutes until soft. Stir in the garlic for 1 minute.",
                  "Pour in the tomatoes, simmer for 8 minutes, then stir in the spinach until it wilts.",
                  "Return the chicken, toss with the pasta and a splash of the cooking water. Serve with Parmesan; season adult portions separately."],
        "timers": [600, 420, 300, 480, None],
    },
    {
        "title": "Red lentil and squash dal", "servings": 4, "prep": 15, "cook": 30, "cuisine": "Indian", "tags": ["vegetarian", "batch cook"],
        "image": "dal.jpg", "description": "Soft, gently spiced lentils with butternut squash and coconut milk.",
        "ingredients": [("250 g red lentils, rinsed", "Red lentils", 250, "g"), ("300 g butternut squash, diced", "Butternut squash", 300, "g"),
                        ("1 onion, chopped", "Onion", 1, "each"), ("1 tsp cumin seeds", "Cumin seeds", 1, "tsp"),
                        ("400 ml coconut milk", "Coconut milk", 400, "ml"), ("100 g spinach", "Spinach", 100, "g"),
                        ("1 tbsp lemon juice", "Lemon juice", 1, "tbsp"), ("200 g basmati rice, to serve", "Basmati rice", 200, "g")],
        "steps": ["Toast the cumin seeds in a dry pan for 30 seconds, then add a splash of oil and the onion. Cook for 6 minutes.",
                  "Add the lentils, squash, coconut milk and 500 ml water. Simmer for 25 minutes, stirring often, until thick.",
                  "Meanwhile cook the rice according to the pack.",
                  "Stir the spinach and lemon juice into the dal and serve with the rice."],
        "timers": [None, 1500, None, None],
    },
    {
        "title": "Banana porridge", "servings": 4, "prep": 2, "cook": 6, "cuisine": "British", "tags": ["breakfast", "toddler friendly"],
        "image": "porridge.jpg", "description": "Creamy oats with mashed banana for natural sweetness.",
        "ingredients": [("160 g porridge oats", "Porridge oats", 160, "g"), ("800 ml semi-skimmed milk", "Semi-skimmed milk", 800, "ml"),
                        ("2 bananas", "Bananas", 2, "each")],
        "steps": ["Put the oats and milk in a pan over a medium heat.", "Stir for 5–6 minutes until thick and creamy.",
                  "Mash one banana into the porridge and slice the other on top."],
        "timers": [None, 360, None],
    },
    {
        "title": "Broccoli, tomato and cheddar frittata", "servings": 4, "prep": 10, "cook": 20, "cuisine": "British", "tags": ["vegetarian", "lunch"],
        "image": "frittata.jpg", "description": "A thick oven-baked omelette; good cold in lunchboxes.",
        "ingredients": [("6 eggs", "Eggs", 6, "each"), ("150 g broccoli, small florets", "Broccoli", 150, "g"),
                        ("80 g Cheddar, grated", "Cheddar", 80, "g"), ("2 tomatoes, sliced", "Tomatoes", 2, "each"), ("1 tbsp olive oil", "Olive oil", 1, "tbsp"),
                        ("Black pepper, to taste", "Black pepper", None, None)],
        "steps": ["Heat the oven to 190°C (170°C fan). Steam the broccoli for 3 minutes.",
                  "Whisk the eggs with most of the cheese.",
                  "Heat the oil in an ovenproof pan, add the broccoli and pour in the eggs. Lay the tomato slices on top and scatter over the remaining cheese.",
                  "Bake for 15–18 minutes until set all the way through."],
        "timers": [180, None, None, 1020],
    },
]

PRODUCTS = [
    # name, brand, food, size, unit, location, nutrition per 100 (label, verified), min stock
    ("Farfalle", "Store brand", "Dried pasta", 500, "g", "cupboard",
     {"energy_kcal": 359, "fat_g": 2.0, "saturates_g": 0.5, "carbohydrate_g": 70.0, "sugars_g": 3.5, "fibre_g": 3.0, "protein_g": 12.5, "salt_g": 0.01}, None),
    ("Chopped Tomatoes", "Store brand", "Chopped tomatoes (tinned)", 400, "g", "cupboard", None, 400),
    ("Semi Skimmed Milk", "Store brand", "Semi-skimmed milk", 2272, "ml", "fridge", None, 1000),
    ("Free Range Eggs x6", "Store brand", "Eggs", 6, "each", "fridge", None, 6),
]


def _seed_history(db, hid, actor, products, foods, dog, t: date) -> None:
    from .services.stock import EventInput, record_event

    def at(days_ago: int, hour: int = 8) -> datetime:
        return datetime.combine(t - timedelta(days=days_ago), datetime.min.time(), timezone.utc) + timedelta(hours=hour)

    def bought(prod, food, qty, unit, days_ago, pence, store, loc="fridge"):
        lot = create_lot(db, hid, product=prod, food=food, quantity=qty, unit=unit, location=loc, store=store,
                         purchased_on=t - timedelta(days=days_ago), price_paid_pence=pence, actor_user_id=actor, occurred_at=at(days_ago, 17))
        pur = record_purchase(db, hid, product_id=prod.id if prod else None, food_id=prod.food_id if prod else food.id, store=store,
                              retailer=store, purchased_at=at(days_ago, 17), quantity_base=lot.initial_quantity_base,
                              base_unit=lot.base_unit, paid_price_pence=pence, shelf_price_pence=pence, is_pet_food=lot.is_pet_food)
        lot.purchase_id = pur.id
        return lot

    def use(lot, qty, days_ago, kind="consumption", reason=None):
        qty = min(qty, lot.quantity_base)
        if qty > 0:
            record_event(db, lot.id, hid, EventInput(type=kind, quantity=qty, unit=lot.base_unit, reason=reason, occurred_at=at(days_ago, 19)),
                         actor_user_id=actor)

    milk, stores = products["Semi Skimmed Milk"], ["Lidl", "Tesco", "Lidl", "Sainsbury's", "Lidl"]
    wobble = [1.0, 1.15, 0.9, 1.05, 0.95, 1.1, 0.85]
    for week in range(10, 0, -1):
        start = week * 7 + 2
        # milk: two 4-pint bottles a week, a little over half a litre a day
        lots = [bought(milk, None, 1, "pack", start, 155 + (week % 3) * 5, stores[week % 5]) for _ in range(2)]
        for i in range(7):
            lot = lots[0] if lots[0].quantity_base > 0 else lots[1]
            use(lot, round(620 * wobble[(i + week) % 7]), start - i - 1)
        for lot in lots:
            use(lot, lot.quantity_base, start - 7, "waste", "Past its date")
        # bananas: a bunch of 8 a week, and the odd one goes brown
        b = bought(None, foods["Bananas"], 8, "each", start, 120 + (week % 2) * 5, stores[(week + 1) % 5], "cupboard")
        for i in range(0, 7, 2):
            use(b, 2, start - i - 1)
        if week % 3 == 0:
            use(b, b.quantity_base, start - 7, "waste", "Too ripe")
        else:
            use(b, b.quantity_base, start - 7)
        # spinach every fortnight: half of it tends to get thrown away
        if week % 2 == 0:
            sp = bought(None, foods["Spinach"], 200, "g", start, 150, "Lidl")
            use(sp, 100, start - 2)
            use(sp, 100, start - 6, "waste", "Wilted")
    # dog food: a 2 kg bag lasts about a fortnight
    for days_ago in (66, 52, 38, 24):
        bag = bought(dog, None, 1, "pack", days_ago, 899 if days_ago > 40 else 949, "Tesco", "other")
        for d in range(0, 14, 2):
            use(bag, 290, days_ago - d - 1)
        use(bag, bag.quantity_base, days_ago - 14)
    db.flush()


def seed_demo() -> dict:
    db = SessionLocal()
    try:
        if db.scalar(select(func.count()).select_from(User)):
            household = db.scalar(select(Household))
            admin = db.scalar(select(User).where(User.role == "admin"))
            created_users = False
        else:
            household = Household(name="Demo household", timezone="Europe/London")
            db.add(household)
            db.flush()
            pw = os.environ.get("LARDER_DEMO_PASSWORD") or secrets.token_urlsafe(12)
            admin = User(household_id=household.id, email="alex@example.com", display_name="Alex", password_hash=hash_password(pw), role="admin")
            sam = User(household_id=household.id, email="sam@example.com", display_name="Sam", password_hash=hash_password(pw), role="member")
            db.add_all([admin, sam])
            db.flush()
            db.add_all([PersonProfile(household_id=household.id, user_id=admin.id, name="Alex", kind="adult"),
                        PersonProfile(household_id=household.id, user_id=sam.id, name="Sam", kind="adult", dietary_preferences=["pescatarian"])])
            created_users = pw
        hid = household.id
        if not db.scalar(select(PersonProfile).where(PersonProfile.household_id == hid, PersonProfile.kind == "child")):
            today = date.today()
            db.add_all([PersonProfile(household_id=hid, name="Mia", kind="child", birth_month=date(today.year - 2, 3, 1), default_portion=0.5,
                                      dislikes=["mushrooms"]),
                        PersonProfile(household_id=hid, name="Leo", kind="child", birth_month=date(today.year - 3, 9, 1), default_portion=0.5,
                                      allergies=["sesame"])])
        if not db.scalar(select(PetProfile).where(PetProfile.household_id == hid)):
            db.add(PetProfile(household_id=hid, name="Biscuit", species="dog",
                              feeding_plan="As agreed with our vet: see the note on the fridge.", feeding_plan_source="Household vet"))
        db.flush()

        cofid.load_sample(db)
        data = json.loads((SEED_DIR / "cofid_subset.json").read_text())
        from .api.catalog import _map_cofid
        from .auth import Principal

        p = Principal(household_id=hid, user=admin)
        foods = {}
        for sf in data["seed_foods"]:
            f = db.scalar(select(Food).where(Food.household_id == hid, Food.name == sf["name"]))
            if f is None:
                f = Food(household_id=hid, name=sf["name"], category=sf["category"], default_unit=sf["default_unit"],
                         density_g_per_ml=sf["density_g_per_ml"],
                         notes="Density is an approximate kitchen value." if sf["density_g_per_ml"] else None)
                db.add(f)
                db.flush()
                _map_cofid(db, p, f, sf["cofid_code"])
            foods[f.name] = f
        foods["Dried pasta"].min_stock_base = 500
        # A household-confirmed per-item weight (sample value) so banana counts convert; eggs are left unconfirmed
        # to show how unknown nutrition is reported.
        foods["Bananas"].each_weight_g, foods["Bananas"].each_weight_confirmed = 100.0, True
        db.flush()

        products = {}
        for name, brand, food, size, unit, loc, label, minimum in PRODUCTS:
            prod = db.scalar(select(Product).where(Product.household_id == hid, Product.name == name))
            if prod is None:
                prod = Product(household_id=hid, food_id=foods[food].id, name=name, brand=brand, package_quantity=size, package_unit=unit,
                               package_base_quantity=size, base_unit=unit, category=foods[food].category, provenance="manual",
                               min_stock_base=minimum)
                db.add(prod)
                db.flush()
                if label:
                    from .models import NutritionRecord
                    db.add(NutritionRecord(household_id=hid, product_id=prod.id, source="label", basis="100g", nutrients=label,
                                           verified=True, confidence=0.95, notes="Typical values from the pack label (sample data)"))
            products[name] = prod

        # A dog food product: separate inventory, never in human nutrition
        dog = db.scalar(select(Product).where(Product.household_id == hid, Product.is_pet_food.is_(True)))
        if dog is None:
            dogfood = Food(household_id=hid, name="Dry dog food", category="Pet", default_unit="g", is_pet_food=True)
            db.add(dogfood)
            db.flush()
            dog = Product(household_id=hid, food_id=dogfood.id, name="Adult Dry Dog Food", brand="Store brand", package_quantity=2,
                          package_unit="kg", package_base_quantity=2000, base_unit="g", is_pet_food=True, category="Pet")
            db.add(dog)
            db.flush()

        now = datetime.now(timezone.utc)
        t = date.today()
        if not db.scalar(select(func.count()).select_from(Recipe).where(Recipe.household_id == hid)):
            lots = [
                (products["Farfalle"], None, 1, "pack", "cupboard", t - timedelta(days=12), t + timedelta(days=200), None, 85, "Sainsbury's"),
                (products["Chopped Tomatoes"], None, 2, "pack", "cupboard", t - timedelta(days=12), t + timedelta(days=500), None, 55, "Tesco"),
                (products["Semi Skimmed Milk"], None, 1, "pack", "fridge", t - timedelta(days=2), None, t + timedelta(days=4), 165, "Lidl"),
                (products["Free Range Eggs x6"], None, 6, "each", "fridge", t - timedelta(days=3), t + timedelta(days=14), None, 189, "Tesco"),
                (None, foods["Spinach"], 200, "g", "fridge", t - timedelta(days=2), None, t + timedelta(days=1), 150, "Lidl"),
                (None, foods["Chicken breast"], 300, "g", "fridge", t - timedelta(days=1), None, t + timedelta(days=2), 375, "Sainsbury's"),
                (None, foods["Onion"], 3, "each", "cupboard", t - timedelta(days=6), None, None, 45, "Lidl"),
                (None, foods["Garlic"], 40, "g", "cupboard", t - timedelta(days=6), None, None, 30, "Lidl"),
                (None, foods["Red lentils"], 500, "g", "cupboard", t - timedelta(days=30), t + timedelta(days=300), None, 120, "Tesco"),
                (None, foods["Porridge oats"], 1000, "g", "cupboard", t - timedelta(days=9), t + timedelta(days=120), None, 95, "Sainsbury's"),
                (None, foods["Bananas"], 5, "each", "cupboard", t - timedelta(days=2), None, None, 80, "Lidl"),
                (None, foods["Broccoli"], 350, "g", "fridge", t - timedelta(days=3), t + timedelta(days=2), None, 69, "Tesco"),
                (None, foods["Cheddar"], 250, "g", "fridge", t - timedelta(days=8), t + timedelta(days=20), None, 250, "Tesco"),
                (None, foods["Olive oil"], 500, "ml", "cupboard", t - timedelta(days=40), t + timedelta(days=400), None, 450, "Sainsbury's"),
                (None, foods["Frozen peas"], 900, "g", "freezer", t - timedelta(days=20), t + timedelta(days=240), None, 150, "Tesco"),
                (dog, None, 1, "pack", "other", t - timedelta(days=10), t + timedelta(days=180), None, 899, "Tesco"),
            ]
            for prod, food, q, unit, loc, bought, bb, ub, price, store in lots:
                lot = create_lot(db, hid, product=prod, food=food, quantity=q, unit=unit, location=loc, purchased_on=bought,
                                 best_before=bb, use_by=ub, store=store, price_paid_pence=price, actor_user_id=admin.id,
                                 occurred_at=datetime.combine(bought, datetime.min.time(), timezone.utc))
                pur = record_purchase(db, hid, product_id=prod.id if prod else None, food_id=(prod.food_id if prod else food.id),
                                      store=store, purchased_at=datetime.combine(bought, datetime.min.time(), timezone.utc),
                                      package_count=1, quantity_base=lot.initial_quantity_base, base_unit=lot.base_unit,
                                      paid_price_pence=price, shelf_price_pence=price, is_pet_food=lot.is_pet_food)
                lot.purchase_id = pur.id
            # Ten weeks of history so Insights and forecasts have something to show
            _seed_history(db, hid, admin.id, products, foods, dog, t)
            # extra price history for pasta (different stores and a multibuy)
            for days, store, paid, promo in ((70, "Tesco", 95, None), (45, "Sainsbury's", 80, "Nectar price"), (25, "Lidl", 75, None)):
                record_purchase(db, hid, product_id=products["Farfalle"].id, food_id=foods["Dried pasta"].id, store=store,
                                purchased_at=now - timedelta(days=days), quantity_base=500, base_unit="g", paid_price_pence=paid,
                                shelf_price_pence=95, promotion=promo)

            img_dir = SEED_DIR / "images"
            for spec in RECIPES:
                r = Recipe(household_id=hid, title=spec["title"])
                db.add(r)
                db.flush()
                content = RecipeContent(
                    title=spec["title"], description=spec["description"], cuisine=spec["cuisine"], tags=spec["tags"],
                    prep_minutes=spec["prep"], cook_minutes=spec["cook"], default_servings=spec["servings"],
                    ingredients=[IngredientIn(original_text=o, name=n, quantity=q, unit=u, food_id=foods[n].id if n in foods else None)
                                 for o, n, q, u in spec["ingredients"]],
                    steps=[StepIn(text=s, timer_seconds=tm) for s, tm in zip(spec["steps"], spec["timers"])],
                    source_name="Larder sample recipes", provenance="manual")
                v = build_version(db, r, content, household_id=hid, label="Original", parent=None, user_id=admin.id)
                r.current_version_id = v.id
                img = img_dir / spec["image"]
                if img.exists():
                    from .services.media import store_image
                    rel, w, h = store_image(img.read_bytes(), f"recipes/{r.id}")
                    credits = json.loads((img_dir / "CREDITS.json").read_text()) if (img_dir / "CREDITS.json").exists() else {}
                    c = credits.get(spec["image"], {})
                    m = RecipeMedia(recipe_id=r.id, household_id=hid, kind="photo", path=rel, width=w, height=h,
                                    source_url=c.get("source_url"), attribution=c.get("attribution"), focal_x=c.get("focal_x", 0.5),
                                    focal_y=c.get("focal_y", 0.5), alt_text=spec["title"], provenance="url" if c else "manual")
                    db.add(m)
                    db.flush()
                    r.hero_media_id = m.id
                if spec["title"].startswith("Chicken"):
                    pasta_version = v
            people = list(db.scalars(select(PersonProfile).where(PersonProfile.household_id == hid)))
            db.add(MealPlan(household_id=hid, date=t, slot="dinner", recipe_version_id=pasta_version.id, title=pasta_version.title,
                            servings=3, participant_ids=[x.id for x in people]))
            db.add(MealPlan(household_id=hid, date=t + timedelta(days=1), slot="breakfast",
                            recipe_version_id=db.scalar(select(Recipe).where(Recipe.title == "Banana porridge")).current_version_id,
                            title="Banana porridge", servings=3, participant_ids=[x.id for x in people]))
            byname = {x.name: x for x in people}
            dal = db.scalar(select(Recipe).where(Recipe.title.like("Red lentil%")))
            db.add(MealPlan(household_id=hid, date=t + timedelta(days=3), slot="dinner", recipe_version_id=dal.current_version_id,
                            title=dal.title, servings=3, participant_ids=[x.id for x in people]))
            db.add_all([
                Absence(household_id=hid, person_id=byname["Sam"].id, start_date=t + timedelta(days=1), end_date=t + timedelta(days=1),
                        slots=["dinner"], reason="social", note="Pub quiz"),
                Absence(household_id=hid, person_id=byname["Alex"].id, start_date=t + timedelta(days=3), end_date=None,
                        weekdays=[(t + timedelta(days=3)).weekday()], slots=["dinner"], reason="study", note="Evening class"),
                Absence(household_id=hid, person_id=byname["Sam"].id, start_date=t + timedelta(days=5), end_date=t + timedelta(days=6),
                        slots=[], reason="work", note="Conference in Leeds"),
            ])
        if not db.scalar(select(Product).where(Product.household_id == hid, Product.kind.in_(("supplement", "medicine")))):
            from .models import NutritionRecord, Regimen
            drops = Product(household_id=hid, kind="supplement", name="Children's vitamin drops (A, C, D)",
                            package_quantity=10, package_unit="ml", package_base_quantity=10, base_unit="ml", dose_amount=5, dose_unit="drop",
                            category="Medicines & vitamins", label_directions="Sample item: copy the directions from your own bottle.",
                            min_stock_base=3)
            whey = Product(household_id=hid, kind="supplement", name="Whey protein powder", brand="Store brand", package_quantity=1,
                           package_unit="kg", package_base_quantity=1000, base_unit="g", dose_amount=30, dose_unit="g",
                           category="Medicines & vitamins", min_stock_base=200)
            para = Product(household_id=hid, kind="medicine", name="Infant paracetamol suspension", package_quantity=100,
                           package_unit="ml", package_base_quantity=100, base_unit="ml", dose_amount=5, dose_unit="ml",
                           label_min_interval_hours=4, label_max_doses_24h=4, active_ingredients="Paracetamol 120 mg/5 ml",
                           label_directions="Sample item: enter the age-band directions from your own bottle.", category="Medicines & vitamins")
            db.add_all([drops, whey, para])
            db.flush()
            db.add_all([
                NutritionRecord(household_id=hid, product_id=drops.id, source="label", basis="dose", verified=True, confidence=0.9,
                                nutrients={"vitamin_a_ug": 233, "vitamin_c_mg": 20, "vitamin_d_ug": 10}, notes="Sample values"),
                NutritionRecord(household_id=hid, product_id=whey.id, source="label", basis="dose", verified=True, confidence=0.9,
                                nutrients={"protein_g": 24, "energy_kcal": 118, "carbohydrate_g": 2.1, "sugars_g": 1.6, "fat_g": 1.8, "salt_g": 0.15},
                                notes="Sample values"),
            ])
            create_lot(db, hid, product=drops, food=None, quantity=1, unit="pack", location="other", event_type="add",
                       use_by=t + timedelta(days=21), date_label="Expiry")
            create_lot(db, hid, product=whey, food=None, quantity=1, unit="pack", location="cupboard", event_type="add",
                       use_by=t + timedelta(days=200), date_label="Expiry")
            create_lot(db, hid, product=para, food=None, quantity=1, unit="pack", location="other", event_type="add",
                       use_by=t + timedelta(days=330), date_label="Expiry")
            kids = list(db.scalars(select(PersonProfile).where(PersonProfile.household_id == hid, PersonProfile.kind == "child")))
            for k in kids:
                db.add(Regimen(household_id=hid, person_id=k.id, product_id=drops.id, dose_amount=5, dose_unit="drop", times_per_day=1,
                               start_date=t - timedelta(days=30), source="Health visitor (sample)"))
        db.commit()
        return {"household_id": str(hid), "admin_email": admin.email,
                "password": created_users if created_users else "(existing users unchanged)"}
    finally:
        db.close()
