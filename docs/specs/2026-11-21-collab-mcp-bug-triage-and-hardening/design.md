# Technical Design: Collab Books MCP Bug Triage & Hardening

- Feature ID: `2026-11-21-collab-mcp-bug-triage-and-hardening`
- Governed Requirements: `REQ-057`, `REQ-058`, `REQ-059`, `REQ-060`
- Governing ADRs: `ADR-036`

## Architecture Overview

### 1. MCP `ToolError` Passthrough (`REQ-057`)
`MCPServer.call_tool` treats unhandled exceptions as internal faults (`UnexpectedToolError` -> `"Error executing tool <name>"`) while surfacing `mcp.server.mcpserver.exceptions.ToolError` directly in tool error responses (`isError: true`).
- Add `_format_tool_error(exc: Exception) -> str` and `_mcp_safe(fn)` inside `scripts/mcp_server/app.py`:
  - Catch `(AuthError, RateLimitError, ServiceError, ReviewServerError)`.
  - Format as `[<status_code>] <message>` plus compact JSON suffix when `details` or `retry_after_seconds` are present.
  - Use `@functools.wraps(fn)` and stack `@mcp.tool()` outer, `@_mcp_safe` inner on every MCP tool handler so parameter annotations and `tools/list` JSON schemas remain unchanged.

### 2. Persistence Schema & Recovery Primitives (`REQ-058`, `REQ-059`)
In `scripts/mcp_server/db.py`:
- Extend `SCHEMA_STATEMENTS_COMMON` with `bug_reports`:
  - Columns: `id` (PK), `title`, `severity`, `status`, `reporter_actor_id`, `reporter_handle`, `reporter_ip_hash`, `book_slug`, `chapter_id`, `submission_id`, `tool_name`, `error_code`, `body_md`, `security_flags_json`, `triaged_by`, `resolution_note`, `fixed_in_version`, `created_at`, `updated_at`.
- Store methods on `CollabStore`:
  - `create_bug_report(...)`, `get_bug_report(bug_id)`, `list_bug_reports(...)`, `update_bug_report(...)`.
  - `recover_stale_evaluating_submissions(*, max_age_seconds: float, reason: str, now_ts: float | None = None) -> int`: scans `status = 'evaluating'`, parses ISO `updated_at`, transitions rows with `(now - updated_ts) >= max_age_seconds` to `status = 'remediation_required'` and appends/sets `judge_summary = reason`.
  - `supersede_other_chapter_submissions(*, book_slug: str, chapter_id: str, accepted_submission_id: str) -> int`: transitions sibling non-accepted drafts (`status IN ('remediation_required', 'rejected')` or `status = 'remediation_required'` distinct from `accepted_submission_id`) on `(book_slug, chapter_id)` to `status = 'superseded'`.
  - `supersede_all_accepted_chapter_drafts() -> int`: finds chapters with `status = 'accepted'` and calls `supersede_other_chapter_submissions` for each accepted submission (or supersedes any lingering `remediation_required` rows on accepted chapters).

### 3. Domain Service Hardening & Bug Triage (`REQ-058`, `REQ-059`, `REQ-060`)
In `scripts/mcp_server/service.py`:
- Define `EVALUATING_STALE_SECONDS = 480.0`.
- In `CollabMcpService.__init__`: run startup `recover_stale_evaluating_submissions(max_age_seconds=0.0, ...)` and `supersede_all_accepted_chapter_drafts()`.
- Add `self._sweep_stale_evaluating()` (`max_age_seconds=EVALUATING_STALE_SECONDS`) called at the entry of `list_tasks`, `submit_translation`, `query_submissions`, and `get_submission_status`.
- Enforce `seeding` chapter reservation:
  - In `claim_chapter` and Round-1 `submit_translation`: require `ctx.tier == "ADMIN" or (ctx.tier == "AGENT_KEY" and ctx.agent_role == "agent_admin")` when `book["status"] == "seeding"`, else raise `ServiceError(..., status_code=409)`.
- Distinct `409` diagnostics in Round-1 `submit_translation`:
  - Check blocking submissions separately: active `"evaluating"` cites submission ID and elapsed seconds (`int(max(0.0, time.time() - updated_ts))`), while `"pending_review"` / `"pending_admin_approval"` states clearly that a passing submission awaits peer/admin review.
- Draft cleanup & queue hygiene:
  - Call `supersede_other_chapter_submissions` inside `_finalize_accepted_chapter`.
  - In `list_tasks`: map `ch_status_by_id` for each book so `revisions_needed` and actionable `peer_review_candidates` only include submissions whose chapter still has `status == "open"`.
- Bug report service lifecycle:
  - `submit_bug_report(ctx, ...) -> tuple[int, dict]`: enforces write rate limit; validates `title` (`1..200` chars), `body_md` (`1..65536` UTF-8 bytes), `severity in {"P0", "P1", "P2", "P3"}`, optional `error_code >= 0`; scans `title` and `body_md` with `detect_prompt_injection`. If flags found: persists `status="flagged_security"`, returns `(202, sanitized)` without minting an auto agent key. If clean: `_maybe_auto_assign_agent(ctx)` mints a 3-word key for `ANONYMOUS` callers and creates `status="open"`, returning `(201, sanitized)` plus optional `assigned_agent_key`.
  - `query_bug_reports` & `get_bug_report`: redact `reporter_ip_hash`, hide `flagged_security` rows from non-`ADMIN` callers (`404` on direct `get_bug_report` for non-`ADMIN`).
  - `triage_bug_report(ctx, *, bug_id, status, resolution_note, fixed_in_version="")`: enforces `assert_agent_admin_or_admin(ctx)` (and `assert_admin(ctx)` if current status is `flagged_security`); restricts `status` to `{"open", "accepted", "fixed", "declined"}`; requires non-empty `resolution_note` for `"fixed"` and `"declined"`.
  - `list_tasks`: when `is_coord and role in {"all", "agent_admin"}`, populates `"open_bug_reports"` with `status in {"open", "accepted"}`.

### 4. MCP Tools, REST Endpoints, `/admin` UI, & CLI (`REQ-060`)
In `scripts/mcp_server/app.py`, `scripts/collab_worker.py`, agent docs, and `plugin.json`:
- Bump `MCPServer(..., version="4.1.0")`, `/api/health` `"version": "4.1.0"`, `_render_llms_txt` `v4.1.0`, and `plugin.json` `"4.1.0"`.
- Add `@mcp.prompt() def triage_open_bugs(...)`, `@mcp.tool() submit_bug_report`, `@mcp.tool() query_bug_reports`, and `@mcp.tool() triage_bug_report` (28 tools, 4 prompts).
- Add `_run_sync`-offloaded REST routes `/api/v1/bugs`, `/api/v1/bugs/{bug_id}`, `/api/v1/bugs/{bug_id}/triage`, `/admin/api/bugs/{bug_id}/triage`.
- Add HTML-escaped **Agent Bug Reports Queue** panel (`_esc(...)`) and `.js-triage-bug` click handler inside `_render_admin_html` / `_ADMIN_JS`.
- Add `submit-bug`, `query-bugs`, `triage-bug` subcommands in `scripts/collab_worker.py`.
