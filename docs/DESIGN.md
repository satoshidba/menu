# Design: a quiet kitchen journal

Food photography carries the colour. The interface sits on warm paper with deep ink, uses herb green for action, and
keeps tomato and ochre for things that need attention. Colour that carries meaning is always paired with words ("Use
by tomorrow", "Missing 1") or an icon. A live reference page is at `/design` (no sign-in needed).

## Tokens (`frontend/src/styles.css`)

| Token | Light | Use | Contrast on paper |
|---|---|---|---|
| `--paper` | `#F7F4EE` | page | — |
| `--card` | `#FFFDF9` | raised sheets, panels | — |
| `--ink` | `#263029` | text | 12.4:1 |
| `--quiet` | `#5C665D` | secondary text (the brief's `#667067` is 4.7:1, so it was darkened slightly for small text) | 5.4:1 |
| `--herb` | `#3E6B53` | primary actions (white text 6.1:1), progress, links | 5.6:1 |
| `--tomato` | `#B65D45` | urgent marks and fills (white text 4.5:1) | 4.1:1, so marks only |
| `--tomato-ink` | `#9E4A33` | urgent text | 5.5:1 |
| `--ochre` | `#B9843B` | marks only | 3.0:1, never text |
| `--ochre-ink` | `#8A5F22` | "soon"/review text | 5.1:1 |

Dark-mode values are defined for the same tokens and follow `prefers-color-scheme`; all dark text tokens are at least
6.2:1. Focus rings are a 3 px blue outline. Touch targets are at least 44 px (64 px for cooking-mode navigation).

**Type.**
- Newsreader (variable, optical sizes) is used for recipe titles, section headings, the cooking step text and
  standfirsts.
- IBM Plex Sans (400/500/600) is used for controls, lists and data.
- Prices, quantities and dates use tabular lining numerals.
- Both fonts are self-hosted from npm packages and their OFL licences ship in the web image (`/licences/`).

**Rhythm.**
- 8 px spacing scale; 1 px rules for dense data; radii of 6, 10 and 14 px.
- Shadows only on sheets, overlays and the floating action button.

**Motion** is used only to confirm a change or keep your place:
- a quantity nudge after **Use 1**
- bottom sheets on phones (side sheets on tablets)
- a receipt row settling from ochre to resolved
- the cooking step text sliding in
- step photos opening full screen and returning to the step

Durations are 120–240 ms, and `prefers-reduced-motion` removes them.

## Per-surface choices

- **Recipes**: a large hero image with a deliberate focal point and attribution overlaid, an editorial serif title and
  italic standfirst, facts row, and an ingredients/method split (ingredients sticky on wide screens). On iPad landscape
  and desktop the recipe list and the open recipe sit side by side.
- **Pantry**: a compact list. Quantity, location and the label-worded earliest date are visible without opening
  anything, and a one-tap **−** is on each row. On wide screens the detail panel sits beside the list.
- **Receipt review**: a ledger with tabular figures; unresolved rows have an ochre rule and tint, resolved rows a herb
  rule. Exact retailer wording is shown in monospace only where it differs from the reviewed description. The footer
  reconciles lines against the printed total, with basket coupons kept separate. On phones the rows stack.
- **Cooking mode**: one step in large serif type, a timer, scaled ingredients, a large photo (the step photo, or the
  dish), and big Previous/Next buttons. The screen is kept awake.
- **Nutrition**: a labelled bar chart with exact values (hatched bars mean "at least"), a table, and an entry list.
  No gauges, no targets.
- **Plan**: a plain week list. Absences appear as dashed "who's out" tags, and conflicting meals get an inline "Plan
  without …" action.

## References and what was taken from them

- **Landbook / Landdding (Food & Drinks, editorial)**: generous image crops, serif display paired with a neutral sans,
  and quiet off-white grounds. These were applied to recipe pages and Today, not to the app shell.
- **Recent / Godly (which currently redirects to Recent)**: a strong type hierarchy, single-accent palettes, and
  attention to small details such as eyebrow labels, tabular numbers and consistent 1 px rules.
- **60fps: Creme recipe preview and step flow**: the principle of keeping context when media expands (the photo opens
  full screen and returns to its step) and of one focused step at a time. **Blinkit add/remove**: a small, immediate
  quantity confirmation after a tap. All of these were recreated in this app's own style.
- **MotionSites**: not used. Its premium landing-page motion doesn't suit stocktaking or receipt review, and the
  Studio page isn't a dependency.
- **Font in Logo**: used only to sanity-check that the wordmark (Newsreader with a hand-drawn leaf) isn't derivative of
  an existing brand.

Deliberately avoided: gradients, glass panels, emoji, identical cards everywhere, 3D objects, confetti, and charts
without labels. Empty states use plain text and a line icon, never fake food photos.

## Screenshots

Phone (390 px), tablet (820 px) and desktop (1280 px) captures of the seven representative screens, plus Plan, are in
[`docs/screenshots/`](screenshots/). They were taken at 2× through the real Docker/Caddy HTTPS stack with
`tools/screens.mjs` (`design`, `today`, `recipe`, `cooking`, `pantry`, `receipt`, `nutrition` and `plan`, each in
`-phone`, `-tablet` and `-desktop`).

| | Phone | Tablet | Desktop |
|---|---|---|---|
| Today | ![](screenshots/today-phone.jpg) | ![](screenshots/today-tablet.jpg) | ![](screenshots/today-desktop.jpg) |
| Recipe | ![](screenshots/recipe-phone.jpg) | ![](screenshots/recipe-tablet.jpg) | ![](screenshots/recipe-desktop.jpg) |
| Cooking | ![](screenshots/cooking-phone.jpg) | ![](screenshots/cooking-tablet.jpg) | ![](screenshots/cooking-desktop.jpg) |
| Pantry | ![](screenshots/pantry-phone.jpg) | ![](screenshots/pantry-tablet.jpg) | ![](screenshots/pantry-desktop.jpg) |
| Receipt | ![](screenshots/receipt-phone.jpg) | ![](screenshots/receipt-tablet.jpg) | ![](screenshots/receipt-desktop.jpg) |
| Nutrition | ![](screenshots/nutrition-phone.jpg) | ![](screenshots/nutrition-tablet.jpg) | ![](screenshots/nutrition-desktop.jpg) |
| Plan | ![](screenshots/plan-phone.jpg) | ![](screenshots/plan-tablet.jpg) | ![](screenshots/plan-desktop.jpg) |
| Design system | ![](screenshots/design-phone.jpg) | ![](screenshots/design-tablet.jpg) | ![](screenshots/design-desktop.jpg) |

**Issues found in review and fixed:**
- Grid children weren't allowed to shrink, so horizontal scrollers overflowed the page.
- The servings stepper stacked vertically because of an over-broad selector.
- The location filter overflowed on phones.
- The receipt table was too wide for phones, so a stacked layout was added.
- Raw receipt text was duplicated under the description.
- Nutrition entries said "label" when they had no data.
- The next-meal card was cramped in a narrow column (fixed with container queries).
- On phones the Plan day column squeezed meals, so days now stack.
