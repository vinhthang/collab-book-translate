# Design: Collab Books MCP Security, Scalability, & CI Hardening (`v4.8.0`)

Governing specification: [spec.md](./spec.md)

## Components

- `scripts/mcp_server/keys.py` (**NEW**):
  - Houses `sha256_hex`, 256-word `_ADJECTIVES` / `_NOUNS` / `_PLACES`, `mint_agent_key` (`hint = f"{w1}-***"`), `generate_readable_handle`, and `generate_default_handle`, breaking the `db <-> auth` import cycle (`REQ-083`, `REQ-086`).
- `scripts/mcp_server/config.py`:
  - Adds `trust_proxy_headers`, `trusted_proxy_ips`, `auth_fail_rpm`, and `agent_heartbeat_rpm` configuration knobs (`REQ-082`, `REQ-083`).
- `scripts/mcp_server/auth.py`:
  - Re-exports `keys.py` helpers; enforces fail-closed Forward-Auth SSO (`bool(cfg.admin_emails) and sso_email in cfg.admin_emails`), trusted-proxy-gated `_extract_client_ip`, bounded `SlidingWindowRateLimiter` bucket eviction, `rate_limiter`-backed `auth_fail:<ip_hash>` throttling, and `pre_authenticated_agent` reuse (`REQ-082`, `REQ-083`, `REQ-084`).
- `scripts/mcp_server/app.py`:
  - Enforces streaming `MAX_HTTP_BODY_BYTES = 10 MiB` (`413`) in `RequestContextMiddleware` and `_parse_json_body`, JSON media type (`415`) on non-empty bodies, `/admin*` CSRF validation (`Sec-Fetch-Site` / `Origin`, `403`), worker-thread offloading of middleware presence DB calls via `anyio.to_thread.run_sync`, `pre_authenticated_agent` propagation, and `4.8.0` invite-only discovery/prompt copy (`REQ-082`, `REQ-084`, `REQ-086`).
- `scripts/mcp_server/db.py` & `service.py` & `git_sync.py`:
  - Persistent `RLock`-guarded PostgreSQL connection (`_get_pg`), atomic `transaction()` context manager across SQLite and PostgreSQL, 6 secondary indexes, projected `list_books_summary` + aggregate SQL counts (`<= 4` queries), omission of duplicate `translation_xml` in `rounds_history`, git-before-DB acceptance, checked GitHub release asset replacement, and `agent_heartbeat_rpm` quota (`REQ-083`, `REQ-084`).
- `scripts/mcp_server/admin_ui.py`, `scripts/extractors/__init__.py`, `scripts/extract.py`, `scripts/utils/pr_body.py` (**NEW**), `scripts/collab_worker.py`, `scripts/collab_seed.py`, `scripts/collab_sweep.py`, `scripts/compile_pdf.py`, `scripts/compilers/epub_compiler.py`, `.github/workflows/ci-cd.yml`, `Dockerfile`, docs:
  - Fixes `.js-retry-seed` (`hold` -> `true`), catches `(ImportError, OSError)` on `weasyprint` with a `PyMuPDF` `fitz.Story` fallback, lazy-loads extractors, moves `build_translation_pr_body` to `scripts/utils/pr_body.py`, fixes `epub_compiler.py` raw docstring, adds CI `test` gate + dynamic digest resolution + non-root `uv.lock` `Dockerfile`, and aligns `v4.8.0` docs (`REQ-085`, `REQ-086`).

## Interfaces and flow

1. **HTTP Security & Offloaded Middleware Presence (`REQ-082`, `REQ-084`)**:
   - `RequestContextMiddleware` streams `/mcp` and `/messages*` chunks up to `10 MiB` (returning `413` early if exceeded), resolves Bearer/query presence on a worker thread via `anyio.to_thread.run_sync`, stores the Bearer agent in `_REQ_BEARER_AGENT`, and passes it to `resolve_auth` so Bearer `/mcp` calls query/touch `agents` once.
   - `_parse_json_body` streams up to `10 MiB` (`413`) and rejects non-empty non-JSON `Content-Type` (`415`); mutating `/admin*` routes validate `_assert_admin_csrf_safe` (`403`).
2. **DB Reuse, Aggregation, & Git-Before-DB Acceptance (`REQ-084`)**:
   - `CollabStore.list_books_summary` + `get_book_chapter_counts` + `get_book_actionable_submission_counts` supply `CollabMcpService.list_books` in 3 SQL queries without reading `source_text`.
   - `_finalize_accepted_chapter` runs `git_sync.sync_accepted_chapter` first for public books and only then commits DB `accepted` state inside `with self.store.transaction():`.

## Boundaries and recovery

- **Zero Breaking Auth Changes (`REQ-083`)**: Existing 3-word and `cbt_ak_*` `key_sha256` entries continue authenticating cleanly while new/rotated keys use 256-word slots and `w1-***` hints.
- **Portable PDF Fallback (`REQ-086`)**: On hosts missing `libpango-1.0-0` (raising `OSError` on `import weasyprint`), `scripts/compile_pdf.py` falls back cleanly to `PyMuPDF` (`fitz.Story` + `fitz.DocumentWriter`) while keeping WeasyPrint primary when available.
- **Test Mock Compatibility (`REQ-086`)**: `scripts/collab_seed.py` re-exports `build_translation_pr_body` and `scripts/collab_worker.py` retains `GitHubAccountContext`.
