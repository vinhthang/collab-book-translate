# Verification: Option A Corpus Split & Remote Book Collaboration MCP Server

Governing specification: [spec.md](./spec.md)

## Planned checks

### AC-037-1 (REQ-037)

- Method: Run `uv run pytest tests/test_option_a_hygiene.py tests/test_collab_completeness.py tests/test_collab_ledger.py` and verify `git ls-files books runs` is empty.
- Expected outcome: Zero tracked files remain under `books/` or `runs/`, relocated fixture tests pass, `mcp_config.json` points to `https://collab.vinhthang.dev/mcp`, and `config.example.json` loads with `<owner>/collab-books`.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-037-2 (REQ-037)

- Method: Run hygiene scanner in `tests/test_option_a_hygiene.py` against tracked files and synthetic forbidden-token inputs.
- Expected outcome: Scanner passes on repository tree and fails deterministically when forbidden tokens or tracked `books/`/`runs/` paths are injected.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-038-1 (REQ-038)

- Method: Run `uv run pytest tests/test_mcp_service_and_app.py` testing `/api/health`, `/llms.txt`, `/` HTML Bookshelf, `/api/v1/*`, and Streamable HTTP `/mcp` tool listing/invocation.
- Expected outcome: All endpoints return expected status codes, MIME types, and valid MCP/JSON payloads.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-038-2 (REQ-038)

- Method: Repeatedly call `store.init_db()` in `tests/test_mcp_auth_and_store.py` and send malformed JSON / unknown `/api/v1/unknown` requests in `tests/test_mcp_service_and_app.py`.
- Expected outcome: Repeated `init_db()` preserves existing rows without error; malformed JSON returns `400` and unknown routes return `404` JSON.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-039-1 (REQ-039)

- Method: Run `uv run pytest tests/test_mcp_auth_and_store.py` exercising `ANONYMOUS`, `register_agent` (`Bearer` header and `agent_key` argument, verifying DB stores SHA-256 hash only), and `ADMIN` (`COLLAB_ADMIN_TOKEN`, `admin_token`, and `/admin/*` `X-Auth-Request-Email`).
- Expected outcome: Each tier resolves accurately, raw `cbt_ak_*` is absent from DB rows, and admin operations succeed for `ADMIN`.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-039-2 (REQ-039)

- Method: Send invalid tokens, non-admin calls to admin tools, and forged `X-Auth-Request-Email` headers on `/mcp` and `/api/v1/*` in `tests/test_mcp_auth_and_store.py`.
- Expected outcome: Invalid tokens return `401`, non-admin admin calls return `403`, and forged `X-Auth-Request-Email` headers on non-`/admin/*` paths are ignored.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-040-1 (REQ-040)

- Method: Submit Round-1 `ANONYMOUS` translation, capture `revision_token`, submit Round-2 revision with `revision_token`, and submit an independent peer review (`reviewer != author`) in `tests/test_mcp_service_and_app.py`.
- Expected outcome: Round 1 returns `revision_token`, Round 2 succeeds with matching `revision_token`, and independent peer review records 6-dimension scores.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-040-2 (REQ-040)

- Method: Trigger rate-limit overflow, `> 512 KB` XML, segment mismatch, 4-class prompt injection, wrong `revision_token` on Round 2, and self-review (`reviewer == author`) in `tests/test_mcp_service_and_app.py` with a spy `ReviewServerClient`.
- Expected outcome: Requests fail/quarantine (`429`, `413`, `422`, `flagged_security`, `403`) with `0` calls made to `ReviewServerClient`.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-041-1 (REQ-041)

- Method: Run multi-round `TRANSLATION_REVIEW` integration tests in `tests/test_mcp_service_and_app.py` (`R1 BLOCK` -> `R2` with `rebuttals` -> `PASS`).
- Expected outcome: Workstream advances to `APPROVED`, chapter status becomes `accepted`, and `get_submission_status` returns round and ledger details.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-041-2 (REQ-041)

- Method: Exhaust `max_revise_rounds` with `BLOCK` verdicts and attempt an additional non-admin round in `tests/test_mcp_service_and_app.py`.
- Expected outcome: Submission transitions to `escalated_to_admin` with open `findingKey` entries and rejects further non-admin rounds with `409`.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-042-1 (REQ-042)

- Method: Run `seed_book` (`ch01` via `/api/v1/translate` + `TRANSLATION_REVIEW`) and final-chapter acceptance in `tests/test_mcp_service_and_app.py` against a bare git remote, verifying commits and compiled `.epub`/`.pdf`/`.docx` release artifacts.
- Expected outcome: `ch01` locks `glossary_sha` and unlocks `ch02..chNN`; final chapter commits `final.txt`, `translation.xml`, `book_record.json`, and `collab_ledger.json` and generates `.epub`, `.pdf`, and `.docx` deliverables.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-042-2 (REQ-042)

- Method: Test malformed `/api/v1/translate` response and active-claim `< fallback_hours` chapters in `tests/test_mcp_service_and_app.py`.
- Expected outcome: Failed translation/review leaves `ch02..chNN` locked (`seed_failed`) with zero partial git commits, and active/recent chapters are skipped by starved fallback.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-043-1 (REQ-043)

- Method: Run `uv run pytest tests/test_collab_worker_mcp.py tests/test_collab_worker.py` and plugin documentation conformance checks.
- Expected outcome: Both `--mcp-url` and `--gh-user` workflows pass, `plugin.json` is `3.0.0`, and all 4 role sections plus MCP docs exist in `README.md`.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-043-2 (REQ-043)

- Method: Invoke `collab_worker.py` with neither `--gh-user` nor `--mcp-url`, and against a `404` MCP response in `tests/test_collab_worker_mcp.py`.
- Expected outcome: CLI exits non-zero and prints structured JSON error output.
- Execution: [Run 1](#run-1--2026-11-19)

## Execution evidence

### Run 1 — 2026-11-19

- Checked: REQ-037..REQ-043 / AC-037-1..AC-043-2 (local Python 3.12 test suite + mutation-guard)
- Command: `uv run pytest` and `mutation_runner.py` on `scripts/mcp_server/auth.py` and `scripts/mcp_server/service.py` (exit code: `0`)
- Output excerpt:
  ```text
  318 passed in 11.4s
  [Mutation Guard] scripts/mcp_server/auth.py -> Mutation Score: 90.0% (9/10 killed)
  [Mutation Guard] scripts/mcp_server/service.py -> Mutation Score: 95.0% (19/20 killed)
  ```
- Result: AC-037-1 `PASS`, AC-037-2 `PASS`, AC-038-1 `PASS`, AC-038-2 `PASS`, AC-039-1 `PASS`, AC-039-2 `PASS`, AC-040-1 `PASS`, AC-040-2 `PASS`, AC-041-1 `PASS`, AC-041-2 `PASS`, AC-042-1 `PASS`, AC-042-2 `PASS`, AC-043-1 `PASS`, AC-043-2 `PASS` — Full unit/integration suite (`318 passed`) and mutation testing (`90.0%`–`95.0%`) passed cleanly.

## Real-world observations

- **2026-11-19 (Live Production Rollout on `arm10` K3s + Dual-Edge Caddy `amd10`/`gce10`, `ADR-0055` commit `249e381`)**:
  - `https://review.vinhthang.dev/llms.txt` confirmed live `v0.4.0` with `TRANSLATION_REVIEW`, `/api/v1/translate`, `vi-translation-fidelity`, `vi-literary-fluency`, and `vi-terminology-pronoun-hawk`.
  - `https://collab.vinhthang.dev/api/health`, `/llms.txt`, and `/` HTML Bookshelf returned `200 OK` with `happy-prince` (`5/5`) and `scandal-in-bohemia` (`3/3`) automatically bootstrapped from `vinhthang/collab-books`.
  - Unauthenticated (`ANONYMOUS`) Streamable HTTP MCP `POST https://collab.vinhthang.dev/mcp` (`initialize` and `tools/call` `list_books`) and `python3 scripts/collab_worker.py list-tasks --mcp-url https://collab.vinhthang.dev/mcp --role translator` succeeded with zero credentials.
  - Self-serve `POST /api/v1/agents/register` issued a `cbt_ak_*` key and authenticated subsequent `/api/v1/tasks` calls as `tier="AGENT_KEY"`.
  - Edge security verified: spoofed `X-Auth-Request-Email` on `/api/v1/admin/*` returned `403`, and unauthenticated `/admin/*` returned `302` to `https://auth.vinhthang.dev/oauth2/start`.
