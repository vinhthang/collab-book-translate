# Design: Admin Approvals UI, Book Proposal Reject/Open Lifecycle, & Per-Book Manual Chapter Approval

Sources: [spec.md](./spec.md), [decisions.md](./decisions.md)

## Components

| Requirement / AC | Path and symbol | Responsibility / change |
| --- | --- | --- |
| REQ-048 / AC-048-1 | `scripts/mcp_server/db.py` (`CollabStore.init_db`, `_hydrate_book`, `set_book_approval_mode`, `set_book_status`) | Additive `books.approval_mode` column (`DEFAULT 'auto'`) in SQLite & PostgreSQL, hydration default, and targeted helpers for updating `approval_mode` and `status`. |
| REQ-047, REQ-048 / AC-047-1..AC-048-2 | `scripts/mcp_server/service.py` (`list_books`, `list_tasks`, `submit_translation`, `set_book_access`, `set_book_approval_mode`, `approve_book_intake`, `reject_book_proposal`, `trigger_starved_fallback`, `list_admin_submissions`, `manage_flagged_submission`) | Open-for-agents `approve_book_intake(auto_seed=False)` (`status="active"`), idempotent chapter extraction retry, `reject_book_proposal`, `"manual"` chapter hold (`pending_admin_approval`) with `override_escalated` distinction, and prioritized Admin submissions list. |
| REQ-049 / AC-049-1, AC-049-2 | `scripts/mcp_server/app.py` (`_render_bookshelf_html`, `_render_admin_html`, `create_app`, `MCP_SERVER_INSTRUCTIONS`, `_render_llms_txt`) | Add `/admin/api/submissions`, `/admin/books/{slug}/reject`, `/admin/api/books/{slug}/approval-mode`, MCP tools (`reject_book_proposal`, `list_submissions`), homepage pending indicator & `/admin` action links, full `/admin` approval queues with `html.escape` + `data-*` event handlers, and bump API version to `3.2.0`. |

## Interfaces and flow

1. **Book Intake & Reject Lifecycle**:
   - `approve_book_intake(book_slug, auto_seed=True)`: rejects `published` with `409`; extracts chapters only when `list_chapters(book_slug)` is empty; if `auto_seed=True`, sets `status="seeding"` and calls `seed_book`; if `auto_seed=False`, sets `status="active"` with `ch01` open and `ch02..` `locked_seed`.
   - `reject_book_proposal(book_slug, reason="")`: rejects `active`/`published` with `409`; transitions pending/flagged/`seed_failed` book to `status="rejected"` so public `list_books()` hides it.
2. **Per-Book Chapter Approval Mode (`auto` | `manual`)**:
   - `set_book_approval_mode` and `set_book_access(..., approval_mode=...)` validate `"auto"` or `"manual"` (`400` otherwise).
   - In `submit_translation`: non-admin Round-1 submissions fail with `409` if the target chapter already has a `pending_admin_approval` submission; non-admin passing rounds on `approval_mode="manual"` save `status="pending_admin_approval"` without calling `approve_workstream` or git sync. Admin `seed_book` / `trigger_starved_fallback` bypass this hold because `ctx.tier == "ADMIN"`.
   - `manage_flagged_submission(submission_id, action)`: accepts `"approve"`, `"approve_override"`, and `"reject"`, returns `409` if submission or target chapter is already `"accepted"`, and passes `override_escalated=(sub["status"] != "pending_admin_approval")` when finalizing a `workstream_id`.
3. **`/` and `/admin` UI**:
   - `/` renames the `/admin` link to `Admin Approvals & Access (SSO)`, displays an amber pending count badge when pending/flagged books exist, shows `manual approval` pill when set, and adds `Review in /admin ->` links on pending rows.
   - `/admin` displays Summary counts + `Run Starved Fallback`, Book Proposals & Seeding queue, Chapter Submissions queue (prioritized pending/flagged/escalated first with escaped previews & findings), and Per-book Access + Approval Mode + Release cards above the `AGENT_KEY` manager.

## Boundaries and recovery

- **XSS & `href` safety**: All dynamic strings in `/` and `/admin` pass through `html.escape(..., quote=True)`. Interactive buttons bind `click` listeners reading `dataset.*` instead of inline `onclick="fn('...')"` string interpolation. `license_url` only renders as `<a href=...>` if starting with `https://` or `http://`.
- **Zero secret exposure**: Only `key_hint` (`w1-***-w3`) and canonical handles appear in `/admin`; raw `AGENT_KEY`s, `COLLAB_ADMIN_TOKEN`, and `REVIEW_BEARER_TOKEN` are never embedded in HTML or public JSON.

## Dependencies and unresolved design work

None.
