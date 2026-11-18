# Verification: Collaborative Multi-Agent Book Translation on GitHub

Governing specification: [spec.md](./spec.md)

## Planned checks

### AC-016-1 (REQ-016)

- Method: Run `uv run pytest tests/test_collab_config.py -v` and inspect `git ls-files config.json config.example.json plugin.json`, `.gitignore`, and `rg "(\brtk\b|/Users/)" scripts/ tests/ plugin.json config.example.json .github/ CONTRIBUTING.md`.
- Expected outcome: All 14 typed `CollabConfig` fields load cleanly, `config.json` is excluded from Git tracking while `config.example.json` and `plugin.json` are tracked, and zero `rtk` or `/Users/` strings exist in executable/config/template files.
- Execution: Not run.

### AC-016-2 (REQ-016)

- Method: Run `uv run pytest tests/test_collab_config.py -v` and invoke `COLLAB_CONFIG_PATH=/nonexistent/config.json uv run python scripts/collab_sweep.py`.
- Expected outcome: Missing files, malformed JSON, missing/empty keys, invalid `repo_full_name` / `evaluation_engine` / `release_formats`, non-positive numeric bounds, or `bot_login == human_approver` raise `ConfigError` and exit non-zero without fallback.
- Execution: Not run.

### AC-017-1 (REQ-017)

- Method: Run `uv run pytest tests/test_collab_intake.py -v` covering valid `en->vi` and `zh->vi` `book/<slug>` submissions and inspect `.github/PULL_REQUEST_TEMPLATE/book.md` and `CONTRIBUTING.md`.
- Expected outcome: Valid new-slug submissions with one non-LFS readable source file $\le$ `max_source_bytes` pass intake validation, and contributor templates contain no Git LFS instructions.
- Execution: Not run.

### AC-017-2 (REQ-017)

- Method: Run `uv run pytest tests/test_collab_intake.py -v` with invalid slug characters, pre-existing `books/<slug>/` or `runs/<slug>/` directories, Git LFS pointer files, zero-segment or $> 2,000$-segment or $> 2,621,440$-byte extracted chapters, unreadable/oversize files, non-`https://` `license_url`, and $\ge 5\%$ Vietnamese diacritic density.
- Expected outcome: Every invalid stimulus returns `valid == False` with explicit defect messages and blocks merge/seeding.
- Execution: Not run.

### AC-018-1 (REQ-018)

- Method: Run `uv run pytest tests/test_collab_sweep.py tests/test_collab_intake.py -v` testing latest `bot_login`-authored `[collab-prescreen ...]` with commit-pinned `APPROVED` reviews (`review.commit.oid == head_oid` when `pr.author != human_approver`) and `/approve-intake <head_oid>` comments (`pr.author == human_approver`), and inspect `CONTRIBUTING.md` vs `config.example.json`.
- Expected outcome: Both third-party and owner-authored book PRs merge cleanly with `--match-head-commit <head_oid>` after `bot_login` pre-screen `PASS`, and `CONTRIBUTING.md` lists the default `allowed_licenses` matching `config.example.json`.
- Execution: Not run.

### AC-018-2 (REQ-018)

- Method: Run `uv run pytest tests/test_collab_sweep.py -v` with failing pre-screens, forged non-`bot_login` `[collab-prescreen ...]` comments, bot-only approvals, non-approver comments, stale `APPROVED` reviews (`commit.oid != head_oid`), and `/approve-intake` comments referencing an older SHA.
- Expected outcome: None of the forged, unauthorized, or stale approvals trigger `merge_pr`.
- Execution: Not run.

### AC-019-1 (REQ-019)

- Method: Run `uv run pytest tests/test_collab_intake.py -v` for `("en", "vi")` and `("zh", "vi")` manifests.
- Expected outcome: Both v1 pairs pass `validate_submission_pr`.
- Execution: Not run.

### AC-019-2 (REQ-019)

- Method: Run `uv run pytest tests/test_collab_intake.py -v` with unsupported pairs (`fr->vi`, `en->ja`).
- Expected outcome: Validation fails with `unsupported pair` and zero chapter issues are created.
- Execution: Not run.

### AC-020-1 (REQ-020)

- Method: Run `uv run pytest tests/test_collab_seed.py -v` exercising extraction, `initialize_book_record`, live `assert_fiction_pronouns_populated`, bot `ch01` seed PR (`Issue: seed`, `Glossary SHA: seed`), bound-glossary audit comment, live `PASS` merge (`selected_pr > 0`), canonical SHA-256 `glossary_sha`, and `ch02..chNN` issue creation.
- Expected outcome: `seeded == True`, `book_record.json` contains populated `brief`/pronouns/glossary + 64-char `glossary_sha`, `ch01` seed PR merges with the audit comment, `collab_ledger.json` records `selected_pr > 0`, `checklist.json` records `seed_status == "seeded"`, and `ch02..chNN` issues open once each.
- Execution: Not run.

### AC-020-2 (REQ-020)

- Method: Run `uv run pytest tests/test_collab_seed.py tests/test_collab_sweep.py -v` simulating zero-segment extraction, `assert_fiction_pronouns_populated` failure, and fallback/`FAIL` `ch01` evaluation, followed by a second sweep.
- Expected outcome: `ch01` is not merged (open seed PR closed), zero `ch02..chNN` issues open, `checklist.json` preserves `chapters` while setting `seed_status == "blocked_failed"`, and the subsequent sweep skips seeding `<slug>`.
- Execution: Not run.

### AC-021-1 (REQ-021)

- Method: Run `uv run pytest tests/test_collab_pr_validate.py -v` with competing `ch02..chNN` PRs and bot `ch01` seed PRs (`Issue: seed`, `Glossary SHA: seed`) satisfying caps, `manifest.json`/`source.txt` cross-checks, `<original>` byte-identity, `<translate>`/`<hanviet>` completeness, and `<translate>` code/image byte-invariance.
- Expected outcome: Both `ch01` seed PRs and competing `ch02..chNN` PRs pass `validate_translation_pr`.
- Execution: Not run.

### AC-021-2 (REQ-021)

- Method: Run `uv run pytest tests/test_collab_pr_validate.py -v` testing disallowed paths, `ch02-p1` branches, $> 5\text{ MB}$ or $> 2,000$-segment `translation.xml`, $> 512\text{ KB}$ `analyze_additions.json`, spoofed `Source language: en` on a `zh` manifest, mismatched `Segments`/`Issue`/`glossary_sha`, mutated `<original>`, mutated code blocks or `![...](...)` anchors inside `<translate>`, and missing `zh` `<hanviet>`.
- Expected outcome: Every violation returns `valid == False` and blocks review, LLM evaluation, and merge.
- Execution: Not run.

### AC-022-1 (REQ-022)

- Method: Run `uv run pytest tests/test_collab_pr_validate.py -v` with non-author reviews providing inline diff comments or `S-NNNN` segment notes and all 6 rubric dimensions in `[1.0, 5.0]`.
- Expected outcome: `validate_review` returns `valid == True` with parsed 6-dimension scores.
- Execution: Not run.

### AC-022-2 (REQ-022)

- Method: Run `uv run pytest tests/test_collab_pr_validate.py -v` with self-reviews (`reviewer == author`), missing inline/`S-NNNN` comments, and missing or out-of-range dimension scores.
- Expected outcome: `validate_review` returns `valid == False` and the PR remains in `NeedsReview`.
- Execution: Not run.

### AC-023-1 (REQ-023)

- Method: Run `uv run pytest tests/test_collab_pr_validate.py tests/test_collab_sweep.py -v` testing clean pre-LLM execution, `/clear-flag <head_oid>` resuming a flagged PR, `/reject-flag <head_oid>` closing an abusive PR as `status:reassign`, and `commit_and_push` with `runs/<slug>/**` paths.
- Expected outcome: Clean PRs evaluate normally, `/clear-flag` and `/reject-flag` exit `FlaggedForHuman` deterministically, and `runs/<slug>/**` commits succeed.
- Execution: Not run.

### AC-023-2 (REQ-023)

- Method: Run `uv run pytest tests/test_collab_pr_validate.py tests/test_collab_verdict.py tests/test_collab_sweep.py -v` injecting jailbreak payloads into `<original>`, `<hanviet>`, `<translate>`, PR body, `analyze_additions.json`, and reviews, $> 1.0$ score divergence, non-approver `/clear-flag`, and `commit_and_push` staging `scripts/collab_sweep.py`.
- Expected outcome: Un-cleared injection or divergence holds the PR in `FlaggedForHuman` (`status:flagged`), non-approver `/clear-flag` is ignored, and out-of-bounds staging raises `ValueError`.
- Execution: Not run.

### AC-024-1 (REQ-024)

- Method: Run `uv run pytest tests/test_collab_verdict.py tests/test_collab_sweep.py -v` with live evaluations meeting `3.5` (`fiction`/`literary`) and `4.0` (`general`) thresholds, `0` critical issues, non-`FAIL`/`CONTESTED` verdict, and zero blocking `CHANGES_REQUESTED` reviews.
- Expected outcome: PR transitions to `Candidate` (`status:candidate`) and posts `[collab-candidate-score sha=<head_oid> score=... major=... minor=...]`.
- Execution: Not run.

### AC-024-2 (REQ-024)

- Method: Run `uv run pytest tests/test_collab_verdict.py tests/test_collab_sweep.py -v` with evaluator timeouts/exceptions, `fallback_unverified == True`, `< 2.5` redraft scores, `CONTESTED`/critical-issue failures, and active `CHANGES_REQUESTED` reviews (with and without per-reviewer `/dismiss-stale-review <rev_login> <head_oid>`).
- Expected outcome: Evaluator errors and `fallback_unverified` keep the PR in `Evaluating` (`status:evaluating`) without incrementing `revise_round_count`; redraft closes as `Reassign`; other failures or un-dismissed `CHANGES_REQUESTED` route to `Revise`.
- Execution: Not run.

### AC-025-1 (REQ-025)

- Method: Run `uv run pytest tests/test_collab_pr_validate.py tests/test_collab_sweep.py -v` reading canonical `proposals` from `<winner_head_oid>` and binding into `book_record.json`.
- Expected outcome: Non-conflicting terms from `<winner_head_oid>` bind into `book_record["glossary"]` and `book_record["terms"]` (`status: "bound"`) while `book_record["glossary_sha"]` remains unchanged.
- Execution: Not run.

### AC-025-2 (REQ-025)

- Method: Run `uv run pytest tests/test_collab_pr_validate.py tests/test_collab_sweep.py -v` with malformed proposal schemas, competing losing-PR proposals in the working tree, and case-folded conflicts against `book_record.json`.
- Expected outcome: Malformed schemas fail `REQ-021`, losing-PR proposals are never bound, and merge-time conflicts return `<winner_head_oid>` to `Revise` without merging.
- Execution: Not run.

### AC-026-1 (REQ-026)

- Method: Run `uv run pytest tests/test_collab_select.py tests/test_collab_sweep.py -v` testing multi-candidate selection after `window_hours` from `[collab-window-start ...]` and 48 h starved-chapter automatic `translate_chapter` bot PR creation.
- Expected outcome: Top-ranked candidate merges, losing PRs close as `status:not-selected`, and starved chapters open a `REQ-021`-compliant bot PR tracked via `[collab-fallback pr=#<pr> attempt=1]`.
- Execution: Not run.

### AC-026-2 (REQ-026)

- Method: Run `uv run pytest tests/test_collab_select.py tests/test_collab_sweep.py -v` testing missing `[collab-window-start ...]` initialization, `(major_issue_count, minor_issue_count, pr_number)` tie-breaks, open-fallback deduplication, `attempt=2` retry after closed `attempt=1`, and `[collab-starved-escalated]` halt after `2` closed attempts.
- Expected outcome: Missing window markers initialize to `cur_now` without immediate merge, ties break deterministically, and fallback retries stop at `2` attempts with `[collab-starved-escalated]`.
- Execution: Not run.

### AC-027-1 (REQ-027)

- Method: Run `uv run pytest tests/test_collab_state.py tests/test_collab_sweep.py -v` across pre-issue `checklist.json` states (`Seeding`, `SeedBlocked`), 3 issue states, 9 PR states, unchanged `[collab-revise sha=<head_oid>]` re-sweeps, spoofed non-bot revise comments, and pushes `1..max_revise_rounds`.
- Expected outcome: Labels match all issue-bearing states, `revise_round_count` counts only distinct bot-authored `[collab-revise sha=...]` SHAs, and valid revision pushes re-enter `NeedsChecks`.
- Execution: Not run.

### AC-027-2 (REQ-027)

- Method: Run `uv run pytest tests/test_collab_state.py tests/test_collab_sweep.py -v` on the `(max_revise_rounds + 1)`-th distinct failing commit and on manual `status:*` label tampering.
- Expected outcome: Exhausted PRs close as `status:reassign` while the chapter issue stays open, and tampered `status:*` labels are overwritten while `book:*`/`lang:*`/`agent:*` labels remain intact.
- Execution: Not run.

### AC-028-1 (REQ-028)

- Method: Run `uv run pytest tests/test_collab_ledger.py -v` on both v2.0 `selected_pr > 0` ledgers and existing `runs/scandal-in-bohemia/collab_ledger.json`.
- Expected outcome: Both v2.0 ledgers and published `scandal-in-bohemia-v1.0` pass `verify_ledger_integrity`.
- Execution: Not run.

### AC-028-2 (REQ-028)

- Method: Run `uv run pytest tests/test_collab_ledger.py -v` with missing chapters, `selected_pr <= 0` on an unreleased book, empty `reviewer_logins` on `ch02`, and `translator_login in reviewer_logins`.
- Expected outcome: `verify_ledger_integrity` returns `valid == False`, flags unaudited chapters, and blocks publication.
- Execution: Not run.

### AC-029-1 (REQ-029)

- Method: Run `uv run pytest tests/test_collab_completeness.py tests/test_collab_sweep.py -v` on completed envelope and list `checklist.json` run directories (including existing-tag backfill recovery).
- Expected outcome: Compiles non-empty deliverables for every configured `release_formats` entry into gitignored `release_assets/`, publishes `<slug>-v1.0` (or backfills `released_tag` if already published), and tracks zero binaries in Git.
- Execution: Not run.

### AC-029-2 (REQ-029)

- Method: Run `uv run pytest tests/test_collab_completeness.py -v` with `seed_status == "blocked_failed"`, empty `chapters`, incomplete statuses, missing `final.txt`, parity/ledger defects, and git-tracked binaries.
- Expected outcome: `ready_to_publish == False` with defects reported and zero releases created.
- Execution: Not run.

### AC-030-1 (REQ-030)

- Method: Run `uv run pytest tests/test_collab_sweep.py -v` testing single-instance mutual exclusion, `--recover-runs` on dirty `runs/**`, fast-forward `origin/main` sync before sweep and after merges, and multi-sweep idempotency.
- Expected outcome: Clean `main` (or `--recover-runs` `runs/**`) fast-forwards cleanly, post-merge commits land atop merged PR commits, and repeated sweeps produce zero duplicate side effects.
- Execution: Not run.

### AC-030-2 (REQ-030)

- Method: Run `uv run pytest tests/test_collab_sweep.py -v` with active concurrent sweep lock, dirty tracked files outside `runs/`, dirty `runs/**` without `--recover-runs`, off-`main` branch, and non-fast-forward `origin/main`.
- Expected outcome: Concurrent sweep logs `"sweep already running"` and exits `0`; dirty/off-`main`/diverged checkout aborts non-zero before touching Git or GitHub.
- Execution: Not run.

## Execution evidence

No checks have been executed.

## Real-world observations

No real-world confirmation has been recorded.
