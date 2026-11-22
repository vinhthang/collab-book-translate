# Tasks: Collab Books MCP Public-Corpus Publish Gate & Non-Public Storage Isolation

Sources: [spec.md](./spec.md), [design.md](./design.md)

## Task 1: TDD Suite

- [x] Write failing RED tests first for `REQ-068` and `REQ-069`, then bump version assertions to `"4.3.1"`.
- Covers: REQ-068 / AC-068-1, AC-068-2, REQ-069 / AC-069-1, AC-069-2
- Files: `tests/test_mcp_public_corpus_gate.py`, `tests/test_collab_config.py`, `tests/test_collab_worker_mcp.py`, `tests/test_mcp_workflow_v2.py`, `tests/test_mcp_bug_triage_and_hardening.py`, `tests/test_mcp_trusted_intake_and_import.py`, `tests/test_mcp_reviewer_queue_and_matter.py`
- Dependencies: None
- Blocked by: None
- Work: Write failing RED tests in `tests/test_mcp_public_corpus_gate.py` verifying `owner-permission` and `restricted` isolation into `private-books/`, zero Git commits/releases on `origin.git`, `status="completed"`, `409` on `publish_release`, `GET /` HTML hiding, startup `origin/main` alignment after history reset, offline startup safety, and `"4.3.1"` discovery metadata; update `"4.3.0"` expectations to `"4.3.1"` in existing test files.
- Completion evidence: Executed Red -> Green for `REQ-068..REQ-069`; full suite passed `352 passed, 0 failed, 0 skipped` (`26.19s`) and `test_option_a_hygiene.py` passed `2 passed` (`0.66s`). See [Run 1](./verification.md#run-1--2026-11-23).

## Task 2: Git & Service Gate

- [x] Implement public-corpus gate and `private-books/` filesystem isolation across `git_sync.py`, `db.py`, and `service.py`.
- Covers: REQ-068 / AC-068-1, AC-068-2, REQ-069 / AC-069-1, AC-069-2
- Files: `scripts/mcp_server/git_sync.py`, `scripts/mcp_server/db.py`, `scripts/mcp_server/service.py`
- Dependencies: Task 1
- Blocked by: None
- Work: Add `PUBLIC_CORPUS_LICENSES`, `is_public_corpus_book`, `private_books_dir`, `evict_to_private_storage`, and `reconcile_origin_and_evict_private`; skip non-public manifests in `bootstrap_from_books_dir`; route all non-public writes/ledgers to `private-books/`, skip Git/GitHub Release steps, set `"completed"` on completion, and enforce `409` on `publish_release`.
- Completion evidence: Implemented cleanly; `mutation-guard` killed `8/10` (`80.0%`) on `scripts/mcp_server/git_sync.py` and `12/15` (`80.0%`) on `scripts/mcp_server/service.py`. See [Run 1](./verification.md#run-1--2026-11-23).

## Task 3: UI & Startup Sync

- [x] Filter public bookshelf `/`, update `/admin` badges/buttons, wire startup reconcile, bump to `"4.3.1"` (`v0.5.1`), verify gates, and ship.
- Covers: REQ-069 / AC-069-1, AC-069-2
- Files: `scripts/mcp_server/app.py`, `plugin.json`, `.github/workflows/ci-cd.yml`, `skills/collab-translate-book/SKILL.md`, `agents/collab_admin.md`, `agents/collab_reviewer.md`, `docs/specs/2026-11-23-collab-mcp-public-corpus-publish-gate/spec.md`, `docs/specs/2026-11-23-collab-mcp-public-corpus-publish-gate/verification.md`, `docs/specs/INDEX.md`
- Dependencies: Task 2
- Blocked by: None
- Work: Update `app.py` (`GET /` license filter, `/admin` `"completed"` badge + release button guard, `create_app` startup reconcile, `"4.3.1"`), bump plugin/workflow/agent docs to `"4.3.1"`/`v0.5.1`, verify full `pytest`, `test_option_a_hygiene.py`, `mutation-guard` (`>= 80%`), and single-agent `code-review`, then push & bump `vinhthang/oci`.
- Completion evidence: Reset `vinhthang/collab-books` `main` to `21a213c6a5`, updated `/` filter, `/admin` controls (`node --check` exit `0`), and `"4.3.1"`/`v0.5.1` surfaces; single-agent `code-review` approved (`0` P0/P1, `git diff --check` exit `0`). See [Run 1](./verification.md#run-1--2026-11-23).

## Gate 2: traceability and readiness

| Requirement | AC | ADR or rationale | Design | Task | Planned verification | Status / reason |
| --- | --- | --- | --- | --- | --- | --- |
| REQ-068 | AC-068-1 | [ADR-039](./decisions.md#adr-039-isolate-non-public-corpus-files-in-sibling-private-books-directory-outside-the-collab-books-git-tree) | [Interfaces](./design.md#interfaces-and-flow) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-git--service-gate) | [AC-068-1](./verification.md#ac-068-1-req-068) | PASS |
| REQ-068 | AC-068-2 | [ADR-039](./decisions.md#adr-039-isolate-non-public-corpus-files-in-sibling-private-books-directory-outside-the-collab-books-git-tree) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-git--service-gate) | [AC-068-2](./verification.md#ac-068-2-req-068) | PASS |
| REQ-069 | AC-069-1 | [Rationale](./decisions.md#architectural-rationale) | [Interfaces](./design.md#interfaces-and-flow) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-git--service-gate), [Task 3](#task-3-ui--startup-sync) | [AC-069-1](./verification.md#ac-069-1-req-069) | PASS |
| REQ-069 | AC-069-2 | [Rationale](./decisions.md#architectural-rationale) | [Boundaries](./design.md#boundaries-and-recovery) | [Task 1](#task-1-tdd-suite), [Task 2](#task-2-git--service-gate), [Task 3](#task-3-ui--startup-sync) | [AC-069-2](./verification.md#ac-069-2-req-069) | PASS |

- Backward scope check: PASS — Every task and component serves `REQ-068..REQ-069`.
- Reference and catalog consistency: PASS — `INDEX.md`, `decisions.md`, `design.md`, and `verification.md` match IDs and anchors.
