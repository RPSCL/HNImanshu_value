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
let getFirestore, doc, setDoc, getDoc, serverTimestamp, collection, getDocs, deleteDoc, onSnapshot, fsQuery, fsOrder, fsLimit;
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
// Cashfree order and, once paid, moves hni_plus/{uid} or hni_prime/{uid}.until forward by the months bought.
//   Plus  ₹499 a year: all lists (Smallcap 500, Microcap 250, Others)
//   Prime ₹249 / 199 / 149 / 99 a month for 1 / 3 / 6 / 12 months: Plus + Portfolio Builder, Checklist, Telegram alerts
const PRIME_PLANS = [{ m: 1, pm: 249 }, { m: 3, pm: 199 }, { m: 6, pm: 149 }, { m: 12, pm: 99 }];
const PLUS_PLANS = [{ m: 12, total: 499 }];
const TIERS = { plus: PLUS_PLANS, prime: PRIME_PLANS };
const PLUS_HINT = "hni_plus";                      // "1" = Plus, Prime or admin last time: all lists show at once
const GST = 0.18;
// Address of the pay Worker (Cloudflare, pay_worker.js). Empty = payments not switched on yet ("Proceed to pay" says payments
// open soon). Nothing here is secret: the Cashfree secret, the prices that are charged and the right to switch Prime on all
// live in the Worker; changing anything in this file (or in the browser) can't change what is charged or what is given.
const PAY_HOOK = "https://hni-pay.himanshushekhar8.workers.dev";
// Plus / Prime data files ("Others": every NSE stock outside the 3 lists) are published encrypted (paid.py). Any request
// this page makes for one of them goes to the data Worker (data_worker.js) with the login token; it checks the plan and
// sends the file back. Every other request is left alone.
const DATA_HOOK = "https://hni-data.himanshushekhar8.workers.dev";
const PAID_RE = /(^|\/)(fundamentals_(?:back_[A-Za-z0-9_]+_|hist_(?:then_)?)?oth|screener_oth)\.json$/;
{
  const plainFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    let u = null;
    try { u = new URL(typeof input === "string" ? input : (input && input.url) || "", location.href); } catch (e) {}
    if (!DATA_HOOK || !u || u.origin !== location.origin || !PAID_RE.test(u.pathname)) return plainFetch(input, init);
    if (!current) return new Response('{"error":"Please log in."}', { status: 401, headers: { "Content-Type": "application/json" } });
    const tok = await current.getIdToken();
    return plainFetch(DATA_HOOK + "/f/" + u.pathname.split("/").pop(), { headers: { Authorization: "Bearer " + tok } });
  };
}
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
  ({ getFirestore, doc, setDoc, getDoc, serverTimestamp, collection, getDocs, deleteDoc, onSnapshot, query: fsQuery, orderBy: fsOrder, limit: fsLimit } = m); db = getFirestore(app);
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
      <h2 class="au-title" id="auTitle">Login to HNImanshu Value</h2>
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
  document.addEventListener("keydown", (e) => {            // typing anywhere in the popup goes straight into Username
    if (!box.classList.contains("open") || e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return;
    const t = e.target, typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
    if (typing || !/^[a-z0-9._]$/i.test(e.key)) return;
    e.preventDefault(); e.stopPropagation();
    const i = f.u; if (i.value.length < 20) i.value += e.key.toLowerCase();
    i.focus(); try { i.setSelectionRange(i.value.length, i.value.length); } catch (x) {}
  }, true);

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
  // lastLogin = when this account last signed in (Firebase's own sign-in time); lastVisit = when it last opened a page here.
  // Written once per browser session, then at most every 10 minutes, so Firestore use stays tiny.
  const key = "hni_rec_" + u.uid, vk = "hni_vis_" + u.uid, now = Date.now();
  let first = true, lastV = 0;
  try { first = !sessionStorage.getItem(key); lastV = +localStorage.getItem(vk) || 0; } catch (e) {}
  if (!first && now - lastV < 10 * 60e3) return;
  await needFS();
  const ref = doc(db, USERS, u.uid);
  const signIn = Date.parse((u.metadata && u.metadata.lastSignInTime) || "");
  const data = { username: nameOf(u), email: u.email || null, provider: providerOf(u), lastVisit: serverTimestamp() };
  if (first) data.lastLogin = isNaN(signIn) ? serverTimestamp() : new Date(signIn);
  try {
    if (first) {
      const snap = await getDoc(ref).catch(() => null);  // each user may read only their own doc (to keep "created" once)
      if (!snap || !snap.exists()) data.created = serverTimestamp();
    }
    try { await setDoc(ref, data, { merge: true }); }
    catch (e) {                                          // Firestore rules not updated yet for lastVisit: save the rest as before
      if (!/permission/i.test(String(e && e.code || e))) throw e;
      delete data.lastVisit; if (!data.lastLogin) return;
      await setDoc(ref, data, { merge: true });
    }
    try { sessionStorage.setItem(key, "1"); localStorage.setItem(vk, String(now)); } catch (e) {}
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
  if (!host || $(".au-user:not(.au-ph)", host)) return;
  const n = nameOf(u);
  try { localStorage.setItem("hni_nm", n); } catch (e) {}                 // hdr.js: the header at once next time
  let el = $(".au-user.au-ph", host);                                     // hdr.js drew it from the last visit: filled in where it stands (no blink)
  if (el) host.querySelectorAll(".au-user.au-ph").forEach((x) => { if (x !== el) x.remove(); });
  else { el = document.createElement("span"); el.className = "au-user"; }
  el.classList.remove("au-ph");
  el.innerHTML = `<button type="button" class="au-av" aria-haspopup="menu" title="Signed in as ${esc(n)}"><span class="au-ini">${esc(initials(n))}${BADGE}</span><span class="au-nm">${esc(n)}</span></button>
    <span class="au-menu" role="menu"><span class="au-who"><b>${esc(n)}</b>${u.email && !u.email.endsWith("@" + USER_DOMAIN) ? `<i>${esc(u.email)}</i>` : ""}</span>
    <button type="button" class="au-out" role="menuitem">Logout</button></span>`;
  $(".au-av", el).addEventListener("click", (e) => { e.stopPropagation(); el.classList.toggle("open"); });
  document.addEventListener("click", (e) => { if (!el.contains(e.target)) el.classList.remove("open"); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") el.classList.remove("open"); });
  $(".au-out", el).addEventListener("click", async () => {
    try { localStorage.removeItem(PRO_HINT); } catch (e) {}
    await clearUserCache();
    LOGGING_OUT = true;                                                  // logged out on purpose: plain landing page, no login box
    await signOut(auth); if (GATED || LOGIN_ONLY) location.href = HOME; else location.reload();
  });
  EXTRA.forEach((it) => $(".au-menu", el).insertBefore(itemBtn(it, el), $(".au-out", el)));
  if (!el.parentNode) host.insertBefore(el, host.querySelector("#theme-toggle"));   // (no toggle in the slot: it simply goes at the end)
}
/* screener: "Login" when signed out, the initials menu when signed in */
function paintSlot(u) {
  const slot = document.getElementById("au-slot");
  if (!slot) return;
  if (u) { slot.querySelectorAll(".au-login").forEach((x) => x.remove()); return showUser(u, slot); }   // hdr.js placeholders stay put
  slot.innerHTML = "";
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
const PACK = "ZHlrTVtETFkgOBUzEw0cAhL27rL404fPmdisqfG5rMaZ2JxnfVR4fQVNQBokZCgzKSAEEQqg5v218o/Z1Mz7qa70upHJn9uaYGB2bkRsQFUdJThyKkwEARkQ5unl8fDIjdXI4qX8pLuXmpaIVWFic25QSl9JYCY9dTUeDAMXEqTl4O/S2oqYkbK7pPSGm5uXiW1oPHpPSgFTVTEvMzksAwcHVLO5vqati5yc3Ki6qrS8l47fn2l1a2ZoTFhUFlU1b284DQcDA173sbH7xcXG2P2xuLerk8mGm5Vua39rd1MDVUsya3wzY0cEHRPe/fzz79eJnsz95aa7tbyLlZWWaH42N0EUEl9QLBMjNRMcCRkR7eSwtdCa9ZfUraOOtb6UhpGPICUye2pVDm4BLS07djBIA14fven7+fCP3IzWyqqh77iNh4OD3mcxcicfSQkQXGo/PR0BEwtKHa3+/tz+0sickuy9v5uxho2fn1J8fX9zQwMQXC5qBxt+TxEVGQvj3uDizsyKldOgqLqvjMrDgpRpbkFtWUtLDx4wIj4qK0QQXUGgaxAEvInVjpKg5Lv/4NqFh5VsOjN0IwHIsasdfGU1Z1JWUpX2F66p7oeOzoiN++n5+9rFzNzXIeiToT0NYFVPKmciOCsLCFs34PznuPzG2JrY6e7gobOvhZKZk2NeYGlLR1cfHCAifhMvSkZaGrnqr6fOwtmV+KK+tPC+z8OAlE5mc3ZyQGhSTiQbOyQ0CgxaW+Xpo9zSgYbK3N6/9/a1l4SVhZdmLj93TkZbXgdmODo2EhNMWQXm6+OivcjY2d6woLP1o8zAi8cpaSNiNBkVSVovKT0hfAZXHx0Ipsn178eB0Z6Sq/G29IaNm7KccG46MBtCAEZZNw4wLDpOXF1X56fj8urow93OqeCq+LqBn7+WbnNmPTUIXhgDXCkjJy9CBk0ZG/Ks1/vVzYfTk6Oupp+Vi4KsmWJ4OTEzQwNTXjYEPzkqDURaUbCkv7+zw87G/aGzq/312JiUjIp0YzR+DFpVQ3okODZyLAkbHlPp4vyxzovBnJXvr/+/upK5nZZnITlqJUBUQ1UzPG8yOAIKBxX0p+/m5c3Jkd7KqK6gso2H0IDWayVodxxGA14AJWM8dwEXHlxH7+/luPCbw5rOsayi+buIjZqWPDUyZCZNTRoYL246JyUYBUxQHvHs/e+CzJjDlq3gpvaPxpqFmHYlZ3BEDgccEiYyOCsrFUVdBuHo5PT2jc2awbyhqf78i8KAnHRyfHsnSkwZEFFoLCQrTAYAAxfq4uDm3dPSn5Ogqqa4goadkKlRRjhsbV9WQgYjPjE+KkUFHgru+vu+v8zfxsmz/eH6q5Sd35+McmxgckEHU1hTaio6KAQKDgUY7vi9qJCJn4aS8+W3sa2Dj5WIZyR0dmpETlJJJGYlJX9NRxRE9qnp8Ojny8XZ3ae+sfOMx5GHjixxcHtVS0ceXG0wLzoPCR0BXPC39ff808DR1bbns6W7hJiWv21tYnNzUAMQSjQ+IjB+SlECVgvj9eDYzcfE0tCx8fGGj8aOg9B3aGBvAAlPQBEhJSkjKA8JABO45Pn5tpGUg8q55P7m7cHCj6VuKW9gMVBJQ0NbLz4kNwMQShES4PSo+83ByNiQrb+3tJPdjZCSd29jI3VTXkBSJDB9NDELGBYU9bL85vzHzp/bpbO5sLmN0ZaZjzw8JGtaEkBWWiElPT1bUR8OXbSw8PbSw8vHkaGlpaywi9fFi3opY3hyTEgTTCA6Z3twBR5fFenp67zh/8Sf2crrvrep2IWRhIoob3tzTUxUVFI2LzcrTQUBAQjs56uo4vrDmtq35KO0rMWOiJxuZnssKxQbEghzNzZ1KgIYWVVS5/i56MPHw57DmaL9u5TFnJWPJHhieE5cSlxPMyYwIWUEARsY6bL2+PDRloaK8ej+562cy4SYci8jOH1WB1xXUSlkbzgNBR8FRPPt4bKMhc7DkKKqu7eUzpOpki1rZDVoT0lRQDUgNCM2XwEaFKm5vqKt1NOeiPD36/znjouJ1ZdjZHNzVhMIAUgtdzczEhgDFwS+7f782JzI2dm757WxrYOOgJJtZyp0cUlZXlQ8FCF4PBFGAwLo4ufy9NeQgozPvvakuoaNmZmZPzwzKxNYVw1QJTk1MA5dVgUEo7qxqa/W1Y/Zrbu0sqzf3YOCIXtgenRAC0RYMm9jeD0WRx0REeOkr/nN29TSzOi+sr6InZzMxXRzKXtBREVSTiw/PzxlEAwGU6+k8eKzw8XW1qXh9LWyiISAw3ZmfD0xDktEFVYoJn1gBAYeA0S0v+Pigd7OxJXp5rOszZSPm48qMX5ta0pEWl54Jz85OxgwHVTg/aLnp8LE0cyzvKy6roePg9WcaWF7aRhfUUUWaGEyL0wJDBVU+df8t8HSg9bTtqSlo7yJgZuJOH9xZTYIAVJPbC4uPzMQQkkf7+n6uOvGw9bQy/z45OuftZ7Zn3AhZ3hNU0BAWDYtPjYXXQ8ACOyx8/ftwsjGgfO5qPetioCaniF+bmQ1CQZTTG0rJzs5SlETFw3i6Oa20MjU3su29uuqmZWzmNNlfj9tQksOQV0hJjQjKA8JABO4uKCnu57O3Milrb37vouHnphwdGsvf0xGXVlPNShvPQ0HBE1Pt6Km6tmI2dfP7Ob/uJXKnZSScCNsRHEITEEWNis8dyoNFwMV8uH7//LKkcHNqaSlrOeXhYHCzz1vdXhJTkJYSysoaSwAGkdbUOX+v//JwsLRlfipvrSwlNeCmnAhPTp/UAFVWygmO39mAgQcDbqxvqW8kpvBwJ+wrKbzz8SRgtNoY311CBNbU0UwZjM1CQAATxDm7OWj78fJ0NKsrurvrp3MwspxcDR0clZPV0ttJSEhKAwHS0kP/q3n9M7A1JfIpL7798yJmtuRbWV3MF17QBtdNmclOjNGGRAA4Ojz/PnXw8bUpfK7pLyKmIKYcmJgYSdXT0lMEichPTwMUxwSGPG39fXP3JWHj+r+oqHAkY+H1C4ncG0yVUxaSGtyIDY6AQUdHbu//+69lZvCwfuloae4hpjcmpByeXt2GBhATx42Iz8zBUgZFw+spr/41YrC3NKm4+q7sIqChsF0aGI/MwhNRhcoJiR/ZhMDGw3lqv3l/cDPi9bQsb+1q5+1ntmfcCFneE0IW0QHKCohLU0EBhwQ56rl/OTEwsbfp7v9tbGRmJyXOzhySnMKSkcUNCUidSgBBRUBX/L/rvPN39XFnrGoqLiAi4SRj2t+fH0aREFZUzFnPDEnTgQaW/H79/WyhdrSyOnl4reoyYKckiknNjAwA15DWVE1PTUpBwcEXkXm4//105LZ18/s5v+4lcqHm5cqd012MUdYGU8gJXAjPAoIClr1+rX+8tLOwJm0o/SztZGZhdWcbmR4f1lLX08TNiQyPg4fVR8T9+7muZLX1pWM4/rxrr6UxdnWY3w9dn1GBU5mL2YuI3AXEh8C4/L85vPRkMHX1qi5sanZi5+FmmB+PnhOXFtZUH56IiFAAwEBCObusfvq1N/R1baKv7uxl5GvlC9pejtuXUYIUS8xKycnAAUdFw28+/XpioSd1svorbC5yJWzmNNlfj9pRlEOG0gvPSo1PhQKHRWv5fXx6p+Yw8K8lKH4vJHGnZdsbmBwZ1NFQlFLLyI6YQQACBIavuD2/NWSnoLNvPCwtpSTgZjGMjphYCRcAF1VJiwobWxQXEge6Pv/+vzdkdTVpb/1s7CGktyclnRod29LRl4NXSogJjcPUw4aFOPlv/DUwsPGhqWmtKDylZmViXYyd3ZuHx9DQno4ID8zEA4AVOXx6/vo0JDf19Gj9qGoh5vdhJtpaXBuG0ZAWFg5Fzx3ARJDGhLv4//9v8/Ww9Kmvbjt6JWUyJJkYWh+aR4dQkF7JSEnOAYYXAoe4uTh6JicgJKFp62wsYaagIOTYDExK0ZFSANdeCg+IHIVBRUf7f6qp76VjIDKuej+pqXEmZWbYS86IjASEwAUDnZ7eHVWXFlMH+vl/vvVwcDYh6W+/6mVi52Q3DEkJ2s/Q0xHXm8gPnoxEBhTE+/u5vj00M7P5a7pr6DxjISdkZFjLWdrQ0dLUVErOGljT10fDl3y6uCxjYrPwJGupb+39t2BkY92bGI6bVVNUFMvL3V4bVYOH0Lj6OL67pnc0MqX6+C1rs+PkZ6QcSVuRk9oRFNEIjkzNAUUThQJrvrk9OzD1oSe7vjg5/ueg4ObYmF7bycKHAdEdXdrLjMTCxIRC/+3pebf9d6Z37DhvrGaioCEmWF5KChQXw5GUy8jNXgpBx9cVq/o5bryzMLWk/qqrrW2g5mdjG5jNGN9UQIcFV4zYDIyBwUUXkXm4//105LZ18/s5v+4lcqDgIhmIyp+cEhZDg1yeXBmbxUUUwzg+qe7sMXen9SvqaH855OLlZyWaGouL1JREA5OPXcxNRMMCgRQ9ur28NXUlIPMu/Gyra2VgobBcmZ5eWpAXghNKSE7M3AXGxMa5b3g+uvRy8HF46jjta7PhJvNlmp6dmhaSkBEWSE5fzoPCwEHRvXr47Cyi8zBlqOqs/7lhoOflXMyeXdvDAYfWDVqJzs3ShctFlHn+Ln2yYff2cWno6G+hJrClZJoZGAjVkZcHRFuKyR1PgUOXUDh5vz47J/a0sjp5eK3qMmKkZopelJ7MkJfHFVUaCwhdgYMHAwT5P7088+Fw9PbsPHkqZiaspvSYn88dXQITEEWJiw8bTYKGhYI+urg5PnB2Z/ar6uhp+aVi4PQ0itsYTZASFQeBSYjPzUTUhkXD6ymv/jViszU2Oq3jbbxh5jZj2Nua3FxS1gJDHF4b2dsFBNSD+H1prixwt+c1dCoov3ggYacmIw/enJoCQUCV0hpLTMwDhNHCCDtpPDtstbD1dansrS+rZWAkoM7YWF6dEpOH1ssKC0+ZxMLFRwW6OquqdLRkI7OvfextZOMioTQdmp2cFVUFAwFejopYz0HDh8c8Obl+fqf3tTYoOD+4+jI2sLOLDU8JjANGwcRBCQiLHYRARETEfK2+vTSzduWjeT78unA1p6N3HFtc3k3FxoEF3N7ZXtsUVxfVLa9pq3+y8fdy/qxr6f0zseQjdJvY38yX3VeGV8wYT4xTwkaWw32pf3328XBx9imuPy7sIqChsEhMHIhfEFKCFkuJCAkZ0cJSkCy4bvowM2E0M2Sq6b6upfEgJvQamJoeE5aS1NPaSg9NQ8VVFZP5ev0rv6dztvXrbvq9OuDj8rCYHVTeH9LT0sXJCY3dXICH1wVFKjs4bbSxZ7Y0OmXt7uVicKClWFmdyRERldoHG0rJHUyDUMVDq/5/Lnxy9fQ1a2nvez+1InEmzJkNXdzUU5UShIlIjg0EFNTRxyz7qH53PTB1NKgsvy9gZ7O2512J3xzMUdYGUswZz85cj4IEg7gpfv++MnOj92hvpP18oKf3JWUKGxhNlJbHlhQPi88Ng4aVVVI5rn2oMOczNrOp6+j9byJgZuJOCondipGTwNHHSZhNyhJHxsV7Pzq/O/TxtDBhaShu7iJ0p2WjGJlfTdVR18MDjQzaT8PCRpPSbO6samviJjEw+K/saX2yMGSjyxlYHhyDRBRViwoPG8qAhhZVVLn+Ln9w8Dew5e4kL30gJ3CgpRoZzx4VQpLW1gmLio7MAoCBkH06OK/s4jNxpejqav/oLiF3Jh1KntlZ0BFXVdNfDs1KUpEXRAR6ui/uoKbydfY8qr7pMDJj4DRZ2Rqe3BKQkYBNCgif3NIDhIerais862SnYSP6bqSu/KCn9yImXB2bjZLR1RSRn9/YmpaCQMfGuqm++3Fyt2P36akpb2tm7Ga1WN8PWd4UwwdWzRlLTklHxwbHfTvtPj1zYKIjo+2tfjq0tnV3sVobWs3SU1GUVUwcTE4DARGREyz/Pm4soaehsu64Ou6v53Bm59ob2diJ0dKXlpodn5lOBUCUVVftb/k44uSwNbaoaW9vdvZ2YaFJDokaVgHHwdMOzcNNnEHGFkL5P+wuf/QgcfTtaSqra2Fj5aQbmAjZ3VEQkUCDHA9LGAEBRUPROvj/f/c9MGY3LHmor+CiIqMh2VmdGAlFw0FGyM8JDhlCAUdV+nt5vH10JGCgq+xq6e6j4WG1YY8bGFvTRJfQVs3PzAoDgQDWx/h4/Pvycjcj9+spKW5tojW2Yxna3t+aghDRV8zLiM5KkkYEQvv6+L88sSQxdfKpaXvtoObl56QPzRjYgEFG0ZFZHtpKQEDChwS5LChuKvW1ZSPsrGti7DLjYbXcW55NjNFXh9NIis1OioGGBcUEPGg7KHD3MTYhaq6tqiHhICB0H0xenBEQ0tbBy4rKXU3AwQTE/az/vjwwNHv1O+puvutgp3S12FyI2F+TwpFUEQ2IicyFgAfGUT2+PLuyMvS6tPqqqf0kIGY1dJifzxsfUoNQFktLSl3KhdWGxX37f226cDQ0NijrKmns5aElcKLdGx6aFJIQlJQMTEPNE8JGlsN4v2yt8HSg8Her+qlurCClNSPcDN4eGhAXhNOJXIpPy8XH18a6O7i8efBxcmVzK6ssLSV056YkGBxT3QPSVobTSI9cncBEkMBHu+q5fyx1IGa2rfkoLGoxcKSjyx8bXo9UEMcSzszKy0oTgsdERjot+byxcHEyuKr4rKvzJiJgN0qe3Q0U1JDTlEiODYxMUsPGw/25v2tr5fcy8edpuG3qMmblJF7Y2dmbE9LSAJZKigsYAMFGRAQqOXn/8zbldTct66+sI6C1ZKdczApaGcdS1tVNnNmZ25FXUEK+aj59++Mhp/YteqjurKMw8qbkGpiZiFUSEIfE2gtJncMHRsTVL/m8+vHzsCPjfe6qfjvxtuEg39Vfjl/UAFDXCloPDs8CAcJH+/p+rjrxsPW0Mv8+OTr2Yqfm5F3NmV7UwACG1wxZjQ4CQkaXAHf5L/56ovd0syju76suIqCh9dyYXVzJxUZQkF7JCE5MxFQBxkNrqC5+teE0tba7Pe+u5OPhpjHNCsiOREXXk1BHyR/OSpLGRsa8f3r5/HWxcfTrqb1sLScjpbCbGJoYSYWGhQDXSk5IDQPU0JFDv235+jAxtzQ0ram6K2ShoCGkGJ+dEA3CxgEHmtyKno3CwgWAru8v6amxsrR0qe1oaCyh9CHmY0uIDl6VwRSUBd+Lzw2DhpVABz2o7+0wdKD3NKo4+q6sJSJkYk4OGBvPlZDX1MlaDk3L0xGXxj1quL88saDiuTR5u22tJCNlYXTd213c1RbFQ9NPHAiOAQDBxsbubPh4L+XmcTD+a+/uarf2sPKITk9ZmUEXVNLaGpjNClOBx4WEK+29vTahMPf36GjpODRyN7GjXwrICFQXw5HWyEreWhzVkFEV6y6pb6lytzS2ai8tuzt35+AmG50Z2F1TEQLV08nLj0vG0leRQ2+/Pzzz9zKxJChvbe3lJTUm5Ntb2xEcQhMQRY2JjEkKksDHQHu+O719NDSiIi9m6D7vZbHhZqTJnl8QEZIRFYTNicOIQIdHQUS9rHi9snJ2tDO+L+iva3LnpGXZ2pkLXBKQlYBNiAmIjhJGAIY4+K0+/PU2NDIwpqj+rqXxISVkiV4e0FFSVtXEDcgD2MICBgQDvjp/vTw1JfC2rDh/fq/kMGalGohckpzCkpHFDQlInUoC0QQDVL14ubvx83L1NGpo6Hgl4md3tApamc0QURNHEEfJH85KksZFhei/fi3997K3NS15byipIiOyJdvdWN0cBhHUEpYLyN5NwcPBE1K9fTuxs+GzsOQt6SgrYKGnI6YanlhdH5fF1pULCxrNjIMCx1X6Pzq++6eyNfXtKK87ruCmsvAj342eXpQTllZBGh+IyJBWE9HTfTzqf/PydqPivP68enulpXUjWN7ODozRFkeVy4mIH9mBwQeFvK9+PTui4ec2crrq7WyjJ3ZiqJrInJvDFtAREkmKiB5EwICEB/38ff0+t6XhYCvoL76qYyIh5I7ODR+eE1MWk16dHolJFgLAQga5//19cHMitnRq6no95aNjZ2UcCZzaVBCT0ddLSk0YjEJAxFA8uj08/fLy4mK4fv/pqXE29LIMHd2Ln5MWFVdTXx8JCNCGh8bF+Gs5fvTgIKb3LHmvrCOgsfOnmx4dX1tC19VXys8I21mFRRIGODr5PHvy97c3fqxr6f0zseQjdJgZHF3RgAQQkwpZA94BQkbF0ft5vP+xYjdw9vosry088Pet4h0bjBvc0lCQAdmIDsiLV5EXQ738KDir43Fw9+Q9P3k682ahpDZJXt6flVAEhEMdGxyMQUOCR0Ivq2nv7qV6JGIgbmxo7bFiM7dTDkvJ3EQCwYZdGp6cnwFAx0UQqHj+/XHjpDEyrejuL/cz8rEzj0yKz4AVFpHUygvfC82AhkcRqW4vqG5iomA/+T7jPmukozXykVbLDw8TUUcSlo2KDUvQhsZEBbxrKKq0dCP1diqv7er24SBmZNxMGd5bQ4AGVo3ZDk5NUxXFRXv/LWgrZSLg4vu8r6t/JWLg9DSK2xhNlFIXkQXfiMmLg0BARNH6uT8/N37wJvdtueit62Sj5WJInp1e3tGWAlcLis6JSYGBAAd5fWj9vPPxcOCyae//PbPiIXan2ZvOmd9RgFXSGk4PSsUBQ8HXPDv/f380o3by7agv7mlh42QkWZ6YGNzQBFEWDJvY3g9FkcTH1a97vv3zduKwd+35P73gJ3Cn5NvIm9FTglPQBEwJSMsPQcfVBn3/eT48N7E1tOmoLvs7tCbisJ3bmphdBkZCUhHfS87KQYMAk1P9fSz6c7ExtKdsqqg8c3Kj4DRb2N/fTYdT1tJJiwieiwECBoP8rK35uWfydPaq6C8uqmNjsuOnnQlOTZDXB1RVyAgN3NaCwAaEvax5PjSj4OY3bbnsLu8z9aSlGx9KiAuFQwCDzEwbyA8FkNfVOHyo/jzzcWYg9yzv6e0kNOAmJdreHZoXHRBdlAhLzs4QE8DFASu/fj8686Xg4vyuaj+pbmC09ovaXo7bUJdSVgsLik7cQoeFBUMvP7g6cfd09+Fta23voiGiMzFdHNvRU4HDhtdNmchPilGQxUOr+v/7+XIzcuXqa2msbWQ0ZyWbmI1fXlKTVlMBXd9ZH5ZCx8FGuD+vujAzMbDzv764KmYmrKb3CMkcG0yVktCG2woJXoqBwBTDuWkoffoidvUz+Dpr6Dxl4id2ItudmR6Rk1ZWVl/eiMiQVAfDgDY5bK5jsbbmMmwr6Or/8iMgdZxZmJjfEReSF4oOz86PB1RFBXl//PJ8t6IndzQpbi5voyd3p+bZGg9e1FYSlhZByM7NQRPHVxQ7aTw/Pvv2dHW6uuFpLuXn9HWJSEjcGhKSEZQLylmfCcKDFlZGfrx8PTB3N3S0LHioq+EmpalmGhucW1PVQYXEiI/fDcxCgQaHqCgueX70dnB1PqroLiukMuTxGRobWBxRkRFFlw0KDUvBywcEhPg4ueyg8zGwJ/t8LP3g4uPho9Na3x9IgRMQRYtJzw+MABOXxuv4eH4+Nbj5vSM+uzptd3W3pHBOn5kekwX0reYZSM9NggGCkpS9/vz956FgtHToL+8vbGSw5aUZnA+dm5VSV1eAiAmOjlMCltC7OL6teiemp3dgqi4uLfZip+ZjXEsfScJARIIRiVlIywFFRcmGe/v8uzw1IWWyLKovvX3y5iWgnVLYHhpQUVGBCBjNSEhQwUfFBbo6PSwisfF29Lk8bblgcjH0oZhdjt5GgUMHEF4PH83MTAMGA7noea57MDKm9zt6qe4tMubgJxzYmB2eQEDHRBee3MvL18IXgQX/+m/9ImB0p+U6L38to6xj5mJZiJnNm1DSxxdbms4OTdKARYO4Kf5/+7N393Ls+Xn+fSC18+DnGljZ28CXQ1WEDMtP3JIUwpLX+r+//vF1YyIgbezob2wgM2AxHYzfmJySQBdEmg1Zn8gTEJeH/Xp7eH1zMSZ2Za9pLLzzbLP0aNgYXF/RRUeal9rZSY8ExNGGRPg6+Xx8MiDx96ju7O/98yelo50emEtdEIDUxAyIjogLg1KBxcW4qW2/dfH08PXqqLx59ycloaYa20ybklJSlpLbSI/MRIJHxFdpP75+frK253Sr6GCua+Bw9CWdG8sPDJAS0VbV25lfHJfVwsKV6ylqPnOxtzCnbD2+vDd2ZWWk215ZTh+G0lbWDckNTkqSwsWDsTk6vv4yt/wwImj5ve+l4TcmZNqLz0gS08YFl85MDc1Ah0CExPwpfX81OLC0NGmpKWapq+J3NlgfX46cVFEERNoOioiKBYFSRrv6f3hvNeX1dfcs6CxtZbHk4WbZHh2X01NQlNTMGNwOxUTGhoSoaOq7LHFwdXIsYexurvYzoaUaCVtYnMGB0YXKSNzdz4XBFwXC+6vuO+M3dXPyoajva6EhpvL30t/enxSVAwZSG0+OCwzA1BWPvTs4u6+ytjb37PogYWYxJiGlmNsIjVzVl5CUVsjbSAzB0lDVxLs/+fpg4TOmNyiv7eryJPHiMchZn55e09DUxl/dG0zMQYZHh/v/KHk+MXPy+q0prqw44eFko2SY2NgNUNNVHJIICInFggbGxMT4fm6u+To4/bTrb60tquqgpWfZ20yO2oMFkcSaDVnOHMDCgYc5K7z9O/axNKY2bOjt6+Lhp7X2i0laHlORlxCRmBxM3UFFA1PE6/56Pb86sLX0Pi6reqz3oCWjiFhMnc1BgVTTG0yPTAuEEhYQxbgpbXyi9LZitqqr6a3hIab2J52bnNtRWJCUFEmJCVwfQIEAlmrpfm5/cnNwMmPqaKz4MaKh9RiZm1+PEJfHE1MIz8neU4AXh4Q6+nh0vXl44ua+K+7r8CEgpSPcDczeWoLT1tDYigleikMCBZYofrg+viZidbQoauhsv7Di4ORnitge39DRQ0VSjc5NnhfNAFWXaSrsrmcxdvByKyk8buzh56HxiBoZTpmBwxHQzEtcnQ/ER8GFu6lrvTuysuc1N6kqLjmwKqcmI1gLi3ZthQAVEgwPz03XjsAVVyjqrG4o86flNiuqKOk48eNhtd1YXt6eAYVZ0olNT11YBAaEBZf5eH16NGUktbL6K+8r4+czcjBK3hieE4ZEhpUcXQNNn9GTVRborX5+e7Q2JPZram8peDGiofUcSUuZXBCSVRQUCopMSlfSyMSH/fv+7rU28rE06Wmt/mPlc6QkWJjfTo/VV1RVy4qODI9DlFRHODk/PO/mvfcmeDn7vX8346Yjt9lYXVoURQSVktoPzwoFQoOBF+6t+HpwcmQ5tOxvvG6ptrCh4tjZy4rbUBAVlk1aCw6PBcYT1vh8qPm74GK0MrWp+C4uoCMnMrcVmNhbgFdXFNPN2swIEJZUloP5ub0++uYkdbOtr2/uf6RlYOfPCptY2lQRFwbYCQiNC8QV1MZCqv+8LmCyMLe3+igsriEhNLUr2F9d2tTQg5aTicvI3phhOvnR63r5ePqysKNhu6spqDjuIXS2SAnLjUgR0NHGFwqLCcoX0sRAlPx7v+4n5SA0tSy9e72hI6Yy9svbn57aktIWk9sKz8zJ0sNAwrk5uvV9c3H1pGp7vW2s42ZhdiLOyU9JhxSWRldKS0gKS0BHAJT9u7/9tbChpfTs6+/+vbKntzSfzJxPzwLTUYXOWpjP3RKChYdxfHr++jvw8LM2qiopvPAipyenW4uP24IBEYYXCAvFy8FCRo5FfD+9Pb61IWW2K6gs7z8ycSSxz9zbjhpRVlVXDR6c2g1RUwFUFb7pL23y4fR09qAura0laSGhYlhZXdrCAVFUEUnJSY2fUpFFUa88vG57dHDw+qzp7+3uoWfm5ZuLyc5PmZZUllPI29pZl8IXhwb/Kq17omB0p+U6K+9upWKi5uILWt1fFpQSFpPDiAjIzsLCQFSo+Pq7/nL3Nyb7O+r6OKYyLSLnGd9cTkfFA1SEC4pKnxHAUEVEeX44dXJ1Nqb36ykpbm2iJ7c2W15dXk8DAoVGyBgbXg8EUYCH/ap4eX5zYiYnpmy5f2my8DcltYnInJvDFkNGlRtZTM9BCIYEBL3xvjr68PD0cnq67m5rpCY0dYpaTIoXgxCHlhuMy8nOwYeXw4e6vjxsouAnNaW5+Kyr8ycjZrfKGI7N0FDSnBKJiQlFDYVGREV5/u4tf3JxdDR4+TnouDakJGWbnR6NXkeXh9MXjQqMS9MChwYDeD/57yH3IHC3Last63OhIKaj2Z5ZTA9UkVvXyM9MXotDjFRU7rtqbC18IPblaXpqrSogpmUjNF1Zj03YQFZG19tbn07FEUeVFHtorzvwcvb0JXqt/jx84fF1tVjfD1kbQcAWhNvKSsyGBIOHA3M7v3h+c3Pw5CdpaW1tYWM0tvWcTEtYXUARhpJaj8zKwcCGlsK4ubk/bOXhJj46qD8tvbHwpKPLHktOnQNBURYLDIrfCFKQ10ZV6Sj9e6P2tSVkqzl/buFjKqAmGp/XnBTU0tbWTFiczszDw4fWa6huL6jm9falJ67oKSp2ZCZwyhuIEpvTFhFREMDZHowTg1KWlbsoszpztrbysGB4vy9ncut3ZUvazk6MUdYGUpgZTl+cBMNHw/kofK/tNnCnNqspr2mkIqZhdaeYmk8OU1ZVVkcbGAgckhEDl5fqurntNTFwpeQquP/sbGIiIazVkRcKjkZXBNZLSk8JWBGCgdU8/Lst6LvxdDc1qiqNltE1d+HwCI3Z2hYU0xZUzc/cjZdBhkUFfeq/7DzjsWY3+vg/KTji4mE2lJtez41RVxTUDRnIH0wSwJdDVavpLr/zcrDmdOkvPvygNXRl9NtbzswCQtcCFImPXEVPhZFXBr16PnjvsuE35Kp5O2+s420gotpams3NQoEUllLJSV8c0pATUlW/uj8+dKS9OvA7eL78M6DgZaPLWdwaDcOTAkFOSo/OS0RTAdH4Kbr9+nFg5uXtam6vLDYmJSMinRjT3oMQFQbSmNqJ3QVByIfEeji4abUidra8aqmvbGszsTOlXdlfEpjDAUaFix1ITMqRCYTCaiv7+L9yt6R1peq5bz3wIGenqF1YGZpAwEGGF4lPzExSE9GXEG9our88MXejuCftPn+98zCl5VieyF7fFQDGlh9eTU2Mw0ZBVgLu+y6/8Pd0Z+X67m9roiE1ISYcH5gd3tGAFxYbz53fitIGRs26+X8/u2a2J3OroWmurGNmNrQOml7eXB+VxgRFmoraR8DHRVZEOr7u7Oawcme1OqUoLaXlNOa0mdlcmsxS0xEE2oobWklBgMdCfWo+6vvisPTyuim4Ly4ysaUxYs5fzp8R10YVhAsKHpgDx0DGlHqtua/ho/AwNCv9+y9o5qIyp0rJX8qcwtEUklpKWE/OU1HHkTvuOO7+8bemdmRr6n94YycnJvSbDF8PAcAQUNRKHZvNRwbAksaqrHj/evT39qbjau6sr2RwpKJcmFoeDVfXltdeiZgPDhPCxUVFui357XKyMOf3+ult/PNmJ2fkGExfDVQVUdYWRcjPTRlCFIRQez8/Puy1d7a16SNobK4gNGG3yYmYCp5GUREVFNqPTguEVMZWw7p+eDOyMTDjNT7p+i3lYuC2Yxvf2JdcUJIUAEtb3Z2N1oASRT05OPrscWF1ti0pub89Z7D2NaMaX9gMwoBURtKbHFtchVGAxcO8Mf9/snJiJPI7aawq6uqgpOSbCdjcn1KQldJPTR/f3BMClwV4fT62fPEw9+emafjuLqRnbyYmWxiPWlES0BYWTc3LmlJTkdZP6vjvbq9j4HVzKOgpPeNzYXa1nZhYXJyUwVaVykXKHwnAAUfCwum46n61cjZw56g5KfyicTB2NN0Ijs3VE9LWxRrK2xmPkgIDBLx/eO/t5rNnd6gvK7+9N6Fh5VsLic7f0JeUlAXbmV9ZlwHBRsSrKWo84/3wt3J+aX+uJeGh4HcU3h+dXZVSBpaLiV4PnA6HhwN8qbi9+2Mg9PKuamt9b3e1IqMjX92d3RMWkQXUXgtJDsIHE8TVfCj+rXDi8+byaqu+PHkh8OEnUZoZHYjSgJWQig7OyV1TVQdV+Tm+vS0ipDfzdOq4bX1ko/NltB1ald7VUkQQVQqLz0uTg8AHCzlpPL588WF1ZWyr5S2qoTAndMvWDV4aEhHT1ohMy09dBdDChlR9uup7c3A1JeOuLH688jErN6UKGo6Ow5GWxhNYWY4cXEQDBgO56Dt6v3E2NDS6bzmrbzMydyYdSp6d3ABBlgRES8jOj4QISQ6Mrjsr+qBy8PXzrf28LiVyouHjiE0UndqSklaHDZpPDg/AUwGCeT6/La1gNDdkbTprbq4hpaNjNZ7JDo7YUFVVFVlODs/QS4GBBj3//3rxYfcwNCmuf/k8JbTlIZ/an95bVEMWAcgdXE3e0IKXArl5OH7+NDWzZDe4Ou19ZaGvZ6SaWVgJUAGW1lwLSc+MBNPR1pN5rmr9urKwZ2XjvSLrLXfzoaJZHotOnEeCWdKJTUgNDEGSF0ZRbeh4qHDlI6f3+u5oL+Tho6bmHh3MDsJCVpacCw9NCocBx4RU6v0vOz1n47W16Cho/TxiNHQvG1mZ3k+D0sLCRMwdzVmXEsXGBHi4Pa4nJWS15O0ub2viYOLh9olaz99ckdEWAQjZzU6PwwAXQ7uxODh+Nbo08ql7+fv/sGX3YOUPC9ickMLHFsEZx86PQ8NC1YI9Kvk8MGFgtSG8uan4r7b05XVcnt/YXdBSUFGPWptK3EfAEhb6ujn+/nHiJ3UheSHu7KMjJTV0nM2cicfQwdXEyc5NzgUAgpcAa/x+qK9yszHz+DlvO38qY2AjiFkYHF0SgkeT3omc2s3SwtfFB71+dj0xcDensPpt7jgw56GhZRwKT51GgViVE83aicxLA8ZVlf0s/GqoM6E0pStqbyii42Ym418e287cEJZRXRQISQ6ch9FCxxEp//w6IOEw4yfl6qkvITHnZaOZm9/az0KWw5af3d4NnAQHw8G2tWmuPHBxdXNqLrirrfZyIGe3SphLjlyRkJDWCogOjVBHg4aCOGpvunGnZ+Zyvmr7Oa+yJ2SxGMnYHEwUkNBTilyISMxCBZeAuu9rPH92oid1IXkibWiwrnWu9wpfHUgEARZDFx5dTN3EAFIUxL25v25oseDxN3srbGu4YTCg5wvbG5vJ0peXlU9azU+ZkELFRUW6K+495iL8dPTrKLx9pfSjsvDZSVzfU1OQAoNeXosdCQNV1YL8OD98ryJwImYkbqmu7jEw56WbmBrZmgDTFhKTDJkdncUUxFKQOSi4+jIxcqJ3Oq7oLCNgrqckG92bSF6FxgOWmw5Ij4zACkdHuTssLusnpvPlbus9Pesj5+C2tNqNzZLTlxDFxYpIz09BBsbVhvt+eHtiYWCw4ai9++58ZaBgYg9aD5nclBfZ1MtJDMqZAFaR0Phqf756dDv39zaovL56tjZjarSQDFocRsKQ1dOMGl+PVpKXwhQ17e5+bPSgdGS//ertLGLn4fabzVuOEJXREBNPDsLeTNeJl8eFujpvLPDlI7WkK7x7ueVwcbNkiItOngOeF1aTjd3KjNlEkEQQef17Pmwzo2Oh7X3oPi829rI1DE9I3syR1cYRQQgODo4FgAfGV7GpPK2xIHU1dKquKaixN2A2ZlwaSt0YhtAGEt/aHEgNwsIHA2v4OH/zcKQ14Sl6bqntY7C2NaLaUF7bEdbc1ZNIGR6YQIHAQUJpPyv+I743drOt7atnfPC0LjVZGB+czYNTQ4EIGYka2BZHFwSqa7y6dD4nuyU1PvltfW9m5+AjXlwSEcIBklfUTAuIHFIBlNLXeb27bD+iNjH3rCnsbq7zs7T2Cogbjh4SUpbVTw7bHd1SkQFFzPp+vHp4cjD0pbs4rq0goSakph3I3cwCQ4AWF0zYnlwPkoZXUa80vG7uovam9vo5LuL9M3FgZZycyY9NEIGRRECeDY3NAwaBFcbuO3Iq/yEwYvJn/qP4pKCmoCObSp/bXNKEAlePjVydWNYURZF7/3j+qCZxc7F4uXz6OGN1ZCjzVsgYEAQdAoGBCs5PzZcVQEKAaapr6SdyZGYjfni86urlISanCA0LWNnVUlcXGEtcDNzCAQRGOzizfrx08vD3Zeo5O6+z4fZ3YkraG9mQHMdaxAwEGAEHU5HWxHi+rmw/puT1eDylPn+8rHR25stfCYrI0QXRlFgIy8hPU4ZGkVdovb15oCNy9aD+PH39IrXyNaeaGphah0FT0ARMCUjLDoCT1NBoKvtt+rM2N/f/Oqcua+Qy5CAICN1YWEBFBVDSztpLzpfVE1TUO6z86bIlovNyuqv7OnfxQxjTiEwM/qJmg9JB20gbjdkR04ORq7856j9iPiP1+jl4LSpzpmC2tNnJC9IBA8YZBAsIj0/EyA7OzG5x7z/ycva0M7r4rDl4ZaRiNpjJ2BxNwwCXlsxYGc3YFoLThbw8+f68oPc0NTKo/D2/5mI3pyDJyhoew9DEgsAYGU5ZkJHHRAQ5unl/fuEl5aZv/f0rL/LgI7GLmd/YnRLRQxZaW5gPzMKBFlaXa+h+rOAh9HCk7ao8faAwcGCmHx/UXZOU0tbSH49fzxhVlJWmQQYsq28RyogmOjkof7/yoqH1GNoe3toAQZQEREyKCwvIQYeAxvr+K76Yx+Pksav5b68joCanYFjJn8wPQhMQRY2Kzx1cgRFXRPv5urk1fDm/oSg+7q0vo+Pz8SLbmh1fxwVREUAYTcHckMdHBMPpqewzNPC3Nvdrq/z8aLClqDTIGx9dndJDh8YBCUuPzFGQg9d+9Omt+rKy5OUnZCktfnLlNSMqi0ueXVIRkpSH2hpGDYJCQsRXqr3tePLjo/Y2rG98vv8qY2AjiFkYHF0SgkbRGQ8Gn1+FQMCEQukobbXw9rEl8isv7quw8GS0oZQIzBqQ1UMGR4QKSM9OggeVlL/revnofGEkcqn6uP0jYuZhp9va2d6PgoBZRAdIiwteU5LNBYHp6W4uJ3cx4iB67+658LdzNeBJ3FFMD1HSVlSLGt8dR8BARoUo6Hysubwg5DJsq6jsP7PyKGKlmtoNjJfDUtjFmc8Py8SSkNULej+4buJ2pLB1P32/qy32NHbj3A3LDhqTUlSXn90OzQyABJMXfvsoPj904KZ2YL4reivkNfMg5o7KGh7D1hdX1AhdDJlExcPG1zg5vDr7JuP1c7vub62s4DO045ofGNzIAZ7QFAtIm44OQ4IFApduKnv94rInsLNoL69u4yNk4rfJiJvJQ9UXlRSfSprNHcHQwEI5/v+9vPA0M+Y4+Gy6vKQj8zFdGMwMWcBTV5XWCoodmZfVBFZDvfj5fPFzd2J0eyq/LyNhoeZgH8oMzElBM+0qGA0bHgqAVJPDuW2q+3xjMqcybKouLy4hpiNhN0kJGknDV1UCQIxKG1+GgpHF1Pn+ff41MLKnMH/5aW84dqZkMUmcnI/fwtAUkk1BCAxNApCD0Wv8+qroNfOj5zEpOW19Y6Ig4OobH96bghVExlJIHVuLQRZSg5U4qTk6+Pa9umS7KW1ubmRhMyaPWp6YmlLRRJNOTcraH4BHwUMEOivtPjOyMPEg+etpveMg83WmWV/czRTRFwIHmcxPXA+SBgdH6v0srfqzNjf3/zq662xzIrcjHMpY3RsCwJQBQEnYzpyS0caGBfrpLG2gYqGn8Dm9faigcmbhtJvb39/a05QFEghOzUyMEEXElT0+6H6+MrMxtH+9vH3r8HQ09qCOiJ2blZdX1kAJXZxuOH7TQtBq//2p4TchtSB/bruuOOSicrfeX9/fnoFHA4HfClhJjtbTE4K8Obgtf/Py8LLguSsofaWiJfVwGtjM3tCS0pFTnhkISkBCVBSRuKk4f6g34XVlbKv/qCxl5ib0zsq7ZaOBlYOFjQjcAkyQ0pRWEPy6bT4zsjDxIPn6Ki7z5iJ0Ntqfn51ARpPG0wlZDU5JlkMWgvkp/T255uRg4Xjqbr7qJTJyNthciNxcgEQExpCZHNwIANHABFB/KTytNHOgdLcvefz6cnMxpuJb2YwJX4IXVIVJigpcXgLGR8WoLXuuO3ChdbYuZetoeODys2LkmdheCUKDUtWEDUqfT4AET8VCbq7rbuLhZSXnr7uqrnxlovan2NwQHRqC1hcfCgwKjJ1VkIPXKm7oebxwsbdht/87/by2MsSd20ncS81VUwRalNka3J5XBMKS1j467/o+eLMwNr9qey1q5GYnJQhfHZmeBkJUEw0MyE7fkMJHRkM9bC2+teE3dyepLn+rIiNmNTdYGpmeA1XSAgeZzE9cD5IGB0fq/SyqcjMycSG7qq6oqmLhcyZOiUsaCAMXlUGX3xvdnJKCFkKQvHorb7ayYHT0KWivuTd2pzK2z95YXlxBk5YWjE6bXU/EEEHG+aqsfv8zcWOlrO3r7vixNCRxJ1zeWB0TAlETk4gcXE4FBwbGROmq/H1wdTdiJ6iv/y1tMKWldVjbX1+cBoOE1UvanV0fxlJUh3h8++46crOjJqbvaH8usycmZPXeC4tPlpJAVdZKSI8ZkImChgV7apzBAyEl5b2o6K197+BgZqUI3UzOX9RX0ZWLnkuKGBMHhVGQ/Lpqr/ZyJ7S06Slv+fc1Z3J2jh4YnhOB01ZXTA5bHo+E0AAGuWrrnUeNpCcybGpoej63ovOm3VzenpyA15ISFp7bzYuFh0fGVyl7//70tuSlNyx5r+ywIab2IxxLmp5MVZfXVYndnJ3MQtOSVij9a22+cXf05Swtae4ud7I1YOTLmw6bktNGUoce2goO08YHR8Q4bSwydLOw9CcIVZC+uXEoJWQZylgZXdISRFHfWctIykQBBxHpPzvu+zRw9zdgKbxp6uDh9CUkmR/YCcDSVobSS0nPnteQxUUUvP4+PX68sTY1/3rpL6yiczR0WYgbjhtVkJfXBQuIjl1WUgfF1/j4/C7xsjE0py48PypkYmByJ0+ajxpUk5DUHktLjQ8YAZRBwvj57D08sTfwIfjqbr7qY2HntlhciNwckdPVRoBIyMwPgZJVAwZre296tPBwtP4qq+3vcma0tqPc2t/Jn8cDxZGIjRseCoBUk8O5bar7fyKzt/Yqavz6OGR1dbEjHZsejtBRVFETXhuMi9MHA4RX7ppEgqciN3F3a309uK/2o+Bj3ZmfjdqXFxWB2MqOiIpCwVQWePr7+bvnojQzZKrpvS6l8SAm9p+bT1qTV1cCR9kJDx7WkVMCF6j7vDs/ovd2M6x9PLzpYnEktR0YWs/YAYVFkIhaT45KRBVUygT8/60eT46ko2ciK24v8GYg4OOJnYuNkJSWkFTLXR1Iz5IHRgO8bbwq+3Vzd2aoqSupa7ZyZOMLXNneXABFBVDXmg9OC4RPRkbErqu5/PNxI+UlqPjs/eQi5uGqGpmfTElBENbGycnNHc6BBgWWPy0oOXtxcWM2fqm4KWwlpm0lptjaSt7HlpAVlBlLz87EhtSVBzxpubwzMuO1Mnur7+8uoLPyp5sbXVzPgFXVBIgZj86KBcuHB3l46fooIzZwdnR+K3u+cCUkIrCKnh3JB1cSwgZPyp8PA0GBxlBvrfj5OPHg8HSpvTt6rPLmYCfcyZ6f3kbCRADIHssICgXBR9YC//98aaAy8XDyqqi8fqChI6Fjjkpc2wNSkUVXTZnNT0zRE0QGvbovfP7yZGRnrqk57fzkYKW0H0lLmF1V0ZUBR0CKDg+FgxQAxbs/7Pv0s3dltyqr/KtiIKHh9xzZWNseUlBXVRgdxQyMgAYFkau6vri6cvFjNm9++GhuN3W3oyNOG09MgxDX15QbW5xcxxUQAIf6+/rp5yI2tTer6/vuPOHw4WOZ3tpRHtJSVBOLjoOOjFMSVwY9arj/sfHy8XZkrOksIbAwN6RkXdJcnlJAAdTAHoufDgEAysDGe3+3fHs0sja3rDh8rSyjI+Y2C0gJz8gGkpBQC4kbjMpDQkFERDopfW3x4DL1NGrv6f6j9XHl9NbeX1uU1tSbmFqZDcxMQJFXBq/t/G568zIjof8reb/5o2N2thuLnxwaFZYXwNcKSMnL0IFTVYQq+3398jGlN/b7Ki9t4aOnJjUK2YuOlJHRlEbYHNyBTsIAwUfoenr+/TKi9TLr6ru9/XIwp/WinVoZnVDRFVLQitiNjcAAQMKAeGiubHMmIyV3a3qsLyyj4PL21ZhdW4+UkVfVmE7KjN9AR0XC/mn++b50YrQ1tvmuby+woaem5draTN5Tl1BQlg2ZXBjQlhMXFWq/uPh5MqS1cyjoKT3sc2Y25ItfSNzNAhQUEB6KmAgLwYYXw0W4qH175jAmJ7D7PayrYCBm9aOLH86cQxSAlAVamY3fnkHGhUS9qH8qOiL39bO6b7hpLiCw5TVIm9gfDNCTlxRUTVidnAHQFxWTqy25bTTzcLZy6HjpPeSgojdmi8oeXZ2CUxQVisnI3h8TglaU6im7Pfpx8Oakeju8+unnsPY1JEobHB2S0cNWxIGZDJ2DEZLXl+q6ue00YWC1JXtvLC0qoPEiZhjfXN/NkQFSFstLT0idUYoHQzs4+Cy6IPJ2dnRoaj0uoaEmZneLS44MkAGTFlZITcuOElMTFxSo8n5/fzNjcDTp+mWvqyAn4eVc20vZGhITkEXYm4zKHQCRhRWG+f59ejH3Z7C16Hl+vPIwcOX03V+d2tZdEtZWSA+PioeCgFcWazo5brzzvfX27Wp4qavjYaXpCIuIHNzUW9QW1duZTFmXAxeFhrhyeX/z9zj386wrry8ks/MlpBqaXo6Mw4FHQZ8KCMuMAZMFQ/v6/v/8sqD05Wl7rW2s42ZhdiROyV1NX1bX0BNOTAIB0hGCR8T4KO6+J2Zz5vJqq7s5eKDxN3Aa284NnAMXlZONDohbT4LBQENoOuz+7LW2dTK0aegsaeeh96Sk2Rlf2ZdTQNEADQ5PTQQE0YbUvP4+PX6mc2QwK608L6txZyBk2xtLzJmSgVCSykqKwE1DwZOWgvv4fi7gILXn9DrvKGzjI27n5FoIig7V05aXRwtJXE9MQJNEBr27LLqsPnC/tWvvKel/ZCE0rhEQy49LQ8KAhQfcGF0alCL8NFXqaz86IGYj8LS5Lm3tI+Ri9WMcWN8fT9IQkMBInMwGj8OCVNe+uTytvyE28DQraLuuLmOiJSK3y5dUjtgXFlbWiA+enoHBx1WFev8svTBydeV0aykpbCs2Y3YlSx5Yn5zQBMRGHtqfnR0XwIUUe7y4vmhntjNxJ3k8OnmkMeEhZdoJDozU01bQ08qcDE2DhQaVRi++vDq7MPk2s/qu/zm7szXmpwpbDErLQINVgV9cX58JwoMWVBPp7Cp/97V3pnOt6W+v8jOyd7NJTYvfVxbTVpSJSMjNXcGPxEW7f/1t+7Xxd7f4a69ubDEz4mVfSdgemscShgRFjI/LSALD1hHQ7ix97PA387fyeS4+q3Ij8LXlG1jTmhtT0BRGW4seX5yC0IDCOjl6qu8lYfcl7C1p7i5t4OdlMJoeHh3DkceR0wsITYfDwwKEkDq/v71m8LCxtm4qb62rJLNlcZ6IX45bldFXl9nbiF4LRYCHxzU7uL5o82EwcrWq6iAso6FyrOfcWk9dE5fBx8RIGJpOBcGBwFc7KLlsPeKj9zVq5agpbeIidHWZCEjbX9dEV8XNTQrJ3IWAxVUHvK3/bOLhcXZyqyg6bSEn8+ynHBuOngJC0NaUjciImI7Sh4GGLir8fPzzMKRx+jkofitloKfnD0mPjlyDVpDUVIjGT03DlQRWxCr/OHzzM3q2Nmhr++3lYuCiL8raz11MQIFFhUjPH0mfEkNWlT36ePj+I3W0di0pKb9vcqRkJSadHk8OWFGRVtaK2snegIADhga4avi68nKy5WU4eH5ufGFgpCefnVxPjUHBR0aAiAqNTZEHxocoMHn5/nQ3t7K2ua/obeHmtCRkXcse3RId19EVCkufHtJGhMQEPDvsfnzw9/Ak+Cdqae7xY3TlHRlbXNvBERUGS0oICE0EEoTHQvx6PH1gpmQ1tCh7OXqz8rGi9VlJ3c3REZaVE8mPn8oLQ8AEVKroLm+ssSCws+kuraFuIiOkY1vdU95cAsIH1lKayA/AAYIBBZT9eDm6fyKhpjbq7mXuIOPxt2ZPjR0Nn5CSXFNJyckGzcWGBYU5PqntP7IwtHS4uvm/fXe1JCLhmhuNH1XR1NDVyoieztNDUYNHuvl4e2AyZOd3e2Vo7eolZGIoF8gPnF3S0gbEiB1cTdzEQIWRL2667y1mMPXkJ6o5Ka+lpyCmcVmY31pVQhDC1NqPiE8EgkPGBn/9v+2+svM3de+tbX7rNicgZVseHs+cwpbXkwzeC5xJw8XUREMpt347tGJlMzQ67y/r5K8hpqROylmcExLDhcXJGI/di8KGAcv6+X8vqSH29rOqeihuf2BhZbZZGZ6cD5eBG1WcikjIDMRSQQYXsTI17qJmYOWjujr5PXA1twXfKUjPThwVA0EGzYmcCU7CAMFH6HY4+PuhMXdzvqn9LWbipyU2Nt9YWk7ckVFRB5tLT82QQQGBQn3orL/z9WO3dO06ry5sZ/NmZRsfXhkIUUAXRQxJDolYkZJSFuxtay8p8rMmdbKqqHp5pCVjNXcODEuaA9cXV9QbGJ7KwUTGwcSuOn+9uzSjdCGsqiipLusgofScyQ+JjQfQlQRJHlzZXpFDk1FSbak7/LEgZiHn/jxt6adhsGGkXF4Oz8GDx4UAX4uLSQ8CQMSEvDkuPfMwMHczKTon7qol8uUi29qLjFnT1cRVlAxcjRyS0AEBQf+5fWykZWSi9ntqqW4iZPOhtR3Ink0PU5DXWQyJSUkfEkJWlOt5qHm8dHYj5jx66D7rI+fgqyWamEpdVdFXBtQazw/LxItARIY4Lb87MzLldDQsK+qu7CInoDbYzRoP3ALXF9PMm5pOHMUBwcK1O7i+aPNhMHUyrWZvbeO07SWimAifXVWAAYaWW1wMy4BDhpVE6v+ufCzhMXa0p25vKKtx8CW0y1zbW8nSQVHSiU1YCA1B0YQDEXvpb2318fE3tL/oratwayOgpgsajs1TUhAQVQwcDV0LBQOTlnj7f3+8IfRmpav5r+6qJfW08ksaSBlcFZZZVFTKnA1dwxHABsL9sn9/sTMktjIqKevmsiGwpjSJyIzNn5TAEUZbih5eSgEAAYfqPXs9+nHw5rY6byvubmRntnavGl4eH9MDkQXXS0tPT0ESD8aCPeruruLj8+b36yutKSjh8Tf2SsnMFR2QE9YGjUgKnYbDRkXCvTo/PC80d/d3czmq7upwoGenqF1fnp3RAYNH0A5Lj4qBUcPGRnx/rm6y9/d0Zuj6b6is4eJgdpubi97ckpfWkpgJSshKwYPH1hPpuz6/4KfgJmc7LH7u82NwZKccGphfFQJXllJMGN4cXZPQRVV8/z15ef2yd/forygpJyIh9rbW2NvYX0OTlRUYmRkej0NGzUWHe2ku/+clsqY3KCvl6+FiZq5lXB+dHZ6VAUWWC4gMzx8SURbU7y27uXkysiS37WpraG1jITZmdNjJG94TUdDQx4rcXs7TzcdGQr39+7C/Y6A09WtrvnwvtvTldV3YHQqIxhJGhN6ISl+fAoXDheu4uP09c+XjIXNurGx5t/UndmLdmlhNFRBSx9PIT8nKw5cDRoS8P6x9KLIg8HIp7u+trOAkI+UL21id3RIV05cey4ofX4nLz09K8OvqaafgcDF0ai8p/KBrIqamHBuMj1bS1MKHBciNDEtRgEbHOvnsPbwwYzD1bO8qbmxjYTSmHJiLmd5TkVHXVtmKzspQg4fGBqr0P3O2NjKlvmBh5eNpceamtxgZX9+dlRAGltrNSx1fExCBwjo5ae/s9DE58mworyWvZCP2dHWcn9tYENeUV5KZSN7LkkAQ1QV6uLN/cXLy8HZsOj9vfbKlpaCOGQ+Ym1AXh1PKCxjNyleAlpQrOnv+PmZxsyRk6e6tbKWyaCFkWhlYH8PSUNaFR84ei1ID0IWUOajuLTsjtmc0+6t/LL3zMCd1GBsYn9zG1gaTWgvYiBwBkNYQhHz4fi3zIfAxdeoqa+mj52Dmtw5ZTxpUk5DUHktLjQ8YBVFAFPqpbL/8Mzzw8iopar08YHC29djZnp2dAsCGRECeDYpcktTHgIS6aD9tNHE2sXBuKWntYzG05vSc2Zka1pISVFffTp4I3YNQFES7+HQ5vHR2JCVpe7n+7+CnpKQ1y4lPSYcUk0eF38iJjYNNUEQFOj/9+uI5cHa0Karv/H2yoPammZkeXk4A0oVHCA/Lj8pRB1cC+Xq4eP5i9yfytqg5bL3wIGentFkaH5zT1sAFBYhYnt3AwYaFhSrormxopjWyZLr5bH5gZeDhIk8aSFJb0tcQRcmLiIhORFCWRlCuOy67svNkYqDoOX69qLAjtqQKi86Ow5GWxhNYWYwcXEQDBgO56C88evLz8fTrqbnt/Sfh5eNIHMzcXNAX1xdUTJjJS4HGwkkG+np8O7O2oeUk6W+/62Php2B3ioxZWRjDlkJXy0qJTo7CxhdGfPt7uL44cfX1KWpuv3+h4OH2tYqeTp4TkhDRHAkITZnQwkaWwnr6uHtgovam8+mvpCsq5SElo52bDg1bEpAVhhtajwiPBAeAVupq+r6/9bH1NbL6K+7v5vHkYeOYGJ3WUlBQ1IVMGJ7dRRJGhAE98n+9uvDw8CGo+Wk+b2JjYCJTWF8YjNFT1YRYiggd3VPCR0dHvTZ/fbHxsXDlrHijLLIxJvYomw2YXxUc0dYWSw/JXB3TkRJRfan8/v/1t//07K84aS4iYSEnCglYXs+CgMdCw12fX0mSglUDBL4rPf/zc3b09nq64axhY6c1ZBsbXh2P0VBW0gnOnA4MEUYGx+h5uru6YTbwNCjou6nqY3EkdGCZWxgeEoBUR5FJCA2KBVATTUS8ef294fTjtHZr6+lvf/Oz9/TYydzeHpAUE9baGNtf3NEKBoc4+yu4fTGivfRzaO+oLSQjNCFi2lpYDQDAVJLFSVnN3cEBhoUD+b+v/z6yoSdkuvg/LbwlJmWiHhbanp4R19dSwErIn1+OA4QDB6r/fLGgICe0dG3ibK5icDHgsA6fzx4RENrQ1ktPh0xLBIIGh7wobL08szP2Jjt4Of/4NqQkZZudHo1eR5LH2dNKTondQQAHhNWre2upMCG2t/Z+fbvrc6Dj4GdcG9lNm9ABB0AJ292MSsLDwcT7uan97HQgsnar6m9oafH0JTUjH9jd1dNSlsNUDhxPmENDRtWErnuuruOxtuYzKW88/Hkj4vc2m0ga3gjQUNQTywtISJzBxkXGPTiy/n5zs/fzJfkqb2twMDcmNBmYHJpUmZOW1h5aTMsTQUPFhej6+S178Dblpet57m5sICeu65MRDIxIUBCRBkjKy8mL15IEA1S5OLsu8PcncDXoanx+pOHg5PAJm97eExISRccIjg4OXILAhAa7rSy4+zQyZGE/aq6oqmLhdKabGZ9ZiEBS0QVR2RtICISDE1VHPD45/XPio/Xz62q/7WBhYuZwSFJfXdsQw8K+NV1fzUrERgcFL+056S9x8fTyrP67LSpzp6YjJNjLyonDUECCQIhJSV6AgQOBQ65qfPsjdfI19Ons/Pm48mJnY08NT9zd1MSFBYlJywjMAEFBlfi6OrsssLawd3Roo68so6N2JjXPm98dFJcD1cAbGJvZxsIQBYQ4vni1PbV2ZrJp6S/obvNzpyKZGYtPzFBAxAXITJjJToBBRUBXarivbXLx97SzI2Ynpbcys3akywibyJFDwwbXTZnKXpzCURaGubt1eH7y9j/07K8qri4lsPQmmxubX4+D0sYFFBoLDA/Jx8VGQrJ5eDuxMbKxJXmqL6wg4zM2dR3Ny9jawhZVUklLCRqY1gDVVzgoKbrtI2H1tajsqOwspfEkJybQ3txdVZlWURKICI2KElKBBME4OTl94KLhsGB/bHznayFjISeIDQtKmoLR1ZDZ24geD4ICgEKzO794bLAxd/M3q+jp/PAhoCSkCclNTwATEBVSCkuPC1OFhsQDvrZ9PT6xdnbyerr/qC3lcGRm2JjLT87AgNGFzMzISUVDgcUHBbn+fHL0MbA1tmkuLq1j8DG2pwsIjtkCQsPBRU+L3l6cQcYWQ/r/fzyvInDmpS1rbeinouFhpxuczM9fQ1fQl1NKCw5Ph4VEVkb6O369t3UjePOobnw8MvFzjdLI3p+amtAQlhSLWtrNDELHwda8rXqvr+KyseUsKGsuriayN2X1j1+OnJMR1VFdhEBH2dGVB9WHujq4eqdhc/AkbC/s/rhqoKVn2tnd/WegxAcSn9vYzlzBwcTCvPL5+bojcvV3JfkoqS+jMvZ25AtJT9oRFlaU04wCjwwDQYaHBPtzOP58sOFnJPr9O6srcuFnZRkekdCUGgWVEwuJDo8Mw1CEFQLr/b39MzaxJfb+Lu6tIWHmNiVamJCfwxcSFhIeSR9KixcAlgI6+7+8vqf35/fsqv1uvGws621QUVLWSZKVwxdEz0dbilOHQgZDb/o7qfEhszX0afjs/WUzsKAwW1vZjhMQ1kcT2RvJHkwChg/E/L86vLh2PDvkOyk8/29z57dncIkLz0mHEkMU1czbDA2ABscS1/07b/ygIPV0MHh9O2rr4eDyt95aG0rMVZcUlR/dC1oeR8fD0Wv5bCps8fDx4bf6r3p84PFhNubOC4xMxwWTwpZLT1yOgwGHQZBoevkte/AxZaF5rKxqv7Zn56bbWQxMmZQVhZCJXhsdZ7USlNTGryvtuaehsPa36mg7ebOjIaAw2QnfyQIRgJBECZmP3FiWA1IH+v/sPTyxN/Ah+OpuvuphofQxzxzb3dwRhQNTFcjLDBlXh0CSVr+7e6mjtzdiIHrv7q8gYPQyYhhZXVhIQJWQEc+KWwjLFtQBx6h6+D67tTK3ITi47W7ocHU1YOaezE7b0YXDBhKN3IzJ11HGxQS4PKspY/Tz9fQpvTt97uPm8qbLmEtdiMbDlVPLys7PzIKSU9E9P7+8PPFit/dyLWCor6QhZGOwWUwcTpCRE5FTnlpMyxNFBcYXqPu8Oz+i8PRzLH08vOlicSS03wqL2J0UEdXBGIJKyIvQwsfHF/i6OD6y8XDlYDht7/ygMGSytJmNXIjQBtMCxg4Jnk5dhtRWxm86bzxo9eC3MqkpuGlsY2Il9EpKX16blcCGRBeajl9ZlwdXgEf6fn2t8CG2dfRsa778M6Kj4XUK2ssJn8aWUYFfj00aXoeBFsbr/v2+7TZj8nM7q+vpvSCxIKBki8yMzseWkBWUGUvPzsSG1JUHPGm9veCh9rcyK+v7PqRiZnUlGwpXkRbeQtAGi0hPCJ9EAQWGPmlsHcGA5aey8+no+r82MvSit4hdzt7HBZUQk89MCA8FBIcG17l///768/C2pn/9KSuroCDldp3bX1ydEdfFB9iITs7PxcDHhZdu7Dg4tLM39Ges6iQsoiY0ICZR2N7aQhRS0dYKiklcD5KH11SuKuy6v3E2NDS6anmra+Bn4eLbiUsaGEKAlAWTD8gfSZeRgQTQLn497rCxM7FzvnpoPvew5Wb1GIkYGxmCh0dRn5mJDNgWRgXWuLk7uXumYnAm/7jtbv0gsSQjpgqPz1mHgZEUwB5ODd6AgQOBQ65qeC7noPV28mvpuzlvsiBgIs9K/KXjQcWXRIgZiMiLUhZWwS8qPrxov/EkZif5vGgv8KKnJaNdjExaAMWC01TbCp8LwELGxBQs6PspLDSyYqHtq3wtLKEn4DHI3otKDlfShxJMi4tMDhcGVkZUfbj+LKYi1I3Leex7/WVjNHKiWArcXVBVF0IHjFob3wkCBgYF7+08bn6xNWMmCNIXPTnl8OT12RmdzxhHwVFXAF6YiApXAlZXlDv4/r0iYqNn5Gy9qD3g4uBhplnJGJ0dkVIHBJsOj8lKk1EWxut/Karo/ffwNCuoOah8o+LgoyDei82MgxFX1RfKSkQNQwYDgQYrNjm68nJyZ3d7aawq6uakdbZKyA5PjBITUMSaSlyaD1YHwBHvPPqq7jYwpnZkbW0ufKf1d+DmjswZ34BS0NXTjd2cCtCWUoOEqvrv+vO0tSYi+u07PiqgdKvlCEoLzYhUE8SWiwmPSZhQRhTRlv947z6jMvhw8f6rf24oIWb2ZwqaUNtWR1AQFAvZmNxIkaP8umirev5tsSCwOu1sfC385eqn40vZiBmTVdTC1ZKKiF4aUsUTFgK4bKv7sWIzNrct7jv+5LF0NGHcCJwNm1DTFhSOCw0ej9LDxsb8+/q5bTZl53NpPnyobjdzoqU12cjeHpRXUxLHGdlLmZOHAtIQav/4KfAjoeb1qyjv/D9xMTYmT9tPmRyTE9WEmhmPTMrARkBHKiuoPj904KZ2YL4reivkNfMg5o7KGh2CUkBUlwwLi4lQkVHCECs/vWmo9LJip+5oIu28JGxj4ZgJntrIQtfVgd8MypreBgLXwsG67K2p8CXkpzS7a39qZiFxt3fOCRwJwAFBVsUImQgLCZKXV1QoKnQt7yOwpvb77i3+u/N0dDbfTshYXgddl8YH2ZtaC8GSRMbH/b/rrjTipFUP33vqbfIhsCGhW41cDZuUlQeWmw5KG0/Sw0eDq24puuhi9/Wh/yzqvW/j4uCi8IkfzYlBlJRGV0tcz1yAEYMHlG2oqi7gtqSmsin9O33q5TTlNIrJ3p4d0sEERhoZDZrL0oEAhzuqej88NfPw5CXp/DqrsyBkYTWZCJgY0wBBh8TKSoicUgGU0sdrfno9baPlsbetryiub7ZiJqMIWtjd25XFhBJJmo9IDFBVFUDHK6v2v7WidfW16vu/7XJmsGRnG1lPjgQDgIXWyIjP3h9TUUGVeXo+fmgmJyMmLGnvPTnxoWXniIuJ2g4WEkZGnsnNHZ3DBwcG0O4/r3+wNGQlF9EWPDjk8+c25hiczgzN0hYWFdjdCJ5OgQVIxn1t++2odfG09Ws5625vZCZzNrbfX86f0NQDgoOem4yL0wdH1RHpurntMTJjMie/e6qqvGCjI2rYX0uJyEHBxEAY2oyciYKQwBX5Ob3xf/XhoORwuPx+6iPiJybwGU2MTgIAVISRidjcBcFE04CE/H++bqzyYXGlbWmoqO2zMWO3nprJzReRVhaG2woZidyMUQSGQzupL3mhtLTn5yNo7++iIaIhd8oZDprDk9BWVhqYyx8JAVFVjbt5/XuvszCkZau4L34uYGbnYppc308NV4WHlxWMHMINUJJUFda/vW99sTGyMLV+6vuqcCEgpSPcDczeWoLXVJMIzs+dWCH9tNaz+f7tvLKi/zqheC99bCKmYXYi2lpdWIYCRRMR2shMipJBEZYF+vi/LGCi46Xlb7q+by6ioSHj2dtPDdtUF9DXy8sKjJ9CxlSGKDw/PryxIrCwdKkorjyzNXfh8BlNjE4XHRBFh1ka3YiEE9MPRPv7vj2+NWPmMnspqCysMuAlpRmfGc6bwpKQXYmeGwZCDNKU1MTrv+6+tHm1p6E56CyqZXIjJqSd24wMF17QBUcY2p1IzJOSkgP6rfD4/HGx4+VtaDx6qmMy5GVYXR9KD5RCA9pSz9xey8KV0wDFqXv//vS25KUz+b1k6+H28GBlD02ZXA/RUFVSDF0ciV8WyAnKr2n+/6jmN/amaOrr6av3siD2sFQbHhuRxUfQ1Z7cCcyQQsDFw73trDrgpn+k92uuuqU48mZnMU+fXg3fUlNQEl8aj10YyAKC0Wv8+aru4/MnZrxqe27q4eH0J+RaWh6dEZbARQRc2IvBQ5HTlVcp/HhsL3lwdvIp63y+6zLj5+Vcm1rOHFBRVVNKG4zCTJDSlFYW/3gvLye3diJ7bGjsLHdx5uewzh/ejlDS09GT35oI3phNxkNR639+Kmi0cST2a2pvKXgxpnQx0JydzX+pbgRa1oqIXS54NBMWArtsq/uyYjM2ty3uO/7ksXQu5l3KvOaphoCQFN8dSQ/YCYAHAnk7LO56cyVlZW26+ybs42P39rTMyRpR0wJEBceYTcjckM8HRcT9+rx7cnIwMae767/tLqIioCTLit+cmlAX0caJyE9JSlGQg8l7qeutbyH0dyQmPq5vOWmiISSwip4eyQdXEcIaT07N2VPEwZLQPfir8vryc7fh+29uOnikYTTmW1pfGUgBlkQBwEqISAyF1ZeDBe4seDzgsrc1s228fGow9asnpx2bHdqHAhaXQJkZjN0fSgCGh6i8PXjsIeAhpO8qLL+vMqblL1hc285aAoGQhZMJT87Nw49HwdDtfG6s9yAyprc6pS/spTOk9zVKiM9eTFXWFFJOxo1OzsGGBwIwOTjvr//z9PNoeq9tq6+yNjWmWl/UXpBQRgfSnhyJ3QADAszC+Hl5tXJ1NrQ0qa4+fq8ioSXkCAlOD83GBJVTy8rOz8yCkMTUPvu6L29wtbNmZen46GonpWrqtcrYHZ0RlxHH08hPycrDlwNGhLw/uq8pdKBx8Ksqpy4vY7WltZke2wsc1kWXwIsIjp1M14eWVpR5/i56MHbxpWX/qW18sCHxo2SOW99elVKS1tIbSkjPT4SCDEX5+T1+eqNjtfTt+rm+rLKiJ6Yc3RAdHFGFxNZSmsvNTgJSRECU/bv4eyDhMCYzrCyvrzOnaebmGZyLDosFB0WFy1nOTkwAB47LszEsrGhwMLEmaOrr6av3siQjdJkYmw5AltfW1t4bjczAAQAEV+k6uDwwYrD2tiipuz6q5SYkdk8NXJialFDXRoiJC4lLllJEwyt/6y16Nra1IWdpLigr42H0tefd2VyN01JTVNReWkRNQ8UC1dCQB2tt/3T2cDUrPfsv+zFj5+bcnsyNHxRBkZQNCsrd2JfRRlKQbr9tPjOyMPEg+etpveSnY3Uw0t7d3dTB0ZQTiZqNzctRhkcEvGp5v7tzNiT1a+ktu39kIOXkHInfXZuRk9fGFY1bTo0FkkTHx/r6/b+j5SAxoP4r7uvwISClI9wNzN5agteV1dgd2x4OgwaTUau7Obgo4OH1tajsqOwspfEk5ebfyN1a1JMXlN9LSU/PkkHRk0e6+Xh7YDGk52V/vSqt/GFgZWIcUV5ZGoLXlZXLj4qfn8LGxcXoq6i8LSK14rMl+Tjta7PkdLbkSwicn5FbVlTUzAHOyoUAgAQDquo8vT2xcaWl6Pg/LjwhIiXv3dtYWJRTVhGXC4iPH1+AAYYGxSkobzvn5fLw5CxraG9hJzSy8BrLTR4CA5THBVvLj47KgsIGg+s6PTz29PJ3c6NobyiuIqOgNEibGtseExdXxoTbjlpZRlLNQQd5Pz2uJyVksKTr66r/8aIwJaQYnliVHZVWRpYLSckNjcLH1tY7vjq+L+NjZSRtOm9obOTo5yVmmJkdW9HeUJYTiQrMi4IBwFeVKjqurCJ2oeZnfPjrLuwiJ6A23E0a2F4Hw5lWy09KnYuBxkXHO6lovjzzsyLmvKpoLG1lpyd1dJkaik4YERDFm4wJDEyE0VCBAq5qMDt/srEwMLg5aCl5Me8gZVsZ3tzbwYHQkx6ZR4gPg8DElpT7+CuuevEwMXRs6W9vcOV1ILVJiVzbA1TR0FQJmh9N3ZIGRED9sr/+erAwseH6anho66BmZyYbWJyaX0NT1xZVioxKHk3GhUFXKynsbpjH4/F3LKutvmThJyQmW15MzRrDg8aWjdkIzQyR0AcU6/h4fj41uPm9Iz6r/upkMScmY8uJTx6Dl0ZCgAlcDczF0gMGhz3+K+7wdKDxt+x6O/ku4+bysdgNzRscA1NHVQ9NG10dBlXXRu+u/3l/c2UlcPR7r6PusyCrYuCZCJ4M1wMVFcTJT9te0Cl2VUP4vz0/L+EhtOTo+exo/ffztGHPSd8ZnxKFQ4WJC44a3gYMVMOGaShtvbNxNaVkuettfi8xoaYnmh+dnxTD08bV2p1MWQ9ExkAFOyp5O7uwJGR2LS8u7mzxsuRlWF0fSg+Ql8cVVRmIjp5Qg0RAx+o4+P/z5WNksawtvDnr5eLm8AsaGRsa0lDClt4bmwkLgQCUxnt6fzloIbKx5S0pqn34gFqYsTQdX11dRwOTQsRISUlZAFBRlgX6+L8sYKFh5nT7bukva2fvpGXZ2pkeGxkQF8SYxMrNykFRh0J5enTt7WNzN7K+qeuvPPKnc3Jiittd35kXkpYSQgiIS0FCQsHVKHp/fH8zY+Yk+rg7emlhoOdiXUoYSt8Cl5BYmszYDE9FwsCHQuo4uT+zPSc2IPtrf2vko2dmJxpbm5lAnJdUE5hY3p6f6TaVFmp5775pczKm96uq7q7uIqf3Ih1YnxsT0ZGVFtLKT8VNw5BUlkf8KHx+8LDgdnNoaXw8M6BgYe5Yml5MDdHEApabCo8Ni0WIBoJ9ab98/DL3deR4qi+sLLBw9jR02MlPTcDXllZWio7fTIPASAGGOrY8evFwsDJwOK9uLa7iZrak2xgX2d7S39QSCQtIX4zSgBeF670+rnzioPKzM2/tqe+kZqZmJBWeHxoQE9KGE4hPxstBQpGVxTt4/fH8NbI2pnug4OYkMufh4hoZmh/e10DSVJ6KWA+cBAeSxZR9fm498PL1duEqrH685yLjoKebCNzMFtaQlpfIj44NzFIBQYe5LS/y7DN2N7W5efhoriXn9qVb2RvYXVMRB9IXjIlOjoPDFlIXOPt8O7O2oHeyamn8OPCgY+WiGx4M2ViDwQdEm4mfjQyBB8ANuj7+7j8wM+am6+3q7v+ysaU0NZ7JTx6DHZCWEk2MC8BPEFBEBTq77qxwZqQ1JK2o7Xl4tuZ2p9jfXFke1ECQFkzYWZ/dE1CWwTh9Pf7/4PMxNbcsqS7tcK62JbXfnhhY1pLQFhOMGsmZAEQDxwIo+S59LfOgZbTrKCPpLuRmJqUZnstPzQITg9XJTBuGD0TQgVWG+nu57XPyMCflqTx7YGAxoaS0SxqPH1BU08dFW0sNyQjHRBdVff67OvF+PGak+jz57fzu5mdjnN7ck5BCgRXV00DLDczSkERSkD+7b3v0pXKmNqhv/q4zpKHkdV/dkpFYg8EGHhqKHw6cEFEUVTg/aLnv4jKm5e2pqKgucqXkpmLZWU8egtSTUo=";
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
let PRIME = null, PLUS = null, IS_ADMIN = false, LOGGING_OUT = false, planBox = null, planSel = 0, planBusy = false, planTier = "prime";
let TRIAL_P = null, TRIAL_USED = null;                                 // free trial already used by this account? (hni_trials/{uid})
let PLAN_NEXT = null;                                                  // page to open once paid (PF Builder), else stay here
const isGoogle = (u) => !!(u && (u.providerData || []).some((x) => x.providerId === "google.com"));
let PRO_NOW = (() => { try { return localStorage.getItem(PRO_HINT) === "1"; } catch (e) { return false; } })();   // admin or Prime (hint until known)
// crown: one outline stroke, white (dark purple in day mode, see auth.css .au-crown)
/* Plus: a hollow "+" drawn with a double outline */
const PLUSI = '<svg class="au-plusi" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" aria-hidden="true"><path d="M9 3h6v6h6v6h-6v6H9v-6H3V9h6z"/></svg>';
/* small badge on the avatar circle: crown for Prime, + for Plus (which one shows: CSS, from .au-prime / .au-plusm) */
const BADGE = '<span class="au-badge" aria-hidden="true"><svg class="b-cr" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"><path d="M3 7.5l4.5 4.5L12 5l4.5 7L21 7.5 19 19H5z"/></svg><svg class="b-pl" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linejoin="round"><path d="M9 3h6v6h6v6h-6v6H9v-6H3V9h6z"/></svg></span>';
const CROWN = '<svg class="au-crown" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true"><path d="M3 7.5l4.5 4.5L12 5l4.5 7L21 7.5 19 19H5z"/></svg>';
const inr = (n) => "₹" + Math.round(n).toLocaleString("en-IN");
const dateTxt = (ms) => new Date(ms).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
const total = (p) => p.total != null ? p.total : p.pm * p.m;          // ₹ for the whole period, before GST
const payable = (p) => Math.round(total(p) * (1 + GST));            // what Cashfree charges, GST included (the Worker works it out the same way)
function markPro(admin, p, plus) {
  PRIME = p; IS_ADMIN = !!admin;
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
    el.classList.toggle("au-prime", !!PRIME || IS_ADMIN);               // admins have everything Prime has: same crown
    el.classList.toggle("au-plusm", !PRIME && !IS_ADMIN && !!PLUS);
    const who = $(".au-who", el);
    let t = who && $(".au-ptag", who);
    if (!who) return;
    if (!PRIME && !PLUS && !IS_ADMIN) { if (t) t.remove(); return; }
    if (!t) { t = document.createElement("span"); t.className = "au-ptag"; who.appendChild(t); }
    t.textContent = IS_ADMIN && !PRIME ? "Admin · everything in Prime" : !PRIME ? "Plus · " + (PLUS.until ? "valid till " + dateTxt(PLUS.until) : "no end date")
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
      <div class="au-tier" role="tablist"><button type="button" role="tab" data-tier="plus">${PLUSI}Plus</button><button type="button" role="tab" data-tier="prime">${CROWN}Prime</button></div>
      <p class="au-plan-lead"><span></span></p>
      <div class="au-trial" hidden><div><b>Free trial</b><small></small></div><button type="button" class="au-trial-go">Start · ₹0</button></div>
      <div class="au-opts" role="radiogroup" aria-labelledby="auPlanT"></div>
      <p class="au-gst">Price excludes ${Math.round(GST * 100)}% GST</p>
      <button class="au-pay" type="button"></button>
      <p class="au-plan-msg" aria-live="polite"></p>
    </div>`;
  document.body.appendChild(planBox);
  const close = () => { if (planBusy) return; planBox.classList.remove("open"); syncLock();
    if (!current) setTimeout(() => openPopup(PLAN_NEXT), 60); };          // not logged in: the login box next
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
  const guest = !current;                                               // not logged in: shown, but locked
  b.disabled = planBusy || nope;
  b.classList.toggle("au-locked", guest); b.setAttribute("aria-disabled", guest ? "true" : "false");
  b.title = guest ? "Log in first to purchase" : "";
  if (!planBusy) b.textContent = nope ? "Included in your Prime" : `Proceed to pay ${inr(payable(p))}`;
  planBox.classList.toggle("au-plus", plus);                            // Plus tab: the whole window turns Plus green
}
function planMsg(t, kind) {
  const el = $(".au-plan-msg", planBox);
  el.textContent = t || ""; el.className = "au-plan-msg" + (kind ? " " + kind : "");
}
// next: the page to open after a successful payment / trial (e.g. "builder"). Not paid → the visitor stays on this page.
// tier: which tab the popup opens on ("plus" for the lists, else Prime)
function openPlans(next, tier) {
  PLAN_NEXT = typeof next === "string" ? next : null;
  // signed out: the plans show too (pay is locked: "Log in first to purchase"); closing them opens the login box
  buildPlans(); setTier(tier === "plus" && !PRIME && !PRO_NOW ? "plus" : "prime"); planMsg(""); paintPlans();
  if (TRIAL_P && TRIAL_USED === null) TRIAL_P.then((t) => { TRIAL_USED = t.used; if (planBox) { paintPlans(); fitPlans(); } });
  planBox.classList.add("open"); syncLock(); fitPlans();
  setTimeout(() => { const i = planBox.querySelector("input:checked"); if (i) i.focus(); }, 30);
}
window.hniPlans = openPlans;                                            // signed out too (landing page: Prime Benefits → plans)
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
// Cashfree's own failure messages → one word people understand
const cfWord = (m) => { const t = String(m || "").toLowerCase();
  return /cancel|drop|clos/.test(t) ? "cancelled" : /insufficient|fund/.test(t) ? "funds" : /otp/.test(t) ? "OTP" : /time|expire/.test(t) ? "timeout"
    : /declin|reject|fail/.test(t) ? "declined" : "error"; };
let CFJS = null;
const loadCashfree = () => CFJS = CFJS || new Promise((ok, bad) => {
  if (window.Cashfree) return ok();
  const s = document.createElement("script");
  s.src = "https://sdk.cashfree.com/js/v3/cashfree.js";
  s.onload = () => ok(); s.onerror = () => { CFJS = null; bad(new Error("Couldn't open Cashfree. Check your connection and try again.")); };
  document.head.appendChild(s);
});
async function pay() {
  const p = TIERS[planTier][planSel], u = current, tier = planTier, name = tier === "plus" ? "Plus" : "Prime";
  if (!u) return planMsg("Log in first to purchase. Close this window to log in.", "bad");
  if (planBusy) return;
  if (!PAY_HOOK) return planMsg("Online payment opens soon. Please check back in a little while.");
  planBusy = true; paintPlans(); $(".au-pay", planBox).textContent = "Opening payment…"; planMsg("");
  try {
    const o = await payCall("/order", { tier, months: p.m });          // the Worker prices it from its own table, not from this page
    if (!o || o.error || !o.payment_session_id) throw new Error((o && o.error) || "Couldn't start the payment.");
    await loadCashfree().catch(() => { throw new Error("Unable to complete payment (Cashfree: unreachable)"); });
    const cf = window.Cashfree({ mode: o.mode === "sandbox" ? "sandbox" : "production" });
    const res = await cf.checkout({ paymentSessionId: o.payment_session_id, redirectTarget: "_modal" }) || {};   // resolves when the window closes
    planMsg("Checking your payment…");
    const r = await payCall("/verify", { order_id: o.order_id });       // the Worker reads the order back from Cashfree, then extends the plan
    if (!(r && r.ok)) {
      const why = (res.error && (res.error.message || res.error.code)) || (res.paymentDetails && res.paymentDetails.paymentMessage) || "";
      if (res.error && !res.paymentDetails && (!why || cfWord(why) === "cancelled")) throw new Error("");      // window closed without paying
      throw new Error(`Payment not confirmed (${cfWord(why)}). If money was debited, ${name} switches on by itself within a few minutes; reload this page then.`);
    }
    const v = r;
    planMsg((v.note ? v.note + " " : "") + `${name} is on, valid till ${dateTxt(v.until)}. ${PLAN_NEXT && tier === "prime" ? "Opening Portfolio Builder…" : "Reloading…"}`, "ok");
    try { localStorage.setItem(PRO_HINT, "1"); } catch (e) {}
    try { localStorage.setItem(PLUS_HINT, "1"); } catch (e) {}
    setTimeout(() => { if (PLAN_NEXT && tier === "prime") location.href = PLAN_NEXT; else location.reload(); }, 1600);
  } catch (e) {
    if (e.message) planMsg(e.message, "bad");
    else planMsg("");
  } finally { planBusy = false; paintPlans(); }
}

// Back from a Cashfree page that left this site (some UPI apps / net banking): the order's return_url is
// /?order_id=… — check it with the Worker (the webhook may already have switched the plan on) and say so here.
async function payReturn() {
  const q = new URLSearchParams(location.search), oid = q.get("order_id");
  if (!oid || !/^hni_[pu]\d+_[A-Za-z0-9_-]+$/.test(oid) || !current) return;
  q.delete("order_id");
  history.replaceState(null, "", location.pathname + (q.toString() ? "?" + q : "") + location.hash);
  const name = /^hni_u/.test(oid) ? "Plus" : "Prime";
  openPlans(); planMsg("Checking your payment…");
  const r = await payCall("/verify", { order_id: oid }).catch(() => null);
  if (r && r.ok) {
    planMsg((r.note ? r.note + " " : "") + `${name} is on, valid till ${dateTxt(r.until)}. Reloading…`, "ok");
    try { localStorage.setItem(PRO_HINT, name === "Prime" ? "1" : localStorage.getItem(PRO_HINT) || "0"); localStorage.setItem(PLUS_HINT, "1"); } catch (e) {}
    setTimeout(() => location.reload(), 1600);
  } else planMsg(`Payment not confirmed yet. If money was debited, ${name} switches on by itself within a few minutes; reload this page then.`, "bad");
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
  builder: SVG('<g transform="translate(9.6 6.7) rotate(-38)"><path d="M0 2V15.6" stroke-width="3"/><g fill="currentColor" stroke="none"><rect x="-8.9" y="-2.3" width="2.7" height="4.6" rx="1.2"/><path d="M-6.5 -1.5h1.6v3h-1.6z"/><path d="M-5.2 -2.5Q-4 -3.7 -1.5 -3.7H1.6C5.6 -3.7 8.5 -2.2 9.7 .1 7.7 -1 5.2 -1.3 2.6 -.5L1.9 2.3H-1.9L-3.2 1.9H-5.2z"/></g></g><path d="M3.3 15.6h4.5v6.1H2.1z" stroke-width="1.8"/><path d="M5 15.6v-1.1a1.5 1.5 0 0 1 1.5-1.5h1.3" stroke-width="1.7"/>'),
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
  const ph = document.querySelector(".hni-navbtn.au-ph");                // hdr.js drew it already: take it over (no redraw)
  if (ph) { ph.classList.remove("au-ph"); ph.setAttribute("aria-haspopup", "dialog"); ph.addEventListener("click", (e) => { e.stopPropagation(); openNav(); });
    const av = document.querySelector(".au-user"); if (av && ph.nextElementSibling !== av) av.parentNode.insertBefore(ph, av);
    document.documentElement.classList.add("hni-nav"); return; }
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
// Everything kept in this browser for ONE account (Factor Screener filters / tabs / Compare list "hnif_*", PF Builder
// settings "pb_*", the builder → portfolio basket, the demo flag) goes at logout, so the next account on this device
// starts clean. Theme, the daily notice and per-account keys ("…_<uid>") stay.
const USER_KEYS = /^(hnif_|pb_)|^hni_pf_basket$|^hni_demo$|^hni_nm$/;
function clearUserKeys() {
  try { const L = localStorage; for (let i = L.length - 1; i >= 0; i--) { const k = L.key(i); if (k && USER_KEYS.test(k)) L.removeItem(k); } } catch (e) {}
}
const clearUserCache = () => new Promise((done) => {
  clearUserKeys();
  try { const r = indexedDB.deleteDatabase("hni-pb"); r.onsuccess = r.onerror = r.onblocked = () => done(); setTimeout(done, 800); }
  catch (e) { done(); }
});
window.hniClearCache = clearUserCache;

// ---------- daily notice: every page, once a day (India date), until "I understand" ----------
// Was the Valuation page's own welcome box; now shared so whichever page is opened first that day shows it.
// Same key as before, so nobody sees it twice on the day this changes. Not inside PF Builder's hidden frame.
const NOTICE_KEY = "hnimanshu_welcome_day";
function dailyNotice() {
  if (window.top !== window || /[?&]embed=1\b/.test(location.search)) return;
  const today = new Date(Date.now() + 5.5 * 3600 * 1000).toISOString().slice(0, 10);
  try { if (localStorage.getItem(NOTICE_KEY) === today) return; } catch (e) { return; }
  const box = document.createElement("div");
  box.className = "au-back au-notice open";
  box.innerHTML = `
    <div class="au-box au-nbox" role="dialog" aria-modal="true" aria-labelledby="auNoticeT">
      <h2 class="au-title" id="auNoticeT">Before you continue</h2>
      <p>HNImanshu is <b>software for learning and research</b>. It works out ratios and model values from companies' published results
        and public prices; you choose every filter. It gives <b>no tips, no buy / sell / hold calls and no target prices</b>, and it is not a
        SEBI-registered investment adviser or research analyst.</p>
      <div class="au-risk"><b>Risk disclosure (SEBI study)</b>
        9 out of 10 individual traders in the equity Futures &amp; Options segment incurred net losses. Trading and investing carry real risk
        of loss. Past results, rankings and model values do not guarantee future returns.</div>
      <p>Please do your own research, or speak to a SEBI-registered adviser, before you invest.</p>
      <label class="au-ok"><input type="checkbox"> <span>I understand this is educational software, not investment advice.</span></label>
      <button type="button" class="au-ngo" disabled>I understand</button>
    </div>`;
  document.body.appendChild(box); syncLock();
  const chk = $("input", box), go = $(".au-ngo", box);
  chk.addEventListener("change", () => { go.disabled = !chk.checked; });
  go.addEventListener("click", () => {
    try { localStorage.setItem(NOTICE_KEY, today); } catch (e) {}
    box.classList.remove("open"); syncLock(); setTimeout(() => box.remove(), 300);
  });
  setTimeout(() => chk.focus(), 50);
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", dailyNotice); else dailyNotice();

// ---------- page wiring ----------
const SIGNED_HINT = "hni_signed";                          // "1" = signed in last time (only a hint: Fundamentals checks for real)
const hinted = () => { try { return localStorage.getItem(SIGNED_HINT) === "1"; } catch (e) { return false; } };
// Login gate: once a login is checked, the gated pages open at once for the next hour (an inline line in each page's
// <head> adds html.auth-ok before anything draws). Every page still confirms the login quietly in the background.
// Used again after more than an hour away (or a tab left open that long): the gate shows "Checking login…" and asks
// Firebase's server again (a fresh token), so a deleted / disabled account is shut out.
const AUTH_AT = "hni_auth_at", TRUST_MS = 60 * 60 * 1000;
// Demo (review) logins: "Portfolio" is called "Portfolio" everywhere they look (no real money). Empty the list to switch off.
const DEMO = ["razorpay@users.hnimanshu.app", "demo@users.hnimanshu.app"];
function markDemo(u) {
  const on = !!(u && DEMO.includes((u.email || "").toLowerCase()));
  window.hniDemo = on;
  try { localStorage.setItem("hni_demo", on ? "1" : "0"); } catch (e) {}   // pages show the demo view at once next time
  document.documentElement.classList.toggle("hni-demo", on);
  if (!on) return;
  const pf = NAV.find((x) => x.k === "portfolio"); if (pf) { pf.t = "Portfolio"; pf.s = "Your holdings and P&L"; }
  document.querySelectorAll("[data-pf-name]").forEach((el) => { el.textContent = "Portfolio"; });
  
  document.querySelectorAll("#pfBag").forEach((el) => { el.title = "Portfolio"; el.setAttribute("aria-label", "Portfolio"); });
}
const trusted = () => { try { return hinted() && Date.now() - (+localStorage.getItem(AUTH_AT) || 0) < TRUST_MS; } catch (e) { return false; } };
const stampAuth = (on) => { try { on ? localStorage.setItem(AUTH_AT, String(Date.now())) : localStorage.removeItem(AUTH_AT); } catch (e) {} };
let current = null;
// ---------- 2048 (landing page card only): game2048.js loads only when opened; open for everyone ----------
// hni_game/{uid} = the account's last board + best (owner only) · hni_game_top/{uid} = {best, name} (everyone reads: the Top 10)
const topName = (n) => (/^admin$/i.test(String(n || "").trim()) ? "Himanshu" : String(n || "Player").slice(0, 40));   // the admin account shows as Himanshu
window.hniGameDB = {
  ready: () => ready,
  uid: () => (current ? current.uid : null),
  load: async () => { await needFS(); const d = await getDoc(doc(db, "hni_game", current.uid)); return d.exists() ? d.data() : null; },
  save: async (s) => { await needFS(); return setDoc(doc(db, "hni_game", current.uid), { cells: s.cells, score: s.score, best: s.best, keep: !!s.keep, over: !!s.over, at: serverTimestamp() }); },
  saveTop: async (best) => { await needFS(); return setDoc(doc(db, "hni_game_top", current.uid), { best, name: topName(current.email === ADMIN_EMAIL ? "Himanshu" : nameOf(current)), at: serverTimestamp() }); },
  top: async () => { await needFS();
    const q = await getDocs(fsQuery(collection(db, "hni_game_top"), fsOrder("best", "desc"), fsLimit(10)));
    return q.docs.map((d) => ({ id: d.id, name: topName(d.data().name), best: d.data().best })); },
};
window.hniGame = () => import("./game2048.js?v=46").then((m) => m.open());
const ready = new Promise((resolve) => {
  onAuthStateChanged(auth, async (u) => {
    if (u && expired(u)) { await clearUserCache(); await signOut(auth); u = null; }   // 7-day session limit
    current = u;
    try { localStorage.setItem(SIGNED_HINT, u ? "1" : "0"); } catch (e) {}   // lets the next visit open Fundamentals instantly
    if (u) try {                                                // a different account on this browser than last time: its settings start clean
      const was = localStorage.getItem("hni_uid");
      if (was && was !== u.uid) { clearUserKeys(); try { indexedDB.deleteDatabase("hni-pb"); } catch (e) {} }
      localStorage.setItem("hni_uid", u.uid);
    } catch (e) {}
    if (u && (GATED || LOGIN_ONLY) && !trusted()) {             // more than an hour since the last check: ask the server, not the cache
      try { await u.getIdToken(true); }
      catch (e) { if (!/network|offline/i.test(String(e && e.code || e))) { await signOut(auth); u = null; current = null; } }
    }
    stampAuth(!!u);
    resolve();
    if ((GATED || LOGIN_ONLY) && !u) { if (!LOGGING_OUT) toLanding(); return; }    // no login: the landing page, login box open, then back here
    if (GATED || LOGIN_ONLY) document.documentElement.classList.add("auth-ok");   // show the page at once
    if (!GATED) paintSlot(u);                                  // Valuation (login-only) too: the user menu in its #au-slot
    markDemo(u);
    if (!u) { markPro(false, null); if (GATED) document.documentElement.classList.remove("hni-nav"); else mountNav(); return; }
    // Portfolio tab: the page saves / loads the user's holdings through this (Firestore hni_portfolio/{uid}, owner only)
    window.hniStore = {
      uid: u.uid,                                            // PF Builder remembers its last tab per user
      email: u.email || "",                                  // PF Builder: the review account sees its portfolio instead
      load: async () => { await needFS(); const d = await getDoc(doc(db, PORTFOLIO, u.uid)); return d.exists() ? d.data() : {}; },             // {txns, rates} (or old {items})
      save: async (data) => { await needFS(); return setDoc(doc(db, PORTFOLIO, u.uid), { txns: data.txns || [], rates: data.rates || {}, updated: serverTimestamp() }); },
      // PF Builder settings (fund, split, list / sort / top n, backtest period): one per account, so every device shows the same
      prefs: async () => { await needFS(); const d = await getDoc(doc(db, "hni_settings", u.uid)); return d.exists() ? d.data() : null; },
      savePrefs: async (pb) => { await needFS(); return setDoc(doc(db, "hni_settings", u.uid), { pb, updated: serverTimestamp() }, { mergeFields: ["pb", "updated"] }); },
      // Factor Screener settings (every screen's filters + PSU / Semi PSU), kept until "Reset to default"; PF Builder picks with them
      saveFF: async (ff) => { await needFS(); return setDoc(doc(db, "hni_settings", u.uid), { ff, updated: serverTimestamp() }, { mergeFields: ["ff", "updated"] }); },
      market: async () => { await needFS(); const d = await getDoc(doc(db, ...MARKET)); return d.exists() ? d.data() : null; },
      // live: cb(prices) every time new prices are written (Firestore pushes them, no polling) → returns "stop"
      watchMarket: async (cb) => { await needFS(); return onSnapshot(doc(db, ...MARKET), (d) => cb(d.exists() ? d.data() : null), () => {}); },
      canRefresh: () => !!LTP_HOOK,                          // every signed-in user
      // start the live-price job (Cloudflare Worker → GitHub ltp.yml) → {ok} | {error, wait} | {error, running}
      // (an Oracle-server version of this lives in oracle/: for later, on a server located in India)
      // symbols (PF Builder, Prime / admin): also price these today, even if nobody holds them
      refresh: async (symbols) => {
        const r = await fetch(LTP_HOOK, { method: "POST", headers: { Authorization: "Bearer " + await u.getIdToken(), "Content-Type": "application/json" },
          body: JSON.stringify(Array.isArray(symbols) && symbols.length ? { symbols } : {}) });
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
    payReturn();
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
