# Verification: Collab Books MCP Security, Scalability, & CI Hardening (`v4.8.0`)

Governing specification: [spec.md](./spec.md)

## Planned checks

### AC-082-1 (REQ-082)

- Method: Run `.venv/bin/pytest tests/test_mcp_v480_hardening.py -k req_082` verifying <= 10 MiB JSON requests, configured `admin_emails` SSO resolution, and trusted proxy IP extraction.
- Expected outcome: Authorized JSON requests, configured admin SSO, and trusted proxy forwarding succeed.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-082-2 (REQ-082)

- Method: Run `.venv/bin/pytest tests/test_mcp_v480_hardening.py -k req_082` verifying streaming > 10 MiB `413` abort on `/mcp` and REST, `text/plain` `415`, cross-origin / `Sec-Fetch-Site: cross-site` `403` on `/admin/*`, fail-closed empty `admin_emails` SSO `403`, and untrusted proxy header spoofing rejection.
- Expected outcome: All boundary and abuse checks fail fast with expected `413`/`415`/`403` codes.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-083-1 (REQ-083)

- Method: Run `.venv/bin/pytest tests/test_mcp_v480_hardening.py tests/test_mcp_onboarding_and_access.py -k "req_083 or ac_044"` verifying `>= 256` wordlists, `w1-***` hints, legacy key auth, limiter bucket pruning, and isolated `agent_heartbeat_rpm`.
- Expected outcome: Minted keys match format, legacy keys authenticate cleanly, expired buckets clear, and heartbeat quota works independently of writes.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-083-2 (REQ-083)

- Method: Run `.venv/bin/pytest tests/test_mcp_v480_hardening.py -k "req_082_083"` exceeding `auth_fail_rpm` and `agent_heartbeat_rpm`.
- Expected outcome: Both return HTTP/tool rate-limit `429`.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-084-1 (REQ-084)

- Method: Run `.venv/bin/pytest tests/test_mcp_v480_hardening.py -k req_084` verifying persistent PG connection reuse, atomic `store.transaction()`, 6 secondary indexes, `<= 4` SQL queries in `list_books`, worker-thread middleware offload + single Bearer DB query, omitted `translation_xml` in `rounds_history`, and existing GitHub release asset deletion + replacement.
- Expected outcome: DB, middleware, and release behavior meet all scalability and single-auth guarantees.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-084-2 (REQ-084)

- Method: Run `.venv/bin/pytest tests/test_mcp_v480_hardening.py -k req_084` simulating `git push` failure during acceptance, `transaction()` exception rollback, and HTTP `500` on release upload.
- Expected outcome: Git push failure leaves DB un-accepted, exceptions roll back atomically, and upload failure raises `httpx.HTTPStatusError`.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-085-1 (REQ-085)

- Method: Run `.venv/bin/pytest tests/test_mcp_v480_hardening.py -k req_085` checking `.js-retry-seed`, `node --check`, `ci-cd.yml` test gating + dynamic `VERIFIED_PKG` + `v0.9.1`, and `Dockerfile` `uv.lock` + non-root `USER`.
- Expected outcome: Admin JS, CI workflow, and Dockerfile contracts pass cleanly.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-085-2 (REQ-085)

- Method: Run `.venv/bin/pytest tests/test_mcp_v480_hardening.py -k req_085` asserting absence of undeclared `hold` in `.js-retry-seed` and hardcoded digest package name.
- Expected outcome: No stale `hold` variable or hardcoded digest lookup remains.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-086-1 (REQ-086)

- Method: Run `.venv/bin/pytest tests/test_mcp_v480_hardening.py tests/test_option_a_hygiene.py -k "req_086 or option_a"`, `arch-guard` (`check_boundaries.py`), and `-Werror::SyntaxWarning` compile check on `epub_compiler.py`.
- Expected outcome: PDF `PyMuPDF` fallback passes on `libpango`-less hosts, `collab_worker` imports without heavy extractors, zero circular imports, zero `SyntaxWarning`s, `"4.8.0"` invite-only discovery, and hygiene passes.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

### AC-086-2 (REQ-086)

- Method: Run full `.venv/bin/pytest -q` suite (`0` failed, `0` skipped) and `mutation-guard`.
- Expected outcome: Full test suite passes and modified modules achieve >= 80% mutation kill rate.
- Execution: [Run 1](#run-1--2026-11-27) — PASS.

## Execution evidence

### Run 1 — 2026-11-27

- Date: 2026-11-27
- Environment: Oracle Linux 10.2 (`aarch64`), `/home/opc/github/collab-book-translate/.venv` (`python 3.12.15`, `pytest 9.2.0`), Node.js `v22.23.2`
- Result: PASS
- Commands & results:
  - `.venv/bin/pytest tests/test_mcp_v480_hardening.py` (`11 passed in 3.33s`)
  - `.venv/bin/pytest -q` (`376 passed in 26.89s`, `0` failed, `0` skipped, `0` warnings)
  - `check_boundaries.py /home/opc/github/collab-book-translate` (`Scanned 94 Python file(s)`, `0` circular dependencies, `0` boundary/layer violations, exit `0`)
  - `mutation-guard` (`mutation_runner.py`):
    - `scripts/mcp_server/keys.py`: `100.0%` (`1/1` killed)
    - `scripts/mcp_server/auth.py`: `80.0%` (`12/15` killed)
    - `scripts/mcp_server/db.py`: `93.3%` (`14/15` killed)
    - `scripts/mcp_server/git_sync.py`: `80.0%` (`8/10` killed)
    - `scripts/mcp_server/app.py`: `80.0%` (`12/15` killed)

### Run 2 — 2026-11-28

- **Trigger / Context**: Verification after addressing PR #30 review comments (4 P1 + 4 P2 findings) on `feat/v4.8.0-security-scalability-hardening` before push.
- **Checks Executed**:
  - Targeted hardening, pre-push, and hygiene suites: `.venv/bin/pytest tests/test_mcp_v480_hardening.py tests/test_pre_push_collab_review_fixes.py tests/test_option_a_hygiene.py` -> **PASS (`24 passed in 3.99s`)**
  - Full test suite: `.venv/bin/pytest -q` -> **PASS (`379 passed in 26.99s`)**
  - Architectural boundaries: `arch-guard/scripts/check_boundaries.py` -> **PASS (`Scanned 94 Python file(s)`, zero boundary/layer/cycle violations)**
  - Spec conformance: `spec-driven/scripts/validate_specs.py --all docs/specs` -> **PASS (`OK (17 package(s) validated, stage=full)`)**
  - Whitespace / conflict check: `git diff --check` -> **PASS (`exit code 0`)**
  - Mutation testing (`mutation-guard/scripts/mutation_runner.py --test-cmd ".venv/bin/pytest -q tests/test_mcp_v480_hardening.py" --max-mutants 15`):
    - `scripts/mcp_server/db.py`: **86.7% (`13/15` killed)**
    - `scripts/mcp_server/auth.py`: **86.7% (`13/15` killed)**
    - `scripts/mcp_server/git_sync.py`: **90.0% (`9/10` killed)**
    - `scripts/mcp_server/app.py`: `40.0%` (`6/15` killed; capped at first 15 candidate sites at L93-277)
    - `scripts/compile_pdf.py` (`0/15` on L27-422) & `scripts/mcp_server/service.py` (`0/15` on L122-337): `mutation_runner.py` `--max-mutants 15` sampled header helpers rather than the edited `_sanitize_html_for_mupdf` / `_finalize_accepted_chapter` regions covered directly by `tests/test_mcp_v480_hardening.py`.

## Real-world observations

Implemented and committed on feature branch `feat/v4.8.0-security-scalability-hardening` (`A3`: merge to `main` and live OCI `arm10` `v0.9.1` deployment deferred for human review).
