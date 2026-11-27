# Verification: Agent Live MCP Presence, Heartbeat, & Readable Console

Governing specification: [spec.md](./spec.md)

## Planned checks

### AC-079-1 (REQ-079)

- Method: Run `pytest tests/test_mcp_agent_live_presence.py -k req_079` testing live `/sse` connect/disconnect tracking, authenticated `/mcp` `initialize` / `ping` / `tools/list`, explicit `agent_heartbeat` via MCP/REST/CLI, and presence window classification (`connected`/`online`/`idle`/`offline`/`never`/`revoked`).
- Expected outcome: All live transports and heartbeat paths update agent presence accurately without consuming write quota.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-079-2 (REQ-079)

- Method: Run `pytest tests/test_mcp_agent_live_presence.py -k req_079` checking `ANONYMOUS`/unkeyed `403`, overlong `status_note` `400`, and prompt-injection `422`.
- Expected outcome: Unauthorized or malformed heartbeats fail fast cleanly.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-080-1 (REQ-080)

- Method: Run `pytest tests/test_mcp_agent_live_presence.py -k req_080` testing readable slug allocation without hex, collision fallback, initial role at mint, `/profile` rename preserving `AGENT_KEY`, and `/admin/api/agents` `summary`.
- Expected outcome: Readable names and roles persist cleanly while sorting presence-first.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-080-2 (REQ-080)

- Method: Run `pytest tests/test_mcp_agent_live_presence.py -k req_080` checking `403`, `404`, `400`, and `422` guards on `/profile` and mint `role`.
- Expected outcome: Invalid roles, missing handles, invalid strings, and non-admin requests fail with expected codes.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-081-1 (REQ-081)

- Method: Run `pytest tests/test_mcp_agent_live_presence.py -k req_081` inspecting `/admin` Agents table markup, Live Status pills, relative time, `status_note`, filter chips, Edit trigger, Overview `online` count, `node --check`, and `"4.7.0"` discovery.
- Expected outcome: Readable Console renders accurately with valid JS and `4.7.0` metadata.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-081-2 (REQ-081)

- Method: Run full `pytest` suite + `tests/test_option_a_hygiene.py`.
- Expected outcome: All tests pass (`0` failed, `0` skipped) with zero misleading `Active` badges and HTML-escaped XSS strings.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

## Execution evidence

### Run 1 — 2026-11-27

- Date: 2026-11-27
- Environment: Local macOS venv (`/Users/thanghoang/github/collab-book-translate/.venv/bin/pytest`), Node.js (`node --check`)
- Result: PASS
- Commands & results:
  - `.venv/bin/pytest tests/test_mcp_agent_live_presence.py` (`3 passed, 6 warnings`)
  - `.venv/bin/pytest tests/test_option_a_hygiene.py` (`2 passed in 0.34s`)
  - `.venv/bin/pytest tests` (`365 passed, 6 warnings in 30.41s`, `0` failed, `0` skipped)
  - `mutation-guard`: `presence.py` `100.0%` (`15/15`), `auth.py` `93.3%` (`14/15`), `db.py` `100.0%` (`15/15`), `service.py` `80.0%` (`12/15`), `admin_ui.py` `100.0%` (`15/15`)
  - `prepare_code_review.py` + `git diff --check`: exit `0`, `0` P0/P1 findings

## Real-world observations

Verified live on `https://collab.vinhthang.dev` on 2026-11-27 after deploying `ghcr.io/vinhthang/collab-mcp:v0.9.0` (`vinhthang/collab-book-translate@0dd3d590081ec3ecf1765c11657cc75bcf843f91`, CI run `46024624722`; `vinhthang/oci@0e1552b0bcb302a51db3af1fe9c494f5aac887f3`, Fleet run `46024877539`):
- `GET /api/health`: `200`, `{"status":"ok","service":"collab-mcp","version":"4.7.0","books_repo":"vinhthang/collab-books"}`
- `GET /llms.txt`: `200`, includes `v4.7.0` and `agent_heartbeat`
- Unauthenticated `POST /api/v1/agents/heartbeat`: `403`
- Unauthenticated `GET /admin`, `POST /admin/api/agents/test/profile`, and `POST /admin/api/bugs/nonexistent/claim`: `302`
- Public routes: `GET /` (`200`), `GET /read/happy-prince` (`200`), `GET /threads` (`200`), `GET /read/the-changing-world-order` (`404`)
- `vinhthang/collab-books` `main` HEAD unchanged at `21a213c6a5417725fc0197dcbe6e476f5b414136`
