# Collab Books MCP Dedicated `tech_admin` Role & Hybrid Bug Resolution Workflow (`collab-mcp:v0.8.0` / `MCP v4.6.0`)

- Feature ID: 2026-11-27-collab-mcp-tech-admin-bug-workflow
- Type: enhancement
- Status: In-Testing
- First entered In-Testing: 2026-11-27
- Related specifications: Implements: Changes [2026-11-21-collab-mcp-bug-triage-and-hardening](../2026-11-21-collab-mcp-bug-triage-and-hardening/spec.md), Changes [2026-11-27-collab-mcp-admin-console-rework](../2026-11-27-collab-mcp-admin-console-rework/spec.md)

## Problem and source intent

Observed state on `https://collab.vinhthang.dev/admin#bugs` (`v4.5.0` / `v0.7.0`):
1. **Passive `Accept` button**: Clicking **Accept** on a bug report at `/admin#bugs` only updates `bug_reports.status = "accepted"` in SQLite/PostgreSQL. No dedicated engineering role owns accepted bugs, no exclusive bug claim mechanism prevents duplicate fix work, no outbound webhook or GitHub Issue dispatch exists, and `bug_reports` lacks `claimed_by`, `claimed_at`, `issue_url`, `pr_url`, and `dispatch_status` fields.
2. **Role coupling**: Currently `agent_admin` handles both literary coordination (book parsing, glossary curation, `ch01` seeding, chapter adjudication) and bug triage. Engineering agents fixing server/CLI/UI defects need a dedicated `tech_admin` role separated from content/identity controls.

Authorizing user intent (2026-11-27, prompt + `/grill-me` resolved choices `A1`–`A3`):
- **Prompt**: *"I want an agent for handle this trigger, like the tech-admin role, tech admin will handle the bug"*
- **A1 (Dedicated `tech_admin` Role)**: Separated from content `agent_admin` — can query, claim, update (`in_progress` / `fixed` / `declined`), and inspect bug/submission context, but CANNOT approve chapters/books, parse/import books, edit glossaries, or manage agent keys/roles.
- **A2 (Hybrid `Accept` Trigger)**: Accepting a bug queues it in `list_tasks(role="tech_admin")` (`accepted_bug_reports`) AND optionally fires an outgoing webhook (`BUG_ACCEPT_WEBHOOK_URL` + optional `BUG_ACCEPT_WEBHOOK_SECRET`) and/or creates a GitHub Issue on `BUG_GITHUB_REPO` (auto when `BUG_AUTO_GITHUB_ISSUE=true` or explicit `create_github_issue=true`).
- **A3 (Branch + TDD PR Workflow)**: Dedicated `collab_tech_admin` agent claims accepted bugs, creates a fix on a branch with TDD tests, opens a PR on `vinhthang/collab-book-translate`, links `pr_url` (`status="in_progress"`), and marks `fixed` (`fixed_in_version`) after merge/deploy.

## Repository grounding

Inspected at `/Users/thanghoang/github/collab-book-translate` (`b1c5e80`):
- `scripts/mcp_server/config.py`: lacks `BUG_ACCEPT_WEBHOOK_URL`, `BUG_ACCEPT_WEBHOOK_SECRET`, `BUG_GITHUB_REPO`, `BUG_AUTO_GITHUB_ISSUE`.
- `scripts/mcp_server/db.py`: `bug_reports` schema lacks `claimed_by`, `claimed_at`, `issue_url`, `pr_url`, `dispatch_status`; no `claim_bug_report` store helper.
- `scripts/mcp_server/auth.py`: `AgentRole` only allows `"contributor" | "agent_admin" | "admin"`; no `assert_bug_handler_or_admin`.
- `scripts/mcp_server/service.py`: `VALID_AGENT_ROLES = {"contributor", "agent_admin"}`, `VALID_BUG_STATUSES = {"open", "accepted", "fixed", "declined"}`, `list_tasks` has no `tech_admin` role or `accepted_bug_reports`, and `triage_bug_report` only writes DB status without hybrid dispatch.
- `scripts/mcp_server/admin_ui.py` & `app.py`: Agents table toggles between only 2 roles (`contributor`/`agent_admin`), bug cards lack `In Progress` / `pr_url` / `issue_url` / `claimed_by` / `dispatch_status`, and no `/api/v1/bugs/{bug_id}/claim` route or `claim_bug_report` MCP tool exists.
- `scripts/collab_worker.py` & `agents/`: no `claim-bug` subcommand or `agents/collab_tech_admin.md` persona.

## Non-goals

- **No inline auto-commit to `main` from `/admin`**: Clicking `Accept` never edits source code or pushes commits automatically; engineering changes flow through `collab_tech_admin` branches, TDD tests, and Pull Requests (`A3`).
- **No weakening of role separation (`A1`)**: Promoting an agent to `tech_admin` MUST NOT grant `agent_admin` content permissions (`submit_book_parse`, `import_chapter_translations`, `update_book_record`, `seed_book`, `manage_flagged_submission`) or `ADMIN` identity controls.

## Requirements

### REQ-076: Dedicated `tech_admin` Role, Bug Claim Lifecycle, & Strict Role Separation

- Changes: [2026-11-21-collab-mcp-bug-triage-and-hardening/REQ-059](../2026-11-21-collab-mcp-bug-triage-and-hardening/spec.md#req-059-bug_reports-store-injection-quarantine--triage-lifecycle)
- Source intent: Resolved `A1` & `A3` (*"Dedicated server role tech_admin (separated from content agent_admin) — can query, claim, update (in_progress/fixed/declined), and inspect bug/submission context, but cannot approve chapters/books or manage agent keys"*).
- Previous contract: `VALID_AGENT_ROLES` only allowed `{"contributor", "agent_admin"}`; `VALID_BUG_STATUSES` allowed `{"open", "accepted", "fixed", "declined"}`; `bug_reports` had no claim or PR/issue/dispatch tracking; `list_tasks` only surfaced `open_bug_reports` to `agent_admin`/`ADMIN`.
- Statement: `auth.py`, `db.py`, and `service.py` SHALL add the `"tech_admin"` role (`VALID_AGENT_ROLES = {"contributor", "agent_admin", "tech_admin"}`), `assert_bug_handler_or_admin` (allowing `ADMIN`, `agent_admin`, or `tech_admin`), and extend `VALID_BUG_STATUSES` to `{"open", "accepted", "in_progress", "fixed", "declined"}`. `bug_reports` SHALL add idempotent SQLite/PostgreSQL columns `claimed_by`, `claimed_at`, `issue_url`, `pr_url`, and `dispatch_status` (`TEXT NOT NULL DEFAULT ''`). `CollabMcpService.claim_bug_report` SHALL transition non-terminal bugs (`not in {"fixed", "declined"}`) to `status="in_progress"` with `claimed_by` and `claimed_at` (rejecting active non-expired claims held by a different handler with `409`, allowing same-handler idempotent re-claim and `ADMIN` override, and honoring `cfg.claim_ttl_seconds`). `triage_bug_report` SHALL allow `tech_admin` (except `flagged_security` bugs which remain `ADMIN`-only), accept optional `pr_url` and `issue_url` (validated as `http(s)://` <= 500 chars and scanned for prompt injection), and `list_tasks(role="tech_admin")` SHALL return `accepted_bug_reports` (`statuses=("accepted", "in_progress")`, ordered `accepted` before `in_progress`). `tech_admin` callers SHALL receive `403` on content/identity admin operations (`submit_book_parse`, `update_book_record`, `import_chapter_translations`, `seed_book`, `manage_flagged_submission`, `set_agent_role`, `rotate_agent_key`, `revoke_agent`), and `contributor` callers SHALL receive `403` on `claim_bug_report` and `triage_bug_report`.
- AC-076-1 — Success: Promoting an agent to `tech_admin` surfaces `accepted` & `in_progress` bugs in `list_tasks(role="tech_admin")`, allows `claim_bug_report` (`in_progress`, `claimed_by`, `claimed_at`), updates `pr_url` and terminal `fixed` (`fixed_in_version`), and supports same-handler re-claim and `ADMIN` override.
- AC-076-2 — Boundary/failure: Active claims by another `tech_admin` return `409`; claiming `fixed`/`declined` bugs returns `409`; non-HTTP `pr_url`/`issue_url` returns `400` and prompt-injection payloads return `422`; `tech_admin` receives `403` on content parsing, glossary updates, seeding, chapter approval, agent role/key management, and `flagged_security` bugs; `contributor` receives `403` on `claim_bug_report`.

### REQ-077: Hybrid `Accept` Trigger Dispatcher (MCP Queue + Webhook + GitHub Issue)

- Source intent: Resolved `A2` (*"Hybrid: Accept queues the bug for list_tasks(role='tech_admin') AND optionally fires an outgoing webhook (e.g., to ai-incident-commander or custom URL) / creates a GitHub Issue on vinhthang/collab-book-translate"*).
- Statement: `McpServerConfig` SHALL add `bug_accept_webhook_url` (`BUG_ACCEPT_WEBHOOK_URL`), `bug_accept_webhook_secret` (`BUG_ACCEPT_WEBHOOK_SECRET`), `bug_github_repo` (`BUG_GITHUB_REPO`, default `"vinhthang/collab-book-translate"`), and `bug_auto_github_issue` (`BUG_AUTO_GITHUB_ISSUE`, default `False`). When `triage_bug_report` transitions a bug to `status="accepted"`, it SHALL run non-blocking dispatch after persisting the DB transition:
  1. Queue availability: the persisted `"accepted"` row immediately appears in `list_tasks(role="tech_admin")` (`accepted_bug_reports`).
  2. Outgoing webhook: if `cfg.bug_accept_webhook_url` is configured, POST JSON (`event="bug.accepted"`, `bug_report`, `hub_url`) with `Authorization: Bearer <secret>` when configured and record `"webhook:ok"` or `"webhook:error:<code>"` in `dispatch_status`.
  3. GitHub Issue creation: if `(cfg.bug_auto_github_issue or create_github_issue)` is true and `cfg.github_token` is configured and `issue_url` is empty, POST a labeled (`bug`, severity) issue to `https://api.github.com/repos/{cfg.bug_github_repo}/issues`, persist the returned `html_url` into `issue_url`, and record `"github_issue:ok"` (or `"github_issue:error:<code>"`); already-populated `issue_url` SHALL NOT be duplicated on re-accept. Dispatches failing over the network or returning HTTP >= 400 SHALL be captured into `dispatch_status` without failing the `triage_bug_report` HTTP response.
- AC-077-1 — Success: Accepting a bug triggers configured webhook with Bearer auth and/or creates one GitHub Issue on `bug_github_repo`, storing `issue_url` and `dispatch_status` (`webhook:ok`, `github_issue:ok`), and idempotently skips duplicate GitHub Issue creation if `issue_url` already exists.
- AC-077-2 — Boundary/failure: Webhook or GitHub API timeouts / HTTP `500` errors record bounded error markers in `dispatch_status` while still returning HTTP `200` with the persisted `accepted` bug report; when neither webhook nor GitHub trigger is enabled/requested, zero outbound HTTP calls occur.

### REQ-078: MCP/REST/CLI Claim Surface, Admin UI 3-Role & PR Controls, `collab_tech_admin` Agent, & `v4.6.0`

- Changes: [2026-11-21-collab-mcp-bug-triage-and-hardening/REQ-060](../2026-11-21-collab-mcp-bug-triage-and-hardening/spec.md#req-060-bug-triage-mcp-surface-rest-api-admin-ui--cli), [2026-11-27-collab-mcp-admin-console-rework/REQ-075](../2026-11-27-collab-mcp-admin-console-rework/spec.md#req-075-modular-admin-workspace-console-ui-unified-chrome-adminthreads-adminread--v450)
- Current contract: Changed by [2026-11-27-collab-mcp-security-scalability-and-ci-hardening/REQ-085](../2026-11-27-collab-mcp-security-scalability-and-ci-hardening/spec.md#req-085-admin-retry-seed-js-fix-ci-test-gating--uvlock-pinned-non-root-dockerfile); Changed by [2026-11-27-collab-mcp-security-scalability-and-ci-hardening/REQ-086](../2026-11-27-collab-mcp-security-scalability-and-ci-hardening/spec.md#req-086-lazy-extractors-zero-circular-dependencies-raw-docstring--v480-invite-only-surface)
- Source intent: Resolved `A1`–`A3` (*"bug-reports / admin#bugs trigger + tech-admin role + fix on branch with TDD & PR"*).
- Previous contract: `/admin` Agents table toggled only between `contributor` and `agent_admin`; bug cards lacked `in_progress`, `claimed_by`, `pr_url`, `issue_url`, `dispatch_status`, and optional GitHub Issue creation; MCP/REST/CLI lacked `claim_bug_report` and `role="tech_admin"`; `agents/collab_tech_admin.md` did not exist; server/plugin advertised `"4.5.0"` (`v0.7.0`).
- Statement: `app.py` SHALL expose MCP tool `claim_bug_report`, MCP prompt `collab_tech_admin_workflow`, REST routes `POST /api/v1/bugs/{bug_id}/claim` & `POST /admin/api/bugs/{bug_id}/claim`, extend `triage_bug_report` + `/triage` routes with `pr_url`, `issue_url`, and `create_github_issue`, and support `role="tech_admin"` across `list_tasks` & `set_agent_role`. `admin_ui.py` SHALL render a 3-role `<select id="role-{handle}">` (`contributor`, `agent_admin`, `tech_admin`) + `.js-toggle-agent-role` in `#agents`, display `in_progress` badges cleanly under Open bugs, render HTML-escaped `claimed_by`, `dispatch_status`, safe HTTP links for `issue_url`/`pr_url`, `<input id="bug-pr-{id}">`, `In Progress` triage button, and pass `node --check`. `scripts/collab_worker.py` SHALL support `claim-bug`, `list-tasks --role tech_admin`, and `triage-bug` (`in_progress`, `--pr-url`, `--issue-url`, `--create-github-issue`). `agents/collab_tech_admin.md` SHALL define the `collab_tech_admin` workflow persona, and server/`plugin.json`/`.github/workflows/ci-cd.yml`/`SKILL.md`/`agents/collab_admin.md` SHALL advance to `"4.6.0"` (`v0.8.0`).
- AC-078-1 — Success: `/admin` renders 3-role controls, `in_progress`/PR/issue/dispatch UI passing `node --check`, `collab_worker.py` commands execute end-to-end against REST routes, `agents/collab_tech_admin.md` documents the branch+PR workflow, and `/api/health`, `/llms.txt`, `plugin.json`, and MCP `initialize` report `"4.6.0"`.
- AC-078-2 — Boundary/failure: Unsafe `javascript:` URLs in `pr_url`/`issue_url` are omitted cleanly by `_safe_http_url`; XSS payloads in `claimed_by`/`dispatch_status`/`pr_url` are HTML-escaped; unauthenticated `/admin/api/bugs/{id}/claim` remains `403` and full `pytest` suite passes with `0` failures.

## Assumptions and owner-deferred questions

- Production `vinhthang/oci` Helm chart defaults can enable `BUG_ACCEPT_WEBHOOK_URL` / `BUG_AUTO_GITHUB_ISSUE` via secrets/env whenever live automated webhook/issue dispatch is desired, while keeping safe defaults (`""` / `False`) so zero accidental outbound traffic fires without opt-in configuration or explicit admin trigger. No open `[PROVISIONAL]` questions remain.

## Approvals and lifecycle history

- `2026-11-27`: `Planning -> In-Progress` — User approved Q1–Q3 (`A1`–`A3`) via `/grill-me` authorizing dedicated `tech_admin` role, hybrid `Accept` dispatcher, Admin UI updates, CLI/agent persona, and `collab-mcp:v0.8.0` (`MCP v4.6.0`).
- `2026-11-27`: `In-Progress -> In-Testing` — Completed Stages 1–3 (`362 passed, 6 warnings, 0 failed, 0 skipped` full regression suite, `tests/test_option_a_hygiene.py` `2 passed`, `mutation-guard` `100.0%` (`15/15`) on `auth.py`, `93.3%` (`14/15`) on `db.py`, `80.0%` (`12/15`) on `service.py`, `100.0%` (`15/15`) on `admin_ui.py`, `node --check` exit `0`, single-agent `code-review` `Approved` `0` P0/P1) and updated 1-hop `- Current contract:` links on `REQ-059`, `REQ-060`, and `REQ-075`. See [Run 1](./verification.md#run-1--2026-11-27).
