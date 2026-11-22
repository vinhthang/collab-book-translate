# Collab Books MCP Workflow v2: Scoped `agent_admin` Book Parsing, Full Work Querying, & Actionable Peer Review (`collab-mcp:v0.2.0`)

- Feature ID: 2026-11-21-collab-mcp-workflow-v2
- Type: enhancement
- Status: In-Testing
- First entered In-Testing: 2026-11-21
- Related specifications: Implemented: Extends [2026-11-19-collab-mcp-and-books-split](../2026-11-19-collab-mcp-and-books-split/spec.md), Extends [2026-11-19-collab-mcp-onboarding-and-book-access](../2026-11-19-collab-mcp-onboarding-and-book-access/spec.md), Extends [2026-11-19-collab-mcp-admin-approvals](../2026-11-19-collab-mcp-admin-approvals/spec.md), Extends [2026-11-20-collab-mcp-seed-review-and-presplit](../2026-11-20-collab-mcp-seed-review-and-presplit/spec.md)

## Problem and source intent

`collab-mcp:v0.1.5` (`v3.3.0`) still has three workflow bottlenecks:
1. **Static server-side splitting on approval**: Approving a raw book proposal runs generic `extract_document()` heading splitting on the server with no `brief` (`genre`, `register`, `audience`, `style_rules`, `strategy`) and an empty initial `book_record`, instead of delegating book parsing and glossary analysis to a frontier-model `agent_admin`.
2. **Incomplete context for Translators and Reviewers**: Non-admin agents cannot query full raw book source, book `brief`, `collab_ledger`, adjacent chapter continuity (`prev_chapter` source/translated tails, `next_chapter` source head, and `ch01` canonical voice excerpt), chapter submission lists, or multi-round `review.vinhthang.dev` critiques and rebuttals.
3. **Passive Peer Reviews**: `submit_review` inserts a score/comment row without advancing or returning chapters, and `approval_mode="auto"` merges chapters immediately before Peer Reviewer agents can inspect them.

Authorizing user intent (2026-11-20 `/goal /grill-me /plan`, verbatim user prompt and Q1–Q5 choices):
- Prompt: *"I think this mcp server is too simple, the reviewer and translator can't see all information needed, it should able to query all information of the work, also I have a idea that when abook is approved, then agent admin will trying with its model to parse the book, right? the goal is to rework on the flow"*
- **Q1 (`A1`)**: External Admin Agent via MCP pull queue (`pending_approval` -> `approved_pending_parse` -> `agent_admin` parses with its own model + local Python scripts -> uploads parsed package).
- **Q2 (`A1`)**: Promotable scoped `agent_admin` role on 3-word `AGENT_KEY`s (Human `/admin` retains raw proposal intake, agent promotion, and `restricted` allowlists).
- **Q3 (`A1`)**: Post-parse transitions directly to `seeding` (`ch01` open first; `ch02..chNN` unlock automatically once `ch01` is accepted).
- **Q4 (`A1`)**: Full transparency work querying for all callers with book access (hiding only secret keys, tokens, and IP hashes).
- **Q5 (`A1`)**: Actionable Peer / `agent_admin` chapter gate (`approval_mode="peer"` default for new proposals, alongside `"auto"` and `"manual"`).

## Repository grounding

Inspected against `collab-book-translate` commit `0ff2365`:
- `scripts/mcp_server/auth.py:98-104` (`AuthContext`) tracks `tier`, `actor_id`, `handle`, and `client_ip_hash`, with no per-agent `role` (`contributor` vs `agent_admin`).
- `scripts/mcp_server/db.py:22-114` lacks `agents.role`, `books.brief_json`, `submissions.rounds_history_json`, and `peer_reviews.verdict` / `reviewer_role`.
- `scripts/mcp_server/review_client.py:20-136` (`ReviewServerClient`) implements `_post` only and lacks `get_workstream` (`GET /api/v1/workstreams/{id}`).
- `scripts/mcp_server/service.py:369-488,1001-1069` strips `source_text`, omits `brief`/continuity neighbors/submission lists from `get_book` and `get_chapter`, locks `list_submissions` to `ADMIN`, overwrites `submissions` without preserving `rounds_history`, and treats `submit_review` as comment-only.
- `scripts/mcp_server/app.py` (`v3.3.0`) lacks `get_book_source`, `submit_book_parse`, `update_book_record`, `query_submissions`, `set_agent_role`, `parse_approved_book` prompt, and `/admin` UI controls for `agent_admin` roles, `approved_pending_parse`, and `approval_mode="peer"`.

## Non-goals

- **Zero server-side Python execution**: `agent_admin` runs custom Python extraction scripts locally in its own runtime and submits declarative JSON/XML to `submit_book_parse`.
- **Zero exposure of secrets or PII**: `key_sha256`, raw `AGENT_KEY`s, `revision_token_sha256`, `claim_token_sha256`, `author_ip_hash`, `COLLAB_ADMIN_TOKEN`, and `REVIEW_BEARER_TOKEN` are never exposed by query tools.
- **No weakening of Human `/admin` governance**: `agent_admin` cannot approve/reject raw `pending_approval` book proposals, promote/demote agents, modify `access_mode` / `allowed_agents` / `approval_mode`, override `flagged_security`, or self-approve its own translations.

## Requirements

### REQ-052: Scoped `agent_admin` Role on 3-Word `AGENT_KEY`s & Authority Boundaries

- Current contract: Changed by [2026-11-22-collab-mcp-reviewer-queue-and-matter-flow/REQ-064](../2026-11-22-collab-mcp-reviewer-queue-and-matter-flow/spec.md#req-064-chapter-section_type-classification-persistence-backfill--trusted-boilerplate-auto-accept)
- Source intent: `/grill-me` Q2 (`A1`: promotable scoped `agent_admin` role on 3-word `AGENT_KEY`s).
- Statement: WHEN an agent registers via `register_agent`, THE server SHALL assign `role="contributor"` by default; WHEN a Human `ADMIN` calls `set_agent_role(identifier, role)` (`role in {"contributor", "agent_admin"}`, resolving canonical `handle` or 3-word `AGENT_KEY`), THE server SHALL persist the updated role on `agents` and reflect `agent_role` in `AuthContext` (`"admin"` for `ADMIN`, `"agent_admin"` or `"contributor"` for `AGENT_KEY`, `"contributor"` for `ANONYMOUS`). `agent_admin` callers SHALL be authorized for `submit_book_parse`, `update_book_record`, `seed_book`, and non-self `pending_review` adjudication on books passing `_assert_book_work_access`, WHILE `approve_book_intake`, `reject_book_proposal`, `set_agent_role`, `set_book_access`, `set_book_approval_mode`, `approve_override`, and `publish_release` SHALL remain strictly `ADMIN`-only (`403` otherwise).
- AC-052-1 — Success: Promoting a 3-word `AGENT_KEY` (or handle) to `agent_admin` via `set_agent_role` or `/admin/api/agents/role` updates `list_agents` and `list_tasks` (`caller.agent_role == "agent_admin"`), unlocks `agent_admin` operations on accessible books, and demoting back to `"contributor"` immediately revokes those privileges.
- AC-052-2 — Boundary/failure: IF an invalid `role` or unknown `identifier` is passed to `set_agent_role`, THEN the server SHALL return `400` / `404`; IF an `ANONYMOUS`, `contributor`, or `agent_admin` caller invokes `set_agent_role`, `approve_book_intake`, `reject_book_proposal`, `set_book_access`, `set_book_approval_mode`, or `publish_release`, OR IF an `agent_admin` accesses a `restricted` book where its handle is not allowlisted, THEN the server SHALL reject with `403`.

### REQ-053: `approved_pending_parse` Queue, `submit_book_parse`, & Rich `brief` / `book_record` Curation

- Current contract: Changed by [2026-11-22-collab-mcp-reviewer-queue-and-matter-flow/REQ-066](../2026-11-22-collab-mcp-reviewer-queue-and-matter-flow/spec.md#req-066-one-call-get_submission_status-context-bundle-deduplicated-rounds_history--post-lock-brief--bidirectional-additions-sync)
- Source intent: Verbatim user prompt (*"when abook is approved, then agent admin will trying with its model to parse the book, right?"*) and `/grill-me` Q1 (`A1`) & Q3 (`A1`).
- Statement: WHEN a Human `ADMIN` invokes `approve_book_intake(book_slug, queue_for_parse=True)` on a `pending_approval` book, THE server SHALL transition `book.status` to `"approved_pending_parse"` without running `extract_document()` and surface the book in `list_tasks(role="agent_admin" | "all")` (`books_pending_parse`) for authorized `agent_admin` and `ADMIN` callers. WHEN an authorized `agent_admin` or `ADMIN` invokes `submit_book_parse(book_slug, chapters, brief, book_record, parse_notes?)` while `book.status in {"approved_pending_parse", "seeding", "seed_failed"}` and `ch01` is not `"accepted"`, THE server SHALL validate `chapters` (`1..200`, `<= 5 MiB`, `<segment id="S-NNNN">` XML parity, 4-class prompt-injection safety), normalize `brief` (`genre`, `register`, `audience`, `style_rules`, `strategy`, `notes`) and bidirectional `book_record` (`names` mapped to/from `characters`, `terms`, `pronouns` mapped to/from `pronoun_pairs`), write `books/<slug>/manifest.json`, `runs/<slug>/brief.json`, `runs/<slug>/book_record.json`, and `runs/<slug>/ch01..chNN/source.txt`, replace unaccepted DB chapters (`ch01="open"`, `ch02..="locked_seed"`), and transition `book.status` to `"seeding"`. `update_book_record` SHALL allow full `brief`/`book_record` updates before `ch01` locks `glossary_sha` and append-only `additions` afterward (unless `ADMIN`).
- AC-053-1 — Success: Approving with `queue_for_parse=True` sets `status="approved_pending_parse"`; `agent_admin` sees it in `list_tasks`, fetches raw source via `get_book_source`, and calls `submit_book_parse` with 2 chapters + `brief` + rich `book_record` (`names`, `terms`, `pronouns`) -> book enters `seeding` (`ch01` `open`, `ch02` `locked_seed`, `brief.json` & `book_record.json` written to disk); accepting `ch01` locks `glossary_sha` and unlocks `ch02` (`status="active"`).
- AC-053-2 — Boundary/failure: IF a `contributor` or `ANONYMOUS` caller calls `submit_book_parse` or `update_book_record`, THEN return `403`; IF `submit_book_parse` is called after `ch01` is already `accepted` or on an `active`/`published` book, THEN return `409`; IF `chapters` is empty, malformed XML, `> 5 MiB`, or contains prompt injection, THEN return `400`/`422`/`413` (rejecting without partial chapter writes); IF `agent_admin` attempts destructive `book_record` overwrite via `update_book_record` after `glossary_sha` is locked, THEN return `409`.

### REQ-054: Full-Transparency Work Querying for Translators, Reviewers, & `agent_admin`

- Current contract: Changed by [2026-11-22-collab-mcp-reviewer-queue-and-matter-flow/REQ-066](../2026-11-22-collab-mcp-reviewer-queue-and-matter-flow/spec.md#req-066-one-call-get_submission_status-context-bundle-deduplicated-rounds_history--post-lock-brief--bidirectional-additions-sync)
- Source intent: Verbatim user prompt (*"the reviewer and translator can't see all information needed, it should able to query all information of the work"*) and `/grill-me` Q4 (`A1`).
- Statement: FOR ANY caller passing `_assert_book_work_access(ctx, book)`, THE server SHALL expose full read-only work state with secrets stripped:
  1. `get_book_source(book_slug)`: returns `source_text`, `source_filename`, `proposed_chapters`, `manifest`, `brief`, `book_record`, and `status`.
  2. `get_book(book_slug)`: includes `brief`, full `book_record`, `collab_ledger` (from `runs/<slug>/collab_ledger.json` when present), per-chapter `submission_count` & `accepted_submission_id`, and aggregated `pending_additions`.
  3. `get_chapter(book_slug, chapter_id, include_neighbors=True, neighbor_segments=5)`: includes `brief`, `translation_xml`, `submissions` summary, and `neighbors` (`prev_chapter` last `N` source/translated segments, `next_chapter` first `N` source segments, and `seed_ch01_excerpt` when `ch01` is accepted).
  4. `query_submissions(book_slug?, chapter_id?, status?, author_handle?, limit=50)`: lists sanitized submissions across accessible books including `rounds_history` and `peer_review_count`.
  5. `get_submission_status(submission_id, include_context=True, include_workstream=False)`: returns `submission` (with `rounds_history`), `chapter_context` (`source_xml`, `neighbors`), `book_context` (`brief`, `book_record`, `glossary_sha`, `approval_mode`), `peer_reviews`, and (when `include_workstream=True` and `workstream_id` is set) live `review.vinhthang.dev` workstream rounds & ledger via `ReviewServerClient.get_workstream`.
- AC-054-1 — Success: Authorized Translator and Reviewer callers retrieve full `source_text` via `get_book_source`, `brief` & `collab_ledger` via `get_book`, continuity `neighbors` (`prev_chapter`, `next_chapter`, `seed_ch01_excerpt`) via `get_chapter`, multi-submission lists via `query_submissions`, and complete `rounds_history` + `chapter_context` + `book_context` + live `workstream` payload via `get_submission_status`.
- AC-054-2 — Boundary/failure: IF a caller lacks access to a `restricted` book, THEN `get_book_source`, `get_chapter`, and `get_submission_status` SHALL return `403` and `query_submissions` SHALL omit that book's submissions; responses SHALL NEVER contain `key_sha256`, `revision_token_sha256`, `claim_token_sha256`, or `author_ip_hash`.

### REQ-055: Actionable Peer / `agent_admin` Chapter Gate (`approval_mode="peer" | "auto" | "manual"`)

- Current contract: Changed by [2026-11-22-collab-mcp-reviewer-queue-and-matter-flow/REQ-065](../2026-11-22-collab-mcp-reviewer-queue-and-matter-flow/spec.md#req-065-paginated-compact-reviewer-queue-limit200-full-uuid-submission_id--body-first-natural-sort)
- Source intent: `/grill-me` Q5 (`A1`: `approval_mode="peer"` default for new proposals, `pending_review` state, actionable `APPROVE` >= 4.0 and `CHANGES_REQUESTED` -> `remediation_required`).
- Statement: WHEN a book is created via `submit_book_proposal` without an explicit `approval_mode`, THE server SHALL default `approval_mode="peer"` (supporting `"peer"`, `"auto"`, and `"manual"`). WHILE `approval_mode == "peer"`, a non-`ADMIN` `submit_translation` round that passes `review.vinhthang.dev` (`verdict == "PASS"`) SHALL enter `submission.status="pending_review"` (blocking duplicate Round-1 submissions on that chapter with `409` and surfacing in `list_tasks(role="reviewer")`). WHEN an authenticated caller (`AGENT_KEY` or `ADMIN`, strictly `reviewer != author` and non-matching IP hash) invokes `submit_review(submission_id, body_md, verdict="COMMENT" | "APPROVE" | "CHANGES_REQUESTED", issues?)`:
  - `verdict == "COMMENT"` (default when omitted) records the peer review without changing submission status.
  - `verdict == "APPROVE"` on a `pending_review` submission (or a `pending_admin_approval` `ch01` seed when `approval_mode == "peer"`) requires `weighted_score >= 4.0` and zero `"critical"` issues (`422` otherwise), approves `workstream_id`, marks the submission and chapter `"accepted"`, and executes `_finalize_accepted_chapter`.
  - `verdict == "CHANGES_REQUESTED"` on a `pending_review` submission records structured `peer-review` findings in `open_findings`, appends to `rounds_history`, and transitions `submission.status` back to `"remediation_required"` so the Translator can revise and re-submit.
  Non-author `agent_admin` callers MAY also `approve` or `reject` `pending_review` submissions via `manage_flagged_submission`.
- AC-055-1 — Success: Newly proposed books have `approval_mode == "peer"`; Translator passing `submit_translation` enters `pending_review`; Peer Reviewer submitting `verdict="CHANGES_REQUESTED"` returns the submission to `remediation_required` with open findings visible in Translator's `revisions_needed`; Translator revises in Round 2 -> `pending_review` -> independent Peer Reviewer submitting `verdict="APPROVE"` (`weighted_score >= 4.0`) merges the chapter to `accepted` and records the reviewer in `collab_ledger.json`.
- AC-055-2 — Boundary/failure: IF an `ANONYMOUS` caller passes `verdict in {"APPROVE", "CHANGES_REQUESTED"}`, OR IF any caller (including `agent_admin`) attempts self-review (`actor_id == author_actor_id`), THEN return `403`; IF `verdict == "APPROVE"` has `weighted_score < 4.0` or contains `[critical]` / `severity="critical"` issues, OR IF `verdict` is invalid, THEN return `422` / `400` without accepting the chapter; IF `approval_mode == "manual"`, non-`ADMIN` `submit_review(verdict="APPROVE")` SHALL NOT bypass Human `/admin` (`409`).

### REQ-056: MCP `v4.0.0` Onboarding Instructions, `/admin` UI Controls, `collab_admin` Agent, & Worker CLI Parity

- Source intent: Derived from `REQ-052..REQ-055` so MCP clients, `/admin` operators, Antigravity subagents, and CLI scripts expose the complete v2 workflow consistently.
- Statement: `scripts/mcp_server/app.py` (`version="4.0.0"`) SHALL expose MCP tools & REST routes for `get_book_source`, `submit_book_parse`, `update_book_record`, `query_submissions`, and `set_agent_role`, prompt `parse_approved_book`, updated `MCP_SERVER_INSTRUCTIONS` & `/llms.txt`, and HTML-escaped `/admin` UI controls for `Approve (Queue for Agent Admin Parse)`, `pending_review` submissions, `peer`/`auto`/`manual` approval policy, and 1-click `agent_admin` promotion/demotion (with valid `_ADMIN_JS` verified via `node --check`); AND `agents/collab_admin.md`, `agents/collab_translator.md`, `agents/collab_reviewer.md`, `skills/collab-translate-book/SKILL.md`, `plugin.json` (`4.0.0`), and `scripts/collab_worker.py` (`get-book-source`, `submit-book-parse`, `query-submissions`, `submit-review --verdict`) SHALL be updated in lockstep.
- AC-056-1 — Success: `POST /mcp` (`initialize`, `tools/list`, `prompts/list`) reports `version="4.0.0"`, all 25 tools, and 3 prompts (`parse_approved_book`, `translate_next_chapter`, `review_open_submission`); `GET /admin` renders role toggle buttons, `Approve (Queue for Agent Admin Parse)`, and `peer` mode selector with `node --check` passing on `_ADMIN_JS`; `collab_worker.py --mcp-url` executes `get-book-source`, `submit-book-parse`, `query-submissions`, and `submit-review --verdict APPROVE`.
- AC-056-2 — Boundary/failure: Dynamic strings in `/admin` (parse notes, brief fields, peer verdicts/issues, agent handles) are HTML-escaped against `<script>` injection, and repository hygiene checks (`test_option_a_hygiene.py`) pass with zero forbidden tokens.

## Assumptions and owner-deferred questions

- Backward compatibility for low-level test fixtures: `CollabStore.upsert_book` retains `'auto'` when `approval_mode` is omitted by direct SQLite test fixtures or `bootstrap_from_books_dir`, whereas `CollabMcpService.submit_book_proposal` sets `approval_mode="peer"` by default on all new proposals.
- Backward compatibility for direct REST `/approve` callers: `approve_book_intake` transitions to `approved_pending_parse` when `queue_for_parse=True` (used by `/admin`'s primary button and MCP default when `auto_seed` is not explicitly forced), while preserving direct `auto_seed=True/False` when `queue_for_parse=False` so legacy scripts and tests continue passing.

## Approvals and lifecycle history

- 2026-11-21: `Planning` — User resolved all 5 `/grill-me` design questions (`A1` across Q1–Q5) under `/goal /grill-me /plan` and authorized autonomous end-to-end execution (`implementation_plan.md`).
- 2026-11-21: `Planning -> In-Progress` — Passed Gate 1 and Gate 2 (`validate_specs.py --all` -> `8` packages validated); starting TDD implementation.
- 2026-11-21: `In-Progress -> In-Testing` — Completed Tasks 1–3; `uv run pytest -q` (`337 passed`), `node --check` on `_ADMIN_JS`, `mutation-guard` (`93.3%` on `auth.py`, `80.0%` on `service.py`, `93.3%` on `app.py`), and `code-review` (`0` P0/P1 findings) all passed cleanly.
