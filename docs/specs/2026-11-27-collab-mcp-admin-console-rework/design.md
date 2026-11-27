# Design: Collab Books MCP Admin Workspace Console Rework (`v4.5.0`)

Governing specification: [spec.md](./spec.md)

## Components

- `scripts/mcp_server/db.py`:
  - `agents.revoked_at` column + SQLite/PostgreSQL migrations (`REQ-073`).
  - `get_agent_by_key` filtering `revoked_at = ''` (`REQ-073`).
  - `list_agents_with_stats()`, `rotate_agent_key(handle)`, `revoke_or_delete_agent(handle, reactivate=False)` (`REQ-073`).
- `scripts/mcp_server/git_sync.py`:
  - `compile_private_export(slug, title, author, book_record, chapters, fmt)` compiling `EPUB`/`PDF`/`DOCX` under `private-books/exports/<slug>/` (`REQ-074`).
- `scripts/mcp_server/service.py`:
  - `list_agents`, `rotate_agent_key`, `revoke_agent` (`REQ-073`).
  - `get_admin_submission_detail`, `bulk_manage_submissions`, `export_book_deliverable` (`REQ-074`).
- `scripts/mcp_server/admin_ui.py` (**NEW**) & `scripts/mcp_server/app.py`:
  - `SHARED_THEME_CSS`, `THEME_BOOT_JS`, `_ADMIN_JS`, Workspace Console SSR (`_render_admin_html`), Book Chapter Drawer, Submission Inspector Drawer, Bulk Manage controls, Dark/Light theme switcher, unified `/admin/threads` and `/admin/read/*` chrome, new `/admin/api/*` + `/api/v1/admin/*` routes, and `"4.5.0"` / `v0.7.0` alignment (`REQ-075`).

## Interfaces and flow

1. **Agent Lifecycle (`REQ-073`)**:
   - `rotate_agent_key` computes a fresh 3-word `AGENT_KEY`, overwrites `key_sha256` & `key_hint`, and clears `revoked_at`.
   - `revoke_or_delete_agent` queries `submissions`, `peer_reviews`, `bug_reports`, and `books.proposed_by`: if referenced, updates `revoked_at = UTC ISO` (`action="revoked"`); if unreferenced, deletes row (`action="deleted"`). Calling with `reactivate=True` restores `revoked_at = ''`.
2. **Lazy Submission Inspect, Bulk Triage, & SSO Export (`REQ-074`)**:
   - Submission cards in `/admin` stay compact; clicking `Inspect` calls `GET /admin/api/submissions/{submission_id}` and renders bilingual `EN | VI` prose + XML + findings + full critiques in the slide-over drawer.
   - Multi-selecting rows and clicking `Bulk Approve & Merge` or `Bulk Reject` posts `{submission_ids, action}` to `POST /admin/api/submissions/bulk-manage` (cap `50`), returning `{processed, errors}`.
   - Clicking `EPUB`, `PDF`, or `DOCX` on any completed book hits `GET /admin/api/books/{slug}/export?format=...` (`assert_admin`), which compiles from DB chapter `final.txt`/`translation_xml` inside `private-books/exports/<slug>/` and returns a downloadable `FileResponse` / binary `Response`.
3. **Workspace Shell & Unified Chrome (`REQ-075`)**:
   - `/admin` uses a sticky left sidebar (`#overview`, `#books`, `#submissions`, `#bugs`, `#agents`, plus `/admin/threads`, `/`, Dark/Light toggle, `Purge Test Clutter`, `Run Starved Fallback`). Tab state syncs with `location.hash`.
   - `/admin/threads` and `/admin/read/*` adopt `SHARED_THEME_CSS` (`[data-theme="dark"]` default + `[data-theme="light"]`), `#btn-theme-toggle`, and consistent navigation links.

## Boundaries and recovery

- **SQL dialect portability (`REQ-073`)**: `list_agents_with_stats` runs simple portable `GROUP BY` queries without vendor-specific SQL extensions.
- **No accidental secret exposure (`REQ-073`, `REQ-075`)**: Only `admin_mint_agent` and `rotate_agent_key` return a raw one-time 3-word key (over HTTPS to an authenticated `ADMIN`); `list_agents` and `/admin` SSR never emit raw tokens or keys.
- **Private export containment (`REQ-074`)**: Export compilation uses `self.private_books_dir / "exports"` and asserts all chapters are `accepted` (`409` otherwise).
