# Design: Agent-Agnostic Role-Based Multi-Agent Collaboration & Worker CLI

Sources: [spec.md](./spec.md), [decisions.md](./decisions.md)

## Components

| Requirement / AC | Path and symbol | Responsibility / change |
| --- | --- | --- |
| REQ-031 / AC-031-1, AC-031-2 | `scripts/collab_config.py` (`CollabConfig`, `REQUIRED_KEYS`, `load_collab_config`), `config.example.json`, `plugin.json`, `scripts/evaluators/__init__.py`, `scripts/evaluate.py`, `scripts/orchestrate_run.py`, `scripts/refine_chapter.py` | Streamline `CollabConfig` to 12 required keys, delete `scripts/evaluate_workbuddy.py` and `scripts/evaluators/workbuddy.py`, remove all `workbuddy` tokens, and align `plugin.json` with Antigravity's plugin manifest schema. |
| REQ-032 / AC-032-1, AC-032-2 | `scripts/collab_verdict.py` (`evaluate_pr_from_reviews`, `filter_fresh_valid_reviews`), `scripts/collab_sweep.py` | Compute `EvaluationResult` and `score_of_record` from commit-fresh non-author `REQ-022` peer reviews + `enforce_language_detection` + `anti_convert` without external CLI evaluators. |
| REQ-033 / AC-033-1, AC-033-2 | `scripts/collab_select.py` (`select_winning_pr`), `scripts/collab_sweep.py` (`run_sweep`) | Remove `window_hours` and `[collab-window-start]`; select and merge the highest-ranked passing PR immediately in Phase `(c)` (`Open -> Done`) while keeping 48h starved-chapter fallback. |
| REQ-034 / AC-034-1, AC-034-2 | `scripts/collab_seed.py` (`seed_merged_book`, `_GitHubSeedClient`), `scripts/collab_sweep.py` (`GitHubCLIClient`, `--no-auto-seed`) | Push initial `runs/<slug>` to `origin/main` before branching `ch01`, isolate `ch01` seed PR commit/push in an ephemeral `.worktrees/` directory, support `--prepare-only` and `--ch01-eval-file` / peer-review evaluation, and auto-create labels via `gh label create --force`. |
| REQ-035 / AC-035-1, AC-035-2 | `scripts/collab_worker.py` (`GitHubAccountContext`, `list_role_tasks`, `submit_book`, `approve_intake`, `submit_translation`, `submit_review`, `main`) | Multi-account isolated worker CLI using `GH_TOKEN` + ephemeral `0700` `GIT_ASKPASS` + `.worktrees/` with local pre-flight validation before pushing or commenting. |
| REQ-036 / AC-036-1, AC-036-2 | `README.md`, `CONTRIBUTING.md`, `skills/collab-translate-book/SKILL.md`, `agents/collab_translator.md`, `agents/collab_reviewer.md` | Document the 4 agent-agnostic roles (`Submitter & Human Approver`, `Translator`, `Reviewer`, `Coordinator`) with CLI commands and starter prompts, and ship discoverable Antigravity skill/subagent files. |

## Interfaces and flow

1. **Multi-Account Subprocess Isolation (`GitHubAccountContext`)**:
   - Resolves `token = gh auth token -u <gh_user>` in memory without calling `gh auth switch`.
   - Passes `GH_TOKEN=token` in subprocess `env` and writes an ephemeral `0700` shell script inside a temporary directory (`GIT_ASKPASS`) returning `x-access-token` for username prompts and `$GH_TOKEN` for password prompts, invoked via `git -c credential.helper= -c core.askPass=<script>`.
   - Ephemeral branch worktrees live under `<repo_root>/.worktrees/<task_id>` and are removed in a `finally:` block via `git worktree remove --force`.
2. **Peer-Review + Deterministic `score_of_record` (`evaluate_pr_from_reviews`)**:
   - Filters `reviews` to non-author `REQ-022`-valid reviews whose `commit_oid` (when present) equals `head_oid`, retaining the chronologically latest valid review per `reviewer_login`.
   - Computes per-dimension arithmetic mean across reviewers (`fidelity`, `fluency`, `terminology`, `register`, `structure`, `completeness`) and `weighted_total = compute_weighted_total(dimension_scores)`.
   - Parses optional `[critical]`, `[major]`, and `[minor]` issue markers from review bodies, runs `enforce_language_detection` on `translation.xml` (and `anti_convert` checks when `source_lang == "zh"`), sets `fallback_unverified = False`, and normalizes `verdict` against `resolve_pass_threshold(book_record=book_record)`.
3. **Windowless Selection (`select_winning_pr`)**:
   - Accepts `candidates: List[Dict[str, Any]]`, filters `_is_eligible_candidate`, sorts by `(-score_of_record, major_issue_count, minor_issue_count, pr_number)`, and immediately returns `{"selected": bool(winner), "window_closed": True, "winner": winner, "non_selected": non_selected}`.

## Boundaries and recovery

- **Stale or self-reviews (`AC-032-2`)**: Reviews where `reviewer == pr_author` or `commit_oid != head_oid` are excluded by `filter_fresh_valid_reviews`; if zero fresh valid reviews remain, the PR stays in `status:needs-review`.
- **Untranslated or convert-tainted drafts (`AC-032-2`, `AC-034-2`)**: `enforce_language_detection` (and `anti_convert` on `zh`) inserts `critical` issues and forces `verdict = "FAIL"` even if a reviewer supplied high dimension scores.
- **Fail-fast worker pre-flight (`AC-035-2`)**: `collab_worker.py` runs `validate_submission_pr`, `validate_translation_pr`, or `validate_review` locally before any `git push`, `gh pr create`, or `gh pr review` call, exiting non-zero with JSON defects on failure.
- **Missing GitHub labels (`AC-034-1`)**: `ensure_labels` caches known label names per client and runs `gh label create <label> --color <hex> --force` before applying labels to issues or PRs.

## Dependencies and unresolved design work

None.
