// game2048.js — 2048 in an overlay (landing page card, Navigate → 2048 on every other page). Loaded only when opened.
//
// Saving:
//   • guest (not logged in): this browser only (localStorage "hni2048"), after every move.
//   • logged in: this browser after every move ("hni2048_u_<uid>"), and the account (Firestore hni_game/{uid}) only when
//     it matters: overlay closed, tab hidden / closed, game over. The newer of the two is used on open, so any device
//     continues where the last one stopped. First time on an account: the guest game of this browser carries over.
//   • Top 10 (Firestore hni_game_top/{uid} = {best, name}): written only when the account's best goes up, read only when
//     the Top 10 list is opened (hover on PC, tap on phone). No live updates.
// Keys while open: arrows / WASD move, Ctrl+Z undoes, Esc closes. Phone: swipe on the board.

const KEY_GUEST = "hni2048";
const keyUser = (uid) => "hni2048_u_" + uid;
const G = () => window.hniGameDB || null;
const lsGet = (k) => { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch (e) { return null; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (n) => (+n || 0).toLocaleString("en-IN");
const okCells = (c) => Array.isArray(c) && c.length === 16 && c.every((v) => v === 0 || (Number.isInteger(v) && v >= 2 && (v & (v - 1)) === 0));

const CSS = `
.g48b .au-box.g48{width:min(460px,100%);padding:18px 18px 16px;max-height:calc(100vh - 32px);overflow:auto;
  --gacc:#7fb0ab;--gaccfg:#14201f;--gpan:#2c3534;--gpanfg:#e4eae8;
  --t2:#eee4da;--t4:#ede0c8;--t8:#f2b179;--t16:#f59563;--t32:#f67c5f;--t64:#f65e3b;--t128:#edcf72;--t256:#edcc61;--t512:#edc850;--t1024:#edc53f;--t2048:#edc22e;--tbig:#3c3a32;
  --g:clamp(6px,2vw,10px);--cs:calc((100% - 5 * var(--g)) / 4)}
html[data-theme=day] .g48b .au-box.g48{--gacc:#4f7f7b;--gaccfg:#fff;--gpan:#d8dfdb;--gpanfg:#2c3433}
.g48 *{box-sizing:border-box}
.g48:focus{outline:none}
.g48 .gh{display:flex;align-items:flex-end;justify-content:space-between;gap:10px;flex-wrap:wrap;margin:0 34px 12px 0}
.g48 .gt{display:flex;align-items:center;gap:10px}
.g48 .glogo{width:40px;height:40px;border-radius:9px;background:#edc53f;color:#fdf9f0;display:grid;place-items:center;font:800 12px system-ui,sans-serif;flex:none}
.g48 h2{margin:0;font:700 20px var(--au-mono);letter-spacing:.3px}
.g48 .gsub{margin:2px 0 0;font-size:12px;color:var(--au-faint)}
.g48 .gsc{display:flex;gap:6px}
.g48 .gs{background:var(--au-field);border:1px solid var(--au-line);border-radius:9px;padding:4px 10px;min-width:62px;text-align:center}
.g48 .gs b{display:block;font:600 9.5px var(--au-mono);letter-spacing:.08em;text-transform:uppercase;color:var(--au-faint)}
.g48 .gs span{font:700 17px var(--au-mono);font-variant-numeric:tabular-nums}
.g48 .gbar{display:flex;gap:8px;align-items:center;justify-content:space-between;margin:0 0 10px}
.g48 .gbtns{display:flex;gap:6px;align-items:center}
.g48 .gbtn{font:600 12.5px var(--au-sans);border:0;border-radius:9px;padding:7px 12px;cursor:pointer;background:var(--gpan);color:var(--gpanfg)}
.g48 .gbtn.pri{background:var(--gacc);color:var(--gaccfg)}
.g48 .gbtn:disabled{opacity:.45;cursor:default}
.g48 .gbtn:focus-visible{outline:2px solid var(--gacc);outline-offset:2px}
.g48 .gtop{position:relative}
.g48 .gtop .gbtn{display:inline-flex;align-items:center;gap:5px}
.g48 .gpop{position:absolute;left:0;top:calc(100% + 6px);z-index:5;width:min(260px,calc(100vw - 64px));background:var(--au-bg);border:1px solid var(--au-line);border-radius:10px;
  box-shadow:0 12px 32px rgba(0,0,0,.4);padding:10px 12px;font:12.5px var(--au-mono)}
.g48 .gpop h3{margin:0 0 6px;font:700 11px var(--au-mono);letter-spacing:.08em;text-transform:uppercase;color:var(--au-faint)}
.g48 .gpop ol{margin:0;padding:0;list-style:none}
.g48 .gpop li{display:flex;gap:8px;padding:3px 0;border-bottom:1px solid var(--au-line)}
.g48 .gpop li:last-child{border-bottom:0}
.g48 .gpop li i{font-style:normal;color:var(--au-faint);width:18px;text-align:right;flex:none}
.g48 .gpop li span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.g48 .gpop li.me{color:var(--au-acc);font-weight:700}
.g48 .gpop p{margin:6px 0 0;color:var(--au-faint);font-size:11.5px;line-height:1.4}
.g48 .board{position:relative;width:100%;aspect-ratio:1;background:rgba(127,127,127,.2);border-radius:12px;container-type:inline-size;
  touch-action:none;user-select:none;-webkit-user-select:none}
.g48 .cells{position:absolute;inset:0;padding:var(--g);display:grid;grid-template-columns:repeat(4,1fr);grid-template-rows:repeat(4,1fr);gap:var(--g)}
.g48 .cells i{background:rgba(127,127,127,.22);border-radius:8px}
.g48 .tiles{position:absolute;inset:0}
.g48 .tile{position:absolute;width:var(--cs);height:var(--cs);left:calc(var(--g) + var(--c) * (var(--cs) + var(--g)));top:calc(var(--g) + var(--r) * (var(--cs) + var(--g)));
  display:flex;align-items:center;justify-content:center;border-radius:8px;font:800 9cqw system-ui,-apple-system,sans-serif;color:#f9f6f2;background:var(--t2);
  font-variant-numeric:tabular-nums;transition:left .11s ease,top .11s ease}
.g48 .tile[data-len="3"]{font-size:7.6cqw}
.g48 .tile[data-len="4"],.g48 .tile[data-len="5"]{font-size:6.2cqw}
.g48 .tile[data-len="6"],.g48 .tile[data-len="7"]{font-size:5cqw}
.g48 .tile[data-v="2"],.g48 .tile[data-v="4"]{color:#776e65}
.g48 .tile[data-v="4"]{background:var(--t4)}.g48 .tile[data-v="8"]{background:var(--t8)}.g48 .tile[data-v="16"]{background:var(--t16)}
.g48 .tile[data-v="32"]{background:var(--t32)}.g48 .tile[data-v="64"]{background:var(--t64)}.g48 .tile[data-v="128"]{background:var(--t128)}
.g48 .tile[data-v="256"]{background:var(--t256)}.g48 .tile[data-v="512"]{background:var(--t512)}.g48 .tile[data-v="1024"]{background:var(--t1024)}
.g48 .tile[data-v="2048"]{background:var(--t2048)}.g48 .tile[data-v="big"]{background:var(--tbig)}
.g48 .pop{animation:g48pop .16s ease .08s backwards}
.g48 .bump{animation:g48bump .16s ease}
@keyframes g48pop{from{transform:scale(.5);opacity:0}}
@keyframes g48bump{50%{transform:scale(1.08)}}
.g48 .veil{position:absolute;inset:0;border-radius:12px;background:color-mix(in srgb,var(--au-bg) 82%,transparent);backdrop-filter:blur(2px);
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;text-align:center;padding:16px}
.g48 .veil[hidden]{display:none}
.g48 .veil p{margin:0;font:700 20px var(--au-mono)}
.g48 .veil small{display:block;margin-top:4px;font:500 13px var(--au-sans);color:var(--au-faint)}
.g48 .gfoot{margin:10px 0 0;color:var(--au-faint);font-size:11.5px;text-align:center;line-height:1.45}
@media (prefers-reduced-motion:reduce){.g48 .tile{transition:none}.g48 .pop,.g48 .bump{animation:none}}
@media (hover:none){.g48 .ghint{display:none}}
`;
const CUP = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 4h8v5a4 4 0 0 1-8 0z"/><path d="M16 5h3v2a3 3 0 0 1-3 3M8 5H5v2a3 3 0 0 0 3 3"/><path d="M12 13v4M8.5 20h7M10 17h4v3h-4z"/></svg>';

let back = null, $ = null;
let tiles = [], dying = [], nid = 1, score = 0, best = 0, prev = null, keep = false, over = false;
let els = new Map(), uid = null, loadedFor = undefined, dirty = false, topBest = 0, TOP = null;

/* ---------- the game (same rules as the original: 90% 2s / 10% 4s, one merge per tile per move) ---------- */
function grid() { const g = [[], [], [], []].map(() => [null, null, null, null]); tiles.forEach((t) => { g[t.r][t.c] = t; }); return g; }
function spawn() {
  const g = grid(), free = [];
  for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) if (!g[r][c]) free.push([r, c]);
  if (!free.length) return;
  const p = free[Math.floor(Math.random() * free.length)];
  tiles.push({ id: nid++, v: Math.random() < 0.9 ? 2 : 4, r: p[0], c: p[1], fresh: true });
}
function canMove() {
  const g = grid();
  for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) {
    const t = g[r][c];
    if (!t) return true;
    if (c < 3 && g[r][c + 1] && g[r][c + 1].v === t.v) return true;
    if (r < 3 && g[r + 1][c] && g[r + 1][c].v === t.v) return true;
  }
  return false;
}
function render() {
  const live = new Set(), layer = $(".tiles");
  tiles.concat(dying).forEach((t) => {
    live.add(t.id);
    let el = els.get(t.id);
    if (!el) {
      el = document.createElement("div"); el.className = "tile"; if (t.fresh) el.classList.add("pop");
      el.style.setProperty("--r", t.r); el.style.setProperty("--c", t.c);
      layer.appendChild(el); els.set(t.id, el);
    }
    el.dataset.v = t.v > 2048 ? "big" : String(t.v); el.dataset.len = String(String(t.v).length); el.textContent = t.v;
    el.style.setProperty("--r", t.r); el.style.setProperty("--c", t.c);
    if (t.merged) { el.classList.remove("bump"); void el.offsetWidth; el.classList.add("bump"); }
    t.fresh = false; t.merged = false;
  });
  els.forEach((el, id) => { if (!live.has(id)) { el.remove(); els.delete(id); } });
  $("#g48s").textContent = fmt(score); $("#g48b").textContent = fmt(best);
  $("#g48u").disabled = !prev;
}
function veil(kind) {
  const v = $(".veil"), msg = $(".veil p");
  if (!kind) { v.hidden = true; return; }
  v.hidden = false;
  if (kind === "win") { msg.innerHTML = "You made 2048<small>Keep playing for a higher tile.</small>"; $("#g48k").hidden = false; }
  else { msg.innerHTML = `No moves left<small>Final score ${fmt(score)}</small>`; $("#g48k").hidden = true; }
}
function clearBoard() { els.forEach((el) => el.remove()); els = new Map(); dying = []; }
function newGame() {
  clearBoard(); tiles = []; score = 0; prev = null; keep = false; over = false; veil(null);
  spawn(); spawn(); render(); stash(); flush();
}
function move(d) {
  if (over || !$(".veil").hidden) return;
  const snap = { tiles: tiles.map((t) => ({ id: t.id, v: t.v, r: t.r, c: t.c })), score };
  const vec = [[0, -1], [-1, 0], [0, 1], [1, 0]][d], rs = [0, 1, 2, 3], cs = [0, 1, 2, 3];
  if (vec[0] === 1) rs.reverse();
  if (vec[1] === 1) cs.reverse();
  const g = grid(); let moved = false, made = false;
  rs.forEach((r) => cs.forEach((c) => {
    const t = g[r][c]; if (!t) return;
    let nr = r, nc = c;
    for (;;) { const a = nr + vec[0], b = nc + vec[1]; if (a < 0 || a > 3 || b < 0 || b > 3 || g[a][b]) break; nr = a; nc = b; }
    const a2 = nr + vec[0], b2 = nc + vec[1], n = a2 >= 0 && a2 < 4 && b2 >= 0 && b2 < 4 ? g[a2][b2] : null;
    g[r][c] = null;
    if (n && n.v === t.v && !n.merged) {
      n.v *= 2; n.merged = true; score += n.v; t.r = n.r; t.c = n.c;
      tiles = tiles.filter((x) => x !== t); dying.push(t); moved = true;
      if (n.v === 2048 && !keep) made = true;
    } else { g[nr][nc] = t; if (nr !== r || nc !== c) { t.r = nr; t.c = nc; moved = true; } }
  }));
  if (!moved) return;
  prev = snap; spawn();
  if (score > best) best = score;
  render();
  setTimeout(() => { dying = []; render(); }, 140);
  if (made) { keep = true; setTimeout(() => veil("win"), 220); }
  else if (!canMove()) { over = true; setTimeout(() => veil("over"), 220); }
  stash();
  if (over) flush();
}
function undo() {
  if (!prev) return;
  clearBoard();
  tiles = prev.tiles.map((t) => ({ id: t.id, v: t.v, r: t.r, c: t.c })); score = prev.score; prev = null; over = false; veil(null);
  render(); stash();
}

/* ---------- saving ---------- */
const cells = () => { const a = new Array(16).fill(0); tiles.forEach((t) => { a[t.r * 4 + t.c] = t.v; }); return a; };
const snapState = () => ({ cells: cells(), score, best, keep, over, at: Date.now() });
function stash() { lsSet(uid ? keyUser(uid) : KEY_GUEST, snapState()); if (uid) dirty = true; }   // this browser, every move
async function flush() {                                                 // the account: only on close / hide / game over
  const g = G();
  if (!uid || !dirty || !g) return;
  dirty = false;
  const s = snapState();
  try { await g.save({ cells: s.cells, score: s.score, best: s.best, keep: s.keep, over: s.over }); } catch (e) { dirty = true; }
  if (best > topBest) { try { await g.saveTop(best); topBest = best; TOP = null; } catch (e) {} }
}
function restore(s) {
  clearBoard(); tiles = []; prev = null; nid = 1;
  if (s && okCells(s.cells) && s.cells.some((v) => v)) {
    s.cells.forEach((v, i) => { if (v) tiles.push({ id: nid++, v, r: Math.floor(i / 4), c: i % 4 }); });
    score = +s.score || 0; keep = !!s.keep; over = !!s.over;
  } else { score = 0; keep = false; over = false; spawn(); spawn(); }
  render(); veil(over ? "over" : null);
}
async function loadFor(who) {                                            // who: uid or null (guest)
  uid = who; dirty = false; topBest = 0; TOP = null;
  if (!who) { const s = lsGet(KEY_GUEST); best = Math.max(0, +(s && s.best) || 0); restore(s); return; }
  const local = lsGet(keyUser(who));
  let remote = null; try { remote = await G().load(); } catch (e) {}
  const rAt = remote && remote.at && remote.at.toMillis ? remote.at.toMillis() : 0;
  topBest = remote ? +remote.best || 0 : 0;
  let pick = local && (!remote || (+local.at || 0) >= rAt) ? local : remote;
  if (!pick) pick = lsGet(KEY_GUEST);                                    // first time on this account: the guest game carries on
  best = Math.max(+(local && local.best) || 0, +(remote && remote.best) || 0, +(pick && pick.best) || 0);
  restore(pick);
  if (pick && pick !== remote) { stash(); }                               // newer here than on the account: saved on close
}

/* ---------- Top 10 (read only when opened) ---------- */
async function showTop() {
  const pop = $(".gpop"); pop.hidden = false; $("#g48t").setAttribute("aria-expanded", "true");
  const g = G();
  if (!g || !g.top) { pop.innerHTML = "<h3>Top 10</h3><p>Not available right now.</p>"; return; }
  if (!TOP || Date.now() - TOP.t > 120000) {
    pop.innerHTML = "<h3>Top 10</h3><p>Loading…</p>";
    try { TOP = { t: Date.now(), rows: await g.top() }; } catch (e) { pop.innerHTML = "<h3>Top 10</h3><p>Could not load the list.</p>"; return; }
  }
  if (pop.hidden) return;
  const rows = TOP.rows || [];
  pop.innerHTML = "<h3>Top 10 · all players</h3>" + (rows.length
    ? "<ol>" + rows.map((r, i) => `<li${r.id === uid ? ' class="me"' : ""}><i>${i + 1}</i><span>${esc(r.name || "Player")}</span><b>${fmt(r.best)}</b></li>`).join("") + "</ol>"
    : "<p>No scores yet. Be the first.</p>")
    + (uid ? "" : "<p>Log in to get your score on the list.</p>");
}
function hideTop() { const p = $(".gpop"); if (p) { p.hidden = true; $("#g48t").setAttribute("aria-expanded", "false"); } }

/* ---------- the overlay ---------- */
const lock = () => document.documentElement.classList.toggle("au-lock", !!document.querySelector(".au-back.open"));
function onKey(e) {
  if (!back || !back.classList.contains("open")) return;
  const map = { ArrowLeft: 0, ArrowUp: 1, ArrowRight: 2, ArrowDown: 3, a: 0, w: 1, d: 2, s: 3, A: 0, W: 1, D: 2, S: 3 };
  if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); close(); return; }
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && (e.key === "z" || e.key === "Z")) { e.preventDefault(); e.stopImmediatePropagation(); undo(); return; }
  if (e.key === "Tab" || e.ctrlKey || e.metaKey || e.altKey) return;
  e.stopImmediatePropagation();                                          // nothing on the page behind reacts while the game is open
  if (e.key in map) { e.preventDefault(); move(map[e.key]); }
}
function build() {
  const st = document.createElement("style"); st.textContent = CSS; document.head.appendChild(st);
  back = document.createElement("div");
  back.className = "au-back g48b";
  back.innerHTML = `<div class="au-box g48" tabindex="-1" role="dialog" aria-modal="true" aria-labelledby="g48T">
    <button class="au-x" type="button" aria-label="Close">×</button>
    <div class="gh">
      <div class="gt"><span class="glogo" aria-hidden="true">2048</span><div><h2 id="g48T">2048</h2><p class="gsub">Slide tiles. Match two to make one.</p></div></div>
      <div class="gsc" aria-live="polite"><div class="gs"><b>Score</b><span id="g48s">0</span></div><div class="gs"><b>Best</b><span id="g48b">0</span></div></div>
    </div>
    <div class="gbar">
      <div class="gtop"><button class="gbtn" id="g48t" type="button" aria-expanded="false" aria-haspopup="true">${CUP}Top 10</button><div class="gpop" hidden></div></div>
      <div class="gbtns">
        <button class="gbtn" id="g48u" type="button" disabled>Undo</button>
        <button class="gbtn pri" id="g48n" type="button">New game</button>
      </div>
    </div>
    <div class="board">
      <div class="cells">${"<i></i>".repeat(16)}</div>
      <div class="tiles"></div>
      <div class="veil" hidden><p></p><div class="gbtns"><button class="gbtn" id="g48k" type="button" hidden>Keep going</button><button class="gbtn pri" id="g48a" type="button">New game</button></div></div>
    </div>
    <p class="gfoot"><span class="ghint">Arrows / WASD to move · Ctrl+Z undo · Esc closes<br></span><span id="g48f"></span></p></div>`;
  document.body.appendChild(back);
  $ = (s) => back.querySelector(s);
  back.addEventListener("click", (e) => {
    if (e.target === back || e.target.closest(".au-x")) return close();
    if (!e.target.closest(".gtop")) hideTop();
  });
  $("#g48n").addEventListener("click", newGame);
  $("#g48a").addEventListener("click", newGame);
  $("#g48u").addEventListener("click", undo);
  $("#g48k").addEventListener("click", () => { veil(null); stash(); });
  // Top 10: hover on a PC, tap on a phone
  const tw = $(".gtop"), hover = matchMedia("(hover:hover) and (pointer:fine)").matches;
  if (hover) { tw.addEventListener("mouseenter", showTop); tw.addEventListener("mouseleave", hideTop); }
  $("#g48t").addEventListener("click", () => { if (hover) { showTop(); return; } $(".gpop").hidden ? showTop() : hideTop(); });
  // swipe
  const board = $(".board"); let sx = 0, sy = 0, tr = false;
  board.addEventListener("pointerdown", (e) => { sx = e.clientX; sy = e.clientY; tr = true; });
  board.addEventListener("pointerup", (e) => {
    if (!tr) return; tr = false;
    const dx = e.clientX - sx, dy = e.clientY - sy, ax = Math.abs(dx), ay = Math.abs(dy);
    if (Math.max(ax, ay) < 24) return;
    move(ax > ay ? (dx < 0 ? 0 : 2) : (dy < 0 ? 1 : 3));
  });
  board.addEventListener("pointercancel", () => { tr = false; });
  window.addEventListener("keydown", onKey, true);
  // the account copy is written when the tab is hidden or closed
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flush(); });
  window.addEventListener("pagehide", () => flush());
}
function close() { if (!back) return; hideTop(); back.classList.remove("open"); lock(); flush(); }

export async function open() {
  if (!back) build();
  document.querySelectorAll(".au-back.open").forEach((el) => { if (el !== back) el.classList.remove("open"); });   // Navigate etc. closes
  back.classList.add("open"); lock();
  const g = G();
  if (g && g.ready) await Promise.race([g.ready(), new Promise((r) => setTimeout(r, 3000))]);
  const who = (g && g.uid && g.uid()) || null;
  $("#g48f").textContent = who ? "Saved to your account: continue on any device." : "Playing as guest: saved in this browser only. Log in to save it to your account and join the Top 10.";
  if (loadedFor !== who) { loadedFor = who; await loadFor(who); }
  setTimeout(() => { try { back.querySelector(".au-box").focus({ preventScroll: true }); } catch (e) {} }, 30);
}
