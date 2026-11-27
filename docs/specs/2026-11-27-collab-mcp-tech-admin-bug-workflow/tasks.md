# Tasks: Dedicated `tech_admin` Role & Hybrid Bug Resolution Workflow

Sources: [spec.md](./spec.md), [design.md](./design.md)

## Stage 1: TDD Suite

- [x] Write failing RED tests first for `REQ-076..REQ-078` and align `"4.6.0"` version expectations.
- Covers: REQ-076 / AC-076-1, AC-076-2, REQ-077 / AC-077-1, AC-077-2, REQ-078 / AC-078-1, AC-078-2
- Files: `tests/test_mcp_tech_admin_bug_workflow.py`, `tests/test_collab_config.py`, `tests/test_collab_worker_mcp.py`, `tests/test_mcp_admin_console_rework.py`, `tests/test_mcp_bug_triage_and_hardening.py`, `tests/test_mcp_public_corpus_gate.py`, `tests/test_mcp_reader_threads_and_invite_lockdown.py`, `tests/test_mcp_reviewer_queue_and_matter.py`, `tests/test_mcp_trusted_intake_and_import.py`, `tests/test_mcp_workflow_v2.py`
- Dependencies: None
- Blocked by: None
- Work: Write failing RED tests covering `tech_admin` isolation + `claim_bug_report` lifecycle, hybrid webhook + idempotent GitHub Issue dispatch, Admin UI 3-role `<select>` + bug PR/issue controls (`node --check`), CLI parity, `agents/collab_tech_admin.md`, and `"4.6.0"` metadata.

## Stage 2: Backend Config, Persistence, Auth, & Service

- [x] Implement `tech_admin` role, `bug_reports` claim/PR/issue columns, `claim_bug_report`, `list_tasks(role="tech_admin")`, and hybrid `Accept` dispatcher.
- Covers: REQ-076 / AC-076-1, AC-076-2, REQ-077 / AC-077-1, AC-077-2
- Files: `scripts/mcp_server/config.py`, `scripts/mcp_server/db.py`, `scripts/mcp_server/auth.py`, `scripts/mcp_server/service.py`
- Dependencies: Stage 1
- Blocked by: None
- Work: Add env config fields, SQLite/PostgreSQL migrations on `bug_reports`, `AgentRole` `"tech_admin"` + `assert_bug_handler_or_admin`, `claim_bug_report` with claim TTL lock, and non-blocking `_dispatch_accepted_bug`.

## Stage 3: MCP/REST Surface, Admin UI, CLI, Agent Persona, & Rollout

- [x] Wire MCP/REST claim routes, Admin UI 3-role + bug PR controls, `collab_worker.py`, `agents/collab_tech_admin.md`, `"4.6.0"`/`v0.8.0`, verify gates, and deploy live.
- Covers: REQ-078 / AC-078-1, AC-078-2
- Files: `scripts/mcp_server/app.py`, `scripts/mcp_server/admin_ui.py`, `scripts/collab_worker.py`, `agents/collab_tech_admin.md`, `agents/collab_admin.md`, `skills/collab-translate-book/SKILL.md`, `plugin.json`, `.github/workflows/ci-cd.yml`, `docs/specs/INDEX.md`
- Dependencies: Stage 2
- Blocked by: None
- Work: Expose MCP/REST/CLI claim & triage extensions, update Admin UI role `<select>` and bug cards (`node --check`), author `agents/collab_tech_admin.md`, run full `pytest`, `mutation-guard` (`>= 80%`), `code-review`, `validate_specs.py --all`, deploy `collab-mcp:v0.8.0`, verify live hub, persist OpenViking milestone, and update `walkthrough.md`.

## Gate 2: traceability and readiness

| Requirement | AC | ADR or rationale | Design | Stage | Planned verification | Status / reason |
| --- | --- | --- | --- | --- | --- | --- |
| REQ-076 | AC-076-1 | [ADR-042](./decisions.md#adr-042-dedicated-tech_admin-role--hybrid-mcp-queue--non-blocking-webhookgithub-issue-dispatch) | [Interfaces](./design.md#interfaces-and-flow) | [Stage 1](#stage-1-tdd-suite), [Stage 2](#stage-2-backend-config-persistence-auth--service) | [AC-076-1](./verification.md#ac-076-1-req-076) | PASS |
| REQ-076 | AC-076-2 | [ADR-042](./decisions.md#adr-042-dedicated-tech_admin-role--hybrid-mcp-queue--non-blocking-webhookgithub-issue-dispatch) | [Boundaries](./design.md#boundaries-and-recovery) | [Stage 1](#stage-1-tdd-suite), [Stage 2](#stage-2-backend-config-persistence-auth--service) | [AC-076-2](./verification.md#ac-076-2-req-076) | PASS |
| REQ-077 | AC-077-1 | [ADR-042](./decisions.md#adr-042-dedicated-tech_admin-role--hybrid-mcp-queue--non-blocking-webhookgithub-issue-dispatch) | [Interfaces](./design.md#interfaces-and-flow) | [Stage 1](#stage-1-tdd-suite), [Stage 2](#stage-2-backend-config-persistence-auth--service) | [AC-077-1](./verification.md#ac-077-1-req-077) | PASS |
| REQ-077 | AC-077-2 | [ADR-042](./decisions.md#adr-042-dedicated-tech_admin-role--hybrid-mcp-queue--non-blocking-webhookgithub-issue-dispatch) | [Boundaries](./design.md#boundaries-and-recovery) | [Stage 1](#stage-1-tdd-suite), [Stage 2](#stage-2-backend-config-persistence-auth--service) | [AC-077-2](./verification.md#ac-077-2-req-077) | PASS |
| REQ-078 | AC-078-1 | [ADR-042](./decisions.md#adr-042-dedicated-tech_admin-role--hybrid-mcp-queue--non-blocking-webhookgithub-issue-dispatch) | [Interfaces](./design.md#interfaces-and-flow) | [Stage 1](#stage-1-tdd-suite), [Stage 3](#stage-3-mcprest-surface-admin-ui-cli-agent-persona--rollout) | [AC-078-1](./verification.md#ac-078-1-req-078) | PASS |
| REQ-078 | AC-078-2 | [ADR-042](./decisions.md#adr-042-dedicated-tech_admin-role--hybrid-mcp-queue--non-blocking-webhookgithub-issue-dispatch) | [Boundaries](./design.md#boundaries-and-recovery) | [Stage 1](#stage-1-tdd-suite), [Stage 3](#stage-3-mcprest-surface-admin-ui-cli-agent-persona--rollout) | [AC-078-2](./verification.md#ac-078-2-req-078) | PASS |

- Backward scope check: PASS — Every stage and component traces directly to `REQ-076..REQ-078`.
- Reference and catalog consistency: PASS — `INDEX.md`, `decisions.md`, `design.md`, and `verification.md` agree on IDs and anchors.
