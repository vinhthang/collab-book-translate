# Tasks: Seed-to-Review Queue & Pre-Split Chapters / Initial `book_record` Proposals

Sources: [spec.md](./spec.md), [design.md](./design.md)

## Task 1: Author strict TDD tests for `REQ-050..REQ-051` (RED phase)

- [x] Complete the work and its applicable checks.
- Covers: REQ-050 / AC-050-1, AC-050-2, REQ-051 / AC-051-1, AC-051-2
- Files: `tests/test_mcp_seed_review_and_presplit.py`
- Dependencies: None
- Blocked by: None
- Work: Write failing tests covering Seed-to-Review Queue (`hold_for_review=True`, `seeding` reviewer visibility, `submit_review` XSS escaping in `/admin`, Admin merge unlocking `ch02` + writing `reviewer_logins`, duplicate `409`, restricted-book `403`), pre-split `chapters` + initial `book_record` intake & preservation, and `400`/`413`/`422`/`flagged_security` validation boundaries.
- Completion evidence: Verified 3 genuine RED failures before implementation, then all 3 passed (`329 passed` across full suite).

## Task 2: Implement `proposed_chapters` storage, git/ledger support, and service logic

- [x] Complete the work and its applicable checks.
- Covers: REQ-050 / AC-050-1, AC-050-2, REQ-051 / AC-051-1, AC-051-2
- Files: `scripts/mcp_server/db.py`, `scripts/mcp_server/git_sync.py`, `scripts/mcp_server/service.py`
- Dependencies: Task 1
- Blocked by: None
- Work: Add `proposed_chapters_json` column & hydration in `db.py`; support `proposed_chapters`, `initial_book_record`, and `ch01` peer `reviewer_logins` in `git_sync.py`; add normalization/validation/scanning helpers, `hold_for_review`, reviewer `seeding` visibility, and `get_submission_status` book-access guard in `service.py`.
- Completion evidence: `mutation-guard` on `git_sync.py` -> `100.0% (6/6 killed)`; `mutation-guard` on `service.py` -> `90.0% (27/30 killed)`.

## Task 3: Expose MCP/REST parameters & update `/admin` UI in `app.py`

- [x] Complete the work and its applicable checks.
- Covers: REQ-050 / AC-050-1, REQ-051 / AC-051-1
- Files: `scripts/mcp_server/app.py`
- Dependencies: Task 2
- Blocked by: None
- Work: Expose `chapters` & `book_record` on `submit_book_proposal`, `hold_for_review` on `approve_book_intake` & `seed_book`, bump API version `"3.3.0"`, update `MCP_SERVER_INSTRUCTIONS` & `/llms.txt`, add `Approve & Seed to Review` / `Retry Seed to Review`, render pre-split chapter summaries & escaped peer-review notes in `/admin`, and pass pytest, mutation-guard, and code-review gates.
- Completion evidence: `uv run pytest -q` -> `329 passed` (`0` failed, `0` skipped); single-agent `code-review` -> `Approved` (`0 P0`, `0 P1`).

## Gate 2: traceability and readiness

| Requirement | AC | ADR or rationale | Design | Task | Planned verification | Status / reason |
| --- | --- | --- | --- | --- | --- | --- |
| REQ-050 | AC-050-1 | [ADR-034](./decisions.md#adr-034-declarative-pre-split-json-intake-zero-server-side-script-execution-and-seeding-peer-review-visibility) | [Interfaces](./design.md#interfaces-and-flow) | [Task 1](#task-1-author-strict-tdd-tests-for-req-050req-051-red-phase), [Task 2](#task-2-implement-proposed_chapters-storage-gitledger-support-and-service-logic), [Task 3](#task-3-expose-mcprest-parameters--update-admin-ui-in-apppy) | [AC-050-1](./verification.md#ac-050-1-req-050) | PASS — Seed hold in `pending_admin_approval`, reviewer visibility, peer-review UI, and Admin merge planned |
| REQ-050 | AC-050-2 | [ADR-034](./decisions.md#adr-034-declarative-pre-split-json-intake-zero-server-side-script-execution-and-seeding-peer-review-visibility) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 1](#task-1-author-strict-tdd-tests-for-req-050req-051-red-phase), [Task 2](#task-2-implement-proposed_chapters-storage-gitledger-support-and-service-logic) | [AC-050-2](./verification.md#ac-050-2-req-050) | PASS — Duplicate seed `409`, restricted-book `403`, and non-admin bypass guard planned |
| REQ-051 | AC-051-1 | [ADR-034](./decisions.md#adr-034-declarative-pre-split-json-intake-zero-server-side-script-execution-and-seeding-peer-review-visibility) | [Interfaces](./design.md#interfaces-and-flow) | [Task 1](#task-1-author-strict-tdd-tests-for-req-050req-051-red-phase), [Task 2](#task-2-implement-proposed_chapters-storage-gitledger-support-and-service-logic), [Task 3](#task-3-expose-mcprest-parameters--update-admin-ui-in-apppy) | [AC-051-1](./verification.md#ac-051-1-req-051) | PASS — Pre-split chapters, initial `book_record`, `/admin` preview, and non-leakage planned |
| REQ-051 | AC-051-2 | [ADR-034](./decisions.md#adr-034-declarative-pre-split-json-intake-zero-server-side-script-execution-and-seeding-peer-review-visibility) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 1](#task-1-author-strict-tdd-tests-for-req-050req-051-red-phase), [Task 2](#task-2-implement-proposed_chapters-storage-gitledger-support-and-service-logic) | [AC-051-2](./verification.md#ac-051-2-req-051) | PASS — `400`/`413`/`422` validation boundaries and prompt-injection quarantine planned |

- Backward scope check: PASS — Every file and task maps directly to `REQ-050..REQ-051`.
- Reference and catalog consistency: PASS — `INDEX.md`, `spec.md`, `decisions.md`, `design.md`, `tasks.md`, and `verification.md` agree on `REQ-050..REQ-051`, `ADR-034`, and status `In-Testing`.
