# Testing and Release 1 acceptance

## Automated tests

The backend has 57 pytest tests that run against a real PostgreSQL database migrated with Alembic. Background jobs
run inline. External services are not called: the AI adapter is replaced by a fake that returns the same schema, URL
imports use a local HTTP server, and Open Food Facts is disabled.

```bash
cd backend && LARDER_TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost/larder_test .venv/bin/pytest
```

Inside the production image, against the compose database (this also runs the OCR test, which needs Tesseract):

```bash
docker compose exec db createdb -U larder larder_test
```

```bash
docker compose run --rm -T -e LARDER_TEST_DATABASE_URL=postgresql+psycopg://larder:<password>@db:5432/larder_test web test
```

**Latest results (5 Nov 2026).** 57 passed in the Docker image, including OCR. Locally, without Tesseract, 56 passed and
1 was skipped. The frontend type-checks (`tsc --noEmit`) and builds cleanly.

| Area the brief asked for | Tests |
|---|---|
| Unit conversion | `test_units.py`: metric conversions, cups need an ingredient factor, counts never become grams, ingredient-line parsing (fractions, ranges, parenthetical metric, glued units), package sizes, °F→°C, UK display |
| Nutrition scaling and unknown values | `test_nutrition.py`: scaling keeps `None`, partial/unknown status, derived kJ and salt only from known units, recipe nutrition per serving and when rescaled, confirming a per-item weight completes the total, pet food refused |
| Stock-event arithmetic | `test_stock.py`: two lots with different prices and dates, events (consumption, waste, override needing a reason, correction, transfer), ledger equal to projection, idempotent events and HTTP `Idempotency-Key` replay, duplicate-lot warning, append-only trigger, optimistic-lock conflict |
| Receipt idempotency | `test_receipts.py`: parser (multibuy, Nectar saving, weighed item, basket coupon), OCR spacing tolerance, review blocks apply while lines are unresolved, OCR line correction keeps the raw text, apply creates purchases, lots and paid/shelf prices once, re-apply is a no-op, identical re-upload refused, a different scan of the same receipt flagged and refused, learned matches, original-file deletion, photographed receipt read by OCR |
| Token authorisation | `test_tokens.py`: read-only token exports one adult as JSON and CSV with stable IDs and units, can't read the other adult or a toddler, can't write or read other areas, child scope needed, incremental sync with tombstones, rotation and revocation, CSRF on cookie sessions, member can't mint tokens |
| Recipes | `test_recipes.py`: schema.org URL import (UK units, unresolved amounts, method order, source, step and hero images), save, AI change proposal with diff, publish as new version with the original retained, immutability trigger, overwrite mode, scaling without changing the base, text import, no-AI fallback, SSRF blocks |
| Planning and cooking | `test_planning_cooking.py`: shopping list = plan minus usable stock, updated when servings or pantry change, use-by respected, user edits kept; cooking with unequal portions, leftovers, waste, deductions, replay protection, per-person intake, unknown nutrients stay unknown, historical snapshots unchanged after recipe edits |
| Absences | `test_absences.py`: conflicts flagged, "plan without" reduces servings, new meals default to who is home, multi-day and whole-day absences, validation, Today shows who's out |
| Backup, restore and export | `test_backup_export.py`: backup and restore round trip (plain and encrypted) including media, wrong passphrase fails, export excludes secrets, import into an empty instance keeps versions and media |

## MCP server

`mcp-server/tests/test_tools.py` runs 6 end-to-end tests with a real MCP client against a running, seeded Larder.
They check:
- tool, prompt and resource listing, annotations and server instructions;
- read tools;
- plan → mark someone away → conflict → plan without them → shopping list, with clean-up;
- add to pantry and use from it, including the never-below-zero shortfall;
- receipt import, where apply is refused without `confirm`, then applied, then re-applied as a no-op;
- readable errors.

**Results (5 Nov 2026).**
- 6/6 against the dev API.
- 6/6 in stdio mode against the Docker stack over HTTPS, trusting Caddy's CA via `LARDER_CA_CERT`.
- Remote transport through Caddy at `https://<site>/mcp`: 401 without a token, 37 tools listed, live data returned,
  and token scope enforced (a child's intake is refused without `intake:read:children`).
- The `mcp` service's container healthcheck passes.

## Release 1 acceptance

| # | Result | Evidence and notes |
|---|---|---|
| 1 | **Pass (AI faked)** | `test_url_import_review_save_change_and_version`: a schema.org page gives a reviewable draft in UK units (8 oz becomes 225 g; the cup is flagged for confirmation), with ordered method, °F converted, source link and author, and hero and step photos. "Salt, to taste" stays unresolved. The draft is saved; a substitution is requested; the diff shows removed and added ingredients, modified steps and nutrition; it's saved as a new version and the original is retained. The AI call itself was replaced by a fake returning the real schema, because no API key was available here. The real adapter (`services/ai.py`) is untested against the live API. |
| 2 | **Pass** | `test_scan_known_two_lots_prices_and_earliest_date` and `test_unknown_barcode_quick_add`: correct totals (1.5 kg), both prices kept (£1.50/kg and £1.90/kg), earliest date shown, and quick-add for an unknown barcode. The live Open Food Facts lookup was checked manually (HTTP 200) but isn't part of the automated suite. |
| 3 | **Pass** | `test_review_correct_apply_once` uses a receipt with a Nectar promotion and corrects one OCR line. Products, lots and the actual paid price (£1.50 for 2 × 500 g after a £0.40 saving) are created once. Re-uploading the same file warns and creates nothing, and a different scan of the same receipt is refused at apply. `test_photo_receipt_ocr` reads a rendered photo of the receipt with Tesseract in the production image. |
| 4 | **Pass** | `test_shopping_list_subtracts_usable_stock`: 500 g needed with 200 g usable gives 300 g. Changing to 6 servings gives 550 g; adding 300 g to the pantry gives 250 g. |
| 5 | **Pass** | `test_cook_batch_portions_leftovers_and_intake`: unequal portions (1.5, 1, 0.25 and 0.5 servings), 0.5 leftovers and 0.25 waste. Pantry deductions are correct, and each person's intake is the per-serving value times their portion. An unknown ingredient leaves totals "partial", and CoFID's undetermined fibre stays unknown. |
| 6 | **Pass** | `test_read_only_token_exports_one_adult` and related tests (above). |
| 7 | **Pass, with a caveat on real devices** | <a id="acceptance-test-7"></a>From a fresh clone with `cp .env.example .env` and `docker compose up`, all four services were healthy and served over HTTPS: the Caddy internal CA certificate verified with curl, CSP/HSTS headers were present, and the manifest, service worker and deep links loaded. **Restore drill:** seed data; take an encrypted backup; copy it off the stack; `docker compose down -v` to destroy every volume; bring up an empty instance (`needs_setup: true`); restore from stdin; sign in. All 4 recipes came back, the hero image was byte-identical (641,713 bytes), all 15 pantry items came back, and `cli check` reported 0 mismatched lots. **Not done:** this environment has no physical iPhone, iPad or Android device. Phone and tablet browsers were emulated in Chromium at 390 and 820 px; Safari behaviour (ZXing camera fallback, install to Home Screen, root-CA trust) is untested. |
| 8 | **Pass, with a caveat** | 24 screenshots (8 screens at 3 widths) are in `docs/screenshots/`, and the tokens and contrast ratios are in `DESIGN.md`. Screens were inspected and the layout issues fixed. Reduced motion is honoured via a CSS media query, and Playwright captured the screenshots with `reducedMotion: "reduce"`. Contrast was computed for every text token. **Not done:** testing with a real screen reader (VoiceOver or TalkBack). The markup uses labelled controls, landmarks, `aria-live` for timers and toasts, and focus management in sheets, but it hasn't been audited with assistive technology. |

## Things found by testing and fixed during the build

- Alembic's `create_table` silently drops `use_alter` foreign keys. The initial migration now adds them explicitly.
- The non-root container couldn't write to a host-folder bind mount for backups. Backups now default to a named
  volume, and the entrypoint warns when a folder isn't writable.
- `docker compose cp` leaves files owned by root with mode 0600, so the non-root restore couldn't read them. Restore
  now reads the archive from stdin.
- Tesseract's default page segmentation split pence into a separate column. Mode 6 with interword spacing preserved
  fixes it, and the parser accepts "- £0.40".
- The worker healthcheck relied on `pgrep`, which isn't in the slim image. It now uses a heartbeat file.
- Child profiles defaulted to a full portion; they now default to half a serving.
