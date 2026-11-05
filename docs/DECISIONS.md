# Architecture decision log

Each entry records a choice made where the brief left room, and the reasons. Newest last.

### ADR-001 — Own application; Mealie as a UX and interoperability reference only
**Decision.** Build a separate modular monolith, and don't fork or embed Mealie.
**Why.** The core of this brief is outside Mealie's documented feature set: lot-level pantry with an auditable ledger,
receipt review with idempotent purchase import, price history, nutrition that rescales with servings and portions,
per-person intake snapshots, scoped intake export, and absences. Mealie's recipe nutrition is static when servings
change. Retrofitting these into a fork would mean carrying a large patch set against an actively developed codebase.
Mealie is AGPL-3.0, so a modified, network-served fork would also have to publish its source. That's fine for a
household, but it limits how the code can be reused. No proof of concept showed a Mealie-based approach being
substantially simpler, so the brief's default stands.
**Follow-up.** Mealie recipe import/export through its documented API is Release 3.

### ADR-002 — Stack: FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL 16; React + Vite PWA; Caddy
**Why.** Typed, well-understood tools with a small operational footprint. PostgreSQL gives transactional stock
updates, JSONB for provenance and snapshots, `pg_trgm` for fuzzy matching, and triggers for invariants. Caddy provides
automatic HTTPS, including a private CA for home networks, so the camera and PWA work on phones.

### ADR-003 — Postgres-backed job queue instead of Redis/Celery
**Why.** One fewer service to run and back up. `SELECT … FOR UPDATE SKIP LOCKED` is enough for a two-person household.
Jobs survive restarts, and failed ones can be retried from the admin page.

### ADR-004 — Database-enforced invariants
Published recipe versions and their children are immutable, and the stock ledger is append-only. Both are enforced
by triggers (migration `0002`), not just application code, so bugs and manual SQL can't silently rewrite history.
Corrections are new events or versions.

### ADR-005 — Stock: projection plus ledger in one transaction
`PantryLot.quantity_base` is a projection updated in the same transaction as each `StockEvent`, under a row lock. It
is cheap to read, and verifiable at any time against the ledger (`/pantry/consistency`, `cli check`).

### ADR-006 — "Usable" stock for planning
A lot counts towards a planned meal unless its **use-by** date is before the meal date. Best-before dates don't
exclude stock. This is planning arithmetic, not a food-safety judgement: the pantry always shows the label wording
and Larder never declares food safe or unsafe. Reserved quantities are subtracted.

### ADR-007 — Units
Mass is stored in g, volume in ml, and counts in "each". tsp/tbsp are kept as written; oz, lb, fl oz and pint are
converted to metric because the conversion doesn't depend on the ingredient. **Cups** are converted to weight only
with an ingredient-specific factor that the household confirms once ("1 cup of plain flour = 125 g"); until then they
stay as volume and are flagged. **Counts** ("1 onion") never become grams unless a per-item weight has been
confirmed. "cup" means the US cup (236.6 ml), "fl oz" and "pint" the UK measures. Approximate densities in the sample
data are labelled as such.

### ADR-008 — Nutrition totals and unknowns
Every nutrient total carries a status: **complete** (all contributors known), **partial** (the sum of the known
contributors, so a lower bound, shown as "≥"), or **unknown** (`null`). It also lists which ingredients were missing.
kJ is derived from kcal (and vice versa), and salt from sodium, only when the source unit is known (mg); derived
values are flagged. Values are estimates unless every source is a verified label.

**Source priority:** verified label, then manual entry, then CoFID, then Open Food Facts, then AI. CoFID "Tr" is stored
as 0 with a `trace` flag; "N" and blanks are unknown. Cooking losses aren't modelled unless a yield is recorded.

### ADR-009 — Recipe versions: "new" vs "overwrite"
Saving a change creates a new immutable version every time. **New** (the default) adds a separately named variant,
e.g. "Vegetarian". **Overwrite** gives the new version the base version's label and makes it current; the old version
stays in the history. AI proposals and manual edits are stored as **draft** versions until accepted, so a diff can be
reviewed and edited before anything is published.

### ADR-010 — Media is not versioned content
The dish photo lives on the recipe. Step photos attach to a version and step position, and are carried forward when
a new version keeps the step. Photos can therefore be added after publishing without creating a version. AI-generated
illustrations are labelled and can't be set as the dish photo.

### ADR-011 — Receipts: rules first, AI as a second opinion
A deterministic parser handles common UK receipt layouts: multibuys, weighed items, Nectar/Clubcard savings, basket
coupons, bags and deposits. AI is used only when the parser is unsure (confidence below 0.7) and a provider is
configured. Line-level discounts are allocated to the item they follow, giving an effective price paid; basket-level
coupons aren't allocated.

OCR uses Tesseract page segmentation mode 6 with interword spacing preserved; this was tested on a rendered receipt in
the production image. Matches are suggested from previously confirmed lines at the same retailer, then by fuzzy name,
and are never auto-confirmed.

### ADR-012 — Leftovers are lots of a "Leftovers: <recipe>" food
They are counted in servings, with the cooked batch's per-serving nutrition snapshot used when someone eats them
later. This reuses the pantry, ledger and expiry views instead of adding a parallel inventory.

### ADR-013 — Absences ("who's out") inform, they don't silently change plans
This was added at the household's request during the build. An `Absence` covers one person, an inclusive date range
and either specific meals or the whole day, with a reason (social, work, study, travel, other) and a note.

New meals default to the people at home, with servings equal to the sum of their usual portions (adults 1, children
0.5, editable per person). Existing planned meals aren't changed automatically: they're flagged, and "Plan without
them" removes the person and reduces servings by their portion, which updates the shopping list. This follows the
brief's rule that nothing is applied silently.

### ADR-014 — AI provider adapter
Anthropic's Messages API is called through the official Python SDK, server-side only. It uses structured outputs
validated against Pydantic schemas, adaptive thinking, configurable effort, and server-side refusal fallbacks. The
default model is `claude-opus-5-5`.

Prompts tell the model to treat source text as data, never to invent amounts or steps (or to mark them as
proposed), and never to claim allergen safety. Only model, token counts, estimated cost and purpose are logged. With no
key configured, AI features explain themselves and everything else works.

### ADR-015 — Offline behaviour in Release 1
The service worker caches the app shell and recent read views: Today, recipes, pantry, plan, the shopping list and
media. When offline it serves these with a visible offline banner. Mutations fail visibly and are never queued or
replayed, so nothing is silently overwritten. Conflicts on lot label edits use optimistic `row_version` checks. The
offline edit queue is Release 2, as the brief schedules.

### ADR-016 — Sessions and tokens
Browser sessions are server-side, stored as SHA-256 hashes, and carry a CSRF token. API tokens are random, shown
once, and stored as SHA-256 hashes. A token is limited to scopes and, for intake, to listed people; child intake also
needs the explicit `intake:read:children` scope. Admins can rotate (which revokes the old token) or revoke tokens.

### ADR-017 — Backups
`pg_dump -Fc`, media and receipt originals go into a tar.gz with a manifest. Encryption is optional, using AES-256-GCM
with a scrypt-derived key from `LARDER_BACKUP_PASSPHRASE`. Restore runs `pg_restore --clean --single-transaction`
and replaces the file folders.

Backups go to a named volume by default, because the container runs as a non-root user and can't write to an
arbitrary host folder. Restore reads the archive from stdin to avoid file-ownership problems. Both were found and
fixed during the restore drill.

### ADR-018 — Product and recipe data licences
CoFID (Crown copyright, OGL v3) is stored in its own table with attribution; a 47-food subset is bundled. Open Food
Facts data (ODbL) is cached separately, labelled as community data, and treated as unverified until a human confirms
it. Recipe images are imported only when the page exposes them, with the source and attribution kept. If an image
can't be fetched, the recipe shows a placeholder rather than a substitute.

### ADR-019 — Sample data
The seed household and its people are fictional, the recipes were written for this project, and product names are
generic. The four dish photos come from Wikimedia Commons under CC0, CC BY 2.0 and CC BY 4.0, with attribution
recorded and shown on the image. Two recipe titles were adjusted to match what the photos show.

### ADR-020 — MCP server as a stateless client of the REST API
Added at the household's request so the app can be used by agents. The server lives in `mcp-server/` and uses the
official MCP Python SDK 2.x (`MCPServer`). It has two transports: streamable HTTP (stateless, JSON responses), served
by Caddy at `/mcp`, and stdio for local clients.

**It holds no credentials and no data.** Each HTTP request must carry the caller's Larder API token, which is
forwarded to the API; in stdio mode the token comes from `LARDER_TOKEN`. This means:
- token scopes, the review-first rules, idempotency, the audit trail and revocation all work unchanged;
- there is no second permission model to keep in sync.

**Agent-friendly API additions:**
- `POST /pantry/use`: use or waste an amount of an item, first-expiry-first-out, never below zero.
- Tokens with `plan:read` can list household profiles, but not intake, for planning.
- A Settings preset creates an agent token: everything except admin, export and children's intake.

**Tool design:**
- Tools accept names as well as ids, and return compact shapes.
- Read, write and remove tools carry MCP annotations.
- Draft, apply and overwrite steps are separate tools, and `apply_receipt` requires an explicit `confirm=true`.

OAuth for remote clients that only support OAuth sign-in isn't implemented. Those clients can use stdio instead.

### ADR-021 — Vitamins, supplements and medicines
Added at the household's request.

**Data model.**
- Products have a `kind`: food, pet food, supplement or medicine. Health items live in the cabinet, kept out of the
  food pantry, recipes (medicines) and food logging.
- Supplements carry nutrition **per dose** (basis `dose`) and count towards intake through a `MealConsumption` marked
  `supplement`. Only the nutrients on the label are counted; the rest stay unknown.
- Medicines never count as nutrition.

**Safety stance.** Larder records label or prescriber directions entered by the household (dose, minimum interval,
maximum doses in 24 hours, ingredients, free-text directions) and never calculates or recommends doses.
- A dose that is too soon, over the 24-hour maximum, or from expired stock is refused with the reasons (HTTP 409).
- It can still be recorded with explicit acknowledgement, which is stored on the dose for the record.
- This is a memory aid that is especially useful with two adults sharing care of toddlers; every screen points to a
  pharmacist, GP or NHS 111.

**Routines.** Regimens (daily, or "as needed") drive a "due today" checklist; "as needed" items never nag.

**Scopes.** Health data has its own scopes (`health:read`, `health:write`). Doses follow the same per-person and
explicit-children rules as intake. The MCP agent preset includes the health scopes, which can be unticked.

### ADR-022 — Recurring absences and a calendar feed
Absences can repeat weekly on chosen weekdays, from a start date until an optional end date. Each day is checked with
`Absence.covers`, so planning defaults, conflicts and Today work the same for one-off and recurring absences. The plan
API also returns `away_by_date`, so clients don't have to expand repeats themselves.

`GET /api/v1/calendar.ics?token=…` is an RFC 5545 feed of planned meals (timed by slot) and absences. Absences become
all-day or timed events, with `RRULE`s for multi-day and weekly ones. Calendar apps can't send headers, so the feed
uses a separate token with only the `calendar:read` scope, which can be revoked or rotated like any other.

Tokens are redacted from all log lines. The feed never includes medicines or intake. It was validated with a
third-party iCalendar parser, including expanding the weekly rule.
