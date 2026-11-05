# Architecture

A modular monolith: one FastAPI application with domain modules, one worker process using the same code, PostgreSQL
16, and a React PWA served by Caddy.

```
browser (PWA, service worker) ──HTTPS──> Caddy ──/api──> web (FastAPI, uvicorn) ──> PostgreSQL 16
AI agent (MCP client) ─────────HTTPS──>   └─/mcp──> mcp (larder-mcp) ──(caller's token)──> web
                                          └─static /srv                 │
                                                        worker ─────────┘  (job queue: import_job, SKIP LOCKED)
                                                          ├─ OCR (Tesseract), pdftotext, pg_dump
                                                          └─ adapters: AI provider · Open Food Facts · safe URL fetch
```

## Code map

| Path | Contents |
|---|---|
| `backend/app/models/` | SQLAlchemy models by domain (household, food, recipe, pantry, receipt, planning, system) |
| `backend/migrations/` | Alembic: `0001` schema, `0002` invariants (triggers, trigram indexes), `0003` absences |
| `backend/app/api/` | Routers: accounts/tokens, catalog (foods/products/nutrition), pantry, recipes, receipts, planning/absences, meals/intake, system/admin |
| `backend/app/services/` | Domain logic: `units`, `nutrition`, `nutrition_db`, `stock`, `recipes`, `recipe_extract`, `recipe_jobs`, `receipt_parse`, `receipts`, `shopping`, `absences`, `prices`, `flags`, `backup`, `export`, `jobs`, and the adapters `ai`, `off`, `safe_fetch`, `cofid` |
| `backend/app/worker.py`, `cli.py`, `seed.py` | Worker loop and scheduler, admin CLI, sample data |
| `frontend/src/` | PWA: `screens/`, `components/`, `lib/` (API client, formats), `styles.css` (design tokens) |
| `deploy/` | Caddyfile and the web image |
| `mcp-server/` | MCP server for agents (`larder_mcp`): stdio and streamable HTTP, forwarding each caller's token to the API; see `docs/MCP.md` |
| `tools/screens.mjs` | Screenshot script used for design review |

## Data model

The tables required by the brief, plus a few supporting ones:

- **Household, User, UserSession, ApiToken, PersonProfile, PetProfile, Absence**: two adult logins (admin and
  member); toddlers are profiles. Absences record who is out for which meals between two dates.
- **Food** (generic ingredient) and **Product** (branded package, with `Barcode`). Recipe ingredients link to a food
  and optionally a product. Foods carry ingredient-specific conversions (density, confirmed per-item weight, per-unit
  grams such as "1 cup = 125 g").
- **NutritionRecord**: values per 100 g or 100 ml, with `source` (label, manual, CoFID, Open Food Facts, AI), version,
  retrieval date, verification, confidence and flags. **CofidFood** is a separate, versioned reference table
  (OGL v3), and **ExternalProductCache** holds Open Food Facts data (ODbL), kept apart for licensing.
- **Recipe, RecipeVersion, RecipeIngredient, RecipeStep, RecipeMedia**: versions keep the source text, original
  values, extraction confidence, unresolved fields, change request and summary, and a structured `diff` against
  their parent. Media belongs to the recipe; step photos attach to a version and step position.
- **PantryLot** and **StockEvent**: one lot per product or food, location, and purchase/expiry context, with the
  entered unit, base unit and conversion factor. Events are purchase, add, consumption, waste, correction, transfer,
  return, expiration or open.
- **Receipt, ReceiptLine, Purchase, PriceObservation, Offer**: a receipt line keeps the exact retailer wording and the
  originally parsed values, plus the corrections and reviewed match. Price observations record paid and shelf price,
  pack size, normalised unit price, shop, time, source and confidence.
- **MealPlan, CookedBatch, MealConsumption, ShoppingListItem**: consumption rows freeze a nutrition snapshot with
  provenance and have a stable `external_id`.
- **ImportJob, AuditEvent, IdempotencyRecord, RateLimitHit**: the Postgres job queue, the audit trail (including AI
  usage and cost, never prompts), HTTP idempotency, and rate limits.

### Invariants, and where they're enforced

| Rule | Enforcement |
|---|---|
| A published `RecipeVersion` and its ingredients and steps are immutable | Postgres triggers (`0002`). Edits create a draft and then a new version |
| The stock ledger is append-only | Trigger blocks UPDATE/DELETE on `stock_event`, except FK `SET NULL` and cascade deletes |
| A lot's quantity equals the sum of its events | Event and projection written in one transaction under `SELECT … FOR UPDATE`. `GET /pantry/consistency` and `cli check` verify it |
| No negative stock without an explicit override and explanation | `services/stock.record_event`, plus a CHECK constraint that an override has a reason |
| A receipt is applied at most once | Row lock and status, `apply_idempotency_key`, `idempotency_key` on each line's stock event, and a unique `purchase.receipt_line_id` |
| Duplicate receipts are detected | File SHA-256, a fingerprint (retailer, date, total, receipt number), and same-day/same-total matching. Apply refuses unless confirmed |
| Pet food never enters human nutrition | Nutrition records refused for pet products; recipes and consumption refuse pet items; pet lots excluded from planning |
| Unknown is never zero | Nutrients are `null` when unknown. Totals carry a `complete`/`partial`/`unknown` status and the contributors that were missing |
| Counts never silently become grams | `services/units.convert` needs a confirmed per-item weight or an explicit per-unit factor |
| Historical intake doesn't change | `MealConsumption.nutrition` is a snapshot. Corrections rescale from the per-unit snapshot taken at the time |

## API

JSON under `/api/v1`, with OpenAPI at `/api/openapi.json` and interactive docs at `/api/docs`. IDs are UUIDs,
timestamps are ISO 8601, money is integer pence, and quantities are metric. List endpoints that can grow use cursor
pagination. Any mutation accepts an `Idempotency-Key` header (the response is stored and replayed), and some also take
a domain idempotency key in the body: stock events, cooking, receipt apply.

**Authentication.**
- Browsers use a server-side session cookie (HttpOnly, Secure, SameSite=Lax) plus a CSRF header on every mutation.
- Other apps use `Authorization: Bearer lrd_…` tokens. Tokens are stored hashed, can be scoped and limited to named
  people, can expire, and can be rotated and revoked.
- Scopes are `recipes:read|write`, `pantry:read|write`, `receipts:write`, `plan:read|write`,
  `intake:read`, `intake:read:children`, `intake:write` and `export:read`.

The endpoints required by the brief all exist with the specified paths (see `docs/TESTING.md`), alongside the rest of
the CRUD surface.

### Intake export for an activity app

1. In **Settings → Connected apps**, create a token with `intake:read` for one adult. Add `intake:read:children` only
   if you want to share a child's intake as well.
2. Do a first full pull:
   `GET /api/v1/people/{id}/intake/export?format=json&updated_since=2000-01-01T00:00:00Z`
3. Store the response's `cursor`. Each later poll sends `updated_since=<cursor>` and gets only new or changed entries.
   Deleted entries come back as tombstones (`"deleted": true`).
4. Deduplicate on `external_id`, which is stable for the life of the entry.

Each record contains:
- `external_id`, `person_id`, `consumed_at` (UTC ISO 8601), `timezone` and `local_date`
- `meal_slot`, `description`, `amount` and `amount_unit`
- `energy_kcal`, plus `nutrients{key: {value, unit, status}}`
- `source`, `recipe_version_id`, `food_id` and `product_id`
- `confidence`, `estimated`, `complete`, `missing_from`, `deleted` and `updated_at`

CSV (`format=csv`) has the same fields, with one column per nutrient (the unit is in the column name, e.g. `salt_g`)
plus a `<nutrient>_status` column. A token without the person in its list, or without the children scope for a
child, gets `404`, so it can't discover which profiles exist.

## Background jobs

`import_job` is the queue. The worker claims jobs with `FOR UPDATE SKIP LOCKED`, retries unexpected errors with
backoff, and marks user-facing failures as failed straight away. Admins can retry failed jobs in Settings.

Job inputs and results never contain credentials; receipt text stays on the receipt row. Set
`LARDER_INLINE_JOBS=true` to run jobs inside the request instead (used by the tests and by tiny installs without a
worker).

## Security notes

- **URL import**: only http/https on ports 80/443, with no credentials in the URL. Every redirect hop is re-resolved,
  and every resolved address must be public (private, loopback, link-local, CGNAT, reserved and multicast addresses
  are refused). The connection is pinned to the vetted IP and sizes and timeouts are capped. Imported HTML is reduced
  to plain text, and images are decoded and re-encoded (which also strips metadata) with a decompression-bomb guard.
- **Uploads**: file types are sniffed, and size is limited twice (in the API and in Caddy). Images are re-encoded, and
  OCR and PDF extraction run in the worker with timeouts and container memory/PID limits.
- **Passwords**: Argon2id. Login and import are rate-limited, and changing a password revokes your other sessions.
- **Headers**: Caddy sets a strict CSP, HSTS, nosniff, `frame-ancestors 'none'` and Permissions-Policy (camera only
  for this origin).
- **Media**: served only to authenticated members of the owning household.
