# Design: Zero-Prompt MCP Onboarding, 3-Word `AGENT_KEY`, & Per-Book Access Control

Sources: [spec.md](./spec.md), [decisions.md](./decisions.md)

## Components

| Requirement / AC | Path and symbol | Responsibility / change |
| --- | --- | --- |
| REQ-044 / AC-044-1, AC-044-2 | `scripts/mcp_server/auth.py` (`mint_agent_key`, `generate_default_handle`, `resolve_auth`) | Mint readable 3-word passphrases (`word1-word2-word3`) with SHA-256 hash and `word1-***-word3` hint, generate default handles, touch `last_seen_at` on `AGENT_KEY` auth, and preserve legacy `cbt_ak_*` key support. |
| REQ-044, REQ-045 / AC-044-1, AC-045-1, AC-045-2 | `scripts/mcp_server/db.py` (`CollabStore.init_db`, `register_agent`, `list_agents`, `touch_agent_last_seen`, `resolve_agent_identifier`, `set_book_access`) | Additive columns (`agents.key_hint`, `agents.last_seen_at`, `books.access_mode`, `books.allowed_agents_json`), zero-arg agent registration with collision retry, and allowlist identifier resolution (`handle` or `agent_key`). |
| REQ-044, REQ-045 / AC-044-1, AC-044-2, AC-045-1, AC-045-2 | `scripts/mcp_server/service.py` (`CollabMcpService._can_access_book_work`, `_maybe_auto_assign_agent`, `list_tasks`, `get_chapter`, `claim_chapter`, `submit_translation`, `submit_review`, `list_agents`, `admin_mint_agent`, `set_book_access`) | Auto-mint 3-word `AGENT_KEY` on first anonymous `claim_chapter` / `submit_translation` on public books; enforce write-restricted `403` and `list_tasks` filtering on restricted books; expose admin agent & access management. |
| REQ-045, REQ-046 / AC-045-1, AC-045-2, AC-046-1, AC-046-2 | `scripts/mcp_server/app.py` (`MCP_SERVER_INSTRUCTIONS`, `create_app`, `_render_bookshelf_html`, `_render_admin_html`, `@mcp.prompt`) | Pass `instructions=MCP_SERVER_INSTRUCTIONS` to `MCPServer`, enrich tool docstrings, add `@mcp.prompt()` helpers, add `list_agents`/`set_book_access` MCP tools, render copy-ready onboarding card on `/`, and serve SSO-protected `/admin` HTML UI & `/admin/api/*` endpoints. |

## Interfaces and flow

1. **3-Word Key Minting**: `mint_agent_key()` selects 1 word each from three 128-word curated lists (`_ADJECTIVES`, `_NOUNS`, `_PLACES`) via `secrets.choice`, returning `(raw_key, sha256_hex(raw_key), f"{w1}-***-{w3}")`.
2. **Automatic Identity Assignment**: Calling `register_agent()` with `{}` generates a default handle (`<model_prefix>-<4hex>` or `agent-<4hex>`) and returns `{handle, display_name, model_info, agent_key, key_hint, created_at}`. Anonymous `claim_chapter` / `submit_translation` on a `public` book calls `_maybe_auto_assign_agent()` and returns `assigned_agent_key`, `assigned_handle`, and `auth_notice` alongside the normal response.
3. **Per-Book Access Control**: `books.access_mode` defaults to `"public"`. When set to `"restricted"`, `set_book_access` maps each entry in `allowed_agents` through `store.resolve_agent_identifier()` (accepting canonical `handle` OR raw `AGENT_KEY`) and saves deduplicated canonical `handle`s into `books.allowed_agents_json`.

## Boundaries and recovery

- **Zero secret leaks**: `agents` stores only `key_sha256` and `key_hint`; public responses (`GET /`, `/llms.txt`, `list_books`, `get_book`, `list_tasks`, `collab_ledger.json`) never output raw `AGENT_KEY`s or admin tokens.
- **Read-only crawler protection**: Read-only endpoints never call `_maybe_auto_assign_agent()`.
- **Restricted book anonymous write rejection**: `_can_access_book_work` runs **before** `_maybe_auto_assign_agent()`, ensuring unauthorized anonymous writes on restricted books fail with `403` without creating agent rows.

## Dependencies and unresolved design work

None.
