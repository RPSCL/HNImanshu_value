// auth.js — HNImanshu login (username + password, or Google) on Firebase Authentication (project nfsxcas).
//
// One file for both pages:
//   • Screener (index.html): clicking an element with [data-auth-link] (the Fundamentals button) opens the
//     login / register popup when nobody is signed in; after a successful login it goes to that link.
//     Opening the screener with ?login=<path> pops the popup straight away (the Fundamentals gate sends people here).
//   • Fundamentals (<html data-auth="required">): the page stays hidden until a login is confirmed; no login →
//     back to the screener with the popup open. Shows "name · Logout" in the header.
//
// Usernames: Firebase needs an email, so username "chirag" is stored as chirag@users.hnimanshu.app (never shown,
// never mailed). Firebase keeps them unique, so a taken username is refused automatically.
// Sessions end 7 days after the last sign-in (Firebase's own lastSignInTime, so it can't be faked by the browser).

import { initializeApp } from "https://www.gstatic.com/firebasejs/10.13.0/firebase-app.js";
import {
  getAuth, onAuthStateChanged, signInWithEmailAndPassword, createUserWithEmailAndPassword,
  updateProfile, GoogleAuthProvider, signInWithPopup, signOut,
} from "https://www.gstatic.com/firebasejs/10.13.0/firebase-auth.js";

// Same Firebase project as RollbackCAS, so one account works on both sites. These values are public by design.
const firebaseConfig = {
  apiKey: "AIzaSyANIaWhY5RfhjaBYJLjfFcIKxGwJUapD44",
  authDomain: "nfsxcas.firebaseapp.com",
  projectId: "nfsxcas",
};
const USER_DOMAIN = "users.hnimanshu.app";        // username → hidden email for Firebase
const SESSION_DAYS = 7;                           // log out a week after the last sign-in
const HOME = "./";                                // the screener
const GATED = document.documentElement.dataset.auth === "required";

const auth = getAuth(initializeApp(firebaseConfig));
const google = new GoogleAuthProvider();
google.setCustomParameters({ prompt: "select_account" });

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const toEmail = (u) => `${u.trim().toLowerCase()}@${USER_DOMAIN}`;
const nameOf = (u) => u.displayName || (u.email || "").replace("@" + USER_DOMAIN, "") || "user";
const expired = (u) => {
  const t = Date.parse(u.metadata && u.metadata.lastSignInTime);
  return t && Date.now() - t > SESSION_DAYS * 864e5;
};

// ---------- popup ----------
let box = null, mode = "login", nextUrl = null;
function buildPopup() {
  if (box) return box;
  box = document.createElement("div");
  box.className = "au-back";
  box.innerHTML = `
    <div class="au-box" role="dialog" aria-modal="true" aria-labelledby="auTitle">
      <button class="au-x" type="button" aria-label="Close">×</button>
      <h2 class="au-title" id="auTitle">Fundamentals</h2>
      <p class="au-sub">Free account. Log in or register to open the Fundamentals page.</p>
      <div class="au-tabs" role="tablist">
        <button type="button" class="au-tab on" data-m="login">Login</button>
        <button type="button" class="au-tab" data-m="register">Register</button>
      </div>
      <form class="au-form" novalidate>
        <label class="au-fld"><span>Username</span>
          <input name="u" autocomplete="username" autocapitalize="none" spellcheck="false" maxlength="20" required></label>
        <div class="au-hint" data-reg hidden>3–20 characters: letters, numbers, dot or underscore.</div>
        <label class="au-fld"><span>Password</span>
          <span class="au-pw"><input name="p" type="password" autocomplete="current-password" required>
          <button type="button" class="au-eye" aria-label="Show password">SHOW</button></span></label>
        <label class="au-fld" data-reg hidden><span>Confirm password</span>
          <input name="p2" type="password" autocomplete="new-password"></label>
        <div class="au-err" aria-live="polite"></div>
        <button class="au-btn" type="submit">Login</button>
      </form>
      <div class="au-or">or</div>
      <button class="au-google" type="button">
        <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>
        Continue with Google</button>
      <p class="au-foot">You stay signed in for 7 days on this device.</p>
    </div>`;
  document.body.appendChild(box);
  const f = $("form", box), err = $(".au-err", box), btn = $(".au-btn", box);
  const setMode = (m) => {
    mode = m;
    box.querySelectorAll(".au-tab").forEach((t) => t.classList.toggle("on", t.dataset.m === m));
    box.querySelectorAll("[data-reg]").forEach((el) => (el.hidden = m !== "register"));
    f.p.autocomplete = m === "register" ? "new-password" : "current-password";
    btn.textContent = m === "register" ? "Create account" : "Login";
    err.textContent = "";
  };
  box.querySelectorAll(".au-tab").forEach((t) => t.addEventListener("click", () => { setMode(t.dataset.m); f.u.focus(); }));
  $(".au-eye", box).addEventListener("click", (e) => {
    const show = f.p.type === "password";
    f.p.type = f.p2.type = show ? "text" : "password";
    e.currentTarget.textContent = show ? "HIDE" : "SHOW";
  });
  $(".au-x", box).addEventListener("click", closePopup);
  box.addEventListener("click", (e) => { if (e.target === box) closePopup(); });
  box.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Escape") closePopup(); });   // page shortcuts stay out

  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const u = f.u.value.trim().toLowerCase(), p = f.p.value;
    if (!/^[a-z0-9._]{3,20}$/.test(u)) return (err.textContent = "Username: 3–20 letters, numbers, dot or underscore.");
    if (p.length < 8) return (err.textContent = "Password must be at least 8 characters.");
    if (mode === "register" && p !== f.p2.value) return (err.textContent = "The two passwords don't match.");
    btn.disabled = true; err.textContent = "";
    try {
      if (mode === "register") {
        const cred = await createUserWithEmailAndPassword(auth, toEmail(u), p);
        await updateProfile(cred.user, { displayName: u });
      } else {
        await signInWithEmailAndPassword(auth, toEmail(u), p);
      }
      done();
    } catch (ex) { err.textContent = message(ex); }
    finally { btn.disabled = false; }
  });
  $(".au-google", box).addEventListener("click", async () => {
    err.textContent = "";
    try { await signInWithPopup(auth, google); done(); }
    catch (ex) { err.textContent = message(ex); }
  });
  box._setMode = setMode;
  return box;
}
function openPopup(next) {
  nextUrl = next || null;
  buildPopup();
  box._setMode("login");
  box.classList.add("open");
  setTimeout(() => $("input[name=u]", box).focus(), 30);
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

// ---------- signed-in chip (Fundamentals header) ----------
function showUser(u) {
  const host = document.querySelector("header .global");
  if (!host || $(".au-user", host)) return;
  const n = nameOf(u);
  const el = document.createElement("span");
  el.className = "au-user";
  el.title = "Signed in as " + n;
  el.innerHTML = `<b>${esc(n.charAt(0).toUpperCase())}</b><span class="au-name">${esc(n)}</span><button type="button">Logout</button>`;
  $("button", el).addEventListener("click", async () => { await signOut(auth); location.href = HOME; });
  host.insertBefore(el, host.querySelector("#theme-toggle"));
}

// ---------- page wiring ----------
let current = null;
const ready = new Promise((resolve) => {
  onAuthStateChanged(auth, async (u) => {
    if (u && expired(u)) { await signOut(auth); u = null; }   // 7-day session limit
    current = u;
    resolve();
    if (GATED) {
      if (u) { document.documentElement.classList.add("auth-ok"); showUser(u); }
      else location.replace(HOME + "?login=" + encodeURIComponent(location.pathname.replace(/^\//, "") + location.hash));
    }
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
