// auth.js — HNImanshu login (username + password, or Google) on Firebase Authentication (project hnimanshu).
//
// One file for both pages:
//   • Landing (index.html) and Valuation (valuation.html): clicking an element with [data-auth-link] (Factor Screener, Portfolio) opens the
//     login popup when nobody is signed in; after a successful login it goes to that link.
//     Opening the screener with ?login=<path> pops the popup straight away (the Fundamentals gate sends people here).
//   • Fundamentals (<html data-auth="required">): the page stays hidden until a login is confirmed; no login →
//     back to the screener with the popup open. Shows the user's initials menu in the header.
//   • Passwords never pass through this code's storage: Firebase Authentication keeps them hashed on its servers.
//
// Usernames: Firebase needs an email, so username "chirag" is stored as chirag@users.hnimanshu.app (never shown,
// never mailed). Firebase keeps them unique, so a taken username is refused automatically.
// Sessions end 7 days after the last sign-in (Firebase's own lastSignInTime, so it can't be faked by the browser).

import { initializeApp } from "https://www.gstatic.com/firebasejs/10.13.0/firebase-app.js";
import {
  getAuth, onAuthStateChanged, signInWithEmailAndPassword, GoogleAuthProvider, signInWithPopup, signOut,
} from "https://www.gstatic.com/firebasejs/10.13.0/firebase-auth.js";
// Firestore and the Realtime Database are loaded only when first needed (not before the page shows):
// only Auth is needed to open a page, which keeps the first load light on slow (2G) connections.
let getFirestore, doc, setDoc, getDoc, serverTimestamp, collection, getDocs, deleteDoc;
let getDatabase, ref, set, onValue, onDisconnect, increment;

// Firebase project "hnimanshu", web app "valueapp" (Project settings → Your apps). These values are public by design;
// security comes from the Firestore / Realtime Database rules, not from hiding them.
const firebaseConfig = {
  apiKey: "AIzaSyD1oA953pXZxBAuOR9ObSk6vAmKf1gyVbE",
  authDomain: "hnimanshu-7c544.firebaseapp.com",
  projectId: "hnimanshu",
  storageBucket: "hnimanshu.firebasestorage.app",
  messagingSenderId: "428887828946",
  appId: "1:428887828946:web:854acb9c7651aebd38000b",
};
// Prime members and admins get the purple theme. Painted at once from last visit's answer (no charcoal flash),
// then confirmed below once the login is known.
const PRO_HINT = "hni_pro";
// signed-in pages: the page links sit in the Navigate overlay, so hide them at once (no flash before the login is known)
// (every page but the landing page, which has its own big links: there the Navigate button never shows)
if (document.documentElement.dataset.page !== "home") document.documentElement.classList.add("hni-nav");
// Tab icon (favicon): the "H" tile. It follows the BROWSER's own light / dark look (prefers-color-scheme inside the SVG:
// dark browser → light tile, light browser → dark tile) and turns purple for Prime members and admins.
function tabIcon(pro) {
  const [bg, h, bgD, hD] = pro ? ["#2b1250", "#e3cfff", "#dcc8fb", "#3a1670"]      // Prime: deep purple / light purple
                               : ["#141414", "#d4b47a", "#f1e8d8", "#4c2e0a"];     // everyone else: black + gold / cream + brown
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><style>rect{fill:${bg}}text{fill:${h}}@media (prefers-color-scheme:dark){rect{fill:${bgD}}text{fill:${hD}}}</style><rect width='32' height='32' rx='7'/><text x='16' y='23' font-family='Georgia,serif' font-size='18' font-weight='bold' text-anchor='middle'>H</text></svg>`;
  const href = "data:image/svg+xml," + encodeURIComponent(svg);
  let l = document.querySelector("link[rel~='icon']");
  if (!l) { l = document.createElement("link"); l.rel = "icon"; document.head.appendChild(l); }
  if (l.getAttribute("href") === href) return;
  l.type = "image/svg+xml"; l.href = href;
}
try {
  const h = localStorage.getItem(PRO_HINT);
  tabIcon(h === "1");                                                    // at once from the last visit; markPro() confirms it
  if (h === "1") document.documentElement.classList.add("hni-pro");
  else if (h === "0") document.documentElement.classList.add("hni-free");   // signed in, not Prime: purple PF Builder + Weekly
} catch (e) {}
// Realtime Database address: Firebase Hosting hands it out once the database exists, so nothing to paste here later.
try {
  const hosted = await fetch("/__/firebase/init.json").then((r) => r.json());
  if (hosted.projectId === firebaseConfig.projectId && hosted.databaseURL) firebaseConfig.databaseURL = hosted.databaseURL;
} catch (e) {}
const ADMIN_EMAIL = "admin@users.hnimanshu.app";       // same admin rule as the Users add-on: this account, or a hni_admins/{uid} document
const USER_DOMAIN = "users.hnimanshu.app";        // username → hidden email for Firebase
const SESSION_DAYS = 7;                           // log out a week after the last sign-in
const USERS = "hni_users";                        // Firestore: one doc per account (its own owner writes it)
const PORTFOLIO = "hni_portfolio";                // Firestore: one doc per user with their holdings
const MARKET = ["hni_market", "ltp"];             // latest prices for held stocks
// "⟳" live prices: a Cloudflare Worker (ltp_worker.js) starts the price job on GitHub. No secret here: the GitHub
// token lives in the Worker, which also checks the login and allows one run every few minutes.
const LTP_HOOK = "https://hni-ltp.himanshushekhar8.workers.dev/run";
// Two levels (₹ before GST). The pay Worker (pay_worker.js) has the SAME tables and is the one that counts: it makes the
// Razorpay order and, once paid, moves hni_plus/{uid} or hni_prime/{uid}.until forward by the months bought.
//   Plus  ₹499 a year: all lists (Smallcap 500, Microcap 250, Others)
//   Prime ₹249 / 199 / 149 / 99 a month for 1 / 3 / 6 / 12 months: Plus + Portfolio Builder, Checklist, Telegram alerts
const PRIME_PLANS = [{ m: 1, pm: 249 }, { m: 3, pm: 199 }, { m: 6, pm: 149 }, { m: 12, pm: 99 }];
const PLUS_PLANS = [{ m: 12, total: 499 }];
const TIERS = { plus: PLUS_PLANS, prime: PRIME_PLANS };
const PLUS_HINT = "hni_plus";                      // "1" = Plus, Prime or admin last time: all lists show at once
const GST = 0.18;
// Address of the pay Worker (Cloudflare, pay_worker.js). Empty = payments not switched on yet ("Proceed to pay" says payments
// open soon). Nothing here is secret: the Razorpay secret, the prices that are charged and the right to switch Prime on all
// live in the Worker; changing anything in this file (or in the browser) can't change what is charged or what is given.
const PAY_HOOK = "https://hni-pay.himanshushekhar8.workers.dev";
const HOME = "./";                                // the screener
const GATED = document.documentElement.dataset.auth === "required";
// Valuation: its own header and Login slot, but only for signed-in visitors (data-auth="login"). Signed out → the landing page.
const LOGIN_ONLY = document.documentElement.dataset.auth === "login";
const toLanding = () => location.replace("./?login=" + encodeURIComponent(location.pathname.replace(/^\//, "") + location.hash));
if (LOGIN_ONLY) { try { if (localStorage.getItem("hni_signed") !== "1") toLanding(); } catch (e) {} }   // never signed in here: go at once

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
let db = null, rtdb = null, FSP = null, RTP = null;
const needFS = () => FSP = FSP || import("https://www.gstatic.com/firebasejs/10.13.0/firebase-firestore.js").then((m) => {
  ({ getFirestore, doc, setDoc, getDoc, serverTimestamp, collection, getDocs, deleteDoc } = m); db = getFirestore(app);
});
const needRT = () => RTP = RTP || (firebaseConfig.databaseURL                                // live online / visitor counters (hni/...)
  ? import("https://www.gstatic.com/firebasejs/10.13.0/firebase-database.js").then((m) => {
      ({ getDatabase, ref, set, onValue, onDisconnect, increment } = m); rtdb = getDatabase(app); })
  : Promise.resolve());
const idle = (fn, ms = 2500) => setTimeout(() => (window.requestIdleCallback || ((f) => f()))(fn), ms);   // after the page has settled
const google = new GoogleAuthProvider();
google.setCustomParameters({ prompt: "select_account" });

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const toEmail = (u) => `${u.trim().toLowerCase()}@${USER_DOMAIN}`;
const nameOf = (u) => u.displayName || (u.email || "").replace("@" + USER_DOMAIN, "") || "user";
const initials = (n) => {                                    // "Himanshu Shekhar" → HS, "chirag" → CH
  const w = String(n || "?").trim().split(/[\s._-]+/).filter(Boolean);
  return (w.length > 1 ? w[0][0] + w[w.length - 1][0] : (w[0] || "?").slice(0, 2)).toUpperCase();
};
const providerOf = (u) => (u.providerData && u.providerData[0] && u.providerData[0].providerId) === "google.com" ? "google" : "username";
const expired = (u) => {
  const t = Date.parse(u.metadata && u.metadata.lastSignInTime);
  return t && Date.now() - t > SESSION_DAYS * 864e5;
};

// ---------- popup ----------
let box = null, nextUrl = null;
function buildPopup() {
  if (box) return box;
  box = document.createElement("div");
  box.className = "au-back";
  box.innerHTML = `
    <div class="au-box" role="dialog" aria-modal="true" aria-labelledby="auTitle">
      <button class="au-x" type="button" aria-label="Close">×</button>
      <h2 class="au-title" id="auTitle">HNImanshu</h2>
      <p class="au-sub">Free. Continue with Google, or log in with your username.</p>
      <button class="au-google" type="button">
        <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>
        Continue with Google</button>
      <div class="au-or">or</div>
      <form class="au-form" novalidate>
        <label class="au-fld"><span>Username</span>
          <input name="u" autocomplete="username" autocapitalize="none" spellcheck="false" maxlength="20" required></label>
        <label class="au-fld"><span>Password</span>
          <span class="au-pw"><input name="p" type="password" autocomplete="current-password" required>
          <button type="button" class="au-eye" aria-label="Show password">SHOW</button></span></label>
        <div class="au-err" aria-live="polite"></div>
        <button class="au-btn" type="submit">Login</button>
      </form>
      <p class="au-foot">You stay signed in for 7 days on this device.</p>
    </div>`;
  document.body.appendChild(box);
  const f = $("form", box), err = $(".au-err", box), btn = $(".au-btn", box);
  $(".au-eye", box).addEventListener("click", (e) => {
    const show = f.p.type === "password";
    f.p.type = show ? "text" : "password";
    e.currentTarget.textContent = show ? "HIDE" : "SHOW";
  });
  $(".au-x", box).addEventListener("click", closePopup);
  box.addEventListener("click", (e) => { if (e.target === box) closePopup(); });
  box.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Escape") closePopup(); });   // page shortcuts stay out

  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const u = f.u.value.trim().toLowerCase(), p = f.p.value;
    if (!/^[a-z0-9._]{3,20}$/.test(u) || !p) return (err.textContent = "Wrong username or password.");
    btn.disabled = true; err.textContent = "";
    try { await signInWithEmailAndPassword(auth, toEmail(u), p); done(); }
    catch (ex) { err.textContent = message(ex); }
    finally { btn.disabled = false; }
  });
  $(".au-google", box).addEventListener("click", async () => {
    err.textContent = "";
    try { await signInWithPopup(auth, google); done(); }
    catch (ex) { err.textContent = message(ex); }
  });
  box._reset = () => { err.textContent = ""; f.reset(); };
  return box;
}
function openPopup(next) {
  nextUrl = next || null;
  buildPopup();
  if (!GATED) box.classList.add("au-blur");                 // screener: the page behind is blurred
  box._reset();
  box.classList.add("open");
  setTimeout(() => $(".au-google", box).focus(), 30);
}
function closePopup() { if (box) box.classList.remove("open"); }
async function done() {
  closePopup();
  const go = nextUrl; nextUrl = null;
  if (!go) return;
  if (/(^|\/)builder(\.html)?(\?|#|$)/.test(go)) {                                  // PF Builder: only for Prime / admin, else the plans HERE
    for (let i = 0; i < 60 && !(current && window.hniBuilder); i++) await new Promise((r) => setTimeout(r, 100));
    const ok = window.hniBuilder ? await window.hniBuilder.catch(() => false) : true;
    if (ok) location.href = go; else openPlans(go);
    return;
  }
  location.href = go;
}
function message(ex) {
  const c = (ex && ex.code) || "";
  if (c === "auth/popup-closed-by-user" || c === "auth/cancelled-popup-request") return "";
  if (c === "auth/invalid-credential" || c === "auth/wrong-password" || c === "auth/user-not-found" || c === "auth/invalid-email")
    return "Wrong username or password.";
  if (c === "auth/email-already-in-use") return "That username is taken. Try another.";
  if (c === "auth/weak-password") return "Password is too weak. Use at least 8 characters.";
  if (c === "auth/too-many-requests") return "Too many attempts. Wait a few minutes and try again.";
  if (c === "auth/network-request-failed") return "No connection. Check your internet and try again.";
  if (c === "auth/popup-blocked") return "Your browser blocked the Google window. Allow pop-ups for this site and try again.";
  if (c === "auth/unauthorized-domain") return "This website isn't authorised for login yet (Firebase → Authentication → Settings → Authorized domains).";
  if (c === "auth/operation-not-allowed") return "This sign-in method isn't switched on yet (Firebase → Authentication → Sign-in method).";
  return "Something went wrong (" + (c || "unknown") + "). Please try again.";
}

// ---------- the account's own record in Firestore ----------
async function record(u) {
  const key = "hni_rec_" + u.uid;                       // write at most once per browser session, keeps Firestore use tiny
  try { if (sessionStorage.getItem(key)) return; } catch (e) {}
  await needFS();
  const ref = doc(db, USERS, u.uid);
  const data = { username: nameOf(u), email: u.email || null, provider: providerOf(u), lastLogin: serverTimestamp() };
  try {
    const snap = await getDoc(ref).catch(() => null);  // each user may read only their own doc (to keep "created" once)
    if (!snap || !snap.exists()) data.created = serverTimestamp();
    await setDoc(ref, data, { merge: true });
    try { sessionStorage.setItem(key, "1"); } catch (e) {}
  } catch (e) { console.warn("user record not saved:", e.code || e); }   // never blocks the page
}

// while any popup is open the page behind doesn't scroll (phones otherwise scroll the big page underneath)
const syncLock = () => document.documentElement.classList.toggle("au-lock", !!document.querySelector(".au-back.open"));

// ---------- signed-in chip (Fundamentals header, screener #au-slot) ----------
const EXTRA = [];                                          // extra menu items: {label, fn} (see addItem)
const itemBtn = (it, el) => {
  const b = document.createElement("button");
  b.type = "button"; b.className = "au-ext" + (it.cls ? " " + it.cls : ""); b.setAttribute("role", "menuitem"); b.textContent = it.label;
  b.addEventListener("click", () => { el.classList.remove("open"); it.fn(); });
  return b;
};
function addItem(label, fn, cls) {                         // also adds to a menu that is already on the page
  const it = { label, fn, cls };
  EXTRA.push(it);
  document.querySelectorAll(".au-user").forEach((el) => $(".au-menu", el).insertBefore(itemBtn(it, el), $(".au-out", el)));
}
function showUser(u, slot) {
  const host = slot || document.querySelector("header .global");        // Fundamentals header, or the screener's #au-slot
  if (!host || $(".au-user", host)) return;
  const n = nameOf(u);
  const el = document.createElement("span");
  el.className = "au-user";
  el.innerHTML = `<button type="button" class="au-av" aria-haspopup="menu" title="Signed in as ${esc(n)}"><span class="au-ini">${esc(initials(n))}</span><span class="au-nm">${esc(n)}</span></button>
    <span class="au-menu" role="menu"><span class="au-who"><b>${esc(n)}</b>${u.email && !u.email.endsWith("@" + USER_DOMAIN) ? `<i>${esc(u.email)}</i>` : ""}</span>
    <button type="button" class="au-out" role="menuitem">Logout</button></span>`;
  $(".au-av", el).addEventListener("click", (e) => { e.stopPropagation(); el.classList.toggle("open"); });
  document.addEventListener("click", (e) => { if (!el.contains(e.target)) el.classList.remove("open"); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") el.classList.remove("open"); });
  $(".au-out", el).addEventListener("click", async () => {
    try { localStorage.removeItem(PRO_HINT); } catch (e) {}
    await signOut(auth); if (GATED) location.href = HOME; else location.reload();   // screener: reloads signed out
  });
  EXTRA.forEach((it) => $(".au-menu", el).insertBefore(itemBtn(it, el), $(".au-out", el)));
  host.insertBefore(el, host.querySelector("#theme-toggle"));   // (no toggle in the slot: it simply goes at the end)
}
/* screener: "Login" when signed out, the initials menu when signed in */
function paintSlot(u) {
  const slot = document.getElementById("au-slot");
  if (!slot) return;
  slot.innerHTML = "";
  if (u) return showUser(u, slot);
  const b = document.createElement("button");
  b.type = "button"; b.className = "au-login"; b.setAttribute("aria-label", "Login");
  b.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg><span>Login</span>';
  b.addEventListener("click", () => openPopup(null));            // after logging in you stay on this page
  slot.appendChild(b);
}

// ---------- anonymous usage counters ----------
// Every open tab adds hni/presence/<random id>; Firebase deletes it by itself the moment the tab closes or the
// connection drops (onDisconnect). Each IP is counted once ever (SHA-256 hash, never the raw IP) into hni/meta/visitors.
async function presence() {
  await needRT();
  if (!rtdb) return;
  try {
    const sid = (crypto.randomUUID ? crypto.randomUUID() : Date.now() + "-" + Math.random().toString(36).slice(2)).replace(/[^a-z0-9-]/gi, "");
    const me = ref(rtdb, "hni/presence/" + sid);
    onValue(ref(rtdb, ".info/connected"), (s) => {
      if (s.val() !== true) return;
      onDisconnect(me).remove();                    // re-armed on every reconnect
      set(me, true).catch(() => {});
    });
  } catch (e) { console.warn("presence off:", e); }
}
async function countVisitor() {
  try {
    if (localStorage.getItem("hni_counted")) return;   // this browser already counted, skip the IP lookup
  } catch (e) {}
  await needRT();
  if (!rtdb) return;
  try {
    const r = await fetch("https://api.ipify.org?format=json");
    const { ip } = await r.json();
    if (!ip) return;
    const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode("hni:" + ip));
    const key = Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, "0")).join("");
    let isNew = true;
    await set(ref(rtdb, "hni/visitors/" + key), true).catch(() => { isNew = false; });   // rules refuse an IP seen before
    if (isNew) await set(ref(rtdb, "hni/meta/visitors"), increment(1));                    // +1 on the server, no read needed
    try { localStorage.setItem("hni_counted", "1"); } catch (e) {}
  } catch (e) { console.warn("visitor count skipped:", e); }
}

// ---------- add-on (packed; it decides for itself whether to do anything) ----------
const PACK = "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";
function unpack(t) {
  const b = Uint8Array.from(atob(t), (c) => c.charCodeAt(0));
  for (let i = 0; i < b.length; i++) b[i] ^= (i * 7 + 13) & 255;
  return new TextDecoder().decode(b);
}
async function loadExt(u) {
  if (!PACK) return;
  const url = URL.createObjectURL(new Blob([unpack(PACK)], { type: "text/javascript" }));
  try {
    const m = await import(url);
    await m.default({ app, auth, user: u, gated: GATED, addItem, syncLock, esc, $ });
  } catch (e) { console.warn("add-on:", e); }
  finally { URL.revokeObjectURL(url); }
}
const EMBED = /[?&]embed=1\b/.test(location.search);    // Fundamentals inside the Portfolio Builder: the builder already counts
if (!EMBED) {
  idle(presence);                                       // online counter: after the page has settled, never before it shows
  idle(countVisitor, 4000);
}

// ---------- Prime: purple theme, "Prime · till …" in the menu, the plans popup and payment ----------
let PRIME = null, PLUS = null, planBox = null, planSel = 0, planBusy = false, planTier = "prime";
let TRIAL_P = null, TRIAL_USED = null;                                 // free trial already used by this account? (hni_trials/{uid})
let PLAN_NEXT = null;                                                  // page to open once paid (PF Builder), else stay here
const isGoogle = (u) => !!(u && (u.providerData || []).some((x) => x.providerId === "google.com"));
let PRO_NOW = (() => { try { return localStorage.getItem(PRO_HINT) === "1"; } catch (e) { return false; } })();   // admin or Prime (hint until known)
// crown: one outline stroke, white (dark purple in day mode, see auth.css .au-crown)
const CROWN = '<svg class="au-crown" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true"><path d="M3 7.5l4.5 4.5L12 5l4.5 7L21 7.5 19 19H5z"/></svg>';
const inr = (n) => "₹" + Math.round(n).toLocaleString("en-IN");
const dateTxt = (ms) => new Date(ms).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
const total = (p) => p.total != null ? p.total : p.pm * p.m;          // ₹ for the whole period, before GST
const payable = (p) => Math.round(total(p) * (1 + GST));            // what Razorpay charges, GST included (the Worker works it out the same way)
function markPro(admin, p, plus) {
  PRIME = p;
  if (plus !== undefined) PLUS = plus;
  const on = !!(admin || p);
  PRO_NOW = on;
  document.documentElement.classList.toggle("hni-pro", on);           // auth.css: darkest purple (night) / light purple (day)
  tabIcon(on);                                                         // purple tab icon for Prime / admin
  document.documentElement.classList.toggle("hni-free", !on && !!current);   // free account: Prime buttons in purple
  try {
    if (current) { localStorage.setItem(PRO_HINT, on ? "1" : "0"); localStorage.setItem(PLUS_HINT, on || PLUS ? "1" : "0"); }
    else { localStorage.removeItem(PRO_HINT); localStorage.removeItem(PLUS_HINT); }
  } catch (e) {}
  markUser();
  watchEnd(admin ? null : p);                                          // trial / Prime ends while the page is open → popup
  try { if (current && !admin && p && p.until) localStorage.setItem(pendKey(), JSON.stringify({ until: p.until, trial: !!p.trial })); } catch (e) {}
  try { if (current && !admin && PLUS && PLUS.until) localStorage.setItem(pendKey() + "_plus", JSON.stringify({ until: PLUS.until })); } catch (e) {}
  if (current && !admin && !EXTRA.some((it) => it.cls === "au-get"))
    addItem(p ? "Extend Prime" : PLUS ? "Upgrade to Prime" : "Get Plus or Prime", () => openPlans(), "au-get");
}
function markUser() {                                                  // avatar ring + "Prime · till 18 Jan 2027" under the name
  document.querySelectorAll(".au-user").forEach((el) => {
    el.classList.toggle("au-prime", !!PRIME);
    const who = $(".au-who", el);
    let t = who && $(".au-ptag", who);
    if (!who) return;
    if (!PRIME && !PLUS) { if (t) t.remove(); return; }
    if (!t) { t = document.createElement("span"); t.className = "au-ptag"; who.appendChild(t); }
    t.textContent = !PRIME ? "Plus · " + (PLUS.until ? "valid till " + dateTxt(PLUS.until) : "no end date")
      : PRIME.trial ? "Free trial · till " + new Date(PRIME.until).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" })
      : "Prime · " + (PRIME.until ? "valid till " + dateTxt(PRIME.until) : "no end date");
  });
}
function buildPlans() {
  if (planBox) return planBox;
  planBox = document.createElement("div");
  planBox.className = "au-back au-plans";
  planBox.innerHTML = `
    <div class="au-box au-plan" role="dialog" aria-modal="true" aria-labelledby="auPlanT">
      <button class="au-x" type="button" aria-label="Close">×</button>
      <h2 class="au-title" id="auPlanT">Choose a plan</h2>
      <div class="au-tier" role="tablist"><button type="button" role="tab" data-tier="plus">Plus</button><button type="button" role="tab" data-tier="prime">${CROWN}Prime</button></div>
      <p class="au-plan-lead"><span></span></p>
      <div class="au-trial" hidden><div><b>Free trial</b><small></small></div><button type="button" class="au-trial-go">Start · ₹0</button></div>
      <div class="au-opts" role="radiogroup" aria-labelledby="auPlanT"></div>
      <p class="au-gst">Price excludes ${Math.round(GST * 100)}% GST</p>
      <button class="au-pay" type="button"></button>
      <p class="au-plan-msg" aria-live="polite"></p>
    </div>`;
  document.body.appendChild(planBox);
  const close = () => { if (planBusy) return; planBox.classList.remove("open"); syncLock(); };
  $(".au-x", planBox).addEventListener("click", close);
  planBox.addEventListener("click", (e) => { if (e.target === planBox) close(); });
  planBox.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Escape") close(); });
  planBox.addEventListener("change", (e) => { if (e.target.name === "auPlan") { planSel = +e.target.value; planMsg(""); paintPlans(); } });
  $(".au-pay", planBox).addEventListener("click", pay);
  $(".au-trial-go", planBox).addEventListener("click", startTrial);
  planBox.querySelectorAll(".au-tier button").forEach((b) => b.addEventListener("click", () => { if (planBusy) return; setTier(b.dataset.tier); planMsg(""); paintPlans(); }));
  return planBox;
}
// Plus / Prime toggle: the options under it are drawn for the level picked (12 months preselected: the best value)
function setTier(t) {
  planTier = t === "plus" ? "plus" : "prime";
  const L = TIERS[planTier];
  planSel = L.length - 1;
  $(".au-opts", planBox).innerHTML = L.map((p, k) => `
        <label class="au-opt"><input type="radio" name="auPlan" value="${k}">
          <span class="au-opt-m">${p.m} month${p.m > 1 ? "s" : ""}${p.m > 1 ? `<small>${inr(total(p))} for ${p.m} months${p.total != null ? " · " + inr(p.total / 12) + " a month" : ""}</small>` : ""}</span>
          <span class="au-opt-p">${p.total != null ? inr(p.total) + "/yr" : inr(p.pm) + "/mo"}</span></label>`).join("");
}
function paintPlans() {
  const L = TIERS[planTier], p = L[planSel], plus = planTier === "plus";
  planBox.querySelectorAll(".au-tier button").forEach((b) => { const on = b.dataset.tier === planTier; b.classList.toggle("on", on); b.setAttribute("aria-selected", on); });
  planBox.querySelectorAll(".au-opt").forEach((el, k) => {
    el.classList.toggle("on", k === planSel); $("input", el).checked = k === planSel; $("input", el).disabled = planBusy;
  });
  $(".au-plan-lead span", planBox).textContent = plus
    ? (PRIME || PRO_NOW ? "Prime already includes everything in Plus." : PLUS && PLUS.until ? `You are Plus till ${dateTxt(PLUS.until)}. A new year is added after that.`
       : "All lists: Smallcap 500, Microcap 250 and every other NSE stock (Others), on top of NIFTY 500.")
    : PRIME ? (PRIME.until ? `You are Prime till ${dateTxt(PRIME.until)}. New months are added after that.` : "You are Prime with no end date.")
    : "Portfolio Builder, Checklist and Telegram alerts, plus everything in Plus.";
  const tr = $(".au-trial", planBox), g = isGoogle(current);
  tr.hidden = plus || PRO_NOW || PRIME || TRIAL_USED !== false;       // Prime tab only, for an account that never had the trial
  $("small", tr).textContent = g ? "1 hour of Prime, once per Google account" : "Needs a Google sign-in (log in with Google)";
  $(".au-trial-go", tr).disabled = planBusy || !g;
  const b = $(".au-pay", planBox);
  const nope = plus && (PRIME || PRO_NOW);                              // already has it through Prime / admin
  b.disabled = planBusy || nope;
  if (!planBusy) b.textContent = nope ? "Included in your Prime" : `Proceed to pay ${inr(payable(p))}`;
}
function planMsg(t, kind) {
  const el = $(".au-plan-msg", planBox);
  el.textContent = t || ""; el.className = "au-plan-msg" + (kind ? " " + kind : "");
}
// next: the page to open after a successful payment / trial (e.g. "builder"). Not paid → the visitor stays on this page.
// tier: which tab the popup opens on ("plus" for the lists, else Prime)
function openPlans(next, tier) {
  PLAN_NEXT = typeof next === "string" ? next : null;
  if (!current) return openPopup(PLAN_NEXT);                           // signed out: log in first
  buildPlans(); setTier(tier === "plus" && !PRIME && !PRO_NOW ? "plus" : "prime"); planMsg(""); paintPlans();
  if (TRIAL_P && TRIAL_USED === null) TRIAL_P.then((t) => { TRIAL_USED = t.used; if (planBox) paintPlans(); });
  planBox.classList.add("open"); syncLock();
  setTimeout(() => { const i = planBox.querySelector("input:checked"); if (i) i.focus(); }, 30);
}
// the pay Worker, with the signed-in user's login token. Errors come back short: "Unable to … (Source: word)".
async function payCall(path, body) {
  const say = path === "/trial" ? "Unable to start the free trial" : "Unable to complete payment";
  let r;
  try {
    r = await fetch(PAY_HOOK.replace(/\/+$/, "") + path, { method: "POST",
      headers: { Authorization: "Bearer " + await current.getIdToken(), "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch (e) { return { error: `${say} (Network: offline)` }; }
  return r.json().catch(() => ({ error: `${say} (Server: ${r.status >= 500 ? "unavailable" : "error"})` }));
}
// Razorpay's own failure reasons → one word people understand
const RZP_WHY = { payment_cancelled: "cancelled", payment_failed: "declined", payment_timed_out: "timeout", authentication_failed: "authentication",
  insufficient_funds: "funds", card_declined: "declined", incorrect_otp: "OTP", payment_risk_check_failed: "risk" };
const rzpWord = (e) => { const k = String((e && (e.reason || e.code)) || "").toLowerCase(); return RZP_WHY[k] || k.split("_").pop() || "error"; };
let RZP = null;
const loadRazorpay = () => RZP = RZP || new Promise((ok, bad) => {
  const s = document.createElement("script");
  s.src = "https://checkout.razorpay.com/v1/checkout.js";
  s.onload = () => ok(); s.onerror = () => { RZP = null; bad(new Error("Couldn't open Razorpay. Check your connection and try again.")); };
  document.head.appendChild(s);
});
async function pay() {
  const p = TIERS[planTier][planSel], u = current, tier = planTier, name = tier === "plus" ? "Plus" : "Prime";
  if (planBusy || !u) return;
  if (!PAY_HOOK) return planMsg("Online payment opens soon. Please check back in a little while.");
  planBusy = true; paintPlans(); $(".au-pay", planBox).textContent = "Opening payment…"; planMsg("");
  try {
    const o = await payCall("/order", { tier, months: p.m });          // the Worker prices it from its own table, not from this page
    if (!o || o.error || !o.order_id) throw new Error((o && o.error) || "Couldn't start the payment.");
    await loadRazorpay().catch(() => { throw new Error("Unable to complete payment (Razorpay: unreachable)"); });
    const v = await new Promise((resolve, reject) => {
      const rz = new window.Razorpay({
        key: o.key, order_id: o.order_id, amount: o.amount, currency: o.currency || "INR",
        name: "HNImanshu", description: `${name} · ${p.m} month${p.m > 1 ? "s" : ""}`,
        prefill: { name: nameOf(u), email: u.email && !u.email.endsWith("@" + USER_DOMAIN) ? u.email : "" },
        theme: { color: "#5b2d9c" },
        handler: async (res) => {                                      // paid: the Worker checks Razorpay's signature, then extends Prime
          planMsg(`Payment received. Switching ${name} on…`);
          try {
            const r = await payCall("/verify", res);
            if (r && r.ok) resolve(r);
            else reject(Object.assign(new Error(r && r.error || "not confirmed"), { paid: true }));
          } catch (e) { reject(Object.assign(e, { paid: true })); }
        },
        modal: { ondismiss: () => reject(new Error("")) },              // closed without paying
      });
      rz.on("payment.failed", (r) => {                                  // the Razorpay window stays open so they can try again
        console.warn("Razorpay payment failed:", r && r.error);
        planMsg(`Unable to complete payment (Razorpay: ${rzpWord(r && r.error)})`, "bad");
      });
      rz.open();
    });
    planMsg((v.note ? v.note + " " : "") + `${name} is on, valid till ${dateTxt(v.until)}. ${PLAN_NEXT && tier === "prime" ? "Opening Portfolio Builder…" : "Reloading…"}`, "ok");
    try { localStorage.setItem(PRO_HINT, "1"); } catch (e) {}
    try { localStorage.setItem(PLUS_HINT, "1"); } catch (e) {}
    setTimeout(() => { if (PLAN_NEXT && tier === "prime") location.href = PLAN_NEXT; else location.reload(); }, 1600);
  } catch (e) {
    if (e.paid) planMsg(`Payment received, ${name} not confirmed yet (${e.message}). It switches on by itself within a few minutes; reload this page then.`, "bad");
    else if (e.message) planMsg(e.message, "bad");
  } finally { planBusy = false; paintPlans(); }
}

// ---------- free trial: 1 hour of Prime for ₹0, once per Google account (the pay Worker checks and blocks the email) ----------
async function startTrial() {
  if (planBusy || !current) return;
  if (!PAY_HOOK) return planMsg("The free trial opens soon. Please check back in a little while.");
  planBusy = true; paintPlans(); planMsg("Starting your free trial…");
  try {
    const r = await payCall("/trial", {});
    if (!r || !r.ok) { if (r && /already/.test(r.error || "")) TRIAL_USED = true; throw new Error((r && r.error) || "Couldn't start the trial."); }
    planMsg(`Free trial on till ${new Date(r.until).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" })}. ${PLAN_NEXT ? "Opening Portfolio Builder…" : "Reloading…"}`, "ok");
    try { localStorage.setItem(PRO_HINT, "1"); localStorage.setItem(PLUS_HINT, "1"); } catch (e) {}
    setTimeout(() => { if (PLAN_NEXT) location.href = PLAN_NEXT; else location.reload(); }, 1400);
  } catch (e) { planMsg(e.message || "Unable to start the free trial (Server: error)", "bad"); }
  finally { planBusy = false; if (planBox) paintPlans(); }
}
// trial / Prime runs out while a page is open: switch to free at once and say so (Firestore stops answering at that moment anyway)
let END_T = null, endBox = null;
function watchEnd(p) {
  clearTimeout(END_T); END_T = null;
  if (!p || !p.until) return;
  const left = p.until - Date.now();
  if (left < 2e9) END_T = setTimeout(primeEnded, Math.max(0, left) + 300);
}
document.addEventListener("visibilitychange", () => {               // laptop asleep past the end: check on waking up
  if (!document.hidden && PRIME && PRIME.until && PRIME.until <= Date.now()) primeEnded();
});
function primeEnded() {
  const p = PRIME;
  if (!p || !p.until || p.until > Date.now() + 1000) return;
  window.hniPrimeInfo = Promise.resolve(null); window.hniPrime = Promise.resolve(false); window.hniBuilder = Promise.resolve(false);
  window.hniPlus = Promise.resolve(!!PLUS); window.dispatchEvent(new Event("hni-tier"));   // pages re-check what is unlocked
  markPro(false, null, PLUS);
  TRIAL_USED = true;
  const onBuilder = document.documentElement.classList.contains("pb-admin");
  document.documentElement.classList.remove("pb-admin");              // PF Builder: its contents go at once
  endPopup(p.trial ? "trial" : "prime", onBuilder);
}
// "Free trial expired" / "Prime has ended": shown once, right when it ends, or on the next visit / login after it ended
const pendKey = () => current ? "hni_pend_" + current.uid : "";        // last Prime seen on this browser: {until, trial}
const seenKey = () => current ? "hni_tseen_" + current.uid : "";       // trial-ended popup already shown
function endPopup(kind, reload) {                                       // kind: "trial" | "prime" | "plus"
  const trial = kind === "trial";
  try { localStorage.removeItem(pendKey() + (kind === "plus" ? "_plus" : "")); if (trial) localStorage.setItem(seenKey(), "1"); } catch (e) {}
  if (!endBox) {
    endBox = document.createElement("div");
    endBox.className = "au-back au-plans au-ended";
    endBox.innerHTML = `<div class="au-box au-plan" role="alertdialog" aria-modal="true" aria-labelledby="auEndT">
      <button class="au-x" type="button" aria-label="Close">×</button>${CROWN}
      <h2 class="au-title" id="auEndT"></h2><p class="au-plan-lead"></p>
      <button class="au-pay" type="button">See plans</button></div>`;
    document.body.appendChild(endBox);
    const close = (plans) => {
      endBox.classList.remove("open"); syncLock();
      if (plans) openPlans(endBox._reload ? "builder" : null, endBox._kind === "plus" ? "plus" : null); else if (endBox._reload) location.reload();
    };
    $(".au-x", endBox).addEventListener("click", () => close(false));
    endBox.addEventListener("click", (e) => { if (e.target === endBox) close(false); });
    endBox.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Escape") close(false); });
    $(".au-pay", endBox).addEventListener("click", () => close(true));
  }
  endBox._reload = reload; endBox._kind = kind;
  $(".au-title", endBox).textContent = trial ? "Free trial expired" : kind === "plus" ? "Plus has ended" : "Prime has ended";
  $(".au-plan-lead", endBox).textContent = trial
    ? "Your 1-hour free trial is over. Choose a plan to keep Portfolio Builder and Telegram alerts."
    : kind === "plus" ? "Your Plus year has ended: back to NIFTY 500 only. Renew to keep Smallcap 500, Microcap 250 and Others."
    : "Your Prime plan has ended. Choose a plan to keep Portfolio Builder and Telegram alerts.";
  document.querySelectorAll(".au-back.open").forEach((b) => { if (b !== endBox) b.classList.remove("open"); });
  endBox.classList.add("open"); syncLock();
  setTimeout(() => $(".au-pay", endBox).focus(), 30);
}

// next visit / login after Prime or the trial ran out (while away): the same popup, once
async function endedSince() {
  let was = null; try { was = JSON.parse(localStorage.getItem(pendKey()) || "null"); } catch (e) {}
  if (was && was.until && was.until <= Date.now()) return setTimeout(() => endPopup(was.trial ? "trial" : "prime", false), 900);
  let wp = null; try { wp = JSON.parse(localStorage.getItem(pendKey() + "_plus") || "null"); } catch (e) {}
  if (wp && wp.until && wp.until <= Date.now() && !PLUS) return setTimeout(() => endPopup("plus", false), 900);
  const t = TRIAL_P && await TRIAL_P;                                   // trial taken on another device / browser
  let seen = true; try { seen = !!localStorage.getItem(seenKey()); } catch (e) {}
  if (t && t.used && t.until && t.until <= Date.now() && !seen) setTimeout(() => endPopup("trial", false), 900);
}

// ---------- phone: one "Navigate to" button (fork icon, drawn one line at a time) → every other page in one overlay ----------
const SVG = (inner, extra = "") => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${inner}</svg>`;
const FORK = SVG('<circle pathLength="1" cx="6" cy="5" r="2.4"/><path pathLength="1" d="M6 7.4v1.6a3 3 0 0 0 3 3h6a3 3 0 0 0 3-3V7.4"/><circle pathLength="1" cx="18" cy="5" r="2.4"/><path pathLength="1" d="M12 12v4.6"/><circle pathLength="1" cx="12" cy="19" r="2.4"/>', 'class="hni-fork"');
const NAV_ICON = {
  valuation: SVG('<path d="M4 6h16M4 12h16M4 18h10"/>'),
  factor: SVG('<path d="M3 20h18"/><path d="M6 16v-5M11 16V6M16 16v-8M21 16v-3"/>'),
  portfolio: SVG('<path d="M6 7h12l1 14H5L6 7z"/><path d="M9 7V5a3 3 0 0 1 6 0v2"/>'),
  builder: SVG('<path d="M10 10.5H4.8L3.5 21.5H10z" fill="currentColor" fill-opacity=".18"/><path d="M6.9 10.5V8.8a3 3 0 0 1 3.1-3"/><g transform="translate(1.2 -0.6) rotate(-42 16.5 11)"><path d="M12.6 3.4h8a1 1 0 0 1 1 1v2.8a1 1 0 0 1-1 1h-8l-1.9-2.4z" fill="currentColor" stroke-width="1"/><path d="M16.6 8.2v10.4" stroke-width="3"/></g>'),
  weekly: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="11" fill="#29a9eb"/><path d="M5.4 11.6l11.5-4.4c.5-.2 1 .1.8.9l-2 9.3c-.1.6-.5.8-1.1.5l-3-2.2-1.5 1.4c-.2.2-.3.3-.6.3l.2-3.1 5.6-5.1c.2-.2 0-.3-.4-.1l-6.9 4.4-3-.9c-.6-.2-.7-.6.1-1z" fill="#fff"/></svg>',
};
const NAV = [
  { k: "valuation", t: "Valuation", s: "Every stock valued and ranked", href: "valuation" },
  { k: "factor", t: "Factor Screener", s: "Industries, value and momentum screens", href: "factor#/" },
  { k: "portfolio", t: "Portfolio", s: "Your holdings and P&L", href: "factor#/portfolio" },
  { k: "builder", t: "Portfolio Builder", s: "ETFs + value + momentum, with a backtest", href: "builder", prime: true },
  { k: "weekly", t: "Telegram alerts", s: "Your portfolio summary every week", prime: true },
];
function pageNow() {                                                    // which page this is (left out of the list)
  const p = location.pathname.split("/").pop() || "";
  if (/^builder/.test(p)) return "builder";
  if (/^(factor|fundamentals)/.test(p)) return /^#\/portfolio/.test(location.hash) ? "portfolio" : "factor";
  if (/^valuation/.test(p)) return "valuation";
  return "home";
}
let navBox = null;
function mountNav() {                                                   // the button, just left of the avatar / Login button
  if (document.querySelector(".hni-navbtn") || document.documentElement.dataset.page === "home") return;   // the landing page has its own links
  const av = document.querySelector(".au-user") || document.querySelector(".au-slot .au-login");
  if (!av) return;
  const b = document.createElement("button");
  b.type = "button"; b.className = "hni-navbtn"; b.title = "Navigate to"; b.setAttribute("aria-label", "Navigate to");
  b.setAttribute("aria-haspopup", "dialog"); b.innerHTML = FORK + "<span>Navigate</span>";
  b.addEventListener("click", (e) => { e.stopPropagation(); openNav(); });
  av.parentNode.insertBefore(b, av);
  document.documentElement.classList.add("hni-nav");
}
function closeNav() { if (navBox) { navBox.classList.remove("open"); syncLock(); } }
function openNav() {
  if (!navBox) {
    navBox = document.createElement("div");
    navBox.className = "au-back hni-navsheet";
    navBox.innerHTML = `<div class="au-box" role="dialog" aria-modal="true" aria-labelledby="hniNavT">
      <button class="au-x" type="button" aria-label="Close">×</button>
      <h2 class="au-title" id="hniNavT">${FORK.replace('class="hni-fork"', "")}Navigate to</h2><div class="hni-nvl"></div></div>`;
    document.body.appendChild(navBox);
    navBox.addEventListener("click", (e) => {                           // outside the box or ×: close
      if (e.target === navBox || e.target.closest(".au-x")) return closeNav();
      const it = e.target.closest(".hni-nv"); if (!it) return;
      const n = NAV.find((x) => x.k === it.dataset.k);
      if (!current && !n.open) {                                        // not logged in: log in first, then that page
        e.preventDefault(); closeNav(); setTimeout(() => openPopup(n.href || "factor#/"), 160); return;
      }
      if (n.prime && !PRO_NOW) {                                        // Prime page, free account: close this first, then the plans
        e.preventDefault(); closeNav(); setTimeout(() => openPlans(n.k === "builder" ? n.href : null), 160); return;
      }
      if (n.k === "weekly") {
        e.preventDefault(); closeNav();
        setTimeout(() => { const w = document.getElementById("weeklyBtn"); if (w) w.click(); else weeklyNote(); }, 160);
        return;
      }
      closeNav();                                                       // a link: the browser follows it (same tab)
    });
    navBox.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Escape") closeNav(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && navBox.classList.contains("open")) closeNav(); });
    window.addEventListener("hashchange", closeNav);
  }
  const here = pageNow();
  $(".hni-nvl", navBox).innerHTML = NAV.filter((n) => n.k !== here).map((n) => {
    const lock = n.prime && !PRO_NOW;                                    // purple + crown for anyone without Prime, logged out too
    const tag = n.href ? `a href="${n.href}"` : 'button type="button"';
    // purple tile + crown only for a locked (free account) item; Prime members see it like every other page
    return `<${tag} class="hni-nv${lock ? " pr lock" : ""}" data-k="${n.k}">
      <span class="ic">${NAV_ICON[n.k]}</span><span class="tx"><b>${esc(n.t)}</b><small>${esc(lock ? (current ? "Part of Prime · see plans" : "Part of Prime · log in first") : n.s)}</small></span>
      ${lock ? CROWN.replace('class="au-crown"', 'class="cr"') : '<span class="go">›</span>'}</${n.href ? "a" : "button"}>`;
  }).join("");
  navBox.classList.add("open"); syncLock();
  setTimeout(() => { const f = navBox.querySelector(".hni-nv"); if (f) f.focus(); }, 30);
}
function weeklyNote() {                                                 // Telegram, on a page without its own Weekly button
  let m = document.querySelector(".au-week");
  if (!m) {
    m = document.createElement("div"); m.className = "au-back au-week";
    m.innerHTML = '<div class="au-box" role="dialog" aria-modal="true"><button class="au-x" type="button" aria-label="Close">×</button><h2 class="au-title">Weekly updates</h2><p class="au-sub">Work in progress. Soon you\'ll get your portfolio summary every week on Telegram.</p></div>';
    document.body.appendChild(m);
    const close = () => { m.classList.remove("open"); syncLock(); };
    m.addEventListener("click", (e) => { if (e.target === m || e.target.closest(".au-x")) close(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  }
  m.classList.add("open"); syncLock();
  fadeAway(m, () => { m.classList.remove("open"); syncLock(); });
}
function fadeAway(el, done, ms = 5000) {                                // an info popup closes itself: fade, then close
  clearTimeout(el._fade); el.style.transition = ""; el.style.opacity = "";
  el._fade = setTimeout(() => { el.style.transition = "opacity .6s"; el.style.opacity = "0";
    el._fade = setTimeout(() => { done(); el.style.transition = ""; el.style.opacity = ""; }, 600); }, ms);
}
window.hniFadeAway = fadeAway;

// ---------- page wiring ----------
const SIGNED_HINT = "hni_signed";                          // "1" = signed in last time (only a hint: Fundamentals checks for real)
const hinted = () => { try { return localStorage.getItem(SIGNED_HINT) === "1"; } catch (e) { return false; } };
let current = null;
const ready = new Promise((resolve) => {
  onAuthStateChanged(auth, async (u) => {
    if (u && expired(u)) { await signOut(auth); u = null; }   // 7-day session limit
    current = u;
    try { localStorage.setItem(SIGNED_HINT, u ? "1" : "0"); } catch (e) {}   // lets the next visit open Fundamentals instantly
    resolve();
    if ((GATED || LOGIN_ONLY) && !u) { toLanding(); return; }    // no login: the landing page, login box open, then back here
    if (GATED || LOGIN_ONLY) document.documentElement.classList.add("auth-ok");   // show the page at once
    else paintSlot(u);                                         // screener: Login button or the user menu
    if (!u) { markPro(false, null); if (GATED) document.documentElement.classList.remove("hni-nav"); else mountNav(); return; }
    // Portfolio tab: the page saves / loads the user's holdings through this (Firestore hni_portfolio/{uid}, owner only)
    window.hniStore = {
      uid: u.uid,                                            // PF Builder remembers its last tab per user
      load: async () => { await needFS(); const d = await getDoc(doc(db, PORTFOLIO, u.uid)); return d.exists() ? d.data() : {}; },             // {txns, rates} (or old {items})
      save: async (data) => { await needFS(); return setDoc(doc(db, PORTFOLIO, u.uid), { txns: data.txns || [], rates: data.rates || {}, updated: serverTimestamp() }); },
      market: async () => { await needFS(); const d = await getDoc(doc(db, ...MARKET)); return d.exists() ? d.data() : null; },
      canRefresh: () => !!LTP_HOOK,                          // every signed-in user
      // start the live-price job (Cloudflare Worker → GitHub ltp.yml) → {ok} | {error, wait} | {error, running}
      // (an Oracle-server version of this lives in oracle/: for later, on a server located in India)
      refresh: async () => {
        const r = await fetch(LTP_HOOK, { method: "POST", headers: { Authorization: "Bearer " + await u.getIdToken() } });
        return r.json().catch(() => ({ error: "HTTP " + r.status }));
      },
    };
    // PF Builder (admins and prime members, checked again by the Worker): download day-by-day prices for these stocks → {ok} | {error, running}
    window.hniStore.builderPrices = async (symbols) => {
      const r = await fetch(LTP_HOOK.replace(/\/run$/, "/builder"), { method: "POST",
        headers: { Authorization: "Bearer " + await u.getIdToken(), "Content-Type": "application/json" }, body: JSON.stringify({ symbols }) });
      return r.json().catch(() => ({ error: "HTTP " + r.status }));
    };
    // true / false: is this account an admin? (Portfolio builder button on Fundamentals + the builder page itself)
    window.hniAdmin = (async () => {
      if (u.email === ADMIN_EMAIL) return true;
      try { await needFS(); return (await getDoc(doc(db, "hni_admins", u.uid))).exists(); } catch (e) { return false; }
    })();
    // Prime member? hni_prime/{uid} = {until, months, by, at}. Written by an admin (Users list → Make prime) or by the pay Worker
    // after a payment. Past `until` it is over by itself: the Firestore rules stop a member reading an ended document, so the
    // Worker refuses too. No `until` = no end date (older documents). → {until: ms | null} or null
    window.hniPrimeInfo = (async () => {
      try {
        await needFS();
        const d = await getDoc(doc(db, "hni_prime", u.uid));
        if (!d.exists()) return null;
        const t = d.data().until, until = t && t.toMillis ? t.toMillis() : null;
        return until && until <= Date.now() ? null : { until, trial: d.data().src === "trial" };
      } catch (e) { return null; }                     // ended (rules refuse) or offline
    })();
    window.hniPrime = window.hniPrimeInfo.then((p) => !!p);
    // Plus member? hni_plus/{uid} = {until, …}, same rules as Prime. → {until} or null
    window.hniPlusInfo = (async () => {
      try {
        await needFS();
        const d = await getDoc(doc(db, "hni_plus", u.uid));
        if (!d.exists()) return null;
        const t = d.data().until, until = t && t.toMillis ? t.toMillis() : null;
        return until && until <= Date.now() ? null : { until };
      } catch (e) { return null; }
    })();
    TRIAL_USED = null;                                                // free trial taken before? (only the owner can read it)
    TRIAL_P = (async () => {                                         // → {used, until}
      try { await needFS(); const d = await getDoc(doc(db, "hni_trials", u.uid));
        const t = d.exists() && d.data().until; return { used: d.exists(), until: t && t.toMillis ? t.toMillis() : null }; }
      catch (e) { return { used: true, until: null }; }
    })();
    // PF Builder (button on Fundamentals + the page itself): admins and prime members. Everyone else sees the plans.
    window.hniBuilder = (async () => (await window.hniAdmin) || (await window.hniPrime))();
    // all lists (Smallcap 500, Microcap 250, Others): Plus, Prime or admin
    window.hniPlus = (async () => (await window.hniBuilder) || !!(await window.hniPlusInfo))();
    window.hniPlans = openPlans;
    window.dispatchEvent(new Event("hni-ready"));
    if (GATED) showUser(u);
    mountNav();
    Promise.all([window.hniAdmin, window.hniPrimeInfo, window.hniPlusInfo]).then(([admin, p, plus]) => { markPro(!!admin, p, plus); if (!admin && !p) endedSince(); });
    idle(() => { record(u); loadExt(u); }, 1500);
  });
});

if (!GATED) {
  // screener: the Fundamentals button. Its tap is caught by a small script in the page itself (so the FIRST tap works
  // even before this file has loaded): it disables the button, shows "Checking login…" and then calls this.
  window.hniAuthGo = async (href) => {
    window.hniPending = null;
    if (/(^|\/)builder(\.html)?(\?|#|$)/.test(href)) {                          // PF Builder: Prime / admin go; others get the plans here
      await ready;
      if (window.hniDone) window.hniDone();
      if (!current) return openPopup(href);
      for (let i = 0; i < 60 && !window.hniBuilder; i++) await new Promise((r) => setTimeout(r, 100));
      if (window.hniBuilder && !(await window.hniBuilder.catch(() => false))) return openPlans("builder");
      location.href = href; return;
    }
    // signed in last time → go at once (the Fundamentals page confirms the login itself, and sends you back if needed)
    if (current || hinted()) { location.href = href; return; }
    await ready;
    if (current) { location.href = href; return; }
    if (window.hniDone) window.hniDone();                      // not signed in: "Checking login…" → the login box
    openPopup(href);
  };
  if (window.hniPending) window.hniAuthGo(window.hniPending);   // tapped while this file was still loading
  // signed-in visitors: fetch the Fundamentals page quietly in the background, so it opens instantly (not for everyone:
  // it is ~260 KB, and free hosting has a daily transfer limit)
  ready.then(() => {
    const c = navigator.connection;
    if (!current || (c && (c.saveData || /2g/.test(c.effectiveType || "")))) return;
    const a = document.querySelector("[data-auth-link]");
    if (!a) return;
    idle(() => { const l = document.createElement("link"); l.rel = "prefetch"; l.href = a.href; document.head.appendChild(l); }, 3000);
  });
  const want = new URLSearchParams(location.search).get("login");
  if (want != null) {
    history.replaceState(null, "", location.pathname + location.hash);          // clean the address bar
    ready.then(() => {
      let target = "factor";                                                    // only pages on this site
      try { const t = new URL(want, location.href); if (want && t.origin === location.origin) target = t.pathname + t.hash; } catch (e) {}
      if (current) location.href = target; else openPopup(target);
    });
  }
}
