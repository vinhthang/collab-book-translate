"""Modular Server-Side HTML, Theme CSS, & ES2020 Client Controller for /admin, /admin/threads, & /admin/read/*."""

from __future__ import annotations

import html
from typing import Any, Optional

from scripts.mcp_server.config import McpServerConfig

STATUS_BADGE_CLASS = {
    "pending_approval": "badge-warn",
    "approved_pending_parse": "badge-warn",
    "pending_review": "badge-warn",
    "pending_admin_approval": "badge-warn",
    "open": "badge-warn",
    "flagged_security": "badge-danger",
    "escalated_to_admin": "badge-danger",
    "seed_failed": "badge-danger",
    "rejected": "badge-danger",
    "revoked": "badge-danger",
    "evaluating": "badge-info",
    "remediation_required": "badge-info",
    "superseded": "badge-info",
    "seeding": "badge-info",
    "active": "badge-info",
    "in_progress": "badge-info",
    "declined": "badge-info",
    "accepted": "badge-ok",
    "completed": "badge-ok",
    "fixed": "badge-ok",
    "published": "badge-ok",
}

SHARED_THEME_CSS = """
:root {
  --bg: #090d16;
  --surface: #111827;
  --elevated: #1e293b;
  --code-bg: #0b1220;
  --border: #334155;
  --border-subtle: #1e293b;
  --text: #f8fafc;
  --text-muted: #94a3b8;
  --text-soft: #cbd5e1;
  --accent: #38bdf8;
  --btn-bg: #0284c7;
  --btn-hover: #0369a1;
  --ok-bg: #064e3b;
  --ok-fg: #6ee7b7;
  --ok-border: #047857;
  --warn-bg: #451a03;
  --warn-fg: #fcd34d;
  --warn-border: #b45309;
  --danger-bg: #450a0a;
  --danger-fg: #fca5a5;
  --danger-border: #b91c1c;
  --info-bg: #0c4a6e;
  --info-fg: #7dd3fc;
  --info-border: #0369a1;
}
[data-theme="light"] {
  --bg: #f8fafc;
  --surface: #ffffff;
  --elevated: #f1f5f9;
  --code-bg: #f1f5f9;
  --border: #cbd5e1;
  --border-subtle: #e2e8f0;
  --text: #0f172a;
  --text-muted: #475569;
  --text-soft: #334155;
  --accent: #0284c7;
  --btn-bg: #0284c7;
  --btn-hover: #0369a1;
  --ok-bg: #dcfce7;
  --ok-fg: #166534;
  --ok-border: #86efac;
  --warn-bg: #fef3c7;
  --warn-fg: #92400e;
  --warn-border: #fcd34d;
  --danger-bg: #fee2e2;
  --danger-fg: #991b1b;
  --danger-border: #fca5a5;
  --info-bg: #e0f2fe;
  --info-fg: #075985;
  --info-border: #7dd3fc;
}
* { box-sizing: border-box; }
body { font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 1.75rem; line-height: 1.5; }
body.admin-body { padding: 0; }
.wrap { max-width: 1120px; margin: 0 auto; }
h1, h2, h3 { margin-top: 0; color: var(--text); }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.pill { display: inline-flex; align-items: center; gap: 0.35rem; background: var(--elevated); border: 1px solid var(--border); padding: 0.25rem 0.65rem; border-radius: 999px; font-size: 0.84rem; color: var(--text-soft); margin: 0 0.45rem 0.45rem 0; }
.badge { display: inline-block; padding: 0.14rem 0.52rem; border-radius: 999px; font-size: 0.74rem; font-weight: 600; vertical-align: middle; border: 1px solid transparent; }
.badge-public, .badge-ok { background: var(--ok-bg); color: var(--ok-fg); border-color: var(--ok-border); }
.badge-restricted, .badge-warn, .badge-manual { background: var(--warn-bg); color: var(--warn-fg); border-color: var(--warn-border); }
.badge-danger { background: var(--danger-bg); color: var(--danger-fg); border-color: var(--danger-border); }
.badge-info { background: var(--info-bg); color: var(--info-fg); border-color: var(--info-border); }
table { width: 100%; border-collapse: collapse; margin-top: 0.85rem; background: var(--surface); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
th, td { text-align: left; padding: 0.72rem 0.9rem; border-bottom: 1px solid var(--border-subtle); font-size: 0.9rem; }
th { background: var(--code-bg); color: var(--text-muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; }
tr:last-child td { border-bottom: none; }
code { background: var(--code-bg); padding: 0.14rem 0.36rem; border-radius: 4px; color: var(--text); font-size: 0.84em; border: 1px solid var(--border-subtle); }
pre { background: var(--code-bg); border: 1px solid var(--border); padding: 0.85rem 1rem; border-radius: 8px; overflow-x: auto; white-space: pre-wrap; margin: 0.5rem 0 0; font-size: 0.84rem; color: var(--text); }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 1.05rem 1.2rem; margin-top: 0.85rem; }
.card-action { border-color: var(--warn-border); }
.card-danger { border-color: var(--danger-border); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 0.9rem; margin-top: 0.75rem; }
.snippet-head { display: flex; justify-content: space-between; align-items: center; font-size: 0.88rem; color: var(--text-soft); }
button.copy-btn { background: var(--elevated); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 0.25rem 0.6rem; cursor: pointer; font-size: 0.78rem; }
button.copy-btn:hover { opacity: 0.88; }
input[type="text"], input[type="search"], select { background: var(--code-bg); color: var(--text); border: 1px solid var(--border); border-radius: 7px; padding: 0.45rem 0.68rem; font-size: 0.86rem; }
.btn { background: var(--btn-bg); color: #fff; border: 1px solid transparent; border-radius: 7px; padding: 0.44rem 0.85rem; cursor: pointer; font-weight: 600; font-size: 0.84rem; display: inline-flex; align-items: center; gap: 0.35rem; }
.btn:hover { background: var(--btn-hover); }
.btn:disabled { opacity: 0.55; cursor: wait; }
.btn-sec { background: var(--elevated); color: var(--text); border-color: var(--border); }
.btn-sec:hover { opacity: 0.9; background: var(--elevated); }
.btn-warn { background: #b45309; color: #fff; }
.btn-warn:hover { background: #92400e; }
.btn-danger { background: #b91c1c; color: #fff; }
.btn-danger:hover { background: #991b1b; }
.btn-sm { padding: 0.28rem 0.58rem; font-size: 0.78rem; }
.chk-grid { display: flex; flex-wrap: wrap; gap: 0.6rem 1.1rem; margin-top: 0.65rem; }
.chk { font-size: 0.86rem; cursor: pointer; color: var(--text-soft); }
.status-msg { margin-top: 0.5rem; font-size: 0.85rem; color: var(--ok-fg); }
.mint-box { margin-top: 0.75rem; padding: 0.85rem 1rem; background: var(--ok-bg); color: var(--ok-fg); border: 1px solid var(--ok-border); border-radius: 8px; display: none; }
.row-flex { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.6rem; }
.btn-row { display: flex; flex-wrap: wrap; gap: 0.45rem; margin-top: 0.7rem; align-items: center; }
details { margin-top: 0.55rem; }
summary { cursor: pointer; color: var(--text-muted); font-size: 0.85rem; }
.hidden { display: none !important; }
.topbar-nav { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.6rem; padding-bottom: 0.9rem; margin-bottom: 1rem; border-bottom: 1px solid var(--border-subtle); }

/* Workspace Console Shell */
.console-shell { display: grid; grid-template-columns: 248px 1fr; min-height: 100vh; }
.console-sidebar { background: var(--surface); border-right: 1px solid var(--border); padding: 1.25rem 1rem; position: sticky; top: 0; height: 100vh; display: flex; flex-direction: column; justify-content: space-between; gap: 1rem; overflow-y: auto; }
.brand-title { font-size: 1rem; font-weight: 700; letter-spacing: -0.01em; display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; margin-bottom: 0.25rem; }
.brand-sub { font-size: 0.76rem; color: var(--text-muted); margin-bottom: 1rem; }
.nav-group { display: flex; flex-direction: column; gap: 0.25rem; }
.nav-item { display: flex; align-items: center; justify-content: space-between; width: 100%; background: transparent; color: var(--text-soft); border: 1px solid transparent; border-radius: 8px; padding: 0.55rem 0.75rem; font-size: 0.88rem; font-weight: 500; cursor: pointer; text-align: left; }
.nav-item:hover { background: var(--elevated); color: var(--text); text-decoration: none; }
.nav-item.active { background: var(--info-bg); color: var(--info-fg); border-color: var(--info-border); font-weight: 600; }
.nav-count { font-size: 0.74rem; padding: 0.08rem 0.48rem; border-radius: 999px; background: var(--elevated); color: var(--text-muted); border: 1px solid var(--border); }
.sidebar-actions { border-top: 1px solid var(--border-subtle); padding-top: 0.85rem; display: flex; flex-direction: column; gap: 0.45rem; }
.console-main { padding: 1.5rem 2rem; max-width: 1240px; width: 100%; margin: 0 auto; }
.kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0.85rem; margin: 1rem 0 1.25rem; }
.kpi-card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 1rem 1.1rem; cursor: pointer; text-align: left; width: 100%; transition: border-color 0.12s; }
.kpi-card:hover { border-color: var(--accent); }
.kpi-label { font-size: 0.78rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; }
.kpi-val { font-size: 1.7rem; font-weight: 700; color: var(--text); margin-top: 0.25rem; line-height: 1.1; }
.kpi-sub { font-size: 0.78rem; color: var(--text-muted); margin-top: 0.3rem; }
.filter-bar { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 0.65rem; background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 0.75rem 0.95rem; margin: 0.75rem 0; }
.chip-row { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.chip { background: var(--elevated); color: var(--text-soft); border: 1px solid var(--border); border-radius: 999px; padding: 0.28rem 0.72rem; font-size: 0.8rem; cursor: pointer; font-weight: 500; }
.chip.active { background: var(--btn-bg); color: #fff; border-color: var(--btn-bg); font-weight: 600; }
.progress-track { width: 100%; height: 7px; background: var(--code-bg); border: 1px solid var(--border-subtle); border-radius: 999px; overflow: hidden; margin-top: 0.4rem; }
.progress-fill { height: 100%; background: linear-gradient(90deg, #0284c7, #10b981); border-radius: 999px; }
.bulk-bar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 0.65rem; padding: 0.65rem 0.95rem; background: var(--elevated); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 0.75rem; }

/* Slide-Over Drawer & Toast */
.drawer-backdrop { position: fixed; inset: 0; background: rgba(2, 6, 23, 0.65); z-index: 40; display: none; justify-content: flex-end; }
.drawer-backdrop.open { display: flex; }
.drawer { width: min(620px, 100vw); height: 100%; background: var(--surface); border-left: 1px solid var(--border); padding: 1.4rem; overflow-y: auto; display: flex; flex-direction: column; gap: 0.85rem; }
.toast-stack { position: fixed; right: 1.25rem; bottom: 1.25rem; z-index: 60; display: flex; flex-direction: column; gap: 0.5rem; max-width: 360px; }
.toast { background: var(--surface); color: var(--text); border: 1px solid var(--border); border-left: 4px solid var(--accent); border-radius: 8px; padding: 0.65rem 0.9rem; font-size: 0.85rem; box-shadow: 0 10px 25px rgba(0,0,0,0.35); }
.toast.toast-ok { border-left-color: #10b981; }
.toast.toast-err { border-left-color: #ef4444; }
@media (max-width: 920px) {
  .console-shell { grid-template-columns: 1fr; }
  .console-sidebar { position: static; height: auto; border-right: none; border-bottom: 1px solid var(--border); }
  .nav-group { flex-direction: row; flex-wrap: wrap; }
  .nav-item { width: auto; }
  .console-main { padding: 1.15rem; }
}

/* Reader & Threads */
.reader-layout { display: grid; grid-template-columns: 270px 1fr; gap: 1.5rem; margin-top: 1rem; align-items: start; }
@media (max-width: 820px) { .reader-layout { grid-template-columns: 1fr; } }
.reader-toc { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 1rem; position: sticky; top: 1rem; max-height: 85vh; overflow-y: auto; }
.reader-toc h3 { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted); margin: 0.9rem 0 0.35rem; }
.reader-toc a { display: block; padding: 0.28rem 0.45rem; border-radius: 6px; font-size: 0.86rem; color: var(--text-soft); margin-bottom: 0.15rem; }
.reader-toc a.active { background: var(--info-bg); color: var(--info-fg); font-weight: 600; }
.reader-main { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 1.75rem 2rem; font-size: 1.05rem; line-height: 1.75; }
.reader-prose h1, .reader-prose h2, .reader-prose h3, .reader-prose h4 { color: var(--text); margin: 1.3rem 0 0.6rem; line-height: 1.3; }
.reader-prose p { margin: 0.75rem 0; color: var(--text-soft); }
.reader-prose ul, .reader-prose ol { margin: 0.65rem 0 0.65rem 1.4rem; color: var(--text-soft); }
.reader-prose blockquote { border-left: 3px solid var(--accent); margin: 0.9rem 0; padding: 0.35rem 0.9rem; color: var(--text-soft); background: var(--elevated); border-radius: 0 6px 6px 0; }
.seg-block { margin-bottom: 0.85rem; }
.seg-orig { display: none; background: var(--code-bg); border-left: 3px solid var(--border); padding: 0.55rem 0.85rem; margin-bottom: 0.4rem; border-radius: 0 6px 6px 0; color: var(--text-muted); font-size: 0.93rem; }
.reader-prose[data-mode="bilingual"] .seg-orig { display: block; }
.fig-callout { background: var(--elevated); border: 1px dashed var(--border); border-radius: 8px; padding: 0.65rem 0.9rem; margin: 0.8rem 0; color: var(--text-soft); font-size: 0.9rem; }
.thread-card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 1rem 1.15rem; margin-top: 0.85rem; }
.thread-body { font-size: 0.86rem; color: var(--text); background: var(--code-bg); border: 1px solid var(--border); padding: 0.7rem 0.85rem; border-radius: 6px; white-space: pre-wrap; margin-top: 0.55rem; }
"""

THEME_BOOT_JS = """
function applyTheme(t) {
  const clean = t === 'light' ? 'light' : 'dark';
  document.documentElement.dataset.theme = clean;
  try { localStorage.setItem('collab-theme', clean); } catch (_e) {}
  const tBtn = document.getElementById('btn-theme-toggle');
  if (tBtn) tBtn.textContent = clean === 'light' ? 'Dark Mode' : 'Light Mode';
}
try {
  const st = localStorage.getItem('collab-theme');
  if (st) applyTheme(st);
} catch (_e) {}
document.getElementById('btn-theme-toggle')?.addEventListener('click', () => {
  const cur = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
  applyTheme(cur === 'light' ? 'dark' : 'light');
});
"""

_ADMIN_JS = THEME_BOOT_JS + """
function showToast(text, kind) {
  const stack = document.getElementById('toast-stack');
  if (!stack) return;
  const el = document.createElement('div');
  el.className = 'toast ' + (kind === 'err' ? 'toast-err' : 'toast-ok');
  el.textContent = String(text || '');
  stack.appendChild(el);
  setTimeout(() => { el.remove(); }, 4200);
}

async function postJson(url, payload, msgEl, pendingText) {
  if (msgEl) {
    msgEl.style.color = 'var(--accent)';
    msgEl.textContent = pendingText || 'Working...';
  }
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {})
  });
  let data = {};
  try { data = await res.json(); } catch (_e) {}
  if (!res.ok) {
    const errTxt = 'Error ' + res.status + ': ' + (data.error || res.statusText);
    if (msgEl) {
      msgEl.style.color = 'var(--danger-fg)';
      msgEl.textContent = errTxt;
    }
    showToast(errTxt, 'err');
    return { ok: false, data };
  }
  return { ok: true, data };
}

function escHtml(val) {
  return String(val ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function showKeyBanner(title, data) {
  const box = document.getElementById('mint-out');
  if (!box) return;
  const baseUrl = box.dataset.baseUrl || location.origin;
  const mcpSnippet = JSON.stringify({
    mcpServers: {
      'collab-books': {
        url: baseUrl + '/mcp',
        headers: { Authorization: 'Bearer ' + data.agent_key }
      }
    }
  }, null, 2);
  box.style.display = 'block';
  box.innerHTML = '';
  const head = document.createElement('div');
  head.className = 'row-flex';
  const strong = document.createElement('strong');
  strong.textContent = title + ': ' + data.handle + ' (' + data.agent_key + ')';
  const cpBtn = document.createElement('button');
  cpBtn.className = 'copy-btn';
  cpBtn.type = 'button';
  cpBtn.textContent = 'Copy Key';
  cpBtn.addEventListener('click', () => {
    navigator.clipboard?.writeText(data.agent_key);
    cpBtn.textContent = 'Copied';
  });
  head.appendChild(strong);
  head.appendChild(cpBtn);
  const hint = document.createElement('div');
  hint.style.fontSize = '0.82rem';
  hint.style.marginTop = '0.3rem';
  hint.textContent = 'Save this 3-word AGENT_KEY now (only its SHA-256 hash is persisted). Hint: ' + (data.key_hint || '');
  const pre = document.createElement('pre');
  pre.textContent = mcpSnippet;
  box.appendChild(head);
  box.appendChild(hint);
  box.appendChild(pre);
  switchTab('agents');
  showToast(title + ' for ' + data.handle, 'ok');
}

function switchTab(tabName) {
  const valid = ['overview', 'books', 'submissions', 'bugs', 'agents'];
  const clean = valid.includes(tabName) ? tabName : 'overview';
  document.querySelectorAll('.tab-panel').forEach(p => {
    p.classList.toggle('hidden', p.id !== 'tab-' + clean);
  });
  document.querySelectorAll('.nav-item[data-tab]').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === clean);
  });
  if (location.hash !== '#' + clean) {
    try { history.replaceState(null, '', '#' + clean); } catch (_e) {}
  }
}

document.querySelectorAll('[data-tab]').forEach(el => {
  el.addEventListener('click', () => switchTab(el.dataset.tab));
});
if (location.hash) {
  switchTab(location.hash.slice(1));
}

// Drawer helpers
const drawerBackdrop = document.getElementById('drawer-backdrop');
const drawerTitle = document.getElementById('drawer-title');
const drawerBody = document.getElementById('drawer-body');
function openDrawer(title, htmlStr) {
  if (!drawerBackdrop || !drawerTitle || !drawerBody) return;
  drawerTitle.textContent = title;
  drawerBody.innerHTML = htmlStr;
  drawerBackdrop.classList.add('open');
}
function closeDrawer() {
  drawerBackdrop?.classList.remove('open');
}
document.getElementById('drawer-close')?.addEventListener('click', closeDrawer);
drawerBackdrop?.addEventListener('click', (e) => {
  if (e.target === drawerBackdrop) closeDrawer();
});

// Conditional allowlist checkbox grid visibility
document.querySelectorAll('select[id^="mode-"]').forEach(sel => {
  sel.addEventListener('change', () => {
    const slug = sel.id.slice('mode-'.length);
    const wrap = document.getElementById('allowlist-' + slug);
    if (wrap) wrap.classList.toggle('hidden', sel.value !== 'restricted');
  });
});

// Books search & filter chips
let activeBookFilter = 'all';
function applyBookFilters() {
  const q = (document.getElementById('book-search')?.value || '').trim().toLowerCase();
  document.querySelectorAll('.book-manage-item').forEach(el => {
    const hay = (el.dataset.search || '').toLowerCase();
    const isPub = el.dataset.public === 'true';
    const isComp = el.dataset.completed === 'true';
    const isPend = el.dataset.pending === 'true';
    let matchChip = true;
    if (activeBookFilter === 'public') matchChip = isPub;
    else if (activeBookFilter === 'private') matchChip = !isPub;
    else if (activeBookFilter === 'completed') matchChip = isComp;
    else if (activeBookFilter === 'pending') matchChip = isPend;
    el.classList.toggle('hidden', !(matchChip && (!q || hay.includes(q))));
  });
}
document.querySelectorAll('.js-book-chip').forEach(chip => {
  chip.addEventListener('click', () => {
    activeBookFilter = chip.dataset.filter || 'all';
    document.querySelectorAll('.js-book-chip').forEach(c => c.classList.toggle('active', c === chip));
    applyBookFilters();
  });
});
document.getElementById('book-search')?.addEventListener('input', applyBookFilters);

// Submissions filter chips, book filter, search, and bulk actions
let activeSubFilter = 'actionable';
function applySubmissionFilters() {
  const bookSel = document.getElementById('sub-book-filter')?.value || '';
  const q = (document.getElementById('sub-search')?.value || '').trim().toLowerCase();
  document.querySelectorAll('.sub-item').forEach(el => {
    const stGroup = el.dataset.subGroup || 'actionable';
    const stRaw = el.dataset.subStatus || '';
    const slug = el.dataset.bookSlug || '';
    const hay = (el.dataset.search || '').toLowerCase();
    let matchStatus = true;
    if (activeSubFilter === 'actionable') matchStatus = stGroup === 'actionable';
    else if (activeSubFilter === 'accepted') matchStatus = stRaw === 'accepted';
    else if (activeSubFilter === 'rejected') matchStatus = stRaw === 'rejected' || stRaw === 'superseded';
    const matchBook = !bookSel || slug === bookSel;
    const matchQ = !q || hay.includes(q);
    el.classList.toggle('hidden', !(matchStatus && matchBook && matchQ));
  });
}
document.querySelectorAll('.js-sub-chip').forEach(chip => {
  chip.addEventListener('click', () => {
    activeSubFilter = chip.dataset.filter || 'actionable';
    document.querySelectorAll('.js-sub-chip').forEach(c => c.classList.toggle('active', c === chip));
    applySubmissionFilters();
  });
});
document.getElementById('sub-book-filter')?.addEventListener('change', applySubmissionFilters);
document.getElementById('sub-search')?.addEventListener('input', applySubmissionFilters);
applySubmissionFilters();

document.getElementById('btn-select-visible-subs')?.addEventListener('click', () => {
  const boxes = Array.from(document.querySelectorAll('.sub-item:not(.hidden) .sub-chk'));
  const allChecked = boxes.length > 0 && boxes.every(b => b.checked);
  boxes.forEach(b => { b.checked = !allChecked; });
});

async function runBulkManage(action) {
  const ids = Array.from(document.querySelectorAll('.sub-chk:checked')).map(b => b.dataset.subId).filter(Boolean);
  const msg = document.getElementById('bulk-sub-msg');
  if (!ids.length) {
    showToast('Select at least one submission checkbox first.', 'err');
    return;
  }
  if (action === 'reject' && !window.confirm('Reject ' + ids.length + ' selected submissions and reopen their chapters?')) return;
  const out = await postJson('/admin/api/submissions/bulk-manage', { submission_ids: ids, action }, msg, 'Processing ' + ids.length + ' submissions...');
  if (out.ok) {
    const doneCnt = (out.data.processed || []).length;
    const errCnt = (out.data.errors || []).length;
    showToast('Bulk ' + action + ': ' + doneCnt + ' updated' + (errCnt ? ', ' + errCnt + ' skipped' : '') + '.', errCnt ? 'err' : 'ok');
    setTimeout(() => location.reload(), 700);
  }
}
document.querySelector('.js-bulk-approve')?.addEventListener('click', () => runBulkManage('approve'));
document.querySelector('.js-bulk-reject')?.addEventListener('click', () => runBulkManage('reject'));

// Lazy Submission Inspector Drawer
document.querySelectorAll('.js-inspect-sub').forEach(btn => {
  btn.addEventListener('click', async () => {
    const sid = btn.dataset.subId;
    openDrawer('Submission Inspector', '<small style="color:var(--text-muted);">Loading submission details...</small>');
    try {
      const res = await fetch('/admin/api/submissions/' + encodeURIComponent(sid));
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.statusText);
      const s = data.submission;
      const reviews = (s.peer_reviews || []).map(r => (
        '<div class="card"><strong>' + escHtml(r.reviewer_handle) + '</strong> <code>' + escHtml(r.verdict) + '</code> &middot; ' +
        Number(r.weighted_score || 0).toFixed(2) + '/5<pre>' + escHtml(r.body_md || '') + '</pre></div>'
      )).join('');
      const findings = (s.open_findings || []).map(f => (
        '<li><code>' + escHtml(f.severity || 'P1') + '</code> ' + escHtml(f.symptom || '') + '</li>'
      )).join('');
      const actBtns = (s.status !== 'accepted' && s.status !== 'rejected') ? (
        '<div class="btn-row">' +
        '<a class="btn btn-sec" href="/admin/read/' + escHtml(s.book_slug) + '/' + escHtml(s.chapter_id) + '">Open in Reader &rarr;</a>' +
        '<button class="btn js-drawer-manage" data-action="' + (s.status === 'pending_review' || s.status === 'pending_admin_approval' ? 'approve' : 'approve_override') + '">Approve &amp; Merge</button>' +
        '<button class="btn btn-danger js-drawer-manage" data-action="reject">Reject</button>' +
        '</div>'
      ) : ('<div class="btn-row"><a class="btn btn-sec" href="/admin/read/' + escHtml(s.book_slug) + '/' + escHtml(s.chapter_id) + '">Open in Reader &rarr;</a></div>');
      openDrawer(
        s.book_slug + ' / ' + s.chapter_id,
        '<div class="row-flex"><span class="badge badge-info">' + escHtml(s.section_type || 'body') + '</span>' +
        '<span class="badge badge-warn">' + escHtml(s.status) + '</span>' +
        '<small><code>' + escHtml(s.id) + '</code></small></div>' +
        '<p style="color:var(--text-soft); margin:0;">Author: <code>' + escHtml(s.author_handle) + '</code> &middot; Engine: <code>' + escHtml(s.engine_model) + '</code></p>' +
        (s.judge_summary ? '<div class="card"><strong>Summary</strong><p style="margin:0.35rem 0 0;">' + escHtml(s.judge_summary) + '</p></div>' : '') +
        (findings ? '<div class="card card-action"><strong>Open Findings</strong><ul>' + findings + '</ul></div>' : '') +
        actBtns +
        (reviews ? '<h3 style="margin-top:0.75rem;">Peer Reviews (' + (s.peer_reviews || []).length + ')</h3>' + reviews : '') +
        '<details open><summary>Translated Output (final_txt)</summary><pre>' + escHtml(s.final_txt || '(empty)') + '</pre></details>' +
        '<details><summary>Bilingual Segment XML</summary><pre>' + escHtml(s.translation_xml || '') + '</pre></details>' +
        '<div id="drawer-sub-msg" class="status-msg"></div>'
      );
      document.querySelectorAll('.js-drawer-manage').forEach(dbtn => {
        dbtn.addEventListener('click', async () => {
          const action = dbtn.dataset.action;
          const out = await postJson('/admin/submissions/' + encodeURIComponent(s.id) + '/manage', { action }, document.getElementById('drawer-sub-msg'), 'Updating submission...');
          if (out.ok) {
            showToast('Submission ' + action + 'd.', 'ok');
            setTimeout(() => location.reload(), 650);
          }
        });
      });
    } catch (err) {
      openDrawer('Error', '<p style="color:var(--danger-fg);">' + escHtml(err.message) + '</p>');
    }
  });
});

// Lazy Book Chapter Drill-Down Drawer
document.querySelectorAll('.js-inspect-book').forEach(btn => {
  btn.addEventListener('click', async () => {
    const slug = btn.dataset.slug;
    openDrawer('Book Chapters: ' + slug, '<small style="color:var(--text-muted);">Loading chapters...</small>');
    try {
      const res = await fetch('/admin/api/books/' + encodeURIComponent(slug));
      const b = await res.json();
      if (!res.ok) throw new Error(b.error || res.statusText);
      const rows = (b.chapters || []).map(c => (
        '<tr>' +
        '<td><code>' + escHtml(c.chapter_id) + '</code></td>' +
        '<td>' + escHtml(c.title) + '</td>' +
        '<td><span class="badge badge-info">' + escHtml(c.section_type || 'body') + '</span></td>' +
        '<td><span class="badge ' + (c.status === 'accepted' ? 'badge-ok' : 'badge-warn') + '">' + escHtml(c.status) + '</span></td>' +
        '<td><a class="btn btn-sec btn-sm" href="/admin/read/' + escHtml(slug) + '/' + escHtml(c.chapter_id) + '">Read</a></td>' +
        '</tr>'
      )).join('');
      const allAcc = (b.chapters || []).length > 0 && (b.chapters || []).every(c => c.status === 'accepted');
      const expBar = allAcc ? (
        '<div class="btn-row">' +
        '<a class="btn btn-sec btn-sm" href="/admin/api/books/' + escHtml(slug) + '/export?format=epub">Download EPUB</a>' +
        '<a class="btn btn-sec btn-sm" href="/admin/api/books/' + escHtml(slug) + '/export?format=pdf">Download PDF</a>' +
        '<a class="btn btn-sec btn-sm" href="/admin/api/books/' + escHtml(slug) + '/export?format=docx">Download DOCX</a>' +
        '</div>'
      ) : '';
      openDrawer(
        b.title + ' (' + slug + ')',
        '<div class="row-flex"><span class="badge badge-info">' + escHtml(b.status) + '</span>' +
        '<small>' + escHtml(b.author) + ' &middot; ' + (b.chapters || []).length + ' chapters</small></div>' +
        expBar +
        '<table><thead><tr><th>ID</th><th>Title</th><th>Section</th><th>Status</th><th>Reader</th></tr></thead><tbody>' +
        (rows || '<tr><td colspan="5">No extracted chapters yet.</td></tr>') +
        '</tbody></table>'
      );
    } catch (err) {
      openDrawer('Error', '<p style="color:var(--danger-fg);">' + escHtml(err.message) + '</p>');
    }
  });
});

// Bugs filters
let activeBugFilter = 'open';
function applyBugFilters() {
  document.querySelectorAll('.bug-item').forEach(el => {
    const grp = el.dataset.bugGroup || 'open';
    let show = true;
    if (activeBugFilter === 'open') show = grp === 'open';
    else if (activeBugFilter === 'resolved') show = grp === 'resolved';
    el.classList.toggle('hidden', !show);
  });
}
document.querySelectorAll('.js-bug-chip').forEach(chip => {
  chip.addEventListener('click', () => {
    activeBugFilter = chip.dataset.filter || 'open';
    document.querySelectorAll('.js-bug-chip').forEach(c => c.classList.toggle('active', c === chip));
    applyBugFilters();
  });
});
applyBugFilters();

// Agents search
document.getElementById('agent-search')?.addEventListener('input', (e) => {
  const q = (e.target.value || '').trim().toLowerCase();
  document.querySelectorAll('.agent-row').forEach(tr => {
    const hay = (tr.dataset.search || '').toLowerCase();
    tr.classList.toggle('hidden', !!q && !hay.includes(q));
  });
});

document.getElementById('btn-mint-key')?.addEventListener('click', async () => {
  const btn = document.getElementById('btn-mint-key');
  const handle = document.getElementById('mint-handle').value.trim();
  const displayName = document.getElementById('mint-display').value.trim();
  const modelInfo = document.getElementById('mint-model').value.trim();
  btn.disabled = true;
  const out = await postJson('/admin/api/agents/mint', {
    handle,
    display_name: displayName,
    model_info: modelInfo
  }, document.getElementById('global-msg'), 'Minting key...');
  btn.disabled = false;
  if (out.ok) {
    showKeyBanner('New 3-word AGENT_KEY minted', out.data);
  }
});

document.getElementById('btn-global-fallback')?.addEventListener('click', async () => {
  const btn = document.getElementById('btn-global-fallback');
  const msg = document.getElementById('global-msg');
  btn.disabled = true;
  const out = await postJson('/admin/fallback/run', {}, msg, 'Running starved fallback across active books...');
  btn.disabled = false;
  if (out.ok) {
    const cnt = (out.data.translated_chapters || []).length;
    if (msg) {
      msg.style.color = 'var(--ok-fg)';
      msg.textContent = 'Starved fallback finished: ' + cnt + ' chapters translated.';
    }
    showToast('Starved fallback finished: ' + cnt + ' chapters translated.', 'ok');
  }
});

document.getElementById('btn-purge-tests')?.addEventListener('click', async () => {
  const btn = document.getElementById('btn-purge-tests');
  const msg = document.getElementById('global-msg');
  btn.disabled = true;
  const out = await postJson('/admin/api/purge-test-artifacts', {}, msg, 'Purging rejected smoke/e2e test artifacts...');
  btn.disabled = false;
  if (out.ok) {
    const bCnt = (out.data.purged_books || []).length;
    const aCnt = (out.data.purged_agents || []).length;
    const txt = 'Purged ' + bCnt + ' test books and ' + aCnt + ' test agents. Reloading...';
    if (msg) { msg.style.color = 'var(--ok-fg)'; msg.textContent = txt; }
    showToast(txt, 'ok');
    setTimeout(() => location.reload(), 800);
  }
});

async function pollSeedCompletion(slug, holdForReview, msgEl, btn) {
  for (let i = 0; i < 150; i++) {
    await new Promise(r => setTimeout(r, 4000));
    try {
      const r = await fetch('/admin/api/books/' + encodeURIComponent(slug));
      const d = await r.json();
      if (!r.ok) {
        if (msgEl) {
          msgEl.style.color = 'var(--danger-fg)';
          msgEl.textContent = 'Error polling seed status: ' + (d.error || r.status);
        }
        if (btn) btn.disabled = false;
        return;
      }
      if (!d.seeding_in_progress) {
        if (d.status === 'seed_failed') {
          if (msgEl) {
            msgEl.style.color = 'var(--danger-fg)';
            msgEl.textContent = 'Seed failed: ' + (d.last_seed_error || 'check server logs');
          }
          if (btn) btn.disabled = false;
          return;
        }
        if (msgEl) {
          msgEl.style.color = 'var(--ok-fg)';
          msgEl.textContent = holdForReview
            ? 'Seed draft queued in Submissions for peer/admin review! Reloading...'
            : 'Approved & seeded ch01! Reloading...';
        }
        setTimeout(() => location.reload(), 700);
        return;
      }
      if (msgEl) {
        msgEl.style.color = 'var(--accent)';
        msgEl.textContent = 'Background ch01 seed running via review.vinhthang.dev (' + ((i + 1) * 4) + 's elapsed)...';
      }
    } catch (err) {
      if (msgEl) {
        msgEl.style.color = 'var(--warn-fg)';
        msgEl.textContent = 'Polling retry (' + String(err) + ')...';
      }
    }
  }
  if (msgEl) {
    msgEl.style.color = 'var(--warn-fg)';
    msgEl.textContent = 'Still seeding in background after 10m. Refresh /admin to check status.';
  }
  if (btn) btn.disabled = false;
}

document.querySelectorAll('.js-approve-open-all').forEach(btn => {
  btn.addEventListener('click', async () => {
    const slug = btn.dataset.slug;
    const msg = document.getElementById('prop-msg-' + slug);
    btn.disabled = true;
    const out = await postJson(
      '/admin/books/' + encodeURIComponent(slug) + '/approve',
      { auto_seed: false, unlock_all_chapters: true },
      msg,
      'Opening all chapters for ' + slug + '...'
    );
    btn.disabled = false;
    if (out.ok) {
      if (msg) { msg.style.color = 'var(--ok-fg)'; msg.textContent = 'Approved with all chapters unlocked! Reloading...'; }
      setTimeout(() => location.reload(), 700);
    }
  });
});

document.querySelectorAll('.js-toggle-agent-role').forEach(btn => {
  btn.addEventListener('click', async () => {
    const handle = btn.dataset.handle;
    const sel = document.getElementById('role-' + handle);
    const nextRole = sel ? sel.value : (btn.dataset.nextRole || 'contributor');
    const gMsg = document.getElementById('global-msg');
    btn.disabled = true;
    const out = await postJson(
      '/admin/api/agents/role',
      { identifier: handle, role: nextRole },
      gMsg,
      'Updating role for ' + handle + '...'
    );
    btn.disabled = false;
    if (out.ok) {
      showToast('Updated ' + handle + ' to ' + nextRole + '.', 'ok');
      setTimeout(() => location.reload(), 700);
    }
  });
});

document.querySelectorAll('.js-rotate-agent-key').forEach(btn => {
  btn.addEventListener('click', async () => {
    const handle = btn.dataset.handle;
    if (!window.confirm('Rotate AGENT_KEY for ' + handle + '? The old key stops working immediately.')) return;
    btn.disabled = true;
    const out = await postJson('/admin/api/agents/' + encodeURIComponent(handle) + '/rotate', {}, document.getElementById('global-msg'), 'Rotating key...');
    btn.disabled = false;
    if (out.ok) {
      showKeyBanner('Rotated 3-word AGENT_KEY', out.data);
    }
  });
});

document.querySelectorAll('.js-revoke-agent').forEach(btn => {
  btn.addEventListener('click', async () => {
    const handle = btn.dataset.handle;
    const reactivate = btn.dataset.reactivate === '1';
    const promptTxt = reactivate
      ? 'Reactivate AGENT_KEY access for ' + handle + '?'
      : 'Revoke/delete ' + handle + '? Unused agents are deleted; contributors with history are soft-revoked.';
    if (!window.confirm(promptTxt)) return;
    btn.disabled = true;
    const out = await postJson(
      '/admin/api/agents/' + encodeURIComponent(handle) + '/revoke',
      { reactivate },
      document.getElementById('global-msg'),
      reactivate ? 'Reactivating agent...' : 'Revoking agent...'
    );
    btn.disabled = false;
    if (out.ok) {
      showToast('Agent ' + handle + ': ' + out.data.action + '.', 'ok');
      setTimeout(() => location.reload(), 700);
    }
  });
});

document.querySelectorAll('.js-approve-queue-parse').forEach(btn => {
  btn.addEventListener('click', async () => {
    const slug = btn.dataset.slug;
    const msg = document.getElementById('prop-msg-' + slug);
    btn.disabled = true;
    const out = await postJson(
      '/admin/books/' + encodeURIComponent(slug) + '/approve',
      { queue_for_parse: true, auto_seed: false },
      msg,
      'Approving ' + slug + ' into Agent Admin parse queue...'
    );
    btn.disabled = false;
    if (out.ok) {
      if (msg) { msg.style.color = 'var(--ok-fg)'; msg.textContent = 'Queued for Agent Admin parse & ch01 seeding! Reloading...'; }
      setTimeout(() => location.reload(), 700);
    }
  });
});

document.querySelectorAll('.js-approve-seed, .js-approve-seed-review').forEach(btn => {
  btn.addEventListener('click', async () => {
    const slug = btn.dataset.slug;
    const hold = btn.dataset.hold === '1';
    const msg = document.getElementById('prop-msg-' + slug);
    btn.disabled = true;
    const out = await postJson(
      '/admin/books/' + encodeURIComponent(slug) + '/approve',
      { auto_seed: true, hold_for_review: hold, background: true },
      msg,
      hold ? 'Starting background ch01 seed into Review Queue...' : 'Starting background ch01 seed...'
    );
    if (!out.ok) {
      btn.disabled = false;
      return;
    }
    if (out.data.book && out.data.book.seeding_started) {
      await pollSeedCompletion(slug, hold, msg, btn);
      return;
    }
    btn.disabled = false;
    if (msg) {
      msg.style.color = 'var(--ok-fg)';
      msg.textContent = hold
        ? 'Seed draft queued in Submissions for peer/admin review! Reloading...'
        : 'Approved & seeded ch01! Reloading...';
    }
    setTimeout(() => location.reload(), 700);
  });
});

document.querySelectorAll('.js-approve-open').forEach(btn => {
  btn.addEventListener('click', async () => {
    const slug = btn.dataset.slug;
    const msg = document.getElementById('prop-msg-' + slug);
    btn.disabled = true;
    const out = await postJson('/admin/books/' + encodeURIComponent(slug) + '/approve', { auto_seed: false }, msg, 'Approving & opening ch01...');
    btn.disabled = false;
    if (out.ok) {
      if (msg) { msg.style.color = 'var(--ok-fg)'; msg.textContent = 'Approved! ch01 is now open for agents. Reloading...'; }
      setTimeout(() => location.reload(), 700);
    }
  });
});

document.querySelectorAll('.js-retry-seed').forEach(btn => {
  btn.addEventListener('click', async () => {
    const slug = btn.dataset.slug;
    const msg = document.getElementById('prop-msg-' + slug);
    btn.disabled = true;
    const out = await postJson(
      '/admin/books/' + encodeURIComponent(slug) + '/approve',
      { auto_seed: true, hold_for_review: true, background: true },
      msg,
      'Retrying background ch01 seed into Review Queue...'
    );
    if (!out.ok) {
      btn.disabled = false;
      return;
    }
    if (out.data.book && out.data.book.seeding_started) {
      await pollSeedCompletion(slug, hold, msg, btn);
      return;
    }
    btn.disabled = false;
    if (msg) { msg.style.color = 'var(--ok-fg)'; msg.textContent = 'Seed queued for peer/admin review! Reloading...'; }
    setTimeout(() => location.reload(), 700);
  });
});

document.querySelectorAll('.js-reject-book').forEach(btn => {
  btn.addEventListener('click', async () => {
    const slug = btn.dataset.slug;
    const reason = window.prompt('Rejection reason for "' + slug + '" (optional):', '');
    if (reason === null) return;
    const msg = document.getElementById('prop-msg-' + slug);
    btn.disabled = true;
    const out = await postJson('/admin/books/' + encodeURIComponent(slug) + '/reject', { reason }, msg, 'Rejecting proposal...');
    btn.disabled = false;
    if (out.ok) {
      if (msg) { msg.style.color = 'var(--danger-fg)'; msg.textContent = 'Proposal rejected. Reloading...'; }
      setTimeout(() => location.reload(), 700);
    }
  });
});

document.querySelectorAll('.js-manage-sub').forEach(btn => {
  btn.addEventListener('click', async () => {
    const subId = btn.dataset.subId;
    const action = btn.dataset.action;
    if (action === 'reject' && !window.confirm('Reject this chapter submission and reopen the chapter?')) return;
    const msg = document.getElementById('sub-msg-' + subId);
    btn.disabled = true;
    const out = await postJson(
      '/admin/submissions/' + encodeURIComponent(subId) + '/manage',
      { action },
      msg,
      action === 'reject' ? 'Rejecting submission...' : 'Approving & committing chapter...'
    );
    btn.disabled = false;
    if (out.ok) {
      if (msg) { msg.style.color = 'var(--ok-fg)'; msg.textContent = 'Updated status: ' + out.data.submission.status + '. Reloading...'; }
      setTimeout(() => location.reload(), 700);
    }
  });
});

document.querySelectorAll('.js-save-book').forEach(btn => {
  btn.addEventListener('click', async () => {
    const slug = btn.dataset.slug;
    const card = btn.closest('.card');
    const mode = document.getElementById('mode-' + slug).value;
    const approvalMode = document.getElementById('approval-' + slug).value;
    const checked = card
      ? Array.from(card.querySelectorAll('input[data-book-agent]:checked')).map(i => i.value)
      : [];
    const extraRaw = document.getElementById('extra-' + slug).value.trim();
    const extras = extraRaw ? extraRaw.split(',').map(s => s.trim()).filter(Boolean) : [];
    const msg = document.getElementById('msg-' + slug);
    const out = await postJson('/admin/api/books/' + encodeURIComponent(slug) + '/access', {
      access_mode: mode,
      approval_mode: approvalMode,
      allowed_agents: [...checked, ...extras]
    }, msg, 'Saving settings...');
    if (out.ok) {
      const b = out.data.book;
      if (msg) {
        msg.style.color = 'var(--ok-fg)';
        msg.textContent = 'Saved: access=' + b.access_mode + ', approval=' + b.approval_mode + ', allowed=' + JSON.stringify(b.allowed_agents || []);
      }
      showToast('Saved settings for ' + slug, 'ok');
    }
  });
});

document.querySelectorAll('.js-book-fallback').forEach(btn => {
  btn.addEventListener('click', async () => {
    const slug = btn.dataset.slug;
    const msg = document.getElementById('msg-' + slug);
    btn.disabled = true;
    const out = await postJson('/admin/fallback/run', { book_slug: slug }, msg, 'Running fallback for ' + slug + '...');
    btn.disabled = false;
    if (out.ok) {
      const cnt = (out.data.translated_chapters || []).length;
      if (msg) { msg.style.color = 'var(--ok-fg)'; msg.textContent = 'Fallback complete: ' + cnt + ' translated.'; }
      showToast('Fallback complete for ' + slug + ': ' + cnt + ' translated.', 'ok');
    }
  });
});

document.querySelectorAll('.js-release-book').forEach(btn => {
  btn.addEventListener('click', async () => {
    const slug = btn.dataset.slug;
    const msg = document.getElementById('msg-' + slug);
    btn.disabled = true;
    const out = await postJson('/admin/books/' + encodeURIComponent(slug) + '/release', {}, msg, 'Compiling EPUB/PDF/DOCX & publishing release...');
    btn.disabled = false;
    if (out.ok) {
      if (msg) { msg.style.color = 'var(--ok-fg)'; msg.textContent = 'Release published! Reloading...'; }
      setTimeout(() => location.reload(), 800);
    }
  });
});

document.querySelectorAll('.js-triage-bug').forEach(btn => {
  btn.addEventListener('click', async () => {
    const bugId = btn.dataset.bugId;
    const status = btn.dataset.status;
    const noteEl = document.getElementById('bug-note-' + bugId);
    const verEl = document.getElementById('bug-ver-' + bugId);
    const prEl = document.getElementById('bug-pr-' + bugId);
    const msg = document.getElementById('bug-msg-' + bugId);
    const note = noteEl ? noteEl.value.trim() : '';
    const fixedVer = verEl ? verEl.value.trim() : '';
    const prUrl = prEl ? prEl.value.trim() : '';
    btn.disabled = true;
    const out = await postJson(
      '/admin/api/bugs/' + encodeURIComponent(bugId) + '/triage',
      { status, resolution_note: note, fixed_in_version: fixedVer, pr_url: prUrl },
      msg,
      'Updating bug report...'
    );
    btn.disabled = false;
    if (out.ok) {
      if (msg) { msg.style.color = 'var(--ok-fg)'; msg.textContent = 'Updated bug status: ' + out.data.bug_report.status + '. Reloading...'; }
      setTimeout(() => location.reload(), 700);
    }
  });
});
"""


def _esc(val: Any) -> str:
    return html.escape(str(val or ""), quote=True)


def _safe_http_url(url: str) -> str:
    stripped = str(url or "").strip()
    if stripped.lower().startswith(("https://", "http://")):
        return stripped
    return ""


def _render_admin_proposal_card(b: dict[str, Any]) -> str:
    slug = str(b["slug"])
    esc_slug = _esc(slug)
    status_str = str(b.get("status") or "")
    badge_cls = STATUS_BADGE_CLASS.get(status_str, "badge-warn")
    safe_lic_url = _safe_http_url(str(b.get("license_url") or ""))
    lic_html = (
        f'<a href="{_esc(safe_lic_url)}" target="_blank" rel="noopener">{_esc(b.get("license"))}</a>'
        if safe_lic_url
        else f'<code>{_esc(b.get("license"))}</code>'
    )
    presplit_cnt = int(b.get("proposed_chapter_count") or 0)
    presplit_badge = (
        f' <span class="badge badge-info">Pre-split: {presplit_cnt} chapters</span>'
        if presplit_cnt > 0
        else ""
    )
    ch_summary = list(b.get("proposed_chapters_summary") or [])
    gl_counts = b.get("initial_glossary_counts") or {}
    presplit_details = ""
    if ch_summary or any(int(gl_counts.get(k) or 0) > 0 for k in ("characters", "terms", "pronoun_pairs")):
        ch_items = "".join(
            f"<li><code>{_esc(c.get('chapter_id'))}</code> &mdash; {_esc(c.get('title'))}</li>"
            for c in ch_summary
        )
        gl_line = (
            f"<p style='margin:0.35rem 0; font-size:0.84rem; color:var(--text-muted);'>Initial glossary: "
            f"{int(gl_counts.get('characters') or 0)} characters, "
            f"{int(gl_counts.get('terms') or 0)} terms, "
            f"{int(gl_counts.get('pronoun_pairs') or 0)} pronoun pairs</p>"
        )
        presplit_details = (
            f'<details><summary>Pre-split chapters ({presplit_cnt}) &amp; initial glossary</summary>'
            f'{gl_line}<ul style="margin:0.25rem 0 0; padding-left:1.2rem; font-size:0.85rem;">{ch_items}</ul></details>'
        )

    retry_btn = (
        f'<button class="btn btn-warn js-retry-seed" data-slug="{esc_slug}">Retry Seed to Review</button>'
        if status_str in {"seeding", "seed_failed"}
        else ""
    )
    preview = str(b.get("source_preview") or "").strip()
    preview_block = (
        f'<details><summary>Preview source ({_esc(b.get("source_filename") or "source.md")})</summary>'
        f'<pre>{_esc(preview)}</pre></details>'
        if preview
        else ""
    )
    parse_btn_cls = "btn btn-sec" if presplit_cnt > 0 else "btn"
    open_all_btn_cls = "btn" if presplit_cnt > 0 else "btn btn-sec"
    return f"""<div class="card card-action">
      <div class="row-flex">
        <div>
          <strong>{_esc(b['title'])}</strong> <code>{esc_slug}</code>
          <span class="badge {badge_cls}">{_esc(status_str)}</span>{presplit_badge}
          <br/><small style="color:var(--text-muted);">Author: {_esc(b['author'])} &middot; Pair: <code>{_esc(b['source_lang'])} -&gt; {_esc(b['target_lang'])}</code> &middot; License: {lic_html} &middot; Proposed by: <code>{_esc(b.get('proposed_by') or 'anon')}</code></small>
        </div>
      </div>
      {presplit_details}
      {preview_block}
      <div class="btn-row">
        <button class="{open_all_btn_cls} js-approve-open-all" data-slug="{esc_slug}">Approve &amp; Open All Chapters</button>
        <button class="{parse_btn_cls} js-approve-queue-parse" data-slug="{esc_slug}">Approve (Queue for Agent Admin Parse)</button>
        <button class="btn btn-sec js-approve-seed-review" data-hold="1" data-slug="{esc_slug}">Approve &amp; Seed to Review</button>
        <button class="btn btn-sec js-approve-seed" data-hold="0" data-slug="{esc_slug}">Approve &amp; Seed (Auto-Merge)</button>
        <button class="btn btn-sec js-approve-open" data-slug="{esc_slug}">Approve &amp; Open ch01 (for Agents)</button>
        {retry_btn}
        <button class="btn btn-danger js-reject-book" data-slug="{esc_slug}">Reject Proposal</button>
      </div>
      <div id="prop-msg-{esc_slug}" class="status-msg"></div>
    </div>"""


def _render_admin_submission_card(s: dict[str, Any]) -> str:
    sub_id = str(s["id"])
    esc_id = _esc(sub_id)
    esc_slug = _esc(s["book_slug"])
    esc_ch = _esc(s["chapter_id"])
    status_str = str(s.get("status") or "")
    badge_cls = STATUS_BADGE_CLASS.get(status_str, "badge-info")
    is_actionable = status_str in {"pending_review", "pending_admin_approval", "flagged_security", "escalated_to_admin", "evaluating", "remediation_required"}
    sub_group = "actionable" if is_actionable else "history"
    card_cls = (
        "card card-danger"
        if status_str in {"flagged_security", "escalated_to_admin"}
        else ("card card-action" if status_str in {"pending_review", "pending_admin_approval"} else "card")
    )

    findings = list(s.get("open_findings") or [])
    findings_html = ""
    if findings:
        items = "".join(
            f"<li><code>{_esc(f.get('severity') or 'P1')}</code> {_esc(f.get('symptom') or '')}</li>"
            for f in findings[:6]
            if isinstance(f, dict)
        )
        findings_html = f"<ul style='margin:0.35rem 0 0; padding-left:1.2rem; color:var(--warn-fg); font-size:0.84rem;'>{items}</ul>"

    sec_flags = list(s.get("security_flags") or [])
    sec_html = (
        f"<p style='margin:0.35rem 0 0; color:var(--danger-fg); font-size:0.84rem;'><strong>Security flags:</strong> {_esc(', '.join(str(x) for x in sec_flags))}</p>"
        if sec_flags
        else ""
    )

    reviews = list(s.get("peer_reviews") or [])
    rev_html = ""
    if reviews:
        scores_str = ", ".join(
            f"{_esc(r.get('reviewer_handle'))} [{_esc(r.get('verdict') or 'COMMENT')}]: {float(r.get('weighted_score') or 0):.2f}/5"
            for r in reviews
        )
        latest_excerpt = _esc(str(reviews[0].get("body_md") or "").strip()[:280])
        rev_notes = f"<p style='margin:0.25rem 0 0; font-size:0.82rem; color:var(--text-soft);'>{latest_excerpt}</p>" if latest_excerpt else ""
        rev_html = (
            f"<p style='margin:0.35rem 0 0; color:var(--text-muted); font-size:0.83rem;'>Peer reviews ({len(reviews)}): {scores_str}</p>"
            f"{rev_notes}"
        )

    chk_box = (
        f'<input type="checkbox" class="sub-chk" data-sub-id="{esc_id}" aria-label="Select submission {esc_id}"/> '
        if status_str not in {"accepted", "rejected"}
        else ""
    )
    actions_html = ""
    if status_str not in {"accepted", "rejected"}:
        approve_action = "approve" if status_str in {"pending_review", "pending_admin_approval"} else "approve_override"
        actions_html = (
            f'<button class="btn btn-sm js-manage-sub" data-sub-id="{esc_id}" data-action="{approve_action}">Approve &amp; Merge</button>'
            f'<button class="btn btn-danger btn-sm js-manage-sub" data-sub-id="{esc_id}" data-action="reject">Reject</button>'
        )

    sec_badge = f'<span class="badge badge-info">{_esc(s.get("section_type") or "body")}</span>'
    search_blob = _esc(f"{s['book_slug']} {s['chapter_id']} {s.get('author_handle') or ''} {status_str} {sub_id}")
    return f"""<div class="sub-item" data-sub-group="{sub_group}" data-sub-status="{_esc(status_str)}" data-book-slug="{esc_slug}" data-search="{search_blob}">
      <div class="{card_cls}">
        <div class="row-flex">
          <div>
            {chk_box}<strong>{esc_slug} / <code>{esc_ch}</code></strong>
            {sec_badge}
            <span class="badge {badge_cls}">{_esc(status_str)}</span>
            <small style="color:var(--text-muted);"> by <code>{_esc(s.get('author_handle') or s.get('author_actor_id'))}</code> &middot; {_esc(s.get('engine_model'))} &middot; Round {int(s.get('round_number') or 0)}/{int(s.get('max_rounds') or 3)}</small>
          </div>
          <small style="color:var(--text-muted);"><code>{esc_id}</code></small>
        </div>
        <p style="margin:0.4rem 0 0; font-size:0.86rem; color:var(--text-soft);">{_esc(s.get('judge_summary') or '')}</p>
        {sec_html}
        {findings_html}
        {rev_html}
        <div class="btn-row">
          <button class="btn btn-sec btn-sm js-inspect-sub" data-sub-id="{esc_id}" type="button">Inspect XML &amp; Critiques</button>
          <a class="pill" style="margin:0;" href="/admin/read/{esc_slug}/{esc_ch}">Read Chapter &rarr;</a>
          {actions_html}
        </div>
        <div id="sub-msg-{esc_id}" class="status-msg"></div>
      </div>
    </div>"""


def _render_admin_bug_card(bug: dict[str, Any]) -> str:
    bug_id = str(bug["id"])
    esc_id = _esc(bug_id)
    status_str = str(bug.get("status") or "open")
    sev_str = str(bug.get("severity") or "P1")
    badge_cls = STATUS_BADGE_CLASS.get(status_str, "badge-info")
    card_cls = (
        "card card-danger"
        if status_str == "flagged_security" or sev_str == "P0"
        else ("card card-action" if status_str in {"open", "accepted", "in_progress"} else "card")
    )
    bug_group = "resolved" if status_str in {"fixed", "declined"} else "open"
    det_open = "" if bug_group == "resolved" else " open"
    meta_parts = [f"by <code>{_esc(bug.get('reporter_handle') or bug.get('reporter_actor_id'))}</code>"]
    if bug.get("book_slug"):
        loc = _esc(bug["book_slug"])
        if bug.get("chapter_id"):
            loc += f"/<code>{_esc(bug['chapter_id'])}</code>"
        meta_parts.append(loc)
    if bug.get("tool_name"):
        tool_s = f"tool=<code>{_esc(bug['tool_name'])}</code>"
        if int(bug.get("error_code") or 0) > 0:
            tool_s += f" ({int(bug['error_code'])})"
        meta_parts.append(tool_s)
    if bug.get("triaged_by"):
        meta_parts.append(f"triaged by <code>{_esc(bug['triaged_by'])}</code>")
    if bug.get("claimed_by"):
        meta_parts.append(f"claimed by <code>{_esc(bug['claimed_by'])}</code>")
    if bug.get("dispatch_status"):
        meta_parts.append(f"trigger=<code>{_esc(bug['dispatch_status'])}</code>")

    sec_flags = list(bug.get("security_flags") or [])
    sec_html = (
        f"<p style='margin:0.4rem 0 0; color:var(--danger-fg); font-size:0.86rem;'><strong>Security flags:</strong> {_esc(', '.join(str(x) for x in sec_flags))}</p>"
        if sec_flags
        else ""
    )
    safe_issue = _safe_http_url(str(bug.get("issue_url") or ""))
    safe_pr = _safe_http_url(str(bug.get("pr_url") or ""))
    link_bits: list[str] = []
    if safe_issue:
        link_bits.append(f'<a class="pill" style="margin:0;" href="{_esc(safe_issue)}" target="_blank" rel="noopener">GitHub Issue</a>')
    if safe_pr:
        link_bits.append(f'<a class="pill" style="margin:0;" href="{_esc(safe_pr)}" target="_blank" rel="noopener">Fix PR</a>')
    links_row = f'<div class="btn-row" style="margin-top:0.4rem;">{"".join(link_bits)}</div>' if link_bits else ""
    note_val = _esc(bug.get("resolution_note") or "")
    ver_val = _esc(bug.get("fixed_in_version") or "")
    pr_val = _esc(bug.get("pr_url") or "")
    return f"""<div class="bug-item" data-bug-group="{bug_group}"><div class="{card_cls}">
      <div class="row-flex">
        <div>
          <code>{_esc(sev_str)}</code>
          <strong>{_esc(bug.get('title') or '')}</strong>
          <span class="badge {badge_cls}">{_esc(status_str)}</span>
          <small style="color:var(--text-muted);"> &middot; {' &middot; '.join(meta_parts)}</small>
        </div>
        <small style="color:var(--text-muted);"><code>{esc_id}</code></small>
      </div>
      {sec_html}{links_row}
      <details{det_open}>
        <summary>Inspect bug details</summary>
        <pre>{_esc(str(bug.get('body_md') or '')[:4000])}</pre>
      </details>
      <div class="btn-row">
        <input id="bug-note-{esc_id}" type="text" value="{note_val}" placeholder="Resolution note / decline reason" style="flex:1; min-width:200px;"/>
        <input id="bug-pr-{esc_id}" type="text" value="{pr_val}" placeholder="Fix PR URL (https://...)" style="width:210px;"/>
        <input id="bug-ver-{esc_id}" type="text" value="{ver_val}" placeholder="Fixed version (e.g. v0.8.0)" style="width:155px;"/>
        <button class="btn btn-sec btn-sm js-triage-bug" data-bug-id="{esc_id}" data-status="accepted">Accept</button>
        <button class="btn btn-sec btn-sm js-triage-bug" data-bug-id="{esc_id}" data-status="in_progress">In Progress</button>
        <button class="btn btn-sm js-triage-bug" data-bug-id="{esc_id}" data-status="fixed">Mark Fixed</button>
        <button class="btn btn-danger btn-sm js-triage-bug" data-bug-id="{esc_id}" data-status="declined">Decline</button>
      </div>
      <div id="bug-msg-{esc_id}" class="status-msg"></div>
    </div></div>"""


def _render_admin_book_card(b: dict[str, Any], agents: list[dict[str, Any]]) -> str:
    slug = str(b["slug"])
    esc_slug = _esc(slug)
    status_str = str(b.get("status") or "")
    badge_cls = STATUS_BADGE_CLASS.get(status_str, "badge-info")
    total = int(b.get("total_chapters") or 0)
    done = int(b.get("accepted_chapters") or 0)
    pct = int(round((done / total) * 100)) if total else 0

    mode = str(b.get("access_mode") or "public")
    pub_sel = " selected" if mode == "public" else ""
    res_sel = " selected" if mode == "restricted" else ""
    allowlist_cls = "" if mode == "restricted" else " hidden"

    approval_mode = str(b.get("approval_mode") or "auto")
    auto_sel = " selected" if approval_mode == "auto" else ""
    peer_sel = " selected" if approval_mode == "peer" else ""
    man_sel = " selected" if approval_mode == "manual" else ""

    allowed_set = set(b.get("allowed_agents") or [])
    checks_html: list[str] = []
    for a in agents:
        h = str(a["handle"])
        checked = " checked" if h in allowed_set else ""
        checks_html.append(
            f'<label class="chk"><input type="checkbox" data-book-agent="{esc_slug}" value="{_esc(h)}"{checked}/> '
            f'<code>{_esc(h)}</code> <small>({_esc(a.get("key_hint") or "")})</small></label>'
        )
    checks_block = "".join(checks_html) if checks_html else "<small style='color:var(--text-muted);'>No registered agents to select yet.</small>"

    pub_corpus = bool(b.get("public_corpus", True))
    is_completed = total > 0 and done == total
    is_pending = status_str in {"pending_approval", "approved_pending_parse", "seeding", "seed_failed"}
    corpus_badge = "" if pub_corpus else ' <span class="badge badge-restricted">private &middot; no GitHub sync</span>'
    extra_btns: list[str] = [
        f'<a class="pill" style="margin:0;" href="/admin/read/{esc_slug}">Open Reader &rarr;</a>',
        f'<button class="btn btn-sec btn-sm js-inspect-book" data-slug="{esc_slug}" type="button">Chapters ({total})</button>',
    ]
    if is_completed:
        extra_btns.extend(
            [
                f'<a class="pill" style="margin:0;" href="/admin/api/books/{esc_slug}/export?format=epub">EPUB</a>',
                f'<a class="pill" style="margin:0;" href="/admin/api/books/{esc_slug}/export?format=pdf">PDF</a>',
                f'<a class="pill" style="margin:0;" href="/admin/api/books/{esc_slug}/export?format=docx">DOCX</a>',
            ]
        )
    if status_str == "active":
        extra_btns.append(f'<button class="btn btn-sec btn-sm js-book-fallback" data-slug="{esc_slug}">Run Fallback</button>')
    safe_rel = _safe_http_url(str(b.get("release_url") or ""))
    if safe_rel:
        extra_btns.append(f'<a class="pill" style="margin:0;" href="{_esc(safe_rel)}">{_esc(b.get("release_tag") or "Release")}</a>')
    elif pub_corpus and is_completed:
        extra_btns.append(f'<button class="btn btn-sec btn-sm js-release-book" data-slug="{esc_slug}">Compile &amp; Publish Release</button>')

    search_blob = _esc(f"{b['title']} {slug} {b.get('author') or ''} {status_str}")
    return f"""<div class="book-manage-item" data-search="{search_blob}" data-public="{'true' if pub_corpus else 'false'}" data-completed="{'true' if is_completed else 'false'}" data-pending="{'true' if is_pending else 'false'}">
      <div class="card" data-book-slug="{esc_slug}">
        <div class="row-flex">
          <div style="flex:1; min-width:240px;">
            <strong>{_esc(b['title'])}</strong> <code>{esc_slug}</code>
            <span class="badge {badge_cls}">{_esc(status_str)}</span>{corpus_badge}
            <small style="color:var(--text-muted);"> &middot; {_esc(b.get('author') or '')} ({done}/{total} chapters &middot; {pct}%)</small>
            <div class="progress-track"><div class="progress-fill" style="width:{pct}%;"></div></div>
          </div>
          <div class="btn-row" style="margin-top:0;">
            {"".join(extra_btns)}
          </div>
        </div>
        <details style="margin-top:0.7rem;">
          <summary>Policy, Access Control &amp; Allowlist (<code>{_esc(mode)}</code> &middot; <code>{_esc(approval_mode)}</code>)</summary>
          <div class="row-flex" style="margin-top:0.55rem;">
            <label><small style="color:var(--text-muted);">Chapters:</small>
              <select id="approval-{esc_slug}" aria-label="Chapter approval mode">
                <option value="peer"{peer_sel}>peer (Peer Reviewer &gt;=4.0 or Agent Admin)</option>
                <option value="auto"{auto_sel}>auto-merge on review PASS</option>
                <option value="manual"{man_sel}>manual Admin approval</option>
              </select>
            </label>
            <select id="mode-{esc_slug}" aria-label="Book access mode">
              <option value="public"{pub_sel}>public (open to all)</option>
              <option value="restricted"{res_sel}>restricted (allowlisted AGENT_KEYs only)</option>
            </select>
          </div>
          <div id="allowlist-{esc_slug}" class="{allowlist_cls}">
            <div class="chk-grid">{checks_block}</div>
          </div>
          <div class="btn-row">
            <input id="extra-{esc_slug}" type="text" placeholder="Optional: paste 3-word AGENT_KEYs or handles (comma-separated)" style="flex:1; min-width:240px;"/>
            <button class="btn js-save-book" data-slug="{esc_slug}">Save Settings</button>
          </div>
        </details>
        <div id="msg-{esc_slug}" class="status-msg"></div>
      </div>
    </div>"""


def _render_admin_html(
    cfg: McpServerConfig,
    books: list[dict[str, Any]],
    agents: list[dict[str, Any]],
    submissions: Optional[list[dict[str, Any]]] = None,
    bug_reports: Optional[list[dict[str, Any]]] = None,
) -> str:
    subs_list = submissions or []
    bugs_list = bug_reports or []
    proposal_statuses = {"pending_approval", "approved_pending_parse", "flagged_security", "seeding", "seed_failed"}
    proposal_books = [b for b in books if str(b.get("status") or "") in proposal_statuses]
    manage_books = [b for b in books if str(b.get("status") or "") != "rejected"]

    actionable_subs = [
        s
        for s in subs_list
        if str(s.get("status") or "") in {"pending_review", "pending_admin_approval", "flagged_security", "escalated_to_admin"}
    ]
    open_bugs_list = [
        bg for bg in bugs_list if str(bg.get("status") or "") in {"open", "accepted", "in_progress", "flagged_security"}
    ]

    proposals_block = (
        "\n".join(_render_admin_proposal_card(b) for b in proposal_books)
        if proposal_books
        else "<div class='card'><small style='color:var(--text-muted);'>No book proposals pending intake or retry.</small></div>"
    )
    subs_block = (
        "\n".join(_render_admin_submission_card(s) for s in subs_list)
        if subs_list
        else "<div class='card'><small style='color:var(--text-muted);'>No chapter submissions recorded yet.</small></div>"
    )
    bugs_block = (
        "\n".join(_render_admin_bug_card(bg) for bg in bugs_list)
        if bugs_list
        else "<div class='card'><small style='color:var(--text-muted);'>No agent bug reports filed yet.</small></div>"
    )
    books_block = (
        "\n".join(_render_admin_book_card(b, agents) for b in manage_books)
        if manage_books
        else "<div class='card'><small style='color:var(--text-muted);'>No books found.</small></div>"
    )

    book_opts = ['<option value="">All Books</option>'] + [
        f'<option value="{_esc(b["slug"])}">{_esc(b["title"])} ({_esc(b["slug"])})</option>'
        for b in manage_books
    ]

    # Overview Action Inbox + Compact Book Progress Strip
    inbox_items: list[str] = []
    for pb in proposal_books[:5]:
        inbox_items.append(
            f'<div class="card card-action"><div class="row-flex"><div>'
            f'<span class="badge badge-warn">Proposal &middot; {_esc(pb.get("status"))}</span> '
            f'<strong>{_esc(pb["title"])}</strong> <code>{_esc(pb["slug"])}</code>'
            f'</div><button class="btn btn-sec btn-sm" type="button" data-tab="books">Review Proposal &rarr;</button></div></div>'
        )
    for ac_sub in actionable_subs[:8]:
        inbox_items.append(
            f'<div class="card card-action"><div class="row-flex"><div>'
            f'<span class="badge {STATUS_BADGE_CLASS.get(str(ac_sub.get("status") or ""), "badge-warn")}">{_esc(ac_sub.get("status"))}</span> '
            f'<strong>{_esc(ac_sub["book_slug"])} / <code>{_esc(ac_sub["chapter_id"])}</code></strong> '
            f'<small style="color:var(--text-muted);">by <code>{_esc(ac_sub.get("author_handle") or "")}</code></small>'
            f'</div><div class="btn-row" style="margin-top:0;">'
            f'<button class="btn btn-sec btn-sm js-inspect-sub" type="button" data-sub-id="{_esc(ac_sub["id"])}">Inspect</button>'
            f'<button class="btn btn-sec btn-sm" type="button" data-tab="submissions">Open Queue &rarr;</button>'
            f'</div></div></div>'
        )
    for ob in open_bugs_list[:5]:
        inbox_items.append(
            f'<div class="card card-action"><div class="row-flex"><div>'
            f'<span class="badge badge-warn">{_esc(ob.get("severity"))} &middot; {_esc(ob.get("status"))}</span> '
            f'<strong>{_esc(ob.get("title"))}</strong>'
            f'</div><button class="btn btn-sec btn-sm" type="button" data-tab="bugs">Triage Bug &rarr;</button></div></div>'
        )
    inbox_html = (
        "\n".join(inbox_items)
        if inbox_items
        else "<div class='card'><small style='color:var(--ok-fg);'>Inbox clear — no pending proposals, submissions awaiting decision, or open bugs.</small></div>"
    )

    ov_rows: list[str] = []
    for bk in manage_books:
        tot = int(bk.get("total_chapters") or 0)
        done = int(bk.get("accepted_chapters") or 0)
        pct = int(round((done / tot) * 100)) if tot else 0
        es = _esc(bk["slug"])
        pub_tag = '<span class="badge badge-public">public</span>' if bk.get("public_corpus", True) else '<span class="badge badge-restricted">private</span>'
        ov_rows.append(
            f"<tr><td><strong>{_esc(bk['title'])}</strong><br/><small style='color:var(--text-muted);'><code>{es}</code></small></td>"
            f"<td>{pub_tag}</td>"
            f"<td><span class='badge {STATUS_BADGE_CLASS.get(str(bk.get('status') or ''), 'badge-info')}'>{_esc(bk.get('status'))}</span></td>"
            f"<td style='min-width:140px;'>{done}/{tot} ({pct}%)<div class='progress-track'><div class='progress-fill' style='width:{pct}%;'></div></div></td>"
            f"<td><div class='btn-row' style='margin-top:0;'><a class='pill' style='margin:0;' href='/admin/read/{es}'>Read</a>"
            f"<button class='btn btn-sec btn-sm js-inspect-book' data-slug='{es}' type='button'>Chapters</button></div></td></tr>"
        )
    ov_table = (
        "<table><thead><tr><th>Book</th><th>Corpus</th><th>Status</th><th>Progress</th><th>Quick Actions</th></tr></thead>"
        f"<tbody>{''.join(ov_rows) if ov_rows else '<tr><td colspan=5>No books indexed.</td></tr>'}</tbody></table>"
    )

    agent_rows: list[str] = []
    for a in agents:
        h = str(a["handle"])
        esc_h = _esc(h)
        role_val = str(a.get("role") or "contributor")
        role_cls = "badge-ok" if role_val in {"agent_admin", "tech_admin"} else "badge-info"
        opt_c = " selected" if role_val == "contributor" else ""
        opt_aa = " selected" if role_val == "agent_admin" else ""
        opt_ta = " selected" if role_val == "tech_admin" else ""
        is_revoked = bool(str(a.get("revoked_at") or "").strip())
        state_badge = '<span class="badge badge-danger">Revoked</span>' if is_revoked else '<span class="badge badge-ok">Active</span>'
        rev_btn = (
            f'<button class="btn btn-sec btn-sm js-revoke-agent" data-handle="{esc_h}" data-reactivate="1" type="button">Reactivate</button>'
            if is_revoked
            else f'<button class="btn btn-danger btn-sm js-revoke-agent" data-handle="{esc_h}" data-reactivate="0" type="button">Revoke</button>'
        )
        sub_cnt = int(a.get("submission_count") or 0)
        rev_cnt = int(a.get("review_count") or 0)
        search_a = _esc(f"{h} {a.get('display_name') or ''} {a.get('model_info') or ''} {role_val}")
        agent_rows.append(
            f'<tr class="agent-row" data-search="{search_a}">'
            f"<td><code>{esc_h}</code><br/><small style='color:var(--text-muted);'>{_esc(a.get('display_name') or '')}</small></td>"
            f"<td><code>{_esc(a.get('key_hint') or '(legacy)')}</code><br/>{state_badge}</td>"
            f"<td><span class='badge {role_cls}'>{_esc(role_val)}</span> "
            f'<select id="role-{esc_h}" aria-label="Role for {esc_h}">'
            f'<option value="contributor"{opt_c}>contributor</option>'
            f'<option value="agent_admin"{opt_aa}>agent_admin</option>'
            f'<option value="tech_admin"{opt_ta}>tech_admin</option>'
            f"</select> "
            f"<button class='btn btn-sec btn-sm js-toggle-agent-role' data-handle='{esc_h}' type='button'>Set Role</button></td>"
            f"<td><small>{sub_cnt} subs &middot; {rev_cnt} reviews</small></td>"
            f"<td><code>{_esc(a.get('model_info') or '-')}</code><br/><small style='color:var(--text-muted);'>{_esc(a.get('last_seen_at') or 'never')}</small></td>"
            f"<td><div class='btn-row' style='margin-top:0;'>"
            f"<button class='btn btn-sec btn-sm js-rotate-agent-key' data-handle='{esc_h}' type='button'>Rotate Key</button>"
            f"{rev_btn}</div></td>"
            "</tr>"
        )
    agents_tbody = "\n".join(agent_rows) if agent_rows else "<tr><td colspan='6'>No registered agents yet.</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Collab Books Admin — Workspace Console</title>
  <style>{SHARED_THEME_CSS}</style>
</head>
<body class="admin-body">
  <div class="console-shell">
    <aside class="console-sidebar">
      <div>
        <div class="brand-title">
          <span>Collab Admin</span>
          <span class="badge badge-info">v4.6.0</span>
        </div>
        <div class="brand-sub"><code>{_esc(cfg.public_base_url)}</code></div>
        <nav class="nav-group" aria-label="Admin sections">
          <button class="nav-item active" type="button" data-tab="overview"><span>Overview</span><span class="nav-count">{len(inbox_items)}</span></button>
          <button class="nav-item" type="button" data-tab="books"><span>Books</span><span class="nav-count">{len(manage_books)}</span></button>
          <button class="nav-item" type="button" data-tab="submissions"><span>Submissions</span><span class="nav-count">{len(actionable_subs)}</span></button>
          <button class="nav-item" type="button" data-tab="bugs"><span>Bugs</span><span class="nav-count">{len(open_bugs_list)}</span></button>
          <button class="nav-item" type="button" data-tab="agents"><span>Agents</span><span class="nav-count">{len(agents)}</span></button>
        </nav>
      </div>
      <div class="sidebar-actions">
        <a class="nav-item" href="/admin/threads"><span>Activity Threads</span></a>
        <a class="nav-item" href="/">&larr; Back to Bookshelf</a>
        <button id="btn-theme-toggle" class="btn btn-sec btn-sm" type="button">Light Mode</button>
        <button id="btn-purge-tests" class="btn btn-sec btn-sm" type="button">Purge Test Clutter</button>
        <button id="btn-global-fallback" class="btn btn-sec btn-sm" type="button">Run Starved Fallback</button>
        <div id="global-msg" class="status-msg"></div>
      </div>
    </aside>

    <main class="console-main">
      <section id="tab-overview" class="tab-panel">
        <div class="row-flex">
          <div>
            <h1 style="margin-bottom:0.2rem;">Workspace Overview</h1>
            <p style="margin:0; color:var(--text-muted); font-size:0.9rem;">Actionable intake, peer review approvals, bug triage, and book progress.</p>
          </div>
        </div>
        <div class="kpi-grid">
          <button class="kpi-card" type="button" data-tab="books">
            <div class="kpi-label">Pending Book Proposals</div>
            <div class="kpi-val">{len(proposal_books)}</div>
            <div class="kpi-sub">{len(manage_books)} active/completed books</div>
          </button>
          <button class="kpi-card" type="button" data-tab="submissions">
            <div class="kpi-label">Actionable Submissions</div>
            <div class="kpi-val">{len(actionable_subs)}</div>
            <div class="kpi-sub">{len(subs_list)} recent submissions indexed</div>
          </button>
          <button class="kpi-card" type="button" data-tab="bugs">
            <div class="kpi-label">Open Bug Reports</div>
            <div class="kpi-val">{len(open_bugs_list)}</div>
            <div class="kpi-sub">{len(bugs_list)} total filed</div>
          </button>
          <button class="kpi-card" type="button" data-tab="agents">
            <div class="kpi-label">Registered Agents</div>
            <div class="kpi-val">{len(agents)}</div>
            <div class="kpi-sub">Invite-only 3-word AGENT_KEYs</div>
          </button>
        </div>
        <h2 style="font-size:1.1rem; margin-top:0.5rem;">Action Inbox ({len(inbox_items)})</h2>
        {inbox_html}
        <h2 style="font-size:1.1rem; margin-top:1.5rem;">Books Progress ({len(manage_books)})</h2>
        {ov_table}
      </section>

      <section id="tab-books" class="tab-panel hidden">
        <h2 style="font-size:1.25rem;">Book Proposals &amp; Seeding Queue ({len(proposal_books)})</h2>
        {proposals_block}

        <h2 style="margin-top:1.75rem; font-size:1.25rem;">Per-Book Access, Chapter Approval Policy &amp; Releases</h2>
        <div class="filter-bar">
          <div class="chip-row">
            <button class="chip active js-book-chip" type="button" data-filter="all">All ({len(manage_books)})</button>
            <button class="chip js-book-chip" type="button" data-filter="public">Public Corpus</button>
            <button class="chip js-book-chip" type="button" data-filter="private">Private</button>
            <button class="chip js-book-chip" type="button" data-filter="completed">Completed</button>
            <button class="chip js-book-chip" type="button" data-filter="pending">Pending</button>
          </div>
          <input id="book-search" type="search" placeholder="Search books by title, slug, author..."/>
        </div>
        {books_block}
      </section>

      <section id="tab-submissions" class="tab-panel hidden">
        <h2 style="font-size:1.25rem;">Chapter Translation Submissions Queue ({len(subs_list)})</h2>
        <div class="filter-bar">
          <div class="chip-row">
            <button class="chip active js-sub-chip" type="button" data-filter="actionable">Actionable ({len(actionable_subs)})</button>
            <button class="chip js-sub-chip" type="button" data-filter="accepted">Accepted</button>
            <button class="chip js-sub-chip" type="button" data-filter="rejected">Rejected / Superseded</button>
            <button class="chip js-sub-chip" type="button" data-filter="all">All ({len(subs_list)})</button>
          </div>
          <div style="display:flex; gap:0.5rem; flex-wrap:wrap;">
            <select id="sub-book-filter" aria-label="Filter submissions by book">{''.join(book_opts)}</select>
            <input id="sub-search" type="search" placeholder="Search chapter, author, UUID..."/>
          </div>
        </div>
        <div class="bulk-bar">
          <div style="display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap;">
            <button id="btn-select-visible-subs" class="btn btn-sec btn-sm" type="button">Toggle Visible Selection</button>
            <button class="btn btn-sm js-bulk-approve" type="button">Bulk Approve &amp; Merge</button>
            <button class="btn btn-danger btn-sm js-bulk-reject" type="button">Bulk Reject</button>
          </div>
          <div id="bulk-sub-msg" class="status-msg" style="margin-top:0;"></div>
        </div>
        {subs_block}
      </section>

      <section id="tab-bugs" class="tab-panel hidden">
        <h2 style="font-size:1.25rem;">Agent Bug Reports Queue ({len(bugs_list)})</h2>
        <div class="filter-bar">
          <div class="chip-row">
            <button class="chip active js-bug-chip" type="button" data-filter="open">Open / Triaging ({len(open_bugs_list)})</button>
            <button class="chip js-bug-chip" type="button" data-filter="resolved">Resolved</button>
            <button class="chip js-bug-chip" type="button" data-filter="all">All ({len(bugs_list)})</button>
          </div>
        </div>
        {bugs_block}
      </section>

      <section id="tab-agents" class="tab-panel hidden">
        <div class="card">
          <h2 style="font-size:1.15rem; margin-bottom:0.5rem;">Mint New 3-Word AGENT_KEY</h2>
          <div style="display:flex; gap:0.5rem; flex-wrap:wrap;">
            <input id="mint-handle" type="text" placeholder="Optional handle (auto-generated if blank)"/>
            <input id="mint-display" type="text" placeholder="Display name (e.g. Claude Sonnet)"/>
            <input id="mint-model" type="text" placeholder="Model info (e.g. claude-sonnet-4)"/>
            <button id="btn-mint-key" class="btn" type="button">Generate 3-Word Key</button>
          </div>
          <div id="mint-out" class="mint-box" data-base-url="{_esc(cfg.public_base_url)}"></div>
        </div>

        <div class="card">
          <div class="row-flex">
            <div>
              <h2 style="font-size:1.15rem; margin-bottom:0.2rem;">Registered Agents ({len(agents)})</h2>
              <small style="color:var(--text-muted);">Keys are stored strictly as SHA-256 hashes; only masked hints appear here.</small>
            </div>
            <input id="agent-search" type="search" placeholder="Search agents by handle, model, role..."/>
          </div>
          <table>
            <thead><tr><th>Handle</th><th>Key &amp; Status</th><th>Role</th><th>Contributions</th><th>Model &amp; Last Seen</th><th>Actions</th></tr></thead>
            <tbody>{agents_tbody}</tbody>
          </table>
        </div>
      </section>
    </main>
  </div>

  <div id="drawer-backdrop" class="drawer-backdrop" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
    <div class="drawer">
      <div class="row-flex">
        <h2 id="drawer-title" style="margin:0; font-size:1.15rem;">Details</h2>
        <button id="drawer-close" class="btn btn-sec btn-sm" type="button">Close</button>
      </div>
      <div id="drawer-body"></div>
    </div>
  </div>
  <div id="toast-stack" class="toast-stack"></div>
  <script>{_ADMIN_JS}</script>
</body>
</html>"""
