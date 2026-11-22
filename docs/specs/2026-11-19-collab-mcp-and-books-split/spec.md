# Option A Corpus Split & Remote Book Collaboration MCP Server

- Feature ID: 2026-11-19-collab-mcp-and-books-split
- Type: new feature
- Status: In-Testing
- First entered In-Testing: 2026-11-19
- Related specifications: Implemented: Changes [2026-11-18-collab-worker-multi-agent](../2026-11-18-collab-worker-multi-agent/spec.md)

## Problem and source intent

Decouple book corpus data from engine/plugin code (**Option A**) and introduce a standalone remote **Book Collaboration MCP + REST Server** (`https://collab.vinhthang.dev`) so multi-agent book translation does not depend solely on GitHub Pull Requests or GitHub accounts, while reusing `https://review.vinhthang.dev` (`review-server`) for automated multi-round translation review and fallback chapter translation.

Authorizing user intent (2026-11-19, verbatim user prompts and `/grill-me` architecture decisions):

1. *"so it is too messy, right? I think each book should have it own branch"* -> *"I would go with option A, and add a mcp server, so the work would not only depend on github pull request. I would reuse the review.vinhthang.dev for the translate and review work, the new mcp server will be for query books, and submit work"*.
2. *"I want other ways to do authenticate, that is also the reason I setup the new mcp server, that could much easier for me now, maybe there is public role, ANONYMOUS for it"*.
3. Resolved `/grill-me` decisions (`A1` across all 5 architecture questions + implementation plan approval):
   - Standalone Python 3.12 MCP + HTTP service inside `collab-book-translate` deployed at `https://collab.vinhthang.dev`, calling `https://review.vinhthang.dev` server-to-server (`Bearer REVIEW_BEARER_TOKEN`) for `TRANSLATION_REVIEW` workstreams and `/api/v1/translate` (`ch01` seeding & starved-chapter fallback).
   - 3-tier authentication: Tier 1 `ANONYMOUS` (read books/tasks/chapters, claim chapters, propose books into `pending_approval`, submit translations with per-submission `revision_token` for `R2`/`R3`, submit peer reviews where `reviewer != author`), Tier 2 self-serve `AGENT_KEY` (`register_agent`), and Tier 3 `ADMIN` (`COLLAB_ADMIN_TOKEN` or edge Google SSO on `/admin/*`).
   - Dedicated public data repository `vinhthang/collab-books` receiving migrated `happy-prince` and `scandal-in-bohemia` books/releases and automatic Git commits + EPUB 3/PDF/DOCX releases from accepted MCP chapters.

## Repository grounding

Inspected against `collab-book-translate` at commit `45b3645`:

- **Mixed engine + book corpus**: `books/{happy-prince,scandal-in-bohemia}` and `runs/{happy-prince,scandal-in-bohemia}` currently reside inside `collab-book-translate`, and two tests (`tests/test_collab_completeness.py:101` and `tests/test_collab_ledger.py:189`) read `runs/scandal-in-bohemia` directly from the repository root.
- **GitHub-PR-only coupling**: `scripts/collab_worker.py`, `scripts/collab_seed.py`, and `scripts/collab_sweep.py` require `gh` CLI authentication (`--gh-user`) and local git worktrees; no `scripts/mcp_server/` package, `Dockerfile`, or `mcp_config.json` exists.
- **Reusable deterministic validators & compilers**: `scripts/collab_pr_validate.py` (`validate_submission_pr`, `validate_translation_pr`, `scan_submission`), `scripts/collab_verdict.py` (`enforce_language_detection`, `validate_review`), `scripts/collab_completeness.py`, `scripts/collab_ledger.py`, and `scripts/compile_book.py` already implement segment parity, code/image byte-invariance, 4-class prompt-injection scanning, ledger integrity, and EPUB 3/PDF/DOCX compilation.
- **Preserved config & hygiene contracts (`REQ-031`)**: `scripts/collab_config.py` validates the 12 required keys in `config.example.json` while ignoring extra keys, and repository hygiene forbids legacy vendor evaluator tokens, CLI wrapper prefixes, or local machine home paths in tracked files.

## Non-goals

- **No removal of optional GitHub PR workflow**: Existing `collab_worker.py --gh-user`, `collab_seed.py`, and `collab_sweep.py` remain supported against `vinhthang/collab-books`.
- **No direct LLM API keys inside `collab-mcp`**: All LLM review and fallback translation calls route strictly through `review.vinhthang.dev` (`REVIEW_BEARER_TOKEN`).
- **No unauthenticated admin mutations**: `ANONYMOUS` book proposals and security-flagged submissions never publish or execute LLM work without pre-LLM gates or `ADMIN` approval.

## Requirements

### REQ-037: Option A corpus separation, self-contained test fixtures, and plugin MCP manifest

- Changes: [2026-11-18-collab-worker-multi-agent/REQ-031](../2026-11-18-collab-worker-multi-agent/spec.md#req-031-streamlined-12-key-collabconfig-zero-vendor-tokens-and-antigravity-pluginjson-schema)
- Source intent: Verbatim user decision (*"I would go with option A, and add a mcp server"* -> separate `vinhthang/collab-books` data repository from `vinhthang/collab-book-translate` engine/plugin repository).
- Previous contract: `books/` and `runs/` pilot book artifacts were tracked inside `collab-book-translate`, `config.example.json` referenced `<owner>/collab-book-translate`, and no `mcp_config.json` existed.
- Statement: WHEN `collab-book-translate` is packaged as an engine and Antigravity plugin, `books/` and `runs/` corpus directories SHALL NOT be tracked in git (pilot test data required by `test_collab_completeness.py` and `test_collab_ledger.py` SHALL reside in `tests/fixtures/runs/scandal-in-bohemia`), `config.example.json` SHALL preserve the exact 12 `CollabConfig` keys pointing `workspace_dir` and `repo_full_name` to `collab-books`, `mcp_config.json` SHALL declare `"collab-books": {"url": "https://collab.vinhthang.dev/mcp"}`, and tracked files SHALL maintain `REQ-031` hygiene invariants.
- AC-037-1 — Success: `git ls-files books runs` in `collab-book-translate` returns empty output, `tests/fixtures/runs/scandal-in-bohemia` satisfies completeness and ledger unit tests, `mcp_config.json` parses with Streamable HTTP `url` `https://collab.vinhthang.dev/mcp`, and `load_collab_config("config.example.json")` succeeds with `repo_full_name == "<owner>/collab-books"`.
- AC-037-2 — Boundary/failure: IF any tracked file under `scripts/`, `tests/`, `plugin.json`, `mcp_config.json`, `config.example.json`, `.github/`, `CONTRIBUTING.md`, or `README.md` reintroduces forbidden tokens from `REQ-031` or tracked `books/*`/`runs/*` payloads, THEN repository hygiene tests SHALL fail.

### REQ-038: Standalone `collab-mcp` transports, discovery endpoints, and PostgreSQL/SQLite persistence

- Current contract: Changed by [2026-11-23-collab-mcp-public-corpus-publish-gate/REQ-069](../2026-11-23-collab-mcp-public-corpus-publish-gate/spec.md#req-069-public-bookshelf-filtering-admin-policy-controls-startup-originmain-reconciliation--mcp-v431)
- Source intent: Verbatim user request (*"add a mcp server, so the work would not only depend on github pull request ... the new mcp server will be for query books, and submit work"*) and `/grill-me` `Q1`/`Q5` (`A1`).
- Statement: WHEN `collab-mcp` (`scripts/mcp_server/app.py`) starts, IT SHALL idempotently initialize its relational persistence store (`CREATE SCHEMA IF NOT EXISTS collab_service` on PostgreSQL when `DATABASE_URL` is set, or SQLite for local/test runs) and serve Streamable HTTP MCP at `/mcp`, SSE MCP compatibility at `/sse` and `/messages/`, JSON REST endpoints at `/api/v1/*`, health status at `/api/health`, machine-readable onboarding documentation at `/llms.txt`, and a read-only HTML Bookshelf & Live Chapter Progress page at `/`.
- AC-038-1 — Success: `GET /api/health` returns `200` with store status, `GET /llms.txt` returns `200 text/plain` documenting all MCP tools and 3 auth tiers, `GET /` returns `200 text/html` rendering active books and chapter completion progress, and MCP clients connecting to `/mcp` list and invoke registered book collaboration tools.
- AC-038-2 — Boundary/failure: IF a database schema bootstrap is executed repeatedly across restarts, THEN initialization SHALL complete idempotently without data loss or duplicate-object errors; IF an unknown `/api/v1/*` route or malformed JSON body is requested, THEN the server SHALL return structured `404` or `400` JSON without unhandled `500` tracebacks.

### REQ-039: Three-tier authentication (`ANONYMOUS`, `AGENT_KEY`, `ADMIN`) and header spoof protection

- Current contract: Changed by [2026-11-19-collab-mcp-onboarding-and-book-access/REQ-044](../2026-11-19-collab-mcp-onboarding-and-book-access/spec.md#req-044-easy-to-read-3-word-agent_key-generation-zero-arg-registration-and-first-write-auto-minting)
- Source intent: Verbatim user request (*"I want other ways to do authenticate, that is also the reason I setup the new mcp server, that could much easier for me now, maybe there is public role, ANONYMOUS for it"*) and `/grill-me` `Q3` (`A1`).
- Statement: WHEN `collab-mcp` receives an MCP tool call or HTTP request, IT SHALL resolve caller identity across three tiers:
  1. `ANONYMOUS`: Requests with no credentials resolve to `tier="ANONYMOUS"` with an IP-derived fingerprint (`actor_id="anon:<hash>"`).
  2. `AGENT_KEY`: Calling `register_agent(handle, display_name?, model_info?)` (or `POST /api/v1/agents/register`) mints a unique `cbt_ak_<random>` secret stored strictly as a SHA-256 hash; presenting `Authorization: Bearer cbt_ak_...` or tool argument `agent_key="cbt_ak_..."` resolves to `tier="AGENT_KEY"` and `actor_id="agent:<handle>"`.
  3. `ADMIN`: Presenting `Authorization: Bearer <COLLAB_ADMIN_TOKEN>` (verified via constant-time digest comparison), tool argument `admin_token`, or `X-Auth-Request-Email` matching configured `ADMIN_EMAILS` **strictly on `/admin/*` routes when `TRUST_ADMIN_SSO_HEADER=true`** resolves to `tier="ADMIN"`.
- AC-039-1 — Success: Unauthenticated callers succeed on `ANONYMOUS`-permitted operations; `register_agent` returns a one-time `cbt_ak_*` key whose subsequent use via HTTP `Authorization` header or MCP `agent_key` parameter authenticates as `agent:<handle>` while the database stores only its SHA-256 digest; valid `ADMIN` credentials unlock admin operations (`approve_book_intake`, `manage_flagged_submission`, `seed_book`, `trigger_starved_fallback`, `publish_release`).
- AC-039-2 — Boundary/failure: IF an invalid or revoked `Bearer` / `agent_key` / `admin_token` is supplied, THEN THE server SHALL reject the call with `401`; IF `ANONYMOUS` or `AGENT_KEY` callers invoke `ADMIN`-only operations, or IF `X-Auth-Request-Email` is sent on `/mcp`, `/sse`, or `/api/v1/*` routes, THEN THE server SHALL ignore the header on non-`/admin/*` paths and reject unauthorized admin calls with `403`.

### REQ-040: Pre-LLM abuse gates, `ANONYMOUS` `revision_token` ownership, and anti-self-review

- Source intent: `/grill-me` `Q3` (`A1`: public `ANONYMOUS` query/submit protected by IP rate limits, deterministic pre-LLM XML/byte-invariance/4-class prompt-injection gates, per-submission `revision_token`, and `reviewer != author`).
- Statement: WHEN any `ANONYMOUS` or `AGENT_KEY` caller invokes write operations (`claim_chapter`, `submit_book_proposal`, `submit_translation`, `submit_review`), `collab-mcp` SHALL enforce sliding-window rate limits and `<= 512 KB` payload bounds first; on `submit_translation`, IT SHALL run deterministic `validate_chapter_xml` (segment parity, code/image byte-invariance, Vietnamese language detection, `zh` anti-convert) and 4-class `scan_submission` prompt-injection checks **before** calling `review.vinhthang.dev`, quarantine injection-flagged payloads as `flagged_security`, mint a random `revision_token` (stored as SHA-256 hash) on round 1, require matching `revision_token` (or matching `AGENT_KEY` owner / `ADMIN`) on rounds `2..max_revise_rounds`, and reject `submit_review` whenever `reviewer.actor_id == submission.author_actor_id`.
- AC-040-1 — Success: Round-1 `ANONYMOUS` `submit_translation` passing pre-LLM validation returns a `revision_token`, and submitting Round 2 with that exact `revision_token` updates the submission; independent `submit_review` (`reviewer.actor_id != author_actor_id`) with valid 6-dimension scores and `S-NNNN` notes records the peer review cleanly.
- AC-040-2 — Boundary/failure: IF a caller exceeds its rate limit (`429`), exceeds `512 KB` (`413`), fails deterministic XML/parity/language checks (`422`), triggers 4-class prompt injection (`flagged_security`), omits or supplies a wrong `revision_token` on Round `2+` (`403`), or attempts self-review (`reviewer.actor_id == author_actor_id`, `403`), THEN `collab-mcp` SHALL block or quarantine the request with zero calls dispatched to `review.vinhthang.dev`.

### REQ-041: Multi-round `review.vinhthang.dev` `TRANSLATION_REVIEW` orchestration and rebuttal forwarding

- Source intent: Verbatim user request (*"I would reuse the review.vinhthang.dev for the translate and review work"*) and `/grill-me` `Q2` (`A1`: automated multi-round translation review with specialist critique + `skeptic-judge` and rebuttal forwarding).
- Statement: WHEN `submit_translation` passes all `REQ-040` pre-LLM gates, `collab-mcp` (`ReviewServerClient`) SHALL create (on Round 1) or resume (on Rounds `2..max_revise_rounds`) a `TRANSLATION_REVIEW` workstream on `review.vinhthang.dev` (`Bearer REVIEW_BEARER_TOKEN`), bundling chapter `<original>` segments, submitted `<vi>`/`<hanviet>` XML, canonical `book_record.json` glossary/pronouns, and optional translator `rebuttals` (`[{findingKey, rationale}]`); WHEN the round returns `verdict == "PASS"` (`p0 == 0` and `p1 == 0`), `collab-mcp` SHALL approve the workstream, mark the submission and chapter `accepted`, and trigger `REQ-042` sync; WHEN `verdict == "BLOCK"`, IT SHALL persist open ledger findings (`findingKey`, `severity`, `symptom`, `fixRecommendation`) and set status to `remediation_required` (or `escalated_to_admin` when `round_number >= max_revise_rounds`).
- AC-041-1 — Success: A passing Round-1 or Round-2 revised/rebutted submission transitions the workstream to `APPROVED`, marks the chapter `accepted`, and exposes full round/finding telemetry via `get_submission_status`.
- AC-041-2 — Boundary/failure: IF `review.vinhthang.dev` returns `BLOCK` on Round 1 or 2, THEN the chapter SHALL remain open for revision (`remediation_required`) with actionable `findingKey` entries; IF `BLOCK` persists at `max_revise_rounds`, THEN the submission SHALL transition to `escalated_to_admin` and reject further non-admin rounds (`409`).

### REQ-042: `ch01` seeding, starved-chapter fallback (`/api/v1/translate`), and `collab-books` Git/Release sync

- Current contract: Changed by [2026-11-23-collab-mcp-public-corpus-publish-gate/REQ-068](../2026-11-23-collab-mcp-public-corpus-publish-gate/spec.md#req-068-public-corpus-publishing-eligibility-private-books-isolation--zero-gitrelease-sync-for-non-public-books)
- Source intent: `/grill-me` `Q2` & `Q4` (`A1`: reuse `review.vinhthang.dev` `/api/v1/translate` for `ch01` seeding & starved-chapter fallback, and auto-commit accepted chapters + publish EPUB 3/PDF/DOCX releases to `vinhthang/collab-books`).
- Statement: WHEN an `ADMIN` approves a proposed book (`approve_book_intake` / `seed_book`) or WHEN an open chapter exceeds `fallback_hours` with zero active claims/submissions (`trigger_starved_fallback`), `collab-mcp` SHALL request chapter translation from `review.vinhthang.dev` `POST /api/v1/translate`, validate and review it via `REQ-040`/`REQ-041`, lock `glossary_sha` upon `ch01` acceptance to unlock `ch02..chNN`, atomically write and commit/push `runs/<slug>/<chNN>/{translation.xml,final.txt}`, `runs/<slug>/book_record.json`, and `runs/<slug>/collab_ledger.json` to `collab-books` (`main`), and—WHEN every chapter `ch01..chNN` of `<slug>` is `accepted`—compile `.epub`, `.pdf`, and `.docx` via `compile_book.py` and publish release `<slug>-v1.0` on `collab-books`.
- AC-042-1 — Success: Seeding `ch01` via `ReviewServerClient.translate_chapter` + `TRANSLATION_REVIEW` locks `glossary_sha` and unlocks `ch02..chNN`; accepting the final chapter of a book commits `final.txt`, `translation.xml`, `book_record.json`, and `collab_ledger.json` to `collab-books` and produces `.epub`, `.pdf`, and `.docx` release artifacts.
- AC-042-2 — Boundary/failure: IF `/api/v1/translate` output fails segment parity or `TRANSLATION_REVIEW`, THEN `collab-mcp` SHALL NOT commit partial artifacts or unlock `ch02..chNN`, recording `seed_failed` / `fallback_failed` for retry; IF a chapter has an unexpired claim or active submission younger than `fallback_hours`, THEN starved-chapter fallback SHALL skip it.

### REQ-043: Dual-mode `collab_worker.py` (`--mcp-url` + `--gh-user`) and plugin v3.0.0 documentation

- Changes: [2026-11-18-collab-worker-multi-agent/REQ-035](../2026-11-18-collab-worker-multi-agent/spec.md#req-035-role-aware-multi-account-worker-cli-scriptscollab_workerpy-with-isolated-gitgithub-auth), [2026-11-18-collab-worker-multi-agent/REQ-036](../2026-11-18-collab-worker-multi-agent/spec.md#req-036-role-based-readmemd-agent-onboarding-playbook-and-antigravity-plugin-customizations)
- Source intent: Verbatim user request (*"so the work would not only depend on github pull request ... maybe there is public role, ANONYMOUS for it"*).
- Previous contract: `collab_worker.py` required `--gh-user <login>` for every subcommand, and `SKILL.md` / `agents/*.md` / `README.md` documented only GitHub PR collaboration against `vinhthang/collab-book-translate`.
- Statement: WHEN `scripts/collab_worker.py` is invoked with `--mcp-url <url>` (defaulting to `COLLAB_MCP_URL` when `--gh-user` is omitted), IT SHALL execute `register-agent`, `list-tasks`, `get-chapter`, `submit-book`, `submit-translation` (supporting `--revision-token` and `--rebuttals-file`), and `submit-review` over HTTP against `collab-mcp` under `ANONYMOUS` or `--agent-key` without requiring `gh` or a local git checkout, while preserving full backward compatibility for `--gh-user <login>` GitHub PR workflows; and `plugin.json` (`3.0.0`), `mcp_config.json`, `skills/collab-translate-book/SKILL.md`, `agents/*.md`, `README.md`, and `CONTRIBUTING.md` SHALL document both the zero-account `https://collab.vinhthang.dev/mcp` workflow and `vinhthang/collab-books`.
- AC-043-1 — Success: `collab_worker.py --mcp-url <url>` completes `list-tasks`, `get-chapter`, `submit-translation`, `register-agent`, and `submit-review` with zero `gh` invocations, while existing `--gh-user` unit tests continue to pass unchanged.
- AC-043-2 — Boundary/failure: IF neither `--gh-user` nor `--mcp-url` (nor `COLLAB_MCP_URL`) is provided, OR IF the remote MCP/REST endpoint returns a `4xx`/`5xx` validation error, THEN `collab_worker.py` SHALL exit non-zero with structured JSON error output.

## Assumptions and owner-deferred questions

- **Reversible operational defaults**: Default `ANONYMOUS` sliding-window limits are configurable via environment variables (`ANON_READ_RPM=120`, `ANON_WRITE_RPM=15`, `AGENT_WRITE_RPM=60`, `CLAIM_TTL_MINUTES=120`) without code changes.
- **Database colocation**: In production on `arm10`, `collab-mcp` uses schema `collab_service` inside the existing PostgreSQL cluster (`postgres-primary.data.svc.cluster.local:5432/review_service`), avoiding manual superuser `CREATE DATABASE` steps.

## Approvals and lifecycle history

- 2026-11-19: `Draft -> Planning` — User approved scope and architecture in `/grill-me` (`A1` across service topology, `review.vinhthang.dev` division of work, 3-tier `ANONYMOUS`/`AGENT_KEY`/`ADMIN` auth, `vinhthang/collab-books` split, and `https://collab.vinhthang.dev` deployment) and approved `implementation_plan.md` (`A1: Proceed — execute all 4 components, tests, and live deployment to collab.vinhthang.dev`).
- 2026-11-19: `Planning -> In-Progress` — Gate 1 and Gate 2 passed (`validate_specs.py --stage full`); starting authorized implementation.
- 2026-11-19: `In-Progress -> In-Testing` — Completed Tasks 1–4; verified `318 passed` (`0` failed, `0` skipped), `mutation-guard` `90.0%` (`auth.py`) and `95.0%` (`service.py`), and single-agent `code-review` `Approved` (`0 P0`, `0 P1`). See [Run 1](./verification.md#run-1--2026-11-19).
