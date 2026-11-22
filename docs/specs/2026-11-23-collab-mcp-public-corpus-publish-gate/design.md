# Design: Collab Books MCP Public-Corpus Publish Gate & Non-Public Storage Isolation

Governing specification: [spec.md](./spec.md)

## Components

- `scripts/mcp_server/git_sync.py`:
  - `PUBLIC_CORPUS_LICENSES = frozenset({"public-domain", "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0"})`
  - `is_public_corpus_book(*, license_id: str, access_mode: str = "public") -> bool`
  - `CollabBooksGitSync.private_books_dir` (`cfg.books_repo_dir.parent / "private-books"`), `_storage_root(public_corpus=...)`, `_write_run_chapter_artifacts(...)`, `evict_to_private_storage(slug=...)`, `reconcile_origin_and_evict_private(non_public_slugs=...)`
- `scripts/mcp_server/db.py`:
  - `CollabStore.bootstrap_from_books_dir` skips any manifest whose license is not in `PUBLIC_CORPUS_LICENSES`.
- `scripts/mcp_server/service.py`:
  - `_book_is_public_corpus(book)` helper shared across `CollabMcpService.__init__`, `list_books`, `get_book`, `submit_book_proposal`, `submit_book_parse`, `import_chapter_translations`, `update_book_record`, `set_book_access`, `approve_book_intake`, `reject_book_proposal`, `_finalize_accepted_chapter`, and `publish_release`.
- `scripts/mcp_server/app.py`, `plugin.json`, `.github/workflows/ci-cd.yml`, `skills/collab-translate-book/SKILL.md`, `agents/collab_admin.md`, `agents/collab_reviewer.md`:
  - `bookshelf_index` hides non-public-license (`owner-permission`) books; `_render_admin_book_card` handles `"completed"` badge and suppresses release publish trigger when `not public_corpus`; `create_app` runs startup reconciliation; service/plugin advance to `"4.3.1"` (`v0.5.1`).

## Interfaces and flow

1. **Non-public book write & accept flow (`public_corpus=False`) (`REQ-068`)**:
   - `submit_book_proposal`, `submit_book_parse`, `update_book_record`, `import_chapter_translations`, and `approve_book_intake` compute `pub_corpus = _book_is_public_corpus(...)` and pass `public_corpus=pub_corpus` into `CollabBooksGitSync`.
   - `sync_accepted_chapter(..., public_corpus=False)` writes chapter outputs, `checklist.json`, and `collab_ledger.json` under `private_books_dir / "runs" / slug`, acquires `self._lock`, updates files atomically, and returns `""` with zero Git or GitHub API calls.
   - `_finalize_accepted_chapter` transitions `all_done` books to `status="completed"` (`release_tag=""`, `release_url=""`) when `not pub_corpus`, skipping `compile_and_publish_release`.
2. **Startup origin reconciliation & UI gating (`REQ-069`)**:
   - Invoked during `create_app` (and `evict_to_private_storage` in `CollabMcpService.__init__`) before `bootstrap_from_books_dir`: copies any `books/<slug>` and `runs/<slug>` matching `non_public_slugs` into `private_books_dir`, resets local `main` to `FETCH_HEAD` after `git fetch origin main` if `origin` exists, and deletes leftover untracked non-public directories under `books_repo_dir`.
   - `GET /` filters `books` to `PUBLIC_CORPUS_LICENSES`, while `/admin` styles `completed`, labels non-public books, omits `js-release-book` when `not public_corpus`, and advances to `"4.3.1"`.
3. **Access toggle & release gate (`REQ-068`)**:
   - `set_book_access` transitioning a book to `restricted` triggers `evict_to_private_storage(slug=book_slug)`.
   - `publish_release` rejects any book with `not _book_is_public_corpus(book)` (`409`).

## Boundaries and recovery

- **Offline / test bare-remote safety**: If `origin` is absent or temporarily unreachable during `git fetch origin main`, `reconcile_origin_and_evict_private` logs a warning and still completes local non-public eviction cleanly without aborting server startup.
- **Ledger read continuity**: `read_ledger(slug, public_corpus=...)` checks `private_books_dir` first when `not public_corpus`, falling back to legacy `books_repo_dir` if present.
- **No accidental untrack errors**: `evict_to_private_storage` copies into `private_books_dir` cleanly without clobbering existing files in `private_books_dir` and only removes untracked directories in `books_repo_dir`.
