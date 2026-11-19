# Verification: Zero-Prompt MCP Onboarding, 3-Word `AGENT_KEY`, & Per-Book Access Control

Governing specification: [spec.md](./spec.md)

## Planned checks

### AC-044-1 (REQ-044)

- Method: Run `uv run pytest tests/test_mcp_auth_and_store.py tests/test_mcp_onboarding_and_access.py` testing zero-arg `register_agent()`, 3-word `AGENT_KEY` format, SHA-256 DB storage, `key_hint`, `last_seen_at`, legacy `cbt_ak_*` compat, and auto-minted `assigned_agent_key` on anonymous `claim_chapter`/`submit_translation`.
- Expected outcome: 3-word keys authenticate cleanly, DB never stores plaintext keys, legacy keys work, and first anonymous writes auto-assign an `AGENT_KEY`.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-044-2 (REQ-044)

- Method: Verify `agents` row count remains `0` after anonymous reads (`GET /`, `list_books`, `list_tasks`), duplicate handle returns `409`, and anonymous write on a `restricted` book returns `403` with `0` agents inserted.
- Expected outcome: All boundary guards pass deterministically.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-045-1 (REQ-045)

- Method: Set a book to `restricted` via `set_book_access` using a 3-word `AGENT_KEY`, verify public `list_books` visibility, hidden `list_tasks` and `403` on `get_chapter`/`claim_chapter`/`submit_translation`/`submit_review` for outsiders, success for the allowlisted agent and `ADMIN`, and `200` on `/admin` + `/admin/api/agents`.
- Expected outcome: Allowlisted agent and `ADMIN` succeed while outsiders receive `403`.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-045-2 (REQ-045)

- Method: Call `set_book_access` with unknown agent/invalid mode (`400`), call `/admin`, `/admin/api/*`, `list_agents`, `set_book_access` as non-admin (`403`), and assert raw `AGENT_KEY` never appears in public responses.
- Expected outcome: All invalid/unauthorized calls fail cleanly with zero raw-key leaks.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-046-1 (REQ-046)

- Method: Call `POST /mcp` (`initialize`, `tools/list`, `prompts/list`) and `GET /` in `tests/test_mcp_onboarding_and_access.py`.
- Expected outcome: `initialize` returns `instructions`, `tools/list` includes detailed contracts, `prompts/list` includes `translate_next_chapter` and `review_open_submission`, and `GET /` renders the onboarding card and `/admin` link.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-046-2 (REQ-046)

- Method: Inspect unauthenticated `GET /`, `GET /llms.txt`, and `POST /mcp` responses for secrets (`COLLAB_ADMIN_TOKEN`, `REVIEW_BEARER_TOKEN`, raw `AGENT_KEY`s).
- Expected outcome: All return `200` with zero secret substrings.
- Execution: [Run 1](#run-1--2026-11-19)

## Execution evidence

### Run 1 — 2026-11-19

- Checked: REQ-044..REQ-046 / AC-044-1..AC-046-2 (local Python 3.12 test suite + mutation-guard)
- Command: `uv run pytest -q` and `mutation_runner.py` on `scripts/mcp_server/auth.py` and `scripts/mcp_server/service.py` (exit code: `0`)
- Output excerpt:
  ```text
  322 passed in 13.71s
  [Mutation Guard] scripts/mcp_server/auth.py -> Mutation Score: 90.0% (9/10 killed)
  [Mutation Guard] scripts/mcp_server/service.py -> Mutation Score: 85.0% (17/20 killed)
  ```
- Result: AC-044-1 `PASS`, AC-044-2 `PASS`, AC-045-1 `PASS`, AC-045-2 `PASS`, AC-046-1 `PASS`, AC-046-2 `PASS` — Full suite (`322 passed`) and mutation testing (`85.0%`–`90.0%`) passed cleanly.

## Real-world observations

Pending live production rollout of `collab-mcp:v0.1.1`.
