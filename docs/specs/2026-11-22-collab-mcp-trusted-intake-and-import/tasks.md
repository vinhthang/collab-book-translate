# Tasks: Collab Books MCP Trusted Submitter Auto-Approve & Batch Import

Sources: [spec.md](./spec.md), [design.md](./design.md)

## Task 1: TDD Suite

- [x] Complete the work and its applicable checks.
- Covers: REQ-061 / AC-061-1, AC-061-2, REQ-062 / AC-062-1, AC-062-2, REQ-063 / AC-063-1, AC-063-2
- Files: `tests/test_mcp_trusted_intake_and_import.py`, `tests/test_collab_config.py`, `tests/test_collab_worker_mcp.py`, `tests/test_mcp_workflow_v2.py`, `tests/test_mcp_bug_triage_and_hardening.py`
- Dependencies: None
- Blocked by: None
- Work: Write failing RED tests per increment in `tests/test_mcp_trusted_intake_and_import.py` covering `REQ-061`, `REQ-062`, and `REQ-063`, then update `4.1.0` / `28`-tool assertions to `4.2.0` / `29` tools in existing tests.
- Completion evidence: Executed Red -> Green for `REQ-061`, `REQ-062`, and `REQ-063`; full suite passed `346 passed, 0 failed, 0 skipped` (`29.91s`), plus `3 passed in 2.57s` after exact boundary hardening. See [Run 1](./verification.md#run-1--2026-11-22).

## Task 2: Service Logic

- [x] Complete the work and its applicable checks.
- Covers: REQ-061 / AC-061-1, AC-061-2, REQ-062 / AC-062-1, AC-062-2
- Files: `scripts/mcp_server/service.py`, `scripts/mcp_server/db.py`
- Dependencies: Task 1
- Blocked by: None
- Work: Implement `_is_trusted_submitter`, `_has_curated_book_record`, trusted auto-approve in `submit_book_proposal`, `unlock_all_chapters` in `submit_book_parse`/`approve_book_intake`, `glossary_sha` preservation & honest ledger reviewers in `_finalize_accepted_chapter`, two-phase `import_chapter_translations`, `list_tasks` `200`-item `book_slug` review queue, and pending-draft continuity in `_build_chapter_neighbors`.
- Completion evidence: Implemented cleanly; `mutation-guard` on `scripts/mcp_server/service.py` killed `15/15` (`100.0%`). See [Run 1](./verification.md#run-1--2026-11-22).

## Task 3: MCP UI & CLI

- [x] Complete the work and its applicable checks.
- Covers: REQ-063 / AC-063-1, AC-063-2
- Files: `scripts/mcp_server/app.py`, `scripts/collab_worker.py`, `plugin.json`, `skills/collab-translate-book/SKILL.md`, `agents/collab_admin.md`, `docs/specs/2026-11-22-collab-mcp-trusted-intake-and-import/spec.md`, `docs/specs/2026-11-22-collab-mcp-trusted-intake-and-import/verification.md`, `docs/specs/INDEX.md`
- Dependencies: Task 2
- Blocked by: None
- Work: Register MCP tool #29 `import_chapter_translations`, bump versions to `"4.2.0"`, add REST `/api/v1/books/{slug}/import-translations`, add `/admin` `Approve & Open All Chapters` button (verified with `node --check`), add `collab_worker.py import-translations`, run full `pytest`, `mutation-guard` (`>= 80%`), single-agent `code-review`, and deploy + verify `87` Ray Dalio chapters on `collab.vinhthang.dev`.
- Completion evidence: MCP `v4.2.0` (`29` tools), REST, `/admin` `Approve & Open All Chapters` (`node --check` exit `0`), `mutation-guard` on `app.py` (`12/15`, `80.0%`), `test_option_a_hygiene.py`, and single-agent `code-review` (`0 P0`, `0 P1`) all passed. Live rollout & 87-chapter migration tracked under Real-world observations. See [Run 1](./verification.md#run-1--2026-11-22).

## Gate 2: traceability and readiness

| Requirement | AC | ADR or rationale | Design | Task | Planned verification | Status / reason |
| --- | --- | --- | --- | --- | --- | --- |
| REQ-061 | AC-061-1 | [ADR-037](./decisions.md#adr-037-deterministic-pre-llm-validation--peer-review-gate-for-trusted-chapter-imports) | [Interfaces](./design.md#interfaces-and-flow) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-service-logic) | [AC-061-1](./verification.md#ac-061-1-req-061) | PASS |
| REQ-061 | AC-061-2 | [ADR-037](./decisions.md#adr-037-deterministic-pre-llm-validation--peer-review-gate-for-trusted-chapter-imports) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-service-logic) | [AC-061-2](./verification.md#ac-061-2-req-061) | PASS |
| REQ-062 | AC-062-1 | [ADR-037](./decisions.md#adr-037-deterministic-pre-llm-validation--peer-review-gate-for-trusted-chapter-imports) | [Interfaces](./design.md#interfaces-and-flow) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-service-logic) | [AC-062-1](./verification.md#ac-062-1-req-062) | PASS |
| REQ-062 | AC-062-2 | [ADR-037](./decisions.md#adr-037-deterministic-pre-llm-validation--peer-review-gate-for-trusted-chapter-imports) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-service-logic) | [AC-062-2](./verification.md#ac-062-2-req-062) | PASS |
| REQ-063 | AC-063-1 | [Rationale](./decisions.md#architectural-rationale) | [Components](./design.md#components) | [Task 1](#task-1-tdd-suite), [Task 3](#task-3-mcp-ui--cli) | [AC-063-1](./verification.md#ac-063-1-req-063) | PASS |
| REQ-063 | AC-063-2 | [Rationale](./decisions.md#architectural-rationale) | [Components](./design.md#components) | [Task 1](#task-1-tdd-suite), [Task 3](#task-3-mcp-ui--cli) | [AC-063-2](./verification.md#ac-063-2-req-063) | PASS |

- Backward scope check: PASS — `ANONYMOUS` and `contributor` flows, existing `approve_book_intake` API defaults, and legacy auto/manual/peer review workflows remain backward-compatible.
- Reference and catalog consistency: PASS — All `REQ-061..REQ-063`, `AC-*`, `ADR-037`, `INDEX.md`, and `verification.md` anchors match cleanly.
