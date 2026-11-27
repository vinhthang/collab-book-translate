# Zero-Prompt MCP Onboarding, 3-Word `AGENT_KEY`, & Per-Book Access Control

- Feature ID: 2026-11-19-collab-mcp-onboarding-and-book-access
- Type: enhancement
- Status: In-Testing
- First entered In-Testing: 2026-11-19
- Related specifications: Implemented: Changes [2026-11-19-collab-mcp-and-books-split](../2026-11-19-collab-mcp-and-books-split/spec.md)

## Problem and source intent

Make joining `https://collab.vinhthang.dev/mcp` completely autonomous for external AI agents—requiring only the MCP server URL with zero manual prompt boilerplate—while automatically issuing easy-to-read 3-word `AGENT_KEY`s and enabling per-book write-restricted `AGENT_KEY` allowlists managed via `/admin` Google SSO Web UI and MCP admin tools.

Authorizing user intent (2026-11-19, verbatim user prompts and `/grill-me` + `/plan` approval):

1. *"why this too detail, I just ask for add this mcp server, https://collab.vinhthang.dev/mcp, and then the agent can do by it self, right?"*
2. *"have a plan to introduce the mcp to other agent, I also want to have an automatically AGENT_KEY, this is to identity the agent, and maybe for some books, I will restrict to a few AGENT_KEY. not all, the AGENT_KEY should be automatically generated, simple and easy to read"*
3. Resolved `/grill-me` decisions & approved `implementation_plan.md`:
   - 3-word lowercase passphrase `AGENT_KEY` (e.g., `amber-fox-river`, zero digits), stored strictly as `key_sha256` + masked `key_hint` (never plaintext in PostgreSQL or public endpoints/git commits), retaining legacy `cbt_ak_*` backward compatibility.
   - Automatic assignment via zero-arg `register_agent()` (guided by built-in MCP `instructions`) AND auto-minting on first anonymous `claim_chapter` / `submit_translation` on public books (never auto-minting on read-only hits).
   - Write-restricted per-book allowlists (`access_mode`: `"public"` | `"restricted"`): public Bookshelf/read visibility stays intact, while `list_tasks`, `get_chapter`, `claim_chapter`, `submit_translation`, and `submit_review` require an allowlisted `AGENT_KEY` (or `ADMIN`).
   - Management via Google SSO `/admin` Web UI + MCP admin tools (`list_agents`, `set_book_access`) + copy-ready onboarding card on `/`.

## Repository grounding

Inspected against `collab-book-translate` at commit `0ae2b74`:

- `scripts/mcp_server/auth.py:56` (`mint_agent_key`) currently emits 48-char hex `cbt_ak_<hex>` tokens; `resolve_auth` authenticates `AGENT_KEY` via `store.get_agent_by_key()`.
- `scripts/mcp_server/db.py:202` (`register_agent`) requires a non-empty `handle`, stores `(handle, display_name, model_info, key_sha256, created_at)` in `agents`, and `books` lacks `access_mode` and `allowed_agents_json` columns.
- `scripts/mcp_server/service.py` does not enforce per-book agent allowlists or auto-mint `AGENT_KEY`s on first anonymous writes.
- `scripts/mcp_server/app.py:253` initializes `MCPServer("collab-books")` with no `instructions`, one-line tool docstrings, no `@mcp.prompt()` entries, and no `/admin` HTML UI.

## Non-goals

- **No plaintext `AGENT_KEY` storage in PostgreSQL or public exposure**: Only `key_sha256` and masked `key_hint` (`word1-***-word3`) are stored; public APIs and `collab_ledger.json` expose canonical `handle`s only.
- **No auto-minting on read-only endpoints**: `GET /`, `list_books`, `get_book`, and `list_tasks` never insert new rows into `agents`.
- **No breaking change to legacy `cbt_ak_*` keys**: Existing hashed keys in PostgreSQL continue to authenticate cleanly.

## Requirements

### REQ-044: Easy-to-read 3-word `AGENT_KEY` generation, zero-arg registration, and first-write auto-minting

- Current contract: Changed by [2026-11-27-collab-mcp-security-scalability-and-ci-hardening/REQ-083](../2026-11-27-collab-mcp-security-scalability-and-ci-hardening/spec.md#req-083-expanded-3-word-agent_key-entropy-prefix-only-key_hint-bounded-limiter-failed-auth-throttle--heartbeat-rate-limit)
- Changes: [2026-11-19-collab-mcp-and-books-split/REQ-039](../2026-11-19-collab-mcp-and-books-split/spec.md#req-039-three-tier-authentication-anonymous-agent_key-admin-and-header-spoof-protection)
- Source intent: Verbatim user prompt (*"I also want to have an automatically AGENT_KEY, this is to identity the agent ... the AGENT_KEY should be automatically generated, simple and easy to read"*) and `/grill-me` Q1/Q2 (`3-word passphrase`, `zero-arg register_agent() + first anonymous claim/submit`).
- Previous contract: `register_agent` required an explicit `handle` and generated `cbt_ak_<48-hex>` tokens; anonymous `claim_chapter` / `submit_translation` stayed `ANONYMOUS` without assigning a persistent `AGENT_KEY`.
- Statement: WHEN `register_agent(handle?, display_name?, model_info?)` is called (with all parameters optional), OR WHEN an `ANONYMOUS` caller invokes `claim_chapter` or `submit_translation` on a `public` book without supplying an `agent_key`, THE server SHALL mint a unique lowercase 3-word passphrase `AGENT_KEY` (`<word1>-<word2>-<word3>`, zero digits), derive a unique public `handle` if none was provided, store strictly `key_sha256`, masked `key_hint` (`<word1>-***-<word3>`), and `last_seen_at` in `agents`, return the one-time `agent_key` (`assigned_agent_key` on auto-mint) to the caller, and accept both 3-word and legacy `cbt_ak_*` keys in `Authorization: Bearer` or tool argument `agent_key`.
- AC-044-1 — Success: Calling `register_agent()` with zero arguments returns a 3-word `agent_key` matching `^[a-z]+-[a-z]+-[a-z]+$` and a non-empty `handle`, DB stores `key_sha256 != agent_key` and `key_hint`, subsequent calls with that 3-word key authenticate as `AGENT_KEY` updating `last_seen_at`, legacy `cbt_ak_*` keys still authenticate, and an `ANONYMOUS` `claim_chapter` / `submit_translation` on a `public` book returns `assigned_agent_key` and attributes the work to `agent:<handle>`.
- AC-044-2 — Boundary/failure: IF an unauthenticated caller calls read-only endpoints (`GET /`, `list_books`, `get_book`, `list_tasks`), THEN zero new `agents` rows SHALL be created; IF a duplicate explicit `handle` is requested on `register_agent`, THEN the server SHALL return `409`; IF an `ANONYMOUS` caller calls `claim_chapter` or `submit_translation` on a `restricted` book, THEN the server SHALL return `403` without auto-minting an `agents` row.

### REQ-045: Write-restricted per-book `AGENT_KEY` allowlists and `/admin` SSO management UI

- Source intent: Verbatim user prompt (*"maybe for some books, I will restrict to a few AGENT_KEY. not all"*) and `/grill-me` Q3/Q4 (`write-restricted` + `/admin` Web UI + MCP admin tools).
- Statement: WHEN a book's `access_mode` is `"public"` (default), any caller MAY read tasks and contribute; WHEN an `ADMIN` sets a book's `access_mode` to `"restricted"` via MCP `set_book_access(book_slug, access_mode, allowed_agents)` or `/admin/api/books/{slug}/access` (accepting either canonical `handle`s or 3-word `AGENT_KEY`s resolved via `key_sha256`), THE server SHALL keep the book visible on `GET /`, `list_books`, and `get_book` (with `access_mode: "restricted"` and zero leaked keys), hide its chapters from `list_tasks` for unauthorized callers, reject unauthorized `get_chapter`, `claim_chapter`, `submit_translation`, and `submit_review` calls with `403`, and expose `list_agents`, `set_book_access`, and an `ADMIN`-gated `/admin` HTML dashboard for minting keys and toggling per-book allowlists.
- AC-045-1 — Success: Setting `access_mode="restricted"` with a 3-word `AGENT_KEY` resolves and stores the canonical `handle`, keeps the book visible in `list_books`, hides it from `list_tasks` and returns `403` on `get_chapter`/`claim_chapter`/`submit_translation`/`submit_review` for non-allowlisted agents, permits the allowlisted agent and `ADMIN`, and serves `/admin` (`200 text/html`) plus `/admin/api/agents` (`200 JSON`) to authenticated `ADMIN` callers.
- AC-045-2 — Boundary/failure: IF an unknown `AGENT_KEY`/`handle` or invalid `access_mode` is passed to `set_book_access`, THEN the server SHALL return `400`; IF a non-`ADMIN` caller requests `/admin`, `/admin/api/*`, `list_agents`, or `set_book_access`, THEN the server SHALL reject with `403`; IF a book is `restricted`, raw `AGENT_KEY` strings SHALL NEVER appear in `list_books`, `get_book`, `list_tasks`, or `GET /`.

### REQ-046: Built-in MCP `instructions`, self-explanatory tool docstrings, `@mcp.prompt()` helpers, and `/` onboarding card

- Current contract: Changed by [2026-11-23-collab-mcp-reader-threads-and-invite-lockdown/REQ-072](../2026-11-23-collab-mcp-reader-threads-and-invite-lockdown/spec.md#req-072-read-only-agent-threads-ui-threads-public--adminthreads-sso-collapsible-long-reviews--v440-surface); Changed by [2026-11-27-collab-mcp-security-scalability-and-ci-hardening/REQ-086](../2026-11-27-collab-mcp-security-scalability-and-ci-hardening/spec.md#req-086-lazy-extractors-zero-circular-dependencies-raw-docstring--v480-invite-only-surface)
- Changes: [2026-11-19-collab-mcp-and-books-split/REQ-038](../2026-11-19-collab-mcp-and-books-split/spec.md#req-038-standalone-collab-mcp-transports-discovery-endpoints-and-postgresqlsqlite-persistence)
- Source intent: Verbatim user prompt (*"why this too detail, I just ask for add this mcp server, https://collab.vinhthang.dev/mcp, and then the agent can do by it self, right?"*) and `/grill-me` Q4.
- Previous contract: `MCPServer("collab-books")` had no `instructions`, one-line tool docstrings, no MCP prompts, and a minimal JSON snippet on `/`.
- Statement: WHEN an MCP client connects to `/mcp` (`initialize`, `tools/list`, `prompts/list`), `collab-mcp` SHALL return self-contained workflow `instructions` in `InitializeResult` (covering automatic 3-word `AGENT_KEY` registration, task loop, `<segment>`/`<original>`/`<translate>`/`<hanviet>`/byte-invariance XML contract, `remediation_required` + `rebuttals` convergence, and 6-dimension review rubric), self-explanatory tool descriptions on core tools, and built-in MCP prompts (`translate_next_chapter`, `review_open_submission`); AND `GET /` plus `/llms.txt` SHALL present copy-ready onboarding snippets for major MCP clients (Claude Code CLI, MCP JSON, 1-sentence prompt) and link to `/admin`.
- AC-046-1 — Success: `POST /mcp` `initialize` returns non-empty `result.instructions` documenting the 3-word `AGENT_KEY` and XML/review workflow, `tools/list` includes detailed descriptions for `register_agent`, `submit_translation`, and `submit_review`, `prompts/list` lists `translate_next_chapter` and `review_open_submission`, and `GET /` renders the copy-ready onboarding card and `/admin` link.
- AC-046-2 — Boundary/failure: IF an unauthenticated client fetches `GET /`, `GET /llms.txt`, or `POST /mcp` (`initialize` / `tools/list` / `prompts/list`), THEN responses SHALL succeed with `200` and SHALL contain zero secrets (`COLLAB_ADMIN_TOKEN`, `REVIEW_BEARER_TOKEN`, or raw registered `AGENT_KEY`s).

## Assumptions and owner-deferred questions

- Reversible default: New books default to `access_mode="public"`; Admin can toggle any book between `"public"` and `"restricted"` at any time without losing chapter or submission state.

## Approvals and lifecycle history

- 2026-11-19: `Draft -> Planning` — User resolved all 4 `/grill-me` questions and explicitly approved `implementation_plan.md` covering `REQ-044..REQ-046` and `collab-mcp:v0.1.1` rollout.
- 2026-11-19: `Planning -> In-Testing` — Completed Tasks 1–3; verified `322 passed` (`0` failed, `0` skipped), `mutation-guard` `90.0%` (`auth.py`) and `85.0%` (`service.py`), and single-agent `code-review` `Approved` (`0 P0`, `0 P1`). See [Run 1](./verification.md#run-1--2026-11-19).
