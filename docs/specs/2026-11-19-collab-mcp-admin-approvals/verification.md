# Verification: Admin Approvals UI, Book Proposal Reject/Open Lifecycle, & Per-Book Manual Chapter Approval

Governing specification: [spec.md](./spec.md)

## Planned checks

### AC-047-1 (REQ-047)

- Method: Run `uv run pytest tests/test_mcp_admin_approvals.py -k ac_047` testing book proposal -> `approve_book_intake(auto_seed=False)` (`status="active"`, `ch01` open in `list_tasks`, `ch02` `locked_seed`), idempotent retry after `seed_failed`, and `reject_book_proposal` hiding rejected books from public `list_books()`.
- Expected outcome: All assertions pass with `0` skipped tests.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-047-2 (REQ-047)

- Method: Verify `409` on rejecting `active`/`published` books or re-approving `published` books, and `403` on non-admin approve/reject endpoints.
- Expected outcome: Exact `409` and `403` responses returned.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-048-1 (REQ-048)

- Method: Verify default `approval_mode="auto"` auto-accepts, toggling `"manual"` holds non-admin passes in `pending_admin_approval`, hides chapter from translator `list_tasks` & starved fallback while keeping it in reviewer `list_tasks`, reopens chapter on Admin reject, finalizes + git-syncs on Admin approve (`override_escalated=False`), bypasses manual hold on Admin `seed_book`, and passes `override_escalated=True` for `flagged_security`/`escalated_to_admin`.
- Expected outcome: Full approval state machine passes cleanly.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-048-2 (REQ-048)

- Method: Verify `400` on invalid `approval_mode`, `409` on duplicate submit during `pending_admin_approval` or double-approve of accepted submission/chapter, and `403` on non-admin access.
- Expected outcome: All boundary guards pass cleanly.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-049-1 (REQ-049)

- Method: Test `GET /`, `GET /admin`, and `GET /admin/api/submissions` via Starlette `TestClient`, verifying pending badge, `Admin Approvals & Access (SSO)`, `Review in /admin` links, Book Proposals queue, prioritized Chapter Submissions queue with `peer_reviews`, and combined `/admin/api/books/{slug}/access` saving both `access_mode` and `approval_mode`.
- Expected outcome: All HTTP/UI checks pass with `200 OK`.
- Execution: [Run 1](#run-1--2026-11-19)

### AC-049-2 (REQ-049)

- Method: Verify `403` for non-admin calls to `/admin`, `/admin/api/submissions`, `/admin/books/{slug}/reject`, and `/admin/api/books/{slug}/approval-mode`, inject `<script>alert(1)</script>` and non-HTTPS `license_url` payloads checking escaping/sanitization, and verify zero raw `AGENT_KEY`/token leakage.
- Expected outcome: Zero unescaped `<script>` or secret leakage; all unauthorized endpoints return `403`.
- Execution: [Run 1](#run-1--2026-11-19)

## Execution evidence

### Run 1 — 2026-11-19

- Checked: REQ-047..REQ-049 / AC-047-1..AC-049-2 (Python 3.12 full test suite + `mutation-guard` + `code-review`)
- Command: `uv run pytest -q`, `mutation_runner.py` on `scripts/mcp_server/db.py` & `scripts/mcp_server/service.py`, and `prepare_code_review.py` (exit code: `0`)
- Output excerpt:
  ```text
  326 passed, 6 warnings in 14.69s
  [Mutation Guard] scripts/mcp_server/db.py -> Mutation Score: 100.0% (31/31 killed)
  [Mutation Guard] scripts/mcp_server/service.py -> Mutation Score: 86.7% (26/30 killed)
  ```
- Result: AC-047-1 `PASS`, AC-047-2 `PASS`, AC-048-1 `PASS`, AC-048-2 `PASS`, AC-049-1 `PASS`, AC-049-2 `PASS` — All 326 tests pass, mutation scores meet `>= 80%`, and single-agent `code-review` is `Approved` (`0 P0`, `0 P1`).
