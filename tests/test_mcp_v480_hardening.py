"""Tests for Collab Books MCP v4.8.0 security, scalability, and CI hardening (REQ-082..REQ-086)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def test_req_086_compile_pdf_falls_back_to_pymupdf_when_weasyprint_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """compile_pdf imports without OSError on libpango-less hosts and compiles PDF via PyMuPDF."""
    from scripts import compile_pdf
    import pymupdf as fitz

    monkeypatch.setattr(compile_pdf, "weasyprint", None)

    run_dir = tmp_path / "run"
    ch01 = run_dir / "ch01"
    ch02 = run_dir / "ch02"
    ch01.mkdir(parents=True)
    ch02.mkdir(parents=True)
    (run_dir / "book_record.json").write_text(
        '{"book_record": {"title": "Sách Test", "author": "Tác Giả", "terms": {"Tiền tệ": "Currency"}}}',
        encoding="utf-8",
    )
    (ch01 / "final.txt").write_text(
        "# Chương 1: Tiền tệ\n\nXin chào thế giới tiếng Việt đầy đủ dấu câu.",
        encoding="utf-8",
    )
    (ch02 / "final.txt").write_text(
        "# Chương 2: Lưu thông\n\nNội dung chương hai về Tiền tệ.",
        encoding="utf-8",
    )

    out_pdf = tmp_path / "out.pdf"
    result_path = compile_pdf.compile_book(
        str(run_dir),
        str(out_pdf),
        title="Sách Test",
        author="Tác Giả",
        include_toc=True,
        include_index=True,
    )

    captured = capsys.readouterr()
    assert "MuPDF error:" not in captured.err
    assert Path(result_path).exists()
    pdf_bytes = Path(result_path).read_bytes()
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 500

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    assert "Chương 1: Tiền tệ" in text
    assert "tiếng Việt" in text


def _make_hardening_service(
    tmp_path: Path,
    *,
    admin_emails: frozenset[str] = frozenset({"admin@example.com"}),
    trust_admin_sso_header: bool = True,
    trust_proxy_headers: bool = False,
    trusted_proxy_ips: frozenset[str] = frozenset({"127.0.0.1", "::1", "testclient"}),
    auth_fail_rpm: int = 20,
    agent_heartbeat_rpm: int = 60,
    agent_write_rpm: int = 60,
):
    from scripts.mcp_server.config import McpServerConfig
    from scripts.mcp_server.db import CollabStore
    from scripts.mcp_server.git_sync import CollabBooksGitSync
    from scripts.mcp_server.service import CollabMcpService
    from tests.test_mcp_service_and_app import FakeReviewServerClient

    cfg = McpServerConfig(
        database_url="",
        sqlite_path=tmp_path / "collab.sqlite3",
        books_repo_dir=tmp_path / "collab-books",
        books_repo_full_name="vinhthang/collab-books",
        review_server_url="https://review.example.invalid",
        review_bearer_token="",
        collab_admin_token="adm-secret-token",
        admin_emails=admin_emails,
        trust_admin_sso_header=trust_admin_sso_header,
        github_token="",
        bot_git_name="collab-bot",
        bot_git_email="bot@example.invalid",
        agent_write_rpm=agent_write_rpm,
        trust_proxy_headers=trust_proxy_headers,
        trusted_proxy_ips=trusted_proxy_ips,
        auth_fail_rpm=auth_fail_rpm,
        agent_heartbeat_rpm=agent_heartbeat_rpm,
    )
    cfg.books_repo_dir.mkdir(parents=True, exist_ok=True)
    store = CollabStore(cfg)
    store.init_db()
    git_sync = CollabBooksGitSync(cfg)
    review_client = FakeReviewServerClient()
    service = CollabMcpService(cfg=cfg, store=store, review_client=review_client, git_sync=git_sync)
    return cfg, store, git_sync, review_client, service


def test_req_083_expanded_wordlists_prefix_only_key_hint_and_legacy_compat(tmp_path: Path) -> None:
    """REQ-083: >= 256 words/slot in keys.py, prefix-only w1-*** key_hint, and legacy key hash compatibility."""
    import re
    from scripts.mcp_server import keys
    from scripts.mcp_server.auth import (
        _extract_client_ip,
        mint_agent_key,
        resolve_auth,
        sha256_hex,
    )

    for wordlist_name in ("_ADJECTIVES", "_NOUNS", "_PLACES"):
        words = getattr(keys, wordlist_name)
        assert len(words) >= 256, f"{wordlist_name} has {len(words)} words, expected >= 256"
        assert len(set(words)) == len(words), f"{wordlist_name} contains duplicate entries"
        assert all(re.fullmatch(r"[a-z]+", w) for w in words), f"{wordlist_name} has non-[a-z] word"

    raw_k, k_hash, k_hint = mint_agent_key()
    assert re.fullmatch(r"[a-z]+-[a-z]+-[a-z]+", raw_k)
    w1, w2, w3 = raw_k.split("-")
    assert k_hash == sha256_hex(raw_k)
    assert k_hint == f"{w1}-***"
    assert w2 not in k_hint and w3 not in k_hint

    # Boundary assertions on readable handles & role helpers (REQ-080/REQ-083)
    from scripts.mcp_server.auth import (
        AuthContext,
        AuthError,
        assert_agent_admin_or_admin,
        assert_bug_handler_or_admin,
    )

    assert keys.generate_readable_handle("ab") == ""
    assert keys.generate_readable_handle("abc") == "abc"
    aa_ctx = AuthContext(tier="AGENT_KEY", actor_id="agent:aa", handle="aa", client_ip_hash="0", agent_role="agent_admin")
    ta_ctx = AuthContext(tier="AGENT_KEY", actor_id="agent:ta", handle="ta", client_ip_hash="0", agent_role="tech_admin")
    co_ctx = AuthContext(tier="AGENT_KEY", actor_id="agent:co", handle="co", client_ip_hash="0", agent_role="contributor")
    assert_agent_admin_or_admin(aa_ctx)
    assert_bug_handler_or_admin(ta_ctx)
    assert_bug_handler_or_admin(aa_ctx)
    with pytest.raises(AuthError):
        assert_agent_admin_or_admin(co_ctx)
    with pytest.raises(AuthError):
        assert_bug_handler_or_admin(co_ctx)

    cfg, store, _, _, _ = _make_hardening_service(tmp_path)
    try:
        # Legacy pre-v4.8.0 keys (w1-***-w3 stored hint and cbt_ak_* format) continue authenticating via SHA-256
        legacy_raw_3w = "amber-fox-river"
        store._exec(
            """
            INSERT INTO agents (handle, display_name, model_info, key_sha256, key_hint, role, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("legacy-3w", "Legacy 3W", "gpt", sha256_hex(legacy_raw_3w), "amber-***-river", "contributor", "2026-11-20T00:00:00Z"),
        )
        legacy_raw_hex = "cbt_ak_legacydeadbeef1234567890"
        store._exec(
            """
            INSERT INTO agents (handle, display_name, model_info, key_sha256, key_hint, role, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("legacy-hex", "Legacy Hex", "gpt", sha256_hex(legacy_raw_hex), "cbt_ak_...7890", "contributor", "2026-11-20T00:00:00Z"),
        )
        ctx1 = resolve_auth(store=store, cfg=cfg, headers={"authorization": f"Bearer {legacy_raw_3w}"}, request_path="/mcp")
        ctx2 = resolve_auth(store=store, cfg=cfg, headers={"authorization": f"Bearer {legacy_raw_hex}"}, request_path="/mcp")
        assert ctx1.tier == "AGENT_KEY" and ctx1.handle == "legacy-3w"
        assert ctx2.tier == "AGENT_KEY" and ctx2.handle == "legacy-hex"
        assert _extract_client_ip({"cf-connecting-ip": "1.2.3.4"}, "127.0.0.1") == "1.2.3.4"
    finally:
        store.close()


def test_req_082_083_fail_closed_sso_trusted_proxy_ip_bounded_limiter_and_auth_fail_throttle(tmp_path: Path) -> None:
    """REQ-082 & REQ-083: Fail-closed empty admin_emails SSO, trusted-proxy IP check, bounded limiter, failed-auth 429, and heartbeat RPM."""
    from starlette.testclient import TestClient
    from scripts.mcp_server.app import create_app
    from scripts.mcp_server.auth import (
        AuthError,
        RateLimitError,
        SlidingWindowRateLimiter,
        _extract_client_ip,
        resolve_auth,
    )

    # 1. Fail-closed SSO when admin_emails is empty
    cfg_empty, store_empty, gs_empty, rc_empty, svc_empty = _make_hardening_service(
        tmp_path / "empty_sso",
        admin_emails=frozenset(),
        trust_admin_sso_header=True,
    )
    try:
        ctx_sso = resolve_auth(
            store=store_empty,
            cfg=cfg_empty,
            headers={"x-auth-request-email": "intruder@example.com"},
            request_path="/admin/api/agents",
        )
        assert ctx_sso.tier == "ANONYMOUS"
        app = create_app(cfg=cfg_empty, service=svc_empty)
        with TestClient(app) as client:
            resp = client.get("/admin/api/agents", headers={"X-Auth-Request-Email": "intruder@example.com"})
            assert resp.status_code == 403
    finally:
        store_empty.close()

    # 2. Proxy IP extraction honors CF-Connecting-IP / X-Forwarded-For only from trusted proxy or explicit opt-in
    assert (
        _extract_client_ip(
            {"cf-connecting-ip": "203.0.113.9", "x-forwarded-for": "198.51.100.5"},
            "198.51.100.77",
            trust_proxy_headers=False,
        )
        == "198.51.100.77"
    )
    assert (
        _extract_client_ip(
            {"cf-connecting-ip": "203.0.113.9"},
            "198.51.100.77",
            trust_proxy_headers=True,
        )
        == "203.0.113.9"
    )
    assert (
        _extract_client_ip(
            {"x-forwarded-for": "203.0.113.10, 127.0.0.1"},
            "127.0.0.1",
            trust_proxy_headers=False,
        )
        == "203.0.113.10"
    )

    # 3. SlidingWindowRateLimiter evicts expired buckets cleanly across many transient keys
    limiter = SlidingWindowRateLimiter()
    for i in range(1500):
        limiter.check(f"transient:{i}", limit=5, window_seconds=60.0, now=1000.0)
    assert len(limiter._buckets) == 1500
    limiter.check("active-key", limit=5, window_seconds=60.0, now=1100.0)
    assert len(limiter._buckets) < 50

    # 4. Failed auth throttling + agent_heartbeat_rpm isolation
    cfg, store, _, _, service = _make_hardening_service(
        tmp_path / "throttle",
        auth_fail_rpm=3,
        agent_heartbeat_rpm=2,
        agent_write_rpm=5,
    )
    try:
        for _ in range(3):
            with pytest.raises(AuthError):
                resolve_auth(
                    store=store,
                    cfg=cfg,
                    headers={"authorization": "Bearer wrong-bad-key"},
                    request_path="/mcp",
                    client_ip="127.0.0.1",
                    rate_limiter=service.rate_limiter,
                )
        with pytest.raises(RateLimitError) as rl_exc:
            resolve_auth(
                store=store,
                cfg=cfg,
                headers={"authorization": "Bearer wrong-bad-key"},
                request_path="/mcp",
                client_ip="127.0.0.1",
                rate_limiter=service.rate_limiter,
            )
        assert rl_exc.value.status_code == 429

        reg = store.register_agent(handle="hb-agent", display_name="HB Agent", model_info="gpt")
        ctx_ag = resolve_auth(
            store=store,
            cfg=cfg,
            headers={"authorization": f"Bearer {reg['agent_key']}"},
            request_path="/mcp",
            rate_limiter=service.rate_limiter,
        )
        service.agent_heartbeat(ctx_ag, status_note="alive 1")
        service.agent_heartbeat(ctx_ag, status_note="alive 2")
        with pytest.raises(RateLimitError) as hb_exc:
            service.agent_heartbeat(ctx_ag, status_note="alive 3")
        assert hb_exc.value.status_code == 429
        # Heartbeat throttling did not consume agent_write_rpm quota
        service._check_write_rate(ctx_ag)
    finally:
        store.close()


def test_req_082_mcp_and_rest_oversized_body_rejected_413_without_unbounded_buffering(tmp_path: Path) -> None:
    """REQ-082: Streaming > 10 MiB body on /mcp or REST aborts with HTTP 413 before buffering remaining chunks."""
    import asyncio
    import json
    from scripts.mcp_server.app import create_app

    cfg, store, _, _, service = _make_hardening_service(tmp_path)
    try:
        app = create_app(cfg=cfg, service=service)
        chunk = b"a" * (1024 * 1024)  # 1 MiB per chunk

        async def _send_oversized(path: str) -> tuple[int, dict[str, Any], int]:
            chunks_sent = 0
            status_code = 0
            resp_chunks: list[bytes] = []

            async def _receive() -> dict[str, Any]:
                nonlocal chunks_sent
                if chunks_sent < 20:
                    chunks_sent += 1
                    return {
                        "type": "http.request",
                        "body": chunk,
                        "more_body": chunks_sent < 20,
                    }
                return {"type": "http.request", "body": b"", "more_body": False}

            async def _send(message: dict[str, Any]) -> None:
                nonlocal status_code
                if message["type"] == "http.response.start":
                    status_code = int(message["status"])
                elif message["type"] == "http.response.body":
                    resp_chunks.append(bytes(message.get("body") or b""))

            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": path,
                "raw_path": path.encode("ascii"),
                "query_string": b"",
                "headers": [
                    (b"host", b"testserver"),
                    (b"content-type", b"application/json"),
                ],
                "client": ("127.0.0.1", 50000),
                "server": ("testserver", 80),
            }
            await app(scope, _receive, _send)
            payload = json.loads(b"".join(resp_chunks).decode("utf-8"))
            return status_code, payload, chunks_sent

        mcp_status, _, mcp_chunks = asyncio.run(_send_oversized("/mcp"))
        assert mcp_status == 413
        assert mcp_chunks <= 11, f"Expected early abort before chunk 20, read {mcp_chunks} MiB"

        rest_status, _, rest_chunks = asyncio.run(_send_oversized("/api/v1/books/propose"))
        assert rest_status == 413
        assert rest_chunks <= 11, f"Expected early abort before chunk 20, read {rest_chunks} MiB"
    finally:
        store.close()


def test_req_082_admin_csrf_blocks_text_plain_and_cross_origin_browser_posts(tmp_path: Path) -> None:
    """REQ-082: Non-empty non-JSON POST returns 415; cross-origin /admin POST returns 403; same-origin & headerless empty POSTs succeed."""
    from starlette.testclient import TestClient
    from scripts.mcp_server.app import create_app

    cfg, store, _, _, service = _make_hardening_service(tmp_path)
    sso_hdr = {"X-Auth-Request-Email": "admin@example.com"}
    try:
        app = create_app(cfg=cfg, service=service)
        with TestClient(app) as client:
            # 1. text/plain non-empty POST blocked with 415
            res_plain_admin = client.post(
                "/admin/api/agents/mint",
                headers={**sso_hdr, "Content-Type": "text/plain"},
                content='{"handle":"csrf-bot"}',
            )
            assert res_plain_admin.status_code == 415

            res_plain_rest = client.post(
                "/api/v1/books/propose",
                headers={"Content-Type": "text/plain"},
                content='{"slug":"csrf-book"}',
            )
            assert res_plain_rest.status_code == 415

            # 2. Cross-origin / Sec-Fetch-Site: cross-site on /admin* blocked with 403 even on empty body
            res_cross_origin = client.post(
                "/admin/api/purge-test-artifacts",
                headers={**sso_hdr, "Origin": "https://evil.example"},
            )
            assert res_cross_origin.status_code == 403

            res_cross_site = client.post(
                "/admin/api/purge-test-artifacts",
                headers={**sso_hdr, "Sec-Fetch-Site": "cross-site"},
            )
            assert res_cross_site.status_code == 403

            # 3. Same-origin Host or public_base_url Origin and empty-body non-browser POST succeed
            res_same_origin = client.post(
                "/admin/api/purge-test-artifacts",
                headers={**sso_hdr, "Origin": "https://collab.vinhthang.dev", "Sec-Fetch-Site": "same-origin"},
            )
            assert res_same_origin.status_code == 200

            res_empty_cli = client.post(
                "/admin/api/purge-test-artifacts",
                headers=sso_hdr,
            )
            assert res_empty_cli.status_code == 200
    finally:
        store.close()


def test_req_084_middleware_offloads_db_presence_to_worker_thread_and_deduplicates_auth(tmp_path: Path) -> None:
    """REQ-084: RequestContextMiddleware offloads Bearer auth/presence DB work off event loop thread and avoids duplicate lookup."""
    import asyncio
    import json
    import threading
    from scripts.mcp_server.app import create_app

    cfg, store, _, _, service = _make_hardening_service(tmp_path)
    reg = store.register_agent(handle="mw-worker", display_name="MW Worker", model_info="gpt")
    raw_key = reg["agent_key"]

    get_threads: list[int] = []
    touch_threads: list[int] = []
    orig_get = store.get_agent_by_key
    orig_touch = store.touch_agent_last_seen

    def tracked_get(key: str):
        get_threads.append(threading.get_ident())
        return orig_get(key)

    def tracked_touch(handle: str, **kwargs):
        touch_threads.append(threading.get_ident())
        return orig_touch(handle, **kwargs)

    store.get_agent_by_key = tracked_get  # type: ignore[method-assign]
    store.touch_agent_last_seen = tracked_touch  # type: ignore[method-assign]

    try:
        app = create_app(cfg=cfg, service=service)
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "list_books",
                    "arguments": {},
                    "clientInfo": {"name": "test-harness"},
                },
            }
        ).encode("utf-8")

        async def _call_mcp() -> tuple[int, int]:
            loop_tid = threading.get_ident()
            sent_body = False
            status_code = 0

            async def _receive() -> dict[str, Any]:
                nonlocal sent_body
                if not sent_body:
                    sent_body = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return {"type": "http.disconnect"}

            async def _send(msg: dict[str, Any]) -> None:
                nonlocal status_code
                if msg["type"] == "http.response.start":
                    status_code = int(msg["status"])

            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/mcp",
                "raw_path": b"/mcp",
                "query_string": b"",
                "headers": [
                    (b"host", b"testserver"),
                    (b"content-type", b"application/json"),
                    (b"accept", b"application/json, text/event-stream"),
                    (b"authorization", f"Bearer {raw_key}".encode("ascii")),
                ],
                "client": ("127.0.0.1", 50001),
                "server": ("testserver", 80),
            }
            async with app.app.router.lifespan_context(app.app):
                await app(scope, _receive, _send)
            return status_code, loop_tid

        status, loop_tid = asyncio.run(_call_mcp())
        assert status == 200
        assert len(get_threads) == 1, f"Expected single DB get_agent_by_key call, saw {len(get_threads)}"
        assert len(touch_threads) == 1, f"Expected single DB touch_agent_last_seen call, saw {len(touch_threads)}"
        assert get_threads[0] != loop_tid, "get_agent_by_key ran directly on asyncio event loop thread"
        assert touch_threads[0] != loop_tid, "touch_agent_last_seen ran directly on asyncio event loop thread"
    finally:
        store.close()


def test_req_084_sqlite_and_pg_indexes_persistent_pg_conn_and_atomic_transaction_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-084: Secondary indexes exist, CollabStore reuses PG connection, and transaction() commits/rolls back atomically."""
    import sqlite3
    from scripts.mcp_server.config import McpServerConfig
    from scripts.mcp_server.db import CollabStore, SCHEMA_STATEMENTS_COMMON

    cfg, store, _, _, _ = _make_hardening_service(tmp_path)
    try:
        raw = sqlite3.connect(cfg.sqlite_path)
        idx_names = {
            row[0]
            for row in raw.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        raw.close()
        for expected_idx in (
            "idx_submissions_book_chapter",
            "idx_submissions_status",
            "idx_submissions_author",
            "idx_peer_reviews_submission",
            "idx_peer_reviews_reviewer",
            "idx_bug_reports_status",
        ):
            assert expected_idx in idx_names, f"Missing index {expected_idx} in SQLite"
            assert any(expected_idx in stmt for stmt in SCHEMA_STATEMENTS_COMMON)

        # Transactional rollback on exception leaves neither statement persisted
        with pytest.raises(RuntimeError, match="boom"):
            with store.transaction():
                store.register_agent(handle="tx-agent-fail", display_name="TX Fail", model_info="gpt")
                raise RuntimeError("boom")
        assert store.get_agent("tx-agent-fail") is None

        # Transactional commit persists nested calls atomically
        with store.transaction():
            with store.transaction():
                store.register_agent(handle="tx-agent-ok", display_name="TX OK", model_info="gpt")
        assert store.get_agent("tx-agent-ok") is not None
    finally:
        store.close()

    # Fake PostgreSQL driver verifying persistent connection reuse and search_path once per connection
    connect_calls: list[dict[str, Any]] = []
    executed_sqls: list[str] = []

    class FakeCursor:
        def __init__(self, conn: "FakePgConn") -> None:
            self._conn = conn

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql: str, params=()):
            executed_sqls.append(sql)
            self._conn.commits  # sanity attr check

        def fetchone(self):
            return {"slug": "b1"}

        def fetchall(self):
            return []

    class FakePgConn:
        def __init__(self) -> None:
            self.closed = False
            self.broken = False
            self.commits = 0
            self.rollbacks = 0

        def cursor(self):
            return FakeCursor(self)

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            self.closed = True

    class FakePsycopg:
        class OperationalError(Exception):
            pass

        class InterfaceError(Exception):
            pass

        @staticmethod
        def connect(dsn: str, row_factory=None):
            conn = FakePgConn()
            connect_calls.append({"dsn": dsn, "conn": conn})
            return conn

    import sys
    import types

    fake_psycopg = types.ModuleType("psycopg")
    fake_psycopg.OperationalError = FakePsycopg.OperationalError  # type: ignore[attr-defined]
    fake_psycopg.InterfaceError = FakePsycopg.InterfaceError  # type: ignore[attr-defined]
    fake_psycopg.connect = FakePsycopg.connect  # type: ignore[attr-defined]
    fake_rows = types.ModuleType("psycopg.rows")
    fake_rows.dict_row = lambda x: x  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", fake_rows)

    pg_cfg = McpServerConfig(
        database_url="postgresql://user:pass@localhost:5432/collab",
        sqlite_path=tmp_path / "unused.sqlite3",
        books_repo_dir=tmp_path / "books_pg",
        books_repo_full_name="vinhthang/collab-books",
        review_server_url="",
        review_bearer_token="",
        collab_admin_token="adm",
        admin_emails=frozenset({"admin@example.com"}),
        trust_admin_sso_header=True,
        github_token="",
        bot_git_name="bot",
        bot_git_email="bot@example.invalid",
    )
    pg_store = CollabStore(pg_cfg)
    try:
        pg_store._exec("SELECT * FROM books WHERE slug = ?", ("b1",), fetchone=True)
        pg_store._exec("SELECT * FROM books ORDER BY slug ASC", (), fetchall=True)
        pg_store._exec("SELECT * FROM chapters WHERE book_slug = ?", ("b1",), fetchall=True)
        assert len(connect_calls) == 1, f"Expected single reused PG connection, got {len(connect_calls)}"
        search_path_count = sum(1 for s in executed_sqls if "SET search_path" in s)
        assert search_path_count == 1, f"Expected SET search_path once per connection, ran {search_path_count} times"

        # PG transaction() commits once at outermost exit and rolls back on exception
        conn_pg = connect_calls[0]["conn"]
        c_before = conn_pg.commits
        pg_store._exec("UPDATE books SET status = ? WHERE slug = ?", ("active", "b1"))
        assert conn_pg.commits == c_before + 1

        commits_before = conn_pg.commits
        with pg_store.transaction():
            pg_store._exec("UPDATE books SET status = ? WHERE slug = ?", ("active", "b1"))
            pg_store._exec("SELECT * FROM books WHERE slug = ?", ("b1",), fetchone=True)
            pg_store._exec("SELECT * FROM books ORDER BY slug ASC", (), fetchall=True)
            assert conn_pg.commits == commits_before
        assert conn_pg.commits == commits_before + 1

        rollbacks_before = conn_pg.rollbacks
        with pytest.raises(RuntimeError, match="pg-rollback"):
            with pg_store.transaction():
                pg_store._exec("UPDATE books SET status = ? WHERE slug = ?", ("rejected", "b1"))
                raise RuntimeError("pg-rollback")
        assert conn_pg.rollbacks == rollbacks_before + 1

        # Non-retryable error outside a transaction rolls back without closing conn
        def _nonretry_execute(sql: str, params=()):
            executed_sqls.append(sql)
            if sql == "BAD SQL":
                raise ValueError("syntax error")

        orig_cursor = conn_pg.cursor
        conn_pg.cursor = lambda: type(  # type: ignore[method-assign]
            "ErrCursor",
            (),
            {
                "__enter__": lambda s: s,
                "__exit__": lambda s, *a: False,
                "execute": lambda s, sql, p=(): _nonretry_execute(sql, p),
                "fetchone": lambda s: {"slug": "b1"},
                "fetchall": lambda s: [],
            },
        )()
        rb_before = conn_pg.rollbacks
        with pytest.raises(ValueError, match="syntax error"):
            pg_store._exec("BAD SQL", ())
        assert conn_pg.rollbacks == rb_before + 1
        conn_pg.cursor = orig_cursor  # type: ignore[method-assign]

        # Transient OperationalError outside a transaction closes old connection and retries once
        def _flaky_execute(sql: str, params=()):
            executed_sqls.append(sql)
            if sql == "SELECT 1":
                raise FakePsycopg.OperationalError("connection reset")

        conn_pg.cursor = lambda: type(  # type: ignore[method-assign]
            "FlakyCursor",
            (),
            {
                "__enter__": lambda s: s,
                "__exit__": lambda s, *a: False,
                "execute": lambda s, sql, p=(): _flaky_execute(sql, p),
                "fetchone": lambda s: {"ok": 1},
                "fetchall": lambda s: [],
            },
        )()
        res_retry = pg_store._exec("SELECT 1", (), fetchone=True)
        assert res_retry == {"slug": "b1"}
        assert len(connect_calls) == 2 and conn_pg.closed is True
    finally:
        pg_store.close()
    assert connect_calls[-1]["conn"].closed is True


def test_req_084_list_books_uses_aggregate_queries_and_rounds_history_omits_duplicate_xml(tmp_path: Path) -> None:
    """REQ-084: list_books executes <= 4 SQL queries without full source_text; rounds_history omits duplicate translation_xml."""
    from scripts.mcp_server.auth import AuthContext

    cfg, store, _, _, service = _make_hardening_service(tmp_path)
    try:
        admin_ctx = AuthContext(
            tier="ADMIN",
            actor_id="admin:test",
            handle="admin",
            client_ip_hash="0000",
            agent_role="admin",
        )
        big_source = "A" * 50000
        for i in range(5):
            slug = f"book-{i}"
            store.upsert_book(
                slug=slug,
                title=f"Book {i}",
                author="Author",
                source_lang="en",
                target_lang="vi",
                genre="fiction",
                license_id="CC-BY-4.0",
                license_url="https://creativecommons.org/licenses/by/4.0/",
                status="active",
                glossary_sha="abc123",
                book_record={"characters": {}, "terms": {}, "pronoun_pairs": []},
                manifest={"chapters": [{"chapter_id": "ch01", "title": "Ch 1"}]},
                proposed_by="admin",
                approval_mode="peer",
                source_text=big_source,
            )
            store.upsert_chapter(
                book_slug=slug,
                chapter_id="ch01",
                chapter_num=1,
                title="Ch 1",
                source_xml='<segment id="S-0001" type="p"><original>Hello</original></segment>',
                status="open",
            )

        executed: list[str] = []
        orig_exec = store._exec

        def counting_exec(sql: str, params=(), **kwargs):
            executed.append(sql)
            return orig_exec(sql, params, **kwargs)

        store._exec = counting_exec  # type: ignore[method-assign]
        try:
            books = service.list_books(admin_ctx)
        finally:
            store._exec = orig_exec  # type: ignore[method-assign]

        assert len(books) == 5
        assert len(executed) <= 4, f"Expected <= 4 aggregate SQL queries for 5 books, ran {len(executed)}: {executed}"
        assert all("SELECT * FROM books" not in s and ", source_text," not in s for s in executed)
        assert all(len(b["source_preview"]) == 2000 for b in books)

        # Test rounds_history omits duplicate translation_xml
        writer = store.register_agent(handle="hist-writer", display_name="Hist Writer", model_info="gpt")
        writer_ctx = AuthContext(
            tier="AGENT_KEY",
            actor_id=f"agent:{writer['handle']}",
            handle=writer["handle"],
            client_ip_hash="1111",
            agent_role="contributor",
        )
        tx_xml = '<segment id="S-0001" type="p"><original>Hello</original><translate>Xin chào</translate></segment>'
        status_code, payload = service.submit_translation(
            writer_ctx,
            book_slug="book-0",
            chapter_id="ch01",
            translation_xml=tx_xml,
        )
        assert status_code == 200
        sub_row = store.get_submission(payload["submission"]["id"])
        assert sub_row is not None
        assert len(sub_row["rounds_history"]) == 1
        assert "translation_xml" not in sub_row["rounds_history"][0]
    finally:
        store.close()


def test_req_084_git_push_failure_rolls_back_chapter_acceptance_and_release_replaces_existing_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-084: Failed git_sync prevents DB acceptance commit; _upload_github_release deletes matching assets and raises on HTTP errors."""
    import httpx
    from scripts.mcp_server.auth import AuthContext
    from scripts.mcp_server.config import McpServerConfig
    from scripts.mcp_server.db import CollabStore
    from scripts.mcp_server.git_sync import CollabBooksGitSync
    from scripts.mcp_server.service import CollabMcpService
    from tests.test_mcp_service_and_app import FakeReviewServerClient, _init_git_books_repo

    work_repo, _ = _init_git_books_repo(tmp_path)
    cfg = McpServerConfig(
        database_url="",
        sqlite_path=tmp_path / "collab.sqlite3",
        books_repo_dir=work_repo,
        books_repo_full_name="owner/collab-books",
        review_server_url="https://review.example.invalid",
        review_bearer_token="",
        collab_admin_token="adm",
        admin_emails=frozenset({"admin@example.com"}),
        trust_admin_sso_header=True,
        github_token="ghp_testtoken",
        bot_git_name="collab-bot",
        bot_git_email="bot@example.invalid",
    )
    store = CollabStore(cfg)
    store.init_db()
    git_sync = CollabBooksGitSync(cfg)
    service = CollabMcpService(cfg=cfg, store=store, review_client=FakeReviewServerClient(), git_sync=git_sync)
    try:
        store.upsert_book(
            slug="pub-git-test",
            title="Public Git Test",
            author="Author",
            source_lang="en",
            target_lang="vi",
            genre="fiction",
            license_id="CC-BY-4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
            status="active",
            glossary_sha="abc",
            book_record={"characters": {}, "terms": {}, "pronoun_pairs": []},
            manifest={"chapters": [{"chapter_id": "ch01", "title": "Ch 1"}, {"chapter_id": "ch02", "title": "Ch 2"}]},
            proposed_by="admin",
            approval_mode="peer",
        )
        store.upsert_chapter(
            book_slug="pub-git-test",
            chapter_id="ch01",
            chapter_num=1,
            title="Ch 1",
            source_xml='<segment id="S-0001" type="p"><original>Hello</original></segment>',
            status="open",
        )
        writer = store.register_agent(handle="w-git", display_name="W Git", model_info="gpt")
        reviewer = store.register_agent(handle="r-git", display_name="R Git", model_info="gpt")
        w_ctx = AuthContext(tier="AGENT_KEY", actor_id="agent:w-git", handle="w-git", client_ip_hash="w1", agent_role="contributor")
        r_ctx = AuthContext(tier="AGENT_KEY", actor_id="agent:r-git", handle="r-git", client_ip_hash="r1", agent_role="contributor")
        a_ctx = AuthContext(tier="ADMIN", actor_id="admin:test", handle="admin", client_ip_hash="a1", agent_role="admin")

        _, sub_payload = service.submit_translation(
            w_ctx,
            book_slug="pub-git-test",
            chapter_id="ch01",
            translation_xml='<segment id="S-0001" type="p"><original>Hello</original><translate>Xin chào</translate></segment>',
        )
        sub_id = sub_payload["submission"]["id"]

        def _failing_sync(**kwargs):
            raise RuntimeError("simulated git push failure")

        monkeypatch.setattr(git_sync, "sync_accepted_chapter", _failing_sync)

        with pytest.raises(RuntimeError, match="simulated git push failure"):
            service.submit_review(
                r_ctx,
                submission_id=sub_id,
                verdict="APPROVE",
                body_md="S-0001 accurate.\n- Fidelity: 4.9\n- Fluency: 4.8\n- Terminology: 4.9\n- Register: 4.8\n- Structure: 5.0\n- Completeness: 5.0\n",
            )
        ch_after_rev = store.get_chapter("pub-git-test", "ch01")
        sub_after_rev = store.get_submission(sub_id)
        assert ch_after_rev is not None and ch_after_rev["status"] != "accepted"
        assert sub_after_rev is not None and sub_after_rev["status"] != "accepted"

        with pytest.raises(RuntimeError, match="simulated git push failure"):
            service.manage_flagged_submission(a_ctx, submission_id=sub_id, action="approve_override")
        ch_after_adm = store.get_chapter("pub-git-test", "ch01")
        sub_after_adm = store.get_submission(sub_id)
        assert ch_after_adm is not None and ch_after_adm["status"] != "accepted"
        assert sub_after_adm is not None and sub_after_adm["status"] != "accepted"

        # Verify _upload_github_release deletes matching pre-existing asset and calls raise_for_status()
        dummy_asset = tmp_path / "pub-git-test_vietnamese.pdf"
        dummy_asset.write_bytes(b"%PDF-1.7\ntest\n")
        deleted_urls: list[str] = []
        upload_status = {"code": 201}

        class FakeResp:
            def __init__(self, status_code: int, payload: dict[str, Any] | None = None, url: str = "") -> None:
                self.status_code = status_code
                self._payload = payload or {}
                self.request = httpx.Request("POST", url or "https://api.github.com/test")

            def json(self):
                return self._payload

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=self.request, response=httpx.Response(self.status_code, request=self.request))

        class FakeHttpxClient:
            def __init__(self, timeout: float = 120.0) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url: str, headers=None, json=None, content=None):
                if url.endswith("/releases"):
                    return FakeResp(
                        200,
                        {
                            "upload_url": "https://uploads.github.com/repos/owner/collab-books/releases/1/assets{?name,label}",
                            "html_url": "https://github.com/owner/collab-books/releases/tag/v1.0",
                            "assets": [{"id": 999, "name": dummy_asset.name}],
                        },
                        url=url,
                    )
                return FakeResp(upload_status["code"], {"url": url}, url=url)

            def delete(self, url: str, headers=None):
                deleted_urls.append(url)
                return FakeResp(204, {}, url=url)

        monkeypatch.setattr("scripts.mcp_server.git_sync.httpx.Client", FakeHttpxClient)
        rel_url = git_sync._upload_github_release(
            slug="pub-git-test",
            title="Public Git Test",
            release_tag="pub-git-test-v1.0",
            files=[dummy_asset],
        )
        assert rel_url.endswith("/v1.0")
        assert deleted_urls == ["https://api.github.com/repos/owner/collab-books/releases/assets/999"]

        upload_status["code"] = 500
        with pytest.raises(httpx.HTTPStatusError):
            git_sync._upload_github_release(
                slug="pub-git-test",
                title="Public Git Test",
                release_tag="pub-git-test-v1.0",
                files=[dummy_asset],
            )

        # Verify git_sync artifact helpers (brief, book_record, checklist, peer-only ch01 ledger)
        git_sync.prepare_book_files(
            slug="gs-priv-book",
            manifest={"title": "GS Priv", "source_lang": "en", "target_lang": "vi"},
            source_filename="book.md",
            source_text="# Ch1",
            proposed_chapters=[{"chapter_id": "ch01", "chapter_num": 1, "title": "Ch 1", "source_xml": "<s/>"}],
            initial_book_record={"characters": {"Hero": "Anh hung"}},
            initial_brief={"genre": "fiction"},
            public_corpus=False,
        )
        priv_run = git_sync.private_books_dir / "runs" / "gs-priv-book"
        assert (priv_run / "brief.json").is_file()
        git_sync.write_brief_and_record(
            slug="gs-priv-book",
            brief={"genre": "essay"},
            book_record={"characters": {"Hero": "Anh hùng"}},
            public_corpus=False,
        )
        assert "essay" in (priv_run / "brief.json").read_text(encoding="utf-8")
        assert "Anh hùng" in (priv_run / "book_record.json").read_text(encoding="utf-8")
        git_sync._write_run_chapter_artifacts(
            run_dir=priv_run,
            slug="gs-priv-book",
            chapter_id="ch01",
            chapter_num=1,
            source_xml="<s/>",
            translation_xml="<t/>",
            final_txt="Ch 1",
            book_record={"characters": {}},
            glossary_sha="gsha",
            translator_handle="w-git",
            reviewer_handles=["review-vinhthang-dev", "peer-a"],
            score_of_record=4.8,
            all_chapters_status=[
                {"chapter_id": "ch01", "chapter_num": 1, "title": "Ch 1", "status": "accepted"},
                {"chapter_id": "ch02", "chapter_num": 2, "title": "Ch 2", "status": "open"},
            ],
            head_sha="deadbeef",
        )
        import json as _json

        cl_data = _json.loads((priv_run / "checklist.json").read_text(encoding="utf-8"))
        assert cl_data["chapters"][0]["status"] == "done"
        assert cl_data["chapters"][1]["status"] == "open"
        led_data = git_sync.read_ledger("gs-priv-book", public_corpus=False)
        assert led_data["chapters"]["ch01"]["issue_number"] == 0
        assert led_data["chapters"]["ch01"]["reviewer_logins"] == ["peer-a"]
    finally:
        store.close()


def test_req_085_admin_retry_seed_passes_boolean_true_and_ci_dockerfile_contracts(tmp_path: Path) -> None:
    """REQ-085: .js-retry-seed passes true (no hold ReferenceError), node --check passes, ci-cd.yml & Dockerfile hardened."""
    import shutil
    import subprocess
    from scripts.mcp_server.admin_ui import _ADMIN_JS

    retry_block = _ADMIN_JS.split(".js-retry-seed", 1)[1].split(".js-reject-book", 1)[0]
    assert "pollSeedCompletion(slug, true, msg, btn)" in retry_block
    assert "pollSeedCompletion(slug, hold" not in retry_block

    js_file = tmp_path / "admin.js"
    js_file.write_text(_ADMIN_JS, encoding="utf-8")
    node_bin = shutil.which("node")
    assert node_bin is not None, "node runtime must be installed to validate _ADMIN_JS syntax"
    res = subprocess.run([node_bin, "--check", str(js_file)], capture_output=True, text=True)
    assert res.returncode == 0, f"node --check failed: {res.stderr}"

    repo_root = Path(__file__).resolve().parent.parent
    ci_text = (repo_root / ".github" / "workflows" / "ci-cd.yml").read_text(encoding="utf-8")
    assert "pull_request:" in ci_text
    assert "uv sync --frozen" in ci_text
    assert "pytest" in ci_text
    assert "needs:" in ci_text and "test" in ci_text
    assert "if: github.event_name != 'pull_request'" in ci_text
    assert "v0.9.1" in ci_text
    assert "VERIFIED_PKG" in ci_text

    df_text = (repo_root / "Dockerfile").read_text(encoding="utf-8")
    assert "uv.lock" in df_text
    assert "uv sync --frozen" in df_text
    assert "\nUSER " in df_text
    assert df_text.index("uv sync --frozen") < df_text.index("\nUSER ")


def test_req_086_lazy_extractors_pymupdf_free_worker_zero_cycles_and_v480_invite_only_surface(
    tmp_path: Path,
) -> None:
    """REQ-086: collab_worker & Markdown extract work without heavy libs; zero circular imports/SyntaxWarnings; v4.8.0 invite-only copy."""
    import ast
    import subprocess
    import warnings
    from starlette.testclient import TestClient
    from scripts.mcp_server.app import MCP_SERVER_INSTRUCTIONS, create_app

    repo_root = Path(__file__).resolve().parent.parent

    # 1. Import collab_worker, extractors, and extract Markdown without pymupdf/fitz/ebooklib/docx/mobi
    md_in = tmp_path / "sample.md"
    md_in.write_text("# Chapter 1\n\nHello world from markdown.\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    probe_code = (
        "import sys\n"
        "for mod in ('pymupdf', 'fitz', 'ebooklib', 'docx', 'mobi'):\n"
        "    sys.modules[mod] = None\n"
        "import scripts.collab_worker\n"
        "import scripts.extractors\n"
        "from scripts.extract import extract_book\n"
        f"res = extract_book({repr(str(md_in))}, output_dir={repr(str(out_dir))}, slug='sample-md')\n"
        "assert len(res) >= 1\n"
    )
    probe = subprocess.run([sys.executable, "-c", probe_code], capture_output=True, text=True)
    assert probe.returncode == 0, f"Lazy import/extract failed without heavy libs: {probe.stderr}"

    # 2. Zero SyntaxWarnings on scripts/compilers/epub_compiler.py
    epub_src = (repo_root / "scripts" / "compilers" / "epub_compiler.py").read_text(encoding="utf-8")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", SyntaxWarning)
        compile(epub_src, "scripts/compilers/epub_compiler.py", "exec")
    assert not [w for w in caught if issubclass(w.category, SyntaxWarning)], f"SyntaxWarning in epub_compiler.py: {caught}"

    # 3. Zero circular imports across scripts/**/*.py
    scripts_dir = repo_root / "scripts"
    py_files = sorted(scripts_dir.rglob("*.py"))
    mod_names: dict[Path, str] = {}
    for pf in py_files:
        rel = pf.relative_to(repo_root).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        mod_names[pf] = ".".join(parts)
    known_mods = set(mod_names.values())
    graph: dict[str, set[str]] = {m: set() for m in known_mods}
    for pf, mod in mod_names.items():
        tree = ast.parse(pf.read_text(encoding="utf-8"), filename=str(pf))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("scripts"):
                if node.module in known_mods and node.module != mod:
                    graph[mod].add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in known_mods and alias.name != mod:
                        graph[mod].add(alias.name)
    visited: dict[str, int] = {}
    cycles: list[list[str]] = []

    def _dfs(node: str, stack: list[str]) -> None:
        visited[node] = 1
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            if visited.get(nxt) == 1:
                idx = stack.index(nxt)
                cycles.append(stack[idx:] + [nxt])
            elif visited.get(nxt, 0) == 0:
                _dfs(nxt, stack)
        stack.pop()
        visited[node] = 2

    for mod in sorted(known_mods):
        if visited.get(mod, 0) == 0:
            _dfs(mod, [])
    assert not cycles, f"Circular imports detected: {cycles}"

    # 4. v4.8.0 & invite-only onboarding text across /api/health, /llms.txt, instructions, & README
    cfg, store, _, _, service = _make_hardening_service(tmp_path / "v480")
    try:
        app = create_app(cfg=cfg, service=service)
        with TestClient(app) as client:
            health = client.get("/api/health").json()
            assert health["version"] == "4.8.0"
            llms = client.get("/llms.txt").text
            assert "v4.8.0" in llms
            assert "ANONYMOUS" in llms
            assert "read-only" in llms.lower()
            assert "admin-minted" in llms.lower() or "AGENT_KEY" in llms
            assert "first anonymous `claim_chapter`" not in llms
    finally:
        store.close()
    assert "v4.8.0" in MCP_SERVER_INSTRUCTIONS
    readme_text = (repo_root / "README.md").read_text(encoding="utf-8")
    assert "v4.8.0" in readme_text
    assert "Invite-Only" in readme_text or "admin-minted" in readme_text
    assert "Zero-Account Agent Onboarding" not in readme_text


