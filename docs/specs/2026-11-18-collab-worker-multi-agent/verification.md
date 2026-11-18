# Verification: Agent-Agnostic Role-Based Multi-Agent Collaboration & Worker CLI

Governing specification: [spec.md](./spec.md)

## Planned checks

### AC-031-1 (REQ-031)

- Method: Run `.venv/bin/pytest tests/test_collab_config.py -v` and inspect `plugin.json`, `config.example.json`, and repository hygiene assertions.
- Expected outcome: 12-key `CollabConfig` loads cleanly, `plugin.json` matches Antigravity's schema, `workbuddy.py` / `evaluate_workbuddy.py` are absent, and zero `workbuddy`, `rtk`, or `/Users/` tokens appear across tracked code and docs.
- Execution: Not run.

### AC-031-2 (REQ-031)

- Method: Run `.venv/bin/pytest tests/test_collab_config.py -v` with missing files, malformed JSON, missing keys, invalid domains, and `bot_login == human_approver`.
- Expected outcome: `load_collab_config` raises `ConfigError` immediately without fallback.
- Execution: Not run.

### AC-032-1 (REQ-032)

- Method: Run `.venv/bin/pytest tests/test_collab_verdict.py tests/test_collab_sweep.py -v` and verify live peer-review scoring on GitHub PRs.
- Expected outcome: Commit-fresh non-author `REQ-022` reviews passing language/anti-convert checks and `effective_threshold` produce `verdict == "PASS"`, `fallback_unverified == False`, and deterministic weighted `score_of_record`.
- Execution: Not run.

### AC-032-2 (REQ-032)

- Method: Run `.venv/bin/pytest tests/test_collab_verdict.py tests/test_collab_sweep.py -v` with self-reviews, stale-commit reviews, untranslated English/Chinese drafts, `[critical]` issues, and un-cleared `CHANGES_REQUESTED` reviews.
- Expected outcome: Stale/self-reviews stay in `status:needs-review`; language/critical/threshold failures route to `status:revise` or `status:reassign`.
- Execution: Not run.

### AC-033-1 (REQ-033)

- Method: Run `.venv/bin/pytest tests/test_collab_select.py tests/test_collab_sweep.py -v` and execute single-PR + competing-PR sweeps live on GitHub.
- Expected outcome: Passing reviewed PRs merge immediately in the same sweep (`Open -> Done`) with zero `[collab-window-start]` comments, closing losing competing PRs as `status:not-selected`.
- Execution: Not run.

### AC-033-2 (REQ-033)

- Method: Run `.venv/bin/pytest tests/test_collab_select.py -v` with tied scores, major/minor issue tie-breaks, and zero-eligible-candidate cases.
- Expected outcome: Ties break by `(major_issue_count, minor_issue_count, pr_number)`, and chapters with zero passing eligible PRs do not merge.
- Execution: Not run.

### AC-034-1 (REQ-034)

- Method: Run `.venv/bin/pytest tests/test_collab_seed.py tests/test_collab_sweep.py -v` and run live `ch01` seeding on GitHub.
- Expected outcome: `--prepare-only` prepares `source.txt` + `book_record.json`, initial `runs/<slug>` pushes to `origin/main`, `ch01` PR opens from an ephemeral worktree, missing labels auto-create via `gh label create --force`, `ch01` merges cleanly, and `ch02..chNN` issues open.
- Execution: Not run.

### AC-034-2 (REQ-034)

- Method: Run `.venv/bin/pytest tests/test_collab_seed.py -v` with invalid XML, failed language detection, and failing `ch01` evaluation scores.
- Expected outcome: `ch01` does not merge, zero `ch02..chNN` issues are created, and `seed_status` becomes `"blocked_failed"`.
- Execution: Not run.

### AC-035-1 (REQ-035)

- Method: Run `.venv/bin/pytest tests/test_collab_worker.py -v` and execute live 2-account (`vinhthang` + `1quachuoi9vang`) `collab_worker.py` commands on GitHub.
- Expected outcome: All 5 subcommands isolate `GH_TOKEN` + `GIT_ASKPASS`, clean up `.worktrees/`, leave global `gh auth` unchanged, and expose zero tokens on argv.
- Execution: Not run.

### AC-035-2 (REQ-035)

- Method: Run `.venv/bin/pytest tests/test_collab_worker.py -v` with invalid manifests, invalid `translation.xml`, wrong intake approver, and self-reviews (`reviewer == pr_author`).
- Expected outcome: `collab_worker.py` exits non-zero with structured JSON defects before pushing branches or posting PRs/reviews.
- Execution: Not run.

### AC-036-1 (REQ-036)

- Method: Run `.venv/bin/pytest tests/test_collab_worker.py -v` and query `lsrpc GetAllPlugins` after symlinking `~/.gemini/config/plugins/collab-book-translate`.
- Expected outcome: `README.md` defines all 4 roles with exact commands and starter prompts, `CONTRIBUTING.md` aligns, and Antigravity discovers `collab-book-translate` with skill `collab-translate-book` and agents `collab_translator`, `collab_reviewer`.
- Execution: Not run.

### AC-036-2 (REQ-036)

- Method: Run `.venv/bin/pytest tests/test_collab_worker.py -v` checking role headings, frontmatter keys, and absence of `WorkBuddy` or window-gate wording.
- Expected outcome: Missing roles, missing frontmatter, or forbidden vendor/window-gate strings fail automated conformance assertions.
- Execution: Not run.

## Execution evidence

No checks have been executed.

## Real-world observations

No real-world confirmation has been recorded.
