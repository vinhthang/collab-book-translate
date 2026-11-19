# Tasks: Option A Corpus Split & Remote Book Collaboration MCP Server

Sources: [spec.md](./spec.md), [design.md](./design.md)

## Task 1: Untrack book corpus, relocate pilot test fixture, and add `mcp_config.json`

- [x] Complete the work and its applicable checks.
- Covers: REQ-037 / AC-037-1, AC-037-2
- Files: `.gitignore`, `config.example.json`, `mcp_config.json`, `books/`, `runs/`, `tests/fixtures/runs/scandal-in-bohemia/`, `tests/test_collab_completeness.py`, `tests/test_collab_ledger.py`, `tests/test_option_a_hygiene.py`
- Dependencies: None
- Blocked by: None
- Work: Write failing hygiene/fixture tests in `tests/test_option_a_hygiene.py`, copy `scandal-in-bohemia` test fixture into `tests/fixtures/runs/scandal-in-bohemia/`, remove tracked `books/` and `runs/` from git, update `.gitignore` and `config.example.json` (`<owner>/collab-books`), and create `mcp_config.json`.
- Completion evidence: `uv run pytest` -> `306 passed` including `tests/test_option_a_hygiene.py` (`AC-037-1`, `AC-037-2`).

## Task 2: Implement `CollabStore`, 3-tier `auth.py`, and pre-LLM abuse gates

- [x] Complete the work and its applicable checks.
- Covers: REQ-038 / AC-038-2, REQ-039 / AC-039-1, AC-039-2, REQ-040 / AC-040-1, AC-040-2
- Files: `pyproject.toml`, `uv.lock`, `scripts/mcp_server/__init__.py`, `scripts/mcp_server/config.py`, `scripts/mcp_server/db.py`, `scripts/mcp_server/auth.py`, `tests/test_mcp_auth_and_store.py`
- Dependencies: Task 1
- Blocked by: None
- Work: Add `mcp`, `starlette`, `uvicorn`, `httpx`, and `psycopg[binary]` dependencies; write failing TDD tests in `tests/test_mcp_auth_and_store.py` for idempotent schema bootstrap, `ANONYMOUS` / `AGENT_KEY` (`cbt_ak_*` SHA-256) / `ADMIN` auth, spoofed `X-Auth-Request-*` rejection, sliding-window rate limits, `revision_token` verification, and anti-self-review; implement `config.py`, `db.py`, and `auth.py`.
- Completion evidence: `uv run pytest tests/test_mcp_auth_and_store.py` -> `4 passed`; `mutation-guard` on `scripts/mcp_server/auth.py` -> `90.0% (9/10 killed)`.

## Task 3: Implement `ReviewServerClient`, `CollabBooksGitSync`, `CollabMcpService`, and HTTP/MCP `app.py`

- [x] Complete the work and its applicable checks.
- Covers: REQ-038 / AC-038-1, AC-038-2, REQ-040 / AC-040-1, AC-040-2, REQ-041 / AC-041-1, AC-041-2, REQ-042 / AC-042-1, AC-042-2
- Files: `scripts/mcp_server/review_client.py`, `scripts/mcp_server/git_sync.py`, `scripts/mcp_server/service.py`, `scripts/mcp_server/app.py`, `Dockerfile`, `tests/test_mcp_service_and_app.py`
- Dependencies: Task 2
- Blocked by: None
- Work: Write failing TDD tests in `tests/test_mcp_service_and_app.py` covering `/mcp`, `/api/v1/*`, `/api/health`, `/llms.txt`, `/` Bookshelf HTML, pre-LLM XML & 4-class injection blocking, multi-round `TRANSLATION_REVIEW` + rebuttals, `ch01` seeding & starved fallback via `/api/v1/translate`, and atomic Git/Release sync; implement `review_client.py`, `git_sync.py`, `service.py`, `app.py`, and `Dockerfile`.
- Completion evidence: `uv run pytest tests/test_mcp_service_and_app.py` -> `5 passed`; `mutation-guard` on `scripts/mcp_server/service.py` -> `95.0% (19/20 killed)`.

## Task 4: Upgrade `collab_worker.py` (`--mcp-url`) and plugin v3.0.0 customizations & docs

- [x] Complete the work and its applicable checks.
- Covers: REQ-043 / AC-043-1, AC-043-2
- Files: `scripts/collab_worker.py`, `plugin.json`, `skills/collab-translate-book/SKILL.md`, `agents/collab_translator.md`, `agents/collab_reviewer.md`, `README.md`, `CONTRIBUTING.md`, `tests/test_collab_worker_mcp.py`
- Dependencies: Task 3
- Blocked by: None
- Work: Write failing TDD tests in `tests/test_collab_worker_mcp.py` for `--mcp-url` (`ANONYMOUS`, `--agent-key`, `--revision-token`, `--rebuttals-file`, error exit codes), implement remote MCP/REST client mode in `collab_worker.py` while keeping `--gh-user` intact, bump `plugin.json` to `3.0.0`, and update `SKILL.md`, `agents/*.md`, `README.md` (retaining all 4 role sections), and `CONTRIBUTING.md`.
- Completion evidence: `uv run pytest` -> `318 passed in 11.4s` (`0` failed, `0` skipped) including `tests/test_collab_worker_mcp.py` and `tests/test_collab_worker.py`.

## Gate 2: traceability and readiness

| Requirement | AC | ADR or rationale | Design | Task | Planned verification | Status / reason |
| --- | --- | --- | --- | --- | --- | --- |
| REQ-037 | AC-037-1 | [ADR-031](./decisions.md#adr-031-postgresql-collab_service-operational-store-paired-with-atomic-gitrelease-sync-to-collab-books) | [Components](./design.md#components) | [Task 1](#task-1-untrack-book-corpus-relocate-pilot-test-fixture-and-add-mcp_configjson) | [AC-037-1](./verification.md#ac-037-1-req-037) | PASS — Corpus untrack, fixture relocation, and `mcp_config.json` planned |
| REQ-037 | AC-037-2 | [Rationale](./decisions.md#architectural-rationale) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 1](#task-1-untrack-book-corpus-relocate-pilot-test-fixture-and-add-mcp_configjson) | [AC-037-2](./verification.md#ac-037-2-req-037) | PASS — Hygiene & untracked corpus test planned |
| REQ-038 | AC-038-1 | [ADR-029](./decisions.md#adr-029-standalone-collab-mcp-service-delegating-llm-review-and-fallback-translation-to-reviewvinhthangdev) | [Interfaces](./design.md#interfaces-and-flow) | [Task 3](#task-3-implement-reviewserverclient-collabbooksgitsync-collabmcpservice-and-httpmcp-apppy) | [AC-038-1](./verification.md#ac-038-1-req-038) | PASS — `/mcp`, `/api/v1/*`, `/llms.txt`, `/api/health`, and `/` HTML planned |
| REQ-038 | AC-038-2 | [ADR-031](./decisions.md#adr-031-postgresql-collab_service-operational-store-paired-with-atomic-gitrelease-sync-to-collab-books) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 2](#task-2-implement-collabstore-3-tier-authpy-and-pre-llm-abuse-gates), [Task 3](#task-3-implement-reviewserverclient-collabbooksgitsync-collabmcpservice-and-httpmcp-apppy) | [AC-038-2](./verification.md#ac-038-2-req-038) | PASS — Idempotent DDL and `400`/`404` error handling planned |
| REQ-039 | AC-039-1 | [ADR-030](./decisions.md#adr-030-three-tier-authentication-with-per-submission-revision_token-and-dual-headerargument-credentials) | [Interfaces](./design.md#interfaces-and-flow) | [Task 2](#task-2-implement-collabstore-3-tier-authpy-and-pre-llm-abuse-gates) | [AC-039-1](./verification.md#ac-039-1-req-039) | PASS — 3-tier auth (`ANONYMOUS`, `AGENT_KEY`, `ADMIN`) planned |
| REQ-039 | AC-039-2 | [ADR-030](./decisions.md#adr-030-three-tier-authentication-with-per-submission-revision_token-and-dual-headerargument-credentials) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 2](#task-2-implement-collabstore-3-tier-authpy-and-pre-llm-abuse-gates) | [AC-039-2](./verification.md#ac-039-2-req-039) | PASS — `401`/`403` and spoofed `X-Auth-Request-*` stripping planned |
| REQ-040 | AC-040-1 | [ADR-030](./decisions.md#adr-030-three-tier-authentication-with-per-submission-revision_token-and-dual-headerargument-credentials) | [Interfaces](./design.md#interfaces-and-flow) | [Task 2](#task-2-implement-collabstore-3-tier-authpy-and-pre-llm-abuse-gates), [Task 3](#task-3-implement-reviewserverclient-collabbooksgitsync-collabmcpservice-and-httpmcp-apppy) | [AC-040-1](./verification.md#ac-040-1-req-040) | PASS — `revision_token` round-2 flow and valid peer review planned |
| REQ-040 | AC-040-2 | [ADR-030](./decisions.md#adr-030-three-tier-authentication-with-per-submission-revision_token-and-dual-headerargument-credentials) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 2](#task-2-implement-collabstore-3-tier-authpy-and-pre-llm-abuse-gates), [Task 3](#task-3-implement-reviewserverclient-collabbooksgitsync-collabmcpservice-and-httpmcp-apppy) | [AC-040-2](./verification.md#ac-040-2-req-040) | PASS — Pre-LLM `429`/`413`/`422`/`403`/`flagged_security` gates planned |
| REQ-041 | AC-041-1 | [ADR-029](./decisions.md#adr-029-standalone-collab-mcp-service-delegating-llm-review-and-fallback-translation-to-reviewvinhthangdev) | [Interfaces](./design.md#interfaces-and-flow) | [Task 3](#task-3-implement-reviewserverclient-collabbooksgitsync-collabmcpservice-and-httpmcp-apppy) | [AC-041-1](./verification.md#ac-041-1-req-041) | PASS — `TRANSLATION_REVIEW` `PASS` & rebuttal convergence planned |
| REQ-041 | AC-041-2 | [ADR-029](./decisions.md#adr-029-standalone-collab-mcp-service-delegating-llm-review-and-fallback-translation-to-reviewvinhthangdev) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 3](#task-3-implement-reviewserverclient-collabbooksgitsync-collabmcpservice-and-httpmcp-apppy) | [AC-041-2](./verification.md#ac-041-2-req-041) | PASS — `BLOCK` remediation & `escalated_to_admin` cap planned |
| REQ-042 | AC-042-1 | [ADR-029](./decisions.md#adr-029-standalone-collab-mcp-service-delegating-llm-review-and-fallback-translation-to-reviewvinhthangdev), [ADR-031](./decisions.md#adr-031-postgresql-collab_service-operational-store-paired-with-atomic-gitrelease-sync-to-collab-books) | [Interfaces](./design.md#interfaces-and-flow) | [Task 3](#task-3-implement-reviewserverclient-collabbooksgitsync-collabmcpservice-and-httpmcp-apppy) | [AC-042-1](./verification.md#ac-042-1-req-042) | PASS — `ch01` seed, starved fallback, Git commit, and EPUB/PDF/DOCX release planned |
| REQ-042 | AC-042-2 | [ADR-031](./decisions.md#adr-031-postgresql-collab_service-operational-store-paired-with-atomic-gitrelease-sync-to-collab-books) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 3](#task-3-implement-reviewserverclient-collabbooksgitsync-collabmcpservice-and-httpmcp-apppy) | [AC-042-2](./verification.md#ac-042-2-req-042) | PASS — Failed seed/fallback abort and active-claim skip planned |
| REQ-043 | AC-043-1 | [ADR-030](./decisions.md#adr-030-three-tier-authentication-with-per-submission-revision_token-and-dual-headerargument-credentials) | [Components](./design.md#components) | [Task 4](#task-4-upgrade-collab_workerpy---mcp-url-and-plugin-v300-customizations--docs) | [AC-043-1](./verification.md#ac-043-1-req-043) | PASS — `collab_worker.py --mcp-url` and plugin v3.0.0 docs planned |
| REQ-043 | AC-043-2 | [ADR-030](./decisions.md#adr-030-three-tier-authentication-with-per-submission-revision_token-and-dual-headerargument-credentials) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 4](#task-4-upgrade-collab_workerpy---mcp-url-and-plugin-v300-customizations--docs) | [AC-043-2](./verification.md#ac-043-2-req-043) | PASS — Missing target / HTTP error exit codes planned |

- Backward scope check: PASS — Every component in `design.md` and task in `tasks.md` maps directly to `REQ-037`..`REQ-043`.
- Reference and catalog consistency: PASS — `docs/specs/INDEX.md`, `spec.md`, `decisions.md`, `design.md`, `tasks.md`, and `verification.md` agree on `REQ-037..REQ-043`, `ADR-029..ADR-031`, and status `Planning`.
