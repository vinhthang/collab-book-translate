# Tasks: Agent-Agnostic Role-Based Multi-Agent Collaboration & Worker CLI

Sources: [spec.md](./spec.md), [design.md](./design.md)

## Task 1: Streamline 12-key `CollabConfig`, remove `WorkBuddy` shims, and align `plugin.json`

- [ ] Complete the work and its applicable checks.
- Covers: REQ-031 / AC-031-1, AC-031-2
- Files: scripts/collab_config.py, scripts/evaluate_workbuddy.py, scripts/evaluators/workbuddy.py, scripts/evaluators/__init__.py, scripts/evaluators/fallback.py, scripts/evaluators/scoring.py, scripts/utils/schemas.py, scripts/evaluate.py, scripts/orchestrate_run.py, scripts/refine_chapter.py, config.example.json, plugin.json, tests/conftest.py, tests/test_collab_config.py
- Dependencies: None
- Blocked by: None
- Work: Write failing tests in `tests/test_collab_config.py` asserting the 12-key `CollabConfig` schema (without `window_hours` or `evaluation_engine`), Antigravity `plugin.json` manifest keys, deletion of `workbuddy.py` / `evaluate_workbuddy.py`, and zero `workbuddy` tokens across `scripts/`, `tests/`, `plugin.json`, and `config.example.json`; then implement the updates and delete the legacy vendor shim files.
- Completion evidence: Not executed.

## Task 2: Implement peer-review + deterministic `score_of_record` and windowless selection

- [ ] Complete the work and its applicable checks.
- Covers: REQ-032 / AC-032-1, AC-032-2, REQ-033 / AC-033-1, AC-033-2
- Files: scripts/collab_verdict.py, scripts/collab_select.py, tests/test_collab_verdict.py, tests/test_collab_select.py
- Dependencies: None
- Blocked by: None
- Work: Write failing unit tests in `tests/test_collab_verdict.py` for `filter_fresh_valid_reviews` and `evaluate_pr_from_reviews` (multi-reviewer weighted mean, stale-commit exclusion, `[critical]`/`[major]`/`[minor]` parsing, `enforce_language_detection` / `anti_convert` overrides) and in `tests/test_collab_select.py` for immediate windowless `select_winning_pr(candidates)` ranking and tie-breaking; then implement both modules.
- Completion evidence: Not executed.

## Task 3: Build `collab_worker.py` CLI, 4-role `README.md` playbook, and Antigravity plugin skills/agents

- [ ] Complete the work and its applicable checks.
- Covers: REQ-035 / AC-035-1, AC-035-2, REQ-036 / AC-036-1, AC-036-2
- Files: scripts/collab_worker.py, tests/test_collab_worker.py, README.md, CONTRIBUTING.md, skills/collab-translate-book/SKILL.md, agents/collab_translator.md, agents/collab_reviewer.md
- Dependencies: None
- Blocked by: None
- Work: Write failing unit/conformance tests in `tests/test_collab_worker.py` covering `GitHubAccountContext` (`GH_TOKEN` + `GIT_ASKPASS` without argv leaks or `gh auth switch`), all 5 subcommands (`list-tasks`, `submit-book`, `approve-intake`, `submit-translation`, `submit-review`) including pre-flight failure rejection and `.worktrees/` cleanup, plus 4-role `README.md`/`CONTRIBUTING.md` and `SKILL.md`/`agents/*.md` frontmatter validation; then implement `scripts/collab_worker.py`, `README.md`, `CONTRIBUTING.md`, `skills/collab-translate-book/SKILL.md`, and `agents/collab_{translator,reviewer}.md`.
- Completion evidence: Not executed.

## Task 4: Harden `collab_seed.py` and `collab_sweep.py` with worktree isolation, auto-labels, and direct merge

- [ ] Complete the work and its applicable checks.
- Covers: REQ-032 / AC-032-1, AC-032-2, REQ-033 / AC-033-1, AC-033-2, REQ-034 / AC-034-1, AC-034-2
- Files: scripts/collab_seed.py, scripts/collab_sweep.py, tests/test_collab_seed.py, tests/test_collab_sweep.py
- Dependencies: Task 1, Task 2, Task 3
- Blocked by: None
- Work: Write failing tests in `tests/test_collab_seed.py` (`--prepare-only`, `push_origin=True` before `ch01` branch, ephemeral worktree `ch01` PR creation, `--ch01-eval-file` / peer-review scoring, `ensure_labels`) and `tests/test_collab_sweep.py` (default `evaluate_pr_from_reviews`, windowless immediate merge of single and competing PRs without `[collab-window-start]`, `--no-auto-seed`, `ensure_labels`); then implement the updates in `scripts/collab_seed.py` and `scripts/collab_sweep.py` and update local untracked `config.json`.
- Completion evidence: Not executed.

## Task 5: Run quality gates, install plugin, push to GitHub, and execute live 2-account book translation

- [ ] Complete the work and its applicable checks.
- Covers: REQ-031 / AC-031-1, AC-031-2, REQ-032 / AC-032-1, AC-032-2, REQ-033 / AC-033-1, AC-033-2, REQ-034 / AC-034-1, AC-034-2, REQ-035 / AC-035-1, AC-035-2, REQ-036 / AC-036-1, AC-036-2
- Files: docs/specs/INDEX.md, docs/specs/2026-11-18-collab-multi-agent-translation/spec.md, docs/specs/2026-11-18-collab-worker-multi-agent/spec.md, docs/specs/2026-11-18-collab-worker-multi-agent/tasks.md, docs/specs/2026-11-18-collab-worker-multi-agent/verification.md
- Dependencies: Task 4
- Blocked by: None
- Work: Verify full `pytest` suite, `mutation-guard` (`>= 80%` kill rate across modified/new `collab_*.py` modules), single-agent `code-review` (`0 P0`, `0 P1`), and `validate_specs.py --all`; symlink `~/.gemini/config/plugins/collab-book-translate`, verify via `lsrpc GetAllPlugins`, push `main` to `vinhthang/collab-book-translate`, run Oscar Wilde's 5-chapter *The Happy Prince and Other Tales* (`en->vi`) live across `vinhthang` and `1quachuoi9vang` through published EPUB 3 / PDF / DOCX GitHub Release, and record run evidence + `In-Testing` activation.
- Completion evidence: Not executed.

## Gate 2: traceability and readiness

| Requirement | AC | ADR or rationale | Design | Task | Planned verification | Status / reason |
| --- | --- | --- | --- | --- | --- | --- |
| REQ-031 | AC-031-1 | [Rationale](./decisions.md#architectural-rationale) | [Components](./design.md#components) | [Task 1](#task-1-streamline-12-key-collabconfig-remove-workbuddy-shims-and-align-pluginjson), [Task 5](#task-5-run-quality-gates-install-plugin-push-to-github-and-execute-live-2-account-book-translation) | [AC-031-1](./verification.md#ac-031-1-req-031) | PASS — 12-key config, deleted shims, and `plugin.json` schema covered |
| REQ-031 | AC-031-2 | [Rationale](./decisions.md#architectural-rationale) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 1](#task-1-streamline-12-key-collabconfig-remove-workbuddy-shims-and-align-pluginjson), [Task 5](#task-5-run-quality-gates-install-plugin-push-to-github-and-execute-live-2-account-book-translation) | [AC-031-2](./verification.md#ac-031-2-req-031) | PASS — missing/invalid key and `bot_login == human_approver` guards covered |
| REQ-032 | AC-032-1 | [ADR-027](./decisions.md#adr-027-commit-fresh-peer-review--deterministic-checks-as-score_of_record) | [Interfaces](./design.md#interfaces-and-flow) | [Task 2](#task-2-implement-peer-review--deterministic-score_of_record-and-windowless-selection), [Task 4](#task-4-harden-collab_seedpy-and-collab_sweeppy-with-worktree-isolation-auto-labels-and-direct-merge), [Task 5](#task-5-run-quality-gates-install-plugin-push-to-github-and-execute-live-2-account-book-translation) | [AC-032-1](./verification.md#ac-032-1-req-032) | PASS — commit-fresh peer-review weighted scoring covered |
| REQ-032 | AC-032-2 | [ADR-027](./decisions.md#adr-027-commit-fresh-peer-review--deterministic-checks-as-score_of_record) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 2](#task-2-implement-peer-review--deterministic-score_of_record-and-windowless-selection), [Task 4](#task-4-harden-collab_seedpy-and-collab_sweeppy-with-worktree-isolation-auto-labels-and-direct-merge), [Task 5](#task-5-run-quality-gates-install-plugin-push-to-github-and-execute-live-2-account-book-translation) | [AC-032-2](./verification.md#ac-032-2-req-032) | PASS — stale/self-review exclusion and language/severity failures covered |
| REQ-033 | AC-033-1 | [Rationale](./decisions.md#architectural-rationale) | [Interfaces](./design.md#interfaces-and-flow) | [Task 2](#task-2-implement-peer-review--deterministic-score_of_record-and-windowless-selection), [Task 4](#task-4-harden-collab_seedpy-and-collab_sweeppy-with-worktree-isolation-auto-labels-and-direct-merge), [Task 5](#task-5-run-quality-gates-install-plugin-push-to-github-and-execute-live-2-account-book-translation) | [AC-033-1](./verification.md#ac-033-1-req-033) | PASS — immediate single and competing PR merge without window gate covered |
| REQ-033 | AC-033-2 | [Rationale](./decisions.md#architectural-rationale) | [Interfaces](./design.md#interfaces-and-flow) | [Task 2](#task-2-implement-peer-review--deterministic-score_of_record-and-windowless-selection), [Task 4](#task-4-harden-collab_seedpy-and-collab_sweeppy-with-worktree-isolation-auto-labels-and-direct-merge), [Task 5](#task-5-run-quality-gates-install-plugin-push-to-github-and-execute-live-2-account-book-translation) | [AC-033-2](./verification.md#ac-033-2-req-033) | PASS — tie-breaks and zero-eligible-candidate hold covered |
| REQ-034 | AC-034-1 | [ADR-027](./decisions.md#adr-027-commit-fresh-peer-review--deterministic-checks-as-score_of_record), [ADR-028](./decisions.md#adr-028-per-invocation-gh_token--ephemeral-git_askpass--worktrees-multi-account-isolation) | [Components](./design.md#components) | [Task 4](#task-4-harden-collab_seedpy-and-collab_sweeppy-with-worktree-isolation-auto-labels-and-direct-merge), [Task 5](#task-5-run-quality-gates-install-plugin-push-to-github-and-execute-live-2-account-book-translation) | [AC-034-1](./verification.md#ac-034-1-req-034) | PASS — `--prepare-only`, `push_origin=True`, worktree `ch01` PR, and `ensure_labels` covered |
| REQ-034 | AC-034-2 | [ADR-027](./decisions.md#adr-027-commit-fresh-peer-review--deterministic-checks-as-score_of_record) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 4](#task-4-harden-collab_seedpy-and-collab_sweeppy-with-worktree-isolation-auto-labels-and-direct-merge), [Task 5](#task-5-run-quality-gates-install-plugin-push-to-github-and-execute-live-2-account-book-translation) | [AC-034-2](./verification.md#ac-034-2-req-034) | PASS — failed `ch01` validation/evaluation quarantine covered |
| REQ-035 | AC-035-1 | [ADR-028](./decisions.md#adr-028-per-invocation-gh_token--ephemeral-git_askpass--worktrees-multi-account-isolation) | [Interfaces](./design.md#interfaces-and-flow) | [Task 3](#task-3-build-collab_workerpy-cli-4-role-readmemd-playbook-and-antigravity-plugin-skillsagents), [Task 5](#task-5-run-quality-gates-install-plugin-push-to-github-and-execute-live-2-account-book-translation) | [AC-035-1](./verification.md#ac-035-1-req-035) | PASS — isolated 2-account worker subcommands and `.worktrees/` cleanup covered |
| REQ-035 | AC-035-2 | [ADR-028](./decisions.md#adr-028-per-invocation-gh_token--ephemeral-git_askpass--worktrees-multi-account-isolation) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 3](#task-3-build-collab_workerpy-cli-4-role-readmemd-playbook-and-antigravity-plugin-skillsagents), [Task 5](#task-5-run-quality-gates-install-plugin-push-to-github-and-execute-live-2-account-book-translation) | [AC-035-2](./verification.md#ac-035-2-req-035) | PASS — local pre-flight rejection before Git/GitHub mutation covered |
| REQ-036 | AC-036-1 | [Rationale](./decisions.md#architectural-rationale) | [Components](./design.md#components) | [Task 3](#task-3-build-collab_workerpy-cli-4-role-readmemd-playbook-and-antigravity-plugin-skillsagents), [Task 5](#task-5-run-quality-gates-install-plugin-push-to-github-and-execute-live-2-account-book-translation) | [AC-036-1](./verification.md#ac-036-1-req-036) | PASS — 4-role `README.md` playbook and Antigravity plugin discovery covered |
| REQ-036 | AC-036-2 | [Rationale](./decisions.md#architectural-rationale) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 3](#task-3-build-collab_workerpy-cli-4-role-readmemd-playbook-and-antigravity-plugin-skillsagents), [Task 5](#task-5-run-quality-gates-install-plugin-push-to-github-and-execute-live-2-account-book-translation) | [AC-036-2](./verification.md#ac-036-2-req-036) | PASS — doc/frontmatter/zero-WorkBuddy conformance checks covered |

- Backward scope check: PASS — Every component in `design.md` and every task (`Task 1`–`Task 5`) serves `REQ-031..REQ-036` with strictly disjoint `Files:` write-sets across parallel root tasks (`Task 1`, `Task 2`, `Task 3`).
- Reference and catalog consistency: PASS — `INDEX.md`, `spec.md`, `decisions.md`, `design.md`, `tasks.md`, and `verification.md` agree on `REQ-031..REQ-036`, `ADR-027..ADR-028`, and parent delta links.
