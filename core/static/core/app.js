// Shared helpers for the guest, staff and kitchen screens. The server decides everything; this only displays.

// Build a DOM node. Text goes in as text, never as HTML, so guest notes cannot inject markup.
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') el.className = v;
    else if (k === 'style') el.setAttribute('style', v);
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat(2)) {
    if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  }
  return el;
}

// Static, trusted SVG markup only (never put user text in here).
function svg(markup) {
  const t = document.createElement('template');
  t.innerHTML = markup.trim();
  return t.content.firstChild;
}
const ICON = {
  plus: '<path d="M12 5v14M5 12h14"/>', x: '<path d="M6 6l12 12M18 6L6 18"/>', minus: '<path d="M5 12h14"/>', back: '<path d="M15 6l-6 6 6 6"/>', check: '<path d="M5 13l4 4L19 7"/>',
  bell: '<path d="M6 16v-5a6 6 0 0 1 12 0v5l2 2H4zM10 21h4"/>', drop: '<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"/>',
  napkin: '<path d="M4 6h16v12H4zM4 10h16M9 6v12"/>', receipt: '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2zM9 8h6M9 12h6"/>',
  cash: '<rect x="3" y="7" width="18" height="10" rx="2"/><circle cx="12" cy="12" r="2.5"/>', bank: '<path d="M3 10l9-6 9 6M5 10v8M9 10v8M15 10v8M19 10v8M3 20h18"/>',
  card: '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M3 10h18"/>', phone: '<rect x="7" y="3" width="10" height="18" rx="2"/><path d="M11 18h2"/>',
  wifi: '<path d="M2 9a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 19.5h.01"/>',
  gift: '<rect x="3" y="8" width="18" height="4"/><path d="M12 8v13M5 12v9h14v-9M12 8C9 8 8 4 10.5 4S12 8 12 8zM12 8c3 0 4-4 1.5-4S12 8 12 8z"/>',
  game: '<rect x="2" y="7" width="20" height="11" rx="5"/><path d="M7 12h4M9 10v4M16 11h.01M18 13h.01"/>', copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
};
const icon = (n, s = 20, sw = 2.2) => svg(`<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[n]}</svg>`);
const flame = () => svg('<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2c1 4 5 6 5 11a5 5 0 0 1-10 0c0-2 1-3 2-4 0 2 1 3 2 3 0-4-1-6 1-10z"/></svg>');

// A dish illustration, or a coloured initial when the dish has none.
const DISH_BASE = (document.querySelector('meta[name=dish-base]') || {}).content || '/static/core/dishes/';
function plate(art, name, size, cls) {
  const photo = art && art.includes('/');  // uploaded photos come as a URL, illustrations as a name
  if (art) return h('img', { class: 'plate ' + (cls || ''), src: photo ? art : DISH_BASE + art + '.svg', width: size, height: size, alt: '', style: `width:${size}px;height:${size}px` + (photo ? ';border-radius:50%;object-fit:cover' : '') });
  return h('div', { class: 'fallback ' + (cls || ''), style: `width:${size}px;height:${size}px;font-size:${Math.round(size / 3)}px` }, (name || '?').split(' ').map((w) => w[0]).join('').slice(0, 2));
}

const csrf = () => document.querySelector('meta[name=csrf]').content;
async function api(url, body) {
  const opts = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() }, body: JSON.stringify(body) };
  const res = await fetch(url, opts);
  let data = {};
  try { data = await res.json(); } catch (e) { /* not JSON */ }
  if (!res.ok) throw new Error(data.error || 'Something went wrong. Please try again.');
  return data;
}

// Refresh now, on every push from the server, and by polling as a fallback if the socket drops.
function live(wsPath, refresh, pollMs = 8000) {
  let busy = false, again = false;
  // a push that lands mid-refresh is not dropped: one more refresh runs right after, so the screen shows the latest state
  const run = async () => {
    if (busy) { again = true; return; }
    busy = true;
    do { again = false; try { await refresh(); } catch (e) { console.warn(e); } } while (again);
    busy = false;
  };
  run();
  setInterval(run, pollMs);
  let first = true;
  const open = () => {
    const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + wsPath);
    ws.onmessage = run;
    // after a reconnect, catch up on anything pushed while the socket was down
    ws.onopen = () => { if (!first) run(); first = false; };
    ws.onclose = () => { clearInterval(beat); setTimeout(open, 1500); };
    // hosting proxies cut sockets that sit silent; a tiny message every 15 s keeps this one open
    const beat = setInterval(() => { if (ws.readyState === 1) ws.send('{"ping":1}'); }, 15000);
  };
  open();
}

const clock = (sec) => Math.floor(sec / 60) + ':' + String(sec % 60).padStart(2, '0');
const money = (cur, v) => cur + ' ' + Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
const ago = (sec) => (sec < 60 ? 'just now' : Math.floor(sec / 60) + ' min ago');
const CIRC = 464.9; // circumference of the countdown ring (r = 74)

// <span data-end=ms> counts down, <span data-since=ms> counts up; a ring (data-est seconds) shrinks with its countdown.
function countdown(seconds) { return h('span', { class: 'tick', 'data-end': Date.now() + seconds * 1000 }, clock(seconds)); }
function elapsed(seconds) { return h('span', { class: 'tick', 'data-since': Date.now() - seconds * 1000 }, clock(seconds)); }
setInterval(() => {
  document.querySelectorAll('[data-end]').forEach((el) => {
    const left = Math.max(0, Math.round((el.dataset.end - Date.now()) / 1000));
    el.textContent = clock(left);
    const ring = el.closest('[data-est]');
    if (ring) ring.querySelector('.fg').style.strokeDashoffset = (CIRC * (1 - left / Math.max(1, ring.dataset.est))).toFixed(1);
  });
  document.querySelectorAll('[data-since]').forEach((el) => {
    const sec = Math.max(0, Math.round((Date.now() - el.dataset.since) / 1000));
    el.textContent = clock(sec);
    const t = el.closest('[data-target]');
    if (t) {
      const late = sec > t.dataset.target;
      t.classList.toggle('late', late);
      el.classList.toggle('late', late);
      const bar = t.querySelector('.tbar i');
      if (bar) { bar.style.width = Math.min(100, sec / t.dataset.target * 100) + '%'; bar.classList.toggle('late', late); }
    }
  });
}, 1000);

let toastT;
function toast(text, bad) {
  let el = document.getElementById('toast');
  if (!el) { el = h('div', { id: 'toast', role: 'status' }); document.body.append(el); }
  el.textContent = text;
  el.className = 'toast' + (bad ? ' bad' : '');
  el.hidden = false;
  clearTimeout(toastT);
  toastT = setTimeout(() => { el.hidden = true; }, 2400);
}

// Run an action: show errors as a toast, then refresh.
async function act(fn, refresh) {
  try { await fn(); } catch (e) { toast(e.message, true); }
  if (refresh) refresh();
}
