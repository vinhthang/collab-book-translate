# Tasks: Admin Approvals UI, Book Proposal Reject/Open Lifecycle, & Per-Book Manual Chapter Approval

Sources: [spec.md](./spec.md), [design.md](./design.md)

## Task 1: Author strict TDD tests for `REQ-047..REQ-049` (RED phase)

- [x] Complete the work and its applicable checks.
- Covers: REQ-047 / AC-047-1, AC-047-2, REQ-048 / AC-048-1, AC-048-2, REQ-049 / AC-049-1, AC-049-2
- Files: `tests/test_mcp_admin_approvals.py`
- Dependencies: None
- Blocked by: None
- Work: Write failing tests covering book proposal `auto_seed=False` open-for-agents & `reject_book_proposal`, `approval_mode="manual"` hold/reject/approve + `override_escalated` semantics, `/admin/api/submissions`, `/` homepage pending links, `/admin` approval queues, and XSS / zero-secret-leak boundaries; verify failures before editing production code.
- Completion evidence: Verified 3 genuine RED failures on `tests/test_mcp_admin_approvals.py`, then hardened DB/boundary checks (`4 passed` in `test_mcp_admin_approvals.py`, `326 passed` full suite).

## Task 2: Add `approval_mode` persistence and approval lifecycle logic in `db.py` & `service.py`

- [x] Complete the work and its applicable checks.
- Covers: REQ-047 / AC-047-1, AC-047-2, REQ-048 / AC-048-1, AC-048-2
- Files: `scripts/mcp_server/db.py`, `scripts/mcp_server/service.py`
- Dependencies: Task 1
- Blocked by: None
- Work: Add `approval_mode` column & migrations, `set_book_approval_mode`, `set_book_status`, `reject_book_proposal`, `list_admin_submissions`, open-for-agents `approve_book_intake(auto_seed=False)` (`status="active"`), idempotent chapter extraction retry, and `"manual"` chapter hold/approve/reject transitions.
- Completion evidence: `mutation-guard` on `db.py` -> `100.0% (31/31 killed)`; `mutation-guard` on `service.py` -> `86.7% (26/30 killed)`.

## Task 3: Wire Admin REST/MCP endpoints, `/` navigation updates, & `/admin` Approval UI in `app.py`

- [x] Complete the work and its applicable checks.
- Covers: REQ-047 / AC-047-1, AC-047-2, REQ-048 / AC-048-1, AC-048-2, REQ-049 / AC-049-1, AC-049-2
- Files: `scripts/mcp_server/app.py`
- Dependencies: Task 2
- Blocked by: None
- Work: Expose MCP tools & REST/SSO routes (`/admin/books/{slug}/reject`, `/admin/api/books/{slug}/approval-mode`, `/admin/api/submissions`), update `_render_bookshelf_html` and `_render_admin_html` with XSS-safe approval queues and per-book controls, update `MCP_SERVER_INSTRUCTIONS` / `_render_llms_txt` / version `"3.2.0"`, and pass full pytest, mutation-guard, and code-review gates.
- Completion evidence: `uv run pytest -q` -> `326 passed` (`0` failed, `0` skipped); `prepare_code_review.py` + single-agent `code-review` -> `Approved` (`0 P0`, `0 P1`).

## Gate 2: traceability and readiness

| Requirement | AC | ADR or rationale | Design | Task | Planned verification | Status / reason |
| --- | --- | --- | --- | --- | --- | --- |
| REQ-047 | AC-047-1 | [Rationale](./decisions.md#architectural-rationale) | [Interfaces](./design.md#interfaces-and-flow) | [Task 1](#task-1-author-strict-tdd-tests-for-req-047req-049-red-phase), [Task 2](#task-2-add-approval_mode-persistence-and-approval-lifecycle-logic-in-dbpy--servicepy) | [AC-047-1](./verification.md#ac-047-1-req-047) | PASS — Open `ch01` intake (`auto_seed=False`), retry idempotency, and proposal rejection planned |
| REQ-047 | AC-047-2 | [Rationale](./decisions.md#architectural-rationale) | [Interfaces](./design.md#interfaces-and-flow) | [Task 1](#task-1-author-strict-tdd-tests-for-req-047req-049-red-phase), [Task 2](#task-2-add-approval_mode-persistence-and-approval-lifecycle-logic-in-dbpy--servicepy) | [AC-047-2](./verification.md#ac-047-2-req-047) | PASS — `409` active/published guards and `403` admin auth gates planned |
| REQ-048 | AC-048-1 | [ADR-033](./decisions.md#adr-033-keep-chaptersstatusopen-while-gating-active-pending_admin_approval-submissions) | [Interfaces](./design.md#interfaces-and-flow) | [Task 1](#task-1-author-strict-tdd-tests-for-req-047req-049-red-phase), [Task 2](#task-2-add-approval_mode-persistence-and-approval-lifecycle-logic-in-dbpy--servicepy) | [AC-048-1](./verification.md#ac-048-1-req-048) | PASS — `auto` vs `manual` hold/approve/reject, reviewer access, seed bypass, and `override_escalated` planned |
| REQ-048 | AC-048-2 | [ADR-033](./decisions.md#adr-033-keep-chaptersstatusopen-while-gating-active-pending_admin_approval-submissions) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 1](#task-1-author-strict-tdd-tests-for-req-047req-049-red-phase), [Task 2](#task-2-add-approval_mode-persistence-and-approval-lifecycle-logic-in-dbpy--servicepy) | [AC-048-2](./verification.md#ac-048-2-req-048) | PASS — `400` invalid mode, `409` duplicate submit / double-approve, `403` auth boundaries planned |
| REQ-049 | AC-049-1 | [Rationale](./decisions.md#architectural-rationale) | [Components](./design.md#components) | [Task 1](#task-1-author-strict-tdd-tests-for-req-047req-049-red-phase), [Task 3](#task-3-wire-admin-restmcp-endpoints--navigation-updates--admin-approval-ui-in-apppy) | [AC-049-1](./verification.md#ac-049-1-req-049) | PASS — Homepage pending indicators and `/admin` proposal/submission queues planned |
| REQ-049 | AC-049-2 | [Rationale](./decisions.md#architectural-rationale) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 1](#task-1-author-strict-tdd-tests-for-req-047req-049-red-phase), [Task 3](#task-3-wire-admin-restmcp-endpoints--navigation-updates--admin-approval-ui-in-apppy) | [AC-049-2](./verification.md#ac-049-2-req-049) | PASS — `403` non-admin gating, XSS escaping, safe `license_url`, and zero secret leaks planned |

- Backward scope check: PASS — Every file and task maps directly to `REQ-047..REQ-049`.
- Reference and catalog consistency: PASS — `INDEX.md`, `spec.md`, `decisions.md`, `design.md`, `tasks.md`, and `verification.md` agree on `REQ-047..REQ-049`, `ADR-033`, and status `In-Testing`.
