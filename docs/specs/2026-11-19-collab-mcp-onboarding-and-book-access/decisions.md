# Decisions: Zero-Prompt MCP Onboarding, 3-Word `AGENT_KEY`, & Per-Book Access Control

Governing specification: [spec.md](./spec.md)

## Architectural rationale

- Governs: REQ-044, REQ-045, REQ-046

Embedding the complete workflow contract into MCP protocol metadata (`InitializeResult.instructions`, rich tool docstrings, and `@mcp.prompt()` templates) eliminates external prompt engineering for connecting agents (`REQ-046`). Adding additive `access_mode` / `allowed_agents_json` columns to `books` and `key_hint` / `last_seen_at` to `agents` preserves zero-downtime PostgreSQL upgrades (`REQ-045`).

## ADR-032: Hashed 3-word `AGENT_KEY` credentials paired with public canonical `handle`s for per-book allowlists

- Governs: REQ-044, REQ-045
- State: Accepted
- Context and constraints: The user wants automatically generated `AGENT_KEY`s that are simple and easy to read (`amber-fox-river`) and wants to restrict specific books to selected `AGENT_KEY`s, while keeping `vinhthang/collab-books` (`collab_ledger.json`) and Bookshelf endpoints public.
- Viable options:
  1. **Option 1 (3-word `AGENT_KEY` hashed via SHA-256 + separate public `handle` + allowlist resolver accepting both)**: Mint 3-word keys (`<adj>-<noun>-<place>`, 128³ = 2,097,152 combinations), store only `key_sha256` and masked `key_hint` (`<w1>-***-<w3>`), display canonical public `handle`s in `claimed_by`/`collab_ledger.json`, and allow `set_book_access` + `/admin` to accept either a 3-word `AGENT_KEY` (resolved via SHA-256) or a `handle` checkbox.
  2. **Option 2 (Use raw 3-word `AGENT_KEY` directly as public handle & store plaintext in PostgreSQL)**: Store and display `amber-fox-river` everywhere including `collab_ledger.json` and `claimed_by`.
- Decision: Choose **Option 1**.
- Costs and risks: Agents must save their one-time 3-word `AGENT_KEY` returned by `register_agent()` or first write; mitigated by `key_hint` visibility in `/admin` and one-click Admin minting of fresh keys if lost.
- Rejected alternatives: **Option 2** was rejected because publishing raw authentication keys into public `collab_ledger.json` commits or `get_book` responses would let any observer impersonate an allowlisted agent on restricted books.
- Affected components: `scripts/mcp_server/auth.py`, `scripts/mcp_server/db.py`, `scripts/mcp_server/service.py`, `scripts/mcp_server/app.py`.

## Gate 1: normative review

| Check | Status | Evidence / required correction |
| --- | --- | --- |
| Codebase grounding | PASS | Grounded against `collab-book-translate` commit `0ae2b74` (`scripts/mcp_server/auth.py:56`, `db.py:202`, `service.py`, `app.py:253`). |
| Attributable intent and explicit scope | PASS | Traces directly to verbatim user requests and `/grill-me` answers (Q1–Q4) plus explicit `implementation_plan.md` approval on 2026-11-19. |
| Observable requirements and meaningful acceptance criteria | PASS | `REQ-044..REQ-046` pair deterministic EARS statements with concrete success (`AC-*-1`) and boundary/failure (`AC-*-2`) criteria. |
| Assumptions and owner-deferred questions are explicit | PASS | Zero `[PROVISIONAL]` or unowned open questions remain; default `access_mode="public"` is recorded under Assumptions. |
| Architectural rationale respects requirements | PASS | `ADR-032` resolves the readable-key vs. public-repo credential leakage tradeoff cleanly while supporting allowlisting by either 3-word key or handle. |
| IDs, references, and revision route are consistent | PASS | Allocates fresh repo-wide `REQ-044..REQ-046` and `ADR-032` above `REQ-043`/`ADR-031` via linked delta without mutating frozen parent `REQ` bodies. |
