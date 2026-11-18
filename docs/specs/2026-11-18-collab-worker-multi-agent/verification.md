# Verification: Agent-Agnostic Role-Based Multi-Agent Collaboration & Worker CLI

Governing specification: [spec.md](./spec.md)

## Planned checks

### AC-031-1 (REQ-031)

- Method: Run `.venv/bin/pytest tests/test_collab_config.py -v` and inspect `plugin.json`, `config.example.json`, and repository hygiene assertions.
- Expected outcome: 12-key `CollabConfig` loads cleanly, `plugin.json` matches Antigravity's schema, `workbuddy.py` / `evaluate_workbuddy.py` are absent, and zero `workbuddy`, `rtk`, or `/Users/` tokens appear across tracked code and docs.
- Execution: [Run 1](#run-1--2026-11-18) — `.venv/bin/pytest tests/test_collab_config.py` passed (`166 passed` in collab suite); `mutation-guard` on `scripts/collab_config.py`: `100.0% (8/8 killed)`.

### AC-031-2 (REQ-031)

- Method: Run `.venv/bin/pytest tests/test_collab_config.py -v` with missing files, malformed JSON, missing keys, invalid domains, and `bot_login == human_approver`.
- Expected outcome: `load_collab_config` raises `ConfigError` immediately without fallback.
- Execution: [Run 1](#run-1--2026-11-18) — Missing-key and `bot_login == human_approver` boundary assertions passed (`8/8` mutants killed).

### AC-032-1 (REQ-032)

- Method: Run `.venv/bin/pytest tests/test_collab_verdict.py tests/test_collab_sweep.py -v` and verify live peer-review scoring on GitHub PRs.
- Expected outcome: Commit-fresh non-author `REQ-022` reviews passing language/anti-convert checks and `effective_threshold` produce `verdict == "PASS"`, `fallback_unverified == False`, and deterministic weighted `score_of_record`.
- Execution: [Run 1](#run-1--2026-11-18) — `tests/test_collab_verdict.py` + `tests/test_collab_sweep.py` passed (`mutation-guard` `collab_verdict.py`: `85.0%`, `17/20 killed`); live PRs `#25` (`4.87`), `#26` (`4.90`), `#27` (`4.80`), `#28` (`4.90`), and `#29` (`4.90`) scored `PASS`.

### AC-032-2 (REQ-032)

- Method: Run `.venv/bin/pytest tests/test_collab_verdict.py tests/test_collab_sweep.py -v` with self-reviews, stale-commit reviews, untranslated English/Chinese drafts, `[critical]` issues, and un-cleared `CHANGES_REQUESTED` reviews.
- Expected outcome: Stale/self-reviews stay in `status:needs-review`; language/critical/threshold failures route to `status:revise` or `status:reassign`.
- Execution: [Run 1](#run-1--2026-11-18) — Self-review, stale-commit, language-detection, and blocking-review boundary tests passed.

### AC-033-1 (REQ-033)

- Method: Run `.venv/bin/pytest tests/test_collab_select.py tests/test_collab_sweep.py -v` and execute single-PR + competing-PR sweeps live on GitHub.
- Expected outcome: Passing reviewed PRs merge immediately in the same sweep (`Open -> Done`) with zero `[collab-window-start]` comments, closing losing competing PRs as `status:not-selected`.
- Execution: [Run 1](#run-1--2026-11-18) — `collab_select.py` (`100.0%`, `20/20 killed`) and `collab_sweep.py` passed; live competing `ch03` selected PR `#26` and closed `#27` (`status:not-selected`), while `ch04` (`#28`) and `ch05` (`#29`) merged in one sweep with zero `[collab-window-start]` comments on Issues `#23` and `#24`.

### AC-033-2 (REQ-033)

- Method: Run `.venv/bin/pytest tests/test_collab_select.py -v` with tied scores, major/minor issue tie-breaks, and zero-eligible-candidate cases.
- Expected outcome: Ties break by `(major_issue_count, minor_issue_count, pr_number)`, and chapters with zero passing eligible PRs do not merge.
- Execution: [Run 1](#run-1--2026-11-18) — Tie-break and empty-eligible-candidate assertions passed (`20/20` mutants killed).

### AC-034-1 (REQ-034)

- Method: Run `.venv/bin/pytest tests/test_collab_seed.py tests/test_collab_sweep.py -v` and run live `ch01` seeding on GitHub.
- Expected outcome: `--prepare-only` prepares `source.txt` + `book_record.json`, initial `runs/<slug>` pushes to `origin/main`, `ch01` PR opens from an ephemeral worktree, missing labels auto-create via `gh label create --force`, `ch01` merges cleanly, and `ch02..chNN` issues open.
- Execution: [Run 1](#run-1--2026-11-18) — `tests/test_collab_seed.py` passed; live `happy-prince` seeding pushed initial `runs/happy-prince` (`3660b4c`), merged `ch01` PR `#20` (`cb01af5`), froze `glossary_sha` (`b0d2d12`), and opened Issues `#21`–`#24`.

### AC-034-2 (REQ-034)

- Method: Run `.venv/bin/pytest tests/test_collab_seed.py -v` with invalid XML, failed language detection, and failing `ch01` evaluation scores.
- Expected outcome: `ch01` does not merge, zero `ch02..chNN` issues are created, and `seed_status` becomes `"blocked_failed"`.
- Execution: [Run 1](#run-1--2026-11-18) — Boundary/failure tests in `tests/test_collab_seed.py` passed.

### AC-035-1 (REQ-035)

- Method: Run `.venv/bin/pytest tests/test_collab_worker.py -v` and execute live 2-account (`vinhthang` + `1quachuoi9vang`) `collab_worker.py` commands on GitHub.
- Expected outcome: All 5 subcommands isolate `GH_TOKEN` + `GIT_ASKPASS`, clean up `.worktrees/`, leave global `gh auth` unchanged, and expose zero tokens on argv.
- Execution: [Run 1](#run-1--2026-11-18) — `tests/test_collab_worker.py` passed (`mutation-guard`: `100.0%`, `17/17 killed` at `f03d8c3`); live execution submitted/reviewed PRs `#25`–`#29` with empty `.worktrees/` and unchanged `gh auth status` keyring state.

### AC-035-2 (REQ-035)

- Method: Run `.venv/bin/pytest tests/test_collab_worker.py -v` with invalid manifests, invalid `translation.xml`, wrong intake approver, and self-reviews (`reviewer == pr_author`).
- Expected outcome: `collab_worker.py` exits non-zero with structured JSON defects before pushing branches or posting PRs/reviews.
- Execution: [Run 1](#run-1--2026-11-18) — Pre-flight rejection, spoofed-prescreen `bot_login` check, self-review block, and CLI `SystemExit(1)` assertions passed (`17/17` mutants killed).

### AC-036-1 (REQ-036)

- Method: Run `.venv/bin/pytest tests/test_collab_worker.py -v` and query `lsrpc GetAllPlugins` after symlinking `~/.gemini/config/plugins/collab-book-translate`.
- Expected outcome: `README.md` defines all 4 roles with exact commands and starter prompts, `CONTRIBUTING.md` aligns, and Antigravity discovers `collab-book-translate` with skill `collab-translate-book` and agents `collab_translator`, `collab_reviewer`.
- Execution: [Run 1](#run-1--2026-11-18) — `TestDocAndPluginConformance` passed and `lsrpc GetAllPlugins` returned `collab-book-translate` (`disabled: false`, `skills: ["collab-translate-book"]`, `agents: ["collab_reviewer", "collab_translator"]`).

### AC-036-2 (REQ-036)

- Method: Run `.venv/bin/pytest tests/test_collab_worker.py -v` checking role headings, frontmatter keys, and absence of `WorkBuddy` or window-gate wording.
- Expected outcome: Missing roles, missing frontmatter, or forbidden vendor/window-gate strings fail automated conformance assertions.
- Execution: [Run 1](#run-1--2026-11-18) — Conformance assertions in `tests/test_collab_worker.py` passed (`166 passed in 5.02s`).

## Execution evidence

### Run 1 — 2026-11-18

- Checked: `REQ-031..REQ-036` / `AC-031-1..AC-036-2` (`main` at `f03d8c3` plus live 5-chapter GitHub run `happy-prince-v1.0`)
- Command: `.venv/bin/pytest tests/ -q`, `.venv/bin/pytest tests/test_collab_{config,verdict,select,seed,sweep,worker}.py -q`, `mutation_runner.py` on `collab_{config,select,verdict,worker}.py`, `prepare_code_review.py --base 10ef139`, `lsrpc GetAllPlugins`, `gh release view happy-prince-v1.0`
- Output excerpt:

```text
296 passed, 6 warnings in 6.73s
166 passed, 6 warnings in 5.02s
mutation-guard: collab_config.py 100.0% (8/8), collab_select.py 100.0% (20/20), collab_verdict.py 85.0% (17/20), collab_worker.py 100.0% (17/17)
code-review: Approved (0 P0, 0 P1 after remediating approve_intake bot_login check and agent-name label in f03d8c3)
lsrpc GetAllPlugins: {"name": "collab-book-translate", "disabled": false, "skills": ["collab-translate-book"], "agents": ["collab_reviewer", "collab_translator"]}
gh release view happy-prince-v1.0: happy-prince_vietnamese.epub (55203 B), happy-prince_vietnamese.pdf (206081 B), happy-prince_vietnamese.docx (83315 B)
```

- Result: AC-031-1 `PASS`, AC-031-2 `PASS`, AC-032-1 `PASS`, AC-032-2 `PASS`, AC-033-1 `PASS`, AC-033-2 `PASS`, AC-034-1 `PASS`, AC-034-2 `PASS`, AC-035-1 `PASS`, AC-035-2 `PASS`, AC-036-1 `PASS`, AC-036-2 `PASS` — All 12 acceptance criteria verified after final edits; live 2-account GitHub workflow completed end-to-end; specification stays `In-Testing` pending owner confirmation for `Verified`.

## Real-world observations

- End-to-end live 2-account book translation executed on `vinhthang/collab-book-translate` for Oscar Wilde's *The Happy Prince and Other Tales* (`en->vi`, 5 unabridged chapters, Project Gutenberg #902):
  - **Intake & Seeding**: Book intake PR `#19` (`vinhthang`, approved after `1quachuoi9vang` pre-screen `PASS`) -> `runs/happy-prince` initialized (`3660b4c`), `ch01` seed PR `#20` merged (`cb01af5`), immutable `glossary_sha` recorded (`b0d2d12`), Issues `#21`–`#24` opened.
  - **Bidirectional Chapters & Competing PRs**: `ch02` (Issue `#21`: PR `#25` by `vinhthang`, reviewed by `1quachuoi9vang` `4.87`), `ch03` competing PRs (Issue `#22`: PR `#26` by `vinhthang` `4.90` selected & merged at `e68e92d`; PR `#27` by `1quachuoi9vang` `4.80` closed `status:not-selected`), `ch04` (Issue `#23`: PR `#28` by `vinhthang`, reviewed by `1quachuoi9vang` `4.90`, single-sweep merge `2e62520`), `ch05` reverse roles (Issue `#24`: PR `#29` by `1quachuoi9vang`, reviewed by `vinhthang` `4.90`, single-sweep merge `596b213`).
  - **Publication & Audit**: All 5 chapters `done` in `runs/happy-prince/checklist.json` and recorded in `runs/happy-prince/collab_ledger.json`; GitHub Release `happy-prince-v1.0` published (`dbaaa3c`) with attached `happy-prince_vietnamese.epub` (`55,203` bytes), `happy-prince_vietnamese.pdf` (`206,081` bytes), and `happy-prince_vietnamese.docx` (`83,315` bytes) and zero binaries tracked in Git. Specification remains `In-Testing` awaiting explicit owner confirmation for `Verified`.
