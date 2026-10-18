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

## 2. Payments (when you connect Razorpay)
1. Cloudflare → Workers & Pages → Create → Worker, name `hni-pay` → Edit code → paste `pay_worker.js` → Deploy.
2. Worker → Settings → Variables and Secrets → add 4 **Secrets**:
   - `RZP_KEY_ID`, `RZP_KEY_SECRET`: Razorpay Dashboard → Account & Settings → API Keys (live keys).
   - `FIREBASE_SA`: Firebase console → Project settings → Service accounts → Generate new private key → paste the whole JSON.
   - `RZP_WEBHOOK_SECRET`: any long random text; use the same text in step 3.
3. Razorpay Dashboard → Webhooks → Add: URL `https://hni-pay.<your-subdomain>.workers.dev/webhook`, secret = `RZP_WEBHOOK_SECRET`,
   event **payment.captured**. (Covers people who close the tab right after paying.)
4. public/auth.js: set `const PAY_HOOK = "https://hni-pay.<your-subdomain>.workers.dev";` → deploy hosting.
   Until then "Proceed to pay" says payments open soon.
5. Payment flow: plan → Worker makes the Razorpay order (price from its own table) → Razorpay checkout → Worker checks the signature
   and that the payment is captured at the plan's full amount → `hni_prime/{uid}.until` += months (after the current end date if still
   Prime) and `hni_payments/{payment_id}` is written in the same transaction, so one payment never counts twice → page reloads purple.
   Razorpay needs auto-capture on (Razorpay → Account & Settings → Payment capture → Automatic), which is the default.
