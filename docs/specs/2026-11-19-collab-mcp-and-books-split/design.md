# Design: Option A Corpus Split & Remote Book Collaboration MCP Server

Sources: [spec.md](./spec.md), [decisions.md](./decisions.md)

## Components

| Requirement / AC | Path and symbol | Responsibility / change |
| --- | --- | --- |
| REQ-037 / AC-037-1, AC-037-2 | `.gitignore`, `config.example.json`, `mcp_config.json`, `tests/fixtures/runs/scandal-in-bohemia/`, `tests/test_collab_completeness.py`, `tests/test_collab_ledger.py` | Untrack root `books/` and `runs/` corpus directories, relocate pilot fixture under `tests/fixtures/runs/`, point `config.example.json` at `<owner>/collab-books`, and add Streamable HTTP `mcp_config.json`. |
| REQ-038 / AC-038-1, AC-038-2 | `scripts/mcp_server/db.py` (`CollabStore`), `scripts/mcp_server/app.py` (`create_app`), `Dockerfile`, `pyproject.toml` | Idempotent PostgreSQL (`collab_service` schema) and SQLite operational store plus Starlette/MCP application serving `/mcp`, `/sse`, `/messages/`, `/api/v1/*`, `/api/health`, `/llms.txt`, and `/` Bookshelf HTML. |
| REQ-039 / AC-039-1, AC-039-2 | `scripts/mcp_server/auth.py` (`AuthContext`, `resolve_auth`, `register_agent_token`) | Resolve `ANONYMOUS`, SHA-256 `AGENT_KEY` (`cbt_ak_*` via `Bearer` or `agent_key`), and constant-time `ADMIN` (`COLLAB_ADMIN_TOKEN` / `admin_token` or `/admin/*` `X-Auth-Request-Email` when `TRUST_ADMIN_SSO_HEADER=true`); strip/ignore `X-Auth-Request-*` on non-`/admin/*` paths. |
| REQ-040 / AC-040-1, AC-040-2 | `scripts/mcp_server/auth.py` (`SlidingWindowRateLimiter`, `verify_revision_token`), `scripts/mcp_server/service.py` (`CollabMcpService.submit_translation`, `submit_review`, `claim_chapter`, `submit_book_proposal`) | Sliding-window per-actor/IP rate limits, `512 KB` body ceiling, deterministic XML/parity/language checks (`validate_chapter_xml`, `enforce_language_detection`) and 4-class `scan_submission` quarantine before LLM calls, SHA-256 `revision_token` ownership on `R2+`, and `reviewer.actor_id != author_actor_id`. |
| REQ-041 / AC-041-1, AC-041-2 | `scripts/mcp_server/review_client.py` (`ReviewServerClient`), `scripts/mcp_server/service.py` (`CollabMcpService.submit_translation`, `get_submission_status`) | Drive `TRANSLATION_REVIEW` workstreams against `review.vinhthang.dev` (`POST /api/v1/workstreams`, `/rounds`, `/governance`), forward translator `rebuttals`, track `R1..max_revise_rounds`, mark `accepted` on `PASS`, or escalate to `escalated_to_admin`. |
| REQ-042 / AC-042-1, AC-042-2 | `scripts/mcp_server/git_sync.py` (`CollabBooksGitSync`), `scripts/mcp_server/service.py` (`approve_book_intake`, `seed_book`, `trigger_starved_fallback`, `publish_release`) | Call `review.vinhthang.dev` `POST /api/v1/translate` + `TRANSLATION_REVIEW` for `ch01` seeding and starved-chapter fallback, lock `glossary_sha` to unlock `ch02..chNN`, commit/push accepted chapters to `collab-books`, and compile/publish `.epub`/`.pdf`/`.docx` releases via `compile_book.py`. |
| REQ-043 / AC-043-1, AC-043-2 | `scripts/collab_worker.py`, `plugin.json`, `skills/collab-translate-book/SKILL.md`, `agents/collab_translator.md`, `agents/collab_reviewer.md`, `README.md`, `CONTRIBUTING.md` | Add `--mcp-url` / `COLLAB_MCP_URL` HTTP client mode (`ANONYMOUS` + `--agent-key` + `--revision-token` + `--rebuttals-file`) alongside `--gh-user`, bump `plugin.json` to `3.0.0`, and document MCP + `collab-books` workflows across all 4 roles. |

## Interfaces and flow

1. **Data Model (`CollabStore` in `scripts/mcp_server/db.py`)**:
   - `agents(handle PK, display_name, model_info, key_sha256 UNIQUE, created_at)`
   - `books(slug PK, title, author, source_lang, target_lang, genre, license, license_url, status, glossary_sha, book_record_json, manifest_json, proposed_by, release_tag, updated_at)`
   - `chapters(book_slug, chapter_id, chapter_num, title, source_xml, status, claimed_by, claim_token_sha256, claim_expires_at, accepted_submission_id, opened_at, updated_at, PRIMARY KEY (book_slug, chapter_id))`
   - `submissions(id PK, book_slug, chapter_id, author_actor_id, author_handle, engine_model, status, round_number, max_rounds, workstream_id, revision_token_sha256, translation_xml, final_txt, additions_json, open_findings_json, security_flags_json, created_at, updated_at)`
   - `peer_reviews(id PK, submission_id, reviewer_actor_id, reviewer_handle, scores_json, weighted_score, issues_json, body_md, created_at)`
   - `rate_limits(bucket_key, ts_epoch)`
2. **MCP Tools & REST Parity (`scripts/mcp_server/service.py` & `app.py`)**:
   - Public / `ANONYMOUS` + `AGENT_KEY`: `register_agent`, `list_books`, `get_book`, `list_tasks`, `get_chapter`, `claim_chapter`, `submit_book_proposal`, `submit_translation`, `get_submission_status`, `submit_review`.
   - `ADMIN`: `approve_book_intake`, `manage_flagged_submission`, `seed_book`, `trigger_starved_fallback`, `publish_release`.
   - Every MCP tool accepts optional `agent_key` / `admin_token` arguments in addition to HTTP `Authorization: Bearer` headers so zero-header `mcp_config.json` clients work across all tiers.
3. **Translation Submission Pipeline**:
   `resolve_auth` -> `rate_limiter.check` -> `<= 512 KB` check -> `validate_chapter_xml` + `enforce_language_detection` -> `scan_submission` (4-class prompt injection) -> `ReviewServerClient.run_translation_round` (`TRANSLATION_REVIEW`) -> on `PASS`: merge `additions`, render `final.txt`, update `collab_ledger.json`, `CollabBooksGitSync.sync_accepted_chapter` (and `publish_book_release` when 100% complete).

## Boundaries and recovery

- **Spoofed SSO headers**: `resolve_auth` ignores `X-Auth-Request-Email` / `X-Auth-Request-User` unless both `request_path.startswith("/admin")` and `TRUST_ADMIN_SSO_HEADER=true`, matching Caddy edge header-stripping invariants.
- **Pre-LLM short-circuiting**: Oversized (`413`), rate-limited (`429`), XML-invalid (`422`), wrong-`revision_token` (`403`), self-review (`403`), and prompt-injection-flagged (`flagged_security`) payloads return structured diagnostics before `ReviewServerClient` sends a single network packet.
- **Git sync resilience**: `CollabBooksGitSync` serializes repository operations under an `asyncio.Lock` / `threading.Lock`, pulls `--ff-only` (or rebases canonical state) before pushing, and retains accepted artifacts in PostgreSQL if GitHub is transiently unreachable so `publish_release` / sync can retry idempotently.

## Dependencies and unresolved design work

None.
