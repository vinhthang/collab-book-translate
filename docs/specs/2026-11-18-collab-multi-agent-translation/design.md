# Design: Collaborative Multi-Agent Book Translation on GitHub

Sources: [spec.md](./spec.md), [decisions.md](./decisions.md)

## Components

| Requirement / AC | Path and symbol | Responsibility / change |
| --- | --- | --- |
| REQ-016 / AC-016-1, AC-016-2 | `.gitignore`, `plugin.json`, `config.example.json`, `scripts/collab_config.py::load_collab_config`, `scripts/utils/paths.py::get_workspace_root`, `tests/conftest.py` | Ignore and untrack local `config.json`, validate all 14 typed `CollabConfig` keys (`repo_full_name`, `evaluation_engine`, `release_formats`, `human_approver != bot_login`) with zero silent fallbacks, and provide isolated test config fixtures. |
| REQ-017 / AC-017-1, AC-017-2 | `scripts/collab_intake.py::validate_submission_pr`, `.github/PULL_REQUEST_TEMPLATE/book.md`, `CONTRIBUTING.md` | Validate `book/<slug>` branch, slug regex `^[a-z0-9]+(?:-[a-z0-9]+)*$` (1–64 chars), absence of `books/<slug>/` and `runs/<slug>/` on `main`, single non-LFS source file $\le$ `max_source_bytes` extracting $\ge 1$ chapter where every chapter has `1..2,000` segments and $\le 2,621,440$ UTF-8 bytes, `manifest.json` schema (`https://` `license_url`), and language/diacritic ratios; remove LFS wording from templates. |
| REQ-018 / AC-018-1, AC-018-2 | `scripts/collab_intake.py::format_prescreen_comment`, `scripts/collab_sweep.py::run_sweep`, `CONTRIBUTING.md` | Post and read latest `bot_login`-only `[collab-prescreen sha=<head_oid> status=PASS|WARN|FAIL]`, scan extracted `<original>` segments pre-LLM, synchronize `allowed_licenses`, and require commit-matching `APPROVED` review (`PASS`, third-party) or post-prescreen `/approve-intake <head_oid>` (`PASS` owner-authored or `WARN` human override). |
| REQ-019 / AC-019-1, AC-019-2 | `scripts/collab_intake.py::validate_submission_pr` | Accept only `supported_pairs` (`en->vi`, `zh->vi`) and reject unsupported pairs at intake. |
| REQ-020 / AC-020-1, AC-020-2 | `scripts/collab_seed.py::seed_merged_book`, `scripts/orchestrate_run.py::initialize_book_record`, `scripts/orchestrate_run.py::assert_fiction_pronouns_populated`, `scripts/helper.py::sync_book_record_views`, `scripts/utils/hanviet.py::transcribe_to_hanviet` | Extract chapters, scan `source.txt` pre-LLM, write envelope `checklist.json` (`seed_status`), populate `book_record.json` over `min(3, N)` chapters, detect already-merged or open `ch01` seed PRs, merge on live `PASS` with bound-glossary audit comment, compute NFC-canonical 64-char SHA-256 seed `glossary_sha`, create-if-missing `ch02..chNN` issues before setting `seed_status: "seeded"`, keep transient eval errors in `"seeding"`, and support `--retry-blocked` for `"blocked_failed"`. |
| REQ-021 / AC-021-1, AC-021-2 | `scripts/collab_pr_validate.py::validate_translation_pr` | Parse `^translate/(?P<slug>...)-(?P<ch>ch\d{2,})-(?P<login>...)$` (`<login> == pr.author.login`), enforce `5 MiB` / `2,000`-segment `translation.xml` cap, `512 KiB` `analyze_additions.json` cap, `manifest.json` language cross-checks, `source.txt` `<first>..<last>` segment range cross-check, `ch01` `seed` vs `ch02..chNN` `#<issue>` / 40-or-64-hex `glossary_sha` checks, `<original>` byte-identity, `<hanviet>` (`zh`), and `<translate>` code/image byte-invariance. |
| REQ-022 / AC-022-1, AC-022-2 | `scripts/collab_pr_validate.py::validate_review` | Enforce `review.author.login != pr.author.login`, $\ge 1$ inline diff comment or `\bS-\d{4}\b` segment reference, and all 6 rubric dimensions in `[1.0, 5.0]`. |
| REQ-023 / AC-023-1, AC-023-2 | `scripts/collab_pr_validate.py::detect_prompt_injection`, `scripts/collab_verdict.py::compute_collab_verdict`, `scripts/collab_sweep.py::commit_and_push` | Scan `source.txt` / `<original>`, `<hanviet>`, `<translate>`, PR bodies, `analyze_additions.json`, and reviews before any LLM call, manage `FlaggedForHuman` (`status:flagged`) with `/clear-flag <head_oid>`, `/reject-flag <head_oid>`, and round-preserving new-commit pushes, and enforce `runs/<slug>/**` confinement in `commit_and_push` (raising `ValueError` before `git add` on violation). |
| REQ-024 / AC-024-1, AC-024-2 | `scripts/collab_verdict.py::compute_collab_verdict`, `scripts/collab_sweep.py::live_evaluate_pr` | Normalize live `run_evaluation` output to `EvaluationResult` (`verdict`, `weighted_total`, `fallback_unverified`, `dimension_scores`, `issues`), resolve genre threshold (`3.5` vs `4.0`), compute per-reviewer blocking `CHANGES_REQUESTED` state (with per-reviewer `/dismiss-stale-review <rev_login> <head_oid>`), keep transient evaluator errors / `fallback_unverified` in `Evaluating`, and route `Candidate` / `Revise` / `Reassign`. |
| REQ-025 / AC-025-1, AC-025-2 | `scripts/collab_pr_validate.py::validate_additions_schema`, `scripts/collab_sweep.py::bind_winning_proposals` | Validate canonical `proposals` JSON on `<head_oid>`, read `analyze_additions.json` strictly from `<winner_head_oid>` via Git blob, detect case-folded conflicts against `book_record.json`, and bind non-conflicting terms (`match: "word"`, `case_sensitive: false`) via `sync_book_record_views` without mutating `glossary_sha`. |
| REQ-026 / AC-026-1, AC-026-2 | `scripts/collab_select.py::select_winning_pr`, `scripts/collab_select.py::check_starved_chapter`, `scripts/collab_sweep.py::run_sweep`, `scripts/utils/hanviet.py::transcribe_to_hanviet` | Post/read latest cohort `[collab-window-start epoch=<float>]`, rank candidates after `window_hours` by `(-score_of_record, major_issue_count, minor_issue_count, pr_number)`, merge winner, close losers (`status:not-selected`), and open up to `2` bounded 48 h starved-chapter bot fallback PRs (after pre-LLM `source.txt` injection check, inserting `transcribe_to_hanviet` `<hanviet>` tags for `zh->vi`) before `[collab-starved-escalated]`. |
| REQ-027 / AC-027-1, AC-027-2 | `scripts/collab_state.py::advance_chapter_state`, `scripts/collab_state.py::advance_pr_state`, `scripts/collab_state.py::reconcile_status_labels` | Enforce 2 pre-issue `checklist.json` states, `Open <-> Candidates -> Done` chapter transitions, 9 PR states with `status:*` labels (preserving `book:*`, `lang:*`, `agent:*`), bot-only `[collab-revise sha=<head_oid>]` distinct-SHA round counting, and `(max_revise_rounds + 1)`-th failure `Reassign` closure. |
| REQ-028 / AC-028-1, AC-028-2 | `scripts/collab_ledger.py::append_ledger_entry`, `scripts/collab_ledger.py::verify_ledger_integrity` | Write and verify `runs/<slug>/collab_ledger.json` (`selected_pr > 0` on v2.0, deterministic `reviewer_score` and sorted `rejected_prs`, grandfathering pre-v2.0 published `scandal-in-bohemia-v1.0`). |
| REQ-029 / AC-029-1, AC-029-2 | `scripts/collab_completeness.py::verify_book_completeness`, `scripts/collab_sweep.py::default_compile_book` | Support envelope and list `checklist.json`, block `seed_status == "blocked_failed"` or empty chapters, backfill `released_tag` if GitHub Release `<slug>-v1.0` already exists, verify `source.txt`/`final.txt`/parity/ledger/zero tracked binaries, compile configured `release_formats`, and publish `<slug>-v1.0`. |
| REQ-030 / AC-030-1, AC-030-2 | `scripts/collab_sweep.py::non_blocking_sweep_lock`, `scripts/collab_sweep.py::sync_clean_main`, `scripts/collab_sweep.py::main` | Enforce single-instance mutual exclusion via POSIX `fcntl.flock` on `<workspace_dir>/.sweep.lock`, assert clean `main` checkout (with explicit `--recover-runs` crash recovery for `runs/**`), fast-forward to `origin/main` before sweep and after merges, resolve `bot_login` token, and advance open items idempotently. |

## Interfaces and flow

1. **Configuration & Git synchronization (`scripts/collab_config.py`, `scripts/collab_sweep.py`)**:
   - `load_collab_config` validates all 14 keys (`evaluation_engine in {"workbuddy", "cbc"}`, `release_formats` subset of `{"epub", "pdf", "docx"}`, `human_approver != bot_login`).
   - `sync_clean_main(root)` verifies `git symbolic-ref --short HEAD == "main"` and `git status --porcelain --untracked-files=no == ""`, then runs `git fetch origin main` and `git merge --ff-only origin/main`.
2. **Book intake & seeding (`scripts/collab_intake.py`, `scripts/collab_seed.py`)**:
   - `validate_submission_pr` checks slug regex, `slug not in existing_slugs`, non-LFS source bytes `1..max_source_bytes`, $\ge 1$ extracted segment, `https://` `license_url`, `supported_pairs`, language/diacritic ratios, and `<original>` injection scan.
   - `seed_merged_book` writes envelope `checklist.json` (`seed_status: "seeding"`), runs `initialize_book_record` + live `assert_fiction_pronouns_populated(..., offline=False)` + `sync_book_record_views`, opens bot PR `translate/<slug>-ch01-<bot_login>` (`Issue: seed`, `Glossary SHA: seed`), validates `REQ-021`/`REQ-023` + live `REQ-024`, posts the bound-glossary audit comment, merges `ch01`, computes canonical SHA-256 `glossary_sha`, sets `seed_status: "seeded"`, commits `runs/<slug>/**`, and creates `ch02..chNN` issues.
3. **Translation PR evaluation, proposal binding & selection (`scripts/collab_sweep.py`)**:
   - Pre-LLM stage reads `translation.xml` and optional `analyze_additions.json` from `<head_oid>` via `git show <head_oid>:<path>`, cross-checking `manifest.json` languages and `chNN/source.txt` segment bounds.
   - Bot comment markers use deterministic bracketed tokens authored solely by `bot_login`:

```text
[collab-prescreen sha=<head_oid> status=PASS|WARN|FAIL]
[collab-revise sha=<head_oid>]
[collab-candidate-score sha=<head_oid> score=<float> major=<int> minor=<int>]
[collab-window-start epoch=<utc_epoch>]
[collab-fallback pr=#<pr_number> attempt=<1|2>]
[collab-starved-escalated]
```

   - Human operator slash commands recognized only when `comment.author.login == human_approver`:

```text
/approve-intake <head_oid>
/clear-flag <head_oid>
/reject-flag <head_oid>
/dismiss-stale-review <rev_login> <head_oid>
/retry-fallback
```

## Boundaries and recovery

- **Lock contention (`REQ-030`)**: Held `.sweep.lock` logs `"sweep already running"` and exits `0`.
- **Dirty or diverged local checkout (`REQ-030`)**: Dirty tracked files, detached/non-`main` branch, or non-fast-forward `origin/main` aborts the sweep non-zero prior to any Git or GitHub mutation.
- **Failed seed quarantine (`REQ-020`)**: Preserves `checklist.json["chapters"]`, sets `seed_status: "blocked_failed"` with `seed_error`, closes any open `ch01` seed PR without merging, and skips future seeding until reset.
- **Transient evaluator failure (`REQ-024`)**: Timeouts, missing engine binaries, malformed JSON, or `fallback_unverified == True` hold the PR in `Evaluating` (`status:evaluating`) without posting `[collab-revise ...]` or consuming a round.
- **Bounded starvation recovery (`REQ-026`)**: Caps automated bot fallback PRs at `2` attempts per chapter before posting `[collab-starved-escalated] @<human_approver>`.

## Dependencies and unresolved design work

None.
