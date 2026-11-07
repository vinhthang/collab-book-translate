# Verification: Collaborative Multi-Agent Book Translation on GitHub (v1.2.0)

- **Feature ID**: `2026-11-06-collab-multi-agent-translation`
- **Status**: `In-Testing`
- **Version**: `1.2.0`
- **Gate 1**: PASS (re-run 2, see decisions.md)
- **Gate 2**: PASS (re-run, see tasks.md) — all tasks T-001..T-404 complete and verified.

## Automated tests (one per code-backed AC)

| Test file | ACs covered | Command |
|---|---|---|
| `tests/test_collab_intake.py` | AC-COLLAB-001-1, 001-2, 002-2, 007-1, 007-2 | `uv run pytest tests/test_collab_intake.py -v` |
| `tests/test_collab_pr_validate.py` | AC-COLLAB-004-1, 004-2, 005-2, 009-2 | `uv run pytest tests/test_collab_pr_validate.py -v` |
| `tests/test_collab_select.py` | AC-COLLAB-013-1, 013-2 | `uv run pytest tests/test_collab_select.py -v` |
| `tests/test_collab_ledger.py` | AC-COLLAB-012-1, 012-2 | `uv run pytest tests/test_collab_ledger.py -v` |
| `tests/test_collab_verdict.py` | AC-COLLAB-006-2 | `uv run pytest tests/test_collab_verdict.py -v` |
| `tests/test_collab_state.py` | AC-COLLAB-014-1, 014-2, 009-2 | `uv run pytest tests/test_collab_state.py -v` |
| `tests/test_collab_completeness.py` | AC-COLLAB-010-2 | `uv run pytest tests/test_collab_completeness.py -v` |
| `tests/test_collab_seed.py` | AC-COLLAB-003-1, 003-2 | `uv run pytest tests/test_collab_seed.py -v` |
| `tests/test_collab_sweep.py` | AC-COLLAB-015-1, 015-2 | `uv run pytest tests/test_collab_sweep.py -v` |

Mutation-guard (T-404): ≥ 80% kill on `scripts/collab_*.py` plus an independent code review, before the spec is marked `In-Testing`.

Manual end-to-end rows (pilot only, the genuinely end-to-end ACs): AC-COLLAB-002-1, 003-1, 005-1, 006-1, 009-1, 010-1 — each verified on the novella pilot (T-402) with the PR / issue / release linked in the run record below. Every other code-backed AC is verified by its automated test above, not by the pilot.

## Verification Runs

| Run | Date | Result | Evidence |
|---|---|---|---|
| Run 1 | 2026-11-07 | PASS | Complete Live Pilot T-402 on `vinhthang/collab-book-translate`. 156/156 automated tests passing. 8/8 files ≥ 80% mutation kill rate. Live links documented below. |

### Live Novella Pilot Empirical Evidence (T-402: A Scandal in Bohemia)

> **Pilot Scope Note**: `scandal-in-bohemia` is the 3-chapter, 12-segment public-domain pilot abridgment of Arthur Conan Doyle's *A Scandal in Bohemia* (Project Gutenberg #1661).

1. **Book Intake & Prescreen (AC-COLLAB-001-1, AC-COLLAB-002-1, AC-COLLAB-002-2)**:
   - PR [#13](https://github.com/vinhthang/collab-book-translate/pull/13): Book submission PR (`book/scandal-in-bohemia`) with `manifest.json` and `source/scandal_in_bohemia.txt`.
   - Bot prescreen: Prescreen PASS comment posted by `1quachuoi9vang` confirming public-domain license (Project Gutenberg #1661) and supported pair `en->vi`.
   - Human approval: Formal sign-off and approval comment posted by owner `vinhthang`.

2. **Chapter 1 Reference Seed & Glossary Freeze (AC-COLLAB-003-1, AC-COLLAB-003-2)**:
   - Merged PR #13 via `seed_merged_book` with live WorkBuddy evaluation PASS (Score 4.60).
   - Chapter 1 seeded: `runs/scandal-in-bohemia/ch01/source.txt`, `translation.xml`, `final.txt`.
   - Canonical `book_record.json` seeded with tone, pronoun system, character pronouns, and frozen glossary.
   - Initial Seed Commit: `fdd5c7e2277992e46761003d876601d2dcd1afec` (`glossary_sha`).
   - Chapter tracking issues opened:
     - Issue [#14](https://github.com/vinhthang/collab-book-translate/issues/14) for Chapter II
     - Issue [#15](https://github.com/vinhthang/collab-book-translate/issues/15) for Chapter III

3. **Chapter 2 Competing Translations & Proposal Binding (AC-COLLAB-004-1, AC-COLLAB-005-1, AC-COLLAB-006-1, AC-COLLAB-009-1, AC-COLLAB-013-1)**:
   - PR A ([#16](https://github.com/vinhthang/collab-book-translate/pull/16)): Submitted by `vinhthang`, reviewed by `1quachuoi9vang`. Candidate score: 4.05.
   - PR B ([#17](https://github.com/vinhthang/collab-book-translate/pull/17)): Submitted by `1quachuoi9vang` with `analyze_additions.json` proposing `Briony Lodge` and `Church of St. Monica`. Reviewed by `vinhthang`.
   - Iterative revision: Evaluator initially flagged register/collocation nuances (score 3.96 FAIL) -> author pushed refined commit `ea28d4e` -> re-evaluation scored 4.50 PASS.
   - Selection: PR #17 selected as winning translation over PR #16 based on score ranking (4.50 vs 4.05).
   - PR #17 merged into `main` (merge commit `cc8e9b0`).
   - PR #16 closed with polite closure comment citing score of record and selection rationale.
   - Term proposal binding: `Briony Lodge` -> `Biệt thự Briony` and `Church of St. Monica` -> `Nhà thờ Thánh Monica` bound into `runs/scandal-in-bohemia/book_record.json`.
   - Issue #14 closed.

4. **Chapter 3 Deterministic Check & Revision Workflow (AC-COLLAB-004-2, AC-COLLAB-006-2, AC-COLLAB-014-1, AC-COLLAB-014-2)**:
   - Issue #15 claimed via `/claim` by `vinhthang`.
   - PR [#18](https://github.com/vinhthang/collab-book-translate/pull/18): Submitted by `vinhthang` with initial commit omitting segment S-0004.
   - Deterministic check: Sweep detected segment count mismatch and missing S-0004, labeled `status:revise`, and posted defect details.
   - Revision: Author pushed complete translation (`S-0001..S-0004`) in commit `66cb3e5`.
   - Independent peer review: Collaborator `1quachuoi9vang` reviewed with 6-dimension scores (4.88 overall).
   - Evaluation & Selection: Live WorkBuddy evaluation scored 4.50 PASS; PR #18 selected as winner and merged into `main` (merge commit `121d6f9`).
   - Chapter 3 finalized: `runs/scandal-in-bohemia/ch03/final.txt` written; Issue #15 closed.

5. **Book Completeness Verification & Publication (AC-COLLAB-010-1, AC-COLLAB-010-2, AC-COLLAB-012-1)**:
   - `verify_book_completeness` confirmed all 3 chapters (`ch01`, `ch02`, `ch03`) status "done" with matching segment parity and final text.
   - Audit trail recorded in `runs/scandal-in-bohemia/collab_ledger.json`.
   - Full publication deliverables compiled:
     - `scandal-in-bohemia_vietnamese.epub` (8,105 bytes)
     - `scandal-in-bohemia_vietnamese.pdf` (29,160 bytes)
     - `scandal-in-bohemia_vietnamese.docx` (39,650 bytes)
   - Official GitHub Release published:
     - Release: [scandal-in-bohemia-v1.0](https://github.com/vinhthang/collab-book-translate/releases/tag/scandal-in-bohemia-v1.0) with all 3 assets attached.
