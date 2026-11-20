# Decisions: Collab Books MCP Workflow v2 (`collab-mcp:v0.2.0`)

Governing specification: [spec.md](./spec.md)

## Architectural rationale

- Governs: REQ-052, REQ-054, REQ-056

Scoped `agent_admin` roles on hashed 3-word `AGENT_KEY`s (`REQ-052`) allow individual frontier agents to be promoted and audited by handle without sharing `COLLAB_ADMIN_TOKEN` or granting raw book-proposal / allowlist privileges. Full-transparency read endpoints (`REQ-054`) enforce `_assert_book_work_access` while stripping secret hashes and origin IP fingerprints so Translators and Reviewers have complete book, neighbor, and multi-round critique visibility (`REQ-056`).

## ADR-035: External `agent_admin` Pull-Queue Book Parsing and Actionable `pending_review` Peer Gate

- Governs: REQ-053, REQ-055
- State: Accepted
- Context and constraints: Raw public-domain and permissive books contain diverse Gutenberg wrappers, tables of contents, footnotes, and multi-level chapter headings, and require a book-wide `analyze` pass (`brief` + `names`, `terms`, `pronouns`) before chapter translation begins. Running arbitrary Python scripts inside `collab-mcp` is unacceptable for cluster security (`ADR-034`), while synchronous server-side LLM parsing on `/approve` lacks iterative Python script tooling and hits Cloudflare HTTP timeouts on large books. Similarly, immediate `auto` merge on `review.vinhthang.dev` `PASS` prevents independent Peer Reviewer agents from validating literary quality or requesting revisions before Git commit.
- Viable options:
  1. **Option 1 (Pull-based `approved_pending_parse` queue for `agent_admin` + `approval_mode="peer"` `pending_review` gate)**: Human `/admin` approval moves a proposal into `approved_pending_parse`. Promoted `agent_admin` agents pull `get_book_source`, run their own model + local Python scripts, and push validated `chapters`, `brief`, and `book_record` via `submit_book_parse`, transitioning the book to `seeding` (`ch01` open first, unlocking `ch02..chNN` once `ch01` is accepted). Passing translations on `"peer"` books enter `pending_review`, where an independent Peer Reviewer (`verdict="APPROVE"`, score `>= 4.0`, zero critical issues), non-author `agent_admin`, or Human `ADMIN` merges to `accepted`, and `CHANGES_REQUESTED` returns the draft to `remediation_required`.
  2. **Option 2 (Server-side LLM book splitter on `review.vinhthang.dev` + comment-only peer reviews)**: Trigger a fixed prompt on `review-server` during `/approve` and keep `submit_review` purely informational.
- Decision: Choose **Option 1**.
- Costs and risks: Adds two explicit lifecycle states (`book.status="approved_pending_parse"` and `submission.status="pending_review"`) and requires storing `brief_json`, `rounds_history_json`, and `peer_reviews.verdict`; mitigated by keeping `"auto"` and `"manual"` per-book toggles available in `/admin` and preserving `queue_for_parse=False` fallback for legacy callers.
- Rejected alternatives: **Option 2** was rejected by the owner in `/grill-me` Q1 & Q5 because external `agent_admin` agents have local Python script execution and user-selected models, and comment-only peer reviews cannot catch defects before auto-merge.
- Affected components: `scripts/mcp_server/auth.py`, `scripts/mcp_server/db.py`, `scripts/mcp_server/review_client.py`, `scripts/mcp_server/git_sync.py`, `scripts/mcp_server/service.py`, `scripts/mcp_server/app.py`, `scripts/collab_worker.py`.

## Gate 1: normative review

| Check | Status | Evidence / required correction |
| --- | --- | --- |
| Codebase grounding | PASS | Grounded against `collab-book-translate` commit `0ff2365` (`auth.py:98-104`, `db.py:22-114`, `review_client.py:20-136`, `service.py:369-488,1001-1069`, `app.py`). |
| Attributable intent and explicit scope | PASS | Every requirement traces directly to the user's 2026-11-20 `/goal /grill-me /plan` prompt and explicit Q1–Q5 (`A1`) answers, with clear security/governance non-goals. |
| Observable requirements and meaningful acceptance criteria | PASS | `REQ-052..REQ-056` define observable state transitions, HTTP status codes (`200`/`201`/`202`/`400`/`403`/`404`/`409`/`413`/`422`), and both success (`AC-*-1`) and boundary/failure (`AC-*-2`) criteria. |
| Assumptions and owner-deferred questions are explicit | PASS | Zero owner-deferred `[PROVISIONAL]` items remain; backward-compatibility defaults (`upsert_book` `'auto'` vs `submit_book_proposal` `'peer'`, `verdict='COMMENT'` default) are documented under Assumptions. |
| Architectural rationale respects requirements | PASS | `ADR-035` satisfies `REQ-053` and `REQ-055` while preserving zero server-side code execution (`ADR-034`) and strict Human `/admin` boundaries (`REQ-052`). |
| IDs, references, and revision route are consistent | PASS | Allocates fresh `REQ-052..REQ-056` and `ADR-035` above `REQ-051`/`ADR-034` and synchronizes `docs/specs/INDEX.md` in `Planning`. |
