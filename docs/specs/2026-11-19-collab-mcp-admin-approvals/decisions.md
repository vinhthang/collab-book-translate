# Decisions: Admin Approvals UI, Book Proposal Reject/Open Lifecycle, & Per-Book Manual Chapter Approval

Governing specification: [spec.md](./spec.md)

## Architectural rationale

- Governs: REQ-047, REQ-048, REQ-049

Exposing `/admin/books/{slug}/approve`, `/admin/books/{slug}/reject`, `/admin/books/{slug}/seed`, `/admin/submissions/{id}/manage`, `/admin/fallback/run`, and `/admin/books/{slug}/release` directly in the Google SSO-protected `/admin` dashboard—paired with an additive `books.approval_mode` column (`auto` | `manual`)—delivers complete human-in-the-loop control in the browser with zero PostgreSQL downtime or breaking API changes (`REQ-047..REQ-049`).

## ADR-033: Keep `chapters.status="open"` while gating active `pending_admin_approval` submissions

- Governs: REQ-048
- State: Accepted
- Context and constraints: Under `approval_mode="manual"`, a chapter can pass `review.vinhthang.dev` and enter Admin review, after which Admin may either **Approve** (merging the chapter) or **Reject** (reopening the chapter to translators). Storing a transient chapter status in `chapters.status` alongside `submissions.status` risks chapter/submission state drift across retries or rejects.
- Viable options:
  1. **Option 1 (Keep `chapters.status="open"` and derive translator availability + duplicate guard from active `pending_admin_approval` rows in `submissions`)**: While a chapter has a `pending_admin_approval` submission, `list_tasks(role="translator")`, `trigger_starved_fallback`, and non-admin `submit_translation` treat it as locked awaiting Admin decision (`409` on duplicate submit), while `list_tasks(role="reviewer")` keeps it reviewable; Admin Reject sets only `submission.status="rejected"` (automatically reopening the chapter), and Admin Approve marks both `submission` and `chapter` as `"accepted"`.
  2. **Option 2 (Add a new `pending_admin_approval` value directly to `chapters.status`)**: Requires synchronized two-table rollback on Admin Reject and breaks existing helpers checking `ch["status"] == "open"`.
- Decision: Choose **Option 1**.
- Costs and risks: `list_tasks`, `submit_translation`, and `trigger_starved_fallback` must consult `list_submissions` for `pending_admin_approval`; per-book submission counts on this workload are small and indexed in SQLite/PostgreSQL.
- Rejected alternatives: **Option 2** was rejected to avoid redundant state machines between `chapters` and `submissions`.
- Affected components: `scripts/mcp_server/db.py`, `scripts/mcp_server/service.py`, `scripts/mcp_server/app.py`.

## Gate 1: normative review

| Check | Status | Evidence / required correction |
| --- | --- | --- |
| Codebase grounding | PASS | Grounded against `collab-book-translate` commit `7fcb3bd` (`db.py:33-55`, `service.py:259,645-648,978-996,1106-1138`, `app.py:144-412`). |
| Attributable intent and explicit scope | PASS | Traces directly to user prompt *"review this, https://collab.vinhthang.dev/, I can't see how can I approval for a book translation"*, approved choices A1 & A2, and the approved execution plan. |
| Observable requirements and meaningful acceptance criteria | PASS | `REQ-047..REQ-049` specify concrete `AC-*-1` (success) and `AC-*-2` (boundary/failure) HTTP codes, state transitions, and XSS/secret guards. |
| Assumptions and owner-deferred questions are explicit | PASS | Default `approval_mode="auto"` recorded under Assumptions; zero unresolved `[PROVISIONAL]` questions. |
| Architectural rationale respects requirements | PASS | `ADR-033` eliminates chapter/submission state drift across Admin approve/reject cycles while preserving `open` chapter semantics. |
| IDs, references, and revision route are consistent | PASS | Allocates fresh `REQ-047..REQ-049` and `ADR-033` above frozen `REQ-046`/`ADR-032` via linked delta without modifying frozen specs. |
