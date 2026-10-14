// ltp_worker.js — Cloudflare Worker (free plan) behind the website's "⟳ Live prices" button.
//
// Why a Worker: starting a GitHub workflow needs a GitHub token. A token in the website would be readable by anyone,
// so it lives here, as a Worker secret, and the website only sends the signed-in user's Firebase login token.
//
// What it does on POST /run (Authorization: Bearer <Firebase ID token>):
//   1. Google checks the login token (Identity Toolkit accounts:lookup) → uid + email. A fake token stops here.
//   2. Who may press it: every signed-in user (ALLOW_ALL_USERS = true). Set it to false for admins only
//      (the main "admin" account, or a user with a document in Firestore hni_admins/{uid}).
//   3. Not more than once every COOLDOWN_MIN minutes (asks GitHub when the last run started).
//   4. Starts .github/workflows/ltp.yml on main (workflow_dispatch). The run takes about 1–2 minutes.
//
// Clock: a Cloudflare Cron Trigger (Worker → Settings → Trigger events → Cron: */10 3-10 * * MON-FRI) wakes this Worker
//   every 10 minutes; it starts ltp.yml only between 09:00 and 15:40 IST, Monday to Friday. Cloudflare's timer is
//   on time, unlike GitHub's own schedule, which often runs late or skips.
//
// Secrets / settings (Worker → Settings → Variables and Secrets):
//   GH_TOKEN  (Secret)  fine-grained GitHub token: only repository RPSCL/HNImanshu_value, permission "Actions: Read and write"
// Nothing else is secret: the Firebase web key below is public by design (same as in auth.js).

const REPO = "RPSCL/HNImanshu_value";
const WORKFLOW = "ltp.yml";
const BRANCH = "main";
const FIREBASE_KEY = "AIzaSyD1oA953pXZxBAuOR9ObSk6vAmKf1gyVbE";
const PROJECT = "hnimanshu";
const MAIN_ADMIN = "admin@users.hnimanshu.app";
const ALLOW_ALL_USERS = true;      // true = any signed-in user may press the button (cooldown still applies)
const COOLDOWN_MIN = 3;
const ORIGINS = ["https://hnimanshu-value.web.app", "https://hnimanshu-value.firebaseapp.com"];
const AUTO_FROM = 9 * 60, AUTO_TILL = 15 * 60 + 40;   // live prices only 09:00–15:40 IST, Mon–Fri (timer AND button)
const inHours = (ms) => {                              // ms = a UTC timestamp
	const ist = new Date(ms + 5.5 * 3600 * 1000);
	const day = ist.getUTCDay(), min = ist.getUTCHours() * 60 + ist.getUTCMinutes();
	return day !== 0 && day !== 6 && min >= AUTO_FROM && min <= AUTO_TILL;
};

const gh = (env, path, init = {}) => fetch(`https://api.github.com/repos/${REPO}${path}`, {...init, headers: {
	Authorization: `Bearer ${env.GH_TOKEN}`, Accept: "application/vnd.github+json",
	"X-GitHub-Api-Version": "2022-11-28", "User-Agent": "hnimanshu-ltp-worker", ...(init.headers || {}),
}});
/* last run of ltp.yml → null (free to start) or {error, running} */
async function busy(env, cooldownMin) {
	const runs = await gh(env, `/actions/workflows/${WORKFLOW}/runs?per_page=1`);
	if (!runs.ok) return null;
	const last = ((await runs.json()).workflow_runs || [])[0];
	if (!last) return null;
	const ago = (Date.now() - Date.parse(last.created_at)) / 60000;
	if (last.status !== "completed") return {error: "already running", running: true};
	if (ago < cooldownMin) return {error: `ran ${Math.round(ago * 60)} s ago`, running: false};
	return null;
}
const dispatch = (env) => gh(env, `/actions/workflows/${WORKFLOW}/dispatches`, {method: "POST", body: JSON.stringify({ref: BRANCH})});

export default {
	// every 10 min from the Cron Trigger: start ltp.yml inside market hours (IST), unless one is still running
	async scheduled(event, env, ctx) {
		if (!inHours(event.scheduledTime)) return;
		ctx.waitUntil((async () => { if (!(await busy(env, 5))) await dispatch(env); })());
	},

	async fetch(req, env) {
		const origin = req.headers.get("Origin") || "";
		const cors = {
			"Access-Control-Allow-Origin": ORIGINS.includes(origin) ? origin : ORIGINS[0],
			"Access-Control-Allow-Methods": "POST, OPTIONS",
			"Access-Control-Allow-Headers": "Authorization, Content-Type",
			"Access-Control-Max-Age": "86400",
			"Vary": "Origin",
		};
		const reply = (status, body) => new Response(JSON.stringify(body), {status, headers: {...cors, "Content-Type": "application/json"}});
		if (req.method === "OPTIONS") return new Response(null, {status: 204, headers: cors});
		if (req.method !== "POST" || new URL(req.url).pathname !== "/run") return reply(404, {error: "not found"});
		if (!ORIGINS.includes(origin)) return reply(403, {error: "wrong site"});
		if (!inHours(Date.now())) return reply(403, {error: "market closed, live prices run 09:00–15:40 IST, Mon–Fri"});

		// 1. who is this? (Google verifies the token)
		const idToken = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
		if (!idToken) return reply(401, {error: "not signed in"});
		const look = await fetch(`https://identitytoolkit.googleapis.com/v1/accounts:lookup?key=${FIREBASE_KEY}`, {
			method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({idToken}),
		});
		const user = look.ok ? ((await look.json()).users || [])[0] : null;
		if (!user) return reply(401, {error: "login expired, sign in again"});

		// 2. admin?
		let admin = user.email === MAIN_ADMIN;
		if (!admin && !ALLOW_ALL_USERS) {
			const r = await fetch(`https://firestore.googleapis.com/v1/projects/${PROJECT}/databases/(default)/documents/hni_admins/${encodeURIComponent(user.localId)}`,
				{headers: {Authorization: `Bearer ${idToken}`}});
			admin = r.ok;                                        // 200 = the admin document exists (rules let a user read their own)
		}
		if (!admin && !ALLOW_ALL_USERS) return reply(403, {error: "admins only"});

		// 3. cooldown: the last run of ltp.yml
		const b = await busy(env, COOLDOWN_MIN);
		if (b) return reply(429, b);

		// 4. start it
		const go = await dispatch(env);
		if (go.status !== 204) return reply(502, {error: `GitHub said ${go.status}`});
		return reply(200, {ok: true});
	},
};
