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
let getFirestore, doc, setDoc, getDoc, serverTimestamp, collection, getDocs, deleteDoc, onSnapshot;
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
  else if (h === "0") {
    document.documentElement.classList.add("hni-free");                 // signed in, not Prime: purple PF Builder + Weekly
    if (localStorage.getItem("hni_plus") === "1") document.documentElement.classList.add("hni-plus");   // Plus: teal / mint page
  }
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
  ({ getFirestore, doc, setDoc, getDoc, serverTimestamp, collection, getDocs, deleteDoc, onSnapshot } = m); db = getFirestore(app);
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
    await clearUserCache();
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
  document.documentElement.classList.toggle("hni-plus", !on && !!current && !!PLUS);   // Plus: Deep Teal (night) / Soft Mint (day)
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
    el.classList.toggle("au-plusm", !PRIME && !!PLUS);
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
// Plus and Prime windows the same size: both are drawn once, the taller height is kept, so the toggle never moves
function fitPlans() {
  if (!planBox || !planBox.classList.contains("open")) return;
  const b = $(".au-plan", planBox), keep = planTier, sel = planSel;
  b.style.minHeight = "";
  let h = 0;
  for (const t of ["plus", "prime"]) { setTier(t); paintPlans(); h = Math.max(h, b.offsetHeight); }
  setTier(keep); planSel = sel; paintPlans();
  b.style.minHeight = h + "px";
}
addEventListener("resize", () => { clearTimeout(fitPlans.t); fitPlans.t = setTimeout(fitPlans, 150); });
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
  if (TRIAL_P && TRIAL_USED === null) TRIAL_P.then((t) => { TRIAL_USED = t.used; if (planBox) { paintPlans(); fitPlans(); } });
  planBox.classList.add("open"); syncLock(); fitPlans();
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
  { k: "valuation", t: "Valuation", s: "Model values from company financials", href: "valuation" },
  { k: "factor", t: "Factor Screener", s: "Filter stocks with your own limits", href: "factor#/" },
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
  document.querySelectorAll(".au-user.open").forEach((el) => el.classList.remove("open"));   // the user menu closes when Navigate opens
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

// PF Builder keeps its finished lists in this browser (IndexedDB "hni-pb") until the user logs out
const clearUserCache = () => new Promise((done) => {
  try { const r = indexedDB.deleteDatabase("hni-pb"); r.onsuccess = r.onerror = r.onblocked = () => done(); setTimeout(done, 800); }
  catch (e) { done(); }
});
window.hniClearCache = clearUserCache;

// ---------- page wiring ----------
const SIGNED_HINT = "hni_signed";                          // "1" = signed in last time (only a hint: Fundamentals checks for real)
const hinted = () => { try { return localStorage.getItem(SIGNED_HINT) === "1"; } catch (e) { return false; } };
// Login gate: once a login is checked, the gated pages open at once for the next hour (an inline line in each page's
// <head> adds html.auth-ok before anything draws). Every page still confirms the login quietly in the background.
// Used again after more than an hour away (or a tab left open that long): the gate shows "Checking login…" and asks
// Firebase's server again (a fresh token), so a deleted / disabled account is shut out.
const AUTH_AT = "hni_auth_at", TRUST_MS = 60 * 60 * 1000;
// Demo (review) logins: "Portfolio" is called "Paper portfolio" everywhere they look (no real money). Empty the list to switch off.
const DEMO = ["razorpay@users.hnimanshu.app", "demo@users.hnimanshu.app"];
function markDemo(u) {
  const on = !!(u && DEMO.includes((u.email || "").toLowerCase()));
  window.hniDemo = on;
  try { localStorage.setItem("hni_demo", on ? "1" : "0"); } catch (e) {}   // pages show the demo view at once next time
  document.documentElement.classList.toggle("hni-demo", on);
  if (!on) return;
  const pf = NAV.find((x) => x.k === "portfolio"); if (pf) { pf.t = "Paper portfolio"; pf.s = "Practice holdings, no real money"; }
  document.querySelectorAll("[data-pf-name]").forEach((el) => { el.textContent = "Paper portfolio"; });
  document.querySelectorAll("[data-pf-sub]").forEach((el) => { el.textContent = "Practice holdings, no real money"; });
  document.querySelectorAll("#pfBag").forEach((el) => { el.title = "Paper portfolio"; el.setAttribute("aria-label", "Paper portfolio"); });
}
const trusted = () => { try { return hinted() && Date.now() - (+localStorage.getItem(AUTH_AT) || 0) < TRUST_MS; } catch (e) { return false; } };
const stampAuth = (on) => { try { on ? localStorage.setItem(AUTH_AT, String(Date.now())) : localStorage.removeItem(AUTH_AT); } catch (e) {} };
let current = null;
const ready = new Promise((resolve) => {
  onAuthStateChanged(auth, async (u) => {
    if (u && expired(u)) { await clearUserCache(); await signOut(auth); u = null; }   // 7-day session limit
    current = u;
    try { localStorage.setItem(SIGNED_HINT, u ? "1" : "0"); } catch (e) {}   // lets the next visit open Fundamentals instantly
    if (u && (GATED || LOGIN_ONLY) && !trusted()) {             // more than an hour since the last check: ask the server, not the cache
      try { await u.getIdToken(true); }
      catch (e) { if (!/network|offline/i.test(String(e && e.code || e))) { await signOut(auth); u = null; current = null; } }
    }
    stampAuth(!!u);
    resolve();
    if ((GATED || LOGIN_ONLY) && !u) { toLanding(); return; }    // no login: the landing page, login box open, then back here
    if (GATED || LOGIN_ONLY) document.documentElement.classList.add("auth-ok");   // show the page at once
    if (!GATED) paintSlot(u);                                  // Valuation (login-only) too: the user menu in its #au-slot
    markDemo(u);
    if (!u) { markPro(false, null); if (GATED) document.documentElement.classList.remove("hni-nav"); else mountNav(); return; }
    // Portfolio tab: the page saves / loads the user's holdings through this (Firestore hni_portfolio/{uid}, owner only)
    window.hniStore = {
      uid: u.uid,                                            // PF Builder remembers its last tab per user
      email: u.email || "",                                  // PF Builder: the review account sees its paper portfolio instead
      load: async () => { await needFS(); const d = await getDoc(doc(db, PORTFOLIO, u.uid)); return d.exists() ? d.data() : {}; },             // {txns, rates} (or old {items})
      save: async (data) => { await needFS(); return setDoc(doc(db, PORTFOLIO, u.uid), { txns: data.txns || [], rates: data.rates || {}, updated: serverTimestamp() }); },
      market: async () => { await needFS(); const d = await getDoc(doc(db, ...MARKET)); return d.exists() ? d.data() : null; },
      // live: cb(prices) every time new prices are written (Firestore pushes them, no polling) → returns "stop"
      watchMarket: async (cb) => { await needFS(); return onSnapshot(doc(db, ...MARKET), (d) => cb(d.exists() ? d.data() : null), () => {}); },
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

// a gated tab left open: using it keeps the hour going; coming back after more than an hour away checks the login again
if (GATED || LOGIN_ONLY) {
  let last = 0;
  const touch = () => { if (current && Date.now() - last > 5 * 60000 && trusted()) { last = Date.now(); stampAuth(true); } };
  ["pointerdown", "keydown", "scroll"].forEach((ev) => addEventListener(ev, touch, { passive: true, capture: true }));
  document.addEventListener("visibilitychange", async () => {
    if (document.hidden || !current || trusted()) return;
    const el = document.documentElement; el.classList.remove("auth-ok");   // gate shows "Checking login…"
    try { await current.getIdToken(true); stampAuth(true); el.classList.add("auth-ok"); }
    catch (e) {
      if (/network|offline/i.test(String(e && e.code || e))) { el.classList.add("auth-ok"); return; }   // offline: not a reason to lock out
      stampAuth(false); await signOut(auth).catch(() => {}); toLanding();
    }
  });
}

if (!GATED) {
  // screener: the Fundamentals button. Its tap is caught by a small script in the page itself (so the FIRST tap works
  // even before this file has loaded): it disables the button, shows "Checking login…" and then calls this.
  window.hniAuthGo = async (href) => {
    window.hniPending = null;
    if (/(^|\/)builder(\.html)?(\?|#|$)/.test(href)) {                          // PF Builder: Prime / admin go; others get the plans here
      // checked within the hour and Prime / admin last time → straight in (the builder page confirms it again quietly)
      try { if (trusted() && localStorage.getItem(PRO_HINT) === "1") { location.href = href; return; } } catch (e) {}
      await ready;
      if (window.hniDone) window.hniDone();
      if (!current) return openPopup(href);
      for (let i = 0; i < 60 && !window.hniBuilder; i++) await new Promise((r) => setTimeout(r, 100));
      if (window.hniBuilder && !(await window.hniBuilder.catch(() => false))) return openPlans("builder");
      location.href = href; return;
    }
    // signed in last time → go at once (the Fundamentals page confirms the login itself, and sends you back if needed)
    if (current || trusted() || hinted()) { location.href = href; return; }
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
