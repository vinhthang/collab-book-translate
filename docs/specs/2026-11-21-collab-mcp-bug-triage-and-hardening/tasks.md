# Implementation Tasks: Collab Books MCP Bug Triage & Hardening

- Feature ID: `2026-11-21-collab-mcp-bug-triage-and-hardening`
- Governed Requirements: `REQ-057`, `REQ-058`, `REQ-059`, `REQ-060`
- Governing ADRs: `ADR-036`

## Gate 2 — Readiness & Traceability

| REQ | AC | Task | Gate 2 |
| :--- | :--- | :--- | :--- |
| `REQ-057` | `AC-057-1` | Task 1, Task 3 | PASS — MCP `/mcp` `ToolError` passthrough specified and covered by `test_mcp_bug_triage_and_hardening.py`. |
| `REQ-057` | `AC-057-2` | Task 1, Task 3 | PASS — `@mcp.tool()` outer / `@_mcp_safe` inner schema preservation defined in `design.md`. |
| `REQ-058` | `AC-058-1` | Task 1, Task 2 | PASS — `seeding` guard on `claim_chapter` and Round-1 `submit_translation` scoped in `service.py`. |
| `REQ-058` | `AC-058-2` | Task 1, Task 2 | PASS — `0.0s` startup sweep and `480.0s` lazy TTL sweep specified in `db.py` and `service.py`. |
| `REQ-058` | `AC-058-3` | Task 1, Task 2 | PASS — Separate `evaluating` vs `pending_review`/`pending_admin_approval` `409` messages specified. |
| `REQ-058` | `AC-058-4` | Task 1, Task 2 | PASS — `_finalize_accepted_chapter` superseding, startup draft cleanup, and `list_tasks` `open` filter scoped. |
| `REQ-059` | `AC-059-1` | Task 1, Task 2 | PASS — `bug_reports` DDL, field bounds, rate limit, auto-key, and 4-class injection quarantine scoped. |
| `REQ-059` | `AC-059-2` | Task 1, Task 2 | PASS — `query_bug_reports`/`get_bug_report` redaction and `flagged_security` visibility rules scoped. |
| `REQ-059` | `AC-059-3` | Task 1, Task 2 | PASS — `triage_bug_report` RBAC, valid transitions, and `resolution_note` requirements scoped. |
| `REQ-060` | `AC-060-1` | Task 1, Task 3, Task 4 | PASS — `28` MCP tools, `4` prompts, `open_bug_reports`, and `4.1.0` version bump scoped. |
| `REQ-060` | `AC-060-2` | Task 1, Task 3, Task 4 | PASS — REST `/api/v1/bugs*`, `/admin` UI + JS check, and `collab_worker.py` subcommands scoped. |

- Backward scope check: PASS — Changes remain strictly additive to `db.py`, `service.py`, `app.py`, `collab_worker.py`, docs, and tests with idempotent startup migration of existing `evaluating` / accepted-chapter drafts.
- Reference and catalog consistency: PASS — `INDEX.md`, `spec.md`, `decisions.md`, `design.md`, and `verification.md` agree on `REQ-057..REQ-060` and `ADR-036`.

## Task 1: TDD suite
- Status: [x] Completed
- Requirements: `REQ-057`, `REQ-058`, `REQ-059`, `REQ-060`
- Files: `tests/test_mcp_bug_triage_and_hardening.py`
- Dependencies: none
- Scope: Write failing TDD test suite verifying `AC-057-1..AC-060-2` (`ToolError` `/mcp` diagnostics, `tools/list` schema preservation, `seeding` 409 gating, startup & `480s` stale-`evaluating` recovery, distinct `409` messages, `superseded` draft cleanup, `submit_bug_report`/`query_bug_reports`/`triage_bug_report`, `open_bug_reports`, `/admin` HTML XSS + JS, and `collab_worker.py` CLI).

## Task 2: Store & service
- Status: [x] Completed
- Requirements: `REQ-058`, `REQ-059`, `REQ-060`
- Files: `scripts/mcp_server/db.py`, `scripts/mcp_server/service.py`
- Dependencies: Task 1
- Scope: Add `bug_reports` table + store CRUD, `recover_stale_evaluating_submissions`, `supersede_other_chapter_submissions`, `supersede_all_accepted_chapter_drafts`, startup + `480s` sweeps, `seeding` reservation, distinct `409` conflict checks, `open`-chapter `list_tasks` filtering, and bug service lifecycle + `open_bug_reports`.

## Task 3: MCP UI & CLI
- Status: [x] Completed
- Requirements: `REQ-057`, `REQ-060`
- Files: `scripts/mcp_server/app.py`, `scripts/collab_worker.py`
- Dependencies: Task 2
- Scope: Add `_mcp_safe` `ToolError` passthrough across all `@mcp.tool()` handlers, `triage_open_bugs` prompt, 3 bug MCP tools (`28` total), REST `/api/v1/bugs*` & `/admin/api/bugs/{bug_id}/triage` routes, `/admin` Bug Reports Queue HTML/JS (`_esc`), `4.1.0` version strings, and `collab_worker.py` `submit-bug`/`query-bugs`/`triage-bug`.

## Task 4: Docs & version
- Status: [x] Completed
- Requirements: `REQ-060`
- Files: `agents/collab_admin.md`, `agents/collab_translator.md`, `skills/collab-translate-book/SKILL.md`, `plugin.json`, `tests/test_collab_config.py`, `tests/test_collab_worker_mcp.py`, `tests/test_mcp_workflow_v2.py`
- Dependencies: Task 3
- Scope: Update contributor/admin guidance with bug reporting & triage workflows, bump `plugin.json` to `"4.1.0"`, and update version/tool/prompt assertions in existing tests.

## Task 5: Gates & rollout
- Status: [x] Completed
- Requirements: `REQ-057`, `REQ-058`, `REQ-059`, `REQ-060`
- Files: `docs/specs/2026-11-21-collab-mcp-bug-triage-and-hardening/spec.md`, `docs/specs/2026-11-21-collab-mcp-bug-triage-and-hardening/tasks.md`, `docs/specs/2026-11-21-collab-mcp-bug-triage-and-hardening/verification.md`, `docs/specs/INDEX.md`
- Dependencies: Task 4
- Scope: Run full `pytest`, `node --check`, `mutation-guard`, `code-review`, transition specs to `In-Testing`, deploy `collab-mcp:v0.3.0`, verify live state & triage Muse's report, and record real-world observations.
