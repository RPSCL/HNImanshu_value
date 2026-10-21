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
// Prime plans (₹ per month, before GST). The pay Worker (pay_worker.js) has the SAME table and is the one that counts:
// it makes the Razorpay order and, once paid, moves hni_prime/{uid}.until forward by the months bought.
const PLANS = [{ m: 1, pm: 999 }, { m: 3, pm: 899 }, { m: 6, pm: 799 }, { m: 12, pm: 599 }];
const GST = 0.18;
// Address of the pay Worker (Cloudflare, pay_worker.js). Empty = payments not switched on yet ("Proceed to pay" says payments
// open soon). Nothing here is secret: the Razorpay secret, the prices that are charged and the right to switch Prime on all
// live in the Worker; changing anything in this file (or in the browser) can't change what is charged or what is given.
const PAY_HOOK = "https://hni-pay.himanshushekhar8.workers.dev";
const HOME = "./";                                // the screener
const GATED = document.documentElement.dataset.auth === "required";

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
function done() {
  closePopup();
  if (nextUrl) location.href = nextUrl;
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
  el.innerHTML = `<button type="button" class="au-av" aria-haspopup="menu" title="Signed in as ${esc(n)}">${esc(initials(n))}</button>
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
let PRIME = null, planBox = null, planSel = 0, planBusy = false;
let TRIAL_P = null, TRIAL_USED = null;                                 // free trial already used by this account? (hni_trials/{uid})
const isGoogle = (u) => !!(u && (u.providerData || []).some((x) => x.providerId === "google.com"));
let PRO_NOW = (() => { try { return localStorage.getItem(PRO_HINT) === "1"; } catch (e) { return false; } })();   // admin or Prime (hint until known)
// crown: one outline stroke, white (dark purple in day mode, see auth.css .au-crown)
const CROWN = '<svg class="au-crown" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true"><path d="M3 7.5l4.5 4.5L12 5l4.5 7L21 7.5 19 19H5z"/></svg>';
const inr = (n) => "₹" + Math.round(n).toLocaleString("en-IN");
const dateTxt = (ms) => new Date(ms).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
const payable = (p) => Math.round(p.pm * p.m * (1 + GST));          // what Razorpay charges, GST included (the Worker works it out the same way)
function markPro(admin, p) {
  PRIME = p;
  const on = !!(admin || p);
  PRO_NOW = on;
  document.documentElement.classList.toggle("hni-pro", on);           // auth.css: darkest purple (night) / light purple (day)
  tabIcon(on);                                                         // purple tab icon for Prime / admin
  document.documentElement.classList.toggle("hni-free", !on && !!current);   // free account: Prime buttons in purple
  try { if (current) localStorage.setItem(PRO_HINT, on ? "1" : "0"); else localStorage.removeItem(PRO_HINT); } catch (e) {}
  markUser();
  watchEnd(admin ? null : p);                                          // trial / Prime ends while the page is open → popup
  if (current && !admin && !EXTRA.some((it) => it.cls === "au-get"))
    addItem(p ? "Extend Prime" : "Get Prime", openPlans, "au-get");
}
function markUser() {                                                  // avatar ring + "Prime · till 18 Jan 2027" under the name
  document.querySelectorAll(".au-user").forEach((el) => {
    el.classList.toggle("au-prime", !!PRIME);
    const who = $(".au-who", el);
    let t = who && $(".au-ptag", who);
    if (!who) return;
    if (!PRIME) { if (t) t.remove(); return; }
    if (!t) { t = document.createElement("span"); t.className = "au-ptag"; who.appendChild(t); }
    t.textContent = PRIME.trial ? "Free trial · till " + new Date(PRIME.until).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" })
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
      <p class="au-plan-lead">${CROWN}<span></span></p>
      <div class="au-trial" hidden><div><b>Free trial</b><small></small></div><button type="button" class="au-trial-go">Start · ₹0</button></div>
      <div class="au-opts" role="radiogroup" aria-labelledby="auPlanT">${PLANS.map((p, k) => `
        <label class="au-opt"><input type="radio" name="auPlan" value="${k}">
          <span class="au-opt-m">${p.m} month${p.m > 1 ? "s" : ""}${p.m > 1 ? `<small>${inr(p.pm * p.m)} for ${p.m} months</small>` : ""}</span>
          <span class="au-opt-p">${inr(p.pm)}/mo</span></label>`).join("")}
      </div>
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
  return planBox;
}
function paintPlans() {
  const p = PLANS[planSel];
  planBox.querySelectorAll(".au-opt").forEach((el, k) => {
    el.classList.toggle("on", k === planSel); $("input", el).checked = k === planSel; $("input", el).disabled = planBusy;
  });
  $(".au-plan-lead span", planBox).textContent = PRIME
    ? (PRIME.until ? `You are Prime till ${dateTxt(PRIME.until)}. New months are added after that.` : "You are Prime with no end date.")
    : "Telegram alerts, and Portfolio Builder are a part of Prime.";
  const tr = $(".au-trial", planBox), g = isGoogle(current);
  tr.hidden = PRO_NOW || PRIME || TRIAL_USED !== false;               // only for a free account that never had the trial
  $("small", tr).textContent = g ? "1 hour of Prime, once per Google account" : "Needs a Google sign-in (log in with Google)";
  $(".au-trial-go", tr).disabled = planBusy || !g;
  const b = $(".au-pay", planBox);
  b.disabled = planBusy;
  if (!planBusy) b.textContent = `Proceed to pay ${inr(payable(p))}`;
}
function planMsg(t, kind) {
  const el = $(".au-plan-msg", planBox);
  el.textContent = t || ""; el.className = "au-plan-msg" + (kind ? " " + kind : "");
}
function openPlans() {
  if (!current) return openPopup(null);                                // signed out: log in first
  buildPlans(); planMsg(""); paintPlans();
  if (TRIAL_P && TRIAL_USED === null) TRIAL_P.then((used) => { TRIAL_USED = used; if (planBox) paintPlans(); });
  planBox.classList.add("open"); syncLock();
  setTimeout(() => { const i = planBox.querySelector("input:checked"); if (i) i.focus(); }, 30);
}
async function payCall(path, body) {                                   // the pay Worker, with the signed-in user's login token
  const r = await fetch(PAY_HOOK.replace(/\/+$/, "") + path, { method: "POST",
    headers: { Authorization: "Bearer " + await current.getIdToken(), "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return r.json().catch(() => ({ error: "HTTP " + r.status }));
}
let RZP = null;
const loadRazorpay = () => RZP = RZP || new Promise((ok, bad) => {
  const s = document.createElement("script");
  s.src = "https://checkout.razorpay.com/v1/checkout.js";
  s.onload = () => ok(); s.onerror = () => { RZP = null; bad(new Error("Couldn't open Razorpay. Check your connection and try again.")); };
  document.head.appendChild(s);
});
async function pay() {
  const p = PLANS[planSel], u = current;
  if (planBusy || !u) return;
  if (!PAY_HOOK) return planMsg("Online payment opens soon. Please check back in a little while.");
  planBusy = true; paintPlans(); $(".au-pay", planBox).textContent = "Opening payment…"; planMsg("");
  try {
    const o = await payCall("/order", { months: p.m });                // the Worker prices it from its own table, not from this page
    if (!o || o.error || !o.order_id) throw new Error((o && o.error) || "Couldn't start the payment.");
    await loadRazorpay();
    const v = await new Promise((resolve, reject) => {
      const rz = new window.Razorpay({
        key: o.key, order_id: o.order_id, amount: o.amount, currency: o.currency || "INR",
        name: "HNImanshu", description: `Prime · ${p.m} month${p.m > 1 ? "s" : ""}`,
        prefill: { name: nameOf(u), email: u.email && !u.email.endsWith("@" + USER_DOMAIN) ? u.email : "" },
        theme: { color: "#5b2d9c" },
        handler: async (res) => {                                      // paid: the Worker checks Razorpay's signature, then extends Prime
          planMsg("Payment received. Switching Prime on…");
          try {
            const r = await payCall("/verify", res);
            if (r && r.ok) resolve(r);
            else reject(Object.assign(new Error(r && r.error || "not confirmed"), { paid: true }));
          } catch (e) { reject(Object.assign(e, { paid: true })); }
        },
        modal: { ondismiss: () => reject(new Error("")) },              // closed without paying
      });
      rz.open();
    });
    planMsg((v.note ? v.note + " " : "") + `Prime is on, valid till ${dateTxt(v.until)}. Reloading…`, "ok");
    try { localStorage.setItem(PRO_HINT, "1"); } catch (e) {}
    setTimeout(() => location.reload(), 1600);
  } catch (e) {
    if (e.paid) planMsg(`Payment received, Prime not confirmed yet (${e.message}). It switches on by itself within a few minutes; reload this page then.`, "bad");
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
    planMsg(`Free trial on till ${new Date(r.until).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" })}. Reloading…`, "ok");
    try { localStorage.setItem(PRO_HINT, "1"); } catch (e) {}
    setTimeout(() => location.reload(), 1400);
  } catch (e) { planMsg(e.message, "bad"); }
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
  markPro(false, null);
  TRIAL_USED = true;
  const onBuilder = document.documentElement.classList.contains("pb-admin");
  document.documentElement.classList.remove("pb-admin");              // PF Builder: its contents go at once
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
      if (plans) openPlans(); else if (endBox._reload) location.reload();
    };
    $(".au-x", endBox).addEventListener("click", () => close(false));
    endBox.addEventListener("click", (e) => { if (e.target === endBox) close(false); });
    endBox.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Escape") close(false); });
    $(".au-pay", endBox).addEventListener("click", () => close(true));
  }
  endBox._reload = onBuilder;
  $(".au-title", endBox).textContent = p.trial ? "Free trial expired" : "Prime has ended";
  $(".au-plan-lead", endBox).textContent = p.trial
    ? "Your 1-hour free trial is over. Choose a plan to keep Portfolio Builder and Telegram alerts."
    : "Your Prime plan has ended. Choose a plan to keep Portfolio Builder and Telegram alerts.";
  document.querySelectorAll(".au-back.open").forEach((b) => { if (b !== endBox) b.classList.remove("open"); });
  endBox.classList.add("open"); syncLock();
  setTimeout(() => $(".au-pay", endBox).focus(), 30);
}

// ---------- phone: one "Navigate to" button (fork icon, drawn one line at a time) → every other page in one overlay ----------
const SVG = (inner, extra = "") => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${inner}</svg>`;
const FORK = SVG('<circle pathLength="1" cx="6" cy="5" r="2.4"/><path pathLength="1" d="M6 7.4v1.6a3 3 0 0 0 3 3h6a3 3 0 0 0 3-3V7.4"/><circle pathLength="1" cx="18" cy="5" r="2.4"/><path pathLength="1" d="M12 12v4.6"/><circle pathLength="1" cx="12" cy="19" r="2.4"/>', 'class="hni-fork"');
const NAV_ICON = {
  valuation: SVG('<path d="M4 6h16M4 12h16M4 18h10"/>'),
  factor: SVG('<path d="M3 20h18"/><path d="M6 16v-5M11 16V6M16 16v-8M21 16v-3"/>'),
  portfolio: SVG('<path d="M6 7h12l1 14H5L6 7z"/><path d="M9 7V5a3 3 0 0 1 6 0v2"/>'),
  builder: SVG('<path d="M12 8H5.6L4.5 21H12V8"/><path d="M8.2 8V6.2a3.3 3.3 0 0 1 3.8-3.2"/><g transform="rotate(-40 17 12)"><rect x="14" y="4.6" width="7" height="4.4" rx="1"/><path d="M17.5 9v10.5"/></g>'),
  weekly: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="11" fill="#29a9eb"/><path d="M5.4 11.6l11.5-4.4c.5-.2 1 .1.8.9l-2 9.3c-.1.6-.5.8-1.1.5l-3-2.2-1.5 1.4c-.2.2-.3.3-.6.3l.2-3.1 5.6-5.1c.2-.2 0-.3-.4-.1l-6.9 4.4-3-.9c-.6-.2-.7-.6.1-1z" fill="#fff"/></svg>',
};
const NAV = [
  { k: "valuation", t: "Valuation", s: "Every stock valued and ranked", href: "valuation", open: true },
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
        e.preventDefault(); closeNav(); setTimeout(openPlans, 160); return;
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
    if (GATED && !u) {                                        // no login: back to the screener with the popup open
      location.replace(HOME + "?login=" + encodeURIComponent(location.pathname.replace(/^\//, "") + location.hash));
      return;
    }
    if (GATED) document.documentElement.classList.add("auth-ok");   // show the page at once
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
    TRIAL_USED = null;                                                // free trial taken before? (only the owner can read it)
    TRIAL_P = (async () => { try { await needFS(); return (await getDoc(doc(db, "hni_trials", u.uid))).exists(); } catch (e) { return true; } })();
    // PF Builder (button on Fundamentals + the page itself): admins and prime members. Everyone else sees the plans.
    window.hniBuilder = (async () => (await window.hniAdmin) || (await window.hniPrime))();
    window.hniPlans = openPlans;
    window.dispatchEvent(new Event("hni-ready"));
    if (GATED) showUser(u);
    mountNav();
    Promise.all([window.hniAdmin, window.hniPrimeInfo]).then(([admin, p]) => markPro(!!admin, p));
    idle(() => { record(u); loadExt(u); }, 1500);
  });
});

if (!GATED) {
  // screener: the Fundamentals button. Its tap is caught by a small script in the page itself (so the FIRST tap works
  // even before this file has loaded): it disables the button, shows "Checking login…" and then calls this.
  window.hniAuthGo = async (href) => {
    window.hniPending = null;
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
