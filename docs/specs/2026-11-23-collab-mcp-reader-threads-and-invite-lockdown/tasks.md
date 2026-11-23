# Tasks: Collab Books MCP Invite-Only Lockdown, Human Web Reader, & Clean Agent Threads

Sources: [spec.md](./spec.md), [design.md](./design.md)

## Stage 1: TDD Suite

- [x] Write failing RED tests first for `REQ-070..REQ-072`, align opt-in anonymous tests, and bump `"4.4.0"` version expectations.
- Covers: REQ-070 / AC-070-1, AC-070-2, REQ-071 / AC-071-1, AC-071-2, REQ-072 / AC-072-1, AC-072-2
- Files: `tests/test_mcp_reader_threads_and_invite_lockdown.py`, `tests/test_mcp_service_and_app.py`, `tests/test_mcp_bug_triage_and_hardening.py`, `tests/test_mcp_trusted_intake_and_import.py`, `tests/test_mcp_reviewer_queue_and_matter.py`, `tests/test_mcp_public_corpus_gate.py`, `tests/test_mcp_onboarding_and_access.py`, `tests/test_mcp_workflow_v2.py`, `tests/test_collab_config.py`, `tests/test_collab_worker_mcp.py`
- Dependencies: None
- Blocked by: None
- Work: Write failing RED tests in `tests/test_mcp_reader_threads_and_invite_lockdown.py` first covering invite-only lockdown, `ANONYMOUS` block on `owner-permission`, `2,000`-char review cap, `purge_test_artifacts`, `/read/*` + `/admin/read/*` bilingual Reader + XSS safety, `/threads` + `/admin/threads` collapsible UI, and `"4.4.0"`; update opt-in `allow_anonymous_writes=True` or `ADMIN` agent-minting setup + `"4.4.0"` assertions across existing suites.

## Stage 2: Store & Service

- [x] Implement `allow_anonymous_writes`, `purge_test_artifacts`, private manuscript block, `2,000`-char review cap, `get_reader_book_view`, and `list_activity_threads`.
- Covers: REQ-070 / AC-070-1, AC-070-2, REQ-071 / AC-071-1, AC-071-2, REQ-072 / AC-072-1
- Files: `scripts/mcp_server/config.py`, `scripts/mcp_server/db.py`, `scripts/mcp_server/service.py`
- Dependencies: Stage 1
- Blocked by: None
- Work: Add `allow_anonymous_writes=False` config default, portable `purge_test_artifacts`, `_assert_write_identity`, `register_agent` `ADMIN` check, `ANONYMOUS` block on non-public-license books, `MAX_REVIEW_BODY_CHARS=2000` check, `get_reader_book_view`, and `list_activity_threads`.

## Stage 3: Reader & Threads UI

- [x] Add Web Reader, Agent Threads routes, Invite-Only `/` card, `/admin` purge controls, `"4.4.0"`/`v0.6.0` bump, and deploy live.
- Covers: REQ-071 / AC-071-1, AC-071-2, REQ-072 / AC-072-1, AC-072-2
- Files: `scripts/mcp_server/app.py`, `plugin.json`, `.github/workflows/ci-cd.yml`, `skills/collab-translate-book/SKILL.md`, `agents/collab_admin.md`, `agents/collab_reviewer.md`, `docs/specs/2026-11-23-collab-mcp-reader-threads-and-invite-lockdown/spec.md`, `docs/specs/2026-11-23-collab-mcp-reader-threads-and-invite-lockdown/verification.md`, `docs/specs/INDEX.md`
- Dependencies: Stage 2
- Blocked by: None
- Work: Implement HTML Reader & Threads UIs in `app.py`, add `/admin/api/purge-test-artifacts`, refresh `/` and `/admin` (`node --check`), advance docs/workflows to `"4.4.0"`/`v0.6.0`, run full `pytest`, `mutation-guard`, single-agent `code-review`, `validate_specs.py --all`, build `v0.6.0`, bump `vinhthang/oci`, and verify live `collab.vinhthang.dev`.

## Gate 2: traceability and readiness

| Requirement | AC | ADR or rationale | Design | Task | Planned verification | Status / reason |
| --- | --- | --- | --- | --- | --- | --- |
| REQ-070 | AC-070-1 | [ADR-040](./decisions.md#adr-040-default-invite-only-auth-gate-allow_anonymous_writesfalse--unconditional-anonymous-block-on-owner-permission-books--admin-sso-route-split) | [Interfaces](./design.md#interfaces-and-flow) | [Stage 1](#stage-1-tdd-suite), [Stage 2](#stage-2-store--service) | [AC-070-1](./verification.md#ac-070-1-req-070) | PASS |
| REQ-070 | AC-070-2 | [ADR-040](./decisions.md#adr-040-default-invite-only-auth-gate-allow_anonymous_writesfalse--unconditional-anonymous-block-on-owner-permission-books--admin-sso-route-split) | [Boundaries](./design.md#boundaries-and-recovery) | [Stage 1](#stage-1-tdd-suite), [Stage 2](#stage-2-store--service) | [AC-070-2](./verification.md#ac-070-2-req-070) | PASS |
| REQ-071 | AC-071-1 | [Rationale](./decisions.md#architectural-rationale) | [Interfaces](./design.md#interfaces-and-flow) | [Stage 1](#stage-1-tdd-suite), [Stage 2](#stage-2-store--service), [Stage 3](#stage-3-reader--threads-ui) | [AC-071-1](./verification.md#ac-071-1-req-071) | PASS |
| REQ-071 | AC-071-2 | [Rationale](./decisions.md#architectural-rationale) | [Boundaries](./design.md#boundaries-and-recovery) | [Stage 1](#stage-1-tdd-suite), [Stage 3](#stage-3-reader--threads-ui) | [AC-071-2](./verification.md#ac-071-2-req-071) | PASS |
| REQ-072 | AC-072-1 | [Rationale](./decisions.md#architectural-rationale) | [Interfaces](./design.md#interfaces-and-flow) | [Stage 1](#stage-1-tdd-suite), [Stage 2](#stage-2-store--service), [Stage 3](#stage-3-reader--threads-ui) | [AC-072-1](./verification.md#ac-072-1-req-072) | PASS |
| REQ-072 | AC-072-2 | [Rationale](./decisions.md#architectural-rationale) | [Boundaries](./design.md#boundaries-and-recovery) | [Stage 1](#stage-1-tdd-suite), [Stage 3](#stage-3-reader--threads-ui) | [AC-072-2](./verification.md#ac-072-2-req-072) | PASS |

- Backward scope check: PASS — Every task and component serves `REQ-070..REQ-072`.
- Reference and catalog consistency: PASS — `INDEX.md`, `decisions.md`, `design.md`, and `verification.md` agree on IDs and anchors.
