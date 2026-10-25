/* hdr.js — the signed-in header at once (Navigate + your avatar), before Firebase has loaded.
   auth.js needs the Firebase modules first, so without this the header filled in a moment after every page load.
   It draws the same buttons from the last visit (name kept by auth.js in localStorage hni_nm); auth.js then takes them
   over (Navigate) or swaps in the real menu (avatar) in the same place, so nothing moves. Not on the landing page. */
(function () {
  try {
    var d = document.documentElement, L = localStorage;
    if (d.dataset.page === 'home' || L.getItem('hni_signed') !== '1') return;
    var n = L.getItem('hni_nm'); if (!n) return;
    var host = document.getElementById('au-slot') || document.querySelector('header .global');
    if (!host || host.querySelector('.au-user')) return;
    var w = String(n).trim().split(/[\s._-]+/).filter(Boolean);
    var ini = (w.length > 1 ? w[0][0] + w[w.length - 1][0] : (w[0] || '?').slice(0, 2)).toUpperCase();
    var esc = function (s) { return String(s).replace(/[&<>"']/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); };
    var u = document.createElement('span');
    u.className = 'au-user au-ph' + (d.classList.contains('hni-pro') ? ' au-prime' : '') + (d.classList.contains('hni-plus') ? ' au-plusm' : '');
    u.innerHTML = '<button type="button" class="au-av" title="Signed in as ' + esc(n) + '"><span class="au-ini">' + esc(ini) + '</span><span class="au-nm">' + esc(n) + '</span></button>';
    host.insertBefore(u, host.querySelector('#theme-toggle'));
    var b = document.createElement('button');
    b.type = 'button'; b.className = 'hni-navbtn au-ph'; b.title = 'Navigate to'; b.setAttribute('aria-label', 'Navigate to');
    b.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="hni-fork"><circle pathLength="1" cx="6" cy="5" r="2.4"/><path pathLength="1" d="M6 7.4v1.6a3 3 0 0 0 3 3h6a3 3 0 0 0 3-3V7.4"/><circle pathLength="1" cx="18" cy="5" r="2.4"/><path pathLength="1" d="M12 12v4.6"/><circle pathLength="1" cx="12" cy="19" r="2.4"/></svg><span>Navigate</span>';
    host.insertBefore(b, u);
  } catch (e) {}
})();
