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
const EMBED = /[?&]embed=1\b/.test(location.search);    // Fundamentals inside the Portfolio Builder: the builder already counts
if (!EMBED) {
  idle(presence);                                       // online counter: after the page has settled, never before it shows
  idle(countVisitor, 4000);
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
    // Admins: who may use the PF Builder, one click each. The Firestore rules decide for real (only admins may write hni_admins / hni_prime).
    //   members.list() → [{uid, username, email, lastLogin, admin, prime}]   members.set(uid, "prime" | "admin", true | false)
    window.hniStore.members = {
      list: async () => {
        await needFS();
        const ids = async (c) => new Set((await getDocs(collection(db, c)).catch(() => ({ docs: [] }))).docs.map((d) => d.id));
        const [users, admins, prime] = await Promise.all([getDocs(collection(db, USERS)), ids("hni_admins"), ids("hni_prime")]);
        return users.docs.map((d) => { const x = d.data(); return {
          uid: d.id, username: x.username || "", email: x.email || "", lastLogin: x.lastLogin && x.lastLogin.toMillis ? x.lastLogin.toMillis() : 0,
          admin: admins.has(d.id) || x.email === ADMIN_EMAIL, prime: prime.has(d.id), main: x.email === ADMIN_EMAIL }; });
      },
      set: async (uid, role, on) => {
        await needFS();
        const r = doc(db, role === "admin" ? "hni_admins" : "hni_prime", uid);
        return on ? setDoc(r, { by: u.uid, at: serverTimestamp() }) : deleteDoc(r);
      },
    };
    // true / false: is this account a prime member (a hni_prime/{uid} document, written by an admin)? Prime members use the PF Builder like admins
    window.hniPrime = (async () => {
      try { await needFS(); return (await getDoc(doc(db, "hni_prime", u.uid))).exists(); } catch (e) { return false; }
    })();
    // PF Builder (button on Fundamentals + the page itself): admins and prime members
    window.hniBuilder = (async () => (await window.hniAdmin) || (await window.hniPrime))();
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
