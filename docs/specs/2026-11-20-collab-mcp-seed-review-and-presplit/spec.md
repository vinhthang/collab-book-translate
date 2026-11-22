# Seed-to-Review Queue & Pre-Split Chapters / Initial `book_record` Proposals (`collab-mcp:v0.1.3`)

- Feature ID: 2026-11-20-collab-mcp-seed-review-and-presplit
- Type: enhancement
- Status: In-Testing
- First entered In-Testing: 2026-11-20
- Related specifications: Implemented: Extends [2026-11-19-collab-mcp-and-books-split](../2026-11-19-collab-mcp-and-books-split/spec.md), Extends [2026-11-19-collab-mcp-admin-approvals](../2026-11-19-collab-mcp-admin-approvals/spec.md)

## Problem and source intent

Two workflow gaps remain in `collab-mcp:v0.1.2`:
1. Clicking **Approve & Seed ch01** auto-accepts the `ch01` seed immediately upon `review.vinhthang.dev` `PASS`, locking `glossary_sha`, pushing to Git, and unlocking `ch02..chNN` without allowing external peer agents or Admin to review the opening chapter voice/glossary first.
2. Books with irregular formatting, headers/footers, or custom bilingual/glossary tables benefit when an agent runs its own local Python preprocessing script — yet `submit_book_proposal` only accepts raw monolithic `source_text`, forcing generic `extract_document()` splitting and offering no initial `book_record` intake.

Authorizing user intent (2026-11-20, verbatim user prompts):
1. *"but sometime it is needed for a agent to review the result of the seed result, and sometime agent make a python script for that book too, what do you think"*
2. *"yes proceed"* (after proposal of Seed-to-Review Queue and Pre-Split Book Proposals with zero server-side code execution).

## Repository grounding

Inspected against `collab-book-translate` at commit `bb08997`:
- `scripts/mcp_server/db.py:33-56` (`books` table) lacks `proposed_chapters_json`.
- `scripts/mcp_server/git_sync.py:73-130` (`prepare_book_files`) unconditionally invokes `extract_document()` and initializes `book_record.json` with empty `characters`/`terms`/`pronoun_pairs`; `sync_accepted_chapter` (`git_sync.py:189`) sets `"reviewer_logins": []` for all `ch01` entries even when peer agents reviewed a held seed.
- `scripts/mcp_server/service.py:293-330` (`list_tasks`) skips books where `status == "seeding"`, hiding held `ch01` seeds from reviewer agents; `submit_book_proposal` (`service.py:402-473`) requires non-empty `source_text` and does not accept `chapters` or `book_record`; `seed_book` (`service.py:1077-1125`) expects `status == "accepted"` unconditionally.
- `scripts/mcp_server/service.py:857-866` (`get_submission_status`) does not check `_assert_book_work_access` for restricted books.
- `scripts/mcp_server/app.py` (`_render_admin_proposal_card`, `_render_admin_submission_card`) lacks `Approve & Seed to Review` (`hold_for_review=True`), pre-split chapter preview, and expandable peer-review critique notes on submission cards.

## Non-goals

- **Zero server-side Python execution**: `collab-mcp` MUST NEVER execute agent-submitted Python code; agents run preprocessing locally and submit structured JSON/XML data only.
- **No breaking change to existing `auto_seed` / `source_text` callers**: `hold_for_review` defaults to `False` and `chapters`/`book_record` are optional.

## Requirements

### REQ-050: Seed-to-Review Queue (`hold_for_review=True`)

- Changes: [2026-11-19-collab-mcp-and-books-split/REQ-042](../2026-11-19-collab-mcp-and-books-split/spec.md#req-042-ch01-seeding-starved-chapter-fallback-apiv1translate-and-collab-books-gitrelease-sync)
- Source intent: Allow external AI agents (`submit_review`) and Admin to inspect, critique, and approve `ch01` seed drafts before locking `glossary_sha`, pushing to Git, and unlocking remaining chapters.
- Previous contract: `approve_book_intake(..., auto_seed=True)` and `seed_book(...)` ran `submit_translation` as `ADMIN` and immediately finalized `ch01` to `accepted`.
- Statement: WHEN an `ADMIN` calls `approve_book_intake(slug, auto_seed=True, hold_for_review=True)` or `seed_book(slug, hold_for_review=True)` (or clicks **`Approve & Seed to Review`** / **`Retry Seed to Review`** in `/admin`), THE server SHALL hold passing `ch01` seeds in `submission.status="pending_admin_approval"`, keep book `status="seeding"` and `ch02..` `locked_seed`, hide the book from `list_tasks(role="translator")`, expose the `ch01` seed submission in `list_tasks(role="reviewer")` for authorized callers, accept peer `submit_review(...)` notes and render them (HTML-escaped) on `/admin`, and upon Admin `approve` merge `additions`, lock `glossary_sha`, record actual peer reviewers in `collab_ledger.json`, unlock `ch02..` to `open`, and advance the book to `"active"` (or `"published"` if 1 chapter). Default `hold_for_review=False` SHALL preserve auto-accept.
- AC-050-1 — Success: `Approve & Seed to Review` creates `pending_admin_approval` on `ch01` without writing `ch01/final.txt` or calling `approve_workstream`; `list_tasks(role="reviewer")` surfaces the seed; external agent `submit_review` attaches scores & notes rendered safely in `/admin`; Admin `approve` merges `ch01`, unlocks `ch02`, sets book `status="active"`, and writes peer reviewer handle into `collab_ledger.json`.
- AC-050-2 — Boundary/failure: Calling `seed_book` while a `pending_admin_approval` seed already exists for `ch01` returns `409`; unauthorized callers on a `restricted` book receive `403` on `get_submission_status` and never see the seed in `list_tasks(role="reviewer")`; non-admin callers cannot trigger `hold_for_review=True` on `submit_translation`.

### REQ-051: Pre-Split Chapters & Initial `book_record` in `submit_book_proposal`

- Current contract: Changed by [2026-11-22-collab-mcp-reviewer-queue-and-matter-flow/REQ-064](../2026-11-22-collab-mcp-reviewer-queue-and-matter-flow/spec.md#req-064-chapter-section_type-classification-persistence-backfill--trusted-boilerplate-auto-accept)
- Changes: [2026-11-19-collab-mcp-and-books-split/spec.md](../2026-11-19-collab-mcp-and-books-split/spec.md)
- Source intent: Support local agent-written Python preprocessing scripts without executing untrusted code on `collab-mcp` by allowing pre-split `chapters` and initial `book_record` JSON in book proposals.
- Previous contract: `submit_book_proposal` accepted only flat `source_text`, always ran `extract_document()`, and initialized `book_record` with empty `characters`/`terms`/`pronoun_pairs`.
- Statement: WHEN an agent submits `submit_book_proposal` with optional `chapters` (`[{chapter_id?, title?, source_text | source_xml}]`) and/or initial `book_record` (`characters`, `terms`, `pronoun_pairs`, optional `style_notes`), THE server SHALL validate structure, size (`<= 5 MiB` total UTF-8 bytes), segment XML parity (for `source_xml`), and prompt-injection safety across all chapters and `book_record`; store `proposed_chapters_json` and merged `book_record` without executing any submitted script; expose pre-split summary & glossary counts in `/admin`; and on `approve_book_intake`, bypass generic `extract_document()`—segmenting `source_text` entries via `BaseExtractor.segment_chapter_content` or using valid `source_xml` verbatim—while preserving exact chapter titles and initial `book_record` in `runs/<slug>/book_record.json`.
- AC-051-1 — Success: Proposing a book with `source_text=""`, `chapters=[...]` (mixed `source_text` + `source_xml`), and initial `book_record` returns `201` with `proposed_chapter_count=len(chapters)`; `/admin` displays pre-split chapter count/titles; `approve_book_intake(..., auto_seed=False)` creates exact `ch01..chNN` chapters with preserved titles/XML and writes initial `book_record` to DB and `runs/<slug>/book_record.json`; raw `proposed_chapters` never leak in public `list_books` / `get_book`.
- AC-051-2 — Boundary/failure: Empty `source_text` AND empty `chapters`, invalid/duplicate `chapter_id`, invalid `book_record` type, or `> 200` chapters returns `400`; total payload bytes `> 5_242_880` returns `413`; malformed `source_xml` returns `422`; prompt-injection text in any pre-split chapter or `book_record` returns `201` with `status="flagged_security"`.

## Assumptions and owner-deferred questions

- Reversible default: `hold_for_review` defaults to `False` on API/MCP for backward compatibility, while `/admin` makes **`Approve & Seed to Review`** the primary one-click action alongside **`Approve & Seed (Auto-Merge)`** and **`Approve & Open ch01 (for Agents)`**.

## Approvals and lifecycle history

- 2026-11-20: `Planning` — Authored directly from user approval (*"yes proceed"*) and approved `exit_plan_mode` plan.
- 2026-11-20: `Planning -> In-Testing` — Completed Tasks 1–3; verified `329 passed` (`0` failed, `0` skipped), `mutation-guard` `100.0%` (`git_sync.py`, `6/6`) and `90.0%` (`service.py`, `27/30`), and single-agent `code-review` `Approved` (`0 P0`, `0 P1`). See [Run 1](./verification.md#run-1--2026-11-20).
