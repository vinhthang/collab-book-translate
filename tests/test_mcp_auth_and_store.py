"""TDD tests for Task 2: CollabStore persistence, 3-tier auth, and pre-LLM rate/token guards."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import pytest

from scripts.mcp_server.auth import (
    AuthError,
    RateLimitError,
    SlidingWindowRateLimiter,
    assert_admin,
    assert_not_self_review,
    mint_revision_token,
    resolve_auth,
    verify_revision_token,
)
from scripts.mcp_server.config import McpServerConfig
from scripts.mcp_server.db import CollabStore


def _make_config(tmp_path: Path, **overrides) -> McpServerConfig:
    defaults = {
        "database_url": "",
        "sqlite_path": tmp_path / "collab_test.sqlite3",
        "books_repo_dir": tmp_path / "collab-books",
        "books_repo_full_name": "vinhthang/collab-books",
        "review_server_url": "https://review.vinhthang.dev",
        "review_bearer_token": "rs-secret-token",
        "collab_admin_token": "admin-secret-token-12345",
        "admin_emails": frozenset({"owner@vinhthang.dev"}),
        "trust_admin_sso_header": True,
        "github_token": "",
        "bot_git_name": "collab-bot",
        "bot_git_email": "collab-bot@users.noreply.github.com",
        "anon_read_rpm": 120,
        "anon_write_rpm": 5,
        "agent_write_rpm": 20,
        "claim_ttl_seconds": 3600,
        "fallback_hours": 48.0,
        "max_revise_rounds": 3,
    }
    defaults.update(overrides)
    return McpServerConfig(**defaults)


def test_ac_038_2_idempotent_init_db_preserves_state(tmp_path: Path):
    cfg = _make_config(tmp_path)
    store = CollabStore(cfg)
    store.init_db()

    reg = store.register_agent(handle="alpha-translator", display_name="Alpha", model_info="gemini")
    assert reg["handle"] == "alpha-translator"
    assert reg["agent_key"].startswith("cbt_ak_")

    store.upsert_book(
        slug="demo-book",
        title="Demo Book",
        author="Demo Author",
        source_lang="en",
        target_lang="vi",
        genre="fiction",
        license_id="public-domain",
        license_url="https://example.org/pd",
        status="active",
        glossary_sha="a" * 64,
        book_record={"characters": {}, "terms": {}},
        manifest={"slug": "demo-book"},
        proposed_by="admin",
    )

    # Re-running init_db() twice must be 100% idempotent and preserve existing rows
    store.init_db()
    store.init_db()

    book = store.get_book("demo-book")
    assert book is not None
    assert book["title"] == "Demo Book"
    agent = store.get_agent_by_key(reg["agent_key"])
    assert agent is not None
    assert agent["handle"] == "alpha-translator"
    store.close()


def test_ac_039_1_three_tier_auth_and_hashed_agent_keys(tmp_path: Path):
    cfg = _make_config(tmp_path)
    store = CollabStore(cfg)
    store.init_db()

    # 1. ANONYMOUS resolution with IP fingerprinting
    anon_ctx = resolve_auth(
        store=store,
        cfg=cfg,
        headers={"cf-connecting-ip": "203.0.113.42"},
        request_path="/mcp",
    )
    assert anon_ctx.tier == "ANONYMOUS"
    assert anon_ctx.actor_id.startswith("anon:")
    assert "203.0.113.42" not in anon_ctx.actor_id  # hashed, never leaks raw IP

    # 2. Self-serve AGENT_KEY registration stores SHA-256 digest only
    reg = store.register_agent(handle="beta-bot", display_name="Beta Bot", model_info="claude")
    raw_key = reg["agent_key"]
    raw_conn = sqlite3.connect(cfg.sqlite_path)
    stored_row = raw_conn.execute("SELECT key_sha256 FROM agents WHERE handle = ?", ("beta-bot",)).fetchone()
    raw_conn.close()
    assert stored_row is not None
    assert stored_row[0] != raw_key
    assert len(stored_row[0]) == 64

    # Authenticate via HTTP Authorization header
    hdr_ctx = resolve_auth(
        store=store,
        cfg=cfg,
        headers={"authorization": f"Bearer {raw_key}"},
        request_path="/api/v1/tasks",
    )
    assert hdr_ctx.tier == "AGENT_KEY"
    assert hdr_ctx.actor_id == "agent:beta-bot"
    assert hdr_ctx.handle == "beta-bot"

    # Authenticate via zero-header MCP tool argument agent_key
    arg_ctx = resolve_auth(
        store=store,
        cfg=cfg,
        headers={},
        request_path="/mcp",
        agent_key_arg=raw_key,
    )
    assert arg_ctx.tier == "AGENT_KEY"
    assert arg_ctx.actor_id == "agent:beta-bot"

    # 3. ADMIN via Bearer token, admin_token tool arg, and /admin/* SSO email
    admin_bearer = resolve_auth(
        store=store,
        cfg=cfg,
        headers={"authorization": f"Bearer {cfg.collab_admin_token}"},
        request_path="/mcp",
    )
    assert admin_bearer.tier == "ADMIN"
    assert_admin(admin_bearer)

    admin_arg = resolve_auth(
        store=store,
        cfg=cfg,
        headers={},
        request_path="/mcp",
        admin_token_arg=cfg.collab_admin_token,
    )
    assert admin_arg.tier == "ADMIN"
    assert_admin(admin_arg)

    admin_sso = resolve_auth(
        store=store,
        cfg=cfg,
        headers={"x-auth-request-email": "owner@vinhthang.dev"},
        request_path="/admin/books",
    )
    assert admin_sso.tier == "ADMIN"
    assert_admin(admin_sso)
    store.close()


def test_ac_039_2_invalid_tokens_and_spoofed_sso_headers_rejected(tmp_path: Path):
    cfg = _make_config(tmp_path)
    store = CollabStore(cfg)
    store.init_db()

    # Forged X-Auth-Request-Email on /mcp or /api/v1/* MUST be ignored (remains ANONYMOUS -> 403 on admin)
    for pub_path in ("/mcp", "/sse", "/api/v1/books", "/llms.txt"):
        spoof_ctx = resolve_auth(
            store=store,
            cfg=cfg,
            headers={"x-auth-request-email": "owner@vinhthang.dev"},
            request_path=pub_path,
        )
        assert spoof_ctx.tier == "ANONYMOUS"
        with pytest.raises(AuthError) as exc_info:
            assert_admin(spoof_ctx)
        assert exc_info.value.status_code == 403

    # Unknown Bearer token -> 401
    with pytest.raises(AuthError) as exc_401:
        resolve_auth(
            store=store,
            cfg=cfg,
            headers={"authorization": "Bearer invalid-token"},
            request_path="/mcp",
        )
    assert exc_401.value.status_code == 401

    # Wrong admin_token arg -> 401
    with pytest.raises(AuthError) as exc_bad_admin:
        resolve_auth(
            store=store,
            cfg=cfg,
            headers={},
            request_path="/mcp",
            admin_token_arg="wrong-admin-secret",
        )
    assert exc_bad_admin.value.status_code == 401

    # Duplicate handle registration -> ValueError / conflict
    store.register_agent(handle="dup-bot")
    with pytest.raises(ValueError, match="already registered"):
        store.register_agent(handle="dup-bot")

    store.close()


def test_ac_040_rate_limits_revision_token_and_anti_self_review():
    limiter = SlidingWindowRateLimiter()
    for i in range(3):
        limiter.check("write:anon:abc", limit=3, window_seconds=60.0, now=1000.0 + i)
    with pytest.raises(RateLimitError) as rl_exc:
        limiter.check("write:anon:abc", limit=3, window_seconds=60.0, now=1005.0)
    assert rl_exc.value.status_code == 429

    # Window expiry allows requests again
    limiter.check("write:anon:abc", limit=3, window_seconds=60.0, now=1065.0)

    # Revision token SHA-256 verification
    raw_rev, rev_sha = mint_revision_token()
    assert raw_rev.startswith("cbt_rt_")
    assert len(rev_sha) == 64
    verify_revision_token(raw_rev, rev_sha)
    with pytest.raises(AuthError) as rev_err:
        verify_revision_token("cbt_rt_wrong", rev_sha)
    assert rev_err.value.status_code == 403
    with pytest.raises(AuthError) as missing_err:
        verify_revision_token("", rev_sha)
    assert missing_err.value.status_code == 403

    # Self-review block
    assert_not_self_review("anon:reviewer1", "anon:author1")
    with pytest.raises(AuthError) as self_err:
        assert_not_self_review("anon:same", "anon:same")
    assert self_err.value.status_code == 403
