# Tasks: Collab Books MCP Security, Scalability, & CI Hardening (`v4.8.0`)

Sources: [spec.md](./spec.md), [design.md](./design.md)

## Stage 1: PDF Fallback Portability Baseline & Hardening Test Suite

- [x] Add `compile_pdf.py` `(ImportError, OSError)` + `PyMuPDF` fallback and author `tests/test_mcp_v480_hardening.py` RED tests across `REQ-082..REQ-086`.
- Covers: REQ-082 / AC-082-1, AC-082-2, REQ-083 / AC-083-1, AC-083-2, REQ-084 / AC-084-1, AC-084-2, REQ-085 / AC-085-1, AC-085-2, REQ-086 / AC-086-1, AC-086-2
- Files: `scripts/compile_pdf.py`, `tests/test_mcp_v480_hardening.py`, `tests/test_collab_seed.py`, `tests/test_collab_sweep.py`, `tests/test_mcp_onboarding_and_access.py`, `tests/test_collab_config.py`, `tests/test_collab_worker_mcp.py`, `tests/test_mcp_workflow_v2.py`, `tests/test_mcp_trusted_intake_and_import.py`, `tests/test_mcp_reviewer_queue_and_matter.py`, `tests/test_mcp_public_corpus_gate.py`, `tests/test_mcp_reader_threads_and_invite_lockdown.py`, `tests/test_mcp_admin_console_rework.py`, `tests/test_mcp_tech_admin_bug_workflow.py`, `tests/test_mcp_agent_live_presence.py`
- Dependencies: None
- Blocked by: None
- Work: Verify `libpango`-free `PyMuPDF` PDF fallback via TDD Cycle 0 so full baseline suite passes (`366 passed`), then write RED tests for Cycles 1–4 and align `"4.8.0"` / `w1-***` test expectations.

## Stage 2: Auth, Perimeter Security, & Offloaded Middleware (`REQ-082`, `REQ-083`, `REQ-084`)

- [x] Implement `keys.py`, `config.py`, `auth.py`, `app.py` body cap / CSRF / worker-thread presence offload, and `agent_heartbeat` rate limiting.
- Covers: REQ-082 / AC-082-1, AC-082-2, REQ-083 / AC-083-1, AC-083-2, REQ-084 / AC-084-1
- Files: `scripts/mcp_server/keys.py`, `scripts/mcp_server/config.py`, `scripts/mcp_server/auth.py`, `scripts/mcp_server/app.py`, `scripts/mcp_server/service.py`
- Dependencies: Stage 1
- Blocked by: None
- Work: Create `keys.py` with 256-word lists & `w1-***` hints, harden Forward-Auth SSO & proxy IP extraction, add bounded `SlidingWindowRateLimiter` pruning + failed-auth throttle, enforce streaming `10 MiB` body cap + `application/json` + `/admin*` CSRF checks, offload middleware presence via `anyio.to_thread.run_sync`, and enforce `agent_heartbeat_rpm`.

## Stage 3: Persistent PostgreSQL, `O(1)` `list_books`, & Git-Before-DB Acceptance (`REQ-084`)

- [x] Upgrade `db.py`, `service.py`, and `git_sync.py` for persistent PG connection reuse, atomic `transaction()`, indexes, summary aggregation, and git-first chapter acceptance.
- Covers: REQ-084 / AC-084-1, AC-084-2
- Files: `scripts/mcp_server/db.py`, `scripts/mcp_server/service.py`, `scripts/mcp_server/git_sync.py`
- Dependencies: Stage 2
- Blocked by: None
- Work: Add `_get_pg()`, `transaction()`, 6 secondary indexes, `list_books_summary` + `GROUP BY` counters, remove duplicate `translation_xml` from `rounds_history`, execute git sync prior to transactional DB commit on acceptance, and check/replace GitHub release assets cleanly.

## Stage 4: UI Fix, Lazy Extractors, Cycles, CI/CD, `Dockerfile`, `v4.8.0` Docs, & Gates (`REQ-085`, `REQ-086`)

- [x] Fix `.js-retry-seed`, lazy-load extractors, break circular imports, fix `SyntaxWarning`, harden `ci-cd.yml` & `Dockerfile`, bump `v4.8.0`, and pass all verification gates.
- Covers: REQ-085 / AC-085-1, AC-085-2, REQ-086 / AC-086-1, AC-086-2
- Files: `scripts/mcp_server/admin_ui.py`, `scripts/extractors/__init__.py`, `scripts/extract.py`, `scripts/utils/pr_body.py`, `scripts/collab_worker.py`, `scripts/collab_seed.py`, `scripts/collab_sweep.py`, `scripts/compilers/epub_compiler.py`, `.github/workflows/ci-cd.yml`, `Dockerfile`, `plugin.json`, `skills/collab-translate-book/SKILL.md`, `agents/collab_admin.md`, `agents/collab_reviewer.md`, `agents/collab_translator.md`, `README.md`, `CONTRIBUTING.md`, `docs/specs/INDEX.md`
- Dependencies: Stage 3
- Blocked by: None
- Work: Implement remaining `REQ-085`/`REQ-086` changes, run `pytest`, `arch-guard`, `mutation-guard`, `validate_specs.py --all`, `code-review`, commit to `feat/v4.8.0-security-scalability-hardening`, and persist OpenViking docs.

## Gate 2: traceability and readiness

| Requirement | AC | ADR or rationale | Design | Stage | Planned verification | Status / reason |
| --- | --- | --- | --- | --- | --- | --- |
| REQ-082 | AC-082-1 | [ADR-044](./decisions.md#adr-044-pre-auth-body-cap-admin-browser-csrf-gate-fail-closed-ssoproxy-perimeter--expanded-3-word-agent_key) | [Interfaces](./design.md#interfaces-and-flow) | [Stage 1](#stage-1-pdf-fallback-portability-baseline--hardening-test-suite), [Stage 2](#stage-2-auth-perimeter-security--offloaded-middleware-req-082-req-083-req-084) | [AC-082-1](./verification.md#ac-082-1-req-082) | PASS |
| REQ-082 | AC-082-2 | [ADR-044](./decisions.md#adr-044-pre-auth-body-cap-admin-browser-csrf-gate-fail-closed-ssoproxy-perimeter--expanded-3-word-agent_key) | [Interfaces](./design.md#interfaces-and-flow) | [Stage 1](#stage-1-pdf-fallback-portability-baseline--hardening-test-suite), [Stage 2](#stage-2-auth-perimeter-security--offloaded-middleware-req-082-req-083-req-084) | [AC-082-2](./verification.md#ac-082-2-req-082) | PASS |
| REQ-083 | AC-083-1 | [ADR-044](./decisions.md#adr-044-pre-auth-body-cap-admin-browser-csrf-gate-fail-closed-ssoproxy-perimeter--expanded-3-word-agent_key) | [Boundaries](./design.md#boundaries-and-recovery) | [Stage 1](#stage-1-pdf-fallback-portability-baseline--hardening-test-suite), [Stage 2](#stage-2-auth-perimeter-security--offloaded-middleware-req-082-req-083-req-084) | [AC-083-1](./verification.md#ac-083-1-req-083) | PASS |
| REQ-083 | AC-083-2 | [ADR-044](./decisions.md#adr-044-pre-auth-body-cap-admin-browser-csrf-gate-fail-closed-ssoproxy-perimeter--expanded-3-word-agent_key) | [Boundaries](./design.md#boundaries-and-recovery) | [Stage 1](#stage-1-pdf-fallback-portability-baseline--hardening-test-suite), [Stage 2](#stage-2-auth-perimeter-security--offloaded-middleware-req-082-req-083-req-084) | [AC-083-2](./verification.md#ac-083-2-req-083) | PASS |
| REQ-084 | AC-084-1 | [ADR-045](./decisions.md#adr-045-single-connection-locked-postgresql-reuse-sql-summary-aggregation-thread-offloaded-middleware-presence--git-first-chapter-acceptance) | [Interfaces](./design.md#interfaces-and-flow) | [Stage 1](#stage-1-pdf-fallback-portability-baseline--hardening-test-suite), [Stage 2](#stage-2-auth-perimeter-security--offloaded-middleware-req-082-req-083-req-084), [Stage 3](#stage-3-persistent-postgresql-o1-list_books--git-before-db-acceptance-req-084) | [AC-084-1](./verification.md#ac-084-1-req-084) | PASS |
| REQ-084 | AC-084-2 | [ADR-045](./decisions.md#adr-045-single-connection-locked-postgresql-reuse-sql-summary-aggregation-thread-offloaded-middleware-presence--git-first-chapter-acceptance) | [Interfaces](./design.md#interfaces-and-flow) | [Stage 1](#stage-1-pdf-fallback-portability-baseline--hardening-test-suite), [Stage 3](#stage-3-persistent-postgresql-o1-list_books--git-before-db-acceptance-req-084) | [AC-084-2](./verification.md#ac-084-2-req-084) | PASS |
| REQ-085 | AC-085-1 | [ADR-045](./decisions.md#adr-045-single-connection-locked-postgresql-reuse-sql-summary-aggregation-thread-offloaded-middleware-presence--git-first-chapter-acceptance) | [Components](./design.md#components) | [Stage 1](#stage-1-pdf-fallback-portability-baseline--hardening-test-suite), [Stage 4](#stage-4-ui-fix-lazy-extractors-cycles-cicd-dockerfile-v480-docs--gates-req-085-req-086) | [AC-085-1](./verification.md#ac-085-1-req-085) | PASS |
| REQ-085 | AC-085-2 | [ADR-045](./decisions.md#adr-045-single-connection-locked-postgresql-reuse-sql-summary-aggregation-thread-offloaded-middleware-presence--git-first-chapter-acceptance) | [Components](./design.md#components) | [Stage 1](#stage-1-pdf-fallback-portability-baseline--hardening-test-suite), [Stage 4](#stage-4-ui-fix-lazy-extractors-cycles-cicd-dockerfile-v480-docs--gates-req-085-req-086) | [AC-085-2](./verification.md#ac-085-2-req-085) | PASS |
| REQ-086 | AC-086-1 | [ADR-045](./decisions.md#adr-045-single-connection-locked-postgresql-reuse-sql-summary-aggregation-thread-offloaded-middleware-presence--git-first-chapter-acceptance) | [Boundaries](./design.md#boundaries-and-recovery) | [Stage 1](#stage-1-pdf-fallback-portability-baseline--hardening-test-suite), [Stage 4](#stage-4-ui-fix-lazy-extractors-cycles-cicd-dockerfile-v480-docs--gates-req-085-req-086) | [AC-086-1](./verification.md#ac-086-1-req-086) | PASS |
| REQ-086 | AC-086-2 | [ADR-045](./decisions.md#adr-045-single-connection-locked-postgresql-reuse-sql-summary-aggregation-thread-offloaded-middleware-presence--git-first-chapter-acceptance) | [Boundaries](./design.md#boundaries-and-recovery) | [Stage 1](#stage-1-pdf-fallback-portability-baseline--hardening-test-suite), [Stage 4](#stage-4-ui-fix-lazy-extractors-cycles-cicd-dockerfile-v480-docs--gates-req-085-req-086) | [AC-086-2](./verification.md#ac-086-2-req-086) | PASS |

- Backward scope check: PASS — Every stage and file slice traces directly to `REQ-082..REQ-086`.
- Reference and catalog consistency: PASS — `INDEX.md`, `decisions.md`, `design.md`, and `verification.md` agree on IDs and anchors.
