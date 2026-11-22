# Design: Collab Books MCP Reviewer Queue, Front/Back Matter Flow, & Context Bundle

Sources: [spec.md](./spec.md), [decisions.md](./decisions.md)

## Components

| Requirement / AC | Path and symbol | Responsibility / change |
| --- | --- | --- |
| REQ-064 / AC-064-1, AC-064-2 | `scripts/mcp_server/db.py` (`chapters.section_type`, `upsert_chapter`, `replace_unaccepted_chapters`, `update_chapter_section_type`), `scripts/mcp_server/service.py` (`VALID_SECTION_TYPES`, `_classify_chapter_section_type`, `_attach_chapter_section_types`, `_backfill_chapter_section_types`, `_persist_and_maybe_auto_accept_import`, `_finalize_accepted_chapter`, `submit_book_proposal`, `import_chapter_translations`) | Persist & backfill chapter `section_type` (`body`, `front_matter`, `back_matter`, `boilerplate`), classify deterministically, expose in `get_book` / `get_chapter`, and auto-accept `"boilerplate"` during trusted imports (`approval_mode != "manual"`). |
| REQ-065 / AC-065-1, AC-065-2 | `scripts/mcp_server/service.py` (`_summarize_submission_task`, `list_tasks`, `query_submissions`, `query_submissions_page`) | Return compact summaries with full `submission_id == id` and zero bulky `translation_xml`/`final_txt`, sort `(status_rank, section_rank, book_slug, chapter_num)` (`body` first ascending), paginate `list_tasks` (`limit=200`, max `500`, `offset=0`, `include_matter`, `section_type`) and `query_submissions` (`limit=100`, max `500`, `offset`, `include_xml`). |
| REQ-066 / AC-066-1, AC-066-2 | `scripts/mcp_server/service.py` (`_sanitize_submission_public`, `_persist_imported_submission`, `_merge_proposals_into_record`, `get_submission_status`, `update_book_record`) | Strip redundant `translation_xml` in `rounds_history`, expose `submission_id`, populate unified `context` + top-level `book_record`/`brief` when `include_context=True`, allow post-lock `agent_admin` `brief` updates, and sync `additions` across `terms`/`term_entries` and `characters`/`names`. |
| REQ-067 / AC-067-1, AC-067-2 | `scripts/mcp_server/app.py`, `scripts/collab_worker.py`, `plugin.json`, `.github/workflows/ci-cd.yml`, `skills/collab-translate-book/SKILL.md`, `agents/collab_reviewer.md`, `agents/collab_admin.md` | Bump to MCP `"4.3.0"` (`v0.5.0`), expose pagination/matter/context/boilerplate params across MCP & REST, render full 36-char UUIDs + `section_type` badges in `/admin` (verified with `node --check`), add `get-submission` & pagination flags to `collab_worker.py`. |

## Interfaces and flow

1. **Deterministic Section Tagging & Boilerplate Auto-Accept**:
   - `_classify_chapter_section_type(chapter_id, title, source_xml)`: validates explicit `section_type` (`400` if outside `VALID_SECTION_TYPES`). If omitted/`"body"` default, matches Cover (`![Cover...` / `Bìa sách`), Title Page, S&S ebook signup (`Thank you for downloading` / `We hope you enjoyed reading`), Copyright/Avid Reader Press (`ISBN` / `Library of Congress`), `Also From`, and `<= 40`-word `Part` divider headers -> `"boilerplate"`; `With Appreciation` / `How to Read This Book` / `Why I'm Sharing` / `Dedication` (standalone) / `The Past Is Prologue` -> `"front_matter"`; `About the Author` / `Acknowledgments` / `Bibliography` / `Index` / `Image Descriptions` -> `"back_matter"`; everything else -> `"body"`.
   - When `auto_approve_boilerplate=True` and `book["approval_mode"] != "manual"`, `submit_book_proposal` and `import_chapter_translations` persist validated `"boilerplate"` chapters directly as `status="accepted"` (with `reviewer_handles=["auto-boilerplate"]`) via `_finalize_accepted_chapter`.
2. **Compact Paginated Queue & One-Call Context**:
   - `list_tasks` builds `_summarize_submission_task(sub, book, ch)` without `translation_xml`/`final_txt`, sorts by `(status_rank, section_rank, book_slug, chapter_num)`, slices `[offset:offset+limit]`, and attaches `peer_review_total`, `peer_review_limit`, `peer_review_offset`, `peer_review_has_more`.
   - `get_submission_status(..., include_context=True)` returns `context`, `book_record`, and `brief` alongside `book_context` and `chapter_context`.

## Boundaries and recovery

- `update_book_record` keeps `409` on post-lock `book_record={...}` full overwrite by `agent_admin`, while allowing `brief={...}` and additive `additions={"proposals": [...]}`.
- `/admin` HTML escapes all dynamic `section_type`, book, and submission fields, and `query_submissions` retains `list[dict]` return compatibility for direct Python callers while `query_submissions_page` serves the paginated envelope.

## Dependencies and unresolved design work

None.
