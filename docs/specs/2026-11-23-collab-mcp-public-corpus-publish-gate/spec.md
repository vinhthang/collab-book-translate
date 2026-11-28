# Collab Books MCP Public-Corpus Publish Gate & Non-Public Storage Isolation (`collab-mcp:v0.5.1`)

- Feature ID: 2026-11-23-collab-mcp-public-corpus-publish-gate
- Type: enhancement
- Status: In-Testing
- First entered In-Testing: 2026-11-23
- Related specifications: Implemented: Changes [2026-11-19-collab-mcp-and-books-split](../2026-11-19-collab-mcp-and-books-split/spec.md), Changes [2026-11-19-collab-mcp-onboarding-and-book-access](../2026-11-19-collab-mcp-onboarding-and-book-access/spec.md), Changes [2026-11-22-collab-mcp-reviewer-queue-and-matter-flow](../2026-11-22-collab-mcp-reviewer-queue-and-matter-flow/spec.md)

## Problem and source intent

`vinhthang/collab-books` is a public corpus repository reserved strictly for open-license books (`public-domain`, `CC0-1.0`, `CC-BY-4.0`, `CC-BY-SA-4.0` with `access_mode="public"`). Currently:
1. `CollabMcpService._finalize_accepted_chapter` (`scripts/mcp_server/service.py:2537-2579`) and `CollabBooksGitSync.sync_accepted_chapter` / `compile_and_publish_release` (`scripts/mcp_server/git_sync.py:210-366`) unconditionally stage, commit, and push every accepted chapter under `books/<slug>` and `runs/<slug>` to `vinhthang/collab-books` (`main`) and publish a GitHub Release on completion—causing copyrighted `owner-permission` books (`the-changing-world-order` `ch01..ch04`) to be pushed to the public GitHub repository.
2. Working directories (`books/<slug>`, `runs/<slug>`) for `owner-permission` and `restricted` books are written directly inside `cfg.books_repo_dir` (`collab-books` checkout), risking accidental staging/bootstrapping on `vinhthang/collab-books` or persistent pod checkout drift after remote history resets.
3. Public HTML bookshelf `GET /` (`scripts/mcp_server/app.py:1867-1878`) lists `owner-permission` books publicly alongside public-domain titles, and `/admin` shows the `Compile & Publish Release` button even for non-public books.

Authorizing user intent (2026-11-23, verbatim prompt + resolved choices `A1`, `A2`):
- Prompt: *"and do not publish the book like ray dalio ones, need to delete from https://github.com/vinhthang/collab-books, these are for public books"*
- **A1 (GitHub Cleanup)**: Reset `vinhthang/collab-books` `main` back to `21a213c6a5417725fc0197dcbe6e476f5b414136` (`gift-of-the-magi` ch01) so `the-changing-world-order` commits are purged from public Git history.
- **A2 (Non-Public Books Policy)**: Keep `owner-permission` books open for MCP/REST translation and peer review on `collab.vinhthang.dev`, never sync/commit/push/release them to `github.com/vinhthang/collab-books` (marking `status="completed"` instead of `"published"`), and hide them from the public `/` HTML bookshelf.

## Repository grounding

Inspected at `/Users/thanghoang/github/collab-book-translate` (`b004ac9`):
- `scripts/mcp_server/git_sync.py:75-427`: writes `books/<slug>` and `runs/<slug>` inside `cfg.books_repo_dir`, stages/commits/pushes on every `sync_accepted_chapter`, and uploads assets via `compile_and_publish_release`.
- `scripts/mcp_server/db.py:983-1052`: `CollabStore.bootstrap_from_books_dir` imports all `books/*/manifest.json` without checking `manifest["license"]`.
- `scripts/mcp_server/service.py:1553-1710, 1846-2053, 2505-2580, 3060-3140, 3411-3426`: passes all books through `git_sync`, sets `new_book_status="published"` on completion regardless of license/access, and invokes `compile_and_publish_release`.
- `scripts/mcp_server/app.py:628-681, 962-1028, 1223-1250, 1867-1909`: clones `books_repo_dir` without reconciling force-reset `origin/main` or evicting private directories, renders all non-rejected books on `GET /`, renders `Compile & Publish Release` in `/admin` for all completed books, and reports `"4.3.0"`.

## Non-goals

- **No disabling of MCP peer review for `owner-permission` books**: Agents must still list, claim, translate, import, and peer-review `owner-permission` chapters via MCP/REST (`list_books`, `get_book`, `list_tasks`, `get_chapter`, `submit_translation`, `submit_review`, `import_chapter_translations`).
- **No change to public-domain Git sync & GitHub Releases**: Open-license public books (`public-domain`, `CC0-1.0`, `CC-BY-4.0`, `CC-BY-SA-4.0` with `access_mode="public"`) continue syncing to `vinhthang/collab-books` `main`, compiling EPUB/PDF/DOCX, and creating GitHub Releases unchanged.

## Requirements

### REQ-068: Public-Corpus Publishing Eligibility, `private-books` Isolation, & Zero Git/Release Sync for Non-Public Books

- Current contract: Changed by [2026-11-27-collab-mcp-security-scalability-and-ci-hardening/REQ-084](../2026-11-27-collab-mcp-security-scalability-and-ci-hardening/spec.md#req-084-persistent-postgresql-persistence-atomic-transactions-secondary-indexes-o1-list_books-offloaded-middleware-presence--git-before-db-acceptance)
- Changes: [2026-11-19-collab-mcp-and-books-split/REQ-042](../2026-11-19-collab-mcp-and-books-split/spec.md#req-042-ch01-seeding-starved-chapter-fallback-apiv1translate-and-collab-books-gitrelease-sync)
- Source intent: Verbatim request (*"do not publish the book like ray dalio ones, need to delete from https://github.com/vinhthang/collab-books, these are for public books"*) and `A2`.
- Previous contract: `CollabBooksGitSync` wrote every book into `cfg.books_repo_dir` and committed/pushed accepted chapters and GitHub Releases to `vinhthang/collab-books` regardless of `license_id` or `access_mode`; completing all chapters always set `status="published"` and published a GitHub Release.
- Statement: `scripts/mcp_server/git_sync.py` SHALL define `PUBLIC_CORPUS_LICENSES = frozenset({"public-domain", "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0"})` and `is_public_corpus_book(*, license_id: str, access_mode: str = "public") -> bool`, returning `True` iff `license_id in PUBLIC_CORPUS_LICENSES and access_mode == "public"`. WHEN a book is non-public (`is_public_corpus_book(...) == False`), `CollabBooksGitSync` and `CollabMcpService` SHALL isolate all working files (`books/<slug>`, `runs/<slug>`, `book_record.json`, `brief.json`, `checklist.json`, `collab_ledger.json`) under `cfg.books_repo_dir.parent / "private-books"` strictly outside the `collab-books` Git working tree, SHALL execute zero `git add` / `commit` / `push` or GitHub Release calls against `vinhthang/collab-books`, SHALL transition the book to `status="completed"` (`release_tag=""`, `release_url=""`) instead of `"published"` once all chapters are `accepted`, and SHALL return HTTP `409` on `publish_release`. Flipping an open book to `access_mode="restricted"` via `set_book_access` SHALL evict untracked files from `books_repo_dir` to `private-books/`.
- AC-068-1 — Success: Accepting all chapters (including auto-accepted boilerplate) of an `owner-permission` or `restricted` book writes `collab_ledger.json`, `checklist.json`, `translation.xml`, and `final.txt` under `private-books/runs/<slug>`, creates zero files under `books_repo_dir` and zero commits/pushes/releases on `origin` (`vinhthang/collab-books`), and transitions book `status` to `"completed"` (`public_corpus is False`, `release_url == ""`), while a `public-domain` `access_mode="public"` book continues committing, pushing, and releasing to `vinhthang/collab-books` with `status="published"`.
- AC-068-2 — Boundary/failure: Invoking `publish_release` on an `owner-permission` or `restricted` book raises `ServiceError` with `status_code == 409`; `reject_book_proposal` rejects `"completed"` books with `409`; `import_chapter_translations` preserves `"completed"` status rather than reverting to `"active"`.

### REQ-069: Public Bookshelf Filtering, `/admin` Policy Controls, Startup `origin/main` Reconciliation, & MCP `v4.3.1`

- Current contract: Changed by [2026-11-23-collab-mcp-reader-threads-and-invite-lockdown/REQ-072](../2026-11-23-collab-mcp-reader-threads-and-invite-lockdown/spec.md#req-072-read-only-agent-threads-ui-threads-public--adminthreads-sso-collapsible-long-reviews--v440-surface)
- Changes: [2026-11-19-collab-mcp-onboarding-and-book-access/REQ-046](../2026-11-19-collab-mcp-onboarding-and-book-access/spec.md#req-046-built-in-mcp-instructions-self-explanatory-tool-docstrings-mcpprompt-helpers-and--onboarding-card), [2026-11-22-collab-mcp-reviewer-queue-and-matter-flow/REQ-067](../2026-11-22-collab-mcp-reviewer-queue-and-matter-flow/spec.md#req-067-mcp-v430-surface-rest-pagination-full-id-admin-cards--collab_workerpy-cli-parity)
- Source intent: Verbatim request and `A2` (hide `owner-permission` books from public HTML bookshelf `/` while preserving MCP/REST review access and reconciling pod state after `vinhthang/collab-books` reset).
- Previous contract: `GET /` displayed all non-rejected books including `owner-permission`; `CollabStore.bootstrap_from_books_dir` imported any local manifest regardless of license; startup never aligned persistent `books_repo_dir` with a force-reset `origin/main` or evicted legacy private directories; `/admin` always rendered the release button on finished books and reported `"4.3.0"`.
- Statement: `GET /` (`bookshelf_index`) SHALL exclude `license == "owner-permission"` (non-public-license) books from the rendered public HTML table while `list_books`, `get_book`, `list_tasks`, and `/admin` continue exposing them with `"public_corpus": bool` (`False` for non-public books). `/admin` SHALL style `"completed"` badges (`badge-ok`), label non-public books clearly, and omit the `Compile & Publish Release` button when `not public_corpus` (passing `node --check`). On startup (`CollabMcpService.__init__` and `create_app`), `CollabBooksGitSync.reconcile_origin_and_evict_private` SHALL migrate non-public `books/<slug>`/`runs/<slug>` directories into `private-books/`, force-align `books_repo_dir` `main` to `FETCH_HEAD` when `origin/main` exists (so purged commits cannot be re-pushed on subsequent public-book syncs), and `CollabStore.bootstrap_from_books_dir` SHALL skip non-public-license manifests. Server, `plugin.json`, `.github/workflows/ci-cd.yml` (`v0.5.1`), `SKILL.md`, `agents/collab_admin.md`, and `agents/collab_reviewer.md` SHALL advance to `"4.3.1"`.
- AC-069-1 — Success: After simulating a remote `origin/main` reset back past a legacy `owner-permission` commit sitting on `books_repo_dir`, `create_app()` migrates the private ledger to `private-books/`, aligns `books_repo_dir` to `origin/main` (leaving zero private files in `books_repo_dir`), and subsequent acceptance of a `public-domain` chapter pushes cleanly without re-introducing purged history; `GET /` hides `owner-permission` books while `GET /api/v1/books` and `/admin` list them (`public_corpus: False`); `/api/health`, `/llms.txt`, `plugin.json`, and MCP `initialize` report `"4.3.1"`.
- AC-069-2 — Boundary/failure: When offline (`origin` unreachable), `reconcile_origin_and_evict_private` still migrates/evicts non-public directories safely without crashing startup; `bootstrap_from_books_dir` ignores any leftover `owner-permission` `manifest.json`; `/admin` JS passes `node --check` and `tests/test_option_a_hygiene.py` passes.

## Assumptions and owner-deferred questions

- `GET /` hides books whose `license` is not in `PUBLIC_CORPUS_LICENSES` (i.e. `owner-permission` titles like Ray Dalio books). Open-license books temporarily set to `access_mode="restricted"` retain their existing `REQ-045` summary visibility on `/api/v1/books` and `GET /` while still being blocked from `vinhthang/collab-books` Git sync and GitHub Releases (`public_corpus=False`).

## Approvals and lifecycle history

- `2026-11-23`: `Planning -> In-Progress` — User authorized resetting `vinhthang/collab-books` `main` to `21a213c6a5` (`A1`) and keeping `owner-permission` books open for MCP peer review while blocking Git/GitHub Releases (`status="completed"`) and hiding them from `GET /` (`A2`); executed plan approved for `collab-mcp:v0.5.1`.
- `2026-11-23`: `In-Progress -> In-Testing` — Completed Tasks 1–3 (`352 passed, 0 failed, 0 skipped` full suite, `test_option_a_hygiene.py` `2 passed`, `mutation-guard` `80.0%` (`8/10`) on `git_sync.py` and `80.0%` (`12/15`) on `service.py`, `node --check` exit `0`, single-agent `code-review` `Approved` `0` P0/P1) and updated 1-hop `Current contract` links across ancestors. See [Run 1](./verification.md#run-1--2026-11-23).
