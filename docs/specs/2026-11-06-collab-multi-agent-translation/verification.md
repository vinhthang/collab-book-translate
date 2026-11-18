# Verification: Collaborative Multi-Agent Book Translation on GitHub (v1.2.0)

- Feature ID: 2026-11-06-collab-multi-agent-translation
- Version: 1.2.0
- Gate 1: PASS (re-run 2, see decisions.md)
- Gate 2: PASS (re-run, see tasks.md) — all tasks T-001..T-404 complete and verified.

## Planned checks

### AC-001-1 (REQ-001)
- Method: `uv run pytest tests/test_collab_intake.py -v` and pilot book submission PR #13.
- Expected outcome: Valid `book/<slug>` PR with `manifest.json` and one readable source file enters intake review.
- Execution: Passed in Run 1 (2026-11-07).

### AC-001-2 (REQ-001)
- Method: `uv run pytest tests/test_collab_intake.py -v` (path allowlist, multi-book, invalid manifest, oversize source).
- Expected outcome: Disallowed paths, invalid manifests, or oversize source files are rejected with a defect comment.
- Execution: Passed in Run 1 (2026-11-07).

### AC-002-1 (REQ-002)
- Method: Live novella pilot submission PR #13 pre-screen comment + human approval from `vinhthang`.
- Expected outcome: Allowed licence, readable format, matching `source_lang`, and supported pair pass pre-screen and merge after human approval.
- Execution: Passed in Run 1 (2026-11-07).

### AC-002-2 (REQ-002)
- Method: `uv run pytest tests/test_collab_intake.py -v` (disallowed licence, language mismatch, bot-only approval).
- Expected outcome: Invalid licence/format/language or missing `vinhthang` approval blocks merge.
- Execution: Passed in Run 1 (2026-11-07).

### AC-003-1 (REQ-003)
- Method: `uv run pytest tests/test_collab_seed.py -v` and pilot `scandal-in-bohemia` ch01 seed + tracking issues #14 and #15.
- Expected outcome: ch01 seeds on live `evaluate.py` PASS, freezes `glossary_sha`, and opens one issue per remaining chapter.
- Execution: Passed in Run 1 (2026-11-07).

### AC-003-2 (REQ-003)
- Method: `uv run pytest tests/test_collab_seed.py -v` (ingest/analyze failure and fallback/FAIL evaluation branches).
- Expected outcome: Failed ingestion or fallback/FAIL ch01 evaluation aborts merge and opens zero chapter issues.
- Execution: Passed in Run 1 (2026-11-07).

### AC-004-1 (REQ-004)
- Method: `uv run pytest tests/test_collab_pr_validate.py -v` and competing pilot PRs #16 and #17.
- Expected outcome: Multiple `translate/<slug>-<chunk>-<agent>` PRs with complete body fields and chapter-only files enter review.
- Execution: Passed in Run 1 (2026-11-07).

### AC-004-2 (REQ-004)
- Method: `uv run pytest tests/test_collab_pr_validate.py -v` and pilot PR #18 missing-segment check.
- Expected outcome: Disallowed paths, missing body fields, segment mismatches, mutated `<original>`, or missing `<hanviet>` are rejected with a defect comment.
- Execution: Passed in Run 1 (2026-11-07).

### AC-005-1 (REQ-005)
- Method: Live pilot non-author peer reviews on PRs #16, #17, and #18.
- Expected outcome: Review by a login distinct from PR author with line comment and 6-dimension score block counts as valid.
- Execution: Passed in Run 1 (2026-11-07).

### AC-005-2 (REQ-005)
- Method: `uv run pytest tests/test_collab_pr_validate.py -v` (self-review, missing line comment, incomplete score block).
- Expected outcome: Self-reviews or incomplete rubric blocks are rejected as invalid.
- Execution: Passed in Run 1 (2026-11-07).

### AC-006-1 (REQ-006)
- Method: Live WorkBuddy evaluation on pilot PRs #16, #17, and #18.
- Expected outcome: PR passing resolved threshold with zero critical issues, no quorum failure, and zero unresolved comments becomes a PASS candidate.
- Execution: Passed in Run 1 (2026-11-07).

### AC-006-2 (REQ-006)
- Method: `uv run pytest tests/test_collab_verdict.py -v` and pilot PR #17 REVISE cycle.
- Expected outcome: FAIL without redraft maps to REVISE; FAIL with `check_redraft` maps to REASSIGN; unresolved comments block merge.
- Execution: Passed in Run 1 (2026-11-07).

### AC-007-1 (REQ-007)
- Method: `uv run pytest tests/test_collab_intake.py -v` (`en->vi` and `zh->vi` manifests).
- Expected outcome: Both `en->vi` and `zh->vi` pass intake pair validation.
- Execution: Passed in Run 1 (2026-11-07).

### AC-007-2 (REQ-007)
- Method: `uv run pytest tests/test_collab_intake.py -v` (unsupported source/target language pairs).
- Expected outcome: Pairs outside `{en->vi, zh->vi}` fail intake with `unsupported pair`.
- Execution: Passed in Run 1 (2026-11-07).

### AC-009-1 (REQ-009)
- Method: Pilot PR #17 `analyze_additions.json` proposal binding at merge (`cc8e9b0`).
- Expected outcome: Approved `status: proposed` terms bind into `book_record.json` with `bind=True`.
- Execution: Passed in Run 1 (2026-11-07).

### AC-009-2 (REQ-009)
- Method: `uv run pytest tests/test_collab_pr_validate.py tests/test_collab_state.py -v`.
- Expected outcome: Conflicting proposals return PR to REVISE; PRs touching `book_record.json` do not enter review.
- Execution: Passed in Run 1 (2026-11-07).

### AC-010-1 (REQ-010)
- Method: Pilot compilation and GitHub Release `scandal-in-bohemia-v1.0`.
- Expected outcome: Completed book compiles into non-empty EPUB 3, PDF, and DOCX attached to `<slug>-v<N>` release.
- Execution: Passed in Run 1 (2026-11-07).

### AC-010-2 (REQ-010)
- Method: `uv run pytest tests/test_collab_completeness.py -v`.
- Expected outcome: Incomplete chapters or segment-count mismatches block publication.
- Execution: Passed in Run 1 (2026-11-07).

### AC-012-1 (REQ-012)
- Method: `uv run pytest tests/test_collab_ledger.py -v` and `runs/scandal-in-bohemia/collab_ledger.json`.
- Expected outcome: Merged chapter records issue, selected/rejected PRs, logins, scores, verdict, merge SHA, and glossary SHA.
- Execution: Passed in Run 1 (2026-11-07).

### AC-012-2 (REQ-012)
- Method: `uv run pytest tests/test_collab_ledger.py -v` (missing audit-link cases).
- Expected outcome: Missing ledger links flag the chapter merge as unaudited.
- Execution: Passed in Run 1 (2026-11-07).

### AC-013-1 (REQ-013)
- Method: `uv run pytest tests/test_collab_select.py -v` and pilot competing PRs #16 vs #17.
- Expected outcome: Highest-ranked PASS candidate merges and non-selected PRs close with score and rationale.
- Execution: Passed in Run 1 (2026-11-07).

### AC-013-2 (REQ-013)
- Method: `uv run pytest tests/test_collab_select.py -v` (open window, tie-breaks, 2-day starved-chapter fallback).
- Expected outcome: Open window defers selection, ties break by fewer critical/major issues then earliest PR, and starved chapters trigger bot fallback PR.
- Execution: Passed in Run 1 (2026-11-07).

### AC-014-1 (REQ-014)
- Method: `uv run pytest tests/test_collab_state.py -v` and pilot PR #17/#18 state transitions.
- Expected outcome: Issues and PRs transition across defined states/labels and re-enter `NeedsChecks` up to round 3.
- Execution: Passed in Run 1 (2026-11-07).

### AC-014-2 (REQ-014)
- Method: `uv run pytest tests/test_collab_state.py -v` (4th-round close as REASSIGN and unauthorized `status:*` label correction).
- Expected outcome: Fourth REVISE round closes PR as REASSIGN and unauthorized `status:*` labels are corrected on sweep.
- Execution: Passed in Run 1 (2026-11-07).

### AC-015-1 (REQ-015)
- Method: `uv run pytest tests/test_collab_sweep.py -v`.
- Expected outcome: Idempotent local sweep advances open items, writes ledger, and avoids double-processing.
- Execution: Passed in Run 1 (2026-11-07).

### AC-015-2 (REQ-015)
- Method: `uv run pytest tests/test_collab_sweep.py -v` (held lock exit 0 and PR defect comments).
- Expected outcome: Overlapping sweep logs `"sweep already running"` and exits 0; deterministic failures post PR comments.
- Execution: Passed in Run 1 (2026-11-07).

## Automated tests (one per code-backed AC)

| Test file | ACs covered | Command |
|---|---|---|
| `tests/test_collab_intake.py` | AC-001-1, AC-001-2, AC-002-2, AC-007-1, AC-007-2 | `uv run pytest tests/test_collab_intake.py -v` |
| `tests/test_collab_pr_validate.py` | AC-004-1, AC-004-2, AC-005-2, AC-009-2 | `uv run pytest tests/test_collab_pr_validate.py -v` |
| `tests/test_collab_select.py` | AC-013-1, AC-013-2 | `uv run pytest tests/test_collab_select.py -v` |
| `tests/test_collab_ledger.py` | AC-012-1, AC-012-2 | `uv run pytest tests/test_collab_ledger.py -v` |
| `tests/test_collab_verdict.py` | AC-006-2 | `uv run pytest tests/test_collab_verdict.py -v` |
| `tests/test_collab_state.py` | AC-014-1, AC-014-2, AC-009-2 | `uv run pytest tests/test_collab_state.py -v` |
| `tests/test_collab_completeness.py` | AC-010-2 | `uv run pytest tests/test_collab_completeness.py -v` |
| `tests/test_collab_seed.py` | AC-003-1, AC-003-2 | `uv run pytest tests/test_collab_seed.py -v` |
| `tests/test_collab_sweep.py` | AC-015-1, AC-015-2 | `uv run pytest tests/test_collab_sweep.py -v` |

Mutation-guard (T-404): ≥ 80% kill on `scripts/collab_*.py` plus an independent code review, before the spec is marked `In-Testing`.

Manual end-to-end rows (pilot only, the genuinely end-to-end ACs): AC-002-1, AC-003-1, AC-005-1, AC-006-1, AC-009-1, AC-010-1 — each verified on the novella pilot (T-402) with the PR / issue / release linked in the run record below. Every other code-backed AC is verified by its automated test above, not by the pilot.

## Verification Runs

| Run | Date | Result | Evidence |
|---|---|---|---|
| Run 1 | 2026-11-07 | PASS | Complete Live Pilot T-402 on `vinhthang/collab-book-translate`. 156/156 automated tests passing. 8/8 files ≥ 80% mutation kill rate. Live links documented below. |

### Live Novella Pilot Empirical Evidence (T-402: A Scandal in Bohemia)

> **Pilot Scope Note**: `scandal-in-bohemia` is the 3-chapter, 12-segment public-domain pilot abridgment of Arthur Conan Doyle's *A Scandal in Bohemia* (Project Gutenberg #1661).

1. **Book Intake & Prescreen (AC-001-1, AC-002-1, AC-002-2)**:
   - PR [#13](https://github.com/vinhthang/collab-book-translate/pull/13): Book submission PR (`book/scandal-in-bohemia`) with `manifest.json` and `source/scandal_in_bohemia.txt`.
   - Bot prescreen: Prescreen PASS comment posted by `1quachuoi9vang` confirming public-domain license (Project Gutenberg #1661) and supported pair `en->vi`.
   - Human approval: Formal sign-off and approval comment posted by owner `vinhthang`.

2. **Chapter 1 Reference Seed & Glossary Freeze (AC-003-1, AC-003-2)**:
   - Merged PR #13 via `seed_merged_book` with live WorkBuddy evaluation PASS (Score 4.60).
   - Chapter 1 seeded: `runs/scandal-in-bohemia/ch01/source.txt`, `translation.xml`, `final.txt`.
   - Canonical `book_record.json` seeded with tone, pronoun system, character pronouns, and frozen glossary.
   - Initial Seed Commit: `fdd5c7e2277992e46761003d876601d2dcd1afec` (`glossary_sha`).
   - Chapter tracking issues opened:
     - Issue [#14](https://github.com/vinhthang/collab-book-translate/issues/14) for Chapter II
     - Issue [#15](https://github.com/vinhthang/collab-book-translate/issues/15) for Chapter III

3. **Chapter 2 Competing Translations & Proposal Binding (AC-004-1, AC-005-1, AC-006-1, AC-009-1, AC-013-1)**:
   - PR A ([#16](https://github.com/vinhthang/collab-book-translate/pull/16)): Submitted by `vinhthang`, reviewed by `1quachuoi9vang`. Candidate score: 4.05.
   - PR B ([#17](https://github.com/vinhthang/collab-book-translate/pull/17)): Submitted by `1quachuoi9vang` with `analyze_additions.json` proposing `Briony Lodge` and `Church of St. Monica`. Reviewed by `vinhthang`.
   - Iterative revision: Evaluator initially flagged register/collocation nuances (score 3.96 FAIL) -> author pushed refined commit `ea28d4e` -> re-evaluation scored 4.50 PASS.
   - Selection: PR #17 selected as winning translation over PR #16 based on score ranking (4.50 vs 4.05).
   - PR #17 merged into `main` (merge commit `cc8e9b0`).
   - PR #16 closed with polite closure comment citing score of record and selection rationale.
   - Term proposal binding: `Briony Lodge` -> `Biệt thự Briony` and `Church of St. Monica` -> `Nhà thờ Thánh Monica` bound into `runs/scandal-in-bohemia/book_record.json`.
   - Issue #14 closed.

4. **Chapter 3 Deterministic Check & Revision Workflow (AC-004-2, AC-006-2, AC-014-1, AC-014-2)**:
   - Issue #15 claimed via `/claim` by `vinhthang`.
   - PR [#18](https://github.com/vinhthang/collab-book-translate/pull/18): Submitted by `vinhthang` with initial commit omitting segment S-0004.
   - Deterministic check: Sweep detected segment count mismatch and missing S-0004, labeled `status:revise`, and posted defect details.
   - Revision: Author pushed complete translation (`S-0001..S-0004`) in commit `66cb3e5`.
   - Independent peer review: Collaborator `1quachuoi9vang` reviewed with 6-dimension scores (4.88 overall).
   - Evaluation & Selection: Live WorkBuddy evaluation scored 4.50 PASS; PR #18 selected as winner and merged into `main` (merge commit `121d6f9`).
   - Chapter 3 finalized: `runs/scandal-in-bohemia/ch03/final.txt` written; Issue #15 closed.

5. **Book Completeness Verification & Publication (AC-010-1, AC-010-2, AC-012-1)**:
   - `verify_book_completeness` confirmed all 3 chapters (`ch01`, `ch02`, `ch03`) status "done" with matching segment parity and final text.
   - Audit trail recorded in `runs/scandal-in-bohemia/collab_ledger.json`.
   - Full publication deliverables compiled:
     - `scandal-in-bohemia_vietnamese.epub` (8,105 bytes)
     - `scandal-in-bohemia_vietnamese.pdf` (29,160 bytes)
     - `scandal-in-bohemia_vietnamese.docx` (39,650 bytes)
   - Official GitHub Release published:
     - Release: [scandal-in-bohemia-v1.0](https://github.com/vinhthang/collab-book-translate/releases/tag/scandal-in-bohemia-v1.0) with all 3 assets attached.
