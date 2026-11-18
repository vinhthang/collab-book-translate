# Decisions: Agent-Agnostic Role-Based Multi-Agent Collaboration & Worker CLI

Governing specification: [spec.md](./spec.md)

## Architectural rationale

- Governs: REQ-031, REQ-033, REQ-036

Owner product decisions during `/grill-me` (2026-11-18) directly fix three boundaries without multi-option ambiguity: (1) all `WorkBuddy` evaluator shims and `window_hours` / `evaluation_engine` config keys are removed so the repository is 100% agent-agnostic (`REQ-031`), (2) passing reviewed PRs merge directly in the coordinator sweep with no timed candidate collection gate (`REQ-033`), and (3) `README.md` plus bundled Antigravity plugin skills/agents document four explicit operational roles (`Submitter & Human Approver`, `Translator`, `Reviewer`, `Coordinator`) (`REQ-036`).

## ADR-027: Commit-fresh peer review + deterministic checks as `score_of_record`

- Governs: REQ-032, REQ-034
- State: Accepted
- Context and constraints: Previously `collab_sweep.py` called an internal `cbc` / `workbuddy` CLI binary after peer review to compute `score_of_record`. The owner clarified that external AI CLIs are peer collaborator agents on GitHub rather than internal sweep binaries, while `collab_sweep.py` still requires an objective, manipulation-resistant `score_of_record` to decide `PASS` vs `REVISE` / `REASSIGN` and rank competing PRs.
- Viable options:
  1. **Option A (Chosen — Commit-fresh non-author `REQ-022` peer reviews + deterministic content guards)**: Compute `score_of_record` in `collab_verdict.py` (`evaluate_pr_from_reviews`) as the weighted 6-dimension mean across each distinct non-author reviewer's latest valid `REQ-022` review on `<head_oid>`, combined with deterministic `enforce_language_detection` (Vietnamese diacritic & untranslated English/Chinese guards), `anti_convert` (`zh->vi`), byte-invariance, and severity/blocking checks.
  2. **Option B (Separate out-of-band coordinator LLM evaluation file per PR)**: Require a second local subagent pass to write `eval.json` files outside GitHub PR reviews before `collab_sweep.py` can run.
- Decision: Choose **Option A**.
- Costs and risks: Score accuracy depends on the independent Reviewer agent's rubric discipline. Mitigated by enforcing `reviewer_login != pr_author_login`, commit-SHA freshness (`commit_oid == <head_oid>`), mandatory `S-NNNN` segment notes, pre-LLM prompt-injection scanning, and deterministic `enforce_language_detection` / `anti_convert` overrides that force `FAIL` (`1.0`) on untranslated or convert-tainted text regardless of reviewer scores.
- Rejected alternatives: Option B duplicates the peer review already posted on GitHub and prevents standalone `collab_sweep.py` from merging PRs reviewed by external agents without manual local JSON staging.
- Affected components: `scripts/collab_verdict.py`, `scripts/collab_sweep.py`, `scripts/collab_seed.py`.

## ADR-028: Per-invocation `GH_TOKEN` + ephemeral `GIT_ASKPASS` + `.worktrees/` multi-account isolation

- Governs: REQ-034, REQ-035
- State: Accepted
- Context and constraints: Two GitHub accounts (`vinhthang` and `1quachuoi9vang`) are authenticated in the local `gh` keyring (`git_protocol: https`) on the same machine and must run parallel Translator and Reviewer tasks without clobbering global `gh auth switch` state, leaking tokens in `ps` argv, or dirtying the primary `main` checkout used by `collab_sweep.py`.
- Viable options:
  1. **Option A (Chosen — Per-process `GH_TOKEN` + ephemeral `0700` `GIT_ASKPASS` script + ephemeral `git worktree` per task)**: Resolve the account token via `gh auth token -u <login>` in memory, pass `GH_TOKEN` in subprocess `env`, configure `git -c credential.helper=` with `GIT_ASKPASS=<temp_script>` reading `GH_TOKEN` from `env`, and perform every branch commit/push inside `.worktrees/<task_dir>` cleaned up via `git worktree remove --force`.
  2. **Option B (Global `gh auth switch` + branch switching in primary checkout)**: Switch active `gh` account and `git checkout -b` inside `<workspace_dir>`.
- Decision: Choose **Option A**.
- Costs and risks: Slightly more subprocess plumbing in `collab_worker.py` and `collab_seed.py`, fully encapsulated in one shared helper (`GitHubAccountContext`). Guarantees race-free parallel worker execution and keeps `main` permanently clean for `collab_sweep.py`.
- Rejected alternatives: Option B causes credential race conditions across parallel agents and leaves untracked/branch state on `main` that breaks `sync_clean_main` and `git pull --ff-only`.
- Affected components: `scripts/collab_worker.py`, `scripts/collab_seed.py`.

## Gate 1: normative review

| Check | Status | Evidence / required correction |
| --- | --- | --- |
| Codebase grounding | PASS | Grounded against commit `10ef139`: verified 14-key `collab_config.py`, `workbuddy.py` / `evaluate_workbuddy.py` references across 11 files, `select_winning_pr` window timer, `collab_seed.py` `push_origin=False` / working-tree issue, and live `gh label list` + collaborator roles (`vinhthang` admin, `1quachuoi9vang` write). |
| Attributable intent and explicit scope | PASS | Every requirement `REQ-031..REQ-036` traces directly to verbatim 2026-11-18 user prompts and `/grill-me` selections; `Non-goals` explicitly exclude timed candidate windows, `WorkBuddy` shims, per-tool GitHub accounts, and CI/webhooks. |
| Observable requirements and meaningful acceptance criteria | PASS | `REQ-031..REQ-036` use EARS syntax with paired deterministic success (`AC-*-1`) and boundary/failure (`AC-*-2`) criteria. |
| Assumptions and owner-deferred questions are explicit | PASS | Keyring auth assumption recorded; zero `[PROVISIONAL]` items or `TODO`/`TBD` markers exist. |
| Architectural rationale respects requirements | PASS | `Architectural rationale` + `ADR-027` and `ADR-028` govern all 6 requirements (`REQ-031..REQ-036`) without contradicting `spec.md`. |
| IDs, references, and revision route are consistent | PASS | Linked delta route selected because parent `2026-11-18-collab-multi-agent-translation` has `First entered In-Testing: 2026-11-18`; new repo-wide IDs `REQ-031..REQ-036` and `ADR-027..ADR-028` have zero collisions across `INDEX.md`. |
