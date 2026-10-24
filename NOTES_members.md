# Prime: plans, valid-till date, payments, purple theme

## What users see
- **PF Builder button**: every signed-in user. Admins and Prime members open the builder. Everyone else gets **Choose a plan**
  (1 month ₹999/mo · 3 months ₹899/mo · 6 months ₹799/mo · 12 months ₹599/mo, + 18% GST; "Proceed to pay ₹…" includes GST).
  Opening /builder directly without Prime shows the same popup.
- Avatar menu: **Get Prime** (not Prime) / **Extend Prime** + "Prime · valid till 18 Jan 2027" (Prime). Admins see neither.
- Prime members and admins: the page turns purple (night: darkest purple, day: light purple) on Screener, Fundamentals and PF Builder.
- Prices live in TWO places, keep them the same: `PLANS` in public/auth.js (what is shown) and `PLANS` in pay_worker.js (what is charged).

## Admin: Users list (avatar menu → Users)
- Prime members have a purple pill around their name. Prime column: `Make prime` / `Prime ✓` + "till <date>", "no end date", or "ended <date>".
- **Make prime** asks for months (1, 3, 6, 12 …). **Prime ✓** asks for months to ADD (added after the current end date), or 0 to remove.
- Documents: `hni_prime/{uid}` = `{until, months, by, at, src}`. Old documents without `until` = no end date.

## Expiry (automatic)
Past `until`, the Firestore rule refuses the member's read of their own `hni_prime` document, so the page, PF Builder and the
`hni-ltp` Worker (which reads that document with the user's own token) all treat them as not Prime. Nothing to run.

## 1. Firestore rules (once)
Firebase console → Firestore Database → Rules → paste `firebase_rules/firestore.rules` → Publish.

## 2. Payments (Cashfree) — full step-by-step in CASHFREE_SETUP_SOP.md
1. Cloudflare → Workers & Pages → `hni-pay` → Edit code → paste `pay_worker.js` → Deploy.
2. Worker → Settings → Variables and Secrets:
   - Secrets: `CF_APP_ID`, `CF_SECRET_KEY` (Cashfree → Developers → API Keys, production keys), `FIREBASE_SA` (whole service-account JSON).
   - Text variable: `CF_ENV` = `PROD` (live) or `TEST` (sandbox). Nothing else is accepted.
   - Delete the old `RZP_KEY_ID`, `RZP_KEY_SECRET`, `RZP_WEBHOOK_SECRET`.
3. Cashfree → Developers → Webhooks → Add Webhook Endpoint: `https://hni-pay.<your-subdomain>.workers.dev/webhook`, event **Payment Success**.
   (Cashfree signs webhooks with the same Secret Key as the API, so there is no separate webhook secret.)
4. public/auth.js: `PAY_HOOK = "https://hni-pay.<your-subdomain>.workers.dev"` → deploy hosting.
5. Payment flow: plan → Worker makes the Cashfree order (price from its own table, `hni_p3_<uid>_<time>` as order id) → page opens
   Cashfree checkout with `payment_session_id` → Worker (`/verify`, or the webhook if the tab was closed) reads the order and its
   payments back from Cashfree: must be PAID, made by this Worker, SUCCESS payment at the plan's full amount → `hni_prime/{uid}.until`
   (or `hni_plus`) += months and `hni_payments/{order_id}` is written in the same transaction, so one order never counts twice.
