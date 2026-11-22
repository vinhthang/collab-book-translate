# Decisions: Collab Books MCP Reviewer Queue, Front/Back Matter Flow, & Context Bundle

Governing specification: [spec.md](./spec.md)

## Architectural rationale

- Governs: REQ-065, REQ-066, REQ-067

Existing constraints in `CollabStore` and `CollabMcpService` make raising `list_tasks` defaults to `limit=200` (with compact summaries stripping `translation_xml`/`final_txt` and `offset` pagination), deduplicating `rounds_history` XML, bundling unified `context`/`book_record`/`brief` on `get_submission_status`, allowing post-lock `brief` updates by `agent_admin`, and showing full UUIDs in `/admin` the only non-breaking, backward-compatible path for existing REST/MCP callers.

## ADR-038: Persist Chapter `section_type` and Auto-Accept Boilerplate Only at Import Time

- Governs: REQ-064
- State: Accepted
- Context and constraints: Books imported from EPUB/PDF contain both non-content publishing boilerplate (`Cover`, `Title Page`, `Simon & Schuster ebook promo`, `Copyright / Avid Reader Press`, `Also From`, `<= 40`-word `Part` dividers) and real editorial front/back matter (`With Appreciation`, `How to Read This Book`, `Why I'm Sharing This Book`, `About the Author`, `Image Descriptions`, `Appendix`) alongside body chapters.
- Viable options:
  1. Persist `chapters.section_type` (`body`, `front_matter`, `back_matter`, `boilerplate`), sort `body` first in `list_tasks`, allow filtering via `include_matter`/`section_type`, and auto-accept only `"boilerplate"` chapters during trusted imports (`auto_approve_boilerplate=True`, disabled when `approval_mode="manual"`).
  2. Auto-accept ALL non-body front and back matter including `Acknowledgments`, `About the Author`, `How to Read This Book`, and `Image Descriptions`.
  3. Tag and hide front/back matter from default `list_tasks` without auto-accepting boilerplate.
- Decision: Choose Option 1 (`section_type` classification + boilerplate-only auto-accept).
- Costs and risks: Requires idempotent SQLite/PostgreSQL `ALTER TABLE chapters ADD COLUMN section_type` plus startup backfill for already-imported chapters, and heuristic rules must never misclassify substantive `Part I` prose or `Introduction` chapters as boilerplate (mitigated by strict `<= 40`-word ceiling on `Part` dividers and title/prose checks tested explicitly).
- Rejected alternatives: Option 2 risks publishing untranslated/buggy `Image Descriptions` (`19,435` words) or author prefaces without human/peer review; Option 3 still leaves `Cover` and `Copyright` pages sitting unaccepted, blocking `published` book status until manually cleared.
- Affected components: `scripts/mcp_server/db.py`, `scripts/mcp_server/service.py`, `scripts/mcp_server/app.py`.

## Gate 1: normative review

| Check | Status | Evidence / required correction |
| --- | --- | --- |
| Codebase grounding | PASS | Inspected `scripts/mcp_server/db.py`, `service.py`, and `app.py` at `36e07ba` and live MCP calls against `collab.vinhthang.dev`. |
| Attributable intent and explicit scope | PASS | Quoted exact user feedback and resolved A1/A2 selections; non-goals exclude silent auto-accept of substantive front/back matter and post-lock `book_record` overwrite. |
| Observable requirements and meaningful acceptance criteria | PASS | `REQ-064..REQ-067` specify observable inputs, HTTP/schema outputs, sorting/pagination counts, and `400`/`409` boundary checks. |
| Assumptions and owner-deferred questions are explicit | PASS | `query_submissions` Python list return backward compatibility recorded explicitly; zero `[PROVISIONAL]` items remain. |
| Architectural rationale respects requirements | PASS | `ADR-038` and rationale cover `REQ-064..REQ-067` without altering frozen parent semantics silently. |
| IDs, references, and revision route are consistent | PASS | Linked delta package uses repo-wide `REQ-064..REQ-067` and `ADR-038` above `REQ-063`/`ADR-037`. |
