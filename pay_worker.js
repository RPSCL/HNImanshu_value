// pay_worker.js — Cloudflare Worker "hni-pay": Prime payments with Razorpay → hni_prime/{uid}.until moves forward by itself.
//
// The website (auth.js, plans popup) only ever sends the signed-in user's Firebase login token and the months picked.
// Prices, the Razorpay secret and the right to write hni_prime all live HERE, never in the page.
//
//   POST /order    {months}                       → Razorpay order for that plan → {order_id, amount, currency, key}
//   POST /verify   {razorpay_order_id, razorpay_payment_id, razorpay_signature}
//                  checks Razorpay's signature + that the payment is captured for this user's order → Prime extended → {ok, until}
//   POST /webhook  Razorpay webhook (event "payment.captured"): the same, for when the person closed the page before /verify ran.
//
// Prime is extended ONCE per payment (hni_payments/{payment_id} is written in the same Firestore transaction; a second try
// finds it and stops). New months start after the current end date if Prime is still on, else today.
//
// Secrets (Worker → Settings → Variables and Secrets, type Secret):
//   RZP_KEY_ID           Razorpay key id      (Razorpay Dashboard → Account & Settings → API Keys)  e.g. rzp_live_xxxxx
//   RZP_KEY_SECRET       Razorpay key secret
//   RZP_WEBHOOK_SECRET   the secret you type when adding the webhook (Razorpay → Webhooks → Add: URL <this worker>/webhook,
//                        event payment.captured)
//   FIREBASE_SA          the whole JSON of a Firebase service account key (Firebase console → Project settings → Service accounts
//                        → Generate new private key). It lets this Worker write hni_prime / hni_payments (rules don't apply to it).

const PROJECT = "hnimanshu";
const FIREBASE_KEY = "AIzaSyD1oA953pXZxBAuOR9ObSk6vAmKf1gyVbE";       // public web key (same as auth.js), only to check login tokens
const ORIGINS = ["https://hnimanshu-value.web.app", "https://hnimanshu-value.firebaseapp.com"];
// ₹ per month before GST — keep in step with PLANS in auth.js (the page shows them, this table is what is charged)
const PLANS = { 1: 999, 3: 899, 6: 799, 12: 599 };
const GST = 0.18;
const payable = (m) => Math.round(PLANS[m] * m * (1 + GST));        // rupees, GST included; same rounding as the page

const FS = `https://firestore.googleapis.com/v1/projects/${PROJECT}/databases/(default)/documents`;
const DOC = (path) => `projects/${PROJECT}/databases/(default)/documents/${path}`;

export default {
	async fetch(req, env) {
		const url = new URL(req.url), origin = req.headers.get("Origin") || "";
		const cors = {
			"Access-Control-Allow-Origin": ORIGINS.includes(origin) ? origin : ORIGINS[0],
			"Access-Control-Allow-Methods": "POST, OPTIONS",
			"Access-Control-Allow-Headers": "Authorization, Content-Type",
			"Access-Control-Max-Age": "86400",
			"Vary": "Origin",
		};
		const reply = (status, body) => new Response(JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json" } });
		if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
		if (req.method !== "POST") return reply(404, { error: "not found" });
		try {
			if (url.pathname === "/webhook") return await webhook(req, env, reply);
			if (!ORIGINS.includes(origin)) return reply(403, { error: "wrong site" });
			const user = await whoIs(req);
			if (!user) return reply(401, { error: "Login expired. Please sign in again." });
			const body = await req.json().catch(() => ({}));
			if (url.pathname === "/order") return await order(body, user, env, reply);
			if (url.pathname === "/verify") return await verify(body, user, env, reply);
			return reply(404, { error: "not found" });
		} catch (e) {
			return reply(500, { error: String(e && e.message || e).slice(0, 200) });
		}
	},
};

// ---------- the signed-in user (Google checks the Firebase login token) ----------
async function whoIs(req) {
	const idToken = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
	if (!idToken) return null;
	const r = await fetch(`https://identitytoolkit.googleapis.com/v1/accounts:lookup?key=${FIREBASE_KEY}`, {
		method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ idToken }),
	});
	const u = r.ok ? ((await r.json()).users || [])[0] : null;
	return u ? { uid: u.localId, email: u.email || "" } : null;
}

// ---------- Razorpay ----------
const rzp = (env, path, init = {}) => fetch(`https://api.razorpay.com/v1${path}`, { ...init, headers: {
	Authorization: "Basic " + btoa(`${env.RZP_KEY_ID}:${env.RZP_KEY_SECRET}`), "Content-Type": "application/json", ...(init.headers || {}) } })
	.then(async (r) => { const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error("Razorpay: " + ((j.error && j.error.description) || r.status)); return j; });

async function order(body, user, env, reply) {
	const m = Number(body.months);
	if (!PLANS[m]) return reply(400, { error: "Unknown plan." });
	const o = await rzp(env, "/orders", { method: "POST", body: JSON.stringify({
		amount: payable(m) * 100, currency: "INR",
		receipt: `p${m}_${user.uid.slice(0, 20)}_${Date.now().toString(36)}`.slice(0, 40),
		notes: { uid: user.uid, months: String(m), email: user.email },
	}) });
	return reply(200, { order_id: o.id, amount: o.amount, currency: o.currency, key: env.RZP_KEY_ID });
}

async function verify(body, user, env, reply) {
	const { razorpay_order_id: oid, razorpay_payment_id: pid, razorpay_signature: sig } = body || {};
	if (!oid || !pid || !sig) return reply(400, { error: "Payment details missing." });
	if (!safeEq(await hmacHex(env.RZP_KEY_SECRET, `${oid}|${pid}`), String(sig))) return reply(400, { error: "Payment signature did not match." });
	const r = await applyPayment(env, pid, user.uid);
	return r.error ? reply(400, r) : reply(200, r);
}

// Razorpay → POST /webhook (raw body signed with RZP_WEBHOOK_SECRET). Always 200 once the signature is right, so Razorpay stops retrying.
async function webhook(req, env, reply) {
	const raw = await req.text();
	if (!safeEq(await hmacHex(env.RZP_WEBHOOK_SECRET, raw), req.headers.get("X-Razorpay-Signature") || "")) return reply(400, { error: "bad signature" });
	const ev = JSON.parse(raw);
	const p = ev.payload && ev.payload.payment && ev.payload.payment.entity;
	if (ev.event !== "payment.captured" || !p) return reply(200, { ignored: ev.event });
	return reply(200, await applyPayment(env, p.id, null));
}

// The one place Prime is given: payment captured, for an order this Worker made, at the full price of its plan.
async function applyPayment(env, pid, uidMustBe) {
	const p = await rzp(env, `/payments/${encodeURIComponent(pid)}`);
	if (p.status !== "captured") return { error: `Payment is ${p.status}, not captured yet.` };
	if (!p.order_id) return { error: "Payment has no order." };
	const o = await rzp(env, `/orders/${encodeURIComponent(p.order_id)}`);
	const uid = o.notes && o.notes.uid, m = Number(o.notes && o.notes.months);
	if (!uid || !PLANS[m]) return { error: "Order is not a Prime order." };
	if (uidMustBe && uid !== uidMustBe) return { error: "This payment belongs to another account." };
	if (p.amount !== o.amount || o.amount !== payable(m) * 100 || p.currency !== "INR") return { error: "Amount does not match the plan." };
	return extendPrime(env, uid, m, { pid, oid: o.id, amount: p.amount });
}

// ---------- Firestore (service account; one transaction: read Prime, write the new date + the payment record) ----------
async function extendPrime(env, uid, months, pay) {
	const tok = await googleToken(env);
	const call = (path, init = {}) => fetch(`${FS}${path}`, { ...init, headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json" } });
	for (let attempt = 0; attempt < 3; attempt++) {
		const tx = (await (await call(":beginTransaction", { method: "POST", body: "{}" })).json()).transaction;
		const q = `?transaction=${encodeURIComponent(tx)}`;
		const done = await call(`/hni_payments/${encodeURIComponent(pay.pid)}${q}`);
		if (done.ok) {                                                    // already applied (verify + webhook both arrive): report, change nothing
			await call(":rollback", { method: "POST", body: JSON.stringify({ transaction: tx }) });
			return { ok: true, until: await currentUntil(call, uid), already: true };
		}
		const cur = await call(`/hni_prime/${encodeURIComponent(uid)}${q}`);
		let from = Date.now();
		if (cur.ok) {
			const f = (await cur.json()).fields || {};
			const t = f.until && Date.parse(f.until.timestampValue);
			if (t && t > from) from = t;                                  // still Prime: add after the current end date
		}
		const until = addMonths(from, months), now = new Date().toISOString();
		const r = await call(":commit", { method: "POST", body: JSON.stringify({ transaction: tx, writes: [
			{ update: { name: DOC(`hni_prime/${uid}`), fields: {
				until: { timestampValue: new Date(until).toISOString() }, months: { integerValue: String(months) },
				by: { stringValue: "razorpay" }, src: { stringValue: "razorpay" }, at: { timestampValue: now },
				payment: { stringValue: pay.pid } } } },
			{ update: { name: DOC(`hni_payments/${pay.pid}`), fields: {
				uid: { stringValue: uid }, months: { integerValue: String(months) }, amount: { integerValue: String(pay.amount) },
				order: { stringValue: pay.oid }, at: { timestampValue: now }, until: { timestampValue: new Date(until).toISOString() } } },
			  currentDocument: { exists: false } },
		] }) });
		if (r.ok) return { ok: true, until };
		if (r.status !== 409 && r.status !== 400 && r.status !== 412) throw new Error("Firestore " + r.status + ": " + (await r.text()).slice(0, 150));
	}
	throw new Error("Busy, please try again.");
}
async function currentUntil(call, uid) {
	const r = await call(`/hni_prime/${encodeURIComponent(uid)}`);
	const f = r.ok ? ((await r.json()).fields || {}) : {};
	return f.until ? Date.parse(f.until.timestampValue) : null;
}
// calendar months, kept on the same day (31 Jan + 1 → 28/29 Feb), India time
function addMonths(ms, k) {
	const ist = 5.5 * 3600e3, d = new Date(ms + ist), day = d.getUTCDate();
	d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() + k);
	const last = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).getUTCDate();
	d.setUTCDate(Math.min(day, last));
	return d.getTime() - ist;
}

// Google access token for the service account (signed JWT → OAuth), kept for 50 minutes
let TOKEN = null;
async function googleToken(env) {
	if (TOKEN && TOKEN.exp > Date.now()) return TOKEN.value;
	const sa = JSON.parse(env.FIREBASE_SA), now = Math.floor(Date.now() / 1000);
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

// ---------- signatures ----------
async function hmacHex(secret, msg) {
	const k = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret || ""), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
	const s = await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(msg));
	return [...new Uint8Array(s)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
function safeEq(a, b) {
	if (!a || a.length !== b.length) return false;
	let d = 0; for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
	return d === 0;
}
