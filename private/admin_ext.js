// admin_ext.js — the admin tools. NOT part of the website files and NOT for the public GitHub repo.
//
// It is stored in Firestore (hni_ext/ui, field "code") by push_ext.py, and the Firestore rules let only admins read
// that document. auth.js reads it after login; for admins it loads this code, for everyone else the read is refused.
// So a guest's page source has no Users list, no "Make admin", no portfolio viewer and no Worker address.
//
// What it adds:  menu → "Users" (everyone who logged in; on Fundamentals with portfolio worth / day and a View button;
//                Make admin / Remove admin), menu → "Refresh live prices", the "⟳" button on Portfolio,
//                and the "N online (total)" readout in the bottom-left corner.
// ctx from auth.js: {app, auth, user, gated, addItem(label, fn), syncLock(), esc, $}

import { getFirestore, doc, getDoc, getDocs, setDoc, deleteDoc, collection, serverTimestamp }
  from "https://www.gstatic.com/firebasejs/10.13.0/firebase-firestore.js";

const MAIN_ADMIN = "admin@users.hnimanshu.app";   // the "admin" username: always admin, can't be removed
const USERS = "hni_users", ADMINS = "hni_admins", PORTFOLIO = "hni_portfolio", MARKET = ["hni_market", "ltp"];
// Cloudflare Worker (ltp_worker.js) that starts ltp.yml on GitHub. No secret here: the GitHub token lives in the Worker.
const LTP_HOOK = "https://hni-ltp.himanshushekhar8.workers.dev/run";

const CSS = `
.au-wide{width:min(860px,100%);max-height:86vh;display:flex;flex-direction:column}
.au-q{height:34px;padding:0 12px;margin:8px 0 10px;border:1px solid var(--au-line);border-radius:8px;background:var(--au-field);color:var(--au-ink);font:13px var(--au-sans);outline:none}
.au-q:focus{border-color:var(--au-acc)}
.au-count{color:var(--au-faint);font-weight:500}
.au-tbl{overflow:auto;border:1px solid var(--au-line);border-radius:8px}
.au-tbl table{width:100%;border-collapse:collapse;font:12.5px var(--au-sans)}
.au-tbl th{position:sticky;top:0;background:var(--au-field);color:var(--au-faint);font:600 11px var(--au-mono);text-align:left;padding:8px 10px;border-bottom:1px solid var(--au-line)}
.au-tbl td{background:transparent;text-align:left;font:12.5px var(--au-sans);padding:7px 10px;border-bottom:1px solid var(--au-line);color:var(--au-ink);white-space:nowrap}
.au-tbl tr:last-child td{border-bottom:0}
.au-tbl tbody tr:hover td{background:color-mix(in srgb, var(--au-ink) 8%, transparent);color:var(--au-ink)}
.au-tbl tbody tr:hover td:first-child{box-shadow:inset 2px 0 0 var(--au-acc)}
.au-online{position:fixed;left:14px;bottom:10px;z-index:250;display:flex;flex-direction:column;align-items:flex-start;gap:3px;pointer-events:none;user-select:none}
.au-online i{width:6px;height:6px;border-radius:50%;background:#2fbf6a;box-shadow:0 0 3px 1px rgba(47,191,106,.45);animation:au-pulse 2.6s ease-in-out infinite}
.au-online span{font:9.5px var(--au-mono);letter-spacing:.02em;color:var(--au-faint)}
@keyframes au-pulse{0%,100%{opacity:.75}50%{opacity:1}}
.au-mk{border:1px solid var(--au-line);background:var(--au-field);color:var(--au-mute);font:600 11px var(--au-mono);padding:4px 9px;border-radius:6px;cursor:pointer;white-space:nowrap}
.au-mk:hover{border-color:var(--au-acc);color:var(--au-ink)}
.au-mk.on{border-color:var(--au-acc);color:var(--au-acc)}
.au-tag{font:600 11px var(--au-mono);color:var(--au-faint)}
.au-up{color:var(--good, #3fae6a)} .au-dn{color:var(--bad, #e06666)}
.au-pfv{z-index:310;align-items:center}
.au-pfv .au-box{width:min(960px,100%);max-height:calc(100vh - 32px);max-height:calc(100dvh - 32px);padding:16px 16px 12px}
.au-pfv .au-title{padding-right:36px;flex:none}
.au-pfbody{flex:1 1 auto;min-height:0;overflow-y:auto;overscroll-behavior:contain;-webkit-overflow-scrolling:touch;margin:8px -4px 0;padding:0 4px 4px}
.au-pfv .au-tbl{overflow-x:auto;overflow-y:hidden;max-height:none}
.au-pfv .au-tbl th{position:static}
.au-pfv .au-tbl tbody tr:hover td{background:transparent}
.au-pfv .au-tbl tbody tr:hover td:first-child{box-shadow:none}
.au-pfv .au-tbl td.r,.au-pfv .au-tbl th.r{text-align:right}
.au-pfv .pf-sum{margin-bottom:12px}
.au-pfh{display:flex;align-items:baseline;gap:8px;font:600 12px var(--au-mono);color:var(--au-mute);margin:14px 0 6px}
.au-pfh small{font-weight:500;color:var(--au-faint)}
.au-pfwarn{font-size:12px;color:var(--au-bad);margin:0 0 10px}
.au-toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:400;background:var(--au-bg);color:var(--au-ink);border:1px solid var(--au-line);
  border-radius:9px;padding:9px 14px;font:600 12px var(--au-mono);box-shadow:0 10px 28px rgba(0,0,0,.35);opacity:0;transition:opacity .2s;pointer-events:none}
.au-toast.on{opacity:1}
@media (max-width:700px){
  .au-pfv{align-items:stretch;padding:8px}
  .au-pfv .au-box{max-height:none;height:100%;border-radius:12px}
  .au-pfv .au-tbl td,.au-pfv .au-tbl th{padding:6px 8px}
}`;

let X, db, rtdb = null, R = null;   // ctx, Firestore, Realtime Database (+ its functions)
const fmtT = (t) => (t && t.toDate ? t.toDate().toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }) : "–");
const inr = (v, sign) => v == null ? "–" : (v < 0 ? "−" : sign && v > 0 ? "+" : "") + "₹" + Math.round(Math.abs(v)).toLocaleString("en-IN");

export default async function init(ctx) {
  X = ctx;
  db = getFirestore(ctx.app);
  if ((ctx.app.options || {}).databaseURL) {
    try { R = await import("https://www.gstatic.com/firebasejs/10.13.0/firebase-database.js"); rtdb = R.getDatabase(ctx.app); } catch (e) {}
  }
  const st = document.createElement("style"); st.textContent = CSS; document.head.appendChild(st);

  // Portfolio "⟳" button + menu item: start ltp.yml through the Worker → {ok} | {error, running}
  if (window.hniStore) {
    window.hniStore.refresh = async () => {
      const r = await fetch(LTP_HOOK, { method: "POST", headers: { Authorization: "Bearer " + await ctx.user.getIdToken() } });
      return r.json().catch(() => ({ error: "HTTP " + r.status }));
    };
    window.dispatchEvent(new Event("hni-ext"));
  }
  ctx.addItem("Users", openUsers);
  ctx.addItem("Refresh live prices", async () => {
    let r; try { r = await window.hniStore.refresh(); } catch (e) { r = { error: "no connection" }; }
    toast(r.ok ? "Live prices in about a minute" : r.running ? "Already running, about a minute" : "Live prices: " + (r.error || "failed"));
  });
  onlineDot();
}

function toast(msg) {
  let t = document.querySelector(".au-toast");
  if (!t) { t = document.createElement("div"); t.className = "au-toast"; t.setAttribute("role", "status"); document.body.appendChild(t); }
  t.textContent = msg; t.classList.add("on");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("on"), 3200);
}

// ---------- Users list ----------
async function openUsers() {
  const { $, esc, syncLock } = X;
  let back = $(".au-users");
  if (!back) {
    back = document.createElement("div");
    back.className = "au-back au-users";
    back.innerHTML = `<div class="au-box au-wide" role="dialog" aria-modal="true">
      <button class="au-x" type="button" aria-label="Close">×</button>
      <h2 class="au-title">Users <span class="au-count"></span></h2>
      <input class="au-q" placeholder="Search username or email" spellcheck="false">
      <div class="au-tbl"></div></div>`;
    document.body.appendChild(back);
    const close = () => { back.classList.remove("open"); syncLock(); };
    $(".au-x", back).addEventListener("click", close);
    back.addEventListener("click", (e) => { if (e.target === back) close(); });
    back.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Escape") close(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && back.classList.contains("open") && !$(".au-pfv.open")) close(); });
    $(".au-q", back).addEventListener("input", (e) => paint(back, e.target.value));
  }
  back.classList.add("open"); syncLock();
  $(".au-tbl", back).innerHTML = '<p class="au-sub">Loading…</p>';
  try {
    const snap = await getDocs(collection(db, USERS));
    const adm = new Set((await getDocs(collection(db, ADMINS))).docs.map((d) => d.id));
    back._rows = snap.docs.map((d) => Object.assign({ uid: d.id, admin: adm.has(d.id) }, d.data()))
      .sort((a, b) => ((b.lastLogin && b.lastLogin.seconds) || 0) - ((a.lastLogin && a.lastLogin.seconds) || 0));
    paint(back, "");
    if (window.hniPf) {                                       // Fundamentals page: each user's portfolio worth and day gain
      const mkt = await getDoc(doc(db, ...MARKET)).then((d) => (d.exists() ? d.data() : null)).catch(() => null);
      back._mkt = mkt;
      await Promise.all(back._rows.map(async (r) => {
        try {
          const d = await getDoc(doc(db, PORTFOLIO, r.uid));
          r.pfData = d.exists() ? d.data() : null;
          r.pf = r.pfData ? window.hniPf.calc(r.pfData, mkt).P : null;
        } catch (e) { r.pf = undefined; }
      }));
      paint(back, $(".au-q", back).value);
    }
  } catch (e) {
    $(".au-tbl", back).innerHTML = `<p class="au-err">Couldn't load users (${esc(e.code || e)}). Check the Firestore rules.</p>`;
  }
}
function paint(back, q) {
  const { $, esc } = X, pf = !!window.hniPf;
  q = q.trim().toLowerCase();
  const rows = (back._rows || []).filter((r) => !q || (r.username + " " + (r.email || "")).toLowerCase().includes(q));
  $(".au-count", back).textContent = `· ${rows.length}`;
  const pfCell = (r) => !pf ? "" : `<td>${r.pf === undefined ? '<span class="au-tag">no access</span>' : r.pf ? inr(r.pf.worth) : "–"}</td>
    <td class="${r.pf && r.pf.day != null ? (r.pf.day >= 0 ? "au-up" : "au-dn") : ""}">${r.pf ? inr(r.pf.day, true) : "–"}</td>
    <td>${r.pfData ? `<button type="button" class="au-mk au-view" data-pf="${esc(r.uid)}">View</button>` : ""}</td>`;
  $(".au-tbl", back).innerHTML = `<table><thead><tr><th>Username</th><th>Email</th><th>Via</th><th>Joined</th><th>Last login</th>${pf ? "<th>Portfolio</th><th>Day</th><th></th>" : ""}<th>Admin</th></tr></thead><tbody>${
    rows.map((r) => `<tr><td>${esc(r.username || "")}</td><td>${r.provider === "google" ? esc(r.email || "") : "–"}</td><td>${esc(r.provider || "")}</td><td>${fmtT(r.created)}</td><td>${fmtT(r.lastLogin)}</td>${pfCell(r)}<td>${
      r.email === MAIN_ADMIN ? '<span class="au-tag">main</span>'
        : `<button type="button" class="au-mk${r.admin ? " on" : ""}" data-uid="${esc(r.uid)}">${r.admin ? "Admin ✓" : "Make admin"}</button>`}</td></tr>`).join("")
  }</tbody></table>`;
  back.querySelectorAll(".au-mk[data-uid]").forEach((b) => b.addEventListener("click", () => setAdmin(back, b.dataset.uid)));
  back.querySelectorAll("[data-pf]").forEach((b) => b.addEventListener("click", () => {
    const r = back._rows.find((x) => x.uid === b.dataset.pf); if (r) viewPortfolio(r, back._mkt);
  }));
}

// ---------- one user's portfolio, read-only (light plain tables: opens at once, scrolls smoothly) ----------
function pfHtml(d, mkt) {
  const H = window.hniPf, { fmt, rs, signed, esc, TX_LABEL } = H;
  const { P, txns } = H.calc(d, mkt), nl = new Set((mkt && mkt.notListed) || []);
  const k = (a, b, c = "") => `<div class="pf-k ${c}"><span>${a}</span><b>${b}</b></div>`;
  const h = (title, n, sub = "") => `<div class="au-pfh">${title} <small>${n}${sub ? " · " + sub : ""}</small></div>`;
  const tbl = (head, body, empty, cols) => `<div class="au-tbl"><table><thead><tr>${head}</tr></thead><tbody>${body || `<tr><td colspan="${cols}">${empty}</td></tr>`}</tbody></table></div>`;
  const rows = P.open.slice().sort((a, b) => b.value - a.value).map((r) => `<tr><td><b>${esc(r.sym)}</b>${nl.has(r.sym) ? ' <span class="au-dn" title="Not on NSE\'s list today">⚠</span>' : ""}</td><td class="r">${fmt(r.qty, 0)}</td><td class="r">${fmt(r.avg, 2)}</td><td class="r">${r.ltp == null ? "–" : fmt(r.ltp, 2)}</td>
    <td class="r">${fmt(r.value, 0)}</td><td class="r">${r.priced ? signed(r.pnl) : "–"}</td><td class="r">${r.day == null ? "–" : signed(r.day)}</td></tr>`).join("");
  const cl = P.closed.slice().sort((a, b) => String(b.last || "").localeCompare(String(a.last || ""))).map((r) => `<tr><td><b>${esc(r.sym)}</b></td><td class="r">${fmt(r.sQty, 0)}</td>
    <td class="r">${fmt(r.bQty ? r.bAmt / r.bQty : null, 2)} → ${fmt(r.sQty ? r.sAmt / r.sQty : null, 2)}</td><td class="r">${signed(r.realized - r.charges)}</td><td>${esc(r.last || "")}</td></tr>`).join("");
  const tx = txns.slice().reverse().map((x) => `<tr><td>${esc(x.date || "")}</td><td>${TX_LABEL[x.t] || x.t}</td><td>${x.sym ? "<b>" + esc(x.sym) + "</b> " + fmt(x.qty, 0) + " @ " + fmt(x.px, 2) : ""}</td>
    <td class="r">₹${fmt(x.sym ? x.qty * x.px : x.amt, 0)}</td><td class="r">${x.ch ? fmt(x.ch, 2) : ""}</td></tr>`).join("");
  const bad = P.open.filter((r) => nl.has(r.sym)).map((r) => r.sym);
  return `<div class="pf-sum">${k("Net gain", rs(P.gain, true), "gain " + (P.gain >= 0 ? "pos" : "neg"))}${k("Day", P.day == null ? "–" : signed(P.day))}${k("Net worth", rs(P.worth))}${k("Cash", rs(P.R.cash))}${k("Holdings", rs(P.hold))}${k("Money in", rs(P.deposits))}</div>
    ${bad.length ? `<p class="au-pfwarn">⚠ Not on NSE's list today: ${bad.map(esc).join(", ")} (delisted, suspended or a wrong symbol).</p>` : ""}
    ${h("Holdings", P.open.length, P.asOf ? "LTP " + esc(P.asOf) : "last close")}
    ${tbl('<th>Stock</th><th class="r">Qty</th><th class="r">Avg</th><th class="r">LTP</th><th class="r">Value</th><th class="r">P&amp;L</th><th class="r">Day</th>', rows, "No open holdings.", 7)}
    ${h("Closed", P.closed.length)}
    ${tbl('<th>Stock</th><th class="r">Qty</th><th class="r">Buy → Sell ₹</th><th class="r">Net ₹</th><th>Closed</th>', cl, "None.", 5)}
    ${h("Transactions", txns.length, "newest first")}
    ${tbl('<th>Date</th><th>Type</th><th>Stock</th><th class="r">Amount</th><th class="r">Charges</th>', tx, "None yet.", 5)}`;
}
function viewPortfolio(r, mkt) {
  const { $, syncLock } = X;
  let o = $(".au-pfv");
  if (!o) {
    o = document.createElement("div"); o.className = "au-back au-pfv";
    o.innerHTML = `<div class="au-box au-wide" role="dialog" aria-modal="true"><button class="au-x" type="button" aria-label="Close">×</button><h2 class="au-title"></h2><div class="au-pfbody"></div></div>`;
    document.body.appendChild(o);
    const close = () => { o.classList.remove("open"); $(".au-pfbody", o).innerHTML = ""; syncLock(); };
    $(".au-x", o).addEventListener("click", close);
    o.addEventListener("click", (e) => { if (e.target === o) close(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && o.classList.contains("open")) { e.stopImmediatePropagation(); close(); } }, true);
  }
  $(".au-title", o).textContent = (r.username || r.email || "User") + " · portfolio";
  const body = $(".au-pfbody", o);
  body.innerHTML = '<p class="au-sub">Loading…</p>';
  o.classList.add("open"); syncLock();
  requestAnimationFrame(() => { body.innerHTML = pfHtml(r.pfData, mkt); body.scrollTop = 0; });
}

// ---------- make / remove an admin: Firestore hni_admins/{uid} + RTDB hni/admins/{uid} ----------
async function setAdmin(back, uid) {
  const r = (back._rows || []).find((x) => x.uid === uid);
  if (!r) return;
  const make = !r.admin;
  if (!confirm((make ? "Make " : "Remove admin from ") + (r.username || r.email || uid) + (make ? " an admin? They will see every user and the online counter." : "?"))) return;
  try {
    if (make) await setDoc(doc(db, ADMINS, uid), { by: X.user.uid, at: serverTimestamp() });
    else await deleteDoc(doc(db, ADMINS, uid));
    if (rtdb) await (make ? R.set(R.ref(rtdb, "hni/admins/" + uid), true) : R.remove(R.ref(rtdb, "hni/admins/" + uid))).catch(() => {});
    r.admin = make;
    paint(back, X.$(".au-q", back).value);
  } catch (e) { alert("Couldn't change admin (" + (e.code || e) + "). Check the Firestore rules."); }
}

// ---------- "N online (total visitors)", bottom-left ----------
function onlineDot() {
  if (!rtdb || document.querySelector(".au-online")) return;
  const dot = document.createElement("div");
  dot.className = "au-online";
  dot.innerHTML = "<i></i><span>… online</span>";
  document.body.appendChild(dot);
  let online = 0, total = null;
  const show = () => { dot.querySelector("span").textContent = `${online} online` + (total != null ? ` (${total})` : ""); };
  R.onValue(R.ref(rtdb, "hni/presence"), (s) => { online = s.size; show(); });
  R.onValue(R.ref(rtdb, "hni/meta/visitors"), (s) => { const v = s.val(); total = typeof v === "number" ? v : null; show(); });
}
