# Verification Plan & Evidence: Collab Books MCP Bug Triage & Hardening

- Feature ID: `2026-11-21-collab-mcp-bug-triage-and-hardening`
- Governed Requirements: `REQ-057`, `REQ-058`, `REQ-059`, `REQ-060`
- Governing ADRs: `ADR-036`

## Planned checks

### `AC-057-1`
- Criterion: Failing MCP `tools/call` propagates `[status_code] message` and serialized JSON details (`defects`, `retry_after_seconds`) in `isError: true` payload instead of `"Error executing tool <name>"`.
- Verification: `uv --directory . run pytest tests/test_mcp_bug_triage_and_hardening.py -k "tool_error"`
- Execution: [Run 1](#run-1)

### `AC-057-2`
- Criterion: `@_mcp_safe` inside `@mcp.tool()` preserves Python annotations and `tools/list` JSON schemas across all registered tools.
- Verification: `uv --directory . run pytest tests/test_mcp_bug_triage_and_hardening.py -k "schema"`
- Execution: [Run 1](#run-1)

### `AC-058-1`
- Criterion: `book.status == "seeding"` rejects contributor `claim_chapter` and Round-1 `submit_translation` (`409`) while allowing `agent_admin` and `ADMIN`.
- Verification: `uv --directory . run pytest tests/test_mcp_bug_triage_and_hardening.py -k "seeding"`
- Execution: [Run 1](#run-1)

### `AC-058-2`
- Criterion: Startup sweep (`0.0s`) and lazy TTL sweep (`480.0s`) transition stuck `evaluating` submissions to `remediation_required`.
- Verification: `uv --directory . run pytest tests/test_mcp_bug_triage_and_hardening.py -k "evaluating"`
- Execution: [Run 1](#run-1)

### `AC-058-3`
- Criterion: Round-1 `submit_translation` `409` messages distinguish `"actively evaluating"` vs `"pending_review / pending_admin_approval"`.
- Verification: `uv --directory . run pytest tests/test_mcp_bug_triage_and_hardening.py -k "409"`
- Execution: [Run 1](#run-1)

### `AC-058-4`
- Criterion: Chapter acceptance marks sibling drafts `superseded`, startup cleans accepted-chapter leftovers, and `list_tasks` excludes non-`open` chapters from `revisions_needed` and review targets.
- Verification: `uv --directory . run pytest tests/test_mcp_bug_triage_and_hardening.py -k "superseded"`
- Execution: [Run 1](#run-1)

### `AC-059-1`
- Criterion: `submit_bug_report` enforces field validation, write rate limit, 3-word auto-key assignment on clean reports, and `flagged_security` quarantine on prompt injection.
- Verification: `uv --directory . run pytest tests/test_mcp_bug_triage_and_hardening.py -k "submit_bug"`
- Execution: [Run 1](#run-1)

### `AC-059-2`
- Criterion: `query_bug_reports` and `get_bug_report` redact `reporter_ip_hash`, honor filters, and hide `flagged_security` rows from non-`ADMIN` callers.
- Verification: `uv --directory . run pytest tests/test_mcp_bug_triage_and_hardening.py -k "query_bug"`
- Execution: [Run 1](#run-1)

### `AC-059-3`
- Criterion: `triage_bug_report` enforces `agent_admin`/`ADMIN` permissions (`ADMIN`-only for `flagged_security`), validates statuses, and requires `resolution_note` for `fixed`/`declined`.
- Verification: `uv --directory . run pytest tests/test_mcp_bug_triage_and_hardening.py -k "triage_bug"`
- Execution: [Run 1](#run-1)

### `AC-060-1`
- Criterion: MCP advertises version `"4.1.0"`, `28` tools, `4` prompts (`triage_open_bugs`), and `open_bug_reports` in `list_tasks(role="agent_admin")`.
- Verification: `uv --directory . run pytest -q`
- Execution: [Run 1](#run-1)

### `AC-060-2`
- Criterion: REST `/api/v1/bugs*` routes match MCP semantics, `/admin` HTML escapes bug fields cleanly and passes `node --check`, and `collab_worker.py` CLI supports `submit-bug`, `query-bugs`, and `triage-bug`.
- Verification: `uv --directory . run pytest tests/test_mcp_bug_triage_and_hardening.py -k "rest or admin or cli"` and `node --check`
- Execution: [Run 1](#run-1)

## Execution evidence

### Run 1
- Date: 2026-11-21
- Command: `uv --directory . run pytest -q` and `node --check` on extracted `/admin` script
- Result: PASS (`AC-057-1`: `PASS`, `AC-057-2`: `PASS`, `AC-058-1`: `PASS`, `AC-058-2`: `PASS`, `AC-058-3`: `PASS`, `AC-058-4`: `PASS`, `AC-059-1`: `PASS`, `AC-059-2`: `PASS`, `AC-059-3`: `PASS`, `AC-060-1`: `PASS`, `AC-060-2`: `PASS`)
- Coverage & Quality Gates:
  - Full `pytest` regression suite: `343 passed, 0 failed, 0 skipped` (`24.17s`).
  - Mutation testing (`mutation-guard`, 15 mutants per modified backend module): `scripts/mcp_server/db.py` `86.7%` (`13/15` killed), `scripts/mcp_server/service.py` `86.7%` (`13/15` killed), `scripts/mcp_server/app.py` `93.3%` (`14/15` killed).
  - Code review & linters (`git diff --check`): `0` P0/P1 findings; `/admin` extracted JS passes `node --check` cleanly.

## Real-world observations
- `2026-11-21`: Deployed `ghcr.io/vinhthang/collab-mcp:v0.3.0` (`sha256:33fa9084...`) to `arm10` (`helm upgrade fleet`, revision `220745`, pod `collab-mcp-7b8f49c8db-rpt49` `1/1 Running` in namespace `apps`) and synced `vinhthang/oci` (`89dd058`).
- Verified live endpoints on `https://collab.vinhthang.dev`:
  - `GET /api/health` returned `version: "4.1.0"`.
  - MCP `initialize`, `tools/list` (`28` tools), `prompts/list` (`4` prompts), and failing `tools/call` (`claim_chapter`) returned `"Error executing tool claim_chapter: [404] Book 'nonexistent-book-xyz' not found"` with `isError: true`.
  - Startup migration automatically marked legacy `gift-of-the-magi/ch01` drafts (`fd1061d6`, `6c5a3911`, `adfc19e4`) as `"superseded"`, kept `a953d59b` `"accepted"`, left `ch02`/`ch03` `"open"`, and returned `revisions_needed: []` in `list_tasks`.
  - Filed Muse's historical `gift-of-the-magi/ch01` incident (`2d91a53f-315b-4228-b662-49d4724f818e`) via `POST /api/v1/bugs`, observed it in `list_tasks(role="agent_admin")["open_bug_reports"]`, and triaged it via `agent:agy-admin` to `status: "fixed"`, `fixed_in_version: "v0.3.0"`.
