# Collab Books MCP Admin Workspace Console Rework (`collab-mcp:v0.7.0` / `MCP v4.5.0`)

- Feature ID: 2026-11-27-collab-mcp-admin-console-rework
- Type: enhancement
- Status: In-Testing
- First entered In-Testing: 2026-11-27
- Related specifications: Implements: Changes [2026-11-23-collab-mcp-reader-threads-and-invite-lockdown](../2026-11-23-collab-mcp-reader-threads-and-invite-lockdown/spec.md)

## Problem and source intent

Observed production state on `https://collab.vinhthang.dev/admin` (`v4.4.0`):
1. **Single-page overload**: `/admin` dumps pending proposals, up to `50` historical/accepted submission cards with embedded XML, fixed bugs, all `6` books (each with full agent checkbox grids even for `public` mode), minting controls, and the agent table into one vertical stream.
2. **Missing day-to-day operational ergonomics**: Admins lack an actionable landing overview, live filters/search, lazy submission inspection, bulk approve/reject for cleared queues, chapter-level drill-down, SSO-only deliverable download (`EPUB`/`PDF`/`DOCX`) for completed `owner-permission` books, agent key rotation/revocation, and consistent Dark/Light theme chrome across `/admin`, `/admin/threads`, and `/admin/read/*`.

Authorizing user intent (2026-11-27, `/goal /grill-me /plan` + resolved `A1`–`A5`):
- **Prompt**: *"I think need to rework on the admin page, https://collab.vinhthang.dev/admin"*
- **A1 (Full Workspace Console Redesign)**: Sticky left sidebar + instant client-side tabs (`Overview`, `Books`, `Submissions`, `Bugs`, `Agents`), KPI summary, search/filters, drawers, and refined dark visual design.
- **A2 (Actionable Overview + Dark/Light Theme Toggle)**: Lands on Overview (4 KPI cards, Action Inbox, quick Books progress) with Linear/Vercel-style dark slate palette + crisp 1px borders + Light/Dark theme toggle.
- **A3 (Full Book Management + Chapter Drawer + SSO Private Export)**: Filterable Books view, progress bars, conditional allowlist visibility (hidden unless `restricted`), slide-over Chapter Drill-Down Drawer (`/admin/read/{slug}/{ch}` links), and SSO-only Compile & Download (`EPUB`/`PDF`/`DOCX`) for completed books.
- **A4 (Actionable Triage + Lazy Inspect Drawer + Bulk Actions)**: Submissions default to Actionable (`Actionable`, `Accepted`, `Rejected/Superseded`, `All`), lazy XML/critique Inspector Drawer, direct `/admin/read/{slug}/{ch}` links, multi-select Bulk Approve/Reject, and Bugs defaulting to Open with collapsed Resolved history.
- **A5 (Full Agent Lifecycle + Unified Chrome)**: `agents.revoked_at` soft-revoke/hard-delete, Rotate Key, contribution stats (`submission_count`, `review_count`), copyable MCP JSON/CLI snippet on mint/rotate, and unified Dark/Light theme + topbar chrome across `/admin`, `/admin/threads`, and `/admin/read/*`.

## Repository grounding

Inspected at `/Users/thanghoang/github/collab-book-translate` (`7516867`):
- `scripts/mcp_server/db.py`: `agents` table lacks `revoked_at` column; no `list_agents_with_stats`, `rotate_agent_key`, or `revoke_or_delete_agent` helpers.
- `scripts/mcp_server/git_sync.py`: `compile_and_publish_release` pushes to `vinhthang/collab-books` and blocks non-public books; no isolated `private-books/exports/<slug>/` compiler exists for SSO downloads.
- `scripts/mcp_server/service.py`: lacks `rotate_agent_key`, `revoke_agent`, `get_admin_submission_detail`, `bulk_manage_submissions`, and `export_book_deliverable`.
- `scripts/mcp_server/app.py`: `_SHARED_CSS`, `_ADMIN_JS`, and `_render_admin_*` (~1,300 lines inside `app.py`) render one flat vertical `/admin` page without tabs, filters, bulk actions, drawers, or theme switching.

## Non-goals

- **Zero Node/Vite frontend build step**: Keep zero-build SSR + vanilla ES2020 JS (verified by `node --check`) so `collab-mcp` remains a lean single-stage OCI container (`ADR-041`).
- **No weakening of Git/GitHub public-corpus isolation (`REQ-068`)**: SSO book exports for `owner-permission` titles stream HTTP responses directly from `private-books/exports/<slug>/` and NEVER commit or push to `vinhthang/collab-books`.

## Requirements

### REQ-073: Agent Key Lifecycle (`revoked_at`, Rotate Key, Soft-Revoke / Delete, & Contribution Stats)

- Current contract: Changed by [2026-11-27-collab-mcp-agent-live-presence-and-readability/REQ-080](../2026-11-27-collab-mcp-agent-live-presence-and-readability/spec.md#req-080-readable-agent-handles-initial-role-minting--inline-profile-editing)

- Source intent: Resolved `A5` (*"Add Rotate Key & Revoke/Delete controls (soft-revoke or delete unused) + contribution stats (submissions/reviews)"*).
- Statement: `agents` persistence (`db.py`) SHALL add `revoked_at TEXT NOT NULL DEFAULT ''` via idempotent SQLite and PostgreSQL schema migrations. `CollabStore.get_agent_by_key` SHALL exclude rows where `revoked_at != ''` so revoked keys immediately fail authentication with `401`. `ADMIN` callers SHALL have `rotate_agent_key` (`POST /admin/api/agents/{handle}/rotate` and `/api/v1/admin/agents/{handle}/rotate`) returning a newly minted 3-word `AGENT_KEY` while invalidating the previous key hash and clearing `revoked_at`, AND `revoke_agent` (`POST /admin/api/agents/{handle}/revoke` and `/api/v1/admin/agents/{handle}/revoke`) which SHALL soft-revoke (`revoked_at = UTC ISO`) when the agent has any submissions, peer reviews, bug reports, or book proposals (or reactivate when `reactivate=True`), OR hard-delete the `agents` row when unreferenced. `list_agents` (`GET /admin/api/agents` & `/api/v1/admin/agents`) SHALL enrich each agent dict with `revoked_at`, `submission_count`, and `review_count`.
- AC-073-1 — Success: Rotating an agent key mints a new 3-word `AGENT_KEY` that authenticates (`200`) while the old key fails (`401`); revoking a referenced agent sets `revoked_at` (failing auth with `401` while keeping submissions intact) and `reactivate=True` restores auth (`200`); revoking an unreferenced agent deletes its row; `list_agents` returns accurate `submission_count` and `review_count`.
- AC-073-2 — Boundary/failure: Non-`ADMIN` callers calling rotate/revoke receive `403`; unknown agent handles return `404`.

### REQ-074: Admin Backend Endpoints for Lazy Submission Inspect, Bulk Manage, & SSO Private Export

- Source intent: Resolved `A3` & `A4` (*"SSO-only Compile & Download (EPUB/PDF/DOCX) for completed private books"*, *"lazy-load full bilingual XML/critiques in a slide-over drawer"*, *"multi-select Bulk Approve/Reject"*).
- Statement: `CollabMcpService` and `app.py` SHALL expose strictly behind `assert_admin`:
  1. `GET /admin/api/submissions/{submission_id}` (and `/api/v1/admin/submissions/{submission_id}`): returning complete sanitized submission metadata (`translation_xml`, `final_txt`, `open_findings`, `security_flags`, `peer_reviews`, `section_type`, `chapter_title`, `book_title`, `404` if missing).
  2. `POST /admin/api/submissions/bulk-manage` (and `/api/v1/admin/submissions/bulk-manage`): validating `action in {"approve", "approve_override", "reject"}` and `1 <= len(submission_ids) <= 50` (`400` otherwise) and processing each submission via `manage_flagged_submission`, returning `{"processed": [...], "errors": [...]}`.
  3. `GET /admin/api/books/{slug}/export` (and `/api/v1/admin/books/{slug}/export`) with `?format=epub|pdf|docx` (`400` on invalid format, `404` on unknown/rejected book, `409` if any chapter is not `accepted`): materializing DB accepted chapter `final.txt` + `book_record.json` under `private-books/exports/<slug>/run/` and streaming the compiled binary deliverable with zero files or commits written to `books_repo_dir` or GitHub.
- AC-074-1 — Success: Admin detail endpoint returns full bilingual XML and critiques; bulk approve accepts multiple `pending_review` chapters and transitions a finished `owner-permission` book to `completed`; admin export downloads valid non-empty `.epub`, `.pdf`, and `.docx` files for completed `owner-permission` books while keeping `books_repo_dir` clean.
- AC-074-2 — Boundary/failure: Non-`ADMIN` callers receive `403` across all three routes; unknown IDs/books return `404`; invalid export format or empty/invalid bulk payload returns `400`; incomplete books return `409` on export.

### REQ-075: Modular `/admin` Workspace Console UI, Unified Chrome (`/admin/threads`, `/admin/read/*`), & `v4.5.0`

- Changes: [2026-11-23-collab-mcp-reader-threads-and-invite-lockdown/REQ-072](../2026-11-23-collab-mcp-reader-threads-and-invite-lockdown/spec.md#req-072-read-only-agent-threads-ui-threads-public--adminthreads-sso-collapsible-long-reviews--v440-surface)
- Current contract: Changed by [2026-11-27-collab-mcp-tech-admin-bug-workflow/REQ-078](../2026-11-27-collab-mcp-tech-admin-bug-workflow/spec.md#req-078-mcprestcli-claim-surface-admin-ui-3-role--pr-controls-collab_tech_admin-agent--v460)
- Source intent: Resolved `A1`–`A5` (*"Sticky left sidebar + tabbed views (Overview/Inbox, Books, Submissions, Bugs, Agents)"*, *"Unified Chrome across /admin, /admin/threads, and /admin/read/*"*).
- Previous contract: `GET /admin` rendered all 6 sections inline in one monolithic `app.py` template without tabs, filters, drawers, bulk actions, agent key rotation/revocation, deliverable export links, or light/dark theme toggle; `/admin`, `/admin/threads`, and `/admin/read/*` did not share theme toggling or unified admin navigation chrome; server/plugin advertised `"4.4.0"` (`v0.6.0`).
- Statement: `scripts/mcp_server/admin_ui.py` SHALL implement `SHARED_THEME_CSS`, `THEME_BOOT_JS`, `_ADMIN_JS`, and `_render_admin_*` (re-exported by `app.py`). `GET /admin` SHALL render a Workspace Console with a sticky sidebar switching `#overview` (4 KPI cards, Action Inbox, quick book progress), `#books` (search, Public/Private/Pending/Completed filters, progress bars, Book Chapter Drill-Down Drawer trigger, conditional allowlist visibility for `restricted` only, private EPUB/PDF/DOCX export links), `#submissions` (`Actionable`/`Accepted`/`Rejected`/`All` filters, book filter, search, full 36-char UUIDs, bulk checkboxes + `.js-bulk-approve`/`.js-bulk-reject`, `.js-inspect-sub` lazy drawer, direct `/admin/read/{slug}/{ch}` links), `#bugs` (`Open`/`Resolved`/`All` filters, collapsed resolved history, preserved `.js-triage-bug`), and `#agents` (mint form + copyable MCP config snippet, `submission_count`/`review_count`, `.js-rotate-agent-key`, `.js-revoke-agent`, `.js-toggle-agent-role`), plus `#btn-theme-toggle` Dark/Light theme switcher. `/admin/threads` and `/admin/read/*` SHALL adopt `SHARED_THEME_CSS` + `#btn-theme-toggle` + unified topbar navigation. Inline JS across `/admin`, `/admin/threads`, and `/admin/read/*` SHALL pass `node --check`. Server, `plugin.json`, `.github/workflows/ci-cd.yml` (`v0.7.0`), `SKILL.md`, and `agents/collab_admin.md` SHALL advance to `"4.5.0"`.
- AC-075-1 — Success: `/admin` renders the complete sidebar Workspace Console, drawered inspection/chapter controls, export links, bulk actions, agent rotate/revoke controls, and Dark/Light toggle (`node --check` exit `0`); `/admin/threads` and `/admin/read/{slug}` share the theme system; `/api/health`, `/llms.txt`, `plugin.json`, and MCP `initialize` report `"4.5.0"`.
- AC-075-2 — Boundary/failure: Unauthenticated `/admin*` HTML/API routes remain `403`; raw XSS payloads stay HTML-escaped across all views; zero raw secrets leak into rendered HTML/JS; existing `tests/test_option_a_hygiene.py` and full `pytest` suite pass with `0` failures.

## Assumptions and owner-deferred questions

- Opt-in anonymous test fixtures (`test_mcp_service_and_app.py`, `test_mcp_bug_triage_and_hardening.py`) remain cleanly isolated on `allow_anonymous_writes=True`; no open `[PROVISIONAL]` items exist.

## Approvals and lifecycle history

- `2026-11-27`: `Planning -> In-Progress` — User resolved Q1–Q5 (`A1`–`A5`) via `/grill-me` and authorized executing the `/admin` Workspace Console rework (`collab-mcp:v0.7.0` / `MCP v4.5.0`).
- `2026-11-27`: `In-Progress -> In-Testing` — Completed Stages 1–3 (`359 passed, 6 warnings, 0 failed, 0 skipped` full regression suite, `tests/test_option_a_hygiene.py` `2 passed`, `mutation-guard` `80.0%` (`12/15`) on `db.py`, `80.0%` (`8/10`) on `git_sync.py`, `80.0%` (`12/15`) on `service.py`, `100.0%` (`15/15`) on `admin_ui.py`, `node --check` exit `0`, single-agent `code-review` `Approved` `0` P0/P1) and updated 1-hop `- Current contract:` on `REQ-072`. See [Run 1](./verification.md#run-1--2026-11-27).
