# Design: Dedicated `tech_admin` Role & Hybrid Bug Resolution Workflow (`v4.6.0`)

Governing specification: [spec.md](./spec.md)

## Components

- `scripts/mcp_server/config.py`:
  - `bug_accept_webhook_url`, `bug_accept_webhook_secret`, `bug_github_repo`, `bug_auto_github_issue` (`REQ-077`).
- `scripts/mcp_server/db.py`:
  - `bug_reports` columns `claimed_by`, `claimed_at`, `issue_url`, `pr_url`, `dispatch_status` + idempotent SQLite/PostgreSQL migrations (`REQ-076`, `REQ-077`).
  - `claim_bug_report` and extended `update_bug_report` preserving unset optional metadata (`REQ-076`).
- `scripts/mcp_server/auth.py`:
  - `AgentRole` with `"tech_admin"`, `_normalize_agent_role`, and `assert_bug_handler_or_admin` (`REQ-076`).
- `scripts/mcp_server/service.py`:
  - `VALID_AGENT_ROLES` + `VALID_BUG_STATUSES` (`in_progress`), `list_tasks(role="tech_admin")` returning `accepted_bug_reports`, `claim_bug_report` (with TTL conflict `409`), `_validate_optional_http_url`, and hybrid `_dispatch_accepted_bug` inside `triage_bug_report` (`REQ-076`, `REQ-077`).
- `scripts/mcp_server/app.py` & `scripts/mcp_server/admin_ui.py`:
  - MCP tool `claim_bug_report`, MCP prompt `collab_tech_admin_workflow`, REST routes `/api/v1/bugs/{bug_id}/claim` & `/admin/api/bugs/{bug_id}/claim`, 3-role `<select>` in `#agents`, bug card `In Progress` + `pr_url` + `issue_url` + `claimed_by` + `dispatch_status`, `"4.6.0"` / `v0.8.0` alignment (`REQ-078`).
- `scripts/collab_worker.py` & `agents/collab_tech_admin.md`:
  - CLI `claim-bug`, `list-tasks --role tech_admin`, `triage-bug` (`in_progress`, `--pr-url`, `--issue-url`, `--create-github-issue`), and autonomous `collab_tech_admin` persona (`REQ-078`).

## Interfaces and flow

1. **Human `/admin` Triage -> Accept Trigger (`REQ-077`)**:
   - Clicking **Accept** on `/admin#bugs` calls `POST /admin/api/bugs/{bug_id}/triage` (`status="accepted"`, optional `create_github_issue`).
   - Service transitions `bug_reports.status = "accepted"` immediately so `list_tasks(role="tech_admin")` exposes it in `accepted_bug_reports`.
   - Service then runs `_dispatch_accepted_bug`: posts `{"event": "bug.accepted", "hub_url": ..., "bug_report": ...}` to `cfg.bug_accept_webhook_url` (if configured) and creates one GitHub Issue on `cfg.bug_github_repo` if `(cfg.bug_auto_github_issue or create_github_issue)` and `cfg.github_token` and `not issue_url`. Results/errors append cleanly to `dispatch_status`.
2. **`tech_admin` Claim -> Fix PR -> Resolve (`REQ-076`, `REQ-078`)**:
   - A registered agent promoted to `role="tech_admin"` calls `list_tasks(role="tech_admin")`, then claims a bug via `claim_bug_report(bug_id, note=...)` (`status="in_progress"`, `claimed_by`, `claimed_at`).
   - Agent creates a fix branch, runs TDD tests, opens a PR, updates `triage_bug_report(..., status="in_progress", pr_url="https://...")`, and marks `status="fixed"` with `fixed_in_version` and `resolution_note` once merged/deployed.

## Boundaries and recovery

- **Authority isolation (`REQ-076`)**: `_is_coord(ctx)` stays strictly `ADMIN` or `agent_admin` so `tech_admin` callers get `403` on content/identity admin calls (`submit_book_parse`, `update_book_record`, `import_chapter_translations`, `seed_book`, `manage_flagged_submission`, `set_agent_role`, `rotate_agent_key`, `revoke_agent`) and on quarantined `flagged_security` bugs.
- **Claim TTL recovery (`REQ-076`)**: Stale `in_progress` claims older than `cfg.claim_ttl_seconds` expire automatically so another `tech_admin` can claim without `409`; `ADMIN` can always override/re-open or re-assign via triage/claim.
- **Dispatch resilience (`REQ-077`)**: Webhook or GitHub API exceptions never roll back `status="accepted"` and are truncated cleanly into `dispatch_status`.
