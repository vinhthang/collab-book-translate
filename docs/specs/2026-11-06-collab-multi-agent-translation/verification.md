# Verification: Collaborative Multi-Agent Book Translation on GitHub (v1.2.0)

- **Feature ID**: `2026-11-06-collab-multi-agent-translation`
- **Status**: `Planning`
- **Version**: `1.2.0`
- **Gate 1**: PASS (re-run 2, see decisions.md)
- **Gate 2**: PASS (re-run, see tasks.md) — the traceability matrix lives in tasks.md; this file is the empirical log. No verification run has been executed yet; runs are recorded here only after implementation.

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
| Run 0 | — | Not run | No implementation yet; runs are recorded here only after Proceed and implementation by Gemini Argon |
