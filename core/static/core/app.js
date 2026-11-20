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
  plus: '<path d="M12 5v14M5 12h14"/>', minus: '<path d="M5 12h14"/>', back: '<path d="M15 6l-6 6 6 6"/>', check: '<path d="M5 13l4 4L19 7"/>',
  bell: '<path d="M6 16v-5a6 6 0 0 1 12 0v5l2 2H4zM10 21h4"/>', drop: '<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"/>',
  napkin: '<path d="M4 6h16v12H4zM4 10h16M9 6v12"/>', receipt: '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2zM9 8h6M9 12h6"/>',
};
const icon = (n, s = 20, sw = 2.2) => svg(`<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[n]}</svg>`);
const flame = () => svg('<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2c1 4 5 6 5 11a5 5 0 0 1-10 0c0-2 1-3 2-4 0 2 1 3 2 3 0-4-1-6 1-10z"/></svg>');

// A dish illustration, or a coloured initial when the dish has none.
const DISH_BASE = (document.querySelector('meta[name=dish-base]') || {}).content || '/static/core/dishes/';
function plate(art, name, size, cls) {
  if (art) return h('img', { class: 'plate ' + (cls || ''), src: DISH_BASE + art + '.svg', width: size, height: size, alt: '', style: `width:${size}px;height:${size}px` });
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
  let busy = false;
  const run = async () => { if (busy) return; busy = true; try { await refresh(); } catch (e) { console.warn(e); } busy = false; };
  run();
  setInterval(run, pollMs);
  const open = () => {
    const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + wsPath);
    ws.onmessage = run;
    ws.onclose = () => setTimeout(open, 3000);
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
