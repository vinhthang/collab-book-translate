# Tasks: Collab Books MCP Workflow v2 (`collab-mcp:v0.2.0`)

Sources: [spec.md](./spec.md), [design.md](./design.md)

## Task 1: TDD suite

- [x] Complete the work and its applicable checks.
- Covers: REQ-052 / AC-052-1, AC-052-2, REQ-053 / AC-053-1, AC-053-2, REQ-054 / AC-054-1, AC-054-2, REQ-055 / AC-055-1, AC-055-2, REQ-056 / AC-056-1, AC-056-2
- Files: `tests/test_mcp_workflow_v2.py`
- Dependencies: None
- Blocked by: None
- Work: Write falsifiable tests covering `agent_admin` promotion/demotion & `403` authority boundaries (`AC-052-*`), `queue_for_parse=True` -> `approved_pending_parse` -> `submit_book_parse` -> `seeding` -> `active` & validation/immutability boundaries (`AC-053-*`), `get_book_source` / neighbor continuity / `query_submissions` / `rounds_history` + `workstream` querying & restricted-book/secret isolation (`AC-054-*`), `approval_mode="peer"` `pending_review` + `CHANGES_REQUESTED` -> `remediation_required` + `APPROVE` >= 4.0 merge & self-review/score boundaries (`AC-055-*`), and MCP `v4.0.0` tools/prompts, `/admin` XSS escaping, `_ADMIN_JS` syntax, and `collab_worker.py --mcp-url` commands (`AC-056-*`).

## Task 2: Backend core

- [x] Complete the work and its applicable checks.
- Covers: REQ-052 / AC-052-1, AC-052-2, REQ-053 / AC-053-1, AC-053-2, REQ-054 / AC-054-1, AC-054-2, REQ-055 / AC-055-1, AC-055-2
- Files: `scripts/mcp_server/auth.py`, `scripts/mcp_server/db.py`, `scripts/mcp_server/review_client.py`, `scripts/mcp_server/git_sync.py`, `scripts/mcp_server/service.py`
- Dependencies: Task 1
- Blocked by: None
- Work: Add `agent_role` & `assert_agent_admin_or_admin` in `auth.py`; add schema migrations (`role`, `brief_json`, `rounds_history_json`, `verdict`, `reviewer_role`) and store helpers in `db.py`; add `get_workstream` in `review_client.py` and `brief.json` / `read_ledger` in `git_sync.py`; implement `set_agent_role`, `approved_pending_parse`, `submit_book_parse`, `update_book_record`, `get_book_source`, enriched `get_book` / `get_chapter` / `get_submission_status`, `query_submissions`, and `approval_mode="peer"` review state transitions in `service.py`.

## Task 3: MCP UI & CLI

- [x] Complete the work and its applicable checks.
- Covers: REQ-056 / AC-056-1, AC-056-2
- Files: `scripts/mcp_server/app.py`, `scripts/collab_worker.py`, `agents/collab_admin.md`, `agents/collab_translator.md`, `agents/collab_reviewer.md`, `skills/collab-translate-book/SKILL.md`, `plugin.json`
- Dependencies: Task 2
- Blocked by: None
- Work: Register new MCP tools, prompt `parse_approved_book`, offloaded REST routes, and `/admin` HTML/JS controls in `app.py` (`version="4.0.0"`); add `get-book-source`, `submit-book-parse`, `query-submissions`, and `submit-review --verdict` to `collab_worker.py`; author `agents/collab_admin.md` and update `collab_translator.md`, `collab_reviewer.md`, `SKILL.md`, and `plugin.json`; run full pytest, `node --check`, `mutation-guard`, and `code-review`.

## Gate 2: traceability and readiness

| Requirement | AC | ADR or rationale | Design | Task | Planned verification | Status / reason |
| --- | --- | --- | --- | --- | --- | --- |
| REQ-052 | AC-052-1 | [Rationale](./decisions.md#architectural-rationale) | [Interfaces](./design.md#interfaces-and-flow) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-backend-core) | [AC-052-1](./verification.md#ac-052-1-req-052) | PASS — `agent_admin` promotion, handle/key resolution, and revocation planned |
| REQ-052 | AC-052-2 | [Rationale](./decisions.md#architectural-rationale) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-backend-core) | [AC-052-2](./verification.md#ac-052-2-req-052) | PASS — `400`/`404` role validation and strict `403` `ADMIN` vs `agent_admin` / `restricted` boundaries planned |
| REQ-053 | AC-053-1 | [ADR-035](./decisions.md#adr-035-external-agent_admin-pull-queue-book-parsing-and-actionable-pending_review-peer-gate) | [Interfaces](./design.md#interfaces-and-flow) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-backend-core) | [AC-053-1](./verification.md#ac-053-1-req-053) | PASS — `approved_pending_parse` -> `submit_book_parse` -> `seeding` -> `active` flow planned |
| REQ-053 | AC-053-2 | [ADR-035](./decisions.md#adr-035-external-agent_admin-pull-queue-book-parsing-and-actionable-pending_review-peer-gate) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-backend-core) | [AC-053-2](./verification.md#ac-053-2-req-053) | PASS — `403`/`409`/`400`/`413`/`422` parse & post-lock immutability boundaries planned |
| REQ-054 | AC-054-1 | [Rationale](./decisions.md#architectural-rationale) | [Interfaces](./design.md#interfaces-and-flow) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-backend-core) | [AC-054-1](./verification.md#ac-054-1-req-054) | PASS — Full source, `brief`, `collab_ledger`, neighbor continuity, `query_submissions`, and `rounds_history`+`workstream` queries planned |
| REQ-054 | AC-054-2 | [Rationale](./decisions.md#architectural-rationale) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-backend-core) | [AC-054-2](./verification.md#ac-054-2-req-054) | PASS — Restricted-book `403`/filtering and secret hash/IP stripping planned |
| REQ-055 | AC-055-1 | [ADR-035](./decisions.md#adr-035-external-agent_admin-pull-queue-book-parsing-and-actionable-pending_review-peer-gate) | [Interfaces](./design.md#interfaces-and-flow) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-backend-core) | [AC-055-1](./verification.md#ac-055-1-req-055) | PASS — Default `approval_mode="peer"`, `pending_review`, `CHANGES_REQUESTED` -> `remediation_required`, and `APPROVE` >= 4.0 merge planned |
| REQ-055 | AC-055-2 | [ADR-035](./decisions.md#adr-035-external-agent_admin-pull-queue-book-parsing-and-actionable-pending_review-peer-gate) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-backend-core) | [AC-055-2](./verification.md#ac-055-2-req-055) | PASS — Anonymous/self-review `403`, `< 4.0` / critical `422`, and `manual` bypass `409` boundaries planned |
| REQ-056 | AC-056-1 | [Rationale](./decisions.md#architectural-rationale) | [Components](./design.md#components) | [Task 1](#task-1-tdd-suite), [Task 3](#task-3-mcp-ui--cli) | [AC-056-1](./verification.md#ac-056-1-req-056) | PASS — MCP `v4.0.0` 25 tools/3 prompts, `/admin` UI & `node --check`, `collab_admin.md`, and `collab_worker.py` parity planned |
| REQ-056 | AC-056-2 | [Rationale](./decisions.md#architectural-rationale) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 1](#task-1-tdd-suite), [Task 3](#task-3-mcp-ui--cli) | [AC-056-2](./verification.md#ac-056-2-req-056) | PASS — `/admin` XSS HTML escaping and `test_option_a_hygiene.py` checks planned |

- Backward scope check: PASS — Every modified file and task serves `REQ-052..REQ-056`.
- Reference and catalog consistency: PASS — `INDEX.md`, `spec.md`, `decisions.md`, `design.md`, `tasks.md`, and `verification.md` agree on `REQ-052..REQ-056`, `ADR-035`, and status `In-Testing`.
