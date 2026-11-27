# Decisions: Collab Books MCP Admin Workspace Console Rework (`v4.5.0`)

Governing specification: [spec.md](./spec.md)

## ADR-041: Zero-Build Modular SSR (`admin_ui.py`) + Lazy JSON Drawers over a Bundled SPA

- Governs: REQ-073, REQ-074, REQ-075
- State: Accepted
- Context and constraints: Reworking `/admin` into a full Workspace Console with 5 tabs (`Overview`, `Books`, `Submissions`, `Bugs`, `Agents`), search/filters, chapter drill-down, lazy XML/critique inspection, bulk actions, and Dark/Light theming adds significant HTML/CSS/JS (~900 lines). Adding a React/Vite toolchain would require Node build steps in `Dockerfile` and rewrite SSO/XSS test contracts, while keeping everything inline in `scripts/mcp_server/app.py` (already `3,060` lines) degrades maintainability.
- Viable options:
  1. Extract admin HTML/CSS/JS + shared Dark/Light theme CSS/JS into a dedicated zero-dependency Python module `scripts/mcp_server/admin_ui.py` (re-exported by `app.py`), backed by lightweight `/admin/api/*` JSON endpoints for lazy drawers (`submissions/{id}`, `books/{slug}`), bulk actions, private exports, and agent lifecycle mutations, verified directly with `node --check`.
  2. Keep growing inline HTML/JS strings directly inside `scripts/mcp_server/app.py`.
  3. Introduce a Node/Vite SPA bundle compiled during container build.
- Decision: Choose Option 1 (`scripts/mcp_server/admin_ui.py` + lazy `/admin/api/*` endpoints).
- Costs and risks: Requires keeping interactive selectors (`.js-approve-*`, `.js-manage-sub`, `.js-triage-bug`, `.js-save-book`, `.js-toggle-agent-role`) and single-`<script>` JS structure clean for `node --check`; client-side search/filter assumes `<= 200` recent admin submissions rendered initially while heavy XML/critiques load lazily on click.
- Rejected alternatives: Option 2 balloons `app.py` past `4,000` lines; Option 3 adds container build complexity and breaks SSR XSS/smoke assertions without solving a runtime bottleneck.
- Affected components: `scripts/mcp_server/admin_ui.py`, `scripts/mcp_server/app.py`, `scripts/mcp_server/db.py`, `scripts/mcp_server/service.py`, `scripts/mcp_server/git_sync.py`.

## Gate 1: normative review

| Check | Status | Evidence / required correction |
| --- | --- | --- |
| Codebase grounding | PASS | Inspected `scripts/mcp_server/{db,auth,git_sync,service,app}.py` (`7516867`), `compile_book.py`, `Dockerfile`, and live `https://collab.vinhthang.dev` book/task/bug volume. |
| Attributable intent and explicit scope | PASS | Directly grounded in `/grill-me` choices `A1`–`A5`; non-goals preserve zero-build container simplicity and `REQ-068` GitHub isolation. |
| Observable requirements and meaningful acceptance criteria | PASS | `REQ-073..REQ-075` specify exact HTTP status codes (`200`/`400`/`401`/`403`/`404`/`409`), DB schema behavior, HTML/JS contracts (`node --check`), and `"4.5.0"` discovery alignment. |
| Assumptions and owner-deferred questions are explicit | PASS | Resolved `A1`–`A5`; zero `[PROVISIONAL]` items remain. |
| Architectural rationale respects requirements | PASS | `ADR-041` governs `REQ-073..REQ-075` cleanly. |
| IDs, references, and revision route are consistent | PASS | Uses `REQ-073..REQ-075` and `ADR-041` above `REQ-072`/`ADR-040`, targeting `[2026-11-23-collab-mcp-reader-threads-and-invite-lockdown/REQ-072]`. |
