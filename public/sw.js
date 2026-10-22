/* sw.js — HNImanshu offline cache (screener + Fundamentals). Free: a browser feature, no server.
   Pages and data (.html, .json): answered at once from the phone's cache, then refreshed in the background;
     if the refresh brought something new, the page is told and shows "New data · Reload".
   Versioned files that never change (Firebase SDK on gstatic, Google Fonts): cached once, served from cache.
   Logins, portfolios, live prices (Firebase Auth / Firestore / Realtime DB): never touched, always live. */
const CACHE = "hni-v1";
const SAME = self.location.origin;
const FOREVER = [/^https:\/\/www\.gstatic\.com\/firebasejs\//, /^https:\/\/fonts\.(googleapis|gstatic)\.com\//];

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(
	caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())
));

self.addEventListener("fetch", (e) => {
	const req = e.request;
	if (req.method !== "GET") return;
	const url = new URL(req.url);
	if (FOREVER.some((re) => re.test(req.url))) return e.respondWith(cacheFirst(req));
	if (url.origin !== SAME || url.pathname.startsWith("/__/")) return;   // Firebase calls, reserved URLs: straight to the network
	if (/^\/(builder_[a-z]+|build_info)\.json$/.test(url.pathname)) return e.respondWith(networkFirst(req));   // PF Builder prices / build stamp: always the newest
	e.respondWith(staleWhileRevalidate(req, e));
});

/* newest copy from the network; the saved one only when offline */
async function networkFirst(req) {
	const c = await caches.open(CACHE), key = new Request(new URL(req.url).pathname);   // one saved copy, whatever the ?query
	try {
		const res = await fetch(req, {cache: "no-cache"});
		if (res.ok && res.type === "basic") c.put(key, res.clone());
		return res;
	} catch (e) {
		return (await c.match(key)) || new Response("Offline and not cached yet.", {status: 503, headers: {"Content-Type": "text/plain"}});
	}
}

async function cacheFirst(req) {
	const c = await caches.open(CACHE);
	const hit = await c.match(req);
	if (hit) return hit;
	const res = await fetch(req);
	if (res.ok || res.type === "opaque") c.put(req, res.clone());
	return res;
}

/* cached copy now; fresh copy in the background; tell the page when the fresh one is different */
async function staleWhileRevalidate(req, e) {
	const c = await caches.open(CACHE);
	const key = req.mode === "navigate" ? new Request(new URL(req.url).pathname) : req;   // one entry per page, whatever the #hash / ?query
	const hit = await c.match(key, {ignoreSearch: req.mode === "navigate"});
	const fresh = fetch(req, {cache: "no-cache"}).then(async (res) => {
		if (res.ok && res.type === "basic") {
			const old = hit && (hit.headers.get("etag") || hit.headers.get("last-modified"));
			const now = res.headers.get("etag") || res.headers.get("last-modified");
			await c.put(key, res.clone());
			if (hit && old && now && old !== now && /\.(html|json)$|\/[^.]*$/.test(new URL(req.url).pathname)) {
				const all = await self.clients.matchAll({type: "window"});
				all.forEach((w) => w.postMessage({type: "hni-updated", url: req.url}));
			}
		}
		return res;
	}).catch(() => hit || new Response("Offline and not cached yet.", {status: 503, headers: {"Content-Type": "text/plain"}}));
	if (hit) { e.waitUntil(fresh); return hit; }
	return fresh;
}
