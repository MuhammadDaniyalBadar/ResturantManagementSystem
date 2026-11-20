// Small helpers shared by the guest, staff and kitchen screens. The server decides everything; this only displays.

// Build a DOM node. Text goes in as text, never as HTML, so guest notes cannot inject markup.
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  }
  return el;
}

const csrf = () => document.querySelector('meta[name=csrf]').content;

async function api(url, body) {
  const opts = body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
    body: JSON.stringify(body),
  };
  const res = await fetch(url, opts);
  let data = {};
  try { data = await res.json(); } catch (e) { /* not JSON */ }
  if (!res.ok) throw new Error(data.error || 'Something went wrong. Please try again.');
  return data;
}

// Refresh now, on every push from the server, and by polling as a fallback if the socket drops.
function live(wsPath, refresh, pollMs = 8000) {
  let busy = false;
  const run = async () => {
    if (busy) return;
    busy = true;
    try { await refresh(); } catch (e) { console.warn(e); }
    busy = false;
  };
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

// <span data-end=ms> counts down, <span data-since=ms> counts up.
function countdown(seconds) { return h('span', { class: 'tick', 'data-end': Date.now() + seconds * 1000 }, clock(seconds)); }
function elapsed(seconds) { return h('span', { class: 'tick', 'data-since': Date.now() - seconds * 1000 }, clock(seconds)); }
setInterval(() => {
  document.querySelectorAll('[data-end]').forEach((el) => {
    el.textContent = clock(Math.max(0, Math.round((el.dataset.end - Date.now()) / 1000)));
  });
  document.querySelectorAll('[data-since]').forEach((el) => {
    el.textContent = clock(Math.max(0, Math.round((Date.now() - el.dataset.since) / 1000)));
  });
}, 1000);

let toastTimer;
function toast(text, bad) {
  let el = document.getElementById('toast');
  if (!el) { el = h('div', { id: 'toast' }); document.body.append(el); }
  el.textContent = text;
  el.className = 'show' + (bad ? ' bad' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = ''; }, 3000);
}

// Run an action button: show errors as a toast, then refresh.
async function act(fn, refresh) {
  try { await fn(); } catch (e) { toast(e.message, true); }
  if (refresh) refresh();
}
