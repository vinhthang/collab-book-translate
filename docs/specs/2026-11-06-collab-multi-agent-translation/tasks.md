# Tasks: Collaborative Multi-Agent Book Translation on GitHub (v1.2.0)

- **Feature ID**: `2026-11-06-collab-multi-agent-translation`
- **Status**: `Planning`
- **Version**: `1.2.0`
- **Gate 1**: PASS (re-run 2, see decisions.md)
- **Gate 2**: See the traceability matrix below; it lives in this file, per the governing rule.

No task starts before human Proceed. Check a task only when its acceptance criteria are verified with evidence, not on completion of the edit. Test-first: write the failing test (RED) before the production change (GREEN) for every new script.

## Phase 0 — `collab-book-translate` repo scaffolding

- [ ] T-001: Create the public repo `collab-book-translate` with `books/`, `runs/`, `.github/`, `CONTRIBUTING.md`, and `.gitignore` (`*.epub`, `*.pdf`, `*.docx` under `runs/`). (REQ-COLLAB-001, REQ-COLLAB-010)
- [ ] T-002: Define `manifest.json` schema (slug, title, author, `source_lang`, `target_lang`, `license`, `license_url`, `submitter`) and the allowed-licence list in CONTRIBUTING.md. (REQ-COLLAB-001, REQ-COLLAB-002, REQ-COLLAB-007)
- [ ] T-003: Create the repo labels (`agent:<name>`, `lang:<pair>`, `book:<slug>`, `status:open`, `status:candidates`, `status:done`, `status:needs-review`, `status:revise`, `status:reassign`, `status:candidate`). (REQ-COLLAB-014)
- [ ] T-004: Add `.github/` PR templates with the exact text in design.md §6 — chunk translation template as the default `.github/pull_request_template.md`, book submission under `.github/PULL_REQUEST_TEMPLATE/book.md` (opened with `gh pr create --template book`) — and CODEOWNERS (`books/**` → @vinhthang; `runs/*/book_record.json`, `runs/*/checklist.json` → main agent). (REQ-COLLAB-001, REQ-COLLAB-002, REQ-COLLAB-004)
- [ ] T-005: Add `CONTRIBUTING.md` for book submissions and contributors (public-domain / licensed only, source only, one book per PR, engine-agnostic, provenance declared). (REQ-COLLAB-001, REQ-COLLAB-002, REQ-COLLAB-004)

## Phase 1 — Validator scripts (agent-translator, test-first)

- [ ] T-101: RED: `tests/test_collab_intake.py` — manifest schema, path allowlist, pair support, size cap. GREEN: `scripts/collab_intake.py`. (REQ-COLLAB-001, REQ-COLLAB-002, REQ-COLLAB-007)
- [ ] T-102: RED: `tests/test_collab_pr_validate.py` — one-chapter diff, body fields, author ≠ reviewer, `book_record.json` untouched. GREEN: `scripts/collab_pr_validate.py`. (REQ-COLLAB-004, REQ-COLLAB-005, REQ-COLLAB-009)
- [ ] T-103: RED: `tests/test_collab_select.py` — selection rule, tie-breaks, 1-day window. GREEN: `scripts/collab_select.py`. (REQ-COLLAB-013)
- [ ] T-104: RED: `tests/test_collab_ledger.py` — audit-chain completeness. GREEN: `scripts/collab_ledger.py`. (REQ-COLLAB-012)
- [ ] T-105: Wire the validators into the sweep so deterministic failures are reported as PR comments. (REQ-COLLAB-015)
- [ ] T-106: RED: `tests/test_collab_verdict.py` — verdict mapping, `FAIL ∧ check_redraft` → REASSIGN, `FAIL ∧ ¬check_redraft` → REVISE. GREEN: T-303. (REQ-COLLAB-006)
- [ ] T-107: RED: `tests/test_collab_state.py` — state transitions, 4th-round close, label correction, conflict → REVISE. GREEN: T-305, T-306. (REQ-COLLAB-014, REQ-COLLAB-009)
- [ ] T-108: RED: `tests/test_collab_completeness.py` — completeness block on incomplete book / segment mismatch. GREEN: T-401. (REQ-COLLAB-010)
- [ ] T-109: RED: `tests/test_collab_seed.py` — seed success (live PASS, zero critical issues, audit list in merge comment) and failure (fallback/FAIL → no merge, no issues; ingest/analyze failure → no issue). GREEN: T-202. (REQ-COLLAB-003)
- [ ] T-110: RED: `tests/test_collab_sweep.py` — replay recorded `gh … --json` fixtures with no network; non-blocking lock acquire (held → log "sweep already running", exit 0), no double-processing, deterministic failure → PR comment. GREEN: T-203, T-105. (REQ-COLLAB-015)

## Phase 2 — Main-agent intake, seed, and sweep (local)

- [ ] T-201: Implement the pre-screen comment (manifest schema, format readable, language check reusing `detect_source_language` plus the Vietnamese-diacritic-density check, supported-pair check, licence assessment) for a `book/<slug>` PR. (REQ-COLLAB-002)
- [ ] T-202: Implement ingestion from `books/<slug>/` into `runs/<slug>/` via `scripts/extract.py`, whole-book `analyze`, ch01 seed translation and merge by the bot account on a live `evaluate.py` PASS only (merge comment lists the bound glossary terms; fallback/FAIL → no merge, no issues), glossary freeze with SHA, and tracking-issue creation for ch02..chNN. RED first: T-109. (REQ-COLLAB-003)
- [ ] T-203: Implement the hourly scheduled fetch / manual-run sweep: non-blocking lock acquire, `git fetch`, list open PRs/issues/reviews via `rtk gh … --json`, advance each item until it blocks on external input (review, push, window), write the ledger. No GitHub Actions, no webhook. RED first: T-110. (REQ-COLLAB-015)
- [ ] T-204: Resolve the [PROVISIONAL] chapter-split threshold by measuring the largest existing chapter's segment count, and record the decision. (REQ-COLLAB-004)

## Phase 3 — Translation, review, selection, merge

- [ ] T-301: Implement the translator flow (soft `/claim`, translate one chapter, open `translate/<slug>-<chunk>-<agent>` PR with template body, glossary SHA, and `analyze_additions.json` proposals). (REQ-COLLAB-004, REQ-COLLAB-009)
- [ ] T-302: Implement the reviewer flow (valid-review counting by login, line comments, 6-dimension score block). (REQ-COLLAB-005)
- [ ] T-303: Implement the score of record (`scripts/evaluate.py`, WorkBuddy live), `resolve_pass_threshold`, critical/quorum overrides, and the PASS / REVISE / REASSIGN mapping via `check_redraft`. RED first: T-106. (REQ-COLLAB-006)
- [ ] T-304: Implement competing-PR selection (1-day window from the sweep timestamp that first labels a PR `status:candidate`, deterministic ranking, closure comments, 2-day starved-chapter fallback PR) and the main-agent final review and merge. (REQ-COLLAB-013, REQ-COLLAB-006)
- [ ] T-305: Implement the state machine (design.md §3.5: NeedsChecks → NeedsReview → Evaluating → Revise / Reassign / Candidate → Merged / NotSelected) with the label mapping and the 3-REVISE-round cap. RED first: T-107. (REQ-COLLAB-014)
- [ ] T-306: Implement proposal binding at merge (`bind=True`) and the post-merge ledger write. (REQ-COLLAB-009, REQ-COLLAB-012)

## Phase 4 — Assembly, publication, docs

- [ ] T-401: Implement the completeness check (all chapters Done, segment counts matching) and block publication on failure. RED first: T-108. (REQ-COLLAB-010)
- [ ] T-402: Run the pilot end to end on a short public-domain English novella (3–5 chapters, en→vi): submission + human licence approval, analyze + ch01 seed, ≥2 accounts translating in parallel, ≥1 competing PR, a non-author review, a REVISE round, select-best and merge, compile and release. The pilot verifies only the genuinely end-to-end ACs (AC-002-1, 003-1, 005-1, 006-1, 009-1, 010-1); every other code-backed AC is verified by its automated test. (REQ-COLLAB-010)
- [ ] T-403: Update the repo README and `docs/specs/INDEX.md` status for this package when the pilot passes.
- [ ] T-404: Mutation-guard (≥ 80% kill) on `scripts/collab_*.py` plus an independent code review, before the spec is marked `In-Testing`.

## Gate 2 Traceability Matrix

| AC | REQ | ADR | Design | Task | Verification |
|---|---|---|---|---|---|
| AC-COLLAB-001-1 | REQ-COLLAB-001 | ADR-COLLAB-008 | design §2, §3.1 | T-001, T-002, T-004 | verification: `test_collab_intake.py` + pilot submission |
| AC-COLLAB-001-2 | REQ-COLLAB-001 | ADR-COLLAB-008 | design §3.1 | T-101, T-105 | verification: `test_collab_intake.py` (path allowlist, size cap) |
| AC-COLLAB-002-1 | REQ-COLLAB-002 | ADR-COLLAB-016 | design §3.1 | T-201, T-004 | verification: pilot submission + human approval |
| AC-COLLAB-002-2 | REQ-COLLAB-002 | ADR-COLLAB-016, 018 | design §3.1 | T-201, T-101 | verification: `test_collab_intake.py` (pair, licence) |
| AC-COLLAB-003-1 | REQ-COLLAB-003 | ADR-COLLAB-012, 019 | design §3.2 | T-109, T-202 | verification: `test_collab_seed.py` + pilot analyze + ch01 seed + issues |
| AC-COLLAB-003-2 | REQ-COLLAB-003 | ADR-COLLAB-012, 019 | design §3.2 | T-109, T-202 | verification: `test_collab_seed.py` (fallback/FAIL, ingest failure) |
| AC-COLLAB-004-1 | REQ-COLLAB-004 | ADR-COLLAB-013, 014, 015 | design §3.3 | T-301, T-102 | verification: `test_collab_pr_validate.py` + pilot PRs |
| AC-COLLAB-004-2 | REQ-COLLAB-004 | ADR-COLLAB-013, 014 | design §3.3 | T-102, T-105 | verification: `test_collab_pr_validate.py` (allowlist, body fields, `translation.xml` contract) |
| AC-COLLAB-005-1 | REQ-COLLAB-005 | ADR-COLLAB-013 | design §3.3 | T-302 | verification: pilot non-author review |
| AC-COLLAB-005-2 | REQ-COLLAB-005 | ADR-COLLAB-013 | design §3.3 | T-302, T-102 | verification: `test_collab_pr_validate.py` (author ≠ reviewer) |
| AC-COLLAB-006-1 | REQ-COLLAB-006 | ADR-COLLAB-009 | design §3.4 | T-303 | verification: pilot score of record |
| AC-COLLAB-006-2 | REQ-COLLAB-006 | ADR-COLLAB-009 | design §3.4 | T-106, T-303, T-304 | verification: `test_collab_verdict.py` (REVISE / REASSIGN mapping) |
| AC-COLLAB-007-1 | REQ-COLLAB-007 | ADR-COLLAB-011 | design §3.1 | T-002, T-101 | verification: `test_collab_intake.py` (pairs) |
| AC-COLLAB-007-2 | REQ-COLLAB-007 | ADR-COLLAB-011 | design §3.1 | T-101 | verification: `test_collab_intake.py` (unsupported pair) |
| AC-COLLAB-008-1 | REQ-COLLAB-008 [DEPRECATED] | ADR-COLLAB-010 | — | — | N/A (deprecated, carries no ACs) |
| AC-COLLAB-008-2 | REQ-COLLAB-008 [DEPRECATED] | ADR-COLLAB-010 | — | — | N/A (deprecated, carries no ACs) |
| AC-COLLAB-009-1 | REQ-COLLAB-009 | ADR-COLLAB-012 | design §3.2 | T-301, T-306 | verification: pilot proposal binding |
| AC-COLLAB-009-2 | REQ-COLLAB-009 | ADR-COLLAB-012 | design §3.2 | T-102, T-107, T-306 | verification: `test_collab_pr_validate.py` (book_record untouched) + `test_collab_state.py` (conflict → REVISE) |
| AC-COLLAB-010-1 | REQ-COLLAB-010 | ADR-COLLAB-017 | design §3.8 | T-401, T-402 | verification: pilot release assets |
| AC-COLLAB-010-2 | REQ-COLLAB-010 | ADR-COLLAB-017 | design §3.8 | T-108, T-401 | verification: `test_collab_completeness.py` (incomplete-book block) |
| AC-COLLAB-011-1 | REQ-COLLAB-011 [DEPRECATED] | ADR-COLLAB-007 | — | — | N/A (deprecated, carries no ACs) |
| AC-COLLAB-011-2 | REQ-COLLAB-011 [DEPRECATED] | ADR-COLLAB-007 | — | — | N/A (deprecated, carries no ACs) |
| AC-COLLAB-012-1 | REQ-COLLAB-012 | ADR-COLLAB-010 | design §3.8 | T-104, T-306 | verification: `test_collab_ledger.py` |
| AC-COLLAB-012-2 | REQ-COLLAB-012 | ADR-COLLAB-010 | design §3.8 | T-104, T-306 | verification: `test_collab_ledger.py` (missing link flagged) |
| AC-COLLAB-013-1 | REQ-COLLAB-013 | ADR-COLLAB-015, 020 | design §3.3 | T-103, T-304 | verification: `test_collab_select.py` + pilot competing PRs |
| AC-COLLAB-013-2 | REQ-COLLAB-013 | ADR-COLLAB-015, 020 | design §3.3 | T-103, T-304 | verification: `test_collab_select.py` (window, tie-break) |
| AC-COLLAB-014-1 | REQ-COLLAB-014 | ADR-COLLAB-010, 020 | design §3.5 | T-107, T-305 | verification: `test_collab_state.py` (transitions) |
| AC-COLLAB-014-2 | REQ-COLLAB-014 | ADR-COLLAB-010, 020 | design §3.5 | T-107, T-305 | verification: `test_collab_state.py` (4th-round close, label correction) |
| AC-COLLAB-015-1 | REQ-COLLAB-015 | ADR-COLLAB-010 | design §3.6 | T-110, T-203 | verification: `test_collab_sweep.py` (lock, no double-processing) |
| AC-COLLAB-015-2 | REQ-COLLAB-015 | ADR-COLLAB-010 | design §3.6 | T-105, T-110, T-203 | verification: `test_collab_sweep.py` (PR-comment reporting) |

Gate 2 checks: 100% per-AC forward traceability (26/26 active ACs have a matrix row; 4 deprecated rows marked N/A); 100% backward traceability, zero gold-plating (every task maps to an AC above, except T-403 README/INDEX housekeeping and T-404 mutation-guard + code review, which are quality gates on the whole package); registry sync (INDEX.md row updated to v1.2.0, REQ-COLLAB-001..015, 13 active, Status Planning).

## Gate 2 Re-run (2026-11-06, v1.2.0, author: Muse)

The v1.1.0 matrix above is kept for history. Opus round-2 review §9.5 overturned its test-first claim (R2-P1-4) and found an internal-consistency failure ("CI" vs the no-CI Non-Goal, R2-P2-2) and a missing control (R2-P1-3, deferred to thang).

| Check | Result | Evidence |
|---|---|---|
| Receipt location | PASS | Matrix lives in this file |
| 1-to-1 AC mapping | PASS | 26/26 active ACs mapped; 4 deprecated rows N/A |
| Test-first / automation | PASS | RED tasks T-106..T-110 precede their GREEN tasks (T-303, T-305/T-306, T-401, T-202, T-203/T-105); pilot covers only the genuinely end-to-end ACs (AC-002-1, 003-1, 005-1, 006-1, 009-1, 010-1); T-404 adds the mutation-guard (≥ 80% kill) and independent code review |
| Internal consistency | PASS | "CI" / `NeedsCI` renamed to sweep checks / `NeedsChecks` across spec, design, and tasks; R2-P1-3 recorded as deferred to thang (spec §2 Non-Goal, decisions.md round-2 notes), not silently dropped |
| Zero stale paths / gold-plating | PASS | `translation.xml` replaces the undefined "translation file" everywhere; no hub paths remain |
