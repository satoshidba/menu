/* Larder service worker: app shell + read-only API cache for intermittent connectivity.
   GET /api/v1 views (recipes, pantry, shopping list, today, plan, media) are network-first with a cached fallback.
   Mutations are never cached or replayed: offline edits fail visibly (the offline edit queue is Release 2). */
const VERSION = "larder-v1";
const SHELL = ["/", "/manifest.webmanifest", "/icons/icon.svg"];
const CACHEABLE = [/^\/api\/v1\/(recipes|pantry|shopping-list|today|plan|people|auth\/me|nutrition\/recipes|jobs)/, /^\/api\/v1\/media\//];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) {
    if (!CACHEABLE.some((r) => r.test(url.pathname))) return;
    e.respondWith(
      fetch(req).then((res) => {
        if (res.ok) { const copy = res.clone(); caches.open(VERSION).then((c) => c.put(req, copy)); }
        return res;
      }).catch(async () => {
        const hit = await caches.match(req);
        if (!hit) return new Response(JSON.stringify({ detail: "You're offline and this hasn't been saved on this device yet." }), { status: 503, headers: { "Content-Type": "application/json" } });
        const h = new Headers(hit.headers); h.set("X-Larder-Offline", "1");
        return new Response(await hit.blob(), { status: hit.status, headers: h });
      }),
    );
    return;
  }
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).then((res) => { const copy = res.clone(); caches.open(VERSION).then((c) => c.put("/", copy)); return res; }).catch(() => caches.match("/")));
    return;
  }
  if (url.pathname.startsWith("/assets/") || url.pathname.startsWith("/icons/")) {
    e.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((res) => { const copy = res.clone(); caches.open(VERSION).then((c) => c.put(req, copy)); return res; })));
  }
});
