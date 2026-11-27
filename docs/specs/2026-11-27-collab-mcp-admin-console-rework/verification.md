# Verification: Collab Books MCP Admin Workspace Console Rework

Governing specification: [spec.md](./spec.md)

## Planned checks

### AC-073-1 (REQ-073)

- Method: Run `pytest tests/test_mcp_admin_console_rework.py -k req_073` verifying key rotation, soft-revoke + reactivation for referenced agents, hard-delete for unused agents, and `submission_count`/`review_count` in `list_agents`.
- Expected outcome: Rotated key authenticates (`200`) while old key fails (`401`); revoked referenced agent fails (`401`) with `revoked_at != ""` and historical rows intact, then reactivates (`200`); unreferenced agent is deleted cleanly.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-073-2 (REQ-073)

- Method: Run `pytest tests/test_mcp_admin_console_rework.py -k req_073` verifying `403`/`404` authorization and missing-agent guards.
- Expected outcome: Non-`ADMIN` callers calling rotate/revoke receive `403`; unknown agent handles return `404`.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-074-1 (REQ-074)

- Method: Run `pytest tests/test_mcp_admin_console_rework.py -k req_074` verifying `GET /admin/api/submissions/{id}`, `POST /admin/api/submissions/bulk-manage`, and `GET /admin/api/books/{slug}/export?format=epub|pdf|docx` on a completed `owner-permission` book.
- Expected outcome: Lazy detail returns full XML/critiques; bulk manage approves multiple submissions to completion; export compiles and streams valid EPUB/PDF/DOCX files with zero writes to `books_repo_dir`.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-074-2 (REQ-074)

- Method: Run `pytest tests/test_mcp_admin_console_rework.py -k req_074` checking `403`/`404`/`400`/`409` boundaries.
- Expected outcome: Non-`ADMIN` callers receive `403`; missing submission/book returns `404`; invalid format or bulk action returns `400`; incomplete book export returns `409`.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-075-1 (REQ-075)

- Method: Run `pytest tests/test_mcp_admin_console_rework.py -k req_075` checking `/admin` Workspace Console HTML/JS (`node --check`), unified `/admin/threads` and `/admin/read/{slug}` theme chrome, and `"4.5.0"` discovery metadata.
- Expected outcome: `/admin` renders sidebar tabs, KPI cards, Action Inbox, drawers, bulk buttons, conditional allowlists, export links, rotate/revoke buttons, and Dark/Light toggle (`node --check` exit `0`); `/admin/threads` and `/admin/read/{slug}` include shared theme CSS/toggle; `/api/health`, `/llms.txt`, `plugin.json`, and MCP `initialize` report `"4.5.0"`.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-075-2 (REQ-075)

- Method: Run full `pytest` suite + `tests/test_option_a_hygiene.py`.
- Expected outcome: All unit/integration and hygiene checks pass (`0` failed, `0` skipped) with HTML-escaped XSS payloads and zero leaked secrets.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

## Execution evidence

### Run 1 — 2026-11-27

- Date: 2026-11-27
- Environment: Local macOS venv (`/Users/thanghoang/github/collab-book-translate/.venv/bin/pytest`), Node.js (`node --check`)
- Result: PASS
- Commands & results:
  - `.venv/bin/pytest tests/test_mcp_admin_console_rework.py` (`3 passed, 6 warnings in 2.12s`)
  - `.venv/bin/pytest tests/test_option_a_hygiene.py` (`2 passed in 0.29s`)
  - `.venv/bin/pytest tests` (`359 passed, 6 warnings in 30.13s`, `0` failed, `0` skipped)
  - `mutation-guard`: `scripts/mcp_server/db.py` `80.0%` (`12/15`), `scripts/mcp_server/git_sync.py` `80.0%` (`8/10`), `scripts/mcp_server/service.py` `80.0%` (`12/15`), `scripts/mcp_server/admin_ui.py` `100.0%` (`15/15`)
  - `prepare_code_review.py` + `git diff --check`: exit `0`, `0` P0/P1 findings

## Real-world observations

Pending live production rollout verification on `https://collab.vinhthang.dev` (`collab-mcp:v0.7.0`).
