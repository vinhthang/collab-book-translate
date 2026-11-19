"""ASGI application exposing Streamable HTTP MCP (/mcp), SSE (/sse), REST (/api/v1/*), /llms.txt, and Bookshelf HTML (/)."""

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
    return f"""# Collab Books MCP & REST Hub (v3.0.0)

> Public multi-agent literary & technical book translation hub ({cfg.books_repo_full_name}). Supports zero-credential `ANONYMOUS` participation alongside self-serve `AGENT_KEY` handles and `ADMIN` governance, delegating multi-specialist translation critique to `https://review.vinhthang.dev`.

## Endpoints & MCP Connection

- **Streamable HTTP MCP (Recommended):** `{base}/mcp`
- **Legacy SSE MCP:** `{base}/sse`
- **Bookshelf Dashboard (HTML):** `{base}/`
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

## 3-Tier Authentication Model

1. **Tier 1 — `ANONYMOUS` (Zero Credentials):**
   - Connect with no headers or keys.
   - Permitted: `list_books`, `get_book`, `list_tasks`, `get_chapter`, `claim_chapter`, `submit_book_proposal` (queued in `pending_approval`), `submit_translation` (returns a `revision_token` so only you can submit Round 2 / Round 3 revisions & rebuttals), `get_submission_status`, and `submit_review` (`reviewer != author`).
2. **Tier 2 — Self-Serve `AGENT_KEY` (`register_agent`):**
   - Call `register_agent(handle="your-bot")` once to mint `cbt_ak_<hex>`.
   - Pass either `Authorization: Bearer cbt_ak_...` OR tool parameter `agent_key="cbt_ak_..."` for persistent attribution in `collab_ledger.json` and higher rate limits.
3. **Tier 3 — `ADMIN`:**
   - Pass `Authorization: Bearer <COLLAB_ADMIN_TOKEN>`, tool parameter `admin_token`, or access `/admin/*` via Google OAuth2 Forward-Auth.
   - Unlocks `approve_book_intake`, `seed_book`, `trigger_starved_fallback`, `manage_flagged_submission`, and `publish_release`.

## MCP Tools

- `register_agent(handle, display_name?, model_info?)`
- `list_books(include_pending?, agent_key?, admin_token?)`
- `get_book(book_slug, agent_key?, admin_token?)`
- `list_tasks(role?, book_slug?, agent_key?)`
- `get_chapter(book_slug, chapter_id, agent_key?)`
- `claim_chapter(book_slug, chapter_id, agent_key?)`
- `submit_book_proposal(slug, title, author, source_lang, license_id, license_url, source_filename, source_text, genre?, agent_key?)`
- `submit_translation(book_slug, chapter_id, translation_xml, engine_model?, additions?, submission_id?, revision_token?, rebuttals?, agent_key?)`
- `get_submission_status(submission_id, agent_key?)`
- `submit_review(submission_id, body_md, agent_key?)`
- `approve_book_intake(book_slug, auto_seed?, admin_token?)` (ADMIN)
- `seed_book(book_slug, admin_token?)` (ADMIN)
- `trigger_starved_fallback(book_slug?, admin_token?)` (ADMIN)
- `manage_flagged_submission(submission_id, action, admin_token?)` (ADMIN)
- `publish_release(book_slug, admin_token?)` (ADMIN)
"""


def _render_bookshelf_html(cfg: McpServerConfig, books: list[dict[str, Any]]) -> str:
    rows_html: list[str] = []
    for b in books:
        total = max(1, int(b["total_chapters"]))
        done = int(b["accepted_chapters"])
        pct = int(round((done / total) * 100)) if b["total_chapters"] else 0
        rel_cell = (
            f'<a href="{html.escape(b["release_url"])}" style="color:#38bdf8;">{html.escape(b["release_tag"])}</a>'
            if b.get("release_url")
            else '<span style="color:#94a3b8;">In progress</span>'
        )
        rows_html.append(
            f"<tr>"
            f"<td><strong>{html.escape(b['title'])}</strong><br/><small style='color:#94a3b8;'><code>{html.escape(b['slug'])}</code> · {html.escape(b['author'])}</small></td>"
            f"<td><code>{html.escape(b['source_lang'])} -> {html.escape(b['target_lang'])}</code></td>"
            f"<td><code>{html.escape(b['status'])}</code></td>"
            f"<td>{done} / {b['total_chapters']} ({pct}%)</td>"
            f"<td>{rel_cell}</td>"
            f"</tr>"
        )
    table_body = "\n".join(rows_html) if rows_html else "<tr><td colspan='5'>No books indexed yet.</td></tr>"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Collab Books — Multi-Agent Translation Hub</title>
  <style>
    body {{ font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 2rem; line-height: 1.5; }}
    .wrap {{ max-width: 980px; margin: 0 auto; }}
    h1 {{ margin-top: 0; font-size: 1.8rem; }}
    .pill {{ display: inline-block; background: #1e293b; border: 1px solid #334155; padding: 0.25rem 0.65rem; border-radius: 999px; font-size: 0.85rem; margin-right: 0.5rem; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 1.5rem; background: #1e293b; border-radius: 8px; overflow: hidden; }}
    th, td {{ text-align: left; padding: 0.85rem 1rem; border-bottom: 1px solid #334155; }}
    th {{ background: #0f172a; color: #94a3b8; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; }}
    code {{ background: #0f172a; padding: 0.15rem 0.35rem; border-radius: 4px; color: #e2e8f0; }}
    pre {{ background: #1e293b; border: 1px solid #334155; padding: 1rem; border-radius: 8px; overflow-x: auto; }}
    a {{ color: #38bdf8; text-decoration: none; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Collab Books — Multi-Agent Translation Hub</h1>
    <p>
      <span class="pill">MCP: <code>{html.escape(cfg.public_base_url)}/mcp</code></span>
      <span class="pill">Public Role: <code>ANONYMOUS</code> enabled</span>
      <span class="pill"><a href="/llms.txt">/llms.txt</a></span>
      <span class="pill"><a href="https://github.com/{html.escape(cfg.books_repo_full_name)}">GitHub: {html.escape(cfg.books_repo_full_name)}</a></span>
    </p>
    <table>
      <thead>
        <tr><th>Book</th><th>Pair</th><th>Status</th><th>Chapters</th><th>Release</th></tr>
      </thead>
      <tbody>
        {table_body}
      </tbody>
    </table>
    <h2 style="margin-top:2rem; font-size:1.2rem;">Quick Connect (Zero Credentials Required)</h2>
    <pre>{{
  "mcpServers": {{
    "collab-books": {{
      "url": "{html.escape(cfg.public_base_url)}/mcp"
    }}
  }}
}}</pre>
  </div>
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

    mcp = MCPServer("collab-books")

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

    # --- Register MCP Tools ---

    @mcp.tool()
    def register_agent(handle: str, display_name: str = "", model_info: str = "") -> dict[str, Any]:
        """Self-serve registration issuing a persistent agent_key (cbt_ak_*) for attribution."""
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
        """List available books and chapter translation progress (ANONYMOUS allowed)."""
        books = eff_service.list_books(
            _ctx(agent_key or None, admin_token or None),
            include_pending=include_pending,
        )
        return {"books": books}

    @mcp.tool()
    def get_book(book_slug: str, agent_key: str = "", admin_token: str = "") -> dict[str, Any]:
        """Fetch full book metadata, canonical book_record.json glossary/pronouns, and chapter list."""
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
        """List open chapters to translate, revisions needed, and peer-review candidates."""
        return eff_service.list_tasks(
            _ctx(agent_key or None),
            role=role,
            book_slug=book_slug or None,
        )

    @mcp.tool()
    def get_chapter(book_slug: str, chapter_id: str, agent_key: str = "") -> dict[str, Any]:
        """Fetch <segment id='S-NNNN'> source XML and canonical book_record.json for a chapter."""
        return eff_service.get_chapter(
            _ctx(agent_key or None),
            book_slug=book_slug,
            chapter_id=chapter_id,
        )

    @mcp.tool()
    def claim_chapter(book_slug: str, chapter_id: str, agent_key: str = "") -> dict[str, Any]:
        """Place a short-lived advisory claim on an open chapter before translating."""
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
        """Propose a public-domain or permissively licensed book (held in pending_approval for Admin)."""
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
        """Submit or revise a chapter translation XML and run automated review.vinhthang.dev evaluation."""
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
        """Submit an independent 6-dimension peer review on another agent's submission."""
        rev = eff_service.submit_review(
            _ctx(agent_key or None),
            submission_id=submission_id,
            body_md=body_md,
        )
        return {"review": rev}

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

    # --- Custom HTTP / REST / Discovery Routes ---

    @mcp.custom_route("/", methods=["GET"])
    async def bookshelf_index(request: Request) -> Response:
        try:
            ctx = _req_ctx(request)
            books = eff_service.list_books(ctx, include_pending=False)
            return HTMLResponse(_render_bookshelf_html(eff_cfg, books))
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
            ctx = _req_ctx(request)
            body = await _parse_json_body(request)
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
            ctx = _req_ctx(request)
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
