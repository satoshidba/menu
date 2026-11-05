// Screenshot the representative screens at phone / tablet / desktop widths.
// Usage: node tools/screens.mjs <baseUrl> <outDir> [scale] [--prepare]
import { createRequire } from "node:module";
const { chromium } = createRequire(import.meta.url)("playwright");
import fs from "node:fs";

const base = process.argv[2] || "http://127.0.0.1:4173";
const out = process.argv[3] || "screens";
const scale = parseFloat(process.argv[4] || "1");
const prepare = process.argv.includes("--prepare");
const fullPage = process.argv.includes("--full");
const only = (process.argv.find((a) => a.startsWith("--only=")) || "").slice(7);
fs.mkdirSync(out, { recursive: true });

const RECEIPT = `Sainsbury's Supermarkets Ltd
Islington Local
SS SEMI SKIMMED MILK 2.27L          £1.65
FARFALLE PASTA 500G                 £1.90
2 x £0.95
NECTAR PRICE SAVING                -£0.40
BANANAS LOOSE                       £0.42
0.540 kg @ £0.78/kg
CHOPPD TOMATOS 4OOG                 £0.55
BABY SPINACH 200G                   £1.25
CARRIER BAG                         £0.10
TOTAL                               £5.47
NECTAR ECOUPON                     -£0.50
VISA CONTACTLESS                    £4.97
04/11/2026 18:32  Receipt No 1234-5678
`;

// SHOT_CHROME_ARGS e.g. "--host-resolver-rules=MAP larder.test 127.0.0.1 --no-proxy-server"; SHOT_INSECURE=1 for a private CA
const launchOpts = { args: process.env.SHOT_CHROME_ARGS ? process.env.SHOT_CHROME_ARGS.match(/--[^-][^]*?(?=\s--|$)/g).map((x) => x.trim()) : [] };
const insecure = process.env.SHOT_INSECURE === "1";
const browser = await chromium.launch(launchOpts);
const ctx0 = await browser.newContext({ ignoreHTTPSErrors: insecure });
const page0 = await ctx0.newPage();
const login = await page0.request.post(`${base}/api/v1/auth/login`, { data: { email: "alex@example.com", password: "demo-password-1" } });
const { csrf_token } = await login.json();
const H = { "x-csrf-token": csrf_token };
const j = async (r) => { const t = await r.text(); try { return JSON.parse(t); } catch { return t; } };

const recipes = await j(await page0.request.get(`${base}/api/v1/recipes`));
const pasta = recipes.find((r) => r.title.startsWith("Chicken"));
let receipts = await j(await page0.request.get(`${base}/api/v1/receipts`));
if (prepare) {
  if (!receipts.length) {
    await page0.request.post(`${base}/api/v1/receipts/import`, { headers: H, multipart: { text: RECEIPT } });
    receipts = await j(await page0.request.get(`${base}/api/v1/receipts`));
    const rv = await j(await page0.request.get(`${base}/api/v1/receipts/${receipts[0].id}/review`));
    const products = await j(await page0.request.get(`${base}/api/v1/products`));
    const milk = products.find((p) => p.name.startsWith("Semi"));
    for (const l of rv.lines) {
      if (l.description.startsWith("SS SEMI")) await page0.request.patch(`${base}/api/v1/receipts/${rv.id}/lines/${l.id}`, { headers: H, data: { product_id: milk.id, location: "fridge" } });
      if (l.description.startsWith("BANANAS")) await page0.request.patch(`${base}/api/v1/receipts/${rv.id}/lines/${l.id}`, { headers: H, data: { food_name: "Bananas" } });
    }
  }
  const people = await j(await page0.request.get(`${base}/api/v1/people`));
  const intake = await j(await page0.request.get(`${base}/api/v1/people/${people.find((p) => p.name === "Alex").id}/intake`));
  if (!intake.entries?.length) {
    const porridge = recipes.find((r) => r.title.startsWith("Banana"));
    const dal = recipes.find((r) => r.title.startsWith("Red lentil"));
    const byName = Object.fromEntries(people.map((p) => [p.name, p.id]));
    const day = (n, h) => { const d = new Date(); d.setDate(d.getDate() - n); d.setHours(h, 0, 0, 0); return d.toISOString(); };
    for (let n = 6; n >= 1; n--) {
      await page0.request.post(`${base}/api/v1/meals/cook`, { headers: H, data: { recipe_version_id: porridge.version_id, servings_made: 4, cooked_at: day(n, 8), meal_slot: "breakfast",
        portions: [{ person_id: byName.Alex, servings: 1.25 }, { person_id: byName.Sam, servings: 1 }, { person_id: byName.Mia, servings: 0.5 }, { person_id: byName.Leo, servings: 0.75 }] } });
      if (n % 2 === 0) await page0.request.post(`${base}/api/v1/meals/cook`, { headers: H, data: { recipe_version_id: dal.version_id, servings_made: 4, cooked_at: day(n, 18), meal_slot: "dinner",
        portions: [{ person_id: byName.Alex, servings: 1.5 }, { person_id: byName.Sam, servings: 1 }, { person_id: byName.Mia, servings: 0.25 }, { person_id: byName.Leo, servings: 0.5 }], leftover_servings: 0.5 } });
    }
    // put back what the sample cooking used so the pantry still looks lived-in
    const foods = await j(await page0.request.get(`${base}/api/v1/foods?limit=200`));
    const f = (n) => foods.find((x) => x.name === n)?.id;
    for (const [name, q, unit, loc, useBy] of [["Spinach", 200, "g", "fridge", 1], ["Semi-skimmed milk", 2272, "ml", "fridge", 5], ["Bananas", 5, "each", "cupboard", null], ["Onion", 3, "each", "cupboard", null]]) {
      const d = new Date(); if (useBy) d.setDate(d.getDate() + useBy);
      await page0.request.post(`${base}/api/v1/pantry/lots`, { headers: H, data: { food_id: f(name), quantity: q, unit, location: loc, use_by: useBy ? d.toLocaleDateString("en-CA") : null, confirm_duplicate: true, event_type: "add" } });
    }
  }
}
receipts = await j(await page0.request.get(`${base}/api/v1/receipts`));
const state = await ctx0.storageState();
await browser.close();

const SIZES = { phone: { width: 390, height: 844 }, tablet: { width: 820, height: 1180 }, desktop: { width: 1280, height: 800 } };
const SCREENS = [
  ["design", "/design"],
  ["today", "/"],
  ["recipe", `/recipes/${pasta.id}`],
  ["cooking", `/recipes/${pasta.id}/cook?servings=4`],
  ["pantry", "/pantry"],
  ["receipt", receipts[0] ? `/receipts/${receipts[0].id}` : "/receipts"],
  ["nutrition", "/nutrition"],
  ["plan", "/plan"],
  ["health", "/health"],
];

const b2 = await chromium.launch(launchOpts);
for (const [size, vp] of Object.entries(SIZES)) {
  const ctx = await b2.newContext({ viewport: vp, deviceScaleFactor: scale, storageState: state, reducedMotion: "reduce", locale: "en-GB", ignoreHTTPSErrors: insecure, timezoneId: "Europe/London", hasTouch: size !== "desktop" });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.error(`[${size}] page error:`, e.message));
  page.on("console", (m) => { if (m.type() === "error") console.error(`[${size}] console:`, m.text()); });
  for (const [name, path] of SCREENS) {
    if (only && !only.split(",").includes(name)) continue;
    await page.goto(base + path, { waitUntil: "networkidle" });
    if (name === "cooking") { await page.getByRole("button", { name: /^Next/ }).click(); await page.waitForTimeout(150); }
    await page.waitForTimeout(400);
    const file = `${out}/${name}-${size}.${scale > 1 ? "jpg" : "png"}`;
    await page.screenshot({ path: file, fullPage, ...(scale > 1 ? { type: "jpeg", quality: 82 } : {}) });
    console.log("saved", file);
  }
  await ctx.close();
}
await b2.close();
