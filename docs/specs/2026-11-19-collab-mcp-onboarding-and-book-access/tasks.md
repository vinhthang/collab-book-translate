# Tasks: Zero-Prompt MCP Onboarding, 3-Word `AGENT_KEY`, & Per-Book Access Control

Sources: [spec.md](./spec.md), [design.md](./design.md)

## Task 1: Implement 3-word `AGENT_KEY` minting, zero-arg registration, and additive DB schema

- [x] Complete the work and its applicable checks.
- Covers: REQ-044 / AC-044-1, AC-044-2, REQ-045 / AC-045-1, AC-045-2
- Files: `scripts/mcp_server/auth.py`, `scripts/mcp_server/db.py`, `tests/test_mcp_auth_and_store.py`
- Dependencies: None
- Blocked by: None
- Work: Write failing TDD tests in `tests/test_mcp_auth_and_store.py` for 3-word `AGENT_KEY` format, `key_hint`, legacy `cbt_ak_*` auth, zero-arg `register_agent()`, `last_seen_at`, `list_agents()`, `resolve_agent_identifier()`, and `set_book_access()`; implement changes in `auth.py` and `db.py`.
- Completion evidence: `uv run pytest` -> `322 passed`; `mutation-guard` on `scripts/mcp_server/auth.py` -> `90.0% (9/10 killed)`.

## Task 2: Enforce write-restricted per-book allowlists and first-write auto-minting in `CollabMcpService`

- [x] Complete the work and its applicable checks.
- Covers: REQ-044 / AC-044-1, AC-044-2, REQ-045 / AC-045-1, AC-045-2
- Files: `scripts/mcp_server/service.py`, `tests/test_mcp_service_and_app.py`
- Dependencies: Task 1
- Blocked by: None
- Work: Write failing TDD tests in `tests/test_mcp_service_and_app.py` for first-write `assigned_agent_key` auto-minting (and zero minting on reads / restricted `403`), write-restricted book visibility vs. `403` enforcement on `get_chapter`/`claim_chapter`/`submit_translation`/`submit_review`, and `set_book_access` resolving both 3-word `AGENT_KEY` and `handle`; implement in `service.py`.
- Completion evidence: `uv run pytest` -> `322 passed`; `mutation-guard` on `scripts/mcp_server/service.py` -> `85.0% (17/20 killed)`.

## Task 3: Add built-in MCP `instructions`, prompts, onboarding card on `/`, `/admin` SSO UI, and updated docs

- [x] Complete the work and its applicable checks.
- Covers: REQ-045 / AC-045-1, AC-045-2, REQ-046 / AC-046-1, AC-046-2
- Files: `scripts/mcp_server/app.py`, `README.md`, `skills/collab-translate-book/SKILL.md`, `tests/test_mcp_service_and_app.py`, `tests/test_collab_worker_mcp.py`
- Dependencies: Task 2
- Blocked by: None
- Work: Add tests verifying `initialize` `instructions`, `prompts/list`, enriched `tools/list` docstrings, `/` onboarding card, `/admin` HTML & `/admin/api/*` auth gating, and zero secret leaks; implement in `app.py` and update `README.md` and `SKILL.md`.
- Completion evidence: `uv run pytest tests/test_mcp_onboarding_and_access.py` -> `4 passed`; full suite -> `322 passed`.

## Gate 2: traceability and readiness

| Requirement | AC | ADR or rationale | Design | Task | Planned verification | Status / reason |
| --- | --- | --- | --- | --- | --- | --- |
| REQ-044 | AC-044-1 | [ADR-032](./decisions.md#adr-032-hashed-3-word-agent_key-credentials-paired-with-public-canonical-handles-for-per-book-allowlists) | [Interfaces](./design.md#interfaces-and-flow) | [Task 1](#task-1-implement-3-word-agent_key-minting-zero-arg-registration-and-additive-db-schema), [Task 2](#task-2-enforce-write-restricted-per-book-allowlists-and-first-write-auto-minting-in-collabmcpservice) | [AC-044-1](./verification.md#ac-044-1-req-044) | PASS — 3-word key minting, zero-arg register, legacy compat, and first-write auto-mint planned |
| REQ-044 | AC-044-2 | [ADR-032](./decisions.md#adr-032-hashed-3-word-agent_key-credentials-paired-with-public-canonical-handles-for-per-book-allowlists) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 1](#task-1-implement-3-word-agent_key-minting-zero-arg-registration-and-additive-db-schema), [Task 2](#task-2-enforce-write-restricted-per-book-allowlists-and-first-write-auto-minting-in-collabmcpservice) | [AC-044-2](./verification.md#ac-044-2-req-044) | PASS — Zero minting on reads, duplicate `409`, and restricted `403` planned |
| REQ-045 | AC-045-1 | [ADR-032](./decisions.md#adr-032-hashed-3-word-agent_key-credentials-paired-with-public-canonical-handles-for-per-book-allowlists) | [Components](./design.md#components) | [Task 2](#task-2-enforce-write-restricted-per-book-allowlists-and-first-write-auto-minting-in-collabmcpservice), [Task 3](#task-3-add-built-in-mcp-instructions-prompts-onboarding-card-on--admin-sso-ui-and-updated-docs) | [AC-045-1](./verification.md#ac-045-1-req-045) | PASS — Per-book allowlist enforcement, key/handle resolution, and `/admin` UI planned |
| REQ-045 | AC-045-2 | [ADR-032](./decisions.md#adr-032-hashed-3-word-agent_key-credentials-paired-with-public-canonical-handles-for-per-book-allowlists) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 2](#task-2-enforce-write-restricted-per-book-allowlists-and-first-write-auto-minting-in-collabmcpservice), [Task 3](#task-3-add-built-in-mcp-instructions-prompts-onboarding-card-on--admin-sso-ui-and-updated-docs) | [AC-045-2](./verification.md#ac-045-2-req-045) | PASS — `400`/`403` boundaries and zero raw-key leak checks planned |
| REQ-046 | AC-046-1 | [Rationale](./decisions.md#architectural-rationale) | [Components](./design.md#components) | [Task 3](#task-3-add-built-in-mcp-instructions-prompts-onboarding-card-on--admin-sso-ui-and-updated-docs) | [AC-046-1](./verification.md#ac-046-1-req-046) | PASS — Built-in MCP `instructions`, docstrings, prompts, and `/` card planned |
| REQ-046 | AC-046-2 | [Rationale](./decisions.md#architectural-rationale) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 3](#task-3-add-built-in-mcp-instructions-prompts-onboarding-card-on--admin-sso-ui-and-updated-docs) | [AC-046-2](./verification.md#ac-046-2-req-046) | PASS — Unauthenticated `200` discovery with zero secret leakage planned |

- Backward scope check: PASS — Every file and task maps directly to `REQ-044..REQ-046`.
- Reference and catalog consistency: PASS — `INDEX.md`, `spec.md`, `decisions.md`, `design.md`, `tasks.md`, and `verification.md` agree on `REQ-044..REQ-046`, `ADR-032`, and status `Planning`.
