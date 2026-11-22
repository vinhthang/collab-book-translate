# Tasks: Collab Books MCP Reviewer Queue, Front/Back Matter Flow, & Context Bundle

Sources: [spec.md](./spec.md), [design.md](./design.md)

## Task 1: TDD Suite

- [x] Complete the work and its applicable checks.
- Covers: REQ-064 / AC-064-1, AC-064-2, REQ-065 / AC-065-1, AC-065-2, REQ-066 / AC-066-1, AC-066-2, REQ-067 / AC-067-1, AC-067-2
- Files: `tests/test_mcp_reviewer_queue_and_matter.py`, `tests/test_collab_config.py`, `tests/test_collab_worker_mcp.py`, `tests/test_mcp_workflow_v2.py`, `tests/test_mcp_bug_triage_and_hardening.py`, `tests/test_mcp_trusted_intake_and_import.py`
- Dependencies: None
- Blocked by: None
- Work: Write failing RED tests first for each increment (`REQ-064..REQ-067`) in `tests/test_mcp_reviewer_queue_and_matter.py`, then advance `"4.2.0"` assertions to `"4.3.0"` in existing version tests.
- Completion evidence: Executed Red -> Green for `REQ-064..REQ-067`; full suite passed `350 passed, 0 failed, 0 skipped` (`26.08s`) and `test_option_a_hygiene.py` passed `2 passed` (`0.22s`). See [Run 1](./verification.md#run-1--2026-11-22).

## Task 2: Service Logic

- [x] Complete the work and its applicable checks.
- Covers: REQ-064 / AC-064-1, AC-064-2, REQ-065 / AC-065-1, AC-065-2, REQ-066 / AC-066-1, AC-066-2
- Files: `scripts/mcp_server/db.py`, `scripts/mcp_server/service.py`, `scripts/evaluate.py`
- Dependencies: Task 1
- Blocked by: None
- Work: Persist/migrate `chapters.section_type`, add deterministic classifier & startup backfill, auto-accept `"boilerplate"` during trusted imports (`approval_mode != "manual"`), paginate & compact `list_tasks` (`limit=200`, `offset=0`, body-first natural sort, full `submission_id`), expose unified `context`/`book_record`/`brief` & deduplicate `rounds_history` XML in `get_submission_status`, and allow post-lock `brief` updates + bidirectional `additions` sync in `update_book_record`.
- Completion evidence: Implemented cleanly; `mutation-guard` on `scripts/mcp_server/service.py` killed `12/15` (`80.0%`). See [Run 1](./verification.md#run-1--2026-11-22).

## Task 3: MCP UI & CLI

- [x] Complete the work and its applicable checks.
- Covers: REQ-067 / AC-067-1, AC-067-2
- Files: `scripts/mcp_server/app.py`, `scripts/collab_worker.py`, `plugin.json`, `.github/workflows/ci-cd.yml`, `skills/collab-translate-book/SKILL.md`, `agents/collab_reviewer.md`, `agents/collab_admin.md`, `docs/specs/2026-11-22-collab-mcp-reviewer-queue-and-matter-flow/spec.md`, `docs/specs/2026-11-22-collab-mcp-reviewer-queue-and-matter-flow/verification.md`, `docs/specs/INDEX.md`
- Dependencies: Task 2
- Blocked by: None
- Work: Bump version to `"4.3.0"` (`v0.5.0`), expose pagination/matter/boilerplate/context fields in MCP & REST, show full 36-char UUIDs & `section_type` badges in `/admin` (`node --check`), add `get-submission` & pagination flags to `collab_worker.py`, run full `pytest`, `mutation-guard`, `code-review`, and deploy + sync Ray Dalio corpus on `collab.vinhthang.dev`.
- Completion evidence: MCP `v4.3.0`, REST, `/admin` full UUIDs (`node --check` exit `0`), `mutation-guard` on `app.py` (`14/15`, `93.3%`), and single-agent `code-review` (`Approved`, `0` P0/P1) all passed. See [Run 1](./verification.md#run-1--2026-11-22).

## Gate 2: traceability and readiness

| Requirement | AC | ADR or rationale | Design | Task | Planned verification | Status / reason |
| --- | --- | --- | --- | --- | --- | --- |
| REQ-064 | AC-064-1 | [ADR-038](./decisions.md#adr-038-persist-chapter-section_type-and-auto-accept-boilerplate-only-at-import-time) | [Interfaces](./design.md#interfaces-and-flow) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-service-logic) | [AC-064-1](./verification.md#ac-064-1-req-064) | PASS |
| REQ-064 | AC-064-2 | [ADR-038](./decisions.md#adr-038-persist-chapter-section_type-and-auto-accept-boilerplate-only-at-import-time) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-service-logic) | [AC-064-2](./verification.md#ac-064-2-req-064) | PASS |
| REQ-065 | AC-065-1 | [Rationale](./decisions.md#architectural-rationale) | [Interfaces](./design.md#interfaces-and-flow) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-service-logic) | [AC-065-1](./verification.md#ac-065-1-req-065) | PASS |
| REQ-065 | AC-065-2 | [Rationale](./decisions.md#architectural-rationale) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-service-logic) | [AC-065-2](./verification.md#ac-065-2-req-065) | PASS |
| REQ-066 | AC-066-1 | [Rationale](./decisions.md#architectural-rationale) | [Interfaces](./design.md#interfaces-and-flow) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-service-logic) | [AC-066-1](./verification.md#ac-066-1-req-066) | PASS |
| REQ-066 | AC-066-2 | [Rationale](./decisions.md#architectural-rationale) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-service-logic) | [AC-066-2](./verification.md#ac-066-2-req-066) | PASS |
| REQ-067 | AC-067-1 | [Rationale](./decisions.md#architectural-rationale) | [Components](./design.md#components) | [Task 1](#task-1-tdd-suite), [Task 3](#task-3-mcp-ui--cli) | [AC-067-1](./verification.md#ac-067-1-req-067) | PASS |
| REQ-067 | AC-067-2 | [Rationale](./decisions.md#architectural-rationale) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 1](#task-1-tdd-suite), [Task 3](#task-3-mcp-ui--cli) | [AC-067-2](./verification.md#ac-067-2-req-067) | PASS |

- Backward scope check: PASS — Every task and component maps directly to `REQ-064..REQ-067`.
- Reference and catalog consistency: PASS — `INDEX.md`, `decisions.md`, `design.md`, and `verification.md` agree on IDs and anchors.
