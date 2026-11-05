# Larder

A self-hosted, installable web app for a household's recipes, pantry, receipts, meal planning and nutrition. Built for
a UK household (two adults, two toddlers, one dog): UK English, GBP, metric units and UK dates throughout.

<p>
<img src="docs/screenshots/today-phone.jpg" width="210" alt="Today on a phone">
<img src="docs/screenshots/recipe-tablet.jpg" width="300" alt="Recipe on a tablet">
<img src="docs/screenshots/receipt-desktop.jpg" width="420" alt="Receipt review on a desktop">
</p>

**What it does (Release 1)**

- **Recipes**: paste a link (schema.org data first, AI only for the gaps), paste text, photograph a page, write one,
  or describe what you want using what's in stock. Everything lands on a review screen first. Missing amounts stay
  visibly unresolved. Source, wording, photos and attribution are kept.
- **Change this recipe**: "make it vegetarian", "less spicy for the toddlers", or a new number of servings. You get a
  before/after diff of ingredients, method, allergens and nutrition, and can save it as a new named version or as a
  deliberate replacement. Published versions can't be changed (the database enforces this), and history is kept.
- **Cooking mode**: one step at a time, large text, scaled amounts, timers, step photos. Finishing records the real
  batch, the pantry deductions, each person's actual portion, leftovers and waste.
- **Pantry**: scan a barcode (Open Food Facts lookup, confirmed by you) or add loose items. Each lot keeps its own
  dates and prices, and every change goes through an append-only stock ledger. Use-by and best-before are shown
  exactly as the label words them. A guided stocktake counts one cupboard, fridge or freezer at a time, and
  near-duplicate foods can be merged.
- **Receipts**: photo, PDF, screenshot, e-mail or pasted text, read by OCR and rules (AI optional). You review it line
  by line in a ledger. Applying is idempotent, and duplicates are detected. Shelf price and price actually paid are
  both recorded.
- **Plan, shopping and "who's out"**: a week view. The shopping list is planned meals minus usable stock, respecting
  use-by dates. Mark people as out for meals (socialising, work, study, travel): new meals default to whoever is home
  with servings from their usual portions, and planned meals offer "plan without them". Weekly repeats (an evening
  class) and a subscribable calendar feed.
- **Insights**: weekly spending by food, pet food and medicines; what gets thrown away and what it cost; price per
  kg/L/item over time and the cheapest shops; and run-out forecasts shown as a range, with a confidence label and the
  reason. Forecasts appear only after enough history, and likely run-outs join the shopping list.
- **Nutrition**: CoFID and label data with provenance. Per-serving and per-person intake snapshots. Unknown is never
  counted as zero. JSON and CSV export, plus scoped read-only tokens for an activity app.
- **Medicines & vitamins**: a cabinet for vitamins, protein powder and medicines with stock and expiry dates, the
  label directions you enter, daily routines ("due today"), and a dose log that warns when a dose is too soon or over
  the 24-hour maximum. Supplements count towards nutrition; medicines never do.
- **AI agents (MCP)**: a built-in MCP server at `https://<site>/mcp`, or run locally over stdio. Agents can plan the
  week, handle who's out, manage the pantry and shopping, draft recipes and review receipts, using a scoped token
  and Larder's review-first rules. See [docs/MCP.md](docs/MCP.md).
- **Operations**: Docker Compose, HTTPS via Caddy, nightly (optionally encrypted) backups with a tested restore, and
  a full household export.

## Quick start

```bash
cp .env.example .env
```

Edit `.env` and set at least `POSTGRES_PASSWORD` and `LARDER_SITE_ADDRESS`. Then build and start the stack:

```bash
docker compose up -d --build
```

Open `https://<your LARDER_SITE_ADDRESS>` and create the household. For HTTPS on phones, AI and barcode settings,
backups and upgrades, read [docs/SETUP.md](docs/SETUP.md).

To try it with sample data (fictional household, sample recipes and stock), run this before creating a household:

```bash
docker compose exec -e LARDER_DEMO_PASSWORD=choose-a-password web python -m app.cli seed-demo
```

## Documentation

| | |
|---|---|
| [docs/SETUP.md](docs/SETUP.md) | Install, HTTPS on phones and tablets, configuration, upgrades, backups and restore |
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | Short guide for the household |
| [docs/MCP.md](docs/MCP.md) | Connecting AI agents over MCP: setup, tools, safety |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Modules, data model, invariants, API and export contract |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Architecture decision log, including the Mealie decision |
| [docs/DESIGN.md](docs/DESIGN.md) | Visual direction, tokens, contrast checks and screenshots |
| [docs/TESTING.md](docs/TESTING.md) | Test suite and Release 1 acceptance results |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Known limitations and Release 2–3 work |
| [NOTICE.md](NOTICE.md) | Third-party data, fonts and images |

The API is documented interactively at `https://<site>/api/docs` (OpenAPI at `/api/openapi.json`).

## Development

```bash
cd backend && uv venv .venv && uv pip install -p .venv -r requirements-dev.txt
LARDER_DATABASE_URL=postgresql+psycopg://user:pass@localhost/larder .venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

```bash
cd frontend && npm ci && npm run dev    # proxies /api to :8000
```

Tests need a PostgreSQL database; point `LARDER_TEST_DATABASE_URL` at one and run `.venv/bin/pytest`. See
[docs/TESTING.md](docs/TESTING.md).
