# Design: Seed-to-Review Queue & Pre-Split Chapters / Initial `book_record` Proposals

Sources: [spec.md](./spec.md), [decisions.md](./decisions.md)

## Components

| Requirement / AC | Path and symbol | Responsibility / change |
| --- | --- | --- |
| REQ-051 / AC-051-1 | `scripts/mcp_server/db.py` (`CollabStore.init_db`, `upsert_book`, `_hydrate_book`) | Additive `books.proposed_chapters_json` (`DEFAULT '[]'`) in SQLite & PostgreSQL, preserved on conflict unless explicitly replaced, and hydrated as `proposed_chapters`. |
| REQ-050, REQ-051 / AC-050-1, AC-051-1 | `scripts/mcp_server/git_sync.py` (`CollabBooksGitSync.prepare_book_files`, `sync_accepted_chapter`) | Segment pre-split `source_text` via `BaseExtractor.segment_chapter_content` or write valid pre-split `source_xml` verbatim (skipping `extract_document()`), persist initial `book_record.json`, and record non-bot peer reviewers on `ch01` in `collab_ledger.json`. |
| REQ-050, REQ-051 / AC-050-1..AC-051-2 | `scripts/mcp_server/service.py` (`_normalize_proposed_chapters`, `_merge_initial_book_record`, `submit_book_proposal`, `list_books`, `get_book`, `list_tasks`, `get_submission_status`, `submit_translation`, `approve_book_intake`, `seed_book`) | Validate & scan pre-split chapters and initial `book_record`; strip `proposed_chapters` from public endpoints; expose `seeding` book seeds to `role="reviewer"`; enforce `_assert_book_work_access` in `get_submission_status`; hold `hold_for_review=True` admin seeds as `pending_admin_approval` (`409` on duplicate seed). |
| REQ-050, REQ-051 / AC-050-1..AC-051-2 | `scripts/mcp_server/app.py` (`MCP_SERVER_INSTRUCTIONS`, `_render_llms_txt`, `_render_admin_proposal_card`, `_render_admin_submission_card`, `_ADMIN_JS`, MCP tools & REST routes) | Bump version `"3.3.0"`, add `chapters`/`book_record` to `submit_book_proposal`, add `hold_for_review` to `approve_book_intake`/`seed_book`, add `Approve & Seed to Review` + `Retry Seed to Review` buttons, render pre-split badge/summary and escaped peer-review notes. |

## Interfaces and flow

1. **Pre-Split Proposal Flow (`submit_book_proposal`)**:
   - Local agent script prepares `chapters=[{chapter_id, title, source_text | source_xml}]` and `book_record={characters, terms, pronoun_pairs, style_notes?}`.
   - Server validates list bounds (`<= 200` chapters, `^ch\d{2,4}$` unique IDs), total UTF-8 size (`<= 5_242_880`), `parse_segments` parity on `source_xml` (`422` if invalid), and scans all text + JSON via `detect_prompt_injection` (`flagged_security` if tripped).
   - Stores `proposed_chapters` and merged `book_record` in DB; `prepare_book_files` consumes `proposed_chapters` on Admin intake approval instead of `extract_document()`.
2. **Seed-to-Review Flow (`approve_book_intake` / `seed_book` with `hold_for_review=True`)**:
   - Admin clicks `Approve & Seed to Review` -> book extracts chapters (`ch01` `open`, `ch02..` `locked_seed`), transitions book to `seeding`, calls `review-server` `/api/v1/translate`, evaluates `TRANSLATION_REVIEW`, and holds passing draft in `submission.status="pending_admin_approval"`.
   - `list_tasks(role="reviewer")` surfaces the `ch01` seed; external agents call `submit_review(...)`.
   - Admin inspects the seed draft + escaped peer review notes in `/admin` and clicks `Approve & Merge` (`manage_flagged_submission(action="approve")`), which calls `approve_workstream(override_escalated=False)`, finalizes `ch01`, writes peer reviewers into `collab_ledger.json`, unlocks `ch02..`, and transitions book to `active` (or `published`).

## Boundaries and recovery

- **XSS & Secret Safety**: Peer review `body_md`, reviewer handles, pre-split chapter titles, and initial glossary keys/values are HTML-escaped with `_esc` before rendering in `/admin`.
- **Restricted Book Isolation**: `get_submission_status` calls `_assert_book_work_access` when a book exists so restricted-book `ch01` seeds return `403` to unauthorized callers.

## Dependencies and unresolved design work

None.
