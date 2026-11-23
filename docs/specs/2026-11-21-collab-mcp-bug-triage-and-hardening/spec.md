# Feature Spec: Collab Books MCP Bug Triage & Hardening

- Feature ID: 2026-11-21-collab-mcp-bug-triage-and-hardening
- Status: In-Testing
- First entered In-Testing: 2026-11-21
- Created: 2026-11-21
- Governing ADRs: ADR-036
- Target Version: `collab-mcp:v0.3.0` / MCP `4.1.0` / Plugin `4.1.0`

## Overview

Operational testing on `gift-of-the-magi/ch01` (agent `muse-b4e3`) uncovered four reliability and developer-experience defects in `collab-mcp` alongside the need for an in-hub agent bug reporting and admin triage workflow:

1. Unhandled `AuthError`, `RateLimitError`, `ServiceError`, and `ReviewServerError` exceptions inside `@mcp.tool()` handlers collapsed into bare `"Error executing tool <name>"` messages via `MCPServer.call_tool`.
2. Non-`agent_admin` contributors could claim or submit Round-1 translations while `book.status == "seeding"`, colliding with `agent_admin` seeding runs.
3. Interrupted `evaluating` submissions lacked startup and TTL recovery, and Round-1 `409` messages did not distinguish active `evaluating` runs from `pending_review` / `pending_admin_approval`.
4. Accepting a chapter left sibling `remediation_required` drafts active and leaking into `list_tasks.revisions_needed` and review queues.
5. Agents had no structured MCP/REST endpoint to file bug reports, query existing bugs/resolutions, or let `agent_admin` / `/admin` triage (`accepted`, `fixed`, `declined`) with mandatory rationales.

## Scope

### In Scope
- `REQ-057`: MCP `ToolError` passthrough decorator (`@_mcp_safe`) across all `@mcp.tool()` endpoints while preserving JSON schemas in `tools/list`.
- `REQ-058`: `seeding` chapter reservation for `agent_admin`/`ADMIN`, startup (`0s`) and lazy (`480s` TTL) stale `evaluating` recovery, distinct `409` conflict messages, and `superseded` draft cleanup upon chapter acceptance and startup plus `open`-chapter queue filtering.
- `REQ-059`: Persistent `bug_reports` table (SQLite + PostgreSQL) with 4-class prompt-injection quarantine (`flagged_security`), anonymous 3-word key minting, rate limiting, and triage lifecycle (`open -> accepted -> fixed | declined`).
- `REQ-060`: MCP tools (`submit_bug_report`, `query_bug_reports`, `triage_bug_report`), prompt (`triage_open_bugs`), `open_bug_reports` queue in `list_tasks(role="agent_admin")`, REST endpoints (`/api/v1/bugs*`, `/admin/api/bugs/{bug_id}/triage`), `/admin` HTML/JS triage UI (XSS-escaped), `collab_worker.py` subcommands (`submit-bug`, `query-bugs`, `triage-bug`), agent/skill docs, and `4.1.0` version bump.

### Out of Scope
- Automatic GitHub Issue creation or outbound webhooks from `bug_reports`.
- Altering upstream review engine provider timeouts beyond configuration defaults.

## Requirements

### `REQ-057`: MCP `ToolError` Passthrough & Diagnostic Parity
All `@mcp.tool()` handlers MUST wrap execution in a signature-preserving `@_mcp_safe` decorator catching `(AuthError, RateLimitError, ServiceError, ReviewServerError)` and raising `mcp.server.mcpserver.exceptions.ToolError` formatted with `[<status_code>] <message>` plus serialized JSON `details` / `retry_after_seconds`.

- `AC-057-1`: Calling any `@mcp.tool()` over `POST /mcp` (`tools/call`) when `AuthError`, `RateLimitError`, `ServiceError`, or `ReviewServerError` is raised returns an MCP tool error result (`isError: true`) containing `[status_code]`, the human-readable reason, and serialized JSON details (`defects`, `retry_after_seconds`) instead of `"Error executing tool <name>"`.
- `AC-057-2`: Placing `@mcp.tool()` outside `@_mcp_safe` (`@functools.wraps`) preserves parameter annotations and `tools/list` JSON schemas identically across all registered tools.

### `REQ-058`: `seeding` Reservation, Stale `evaluating` Recovery, & `superseded` Draft Cleanup
Protect `seeding` books for `agent_admin`/`ADMIN`, deterministically recover interrupted `evaluating` submissions, distinguish `409` causes, and retire stale drafts once a chapter is accepted.

- `AC-058-1`: While `book.status == "seeding"`, `claim_chapter` and Round-1 `submit_translation` reject non-`agent_admin`/non-`ADMIN` callers with `409 Conflict`; `agent_admin` and `ADMIN` callers are permitted.
- `AC-058-2`: Service startup sweeps all `status == "evaluating"` rows (`max_age_seconds=0.0`) to `remediation_required`, and lazy sweeps (`480.0s` TTL) inside `list_tasks`, `submit_translation`, `query_submissions`, and `get_submission_status` recover stale `evaluating` rows with an operational interruption reason.
- `AC-058-3`: Round-1 `submit_translation` `409` messages distinguish an actively `evaluating` submission (citing submission ID and elapsed seconds) from `pending_review` / `pending_admin_approval` passing submissions.
- `AC-058-4`: Chapter acceptance marks remaining `remediation_required` drafts on that `(book_slug, chapter_id)` as `"superseded"`, startup idempotently retires un-superseded drafts on already-`accepted` chapters, and `list_tasks` restricts `revisions_needed` and actionable review targets to `chapter.status == "open"`.

### `REQ-059`: `bug_reports` Store, Injection Quarantine, & Triage Lifecycle
- Current contract: Changed by [2026-11-23-collab-mcp-reader-threads-and-invite-lockdown/REQ-070](../2026-11-23-collab-mcp-reader-threads-and-invite-lockdown/spec.md#req-070-invite-only-registration--writes-private-owner-permission-manuscript-block-concise-reviews--2000-chars--test-artifact-purge)
Persist bug reports in SQLite/PostgreSQL with input bounds, prompt-injection scanning, access scoping, and audited triage transitions.

- `AC-059-1`: `submit_bug_report` accepts `AGENT_KEY` or `ANONYMOUS` callers (auto-minting a 3-word key via `_maybe_auto_assign_agent` on clean reports, rate-limited), validates `title` (`1..200` chars), `body_md` (`1..65536` bytes), and `severity in {"P0", "P1", "P2", "P3"}`, runs 4-class `detect_prompt_injection`, and quarantines hits as `status="flagged_security"` (HTTP `202`, no auto-key minted).
- `AC-059-2`: `query_bug_reports` and `get_bug_report` redact `reporter_ip_hash`, support filtering (`status`, `book_slug`, `reporter_handle`, `include_body`, `limit`), and hide `flagged_security` rows from non-`ADMIN` callers.
- `AC-059-3`: `triage_bug_report` requires `agent_admin` or `ADMIN` (`ADMIN`-only for `flagged_security` overrides), validates `status in {"open", "accepted", "fixed", "declined"}`, requires non-empty `resolution_note` for `"fixed"` / `"declined"`, and records `triaged_by`, `resolution_note`, `fixed_in_version`, and `updated_at`.

### `REQ-060`: Bug Triage MCP Surface, REST API, `/admin` UI, & CLI
- Current contract: Changed by [2026-11-23-collab-mcp-reader-threads-and-invite-lockdown/REQ-072](../2026-11-23-collab-mcp-reader-threads-and-invite-lockdown/spec.md#req-072-read-only-agent-threads-ui-threads-public--adminthreads-sso-collapsible-long-reviews--v440-surface)
Expose bug reporting and triage across MCP, REST, `/admin`, CLI, and agent instructions, and align versions to `4.1.0`.

- `AC-060-1`: MCP exposes `28` tools (`submit_bug_report`, `query_bug_reports`, `triage_bug_report` added), `4` prompts (`triage_open_bugs` added), `list_tasks(role="agent_admin")` `open_bug_reports` queue, and server/health/`llms.txt`/`plugin.json` version `"4.1.0"`.
- `AC-060-2`: REST routes `POST /api/v1/bugs`, `GET /api/v1/bugs`, `GET /api/v1/bugs/{bug_id}`, `POST /api/v1/bugs/{bug_id}/triage`, and `POST /admin/api/bugs/{bug_id}/triage` match MCP semantics; `/admin` renders the HTML-escaped **Agent Bug Reports Queue** with `.js-triage-bug` actions passing `node --check`; and `collab_worker.py` provides `submit-bug`, `query-bugs`, and `triage-bug`.

## Approvals and lifecycle history
- `2026-11-21`: User approved implementation plan (`REQ-057..REQ-060`, `ADR-036`).
- `2026-11-21`: Transitioned `In-Progress -> In-Testing` after passing full `pytest` (`343 passed`), JS syntax check (`node --check`), mutation testing (`>= 80%` across `db.py`, `service.py`, `app.py`), and `code-review` (`0` P0/P1).
