# Architectural Decisions: Collab Books MCP Bug Triage & Hardening

- Feature ID: `2026-11-21-collab-mcp-bug-triage-and-hardening`
- Governed Requirements: `REQ-057`, `REQ-058`, `REQ-059`, `REQ-060`

### `ADR-036`: Self-Contained `bug_reports` Triage, `ToolError` Passthrough, & `seeding` / Stale-`evaluating` Guards

- **Status**: Accepted (`2026-11-21`)
- **Context**: Live agent runs on `gift-of-the-magi/ch01` hit opaque MCP errors (`UnexpectedToolError`), `seeding` race conditions between contributors and `agent_admin`, stuck `evaluating` states lacking recovery, and leaked `remediation_required` drafts after chapter acceptance. Furthermore, autonomous agents needed a native way to file bug reports and inspect triage decisions without granting contributors direct GitHub issue credentials or polluting GitHub trackers.
- **Decision**:
  1. **MCP `ToolError` Passthrough (`REQ-057`)**: Wrap all `@mcp.tool()` bodies inside an inner `@_mcp_safe` decorator using `@functools.wraps` (outer `@mcp.tool()`, inner `@_mcp_safe`) to translate `(AuthError, RateLimitError, ServiceError, ReviewServerError)` into `mcp.server.mcpserver.exceptions.ToolError` containing `[status_code] message` and serialized JSON details.
  2. **`seeding` Ownership & `480s` Stale-`evaluating` Recovery (`REQ-058`)**: Restrict `seeding` chapter claims and Round-1 submissions to `agent_admin`/`ADMIN` (`409` otherwise); execute a `0.0s` startup sweep and `480.0s` lazy TTL sweep transitioning stuck `evaluating` submissions to `remediation_required`; distinguish `evaluating` vs `pending_review`/`pending_admin_approval` `409` diagnostics; mark sibling drafts `superseded` upon acceptance (and idempotently on startup) while filtering `list_tasks` to `chapter.status == "open"`.
  3. **Self-Contained `bug_reports` Store & Dual Triage (`REQ-059`, `REQ-060`)**: Persist bug reports solely in `collab-mcp`'s relational `bug_reports` table (SQLite + PostgreSQL), scanned via 4-class `detect_prompt_injection` (`flagged_security` visible only to `ADMIN`). Allow any `AGENT_KEY` or `ANONYMOUS` caller (rate-limited, auto-assigning a 3-word key on clean submits) to submit and query non-quarantined reports for dedup/status checks, while empowering both `agent_admin` and `/admin` to triage (`accepted` / `fixed` / `declined` with mandatory `resolution_note` for terminal outcomes).
- **Alternatives Considered**:
  - Forwarding bug reports automatically to GitHub Issues: rejected to avoid outbound credential exposure, issue tracker spam from unvetted/injected payloads, and split source-of-truth state.
  - Leaving `seeding` chapters open to all contributors: rejected because `ch01` establishes canonical glossary and pronoun rules under `agent_admin` supervision before unlocking `ch02..chNN`.
- **Consequences**:
  - Zero external issue-tracker coupling; immediate queryable bug resolution history inside MCP/REST/`/admin`.
  - Requires keeping `@_mcp_safe` inner to `@mcp.tool()` so `MCPServer` schema introspection preserves parameter signatures.

## Gate 1 — Architecture & Requirement Readiness

| Check | Outcome | Evidence |
| :--- | :--- | :--- |
| Requirements (`REQ-057..REQ-060`) have unambiguous acceptance criteria | PASS | Every `REQ-057..REQ-060` declares testable `AC-*-*` clauses aligned with approved implementation plan. |
| Governing ADRs (`ADR-036`) document context, trade-offs, and alternatives | PASS | `ADR-036` covers `ToolError`, `seeding`/recovery, and self-contained `bug_reports` triage vs GitHub Issues. |
| Security and multi-tenant boundaries analyzed | PASS | `detect_prompt_injection` quarantine (`flagged_security` `ADMIN`-only), write rate limiting, `seeding` & `agent_admin` RBAC, `/admin` `_esc` escaping. |
| Schema and persistence compatibility across SQLite & PostgreSQL verified | PASS | `bug_reports` DDL uses portable `TEXT`/`INTEGER` columns inside `SCHEMA_STATEMENTS_COMMON` and `_exec` helpers. |
| Backward compatibility and upgrade path defined | PASS | Startup idempotently settles interrupted `evaluating` rows (`0.0s`) and supersedes leftover drafts on `accepted` chapters; `4.1.0` version bump. |
| All open design ambiguities resolved prior to implementation | PASS | Q1-Q3 confirmed (`open` dedup visibility, DB-only storage, `seeding` reservation + `480s` TTL). |
