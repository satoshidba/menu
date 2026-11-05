// Browser end-to-end tests against a running, seeded Larder (the web build served with its API).
//
//   E2E_BASE=http://127.0.0.1:4173 E2E_PASSWORD=… node --test e2e/
//
// Uses the `playwright` library with Node's built-in test runner, so there's nothing extra to install beyond
// Playwright and its Chromium. Tests run in order and change the demo data (they add, tick and count things), so
// point them at a throwaway seeded instance, never a real household.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { after, before, test } from "node:test";

const { chromium } = createRequire(import.meta.url)("playwright");
const BASE = process.env.E2E_BASE || "http://127.0.0.1:4173";
const EMAIL = process.env.E2E_EMAIL || "alex@example.com";
const PASSWORD = process.env.E2E_PASSWORD || "demo-password-1";
const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 800 };

let browser, state;
const problems = [];

async function open(path, viewport = DESKTOP) {
  const ctx = await browser.newContext({ viewport, storageState: state, locale: "en-GB", timezoneId: "Europe/London", reducedMotion: "reduce" });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => problems.push(`${path}: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error" && !/favicon/.test(m.text())) problems.push(`${path}: ${m.text()}`); });
  if (path) await page.goto(BASE + path, { waitUntil: "networkidle" });
  return page;
}

before(async () => {
  browser = await chromium.launch(process.env.E2E_CHROMIUM ? { executablePath: process.env.E2E_CHROMIUM } : {});
});
after(async () => { await browser?.close(); });

test("signs in through the form", async () => {
  const ctx = await browser.newContext({ viewport: DESKTOP });
  const page = await ctx.newPage();
  await page.goto(BASE + "/", { waitUntil: "networkidle" });
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill("not the password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByText("Email or password is incorrect").waitFor();  // a readable error, still on the form
  await page.getByRole("button", { name: "Sign in" }).waitFor();
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("navigation", { name: "Main" }).first().waitFor();
  state = await ctx.storageState();
  await ctx.close();
});

test("main pages render without errors or sideways scrolling on a phone or tablet", async () => {
  const routes = ["/", "/recipes", "/pantry", "/plan", "/shopping", "/receipts", "/nutrition", "/health", "/insights",
                  "/settings", "/pantry/stocktake", "/design"];
  for (const viewport of [PHONE, { width: 820, height: 1180 }]) {
    const page = await open(null, viewport);
    for (const r of routes) {
      await page.goto(BASE + r, { waitUntil: "networkidle" });
      await page.locator("main h1, h1").first().waitFor();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      assert.ok(overflow <= 1, `${r} at ${viewport.width}px scrolls sideways by ${overflow}px`);
    }
    await page.context().close();
  }
  assert.deepEqual(problems, []);
});

test("phones can reach every secondary page from Settings", async () => {
  const page = await open("/settings", PHONE);
  const more = page.getByRole("navigation", { name: "More" });
  for (const name of ["Shopping list", "Receipts", "Nutrition", "Insights", "Medicines & vitamins"]) {
    assert.ok(await more.getByRole("link", { name }).isVisible(), `${name} link missing`);
  }
  await more.getByRole("link", { name: "Nutrition" }).click();
  await page.waitForURL(/\/nutrition$/);
  await page.context().close();
});

test("recipe opens in cooking mode and steps forward", async () => {
  const page = await open("/recipes", PHONE);
  await page.locator("a.recipe-tile").filter({ hasText: /porridge/i }).first().click();
  await page.getByRole("link", { name: /cook/i }).first().click();
  await page.waitForURL(/\/cook(\?|$)/);
  const bar = page.getByRole("progressbar", { name: "Progress" });
  assert.equal(await bar.getAttribute("aria-valuenow"), "1");
  await page.getByRole("button", { name: /^Next/ }).click();
  assert.equal(await bar.getAttribute("aria-valuenow"), "2");
  await page.getByRole("button", { name: "Leave cooking mode" }).click();
  await page.context().close();
});

test("shopping list: add, tick and clear an item", async () => {
  const page = await open("/shopping");
  const name = `Kitchen roll ${Date.now() % 10000}`;
  await page.getByLabel("Add an item").fill(name);
  await page.getByRole("button", { name: "Add", exact: true }).click();
  const box = page.getByRole("checkbox", { name: `Got ${name}` });
  await box.waitFor();
  await box.check();
  await page.getByRole("button", { name: /Clear \d+ ticked/ }).click();
  await box.waitFor({ state: "detached" });
  await page.context().close();
});

test("stocktake records a correction for one location", async () => {
  const page = await open("/pantry/stocktake?location=fridge");
  const input = page.getByLabel(/^Counted Cheddar/);
  const was = Number(await input.getAttribute("placeholder"));
  const now = was - 10;
  await input.fill(String(now));
  await page.getByRole("button", { name: "Save count" }).click();
  await page.getByText(`1 corrected (${was} g → ${now} g)`).waitFor();
  await page.waitForFunction((n) => document.querySelector("input[aria-label^='Counted Cheddar']")?.getAttribute("placeholder") === String(n), now);
  await page.context().close();
});

test("insights show forecasts, a chart with a table view, and can add to the list", async () => {
  const page = await open("/insights");
  await page.getByRole("heading", { name: "Running out" }).waitFor();
  assert.ok(await page.getByText(/Got through .* a week/).first().isVisible());
  assert.ok(await page.getByRole("img", { name: /Weekly spending/ }).isVisible());
  await page.getByRole("button", { name: "Table" }).click();
  assert.ok(await page.getByRole("columnheader", { name: "Week of" }).isVisible());
  const add = page.getByRole("button", { name: "Add to list" }).first();
  if (await add.count()) {
    const before = await page.getByText("On the list").count();
    await add.click();
    await page.waitForFunction((n) => document.body.innerText.split("On the list").length - 1 > n, before);
  }
  await page.context().close();
});

test("someone out for dinner shows on the plan", async () => {
  const page = await open("/plan");
  // the demo has Sam away tomorrow and a weekly evening class; both appear as away tags
  assert.ok(await page.locator(".away-tag").count() > 0);
  await page.context().close();
});

test("no console or page errors anywhere", () => {
  assert.deepEqual(problems, []);
});
