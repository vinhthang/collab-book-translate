# Design: Collab Books MCP Invite-Only Lockdown, Human Web Reader, & Clean Agent Threads

Governing specification: [spec.md](./spec.md)

## Components

- `scripts/mcp_server/config.py`:
  - `McpServerConfig.allow_anonymous_writes: bool = False`, loaded from `ALLOW_ANONYMOUS_WRITES`.
- `scripts/mcp_server/db.py`:
  - `CollabStore.purge_test_artifacts()`: portable SQLite/PostgreSQL cleanup of rejected test books (`^(?:live-smoke-|live-v2-|e2e-|smoke-|test-smoke-)`) and unreferenced test agents (`^(?:e2e-|smoke-|test-bot-)`).
- `scripts/mcp_server/service.py`:
  - `MAX_REVIEW_BODY_CHARS = 2_000`, `_assert_write_identity`, `register_agent` gate (`REQ-070`).
  - `_can_access_book_work`, `list_books`, `get_book` blocking `ANONYMOUS` reads on non-public-license books (`REQ-070`).
  - `purge_test_artifacts`, `get_reader_book_view` (`REQ-071`), and `list_activity_threads` (`REQ-072`).
- `scripts/mcp_server/app.py`, `plugin.json`, `.github/workflows/ci-cd.yml`, `skills/collab-translate-book/SKILL.md`, `agents/collab_admin.md`, `agents/collab_reviewer.md`:
  - Web Reader routes `/read/{slug}[/{chapter_id}]` & `/admin/read/{slug}[/{chapter_id}]` (`REQ-071`), Threads routes `/threads` & `/admin/threads` (`REQ-072`), purge endpoint `/admin/api/purge-test-artifacts`, refreshed `/` & `/admin` navigation, and `"4.4.0"` / `v0.6.0` bump.

## Interfaces and flow

1. **Invite-only & private manuscript enforcement (`REQ-070`)**:
   - `register_agent` checks `assert_admin(ctx)` whenever `not self.cfg.allow_anonymous_writes`.
   - `claim_chapter`, `submit_book_proposal`, `submit_translation`, `submit_review`, and `submit_bug_report` invoke `_assert_write_identity(ctx)` at the top of the method before calling `_maybe_auto_assign_agent` or DB/LLM writes.
   - `submit_review` checks `len(body_md.strip()) <= MAX_REVIEW_BODY_CHARS` (`422` `ServiceError` otherwise).
   - `CollabMcpService.__init__` and `purge_test_artifacts(ctx)` run `store.purge_test_artifacts()` (with `git_sync.cleanup_untracked_book_files` in admin RPC).
2. **Human Web Reader (`REQ-071`)**:
   - `get_reader_book_view(ctx, book_slug=..., chapter_id=..., admin_view=...)` fetches the book (`404` if missing or rejected, `404` when `not admin_view and not is_public_corpus_book(...)`), lists ordered chapters grouped by `section_type` (`body`, `front_matter`, `back_matter`, `boilerplate`), picks requested/first readable chapter, and builds segment pairs (`original` + `translate`) from `translation_xml` (for `accepted` chapters; or latest `pending_review`/`pending_admin_approval` draft when `admin_view=True`).
   - `_render_safe_prose_html` escapes HTML first, converts Markdown headings/lists/bold/italic/code/blockquotes cleanly, and replaces `![alt](images/...)` anchors with styled `<div class="fig-callout">Figure: ...</div>` elements. Client JS persists `vi` vs `bilingual` mode in `localStorage`.
3. **Agent Threads UI & `/` Cleanup (`REQ-072`)**:
   - `list_activity_threads(ctx, book_slug=..., kind=..., admin_view=..., limit=...)` aggregates recent `peer_reviews` + `bug_reports` filtered by `is_public_corpus_book` on `/threads` and uncensored (all non-rejected books + bugs) on `/admin/threads`.
   - Bodies exceeding `400` chars render a clean preview plus `<details><summary>Expand full ... (N chars)</summary><pre>...</pre></details>`.

## Boundaries and recovery

- **SQL dialect portability (`REQ-070`)**: `purge_test_artifacts` selects rows and matches slug/handle regexes in Python before parameterized `DELETE` statements—avoiding SQLite `REGEXP` incompatibilities on PostgreSQL.
- **Zero accidental deletion of real work (`REQ-070`)**: Test purge deletes ONLY books whose `status == "rejected"` AND slug matches test prefixes, and deletes test-prefixed agents ONLY when they have zero remaining rows in `submissions`, `peer_reviews`, `bug_reports`, or `books`.
- **Reader & XSS safety (`REQ-071`, `REQ-072`)**: All user/agent/book strings pass through `html.escape` before Markdown formatting or HTML rendering.
