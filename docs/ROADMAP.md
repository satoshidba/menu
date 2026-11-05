# Known limitations and next releases

## Known limitations of Release 1

- **Real devices.** Nothing has been tested on physical iOS or Android devices. iOS Safari has no `BarcodeDetector`,
  so scanning falls back to ZXing, which is untested on iOS. Installing Caddy's root certificate is a one-off manual
  step per device.
- **AI.** The adapter targets the Anthropic SDK's structured outputs but hasn't been run against the live API in this
  build (no key was available). The tests use a fake with the same schemas. AI-generated step illustrations aren't
  implemented; uploaded photos can be labelled as illustrations through the API.
- **Mealie.** Recipe import/export isn't implemented (Release 3).
- **Recipe search.** Search covers titles and tags, and "Cook with what we have" ranks by ingredients in stock plus a
  bonus for ingredients that need using soon. Richer filters (dietary preference, missing-items budget) are Release 2.
- **Matching ingredients to foods.** Automatic links are exact name or alias matches only. Anything else creates a new
  food or offers suggestions in review. Near-duplicates are suggested in Settings → Tidy foods and merged by hand.
- **Insights.** Forecasts are only as good as what's recorded: households that don't log use get purchase-rhythm
  forecasts, which are less precise. Seasonality and planned meals aren't taken into account yet.
- **Offline.** Read views are cached. Edits made offline fail visibly rather than queueing (the queue is Release 2).
- **Receipts.** Rules cover common UK till formats. Retailer-specific e-receipt layouts (Sainsbury's and Tesco PDFs,
  Lidl Plus, Sparks) go through the same generic parser, with AI as a fallback. Returns are recorded as negative
  purchases without automatically adjusting stock.
- **Nutrition.** Cooking yield and weight losses aren't modelled. A cooked batch's total weight can be stored, but
  portion entry by grams of a batch isn't in the UI.
- **Accessibility.** No audit with a screen reader yet.
- **Multi-household.** The data model is household-scoped, but a server holds one household. Setup creates the first
  and only one.

## Release 2 — convenience and insight
- Ranked pantry-aware discovery with dietary filters, a "missing at most N items" control and expiry priority.
- Richer AI adaptations, step-image extraction from video and pages, and labelled AI illustrations.
- Forecasts that take planned meals into account (uses already on the plan shouldn't be counted twice), and seasonal
  patterns.
- An offline edit queue (IndexedDB) with visible sync and conflict resolution using `row_version` and `updated_at`.
- Better toddler flags: age-aware salt guidance links, and choking-hazard preparation prompts in cooking mode.
- Alias management beyond merging (rename, split).
- Absences: iCal *import* (export and weekly repeats are done).

## Release 3 — optional integrations
- Retailer-specific receipt parsers for Sainsbury's and Tesco PDF e-receipts, Lidl Plus and M&S Sparks.
- User-authorised mailbox or folder imports for forwarded e-receipts (IMAP or watched folder), through the same review
  and duplicate controls.
- Verified offer feeds, or an Open Prices experiment, shown as current only with source, capture time, store/region and
  expiry. The `Offer` model and the `offer_is_current` rule exist already.
- Mealie import/export through its documented API.
- A direct activity-app adapter, if that app's API is supplied (the token-based pull export exists now).
