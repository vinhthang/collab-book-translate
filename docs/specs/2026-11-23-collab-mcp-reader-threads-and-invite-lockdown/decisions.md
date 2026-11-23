# Decisions: Collab Books MCP Invite-Only Lockdown, Human Web Reader, & Clean Agent Threads

Governing specification: [spec.md](./spec.md)

## Architectural rationale

- Governs: REQ-071, REQ-072

Rendering HTML Reader (`/read/*`, `/admin/read/*`) and Agent Threads (`/threads`, `/admin/threads`) directly in `scripts/mcp_server/app.py` with server-side HTML escaping (`html.escape`) and minimal client JS (the `Vietnamese Only` / `Bilingual EN-VI` toggle and `/admin` `Purge Test Clutter` button, both syntax-checked via `node --check`) reuses the existing PostgreSQL/SQLite persistence layer, Caddy Google OAuth2 Forward-Auth protection on `/admin*`, and single-container OCI deployment with zero external frontend build dependencies. Converting relative `images/...` links to `[Figure: <alt>]` caption callouts avoids broken image references in web chapters whose source images live locally during offline EPUB/PDF compilation.

## ADR-040: Default Invite-Only Auth Gate (`allow_anonymous_writes=False`) + Unconditional `ANONYMOUS` Block on `owner-permission` Books + `/admin/*` SSO Route Split

- Governs: REQ-070
- State: Accepted
- Context and constraints: Open self-registration (`POST /api/v1/agents/register`) and anonymous auto-minting (`_maybe_auto_assign_agent`) allowed any unauthenticated caller to mint keys and write chapters/reviews/bugs like a public forum, while `license_id="owner-permission"` books with `access_mode="public"` were still readable by `ANONYMOUS` API/MCP callers. Meanwhile, Caddy Forward-Auth protects `/admin*` for the owner's Google SSO session.
- Viable options:
  1. Default `allow_anonymous_writes=False` (requiring `ADMIN` to mint 3-word `AGENT_KEY`s and rejecting `ANONYMOUS` writes with `403`), unconditionally hide `owner-permission` books from `ANONYMOUS` API/MCP reads (`403` on detail/chapter/task queries, omitted from `list_books`), serve public open-license books on `/read/*` and `/threads`, and serve all books (including Ray Dalio's) under Google-SSO-protected `/admin/read/*` and `/admin/threads`.
  2. Put ALL Reader and Threads routes solely behind `/admin/*` without public `/read/*` or `/threads`.
  3. Require per-book `access_mode="restricted"` manually on every `owner-permission` book instead of automatically blocking `ANONYMOUS` at the license layer.
- Decision: Choose Option 1 (`allow_anonymous_writes=False` default + automatic license-based `ANONYMOUS` block + public vs `/admin/*` route split).
- Costs and risks: Existing unit tests written for the earlier open-anonymous era (`test_mcp_service_and_app.py`, `test_mcp_bug_triage_and_hardening.py`) must explicitly opt into `allow_anonymous_writes=True` or use `ADMIN` credentials when minting test agents; human visitors clicking `Read` on `/` can immediately read finished public-domain translations while copyright-restricted Dalio titles stay strictly behind Google SSO.
- Rejected alternatives: Option 2 leaves `/` with zero readable books for public visitors; Option 3 fails open if an `agent_admin` forgets to toggle `access_mode="restricted"`.
- Affected components: `scripts/mcp_server/config.py`, `scripts/mcp_server/db.py`, `scripts/mcp_server/service.py`, `scripts/mcp_server/app.py`.

## Gate 1: normative review

| Check | Status | Evidence / required correction |
| --- | --- | --- |
| Codebase grounding | PASS | Inspected `scripts/mcp_server/{config,auth,db,service,app}.py` at `3b783c6` and audited live `collab.vinhthang.dev` DB/API state. |
| Attributable intent and explicit scope | PASS | Grounded in verbatim prompt and resolved `A1`/`A2`/`A3`; non-goals preserve `AGENT_KEY` contributors and avoid broken static image assets. |
| Observable requirements and meaningful acceptance criteria | PASS | `REQ-070..REQ-072` define exact HTTP status codes (`403`/`404`/`422`), `<= 2,000`-char limit, purge regexes, HTML/JS checks, and `"4.4.0"` discovery metadata. |
| Assumptions and owner-deferred questions are explicit | PASS | Opt-in `allow_anonymous_writes=True` flag for legacy anonymous-mode tests recorded cleanly; zero `[PROVISIONAL]` items remain. |
| Architectural rationale respects requirements | PASS | `ADR-040` and rationale govern `REQ-070..REQ-072` without overriding frozen requirements silently. |
| IDs, references, and revision route are consistent | PASS | Linked delta package uses `REQ-070..REQ-072` and `ADR-040` above `REQ-069`/`ADR-039`. |
