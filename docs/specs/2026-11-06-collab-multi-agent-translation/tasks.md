# Tasks: Collaborative Multi-Agent Book Translation on GitHub (v1.2.0)

- Feature ID: 2026-11-06-collab-multi-agent-translation
- Version: 1.2.0
- Gate 1: PASS (re-run 2, see decisions.md)
- Gate 2: See the traceability matrix below; it lives in this file, per the governing rule.

No task starts before human Proceed. Check a task only when its acceptance criteria are verified with evidence, not on completion of the edit. Test-first: write the failing test (RED) before the production change (GREEN) for every new script.

## Phase 0 — `collab-book-translate` repo scaffolding

- [x] T-001: Create the public repo `collab-book-translate` with `books/`, `runs/`, `.github/`, `CONTRIBUTING.md`, and `.gitignore` (`*.epub`, `*.pdf`, `*.docx` under `runs/`). (REQ-001, REQ-010)
- [x] T-002: Define `manifest.json` schema (slug, title, author, `source_lang`, `target_lang`, `license`, `license_url`, `submitter`) and the allowed-licence list in CONTRIBUTING.md. (REQ-001, REQ-002, REQ-007)
- [x] T-003: Create the repo labels (`agent:<name>`, `lang:<pair>`, `book:<slug>`, `status:open`, `status:candidates`, `status:done`, `status:needs-review`, `status:revise`, `status:reassign`, `status:candidate`). (REQ-014)
- [x] T-004: Add `.github/` PR templates with the exact text in design.md §6 — chunk translation template as the default `.github/pull_request_template.md`, book submission under `.github/PULL_REQUEST_TEMPLATE/book.md` (opened with `gh pr create --template book`) — and CODEOWNERS (`books/**` → @vinhthang; `runs/*/book_record.json`, `runs/*/checklist.json` → main agent). (REQ-001, REQ-002, REQ-004)
- [x] T-005: Add `CONTRIBUTING.md` for book submissions and contributors (public-domain / licensed only, source only, one book per PR, engine-agnostic, provenance declared). (REQ-001, REQ-002, REQ-004)

## Phase 1 — Validator scripts (agent-translator, test-first)

- [x] T-101: RED: `tests/test_collab_intake.py` — manifest schema, path allowlist, pair support, size cap. GREEN: `scripts/collab_intake.py`. (REQ-001, REQ-002, REQ-007)
- [x] T-102: RED: `tests/test_collab_pr_validate.py` — one-chapter diff, body fields, author ≠ reviewer, `book_record.json` untouched. GREEN: `scripts/collab_pr_validate.py`. (REQ-004, REQ-005, REQ-009)
- [x] T-103: RED: `tests/test_collab_select.py` — selection rule, tie-breaks, 1-day window. GREEN: `scripts/collab_select.py`. (REQ-013)
- [x] T-104: RED: `tests/test_collab_ledger.py` — audit-chain completeness. GREEN: `scripts/collab_ledger.py`. (REQ-012)
- [x] T-105: Wire the validators into the sweep so deterministic failures are reported as PR comments. (REQ-015)
- [x] T-106: RED: `tests/test_collab_verdict.py` — verdict mapping, `FAIL ∧ check_redraft` → REASSIGN, `FAIL ∧ ¬check_redraft` → REVISE. GREEN: T-303. (REQ-006)
- [x] T-107: RED: `tests/test_collab_state.py` — state transitions, 4th-round close, label correction, conflict → REVISE. GREEN: T-305, T-306. (REQ-014, REQ-009)
- [x] T-108: RED: `tests/test_collab_completeness.py` — completeness block on incomplete book / segment mismatch. GREEN: T-401. (REQ-010)
- [x] T-109: RED: `tests/test_collab_seed.py` — seed success (live PASS, zero critical issues, audit list in merge comment) and failure (fallback/FAIL → no merge, no issues; ingest/analyze failure → no issue). GREEN: T-202. (REQ-003)
- [x] T-110: RED: `tests/test_collab_sweep.py` — replay recorded `gh … --json` fixtures with no network; non-blocking lock acquire (held → log "sweep already running", exit 0), no double-processing, deterministic failure → PR comment. GREEN: T-203, T-105. (REQ-015)

## Phase 2 — Main-agent intake, seed, and sweep (local)

- [x] T-201: Implement the pre-screen comment (manifest schema, format readable, language check reusing `detect_source_language` plus the Vietnamese-diacritic-density check, supported-pair check, licence assessment) for a `book/<slug>` PR. (REQ-002)
- [x] T-202: Implement ingestion from `books/<slug>/` into `runs/<slug>/` via `scripts/extract.py`, whole-book `analyze`, ch01 seed translation and merge by the bot account on a live `evaluate.py` PASS only (merge comment lists the bound glossary terms; fallback/FAIL → no merge, no issues), glossary freeze with SHA, and tracking-issue creation for ch02..chNN. RED first: T-109. (REQ-003)
- [x] T-203: Implement the hourly scheduled fetch / manual-run sweep: non-blocking lock acquire, `git fetch`, list open PRs/issues/reviews via `gh … --json`, advance each item until it blocks on external input (review, push, window), write the ledger. No GitHub Actions, no webhook. RED first: T-110. (REQ-015)
- [x] T-204: Resolve the [PROVISIONAL] chapter-split threshold by measuring the largest existing chapter's segment count, and record the decision. (REQ-004)

## Phase 3 — Translation, review, selection, merge

- [x] T-301: Implement the translator flow (soft `/claim`, translate one chapter, open `translate/<slug>-<chunk>-<agent>` PR with template body, glossary SHA, and `analyze_additions.json` proposals). (REQ-004, REQ-009)
- [x] T-302: Implement the reviewer flow (valid-review counting by login, line comments, 6-dimension score block). (REQ-005)
- [x] T-303: Implement the score of record (`scripts/evaluate.py`, WorkBuddy live), `resolve_pass_threshold`, critical/quorum overrides, and the PASS / REVISE / REASSIGN mapping via `check_redraft`. RED first: T-106. (REQ-006)
- [x] T-304: Implement competing-PR selection (1-day window from the sweep timestamp that first labels a PR `status:candidate`, deterministic ranking, closure comments, 2-day starved-chapter fallback PR) and the main-agent final review and merge. (REQ-013, REQ-006)
- [x] T-305: Implement the state machine (design.md §3.5: NeedsChecks → NeedsReview → Evaluating → Revise / Reassign / Candidate → Merged / NotSelected) with the label mapping and the 3-REVISE-round cap. RED first: T-107. (REQ-014)
- [x] T-306: Implement proposal binding at merge (`bind=True`) and the post-merge ledger write. (REQ-009, REQ-012)

## Phase 4 — Assembly, publication, docs

- [x] T-401: Implement the completeness check (all chapters Done, segment counts matching) and block publication on failure. RED first: T-108. (REQ-010)
- [x] T-402: Run the pilot end to end on a short public-domain English novella (3–5 chapters, en→vi): submission + human licence approval, analyze + ch01 seed, ≥2 accounts translating in parallel, ≥1 competing PR, a non-author review, a REVISE round, select-best and merge, compile and release. The pilot verifies only the genuinely end-to-end ACs (AC-002-1, AC-003-1, AC-005-1, AC-006-1, AC-009-1, AC-010-1); every other code-backed AC is verified by its automated test. (REQ-010)
- [x] T-403: Update the repo README and `docs/specs/INDEX.md` status for this package when the pilot passes.
- [x] T-404: Mutation-guard (≥ 80% kill) on `scripts/collab_*.py` plus an independent code review, before the spec is marked `In-Testing`.

## Gate 2: Traceability Matrix

| Requirement | AC | ADR or rationale | Design | Task | Planned verification | Status / reason |
|---|---|---|---|---|---|---|
| REQ-001 | AC-001-1 | ADR-008 | design §2, §3.1 | T-001, T-002, T-004 | `test_collab_intake.py` + pilot submission | PASS |
| REQ-001 | AC-001-2 | ADR-008 | design §3.1 | T-101, T-105 | `test_collab_intake.py` (path allowlist, size cap) | PASS |
| REQ-002 | AC-002-1 | ADR-016 | design §3.1 | T-201, T-004 | pilot submission + human approval | PASS |
| REQ-002 | AC-002-2 | ADR-016, ADR-018 | design §3.1 | T-201, T-101 | `test_collab_intake.py` (pair, licence) | PASS |
| REQ-003 | AC-003-1 | ADR-012, ADR-019 | design §3.2 | T-109, T-202 | `test_collab_seed.py` + pilot analyze + ch01 seed + issues | PASS |
| REQ-003 | AC-003-2 | ADR-012, ADR-019 | design §3.2 | T-109, T-202 | `test_collab_seed.py` (fallback/FAIL, ingest failure) | PASS |
| REQ-004 | AC-004-1 | ADR-013, ADR-014, ADR-015 | design §3.3 | T-301, T-102 | `test_collab_pr_validate.py` + pilot PRs | PASS |
| REQ-004 | AC-004-2 | ADR-013, ADR-014 | design §3.3 | T-102, T-105 | `test_collab_pr_validate.py` (allowlist, body fields, `translation.xml` contract) | PASS |
| REQ-005 | AC-005-1 | ADR-013 | design §3.3 | T-302 | pilot non-author review | PASS |
| REQ-005 | AC-005-2 | ADR-013 | design §3.3 | T-302, T-102 | `test_collab_pr_validate.py` (author ≠ reviewer) | PASS |
| REQ-006 | AC-006-1 | ADR-009 | design §3.4 | T-303 | pilot score of record | PASS |
| REQ-006 | AC-006-2 | ADR-009 | design §3.4 | T-106, T-303, T-304 | `test_collab_verdict.py` (REVISE / REASSIGN mapping) | PASS |
| REQ-007 | AC-007-1 | ADR-011 | design §3.1 | T-002, T-101 | `test_collab_intake.py` (pairs) | PASS |
| REQ-007 | AC-007-2 | ADR-011 | design §3.1 | T-101 | `test_collab_intake.py` (unsupported pair) | PASS |
| REQ-009 | AC-009-1 | ADR-012 | design §3.2 | T-301, T-306 | pilot proposal binding | PASS |
| REQ-009 | AC-009-2 | ADR-012 | design §3.2 | T-102, T-107, T-306 | `test_collab_pr_validate.py` (book_record untouched) + `test_collab_state.py` (conflict → REVISE) | PASS |
| REQ-010 | AC-010-1 | ADR-017 | design §3.8 | T-401, T-402 | pilot release assets | PASS |
| REQ-010 | AC-010-2 | ADR-017 | design §3.8 | T-108, T-401 | `test_collab_completeness.py` (incomplete-book block) | PASS |
| REQ-012 | AC-012-1 | ADR-010 | design §3.8 | T-104, T-306 | `test_collab_ledger.py` | PASS |
| REQ-012 | AC-012-2 | ADR-010 | design §3.8 | T-104, T-306 | `test_collab_ledger.py` (missing link flagged) | PASS |
| REQ-013 | AC-013-1 | ADR-015, ADR-020 | design §3.3 | T-103, T-304 | `test_collab_select.py` + pilot competing PRs | PASS |
| REQ-013 | AC-013-2 | ADR-015, ADR-020 | design §3.3 | T-103, T-304 | `test_collab_select.py` (window, tie-break) | PASS |
| REQ-014 | AC-014-1 | ADR-010, ADR-020 | design §3.5 | T-107, T-305 | `test_collab_state.py` (transitions) | PASS |
| REQ-014 | AC-014-2 | ADR-010, ADR-020 | design §3.5 | T-107, T-305 | `test_collab_state.py` (4th-round close, label correction) | PASS |
| REQ-015 | AC-015-1 | ADR-010 | design §3.6 | T-110, T-203 | `test_collab_sweep.py` (lock, no double-processing) | PASS |
| REQ-015 | AC-015-2 | ADR-010 | design §3.6 | T-105, T-110, T-203 | `test_collab_sweep.py` (PR-comment reporting) | PASS |

Note on retired v1.0.0 items: `REQ-COLLAB-008` (task-hub integration, retired by ADR-010) and `REQ-COLLAB-011` (process roles, recorded in ADR-007) are deprecated and carry no acceptance criteria.

- Backward scope check: PASS — Tasks T-001..T-404 map to REQ-001..REQ-007, REQ-009..REQ-010, REQ-012..REQ-015 and package-wide quality gates (T-403, T-404) with zero gold-plating.
- Reference and catalog consistency: PASS — Catalog row in `docs/specs/INDEX.md` reflects Status `Superseded` (superseded by `2026-11-18-collab-multi-agent-translation`).

## Historical Gate 2 Re-run Notes (2026-11-06, v1.2.0, author: Muse)

The v1.1.0 matrix above is kept for history. Opus round-2 review §9.5 overturned its test-first claim (R2-P1-4) and found an internal-consistency failure ("CI" vs the no-CI Non-Goal, R2-P2-2) and a missing control (R2-P1-3, deferred to thang).

| Check | Result | Evidence |
|---|---|---|
| Receipt location | PASS | Matrix lives in this file |
| 1-to-1 AC mapping | PASS | 26/26 active ACs mapped; retired `REQ-COLLAB-008` and `REQ-COLLAB-011` carry no ACs |
| Test-first / automation | PASS | RED tasks T-106..T-110 precede their GREEN tasks (T-303, T-305/T-306, T-401, T-202, T-203/T-105); pilot covers only the genuinely end-to-end ACs (AC-002-1, AC-003-1, AC-005-1, AC-006-1, AC-009-1, AC-010-1); T-404 adds the mutation-guard (≥ 80% kill) and independent code review |
| Internal consistency | PASS | "CI" / `NeedsCI` renamed to sweep checks / `NeedsChecks` across spec, design, and tasks; R2-P1-3 recorded as deferred to thang (spec §2 Non-Goal, decisions.md round-2 notes), not silently dropped |
| Zero stale paths / gold-plating | PASS | `translation.xml` replaces the undefined "translation file" everywhere; no hub paths remain |
