# Larder: a short guide for the household

Larder lives at your household's address (for example `https://larder.home.arpa`). Add it to your phone's home screen
so it opens like an app. On a phone the four main places are at the bottom: **Today, Recipes, Pantry, Plan**. The green
button in the corner does the most useful thing for the screen you're on (usually **Scan item**). On a tablet or
computer everything is in the sidebar.

## Today
What's for the next meal and whether anything is missing, what to **use soon**, what's **running low**, recipes you
could **cook with what you have**, and who's out today or tomorrow.

## Recipes
- **Add a recipe**: paste a link, paste text, photograph a cookbook page, write it yourself, or choose **Cook with what
  we have** and describe what you fancy (this needs AI to be switched on).
- You always see a **review** screen before anything is saved. Amounts the source didn't give are highlighted rather
  than guessed. Cup measures ask you to confirm a weight for that ingredient, and Larder remembers it. Anything AI
  suggested is marked **Proposed** so you can check it.
- **Serves** on a recipe scales the amounts for this view only; the saved recipe doesn't change.
- **Change this recipe**: try "make this vegetarian", "use the spinach and chicken we have", "less spicy for the
  toddlers", or just a new number of servings. You'll see exactly what changes: ingredients, method, allergens and
  nutrition per serving. Then choose **Save as new version** (the original stays), **Edit first**, **Replace current**
  (the old one is still kept in the history) or **Discard**.
- **Check before serving** lists *possible* allergens, clashes with anyone's allergies, dislikes or preferences, and
  preparation notes for the toddlers, with a link to NHS guidance. These are prompts to read the labels, not
  guarantees.
- **Add photo** lets you add a dish photo or a photo for a particular step.

## Cooking
Press **Start cooking**. Swipe or use **Next** and **Previous**; steps with a time have a timer, and the screen stays
on. At the end, **Finish**:
1. Check how many servings you made.
2. Set what each person actually ate (nothing is split evenly for you).
3. Note leftovers (they go into the pantry as their own item) and anything thrown away.
4. Optionally adjust the ingredients you really used.

Larder takes the ingredients out of the pantry (oldest dates first, and never below zero), and records each person's
intake.

## Pantry
- **Scan or add**: scan a barcode, or type it, or add something without one (loose veg, bulk rice). For a barcode
  Larder knows, you just confirm. For a new one it fills in what Open Food Facts knows; check it against the pack.
  Nothing counts as bought until you press **Add to pantry**. **Batch mode** keeps the camera on for a whole cupboard.
- Each line shows how much you have, where it is and the earliest date, worded as on the label ("Use by" or "Best
  before"). Larder never decides whether food is safe.
- **−** uses one pack or item. Tap the item for **Used some**, **Thrown away**, **Move**, **Correct count** and
  **Mark opened**, plus its history and the prices you've paid. Every change is kept in the history.
- **Remind me below** sets a minimum so it shows up as running low.
- **Pet food** is a separate list. It never counts towards anyone's nutrition.

## Receipts
**Add a receipt**: photograph a till receipt, or upload a PDF or e-mail receipt, a Lidl Plus or Sparks screenshot, or
paste the text. Then review it like a ledger:
- Highlighted lines need matching to a product or food, or marking **Not for the pantry**. A "?" means a suggestion
  from last time; confirm it.
- Correct anything the scan misread. The shop's original wording is kept.
- Promotions such as Nectar or Clubcard prices are applied to their item, so the price you really paid is recorded.
  Coupons for the whole basket are kept separately.
- The footer checks the lines against the printed total.
- **Add to pantry** adds everything in one go and records prices. Doing it twice does nothing, and re-uploading the
  same receipt is caught as a duplicate.
- You can delete the original photo afterwards; the prices stay.

## Plan and shopping
- **Plan** shows the week. **Add meal** picks a recipe (or just a name like "Takeaway") and starts with whoever is
  home, with servings based on everyone's usual portion. Toddlers count as half by default; change this in
  **Settings → People**.
- **Someone's out**: mark a person away for a day, several days, or particular meals, with a reason (socialising,
  work, study, away). If they were down for meals you'd already planned, Larder offers to **plan without them**, which
  reduces the servings and the shopping.
- **Shopping list** = what the planned meals need minus what's usable in the pantry. Food past its use-by before the
  meal doesn't count. It updates when plans or stock change. You can pick a brand, set a shop, tick things off, snooze
  or dismiss items, and add your own.

## Nutrition
Choose a person to see each day's energy and nutrients, and every entry. Values come from labels you've confirmed or
the UK CoFID tables, and are **estimates** unless every source was a verified label. A **≥** means some foods had no
data, so the real total is higher; unknown values are never counted as zero. **Log food** adds a snack.
**CSV/JSON** downloads the week. For the children, these numbers are a record, not targets.

## Settings
People (allergies, preferences, usual portion), pets (record your vet's feeding plan if you like; Larder gives no
feeding advice), adults who can sign in, **Connected apps** (read-only tokens for an activity app), your password,
and, for the administrator, backups, export and server status.
