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
const PACK = "ZHlrTVtETFkgOBUzEw0cAhL27rL404famdisqfG5rMaM2JxnfVR4fQVNQBokZCgzKSAEEQqg5v218o/Z1Mz7qa70upHJn9uaYGB2bkRsQFUdJThyKkwEARkQ5unl8fDIjdXI4qD8pLuXmpaIVWFic25QSl9JYCY9dS4eDAMXEqTl4O/S2oqYkbK7pPSGm5uXiW1oPHpPSgFTVTEvMzksAwcHVLO5vqati5yc3Ki6qrS8l47fn2l1a2ZoTFhUFlU1b284DQcDA17hsbH7xcXG2P2xuLerk8mGm5Vua39rd1MDVUsya3w7Y0cEHRPe/fzz79eJnsz95aa7tbyLlZWWaH42N0EUEl9QLBMjNRMcCRkR7eSwtdCa9ZfUraOOtb6UhpGPICUye2pVDm4WKXVtPjMNNBYc7OL68O+Bkd3dy+ag+L3Oi82Zi2lgP2wcRlpaUX8oPTcTE04NQfe3r+y5gNmaz62NsaO72pjdjm5MbmJ4DAIcTS8LITY9Dw8iDA3v4/OzgMzemveL7v+hhYmbk65wcn58GgVDUFgqPzx6cxIEGR7R/en7+5+OwNKuurv0oM3R0BuAlCw5ZR4CRRReb3BqNRcFHEpD8bOxeCE7jYyVsPfi5sIFZmfeOWs3PmsYHQsZaWtqdXxMR1GYAzGtvdDF39qXsqi7u7jLp5CMlyhsdmgKXRkeEDEjHzUCCQMTLvD5+/fHj4zQ0u6Dn/r23YiMi217ZDd6QEpSTy08bzcuHQURWeby4PboysXfmNjuo/2gj9Se25g4eDt0D0lfRhR/JzctQAhTG1L2+fTqscPA1dKu9O3qut6FldIgZyZib11QXQRoJjk0NRdKFFAeruu47o7HnsLNoL79r4iMxt/UKm5qcFNTXR0VPikwLDwORQBS+fT58baEw5rIpLy6pLPfgpTRKGkgdGxTBF5ISy8iOigeFQsKV6vo8u7Ays7F2JGZnvCUlZeOij5rZnl2UgVdVjImIiN2RwQHDvH7tbmy09zFl6e0urSoionfm5BrInJyUExSVk0gJiB1UFhBR06qu73/ydXL192wr/y8vpKMlppxbD59bQcFGhYjdTl4OgEfNhj05uz078aC35betr39poGIhJSWLXg6YVxLQFhOMGshZAQIDQAR5uTltvzUyNXPp4y8srOAgofSI3t7b3FBCRsCM2k6MCQXKR4WC+Pj4KaA9d6Z37DhpLOFjZSBlGB/eiNNTkAdBXV6ISBzV11EXquy/fbmiMTW06agu+zl0p2awmRufWVwQlMLXlMjNW89DgwIWhrs/vb51cHA2IenpL6sjYmTqZIta2Q1bl1FUVIlISRtbVEcC0Hx6evy9MrMiIng9vylpNiHkIqYb2MuI1JREAcedHwjIloKAAQZ4fmoqNDfjsbTr6O1+KmHn9zWL2hlOnJMQlYTeiogJDkBGV8L4ePn4O+ZksHAhKSst7CFm5+CkGE2ZXtTAAIbXDFmNDAFCwpcR+Dl/fftnNvVyerk/baryIWdkSgzaXlzUBEDCjA/biM9EUJcVR7zoOf6zNqZjNGwuL+zj43VmJJqbm9FTglPQBEycDc3PBMeDxnt+/Ty7IjP3NauuvWgvJbD39RhciN0f0ADTGRRaCwhdgEGBRkK/u/89s7alcDctuP/9IGSw5OdamRlMSRAQlpPbz41PjkNGElPsbjyyvOKyseUtKWirrOVj4Oek2l6LnpXXV8MXCo+Nz8TUl4GBaT4/fXJw47D3bHi/PW+k8CYkmxsOSx8Sl5XXzNlPTc5DR4BQ7j39ujAzYTQzZKyr7j7loiSm5t+e3p+VUAVBw10bmk7DxUKEA6u6f7088fdx974qr+7soScgJ86bmB4aR4aABd1NzZ1KgIYWVVS5/i56MPHw57DmaL9u5TFm5SRJH96YlBIXVxIKiU/YiwSBBcQ+7Lk+O6fnIjYoKuksa+LnpydOnFvZzQOB1BNEiAkMTcGQEsUEenj4aDXyd2ekOmqp/SGhoebiCoxd3dxUhcCC3JpYWYuHUwFG/Ogorv80Ybf1q6o5+6ohpKF1Z5qZHN1GEVVUUp+PDI+BQEBEUe8++q5kZfezYehpaO8upTAlpR2fX96JBRcSxoyJyM/OUQdEwuoqqP06Y7G2Nba77CItcyIhdqKZ2AzbkVTTVdeLywgNhUJCk8I8ev/6+/H39HVtvKksqaRwZKWaG9hLHFBTUYCJiggIWZSWF9ND/6t4vrQgZ2a37DhoLuPm8bNjWVvdnBOQBQCTDtqYGgvHlYWFPDt9eWzx8PHzq6l9eetnMuBlmxuajVqQlgZFRInOHk3CwcVXkXm4//105LZ18/s5v+4lcqHm5cqMWZwdlJIGUgyKDMyZAsDBAjg+PLK84rKx5S0paL1qJHQnZmMciB3c0tFVBdKITcxNRMMCgRQ5uTm7c/KlIXBn6T/uarLmZaXIn1yeHpcDEdIeyAgIDgWSwYd++Xv9vfE2N7N0aL3t7SOhoLak2x0O3NPCFxEWiZnci8BFUZYUeL/vPHxzYSUg+fl8KOshIKAimB6anhpDRBRViwoPG8qAhhZVVLn+LnyzMKZyuKr4rKvzJyNmt1waX19WQdaRwYrJSc9LUYZEEHk4OLk6ojP29OtrLS0spzGgZFhY2FiJkpEQl1LZn8kI0JZUEde8+3hsoyFzsOQpaix8J27gNuddidiYXJdTkFJMSYibS4KBR0O5Pq09PLWz9fL7aWhoaiMh8vJj34tcHRWXVVTHiY5ISgEBhs1Eujk4OT8yYDUye65qLXljoKCnnByc3hySl4JTCA6Z3twBR5fGOPkp+jAzYTQzZK2q6L7zJ2cgIVobWF9SEYCWlgiP2htEB8TKRKt6+S18MjB3dWnsqC4rYyYmpVvMml/ZUFPCVUlITpvbVcaCUMd6fng9M+TgYfOvfep94iGi5OFPjknKRtDR0ZMLysoYjkKCAxA5OX177PBxcHforymubPeiJ2VdWpgLn1PQ1ZWEi85MTYRUxYbG/2h4O7A2tuN2qW76OqQn9WFk2pkZX1tC0hCXiw9I20wCgIWQfT76uSw187e3KOz9LuzjY+MpJEobGE2TUdcXlAgbDohFgELAhW+veLhm8/L3Nurvuvur57WlpRwbXVlM1dNV1M0O3VjbUFQEBjj7Onn89bE1YKc9Ku2vdSIy5WRfSFgckBMQEEHdGtieVMXFlVN8/Kx6vjEzJyP9eXh7u/J3cPMLSY7IzQfSlxQLSY6PDMNUBANUvb4+OjHiYKZiLbstruSjcKfkylkZ20ATkBTVS0jJT0iOgNaGvek//nyzMLWmrK4rrimgoScjTo+ICBsWwpHWU1uYHk6F0QdGBDqpaj2xNzb08/puKK4g46AksYtOiN9ch1OW1ctO2ohPxdEXlfg/aLw/M3FxpC9m6CVt4aTl4qea2hnO0NcHUdLKT82IVFNQ0dNtK7p9tDGzdzIuvD/7+qb2MTeeWZgdn1MWEoAcDUyCjNKCgdU7ez19/PRztTKhfe9rPuRhpyemiV6cmgJBQJXSGknOzcFTlUXHeDh9urw08PQgbSoov/zyI2G12dhanp5DRBRViwoPG8qAhhZVVLn+Ln2193VnoWjo72u297fxt01OmJhAFFPRxRuZzAtcgsCGhSrsuD2+sHF3d37/L+u/d2bisJiaHxxeVEHQ1lbLzgnYVQZCEwd8P7g9dOS39nUqr+3q9uQhpyIZidiaH5FSA5VLT4iNi4YMB1U4P2i+/aew93PpbW1t7ORjpSK0mVieHRQE0ZWTG1hfjsURQ4VHq2w8fbMyNyPyqK4+fXyh5jZkmxiOWpCSwJST2wlJHgyChAQFvLj6+exwMXd1838u7WpysTdlosobXB5CBNMWVErOWgvARVGWFHi/7z5/MWEyees57Gi84iH3Zt0JWtzcV9GU0snLiB4MAYMBUJJ9vXpx8yH0cKTqKf9u5TFi5ORPmN9b0VVVVdTMS40KnIFAhgU8LPm9uyNgZ7btOWtt7nN0JGWbGh8L2pCWBkVEic4eTkDDVkKIuui8u+M3M7RxqKkvK3a0d7F3DI7YWA/UExGE29kMSJzCAMdFaiz7Pnxy9mIz6G15vjxgp/cnp5vY2AyX3VeGV8wYSYqGgsAGhL2seT40o+DmNuspbX0/8XekppnP3E+YwUCUk9sLCEtPgsHHQu68e/ntI6H09nb6u33vtLfxsHILHFPdA9JWhtNIj0pI00OABEZ+7Ciqa+dzNjSpaf9vqqAgYDAYm1hYnhWVm5XbiY7eCwFHFFWHvOg9vTa0sfe2rGk6beIhsfPyzR7ajURFx4QFXgnMCByDggdHOr9qvT/yc+bi/H4ub79ycvBy3B/Jy5xQlIcUFovKjwvWAoRGx2tvaOqxd7HlpDk+OCpmM7VhZ1nbnh2eBwcAks6aWFhLh1MQkjx8PLK84rKx5Swobj18oKf3IyWcmFxYFJIVFNXKyt+KAgPBwJHt73i4ZvBwtDE+aS+trqbsZrVY3w9Z3hHQ1dDOi4jMyVeWlJIoOb74fOYx9jWkq6ovbyKncrHxWp6dmhHREBBED1xMywUCFUaCub44vvtycHYlqCsuLaojIOBwGJnYWJ8TUUJFDciLD41F0ceDhr06/j01YTD1MyqoL+zj4/VgpJxaHoiTUZcUlUtcGkoJ0ZAQAv6qaCs7sTI19Ovr/Xm/dCbitk0d3ZoQE0EUE0SNisie0wIBVoK5+Do9dfN3dDRq7z/odqGm4GTOGVnfW1AQVtMbzBqPzcBCBYUuuXu7rDMztveqLP0u7ONj4ykkShsYTZST0YXECQ5fi4DBE8CFf/7/erJ08fa0vm5pbmrj46Jp2wncWIzVUpFGm8pOnspBgdSDeLo6uy819iL0NCwqKb7lo2LlZ9mZ3RoTl1BUgcwOTM3ExcPBxnt/uzE8YjMwZayr6b38ISZ3o5jZC9if0tPSxk0NXQ9MxUPA1gL4rfy8tDaxJrdraW/vpqKgI7Qd2NzfU9QFFtTLS8sBDFIDAFW8u/mt7DE2Z7Oo6TvornKmd7XYXIjZXpVCh9ZSms5NjdCHRhZDP749uLVhc7a1KOl6KuJgIaBgV9kP3lqC11STWJnIDFzFhkeAezp/fH0yobQ1rSzobjm0tiBgIJaYzp6VwRAUVY+KDopEQQOD0fi5/fhm8bC3Nut57isuouezpljenV7d0tJCF0gOHVuLRxQFBbu87SjrJOKgIrPvu2iupDB3dqfcCF+dU9HBg1eKyc9K1oRDwdUrqfw7bLL2MDe6/K9tqyChZ3AMDx/bj0UCwRJODoSO3ICH1wIGe6t5/bDxdzM2Kqip/eWjYaRlXAxJykQHE1aUCw4ay4+FEVZVuP8vfH/zMLHk7yUofi8kcaCn3dmfHtnRUVfTBI1JC4+WFhCBwa+7/z2ztqVwNy24//0gZLDl51nIyp1flRKXVV4eXBnflRcAwL81OG4/NGGxtahtLqurIyZmIyWaWMufUtRVVMFKSk1LltdX1NG5uTm7c/KlIeOs7LqrK2Hg4edbXt9LWpXTV1JLSk7MwVMRkdJpa6177HKxNXdx/z55OvZi5GUlWJ+fG9PTBVAXDZjf3QBEkMXG6qx8vfzyd+OzaO7+PrzhJnek29jJi1/S1lWXDJ9fyUkQxkeFBbireL60IGdmt+w4b+zj43GzaFqKzJ7T1VKUE5uODA8NhMeTkLy8avn/8HI2tSm8vampcTaxol4PGh6clcQBwgPZnxmKxpJBhYMraG++9SFwtnTq+Lpu4+fw4aUYm5+byUWDQULMjFwZWYVFFMI5uruvq2Im56J7On94PXYhYGZnG95bSESEkRFXys/Oi4IBwFMEvTq8fDU3o6bjrDxobe2iJmRiS9sZnJwUV8JVC4mKisBCkUTDK3z4fTv14Te1sSpvbW4i52Jzc94UH1aTE1LX1xkYz84GEoZHBj34quvr5bdzJK5lb73/suNhtdxbnltfEhCVVdtLjowMRBQAgwN4/n385nZ0dParKK04NmYl4uhaisyN0FSA0VaNWp/OSpLDxsD+eTx77PNydrdqbz1uLKKjsmRZW5pfWgZGwEIGn0vOykGDAJaDOTo+u/Skp6Ezby2jrfAx8CUiS56d24/CExBFjYrPHcqAUBdG/Sl//DrhIXTzO2zrLn8l4KKiJ5iaX11RRMGR0ZldCMiHDQBC1+o7/361crL28jtorS5u8iMhItnZ3RUdkxAVxIyYWM4cwUPFjD04uO9vvbZ1MrM5OGj8s6PhZmdcWV8dAkBVF9bbGowJRwDARYJ7u//7LHX2NHJu5q1u7uGmJyIKSohd2gJRFxVKSkrd3VKGBQMCvTjr/jNx8PDnrHxt7WCnYKTk3AlcWtFRlpQeS8vPD0xEkVWH+v/sr6l0YLQ1qC7vJi8iY7P22FyI3pyT0NfXR1qOXoyDAcVBTbRwd+ng5TGiIHrouzlk5ePm8Lhirc4cEhBXVUndX8kLgQCTVit7OD16MnO3M3upaGxpc2LgYiaaGlXc0tFVB9KbHc/PxVIDktNqO6v99XLwo7frKSirP+I0NzSPzdrYzBUWVZIOBsqOjgHHx0LqKX95f3NiJiWy6O1oJiNh4SSkHExcz5aSVIWUionOzcFB0VdEvbm/bmiw5LUm+rtq7KjzIzJ2CMhci1rCkRcbyErOzB0FUQDHRmu77i5ysfZmM63qaC/j4uK1NQofy8nW0YTQRIwIys9cwhFXQarpea58cv60ta0reeg85aOlNFiKyx9ckoFXF1LJ2IiMhEABBgM9q66ttWVkc3eq6WhrcCG04HSdWt9MDYdSAkZLDw9NTsXTk5H9fH/8/LCi9OGof2goLCPxp/Q1nskaTMLBVZCUCY4OjUPQBtfBu3turb7mIjo2a6otLzi17GW1Cx9dWRqDUBcWSA8JjkzShgXGPLk5ry10c/Fzc2o9r29yp3ZhZtxeWF0AV5AX1lsaTQsDgQaHBPtqKyl69/d0dSk6ae+sIGDhNRpZmZbclZOFB83LiAxMxREGRYWy+Lm/oqL38PW5+X9uYCcjJ7VLCIvJ1taBxwHICU/KytGDElTq7Su7P3KwsDO4bzysrKHnp+cbnMgcnlXb11dUiMjIBkbIBRfXOf4/bfAxMOUlP+itPHBk5KJmGxpZHV6SFkaXCc9FTs7CAkdDsPxxvK1hsnG1+2our3+ysODnYtzf3ogQUZeREplLW4+DgsaGxjq/7z60sLPwdmGprS1uoiZ3NlgfGRjcUsOGgEgZiw6PBcYPBjt4rO36c3DnNrLqO/4usyAlMrcZ3h9N05cRxQRJWUmPBgTLRoS9+//7KKE4sDTp7uj9fKEwoeTdWRqKz9hXVdLOWchITQGGFE2LMOt5+/Nytubnqq5p6mIjIrWiWxuMioAS0dGSDBofSxxBwsAHvCh8b7jno7f1aCspri6xtbPxGRobWBxRkRFFk0jLDAiMR0RAxu66Pz51MXK2Mnqqra9pZGLm4hPY2JsekhIRhNgDR8aHQoCBx/v/MP5/MDO1pvspufvvcvDjNCRKGp1b0dNGUpfNjU9OUEOGhge8OL994DQhpzHoKW/q6udyc6PLmxjdCRKAEBDLysDOT4PUQEEveq1+fnXisOFy+7v+rqXxIWEm3d/MTMaQUkeHDZiKStdAwEWCe7v/+yxxd/R2raslbu7iImdjikqa39rBgIeS24kIjQvECQQFRq7r/Xuj8vR1NXlrab3lJuKhI4mJ2A3SUlAUE4LHhwUYkFREBL0qfP7/9bfjpigveK0spzLk4wtcGdxeQEKQ1dTI3B2PwsIHBgZp6zy6MjJgtvSoKq+5MKTnICZITRNdj8GDRQbYnUyIioRAx1a4uTu5e6ZidPM7b/s9aiampTF3WR4YG9NRxIXXzclMncNCQ0TEbmp0fXP1MuXggBd7fe9k5mAlGw3THk+BQwTGmF0J2R9BwcTCvO6rPTpjt7YzNOj7+qOkYyChN45f2N7TwhMWlw3OG97ARJDFhP25OW6oZqCx8ujp+7r8Y3ezaZvKC82PQQLDlAuNzshfAAGEAsMu6/17o/YkpfOqa2wv4mHg5KYdjYwSkVGXFZUYz8iPS0IDBkeoubit/vIzdrW4+i8priIh5GRZWRlKD5FS11LWmRzCDVCSVBXXqWw9/PXiMza3Le47/uBksOBnm8oLyQwQkRCBX5mND4oW0tfHu7r+vv4yt+c26+jt/u9k5qUlptFZX13RgFCHgUmIz0pFUgOS1Wttqzi0onN2d2wuZ2xrJLDhp5vZmZyNgdDQ18vamZ6LkxCD0L0r6y7/daHyZqTtOT6uoaNtYGba3hfc1JcSlhYNmNwOgwODR5er+u4tO2IzNDfh7+1uaqphYCOZGZqZDUGSF5QIyxseSheVAoMUfLs5vzH3Y2Kg7fq9bvJwZLf0XYlc31EYlhQUjcGOCsrAwMRCaqr+/LnwcPE1OPku+vjn5/cinRofkVuTFpQX14yJDs1SkBcVTv27/LqxIqSi4Cw5bm8mcHIlNQqdzg0e0lOQVYnJyR5PwEINgzk5vva9Nff19elteb3t4aTlZeIaC84fh8XSxV7Ni8yKgRKUktA4aX5/NmBiMeSoKawq6yqhIePLGp/eWpERV1JaWogJjgKSVtfpqb6vb6Ny8SVz6C7+rSSjJ7V1yMqcjIIVQYaSWxpfDgVSh9XUPGjv/n7wujC3qy9nL6tkYmdn3MgLX9zVF5GG2wzc2t4SxhdDFHy7Ob8x92ewd+pubbzyJWd2J5oamFqbE5dQRIiLjVwfQkdERWgoLzktoyAx5Lj5q6j8JCJntssdSc7dU1EVEp3EgAYZkVVAFcd6e3g6ZyKzsOQt76w+96rgZSYamR2+p+AERtLfG5rIywcFxAV7/v7tvKZysXYqbPuu/SKwpfUky8kOGgfR1VAHhYpJ3JJCRgXFPCr/LHJj8iZyerj+Pa7iY6H1W9oYD9qGBJHFCgsZn9xFBpPF+Xwrsb514KZ2cinpKD7jMGZ35gpLnt0SHdfRFQpLnBwSUkNFAjg4rmwtpuTnMCmprOk5L6xjtMoISFyckdYHFQhN2YhYV0eXxEbr6Sv8sSBwpnht6OkqdyHwZKSZ3g8dEFXBkEBfQUzMjoFGVoa8fr58PCN18bTpfK7+LSAx5OdbW5gL28NQlBLFzJjPT9LRQAFF+jpqerQhsfXzuy//LCEzpPZiC1ucGx+DgQdEmw6PyUqTUQHVuChsqi1xYXe2LOzgrq7ioTX3p4oYXVoVmVfUFcrYiA/AgcBEg7496KwjY/am9CiuaWUsIGEmt0kfT57f1ZYf1UmISF4LgEIHRfk9PLprIqDnZyXtOH2+cvFh56QYWNkNElGRmZbbTAxNg4UGlUSvuvm+fbSjdGTo+G2+/DLwoPTKCZ7fnhKA0YEfjNgMCQKGQULV6+y4LXGyMTWluz2va+NhMbYnmV/cXEIDwcIAi0/PTR2XR9aJO/i5Krwic3E26i874avi4abimUpb3lwC1gfZ00pOid1DwgAXx/29f35gdySiMawuauig4iAhogjZSx5aEdEQBsnYTF/OEkPXw6v/ebytI2QxpewoYq0qILXntaafmRnb1EBGQhRaygyLgBARkwT8ef+tdSJ3tOBt+ShvpuHmZXEdWB+c3FSAltUKBgpeD4FBxFR9Kn+89jC3tCU0e/jhOGMnJybg2ZtZ3lJAE4fRjBlIj9dEQEcGKO67OW2j4GQk7DlpP/8y42G13AqI2Q0Cl1TVTUiZyghAAsFGxeu7L3g1oGSmd+w4ae4jcrDhNQqYnx3RVVmYXEPdzFkL0YOGBrx+q21/9CB1siz6vGVspGHlpcncy55c0JOEU1MIz8ne0pNCxhW5KLw9cXN08rc7bb798CkhpCfaCplcHoGa11JJzokOCwATAEP7e38uKGL24zZvbqooLKAnpiXkSYpPG8OTBlMXSoiIC4aTFUYUeH48aPJ2pPYkLP38Pmoj4OQlHUneHl3dUoIX3wtYSIvDQZaUK7z4dnz1M/D+961qPzy2YqfmY1xLHQnCVwBaU8rPCElHDwzXFLl4/3s+tSFwIb86LWros2Y3Y9ybX14fElOGRtgZWV9KE0PHBkW6vHouYCAmZnKqoC8rYSarJeOYSM7N0lJTVlJJy8icDpPRE8Vqqu+9uuIz9zPr7zt+qnNxYaceHNNenJXT19MAiaP43tGEhdZEuDi9O7J1c+N0+zp/LiVypqXkCEmZTExT0NaXjABBBoSWAxPDuDq4/OjmN/a3KGj8OmokdTNjJc4WGd+UEdRWlt5YycyX1QbHkPB5vPwzJuBwdT99qWw4bCElcctfXgpIlFEDXAuISEzOVhEBhG+u/r9ou/LwsyfqqKzsozV34OWOyhoah4KE0JVehs9KxQBARkV7La+7PeYkcDT/I2xruLKmJvEPXxnKCELX1oHYn1sdyFfHhlGPuLg/fWehsTfgPm4u+SxmoabmDgkZnEeG1pdAn9lJTBhWkIACby1v+P2wM3XhP28rbm5ndXWgmcpY3RsC14MBl96OSZlXh0USVr+5bvuj93c08+qqr+8nJvM19V+Nj5sexgRQF98bSt1OQoDFBbkqrKroNCFwsuvsaexuZHVmNCLKGh5ektFTEscZ2VpeIPo/FQAuKTm/Z6b2tGC57G48KvInYaUdGB0cmxZUBEYaDVzeSkAVU4N5Lmq7uSL3p/bzaOsoL6GwI3L0XFoLSZVTBESRjxjJncMBh0BMOzt+Pa225Gbz6b39Kz2kdHNij5oM2J5Gg9JTy8uKnVsXldMDFH266u8ntrA1tDlr7+7kpvS1JxxJmZ4RwUQW1NjKzI7OhUeSFTx+fH5oIKWx5SxrvCv9ZDFgp8ucGFnaEsDCxrdxt52Jl5GBBNA2eKzuoGIk8LZ5Ki+uJOU09fYeH4/aHkAC1pOLiVxaipLHBVU5en2qemK29SXpKa36+HT1dOZiit4ZDkYC1FCEyEicWBDShJUQ6Dw5rfQwZHMlLfkob7xgoyN1yM5OS08x6ygGDx0YCI5WjccWaCnrqnox5SVw8vovbKfg52RyJ45bmZuVUdBFkk9OzdkQgUbAQjs5LO4/MrMx8j/67Gi84iH05t0JXl/eFMJEl0hMy94LAVXU1wE76XgtdfA1J7D5/KFs4Sf09mfcX9mdk4ZTg8eYTdtdysCUxRBoKu5v+qM0Y/OpfbrranKjp+YaWszKCFHFRYETDYsOnsBBREEDbiu8u+M3M7Rn/qms7CO28GGjGJkLz8lRhFWTjY9Pzl+ERUDH7yq7ePp0MTcm+CkorSvkNfTmYorYH8/WV0eVlooJT1lQ0gAGF++qbDkgofK1Mii56Sxu9vP0IBrIWQ5a0xIGkdjdmstKUoKFhTp6bG33cfH2NafJFFH+djLvZaVYCxyfkxBQRRAeGQwLBQTARtC4/ett+vCk4jPpvf0rKrLiZ6baGQyKyBAFBUFMzcvO3wABhALDLuv9e6P3dHQnPsuU0ndx5yGnGo1NSNAG0xASDclP3grHx0RRqDr5ePqysKRmqKkrqWu2cmTjC1qZTFnVwRBSlYrKGt5QgYeVUSnru64gczOwtzpu6CwjYLT19h4YzlsMVNEUBI/a25zJRFCAwjo5eqpv/TZ29Sl5yxJT8HQ07WebWg0a1BAXVIcOHB8OBQcGxkTuuvvpY/TyouAt67v/KSSw5GWY2B8KiMYSE9GNWY6PzlZVk8UrvL98O6N39jcgOTv7rvei4WDimpiM25YWEoLHyY+Ji0PCUxVH+/r4uuihMzBlq+i8LaryIiWliMoa3dpRQZWXCx6bHEnCkIFVgrv6b3mgInE3sqpqe74pY2Dk4lhK2ZxSVQOQE8mOHE5MQJNABPn4OK37srex9yupKa5/9qvl5Vlc2spM0FfRUxQKHM0Jl5GBBNAuaPn6J/IhpjXq6K88cLFx4jALH5zd3tfEwgUNigyOztbDF8Or/n68+/d+NfVpaS6uq6ihp3Q3ShsYTZPQmtTXzEtfi8IDDJUVKrt/evlxs3dlKb3773xh4mQvnRsfmNSTF9HXy8tPX5/BwcbGuulor21npTQy8aorvS9l4eTg5dqYjtuDU0GTV4rJSEtQAlTXQit1eP36NXRyOCf4P6xt4uI2448Nns4aE1PDwR9ImduNQVCUBZW9Ojg7tDHi9TRq7+n+ojVzpjTZW9/cE4cR1MUYik+NjkPHxlTquCvtdPEx9aa4/LthLiJhIScIGZqeHVNCldKUCttdnJJQR5ZC/bp4fTAxcrKwarlt7SBjoKJgGYjOjB2GQ8UWixpMTMzDAJMWtXg6u+908Le1eC0q7D8hpyUioYmeGd+UAlRWVplODs/QQcBGhTq7rL6z9LAwdmx5PPi/dnP3dIre3Vja1dCCE4zMTQ/YgUcExD0p+G9/YvMnc2To+T4oICQyprQcH92aA9dRlIRJT9oK0hOE1xG4v3w8euG3pza6q/8ovKAxdrWYy4pd2pFQkYRKXg4ey8GHlkOUfTo8rPAhZLf0Kzjsr6MgYGF0iYgdzAMBh4cBjVkIz0yCRsRU/Sn4vL4jc6fmKmmpvm8gIabl3MoLD55CgMYFlwnOTczSkFZSkD+8bq2z4bO0tCtpe+wzMPGgdBuJDUwPQhMQRYza3wjd0saEhb07abr/sXf0dHos+euvY+Pg4zXJE57bk5NXhBKZS87Ow8PClYc4Ob794CPjJ6Ut+Syt7uDkYiPKyIyPjAFb1tfIiNvIjUBSzQQ8uL94fPRz5HKyqqop/XAwI2K1nEgdjRFSVtXTiE/fCwJA0dcVa/+v+nqw9/N6KeltbSqip6ylm0gLTh8UQZfUhsjLyE9ThoDERLj0LayjM/fxfukr7vyhNXRk9Nlb3ZcVkJAQXAqOSU9MQMfXFnh5fn09YeAm5P89q6lpIqI0p91aW1hdUxEGUwTI2QvOA0HAwNe67G77o/33dnKt7eugr3OwJOVbW45bCIYWRpOKy1tamMARUgT56Cu+LTWzsbMsqn1trONmYXYljssejVSW1laW34lNXJACwAYG+35/7GIzpGX8aKhtPj93M+mnm9mZnI+VV5aVyRoKSQyCUtQUKuv4Lvp0M/D1t6rqKinjMeVmp9sYG9mRAEEHlR7aXI4QBccHBHmqvz98sTIxoTinbiyp8WPkpQhfXxzPXRtEns1LiIxORFEU0Jdua+9sovb1cPLt6LorpORlJ/CZXxzcFQHQR1dayx9ejcIBCsL8OD98ryJyZqWuqq27LDKnoGccil7fHgPS0UCTW5kKXJYCAcWF/Gs4LLAgMman6ylu4aQlYeYmSEmdDE2CkMaSzAgPTJjDEBXUvWk4ri5jImc2LXqv/fwl8Pfjp5qeHEyX0pRQ10tZCdzGgkDEw/wo7Daz9LC0dLkvvG7t4eDk54ieWJ+c0AMGxhqYDt4PgsPFwX886e+voqEkfvXo66/+5aBlde4bH52aVVHXVMdNj4+PBNHCBoOo+L/8cDW393Wp+fy/qOYxIfWZCZrd2lFWFdNbjc8PDEGQ1hRU/Kj5e7H28nk26mpsK6Omq6akSwpSX1BU08YWCYmDHp2SAsbCcfo8/+2wJGN3++pq7KYko6cjUxufWF5TU9DEB0lIT04CUtcX1e4svLp2MbMltuxpbGtiYiA3YgvbzhjfElDR09iJ21/KkszARX2+/PqxvmCnN+pqar9qN7UhdaKb2kpJh9MGQxXI2RyNB0UAVgY6er79Z2ak9HAv6/s5eKLw4GIZ3s+YndBBUFfNT09OGYHBBwK9KfnqPKN38Ldzaisub6elZ7Zm2htenZdVEoNVCJjcB0lKyshOaGrrKW31t/b1rK9+LeagICWjmQoK210WRQSbSgiJyd8DwUWERGm7Pr/gtnfxcqjo7+zjsiOhJgkeXd0T1FLURwlJSN4OAkCEFXe58Tu7sCM9/+NjZuT/ZCE0ppvaWh8bk4EURFDOm92ckwdAh4TraW97s7938bYtoizqoXPx9yOZn5kanEdWUZCOSgnNjcRTBxS4KDpuvWIzpuVu6W377HNn4KdjSh4fX8OSEQNTG1lfzQABQpMFPmivvjXxsfBnJO4vrW2lYjamm5lOExtDU0bXG0rYzN0TUcBUeGv6Lnwj8+YkZOo47W/j4CeyI0tbTt8DV0DUxRtcTwsDAtCG1Lz+Pj1+pnenNrqr/z1touFrIpzYWJzPwhOGxBuJC8hPwtCWVFCuPbpspjHxdvSmOK1s42cioTVRmR9dUVGQBwVbyR/OTsLBBpdpOu2sf/SzdrO4b7hpLiJhIScKHEgZ3lFAlMUHS4jPXQDDR0eEPajsbHEgYaY3qW/sbHIz8fIwnh3ODRrCHJGVDU6bSNwOh4cDfKm6f/x0M7AkbT68KHyloOV2cI7aD03BgFEG1NraHt4TwkaWwymp+awjtHP2cmm4/2+qoiOgJJtZzhjN15AVk5hKXIyMgceHxzu86Dk6cbYyOvaqqi3r42b2NXQZHk+bk5JXEIfbXAzJRxPD0gY7Onk9frI2ZrYsKyxo7uggJaXZGZ7Pj9AQkQbaWsvez8PCwILMefg8aaAyMWayqqtoK7DxI7YjmF/U21UVUdXSTcveXotCQERWa6r4+P/0dnAmOjkq7m+kYaXl3QpbHp4WgRQSE8jIzAYCgAcE1bkpbq2wIbb08WwiL23lIKAgcF3JnA2fEpMR0gOICMjcAQIF1Kj5+G0tIjI3tyhtZq8sYaFhIzXZyNLcwsFURlhLXEgPxU8BhsY6/7msYiOk4vd7am9uayVoZ2IdidicnNKWlYSYychdHRIWEBJsK7zvfyH0djFn6KouL6WjJTZ3lFkdnNTCENZWi0lcjoMCB0QD6Pl/7jrzsiU1aexpPeul4WQnyF6engzRAJPWiEzLT10F0MKGRPj/+CzgOrfwtKhovSuwYyKmphwbjIxAgwGQRIgJTU9IxoZXVCgoL633c3J0NHhvKez/aKCgJxzc2FneQNYRFRaNWN2ch8UWANS4KL3+9XJ3NPJ6q+3tcnOx9mILXtkfW1fflFXJyokOCwkAB9So9Pr9+nFhsLfneXn+7qMmLSZnG4ldSYcSB5WWiEJJT8PHCMfDvDu/PzSj4zW0KqpuvrzzsTJxXlqf3ltUQxWBzVmECQyExhcH+np6r3onpTFlsqvqenm34jek59xbWB/VQZfUBR/LnR/BhIAFgjq5f+w64rMncChpr6kqp7IyZ8te3Z4fmhEUVJ6KTNoMVgGFAxf6bDxs4CH0cKTtaql+MjThpDVJWQ7Yk8aSlpfNic0NitIDgYe4/310vLAwdbUteDtsrSSydvVbylteX1QWX9ZUiNwdjoXRBIWHe6s8u+M2MnAn+ik/LCOiYuHtFdHXSU4GkldTWIqPDYtFlFRG/Sl7fnlhMrHlLeuqrD+w5ielJo7L3ByQ0VfUBxlLSEzAEUCGRnl56+71NXb0J799rOtq5KCmtthZXFkbRgOUk9sMG12KR0bF0Si5fvh6MzEk5jetKS19o6IkpKSOC5Qdk5bShQDh9xudgISGgET7bSt8K2Gztjasbrt9b+QwYeTdWRqNCMYBFoLfnsqPCpDCR0ZDPWwtvrXhMDR3Kqoqvjf1MCSlHI1LjZETlgLG28uPjsqCwgaD6zr//Pni83DyqSmq5W1jYeW0W8uNXZzTVlFGEt7ZX1mXBIfWR3p7eDp7cHcwpO2rr+2loLG15Nzb386NgpIHBlsKCV6LgMOHB74qqP5tIrC3NeltYaBka/X09rTaCU9ZhlMGBUQJDl+IkNEAF9T5e/23NbCwMHwqrmlvbGDn9zZYWV5dHUHAEcTbSdhNzkALgQc7vPC/O/Xz9/dze7vt7eLipvV0mQxLWFABltXTyMuJmRdWgFTWveiuOW2isnb2Lektbmqy42XnkR+anhpaEJBTSUpKyd0QQEUARvp+vq5jsiNicXniaC5gJiK1MA5NnM3S0JXExosZDI0PhUeOBLx/b708cvY0tOvu+f0spSOnNspISg0eExJRFVaKDl6KhcMAg4t4OD2+dXH3Z6f6ry7qc2Fj5aXISM3PjdHA0dPLTkZOjMACBob9e3f5PLUytXYtK6hu/TKxoXQ1i9wODoSAE1SFmdiMi9MHAYCEeGpvvaJidrQxLeJvrarg4OAxip9PmJtQF5dWywtMyopSg4fGOnr8um+9tnUyp3v5vb7IF7Qh5F3eHV1TUFAFAYnJDwqFEcdSBmrqL/56ovd0tmtran18orFyIkvYWF4eFZjZnQMemlpLEMJHRkM9bC2+teEw8Lc5/KftYCMhpia5ou0JQ9XEBIQLGQyND4VHjgS8f2+9vrBhJHVsa2h9PTIhdrQLHVrZGlGWUV5US8gNS8LBh4xDOTh9rKJgZKIxrflu7eOgpy9qE5GLH5qSE5AUi0neCNyBEUIGe7m/OK9wZbF0K6joaLyi4SYqJkqdnJ2VhNeG0w2djx2EgEIGBjgseG1xdTNj9XvnomHk6evsbc4e20qewlXYwAlZDsuMxdRHgS94qD2/c/JmcyTp+T4rt+HlYDeVmlnMkAOCVcTKiQmFQkUGhAY//bKxbaKzomTtuWx+7vYztHTPDZvKnlNXRJaLCY9JmFBGhdVFKap7/7fi46LzbWtveTFk5uLwSt4YnhOGRJXAmcxMCVjSQ9KR63t+eGgxYDDh+m847fxgdbQ2yk6MHUgR0NHGFwqLCcoX0sRAlP16vu4n4zUwsDk96G0gYuCy9h4a2w8ZEMSFhuA/nB1dQBWUVj8tKDl8MXH3of86Kq8qt2K3ZDCLnk4eg5MHFkXeHIzZgUBGVYe6Orh6p2Fz8CRt6i9+uHamZWZbmwuK2pNSVJef3Q7JGNAEAYEvKj656KfhcXQ2qep6ueWi5+ThzsoaHtdVE8KSTZ1bi0ERw0aEPD68PaihInP1b/r7vOlgJHP1XVsMSoyUFkMWT17YSE+DA4IRkOp+fX5zsyOi5GhpaXkgcSCy4k5NWltUl5VR1k3PyM2fQAYGhj24P/5vJiRx8OxraCw/ZKOgJ1pZHozOgFMRFZcMiQ7NUBUTQMH9en8/IHey/XVrbvtr4SkhpyMK3x0antPTkATNmU0fndfTlEH4un79fWM35vCsqK6oK6NyNOFgiprKW8fFxJRSysvJzMOBk1LQPDy4vzPwY7b2bS5nq66lIGVgj1pLHU+RkBSSTJ1bTcoSRgLFKKn6vTowoff3ci18Pb/mYDYg9d4LjNuSFxDUwBmBTcuE0cPGxij7vTs/s/Bx5n87au+9pHFjsYuajF2J0QXUAdkPCd9KEoXTVcduO24+Z/NntjOoKL9qY2BjJPVLSVhdlJTBh1Ibyt4ZWEHQwIa7vz1uuqL2tLWtK3m+LCFm9qNPTluKWhRFA1MW3hpLz1KHV4EB+il7r7a3YHe3LfjpveTnoPcwyQqLWtvR0MUWC4oIyRjRw0GV+Xmrbbpzd/e3P3lgLqow4Wf2LFVSEg8UQlcXk0xbCc1BQkWVENmETKlj9Te1NL97ev6/ZvN0IBvIWQ5bVxBGkd9ZzsyY1gfFlnj6+/m756Iw5qB4ra685bHgYOHKTw6Zx0HW1IDeD82eQMLDwYPvqjjuqGC1tqTtuexobnJ3tqHPSd7ciMYX1YZIysvJi9eSANaQaL2+u7OxY2Kyuugp6reyg12biYxfDFUCUJBTG94eCVjSRkQRd7nsLe+hZDH3uGro7eul9bQiyI5Km5yC14fTl4qODF3UkANS1Hx6K2m1cyP1dGluKHkwpXMy9h4fj9obU9OUV99OngjcBUCH1O7qm0WDobWjpa0o/DpqIfKkpSedX4pOVALDhNFKzk/NlxVG1gZ5fKtu0InPZeGsOKl9ruHlN2GPiZkcyAZA0dIfyhmeDcLAhxRoqWnueqezp/b06m+sb/MmpyenWAkOjRSR11CFWw/fjhJWlAmCPHj//+3x4PY2rG9rKv8x8Xdlm5rbnp4Z0RfSSE1K30PFxgYFhiu+br3w9rEy8Ln7vrzyMaCl40sfy8nQBtaRwJ/PjVmex0LXA+s+un6t9iQnM6l9vOiucSInphzdDM3bgEUFUNRbjl6KDMdCVtOrPGvtdXMkerT5Ovy+dyTitWfb2tiayIEXxYFZjI+fypLDiIO+Lf7uP/lxsaWtOmshKia0J+Nk2ohJjJfCdKxrGVoKDRJHEEFLPDyre2O1O/YyOy+/6uOkpTOlXdlfDssDFEPFTUscWopAEsRFeH0/ai+0YiPnMS15aD1kIyRm5d/aXc3VQZMXlw2LDcqSRpSWgjntK3s+5iJz9Lqvf67v5aYj4YjKiZrIQtfVgd8aDonYgNDXxIQ7+O8uYCAnM+DqeKgtoiLit7UKnl3b0VVXVAUamQ8OS9OGUlF4rXk5aCZ2NeE5bOm/qnKj5ONZXtyNz4KVw0XSyJzaC8GV1QMDN74ve781NPCk7C27vaUg9DJiGc0NWNrCF5NVn1rbDVgR0caUvWm/O/wjYCQhe+l8PX+yITZjNF3eW03EgAbFR4FbHFxD0AbWA38p6CwmoWMyIDsvrXmg4jN1NsiNWRzPkZAUkkydW0kf1qJ8MCk/OC96I3ZyNWAsuOlr5vDhNmOfTZnNEBFWxoNbTZudhQDUEkI56ry9P7V3omZsOvu86WRwpCSPmYnYjNHQx4LaX1sdyFfRQUcQbqi4OmcyZmZ1KqlvfLDysbahDlvPHZQQkAbWiomJT0tThlJRfen+Pbtjdidybil5v/ziYqC0XQ6MGEyUFNcEQQ0KCAuEAcQSxrs+rP5zcncxYDmu7T0k5KD18IncXIwPWhIQBslKDk5fEkDWx6v7+7/84iKgpDs5am0tY3K09PXYiNzektHDgoOem4jNRJKVVQT4eywsInais7f6+iVuabEwZqObmUtKnoLSFJDfmqt1s5GUQFR5Knq9OWKg8ycxKXl9pWHndCAkXd4ezgNRwdSEzMkIC0ITkcIWPjpubrcx97cme6m+LPwt8KQm3JgJj9gAFBREWIPITk4CgQWC12q4rz/jMHf29rs5a7+movH1LBrZXdgAE5AFxAsYjV2OwMdGwjr/eO+t9iQnN6ovvGKs8TL0tkkfHc7cEZEVkxXeS1oK0IKHBYN9rGx+9SF39DKpbm8+94FdFXcTWVlOHBIDXpoB24jdzIMHwda9efr9+Sei5bCuemjtKzLg9jWlWlkejMABRAVFzhsez4EBAYFCeHvvrnT0t3F2a2utLz/iZ/UmiJ+YnhwQgxAQywqIDp0SlddCb7ntLe+3vbfmJ/m7fCgksHSv5FpaHp0RlsNGllqJCI8DkkCEBLk/vm0+4jMx/Sk9vKbirXM0dFoIGs4fFdkVBB6ZSI0LxdKEhQQ9ei2st/13pee5ez3oYnAyMqJbDVBbU9ERQkTNyJvZCsOTRcX4/rjqrzXjo3rtbHz+amM1c6NaCdteX1QWQwaTWRzFS0FVV8DFruw5/KBy8PXzrf28KvC2aKhrD8lZXAhGllcGyElMSQtWE4BWL/e7vrowZedzaj58qG0w4mdmYx1MDZpABdgEV8oPGgWXUcbHkO4//q5w8vPxs/+6KP64aKMjcctfXgpOQlOHxgPJ285LQEFUhHv6+r88sTZn5qT8eSph4zJ0NfeIXdjMgNrQ1lOIS9wdQRJDRkT8O/1tvPDw9PPquCti7DFzNPaJXNnPjoYX1oHEzMhNjdfRQUQQbr5/LvBxdHEzfjuofjfuZuPwSt/eiccU0YVXy8rIitiRB9WRcD86bd8Iz6T6aSko/Y/ZlLO1nRvMCloSwpSVF41Pml5EEtOORvxrHEYGJSAwtX696ax3qSCmo9mbi03a04TExc0ZXIZMQsJXVitvabrwcqLkpng47Wl9MG+g5mRdWx3b0tGXkQcaSB9NgQGCAIVqKn8/NfC3cGcpaOjq6vExImnbCkwNz4BV1sSZnQ7PmMgCgYcvKj6/aKf3tmG67+9sefNnZjJwnFkLUlVR0xdAWs/OmdcEwZVH+/r4uuihN+WhYOkv6KwkdDcjmk2M2J1BEheWDM0c3cuQVQyEB706vHonobE34Di4Kv2w6aAmJgkcndtDgUCABU+KixwK0gdEj/j/fG7/4yAwJSyq725sYi/nYk9N3M8YQtPHUwRGSA/L0sUWV4D";
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
