// data_worker.js — Cloudflare Worker "hni-data": the Plus / Prime data files, only for signed-in Plus / Prime members.
//
// The build publishes those files encrypted (paid.py → /p/<name>.bin on the site). The page (auth.js) asks THIS Worker for
// them instead, with the user's Firebase login token:
//
//   GET /f/<name>     e.g. /f/fundamentals_oth.json      Authorization: Bearer <Firebase ID token>
//       → 401 not logged in · 403 no Plus / Prime · 200 the JSON (gzip)
//
// Who may read: an admin (the admin account or hni_admins/{uid}), a Prime member (hni_prime/{uid}, `until` not passed) or a
// Plus member (hni_plus/{uid}, `until` not passed) — the same rules as the website.
//
// Secrets (Cloudflare → Workers → hni-data → Settings → Variables and Secrets, type Secret):
//   PAID_KEY      the SAME value as the GitHub Actions secret PAID_KEY (32 random bytes, base64)
//   FIREBASE_SA   the same service-account JSON the pay Worker uses (it reads hni_admins / hni_prime / hni_plus)

const PROJECT = "hnimanshu";
const FIREBASE_KEY = "AIzaSyD1oA953pXZxBAuOR9ObSk6vAmKf1gyVbE";       // public web key (same as auth.js), only to check login tokens
const SITE = "https://hnimanshu-value.web.app";
const ORIGINS = ["https://hnimanshu-value.web.app", "https://hnimanshu-value.firebaseapp.com"];
const ADMIN_EMAIL = "admin@users.hnimanshu.app";
const NAME_RE = /^(fundamentals_(?:back_[A-Za-z0-9_]+_|hist_(?:then_)?)?oth|screener_oth)\.json$/;   // = paid.py PAID_RE
const FS = `https://firestore.googleapis.com/v1/projects/${PROJECT}/databases/(default)/documents`;

export default {
	async fetch(req, env) {
		const url = new URL(req.url), origin = req.headers.get("Origin") || "";
		const cors = {
			"Access-Control-Allow-Origin": ORIGINS.includes(origin) ? origin : ORIGINS[0],
			"Access-Control-Allow-Methods": "GET, OPTIONS",
			"Access-Control-Allow-Headers": "Authorization",
			"Access-Control-Max-Age": "86400",
			"Vary": "Origin",
		};
		const say = (status, msg) => new Response(JSON.stringify({ error: msg }), { status, headers: { ...cors, "Content-Type": "application/json" } });
		if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
		if (req.method !== "GET") return say(405, "GET only");
		const m = url.pathname.match(/^\/f\/([^/]+)$/);
		if (!m || !NAME_RE.test(m[1])) return say(404, "not found");
		if (origin && !ORIGINS.includes(origin)) return say(403, "wrong site");
		try {
			const user = await whoIs(req);
			if (!user) return say(401, "Please log in.");
			if (!(await allowed(user, env))) return say(403, "Plus or Prime needed.");
			const r = await fetch(`${SITE}/p/${m[1]}.bin`, { cf: { cacheTtl: 300, cacheEverything: true } });
			if (!r.ok) return say(r.status === 404 ? 404 : 502, "file not found");
			const gz = await decrypt(new Uint8Array(await r.arrayBuffer()), env);
			return new Response(gz, { status: 200, encodeBody: "manual", headers: { ...cors,
				"Content-Type": "application/json; charset=utf-8", "Content-Encoding": "gzip", "Cache-Control": "private, max-age=300" } });
		} catch (e) {
			console.error(url.pathname, (e && e.stack) || e);
			return say(500, "Server error");
		}
	},
};

// ---------- the signed-in user (Google checks the Firebase login token), remembered 5 minutes ----------
const WHO = new Map();
async function whoIs(req) {
	const idToken = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
	if (!idToken) return null;
	const hit = WHO.get(idToken);
	if (hit && hit.exp > Date.now()) return hit.u;
	const r = await fetch(`https://identitytoolkit.googleapis.com/v1/accounts:lookup?key=${FIREBASE_KEY}`, {
		method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ idToken }),
	});
	const x = r.ok ? ((await r.json()).users || [])[0] : null;
	const u = x ? { uid: x.localId, email: (x.email || "").toLowerCase() } : null;
	if (u) { if (WHO.size > 500) WHO.clear(); WHO.set(idToken, { u, exp: Date.now() + 5 * 60e3 }); }
	return u;
}

// ---------- Plus / Prime / admin? (remembered 10 minutes per account) ----------
const OK = new Map();
async function allowed(user, env) {
	const hit = OK.get(user.uid);
	if (hit && hit.exp > Date.now()) return hit.ok;
	let ok = user.email === ADMIN_EMAIL;
	if (!ok) {
		const tok = await googleToken(env);
		const get = (p) => fetch(`${FS}/${p}/${encodeURIComponent(user.uid)}`, { headers: { Authorization: `Bearer ${tok}` } });
		const inDate = async (r) => {
			if (!r.ok) return false;
			const f = ((await r.json()).fields || {}).until;
			const t = f && (f.timestampValue ? Date.parse(f.timestampValue) : f.integerValue ? +f.integerValue : null);
			return !t || t > Date.now();                                   // no end date, or not passed yet
		};
		const [a, p, l] = await Promise.all([get("hni_admins"), get("hni_prime"), get("hni_plus")]);
		ok = a.ok || await inDate(p) || await inDate(l);
	}
	if (OK.size > 2000) OK.clear();
	OK.set(user.uid, { ok, exp: Date.now() + 10 * 60e3 });
	return ok;
}

// ---------- AES-256-GCM: blob = 12-byte nonce + ciphertext (paid.py) ----------
let KEY = null;
async function decrypt(blob, env) {
	if (!KEY) {
		const raw = Uint8Array.from(atob(String(env.PAID_KEY || "").trim()), (c) => c.charCodeAt(0));
		if (raw.length !== 32) throw new Error("PAID_KEY is not 32 bytes (base64)");
		KEY = await crypto.subtle.importKey("raw", raw, { name: "AES-GCM" }, false, ["decrypt"]);
	}
	return new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: blob.slice(0, 12) }, KEY, blob.slice(12)));
}

// ---------- Google access token for the service account (signed JWT → OAuth), kept for 50 minutes ----------
let TOKEN = null;
async function googleToken(env) {
	if (TOKEN && TOKEN.exp > Date.now()) return TOKEN.value;
	const sa = JSON.parse(env.FIREBASE_SA || "{}"), now = Math.floor(Date.now() / 1000);
	if (!sa.private_key || !sa.client_email) throw new Error("FIREBASE_SA is not the service-account JSON");
	const b64u = (x) => btoa(typeof x === "string" ? x : String.fromCharCode(...new Uint8Array(x))).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
	const head = b64u(JSON.stringify({ alg: "RS256", typ: "JWT" }));
	const claim = b64u(JSON.stringify({ iss: sa.client_email, scope: "https://www.googleapis.com/auth/datastore",
		aud: "https://oauth2.googleapis.com/token", iat: now, exp: now + 3600 }));
	const der = Uint8Array.from(atob(sa.private_key.replace(/-----[^-]+-----|\s/g, "")), (c) => c.charCodeAt(0));
	const key = await crypto.subtle.importKey("pkcs8", der, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"]);
	const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, new TextEncoder().encode(`${head}.${claim}`));
	const r = await fetch("https://oauth2.googleapis.com/token", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
		body: `grant_type=${encodeURIComponent("urn:ietf:params:oauth:grant-type:jwt-bearer")}&assertion=${head}.${claim}.${b64u(sig)}` });
	const j = await r.json();
	if (!j.access_token) throw new Error("Service account login failed: " + (j.error_description || j.error || r.status));
	TOKEN = { value: j.access_token, exp: Date.now() + 50 * 60e3 };
	return TOKEN.value;
}
