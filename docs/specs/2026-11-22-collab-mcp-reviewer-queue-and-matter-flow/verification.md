# Verification: Collab Books MCP Reviewer Queue, Front/Back Matter Flow, & Context Bundle

Governing specification: [spec.md](./spec.md)

## Planned checks

### AC-064-1 (REQ-064)

- Method: Run `pytest tests/test_mcp_reviewer_queue_and_matter.py -k req_064` verifying deterministic `section_type` classification, startup DB migration/backfill, and `"boilerplate"` auto-acceptance on trusted imports (`auto_approve_boilerplate=True`).
- Expected outcome: Cover, Title Page, ebook promo, Copyright Colophon, Also From, and short `Part` dividers classify as `"boilerplate"` and auto-accept when `approval_mode != "manual"`; editorial front/back matter (`With Appreciation`, `How to Read`, `About the Author`, `Image Descriptions`) and `body` remain `"pending_review"`.
- Execution: [Run 1](#run-1--2026-11-22)

### AC-064-2 (REQ-064)

- Method: Run `pytest tests/test_mcp_reviewer_queue_and_matter.py -k req_064` verifying invalid `section_type` error and `Part I: Where I'm Coming From` prose classification.
- Expected outcome: Invalid `section_type` raises `400`; `>40`-word `Part I` prose classifies as `"body"` rather than `"boilerplate"`.
- Execution: [Run 1](#run-1--2026-11-22)

### AC-065-1 (REQ-065)

- Method: Run `pytest tests/test_mcp_reviewer_queue_and_matter.py -k req_065` with `90` pending review submissions across books.
- Expected outcome: Single default `list_tasks(role="reviewer")` returns all `90` compact items (`peer_review_total == 90`, `limit == 200`, `has_more is False`) with full `submission_id == id`, zero `translation_xml`/`final_txt`, `body` sorted ahead of `front_matter`/`back_matter` in ascending chapter order; pagination and `include_matter=False` work accurately.
- Execution: [Run 1](#run-1--2026-11-22)

### AC-065-2 (REQ-065)

- Method: Run `pytest tests/test_mcp_reviewer_queue_and_matter.py -k req_065` testing invalid `section_type` and restricted-book caller visibility.
- Expected outcome: Invalid `section_type` raises `400`; unauthorized callers never see restricted submissions or inflated counts.
- Execution: [Run 1](#run-1--2026-11-22)

### AC-066-1 (REQ-066)

- Method: Run `pytest tests/test_mcp_reviewer_queue_and_matter.py -k req_066` inspecting `get_submission_status` and post-lock `update_book_record`.
- Expected outcome: `include_context=True` populates `context` (`book_record`, `brief`, `glossary_sha`, `source_xml`, `section_type`, `neighbors`), top-level `book_record`/`brief`, and `book_context`/`chapter_context`; `rounds_history` omits duplicate `translation_xml`; post-lock `agent_admin` `update_book_record(brief=..., additions=...)` updates `brief`, `terms`/`term_entries`, and `characters`/`names`.
- Execution: [Run 1](#run-1--2026-11-22)

### AC-066-2 (REQ-066)

- Method: Run `pytest tests/test_mcp_reviewer_queue_and_matter.py -k req_066` testing `include_context=False` and post-lock destructive `book_record` overwrite.
- Expected outcome: `include_context=False` omits context keys; post-lock `agent_admin` `update_book_record(book_record={...})` returns `409`.
- Execution: [Run 1](#run-1--2026-11-22)

### AC-067-1 (REQ-067)

- Method: Run `pytest tests/test_mcp_reviewer_queue_and_matter.py -k req_067` + `node --check` on extracted `_ADMIN_JS` + full `pytest`.
- Expected outcome: MCP/REST/`plugin.json`/`/api/health`/`/llms.txt` report `"4.3.0"`; `/admin` renders full 36-char UUIDs and `section_type` badges cleanly (`node --check` exit `0`); `collab_worker.py` `get-submission` and `list-tasks` pagination succeed; full suite passes.
- Execution: [Run 1](#run-1--2026-11-22)

### AC-067-2 (REQ-067)

- Method: Run `pytest tests/test_mcp_reviewer_queue_and_matter.py -k req_067` (XSS escaping) + `pytest tests/test_option_a_hygiene.py`.
- Expected outcome: Dynamic metadata is escaped in `/admin` and `test_option_a_hygiene.py` passes cleanly.
- Execution: [Run 1](#run-1--2026-11-22)

## Execution evidence

### Run 1 — 2026-11-22

- Checked: REQ-064..REQ-067 / AC-064-1..AC-067-2
- Command: `uv run --directory /Users/thanghoang/github/collab-book-translate pytest`, `pytest tests/test_option_a_hygiene.py`, `mutation_runner.py` (`service.py`, `app.py`), `prepare_code_review.py` & `git diff --check` (exit code: `0`)
- Output excerpt:
  ```text
  4 passed, 6 warnings in 3.10s (test_mcp_reviewer_queue_and_matter.py, incl. node --check exit 0)
  350 passed, 6 warnings in 26.08s
  2 passed in 0.22s (test_option_a_hygiene.py)
  Mutation Score (service.py): 80.0% (12/15 killed)
  Mutation Score (app.py): 93.3% (14/15 killed)
  Code Review: Approved (0 P0, 0 P1), git diff --check exit 0
  ```
- Result: AC-064-1 `PASS`, AC-064-2 `PASS`, AC-065-1 `PASS`, AC-065-2 `PASS`, AC-066-1 `PASS`, AC-066-2 `PASS`, AC-067-1 `PASS`, AC-067-2 `PASS` — All automated tests, JS syntax checks, hygiene scans, mutation gates, and single-agent code review passed.

## Real-world observations

No real-world confirmation has been recorded.
