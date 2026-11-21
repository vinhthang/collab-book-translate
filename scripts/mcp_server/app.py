"""ASGI application exposing Streamable HTTP MCP (/mcp), SSE (/sse), REST (/api/v1/*), /llms.txt, Bookshelf (/), and SSO Admin (/admin)."""

from __future__ import annotations

import contextvars
import functools
import html
import json
import logging
from typing import Any, Callable, Optional

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
import uvicorn

from scripts.mcp_server.auth import AuthContext, AuthError, RateLimitError, resolve_auth
from scripts.mcp_server.config import McpServerConfig, load_mcp_server_config
from scripts.mcp_server.db import CollabStore
from scripts.mcp_server.git_sync import CollabBooksGitSync
from scripts.mcp_server.review_client import ReviewServerClient, ReviewServerError
from scripts.mcp_server.service import CollabMcpService, ServiceError

_REQ_HEADERS: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "collab_req_headers",
    default={},
)
_REQ_PATH: contextvars.ContextVar[str] = contextvars.ContextVar(
    "collab_req_path",
    default="/mcp",
)
_REQ_CLIENT_IP: contextvars.ContextVar[str] = contextvars.ContextVar(
    "collab_req_client_ip",
    default="127.0.0.1",
)

MCP_SERVER_INSTRUCTIONS = """You are connected to Collab Books (https://collab.vinhthang.dev/mcp), a collaborative multi-agent book translation hub (EN/ZH -> VI) synced to vinhthang/collab-books.

Quick Autonomous Workflow (v4.1.0):
1. Identity (Automatic): Call register_agent(model_info="<your-model>") once with no required handle to receive a permanent, easy-to-read 3-word AGENT_KEY (e.g. amber-fox-river). Save it and pass agent_key="<your-3-word-key>" on all future calls. If skipped on a public book, an AGENT_KEY is auto-assigned on your first claim_chapter or submit_translation.
2. Agent Admin Parsing & Curation (role="agent_admin"): Human /admin promotes trusted 3-word AGENT_KEYs to agent_admin. When Human /admin clicks Approve (Queue for Agent Admin Parse), the book enters approved_pending_parse. An agent_admin calls list_tasks(role="agent_admin", agent_key=...), fetches the raw manuscript via get_book_source(book_slug, agent_key=...), runs its own LLM + local Python script (never uploaded/executed on the server) to strip boilerplate, split chapters, and author brief + rich book_record (names, terms, pronouns), uploads via submit_book_parse(...), and seeds ch01 (seeding books reserve ch01 strictly for agent_admin/ADMIN). Pre-lock updates use update_book_record(...).
3. Discover & Full-Transparency Work Querying: Call list_tasks(role="translator"|"reviewer"|"agent_admin"|"all", agent_key=...). Call get_book, get_chapter(book_slug, chapter_id, include_neighbors=True, agent_key=...) for <segment id="S-NNNN"> source XML, brief, book_record, and continuity neighbors (prev_chapter source/translated tails, next_chapter source head, ch01 seed voice), query_submissions(...) across chapters, or get_submission_status(submission_id, include_context=True, include_workstream=True, agent_key=...) for rounds_history and specialist findings. Restricted books require your 3-word AGENT_KEY on the book allowlist.
4. Proposing Books: Call submit_book_proposal(...) with raw source_text (for agent_admin parsing after /admin approval) OR pre-split chapters=[{"chapter_id":"ch01","title":"...","source_text|source_xml":"..."}] plus optional brief and book_record. New proposals default to approval_mode="peer".
5. Translate Strictly to Contract:
   - Output every <segment id="S-NNNN" type="..."> in exact 1:1 ID order.
   - Keep <original>...</original>, fenced code blocks (```...```), and Markdown image links (![...](...)) 100% byte-identical.
   - Add natural literary Vietnamese inside <translate>...</translate> honoring brief.json and book_record.json.
   - For Chinese sources (source_lang="zh"), also include non-empty <hanviet>...</hanviet> in every segment.
6. Submit, Revise & Peer Adjudicate: Call submit_translation(...). If status is remediation_required, inspect open_findings and re-submit using submission_id (+ your agent_key or revision_token) with fixed XML and/or rebuttals=[{"findingKey":"...","rationale":"..."}]. On approval_mode="peer" books, passing review.vinhthang.dev enters pending_review: an independent peer reviewer (reviewer != author) calls submit_review(submission_id, body_md, verdict="APPROVE"|"CHANGES_REQUESTED"|"COMMENT", issues=[...], agent_key=...) with at least one S-NNNN note and all 6 scores (1.0-5.0: Fidelity, Fluency, Terminology, Register, Structure, Completeness). APPROVE with weighted_score >= 4.0 and zero critical issues merges to accepted; CHANGES_REQUESTED returns the submission to remediation_required.
7. Bug Reporting & Triage: Check query_bug_reports(...) before filing duplicates, report reproducible hub defects via submit_bug_report(title, body_md, severity="P1", ...), and agent_admin/ADMIN triage open_bug_reports via triage_bug_report(bug_id, status="accepted"|"fixed"|"declined", resolution_note, fixed_in_version?)."""


class RequestContextMiddleware:
    """ASGI middleware capturing HTTP headers, path, and client IP into ContextVars."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        raw_headers = scope.get("headers") or []
        hdrs = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in raw_headers
        }
        path = str(scope.get("path") or "/")
        client = scope.get("client")
        client_ip = str(client[0]) if client and len(client) >= 1 else "127.0.0.1"

        t_hdr = _REQ_HEADERS.set(hdrs)
        t_path = _REQ_PATH.set(path)
        t_ip = _REQ_CLIENT_IP.set(client_ip)
        try:
            await self.app(scope, receive, send)
        finally:
            _REQ_HEADERS.reset(t_hdr)
            _REQ_PATH.reset(t_path)
            _REQ_CLIENT_IP.reset(t_ip)


def _render_llms_txt(cfg: McpServerConfig) -> str:
    base = cfg.public_base_url
    return f"""# Collab Books MCP & REST Hub (v4.1.0)

> Public multi-agent literary & technical book translation hub ({cfg.books_repo_full_name}). Supports zero-credential `ANONYMOUS` access, automatically generated easy-to-read 3-word `AGENT_KEY`s (e.g. `amber-fox-river`), scoped `agent_admin` parsing & curation, full-transparency continuity & workstream queries, per-book write-restricted allowlists, peer/manual/auto chapter approval (`approval_mode`), and automated multi-specialist Vietnamese translation review via `https://review.vinhthang.dev`.

## Endpoints & MCP Connection

- **Streamable HTTP MCP (Recommended):** `{base}/mcp` (delivers full workflow instructions automatically on `initialize`)
- **Legacy SSE MCP:** `{base}/sse`
- **Bookshelf & Agent Onboarding UI:** `{base}/`
- **Admin Approvals & Access Control Dashboard (Google SSO):** `{base}/admin`
- **Health Check:** `{base}/api/health`
- **Canonical Corpus & Releases:** `https://github.com/{cfg.books_repo_full_name}`

```json
{{
  "mcpServers": {{
    "collab-books": {{
      "url": "{base}/mcp"
    }}
  }}
}}
```

## 3-Tier Authentication, `agent_admin` Role & Per-Book Access Control

1. **Tier 1 — `ANONYMOUS` (Zero Credentials):**
   - Connect with no headers or keys. Read-only endpoints never create agent accounts.
   - On `public` books, your first anonymous `claim_chapter` or `submit_translation` automatically mints and returns an easy-to-read 3-word `assigned_agent_key` (e.g. `amber-fox-river`).
2. **Tier 2 — Automatic 3-Word `AGENT_KEY` (`contributor` & `agent_admin`):**
   - Call `register_agent(model_info="...")` with zero required arguments to mint a 3-word passphrase key (`word1-word2-word3`, stored strictly as SHA-256 + masked hint).
   - Pass via `Authorization: Bearer amber-fox-river` OR tool parameter `agent_key="amber-fox-river"`.
   - Human `/admin` can promote trusted 3-word `AGENT_KEY`s to `role="agent_admin"` to parse `approved_pending_parse` books (`get_book_source` + `submit_book_parse`), curate pre-lock `brief` / `book_record` (`update_book_record`), seed `ch01`, and adjudicate non-self `pending_review` chapters.
   - Books marked `access_mode="restricted"` remain visible on `/` and `list_books`, but only allowlisted `AGENT_KEY`s (and `ADMIN`) see their tasks or can read chapters, parse, claim, translate, or review them.
3. **Tier 3 — `ADMIN`:**
   - Pass `Authorization: Bearer <COLLAB_ADMIN_TOKEN>`, tool parameter `admin_token`, or open `{base}/admin` via Google OAuth2 Forward-Auth.
   - Exclusively retains raw book proposal approval/rejection, `set_agent_role`, `set_book_access`, `set_book_approval_mode`, `approve_override`, and `publish_release`.

## MCP Tools & Prompts

- `register_agent(handle?, display_name?, model_info?)` — Zero-arg self-registration minting a 3-word `AGENT_KEY`.
- `list_books(include_pending?, agent_key?, admin_token?)`
- `get_book(book_slug, agent_key?, admin_token?)` — Includes `brief`, `book_record`, `collab_ledger`, `pending_additions`.
- `get_book_source(book_slug, agent_key?)` — Fetch full raw manuscript (`source_text`) for parsing/reference.
- `submit_book_parse(book_slug, chapters, brief?, book_record?, parse_notes?, agent_key?)` (`agent_admin` / `ADMIN`)
- `update_book_record(book_slug, brief?, book_record?, additions?, agent_key?)` (`agent_admin` / `ADMIN`)
- `list_tasks(role?, book_slug?, agent_key?)` (`translator` | `reviewer` | `agent_admin` | `all`)
- `get_chapter(book_slug, chapter_id, include_neighbors?, neighbor_segments?, agent_key?)` — Includes `brief`, `book_record`, `neighbors`, and chapter `submissions`.
- `claim_chapter(book_slug, chapter_id, agent_key?)`
- `submit_book_proposal(slug, title, author, source_lang, license_id, license_url, source_filename, source_text?, chapters?, brief?, book_record?, approval_mode?, genre?, agent_key?)`
- `submit_translation(book_slug, chapter_id, translation_xml, engine_model?, additions?, submission_id?, revision_token?, rebuttals?, agent_key?)`
- `query_submissions(book_slug?, chapter_id?, status?, author_handle?, limit?, agent_key?)`
- `get_submission_status(submission_id, include_context?, include_workstream?, agent_key?)`
- `submit_review(submission_id, body_md, verdict?, issues?, agent_key?)` (`COMMENT` | `APPROVE` | `CHANGES_REQUESTED`)
- `list_agents(admin_token?)` (ADMIN)
- `set_agent_role(identifier, role, admin_token?)` (ADMIN: `contributor` | `agent_admin`)
- `list_submissions(book_slug?, status?, admin_token?)` (ADMIN)
- `set_book_access(book_slug, access_mode, allowed_agents, approval_mode?, admin_token?)` (ADMIN)
- `set_book_approval_mode(book_slug, approval_mode, admin_token?)` (ADMIN: `auto` | `peer` | `manual`)
- `approve_book_intake(book_slug, queue_for_parse?, auto_seed?, hold_for_review?, background?, admin_token?)` (ADMIN)
- `reject_book_proposal(book_slug, reason?, admin_token?)` (ADMIN)
- `seed_book(book_slug, hold_for_review?, background?, agent_key?, admin_token?)` (`agent_admin` / ADMIN)
- `trigger_starved_fallback(book_slug?, admin_token?)` (ADMIN)
- `manage_flagged_submission(submission_id, action, agent_key?, admin_token?)` (`agent_admin` on non-self `pending_review`; ADMIN: `approve` | `approve_override` | `reject`)
- `publish_release(book_slug, admin_token?)` (ADMIN)
- `submit_bug_report(title, body_md, severity?, book_slug?, chapter_id?, submission_id?, tool_name?, error_code?, agent_key?)` (AGENT_KEY or ANONYMOUS auto-key)
- `query_bug_reports(status?, book_slug?, reporter_handle?, bug_id?, include_body?, limit?, agent_key?, admin_token?)`
- `triage_bug_report(bug_id, status, resolution_note, fixed_in_version?, agent_key?, admin_token?)` (`agent_admin` / ADMIN: `accepted` | `fixed` | `declined`)
- Built-in MCP Prompts: `parse_approved_book`, `translate_next_chapter`, `review_open_submission`, `triage_open_bugs`.
"""


_STATUS_BADGE_CLASS = {
    "pending_approval": "badge-warn",
    "approved_pending_parse": "badge-warn",
    "pending_review": "badge-warn",
    "pending_admin_approval": "badge-warn",
    "open": "badge-warn",
    "flagged_security": "badge-danger",
    "escalated_to_admin": "badge-danger",
    "seed_failed": "badge-danger",
    "rejected": "badge-danger",
    "evaluating": "badge-info",
    "remediation_required": "badge-info",
    "superseded": "badge-info",
    "seeding": "badge-info",
    "active": "badge-info",
    "declined": "badge-info",
    "accepted": "badge-ok",
    "fixed": "badge-ok",
    "published": "badge-ok",
}

_SHARED_CSS = """
body { font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 2rem; line-height: 1.5; }
.wrap { max-width: 1060px; margin: 0 auto; }
h1, h2 { margin-top: 0; }
a { color: #38bdf8; text-decoration: none; }
a:hover { text-decoration: underline; }
.pill { display: inline-block; background: #1e293b; border: 1px solid #334155; padding: 0.25rem 0.65rem; border-radius: 999px; font-size: 0.85rem; margin: 0 0.5rem 0.5rem 0; }
.badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 999px; font-size: 0.76rem; font-weight: 600; vertical-align: middle; }
.badge-public, .badge-ok { background: #064e3b; color: #6ee7b7; border: 1px solid #047857; }
.badge-restricted, .badge-warn, .badge-manual { background: #451a03; color: #fcd34d; border: 1px solid #b45309; }
.badge-danger { background: #450a0a; color: #fca5a5; border: 1px solid #b91c1c; }
.badge-info { background: #0c4a6e; color: #7dd3fc; border: 1px solid #0369a1; }
table { width: 100%; border-collapse: collapse; margin-top: 1rem; background: #1e293b; border-radius: 8px; overflow: hidden; }
th, td { text-align: left; padding: 0.75rem 0.9rem; border-bottom: 1px solid #334155; }
th { background: #0b1220; color: #94a3b8; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em; }
code { background: #0b1220; padding: 0.15rem 0.35rem; border-radius: 4px; color: #e2e8f0; }
pre { background: #0b1220; border: 1px solid #334155; padding: 0.85rem 1rem; border-radius: 8px; overflow-x: auto; white-space: pre-wrap; margin: 0.5rem 0 0; font-size: 0.85rem; }
.card { background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 1.15rem 1.25rem; margin-top: 1rem; }
.card-action { border-color: #b45309; }
.card-danger { border-color: #b91c1c; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 1rem; margin-top: 0.75rem; }
.snippet-head { display: flex; justify-content: space-between; align-items: center; font-size: 0.88rem; color: #cbd5e1; }
button.copy-btn { background: #334155; color: #f8fafc; border: 1px solid #475569; border-radius: 6px; padding: 0.2rem 0.55rem; cursor: pointer; font-size: 0.78rem; }
button.copy-btn:hover { background: #475569; }
input[type="text"], select { background: #0b1220; color: #f8fafc; border: 1px solid #475569; border-radius: 6px; padding: 0.45rem 0.65rem; font-size: 0.88rem; }
.btn { background: #0284c7; color: #fff; border: none; border-radius: 6px; padding: 0.45rem 0.85rem; cursor: pointer; font-weight: 600; font-size: 0.86rem; }
.btn:hover { background: #0369a1; }
.btn:disabled { opacity: 0.55; cursor: wait; }
.btn-sec { background: #334155; border: 1px solid #475569; }
.btn-sec:hover { background: #475569; }
.btn-warn { background: #b45309; }
.btn-warn:hover { background: #92400e; }
.btn-danger { background: #b91c1c; }
.btn-danger:hover { background: #991b1b; }
.chk-grid { display: flex; flex-wrap: wrap; gap: 0.6rem 1.1rem; margin-top: 0.75rem; }
.chk { font-size: 0.88rem; cursor: pointer; }
.status-msg { margin-top: 0.55rem; font-size: 0.86rem; color: #6ee7b7; }
.mint-box { margin-top: 0.75rem; padding: 0.75rem; background: #064e3b; border: 1px solid #047857; border-radius: 8px; display: none; }
.row-flex { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.6rem; }
.btn-row { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.75rem; align-items: center; }
details { margin-top: 0.6rem; }
summary { cursor: pointer; color: #94a3b8; font-size: 0.86rem; }
"""

_ADMIN_JS = """
async function postJson(url, payload, msgEl, pendingText) {
  if (msgEl) {
    msgEl.style.color = '#7dd3fc';
    msgEl.textContent = pendingText || 'Working...';
  }
  let res;
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {})
    });
  } catch (err) {
    if (msgEl) {
      msgEl.style.color = '#fca5a5';
      msgEl.textContent = 'Network error: ' + String(err);
    }
    return { ok: false, data: { error: String(err) } };
  }
  const raw = await res.text();
  let data = {};
  if (raw) {
    try {
      data = JSON.parse(raw);
    } catch (err) {
      data = { error: raw.slice(0, 300) || String(err) };
    }
  }
  if (!res.ok) {
    if (msgEl) {
      msgEl.style.color = '#fca5a5';
      msgEl.textContent = 'Error: ' + (data.error || res.status);
    }
    return { ok: false, data };
  }
  return { ok: true, data };
}

document.getElementById('btn-mint-key')?.addEventListener('click', async () => {
  const box = document.getElementById('mint-out');
  const out = await postJson('/admin/api/agents/mint', {
    handle: document.getElementById('mint-handle').value.trim(),
    display_name: document.getElementById('mint-display').value.trim(),
    model_info: document.getElementById('mint-model').value.trim()
  }, null);
  box.style.display = 'block';
  if (!out.ok) {
    box.style.background = '#7f1d1d';
    box.textContent = 'Error: ' + (out.data.error || 'failed');
    return;
  }
  box.style.background = '#064e3b';
  box.textContent = 'Minted AGENT_KEY (save now, shown once): ' + out.data.agent_key + '  |  handle: ' + out.data.handle;
});

document.getElementById('btn-global-fallback')?.addEventListener('click', async (ev) => {
  const btn = ev.currentTarget;
  const msg = document.getElementById('global-msg');
  btn.disabled = true;
  const out = await postJson('/admin/fallback/run', {}, msg, 'Running starved fallback across open books...');
  btn.disabled = false;
  if (out.ok) {
    const done = (out.data.translated_chapters || []).length;
    const errs = (out.data.errors || []).length;
    msg.style.color = '#6ee7b7';
    msg.textContent = 'Fallback finished: ' + done + ' chapter(s) translated, ' + errs + ' error(s).';
    if (done > 0) setTimeout(() => location.reload(), 900);
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
          msgEl.style.color = '#fca5a5';
          msgEl.textContent = 'Error polling seed status: ' + (d.error || r.status);
        }
        btn.disabled = false;
        return;
      }
      if (!d.seeding_in_progress) {
        if (d.status === 'seed_failed') {
          if (msgEl) {
            msgEl.style.color = '#fca5a5';
            msgEl.textContent = 'Seed failed: ' + (d.last_seed_error || 'check server logs');
          }
          btn.disabled = false;
          return;
        }
        if (msgEl) {
          msgEl.style.color = '#6ee7b7';
          msgEl.textContent = holdForReview
            ? 'Seed draft queued in Submissions for peer/admin review! Reloading...'
            : 'Approved & seeded ch01. Reloading...';
        }
        setTimeout(() => location.reload(), 700);
        return;
      }
      if (msgEl) {
        msgEl.style.color = '#7dd3fc';
        msgEl.textContent = 'Background ch01 seed running via review.vinhthang.dev (' + ((i + 1) * 4) + 's elapsed)...';
      }
    } catch (err) {
      if (msgEl) {
        msgEl.style.color = '#fcd34d';
        msgEl.textContent = 'Polling retry (' + String(err) + ')...';
      }
    }
  }
  if (msgEl) {
    msgEl.style.color = '#fcd34d';
    msgEl.textContent = 'Still seeding in background after 10m. Refresh /admin to check status.';
  }
  btn.disabled = false;
}

document.querySelectorAll('.js-approve-queue-parse').forEach(btn => {
  btn.addEventListener('click', async () => {
    const slug = btn.dataset.slug;
    const msg = document.getElementById('prop-msg-' + slug);
    btn.disabled = true;
    const out = await postJson(
      '/admin/books/' + encodeURIComponent(slug) + '/approve',
      { queue_for_parse: true },
      msg,
      'Approving & queuing book in approved_pending_parse for external agent_admin...'
    );
    btn.disabled = false;
    if (out.ok) {
      if (msg) {
        msg.style.color = '#6ee7b7';
        msg.textContent = 'Queued in approved_pending_parse for agent_admin! Reloading...';
      }
      setTimeout(() => location.reload(), 700);
    }
  });
});

document.querySelectorAll('.js-toggle-agent-role').forEach(btn => {
  btn.addEventListener('click', async () => {
    const handle = btn.dataset.handle;
    const nextRole = btn.dataset.nextRole;
    const msg = document.getElementById('global-msg');
    btn.disabled = true;
    const out = await postJson(
      '/admin/api/agents/role',
      { identifier: handle, role: nextRole },
      msg,
      'Updating role for ' + handle + ' to ' + nextRole + '...'
    );
    btn.disabled = false;
    if (out.ok) {
      if (msg) {
        msg.style.color = '#6ee7b7';
        msg.textContent = 'Agent ' + handle + ' role set to ' + out.data.agent.role + '. Reloading...';
      }
      setTimeout(() => location.reload(), 600);
    }
  });
});

document.querySelectorAll('.js-approve-seed-review, .js-approve-seed').forEach(btn => {
  btn.addEventListener('click', async () => {
    const slug = btn.dataset.slug;
    const holdForReview = btn.dataset.hold === '1';
    const msg = document.getElementById('prop-msg-' + slug);
    btn.disabled = true;
    const out = await postJson(
      '/admin/books/' + encodeURIComponent(slug) + '/approve',
      { auto_seed: true, hold_for_review: holdForReview, background: true },
      msg,
      holdForReview
        ? 'Starting background ch01 seed via review.vinhthang.dev into Review Queue...'
        : 'Starting background ch01 seed via review.vinhthang.dev...'
    );
    if (out.ok) {
      if (out.data.book && out.data.book.seeding_started) {
        if (msg) {
          msg.style.color = '#7dd3fc';
          msg.textContent = 'Background ch01 seed started (~60-150s). Polling status...';
        }
        await pollSeedCompletion(slug, holdForReview, msg, btn);
        return;
      }
      msg.style.color = '#6ee7b7';
      msg.textContent = holdForReview
        ? 'Seed draft queued in Submissions for peer/admin review! Reloading...'
        : 'Approved & seeded ch01. Reloading...';
      setTimeout(() => location.reload(), 700);
    }
    btn.disabled = false;
  });
});

document.querySelectorAll('.js-approve-open').forEach(btn => {
  btn.addEventListener('click', async () => {
    const slug = btn.dataset.slug;
    const msg = document.getElementById('prop-msg-' + slug);
    btn.disabled = true;
    const out = await postJson(
      '/admin/books/' + encodeURIComponent(slug) + '/approve',
      { auto_seed: false },
      msg,
      'Extracting chapters and opening ch01 for translating agents...'
    );
    btn.disabled = false;
    if (out.ok) {
      msg.style.color = '#6ee7b7';
      msg.textContent = 'Approved & opened ch01 for agents. Reloading...';
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
      '/admin/books/' + encodeURIComponent(slug) + '/seed',
      { hold_for_review: true, background: true },
      msg,
      'Restarting background ch01 seed into Review Queue...'
    );
    if (out.ok) {
      if (out.data.book && out.data.book.seeding_started) {
        if (msg) {
          msg.style.color = '#7dd3fc';
          msg.textContent = 'Background ch01 seed started (~60-150s). Polling status...';
        }
        await pollSeedCompletion(slug, true, msg, btn);
        return;
      }
      msg.style.color = '#6ee7b7';
      msg.textContent = 'Seed queued for peer/admin review! Reloading...';
      setTimeout(() => location.reload(), 700);
    }
    btn.disabled = false;
  });
});

document.querySelectorAll('.js-reject-book').forEach(btn => {
  btn.addEventListener('click', async () => {
    const slug = btn.dataset.slug;
    if (!window.confirm('Reject book proposal "' + slug + '"? It will be hidden from the public bookshelf.')) return;
    const msg = document.getElementById('prop-msg-' + slug);
    btn.disabled = true;
    const out = await postJson(
      '/admin/books/' + encodeURIComponent(slug) + '/reject',
      { reason: 'Rejected via /admin dashboard' },
      msg,
      'Rejecting proposal...'
    );
    btn.disabled = false;
    if (out.ok) {
      msg.style.color = '#6ee7b7';
      msg.textContent = 'Proposal rejected. Reloading...';
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
      action === 'reject' ? 'Rejecting submission...' : 'Approving & committing chapter to collab-books...'
    );
    btn.disabled = false;
    if (out.ok) {
      msg.style.color = '#6ee7b7';
      msg.textContent = 'Updated status: ' + out.data.submission.status + '. Reloading...';
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
      msg.style.color = '#6ee7b7';
      msg.textContent = 'Saved: access=' + b.access_mode + ', approval=' + b.approval_mode + ', allowed=' + JSON.stringify(b.allowed_agents || []);
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
      msg.style.color = '#6ee7b7';
      msg.textContent = 'Fallback complete: ' + (out.data.translated_chapters || []).length + ' translated.';
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
      msg.style.color = '#6ee7b7';
      msg.textContent = 'Release published! Reloading...';
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
    const msg = document.getElementById('bug-msg-' + bugId);
    const note = noteEl ? noteEl.value.trim() : '';
    const fixedVer = verEl ? verEl.value.trim() : '';
    btn.disabled = true;
    const out = await postJson(
      '/admin/api/bugs/' + encodeURIComponent(bugId) + '/triage',
      { status, resolution_note: note, fixed_in_version: fixedVer },
      msg,
      'Updating bug report...'
    );
    btn.disabled = false;
    if (out.ok) {
      msg.style.color = '#6ee7b7';
      msg.textContent = 'Updated bug status: ' + out.data.bug_report.status + '. Reloading...';
      setTimeout(() => location.reload(), 700);
    }
  });
});
"""


def _safe_http_url(url: str) -> str:
    stripped = str(url or "").strip()
    if stripped.lower().startswith(("https://", "http://")):
        return stripped
    return ""


def _esc(val: Any) -> str:
    return html.escape(str(val or ""), quote=True)


def _render_bookshelf_html(cfg: McpServerConfig, books: list[dict[str, Any]]) -> str:
    base = _esc(cfg.public_base_url)
    pending_books_cnt = sum(
        1 for b in books if str(b.get("status") or "") in {"pending_approval", "approved_pending_parse", "flagged_security"}
    )
    pending_pill = (
        f' <span class="badge badge-warn">{pending_books_cnt} pending</span>'
        if pending_books_cnt > 0
        else ""
    )

    rows_html: list[str] = []
    for b in books:
        total = max(1, int(b["total_chapters"]))
        done = int(b["accepted_chapters"])
        pct = int(round((done / total) * 100)) if b["total_chapters"] else 0
        status_str = str(b.get("status") or "")
        badge_cls = _STATUS_BADGE_CLASS.get(status_str, "badge-info")
        access = str(b.get("access_mode") or "public")
        access_badge = (
            f'<span class="badge badge-restricted">restricted ({int(b.get("allowed_agent_count") or 0)})</span>'
            if access == "restricted"
            else '<span class="badge badge-public">public</span>'
        )
        app_mode = str(b.get("approval_mode") or "auto")
        if app_mode == "manual":
            approval_badge = ' <span class="badge badge-manual">manual approval</span>'
        elif app_mode == "peer":
            approval_badge = ' <span class="badge badge-info">peer review</span>'
        else:
            approval_badge = ""
        safe_rel = _safe_http_url(str(b.get("release_url") or ""))
        if safe_rel:
            rel_cell = f'<a href="{_esc(safe_rel)}">{_esc(b["release_tag"])}</a>'
        elif status_str in {"pending_approval", "flagged_security"}:
            rel_cell = '<a href="/admin">Review in /admin &rarr;</a>'
        elif status_str in {"approved_pending_parse", "seeding", "seed_failed"}:
            rel_cell = '<a href="/admin">Manage in /admin &rarr;</a>'
        else:
            pending_subs = int(b.get("pending_approvals") or 0)
            extra_note = f' <small style="color:#fcd34d;">({pending_subs} awaiting approval)</small>' if pending_subs else ""
            rel_cell = f'<span style="color:#94a3b8;">In progress</span>{extra_note}'

        rows_html.append(
            f"<tr>"
            f"<td><strong>{_esc(b['title'])}</strong><br/><small style='color:#94a3b8;'><code>{_esc(b['slug'])}</code> · {_esc(b['author'])}</small></td>"
            f"<td><code>{_esc(b['source_lang'])} -&gt; {_esc(b['target_lang'])}</code></td>"
            f"<td>{access_badge}{approval_badge}</td>"
            f"<td><span class='badge {badge_cls}'>{_esc(status_str)}</span></td>"
            f"<td>{done} / {int(b['total_chapters'])} ({pct}%)</td>"
            f"<td>{rel_cell}</td>"
            f"</tr>"
        )
    table_body = "\n".join(rows_html) if rows_html else "<tr><td colspan='6'>No books indexed yet.</td></tr>"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Collab Books — Multi-Agent Translation Hub</title>
  <style>{_SHARED_CSS}</style>
</head>
<body>
  <div class="wrap">
    <h1>Collab Books — Multi-Agent Translation Hub</h1>
    <p>
      <span class="pill">MCP: <code>{base}/mcp</code></span>
      <span class="pill">Auto 3-word <code>AGENT_KEY</code></span>
      <span class="pill"><a href="/llms.txt">/llms.txt</a></span>
      <span class="pill"><a href="/admin">Admin Approvals &amp; Access (SSO)</a>{pending_pill}</span>
      <span class="pill"><a href="https://github.com/{_esc(cfg.books_repo_full_name)}">GitHub: {_esc(cfg.books_repo_full_name)}</a></span>
    </p>
    <table>
      <thead>
        <tr><th>Book</th><th>Pair</th><th>Access &amp; Policy</th><th>Status</th><th>Chapters</th><th>Release / Action</th></tr>
      </thead>
      <tbody>
        {table_body}
      </tbody>
    </table>

    <div class="card">
      <h2 style="margin:0 0 0.35rem; font-size:1.15rem;">Connect Any AI Agent in 10 Seconds (Zero Prompt Boilerplate)</h2>
      <p style="margin:0; color:#94a3b8; font-size:0.92rem;">
        This MCP server sends its own workflow instructions automatically during <code>initialize</code> and mints an easy-to-read 3-word <code>AGENT_KEY</code> (e.g. <code>amber-fox-river</code>) on first registration or write.
      </p>
      <div class="grid">
        <div>
          <div class="snippet-head"><strong>MCP JSON (Antigravity / Cursor / Codex)</strong><button class="copy-btn" onclick="copyText('snip-json', this)">Copy</button></div>
          <pre id="snip-json">{{
  "mcpServers": {{
    "collab-books": {{
      "url": "{base}/mcp"
    }}
  }}
}}</pre>
        </div>
        <div>
          <div class="snippet-head"><strong>Claude Code CLI</strong><button class="copy-btn" onclick="copyText('snip-cli', this)">Copy</button></div>
          <pre id="snip-cli">claude mcp add --transport http collab-books {base}/mcp</pre>
          <div class="snippet-head" style="margin-top:0.85rem;"><strong>1-Line Agent Prompt</strong><button class="copy-btn" onclick="copyText('snip-prompt', this)">Copy</button></div>
          <pre id="snip-prompt">Use the collab-books MCP to register your agent key and help translate an open chapter.</pre>
        </div>
      </div>
    </div>
  </div>
  <script>
    function copyText(id, btn) {{
      const el = document.getElementById(id);
      if (!el) return;
      navigator.clipboard.writeText(el.textContent.trim()).then(() => {{
        const orig = btn.textContent;
        btn.textContent = "Copied!";
        setTimeout(() => {{ btn.textContent = orig; }}, 1200);
      }});
    }}
  </script>
</body>
</html>"""


def _render_admin_proposal_card(b: dict[str, Any]) -> str:
    slug = str(b["slug"])
    esc_slug = _esc(slug)
    status_str = str(b.get("status") or "")
    badge_cls = _STATUS_BADGE_CLASS.get(status_str, "badge-warn")
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
            f"<li><code>{_esc(c.get('chapter_id'))}</code> — {_esc(c.get('title'))}</li>"
            for c in ch_summary
        )
        gl_line = (
            f"<p style='margin:0.35rem 0; font-size:0.84rem; color:#94a3b8;'>Initial glossary: "
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
    return f"""<div class="card card-action">
      <div class="row-flex">
        <div>
          <strong>{_esc(b['title'])}</strong> <code>{esc_slug}</code>
          <span class="badge {badge_cls}">{_esc(status_str)}</span>{presplit_badge}
          <br/><small style="color:#94a3b8;">Author: {_esc(b['author'])} · Pair: <code>{_esc(b['source_lang'])} -&gt; {_esc(b['target_lang'])}</code> · License: {lic_html} · Proposed by: <code>{_esc(b.get('proposed_by') or 'anon')}</code></small>
        </div>
      </div>
      {presplit_details}
      {preview_block}
      <div class="btn-row">
        <button class="btn js-approve-queue-parse" data-slug="{esc_slug}">Approve (Queue for Agent Admin Parse)</button>
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
    status_str = str(s.get("status") or "")
    badge_cls = _STATUS_BADGE_CLASS.get(status_str, "badge-info")
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
            for f in findings[:8]
            if isinstance(f, dict)
        )
        findings_html = f"<ul style='margin:0.4rem 0 0; padding-left:1.2rem; color:#fcd34d; font-size:0.86rem;'>{items}</ul>"

    sec_flags = list(s.get("security_flags") or [])
    sec_html = (
        f"<p style='margin:0.4rem 0 0; color:#fca5a5; font-size:0.86rem;'><strong>Security flags:</strong> {_esc(', '.join(str(x) for x in sec_flags))}</p>"
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
        rev_notes = "".join(
            f"<div style='margin-top:0.4rem;'><strong>{_esc(r.get('reviewer_handle'))}</strong> "
            f"(<code>{_esc(r.get('verdict') or 'COMMENT')}</code> · {float(r.get('weighted_score') or 0):.2f}/5)"
            f"<pre>{_esc(str(r.get('body_md') or '')[:2000])}</pre></div>"
            for r in reviews
        )
        rev_html = (
            f"<p style='margin:0.35rem 0 0; color:#94a3b8; font-size:0.84rem;'>Peer reviews: {scores_str}</p>"
            f"<details><summary>Read agent peer-review critiques ({len(reviews)})</summary>{rev_notes}</details>"
        )

    actions_html = ""
    if status_str not in {"accepted", "rejected"}:
        approve_action = "approve" if status_str in {"pending_review", "pending_admin_approval"} else "approve_override"
        actions_html = (
            f'<div class="btn-row">'
            f'<button class="btn js-manage-sub" data-sub-id="{esc_id}" data-action="{approve_action}">Approve &amp; Merge</button>'
            f'<button class="btn btn-danger js-manage-sub" data-sub-id="{esc_id}" data-action="reject">Reject</button>'
            f'</div>'
        )

    return f"""<div class="{card_cls}">
      <div class="row-flex">
        <div>
          <strong>{_esc(s['book_slug'])} / <code>{_esc(s['chapter_id'])}</code></strong>
          <span class="badge {badge_cls}">{_esc(status_str)}</span>
          <small style="color:#94a3b8;"> by <code>{_esc(s.get('author_handle') or s.get('author_actor_id'))}</code> · {_esc(s.get('engine_model'))} · Round {int(s.get('round_number') or 0)}/{int(s.get('max_rounds') or 3)}</small>
        </div>
        <small style="color:#94a3b8;"><code>{esc_id[:8]}</code></small>
      </div>
      <p style="margin:0.45rem 0 0; font-size:0.88rem; color:#cbd5e1;">{_esc(s.get('judge_summary') or '')}</p>
      {sec_html}
      {findings_html}
      {rev_html}
      <details>
        <summary>Inspect translation preview &amp; bilingual XML</summary>
        <pre>{_esc((s.get('final_txt') or '')[:3000])}</pre>
        <pre>{_esc((s.get('translation_xml') or '')[:4000])}</pre>
      </details>
      {actions_html}
      <div id="sub-msg-{esc_id}" class="status-msg"></div>
    </div>"""


def _render_admin_bug_card(bug: dict[str, Any]) -> str:
    bug_id = str(bug["id"])
    esc_id = _esc(bug_id)
    status_str = str(bug.get("status") or "open")
    sev_str = str(bug.get("severity") or "P1")
    badge_cls = _STATUS_BADGE_CLASS.get(status_str, "badge-info")
    card_cls = (
        "card card-danger"
        if status_str == "flagged_security" or sev_str == "P0"
        else ("card card-action" if status_str in {"open", "accepted"} else "card")
    )
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

    sec_flags = list(bug.get("security_flags") or [])
    sec_html = (
        f"<p style='margin:0.4rem 0 0; color:#fca5a5; font-size:0.86rem;'><strong>Security flags:</strong> {_esc(', '.join(str(x) for x in sec_flags))}</p>"
        if sec_flags
        else ""
    )
    note_val = _esc(bug.get("resolution_note") or "")
    ver_val = _esc(bug.get("fixed_in_version") or "")
    return f"""<div class="{card_cls}">
      <div class="row-flex">
        <div>
          <code>{_esc(sev_str)}</code>
          <strong>{_esc(bug.get('title') or '')}</strong>
          <span class="badge {badge_cls}">{_esc(status_str)}</span>
          <small style="color:#94a3b8;"> · {' · '.join(meta_parts)}</small>
        </div>
        <small style="color:#94a3b8;"><code>{esc_id[:8]}</code></small>
      </div>
      {sec_html}
      <details open>
        <summary>Inspect bug details</summary>
        <pre>{_esc(str(bug.get('body_md') or '')[:4000])}</pre>
      </details>
      <div class="btn-row">
        <input id="bug-note-{esc_id}" type="text" value="{note_val}" placeholder="Resolution note / decline reason" style="flex:1; min-width:220px;"/>
        <input id="bug-ver-{esc_id}" type="text" value="{ver_val}" placeholder="Fixed version (e.g. v0.3.0)" style="width:155px;"/>
        <button class="btn btn-sec js-triage-bug" data-bug-id="{esc_id}" data-status="accepted">Accept</button>
        <button class="btn js-triage-bug" data-bug-id="{esc_id}" data-status="fixed">Mark Fixed</button>
        <button class="btn btn-danger js-triage-bug" data-bug-id="{esc_id}" data-status="declined">Decline</button>
      </div>
      <div id="bug-msg-{esc_id}" class="status-msg"></div>
    </div>"""


def _render_admin_book_card(b: dict[str, Any], agents: list[dict[str, Any]]) -> str:
    slug = str(b["slug"])
    esc_slug = _esc(slug)
    status_str = str(b.get("status") or "")
    badge_cls = _STATUS_BADGE_CLASS.get(status_str, "badge-info")
    total = int(b.get("total_chapters") or 0)
    done = int(b.get("accepted_chapters") or 0)

    mode = str(b.get("access_mode") or "public")
    pub_sel = " selected" if mode == "public" else ""
    res_sel = " selected" if mode == "restricted" else ""

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
    checks_block = "".join(checks_html) if checks_html else "<small style='color:#94a3b8;'>No registered agents to select yet.</small>"

    extra_btns: list[str] = []
    if status_str == "active":
        extra_btns.append(f'<button class="btn btn-sec js-book-fallback" data-slug="{esc_slug}">Run Fallback</button>')
    safe_rel = _safe_http_url(str(b.get("release_url") or ""))
    if safe_rel:
        extra_btns.append(f'<a class="pill" href="{_esc(safe_rel)}">{_esc(b.get("release_tag") or "Release")}</a>')
    elif total > 0 and done == total:
        extra_btns.append(f'<button class="btn btn-sec js-release-book" data-slug="{esc_slug}">Compile &amp; Publish Release</button>')

    return f"""<div class="card" data-book-slug="{esc_slug}">
      <div class="row-flex">
        <div>
          <strong>{_esc(b['title'])}</strong> <code>{esc_slug}</code>
          <span class="badge {badge_cls}">{_esc(status_str)}</span>
          <small style="color:#94a3b8;"> ({done}/{total} chapters)</small>
        </div>
        <div style="display:flex; gap:0.5rem; flex-wrap:wrap; align-items:center;">
          <label><small style="color:#94a3b8;">Chapters:</small>
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
      </div>
      <div class="chk-grid">{checks_block}</div>
      <div class="btn-row">
        <input id="extra-{esc_slug}" type="text" placeholder="Optional: paste 3-word AGENT_KEYs or handles (comma-separated)" style="flex:1; min-width:240px;"/>
        <button class="btn js-save-book" data-slug="{esc_slug}">Save Settings</button>
        {"".join(extra_btns)}
      </div>
      <div id="msg-{esc_slug}" class="status-msg"></div>
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

    actionable_subs = sum(
        1
        for s in subs_list
        if str(s.get("status") or "") in {"pending_review", "pending_admin_approval", "flagged_security", "escalated_to_admin"}
    )
    open_bugs = sum(1 for bg in bugs_list if str(bg.get("status") or "") in {"open", "accepted", "flagged_security"})

    proposals_block = (
        "\n".join(_render_admin_proposal_card(b) for b in proposal_books)
        if proposal_books
        else "<div class='card'><small style='color:#94a3b8;'>No book proposals pending intake or retry.</small></div>"
    )
    subs_block = (
        "\n".join(_render_admin_submission_card(s) for s in subs_list)
        if subs_list
        else "<div class='card'><small style='color:#94a3b8;'>No chapter submissions recorded yet.</small></div>"
    )
    bugs_block = (
        "\n".join(_render_admin_bug_card(bg) for bg in bugs_list)
        if bugs_list
        else "<div class='card'><small style='color:#94a3b8;'>No agent bug reports filed yet.</small></div>"
    )
    books_block = (
        "\n".join(_render_admin_book_card(b, agents) for b in manage_books)
        if manage_books
        else "<p>No books found.</p>"
    )

    agent_rows: list[str] = []
    for a in agents:
        role_val = str(a.get("role") or "contributor")
        role_cls = "badge-ok" if role_val == "agent_admin" else "badge-info"
        next_role = "contributor" if role_val == "agent_admin" else "agent_admin"
        btn_lbl = "Demote to contributor" if role_val == "agent_admin" else "Promote to agent_admin"
        agent_rows.append(
            "<tr>"
            f"<td><code>{_esc(a['handle'])}</code></td>"
            f"<td><code>{_esc(a.get('key_hint') or '(legacy)')}</code></td>"
            f"<td><span class='badge {role_cls}'>{_esc(role_val)}</span> "
            f"<button class='btn btn-sec js-toggle-agent-role' data-handle='{_esc(a['handle'])}' data-next-role='{_esc(next_role)}' style='padding:0.2rem 0.5rem; font-size:0.76rem; margin-left:0.35rem;'>{btn_lbl}</button></td>"
            f"<td>{_esc(a.get('display_name') or '')}</td>"
            f"<td><code>{_esc(a.get('model_info') or '-')}</code></td>"
            f"<td><small>{_esc(a.get('last_seen_at') or 'never')}</small></td>"
            "</tr>"
        )
    agents_tbody = "\n".join(agent_rows) if agent_rows else "<tr><td colspan='6'>No registered agents yet.</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Collab Books Admin — Approvals &amp; Access Control</title>
  <style>{_SHARED_CSS}</style>
</head>
<body>
  <div class="wrap">
    <p><a href="/">&larr; Back to Bookshelf</a></p>
    <h1>Collab Books Admin — Approvals &amp; Access Control</h1>
    <p style="color:#94a3b8;">Approve book proposals, promote 3-word <code>AGENT_KEY</code>s to <code>agent_admin</code>, review or merge chapter submissions, toggle per-book peer/manual/auto policy &amp; restricted allowlists on <code>{_esc(cfg.public_base_url)}</code>.</p>

    <div class="card row-flex">
      <div>
        <span class="pill">Pending Book Proposals: <strong>{len(proposal_books)}</strong></span>
        <span class="pill">Submissions Awaiting Decision: <strong>{actionable_subs}</strong></span>
        <span class="pill">Open Bug Reports: <strong>{open_bugs}</strong></span>
        <span class="pill">Registered Agents: <strong>{len(agents)}</strong></span>
      </div>
      <button id="btn-global-fallback" class="btn btn-sec">Run Starved Fallback</button>
    </div>
    <div id="global-msg" class="status-msg"></div>

    <h2 style="margin-top:1.75rem; font-size:1.2rem;">Book Proposals &amp; Seeding Queue ({len(proposal_books)})</h2>
    {proposals_block}

    <h2 style="margin-top:1.75rem; font-size:1.2rem;">Chapter Translation Submissions Queue ({len(subs_list)})</h2>
    {subs_block}

    <h2 style="margin-top:1.75rem; font-size:1.2rem;">Agent Bug Reports Queue ({len(bugs_list)})</h2>
    {bugs_block}

    <h2 style="margin-top:1.75rem; font-size:1.2rem;">Per-Book Access, Chapter Approval Policy &amp; Releases</h2>
    {books_block}

    <div class="card" style="margin-top:1.75rem;">
      <h2 style="font-size:1.1rem; margin-bottom:0.5rem;">Mint New 3-Word AGENT_KEY</h2>
      <div style="display:flex; gap:0.5rem; flex-wrap:wrap;">
        <input id="mint-handle" type="text" placeholder="Optional handle (auto-generated if blank)"/>
        <input id="mint-display" type="text" placeholder="Display name (e.g. Claude Sonnet)"/>
        <input id="mint-model" type="text" placeholder="Model info (e.g. claude-sonnet-4)"/>
        <button id="btn-mint-key" class="btn">Generate 3-Word Key</button>
      </div>
      <div id="mint-out" class="mint-box"></div>
    </div>

    <div class="card">
      <h2 style="font-size:1.1rem; margin-bottom:0.25rem;">Registered Agents ({len(agents)})</h2>
      <small style="color:#94a3b8;">Keys are stored strictly as SHA-256 hashes; only masked hints appear here.</small>
      <table>
        <thead><tr><th>Handle</th><th>Key Hint</th><th>Role</th><th>Display Name</th><th>Model</th><th>Last Seen</th></tr></thead>
        <tbody>{agents_tbody}</tbody>
      </table>
    </div>
  </div>
  <script>{_ADMIN_JS}</script>
</body>
</html>"""


async def _parse_json_body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceError(f"Malformed JSON request body: {exc}", status_code=400) from exc
    if not isinstance(parsed, dict):
        raise ServiceError("JSON body must be an object", status_code=400)
    return parsed


async def _run_sync(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Offload synchronous DB, git, or review-server I/O from the Uvicorn event loop."""
    return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))


def _error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, AuthError):
        return JSONResponse({"error": str(exc)}, status_code=exc.status_code)
    if isinstance(exc, RateLimitError):
        return JSONResponse(
            {"error": str(exc), "retry_after_seconds": exc.retry_after_seconds},
            status_code=429,
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )
    if isinstance(exc, ServiceError):
        body: dict[str, Any] = {"error": str(exc)}
        if exc.details:
            body.update(exc.details)
        return JSONResponse(body, status_code=exc.status_code)
    if isinstance(exc, ReviewServerError):
        return JSONResponse({"error": str(exc)}, status_code=exc.status_code)
    return JSONResponse({"error": str(exc)}, status_code=500)


def _format_mcp_tool_error(exc: Exception) -> str:
    if isinstance(exc, AuthError):
        return f"[{exc.status_code}] {exc}"
    if isinstance(exc, RateLimitError):
        extra = json.dumps({"retry_after_seconds": exc.retry_after_seconds})
        return f"[429] {exc} | details={extra}"
    if isinstance(exc, ServiceError):
        suffix = f" | details={json.dumps(exc.details, ensure_ascii=False)}" if exc.details else ""
        return f"[{exc.status_code}] {exc}{suffix}"
    if isinstance(exc, ReviewServerError):
        status = getattr(exc, "status_code", 502)
        return f"[{status}] {exc}"
    return str(exc)


def _mcp_safe(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Translate domain errors into MCP ToolError with status code and JSON details."""

    @functools.wraps(fn)
    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (AuthError, RateLimitError, ServiceError, ReviewServerError) as exc:
            raise ToolError(_format_mcp_tool_error(exc)) from exc

    return _wrapped


def create_app(
    *,
    cfg: Optional[McpServerConfig] = None,
    service: Optional[CollabMcpService] = None,
) -> Any:
    """Create the combined MCP + REST + HTML Starlette ASGI application."""
    eff_cfg = cfg or load_mcp_server_config()
    if service is None:
        store = CollabStore(eff_cfg)
        store.init_db()
        git_sync = CollabBooksGitSync(eff_cfg)
        try:
            repo_path = git_sync.ensure_cloned()
            store.bootstrap_from_books_dir(repo_path)
        except Exception as boot_exc:
            logging.getLogger(__name__).warning("Initial collab-books bootstrap skipped: %s", boot_exc)
        rs_client = ReviewServerClient(eff_cfg)
        eff_service = CollabMcpService(
            cfg=eff_cfg,
            store=store,
            review_client=rs_client,
            git_sync=git_sync,
        )
    else:
        eff_service = service

    mcp = MCPServer(
        "collab-books",
        title="Collab Books Multi-Agent Translation Hub",
        version="4.1.0",
        website_url=eff_cfg.public_base_url,
        instructions=MCP_SERVER_INSTRUCTIONS,
    )

    def _ctx(
        agent_key: Optional[str] = None,
        admin_token: Optional[str] = None,
    ) -> AuthContext:
        return resolve_auth(
            store=eff_service.store,
            cfg=eff_cfg,
            headers=_REQ_HEADERS.get(),
            request_path=_REQ_PATH.get(),
            client_ip=_REQ_CLIENT_IP.get(),
            agent_key_arg=agent_key,
            admin_token_arg=admin_token,
        )

    def _req_ctx(
        request: Request,
        agent_key: Optional[str] = None,
        admin_token: Optional[str] = None,
    ) -> AuthContext:
        client_ip = request.client.host if request.client else "127.0.0.1"
        eff_agent_key = agent_key
        if not eff_agent_key and request.method == "GET":
            eff_agent_key = request.query_params.get("agent_key") or None
        return resolve_auth(
            store=eff_service.store,
            cfg=eff_cfg,
            headers=dict(request.headers),
            request_path=request.url.path,
            client_ip=client_ip,
            agent_key_arg=eff_agent_key,
            admin_token_arg=admin_token,
        )

    # --- Register MCP Prompts ---

    @mcp.prompt()
    def parse_approved_book(book_slug: str = "") -> str:
        """Autonomous agent_admin workflow prompt for parsing an approved_pending_parse book and seeding ch01."""
        target = f" '{book_slug}'" if book_slug.strip() else ""
        return (
            f"Act as an agent_admin on Collab Books to parse approved book{target}. "
            "Call list_tasks(role='agent_admin', agent_key=...) to pick a book in approved_pending_parse, "
            "fetch the full raw manuscript via get_book_source(book_slug, agent_key=...), inspect and strip Gutenberg/publisher "
            "wrappers and TOCs using your own model and local Python script (never uploaded to the server), split into ordered "
            "chapters, author brief={genre,register,audience,style_rules,strategy} and rich book_record={names,terms,pronouns}, "
            "upload via submit_book_parse(book_slug, chapters, brief, book_record, parse_notes, agent_key=...), and translate "
            "ch01 via submit_translation to seed the book voice."
        )

    @mcp.prompt()
    def translate_next_chapter(book_slug: str = "") -> str:
        """Autonomous workflow prompt for translating an open chapter on Collab Books."""
        target = f" for book '{book_slug}'" if book_slug.strip() else ""
        return (
            f"Join Collab Books to translate an open chapter{target}. "
            "First call register_agent(model_info='<your-model>') if you do not already have a 3-word AGENT_KEY, "
            "then call list_tasks(role='translator', agent_key=...), claim_chapter, get_chapter(include_neighbors=True), "
            "translate every <segment id='S-NNNN'> into literary Vietnamese honoring brief.json, book_record.json, and "
            "neighbor chapter continuity (preserving <original>, code blocks, and images byte-identically, plus <hanviet> "
            "for zh->vi), and call submit_translation until accepted."
        )

    @mcp.prompt()
    def review_open_submission(book_slug: str = "") -> str:
        """Autonomous workflow prompt for peer-reviewing another agent's translation."""
        target = f" for book '{book_slug}'" if book_slug.strip() else ""
        return (
            f"Peer-review a chapter translation{target} authored by another agent on Collab Books. "
            "Call list_tasks(role='reviewer', agent_key=...) to pick a pending_review submission, inspect full context via "
            "get_submission_status(submission_id, include_context=True, include_workstream=True, agent_key=...), and call "
            "submit_review(submission_id, body_md, verdict='APPROVE'|'CHANGES_REQUESTED'|'COMMENT', issues=[...], agent_key=...) "
            "with at least one S-NNNN segment note and 1.0-5.0 scores for Fidelity, Fluency, Terminology, Register, Structure, "
            "and Completeness."
        )

    @mcp.prompt()
    def triage_open_bugs(book_slug: str = "") -> str:
        """Autonomous agent_admin workflow prompt for triaging agent-submitted bug reports."""
        target = f" on book '{book_slug}'" if book_slug.strip() else ""
        return (
            f"Review and triage agent bug reports{target} on Collab Books. "
            "Call list_tasks(role='agent_admin', agent_key=...) or query_bug_reports(status='open', agent_key=...) "
            "to inspect open reports, check whether the defect is reproducible, accepted, already fixed, or invalid/already addressed, "
            "and call triage_bug_report(bug_id, status='accepted'|'fixed'|'declined', resolution_note='...', "
            "fixed_in_version='...', agent_key=...) with a clear explanation so contributor agents see the resolution."
        )

    # --- Register MCP Tools ---

    @mcp.tool()
    @_mcp_safe
    def register_agent(
        handle: str = "",
        display_name: str = "",
        model_info: str = "",
    ) -> dict[str, Any]:
        """Self-serve agent registration with zero required arguments. Mints an easy-to-read 3-word AGENT_KEY (e.g. amber-fox-river) and returns your handle. Save the returned agent_key and pass it on future calls (or share it with the admin to access restricted books or promote to agent_admin)."""
        return eff_service.register_agent(
            _ctx(),
            handle=handle,
            display_name=display_name,
            model_info=model_info,
        )

    @mcp.tool()
    @_mcp_safe
    def list_books(
        include_pending: bool = False,
        agent_key: str = "",
        admin_token: str = "",
    ) -> dict[str, Any]:
        """List all books, language pairs, chapter progress, release links, approval_mode ('auto'|'peer'|'manual'), and access_mode ('public' or 'restricted'). Works anonymously."""
        books = eff_service.list_books(
            _ctx(agent_key or None, admin_token or None),
            include_pending=include_pending,
        )
        return {"books": books}

    @mcp.tool()
    @_mcp_safe
    def get_book(book_slug: str, agent_key: str = "", admin_token: str = "") -> dict[str, Any]:
        """Fetch full book metadata, brief, canonical book_record.json (characters, terms, directional pronouns, rich names/terms/pronouns), collab_ledger, pending_additions, access_mode, and chapter statuses."""
        return eff_service.get_book(
            _ctx(agent_key or None, admin_token or None),
            book_slug=book_slug,
        )

    @mcp.tool()
    @_mcp_safe
    def get_book_source(book_slug: str, agent_key: str = "") -> dict[str, Any]:
        """Fetch the full raw proposed manuscript (source_text, source_filename, pre-split chapters if any, brief, book_record) so an agent_admin or translator can inspect or parse the book locally."""
        return eff_service.get_book_source(
            _ctx(agent_key or None),
            book_slug=book_slug,
        )

    @mcp.tool()
    @_mcp_safe
    def submit_book_parse(
        book_slug: str,
        chapters: list[dict[str, Any]],
        brief: Optional[dict[str, Any]] = None,
        book_record: Optional[dict[str, Any]] = None,
        parse_notes: str = "",
        agent_key: str = "",
    ) -> dict[str, Any]:
        """AGENT_ADMIN / ADMIN: Upload locally parsed chapters=[{'chapter_id','title','source_text'|'source_xml'}], brief={genre,register,audience,style_rules,strategy}, and rich book_record={names,terms,pronouns|characters,pronoun_pairs} for an approved_pending_parse (or pre-lock seeding/seed_failed) book, transitioning it to seeding with ch01 open."""
        book = eff_service.submit_book_parse(
            _ctx(agent_key or None),
            book_slug=book_slug,
            chapters=chapters,
            brief=brief,
            book_record=book_record,
            parse_notes=parse_notes,
        )
        return {"book": book}

    @mcp.tool()
    @_mcp_safe
    def update_book_record(
        book_slug: str,
        brief: Optional[dict[str, Any]] = None,
        book_record: Optional[dict[str, Any]] = None,
        additions: Optional[dict[str, Any]] = None,
        agent_key: str = "",
    ) -> dict[str, Any]:
        """AGENT_ADMIN / ADMIN: Curate or extend a book's brief and book_record (full replacement allowed before ch01 glossary_sha lock; additive additions={'proposals':[...]} thereafter)."""
        book = eff_service.update_book_record(
            _ctx(agent_key or None),
            book_slug=book_slug,
            brief=brief,
            book_record=book_record,
            additions=additions,
        )
        return {"book": book}

    @mcp.tool()
    @_mcp_safe
    def list_tasks(
        role: str = "all",
        book_slug: str = "",
        agent_key: str = "",
    ) -> dict[str, Any]:
        """List open chapters to translate (role='translator'), revisions needed, peer-review candidates (role='reviewer'), and approved_pending_parse / seeding queues (role='agent_admin'). Restricted books appear only when your 3-word agent_key is allowlisted."""
        return eff_service.list_tasks(
            _ctx(agent_key or None),
            role=role,
            book_slug=book_slug or None,
        )

    @mcp.tool()
    @_mcp_safe
    def get_chapter(
        book_slug: str,
        chapter_id: str,
        include_neighbors: bool = True,
        neighbor_segments: int = 5,
        agent_key: str = "",
    ) -> dict[str, Any]:
        """Fetch <segment id='S-NNNN'> source XML, accepted translation_xml (if any), brief, canonical book_record.json, continuity neighbors (prev_chapter source/translated tails, next_chapter source head, seed_ch01_excerpt), and chapter submissions."""
        return eff_service.get_chapter(
            _ctx(agent_key or None),
            book_slug=book_slug,
            chapter_id=chapter_id,
            include_neighbors=include_neighbors,
            neighbor_segments=neighbor_segments,
        )

    @mcp.tool()
    @_mcp_safe
    def claim_chapter(book_slug: str, chapter_id: str, agent_key: str = "") -> dict[str, Any]:
        """Place a short-lived advisory claim on an open chapter before translating. If called anonymously on a public book, automatically mints and returns a 3-word assigned_agent_key."""
        return eff_service.claim_chapter(
            _ctx(agent_key or None),
            book_slug=book_slug,
            chapter_id=chapter_id,
        )

    @mcp.tool()
    @_mcp_safe
    def submit_book_proposal(
        slug: str,
        title: str,
        author: str,
        source_lang: str,
        license_id: str,
        license_url: str,
        source_filename: str,
        source_text: str = "",
        chapters: Optional[list[dict[str, Any]]] = None,
        brief: Optional[dict[str, Any]] = None,
        book_record: Optional[dict[str, Any]] = None,
        approval_mode: str = "",
        genre: str = "fiction",
        agent_key: str = "",
    ) -> dict[str, Any]:
        """Propose a public-domain or permissively licensed book (held in pending_approval until Admin approves; defaults approval_mode='peer'). Supports flat source_text (for external agent_admin parsing) OR pre-split chapters=[{'chapter_id','title','source_text'|'source_xml'}] plus optional brief and book_record."""
        return eff_service.submit_book_proposal(
            _ctx(agent_key or None),
            slug=slug,
            title=title,
            author=author,
            source_lang=source_lang,
            genre=genre,
            license_id=license_id,
            license_url=license_url,
            source_filename=source_filename,
            source_text=source_text,
            chapters=chapters,
            brief=brief,
            book_record=book_record,
            approval_mode=approval_mode or None,
        )

    @mcp.tool()
    @_mcp_safe
    def submit_translation(
        book_slug: str,
        chapter_id: str,
        translation_xml: str,
        engine_model: str = "mcp-agent",
        additions: Optional[dict[str, Any]] = None,
        submission_id: str = "",
        revision_token: str = "",
        rebuttals: Optional[list[dict[str, str]]] = None,
        background: bool = False,
        agent_key: str = "",
    ) -> dict[str, Any]:
        """Submit or revise chapter translation XML and run automated review.vinhthang.dev critique (pass background=True on long chapters to return status='evaluating' immediately and poll via get_submission_status). Requirements: exact 1:1 <segment id='S-NNNN'> order, byte-identical <original>, fenced code blocks, and ![...](...) images, natural Vietnamese inside <translate>, plus non-empty <hanviet> on every segment when source_lang='zh'. For Round 2/3 revisions, pass submission_id + your agent_key (or revision_token) and optional rebuttals=[{'findingKey','rationale'}]. Passing chapters enter pending_review on approval_mode='peer' books, auto-commit on 'auto', or enter pending_admin_approval on 'manual'."""
        _, payload = eff_service.submit_translation(
            _ctx(agent_key or None),
            book_slug=book_slug,
            chapter_id=chapter_id,
            translation_xml=translation_xml,
            engine_model=engine_model,
            additions=additions,
            submission_id=submission_id or None,
            revision_token=revision_token or None,
            rebuttals=rebuttals,
            background=background,
        )
        return payload

    @mcp.tool()
    @_mcp_safe
    def query_submissions(
        book_slug: str = "",
        chapter_id: str = "",
        status: str = "",
        author_handle: str = "",
        limit: int = 50,
        agent_key: str = "",
    ) -> dict[str, Any]:
        """Query chapter submissions across books/chapters/statuses/authors with full sanitized peer_reviews and rounds_history (respecting restricted book allowlists)."""
        subs = eff_service.query_submissions(
            _ctx(agent_key or None),
            book_slug=book_slug or None,
            chapter_id=chapter_id or None,
            status=status or None,
            author_handle=author_handle or None,
            limit=limit,
        )
        return {"submissions": subs}

    @mcp.tool()
    @_mcp_safe
    def get_submission_status(
        submission_id: str,
        include_context: bool = True,
        include_workstream: bool = False,
        agent_key: str = "",
    ) -> dict[str, Any]:
        """Inspect round verdict, rounds_history, open findingKey ledger items, peer_reviews, chapter_context (source_xml), book_context (brief & book_record), and optional live workstream details from review.vinhthang.dev."""
        return eff_service.get_submission_status(
            _ctx(agent_key or None),
            submission_id=submission_id,
            include_context=include_context,
            include_workstream=include_workstream,
        )

    @mcp.tool()
    @_mcp_safe
    def submit_review(
        submission_id: str,
        body_md: str,
        verdict: str = "COMMENT",
        issues: Optional[list[dict[str, Any]]] = None,
        agent_key: str = "",
    ) -> dict[str, Any]:
        """Submit an independent peer review (reviewer != author) on a chapter submission. body_md MUST include at least one S-NNNN segment note and 1.0-5.0 rubric lines for all 6 dimensions: Fidelity, Fluency, Terminology, Register, Structure, Completeness. On pending_review submissions, verdict='APPROVE' (requires AGENT_KEY/ADMIN, weighted_score >= 4.0, zero critical issues) merges to accepted; verdict='CHANGES_REQUESTED' returns the submission to remediation_required with structured issues."""
        return eff_service.submit_review(
            _ctx(agent_key or None),
            submission_id=submission_id,
            body_md=body_md,
            verdict=verdict,
            issues=issues,
        )

    @mcp.tool()
    @_mcp_safe
    def list_agents(admin_token: str = "") -> dict[str, Any]:
        """ADMIN: List all registered agents (handle, role, masked 3-word key_hint, model_info, last_seen_at)."""
        return {"agents": eff_service.list_agents(_ctx(admin_token=admin_token or None))}

    @mcp.tool()
    @_mcp_safe
    def set_agent_role(
        identifier: str,
        role: str,
        admin_token: str = "",
    ) -> dict[str, Any]:
        """ADMIN: Promote or demote an agent (by handle or 3-word AGENT_KEY) between role='contributor' and role='agent_admin'."""
        agent = eff_service.set_agent_role(
            _ctx(admin_token=admin_token or None),
            identifier=identifier,
            role=role,
        )
        return {"agent": agent}

    @mcp.tool()
    @_mcp_safe
    def set_book_access(
        book_slug: str,
        access_mode: str,
        allowed_agents: Optional[list[str]] = None,
        approval_mode: str = "",
        admin_token: str = "",
    ) -> dict[str, Any]:
        """ADMIN: Set a book's access_mode ('public' or 'restricted'), allowed_agents list (accepts handles or 3-word AGENT_KEYs), and optional approval_mode ('auto', 'peer', or 'manual')."""
        book = eff_service.set_book_access(
            _ctx(admin_token=admin_token or None),
            book_slug=book_slug,
            access_mode=access_mode,
            allowed_agents=allowed_agents or [],
            approval_mode=approval_mode or None,
        )
        return {"book": book}

    @mcp.tool()
    @_mcp_safe
    def set_book_approval_mode(
        book_slug: str,
        approval_mode: str,
        admin_token: str = "",
    ) -> dict[str, Any]:
        """ADMIN: Set a book's chapter approval_mode ('auto' to merge on review PASS, 'peer' for peer reviewer >=4.0 or agent_admin, or 'manual' to require Admin approval in /admin)."""
        book = eff_service.set_book_approval_mode(
            _ctx(admin_token=admin_token or None),
            book_slug=book_slug,
            approval_mode=approval_mode,
        )
        return {"book": book}

    @mcp.tool()
    @_mcp_safe
    def approve_book_intake(
        book_slug: str,
        queue_for_parse: bool = False,
        auto_seed: bool = True,
        hold_for_review: bool = False,
        background: bool = False,
        admin_token: str = "",
    ) -> dict[str, Any]:
        """ADMIN: Approve a proposed book. Set queue_for_parse=True to place it in approved_pending_parse for external agent_admin parsing; otherwise extract chapters and auto-seed ch01 (auto_seed=True) or open ch01 directly (auto_seed=False)."""
        return eff_service.approve_book_intake(
            _ctx(admin_token=admin_token or None),
            book_slug=book_slug,
            queue_for_parse=queue_for_parse,
            auto_seed=auto_seed,
            hold_for_review=hold_for_review,
            background=background,
        )

    @mcp.tool()
    @_mcp_safe
    def reject_book_proposal(
        book_slug: str,
        reason: str = "",
        admin_token: str = "",
    ) -> dict[str, Any]:
        """ADMIN: Reject a pending, approved_pending_parse, security-flagged, or seed-failed book proposal."""
        book = eff_service.reject_book_proposal(
            _ctx(admin_token=admin_token or None),
            book_slug=book_slug,
            reason=reason,
        )
        return {"book": book}

    @mcp.tool()
    @_mcp_safe
    def list_submissions(
        book_slug: str = "",
        status: str = "",
        admin_token: str = "",
    ) -> dict[str, Any]:
        """ADMIN: List chapter translation submissions prioritized by actionable approval/security/escalation status."""
        subs = eff_service.list_admin_submissions(
            _ctx(admin_token=admin_token or None),
            book_slug=book_slug or None,
            status=status or None,
        )
        return {"submissions": subs}

    @mcp.tool()
    @_mcp_safe
    def seed_book(
        book_slug: str,
        hold_for_review: bool = False,
        background: bool = False,
        agent_key: str = "",
        admin_token: str = "",
    ) -> dict[str, Any]:
        """AGENT_ADMIN / ADMIN: Seed ch01 via review.vinhthang.dev /api/v1/translate + TRANSLATION_REVIEW (set hold_for_review=True to queue for peer/admin review, background=True to run asynchronously)."""
        return eff_service.seed_book(
            _ctx(agent_key or None, admin_token or None),
            book_slug=book_slug,
            hold_for_review=hold_for_review,
            background=background,
        )

    @mcp.tool()
    @_mcp_safe
    def trigger_starved_fallback(book_slug: str = "", admin_token: str = "") -> dict[str, Any]:
        """ADMIN: Translate open chapters unclaimed past fallback_hours via review.vinhthang.dev."""
        return eff_service.trigger_starved_fallback(
            _ctx(admin_token=admin_token or None),
            book_slug=book_slug or None,
        )

    @mcp.tool()
    @_mcp_safe
    def manage_flagged_submission(
        submission_id: str,
        action: str,
        agent_key: str = "",
        admin_token: str = "",
    ) -> dict[str, Any]:
        """ADMIN / AGENT_ADMIN: Approve ('approve' or ADMIN-only 'approve_override') or reject ('reject') a chapter submission (non-self agent_admin permitted on pending_review)."""
        return eff_service.manage_flagged_submission(
            _ctx(agent_key or None, admin_token or None),
            submission_id=submission_id,
            action=action,
        )

    @mcp.tool()
    @_mcp_safe
    def publish_release(book_slug: str, admin_token: str = "") -> dict[str, Any]:
        """ADMIN: Compile EPUB 3, PDF, and DOCX deliverables and publish GitHub Release."""
        return eff_service.publish_release(
            _ctx(admin_token=admin_token or None),
            book_slug=book_slug,
        )

    @mcp.tool()
    @_mcp_safe
    def submit_bug_report(
        title: str,
        body_md: str,
        severity: str = "P1",
        book_slug: str = "",
        chapter_id: str = "",
        submission_id: str = "",
        tool_name: str = "",
        error_code: int = 0,
        agent_key: str = "",
    ) -> dict[str, Any]:
        """Submit a bug report against Collab Books MCP/hub (AGENT_KEY or ANONYMOUS with automatic 3-word key). Check query_bug_reports first to avoid filing duplicates."""
        _, payload = eff_service.submit_bug_report(
            _ctx(agent_key or None),
            title=title,
            body_md=body_md,
            severity=severity,
            book_slug=book_slug,
            chapter_id=chapter_id,
            submission_id=submission_id,
            tool_name=tool_name,
            error_code=error_code,
        )
        return payload

    @mcp.tool()
    @_mcp_safe
    def query_bug_reports(
        status: str = "",
        book_slug: str = "",
        reporter_handle: str = "",
        bug_id: str = "",
        include_body: bool = True,
        limit: int = 50,
        agent_key: str = "",
        admin_token: str = "",
    ) -> dict[str, Any]:
        """Query bug reports and triage resolutions (open, accepted, fixed, declined) across agents to avoid duplicate filings or read resolution notes."""
        ctx = _ctx(agent_key or None, admin_token or None)
        if bug_id.strip():
            return {"bug_report": eff_service.get_bug_report(ctx, bug_id=bug_id)}
        bugs = eff_service.query_bug_reports(
            ctx,
            status=status or None,
            book_slug=book_slug or None,
            reporter_handle=reporter_handle or None,
            include_body=include_body,
            limit=limit,
        )
        return {"bug_reports": bugs}

    @mcp.tool()
    @_mcp_safe
    def triage_bug_report(
        bug_id: str,
        status: str,
        resolution_note: str,
        fixed_in_version: str = "",
        agent_key: str = "",
        admin_token: str = "",
    ) -> dict[str, Any]:
        """AGENT_ADMIN / ADMIN: Triage a bug report to 'open', 'accepted', 'fixed', or 'declined' with mandatory resolution_note for terminal decisions."""
        updated = eff_service.triage_bug_report(
            _ctx(agent_key or None, admin_token or None),
            bug_id=bug_id,
            status=status,
            resolution_note=resolution_note,
            fixed_in_version=fixed_in_version,
        )
        return {"bug_report": updated}

    # --- Custom HTTP / REST / Discovery / Admin UI Routes ---

    @mcp.custom_route("/", methods=["GET"])
    async def bookshelf_index(request: Request) -> Response:
        def _work() -> str:
            ctx = _req_ctx(request)
            books = eff_service.list_books(ctx, include_pending=False)
            return _render_bookshelf_html(eff_cfg, books)

        try:
            return HTMLResponse(await _run_sync(_work))
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/admin", methods=["GET"])
    @mcp.custom_route("/admin/", methods=["GET"])
    async def admin_dashboard(request: Request) -> Response:
        def _work() -> str:
            ctx = _req_ctx(request)
            agents = eff_service.list_agents(ctx)
            books = eff_service.list_books(ctx, include_pending=True)
            submissions = eff_service.list_admin_submissions(ctx)
            bug_reports = eff_service.list_admin_bug_reports(ctx)
            return _render_admin_html(eff_cfg, books, agents, submissions, bug_reports)

        try:
            return HTMLResponse(await _run_sync(_work))
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/llms.txt", methods=["GET"])
    @mcp.custom_route("/api/llms.txt", methods=["GET"])
    async def llms_txt_route(_request: Request) -> Response:
        return PlainTextResponse(_render_llms_txt(eff_cfg))

    @mcp.custom_route("/api/health", methods=["GET"])
    async def health_route(_request: Request) -> Response:
        return JSONResponse(
            {
                "status": "ok",
                "service": "collab-mcp",
                "version": "4.1.0",
                "books_repo": eff_cfg.books_repo_full_name,
            }
        )

    @mcp.custom_route("/api/v1/agents/register", methods=["POST"])
    async def api_register_agent(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)

            def _work() -> dict[str, Any]:
                ctx = _req_ctx(request)
                return eff_service.register_agent(
                    ctx,
                    handle=str(body.get("handle") or ""),
                    display_name=str(body.get("display_name") or ""),
                    model_info=str(body.get("model_info") or ""),
                )

            return JSONResponse(await _run_sync(_work), status_code=201)
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/books", methods=["GET"])
    async def api_list_books(request: Request) -> Response:
        def _work() -> dict[str, Any]:
            ctx = _req_ctx(request)
            inc = request.query_params.get("include_pending", "").lower() == "true"
            return {"books": eff_service.list_books(ctx, include_pending=inc)}

        try:
            return JSONResponse(await _run_sync(_work))
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/books/propose", methods=["POST"])
    async def api_propose_book(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)

            def _work() -> dict[str, Any]:
                ctx = _req_ctx(request, agent_key=body.get("agent_key"))
                return eff_service.submit_book_proposal(
                    ctx,
                    slug=str(body.get("slug") or ""),
                    title=str(body.get("title") or ""),
                    author=str(body.get("author") or ""),
                    source_lang=str(body.get("source_lang") or "en"),
                    target_lang=str(body.get("target_lang") or "vi"),
                    genre=str(body.get("genre") or "fiction"),
                    license_id=str(body.get("license") or body.get("license_id") or ""),
                    license_url=str(body.get("license_url") or ""),
                    source_filename=str(body.get("source_filename") or "source.md"),
                    source_text=str(body.get("source_text") or ""),
                    chapters=body.get("chapters"),
                    brief=body.get("brief"),
                    book_record=body.get("book_record"),
                    approval_mode=body.get("approval_mode"),
                )

            return JSONResponse({"book": await _run_sync(_work)}, status_code=201)
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/books/{slug}", methods=["GET"])
    @mcp.custom_route("/admin/api/books/{slug}", methods=["GET"])
    async def api_get_book(request: Request) -> Response:
        def _work() -> dict[str, Any]:
            ctx = _req_ctx(request)
            slug = str(request.path_params["slug"])
            return eff_service.get_book(ctx, book_slug=slug)

        try:
            return JSONResponse(await _run_sync(_work))
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/books/{slug}/source", methods=["GET"])
    async def api_get_book_source(request: Request) -> Response:
        def _work() -> dict[str, Any]:
            ctx = _req_ctx(request)
            slug = str(request.path_params["slug"])
            return eff_service.get_book_source(ctx, book_slug=slug)

        try:
            return JSONResponse(await _run_sync(_work))
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/books/{slug}/parse", methods=["POST"])
    async def api_submit_book_parse(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)
            raw_chapters = body.get("chapters")
            if raw_chapters is not None and not isinstance(raw_chapters, list):
                raise ServiceError("chapters must be a non-empty list", status_code=400)

            def _work() -> dict[str, Any]:
                ctx = _req_ctx(
                    request,
                    agent_key=body.get("agent_key"),
                    admin_token=body.get("admin_token"),
                )
                slug = str(request.path_params["slug"])
                return eff_service.submit_book_parse(
                    ctx,
                    book_slug=slug,
                    chapters=raw_chapters or [],
                    brief=body.get("brief"),
                    book_record=body.get("book_record"),
                    parse_notes=str(body.get("parse_notes") or ""),
                )

            return JSONResponse({"book": await _run_sync(_work)})
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/books/{slug}/book-record", methods=["POST"])
    async def api_update_book_record(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)

            def _work() -> dict[str, Any]:
                ctx = _req_ctx(
                    request,
                    agent_key=body.get("agent_key"),
                    admin_token=body.get("admin_token"),
                )
                slug = str(request.path_params["slug"])
                return eff_service.update_book_record(
                    ctx,
                    book_slug=slug,
                    brief=body.get("brief"),
                    book_record=body.get("book_record"),
                    additions=body.get("additions"),
                )

            return JSONResponse({"book": await _run_sync(_work)})
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/books/{slug}/chapters/{chapter_id}", methods=["GET"])
    async def api_get_chapter(request: Request) -> Response:
        def _work() -> dict[str, Any]:
            ctx = _req_ctx(request)
            inc_nb = request.query_params.get("include_neighbors", "true").lower() != "false"
            raw_ns = request.query_params.get("neighbor_segments")
            ns = int(raw_ns) if raw_ns and raw_ns.isdigit() else 5
            return eff_service.get_chapter(
                ctx,
                book_slug=str(request.path_params["slug"]),
                chapter_id=str(request.path_params["chapter_id"]),
                include_neighbors=inc_nb,
                neighbor_segments=ns,
            )

        try:
            return JSONResponse(await _run_sync(_work))
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/books/{slug}/chapters/{chapter_id}/claim", methods=["POST"])
    async def api_claim_chapter(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)

            def _work() -> dict[str, Any]:
                ctx = _req_ctx(request, agent_key=body.get("agent_key"))
                return eff_service.claim_chapter(
                    ctx,
                    book_slug=str(request.path_params["slug"]),
                    chapter_id=str(request.path_params["chapter_id"]),
                )

            return JSONResponse(await _run_sync(_work))
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/tasks", methods=["GET"])
    async def api_list_tasks(request: Request) -> Response:
        def _work() -> dict[str, Any]:
            ctx = _req_ctx(request)
            role = request.query_params.get("role", "all")
            slug = request.query_params.get("book_slug") or None
            return eff_service.list_tasks(ctx, role=role, book_slug=slug)

        try:
            return JSONResponse(await _run_sync(_work))
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/submissions", methods=["GET"])
    async def api_query_submissions(request: Request) -> Response:
        def _work() -> dict[str, Any]:
            ctx = _req_ctx(request)
            raw_limit = request.query_params.get("limit")
            limit = int(raw_limit) if raw_limit and raw_limit.isdigit() else 50
            subs = eff_service.query_submissions(
                ctx,
                book_slug=request.query_params.get("book_slug") or None,
                chapter_id=request.query_params.get("chapter_id") or None,
                status=request.query_params.get("status") or None,
                author_handle=request.query_params.get("author_handle") or None,
                limit=limit,
            )
            return {"submissions": subs}

        try:
            return JSONResponse(await _run_sync(_work))
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/submissions", methods=["POST"])
    async def api_submit_translation(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)

            def _work() -> tuple[int, dict[str, Any]]:
                ctx = _req_ctx(request, agent_key=body.get("agent_key"))
                return eff_service.submit_translation(
                    ctx,
                    book_slug=str(body.get("book_slug") or ""),
                    chapter_id=str(body.get("chapter_id") or ""),
                    translation_xml=str(body.get("translation_xml") or ""),
                    engine_model=str(body.get("engine_model") or "mcp-agent"),
                    additions=body.get("additions"),
                    submission_id=body.get("submission_id"),
                    revision_token=body.get("revision_token"),
                    rebuttals=body.get("rebuttals"),
                    hold_for_review=bool(body.get("hold_for_review", False)),
                    background=bool(body.get("background", False)),
                )

            code, out = await _run_sync(_work)
            return JSONResponse(out, status_code=code)
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/submissions/{submission_id}", methods=["GET"])
    async def api_get_submission(request: Request) -> Response:
        def _work() -> dict[str, Any]:
            ctx = _req_ctx(request)
            sub_id = str(request.path_params["submission_id"])
            inc_ctx = request.query_params.get("include_context", "true").lower() != "false"
            inc_ws = request.query_params.get("include_workstream", "").lower() == "true"
            return eff_service.get_submission_status(
                ctx,
                submission_id=sub_id,
                include_context=inc_ctx,
                include_workstream=inc_ws,
            )

        try:
            return JSONResponse(await _run_sync(_work))
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/submissions/{submission_id}/reviews", methods=["POST"])
    async def api_submit_review(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)

            def _work() -> dict[str, Any]:
                ctx = _req_ctx(request, agent_key=body.get("agent_key"))
                sub_id = str(request.path_params["submission_id"])
                return eff_service.submit_review(
                    ctx,
                    submission_id=sub_id,
                    body_md=str(body.get("body_md") or ""),
                    verdict=str(body.get("verdict") or "COMMENT"),
                    issues=body.get("issues"),
                )

            return JSONResponse(await _run_sync(_work), status_code=201)
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/admin/agents", methods=["GET"])
    @mcp.custom_route("/admin/api/agents", methods=["GET"])
    async def api_admin_list_agents(request: Request) -> Response:
        def _work() -> list[dict[str, Any]]:
            ctx = _req_ctx(request)
            return eff_service.list_agents(ctx)

        try:
            return JSONResponse({"agents": await _run_sync(_work)})
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/admin/agents/mint", methods=["POST"])
    @mcp.custom_route("/admin/api/agents/mint", methods=["POST"])
    async def api_admin_mint_agent(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)

            def _work() -> dict[str, Any]:
                ctx = _req_ctx(request, admin_token=body.get("admin_token"))
                return eff_service.admin_mint_agent(
                    ctx,
                    handle=str(body.get("handle") or ""),
                    display_name=str(body.get("display_name") or ""),
                    model_info=str(body.get("model_info") or ""),
                )

            return JSONResponse(await _run_sync(_work), status_code=201)
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/admin/agents/role", methods=["POST"])
    @mcp.custom_route("/admin/api/agents/role", methods=["POST"])
    async def api_admin_set_agent_role(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)

            def _work() -> dict[str, Any]:
                ctx = _req_ctx(request, admin_token=body.get("admin_token"))
                ident = str(body.get("identifier") or body.get("handle") or "")
                role = str(body.get("role") or "")
                return eff_service.set_agent_role(ctx, identifier=ident, role=role)

            return JSONResponse({"agent": await _run_sync(_work)})
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/admin/books/{slug}/access", methods=["POST"])
    @mcp.custom_route("/admin/api/books/{slug}/access", methods=["POST"])
    async def api_admin_set_book_access(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)
            raw_allowed = body.get("allowed_agents")
            if raw_allowed is not None and not isinstance(raw_allowed, list):
                raise ServiceError("allowed_agents must be a list of strings", status_code=400)

            def _work() -> dict[str, Any]:
                ctx = _req_ctx(request, admin_token=body.get("admin_token"))
                slug = str(request.path_params["slug"])
                return eff_service.set_book_access(
                    ctx,
                    book_slug=slug,
                    access_mode=str(body.get("access_mode") or ""),
                    allowed_agents=raw_allowed or [],
                    approval_mode=body.get("approval_mode"),
                )

            return JSONResponse({"book": await _run_sync(_work)})
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/admin/books/{slug}/approval-mode", methods=["POST"])
    @mcp.custom_route("/admin/api/books/{slug}/approval-mode", methods=["POST"])
    async def api_admin_set_book_approval_mode(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)

            def _work() -> dict[str, Any]:
                ctx = _req_ctx(request, admin_token=body.get("admin_token"))
                slug = str(request.path_params["slug"])
                return eff_service.set_book_approval_mode(
                    ctx,
                    book_slug=slug,
                    approval_mode=str(body.get("approval_mode") or ""),
                )

            return JSONResponse({"book": await _run_sync(_work)})
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/admin/books/{slug}/approve", methods=["POST"])
    @mcp.custom_route("/admin/books/{slug}/approve", methods=["POST"])
    async def api_admin_approve_book(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)

            def _work() -> dict[str, Any]:
                ctx = _req_ctx(request, admin_token=body.get("admin_token"))
                slug = str(request.path_params["slug"])
                return eff_service.approve_book_intake(
                    ctx,
                    book_slug=slug,
                    queue_for_parse=bool(body.get("queue_for_parse", False)),
                    auto_seed=bool(body.get("auto_seed", True)),
                    hold_for_review=bool(body.get("hold_for_review", False)),
                    background=bool(body.get("background", False)),
                )

            book = await _run_sync(_work)
            status = 202 if book.get("seeding_started") else 200
            return JSONResponse({"book": book}, status_code=status)
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/admin/books/{slug}/reject", methods=["POST"])
    @mcp.custom_route("/admin/books/{slug}/reject", methods=["POST"])
    async def api_admin_reject_book(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)

            def _work() -> dict[str, Any]:
                ctx = _req_ctx(request, admin_token=body.get("admin_token"))
                slug = str(request.path_params["slug"])
                reason = str(body.get("reason") or "")
                return eff_service.reject_book_proposal(ctx, book_slug=slug, reason=reason)

            return JSONResponse({"book": await _run_sync(_work)})
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/admin/submissions", methods=["GET"])
    @mcp.custom_route("/admin/api/submissions", methods=["GET"])
    async def api_admin_list_submissions(request: Request) -> Response:
        def _work() -> list[dict[str, Any]]:
            ctx = _req_ctx(request)
            slug = request.query_params.get("book_slug") or None
            status = request.query_params.get("status") or None
            raw_limit = request.query_params.get("limit")
            limit = int(raw_limit) if raw_limit and raw_limit.isdigit() else 50
            return eff_service.list_admin_submissions(
                ctx,
                book_slug=slug,
                status=status,
                limit=limit,
            )

        try:
            return JSONResponse({"submissions": await _run_sync(_work)})
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/admin/books/{slug}/seed", methods=["POST"])
    @mcp.custom_route("/admin/books/{slug}/seed", methods=["POST"])
    async def api_admin_seed_book(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)

            def _work() -> dict[str, Any]:
                ctx = _req_ctx(
                    request,
                    agent_key=body.get("agent_key"),
                    admin_token=body.get("admin_token"),
                )
                slug = str(request.path_params["slug"])
                return eff_service.seed_book(
                    ctx,
                    book_slug=slug,
                    hold_for_review=bool(body.get("hold_for_review", False)),
                    background=bool(body.get("background", False)),
                )

            book = await _run_sync(_work)
            status = 202 if book.get("seeding_started") else 200
            return JSONResponse({"book": book}, status_code=status)
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/admin/fallback/run", methods=["POST"])
    @mcp.custom_route("/admin/fallback/run", methods=["POST"])
    async def api_admin_fallback(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)

            def _work() -> dict[str, Any]:
                ctx = _req_ctx(request, admin_token=body.get("admin_token"))
                slug = body.get("book_slug")
                return eff_service.trigger_starved_fallback(ctx, book_slug=slug)

            return JSONResponse(await _run_sync(_work))
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/admin/submissions/{submission_id}/manage", methods=["POST"])
    @mcp.custom_route("/admin/submissions/{submission_id}/manage", methods=["POST"])
    async def api_admin_manage_submission(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)

            def _work() -> dict[str, Any]:
                ctx = _req_ctx(
                    request,
                    agent_key=body.get("agent_key"),
                    admin_token=body.get("admin_token"),
                )
                sub_id = str(request.path_params["submission_id"])
                action = str(body.get("action") or "")
                return eff_service.manage_flagged_submission(ctx, submission_id=sub_id, action=action)

            return JSONResponse({"submission": await _run_sync(_work)})
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/admin/books/{slug}/release", methods=["POST"])
    @mcp.custom_route("/admin/books/{slug}/release", methods=["POST"])
    async def api_admin_publish_release(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)

            def _work() -> dict[str, Any]:
                ctx = _req_ctx(request, admin_token=body.get("admin_token"))
                slug = str(request.path_params["slug"])
                return eff_service.publish_release(ctx, book_slug=slug)

            return JSONResponse(await _run_sync(_work))
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/bugs", methods=["POST"])
    async def api_submit_bug_report(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)

            def _work() -> tuple[int, dict[str, Any]]:
                ctx = _req_ctx(request, agent_key=body.get("agent_key"))
                return eff_service.submit_bug_report(
                    ctx,
                    title=str(body.get("title") or ""),
                    body_md=str(body.get("body_md") or ""),
                    severity=str(body.get("severity") or "P1"),
                    book_slug=str(body.get("book_slug") or ""),
                    chapter_id=str(body.get("chapter_id") or ""),
                    submission_id=str(body.get("submission_id") or ""),
                    tool_name=str(body.get("tool_name") or ""),
                    error_code=body.get("error_code", 0),
                )

            code, payload = await _run_sync(_work)
            return JSONResponse(payload, status_code=code)
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/bugs", methods=["GET"])
    async def api_query_bug_reports(request: Request) -> Response:
        def _work() -> list[dict[str, Any]]:
            ctx = _req_ctx(request)
            raw_limit = request.query_params.get("limit")
            limit = int(raw_limit) if raw_limit and raw_limit.isdigit() else 50
            inc_body = request.query_params.get("include_body", "true").lower() != "false"
            return eff_service.query_bug_reports(
                ctx,
                status=request.query_params.get("status") or None,
                book_slug=request.query_params.get("book_slug") or None,
                reporter_handle=request.query_params.get("reporter_handle") or None,
                include_body=inc_body,
                limit=limit,
            )

        try:
            return JSONResponse({"bug_reports": await _run_sync(_work)})
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/bugs/{bug_id}", methods=["GET"])
    async def api_get_bug_report(request: Request) -> Response:
        def _work() -> dict[str, Any]:
            ctx = _req_ctx(request)
            bug_id = str(request.path_params["bug_id"])
            return eff_service.get_bug_report(ctx, bug_id=bug_id)

        try:
            return JSONResponse({"bug_report": await _run_sync(_work)})
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/bugs/{bug_id}/triage", methods=["POST"])
    @mcp.custom_route("/admin/api/bugs/{bug_id}/triage", methods=["POST"])
    async def api_triage_bug_report(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)

            def _work() -> dict[str, Any]:
                ctx = _req_ctx(
                    request,
                    agent_key=body.get("agent_key"),
                    admin_token=body.get("admin_token"),
                )
                bug_id = str(request.path_params["bug_id"])
                return eff_service.triage_bug_report(
                    ctx,
                    bug_id=bug_id,
                    status=str(body.get("status") or ""),
                    resolution_note=str(body.get("resolution_note") or ""),
                    fixed_in_version=str(body.get("fixed_in_version") or ""),
                )

            return JSONResponse({"bug_report": await _run_sync(_work)})
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def api_not_found(_request: Request) -> Response:
        return JSONResponse({"error": "API endpoint not found"}, status_code=404)

    sec = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    starlette_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        max_request_body_size=10 * 1024 * 1024,
        transport_security=sec,
    )
    sse_sub = mcp.sse_app(
        sse_path="/sse",
        message_path="/messages/",
        max_request_body_size=10 * 1024 * 1024,
        transport_security=sec,
    )
    for route in sse_sub.routes:
        if getattr(route, "path", "") in {"/sse", "/messages"}:
            starlette_app.routes.insert(0, route)

    return RequestContextMiddleware(starlette_app)


def main() -> None:
    cfg = load_mcp_server_config()
    app = create_app(cfg=cfg)
    uvicorn.run(app, host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    main()
