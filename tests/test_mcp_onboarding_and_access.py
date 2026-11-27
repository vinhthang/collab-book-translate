"""TDD tests for REQ-044, REQ-045, REQ-046: 3-word AGENT_KEY, per-book allowlists, MCP instructions & /admin UI."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from scripts.mcp_server.app import create_app
from scripts.mcp_server.auth import mint_agent_key, resolve_auth, sha256_hex
from scripts.mcp_server.config import McpServerConfig
from scripts.mcp_server.db import CollabStore
from scripts.mcp_server.git_sync import CollabBooksGitSync
from scripts.mcp_server.service import CollabMcpService
from tests.test_mcp_service_and_app import FakeReviewServerClient, _build_env

THREE_WORD_KEY_RE = re.compile(r"^[a-z]+-[a-z]+-[a-z]+$")


def test_ac_044_1_three_word_key_zero_arg_register_and_legacy_compat(tmp_path: Path):
    cfg, store, _, _ = _build_env(tmp_path)

    # 1. mint_agent_key produces readable 3-word passphrase, SHA-256 hash, and masked key_hint
    raw_k, k_sha, k_hint = mint_agent_key()
    assert THREE_WORD_KEY_RE.match(raw_k), f"Expected 3-word key, got {raw_k}"
    assert len(k_sha) == 64 and k_sha == sha256_hex(raw_k)
    parts = raw_k.split("-")
    assert k_hint == f"{parts[0]}-***"

    # 2. Zero-arg register_agent() auto-generates handle and 3-word key; DB stores hash + hint only
    reg = store.register_agent(model_info="claude-sonnet")
    assert THREE_WORD_KEY_RE.match(reg["agent_key"])
    assert reg["handle"].startswith("claude-")
    assert reg["key_hint"].endswith("-***") and reg["key_hint"].count("-") == 1

    raw_conn = sqlite3.connect(cfg.sqlite_path)
    row = raw_conn.execute(
        "SELECT key_sha256, key_hint, last_seen_at FROM agents WHERE handle = ?",
        (reg["handle"],),
    ).fetchone()
    raw_conn.close()
    assert row is not None
    assert row[0] == sha256_hex(reg["agent_key"])
    assert row[0] != reg["agent_key"]
    assert row[1] == reg["key_hint"]

    # 3. Authenticating with the 3-word key resolves to AGENT_KEY and updates last_seen_at
    ctx = resolve_auth(
        store=store,
        cfg=cfg,
        headers={"authorization": f"Bearer {reg['agent_key']}"},
        request_path="/mcp",
    )
    assert ctx.tier == "AGENT_KEY"
    assert ctx.handle == reg["handle"]
    agents = store.list_agents()
    assert len(agents) == 1
    assert agents[0]["last_seen_at"] != ""

    # 4. Legacy cbt_ak_* hashed keys remain backward-compatible
    legacy_raw = "cbt_ak_" + ("ab" * 24)
    store._exec(
        "INSERT INTO agents (handle, display_name, model_info, key_sha256, key_hint, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("legacy-bot", "Legacy Bot", "old", sha256_hex(legacy_raw), "cbt_ak_***", "2026-11-19T00:00:00Z", ""),
    )
    legacy_ctx = resolve_auth(
        store=store,
        cfg=cfg,
        headers={},
        request_path="/mcp",
        agent_key_arg=legacy_raw,
    )
    assert legacy_ctx.tier == "AGENT_KEY"
    assert legacy_ctx.handle == "legacy-bot"

    # 5. resolve_agent_identifier accepts both 3-word AGENT_KEY and canonical handle
    assert store.resolve_agent_identifier(reg["agent_key"]) == reg["handle"]
    assert store.resolve_agent_identifier(reg["handle"]) == reg["handle"]
    assert store.resolve_agent_identifier("nonexistent-word-key") is None
    store.close()


def test_ac_044_auto_mint_on_first_anonymous_write_and_zero_mint_on_reads(tmp_path: Path):
    cfg, store, rs_client, svc = _build_env(tmp_path)
    src_xml = '<segment id="S-0001" type="paragraph">\n<original>\nDawn broke.\n</original>\n</segment>\n'
    tr_xml = (
        '<segment id="S-0001" type="paragraph">\n<original>\nDawn broke.\n</original>\n'
        '<translate>\nBình minh ló rạng trên bầu trời.\n</translate>\n</segment>\n'
    )
    store.upsert_book(
        slug="open-book",
        title="Open Book",
        author="Author",
        source_lang="en",
        target_lang="vi",
        genre="fiction",
        license_id="public-domain",
        license_url="https://example.org",
        status="active",
        glossary_sha="a" * 64,
        book_record={},
        manifest={"slug": "open-book", "source_lang": "en", "target_lang": "vi"},
        proposed_by="admin",
    )
    store.upsert_chapter(
        book_slug="open-book",
        chapter_id="ch02",
        chapter_num=2,
        title="Ch 2",
        source_xml=src_xml,
        status="open",
    )

    app = create_app(cfg=cfg, service=svc)
    with TestClient(app) as client:
        # Read-only endpoints MUST NOT create any agent rows (AC-044-2)
        assert client.get("/").status_code == 200
        assert client.get("/api/v1/books").status_code == 200
        assert client.get("/api/v1/tasks").status_code == 200
        assert store.list_agents() == []

        # First anonymous claim_chapter on a public book auto-mints a 3-word AGENT_KEY (AC-044-1)
        claim_res = client.post("/api/v1/books/open-book/chapters/ch02/claim")
        assert claim_res.status_code == 200
        claim_body = claim_res.json()
        assigned_key = claim_body["assigned_agent_key"]
        assigned_handle = claim_body["assigned_handle"]
        assert THREE_WORD_KEY_RE.match(assigned_key)
        assert claim_body["claimed_by"] == f"agent:{assigned_handle}"
        assert assigned_key not in claim_body["claimed_by"]

        # Subsequent anonymous submit_translation also auto-mints when called without agent_key
        sub_res = client.post(
            "/api/v1/submissions",
            json={
                "book_slug": "open-book",
                "chapter_id": "ch02",
                "translation_xml": tr_xml,
                "engine_model": "gemini-flash",
            },
        )
        assert sub_res.status_code == 200
        sub_body = sub_res.json()
        assert THREE_WORD_KEY_RE.match(sub_body["assigned_agent_key"])
        assert sub_body["submission"]["author_handle"] == sub_body["assigned_handle"]
        assert sub_body["assigned_agent_key"] != sub_body["submission"]["author_handle"]
    store.close()


def test_ac_045_per_book_restricted_allowlist_and_admin_ui(tmp_path: Path):
    cfg, store, rs_client, svc = _build_env(tmp_path)
    src_xml = '<segment id="S-0001" type="paragraph">\n<original>\nSecret chapter.\n</original>\n</segment>\n'
    tr_xml = (
        '<segment id="S-0001" type="paragraph">\n<original>\nSecret chapter.\n</original>\n'
        '<translate>\nChương bí mật đã được dịch chuẩn xác.\n</translate>\n</segment>\n'
    )
    store.upsert_book(
        slug="vip-book",
        title="VIP Book",
        author="Author",
        source_lang="en",
        target_lang="vi",
        genre="fiction",
        license_id="public-domain",
        license_url="https://example.org",
        status="active",
        glossary_sha="b" * 64,
        book_record={},
        manifest={"slug": "vip-book", "source_lang": "en", "target_lang": "vi"},
        proposed_by="admin",
    )
    store.upsert_chapter(
        book_slug="vip-book",
        chapter_id="ch02",
        chapter_num=2,
        title="Ch 2",
        source_xml=src_xml,
        status="open",
    )

    alice = store.register_agent(handle="alice-bot", model_info="claude")
    bob = store.register_agent(handle="bob-bot", model_info="gemini")
    admin_hdr = {"authorization": f"Bearer {cfg.collab_admin_token}"}
    sso_hdr = {"x-auth-request-email": "admin@vinhthang.dev"}

    app = create_app(cfg=cfg, service=svc)
    with TestClient(app) as client:
        # 1. Admin restricts vip-book using Alice's 3-word AGENT_KEY directly!
        set_res = client.post(
            "/api/v1/admin/books/vip-book/access",
            headers=admin_hdr,
            json={"access_mode": "restricted", "allowed_agents": [alice["agent_key"]]},
        )
        assert set_res.status_code == 200
        assert set_res.json()["book"]["access_mode"] == "restricted"
        assert set_res.json()["book"]["allowed_agents"] == ["alice-bot"]

        # Invalid mode or unknown agent -> 400 (AC-045-2)
        assert (
            client.post(
                "/api/v1/admin/books/vip-book/access",
                headers=admin_hdr,
                json={"access_mode": "invalid-mode", "allowed_agents": []},
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/v1/admin/books/vip-book/access",
                headers=admin_hdr,
                json={"access_mode": "restricted", "allowed_agents": ["ghost-key-word"]},
            ).status_code
            == 400
        )

        # 2. Public shelf stays visible with zero raw keys leaked
        books_res = client.get("/api/v1/books")
        assert books_res.status_code == 200
        assert alice["agent_key"] not in books_res.text
        vip_summary = books_res.json()["books"][0]
        assert vip_summary["access_mode"] == "restricted"
        assert vip_summary["allowed_agent_count"] == 1

        # 3. Anonymous & Bob (not allowlisted) cannot see tasks or read/claim/submit chapter (403)
        agents_before = len(store.list_agents())
        anon_tasks = client.get("/api/v1/tasks").json()
        assert anon_tasks["open_chapters"] == []

        assert client.get("/api/v1/books/vip-book/chapters/ch02").status_code == 403
        assert client.post("/api/v1/books/vip-book/chapters/ch02/claim").status_code == 403
        # Anonymous write on restricted book MUST NOT auto-mint an agent row (AC-044-2)
        assert len(store.list_agents()) == agents_before

        bob_hdr = {"authorization": f"Bearer {bob['agent_key']}"}
        assert client.get("/api/v1/tasks", headers=bob_hdr).json()["open_chapters"] == []
        assert client.get("/api/v1/books/vip-book/chapters/ch02", headers=bob_hdr).status_code == 403
        assert client.post("/api/v1/books/vip-book/chapters/ch02/claim", headers=bob_hdr).status_code == 403
        assert (
            client.post(
                "/api/v1/submissions",
                headers=bob_hdr,
                json={"book_slug": "vip-book", "chapter_id": "ch02", "translation_xml": tr_xml},
            ).status_code
            == 403
        )

        # 4. Alice (allowlisted) sees task, fetches chapter, claims, and submits cleanly
        alice_hdr = {"authorization": f"Bearer {alice['agent_key']}"}
        alice_tasks = client.get("/api/v1/tasks", headers=alice_hdr).json()
        assert len(alice_tasks["open_chapters"]) == 1
        assert client.get("/api/v1/books/vip-book/chapters/ch02", headers=alice_hdr).status_code == 200
        assert client.post("/api/v1/books/vip-book/chapters/ch02/claim", headers=alice_hdr).status_code == 200

        rs_client.next_round_results.append(
            {
                "verdict": "BLOCK",
                "stageStatus": "REMEDIATION_REQUIRED",
                "roundNumber": 1,
                "p0Count": 0,
                "p1Count": 1,
                "p2Count": 0,
                "judgeSummary": "Check tone",
                "openFindings": [{"findingKey": "K1", "severity": "P1", "symptom": "Tone", "fixRecommendation": "Fix"}],
            }
        )
        alice_sub = client.post(
            "/api/v1/submissions",
            headers=alice_hdr,
            json={"book_slug": "vip-book", "chapter_id": "ch02", "translation_xml": tr_xml},
        )
        assert alice_sub.status_code == 200
        sub_id = alice_sub.json()["submission"]["id"]

        # Bob is blocked from peer-reviewing a restricted book submission (403)
        rev_md = (
            "Segment S-0001 note.\n- Fidelity: 4.5/5\n- Fluency: 4.5/5\n- Terminology: 4.5/5\n"
            "- Register: 4.5/5\n- Structure: 5.0/5\n- Completeness: 5.0/5\n"
        )
        assert (
            client.post(
                f"/api/v1/submissions/{sub_id}/reviews",
                headers=bob_hdr,
                json={"body_md": rev_md},
            ).status_code
            == 403
        )

        # 5. /admin HTML & /admin/api/* reject non-admin (403) and serve Google SSO Admin (200)
        assert client.get("/admin").status_code == 403
        assert client.get("/api/v1/admin/agents", headers=alice_hdr).status_code == 403

        admin_page = client.get("/admin", headers=sso_hdr)
        assert admin_page.status_code == 200
        assert "vip-book" in admin_page.text
        assert "alice-bot" in admin_page.text
        assert alice["agent_key"] not in admin_page.text  # only masked key_hint shown!

        agents_api = client.get("/admin/api/agents", headers=sso_hdr)
        assert agents_api.status_code == 200
        assert len(agents_api.json()["agents"]) == 2

        mint_api = client.post(
            "/admin/api/agents/mint",
            headers=sso_hdr,
            json={"display_name": "Guest Translator", "model_info": "cursor"},
        )
        assert mint_api.status_code == 201
        assert THREE_WORD_KEY_RE.match(mint_api.json()["agent_key"])
    store.close()


def test_ac_046_mcp_builtin_instructions_prompts_and_onboarding_card(tmp_path: Path):
    cfg, store, _, svc = _build_env(tmp_path)
    app = create_app(cfg=cfg, service=svc)
    mcp_headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        "host": "collab.vinhthang.dev",
    }
    with TestClient(app) as client:
        # 1. MCP initialize returns self-contained instructions
        init_res = client.post(
            "/mcp",
            headers=mcp_headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "brand-new-agent", "version": "1.0.0"},
                },
            },
        )
        assert init_res.status_code == 200
        init_json = init_res.json()
        instructions = init_json["result"].get("instructions", "")
        assert "register_agent" in instructions
        assert "3-word AGENT_KEY" in instructions
        assert "<segment" in instructions
        assert "<hanviet>" in instructions
        assert "rebuttals" in instructions
        assert cfg.collab_admin_token not in instructions

        # 2. tools/list has rich docstrings + list_agents & set_book_access
        tools_res = client.post(
            "/mcp",
            headers=mcp_headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert tools_res.status_code == 200
        tools_by_name = {t["name"]: t for t in tools_res.json()["result"]["tools"]}
        assert "list_agents" in tools_by_name
        assert "set_book_access" in tools_by_name
        assert "<hanviet>" in tools_by_name["submit_translation"]["description"]
        assert "Fidelity" in tools_by_name["submit_review"]["description"]

        # 3. prompts/list exposes onboarding prompts
        prompts_res = client.post(
            "/mcp",
            headers=mcp_headers,
            json={"jsonrpc": "2.0", "id": 3, "method": "prompts/list", "params": {}},
        )
        assert prompts_res.status_code == 200
        prompt_names = {p["name"] for p in prompts_res.json()["result"]["prompts"]}
        assert "translate_next_chapter" in prompt_names
        assert "review_open_submission" in prompt_names

        # 4. GET / Invite-Only card, /threads, /admin & /llms.txt contain zero secrets
        home = client.get("/")
        assert home.status_code == 200
        assert "Invite-Only Agent Collaboration" in home.text
        assert "/threads" in home.text
        assert "/admin" in home.text
        assert cfg.collab_admin_token not in home.text
    store.close()
