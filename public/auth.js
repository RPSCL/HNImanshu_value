// auth.js — HNImanshu login (username + password, or Google) on Firebase Authentication (project hnimanshu).
//
// One file for both pages:
//   • Screener (index.html): clicking an element with [data-auth-link] (Fundamentals, Portfolio) opens the
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
try {
  const h = localStorage.getItem(PRO_HINT);
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
// Address of the pay Worker, e.g. "https://hni-pay.<your-subdomain>.workers.dev". Empty = payments not switched on yet
// (the plans popup still shows; "Proceed to pay" says payments open soon).
const PAY_HOOK = "";
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
      <h2 class="au-title" id="auTitle">Fundamentals</h2>
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
  b.addEventListener("click", () => openPopup(null));            // after logging in you stay on the screener
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
  document.documentElement.classList.toggle("hni-free", !on && !!current);   // free account: Prime buttons in purple
  try { if (current) localStorage.setItem(PRO_HINT, on ? "1" : "0"); else localStorage.removeItem(PRO_HINT); } catch (e) {}
  markUser();
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
    t.textContent = "Prime · " + (PRIME.until ? "valid till " + dateTxt(PRIME.until) : "no end date");
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
        notes: { uid: u.uid, months: String(p.m) },
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
    planMsg(`Prime is on, valid till ${dateTxt(v.until)}. Reloading…`, "ok");
    try { localStorage.setItem(PRO_HINT, "1"); } catch (e) {}
    setTimeout(() => location.reload(), 1600);
  } catch (e) {
    if (e.paid) planMsg(`Payment received, Prime not confirmed yet (${e.message}). It switches on by itself within a few minutes; reload this page then.`, "bad");
    else if (e.message) planMsg(e.message, "bad");
  } finally { planBusy = false; paintPlans(); }
}

// ---------- phone: one "Navigate to" button (fork icon, drawn one line at a time) → every other page in one overlay ----------
const SVG = (inner, extra = "") => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${inner}</svg>`;
const FORK = SVG('<circle pathLength="1" cx="6" cy="5" r="2.4"/><path pathLength="1" d="M6 7.4v1.6a3 3 0 0 0 3 3h6a3 3 0 0 0 3-3V7.4"/><circle pathLength="1" cx="18" cy="5" r="2.4"/><path pathLength="1" d="M12 12v4.6"/><circle pathLength="1" cx="12" cy="19" r="2.4"/>', 'class="hni-fork"');
const NAV_ICON = {
  screener: SVG('<path d="M4 6h16M4 12h16M4 18h10"/>'),
  fundamentals: SVG('<path d="M3 20h18"/><path d="M6 16v-5M11 16V6M16 16v-8M21 16v-3"/>'),
  portfolio: SVG('<path d="M6 7h12l1 14H5L6 7z"/><path d="M9 7V5a3 3 0 0 1 6 0v2"/>'),
  builder: SVG('<path d="M12 8H5.6L4.5 21H12V8"/><path d="M8.2 8V6.2a3.3 3.3 0 0 1 3.8-3.2"/><g transform="rotate(-40 17 12)"><rect x="14" y="4.6" width="7" height="4.4" rx="1"/><path d="M17.5 9v10.5"/></g>'),
  weekly: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="11" fill="#29a9eb"/><path d="M5.4 11.6l11.5-4.4c.5-.2 1 .1.8.9l-2 9.3c-.1.6-.5.8-1.1.5l-3-2.2-1.5 1.4c-.2.2-.3.3-.6.3l.2-3.1 5.6-5.1c.2-.2 0-.3-.4-.1l-6.9 4.4-3-.9c-.6-.2-.7-.6.1-1z" fill="#fff"/></svg>',
};
const NAV = [
  { k: "screener", t: "Screener", s: "Value screener, every stock ranked", href: "./" },
  { k: "fundamentals", t: "Fundamentals", s: "Industries, value and momentum screens", href: "fundamentals#/" },
  { k: "portfolio", t: "Portfolio", s: "Your holdings and P&L", href: "fundamentals#/portfolio" },
  { k: "builder", t: "Portfolio Builder", s: "ETFs + value + momentum, with a backtest", href: "builder", prime: true },
  { k: "weekly", t: "Telegram alerts", s: "Your portfolio summary every week", prime: true },
];
function pageNow() {                                                    // which page this is (left out of the list)
  const p = location.pathname.split("/").pop() || "";
  if (/^builder/.test(p)) return "builder";
  if (/^fundamentals/.test(p)) return /^#\/portfolio/.test(location.hash) ? "portfolio" : "fundamentals";
  return "screener";
}
let navBox = null;
function mountNav() {                                                   // the button, just left of the avatar (shown on phones only)
  if (!current || document.querySelector(".hni-navbtn")) return;
  const av = document.querySelector(".au-user");
  if (!av) return;
  const b = document.createElement("button");
  b.type = "button"; b.className = "hni-navbtn"; b.title = "Navigate to"; b.setAttribute("aria-label", "Navigate to");
  b.setAttribute("aria-haspopup", "dialog"); b.innerHTML = FORK;
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
    const lock = n.prime && !PRO_NOW;
    const tag = n.href ? `a href="${n.href}"` : 'button type="button"';
    return `<${tag} class="hni-nv${n.prime ? " pr" : ""}${lock ? " lock" : ""}" data-k="${n.k}">
      <span class="ic">${NAV_ICON[n.k]}</span><span class="tx"><b>${esc(n.t)}</b><small>${esc(lock ? "Part of Prime · see plans" : n.s)}</small></span>
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
}

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
    if (!u) { markPro(false, null); return; }
    // Portfolio tab: the page saves / loads the user's holdings through this (Firestore hni_portfolio/{uid}, owner only)
    window.hniStore = {
      load: async () => { await needFS(); const d = await getDoc(doc(db, PORTFOLIO, u.uid)); return d.exists() ? d.data() : {}; },             // {txns, rates} (or old {items})
      save: async (data) => { await needFS(); return setDoc(doc(db, PORTFOLIO, u.uid), { txns: data.txns || [], rates: data.rates || {}, updated: serverTimestamp() }); },
      market: async () => { await needFS(); const d = await getDoc(doc(db, ...MARKET)); return d.exists() ? d.data() : null; },
      canRefresh: () => !!LTP_HOOK,                          // every signed-in user
      // start the live-price job → {ok} | {error, running}
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
        return until && until <= Date.now() ? null : { until };
      } catch (e) { return null; }                     // ended (rules refuse) or offline
    })();
    window.hniPrime = window.hniPrimeInfo.then((p) => !!p);
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
      let target = "fundamentals";                                              // only pages on this site
      try { const t = new URL(want, location.href); if (want && t.origin === location.origin) target = t.pathname + t.hash; } catch (e) {}
      if (current) location.href = target; else openPopup(target);
    });
  }
}
