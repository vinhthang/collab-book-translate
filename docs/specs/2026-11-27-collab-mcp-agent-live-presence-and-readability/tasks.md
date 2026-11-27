# Tasks: Agent Live MCP Presence, Heartbeat, & Readable Console

Sources: [spec.md](./spec.md), [design.md](./design.md)

## Stage 1: TDD Suite

- [x] Write failing RED tests first for `REQ-079..REQ-081` and align `"4.7.0"` expectations.
- Covers: REQ-079 / AC-079-1, AC-079-2, REQ-080 / AC-080-1, AC-080-2, REQ-081 / AC-081-1, AC-081-2
- Files: `tests/test_mcp_agent_live_presence.py`, `tests/test_collab_config.py`, `tests/test_collab_worker_mcp.py`, `tests/test_mcp_admin_console_rework.py`, `tests/test_mcp_bug_triage_and_hardening.py`, `tests/test_mcp_public_corpus_gate.py`, `tests/test_mcp_reader_threads_and_invite_lockdown.py`, `tests/test_mcp_reviewer_queue_and_matter.py`, `tests/test_mcp_tech_admin_bug_workflow.py`, `tests/test_mcp_trusted_intake_and_import.py`, `tests/test_mcp_workflow_v2.py`
- Dependencies: None
- Blocked by: None
- Work: Write failing RED tests covering `/sse` live tracking, `/mcp` `initialize`/`ping`/`tools/list`, `agent_heartbeat`, time-window presence buckets, readable handles, mint `role`, `/profile` rename, `/admin` live readable table (`node --check`, XSS), and `"4.7.0"` discovery.

## Stage 2: Backend Presence, Store, Auth, & Service

- [x] Implement `presence.py`, `auth.py`, `db.py` migrations/profile/handle helpers, and `service.py` presence + heartbeat + profile methods.
- Covers: REQ-079 / AC-079-1, AC-079-2, REQ-080 / AC-080-1, AC-080-2
- Files: `scripts/mcp_server/presence.py`, `scripts/mcp_server/auth.py`, `scripts/mcp_server/db.py`, `scripts/mcp_server/service.py`
- Dependencies: Stage 1
- Blocked by: None
- Work: Build `AgentPresenceTracker`, time-bucket presence classifier, SQLite/PG migrations on `agents`, readable slug generation, `update_agent_profile`, and `agent_heartbeat`.

## Stage 3: Middleware, UI, Worker CLI, Docs, & Rollout

- [x] Instrument `RequestContextMiddleware` + routes in `app.py`, rework `/admin#agents` UI in `admin_ui.py`, add CLI `heartbeat`, bump `v4.7.0`/`v0.9.0`, gate, and deploy live.
- Covers: REQ-079 / AC-079-1, AC-079-2, REQ-080 / AC-080-1, AC-080-2, REQ-081 / AC-081-1, AC-081-2
- Files: `scripts/mcp_server/app.py`, `scripts/mcp_server/admin_ui.py`, `scripts/collab_worker.py`, `plugin.json`, `.github/workflows/ci-cd.yml`, `skills/collab-translate-book/SKILL.md`, `agents/collab_admin.md`, `agents/collab_tech_admin.md`, `docs/specs/INDEX.md`
- Dependencies: Stage 2
- Blocked by: None
- Work: Track live `/sse` & `/mcp` requests in middleware, expose heartbeat/profile endpoints, rework Agents UI with readable names, Live Status pills, filter chips, Edit drawer, and 10s polling, pass full `pytest` + `mutation-guard` + `code-review` + `validate_specs.py --all`, deploy `v0.9.0`, verify live hub, persist OpenViking milestone, and update `walkthrough.md`.

## Gate 2: traceability and readiness

| Requirement | AC | ADR or rationale | Design | Stage | Planned verification | Status / reason |
| --- | --- | --- | --- | --- | --- | --- |
| REQ-079 | AC-079-1 | [ADR-043](./decisions.md#adr-043-hybrid-in-memory-sse--db-activity-presence--explicit-heartbeat-over-stateful-http-mcp-sessions) | [Interfaces](./design.md#interfaces-and-flow) | [Stage 1](#stage-1-tdd-suite), [Stage 2](#stage-2-backend-presence-store-auth--service), [Stage 3](#stage-3-middleware-ui-worker-cli-docs--rollout) | [AC-079-1](./verification.md#ac-079-1-req-079) | PASS |
| REQ-079 | AC-079-2 | [ADR-043](./decisions.md#adr-043-hybrid-in-memory-sse--db-activity-presence--explicit-heartbeat-over-stateful-http-mcp-sessions) | [Boundaries](./design.md#boundaries-and-recovery) | [Stage 1](#stage-1-tdd-suite), [Stage 2](#stage-2-backend-presence-store-auth--service), [Stage 3](#stage-3-middleware-ui-worker-cli-docs--rollout) | [AC-079-2](./verification.md#ac-079-2-req-079) | PASS |
| REQ-080 | AC-080-1 | [ADR-043](./decisions.md#adr-043-hybrid-in-memory-sse--db-activity-presence--explicit-heartbeat-over-stateful-http-mcp-sessions) | [Interfaces](./design.md#interfaces-and-flow) | [Stage 1](#stage-1-tdd-suite), [Stage 2](#stage-2-backend-presence-store-auth--service), [Stage 3](#stage-3-middleware-ui-worker-cli-docs--rollout) | [AC-080-1](./verification.md#ac-080-1-req-080) | PASS |
| REQ-080 | AC-080-2 | [ADR-043](./decisions.md#adr-043-hybrid-in-memory-sse--db-activity-presence--explicit-heartbeat-over-stateful-http-mcp-sessions) | [Boundaries](./design.md#boundaries-and-recovery) | [Stage 1](#stage-1-tdd-suite), [Stage 2](#stage-2-backend-presence-store-auth--service), [Stage 3](#stage-3-middleware-ui-worker-cli-docs--rollout) | [AC-080-2](./verification.md#ac-080-2-req-080) | PASS |
| REQ-081 | AC-081-1 | [ADR-043](./decisions.md#adr-043-hybrid-in-memory-sse--db-activity-presence--explicit-heartbeat-over-stateful-http-mcp-sessions) | [Interfaces](./design.md#interfaces-and-flow) | [Stage 1](#stage-1-tdd-suite), [Stage 3](#stage-3-middleware-ui-worker-cli-docs--rollout) | [AC-081-1](./verification.md#ac-081-1-req-081) | PASS |
| REQ-081 | AC-081-2 | [ADR-043](./decisions.md#adr-043-hybrid-in-memory-sse--db-activity-presence--explicit-heartbeat-over-stateful-http-mcp-sessions) | [Boundaries](./design.md#boundaries-and-recovery) | [Stage 1](#stage-1-tdd-suite), [Stage 3](#stage-3-middleware-ui-worker-cli-docs--rollout) | [AC-081-2](./verification.md#ac-081-2-req-081) | PASS |

- Backward scope check: PASS — Every stage and component traces directly to `REQ-079..REQ-081`.
- Reference and catalog consistency: PASS — `INDEX.md`, `decisions.md`, `design.md`, and `verification.md` agree on IDs and anchors.
