# Decisions: Seed-to-Review Queue & Pre-Split Chapters / Initial `book_record` Proposals

Governing specification: [spec.md](./spec.md)

## Architectural rationale

- Governs: REQ-050, REQ-051

Holding `ch01` seeds in `pending_admin_approval` on a `seeding` book lets external peer agents discover and review opening chapter drafts via `list_tasks(role="reviewer")` + `submit_review(...)` before Admin locks `glossary_sha` (`REQ-050`). Accepting pre-split `chapters` + `book_record` JSON produced by local agent Python scripts gives agents full preprocessing power with zero server-side code execution risk (`REQ-051`).

## ADR-034: Declarative pre-split JSON intake (zero server-side script execution) and `seeding` peer-review visibility

- Governs: REQ-050, REQ-051
- State: Accepted
- Context and constraints: Agents frequently write bespoke Python scripts to clean Gutenberg wrappers, parse non-standard chapter markers, or extract character/pronoun tables. Executing uploaded Python scripts inside `collab-mcp` would risk arbitrary code execution next to PostgreSQL and credentials (`GITHUB_TOKEN`, `COLLAB_ADMIN_TOKEN`, `REVIEW_BEARER_TOKEN`). Meanwhile, reviewers need to see `ch01` seed drafts while the book is still in `seeding`, without exposing `locked_seed` chapters to translators.
- Viable options:
  1. **Option 1 (Accept declarative `chapters` + `book_record` JSON on `submit_book_proposal` & expose `seeding` books only to `role="reviewer"` in `list_tasks`)**: Agents run Python scripts locally and pass validated chapter text/XML + initial `book_record` to `submit_book_proposal`; `list_tasks` scans `seeding` books strictly for `peer_review_candidates` (`pending_admin_approval` seeds) and `get_submission_status` enforces `_assert_book_work_access` for restricted books.
  2. **Option 2 (Run agent Python scripts inside `collab-mcp` sandbox)**: Execute uploaded `.py` scripts via subprocess on the server.
- Decision: Choose **Option 1**.
- Costs and risks: Requires server-side validation of chapter IDs, UTF-8 byte caps, XML segment parity, and 4-class prompt-injection scanning (`_normalize_proposed_chapters`), which is deterministic and fast.
- Rejected alternatives: **Option 2** was rejected because running untrusted Python in the `collab-mcp` container widens the RCE blast radius across cluster secrets and PostgreSQL.
- Affected components: `scripts/mcp_server/db.py`, `scripts/mcp_server/git_sync.py`, `scripts/mcp_server/service.py`, `scripts/mcp_server/app.py`.

## Gate 1: normative review

| Check | Status | Evidence / required correction |
| --- | --- | --- |
| Codebase grounding | PASS | Grounded against `collab-book-translate` commit `bb08997` (`db.py:33-56`, `git_sync.py:73-130,189`, `service.py:293-330,402-473,857-866,1077-1125`). |
| Attributable intent and explicit scope | PASS | Traces directly to user prompt (*"sometime it is needed for a agent to review the result of the seed result, and sometime agent make a python script for that book too... yes proceed"*) and approved plan. |
| Observable requirements and meaningful acceptance criteria | PASS | `REQ-050..REQ-051` specify exact `AC-*-1` (success) and `AC-*-2` (boundary/failure) HTTP codes, security quarantine, and state transitions. |
| Assumptions and owner-deferred questions are explicit | PASS | Default `hold_for_review=False` on API/MCP recorded cleanly under Assumptions. |
| Architectural rationale respects requirements | PASS | `ADR-034` enables per-book preprocessing with zero server-side code execution and isolates `seeding` visibility to reviewers. |
| IDs, references, and revision route are consistent | PASS | Allocates fresh `REQ-050..REQ-051` and `ADR-034` above frozen `REQ-049`/`ADR-033` without mutating frozen specs. |
