# Admin Approvals UI, Book Proposal Reject/Open Lifecycle, & Per-Book Manual Chapter Approval

- Feature ID: 2026-11-19-collab-mcp-admin-approvals
- Type: enhancement
- Status: In-Testing
- First entered In-Testing: 2026-11-19
- Related specifications: Implemented: Changes [2026-11-19-collab-mcp-and-books-split](../2026-11-19-collab-mcp-and-books-split/spec.md), Extends [2026-11-19-collab-mcp-onboarding-and-book-access](../2026-11-19-collab-mcp-onboarding-and-book-access/spec.md)

## Problem and source intent

The live Collab Books web UI (`https://collab.vinhthang.dev/` and `/admin`) currently lacks any interactive UI for approving or rejecting book proposals (`gift-of-the-magi` sits in `pending_approval`) or reviewing/approving chapter translations, even though `/admin` backend endpoints exist. Furthermore, approving a book with `auto_seed=False` leaves the book in `status="seeding"` (invisible to agents in `list_tasks`), there is no book-proposal rejection endpoint, and all books auto-merge chapters on `review.vinhthang.dev` `PASS` without an optional per-book human approval toggle.

Authorizing user intent (2026-11-19, verbatim user prompt and approved `implementation_plan` choices):

1. *"review this, https://collab.vinhthang.dev/, I can't see how can I approval for a book translation"*
2. User choice A1: *"Auto-merge on review.vinhthang.dev PASS by default + optional per-book 'Require Admin Approval' toggle for chapters"*
3. User choice A2: *"Plan, implement, and deploy the /admin Approvals UI (v0.1.2) so you can approve gift-of-the-magi and future translations in the browser"*

## Repository grounding

Inspected against `collab-book-translate` at commit `7fcb3bd`:

- `scripts/mcp_server/db.py:33-55` (`books` table) lacks an `approval_mode` column (`"auto"` vs `"manual"`), though `list_books` (`db.py:438`) already filters `status != 'rejected'`.
- `scripts/mcp_server/service.py:259` (`list_tasks`) skips books whose `status` is not in `{"active", "published"}`, while `approve_book_intake` (`service.py:978-996`) sets `status="seeding"` even when `auto_seed=False`.
- `scripts/mcp_server/service.py:645-648` (`submit_translation`) unconditionally sets `sub_status="accepted"` on `verdict == "PASS"`.
- `scripts/mcp_server/service.py:1106-1138` (`manage_flagged_submission`) only accepts `"reject"` and `"approve_override"`, passing `override_escalated=True` unconditionally.
- `scripts/mcp_server/app.py:144-412` (`_render_bookshelf_html` & `_render_admin_html`) exposes `/admin/books/{slug}/approve`, `/admin/books/{slug}/seed`, `/admin/fallback/run`, `/admin/submissions/{id}/manage`, and `/admin/books/{slug}/release`, with zero HTML UI queues wired to them and no submission-listing route or book-rejection route.

## Non-goals

- **No disabling of `review.vinhthang.dev` critique**: `approval_mode="manual"` adds a human gate *after* automated validation and `TRANSLATION_REVIEW` pass; it does not skip quality review.
- **No weakening of Google SSO / `COLLAB_ADMIN_TOKEN` gating**: All approval, rejection, submission listing, and policy mutation endpoints require `ADMIN` tier (`403` otherwise).
- **No retroactive mutation of already-published chapters**: Toggling `approval_mode` affects subsequent non-admin `submit_translation` passes.

## Requirements

### REQ-047: Book proposal intake (`auto_seed=False` open for agents) and book rejection lifecycle

- Changes: [2026-11-19-collab-mcp-and-books-split/REQ-042](../2026-11-19-collab-mcp-and-books-split/spec.md#req-042-ch01-seeding-starved-chapter-fallback-apiv1translate-and-collab-books-gitrelease-sync)
- Source intent: Allow Admins to either auto-seed `ch01` via `review.vinhthang.dev` OR approve & immediately open `ch01` for translating agents, and reject unwanted/flagged book proposals cleanly.
- Previous contract: `approve_book_intake(..., auto_seed=False)` left the book in `status="seeding"` (hidden from `list_tasks`) and re-ran `prepare_book_files` on every retry; no `reject_book_proposal` operation existed.
- Statement: WHEN an `ADMIN` invokes `approve_book_intake(book_slug, auto_seed=False)`, THE server SHALL extract chapters only if none exist yet, keep `ch01` in `status="open"` and `ch02..` in `locked_seed`, set book `status="active"` so translators immediately discover `ch01` in `list_tasks`, and return the updated book; WHEN an `ADMIN` invokes `reject_book_proposal(book_slug, reason?)` (or `POST /admin/books/{slug}/reject` / `POST /api/v1/admin/books/{slug}/reject`) on a non-`active`, non-`published` book, THE server SHALL transition its status to `"rejected"` and omit it from public `list_books()`.
- AC-047-1 — Success: Proposing a book enters `pending_approval`; approving with `auto_seed=False` sets book `status="active"`, keeps `ch01` `open` and `ch02` `locked_seed`, and exposes `ch01` (not `ch02`) in `list_tasks(role="translator")`; retrying `approve_book_intake(..., auto_seed=False)` after `seed_failed` transitions to `active` without duplicating chapters; rejecting a pending/flagged/seed_failed proposal sets `status="rejected"` and hides it from public `list_books()`.
- AC-047-2 — Boundary/failure: IF an `ADMIN` attempts to reject an `active` or `published` book OR re-approve a `published` book, THEN the server SHALL return `409`; IF a non-`ADMIN` caller invokes approve or reject, THEN the server SHALL return `403`.

### REQ-048: Optional per-book manual chapter approval mode (`approval_mode="auto" | "manual"`)

- Source intent: Verbatim user approval choice A1 (*"Auto-merge on review.vinhthang.dev PASS by default + optional per-book 'Require Admin Approval' toggle for chapters"*).
- Statement: WHEN a book's `approval_mode` is `"auto"` (default), passing `submit_translation` rounds auto-accept and sync to git; WHEN an `ADMIN` sets `approval_mode="manual"` (via `set_book_approval_mode`, `set_book_access`, or `/admin/api/books/{slug}/approval-mode`), THE server SHALL hold subsequent passing non-`ADMIN` `submit_translation` submissions in `status="pending_admin_approval"` (without calling `approve_workstream` or syncing to git), hide that chapter from translator `list_tasks` and `trigger_starved_fallback` while exposing it to peer reviewers, AND finalize/commit on Admin `approve` (calling `approve_workstream(override_escalated=False)`) OR reopen the chapter to translators on Admin `reject`. Admin-initiated `seed_book` and `trigger_starved_fallback` SHALL bypass `pending_admin_approval`.
- AC-048-1 — Success: Default `"auto"` auto-accepts; toggling a book to `"manual"` holds a non-admin passing chapter in `pending_admin_approval` with `chapter.status="open"`, hides it from translator `list_tasks` and starved fallback while showing it in reviewer `list_tasks`, reopens it upon Admin `reject`, and finalizes + pushes to git (with `approve_workstream(override_escalated=False)`) upon Admin `approve`; Admin `seed_book` on a `"manual"` book accepts `ch01` immediately; Admin `approve_override` on `flagged_security`/`escalated_to_admin` passes `override_escalated=True`.
- AC-048-2 — Boundary/failure: IF an invalid `approval_mode` is provided, THEN the server SHALL return `400`; IF a non-`ADMIN` agent calls `submit_translation` on a chapter that already has a `pending_admin_approval` submission, OR an `ADMIN` attempts to approve an already-`accepted` submission or chapter, THEN the server SHALL return `409`; non-`ADMIN` callers updating `approval_mode` or managing submissions SHALL receive `403`.

### REQ-049: Admin Approvals & Lifecycle Dashboard (`/admin`) and Homepage Clarity (`/`)

- Changes: [2026-11-19-collab-mcp-onboarding-and-book-access/REQ-045](../2026-11-19-collab-mcp-onboarding-and-book-access/spec.md#req-045-write-restricted-per-book-agent_key-allowlists-and-admin-sso-management-ui)
- Source intent: Verbatim user prompt (*"review this, https://collab.vinhthang.dev/, I can't see how can I approval for a book translation"*) and approved plan.
- Previous contract: `GET /` showed a passive `Admin Access Control (SSO)` link and `/admin` only showed agent-key minting and per-book `access_mode` controls.
- Statement: `GET /` SHALL prominently link **`Admin Approvals & Access (SSO)`**, display a pending-proposal badge when books await review, and show a **`Review in /admin →`** action link on `pending_approval`/`flagged_security` rows; `GET /admin` SHALL serve an `ADMIN`-gated UI containing (1) a Book Proposals & Seeding queue with expandable escaped source preview and `Approve & Seed ch01` / `Approve & Open ch01 (for Agents)` / `Retry Seed ch01` / `Reject Proposal` actions, (2) a Chapter Submissions Review & Approval queue backed by `GET /admin/api/submissions` (`list_submissions`) with expandable escaped Vietnamese/XML preview, findings/flags, peer reviews, and `Approve & Merge` / `Reject` actions, (3) Per-book `access_mode`, `approval_mode`, fallback, and release publishing controls, and (4) 3-word `AGENT_KEY` management.
- AC-049-1 — Success: `GET /` renders `Admin Approvals & Access (SSO)`, pending count badge, `manual approval` indicator when enabled, and `Review in /admin` link on pending books; authenticated `ADMIN` `GET /admin` and `GET /admin/api/submissions` return `200` with prioritized actionable proposals and submissions, working approve/reject/mode controls, and attached `peer_reviews`.
- AC-049-2 — Boundary/failure: Unauthenticated/non-`ADMIN` requests to `/admin`, `/admin/api/submissions`, `/admin/books/{slug}/reject`, and `/admin/api/books/{slug}/approval-mode` return `403`; HTML responses escape all dynamic book/submission strings (preventing `<script>` injection), restrict `license_url` `href` rendering to `https://`/`http://`, and never leak raw `AGENT_KEY`s, `COLLAB_ADMIN_TOKEN`, or `REVIEW_BEARER_TOKEN`.

## Assumptions and owner-deferred questions

- Reversible default: Existing and new books default to `approval_mode="auto"`, matching current automated merge behavior unless an Admin toggles `"manual"` per book.

## Approvals and lifecycle history

- 2026-11-19: `Planning` — Spec package authored directly from user choices A1 & A2 and approved `implementation_plan` after `exit_plan_mode`.
- 2026-11-19: `Planning -> In-Testing` — Completed Tasks 1–3; verified `326 passed` (`0` failed, `0` skipped), `mutation-guard` `100.0%` (`db.py`, `31/31`) and `86.7%` (`service.py`, `26/30`), and single-agent `code-review` `Approved` (`0 P0`, `0 P1`). See [Run 1](./verification.md#run-1--2026-11-19).
