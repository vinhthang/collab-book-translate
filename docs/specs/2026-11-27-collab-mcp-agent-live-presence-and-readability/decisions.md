# Decisions: Agent Live MCP Presence, Heartbeat, & Readable Console (`v4.7.0`)

Governing specification: [spec.md](./spec.md)

## ADR-043: Hybrid In-Memory SSE + DB Activity Presence & Explicit Heartbeat over Stateful HTTP MCP Sessions

- Governs: REQ-079, REQ-080, REQ-081
- State: Accepted
- Context and constraints: `collab-mcp` serves Streamable HTTP `/mcp` with `stateless_http=True` alongside `/sse` and `/api/v1/*`. Admins need real-time visibility into whether agents are live, idle, or offline without losing stateless resilience across pod restarts or forcing all clients onto SSE.
- Viable options:
  1. Keep `/mcp` stateless (`stateless_http=True`), add `AgentPresenceTracker` for open `/sse` sockets, intercept authenticated `/mcp` (`initialize`, `ping`, `tools/list`, `tools/call`) & `/api/v1/*` activity into DB (`last_seen_at`, `last_transport`, `last_action`, `client_name`, `status_note`), and expose an explicit `agent_heartbeat` tool/REST/CLI command polled every 10s by `/admin#agents`.
  2. Switch `/mcp` to stateful `stateless_http=False` sessions in memory.
  3. Compute presence purely from chapter submissions without tracking MCP `initialize`/`ping`/SSE.
- Decision: Choose Option 1 (`Hybrid Live Presence + Explicit Heartbeat`).
- Costs and risks: Stateless HTTP clients passing `agent_key` solely in tool arguments (without HTTP `Authorization: Bearer`) update presence on tool calls rather than `initialize`, which is acceptable because the copyable `/admin` config snippet already injects `Authorization: Bearer` by default. Middleware buffering `/mcp` request bodies uses bounded async chunk replay to preserve FastMCP streaming semantics.
- Rejected alternatives: Option 2 breaks existing stateless clients and resets sessions on deploy; Option 3 fails to show idle watchers or newly connected agents before their first write.
- Affected components: `scripts/mcp_server/{presence,auth,db,service,app,admin_ui}.py`, `scripts/collab_worker.py`, `agents/{collab_admin,collab_tech_admin}.md`, `skills/collab-translate-book/SKILL.md`.

## Gate 1: normative review

| Check | Status | Evidence / required correction |
| --- | --- | --- |
| Codebase grounding | PASS | Inspected `scripts/mcp_server/{auth,db,service,app,admin_ui}.py` (`f459b3f`) confirming stateless `/mcp`, `/sse`, and current `agents` columns/UI. |
| Attributable intent and explicit scope | PASS | Grounded directly in `/grill-me` choices `A1`–`A3` plus approved heartbeat design; non-goals preserve stateless `/mcp` compatibility. |
| Observable requirements and meaningful acceptance criteria | PASS | `REQ-079..REQ-081` specify presence windows (`300s`/`1800s`), HTTP codes (`200`/`400`/`403`/`404`/`422`), relative-time/XSS UI rules (`node --check`), and `"4.7.0"` surface. |
| Assumptions and owner-deferred questions are explicit | PASS | Single-replica SSE note documented; zero `[PROVISIONAL]` items remain. |
| Architectural rationale respects requirements | PASS | `ADR-043` governs `REQ-079..REQ-081` cleanly. |
| IDs, references, and revision route are consistent | PASS | Uses `REQ-079..REQ-081` and `ADR-043` above `REQ-078`/`ADR-042`, targeting `REQ-073` and `REQ-078`. |
