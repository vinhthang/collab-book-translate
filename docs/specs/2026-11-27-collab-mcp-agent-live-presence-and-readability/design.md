# Design: Agent Live MCP Presence, Heartbeat, & Readable Console (`v4.7.0`)

Governing specification: [spec.md](./spec.md)

## Components

- `scripts/mcp_server/presence.py` (**NEW**):
  - `AgentPresenceTracker`, `compute_agent_presence`, `enrich_and_sort_agents`, `format_relative_time` (`REQ-079`).
- `scripts/mcp_server/auth.py`:
  - `generate_readable_handle(display_name, model_info)` and `resolve_auth(..., transport, action, client_name)` (`REQ-079`, `REQ-080`).
- `scripts/mcp_server/db.py`:
  - `agents` columns `last_transport`, `last_action`, `client_name`, `status_note` + SQLite/PostgreSQL migrations, `register_agent(..., role)`, `touch_agent_last_seen(..., transport, action, client_name, status_note)`, and `update_agent_profile` (`REQ-079`, `REQ-080`).
- `scripts/mcp_server/service.py`:
  - `CollabMcpService(..., presence)`, `list_agents_with_summary`, `admin_mint_agent(..., role)`, `update_agent_profile`, `agent_heartbeat` (`REQ-079`, `REQ-080`).
- `scripts/mcp_server/app.py`:
  - Presence-instrumented `RequestContextMiddleware` (`/sse` live connection tracking + `/mcp` JSON-RPC `initialize`/`ping`/`tools/*` interception), MCP tool `agent_heartbeat`, REST `POST /api/v1/agents/heartbeat`, `POST /admin/api/agents/{handle}/profile`, `GET /admin/api/agents` `summary`, and `"4.7.0"` discovery (`REQ-079`–`REQ-081`).
- `scripts/mcp_server/admin_ui.py` & `scripts/collab_worker.py`:
  - Readable Agents table, Live Status pills + relative time + `status_note`, Mint form with `#mint-role`, Edit drawer, status filter chips, 10s live polling with `#agents-tbody` event delegation (`node --check`), Overview KPI online count, and CLI `heartbeat` subcommand (`REQ-079`, `REQ-081`).

## Interfaces and flow

1. **Presence Instrumentation (`REQ-079`)**:
   - On `GET /sse`, `RequestContextMiddleware` checks valid Bearer `AGENT_KEY`: opens an SSE tracking entry (`presence.open_connection`), stamps `last_transport="mcp-sse"` / `action="sse:connect"`, and releases in `finally:` on disconnect.
   - On `POST /mcp` or `/messages/` with valid Bearer `AGENT_KEY`, middleware reads body bytes once, parses JSON-RPC `method` (`initialize`, `ping`, `tools/list`, `tools/call` -> `params.name`) and `params.clientInfo.name`, replays body bytes to Starlette ASGI `receive`, and touches DB presence (`transport="mcp-http"`).
   - Explicit `agent_heartbeat` updates `last_seen_at`, `status_note`, `last_transport`, `client_name`, and `last_action="heartbeat"`.
2. **Readable Identity & Live UI Refresh (`REQ-080`, `REQ-081`)**:
   - `/admin` initially renders the presence-sorted rows in SSR HTML (`<tbody id="agents-tbody">`) and polls `GET /admin/api/agents` every 10 seconds when `#tab-agents` is active, `#drawer-backdrop` is closed, `#chk-agents-autorefresh` is checked, and `!document.hidden`, updating rows cleanly via `escHtml()`.

## Boundaries and recovery

- **Rate-limit separation (`REQ-079`)**: `agent_heartbeat` checks `_check_read_rate(ctx)` instead of `_check_write_rate(ctx)` so heartbeats every 60s never throttle `submit_translation` or `submit_review`.
- **Body replay safety (`REQ-079`)**: Middleware replays exact raw bytes cleanly even on malformed JSON so Starlette/FastMCP parses identical bytes and error responses remain unchanged.
- **No accidental secret exposure (`REQ-080`, `REQ-081`)**: `list_agents` and `/admin/api/agents` never return `key_sha256` or raw `agent_key`, only masked `key_hint`.
