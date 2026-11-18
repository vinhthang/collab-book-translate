# Decisions: Collaborative Multi-Agent Book Translation on GitHub

Governing specification: [spec.md](./spec.md)

## Architectural rationale

- Governs: REQ-017, REQ-019, REQ-021, REQ-022, REQ-025, REQ-027, REQ-028, REQ-029, REQ-030

Existing repository constraints and prior owner mandates fix the following choices without requiring multi-option trade-off debates:

- **GitHub issues, PRs, bracketed bot markers, and fast-forward local sweep (`REQ-017`, `REQ-021`, `REQ-022`, `REQ-027`, `REQ-028`, `REQ-030`)**: External task servers, GitHub Actions, and inbound webhooks are excluded by `Non-goals`. GitHub PRs/issues/labels, structured bot markers (`[collab-prescreen ...]`, `[collab-revise ...]`, `[collab-candidate-score ...]`, `[collab-window-start ...]`, `[collab-fallback ...]`), and `runs/<slug>/collab_ledger.json` driven by an idempotent local sweep (`fcntl.flock` `.sweep.lock` plus `git merge --ff-only origin/main`) provide the sole zero-webhook state surface.
- **v1 language pairs, 1-chapter-per-chunk scope, and non-LFS source cap (`REQ-017`, `REQ-019`, `REQ-021`)**: Downstream compilers, Vietnamese diacritic checks, and Sino-Vietnamese `<hanviet>` stages target Vietnamese (`en->vi`, `zh->vi`), while sub-chapter splitting and Git LFS pointer ingestion are excluded by v1 `Non-goals`.
- **PR-head proposal reads and release asset publication (`REQ-025`, `REQ-029`)**: Reading `analyze_additions.json` strictly from `<winner_head_oid>` prevents cross-PR working-tree contamination, and gitignored compilation into `runs/<slug>/release_assets/` published as GitHub Release assets (`<slug>-v1.0`) prevents binary repository bloat.

## ADR-021: Self-contained standalone repository (`collab-book-translate`)

- Governs: REQ-016, REQ-017, REQ-021, REQ-023, REQ-030
- State: Accepted
- Context and constraints: `2026-11-06-collab-multi-agent-translation` (`ADR-COLLAB-008`) assumed `scripts/` stayed inside private `vinhthang/agent-translator` while `collab-book-translate` held only `books/` and `runs/`. During implementation (`db786a0`, `423b9ba`), the owner directed building `collab-book-translate` as a standalone repository and Python project containing `scripts/`, `tests/`, `docs/specs/`, `plugin.json`, and `pyproject.toml`.
- Viable options:
  1. **Standalone self-contained public repository (Option A)**: Keep `scripts/`, `tests/`, `docs/specs/`, `plugin.json`, `config.example.json`, `books/`, and `runs/` together in `collab-book-translate`, enforcing path allowlists in `collab_intake.py` and `collab_pr_validate.py`, `.github/CODEOWNERS`, and `runs/<slug>/**` bot commit confinement.
  2. **Two-repository split (Option B)**: Strip `scripts/`, `tests/`, and `pyproject.toml` out of `collab-book-translate` and invoke them cross-repo from `agent-translator`.
- Decision: Choose Option A (standalone self-contained repository).
- Costs and risks: Public contributors can inspect `scripts/` and attempt PRs touching code; mitigated by `REQ-017`/`REQ-021` path allowlists, `.github/CODEOWNERS`, and `REQ-023` bot path confinement.
- Rejected alternatives: Option B separates unit tests from the repository structure they verify and contradicts the owner's standalone-project directive.
- Affected components: `plugin.json`, `config.example.json`, `.gitignore`, `.github/CODEOWNERS`, `scripts/collab_intake.py`, `scripts/collab_pr_validate.py`, `scripts/collab_sweep.py`.

## ADR-022: Zero-fallback `CollabConfig` with untracked local `config.json`

- Governs: REQ-016
- State: Accepted
- Context and constraints: Hardcoded paths, usernames, and silent fallbacks (`Path.cwd()`) caused cross-machine brittleness, while tracking `config.json` leaked a machine-local `/Users/...` path into the public repository.
- Viable options:
  1. **Strict 14-key `CollabConfig` loader + `.gitignore`d `config.json` + tracked `config.example.json` (Option A)**: Resolve `--config` $\to$ `COLLAB_CONFIG_PATH` $\to$ `<engine_root>/config.json`, validate all 14 typed fields plus `human_approver != bot_login`, raise `ConfigError` on any violation, untrack `config.json`, and keep `config.example.json` tracked.
  2. **Environment-variable overrides with hardcoded fallback defaults in Python (Option B)**: Default missing keys to repository-owner constants in code.
- Decision: Choose Option A per owner directive (*"do not fallback, but create config file for me"*) and 2026-11-18 Option 1 approval.
- Costs and risks: Fresh clones and test suites must supply explicit configuration; mitigated by `config.example.json` and `tests/conftest.py` fixtures.
- Rejected alternatives: Option B violates the zero-fallback requirement and masks misconfiguration.
- Affected components: `.gitignore`, `plugin.json`, `config.example.json`, `scripts/collab_config.py`, `scripts/utils/paths.py`, `tests/conftest.py`.

## ADR-023: Commit-bound dual-mode human intake approval

- Governs: REQ-018
- State: Accepted
- Context and constraints: GitHub forbids submitting an `APPROVED` pull request review on one's own PR, deadlocking `book/<slug>` intake whenever `pr.author.login == human_approver`, while un-pinned approvals on third-party PRs risk merging post-approval force-pushes.
- Viable options:
  1. **Commit-pinned `APPROVED` review for third-party PRs + `/approve-intake <head_oid>` comment for owner-authored PRs (Option A)**: Require `review.state == "APPROVED"` with `review.commit.oid == <head_oid>` when `pr.author.login != human_approver`, and require `/approve-intake <head_oid>` by `human_approver` after passing pre-screen for `<head_oid>` when `pr.author.login == human_approver`.
  2. **Require a second human approver GitHub account (Option B)**: Provision an additional human account solely to approve owner-submitted books.
- Decision: Choose Option A.
- Costs and risks: Both modes bind strictly to `<head_oid>`, requiring re-approval if new commits are pushed to the intake branch; this prevents stale-approval bypasses.
- Rejected alternatives: Option B imposes multi-account overhead on the single human owner without adding independent judgment.
- Affected components: `scripts/collab_sweep.py`, `scripts/collab_intake.py`, `CONTRIBUTING.md`, `tests/test_collab_sweep.py`.

## ADR-024: Bot-authored `ch01` seed PR, content-addressed `glossary_sha`, and failed-seed quarantine

- Governs: REQ-020, REQ-028
- State: Accepted
- Context and constraints: Parallel translation requires a populated `book_record.json` (`initialize_book_record` + `assert_fiction_pronouns_populated`), an auditable `ch01` reference PR (`Issue: seed`, `Glossary SHA: seed`), and a `glossary_sha` that neither creates a Git `HEAD` self-reference cycle nor breaks when later chapters append bound terms.
- Viable options:
  1. **Whole-book analysis + bot `ch01` seed PR + canonical SHA-256 immutable seed `glossary_sha` + `seed_status: "blocked_failed"` quarantine (Option A)**: Run `initialize_book_record` and live `assert_fiction_pronouns_populated` over `min(3, N)` opening chapters, open (or reuse) and merge `translate/<slug>-ch01-<bot_login>` PR on live `PASS` with a bound-glossary audit comment, compute `glossary_sha` once as the immutable 64-char SHA-256 hex seed digest of canonical `{glossary, names, pronouns}` JSON (accepting 40-char legacy SHAs and `character_pronouns` on pre-v2.0 books), and quarantine failures in `checklist.json` (`seed_status: "blocked_failed"`) while grandfathering pre-v2.0 published `scandal-in-bohemia-v1.0`.
  2. **Git `HEAD` commit-SHA `glossary_sha` with `git commit --amend` (Option B)**: Store `git rev-parse HEAD` inside `book_record.json` and amend the commit afterwards.
- Decision: Choose Option A.
- Costs and risks: `glossary_sha` is an immutable seed identifier rather than a rolling post-merge hash, which avoids invalidating in-flight contributor PRs when parallel chapters merge and eliminates the `git commit --amend` stale-SHA bug.
- Rejected alternatives: Option B produces a recorded SHA that never matches the amended commit containing `book_record.json`.
- Affected components: `scripts/collab_seed.py`, `scripts/collab_pr_validate.py`, `scripts/collab_ledger.py`, `scripts/collab_completeness.py`, `tests/test_collab_seed.py`.

## ADR-025: Pre-LLM prompt-injection gating, reversible `FlaggedForHuman` holds, and review-state clearing

- Governs: REQ-023, REQ-024
- State: Accepted
- Context and constraints: Untrusted public PRs and source files can embed LLM judge-manipulation payloads, and external contributors lack GitHub write permission to click "Resolve conversation" on inline review threads.
- Viable options:
  1. **Pre-LLM scan across all LLM paths + `/clear-flag` and `/reject-flag` exit commands + per-reviewer `/dismiss-stale-review <rev_login> <head_oid>` (Option A)**: Scan `source.txt` / `<original>`, `<hanviet>`, `<translate>`, PR bodies, proposals, and reviews before any LLM pronoun-extraction, translation-generation, or evaluation call; provide explicit `human_approver` exit transitions (`/clear-flag <head_oid>`, `/reject-flag <head_oid>`) out of `FlaggedForHuman`; evaluate blocking review feedback per reviewer (with `/dismiss-stale-review <rev_login> <head_oid>` scoped strictly to `<rev_login>`); and confine bot commits to `runs/<slug>/**`.
  2. **Permanent `FlaggedForHuman` park and GraphQL thread-resolution check (Option B)**: Require GitHub UI conversation resolution and manual label editing to unpark PRs.
- Decision: Choose Option A.
- Costs and risks: Adds 3 explicit slash-command handlers in `collab_sweep.py` (`/clear-flag`, `/reject-flag`, `/dismiss-stale-review`), eliminating permanent PR deadlocks while keeping `status:*` labels 100% sweep-reconciled.
- Rejected alternatives: Option B deadlocks PRs because outside contributors cannot resolve review threads and manual label edits get reverted by `reconcile_status_labels`.
- Affected components: `scripts/collab_pr_validate.py`, `scripts/collab_verdict.py`, `scripts/collab_state.py`, `scripts/collab_sweep.py`.

## ADR-026: Bounded 2-attempt 48 h starved-chapter bot fallback PR

- Governs: REQ-026
- State: Accepted
- Context and constraints: Chapters without a `Candidate` PR after `fallback_hours` (`48.0` h) block book completion, yet an unbounded retry loop on broken chapters would spam PRs while a single permanent marker with no retry leaves transient failures stuck.
- Viable options:
  1. **Automatic `translate_chapter` bot PR bounded to `2` attempts + `[collab-starved-escalated]` alert (Option A)**: Trigger after `fallback_hours` from `issue.created_at` when zero `Candidate` PRs and zero open fallback PRs exist; allow up to `2` attempts (`[collab-fallback pr=#<pr> attempt=1..2]`), escalating once via `[collab-starved-escalated] @<human_approver>` if both attempts close without a `Candidate`.
  2. **Single un-retried marker or passive stdout logging (Option B)**: Open at most one fallback PR forever or log starvation only to stdout.
- Decision: Choose Option A.
- Costs and risks: Caps worst-case automated fallback work to 2 PRs per starved chapter while guaranteeing recovery from a transient first-attempt failure and alerting the human owner if starvation persists.
- Rejected alternatives: Option B either abandons chapters after a single transient failure or fails to make progress unattended.
- Affected components: `scripts/collab_sweep.py`, `scripts/collab_select.py`, `tests/test_collab_sweep.py`.

## Gate 1: normative review

| Check | Status | Evidence / required correction |
| --- | --- | --- |
| Codebase grounding | PASS | Verified exact symbol locations against `collab-book-translate` at `423b9ba` (`ensure_technical_seed_glossary`, `is_fiction_book`, `assert_fiction_pronouns_populated`, and `initialize_book_record` in `scripts/orchestrate_run.py`, `sync_book_record_views`, `resolve_pass_threshold`, and `check_redraft` in `scripts/helper.py`, `transcribe_to_hanviet` in `scripts/utils/hanviet.py`, and pilot schemas in `runs/scandal-in-bohemia/`). |
| Attributable intent and explicit scope | PASS | Every requirement `REQ-016..REQ-030` traces to verbatim user prompts or the 2026-11-18 Option 1 owner decisions; `Non-goals` explicitly excludes task-hub, GitHub Actions/webhooks, non-`vi` targets, sub-chapter splitting, Git LFS, audio/image/web-reader, and internal CLI wrappers. |
| Observable requirements and meaningful acceptance criteria | PASS | All 15 requirements (`REQ-016..REQ-030`) define complete schemas, commit-SHA bindings, `ch01` seed exemptions, content-addressed `glossary_sha`, state exit transitions, and fast-forward Git sync with paired `AC-*-1` / `AC-*-2` criteria. |
| Assumptions and owner-deferred questions are explicit | PASS | Zero `TODO`/`TBD`/`FIXME` markers and zero `[PROVISIONAL]` items exist; sub-chapter splitting and Git LFS ingestion are explicitly excluded in `Non-goals`. |
| Architectural rationale respects requirements | PASS | `Architectural rationale` and `ADR-021..ADR-026` govern all `REQ-016..REQ-030` without contradicting or narrowing any normative requirement. |
| IDs, references, and revision route are consistent | PASS | Allocates `REQ-016..REQ-030` and `ADR-021..ADR-026` above prior `001..015` / `001..020` allocations, declares `Proposed: Replaces 2026-11-06-collab-multi-agent-translation`, and synchronizes `docs/specs/INDEX.md` (`Status: Planning`). |
