# Verification: Dedicated `tech_admin` Role & Hybrid Bug Resolution Workflow

Governing specification: [spec.md](./spec.md)

## Planned checks

### AC-076-1 (REQ-076)

- Method: Run `pytest tests/test_mcp_tech_admin_bug_workflow.py -k req_076` verifying `tech_admin` promotion, `list_tasks(role="tech_admin")` `accepted_bug_reports`, `claim_bug_report`, `in_progress` PR link updates, `fixed` transition, and same-handler/admin re-claim.
- Expected outcome: `tech_admin` discovers accepted bugs cleanly, claims with `in_progress` + `claimed_by`/`claimed_at`, and updates PR & resolution metadata cleanly.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-076-2 (REQ-076)

- Method: Run `pytest tests/test_mcp_tech_admin_bug_workflow.py -k req_076` verifying active-claim `409`, terminal-bug claim `409`, URL validation `400`/`422`, and `403` isolation against content/agent-admin actions and contributor claims.
- Expected outcome: All boundary and least-privilege checks fail fast with expected `400`/`403`/`409`/`422` errors.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-077-1 (REQ-077)

- Method: Run `pytest tests/test_mcp_tech_admin_bug_workflow.py -k req_077` with mocked `httpx.Client` for webhook + GitHub Issue creation on `status="accepted"`.
- Expected outcome: Webhook receives signed `bug.accepted` JSON payload; GitHub Issue is created once cleanly, persisted to `issue_url`, and skipped on subsequent re-accept.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-077-2 (REQ-077)

- Method: Run `pytest tests/test_mcp_tech_admin_bug_workflow.py -k req_077` under mocked webhook/GitHub HTTP failures and default off-config.
- Expected outcome: Dispatch failures record bounded status in `dispatch_status` while returning HTTP `200` for `triage_bug_report`; off-config performs zero outbound HTTP calls.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-078-1 (REQ-078)

- Method: Run `pytest tests/test_mcp_tech_admin_bug_workflow.py -k req_078` verifying Admin UI 3-role `<select>`, bug PR/issue/dispatch controls (`node --check`), `collab_worker.py` subcommands, `agents/collab_tech_admin.md`, and `"4.6.0"` metadata.
- Expected outcome: All UI selectors, CLI calls, agent docs, and discovery endpoints match `v4.6.0` cleanly with `node --check` exit `0`.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-078-2 (REQ-078)

- Method: Run full `pytest` suite + `tests/test_option_a_hygiene.py`.
- Expected outcome: All unit/integration and hygiene checks pass (`0` failed, `0` skipped) with unsafe URLs filtered and XSS payloads escaped.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

## Execution evidence

### Run 1 — 2026-11-27

- Date: 2026-11-27
- Environment: Local macOS venv (`/Users/thanghoang/github/collab-book-translate/.venv/bin/pytest`), Node.js (`node --check`)
- Result: PASS
- Commands & results:
  - `.venv/bin/pytest tests/test_mcp_tech_admin_bug_workflow.py` (`3 passed, 6 warnings`)
  - `.venv/bin/pytest tests/test_option_a_hygiene.py` (`2 passed in 0.14s`)
  - `.venv/bin/pytest tests` (`362 passed, 6 warnings in 28.34s`, `0` failed, `0` skipped)
  - `mutation-guard`: `scripts/mcp_server/auth.py` `100.0%` (`15/15`), `scripts/mcp_server/db.py` `93.3%` (`14/15`), `scripts/mcp_server/service.py` `80.0%` (`12/15`), `scripts/mcp_server/admin_ui.py` `100.0%` (`15/15`)
  - `prepare_code_review.py` + `git diff --check`: exit `0`, `0` P0/P1 findings

## Real-world observations

Pending live production rollout verification on `https://collab.vinhthang.dev` (`collab-mcp:v0.8.0`).
