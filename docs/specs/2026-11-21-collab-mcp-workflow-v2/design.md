# Design: Collab Books MCP Workflow v2 (`collab-mcp:v0.2.0`)

Sources: [spec.md](./spec.md), [decisions.md](./decisions.md)

## Components

| Requirement / AC | Path and symbol | Responsibility / change |
| --- | --- | --- |
| REQ-052 / AC-052-1, AC-052-2 | `scripts/mcp_server/auth.py` (`AuthContext`, `resolve_auth`, `assert_agent_admin_or_admin`) | Add `agent_role` (`"admin"` for `ADMIN`, DB `role` for `AGENT_KEY`, `"contributor"` for `ANONYMOUS`) and enforce scoped `agent_admin` vs `ADMIN` guards. |
| REQ-052..REQ-055 / AC-052-1..AC-055-2 | `scripts/mcp_server/db.py` (`CollabStore.init_db`, `set_agent_role`, `set_book_brief_and_record`, `replace_unaccepted_chapters`, `create_submission`, `update_submission`, `add_peer_review`) | Add idempotent columns `agents.role`, `books.brief_json`, `submissions.rounds_history_json`, `peer_reviews.verdict`, and `peer_reviews.reviewer_role`, plus atomic chapter replacement and `approval_mode` persistence. |
| REQ-053, REQ-054 / AC-053-1, AC-054-1 | `scripts/mcp_server/review_client.py` (`ReviewServerClient._get`, `get_workstream`), `scripts/mcp_server/git_sync.py` (`prepare_book_files`, `sync_accepted_chapter`, `read_ledger`) | Fetch live `review.vinhthang.dev` workstream rounds & ledger (`GET /api/v1/workstreams/{id}`), persist `runs/{slug}/brief.json`, and read `runs/{slug}/collab_ledger.json`. |
| REQ-052..REQ-055 / AC-052-1..AC-055-2 | `scripts/mcp_server/service.py` (`CollabMcpService.set_agent_role`, `approve_book_intake`, `submit_book_parse`, `update_book_record`, `get_book_source`, `get_book`, `get_chapter`, `query_submissions`, `get_submission_status`, `submit_translation`, `submit_review`, `manage_flagged_submission`, `list_tasks`) | Implement `approved_pending_parse` queue, `submit_book_parse` & `update_book_record`, full-transparency queries with continuity neighbors & `rounds_history`, and actionable `approval_mode="peer"` (`pending_review`, `APPROVE` >= 4.0, `CHANGES_REQUESTED` -> `remediation_required`). |
| REQ-056 / AC-056-1, AC-056-2 | `scripts/mcp_server/app.py`, `scripts/collab_worker.py`, `agents/collab_admin.md`, `agents/collab_translator.md`, `agents/collab_reviewer.md`, `skills/collab-translate-book/SKILL.md`, `plugin.json` | Bump version `"4.0.0"`, expose 25 MCP tools + 3 prompts + offloaded REST routes, upgrade `/admin` UI & `_ADMIN_JS` (`node --check`), add `collab_admin.md`, and extend `--mcp-url` CLI commands. |

## Interfaces and flow

1. **`agent_admin` Promotion & Book Parse Flow (`REQ-052`, `REQ-053`)**:
   - Human `ADMIN` promotes an agent via `set_agent_role(identifier, role="agent_admin")` (`POST /admin/api/agents/role` or `/api/v1/admin/agents/role`).
   - Human `ADMIN` clicks **Approve (Queue for Agent Admin Parse)** (`approve_book_intake(book_slug, queue_for_parse=True)`) -> `book.status = "approved_pending_parse"`.
   - `agent_admin` calls `list_tasks(role="agent_admin")` (`books_pending_parse`), fetches `get_book_source(book_slug)`, parses chapters + `brief` + `book_record` locally, and calls `submit_book_parse(book_slug, chapters, brief, book_record, parse_notes)`.
   - Server validates chapters (`1..200`, `<= 5 MiB`, XML parity, 4-class injection scan), writes `manifest.json`, `brief.json`, `book_record.json`, and `ch01..chNN/source.txt`, replaces unaccepted chapters (`ch01="open"`, `ch02..="locked_seed"`), and sets `book.status = "seeding"`.
2. **Full-Transparency Work Querying (`REQ-054`)**:
   - `get_chapter` extracts `prev_chapter` tail (`neighbor_segments` source + translated segments), `next_chapter` head (`neighbor_segments` source segments), `seed_ch01_excerpt` (up to 5 accepted segments of `ch01`), and sanitized chapter `submissions` summaries.
   - `get_submission_status` returns `submission` (`rounds_history` included), `chapter_context`, `book_context`, `peer_reviews`, and optional live `workstream` details from `review_client.get_workstream`.
3. **Actionable Peer / `agent_admin` Chapter Gate (`REQ-055`)**:
   - New proposals via `submit_book_proposal` default `approval_mode="peer"`.
   - On `review.vinhthang.dev` `PASS` under `"peer"`, non-`ADMIN` submissions enter `pending_review`.
   - Independent `AGENT_KEY` or `ADMIN` callers (`reviewer != author`) calling `submit_review(..., verdict="APPROVE")` with `weighted_score >= 4.0` and zero `"critical"` issues approve `workstream_id` and finalize the chapter to `accepted`; `verdict="CHANGES_REQUESTED"` writes structured `peer-review` findings into `open_findings`, appends to `rounds_history`, and transitions the submission to `remediation_required`.

## Boundaries and recovery

- **Immutability After `ch01` Acceptance**: `submit_book_parse` and destructive `update_book_record` overwrites return `409` once `ch01` is `accepted` (`glossary_sha` locked); additive `additions` remain allowed.
- **Secret & XSS Isolation**: `_sanitize_submission_public` and book/agent serializers never emit secret hashes, raw `AGENT_KEY`s, or `author_ip_hash`; all `/admin` HTML strings pass through `_esc` and `_ADMIN_JS` is verified via `node --check`.

## Dependencies and unresolved design work

None.
