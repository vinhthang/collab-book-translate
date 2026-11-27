"""TDD tests for REQ-073..REQ-075: /admin Workspace Console Rework (MCP v4.5.0 / v0.7.0)."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from typing import Any
from unittest.mock import patch
import pytest
from starlette.testclient import TestClient

from scripts.mcp_server.app import create_app
from scripts.mcp_server.config import McpServerConfig
from scripts.mcp_server.db import CollabStore
from scripts.mcp_server.git_sync import CollabBooksGitSync
from scripts.mcp_server.service import CollabMcpService


class _FakeReviewClient:
    """Deterministic stub for review-server calls."""

    def __init__(self) -> None:
        self._ws_seq = 0

    def create_workstream(self, *, title: str, max_rounds: int = 3) -> dict[str, Any]:
        self._ws_seq += 1
        return {"id": f"ws-{self._ws_seq}", "currentStage": "TRANSLATION_REVIEW", "stageStatus": "INIT"}

    def run_translation_round(
        self,
        *,
        workstream_id: str,
        content: str,
        rebuttals: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        return {
            "verdict": "PASS",
            "stageStatus": "GOVERNANCE_CLEAN",
            "roundNumber": 1,
            "p0Count": 0,
            "p1Count": 0,
            "p2Count": 0,
            "judgeSummary": "Clean translation.",
            "openFindings": [],
        }

    def approve_workstream(self, *, workstream_id: str, override_escalated: bool = False) -> dict[str, Any]:
        return {"id": workstream_id, "stageStatus": "APPROVED"}


def _git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)
    return res.stdout.strip()


def _make_cfg(tmp_path: Path) -> McpServerConfig:
    bare_remote = tmp_path / "remote" / "collab-books.git"
    bare_remote.mkdir(parents=True)
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_remote)], check=True, capture_output=True)

    work_repo = tmp_path / "work" / "collab-books"
    work_repo.mkdir(parents=True)
    _git(work_repo, "init", "-b", "main")
    _git(work_repo, "remote", "add", "origin", str(bare_remote))
    (work_repo / "README.md").write_text("# Collab Books\n", encoding="utf-8")
    _git(work_repo, "add", "README.md")
    _git(work_repo, "-c", "user.name=Bot", "-c", "user.email=b@e.com", "commit", "-m", "init")
    _git(work_repo, "push", "-u", "origin", "main")

    return McpServerConfig(
        database_url="",
        sqlite_path=tmp_path / "collab_mcp.db",
        books_repo_dir=work_repo,
        books_repo_full_name="vinhthang/collab-books",
        collab_admin_token="super-secret-admin",
        review_server_url="https://review.vinhthang.dev",
        review_bearer_token="rev-token",
        admin_emails=frozenset({"admin@vinhthang.dev"}),
        trust_admin_sso_header=True,
        github_token="",
        public_base_url="https://collab.vinhthang.dev",
        bot_git_name="Collab Bot",
        bot_git_email="bot@vinhthang.dev",
        anon_read_rpm=500,
        anon_write_rpm=100,
        agent_write_rpm=200,
    )


def _seg(orig: str, vi: str = "", *, seg_id: str = "S-0001", seg_type: str = "paragraph") -> tuple[str, str]:
    src = f'<segment id="{seg_id}" type="{seg_type}">\n<original>\n{orig}\n</original>\n</segment>\n'
    tr = (
        f'<segment id="{seg_id}" type="{seg_type}">\n<original>\n{orig}\n</original>\n'
        f'<translate>\n{vi}\n</translate>\n</segment>\n'
    )
    return src, tr


def test_req_073_agent_key_rotation_soft_revoke_delete_and_stats(tmp_path: Path) -> None:
    """REQ-073: Rotate AGENT_KEY, soft-revoke + reactivate referenced agents, hard-delete unused agents, and return submission/review counts."""
    cfg = _make_cfg(tmp_path)
    store = CollabStore(cfg)
    store.init_db()
    svc = CollabMcpService(
        cfg=cfg,
        store=store,
        review_client=_FakeReviewClient(),
        git_sync=CollabBooksGitSync(cfg, publish_github_release=False),
    )

    sso_hdr = {"x-auth-request-email": "admin@vinhthang.dev"}
    admin_hdr = {"Authorization": f"Bearer {cfg.collab_admin_token}"}
    with patch("scripts.mcp_server.app.ReviewServerClient", lambda _cfg: _FakeReviewClient()):
        app = create_app(cfg=cfg, service=svc)

    with TestClient(app) as client:
        writer = client.post("/admin/api/agents/mint", headers=sso_hdr, json={"handle": "active-writer", "model_info": "opus"}).json()
        reviewer = client.post("/admin/api/agents/mint", headers=sso_hdr, json={"handle": "active-reviewer", "model_info": "gemini"}).json()
        unused = client.post("/admin/api/agents/mint", headers=sso_hdr, json={"handle": "unused-bot", "model_info": "flash"}).json()

        writer_key = writer["agent_key"]
        rev_key = reviewer["agent_key"]
        writer_hdr = {"Authorization": f"Bearer {writer_key}"}
        rev_hdr = {"Authorization": f"Bearer {rev_key}"}

        client.post("/api/v1/admin/agents/role", headers=admin_hdr, json={"identifier": "active-writer", "role": "agent_admin"})

        s1, t1 = _seg("One star shone over the hill.", "Một ngôi sao tỏa sáng trên đỉnh đồi.")
        prop = client.post(
            "/api/v1/books/propose",
            headers=writer_hdr,
            json={
                "slug": "star-hill",
                "title": "Star Hill",
                "author": "O. Writer",
                "source_lang": "en",
                "license": "public-domain",
                "license_url": "https://example.org/pd",
                "source_filename": "star.md",
                "book_record": {"terms": {"hill": "đỉnh đồi"}},
                "chapters": [{"chapter_id": "ch01", "title": "Ch 1", "source_xml": s1, "translation_xml": t1}],
            },
        )
        assert prop.status_code == 201
        sub_id = client.get("/api/v1/tasks", headers=rev_hdr, params={"role": "reviewer", "book_slug": "star-hill"}).json()["peer_review_candidates"][0]["submission_id"]
        rev_res = client.post(
            f"/api/v1/submissions/{sub_id}/reviews",
            headers=rev_hdr,
            json={
                "verdict": "COMMENT",
                "body_md": "S-0001 reads smoothly.\n- Fidelity: 4.8\n- Fluency: 4.8\n- Terminology: 4.8\n- Register: 4.8\n- Structure: 5.0\n- Completeness: 5.0\n",
            },
        )
        assert rev_res.status_code == 201

        # 1. list_agents includes revoked_at="", submission_count, review_count
        agents_list = client.get("/admin/api/agents", headers=sso_hdr)
        assert agents_list.status_code == 200
        by_handle = {a["handle"]: a for a in agents_list.json()["agents"]}
        assert by_handle["active-writer"]["revoked_at"] == ""
        assert by_handle["active-writer"]["submission_count"] == 1
        assert by_handle["active-writer"]["review_count"] == 0
        assert by_handle["active-reviewer"]["submission_count"] == 0
        assert by_handle["active-reviewer"]["review_count"] == 1
        assert by_handle["unused-bot"]["submission_count"] == 0

        # 2. Boundary checks: non-admin 403, unknown 404
        assert client.post("/admin/api/agents/active-writer/rotate", headers=rev_hdr).status_code == 403
        assert client.post("/admin/api/agents/active-writer/revoke", headers=rev_hdr).status_code == 403
        assert client.post("/admin/api/agents/ghost-agent/rotate", headers=sso_hdr).status_code == 404
        assert client.post("/admin/api/agents/ghost-agent/revoke", headers=sso_hdr).status_code == 404

        # 3. Rotate key -> new 3-word key authenticates (200); old key rejected (401)
        rot = client.post("/admin/api/agents/active-writer/rotate", headers=sso_hdr)
        assert rot.status_code == 200
        new_key = rot.json()["agent_key"]
        assert new_key != writer_key
        assert re.match(r"^[a-z]+-[a-z]+-[a-z]+$", new_key)
        assert client.get("/api/v1/books", headers=writer_hdr).status_code == 401
        assert client.get("/api/v1/books", headers={"Authorization": f"Bearer {new_key}"}).status_code == 200

        # 4. Revoke referenced agent -> soft-revoke (revoked_at set, key 401, history intact) + reactivate -> 200
        rvk = client.post("/admin/api/agents/active-writer/revoke", headers=sso_hdr, json={})
        assert rvk.status_code == 200
        assert rvk.json()["action"] == "revoked"
        assert rvk.json()["agent"]["revoked_at"] != ""
        assert client.get("/api/v1/books", headers={"Authorization": f"Bearer {new_key}"}).status_code == 401
        assert store.get_submission(sub_id) is not None

        react = client.post("/admin/api/agents/active-writer/revoke", headers=sso_hdr, json={"reactivate": True})
        assert react.status_code == 200
        assert react.json()["action"] == "reactivated"
        assert react.json()["agent"]["revoked_at"] == ""
        assert client.get("/api/v1/books", headers={"Authorization": f"Bearer {new_key}"}).status_code == 200

        # 5. Revoke unused agent -> hard-delete row cleanly
        dlt = client.post(f"/api/v1/admin/agents/{unused['handle']}/revoke", headers=admin_hdr, json={})
        assert dlt.status_code == 200
        assert dlt.json()["action"] == "deleted"
        assert store.get_agent(unused["handle"]) is None
    store.close()


def test_req_074_admin_submission_detail_bulk_manage_and_private_book_export(tmp_path: Path) -> None:
    """REQ-074: Lazy submission detail, bulk approve/reject, and isolated SSO EPUB/PDF/DOCX export without Git writes."""
    cfg = _make_cfg(tmp_path)
    store = CollabStore(cfg)
    store.init_db()
    svc = CollabMcpService(
        cfg=cfg,
        store=store,
        review_client=_FakeReviewClient(),
        git_sync=CollabBooksGitSync(cfg, publish_github_release=False),
    )

    sso_hdr = {"x-auth-request-email": "admin@vinhthang.dev"}
    admin_hdr = {"Authorization": f"Bearer {cfg.collab_admin_token}"}
    with patch("scripts.mcp_server.app.ReviewServerClient", lambda _cfg: _FakeReviewClient()):
        app = create_app(cfg=cfg, service=svc)

    with TestClient(app) as client:
        aa = client.post("/admin/api/agents/mint", headers=sso_hdr, json={"handle": "aa-export", "model_info": "opus"}).json()
        peer = client.post("/admin/api/agents/mint", headers=sso_hdr, json={"handle": "peer-export", "model_info": "gemini"}).json()
        client.post("/api/v1/admin/agents/role", headers=admin_hdr, json={"identifier": "aa-export", "role": "agent_admin"})
        aa_hdr = {"Authorization": f"Bearer {aa['agent_key']}"}
        peer_hdr = {"Authorization": f"Bearer {peer['agent_key']}"}

        c1_s, c1_t = _seg("# Chapter 1: Money\n\nGold and paper money.", "# Chương 1: Tiền tệ\n\nVàng và tiền giấy.")
        c2_s, c2_t = _seg("# Chapter 2: Debt\n\nCredit expands and contracts.", "# Chương 2: Nợ nần\n\nTín dụng mở rộng rồi thu hẹp.")
        c3_s, c3_t = _seg("# Chapter 3: Policy\n\nCentral banks print currency.", "# Chương 3: Chính sách\n\nNgân hàng trung ương in tiền.")

        # Private owner-permission book with 3 pre-imported chapters pending review
        client.post(
            "/api/v1/books/propose",
            headers=aa_hdr,
            json={
                "slug": "dalio-export-demo",
                "title": "Debt Cycle Study",
                "author": "Ray Dalio",
                "source_lang": "en",
                "license": "owner-permission",
                "license_url": "https://example.org/perm",
                "source_filename": "dalio.md",
                "book_record": {"terms": {"Credit": "Tín dụng"}},
                "chapters": [
                    {"chapter_id": "ch01", "title": "Chapter 1: Money", "source_xml": c1_s, "translation_xml": c1_t},
                    {"chapter_id": "ch02", "title": "Chapter 2: Debt", "source_xml": c2_s, "translation_xml": c2_t},
                    {"chapter_id": "ch03", "title": "Chapter 3: Policy", "source_xml": c3_s, "translation_xml": c3_t},
                ],
            },
        )
        cands = client.get("/api/v1/tasks", headers=peer_hdr, params={"role": "reviewer", "book_slug": "dalio-export-demo"}).json()["peer_review_candidates"]
        sub_by_ch = {c["chapter_id"]: c["submission_id"] for c in cands}

        # 1. Lazy Submission Inspector detail endpoint + boundaries
        assert client.get(f"/api/v1/admin/submissions/{sub_by_ch['ch01']}", headers=peer_hdr).status_code == 403
        assert client.get("/admin/api/submissions/nonexistent-uuid", headers=sso_hdr).status_code == 404
        det = client.get(f"/admin/api/submissions/{sub_by_ch['ch01']}", headers=sso_hdr)
        assert det.status_code == 200
        det_sub = det.json()["submission"]
        assert "<translate>" in det_sub["translation_xml"]
        assert "Vàng và tiền giấy." in det_sub["final_txt"]
        assert det_sub["section_type"] == "body"
        assert det_sub["chapter_title"] == "Chapter 1: Money"

        # 2. Bulk Manage validation boundaries + approve & reject
        assert client.post("/api/v1/admin/submissions/bulk-manage", headers=peer_hdr, json={"submission_ids": [sub_by_ch["ch01"]], "action": "approve"}).status_code == 403
        assert client.post("/admin/api/submissions/bulk-manage", headers=sso_hdr, json={"submission_ids": [], "action": "approve"}).status_code == 400
        assert client.post("/admin/api/submissions/bulk-manage", headers=sso_hdr, json={"submission_ids": [sub_by_ch["ch01"]], "action": "nuke"}).status_code == 400

        # Incomplete export -> 409; invalid format -> 400; non-admin -> 403
        assert client.get("/api/v1/admin/books/dalio-export-demo/export?format=epub", headers=peer_hdr).status_code == 403
        assert client.get("/admin/api/books/dalio-export-demo/export?format=exe", headers=sso_hdr).status_code == 400
        assert client.get("/admin/api/books/dalio-export-demo/export?format=epub", headers=sso_hdr).status_code == 409

        # Bulk reject ch03, then bulk approve ch01 + ch02, re-accept ch03 via review -> book becomes completed
        blk_rej = client.post(
            "/admin/api/submissions/bulk-manage",
            headers=sso_hdr,
            json={"submission_ids": [sub_by_ch["ch03"]], "action": "reject"},
        )
        assert blk_rej.status_code == 200
        assert len(blk_rej.json()["processed"]) == 1
        assert store.get_chapter("dalio-export-demo", "ch03")["status"] == "open"

        blk_app = client.post(
            "/admin/api/submissions/bulk-manage",
            headers=sso_hdr,
            json={"submission_ids": [sub_by_ch["ch01"], sub_by_ch["ch02"]], "action": "approve"},
        )
        assert blk_app.status_code == 200
        assert len(blk_app.json()["processed"]) == 2

        # Finish ch03 cleanly
        s3_res = client.post(
            "/api/v1/submissions",
            headers=aa_hdr,
            json={"book_slug": "dalio-export-demo", "chapter_id": "ch03", "translation_xml": c3_t},
        )
        assert s3_res.status_code in {200, 201}
        sub3_id = s3_res.json()["submission"]["id"]
        client.post(
            f"/admin/submissions/{sub3_id}/manage",
            headers=sso_hdr,
            json={"action": "approve"},
        )
        assert store.get_book("dalio-export-demo")["status"] == "completed"

        # 3. Export completed private book as EPUB, PDF, and DOCX cleanly without touching books_repo_dir!
        head_before = _git(cfg.books_repo_dir, "rev-parse", "main")
        for fmt, magic_prefix, mime_part in (
            ("epub", b"PK", "epub"),
            ("pdf", b"%PDF", "pdf"),
            ("docx", b"PK", "wordprocessingml"),
        ):
            exp = client.get(f"/admin/api/books/dalio-export-demo/export?format={fmt}", headers=sso_hdr)
            assert exp.status_code == 200, f"Export {fmt} failed: {exp.text}"
            assert mime_part in exp.headers.get("content-type", "")
            assert f"dalio-export-demo_vietnamese.{fmt}" in exp.headers.get("content-disposition", "")
            assert exp.content.startswith(magic_prefix)
            assert len(exp.content) > 500

        assert _git(cfg.books_repo_dir, "rev-parse", "main") == head_before
        assert not (cfg.books_repo_dir / "books" / "dalio-export-demo").exists()
        assert not (cfg.books_repo_dir / "runs" / "dalio-export-demo").exists()
    store.close()


def test_req_075_admin_workspace_console_ui_unified_chrome_and_v450(tmp_path: Path) -> None:
    """REQ-075: Sidebar Workspace Console tabs, KPI overview, lazy drawers, bulk UI, XSS safety, node --check, unified chrome, & v4.5.0."""
    cfg = _make_cfg(tmp_path)
    store = CollabStore(cfg)
    store.init_db()
    svc = CollabMcpService(
        cfg=cfg,
        store=store,
        review_client=_FakeReviewClient(),
        git_sync=CollabBooksGitSync(cfg, publish_github_release=False),
    )

    sso_hdr = {"x-auth-request-email": "admin@vinhthang.dev"}
    admin_hdr = {"Authorization": f"Bearer {cfg.collab_admin_token}"}
    with patch("scripts.mcp_server.app.ReviewServerClient", lambda _cfg: _FakeReviewClient()):
        app = create_app(cfg=cfg, service=svc)

    with TestClient(app) as client:
        # 1. Version checks: 4.5.0 & v0.7.0
        assert client.get("/api/health").json()["version"] == "4.5.0"
        assert "v4.5.0" in client.get("/llms.txt").text
        repo_root = Path(__file__).resolve().parent.parent
        assert json.loads((repo_root / "plugin.json").read_text(encoding="utf-8"))["version"] == "4.5.0"
        init_rpc = client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
            },
        ).json()
        assert init_rpc["result"]["serverInfo"]["version"] == "4.5.0"

        aa = client.post("/admin/api/agents/mint", headers=sso_hdr, json={"handle": "aa-console", "display_name": "<script>xssA()</script>", "model_info": "opus"}).json()
        peer = client.post("/admin/api/agents/mint", headers=sso_hdr, json={"handle": "peer-console", "model_info": "gemini"}).json()
        client.post("/api/v1/admin/agents/role", headers=admin_hdr, json={"identifier": "aa-console", "role": "agent_admin"})
        aa_hdr = {"Authorization": f"Bearer {aa['agent_key']}"}
        peer_hdr = {"Authorization": f"Bearer {peer['agent_key']}"}

        s1, t1 = _seg("One star falls <script>xssSeg()</script>.", "Một ngôi sao rơi &lt;script&gt;xssTr()&lt;/script&gt;.")
        client.post(
            "/api/v1/books/propose",
            headers=aa_hdr,
            json={
                "slug": "console-dalio",
                "title": "Console <script>xssBook()</script> Private",
                "author": "Ray Dalio",
                "source_lang": "en",
                "license": "owner-permission",
                "license_url": "https://example.org/perm",
                "source_filename": "c.md",
                "book_record": {"terms": {"star": "ngôi sao"}},
                "chapters": [{"chapter_id": "ch01", "title": "Ch 1", "source_xml": s1, "translation_xml": t1}],
            },
        )
        sub_id = client.get("/api/v1/tasks", headers=peer_hdr, params={"role": "reviewer", "book_slug": "console-dalio"}).json()["peer_review_candidates"][0]["submission_id"]
        client.post(f"/api/v1/submissions/{sub_id}/reviews", headers=peer_hdr, json={"verdict": "APPROVE", "body_md": "S-0001 concise approve.\n- Fidelity: 4.9\n- Fluency: 4.9\n- Terminology: 4.9\n- Register: 4.9\n- Structure: 5.0\n- Completeness: 5.0\n"})
        prop_raw = client.post(
            "/api/v1/books/propose",
            headers=peer_hdr,
            json={
                "slug": "pending-no-presplit",
                "title": "Raw Pending Book",
                "author": "Anon Author",
                "source_lang": "en",
                "license": "public-domain",
                "license_url": "https://example.org/pd",
                "source_filename": "raw.md",
                "source_text": "# Ch 1\n\nRaw body text.",
            },
        )
        assert prop_raw.status_code in {200, 201}
        client.post("/api/v1/bugs", headers=peer_hdr, json={"title": "Console bug <script>xssBug()</script>", "body_md": "Detail", "book_slug": "console-dalio"})
        bug2_resp = client.post("/api/v1/bugs", headers=peer_hdr, json={"title": "Resolved console bug", "body_md": "Already handled.", "book_slug": "console-dalio"})
        assert bug2_resp.status_code in {200, 201}
        bug2_payload = bug2_resp.json()
        bug2_id = (bug2_payload.get("bug_report") or bug2_payload).get("id") or bug2_payload.get("bug_id")
        assert bug2_id
        tr_res = client.post(
            f"/admin/api/bugs/{bug2_id}/triage",
            headers=sso_hdr,
            json={"status": "fixed", "resolution_note": "Verified fixed"},
        )
        assert tr_res.status_code == 200

        # 2. /admin renders modular Workspace Console, drawers, bulk actions, rotate/revoke, export links, XSS escaping, zero secrets, valid JS
        assert client.get("/admin").status_code == 403
        adm = client.get("/admin", headers=sso_hdr)
        assert adm.status_code == 200
        html_out = adm.text
        for expected in (
            'data-tab="overview"',
            'data-tab="books"',
            'data-tab="submissions"',
            'data-tab="bugs"',
            'data-tab="agents"',
            "btn-theme-toggle",
            "js-inspect-book",
            "js-inspect-sub",
            "js-bulk-approve",
            "js-bulk-reject",
            "js-rotate-agent-key",
            "js-revoke-agent",
            "/admin/api/books/console-dalio/export?format=epub",
            "/admin/threads",
            "/admin/read/console-dalio",
            sub_id,
            "async function pollSeedCompletion",
            '<button class="btn btn-sec js-approve-open-all" data-slug="pending-no-presplit">',
            '<button class="btn js-approve-queue-parse" data-slug="pending-no-presplit">',
            '<div class="bug-item" data-bug-group="open"><div class="card card-action">',
            '<div class="bug-item" data-bug-group="resolved"><div class="card">',
            '<details>\n        <summary>Inspect bug details</summary>\n        <pre>Already handled.</pre>',
            '<div id="allowlist-console-dalio" class=" hidden">',
        ):
            assert expected in html_out, f"Missing {expected} in /admin HTML"

        for leaked in ("<script>xssA()</script>", "<script>xssBook()</script>", "<script>xssBug()</script>", aa["agent_key"], peer["agent_key"], cfg.collab_admin_token, cfg.review_bearer_token):
            assert leaked not in html_out

        m_js = re.search(r"<script>([\s\S]*?)</script>\s*</body>", html_out)
        assert m_js is not None
        js_path = tmp_path / "admin_console_check.js"
        js_path.write_text(m_js.group(1), encoding="utf-8")
        chk = subprocess.run(["node", "--check", str(js_path)], capture_output=True, text=True)
        assert chk.returncode == 0, chk.stderr

        # 3. Unified theme chrome on /admin/threads & /admin/read/console-dalio + node --check on reader JS
        th = client.get("/admin/threads", headers=sso_hdr)
        assert th.status_code == 200
        assert "btn-theme-toggle" in th.text and '[data-theme="light"]' in th.text
        rd = client.get("/admin/read/console-dalio", headers=sso_hdr)
        assert rd.status_code == 200
        assert "btn-theme-toggle" in rd.text and '[data-theme="light"]' in rd.text
        rd_js = re.search(r"<script>([\s\S]*?)</script>\s*</body>", rd.text)
        assert rd_js is not None
        rd_js_path = tmp_path / "reader_theme_check.js"
        rd_js_path.write_text(rd_js.group(1), encoding="utf-8")
        assert subprocess.run(["node", "--check", str(rd_js_path)], capture_output=True, text=True).returncode == 0
    store.close()
