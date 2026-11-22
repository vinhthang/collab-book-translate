# Collab Books MCP Reviewer Queue, Front/Back Matter Flow, & Context Bundle (`collab-mcp:v0.5.0`)

- Feature ID: 2026-11-22-collab-mcp-reviewer-queue-and-matter-flow
- Type: enhancement
- Status: In-Testing
- First entered In-Testing: 2026-11-22
- Related specifications: Implemented: Changes [2026-11-22-collab-mcp-trusted-intake-and-import](../2026-11-22-collab-mcp-trusted-intake-and-import/spec.md)

## Problem and source intent

First live peer reviews on `https://collab.vinhthang.dev` (`muse-b4e3` on `the-changing-world-order` across all 87 Ray Dalio chapters) surfaced six reviewer workflow friction points:
1. **50-item reviewer queue cap & XML bloat**: `list_tasks` caps `peer_review_candidates` at `50` globally when `book_slug` is omitted, hiding `37` of the `87` live chapters, and serializes full `translation_xml`/`final_txt` blobs (`~5.1 MB` total response) without pagination or natural chapter order.
2. **Front/back matter and boilerplate mixed with body chapters**: Low-value front/back boilerplate (cover image, ebook promo, title page, copyright colophon, "Also From", 1-line `Part` dividers) sits un-tagged ahead of body chapters in `pending_review`.
3. **8-char truncated IDs in `/admin` and ambiguous candidate keys**: `list_tasks` entries only had `"id"` (and `/admin` cards truncated UUIDs via `esc_id[:8]`), instead of exposing `"submission_id"` cleanly alongside full IDs everywhere.
4. **Inconsistent `context` key in `get_submission_status`**: Reviewers expecting `res["context"]` (containing `book_record`, `brief`, `glossary_sha`, `source_xml`, `neighbors`) alongside top-level `book_record`/`brief` had to make a separate `get_book` call, while duplicate `translation_xml` inside `rounds_history` inflated payloads.
5. **Unpinned `"the US"` -> `"Hoa Kỳ"` & unidirectional `additions` sync**: Ray Dalio `book_record`s mapped `"United States"` -> `"Hoa Kỳ"` only in `names`, omitting `"the US"` / `"US"` / `"U.S."`, and `_merge_proposals_into_record` updated only flat `terms`/`characters` dicts without synchronizing structured `term_entries`/`names`.
6. **`brief` immutability after `glossary_sha` lock**: `update_book_record` rejected `agent_admin` `brief` style clarifications (such as confirming the first-mention English-in-parentheses house style) with `409` once `glossary_sha` was locked.

Authorizing user intent (2026-11-22, verbatim prompt + resolved choices `A1`, `A2`):
- Prompt: *"fix these first, A few flow improvements from doing this first one: - The reviewer queue caps at 50... - Front/back matter sits in the same queue as real chapters... - list_tasks only shows the first 8 chars of the submission ID... - get_submission_status with include_context came back empty... - Add 'the US' → 'Hoa Kỳ' to the book_record... - Confirm the English-in-parentheses style in the brief..."*
- **Q1 (`A1`)**: Tag `section_type` (`front_matter` / `body` / `back_matter` / `boilerplate`), sort `body` first, AND auto-accept pure boilerplate (`Cover`, `Title Page`, ebook promo, `Copyright`, `Also From`, `<= 40`-word `Part` dividers).
- **Q2 (`A2`)**: Pin `"the US"` / `"US"` -> `"Hoa Kỳ"` in `book_record` + `brief` AND normalize standalone country `"Mỹ"` -> `"Hoa Kỳ"` across unaccepted Dalio chapters locally + re-import.

## Repository grounding

Inspected at `/Users/thanghoang/github/collab-book-translate` (`36e07ba`):
- `scripts/mcp_server/db.py:61-78,598-678`: `chapters` table lacks `section_type`.
- `scripts/mcp_server/service.py:388-408,698-823,1132-1708,2233-2325`: `list_tasks` hardcodes `rev_cap = 200 if book_slug else 50`, returns unsummarized submission dicts (5.1 MB), sorts only by status rank (yielding reverse `created_at` `ch41..ch01` order); `get_submission_status` returns `book_context`/`chapter_context` without `context` or top-level `book_record`/`brief`; `_persist_imported_submission` stores redundant `translation_xml` inside `rounds_history`; `update_book_record` blocks `brief` updates when `glossary_sha` is locked; `_merge_proposals_into_record` ignores `names` and `term_entries`.
- `scripts/mcp_server/app.py:885,942,1431-1600,2096-2177`: renders `{esc_id[:8]}` in `/admin` cards and advertises `version="4.2.0"` (`29` tools).

## Non-goals

- **No silent auto-accept of substantive front/back matter**: `Introduction`, `Dedication & Introduction`, `With Appreciation`, `How to Read This Book`, `Why I'm Sharing This Book`, `About the Author`, `Acknowledgments`, `Conclusion`, `Appendix`, and `Image Descriptions` remain in peer review (`front_matter` / `back_matter`), never auto-accepted as `boilerplate`.
- **Zero relaxation of `book_record` post-lock immutability for non-ADMIN**: `agent_admin` still receives `409` on full `book_record={...}` replacement after `glossary_sha` is locked.
- **Zero weakening of `manual` gate**: When `approval_mode == "manual"`, `boilerplate` chapters stay `pending_admin_approval` rather than auto-accepting.

## Requirements

### REQ-064: Chapter `section_type` Classification, Persistence Backfill, & Trusted Boilerplate Auto-Accept

- Changes: [2026-11-22-collab-mcp-trusted-intake-and-import/REQ-061](../2026-11-22-collab-mcp-trusted-intake-and-import/spec.md#req-061-trusted-submitter-auto-approve--curated-glossary-chapter-unlock), [2026-11-22-collab-mcp-trusted-intake-and-import/REQ-062](../2026-11-22-collab-mcp-trusted-intake-and-import/spec.md#req-062-batch-pre-translated-chapter-import-import_chapter_translations--reviewer-continuity)
- Source intent: Verbatim request (*"mark them as front/back matter so reviewers can skip to content, or auto-approve them"*) and Q1 (`A1`).
- Previous contract: `chapters` stored no `section_type`; `submit_book_proposal` and `import_chapter_translations` queued every imported chapter (including Cover, Title Page, ebook promo, Copyright Colophon, Also From, and 1-line Part dividers) identically into `pending_review`.
- Statement: `CollabStore` SHALL persist `chapters.section_type` (`"body"`, `"front_matter"`, `"back_matter"`, `"boilerplate"`, default `"body"`) across SQLite and PostgreSQL migrations. `CollabMcpService` SHALL deterministically classify missing/default `section_type` via `_classify_chapter_section_type(chapter_id, title, source_xml)` (and validate explicit overrides in `VALID_SECTION_TYPES`, raising `400` otherwise), backfill existing chapter rows at startup, and expose `section_type` in `get_book` and `get_chapter`. WHILE `approval_mode != "manual"` and `auto_approve_boilerplate=True` (default), trusted `submit_book_proposal` and `import_chapter_translations` SHALL auto-accept `"boilerplate"` chapters into `status="accepted"` (recording `reviewer_handles=["auto-boilerplate"]`, upgrading any existing unaccepted same-author draft on re-import), while keeping `"front_matter"`, `"back_matter"`, and `"body"` chapters in `pending_review`.
- AC-064-1 — Success: Pre-existing and newly proposed/imported chapters classify accurately (`Cover`, `Title Page`, `Simon & Schuster ebook`, `Copyright / Avid Reader Press`, `Also From`, `<= 40`-word `Part` divider -> `"boilerplate"`; `With Appreciation` / `How to Read` -> `"front_matter"`; `About the Author` / `Image Descriptions` -> `"back_matter"`; `Introduction` / `Chapter N` / `Appendix` -> `"body"`), and trusted imports with `auto_approve_boilerplate=True` transition `"boilerplate"` chapters directly to `"accepted"` (`"pending_review"` when `auto_approve_boilerplate=False` or `approval_mode="manual"`).
- AC-064-2 — Boundary/failure: Passing an unknown `section_type` returns `400`; `Part I: Where I'm Coming From` (`> 40` words of prose) is classified `"body"`, never `"boilerplate"`.

### REQ-065: Paginated Compact Reviewer Queue (`limit=200`), Full UUID `submission_id`, & Body-First Natural Sort

- Changes: [2026-11-22-collab-mcp-trusted-intake-and-import/REQ-062](../2026-11-22-collab-mcp-trusted-intake-and-import/spec.md#req-062-batch-pre-translated-chapter-import-import_chapter_translations--reviewer-continuity)
- Source intent: Verbatim request (*"Raise it or paginate"*, *"Return the full ID in the list"*, *"mark them as front/back matter so reviewers can skip to content"*).
- Previous contract: `list_tasks` sliced `peer_review_candidates[:50]` globally (`200` with `book_slug`), serialized full `translation_xml`/`final_txt`, omitted `submission_id` and pagination metadata, and sorted submissions in reverse creation order without distinguishing matter vs body.
- Statement: `list_tasks(ctx, *, role="all", book_slug=None, section_type=None, include_matter=True, limit=200, offset=0)` SHALL return compact candidate summaries (`id`, `submission_id`, `book_slug`, `book_title`, `chapter_id`, `chapter_num`, `chapter_title`, `section_type`, `author_actor_id`, `author_handle`, `engine_model`, `status`, `round_number`, `max_rounds`, `segment_count`, `judge_summary`, `created_at`, `updated_at`, omitting `translation_xml` and `final_txt`), sorted by `(status_rank, section_rank, book_slug, chapter_num)` where `body` (`0`) precedes `front_matter` (`1`) and `back_matter` (`2`), sliced cleanly with `limit` (`1..500`, default `200`) and `offset` (`>= 0`), and accompanied by `peer_review_total`, `peer_review_limit`, `peer_review_offset`, and `peer_review_has_more`. `revisions_needed` items SHALL likewise include full 36-char `submission_id` and `section_type`. `query_submissions` and REST/MCP `query_submissions` SHALL support `limit` (`1..500`, default `100`), `offset`, `section_type`, `include_xml`, and pagination totals (`total`, `limit`, `offset`, `has_more`).
- AC-065-1 — Success: With `>= 90` pending review submissions across books, a single default `list_tasks(role="reviewer")` call returns all `90` candidates in natural `ch01..chNN` order with `body` before `front_matter`/`back_matter`, each carrying full 36-char `submission_id == id` and zero bulky `translation_xml`, while `limit=40, offset=40` and `include_matter=False` / `section_type="body"` paginate and filter accurately.
- AC-065-2 — Boundary/failure: Invalid `section_type` or negative bounds are normalized/rejected (`400` on invalid `section_type`), and restricted-book submissions remain excluded for unauthorized callers across counts and pages.

### REQ-066: One-Call `get_submission_status` Context Bundle, Deduplicated `rounds_history`, & Post-Lock `brief` / Bidirectional `additions` Sync

- Changes: [2026-11-22-collab-mcp-trusted-intake-and-import/REQ-061](../2026-11-22-collab-mcp-trusted-intake-and-import/spec.md#req-061-trusted-submitter-auto-approve--curated-glossary-chapter-unlock), [2026-11-22-collab-mcp-trusted-intake-and-import/REQ-062](../2026-11-22-collab-mcp-trusted-intake-and-import/spec.md#req-062-batch-pre-translated-chapter-import-import_chapter_translations--reviewer-continuity)
- Source intent: Verbatim request (*"get_submission_status with include_context came back empty"*, *"Add 'the US' → 'Hoa Kỳ' to the book_record"*, *"Confirm the English-in-parentheses style in the brief"*).
- Previous contract: `get_submission_status(include_context=True)` populated `book_context` and `chapter_context` without `context`, `book_record`, or `brief` keys and duplicated full `translation_xml` inside `rounds_history`; `update_book_record` rejected `agent_admin` `brief` updates after `glossary_sha` lock; `_merge_proposals_into_record` omitted `names` and `term_entries`.
- Statement: WHEN `include_context=True` on `get_submission_status`, THE server SHALL return a non-empty `"context"` dict (`book_slug`, `book_title`, `author`, `source_lang`, `target_lang`, `approval_mode`, `glossary_sha`, `brief`, `book_record`, `chapter_id`, `chapter_num`, `chapter_title`, `section_type`, `chapter_status`, `source_xml`, `neighbors`) AND top-level `"book_record"` and `"brief"` alongside backward-compatible `"book_context"` and `"chapter_context"`, include `"submission_id"` on `submission`, and omit duplicate `translation_xml` blobs inside sanitized `rounds_history` entries. `update_book_record` SHALL allow `agent_admin` callers to update `brief` after `glossary_sha` lock (preserving `409` on full `book_record` replacement) and `_merge_proposals_into_record` SHALL synchronize additions idempotently into both flat dicts (`characters`, `terms`) and structured arrays (`names`, `term_entries`).
- AC-066-1 — Success: `get_submission_status(submission_id, include_context=True)` returns populated `context.book_record`, `context.brief`, `context.source_xml`, `context.section_type`, top-level `book_record`/`brief`, and `rounds_history` free of duplicate `translation_xml`; post-lock `agent_admin` `update_book_record(brief=..., additions=...)` updates `brief`, `terms`/`term_entries`, and `characters`/`names` while keeping `glossary_sha` unchanged.
- AC-066-2 — Boundary/failure: `get_submission_status(..., include_context=False)` omits `context`, `book_context`, `chapter_context`, `book_record`, and `brief`; post-lock `agent_admin` `update_book_record(book_record={...})` still returns `409`.

### REQ-067: MCP `v4.3.0` Surface, REST Pagination, Full-ID `/admin` Cards, & `collab_worker.py` CLI Parity

- Changes: [2026-11-22-collab-mcp-trusted-intake-and-import/REQ-063](../2026-11-22-collab-mcp-trusted-intake-and-import/spec.md#req-063-mcp-v420-surface-rest--admin-open-all-controls--worker-cli-parity)
- Source intent: Expose reviewer queue pagination, matter controls, full UUIDs, and single-call context uniformly across MCP, REST, `/admin`, `collab_worker.py`, and agent docs.
- Previous contract: MCP/REST advertised `"4.2.0"` without `list_tasks` pagination/matter params or `auto_approve_boilerplate`; `/admin` cards truncated UUIDs via `esc_id[:8]`; `collab_worker.py` lacked `get-submission` and `list-tasks` pagination/matter flags.
- Statement: `scripts/mcp_server/app.py`, `plugin.json`, `.github/workflows/ci-cd.yml` (`v0.5.0`), `skills/collab-translate-book/SKILL.md`, `agents/collab_reviewer.md`, and `agents/collab_admin.md` SHALL advance to version `"4.3.0"`, expose full 36-char submission and bug IDs + `section_type` badges in `/admin` HTML (passing `node --check`), expose `section_type` / `include_matter` / `limit` / `offset` / `auto_approve_boilerplate` across MCP and REST, add `get-submission` and pagination/matter flags to `scripts/collab_worker.py`, and satisfy `test_option_a_hygiene.py`.
- AC-067-1 — Success: `/api/health`, `/llms.txt`, `plugin.json`, and MCP `initialize` report `"4.3.0"`; `/admin` renders full 36-char UUIDs without `[:8]` truncation and passes `node --check`; REST/MCP/CLI `list-tasks` pagination, `query-submissions` pagination, and `get-submission` work end-to-end.
- AC-067-2 — Boundary/failure: `/admin` HTML escapes dynamic `section_type` and submission metadata cleanly against XSS, and `tests/test_option_a_hygiene.py` passes with zero tracked corpus files or forbidden tokens.

## Assumptions and owner-deferred questions

- Backward compatibility of internal service method signatures: `CollabMcpService.query_submissions` continues returning `list[dict[str, Any]]` for direct Python callers while `CollabMcpService.query_submissions_page` returns the paginated envelope used by MCP/REST (`{"submissions": ..., "total": ..., "limit": ..., "offset": ..., "has_more": ...}`).

## Approvals and lifecycle history

- `2026-11-22`: `Planning` — Authored from user review feedback and resolved Q1/Q2 selections (`A1`, `A2`), executing under approved execution plan.
- `2026-11-22`: `Planning -> In-Progress` — Gate 1 and Gate 2 passed (`validate_specs.py --all` -> `11` packages validated); starting strict Red -> Green -> Refactor TDD implementation.
- `2026-11-22`: `In-Progress -> In-Testing` — Completed Tasks 1–3 (`350 passed, 0 failed` full suite, `test_option_a_hygiene.py` `2 passed`, `mutation-guard` `80.0%` on `service.py` and `93.3%` on `app.py`, `node --check` exit `0`, single-agent `code-review` `Approved` `0` P0/P1). See [Run 1](./verification.md#run-1--2026-11-22).
