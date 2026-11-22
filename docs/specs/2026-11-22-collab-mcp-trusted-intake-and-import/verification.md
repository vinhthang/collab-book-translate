# Verification: Collab Books MCP Trusted Submitter Auto-Approve & Batch Import

Governing specification: [spec.md](./spec.md)

## Planned checks

### AC-061-1 (REQ-061)

- Method: Pytest unit/integration test `test_req_061_trusted_submit_proposal_auto_approve_and_boundaries` on `CollabMcpService`.
- Expected outcome: Trusted `agent_admin` / `ADMIN` `submit_book_proposal` bypasses `pending_approval` (raw text -> `approved_pending_parse`; curated pre-split -> `active` + locked `glossary_sha` + `pending_review` drafts; uncurated pre-split -> `seeding`); `submit_book_parse` on caller's `pending_approval` and `approve_book_intake(..., unlock_all_chapters=True)` unlock all chapters.
- Execution: [Run 1](#run-1--2026-11-22)

### AC-061-2 (REQ-061)

- Method: Pytest boundary tests inside `test_req_061_trusted_submit_proposal_auto_approve_and_boundaries`.
- Expected outcome: Prompt injection in trusted `submit_book_proposal` quarantines to `flagged_security`; `ANONYMOUS`/`contributor` stays `pending_approval` and gets `403` when supplying `translation_xml`; `agent_admin` gets `403` parsing another caller's `pending_approval`; out-of-order `ch02` + `ch01` acceptance preserves locked `glossary_sha` and omits `"review-vinhthang-dev"` when `workstream_id == ""`.
- Execution: [Run 1](#run-1--2026-11-22)

### AC-062-1 (REQ-062)

- Method: Pytest integration test `test_req_062_batch_import_chapter_translations_and_continuity`.
- Expected outcome: `import_chapter_translations` auto-unpacks `approved_pending_parse` and caller's `pending_approval` pre-split books, locks `glossary_sha`, opens chapters, creates/updates `pending_review` submissions, returns `>25` `peer_review_candidates` for `book_slug`, and populates `neighbors` from `pending_review` drafts.
- Execution: [Run 1](#run-1--2026-11-22)

### AC-062-2 (REQ-062)

- Method: Pytest negative/atomicity tests inside `test_req_062_batch_import_chapter_translations_and_continuity`.
- Expected outcome: Rejects `403`, `400`, `413`, `409`, and fails atomically (`422` with `defects`) writing zero chapters when any batch item violates XML parity, byte-invariance, `<hanviet>`, Vietnamese detection, or prompt-injection checks.
- Execution: [Run 1](#run-1--2026-11-22)

### AC-063-1 (REQ-063)

- Method: Pytest Starlette/MCP E2E test `test_req_063_mcp_v420_rest_admin_ui_and_worker_cli` plus `node --check` on extracted `_ADMIN_JS`.
- Expected outcome: MCP `initialize` / `/api/health` / `plugin.json` report `4.2.0`; `tools/list` returns `29` tools; REST `/api/v1/books/{slug}/import-translations`, `/admin` `Approve & Open All Chapters`, and `collab_worker.py import-translations` pass.
- Execution: [Run 1](#run-1--2026-11-22)

### AC-063-2 (REQ-063)

- Method: Pytest HTML XSS escape checks in `test_req_063_*` plus `tests/test_option_a_hygiene.py`.
- Expected outcome: `/admin` HTML-escapes dynamic fields cleanly and `test_option_a_hygiene.py` passes with zero violations.
- Execution: [Run 1](#run-1--2026-11-22)

## Execution evidence

### Run 1 — 2026-11-22

- Checked: REQ-061..REQ-063 / AC-061-1..AC-063-2 (working tree post-mutation sanity check)
- Command: `uv --directory /Users/thanghoang/github/collab-book-translate run pytest -q`, `mutation_runner.py` (`service.py`, `app.py`), `prepare_code_review.py` & `git diff --check` (exit code: `0`)
- Output excerpt:
  ```text
  346 passed, 6 warnings in 29.91s
  5 passed, 6 warnings in 2.56s (test_mcp_trusted_intake_and_import.py + test_option_a_hygiene.py)
  Mutation Score (service.py): 100.0% (15/15 killed)
  Mutation Score (app.py): 80.0% (12/15 killed)
  Code Review: Approved (0 P0, 0 P1), git diff --check exit 0
  ```
- Result: AC-061-1 `PASS`, AC-061-2 `PASS`, AC-062-1 `PASS`, AC-062-2 `PASS`, AC-063-1 `PASS`, AC-063-2 `PASS` — All automated tests, JS syntax checks, hygiene scans, mutation gates, and single-agent code review passed.

## Real-world observations

No real-world confirmation has been recorded.
