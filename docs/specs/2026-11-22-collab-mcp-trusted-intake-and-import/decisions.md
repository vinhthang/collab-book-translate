# Architectural Decisions: Collab Books MCP Trusted Submitter Auto-Approve & Batch Import

- Feature ID: `2026-11-22-collab-mcp-trusted-intake-and-import`
- Governed Requirements: `REQ-061`, `REQ-062`, `REQ-063`

## Architectural rationale

- Governs: REQ-061, REQ-062, REQ-063

`collab-mcp` separates book/glossary curation from peer translation review via deterministic XML/injection validation, `glossary_sha` locking, and PostgreSQL/SQLite persistence. Trusted submitters (`ADMIN` or `agent_admin`) supplying pre-split books or already-translated chapters can be governed cleanly within `CollabMcpService`, `CollabStore`, and `app.py` while keeping `ANONYMOUS` / `contributor` intake gated behind `/admin`.

## ADR-037: Deterministic Pre-LLM Validation + Peer-Review Gate for Trusted Chapter Imports

- Governs: REQ-061, REQ-062, REQ-063
- State: Accepted
- Context and constraints:
  - Migrating an already-translated multi-chapter book (such as the 3 remediated Ray Dalio books, `87` chapters total) through `submit_translation` forces an initial `ch01` seed lock and `87` sequential synchronous calls to `review.vinhthang.dev` (~60–150s each), plus manual `/admin` intake approval.
  - Peer reviewers still expect full XML structural integrity (`<segment>` IDs, byte-identical `<original>`, fenced code blocks, images, `<hanviet>` for `zh`, Vietnamese language detection), prompt-injection safety, and `approval_mode="peer"` review queues (`pending_review`).
- Viable options:
  - **Option A — Direct `pending_review` Import with Deterministic Pre-LLM Validation & `glossary_sha` Lock**: Require `ADMIN` or `agent_admin`, enforce full deterministic structural + language + 4-class prompt-injection validation atomically per batch, lock `glossary_sha` from the curated `book_record`, open `ch01..chNN` immediately (`active`), and insert submissions into `pending_review` (`pending_admin_approval` on `manual`) with `workstream_id=""`. On peer `APPROVE`, omit synthetic `"review-vinhthang-dev"` handles from `collab_ledger.json`.
  - **Option B — Mandatory Synchronous `review.vinhthang.dev` LLM Round per Imported Chapter**: Route every imported chapter through `ReviewServerClient.run_translation_round` before entering `pending_review`.
  - **Option C — Auto-Accept Trusted Imports Directly to `accepted`**: Skip `pending_review` and immediately commit trusted chapters into `vinhthang/collab-books`.
- Decision: Select **Option A**. It eliminates redundant manual `/admin` intake clicks and brittle multi-hour LLM bottlenecks during bulk migration while preserving independent peer review (`pending_review`), full deterministic integrity, and honest ledger attribution (`workstream_id == ""` -> peer/admin reviewers only).
- Costs and risks:
  - Imported chapters enter `pending_review` without an initial automated specialist `rounds_history` entry; mitigated by retaining deterministic structural/language/injection enforcement and surfacing all imported drafts to independent peer reviewers (`submit_review`).
  - Re-opening `locked_seed` chapters or importing out-of-order chapters requires preserving an already-locked `glossary_sha` in `_finalize_accepted_chapter`; mitigated explicitly in `REQ-061`.
- Rejected alternatives:
  - **Option B** keeps multi-hour synchronous `review.vinhthang.dev` timeouts on 87-chapter migrations.
  - **Option C** bypasses peer reviewers completely, violating `approval_mode="peer"` and the user's goal of getting external reviewer opinions.
- Affected components:
  - `scripts/mcp_server/service.py`
  - `scripts/mcp_server/app.py`
  - `scripts/collab_worker.py`

## Gate 1: normative review

| Check | Status | Evidence / required correction |
| --- | --- | --- |
| Codebase grounding | PASS | Inspected `scripts/mcp_server/service.py:132-192,640-799,900-1060,1536-1620,2025-2085`, `scripts/mcp_server/db.py:448-482,656-677`, `scripts/mcp_server/app.py`, and `scripts/collab_worker.py` at `0702b91`. |
| Attributable intent and explicit scope | PASS | Directly grounded in verbatim prompt (*"I do not want to manual do these approve if I manual submit these books, need to change the process"*) and Q1–Q3 (`A1`, `A2`, `A3`). |
| Observable requirements and meaningful acceptance criteria | PASS | `REQ-061..REQ-063` each declare testable `AC-*-1` (success) and `AC-*-2` (boundary/failure) criteria. |
| Assumptions and owner-deferred questions are explicit | PASS | Reversible `50`-chapter batch cap and trusted `10 MiB` proposal cap documented; zero unresolved questions. |
| Architectural rationale respects requirements | PASS | `ADR-037` compares 3 viable options and aligns trusted auto-approve + deterministic batch import + peer-review gate with `REQ-061..REQ-063`. |
| IDs, references, and revision route are consistent | PASS | Delta `- Changes:` links verified against `REQ-051..REQ-055` and `REQ-060` without mutating frozen specs; `INDEX.md` synchronized. |
