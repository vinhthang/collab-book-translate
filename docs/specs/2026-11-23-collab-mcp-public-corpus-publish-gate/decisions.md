# Decisions: Collab Books MCP Public-Corpus Publish Gate & Non-Public Storage Isolation

Governing specification: [spec.md](./spec.md)

## Architectural rationale

- Governs: REQ-069

Given existing Kubernetes PVC deployments where `books_repo_dir` persists across container restarts, aligning `books_repo_dir` to `origin/main` via `git fetch origin main && git checkout -f -B main FETCH_HEAD` at startup (after copying non-public working directories into `private-books/`) and skipping non-public manifests in `bootstrap_from_books_dir` is the only safe way to ensure remote history resets on `vinhthang/collab-books` cannot be undone by `git pull --rebase` when the next `public-domain` chapter is accepted. Filtering `GET /` by `license in PUBLIC_CORPUS_LICENSES` while adding `public_corpus: bool` to `list_books` / `get_book` preserves backward compatibility for existing `REQ-045` `restricted` open-license tests while hiding copyrighted `owner-permission` titles from the public HTML storefront.

## ADR-039: Isolate Non-Public Corpus Files in Sibling `private-books/` Directory Outside the `collab-books` Git Tree

- Governs: REQ-068
- State: Accepted
- Context and constraints: `CollabBooksGitSync` persists `source.txt`, `translation.xml`, `final.txt`, `book_record.json`, `brief.json`, `checklist.json`, and `collab_ledger.json` per slug so `get_book(slug)["collab_ledger"]` works reliably across chapters. Storing `owner-permission` or `restricted` book files inside `books_repo_dir` creates constant risk of accidental `git add`, `git status` contamination, or re-import during startup bootstrap.
- Viable options:
  1. Store all non-public-corpus book artifacts in a sibling directory `cfg.books_repo_dir.parent / "private-books"` outside the `.git` worktree of `collab-books`, and short-circuit all Git / GitHub Release operations when `not public_corpus`.
  2. Keep writing non-public book directories inside `books_repo_dir` and add dynamic `.gitignore` entries per non-public slug.
  3. Store zero filesystem files for non-public books and rewrite `read_ledger` to query PostgreSQL/SQLite tables instead of `collab_ledger.json`.
- Decision: Choose Option 1 (physical `private-books/` sibling isolation + zero Git operations).
- Costs and risks: Requires a one-time startup migration helper (`evict_to_private_storage` / `reconcile_origin_and_evict_private`) to preserve existing `collab_ledger.json` and `runs/<slug>` state already written on disk by `v0.4.0` pods before deleting legacy directories from `books_repo_dir`. Mitigated cleanly with non-destructive copy-first migration before cleaning `books_repo_dir`.
- Rejected alternatives: Option 2 leaves private copyrighted prose physically inside a public git checkout and fails if a directory was already committed earlier (`.gitignore` does not untrack committed paths); Option 3 breaks ledger file parity for private volumes and requires a new schema migration just for archival metadata.
- Affected components: `scripts/mcp_server/git_sync.py`, `scripts/mcp_server/service.py`, `scripts/mcp_server/db.py`, `scripts/mcp_server/app.py`.

## Gate 1: normative review

| Check | Status | Evidence / required correction |
| --- | --- | --- |
| Codebase grounding | PASS | Inspected `scripts/mcp_server/{git_sync,db,service,app}.py` at `b004ac9` and live `vinhthang/collab-books` git ref state. |
| Attributable intent and explicit scope | PASS | Grounded in verbatim prompt and resolved choices `A1`/`A2`; non-goals preserve MCP review for `owner-permission` and Git sync for public corpus. |
| Observable requirements and meaningful acceptance criteria | PASS | `REQ-068..REQ-069` define `PUBLIC_CORPUS_LICENSES`, `private-books/` paths, zero Git push guarantees, `"completed"` transition, `409` boundary, HTML filtering, and startup reconciliation. |
| Assumptions and owner-deferred questions are explicit | PASS | Behavior of `GET /` for `owner-permission` vs `restricted` open-license books stated explicitly; zero `[PROVISIONAL]` items. |
| Architectural rationale respects requirements | PASS | `ADR-039` and rationale map strictly to `REQ-068` and `REQ-069`. |
| IDs, references, and revision route are consistent | PASS | Linked delta package uses `REQ-068..REQ-069` and `ADR-039` above `REQ-067`/`ADR-038`. |
