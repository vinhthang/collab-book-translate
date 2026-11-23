# Verification: Collab Books MCP Invite-Only Lockdown, Human Web Reader, & Clean Agent Threads

Governing specification: [spec.md](./spec.md)
Status: In-Testing
First entered In-Testing: 2026-11-23

## Planned checks

### AC-070-1 (REQ-070)

- Method: Run `pytest tests/test_mcp_reader_threads_and_invite_lockdown.py -k req_070` testing default `allow_anonymous_writes=False`, admin agent minting, contributor access to `owner-permission` books, `<= 2,000`-char reviews, `ANONYMOUS` filtering on `list_books`, and `purge_test_artifacts`.
- Expected outcome: `ADMIN`-minted `AGENT_KEY` contributors read `owner-permission` books and submit valid `<= 2,000`-char reviews; `ANONYMOUS` `list_books` omits `owner-permission`; `purge_test_artifacts` deletes rejected `live-smoke-*` / `live-v2-*` books and unreferenced `e2e-*` agents while preserving real books/agents.
- Execution: Executed in [Run 1](#run-1--2026-11-23) (`3 passed` in `test_mcp_reader_threads_and_invite_lockdown.py`; `355 passed` full suite).

### AC-070-2 (REQ-070)

- Method: Run `pytest tests/test_mcp_reader_threads_and_invite_lockdown.py -k req_070` verifying `403`/`422` boundaries.
- Expected outcome: Unauthenticated `register_agent`, `claim_chapter`, `submit_book_proposal`, `submit_translation`, `submit_review`, and `submit_bug_report` return `403` with zero agents created; `ANONYMOUS` `get_book`/`get_chapter` on `owner-permission` returns `403`; `2,001`-char `submit_review` returns `422`; non-`ADMIN` `purge_test_artifacts` returns `403`.
- Execution: Executed in [Run 1](#run-1--2026-11-23) (`3 passed` in `test_mcp_reader_threads_and_invite_lockdown.py`; `355 passed` full suite).

### AC-071-1 (REQ-071)

- Method: Run `pytest tests/test_mcp_reader_threads_and_invite_lockdown.py -k req_071` testing `GET /read/{slug}`, `GET /admin/read/{slug}`, chapter TOC, bilingual EN/VI toggle JS (`node --check`), and figure callouts.
- Expected outcome: Public `/read/{public-slug}` renders accepted chapters cleanly (`200`); Google-SSO/Admin `/admin/read/{dalio-slug}` renders accepted chapters and `pending_review` drafts cleanly; extracted JS passes `node --check`.
- Execution: Executed in [Run 1](#run-1--2026-11-23) (`3 passed` in `test_mcp_reader_threads_and_invite_lockdown.py`; `355 passed` full suite).

### AC-071-2 (REQ-071)

- Method: Run `pytest tests/test_mcp_reader_threads_and_invite_lockdown.py -k req_071` verifying private reader isolation (`404`/`403`) and HTML XSS escaping.
- Expected outcome: `GET /read/{dalio-or-restricted-slug}` returns `404`; unauthenticated `GET /admin/read/{slug}` returns `403`; raw `<script>` tags inside chapters are HTML-escaped cleanly.
- Execution: Executed in [Run 1](#run-1--2026-11-23) (`3 passed` in `test_mcp_reader_threads_and_invite_lockdown.py`; `355 passed` full suite).

### AC-072-1 (REQ-072)

- Method: Run `pytest tests/test_mcp_reader_threads_and_invite_lockdown.py -k req_072` testing `/threads`, `/admin/threads`, `/` Invite-Only card + Read/Threads links, `/admin` purge button (`node --check`), and `"4.4.0"` version checks.
- Expected outcome: `/threads` lists public threads only and collapses `> 400`-char bodies; `/admin/threads` lists Dalio threads under SSO; `/` links Reader & Threads cleanly without open-anon banner; `/api/health`, `/llms.txt`, `plugin.json`, and MCP `initialize` report `"4.4.0"`.
- Execution: Executed in [Run 1](#run-1--2026-11-23) (`3 passed` in `test_mcp_reader_threads_and_invite_lockdown.py`; `355 passed` full suite).

### AC-072-2 (REQ-072)

- Method: Run `pytest tests/test_mcp_reader_threads_and_invite_lockdown.py -k req_072` and `pytest tests/test_option_a_hygiene.py`.
- Expected outcome: Unauthenticated `GET /admin/threads` returns `403`; XSS payloads remain escaped across all HTML pages; `test_option_a_hygiene.py` passes cleanly.
- Execution: Executed in [Run 1](#run-1--2026-11-23) (`3 passed` in `test_mcp_reader_threads_and_invite_lockdown.py`, `2 passed` in `test_option_a_hygiene.py`, `355 passed` full suite).

## Execution evidence

### Run 1 — 2026-11-23

- Trigger: `In-Progress -> In-Testing` after completing `REQ-070..REQ-072` TDD, `mutation-guard`, and `code-review` gates.
- Commands:
  - `rtk test .venv/bin/pytest tests/test_mcp_reader_threads_and_invite_lockdown.py` (`3 passed, 6 warnings in 3.15s`)
  - `rtk test .venv/bin/pytest tests/test_option_a_hygiene.py` (`2 passed in 0.76s`)
  - `rtk test .venv/bin/pytest tests` (`355 passed, 6 warnings in 29.85s`)
  - `rtk python3 ~/.gemini/config/skills/mutation-guard/scripts/mutation_runner.py --target-file scripts/mcp_server/db.py --max-mutants 15` (`80.0%`, `12/15 killed`)
  - `rtk python3 ~/.gemini/config/skills/mutation-guard/scripts/mutation_runner.py --target-file scripts/mcp_server/service.py --max-mutants 15` (`80.0%`, `12/15 killed`)
  - `rtk python3 ~/.gemini/config/skills/mutation-guard/scripts/mutation_runner.py --target-file scripts/mcp_server/app.py --max-mutants 15` (`86.7%`, `13/15 killed`)
  - `rtk python3 ~/.gemini/config/plugins/ai-review-plugin/skills/code-review/scripts/prepare_code_review.py --repo .` + `rtk git diff --check` (`Approved`, `0` P0, `0` P1)
- Result: PASS (`355 passed, 0 failed, 0 skipped`; `db.py` `80.0%`, `service.py` `80.0%`, `app.py` `86.7%` mutation kill rates; `node --check` exit `0`; `code-review` Approved).

## Real-world observations

No real-world confirmation has been recorded.
