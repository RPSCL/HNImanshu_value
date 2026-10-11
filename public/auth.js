// auth.js — HNImanshu login (username + password, or Google) on Firebase Authentication (project hnimanshu).
//
// One file for both pages:
//   • Screener (index.html): clicking an element with [data-auth-link] (the Fundamentals button) opens the
//     login / register popup when nobody is signed in; after a successful login it goes to that link.
//     Opening the screener with ?login=<path> pops the popup straight away (the Fundamentals gate sends people here).
//   • Fundamentals (<html data-auth="required">): the page stays hidden until a login is confirmed; no login →
//     back to the screener with the popup open. Shows "name · Logout" in the header.
//   • Every login is recorded in Firestore hni_users/{uid} (username, email, provider, dates). Firestore rules let
//     only the admin account read that list; the admin gets a "Users" button in the header to see it.
//   • Passwords never pass through this code's storage: Firebase Authentication keeps them hashed on its servers.
//
// Usernames: Firebase needs an email, so username "chirag" is stored as chirag@users.hnimanshu.app (never shown,
// never mailed). Firebase keeps them unique, so a taken username is refused automatically.
// Sessions end 7 days after the last sign-in (Firebase's own lastSignInTime, so it can't be faked by the browser).

import { initializeApp } from "https://www.gstatic.com/firebasejs/10.13.0/firebase-app.js";
import {
  getAuth, onAuthStateChanged, signInWithEmailAndPassword, GoogleAuthProvider, signInWithPopup, signOut,
} from "https://www.gstatic.com/firebasejs/10.13.0/firebase-auth.js";
import {
  getFirestore, doc, setDoc, getDoc, getDocs, deleteDoc, collection, serverTimestamp,
} from "https://www.gstatic.com/firebasejs/10.13.0/firebase-firestore.js";
import {
  getDatabase, ref, set, remove, onValue, onDisconnect, increment,
} from "https://www.gstatic.com/firebasejs/10.13.0/firebase-database.js";

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
const ADMIN_EMAIL = "admin@" + USER_DOMAIN;       // username "admin" (created by you in the Firebase console)
const USERS = "hni_users";                        // Firestore collection: one doc per account
const ADMINS = "hni_admins";
const PORTFOLIO = "hni_portfolio";                // Firestore: one doc per user with their holdings                      // Firestore: one doc per extra admin (made from the Users list)
const HOME = "./";                                // the screener
const GATED = document.documentElement.dataset.auth === "required";

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getFirestore(app);
const rtdb = firebaseConfig.databaseURL ? getDatabase(app) : null;   // live online / visitor counters (hni/...); skipped if no Realtime DB
const google = new GoogleAuthProvider();
google.setCustomParameters({ prompt: "select_account" });

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const toEmail = (u) => `${u.trim().toLowerCase()}@${USER_DOMAIN}`;
const nameOf = (u) => u.displayName || (u.email || "").replace("@" + USER_DOMAIN, "") || "user";
const isMainAdmin = (u) => !!u && u.email === ADMIN_EMAIL;   // the "admin" username: always admin, can't be removed
let ADMIN = false;                                           // set once per page from hni_admins (see checkAdmin)
async function checkAdmin(u) {
  if (!u) return false;
  if (isMainAdmin(u)) return true;
  try { return (await getDoc(doc(db, ADMINS, u.uid))).exists(); } catch (e) { return false; }
}
const initials = (n) => {                                    // "Himanshu Shekhar" → HS, "admin" → AD
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

// ---------- user record in Firestore (only the admin can read these) ----------
async function record(u) {
  const key = "hni_rec_" + u.uid;                       // write at most once per browser session, keeps Firestore use tiny
  try { if (sessionStorage.getItem(key)) return; } catch (e) {}
  const ref = doc(db, USERS, u.uid);
  const data = { username: nameOf(u), email: u.email || null, provider: providerOf(u), lastLogin: serverTimestamp() };
  try {
    const snap = await getDoc(ref).catch(() => null);  // each user may read only their own doc (to keep "created" once)
    if (!snap || !snap.exists()) data.created = serverTimestamp();
    await setDoc(ref, data, { merge: true });
    try { sessionStorage.setItem(key, "1"); } catch (e) {}
  } catch (e) { console.warn("user record not saved:", e.code || e); }   // never blocks the page
}

// ---------- admin: list of users ----------
const fmt = (t) => (t && t.toDate ? t.toDate().toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }) : "–");
async function openUsers() {
  let back = $(".au-users");
  if (!back) {
    back = document.createElement("div");
    back.className = "au-back au-users";
    back.innerHTML = `<div class="au-box au-wide" role="dialog" aria-modal="true">
      <button class="au-x" type="button" aria-label="Close">×</button>
      <h2 class="au-title">Users <span class="au-count"></span></h2>
      <input class="au-q" placeholder="Search username or email" spellcheck="false">
      <div class="au-tbl"></div></div>`;
    document.body.appendChild(back);
    const close = () => back.classList.remove("open");
    $(".au-x", back).addEventListener("click", close);
    back.addEventListener("click", (e) => { if (e.target === back) close(); });
    back.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Escape") close(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && back.classList.contains("open")) close(); });
    $(".au-q", back).addEventListener("input", (e) => paint(back, e.target.value));
  }
  back.classList.add("open");
  $(".au-tbl", back).innerHTML = '<p class="au-sub">Loading…</p>';
  try {
    const snap = await getDocs(collection(db, USERS));
    const adm = new Set((await getDocs(collection(db, ADMINS))).docs.map((d) => d.id));
    back._rows = snap.docs.map((d) => Object.assign({ uid: d.id, admin: adm.has(d.id) }, d.data())).sort((a, b) => ((b.lastLogin && b.lastLogin.seconds) || 0) - ((a.lastLogin && a.lastLogin.seconds) || 0));
    paint(back, "");
  } catch (e) {
    $(".au-tbl", back).innerHTML = `<p class="au-err">Couldn't load users (${esc(e.code || e)}). Check the Firestore rules.</p>`;
  }
}
function paint(back, q) {
  q = q.trim().toLowerCase();
  const rows = (back._rows || []).filter((r) => !q || (r.username + " " + (r.email || "")).toLowerCase().includes(q));
  $(".au-count", back).textContent = `· ${rows.length}`;
  $(".au-tbl", back).innerHTML = `<table><thead><tr><th>Username</th><th>Email</th><th>Via</th><th>Joined</th><th>Last login</th><th>Admin</th></tr></thead><tbody>${
    rows.map((r) => `<tr><td>${esc(r.username || "")}</td><td>${r.provider === "google" ? esc(r.email || "") : "–"}</td><td>${esc(r.provider || "")}</td><td>${fmt(r.created)}</td><td>${fmt(r.lastLogin)}</td><td>${
      r.email === ADMIN_EMAIL ? '<span class="au-tag">main</span>'
        : `<button type="button" class="au-mk${r.admin ? " on" : ""}" data-uid="${esc(r.uid)}">${r.admin ? "Admin ✓" : "Make admin"}</button>`}</td></tr>`).join("")
  }</tbody></table>`;
  back.querySelectorAll(".au-mk").forEach((b) => b.addEventListener("click", () => setAdmin(back, b.dataset.uid)));
}
// make / unmake an admin: Firestore hni_admins/{uid} (Users list, rules) + RTDB hni/admins/{uid} (online counter rules)
async function setAdmin(back, uid) {
  const r = (back._rows || []).find((x) => x.uid === uid);
  if (!r) return;
  const make = !r.admin;
  if (!confirm((make ? "Make " : "Remove admin from ") + (r.username || r.email || uid) + (make ? " an admin? They will see every user and the online counter." : "?"))) return;
  try {
    if (make) await setDoc(doc(db, ADMINS, uid), { by: current ? current.uid : null, at: serverTimestamp() });
    else await deleteDoc(doc(db, ADMINS, uid));
    if (rtdb) await (make ? set(ref(rtdb, "hni/admins/" + uid), true) : remove(ref(rtdb, "hni/admins/" + uid))).catch(() => {});
    r.admin = make;
    paint(back, $(".au-q", back).value);
  } catch (e) { alert("Couldn't change admin (" + (e.code || e) + "). Check the Firestore rules."); }
}

// ---------- signed-in chip (Fundamentals header) ----------
function showUser(u) {
  const host = document.querySelector("header .global");
  if (!host || $(".au-user", host)) return;
  const n = nameOf(u);
  const el = document.createElement("span");
  el.className = "au-user";
  el.innerHTML = `<button type="button" class="au-av" aria-haspopup="menu" title="Signed in as ${esc(n)}">${esc(initials(n))}</button>
    <span class="au-menu" role="menu"><span class="au-who"><b>${esc(n)}</b>${u.email && !u.email.endsWith("@" + USER_DOMAIN) ? `<i>${esc(u.email)}</i>` : ""}</span>
    ${ADMIN ? '<button type="button" class="au-adm" role="menuitem">Users</button>' : ""}
    <button type="button" class="au-out" role="menuitem">Logout</button></span>`;
  $(".au-av", el).addEventListener("click", (e) => { e.stopPropagation(); el.classList.toggle("open"); });
  document.addEventListener("click", (e) => { if (!el.contains(e.target)) el.classList.remove("open"); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") el.classList.remove("open"); });
  $(".au-out", el).addEventListener("click", async () => { await signOut(auth); location.href = HOME; });
  if (ADMIN) $(".au-adm", el).addEventListener("click", () => { el.classList.remove("open"); openUsers(); });
  host.insertBefore(el, host.querySelector("#theme-toggle"));
}

// ---------- online now + total visitors (admin-only readout) ----------
// Every open tab of the screener or Fundamentals adds hni/presence/<random id>; Firebase deletes it by itself the
// moment the tab closes or the connection drops (onDisconnect). Each IP is counted once ever (SHA-256 hash, never the
// raw IP) into hni/meta/visitors. Only the admin's browser reads these numbers; everyone else just writes.
function presence() {
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
  if (!rtdb) return;
  try {
    if (localStorage.getItem("hni_counted")) return;   // this browser already counted, skip the IP lookup
  } catch (e) {}
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
let adminDot = null;
function showAdminDot() {
  if (adminDot || !rtdb) return;
  adminDot = document.createElement("div");
  adminDot.className = "au-online";
  adminDot.innerHTML = '<i></i><span>… online</span>';
  document.body.appendChild(adminDot);
  let online = 0, total = null;
  const paintDot = () => { $("span", adminDot).textContent = `${online} online` + (total != null ? ` (${total})` : ""); };
  onValue(ref(rtdb, "hni/presence"), (s) => { online = s.size; paintDot(); });
  onValue(ref(rtdb, "hni/meta/visitors"), (s) => { const v = s.val(); total = typeof v === "number" ? v : null; paintDot(); });
}
presence();
countVisitor();

// ---------- page wiring ----------
let current = null;
const ready = new Promise((resolve) => {
  onAuthStateChanged(auth, async (u) => {
    if (u && expired(u)) { await signOut(auth); u = null; }   // 7-day session limit
    current = u;
    resolve();
    if (GATED && !u) {                                        // no login: back to the screener with the popup open
      location.replace(HOME + "?login=" + encodeURIComponent(location.pathname.replace(/^\//, "") + location.hash));
      return;
    }
    if (GATED) document.documentElement.classList.add("auth-ok");   // show the page at once; admin check follows
    if (!u) return;
    // Portfolio tab: the page saves / loads the user's holdings through this (Firestore hni_portfolio/{uid}, owner only)
    const pref = doc(db, PORTFOLIO, u.uid);
    window.hniStore = {
      load: async () => { const d = await getDoc(pref); return d.exists() && Array.isArray(d.data().items) ? d.data().items : []; },
      save: (items) => setDoc(pref, { items, updated: serverTimestamp() }),
    };
    window.dispatchEvent(new Event("hni-ready"));
    record(u);
    ADMIN = await checkAdmin(u);
    if (GATED) showUser(u);
    if (ADMIN) showAdminDot();
  });
});

if (!GATED) {
  // screener: gate the links that need a login
  document.addEventListener("click", async (e) => {
    const a = e.target.closest && e.target.closest("[data-auth-link]");
    if (!a || e.ctrlKey || e.metaKey || e.shiftKey) return;
    e.preventDefault();
    await ready;
    if (current) location.href = a.href;
    else openPopup(a.href);
  }, true);
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
