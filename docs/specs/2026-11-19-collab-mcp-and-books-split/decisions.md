# Decisions: Option A Corpus Split & Remote Book Collaboration MCP Server

Governing specification: [spec.md](./spec.md)

## Architectural rationale

- Governs: REQ-037, REQ-038, REQ-039, REQ-040, REQ-041, REQ-042, REQ-043

Separating the mutable book corpus (`vinhthang/collab-books`) from the translation engine, MCP service, and Antigravity plugin (`vinhthang/collab-book-translate`) eliminates git history bloat and merge contention between engine releases and chapter commits (`REQ-037`). Reusing `collab-book-translate`'s battle-tested Python validators (`validate_chapter_xml`, `scan_submission`, `enforce_language_detection`, `compile_book.py`) inside a Python 3.12 MCP service guarantees 100% deterministic parity between MCP submissions and GitHub PR submissions (`REQ-040`, `REQ-042`, `REQ-043`).

## ADR-029: Standalone `collab-mcp` service delegating LLM review and fallback translation to `review.vinhthang.dev`

- Governs: REQ-038, REQ-041, REQ-042
- State: Accepted
- Context and constraints: `review.vinhthang.dev` (`review-server`, TypeScript/Fastify on `arm10`) already hosts serialized `Gemini` (`agy`) and `DeepSeek-V3`/`DeepSeek-R1` drivers, multi-round `RoundFSM` workstreams, and `skeptic-judge` arbitration, behind strict `Bearer` / SSO authentication. Meanwhile, all XML segment extraction, Sino-Vietnamese `<hanviet>` validation, 4-class prompt-injection scanning, and EPUB 3/PDF/DOCX book compilation live in Python inside `collab-book-translate`.
- Viable options:
  1. **Option 1 (Standalone `collab-mcp` Python service + server-to-server `review.vinhthang.dev`)**: Run `collab-mcp` as an isolated Python 3.12 ASGI service (`https://collab.vinhthang.dev`) with public `ANONYMOUS` access and deterministic Python pre-LLM gates, invoking `review.vinhthang.dev` (`POST /api/v1/workstreams` with `TRANSLATION_REVIEW` and `POST /api/v1/translate`) over authenticated server-to-server `Bearer` calls.
  2. **Option 2 (Embed book MCP & public `ANONYMOUS` routes directly inside `review-server`)**: Rewrite XML/glossary/EPUB/PDF/DOCX pipelines in TypeScript or shell out to Python inside `review-server`, and punch `ANONYMOUS` holes into `review.vinhthang.dev`'s auth middleware.
- Decision: Choose **Option 1**.
- Costs and risks: Introduces one lightweight container (`collab-mcp`, `128Mi–512Mi` RAM on `arm10`) and internal HTTP calls to `review-server`; mitigated by cluster-local routing, deterministic pre-LLM validation blocking invalid payloads before any `review-server` call, and shared `review-server-secrets` (`REVIEW_BEARER_TOKEN`).
- Rejected alternatives: **Option 2** was rejected because duplicating `weasyprint`/EPUB/XML pipelines in Node.js bloats `review-server` and mixing public `ANONYMOUS` routes into `review.vinhthang.dev` widens the blast radius around host-mounted `agy` credentials.
- Affected components: `scripts/mcp_server/app.py`, `scripts/mcp_server/review_client.py`, `Dockerfile`.

## ADR-030: Three-tier authentication with per-submission `revision_token` and dual header/argument credentials

- Governs: REQ-039, REQ-040, REQ-043
- State: Accepted
- Context and constraints: Requiring every participating AI agent to hold a GitHub collaborator account or static OAuth client prevents zero-setup community agent participation, and Antigravity plugin `mcp_config.json` Streamable HTTP entries (`{"url": "https://collab.vinhthang.dev/mcp"}`) connect initially without custom user-specific HTTP headers.
- Viable options:
  1. **Option 1 (3-Tier `ANONYMOUS` + self-serve `AGENT_KEY` + `ADMIN` with `revision_token` & dual header/tool-arg transport)**: Allow `ANONYMOUS` reads, claims, queued book proposals, peer reviews (`reviewer != author`), and translations returning a SHA-256-backed `revision_token` for `R2`/`R3`; offer self-serve `register_agent` (`cbt_ak_*`) accepted via `Authorization: Bearer` OR tool argument `agent_key`; restrict intake/security/release approvals to `ADMIN` (`COLLAB_ADMIN_TOKEN` or Caddy SSO strictly on `/admin/*`).
  2. **Option 2 (Mandatory OAuth2 / GitHub PAT on `/mcp`)**: Require every MCP caller to complete interactive OAuth2 or attach a GitHub PAT before listing tasks or submitting chapters.
- Decision: Choose **Option 1**.
- Costs and risks: Public `ANONYMOUS` write endpoints could be targeted by spam or prompt injection; mitigated by sliding-window IP rate limits, `512 KB` body caps, deterministic XML/language/byte-invariance rejection, and 4-class prompt-injection quarantine **before** any `review.vinhthang.dev` LLM execution, plus `revision_token` preventing third parties from hijacking an in-flight submission.
- Rejected alternatives: **Option 2** was rejected because it reintroduces credential friction and breaks zero-config `mcp_config.json` onboarding.
- Affected components: `scripts/mcp_server/auth.py`, `scripts/mcp_server/service.py`, `scripts/collab_worker.py`, `mcp_config.json`.

## ADR-031: PostgreSQL `collab_service` operational store paired with atomic Git/Release sync to `collab-books`

- Governs: REQ-037, REQ-038, REQ-042
- State: Accepted
- Context and constraints: Concurrent MCP agents claiming chapters, submitting `R1..R3` drafts, and recording rate-limit counters need transactional ACID locking, while readers and downstream tools expect clean canonical files (`book_record.json`, `final.txt`, `collab_ledger.json`) and downloadable `.epub`/`.pdf`/`.docx` GitHub Releases on `vinhthang/collab-books`.
- Viable options:
  1. **Option 1 (PostgreSQL `collab_service` schema for live state + Git auto-sync of accepted chapters/releases to `collab-books`)**: Persist high-frequency claims, drafts, tokens, rounds, and rate limits in PostgreSQL (`collab_service` schema, with SQLite parity in tests), and commit/push only verified `accepted` chapters and compiled releases to `vinhthang/collab-books`.
  2. **Option 2 (Git-branch-per-draft storage with no database)**: Commit every `ANONYMOUS` draft, claim, and rate-limit event as git branches/commits on `vinhthang/collab-books`.
- Decision: Choose **Option 1**.
- Costs and risks: Requires reconciling initial `collab-books` state on startup and serializing git pushes; mitigated by an async per-repo git sync lock inside `CollabBooksGitSync` and idempotent startup import from `books/` + `runs/`.
- Rejected alternatives: **Option 2** was rejected because git push contention under concurrent MCP agents is slow and pollutes `vinhthang/collab-books` with unverified `ANONYMOUS` drafts.
- Affected components: `scripts/mcp_server/db.py`, `scripts/mcp_server/git_sync.py`.

## Gate 1: normative review

| Check | Status | Evidence / required correction |
| --- | --- | --- |
| Codebase grounding | PASS | Grounded against `collab-book-translate` (`45b3645`): cited `scripts/collab_config.py`, `scripts/collab_pr_validate.py`, `scripts/collab_verdict.py`, `scripts/compile_book.py`, `tests/test_collab_completeness.py:101`, and `tests/test_collab_ledger.py:189`. |
| Attributable intent and explicit scope | PASS | Every requirement traces to verbatim user requests and `/grill-me` `A1` decisions recorded on 2026-11-19; non-goals explicitly exclude dropping PR support, direct LLM keys in `collab-mcp`, and unauthenticated admin actions. |
| Observable requirements and meaningful acceptance criteria | PASS | `REQ-037`..`REQ-043` use EARS statements and pair deterministic success criteria (`AC-037-1`..`AC-043-1`) with explicit boundary/failure criteria (`AC-037-2`..`AC-043-2`). |
| Assumptions and owner-deferred questions are explicit | PASS | Zero `[PROVISIONAL]` or unowned `TODO`/`TBD` items remain; configurable rate-limit defaults and `collab_service` schema colocation are documented under `## Assumptions and owner-deferred questions`. |
| Architectural rationale respects requirements | PASS | `ADR-029`, `ADR-030`, and `ADR-031` evaluate genuine multi-option tradeoffs and govern `REQ-037`..`REQ-043` without weakening any requirement invariant. |
| IDs, references, and revision route are consistent | PASS | Allocates fresh repository-wide `REQ-037`..`REQ-043` and `ADR-029`..`ADR-031` above `REQ-036`/`ADR-028` via a linked delta/new-feature package without editing frozen `In-Testing` parent `REQ` bodies in place. |
