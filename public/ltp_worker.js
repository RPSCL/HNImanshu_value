// ltp_worker.js — Cloudflare Worker "hni-ltp": live prices (⟳ on the Portfolio page + a 10-minute timer) and PF Builder prices.
//
// Why a Worker: starting a GitHub workflow needs a GitHub token. A token in the website would be readable by anyone,
// so it lives here, as a Worker secret, and the website only sends the signed-in user's Firebase login token.
//
// POST /run      (Authorization: Bearer <Firebase ID token>) → starts .github/workflows/ltp.yml
//   • Any signed-in user. Google checks the login token first (a fake token stops there).
//   • Admins (the main "admin" account, or a hni_admins/{uid} document): no limits at all. Any time of day, any day,
//     as often as they like; the workflow is started with any_time = true so it fetches even after 15:40 / at weekends.
//   • Everyone else:
//       – once per 10 minutes, counted from the LAST price update (hni_market/ltp "asOf", the same for every user):
//         if anyone fetched at 09:30, the next fetch is possible from 09:40 for everybody →
//         {error: "Prices can be fetched only after 10 minutes (09:40)", wait: true, next: "09:40"}
//       – Mon–Fri from 09:00 IST. After 15:40 only the day's closing-price run (once; the workflow's gate decides).
//   • A run already going → {error: "already running", running: true}.
//   • Body {symbols: [...]} (PF Builder's picks, Prime / admin only): those are priced too, for the rest of the day.
// POST /builder  {symbols: [...]} → starts .github/workflows/builder_prices.yml (PF Builder's 1-year-back daily prices)
//   • Admins and Prime members (hni_prime/{uid}, read with the user's own token: the Firestore rules refuse an ended Prime).
//   • One run at a time → {error: "another chart is being prepared, try again in a few minutes", running: true}.
// Timer (Cloudflare → Worker → Settings → Triggers → Cron: */10 3-10 * * MON-FRI, UTC): starts ltp.yml unless one is running;
//   the workflow's own gate keeps it to market hours.
//
// Secrets / settings (Worker → Settings → Variables and Secrets):
//   GH_TOKEN  (Secret)  fine-grained GitHub token: only repository RPSCL/HNImanshu_value, permission "Actions: Read and write"
// Nothing else is secret: the Firebase web key below is public by design (same as in auth.js).

const REPO = "RPSCL/HNImanshu_value";
const LTP_WORKFLOW = "ltp.yml";
const BUILDER_WORKFLOW = "builder_prices.yml";
const BRANCH = "main";
const FIREBASE_KEY = "AIzaSyD1oA953pXZxBAuOR9ObSk6vAmKf1gyVbE";
const PROJECT = "hnimanshu";
const MAIN_ADMIN = "admin@users.hnimanshu.app";
const USER_GAP_MIN = 10;              // non-admins: minutes between price updates (from the last update, shared by everyone)
const MAX_SYMBOLS = 300;              // PF Builder: most stocks one request may ask prices for
const ORIGINS = ["https://hnimanshu-value.web.app", "https://hnimanshu-value.firebaseapp.com"];
const FS = `https://firestore.googleapis.com/v1/projects/${PROJECT}/databases/(default)/documents`;

export default {
	async fetch(req, env) {
		const origin = req.headers.get("Origin") || "";
		const cors = {
			"Access-Control-Allow-Origin": ORIGINS.includes(origin) ? origin : ORIGINS[0],
			"Access-Control-Allow-Methods": "POST, OPTIONS",
			"Access-Control-Allow-Headers": "Authorization, Content-Type",
			"Access-Control-Max-Age": "86400",
			"Vary": "Origin",
		};
		const reply = (status, body) => new Response(JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json" } });
		if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
		const path = new URL(req.url).pathname;
		if (req.method !== "POST" || (path !== "/run" && path !== "/builder")) return reply(404, { error: "not found" });
		if (!ORIGINS.includes(origin)) return reply(403, { error: "wrong site" });

		try {
			// 1. who is this? (Google verifies the token)
			const idToken = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
			if (!idToken) return reply(401, { error: "not signed in" });
			const look = await fetch(`https://identitytoolkit.googleapis.com/v1/accounts:lookup?key=${FIREBASE_KEY}`, {
				method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ idToken }),
			});
			const user = look.ok ? ((await look.json()).users || [])[0] : null;
			if (!user) return reply(401, { error: "login expired, sign in again" });
			const asUser = (p) => fetch(`${FS}/${p}`, { headers: { Authorization: `Bearer ${idToken}` } });   // the Firestore rules decide
			const admin = user.email === MAIN_ADMIN || (await asUser(`hni_admins/${encodeURIComponent(user.localId)}`)).ok;

			if (path === "/builder") return await builder(req, env, reply, admin, asUser, user);

			// 2. PF Builder's picks (Prime / admin): priced today too, though nobody holds them yet
			const body = await req.json().catch(() => ({}));
			let want = [];
			if (Array.isArray(body.symbols) && body.symbols.length) {
				const prime = admin || (await asUser(`hni_prime/${encodeURIComponent(user.localId)}`)).ok;
				if (prime) want = [...new Set(body.symbols.map((s) => String(s).toUpperCase().trim()).filter((s) => /^[A-Z0-9&._-]{1,20}$/.test(s)))].slice(0, MAX_SYMBOLS);
			}

			// 3. /run: limits for everyone but admins
			const ist = istNow();
			let late = false;                                   // after the close: Angel's LTP is then the day's closing price
			if (!admin) {
				if (ist.dow >= 6) return reply(429, { error: "Market closed today. Live prices run Mon–Fri from 09:00 IST.", wait: true });
				if (ist.mins < 9 * 60) return reply(429, { error: "Live prices start at 09:00 IST.", wait: true, next: "09:00" });
				const m = await asUser("hni_market/ltp");
				const f = m.ok ? ((await m.json()).fields || {}) : {};
				const asOf = (f.asOf || {}).stringValue || null;       // "YYYY-MM-DD HH:MM" (IST)
				const have = asOf && asOf.slice(0, 10) === ist.date ? Object.keys(((f.prices || {}).mapValue || {}).fields || {}) : [];
				const fresh = want.length && want.some((s) => !have.includes(s));   // builder picks not priced today yet: no waiting
				if (asOf && !fresh) {
					const last = Date.parse(asOf.replace(" ", "T") + ":00+05:30");
					const next = last + USER_GAP_MIN * 60000;
					if (Date.now() < next) {
						const hhmm = istNow(next).hhmm;
						return reply(429, { error: `Prices can be fetched only after ${USER_GAP_MIN} minutes (${hhmm})`, wait: true, next: hhmm, asOf });
					}
					if (ist.mins > 15 * 60 + 40 && asOf.slice(0, 10) === ist.date && asOf.slice(11) >= "15:40")
						return reply(429, { error: `Market closed. Today's closing prices are in (${asOf.slice(11)}).`, wait: true, asOf });
				}
				late = !!fresh && ist.mins > 15 * 60 + 40;
			}

			// 4. one run at a time (GitHub)
			if (await running(env, LTP_WORKFLOW)) return reply(429, { error: "already running", running: true });

			// 5. start it (admins: any time of day)
			const go = await gh(env, `/actions/workflows/${LTP_WORKFLOW}/dispatches`, { method: "POST",
				body: JSON.stringify({ ref: BRANCH, inputs: { any_time: admin || late ? "true" : "false", extra: want.join(",") } }) });
			if (go.status !== 204) return reply(502, { error: `GitHub said ${go.status}` });
			return reply(200, { ok: true, admin });
		} catch (e) {
			return reply(500, { error: String(e && e.message || e).slice(0, 200) });
		}
	},

	// every 10 minutes in market hours (Cron Trigger): live prices for everybody
	async scheduled(event, env, ctx) {
		ctx.waitUntil((async () => {
			if (await running(env, LTP_WORKFLOW)) return;
			await gh(env, `/actions/workflows/${LTP_WORKFLOW}/dispatches`, { method: "POST", body: JSON.stringify({ ref: BRANCH }) });
		})());
	},
};

// PF Builder: day-by-day prices for the stocks of its 1-year-back list (admins and Prime members)
async function builder(req, env, reply, admin, asUser, user) {
	const prime = admin || (await asUser(`hni_prime/${encodeURIComponent(user.localId)}`)).ok;   // ended Prime: the rules refuse the read
	if (!prime) return reply(403, { error: "PF Builder is part of Prime" });
	const body = await req.json().catch(() => ({}));
	const syms = [...new Set((Array.isArray(body.symbols) ? body.symbols : []).map((s) => String(s).toUpperCase().trim())
		.filter((s) => /^[A-Z0-9&._-]{1,20}$/.test(s)))].slice(0, MAX_SYMBOLS);
	if (await running(env, BUILDER_WORKFLOW)) return reply(429, { error: "another chart is being prepared, try again in a few minutes", running: true });
	const go = await gh(env, `/actions/workflows/${BUILDER_WORKFLOW}/dispatches`, { method: "POST",
		body: JSON.stringify({ ref: BRANCH, inputs: { symbols: syms.join(",") } }) });
	if (go.status !== 204) return reply(502, { error: `GitHub said ${go.status}` });
	return reply(200, { ok: true, symbols: syms.length });
}

function gh(env, path, init = {}) {
	return fetch(`https://api.github.com/repos/${REPO}${path}`, { ...init, headers: {
		Authorization: `Bearer ${env.GH_TOKEN}`, Accept: "application/vnd.github+json",
		"X-GitHub-Api-Version": "2022-11-28", "User-Agent": "hnimanshu-ltp-worker", ...(init.headers || {}),
	} });
}
async function running(env, workflow) {                     // the latest run of that workflow is still queued / in progress
	const r = await gh(env, `/actions/workflows/${workflow}/runs?per_page=1`);
	if (!r.ok) return false;
	const last = ((await r.json()).workflow_runs || [])[0];
	return !!(last && last.status !== "completed");
}
function istNow(ms = Date.now()) {                          // India time parts
	const d = new Date(ms + 5.5 * 3600e3);
	const p = (n) => String(n).padStart(2, "0");
	return { dow: d.getUTCDay() === 0 ? 7 : d.getUTCDay(), mins: d.getUTCHours() * 60 + d.getUTCMinutes(),
		hhmm: `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`, date: `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}` };
}
