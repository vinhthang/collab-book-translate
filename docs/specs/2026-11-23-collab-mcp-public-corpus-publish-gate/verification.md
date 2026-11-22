# Verification: Collab Books MCP Public-Corpus Publish Gate & Non-Public Storage Isolation

Governing specification: [spec.md](./spec.md)

## Planned checks

### AC-068-1 (REQ-068)

- Method: Run `pytest tests/test_mcp_public_corpus_gate.py -k req_068` exercising `owner-permission` and `restricted` books against a bare `origin.git` remote alongside a `public-domain` positive control.
- Expected outcome: Non-public books store chapter artifacts and `collab_ledger.json` under `private-books/`, create zero files under `books_repo_dir` and zero commits/releases on `origin.git`, and reach `status == "completed"` with `release_url == ""` (`public_corpus is False`), whereas open `public-domain` books commit, push, and publish to `vinhthang/collab-books` with `status == "published"`.
- Execution: [Run 1](#run-1--2026-11-23)

### AC-068-2 (REQ-068)

- Method: Run `pytest tests/test_mcp_public_corpus_gate.py -k req_068` verifying boundary errors on `publish_release`, `reject_book_proposal`, and `import_chapter_translations`.
- Expected outcome: `publish_release` and `reject_book_proposal` raise `409` on non-public / completed books; `import_chapter_translations` preserves `"completed"` status cleanly.
- Execution: [Run 1](#run-1--2026-11-23)

### AC-069-1 (REQ-069)

- Method: Run `pytest tests/test_mcp_public_corpus_gate.py -k req_069` simulating a force-reset `origin.git` remote behind a dirty local `books_repo_dir` clone, plus full `/` HTML, `/api/v1/books`, `/admin` JS (`node --check`), `/api/health`, `/llms.txt`, and MCP `initialize` checks.
- Expected outcome: Startup aligns `books_repo_dir` to `origin/main` while migrating non-public ledgers to `private-books/`, subsequent public-domain pushes do not re-introduce purged commits, `GET /` hides `owner-permission` titles, MCP/REST/`/admin` retain access (`public_corpus: False`), and version reports `"4.3.1"`.
- Execution: [Run 1](#run-1--2026-11-23)

### AC-069-2 (REQ-069)

- Method: Run `pytest tests/test_mcp_public_corpus_gate.py -k req_069` testing offline `origin` tolerance, `bootstrap_from_books_dir` license filtering, `node --check`, and `tests/test_option_a_hygiene.py`.
- Expected outcome: Startup eviction succeeds offline, `bootstrap_from_books_dir` skips leftover `owner-permission` manifests, `/admin` JS passes `node --check`, and `tests/test_option_a_hygiene.py` passes cleanly.
- Execution: [Run 1](#run-1--2026-11-23)

## Execution evidence

### Run 1 — 2026-11-23

- Checked: REQ-068..REQ-069 / AC-068-1..AC-069-2
- Command: `pytest /Users/thanghoang/github/collab-book-translate/tests`, `pytest tests/test_option_a_hygiene.py`, `mutation_runner.py` (`git_sync.py`, `service.py`), `prepare_code_review.py` & `git diff --check` (exit code: `0`)
- Output excerpt:
  ```text
  2 passed in 3.13s (test_mcp_public_corpus_gate.py, incl. node --check exit 0)
  4 passed in 2.12s (test_mcp_reviewer_queue_and_matter.py, incl. exact boundary checks)
  352 passed, 6 warnings in 26.22s
  2 passed in 0.66s (test_option_a_hygiene.py)
  Mutation Score (git_sync.py): 80.0% (8/10 killed)
  Mutation Score (service.py): 80.0% (12/15 killed)
  Code Review: Approved (0 P0, 0 P1), git diff --check exit 0
  ```
- Result: AC-068-1 `PASS`, AC-068-2 `PASS`, AC-069-1 `PASS`, AC-069-2 `PASS` — All automated tests, JS syntax checks, hygiene scans, mutation gates (`80.0%`), and single-agent code review passed.

## Real-world observations

`vinhthang/collab-books` `main` reset to `21a213c6a5417725fc0197dcbe6e476f5b414136` and verified clean via GitHub API (`the-changing-world-order` purged). Live `collab.vinhthang.dev` rollout verification pending `v0.5.1` container deployment.
