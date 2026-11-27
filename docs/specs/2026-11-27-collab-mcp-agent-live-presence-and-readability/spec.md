# Collab Books MCP Agent Live Presence, Heartbeat, & Readable `/admin#agents` Console (`collab-mcp:v0.9.0` / `MCP v4.7.0`)

- Feature ID: 2026-11-27-collab-mcp-agent-live-presence-and-readability
- Type: enhancement
- Status: In-Testing
- First entered In-Testing: 2026-11-27
- Related specifications: Implements: Changes [2026-11-27-collab-mcp-admin-console-rework](../2026-11-27-collab-mcp-admin-console-rework/spec.md), Changes [2026-11-27-collab-mcp-tech-admin-bug-workflow](../2026-11-27-collab-mcp-tech-admin-bug-workflow/spec.md)

## Problem and source intent

Observed state on `https://collab.vinhthang.dev/admin#agents` (`v4.6.0` / `v0.8.0`):
1. **No live MCP connection visibility**: The green `Active` badge next to masked key hints (e.g. `sound-***-cliff Active`) only indicates *"not revoked"*, telling the admin nothing about whether an agent is connected to `/sse`, sending MCP `initialize`/`ping`/`tools/*` traffic, running a worker heartbeat, idle, or offline. `/mcp` `initialize`/`tools/list`/`ping` and open `/sse` streams are not tracked in `agents`.
2. **Hard-to-read agent identity**: The Agents table shows the auto-generated machine `handle` in monospace as the primary label, buries `display_name` underneath, defaults blank handles to random hex (`agent-3f92`), prints raw ISO timestamps (`2026-11-27T09:12:44.123456+00:00`), and offers no way to rename an existing agent cleanly.

Authorizing user intent (2026-11-27, prompt + `/grill-me` choices `A1`–`A3` + heartbeat follow-up):
- **Prompt**: *"review this list, I think need to have a live state of registered agents, I think I want to see an agent is connected to the mcp server or not ... this say nothing, sound-***-cliff Active, and why the name is so hard to read"* + *"what about heart beat check?"*
- **A1 (Hybrid Live Presence + Heartbeat)**: Track live `/sse` streams (`Connected`), authenticated `/mcp` calls (`initialize`, `ping`, `tools/list`, `tools/call`) & REST requests (`Online <= 5m`, `Idle <= 30m`, `Offline > 30m`, `Never connected`), explicit `agent_heartbeat` MCP tool + `POST /api/v1/agents/heartbeat` + `collab_worker.py heartbeat` (with optional `status_note`), and auto-refresh `/admin#agents` every 10 seconds.
- **A2 (Readable Identity & Key Separation)**: Bold human-readable `display_name` first with `@handle`, `model_info`, and subtle masked key hint below; drop misleading `Active` badge (`Revoked` shown only when revoked); human-readable relative time; inline `Edit` drawer (`POST /admin/api/agents/{handle}/profile`).
- **A3 (Full Agents Ergonomics)**: Presence-first sorting (`Connected` -> `Online` -> `Idle` -> `Offline` -> `Never` -> `Revoked`), filter chips (`All`, `Online`, `Idle`, `Offline`, `Revoked`), Overview KPI `X online · Y total`, and Mint form ordered `Display Name` -> `Model` -> `Role` -> `Optional Handle` (readable slug without hex when unused).

## Repository grounding

Inspected at `/Users/thanghoang/github/collab-book-translate` (`f459b3f`):
- `scripts/mcp_server/db.py`: `agents` schema lacks `last_transport`, `last_action`, `client_name`, `status_note`; `register_agent` generates hex handles on blank input and provides no `update_agent_profile` helper.
- `scripts/mcp_server/auth.py`: `resolve_auth` calls `touch_agent_last_seen(handle)` without recording transport/action/client_name.
- `scripts/mcp_server/app.py`: `RequestContextMiddleware` only captures ContextVars without inspecting Bearer headers on `/sse` / `/mcp` (`initialize`, `ping`, `tools/list`, `tools/call`); no `/admin/api/agents/{handle}/profile` or `/api/v1/agents/heartbeat` routes exist.
- `scripts/mcp_server/admin_ui.py`: `#agents` renders handle-first `<code>`, `sound-***-cliff Active`, raw ISO `last_seen_at`, no status filter chips, no Edit action, and no 10s live refresh loop.

## Non-goals

- **No stateful-session lock-in on `/mcp`**: Keep `stateless_http=True` on `/mcp` so horizontal/pod restarts never break stateless HTTP MCP clients (`ADR-043`).
- **No synthetic `ping` from server to NATed client**: Server cannot initiate inbound probes to local IDE agents without an open `/sse` stream; presence relies on live `/sse`, MCP `ping`/requests, and `agent_heartbeat`.

## Requirements

### REQ-079: Hybrid Live MCP Presence Tracker, Protocol `ping`, & Explicit `agent_heartbeat`

- Source intent: Resolved `A1` + heartbeat follow-up (*"Hybrid Presence: track open /sse streams as Connected (live), track authenticated /mcp ... and tool/REST calls as Online (within 5m) / Idle (5-30m) / Offline (over 30m) / Never Connected"* + *"auto-track MCP JSON-RPC ping PLUS explicit agent_heartbeat"*).
- Statement: `scripts/mcp_server/presence.py` SHALL implement thread-safe `AgentPresenceTracker` (`open_connection`, `close_connection`, `active_connection_count`) and deterministic `compute_agent_presence` classifying each agent into `"revoked"` (`revoked_at != ""`), `"connected"` (`live_connections > 0`), `"online"` (`<= 300s` since `last_seen_at`), `"idle"` (`301..1800s`), `"offline"` (`> 1800s`), or `"never"` (`last_seen_at == ""`). `agents` persistence (`db.py`) SHALL add idempotent SQLite/PostgreSQL columns `last_transport`, `last_action`, `client_name`, and `status_note` (`TEXT NOT NULL DEFAULT ''`). `RequestContextMiddleware` (`app.py`) SHALL intercept valid Bearer keys on `/sse` (incrementing `AgentPresenceTracker` while the stream stays open and decrementing in `finally:`), `/mcp`, and `/messages/` (recording `initialize` `clientInfo.name`, `ping`, `tools/list`, and `tools/call` method names without breaking streaming/body consumption). `collab-mcp` SHALL expose MCP tool `agent_heartbeat(status_note="", client_name="", agent_key="")`, REST `POST /api/v1/agents/heartbeat`, and CLI `collab_worker.py heartbeat`, requiring `AGENT_KEY` (`403` otherwise), rate-limited against read quota (`_check_read_rate`) so translation writes are unaffected, validating `len(status_note) <= 140` (`400`) and `detect_prompt_injection` (`422`).
- AC-079-1 — Success: Open `/sse` streams report `connection_state == "connected"` (`live_connections == 1`) and transition to `"online"` on close; authenticated `/mcp` `initialize`, `ping`, and `tools/list` populate `last_seen_at`, `last_transport`, `last_action`, and `client_name`; `agent_heartbeat` updates `status_note` cleanly via MCP, REST, and `collab_worker.py`; presence buckets match `<= 300s` (`online`), `301..1800s` (`idle`), `> 1800s` (`offline`), empty (`never`), and `revoked`.
- AC-079-2 — Boundary/failure: `ANONYMOUS` or key-less `ADMIN` callers calling `agent_heartbeat` receive `403`; `status_note > 140` chars returns `400`; prompt-injection `status_note` returns `422`; invalid Bearer headers on `/sse` or `/mcp` never crash middleware or leak agent details.

### REQ-080: Readable Agent Handles, Initial Role Minting, & Inline Profile Editing

- Changes: [2026-11-27-collab-mcp-admin-console-rework/REQ-073](../2026-11-27-collab-mcp-admin-console-rework/spec.md#req-073-agent-key-lifecycle-revoked_at-rotate-key-soft-revoke--delete--contribution-stats)
- Source intent: Resolved `A2` & `A3` (*"show human-readable Display Name in bold ... add an Edit button to rename existing agents' display_name/model_info"*, *"Display Name first -> auto-generates clean readable slug handle without random hex when available, plus initial Role selector"*).
- Previous contract: Minting with blank `handle` always appended random hex suffix (`model-a1b2`) and forced a separate call to assign non-contributor roles; existing agents could not edit `display_name` or `model_info`; `GET /admin/api/agents` returned unsorted flat dicts without live presence or `summary`.
- Statement: `auth.py` and `db.py` SHALL implement `generate_readable_handle(display_name, model_info)` so minting with blank `handle` selects the clean slug (without hex) when valid and unused, falling back to `generate_default_handle` on collision. `admin_mint_agent` and `POST /admin/api/agents/mint` (`/api/v1/admin/agents/mint`) SHALL accept optional `role` validated against `VALID_AGENT_ROLES` (`400` otherwise). `CollabMcpService.update_agent_profile` (`POST /admin/api/agents/{handle}/profile` and `/api/v1/admin/agents/{handle}/profile`, `assert_admin`) SHALL update `display_name` (`1..80` chars if provided, `400` otherwise) and `model_info` (`0..80` chars, `400` otherwise), reject prompt-injection payloads with `422`, and return `404` for unknown agents. `GET /admin/api/agents` and `/api/v1/admin/agents` SHALL return presence-sorted `agents` alongside `summary` (`total`, `connected`, `online`, `idle`, `offline`, `never`, `revoked`).
- AC-080-1 — Success: Minting `"Claude Sonnet"` with blank `handle` allocates `"claude-sonnet"` and supports assigning `"tech_admin"` at mint; duplicate `"Claude Sonnet"` falls back cleanly; `/profile` updates `display_name` and `model_info` while preserving the active `AGENT_KEY` and stats; `/admin/api/agents` returns presence-sorted agents and accurate `summary`.
- AC-080-2 — Boundary/failure: Non-`ADMIN` callers calling `/profile` receive `403`; unknown handles return `404`; blank/overlong names or invalid roles return `400`; prompt-injection strings return `422`.

### REQ-081: Readable `/admin#agents` UI, Live 10s Auto-Refresh, Overview Online KPI, & `v4.7.0`

- Changes: [2026-11-27-collab-mcp-tech-admin-bug-workflow/REQ-078](../2026-11-27-collab-mcp-tech-admin-bug-workflow/spec.md#req-078-mcprestcli-claim-surface-admin-ui-3-role--pr-controls-collab_tech_admin-agent--v460)
- Source intent: Resolved `A1`–`A3` (*"auto-refresh the Agents table every 10s"*, *"show X online / Y total on Overview KPI"*, *"remove misleading Active badge"*).
- Previous contract: `/admin#agents` showed `handle` in bold `<code>` ahead of `display_name`, paired `key_hint` with a misleading green `Active` badge, printed raw ISO `last_seen_at`, had no live polling, no presence sorting, no status filter chips, and no Edit button; Overview KPI omitted online count; server/plugin advertised `"4.6.0"` (`v0.8.0`).
- Statement: `scripts/mcp_server/admin_ui.py` SHALL render `/admin#agents` sorted presence-first (`Connected` -> `Online` -> `Idle` -> `Offline` -> `Never` -> `Revoked`) with bold `display_name` primary labels, muted `@handle` / `model_info` / `key_hint` (rendering `Revoked` badge ONLY when `revoked_at != ""` and NEVER a misleading `Active` badge), a dedicated **Live Status** column with colored dot badge + relative time (`format_relative_time`, exact UTC in `title`) + last transport/action/`status_note`, status filter chips (`All`, `Online`, `Idle`, `Offline`, `Revoked`), Mint form ordered `#mint-display`, `#mint-model`, `#mint-role`, `#mint-handle`, inline `Edit` drawer (`.js-edit-agent`), manual **Refresh** (`#btn-refresh-agents`), 10-second live poller (`#chk-agents-autorefresh`, pausing while drawer is open or tab hidden) backed by event delegation on `#agents-tbody`, and Overview KPI `#kpi-agents-online` (`"{online} online · {total} total"`). Inline JS SHALL pass `node --check` cleanly and HTML-escape all user/client strings. Server, `app.py`, `plugin.json`, `.github/workflows/ci-cd.yml` (`v0.9.0`), `SKILL.md`, and `agents/*.md` SHALL advance to `"4.7.0"`.
- AC-081-1 — Success: `/admin` renders bold `display_name`, live presence pill/dot, relative time, `status_note`, Edit action, status chips, `#mint-role`, `#kpi-agents-online`, presence-first ordering, and `node --check` exit `0`; `/api/health`, `/llms.txt`, `plugin.json`, and MCP `initialize` report `"4.7.0"`.
- AC-081-2 — Boundary/failure: Non-revoked agents do NOT render the misleading `Active` badge; raw `<script>` payloads in `display_name`, `status_note`, `client_name`, and `last_action` are HTML-escaped cleanly; unauthenticated `/admin/api/agents/*/profile` remains `403` and full `pytest` suite passes cleanly.

## Assumptions and owner-deferred questions

- Horizontal multi-replica scaling is not active (`vinhthang/oci` runs a single `collab-mcp` replica on `arm10`), so in-memory SSE tracking in `AgentPresenceTracker` is authoritative alongside durable DB `last_seen_at` / `status_note`. No open `[PROVISIONAL]` items remain.

## Approvals and lifecycle history

- `2026-11-27`: `Planning -> In-Progress` — User selected `A1`–`A3` via `/grill-me` and approved adding hybrid MCP `ping` + explicit `agent_heartbeat` before authorizing execution (`collab-mcp:v0.9.0` / `MCP v4.7.0`).
- `2026-11-27`: `In-Progress -> In-Testing` — Completed Stages 1–3 (`365 passed, 6 warnings, 0 failed, 0 skipped` full regression suite, `tests/test_option_a_hygiene.py` `2 passed`, `mutation-guard` `100.0%` (`15/15`) on `presence.py`, `93.3%` (`14/15`) on `auth.py`, `100.0%` (`15/15`) on `db.py`, `80.0%` (`12/15`) on `service.py`, `100.0%` (`15/15`) on `admin_ui.py`, `node --check` exit `0`, single-agent `code-review` `Approved` `0` P0/P1) and updated 1-hop `- Current contract:` links on `REQ-073` and `REQ-078`. See [Run 1](./verification.md#run-1--2026-11-27).
