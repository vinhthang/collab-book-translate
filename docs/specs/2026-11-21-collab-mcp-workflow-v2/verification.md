# Verification: Collab Books MCP Workflow v2 (`collab-mcp:v0.2.0`)

Governing specification: [spec.md](./spec.md)

## Planned checks

### AC-052-1 (REQ-052)

- Method: Run `uv run pytest tests/test_mcp_workflow_v2.py -k ac_052` verifying `set_agent_role` promotes a 3-word `AGENT_KEY` or handle to `agent_admin`, reflects `caller.agent_role == "agent_admin"` in `list_tasks`, unlocks `agent_admin` endpoints, and revokes privileges on demotion to `contributor`.
- Expected outcome: All assertions pass cleanly.
- Execution: [Run 1](#run-1--2026-11-21)

### AC-052-2 (REQ-052)

- Method: Verify `400`/`404` on invalid role/identifier, `403` when `agent_admin` or `contributor` invokes `ADMIN`-only operations (`approve_book_intake`, `reject_book_proposal`, `set_agent_role`, `set_book_access`, `set_book_approval_mode`, `publish_release`), and `403` when `agent_admin` accesses a `restricted` book where it is not allowlisted.
- Expected outcome: Exact status codes enforced.
- Execution: [Run 1](#run-1--2026-11-21)

### AC-053-1 (REQ-053)

- Method: Run `uv run pytest tests/test_mcp_workflow_v2.py -k ac_053` testing `approve_book_intake(queue_for_parse=True)` -> `approved_pending_parse`, `list_tasks(role="agent_admin")`, `get_book_source`, `submit_book_parse` (`chapters`, `brief`, rich `book_record` with `names`/`terms`/`pronouns`) -> `seeding` (`brief.json` & `book_record.json` persisted), `update_book_record`, and `ch01` acceptance unlocking `ch02` (`active`).
- Expected outcome: All assertions pass cleanly.
- Execution: [Run 1](#run-1--2026-11-21)

### AC-053-2 (REQ-053)

- Method: Verify `403` for non-`agent_admin` callers, `409` when `submit_book_parse` or destructive `update_book_record` is called after `ch01` locks `glossary_sha`, and `400`/`413`/`422` on empty chapters, oversized payloads, malformed XML, or prompt-injected parse inputs.
- Expected outcome: Every boundary passes cleanly with zero partial writes.
- Execution: [Run 1](#run-1--2026-11-21)

### AC-054-1 (REQ-054)

- Method: Run `uv run pytest tests/test_mcp_workflow_v2.py -k ac_054` verifying Translator & Reviewer queries across `get_book_source`, `get_book` (`brief` + `collab_ledger`), `get_chapter` (`neighbors.prev_chapter`, `neighbors.next_chapter`, `neighbors.seed_ch01_excerpt`), `query_submissions`, and `get_submission_status` (`rounds_history`, `chapter_context`, `book_context`, live `workstream` details).
- Expected outcome: All context fields returned accurately.
- Execution: [Run 1](#run-1--2026-11-21)

### AC-054-2 (REQ-054)

- Method: Verify `403` / filtered results for unauthorized callers on `restricted` books and assert zero secret fields (`key_sha256`, `revision_token_sha256`, `claim_token_sha256`, `author_ip_hash`) across all query responses.
- Expected outcome: Zero leaks and strict `403` isolation.
- Execution: [Run 1](#run-1--2026-11-21)

### AC-055-1 (REQ-055)

- Method: Run `uv run pytest tests/test_mcp_workflow_v2.py -k ac_055` testing `submit_book_proposal` default `approval_mode == "peer"`, `submit_translation` -> `pending_review`, Peer Reviewer `CHANGES_REQUESTED` -> `remediation_required` with open findings -> Translator Round 2 -> Peer Reviewer `APPROVE` (`weighted_score >= 4.0`) -> `accepted` + `collab_ledger.json` reviewer attribution.
- Expected outcome: Full multi-agent peer review convergence passes cleanly.
- Execution: [Run 1](#run-1--2026-11-21)

### AC-055-2 (REQ-055)

- Method: Verify `403` on anonymous `APPROVE`/`CHANGES_REQUESTED` and self-review (including `agent_admin` self-review), `422` on `APPROVE` with `weighted_score < 4.0` or critical issues, `400` on invalid verdict, and `409` on duplicate Round-1 submissions while `pending_review` or non-`ADMIN` `APPROVE` on a `"manual"` book.
- Expected outcome: Every review gate boundary enforced cleanly.
- Execution: [Run 1](#run-1--2026-11-21)

### AC-056-1 (REQ-056)

- Method: Run `uv run pytest tests/test_mcp_workflow_v2.py -k ac_056` and `node --check` on `_ADMIN_JS`, verifying MCP `v4.0.0` `initialize` / `tools/list` (25 tools) / `prompts/list` (3 prompts), `/admin` role/queue/policy controls, `agents/collab_admin.md` presence, and `collab_worker.py --mcp-url` subcommands (`get-book-source`, `submit-book-parse`, `query-submissions`, `submit-review --verdict`).
- Expected outcome: All MCP, UI, JS syntax, and CLI checks pass.
- Execution: [Run 1](#run-1--2026-11-21)

### AC-056-2 (REQ-056)

- Method: Verify `<script>` payloads in parse notes, brief fields, peer review verdicts/issues, and agent handles are HTML-escaped in `/admin`, and run `uv run pytest tests/test_option_a_hygiene.py`.
- Expected outcome: Zero unescaped HTML and zero forbidden tokens.
- Execution: [Run 1](#run-1--2026-11-21)

## Execution evidence

### Run 1 — 2026-11-21

- Checked: REQ-052..REQ-056 / AC-052-1..AC-056-2 (Python 3.12 full test suite + `node --check` + `mutation-guard` + `code-review`)
- Command: `uv run pytest -q`, `node --check`, `mutation_runner.py` on `scripts/mcp_server/{auth,service,app}.py`, and `prepare_code_review.py` (exit code: `0`)
- Output excerpt:
  ```text
  338 passed, 6 warnings
  [Mutation Guard] scripts/mcp_server/auth.py -> Mutation Score: 93.3% (14/15 killed)
  [Mutation Guard] scripts/mcp_server/service.py -> Mutation Score: 80.0% (12/15 killed)
  [Mutation Guard] scripts/mcp_server/app.py -> Mutation Score: 93.3% (14/15 killed)
  ```
- Result: AC-052-1 `PASS`, AC-052-2 `PASS`, AC-053-1 `PASS`, AC-053-2 `PASS`, AC-054-1 `PASS`, AC-054-2 `PASS`, AC-055-1 `PASS`, AC-055-2 `PASS`, AC-056-1 `PASS`, AC-056-2 `PASS` — Full suite (`338 passed`), `_ADMIN_JS` syntax check, mutation testing (`80.0%`–`93.3%`), and single-agent `code-review` (`Approved`, `0 P0`, `0 P1`) all passed.

## Real-world observations

- **2026-11-21 Live E2E (`collab-mcp:v0.2.0` -> `v0.2.1` on `https://collab.vinhthang.dev` + `https://review.vinhthang.dev`)**:
  - Initial `v0.2.0` live run surfaced that `review-server:v1.0.0` defaults `vi-literary-fluency` and `vi-terminology-pronoun-hawk` to `deepseek-v3` (while cluster secrets only enable the warm `agy` `gemini` supervisor). Resolved in `v0.2.1` (`e5afe74`) by adding `McpServerConfig.review_engine` (`default="gemini"`) mapped to `engineOverrides` across all 4 `TRANSLATION_REVIEW` agents plus `background=True` (`202 evaluating` + `get_submission_status` polling) on `submit_translation`.
  - Full 12-step live E2E (`verify_live_v020.py`, Helm `fleet` revision `220741`) passed `12/12` checks cleanly: `GET /api/health` (`version=4.0.0`), MCP `initialize` / `tools/list` (`25` tools) / `prompts/list` (`3` prompts), `set_agent_role` `agent_admin` promotion & `403` authority boundaries, `approve_book_intake(queue_for_parse=True)` -> `approved_pending_parse`, `get_book_source` + `submit_book_parse` -> `seeding`, Round-1 `submit_translation(background=True)` settling in `65.3s` -> `pending_review`, `get_submission_status(include_context=True, include_workstream=True)` + `query_submissions` with zero secret leaks, Peer Reviewer `verdict="CHANGES_REQUESTED"` -> `remediation_required`, Round-2 revision settling in `57.6s` -> `pending_review` (`rounds_history=3`), and clean rejection leaving `collab-mcp-755f68c49d-n46bs` at `1/1 Running` (`0` restarts) with zero leftover disk directories and the public bookshelf untouched.
