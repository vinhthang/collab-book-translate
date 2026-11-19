# Verification: Seed-to-Review Queue & Pre-Split Chapters / Initial `book_record` Proposals

Governing specification: [spec.md](./spec.md)

## Planned checks

### AC-050-1 (REQ-050)

- Method: Run `uv run pytest tests/test_mcp_seed_review_and_presplit.py -k ac_050` testing `approve_book_intake(auto_seed=True, hold_for_review=True)` -> `seeding` + `pending_admin_approval`, `list_tasks(role="reviewer")` discovery, external agent `submit_review` (including `<script>` payload escaping on `/admin`), and Admin `approve` unlocking `ch02` + persisting peer reviewer handle in `collab_ledger.json`.
- Expected outcome: All assertions pass cleanly.
- Execution: [Run 1](#run-1--2026-11-20)

### AC-050-2 (REQ-050)

- Method: Verify `409` on duplicate `seed_book(..., hold_for_review=True)` while a seed is `pending_admin_approval`, `403` / hidden task for outsiders on a `restricted` book, and non-admin `submit_translation` ignoring `hold_for_review=True` on an `auto` book.
- Expected outcome: Exact status codes and isolation enforced.
- Execution: [Run 1](#run-1--2026-11-20)

### AC-051-1 (REQ-051)

- Method: Run `uv run pytest tests/test_mcp_seed_review_and_presplit.py -k ac_051` testing `POST /api/v1/books/propose` with `source_text=""`, mixed `chapters` (`source_text` + `source_xml`), and initial `book_record`, verifying `/admin` summary display, verbatim/segmented extraction on `approve_book_intake`, `book_record.json` preservation, and zero `proposed_chapters` leakage on public endpoints.
- Expected outcome: All assertions pass cleanly.
- Execution: [Run 1](#run-1--2026-11-20)

### AC-051-2 (REQ-051)

- Method: Verify `400` on empty source+chapters, invalid/duplicate `chapter_id`, `> 200` chapters, or non-dict `book_record`; `413` on `> 5_242_880` bytes; `422` on malformed `source_xml`; and `flagged_security` quarantine on injected chapter/glossary payloads.
- Expected outcome: Every validation boundary passes cleanly.
- Execution: [Run 1](#run-1--2026-11-20)

## Execution evidence

### Run 1 — 2026-11-20

- Checked: REQ-050..REQ-051 / AC-050-1..AC-051-2 (Python 3.12 full test suite + `mutation-guard` + `code-review`)
- Command: `uv run pytest -q`, `mutation_runner.py` on `scripts/mcp_server/git_sync.py` & `scripts/mcp_server/service.py`, and `prepare_code_review.py` (exit code: `0`)
- Output excerpt:
  ```text
  329 passed, 6 warnings in 16.29s
  [Mutation Guard] scripts/mcp_server/git_sync.py -> Mutation Score: 100.0% (6/6 killed)
  [Mutation Guard] scripts/mcp_server/service.py -> Mutation Score: 90.0% (27/30 killed)
  ```
- Result: AC-050-1 `PASS`, AC-050-2 `PASS`, AC-051-1 `PASS`, AC-051-2 `PASS` — Full suite (`329 passed`), mutation testing (`90.0%`–`100.0%`), and single-agent `code-review` (`Approved`, `0 P0`, `0 P1`) all passed.
