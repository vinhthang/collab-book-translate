# Collab Books MCP Trusted Submitter Auto-Approve, Curated Glossary Unlock, & Pre-Translated Batch Import (`collab-mcp:v0.4.0`)

- Feature ID: 2026-11-22-collab-mcp-trusted-intake-and-import
- Type: enhancement
- Status: In-Testing
- First entered In-Testing: 2026-11-22
- Related specifications: Implemented: Changes [2026-11-20-collab-mcp-seed-review-and-presplit](../2026-11-20-collab-mcp-seed-review-and-presplit/spec.md), Changes [2026-11-21-collab-mcp-workflow-v2](../2026-11-21-collab-mcp-workflow-v2/spec.md), Changes [2026-11-21-collab-mcp-bug-triage-and-hardening](../2026-11-21-collab-mcp-bug-triage-and-hardening/spec.md)

## Problem and source intent

Two workflow friction points block migrating curated and pre-translated books (such as the 3 remediated Ray Dalio books totaling 87 chapters) onto `https://collab.vinhthang.dev`:
1. **Redundant manual `/admin` approval gates for trusted submitters**: `submit_book_proposal` unconditionally creates books in `status="pending_approval"` even when invoked by `ADMIN` or a promoted `agent_admin`, forcing manual `/admin` clicks per book. Furthermore, clicking `Approve (Queue for Agent Admin Parse)` on `/admin` places even pre-split proposals into `approved_pending_parse` (`0` chapters), and `locked_seed` blocks `ch02..chNN` until `ch01` is accepted.
2. **No pre-translated batch chapter import**: Migrating already-translated chapters (`translation_xml`) into `pending_review` for peer reviewers currently requires running synchronous `review.vinhthang.dev` LLM evaluation rounds on every chapter via `submit_translation` after unlocking `ch01`, instead of validating deterministically and queuing directly for peer review.

Authorizing user intent (2026-11-22, verbatim prompt + resolved choices `A1`, `A2`, `A3`):
- Prompt: *"I do not want to manual do these approve if I manual submit these books, need to change the process"*
- **Q1 (`A1`)**: Trusted submitters (`ADMIN` token / `/admin` SSO + promoted `agent_admin` 3-word keys) auto-approve on submit; untrusted `contributor` / `ANONYMOUS` proposals still wait in `pending_approval`.
- **Q2 (`A2`)**: Lock `glossary_sha` from supplied `book_record`, open all `ch01..chNN` immediately (skipping `locked_seed`), and accept optional `translation_xml` per chapter directly into `pending_review` for peer reviewers.
- **Q3 (`A3`)**: Author new spec + TDD changes in `/Users/thanghoang/github/collab-book-translate`, deploy to `collab.vinhthang.dev` via existing release flow, then migrate all 87 Ray Dalio chapters.

## Repository grounding

Inspected at `/Users/thanghoang/github/collab-book-translate` (`0702b91`):
- `scripts/mcp_server/service.py:900-990` (`submit_book_proposal`): unconditionally sets `init_status = "pending_approval"` regardless of caller tier/role; `_normalize_proposed_chapters` (`132-192`) ignores any `translation_xml` field; `approve_book_intake` (`2025-2085`) and `replace_unaccepted_chapters` (`db.py:656-677`) always put `ch02..chNN` into `"locked_seed"`.
- `scripts/mcp_server/service.py:1536-1620` (`_finalize_accepted_chapter`): unconditionally recomputes `glossary_sha` when `chapter_id == "ch01"` (even if later than `ch02` or already locked) and always appends `"review-vinhthang-dev"` to `collab_ledger.json` `reviewer_logins` even when `workstream_id == ""`.
- `scripts/mcp_server/service.py:640-799` (`list_tasks`, `_build_chapter_neighbors`): slices `peer_review_candidates[:25]` globally and reads `translation_xml` only from `chapters` (empty until `accepted`).
- `scripts/mcp_server/app.py`: exposes `28` MCP tools (`version="4.1.0"`), `/admin` lacks an `Approve & Open All Chapters` action, and `submit_book_proposal`/`submit_book_parse`/`update_book_record`/`submit_translation` lack `admin_token` parity.

## Non-goals

- **Zero weakening of untrusted intake governance**: `ANONYMOUS` and `contributor` proposals continue entering `pending_approval` (and cannot submit pre-translated `translation_xml`).
- **Zero prompt-injection bypass**: Even `ADMIN` and `agent_admin` proposals containing 4-class prompt-injection patterns remain quarantined in `flagged_security`.
- **Zero server-side Python execution**: Only declarative JSON/XML chapter payloads are validated and persisted.

## Requirements

### REQ-061: Trusted Submitter Auto-Approve & Curated Glossary Chapter Unlock

- Current contract: Changed by [2026-11-22-collab-mcp-reviewer-queue-and-matter-flow/REQ-064](../2026-11-22-collab-mcp-reviewer-queue-and-matter-flow/spec.md#req-064-chapter-section_type-classification-persistence-backfill--trusted-boilerplate-auto-accept)
- Changes: [2026-11-20-collab-mcp-seed-review-and-presplit/REQ-051](../2026-11-20-collab-mcp-seed-review-and-presplit/spec.md#req-051-pre-split-chapters--initial-book_record-in-submit_book_proposal), [2026-11-21-collab-mcp-workflow-v2/REQ-052](../2026-11-21-collab-mcp-workflow-v2/spec.md#req-052-scoped-agent_admin-role-on-3-word-agent_keys--authority-boundaries), [2026-11-21-collab-mcp-workflow-v2/REQ-053](../2026-11-21-collab-mcp-workflow-v2/spec.md#req-053-approved_pending_parse-queue-submit_book_parse--rich-brief--book_record-curation)
- Source intent: Verbatim prompt (*"I do not want to manual do these approve if I manual submit these books, need to change the process"*) and Q1/Q2 (`A1`, `A2`).
- Previous contract: All `submit_book_proposal` callers entered `pending_approval`; pre-split `approve_book_intake` and `submit_book_parse` always set `ch02..chNN` to `locked_seed`; `submit_book_parse` rejected `pending_approval` books.
- Statement: WHEN an `ADMIN` or promoted `agent_admin` invokes `submit_book_proposal`, THE server SHALL enforce clean 4-class prompt-injection safety (`flagged_security` if triggered) and otherwise bypass `pending_approval`: raw `source_text` proposals without `chapters` SHALL enter `approved_pending_parse`, WHILE pre-split `chapters` proposals with a curated `book_record`, explicit `unlock_all_chapters=True`, or any chapter `translation_xml` SHALL unpack chapters directly into `status="open"`, compute and lock `glossary_sha`, set `book.status="active"`, and import any supplied `translation_xml` into `pending_review` (`pending_admin_approval` when `approval_mode="manual"`); pre-split trusted proposals with empty `book_record` and `unlock_all_chapters=False` SHALL unpack `ch01="open"`, `ch02..="locked_seed"` in `book.status="seeding"`. `submit_book_parse` SHALL permit optional `unlock_all_chapters=True`, optional `chapters` fallback to `proposed_chapters`, and execution on `pending_approval` when called by `ADMIN` or the `agent_admin` who proposed the book (`proposed_by == ctx.handle`). `approve_book_intake` SHALL support `unlock_all_chapters=True`. `_finalize_accepted_chapter` SHALL preserve an already-locked `glossary_sha` when chapters accept out of order and omit `"review-vinhthang-dev"` from `collab_ledger.json` when `workstream_id == ""`.
- AC-061-1 — Success: `agent_admin` or `ADMIN` `submit_book_proposal` with pre-split `chapters`, curated `book_record`, and optional `translation_xml` returns `book.status == "active"`, non-empty `glossary_sha`, all `ch01..chNN` `"open"`, and imported chapter submissions in `"pending_review"`; raw `source_text` enters `"approved_pending_parse"`; pre-split with empty glossary and `unlock_all_chapters=False` enters `"seeding"`; `submit_book_parse` on caller's own `pending_approval` book and `approve_book_intake(..., auto_seed=False, unlock_all_chapters=True)` unlock all chapters.
- AC-061-2 — Boundary/failure: Prompt-injection text in a trusted `submit_book_proposal` quarantines to `status == "flagged_security"` with `0` chapters extracted; `ANONYMOUS` and `contributor` proposals stay `"pending_approval"` and reject `chapters[*].translation_xml` with `403`; `agent_admin` attempting `submit_book_parse` on another caller's `pending_approval` book receives `403`; accepting `ch02` then `ch01` keeps `glossary_sha` unchanged and records peer reviewers accurately in `collab_ledger.json`.

### REQ-062: Batch Pre-Translated Chapter Import (`import_chapter_translations`) & Reviewer Continuity

- Current contract: Changed by [2026-11-22-collab-mcp-reviewer-queue-and-matter-flow/REQ-065](../2026-11-22-collab-mcp-reviewer-queue-and-matter-flow/spec.md#req-065-paginated-compact-reviewer-queue-limit200-full-uuid-submission_id--body-first-natural-sort)
- Changes: [2026-11-21-collab-mcp-workflow-v2/REQ-054](../2026-11-21-collab-mcp-workflow-v2/spec.md#req-054-full-transparency-work-querying-for-translators-reviewers--agent_admin), [2026-11-21-collab-mcp-workflow-v2/REQ-055](../2026-11-21-collab-mcp-workflow-v2/spec.md#req-055-actionable-peer--agent_admin-chapter-gate-approval_modepeer--auto--manual)
- Source intent: Enable migrating pre-split, already-translated books (including books already in `approved_pending_parse` or caller-proposed `pending_approval`, totaling `87` chapters across 3 Ray Dalio books) into `pending_review` in fast validated batches and ensure peer reviewers see complete lists and neighbor context.
- Previous contract: Only single-chapter `submit_translation` (running synchronous `review.vinhthang.dev` LLM critique) existed; `list_tasks` capped `peer_review_candidates` globally at `25`; `_build_chapter_neighbors` read `translation_xml` only from `accepted` chapters.
- Statement: WHEN an authorized `agent_admin` or `ADMIN` calls `import_chapter_translations(book_slug, chapters, engine_model?, unlock_all_chapters=True)` with `1..50` items (`[{chapter_id, translation_xml, title?, source_xml?}]`, total `<= 5 MiB`, per-chapter XML `<= 512 KiB`), THE server SHALL verify book access, auto-unpack `proposed_chapters` if the book is currently `approved_pending_parse` (or caller's own `pending_approval`), compute and lock `glossary_sha` if empty, unlock `locked_seed` chapters when `unlock_all_chapters=True` and transition `book.status` to `"active"`, atomically validate every batch entry (1:1 `<segment>` IDs, byte-identical `<original>`/code/images, `<hanviet>` for `zh`, Vietnamese language check, 4-class prompt-injection check, and conflict check against `accepted` or other-author active submissions), and persist/idempotently update all batch submissions in `pending_review` (`pending_admin_approval` on `"manual"`). `list_tasks` SHALL return up to `200` `peer_review_candidates` when `book_slug` is specified (`50` globally), and `_build_chapter_neighbors` SHALL fall back to the latest `pending_review`/`pending_admin_approval` draft when `prev_chapter` or `ch01` is not yet `accepted`.
- AC-062-1 — Success: Calling `import_chapter_translations` across multi-batch uploads unpacks an existing `approved_pending_parse` pre-split book into `status="active"`, locks `glossary_sha`, opens all chapters, queues all imported chapters as `pending_review`, idempotently updates same-author unaccepted drafts on re-run, surfaces `>25` candidates when `list_tasks(role="reviewer", book_slug=...)` is queried, and populates `prev_chapter.translated_tail` and `seed_ch01_excerpt` from `pending_review` drafts for peer reviewers.
- AC-062-2 — Boundary/failure: `ANONYMOUS`/`contributor` or unauthorized `restricted`-book callers receive `403`; empty or `>50` chapters returns `400`; `>512 KiB` chapter XML or `>5 MiB` batch returns `413`; targeting an `accepted` chapter or a chapter with another author's active submission returns `409`; any XML parity, byte-invariance, `<hanviet>`, language-detection, or prompt-injection failure on any item in the batch returns `422` (with structured `defects`) and persists ZERO chapters from that batch.

### REQ-063: MCP `v4.2.0` Surface, REST & `/admin` Open-All Controls, & Worker CLI Parity

- Current contract: Changed by [2026-11-23-collab-mcp-public-corpus-publish-gate/REQ-069](../2026-11-23-collab-mcp-public-corpus-publish-gate/spec.md#req-069-public-bookshelf-filtering-admin-policy-controls-startup-originmain-reconciliation--mcp-v431)
- Changes: [2026-11-21-collab-mcp-bug-triage-and-hardening/REQ-060](../2026-11-21-collab-mcp-bug-triage-and-hardening/spec.md#req-060-bug-triage-mcp-surface-rest-api-admin-ui--cli)
- Source intent: Expose the trusted intake and batch import flow consistently across MCP (`v4.2.0`), REST, `/admin`, `collab_worker.py`, and agent/skill documentation without introducing hygiene regressions.
- Previous contract: `collab-mcp` exposed `28` tools at version `"4.1.0"`, `/api/v1/books/{slug}/import-translations` and `/admin` `Approve & Open All Chapters` did not exist, and `submit_book_proposal`/`submit_book_parse`/`update_book_record`/`submit_translation` lacked optional `admin_token`.
- Statement: `scripts/mcp_server/app.py` SHALL advertise version `"4.2.0"`, expose `29` MCP tools (adding `import_chapter_translations` and optional `admin_token`/`unlock_all_chapters` parity), add `POST /api/v1/books/{slug}/import-translations`, extend `/admin/books/{slug}/approve` with `unlock_all_chapters`, render HTML-escaped **`Approve & Open All Chapters`** (`.js-approve-open-all`, passing `node --check`) in `/admin`, add `import-translations` to `scripts/collab_worker.py`, update `plugin.json` (`"4.2.0"`), `skills/collab-translate-book/SKILL.md`, and `agents/collab_admin.md`, and satisfy `test_option_a_hygiene.py`.
- AC-063-1 — Success: `/api/health`, `/llms.txt`, MCP `initialize`, and `plugin.json` report `4.2.0`; `tools/list` returns `29` tools including `import_chapter_translations`; `POST /api/v1/books/{slug}/import-translations`, `POST /admin/books/{slug}/approve`, `/admin` UI (`node --check`), and `collab_worker.py import-translations` pass E2E tests.
- AC-063-2 — Boundary/failure: `/admin` HTML escapes dynamic book/chapter/submission metadata cleanly, and `tests/test_option_a_hygiene.py` passes with zero tracked corpus files and zero forbidden tokens.

## Assumptions and owner-deferred questions

- Reversible default batch cap: `import_chapter_translations` caps single requests at `50` chapters (`<= 5 MiB`) so large books (e.g. `41`-chapter books or `100+`-chapter books) upload cleanly in 1–3 fast HTTP calls without exceeding proxy timeouts or memory limits.
- Proposal payload cap: `submit_book_proposal` raises its total UTF-8 payload cap from `5 MiB` to `10 MiB` strictly for trusted submitters (`ADMIN` / `agent_admin`), keeping `5 MiB` for untrusted `ANONYMOUS` / `contributor` callers.

## Approvals and lifecycle history

- `2026-11-22`: `Planning` — Authored from user prompt (*"I do not want to manual do these approve if I manual submit these books, need to change the process"*) and Q1–Q3 selections (`A1`, `A2`, `A3`), executing under approved execution plan.
- `2026-11-22`: `Planning -> In-Testing` — Completed Tasks 1–3 (`346 passed` full regression suite, `node --check`, `test_option_a_hygiene.py`, `mutation-guard` `100.0%` on `service.py` and `80.0%` on `app.py`, single-agent `code-review` `0` P0/P1). See [Run 1](./verification.md#run-1--2026-11-22).
