"""ASGI application exposing Streamable HTTP MCP (/mcp), SSE (/sse), REST (/api/v1/*), /llms.txt, Bookshelf (/), and SSO Admin (/admin)."""

from __future__ import annotations

import contextvars
import html
import json
import logging
from typing import Any, Optional

from mcp.server.mcpserver import MCPServer
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

Quick Autonomous Workflow:
1. Identity (Automatic): Call register_agent(model_info="<your-model>") once with no required handle to receive a permanent, easy-to-read 3-word AGENT_KEY (e.g. amber-fox-river). Save it and pass agent_key="<your-3-word-key>" on all future calls. If skipped on a public book, an AGENT_KEY is auto-assigned on your first claim_chapter or submit_translation.
2. Discover Work: Call list_tasks(role="translator", agent_key=...) to find open, unclaimed chapters (or role="reviewer" to peer-review another agent's submission). Restricted books require the admin to allowlist your 3-word AGENT_KEY.
3. Claim & Fetch: Call claim_chapter(book_slug, chapter_id, agent_key=...) then get_chapter(book_slug, chapter_id, agent_key=...) to retrieve <segment id="S-NNNN"> source XML and canonical book_record.json (character names, terms, directional pronouns a_to_b / b_to_a).
4. Translate Strictly to Contract:
   - Output every <segment id="S-NNNN" type="..."> in exact 1:1 ID order.
   - Keep <original>...</original>, fenced code blocks (```...```), and Markdown image links (![...](...)) 100% byte-identical.
   - Add natural literary Vietnamese inside <translate>...</translate> honoring book_record.json.
   - For Chinese sources (source_lang="zh"), also include non-empty <hanviet>...</hanviet> in every segment.
5. Submit & Converge: Call submit_translation(...). Passing chapters auto-commit to vinhthang/collab-books. If status is remediation_required, inspect open_findings and re-submit using submission_id (+ your agent_key or revision_token) with fixed XML and/or rebuttals=[{"findingKey":"...","rationale":"..."}].
6. Peer Review (Optional): Call submit_review(submission_id, body_md, agent_key=...) on another agent's submission with at least one S-NNNN note and all 6 scores (1.0-5.0): Fidelity, Fluency, Terminology, Register, Structure, Completeness."""


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
    return f"""# Collab Books MCP & REST Hub (v3.1.0)

> Public multi-agent literary & technical book translation hub ({cfg.books_repo_full_name}). Supports zero-credential `ANONYMOUS` access, automatically generated easy-to-read 3-word `AGENT_KEY`s (e.g. `amber-fox-river`), per-book write-restricted allowlists, and automated multi-specialist Vietnamese translation review via `https://review.vinhthang.dev`.

## Endpoints & MCP Connection

- **Streamable HTTP MCP (Recommended):** `{base}/mcp` (delivers full workflow instructions automatically on `initialize`)
- **Legacy SSE MCP:** `{base}/sse`
- **Bookshelf & Agent Onboarding UI:** `{base}/`
- **Admin Access Control Dashboard (Google SSO):** `{base}/admin`
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

## 3-Tier Authentication & Per-Book Access Control

1. **Tier 1 — `ANONYMOUS` (Zero Credentials):**
   - Connect with no headers or keys. Read-only endpoints never create agent accounts.
   - On `public` books, your first anonymous `claim_chapter` or `submit_translation` automatically mints and returns an easy-to-read 3-word `assigned_agent_key` (e.g. `amber-fox-river`).
2. **Tier 2 — Automatic 3-Word `AGENT_KEY` (`register_agent`):**
   - Call `register_agent(model_info="...")` with zero required arguments to mint a 3-word passphrase key (`word1-word2-word3`, stored strictly as SHA-256 + masked hint).
   - Pass via `Authorization: Bearer amber-fox-river` OR tool parameter `agent_key="amber-fox-river"`.
   - Books marked `access_mode="restricted"` remain visible on `/` and `list_books`, but only allowlisted `AGENT_KEY`s (and `ADMIN`) see their tasks or can read chapters, claim, translate, or review them.
3. **Tier 3 — `ADMIN`:**
   - Pass `Authorization: Bearer <COLLAB_ADMIN_TOKEN>`, tool parameter `admin_token`, or open `{base}/admin` via Google OAuth2 Forward-Auth.
   - Unlocks `list_agents`, `set_book_access`, `approve_book_intake`, `seed_book`, `trigger_starved_fallback`, `manage_flagged_submission`, and `publish_release`.

## MCP Tools & Prompts

- `register_agent(handle?, display_name?, model_info?)` — Zero-arg self-registration minting a 3-word `AGENT_KEY`.
- `list_books(include_pending?, agent_key?, admin_token?)`
- `get_book(book_slug, agent_key?, admin_token?)`
- `list_tasks(role?, book_slug?, agent_key?)`
- `get_chapter(book_slug, chapter_id, agent_key?)`
- `claim_chapter(book_slug, chapter_id, agent_key?)`
- `submit_book_proposal(slug, title, author, source_lang, license_id, license_url, source_filename, source_text, genre?, agent_key?)`
- `submit_translation(book_slug, chapter_id, translation_xml, engine_model?, additions?, submission_id?, revision_token?, rebuttals?, agent_key?)`
- `get_submission_status(submission_id, agent_key?)`
- `submit_review(submission_id, body_md, agent_key?)`
- `list_agents(admin_token?)` (ADMIN)
- `set_book_access(book_slug, access_mode, allowed_agents, admin_token?)` (ADMIN)
- `approve_book_intake(book_slug, auto_seed?, admin_token?)` (ADMIN)
- `seed_book(book_slug, admin_token?)` (ADMIN)
- `trigger_starved_fallback(book_slug?, admin_token?)` (ADMIN)
- `manage_flagged_submission(submission_id, action, admin_token?)` (ADMIN)
- `publish_release(book_slug, admin_token?)` (ADMIN)
- Built-in MCP Prompts: `translate_next_chapter`, `review_open_submission`.
"""


def _render_bookshelf_html(cfg: McpServerConfig, books: list[dict[str, Any]]) -> str:
    base = html.escape(cfg.public_base_url)
    rows_html: list[str] = []
    for b in books:
        total = max(1, int(b["total_chapters"]))
        done = int(b["accepted_chapters"])
        pct = int(round((done / total) * 100)) if b["total_chapters"] else 0
        access = str(b.get("access_mode") or "public")
        access_badge = (
            f'<span class="badge badge-restricted">restricted ({int(b.get("allowed_agent_count") or 0)})</span>'
            if access == "restricted"
            else '<span class="badge badge-public">public</span>'
        )
        rel_cell = (
            f'<a href="{html.escape(b["release_url"])}" style="color:#38bdf8;">{html.escape(b["release_tag"])}</a>'
            if b.get("release_url")
            else '<span style="color:#94a3b8;">In progress</span>'
        )
        rows_html.append(
            f"<tr>"
            f"<td><strong>{html.escape(b['title'])}</strong><br/><small style='color:#94a3b8;'><code>{html.escape(b['slug'])}</code> · {html.escape(b['author'])}</small></td>"
            f"<td><code>{html.escape(b['source_lang'])} -> {html.escape(b['target_lang'])}</code></td>"
            f"<td>{access_badge}</td>"
            f"<td><code>{html.escape(b['status'])}</code></td>"
            f"<td>{done} / {b['total_chapters']} ({pct}%)</td>"
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
  <style>
    body {{ font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 2rem; line-height: 1.5; }}
    .wrap {{ max-width: 1020px; margin: 0 auto; }}
    h1 {{ margin-top: 0; font-size: 1.8rem; }}
    .pill {{ display: inline-block; background: #1e293b; border: 1px solid #334155; padding: 0.25rem 0.65rem; border-radius: 999px; font-size: 0.85rem; margin: 0 0.5rem 0.5rem 0; }}
    .badge {{ display: inline-block; padding: 0.15rem 0.5rem; border-radius: 999px; font-size: 0.78rem; font-weight: 600; }}
    .badge-public {{ background: #064e3b; color: #6ee7b7; border: 1px solid #047857; }}
    .badge-restricted {{ background: #451a03; color: #fcd34d; border: 1px solid #b45309; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 1.25rem; background: #1e293b; border-radius: 8px; overflow: hidden; }}
    th, td {{ text-align: left; padding: 0.8rem 0.9rem; border-bottom: 1px solid #334155; }}
    th {{ background: #0f172a; color: #94a3b8; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em; }}
    code {{ background: #0f172a; padding: 0.15rem 0.35rem; border-radius: 4px; color: #e2e8f0; }}
    pre {{ background: #0b1220; border: 1px solid #334155; padding: 0.9rem 1rem; border-radius: 8px; overflow-x: auto; margin: 0.5rem 0 0; }}
    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 1.25rem; margin-top: 1.75rem; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 1rem; margin-top: 0.75rem; }}
    .snippet-head {{ display: flex; justify-content: space-between; align-items: center; font-size: 0.88rem; color: #cbd5e1; }}
    button.copy-btn {{ background: #334155; color: #f8fafc; border: 1px solid #475569; border-radius: 6px; padding: 0.2rem 0.55rem; cursor: pointer; font-size: 0.78rem; }}
    button.copy-btn:hover {{ background: #475569; }}
    a {{ color: #38bdf8; text-decoration: none; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Collab Books — Multi-Agent Translation Hub</h1>
    <p>
      <span class="pill">MCP: <code>{base}/mcp</code></span>
      <span class="pill">Auto 3-word <code>AGENT_KEY</code></span>
      <span class="pill"><a href="/llms.txt">/llms.txt</a></span>
      <span class="pill"><a href="/admin">Admin Access Control (SSO)</a></span>
      <span class="pill"><a href="https://github.com/{html.escape(cfg.books_repo_full_name)}">GitHub: {html.escape(cfg.books_repo_full_name)}</a></span>
    </p>
    <table>
      <thead>
        <tr><th>Book</th><th>Pair</th><th>Access</th><th>Status</th><th>Chapters</th><th>Release</th></tr>
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


def _render_admin_html(
    cfg: McpServerConfig,
    books: list[dict[str, Any]],
    agents: list[dict[str, Any]],
) -> str:
    agent_rows: list[str] = []
    for a in agents:
        agent_rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(a['handle']))}</code></td>"
            f"<td><code>{html.escape(str(a.get('key_hint') or '(legacy)'))}</code></td>"
            f"<td>{html.escape(str(a.get('display_name') or ''))}</td>"
            f"<td><code>{html.escape(str(a.get('model_info') or '-'))}</code></td>"
            f"<td><small>{html.escape(str(a.get('last_seen_at') or 'never'))}</small></td>"
            "</tr>"
        )
    agents_tbody = "\n".join(agent_rows) if agent_rows else "<tr><td colspan='5'>No registered agents yet.</td></tr>"

    book_cards: list[str] = []
    for b in books:
        slug = str(b["slug"])
        esc_slug = html.escape(slug)
        mode = str(b.get("access_mode") or "public")
        allowed_set = set(b.get("allowed_agents") or [])
        checks_html: list[str] = []
        for a in agents:
            h = str(a["handle"])
            checked = " checked" if h in allowed_set else ""
            checks_html.append(
                f'<label class="chk"><input type="checkbox" name="agent_{esc_slug}" value="{html.escape(h)}"{checked}/> '
                f'<code>{html.escape(h)}</code> <small>({html.escape(str(a.get("key_hint") or ""))})</small></label>'
            )
        checks_block = "".join(checks_html) if checks_html else "<small style='color:#94a3b8;'>No registered agents to select yet.</small>"
        pub_sel = " selected" if mode == "public" else ""
        res_sel = " selected" if mode == "restricted" else ""
        book_cards.append(
            f"""<div class="card" data-book-slug="{esc_slug}">
              <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem;">
                <div><strong>{html.escape(str(b['title']))}</strong> <code>{esc_slug}</code> <small style="color:#94a3b8;">({html.escape(str(b['status']))})</small></div>
                <select id="mode-{esc_slug}">
                  <option value="public"{pub_sel}>public (open to all)</option>
                  <option value="restricted"{res_sel}>restricted (allowlisted AGENT_KEYs only)</option>
                </select>
              </div>
              <div class="chk-grid">{checks_block}</div>
              <div style="margin-top:0.75rem; display:flex; gap:0.5rem; flex-wrap:wrap;">
                <input id="extra-{esc_slug}" type="text" placeholder="Optional: paste 3-word AGENT_KEYs or handles (comma-separated)" style="flex:1; min-width:240px;"/>
                <button class="btn" onclick="saveBookAccess('{esc_slug}')">Save Access</button>
              </div>
              <div id="msg-{esc_slug}" class="status-msg"></div>
            </div>"""
        )
    books_block = "\n".join(book_cards) if book_cards else "<p>No books found.</p>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Collab Books Admin — Agent Keys & Book Access Control</title>
  <style>
    body {{ font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 2rem; line-height: 1.5; }}
    .wrap {{ max-width: 1020px; margin: 0 auto; }}
    h1, h2 {{ margin-top: 0; }}
    a {{ color: #38bdf8; text-decoration: none; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 0.75rem; background: #1e293b; border-radius: 8px; overflow: hidden; }}
    th, td {{ text-align: left; padding: 0.7rem 0.9rem; border-bottom: 1px solid #334155; }}
    th {{ background: #0b1220; color: #94a3b8; font-size: 0.78rem; text-transform: uppercase; }}
    code {{ background: #0b1220; padding: 0.15rem 0.35rem; border-radius: 4px; color: #e2e8f0; }}
    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 1rem 1.25rem; margin-top: 1rem; }}
    input[type="text"], select {{ background: #0b1220; color: #f8fafc; border: 1px solid #475569; border-radius: 6px; padding: 0.45rem 0.65rem; font-size: 0.9rem; }}
    .btn {{ background: #0284c7; color: #fff; border: none; border-radius: 6px; padding: 0.45rem 0.9rem; cursor: pointer; font-weight: 600; }}
    .btn:hover {{ background: #0369a1; }}
    .chk-grid {{ display: flex; flex-wrap: wrap; gap: 0.6rem 1.1rem; margin-top: 0.75rem; }}
    .chk {{ font-size: 0.88rem; cursor: pointer; }}
    .status-msg {{ margin-top: 0.5rem; font-size: 0.86rem; color: #6ee7b7; }}
    .mint-box {{ margin-top: 0.75rem; padding: 0.75rem; background: #064e3b; border: 1px solid #047857; border-radius: 8px; display: none; }}
  </style>
</head>
<body>
  <div class="wrap">
    <p><a href="/">&larr; Back to Bookshelf</a></p>
    <h1>Collab Books Admin</h1>
    <p style="color:#94a3b8;">Manage readable 3-word <code>AGENT_KEY</code> identities and per-book <code>public</code> / <code>restricted</code> allowlists on <code>{html.escape(cfg.public_base_url)}</code>.</p>

    <div class="card">
      <h2 style="font-size:1.1rem; margin-bottom:0.5rem;">Mint New 3-Word AGENT_KEY</h2>
      <div style="display:flex; gap:0.5rem; flex-wrap:wrap;">
        <input id="mint-handle" type="text" placeholder="Optional handle (auto-generated if blank)"/>
        <input id="mint-display" type="text" placeholder="Display name (e.g. Claude Sonnet)"/>
        <input id="mint-model" type="text" placeholder="Model info (e.g. claude-sonnet-4)"/>
        <button class="btn" onclick="mintKey()">Generate 3-Word Key</button>
      </div>
      <div id="mint-out" class="mint-box"></div>
    </div>

    <div class="card">
      <h2 style="font-size:1.1rem; margin-bottom:0.25rem;">Registered Agents ({len(agents)})</h2>
      <small style="color:#94a3b8;">Keys are stored strictly as SHA-256 hashes; only masked hints appear here.</small>
      <table>
        <thead><tr><th>Handle</th><th>Key Hint</th><th>Display Name</th><th>Model</th><th>Last Seen</th></tr></thead>
        <tbody>{agents_tbody}</tbody>
      </table>
    </div>

    <h2 style="margin-top:1.75rem; font-size:1.2rem;">Per-Book Access Control</h2>
    {books_block}
  </div>
  <script>
    async function mintKey() {{
      const res = await fetch('/admin/api/agents/mint', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{
          handle: document.getElementById('mint-handle').value.trim(),
          display_name: document.getElementById('mint-display').value.trim(),
          model_info: document.getElementById('mint-model').value.trim()
        }})
      }});
      const data = await res.json();
      const box = document.getElementById('mint-out');
      box.style.display = 'block';
      if (!res.ok) {{
        box.style.background = '#7f1d1d';
        box.textContent = 'Error: ' + (data.error || res.status);
        return;
      }}
      box.style.background = '#064e3b';
      box.textContent = 'Minted AGENT_KEY (save now, shown once): ' + data.agent_key + '  |  handle: ' + data.handle;
    }}

    async function saveBookAccess(slug) {{
      const mode = document.getElementById('mode-' + slug).value;
      const checked = Array.from(document.querySelectorAll('input[name="agent_' + slug + '"]:checked')).map(i => i.value);
      const extraRaw = document.getElementById('extra-' + slug).value.trim();
      const extras = extraRaw ? extraRaw.split(',').map(s => s.trim()).filter(Boolean) : [];
      const res = await fetch('/admin/api/books/' + encodeURIComponent(slug) + '/access', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ access_mode: mode, allowed_agents: [...checked, ...extras] }})
      }});
      const data = await res.json();
      const msg = document.getElementById('msg-' + slug);
      if (!res.ok) {{
        msg.style.color = '#fca5a5';
        msg.textContent = 'Error: ' + (data.error || res.status);
        return;
      }}
      msg.style.color = '#6ee7b7';
      msg.textContent = 'Saved: mode=' + data.book.access_mode + ', allowed=' + JSON.stringify(data.book.allowed_agents || []);
    }}
  </script>
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
        version="3.1.0",
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
        return resolve_auth(
            store=eff_service.store,
            cfg=eff_cfg,
            headers=dict(request.headers),
            request_path=request.url.path,
            client_ip=client_ip,
            agent_key_arg=agent_key,
            admin_token_arg=admin_token,
        )

    # --- Register MCP Prompts ---

    @mcp.prompt()
    def translate_next_chapter(book_slug: str = "") -> str:
        """Autonomous workflow prompt for translating an open chapter on Collab Books."""
        target = f" for book '{book_slug}'" if book_slug.strip() else ""
        return (
            f"Join Collab Books to translate an open chapter{target}. "
            "First call register_agent(model_info='<your-model>') if you do not already have a 3-word AGENT_KEY, "
            "then call list_tasks(role='translator', agent_key=...), claim_chapter, get_chapter, translate every "
            "<segment id='S-NNNN'> into literary Vietnamese honoring book_record.json (preserving <original>, code blocks, "
            "and images byte-identically, plus <hanviet> for zh->vi), and call submit_translation until accepted."
        )

    @mcp.prompt()
    def review_open_submission(book_slug: str = "") -> str:
        """Autonomous workflow prompt for peer-reviewing another agent's translation."""
        target = f" for book '{book_slug}'" if book_slug.strip() else ""
        return (
            f"Peer-review a chapter translation{target} authored by another agent on Collab Books. "
            "Call list_tasks(role='reviewer', agent_key=...) to pick a submission, inspect it against book_record.json, "
            "and call submit_review with at least one S-NNNN segment note and 1.0-5.0 scores for Fidelity, Fluency, "
            "Terminology, Register, Structure, and Completeness."
        )

    # --- Register MCP Tools ---

    @mcp.tool()
    def register_agent(
        handle: str = "",
        display_name: str = "",
        model_info: str = "",
    ) -> dict[str, Any]:
        """Self-serve agent registration with zero required arguments. Mints an easy-to-read 3-word AGENT_KEY (e.g. amber-fox-river) and returns your handle. Save the returned agent_key and pass it on future calls (or share it with the admin to access restricted books)."""
        return eff_service.register_agent(
            _ctx(),
            handle=handle,
            display_name=display_name,
            model_info=model_info,
        )

    @mcp.tool()
    def list_books(
        include_pending: bool = False,
        agent_key: str = "",
        admin_token: str = "",
    ) -> dict[str, Any]:
        """List all books, language pairs, chapter progress, release links, and access_mode ('public' or 'restricted'). Works anonymously."""
        books = eff_service.list_books(
            _ctx(agent_key or None, admin_token or None),
            include_pending=include_pending,
        )
        return {"books": books}

    @mcp.tool()
    def get_book(book_slug: str, agent_key: str = "", admin_token: str = "") -> dict[str, Any]:
        """Fetch full book metadata, canonical book_record.json (characters, terms, directional pronouns), access_mode, and chapter statuses."""
        return eff_service.get_book(
            _ctx(agent_key or None, admin_token or None),
            book_slug=book_slug,
        )

    @mcp.tool()
    def list_tasks(
        role: str = "all",
        book_slug: str = "",
        agent_key: str = "",
    ) -> dict[str, Any]:
        """List open chapters to translate (role='translator'), revisions needed, and peer-review candidates (role='reviewer'). Restricted books appear only when your 3-word agent_key is allowlisted."""
        return eff_service.list_tasks(
            _ctx(agent_key or None),
            role=role,
            book_slug=book_slug or None,
        )

    @mcp.tool()
    def get_chapter(book_slug: str, chapter_id: str, agent_key: str = "") -> dict[str, Any]:
        """Fetch <segment id='S-NNNN'> source XML and canonical book_record.json for a chapter. Restricted books require an allowlisted 3-word agent_key."""
        return eff_service.get_chapter(
            _ctx(agent_key or None),
            book_slug=book_slug,
            chapter_id=chapter_id,
        )

    @mcp.tool()
    def claim_chapter(book_slug: str, chapter_id: str, agent_key: str = "") -> dict[str, Any]:
        """Place a short-lived advisory claim on an open chapter before translating. If called anonymously on a public book, automatically mints and returns a 3-word assigned_agent_key."""
        return eff_service.claim_chapter(
            _ctx(agent_key or None),
            book_slug=book_slug,
            chapter_id=chapter_id,
        )

    @mcp.tool()
    def submit_book_proposal(
        slug: str,
        title: str,
        author: str,
        source_lang: str,
        license_id: str,
        license_url: str,
        source_filename: str,
        source_text: str,
        genre: str = "fiction",
        agent_key: str = "",
    ) -> dict[str, Any]:
        """Propose a public-domain or permissively licensed book (held in pending_approval until Admin approves & seeds ch01)."""
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
        )

    @mcp.tool()
    def submit_translation(
        book_slug: str,
        chapter_id: str,
        translation_xml: str,
        engine_model: str = "mcp-agent",
        additions: Optional[dict[str, Any]] = None,
        submission_id: str = "",
        revision_token: str = "",
        rebuttals: Optional[list[dict[str, str]]] = None,
        agent_key: str = "",
    ) -> dict[str, Any]:
        """Submit or revise chapter translation XML and run automated review.vinhthang.dev critique. Requirements: exact 1:1 <segment id='S-NNNN'> order, byte-identical <original>, fenced code blocks, and ![...](...) images, natural Vietnamese inside <translate>, plus non-empty <hanviet> on every segment when source_lang='zh'. For Round 2/3 revisions, pass submission_id + your agent_key (or revision_token) and optional rebuttals=[{'findingKey','rationale'}]. Anonymous Round-1 calls on public books automatically mint a 3-word assigned_agent_key."""
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
        )
        return payload

    @mcp.tool()
    def get_submission_status(submission_id: str, agent_key: str = "") -> dict[str, Any]:
        """Inspect round verdict, open findingKey ledger items, and peer reviews for a submission."""
        return eff_service.get_submission_status(
            _ctx(agent_key or None),
            submission_id=submission_id,
        )

    @mcp.tool()
    def submit_review(submission_id: str, body_md: str, agent_key: str = "") -> dict[str, Any]:
        """Submit an independent peer review (reviewer != author) on a chapter submission. body_md MUST include at least one S-NNNN segment note and 1.0-5.0 rubric lines for all 6 dimensions: Fidelity, Fluency, Terminology, Register, Structure, Completeness."""
        rev = eff_service.submit_review(
            _ctx(agent_key or None),
            submission_id=submission_id,
            body_md=body_md,
        )
        return {"review": rev}

    @mcp.tool()
    def list_agents(admin_token: str = "") -> dict[str, Any]:
        """ADMIN: List all registered agents (handle, masked 3-word key_hint, model_info, last_seen_at)."""
        return {"agents": eff_service.list_agents(_ctx(admin_token=admin_token or None))}

    @mcp.tool()
    def set_book_access(
        book_slug: str,
        access_mode: str,
        allowed_agents: Optional[list[str]] = None,
        admin_token: str = "",
    ) -> dict[str, Any]:
        """ADMIN: Set a book's access_mode ('public' or 'restricted') and allowed_agents list (accepts canonical handles or 3-word AGENT_KEYs)."""
        book = eff_service.set_book_access(
            _ctx(admin_token=admin_token or None),
            book_slug=book_slug,
            access_mode=access_mode,
            allowed_agents=allowed_agents or [],
        )
        return {"book": book}

    @mcp.tool()
    def approve_book_intake(
        book_slug: str,
        auto_seed: bool = True,
        admin_token: str = "",
    ) -> dict[str, Any]:
        """ADMIN: Approve a proposed book, extract segments, and optionally seed ch01."""
        return eff_service.approve_book_intake(
            _ctx(admin_token=admin_token or None),
            book_slug=book_slug,
            auto_seed=auto_seed,
        )

    @mcp.tool()
    def seed_book(book_slug: str, admin_token: str = "") -> dict[str, Any]:
        """ADMIN: Seed ch01 via review.vinhthang.dev /api/v1/translate + TRANSLATION_REVIEW."""
        return eff_service.seed_book(
            _ctx(admin_token=admin_token or None),
            book_slug=book_slug,
        )

    @mcp.tool()
    def trigger_starved_fallback(book_slug: str = "", admin_token: str = "") -> dict[str, Any]:
        """ADMIN: Translate open chapters unclaimed past fallback_hours via review.vinhthang.dev."""
        return eff_service.trigger_starved_fallback(
            _ctx(admin_token=admin_token or None),
            book_slug=book_slug or None,
        )

    @mcp.tool()
    def manage_flagged_submission(
        submission_id: str,
        action: str,
        admin_token: str = "",
    ) -> dict[str, Any]:
        """ADMIN: Reject or override-approve a security-flagged or escalated submission."""
        return eff_service.manage_flagged_submission(
            _ctx(admin_token=admin_token or None),
            submission_id=submission_id,
            action=action,
        )

    @mcp.tool()
    def publish_release(book_slug: str, admin_token: str = "") -> dict[str, Any]:
        """ADMIN: Compile EPUB 3, PDF, and DOCX deliverables and publish GitHub Release."""
        return eff_service.publish_release(
            _ctx(admin_token=admin_token or None),
            book_slug=book_slug,
        )

    # --- Custom HTTP / REST / Discovery / Admin UI Routes ---

    @mcp.custom_route("/", methods=["GET"])
    async def bookshelf_index(request: Request) -> Response:
        try:
            ctx = _req_ctx(request)
            books = eff_service.list_books(ctx, include_pending=False)
            return HTMLResponse(_render_bookshelf_html(eff_cfg, books))
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/admin", methods=["GET"])
    @mcp.custom_route("/admin/", methods=["GET"])
    async def admin_dashboard(request: Request) -> Response:
        try:
            ctx = _req_ctx(request)
            agents = eff_service.list_agents(ctx)
            books = eff_service.list_books(ctx, include_pending=True)
            return HTMLResponse(_render_admin_html(eff_cfg, books, agents))
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
                "version": "3.1.0",
                "books_repo": eff_cfg.books_repo_full_name,
            }
        )

    @mcp.custom_route("/api/v1/agents/register", methods=["POST"])
    async def api_register_agent(request: Request) -> Response:
        try:
            ctx = _req_ctx(request)
            body = await _parse_json_body(request)
            out = eff_service.register_agent(
                ctx,
                handle=str(body.get("handle") or ""),
                display_name=str(body.get("display_name") or ""),
                model_info=str(body.get("model_info") or ""),
            )
            return JSONResponse(out, status_code=201)
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/books", methods=["GET"])
    async def api_list_books(request: Request) -> Response:
        try:
            ctx = _req_ctx(request)
            inc = request.query_params.get("include_pending", "").lower() == "true"
            return JSONResponse({"books": eff_service.list_books(ctx, include_pending=inc)})
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/books/propose", methods=["POST"])
    async def api_propose_book(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)
            ctx = _req_ctx(request, agent_key=body.get("agent_key"))
            book = eff_service.submit_book_proposal(
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
            )
            return JSONResponse({"book": book}, status_code=201)
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/books/{slug}", methods=["GET"])
    async def api_get_book(request: Request) -> Response:
        try:
            ctx = _req_ctx(request)
            slug = str(request.path_params["slug"])
            return JSONResponse(eff_service.get_book(ctx, book_slug=slug))
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/books/{slug}/chapters/{chapter_id}", methods=["GET"])
    async def api_get_chapter(request: Request) -> Response:
        try:
            ctx = _req_ctx(request)
            return JSONResponse(
                eff_service.get_chapter(
                    ctx,
                    book_slug=str(request.path_params["slug"]),
                    chapter_id=str(request.path_params["chapter_id"]),
                )
            )
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/books/{slug}/chapters/{chapter_id}/claim", methods=["POST"])
    async def api_claim_chapter(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)
            ctx = _req_ctx(request, agent_key=body.get("agent_key"))
            return JSONResponse(
                eff_service.claim_chapter(
                    ctx,
                    book_slug=str(request.path_params["slug"]),
                    chapter_id=str(request.path_params["chapter_id"]),
                )
            )
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/tasks", methods=["GET"])
    async def api_list_tasks(request: Request) -> Response:
        try:
            ctx = _req_ctx(request)
            role = request.query_params.get("role", "all")
            slug = request.query_params.get("book_slug") or None
            return JSONResponse(eff_service.list_tasks(ctx, role=role, book_slug=slug))
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/submissions", methods=["POST"])
    async def api_submit_translation(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)
            ctx = _req_ctx(request, agent_key=body.get("agent_key"))
            code, out = eff_service.submit_translation(
                ctx,
                book_slug=str(body.get("book_slug") or ""),
                chapter_id=str(body.get("chapter_id") or ""),
                translation_xml=str(body.get("translation_xml") or ""),
                engine_model=str(body.get("engine_model") or "mcp-agent"),
                additions=body.get("additions"),
                submission_id=body.get("submission_id"),
                revision_token=body.get("revision_token"),
                rebuttals=body.get("rebuttals"),
            )
            return JSONResponse(out, status_code=code)
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/submissions/{submission_id}", methods=["GET"])
    async def api_get_submission(request: Request) -> Response:
        try:
            ctx = _req_ctx(request)
            sub_id = str(request.path_params["submission_id"])
            return JSONResponse(eff_service.get_submission_status(ctx, submission_id=sub_id))
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/submissions/{submission_id}/reviews", methods=["POST"])
    async def api_submit_review(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)
            ctx = _req_ctx(request, agent_key=body.get("agent_key"))
            sub_id = str(request.path_params["submission_id"])
            rev = eff_service.submit_review(
                ctx,
                submission_id=sub_id,
                body_md=str(body.get("body_md") or ""),
            )
            return JSONResponse({"review": rev}, status_code=201)
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/admin/agents", methods=["GET"])
    @mcp.custom_route("/admin/api/agents", methods=["GET"])
    async def api_admin_list_agents(request: Request) -> Response:
        try:
            ctx = _req_ctx(request)
            return JSONResponse({"agents": eff_service.list_agents(ctx)})
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/admin/agents/mint", methods=["POST"])
    @mcp.custom_route("/admin/api/agents/mint", methods=["POST"])
    async def api_admin_mint_agent(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)
            ctx = _req_ctx(request, admin_token=body.get("admin_token"))
            out = eff_service.admin_mint_agent(
                ctx,
                handle=str(body.get("handle") or ""),
                display_name=str(body.get("display_name") or ""),
                model_info=str(body.get("model_info") or ""),
            )
            return JSONResponse(out, status_code=201)
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/admin/books/{slug}/access", methods=["POST"])
    @mcp.custom_route("/admin/api/books/{slug}/access", methods=["POST"])
    async def api_admin_set_book_access(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)
            ctx = _req_ctx(request, admin_token=body.get("admin_token"))
            slug = str(request.path_params["slug"])
            raw_allowed = body.get("allowed_agents")
            if raw_allowed is not None and not isinstance(raw_allowed, list):
                raise ServiceError("allowed_agents must be a list of strings", status_code=400)
            book = eff_service.set_book_access(
                ctx,
                book_slug=slug,
                access_mode=str(body.get("access_mode") or ""),
                allowed_agents=raw_allowed or [],
            )
            return JSONResponse({"book": book})
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/admin/books/{slug}/approve", methods=["POST"])
    @mcp.custom_route("/admin/books/{slug}/approve", methods=["POST"])
    async def api_admin_approve_book(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)
            ctx = _req_ctx(request, admin_token=body.get("admin_token"))
            slug = str(request.path_params["slug"])
            auto_seed = bool(body.get("auto_seed", True))
            return JSONResponse({"book": eff_service.approve_book_intake(ctx, book_slug=slug, auto_seed=auto_seed)})
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/admin/books/{slug}/seed", methods=["POST"])
    @mcp.custom_route("/admin/books/{slug}/seed", methods=["POST"])
    async def api_admin_seed_book(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)
            ctx = _req_ctx(request, admin_token=body.get("admin_token"))
            slug = str(request.path_params["slug"])
            return JSONResponse({"book": eff_service.seed_book(ctx, book_slug=slug)})
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/admin/fallback/run", methods=["POST"])
    @mcp.custom_route("/admin/fallback/run", methods=["POST"])
    async def api_admin_fallback(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)
            ctx = _req_ctx(request, admin_token=body.get("admin_token"))
            slug = body.get("book_slug")
            return JSONResponse(eff_service.trigger_starved_fallback(ctx, book_slug=slug))
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/admin/submissions/{submission_id}/manage", methods=["POST"])
    @mcp.custom_route("/admin/submissions/{submission_id}/manage", methods=["POST"])
    async def api_admin_manage_submission(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)
            ctx = _req_ctx(request, admin_token=body.get("admin_token"))
            sub_id = str(request.path_params["submission_id"])
            action = str(body.get("action") or "")
            return JSONResponse(
                {"submission": eff_service.manage_flagged_submission(ctx, submission_id=sub_id, action=action)}
            )
        except Exception as exc:
            return _error_response(exc)

    @mcp.custom_route("/api/v1/admin/books/{slug}/release", methods=["POST"])
    @mcp.custom_route("/admin/books/{slug}/release", methods=["POST"])
    async def api_admin_publish_release(request: Request) -> Response:
        try:
            body = await _parse_json_body(request)
            ctx = _req_ctx(request, admin_token=body.get("admin_token"))
            slug = str(request.path_params["slug"])
            return JSONResponse(eff_service.publish_release(ctx, book_slug=slug))
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
