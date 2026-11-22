# Design: Collab Books MCP Trusted Submitter Auto-Approve & Batch Import

Sources: [spec.md](./spec.md), [decisions.md](./decisions.md)

## Components

| Requirement / AC | Path and symbol | Responsibility / change |
| --- | --- | --- |
| REQ-061 / AC-061-1, AC-061-2 | `scripts/mcp_server/service.py` (`_is_trusted_submitter`, `_has_curated_book_record`, `_compute_glossary_sha`, `_normalize_proposed_chapters`, `submit_book_proposal`, `submit_book_parse`, `approve_book_intake`, `_finalize_accepted_chapter`) | Trusted auto-approve (`ADMIN` or `agent_admin`), `approved_pending_parse` vs `active` vs `seeding` routing, curated `glossary_sha` lock, `locked_seed` bypass when curated or `unlock_all_chapters=True`, honest ledger reviewers. |
| REQ-062 / AC-062-1, AC-062-2 | `scripts/mcp_server/service.py` (`import_chapter_translations`, `list_tasks`, `_build_chapter_neighbors`) | Two-phase atomic batch import (`1..50` chapters, deterministic validation + injection quarantine/reject, idempotent same-author updates), `book_slug` `200`-item reviewer candidate cap, `pending_review` neighbor excerpts. |
| REQ-063 / AC-063-1, AC-063-2 | `scripts/mcp_server/app.py`, `scripts/collab_worker.py`, `plugin.json`, `skills/collab-translate-book/SKILL.md`, `agents/collab_admin.md` | MCP `4.2.0` (`29` tools adding `import_chapter_translations` and `admin_token`/`unlock_all_chapters` args), REST `/api/v1/books/{slug}/import-translations`, `/admin` `Approve & Open All Chapters` UI (`node --check`), CLI `import-translations`. |

## Interfaces and flow

1. **Trusted Book Intake (`submit_book_proposal` / `submit_book_parse` / `approve_book_intake`)**:
   - `_is_trusted_submitter(ctx)` returns `True` iff `ctx.tier == "ADMIN"` or (`ctx.tier == "AGENT_KEY"` and `ctx.agent_role == "agent_admin"`). Untrusted callers supplying `translation_xml` inside `chapters` raise `AuthError(403)`.
   - Clean trusted proposals bypass `pending_approval`: raw `source_text` without `chapters` enters `approved_pending_parse`; pre-split `chapters` unpacks immediately via `git_sync.prepare_book_files(...)`, opening all chapters (`active`) and locking `glossary_sha = hashlib.sha256(json.dumps(book_record, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()` whenever `unlock_all_chapters=True`, `_has_curated_book_record(book_record)` is `True`, or any chapter includes `translation_xml`, and importing supplied `translation_xml` drafts directly into `pending_review` (`pending_admin_approval` on `approval_mode == "manual"`).
   - `_finalize_accepted_chapter` computes `glossary_sha` only if empty (or `ch01` when no other chapter was previously accepted and `proposals` is non-empty) and omits `"review-vinhthang-dev"` from `collab_ledger.json` when `workstream_id == ""`.

2. **Atomic Batch Chapter Import (`import_chapter_translations`)**:
   - Enforces `assert_agent_admin_or_admin(ctx)` and `_assert_book_work_access(ctx, book)`.
   - If the book is `approved_pending_parse` (or `pending_approval` with `proposed_by == ctx.handle` or `ctx.tier == "ADMIN"`) and has `proposed_chapters` with `0` chapters in DB, automatically unpacks `proposed_chapters` first.
   - Validates `1..50` items (`<= 5 MiB` batch, `<= 512 KiB` per chapter), locks `glossary_sha` if empty, and validates every chapter (XML parity, `<hanviet>` for `zh`, Vietnamese language probe, 4-class prompt injection, conflict check) in memory BEFORE writing any DB submission rows.
   - On all-valid batch: unlocks `locked_seed` chapters if `unlock_all_chapters=True`, ensures `book.status = "active"`, and inserts (or updates same-author unaccepted drafts with `workstream_id=""`) into `pending_review` (or `pending_admin_approval` on `"manual"`).

## Boundaries and recovery

- Any prompt-injection hit in `submit_book_proposal` still quarantines to `flagged_security` (`0` chapters extracted); any validation/injection/language defect in `import_chapter_translations` aborts atomically with `422` (`details={"defects": ...}`) before any submission mutation occurs.
- `list_tasks` expands `peer_review_candidates` slice to `200` when filtered by `book_slug` (`50` otherwise), and `_build_chapter_neighbors` reads the latest `accepted`/`pending_review`/`pending_admin_approval` submission's `translation_xml` when `chapters.translation_xml` is empty.

## Dependencies and unresolved design work

None.
