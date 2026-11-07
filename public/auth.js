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
/* online = people, not tabs: hni/presence/{who}/{tab} where who = the account (u + uid) when logged in, else the visitor's IP
   (i + 32 hex of a SHA-256 of it, never the IP itself), else this tab. The counter counts the "who" entries, so several tabs,
   or a phone and a laptop on one account / one network, are 1 online. */
async function presence() {
  await needRT();
  if (!rtdb) return;
  try {
    const sid = (crypto.randomUUID ? crypto.randomUUID() : Date.now() + "-" + Math.random().toString(36).slice(2)).replace(/[^a-z0-9-]/gi, "").slice(0, 36);
    let ipK = null;
    try {
      const r = await fetch("https://api.ipify.org?format=json"); const { ip } = await r.json();
      if (ip) { const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode("hni:" + ip));
        ipK = "i" + Array.from(new Uint8Array(buf)).slice(0, 16).map((b) => b.toString(16).padStart(2, "0")).join(""); }
    } catch (e) {}
    let me = null, live = false;
    const arm = () => { if (!live || !me) return; onDisconnect(me).remove(); set(me, true).catch(() => {}); };   // re-armed on every reconnect
    const place = (who) => {
      const next = ref(rtdb, "hni/presence/" + who + "/" + sid);
      if (me && me.toString() === next.toString()) return;
      if (me) { const old = me; onDisconnect(old).cancel().catch(() => {}); set(old, null).catch(() => {}); }
      me = next; arm();
    };
    onAuthStateChanged(auth, (u) => place(u ? "u" + u.uid : ipK || "s" + sid));
    onValue(ref(rtdb, ".info/connected"), (s) => { live = s.val() === true; arm(); });
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
window.hniGame = () => import("./game2048.js?v=47").then((m) => m.open());
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
