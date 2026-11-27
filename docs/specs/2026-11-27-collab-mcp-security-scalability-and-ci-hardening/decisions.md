# Decisions: Collab Books MCP Security, Scalability, & CI Hardening (`v4.8.0`)

Governing specification: [spec.md](./spec.md)

## ADR-044: Pre-Auth Body Cap, `/admin` Browser CSRF Gate, Fail-Closed SSO/Proxy Perimeter, & Expanded 3-Word `AGENT_KEY`

- Governs: REQ-082, REQ-083
- State: Proposed
- Context and constraints: `collab-mcp` serves browser SSO `/admin*` routes behind Caddy Forward-Auth alongside stateless MCP (`/mcp`) and REST (`/api/v1/*`) endpoints. `RequestContextMiddleware` currently buffers unbounded chunks before Starlette's `max_request_body_size`, `_parse_json_body` ignores `Content-Type` and browser `Origin`, empty `COLLAB_ADMIN_EMAILS` fails open, `CF-Connecting-IP` is trusted from arbitrary peers, and 128-word 3-word keys expose `w1`+`w3` in `key_hint` with zero failed-auth throttling.
- Viable options:
  1. Enforce a streaming `10 MiB` cap inside `RequestContextMiddleware` & `_parse_json_body`, require JSON `Content-Type` on non-empty JSON bodies plus `Origin`/`Sec-Fetch-Site` validation on mutating `/admin*` routes, fail closed on empty `admin_emails`, trust proxy IP headers only from configured `trusted_proxy_ips` (or explicit `TRUST_PROXY_HEADERS=true`), expand 3-word lists to >=256 words/slot with `w1-***` hints (keeping existing `key_sha256` valid), and rate-limit failed auth attempts + `agent_heartbeat`.
  2. Replace 3-word keys with opaque hex tokens and require CSRF tokens stored in server-side sessions on `/admin`.
- Decision: Choose Option 1.
- Costs and risks: Existing `key_hint` rows minted before `v4.8.0` keep their stored string until rotated, while all newly minted/rotated keys emit `w1-***`. Tightening `Content-Type` and `Origin` affects only browser cross-origin or `text/plain` callers; empty-body non-browser API/test POSTs without `Origin` remain unaffected.
- Rejected alternatives: Option 2 breaks the human-readable 3-word `AGENT_KEY` UX (`A2`) and adds stateful CSRF cookie/session complexity unnecessary behind Caddy Forward-Auth + `Origin`/`Sec-Fetch-Site` + `Content-Type: application/json` checks.
- Affected components: `scripts/mcp_server/{keys,auth,config,app,service}.py`.

## ADR-045: Single-Connection Locked PostgreSQL Reuse, SQL Summary Aggregation, Thread-Offloaded Middleware Presence, & Git-First Chapter Acceptance

- Governs: REQ-084, REQ-085, REQ-086
- State: Proposed
- Context and constraints: `collab-mcp` runs as a single replica (`replicas: 1` on `arm10`) using `RLock`-synchronized `CollabStore` and `CollabBooksGitSync`. Currently each SQL call opens a new `psycopg.connect()`, `RequestContextMiddleware` executes synchronous DB calls on Uvicorn's asyncio loop and `/mcp` re-queries `agents` in `resolve_auth`, `list_books` runs `1 + 2*N` full-blob queries, and `_finalize_accepted_chapter` commits `accepted` DB rows before `git push`.
- Viable options:
  1. Reuse a persistent `RLock`-guarded PostgreSQL connection inside `CollabStore` (with `transaction()` context manager and secondary indexes), offload middleware presence DB work via `anyio.to_thread.run_sync` + pass `pre_authenticated_agent` to `resolve_auth`, aggregate `list_books` via 3 projected SQL queries, and execute `git_sync.sync_accepted_chapter` before committing DB `accepted` state inside `store.transaction()`.
  2. Introduce external `psycopg_pool` / `asyncpg` async rewrite across all 4,250 lines of `service.py`.
- Decision: Choose Option 1.
- Costs and risks: Zero new runtime dependencies (`uv.lock` stays unchanged), while eliminating per-query PG TCP/TLS handshakes, event-loop stalls, `N+1` blob queries, and DB/Git split-brain on push failure. Should `collab-mcp` ever scale beyond `replicas: 1`, `psycopg_pool` + distributed lease locking remain straightforward follow-ups.
- Rejected alternatives: Option 2 requires rewriting every synchronous `CollabStore` call site across `service.py` and changing locked dependencies without `uv` installed locally.
- Affected components: `scripts/mcp_server/{db,app,service,git_sync,admin_ui}.py`, `scripts/extractors/__init__.py`, `scripts/{extract,compile_pdf}.py`, `scripts/utils/pr_body.py`, `.github/workflows/ci-cd.yml`, `Dockerfile`.

Note on PDF fallback portability (`REQ-086`): `scripts/compile_pdf.py` catches `(ImportError, OSError)` around `import weasyprint` (handling hosts lacking `libpango-1.0-0`) and falls back to `PyMuPDF` (`fitz.Story` + `fitz.DocumentWriter`) when `weasyprint is None and fitz is not None`, keeping WeasyPrint primary in Docker where `libpango-1.0-0` is installed.

## Gate 1: normative review

| Check | Status | Evidence / required correction |
| --- | --- | --- |
| Codebase grounding | PASS | Inspected `scripts/mcp_server/{app,auth,config,db,service,git_sync,admin_ui}.py`, `scripts/{extract,collab_worker,collab_seed,collab_sweep}.py`, `.github/workflows/ci-cd.yml`, and `Dockerfile` at `71310f4`. |
| Attributable intent and explicit scope | PASS | Grounded in user prompt + `ask_question` `A1`–`A3`; explicit non-goals defer `service.py` splitting and keep work on feature branch. |
| Observable requirements and meaningful acceptance criteria | PASS | `REQ-082..REQ-086` specify exact status codes (`403`/`413`/`415`/`429`), `10 MiB` cap, `>= 256` wordlists, `<= 4` SQL queries, `arch-guard` exit `0`, and `"4.8.0"`/`v0.9.1`. |
| Assumptions and owner-deferred questions are explicit | PASS | Deferred `service.py` modularization and live deployment documented; zero `[PROVISIONAL]` items. |
| Architectural rationale respects requirements | PASS | `ADR-044` and `ADR-045` govern `REQ-082..REQ-086` without contradicting parent specs. |
| IDs, references, and revision route are consistent | PASS | Allocates `REQ-082..REQ-086` and `ADR-044..ADR-045` above `REQ-081`/`ADR-043` as a linked delta package. |
