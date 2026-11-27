# Tasks: Collab Books MCP Admin Workspace Console Rework

Sources: [spec.md](./spec.md), [design.md](./design.md)

## Stage 1: TDD Suite

- [x] Write failing RED tests first for `REQ-073..REQ-075` and align `"4.5.0"` version expectations.
- Covers: REQ-073 / AC-073-1, AC-073-2, REQ-074 / AC-074-1, AC-074-2, REQ-075 / AC-075-1, AC-075-2
- Files: `tests/test_mcp_admin_console_rework.py`, `tests/test_collab_config.py`, `tests/test_collab_worker_mcp.py`, `tests/test_mcp_bug_triage_and_hardening.py`, `tests/test_mcp_public_corpus_gate.py`, `tests/test_mcp_reviewer_queue_and_matter.py`, `tests/test_mcp_trusted_intake_and_import.py`, `tests/test_mcp_workflow_v2.py`
- Dependencies: None
- Blocked by: None
- Work: Write failing RED tests covering agent key rotation/soft-revoke/delete/stats, lazy submission detail, bulk manage, private EPUB/PDF/DOCX export isolation, Workspace Console UI (`node --check`, XSS, zero secrets), unified `/admin/threads` & `/admin/read/*` theme chrome, and `"4.5.0"` metadata.

## Stage 2: Store, Export, & Service

- [x] Implement `agents.revoked_at`, rotation/revocation/stats, isolated `compile_private_export`, lazy submission detail, bulk manage, and `export_book_deliverable`.
- Covers: REQ-073 / AC-073-1, AC-073-2, REQ-074 / AC-074-1, AC-074-2
- Files: `scripts/mcp_server/db.py`, `scripts/mcp_server/git_sync.py`, `scripts/mcp_server/service.py`
- Dependencies: Stage 1
- Blocked by: None
- Work: Update `db.py` schema/migrations and agent lifecycle helpers, add `compile_private_export` in `git_sync.py`, and implement service methods with full `ADMIN` and input-validation boundaries.

## Stage 3: Workspace Console UI, Unified Chrome, & Rollout

- [x] Create `scripts/mcp_server/admin_ui.py`, wire routes + unified theme in `app.py`, bump `"4.5.0"`/`v0.7.0`, gate, and deploy live.
- Covers: REQ-074 / AC-074-1, AC-074-2, REQ-075 / AC-075-1, AC-075-2
- Files: `scripts/mcp_server/admin_ui.py`, `scripts/mcp_server/app.py`, `plugin.json`, `.github/workflows/ci-cd.yml`, `skills/collab-translate-book/SKILL.md`, `agents/collab_admin.md`, `docs/specs/INDEX.md`
- Dependencies: Stage 2
- Blocked by: None
- Work: Implement Sidebar Workspace Console + Drawers + Bulk Controls + Dark/Light Theme toggle, wire 5 new `/admin/api/*` endpoints, verify full `pytest` + `mutation-guard` (`>= 80%`) + `code-review` (`0` P0/P1) + `validate_specs.py --all`, deploy `collab-mcp:v0.7.0`, verify live `collab.vinhthang.dev`, persist OpenViking milestone, and write `walkthrough.md`.

## Gate 2: traceability and readiness

| Requirement | AC | ADR or rationale | Design | Stage | Planned verification | Status / reason |
| --- | --- | --- | --- | --- | --- | --- |
| REQ-073 | AC-073-1 | [ADR-041](./decisions.md#adr-041-zero-build-modular-ssr-admin_uipy--lazy-json-drawers-over-a-bundled-spa) | [Interfaces](./design.md#interfaces-and-flow) | [Stage 1](#stage-1-tdd-suite), [Stage 2](#stage-2-store-export--service) | [AC-073-1](./verification.md#ac-073-1-req-073) | PASS |
| REQ-073 | AC-073-2 | [ADR-041](./decisions.md#adr-041-zero-build-modular-ssr-admin_uipy--lazy-json-drawers-over-a-bundled-spa) | [Boundaries](./design.md#boundaries-and-recovery) | [Stage 1](#stage-1-tdd-suite), [Stage 2](#stage-2-store-export--service) | [AC-073-2](./verification.md#ac-073-2-req-073) | PASS |
| REQ-074 | AC-074-1 | [ADR-041](./decisions.md#adr-041-zero-build-modular-ssr-admin_uipy--lazy-json-drawers-over-a-bundled-spa) | [Interfaces](./design.md#interfaces-and-flow) | [Stage 1](#stage-1-tdd-suite), [Stage 2](#stage-2-store-export--service), [Stage 3](#stage-3-workspace-console-ui-unified-chrome--rollout) | [AC-074-1](./verification.md#ac-074-1-req-074) | PASS |
| REQ-074 | AC-074-2 | [ADR-041](./decisions.md#adr-041-zero-build-modular-ssr-admin_uipy--lazy-json-drawers-over-a-bundled-spa) | [Boundaries](./design.md#boundaries-and-recovery) | [Stage 1](#stage-1-tdd-suite), [Stage 2](#stage-2-store-export--service), [Stage 3](#stage-3-workspace-console-ui-unified-chrome--rollout) | [AC-074-2](./verification.md#ac-074-2-req-074) | PASS |
| REQ-075 | AC-075-1 | [ADR-041](./decisions.md#adr-041-zero-build-modular-ssr-admin_uipy--lazy-json-drawers-over-a-bundled-spa) | [Interfaces](./design.md#interfaces-and-flow) | [Stage 1](#stage-1-tdd-suite), [Stage 3](#stage-3-workspace-console-ui-unified-chrome--rollout) | [AC-075-1](./verification.md#ac-075-1-req-075) | PASS |
| REQ-075 | AC-075-2 | [ADR-041](./decisions.md#adr-041-zero-build-modular-ssr-admin_uipy--lazy-json-drawers-over-a-bundled-spa) | [Boundaries](./design.md#boundaries-and-recovery) | [Stage 1](#stage-1-tdd-suite), [Stage 3](#stage-3-workspace-console-ui-unified-chrome--rollout) | [AC-075-2](./verification.md#ac-075-2-req-075) | PASS |

- Backward scope check: PASS — Every stage and component traces directly to `REQ-073..REQ-075`.
- Reference and catalog consistency: PASS — `INDEX.md`, `decisions.md`, `design.md`, and `verification.md` agree on IDs and anchors.
