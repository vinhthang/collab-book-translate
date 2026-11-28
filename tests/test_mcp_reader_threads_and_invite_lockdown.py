"""TDD tests for REQ-070..REQ-072: Invite-only lockdown, Human Web Reader, & Clean Agent Threads UI."""

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


def test_req_070_invite_only_lockdown_private_manuscript_block_concise_reviews_and_test_purge(tmp_path: Path) -> None:
    """REQ-070: Default allow_anonymous_writes=False blocks anon registration/writes & owner-permission reads, caps reviews at 2000 chars, and purges test clutter."""
    from scripts.mcp_server.service import MAX_REVIEW_BODY_CHARS

    assert MAX_REVIEW_BODY_CHARS == 2_000
    cfg = _make_cfg(tmp_path)
    assert cfg.allow_anonymous_writes is False

    store = CollabStore(cfg)
    store.init_db()
    sync = CollabBooksGitSync(cfg, publish_github_release=False)
    svc = CollabMcpService(cfg=cfg, store=store, review_client=_FakeReviewClient(), git_sync=sync)

    admin_hdr = {"Authorization": f"Bearer {cfg.collab_admin_token}"}
    sso_hdr = {"x-auth-request-email": "admin@vinhthang.dev"}

    # Pre-seed leftover rejected smoke books + orphan test agents to verify startup & manual purge
    e2e_bot = store.register_agent(handle="e2e-trans-999", model_info="test")
    keep_bot = store.register_agent(handle="real-helper", model_info="gemini")
    store.upsert_book(
        slug="live-smoke-1795115993",
        title="Smoke Test",
        author="Bot",
        source_lang="en",
        target_lang="vi",
        genre="fiction",
        license_id="public-domain",
        license_url="https://example.org/pd",
        status="rejected",
        glossary_sha="",
        book_record={},
        manifest={"slug": "live-smoke-1795115993", "license": "public-domain"},
        proposed_by=e2e_bot["handle"],
    )
    store.upsert_chapter(
        book_slug="live-smoke-1795115993",
        chapter_id="ch01",
        chapter_num=1,
        title="Ch 1",
        source_xml="<segment id='S-0001'><original>Hi</original></segment>",
        status="open",
    )
    sub_smoke = store.create_submission(
        book_slug="live-smoke-1795115993",
        chapter_id="ch01",
        author_actor_id=f"agent:{e2e_bot['handle']}",
        author_handle=e2e_bot["handle"],
        engine_model="test",
        status="rejected",
        round_number=1,
        max_rounds=3,
        workstream_id="",
        revision_token_sha256="x",
        translation_xml="",
        final_txt="",
        additions={},
        open_findings=[],
        security_flags=[],
    )
    store.add_peer_review(
        submission_id=sub_smoke["id"],
        reviewer_actor_id=f"agent:{keep_bot['handle']}",
        reviewer_handle=keep_bot["handle"],
        reviewer_role="contributor",
        verdict="CHANGES_REQUESTED",
        scores={"fidelity": 4.0, "fluency": 4.0, "terminology": 4.0, "register": 4.0, "structure": 4.0, "completeness": 4.0},
        weighted_score=4.0,
        issues=[],
        body_md="Smoke review.",
    )
    # Also ensure an active real book is untouched even if name contains nothing test-prefixed
    pub_src, pub_tr = _seg("Public sentence for chapter one.", "Câu công khai cho chương một.")
    priv_src, priv_tr = _seg("Ray Dalio secret economic analysis.", "Phân tích kinh tế riêng tư của Ray Dalio.")

    with patch("scripts.mcp_server.app.ReviewServerClient", lambda _cfg: _FakeReviewClient()):
        app = create_app(cfg=cfg, service=svc)
    with TestClient(app) as client:
        # 1. Admin creates books & mints a contributor + agent_admin key (while anon cannot self-register!)
        aa = client.post("/admin/api/agents/mint", headers=sso_hdr, json={"handle": "aa-lock", "model_info": "opus"})
        peer = client.post("/admin/api/agents/mint", headers=sso_hdr, json={"handle": "peer-lock", "model_info": "gemini"})
        assert aa.status_code == 201 and peer.status_code == 201
        aa_key = aa.json()["agent_key"]
        peer_key = peer.json()["agent_key"]
        client.post("/api/v1/admin/agents/role", headers=admin_hdr, json={"identifier": "aa-lock", "role": "agent_admin"})

        aa_hdr = {"Authorization": f"Bearer {aa_key}"}
        peer_hdr = {"Authorization": f"Bearer {peer_key}"}

        # Trigger purge via Admin endpoint (and verify non-admin gets 403)
        assert client.post("/api/v1/admin/purge-test-artifacts", headers=peer_hdr).status_code == 403
        purge_res = client.post("/admin/api/purge-test-artifacts", headers=sso_hdr)
        assert purge_res.status_code == 200
        assert "live-smoke-1795115993" in purge_res.json()["purged_books"]
        assert "e2e-trans-999" in purge_res.json()["purged_agents"]
        assert store.get_agent("real-helper") is not None

        # Seed one public-domain book & one owner-permission book via agent_admin
        client.post(
            "/api/v1/books/propose",
            headers=aa_hdr,
            json={
                "slug": "public-book",
                "title": "Public Book",
                "author": "O. Henry",
                "source_lang": "en",
                "license": "public-domain",
                "license_url": "https://example.org/pd",
                "source_filename": "pub.md",
                "book_record": {"terms": {"Public": "công khai"}},
                "chapters": [{"chapter_id": "ch01", "title": "Ch 1", "source_xml": pub_src, "translation_xml": pub_tr}],
            },
        )
        client.post(
            "/api/v1/books/propose",
            headers=aa_hdr,
            json={
                "slug": "dalio-locked",
                "title": "Dalio Locked Book",
                "author": "Ray Dalio",
                "source_lang": "en",
                "license": "owner-permission",
                "license_url": "https://example.org/perm",
                "source_filename": "dalio.md",
                "book_record": {"terms": {"cycle": "chu kỳ"}},
                "chapters": [{"chapter_id": "ch01", "title": "Ch 1", "source_xml": priv_src, "translation_xml": priv_tr}],
            },
        )

        # 2. Anonymous self-registration and ALL anonymous writes fail cleanly with 403 and zero minted agents
        agents_before = len(store.list_agents())
        assert client.post("/api/v1/agents/register", json={"handle": "stranger-bot"}).status_code == 403
        assert client.post("/api/v1/agents/register", headers=peer_hdr, json={"handle": "stranger-2"}).status_code == 403
        assert client.post("/api/v1/books/public-book/chapters/ch01/claim").status_code == 403
        assert (
            client.post(
                "/api/v1/books/propose",
                json={
                    "slug": "anon-book",
                    "title": "Anon",
                    "author": "Anon",
                    "source_lang": "en",
                    "license": "public-domain",
                    "license_url": "https://example.org/pd",
                    "source_filename": "a.md",
                    "source_text": "# Ch 1\n\nHi.",
                },
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/submissions",
                json={"book_slug": "public-book", "chapter_id": "ch01", "translation_xml": pub_tr},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/bugs",
                json={"title": "Anon spam bug", "body_md": "Just chatting in the bug queue."},
            ).status_code
            == 403
        )
        assert len(store.list_agents()) == agents_before

        # 3. ANONYMOUS callers CANNOT see or read owner-permission books via /api/v1/* or MCP!
        anon_books = [b["slug"] for b in client.get("/api/v1/books").json()["books"]]
        assert "public-book" in anon_books
        assert "dalio-locked" not in anon_books
        assert client.get("/api/v1/books/dalio-locked").status_code == 403
        assert client.get("/api/v1/books/dalio-locked/chapters/ch01").status_code == 403
        assert client.get("/api/v1/books/dalio-locked/source").status_code == 403
        anon_tasks = client.get("/api/v1/tasks", params={"role": "reviewer"}).json()
        assert all(c["book_slug"] != "dalio-locked" for c in anon_tasks["peer_review_candidates"])

        # Authenticated contributor CAN list & review dalio-locked
        peer_books = [b["slug"] for b in client.get("/api/v1/books", headers=peer_hdr).json()["books"]]
        assert "dalio-locked" in peer_books
        dalio_sub_id = client.get("/api/v1/tasks", headers=peer_hdr, params={"role": "reviewer", "book_slug": "dalio-locked"}).json()["peer_review_candidates"][0]["submission_id"]
        assert client.post(f"/api/v1/submissions/{dalio_sub_id}/reviews", json={"body_md": "Anon reply", "verdict": "COMMENT"}).status_code == 403

        # 4. Concise review cap (<= 2,000 chars -> 201; 2,001 chars -> 422)
        long_essay = "S-0001 overly verbose essay. " + ("x" * 2000) + "\n- Fidelity: 4.8\n- Fluency: 4.8\n- Terminology: 4.8\n- Register: 4.8\n- Structure: 5.0\n- Completeness: 5.0\n"
        res_422 = client.post(
            f"/api/v1/submissions/{dalio_sub_id}/reviews",
            headers=peer_hdr,
            json={"verdict": "APPROVE", "body_md": long_essay},
        )
        assert res_422.status_code == 422

        concise_ok = "S-0001 accurate.\n- Fidelity: 4.9\n- Fluency: 4.8\n- Terminology: 4.9\n- Register: 4.8\n- Structure: 5.0\n- Completeness: 5.0\n"
        res_ok = client.post(
            f"/api/v1/submissions/{dalio_sub_id}/reviews",
            headers=peer_hdr,
            json={"verdict": "APPROVE", "body_md": concise_ok},
        )
        assert res_ok.status_code == 201
        assert res_ok.json()["submission"]["status"] == "accepted"

    # 5. CollabStore missing-slug & partial-None boundary hardening (db.py invariants)
    with pytest.raises(KeyError):
        store.set_book_access(slug="missing", access_mode="public", allowed_agents=[])
    with pytest.raises(KeyError):
        store.set_book_approval_mode(slug="missing", approval_mode="auto")
    with pytest.raises(KeyError):
        store.set_book_status(slug="missing", status="active")
    with pytest.raises(KeyError):
        store.set_book_brief_and_record(slug="missing")

    updated_pub = store.set_book_brief_and_record(
        slug="public-book",
        brief={"tone": "warm"},
        book_record=None,
        glossary_sha=None,
    )
    assert updated_pub["brief"] == {"tone": "warm"}
    assert updated_pub["book_record"]["terms"] == {"Public": "công khai"}
    assert updated_pub["glossary_sha"] != ""
    store.close()
    store.close()


def test_req_071_web_reader_public_vs_admin_sso_bilingual_and_drafts(tmp_path: Path) -> None:
    """REQ-071: Public /read/{slug} (open corpus only), Admin /admin/read/{slug} (all books + drafts), bilingual toggle, figure callout, & XSS safety."""
    cfg = _make_cfg(tmp_path)
    store = CollabStore(cfg)
    store.init_db()
    sync = CollabBooksGitSync(cfg, publish_github_release=False)
    svc = CollabMcpService(cfg=cfg, store=store, review_client=_FakeReviewClient(), git_sync=sync)

    sso_hdr = {"x-auth-request-email": "admin@vinhthang.dev"}
    admin_hdr = {"Authorization": f"Bearer {cfg.collab_admin_token}"}
    with patch("scripts.mcp_server.app.ReviewServerClient", lambda _cfg: _FakeReviewClient()):
        app = create_app(cfg=cfg, service=svc)
    with TestClient(app) as client:
        aa = client.post("/admin/api/agents/mint", headers=sso_hdr, json={"handle": "aa-read", "model_info": "opus"}).json()
        peer = client.post("/admin/api/agents/mint", headers=sso_hdr, json={"handle": "peer-read", "model_info": "gemini"}).json()
        client.post("/api/v1/admin/agents/role", headers=admin_hdr, json={"identifier": "aa-read", "role": "agent_admin"})
        aa_hdr = {"Authorization": f"Bearer {aa['agent_key']}"}
        peer_hdr = {"Authorization": f"Bearer {peer['agent_key']}"}

        # Public book: ch00 boilerplate, ch01 accepted, ch02 still pending_review; includes figure link & XSS probe in book title / prose
        c0_src, c0_tr = _seg("![Cover: Reader](images/cover.jpg)", "![Cover: Reader](images/cover.jpg)")
        c1_src, c1_tr = _seg(
            "# Chapter 1: The Gift\n\nThe Prince smiled.\n\n![Swallow Chart](images/swallow.jpg)",
            "# Chương 1: Món quà &lt;script&gt;alert(1)&lt;/script&gt;\n\nHoàng tử mỉm cười.\n\n![Swallow Chart](images/swallow.jpg)",
        )
        c2_src, c2_tr = _seg("Second chapter English draft.", "Bản nháp chương hai tiếng Việt.")
        p_resp = client.post(
            "/api/v1/books/propose",
            headers=aa_hdr,
            json={
                "slug": "reader-public",
                "title": "Reader Public <script>alert(1)</script> Book",
                "author": "Oscar Wilde",
                "source_lang": "en",
                "license": "public-domain",
                "license_url": "https://example.org/pd",
                "source_filename": "pub.md",
                "book_record": {"terms": {"Prince": "Hoàng tử"}},
                "chapters": [
                    {"chapter_id": "ch00", "title": "Bìa sách (Cover)", "source_xml": c0_src, "translation_xml": c0_tr},
                    {"chapter_id": "ch01", "title": "Opening", "source_xml": c1_src, "translation_xml": c1_tr},
                    {"chapter_id": "ch02", "title": "Later", "source_xml": c2_src, "translation_xml": c2_tr},
                ],
            },
        )
        assert p_resp.status_code == 201
        t_pub = client.get("/api/v1/tasks", headers=peer_hdr, params={"role": "reviewer", "book_slug": "reader-public"}).json()["peer_review_candidates"]
        sub1 = next(x["submission_id"] for x in t_pub if x["chapter_id"] == "ch01")
        client.post(
            f"/api/v1/submissions/{sub1}/reviews",
            headers=peer_hdr,
            json={"verdict": "APPROVE", "body_md": "S-0001 good.\n- Fidelity: 4.8\n- Fluency: 4.8\n- Terminology: 4.8\n- Register: 4.8\n- Structure: 5.0\n- Completeness: 5.0\n"},
        )

        # Owner-permission Dalio book: ch01 accepted, ch02 pending_review draft
        d1_src, d1_tr = _seg("Ray Dalio Chapter 1 English.", "Ray Dalio Chương 1 Tiếng Việt.")
        d2_src, d2_tr = _seg("Ray Dalio Chapter 2 Draft.", "Bản nháp Chương 2 Dalio.")
        client.post(
            "/api/v1/books/propose",
            headers=aa_hdr,
            json={
                "slug": "dalio-reader",
                "title": "Changing World Order",
                "author": "Ray Dalio",
                "source_lang": "en",
                "license": "owner-permission",
                "license_url": "https://example.org/perm",
                "source_filename": "d.md",
                "book_record": {"terms": {"Cycle": "Chu kỳ"}},
                "chapters": [
                    {"chapter_id": "ch01", "title": "Introduction", "source_xml": d1_src, "translation_xml": d1_tr},
                    {"chapter_id": "ch02", "title": "The Big Cycle", "source_xml": d2_src, "translation_xml": d2_tr},
                ],
            },
        )
        d_sub1 = client.get("/api/v1/tasks", headers=peer_hdr, params={"role": "reviewer", "book_slug": "dalio-reader"}).json()["peer_review_candidates"][0]["submission_id"]
        client.post(
            f"/api/v1/submissions/{d_sub1}/reviews",
            headers=peer_hdr,
            json={"verdict": "APPROVE", "body_md": "S-0001 good.\n- Fidelity: 4.9\n- Fluency: 4.8\n- Terminology: 4.9\n- Register: 4.8\n- Structure: 5.0\n- Completeness: 5.0\n"},
        )

        # 1. Public Reader shows accepted ch01 (active TOC, collapsed boilerplate, escaped XSS, figure callout, bilingual EN+VI markup, node --check JS)
        r_pub = client.get("/read/reader-public")
        assert r_pub.status_code == 200
        assert '<a class="active" href="/read/reader-public/ch01">' in r_pub.text
        assert '<a class="" href="/read/reader-public/ch02">' in r_pub.text
        assert "<details><summary>Boilerplate (1)</summary>" in r_pub.text
        assert "Hoàng tử mỉm cười." in r_pub.text
        assert "The Prince smiled." in r_pub.text
        assert "Swallow Chart" in r_pub.text
        assert "<script>alert(1)</script>" not in r_pub.text
        script_m = re.search(r"<script>([\s\S]*?)</script>\s*</body>", r_pub.text)
        assert script_m is not None
        js_file = tmp_path / "reader_check.js"
        js_file.write_text(script_m.group(1), encoding="utf-8")
        assert subprocess.run(["node", "--check", str(js_file)], capture_output=True, text=True).returncode == 0

        # Navigating into boilerplate ch00 expands the Boilerplate section heading cleanly
        r_bp = client.get("/read/reader-public/ch00")
        assert r_bp.status_code == 200
        assert "<h3>Boilerplate</h3>" in r_bp.text
        assert '<a class="active" href="/read/reader-public/ch00">' in r_bp.text

        # Public Reader shows clean placeholder for unaccepted ch02
        r_pub2 = client.get("/read/reader-public/ch02")
        assert r_pub2.status_code == 200
        assert "In translation" in r_pub2.text
        assert "Bản nháp chương hai tiếng Việt." not in r_pub2.text

        # 2. Public Reader BLOCKS owner-permission books (404), and /admin/read/* requires SSO/ADMIN (403)
        assert client.get("/read/dalio-reader").status_code == 404
        assert client.get("/admin/read/dalio-reader").status_code == 403

        # 3. Google SSO Admin Reader renders BOTH accepted ch01 and pending_review draft ch02 of Dalio book!
        r_adm1 = client.get("/admin/read/dalio-reader", headers=sso_hdr)
        assert r_adm1.status_code == 200
        assert "Ray Dalio Chương 1 Tiếng Việt." in r_adm1.text
        r_adm2 = client.get("/admin/read/dalio-reader/ch02", headers=sso_hdr)
        assert r_adm2.status_code == 200
        assert "Draft &middot; Pending Review" in r_adm2.text or "Draft · Pending Review" in r_adm2.text
        assert "Bản nháp Chương 2 Dalio." in r_adm2.text
    store.close()


def test_req_072_agent_threads_ui_collapsible_reviews_and_v440(tmp_path: Path) -> None:
    """REQ-072: /threads (public corpus only) & /admin/threads (all), collapsible >400-char reviews, invite-only '/', /admin links, and v4.4.0."""
    cfg = _make_cfg(tmp_path)
    store = CollabStore(cfg)
    store.init_db()
    sync = CollabBooksGitSync(cfg, publish_github_release=False)
    svc = CollabMcpService(cfg=cfg, store=store, review_client=_FakeReviewClient(), git_sync=sync)

    sso_hdr = {"x-auth-request-email": "admin@vinhthang.dev"}
    admin_hdr = {"Authorization": f"Bearer {cfg.collab_admin_token}"}
    with patch("scripts.mcp_server.app.ReviewServerClient", lambda _cfg: _FakeReviewClient()):
        app = create_app(cfg=cfg, service=svc)
    with TestClient(app) as client:
        aa = client.post("/admin/api/agents/mint", headers=sso_hdr, json={"handle": "aa-th", "model_info": "opus"}).json()
        peer = client.post("/admin/api/agents/mint", headers=sso_hdr, json={"handle": "peer-th", "model_info": "gemini"}).json()
        client.post("/api/v1/admin/agents/role", headers=admin_hdr, json={"identifier": "aa-th", "role": "agent_admin"})
        aa_hdr = {"Authorization": f"Bearer {aa['agent_key']}"}
        peer_hdr = {"Authorization": f"Bearer {peer['agent_key']}"}

        p_src, p_tr = _seg("Public prose line.", "Câu văn công khai.")
        p2_src, p2_tr = _seg("Public chapter two line.", "Câu văn chương hai công khai.")
        d_src, d_tr = _seg("Dalio private line.", "Câu văn riêng tư.")
        client.post(
            "/api/v1/books/propose",
            headers=aa_hdr,
            json={
                "slug": "pub-thread-book",
                "title": "Public Thread Book",
                "author": "O. Henry",
                "source_lang": "en",
                "license": "public-domain",
                "license_url": "https://example.org/pd",
                "source_filename": "p.md",
                "book_record": {"terms": {"prose": "văn xuôi"}},
                "chapters": [
                    {"chapter_id": "ch01", "title": "Ch 1", "source_xml": p_src, "translation_xml": p_tr},
                    {"chapter_id": "ch02", "title": "Ch 2", "source_xml": p2_src, "translation_xml": p2_tr},
                ],
            },
        )
        client.post(
            "/api/v1/books/propose",
            headers=aa_hdr,
            json={
                "slug": "dalio-thread-book",
                "title": "Dalio Thread Book",
                "author": "Ray Dalio",
                "source_lang": "en",
                "license": "owner-permission",
                "license_url": "https://example.org/perm",
                "source_filename": "d.md",
                "book_record": {"terms": {"cycle": "chu kỳ"}},
                "chapters": [{"chapter_id": "ch01", "title": "Ch 1", "source_xml": d_src, "translation_xml": d_tr}],
            },
        )
        pub_cands = client.get("/api/v1/tasks", headers=peer_hdr, params={"role": "reviewer", "book_slug": "pub-thread-book"}).json()["peer_review_candidates"]
        sub_p = next(x["submission_id"] for x in pub_cands if x["chapter_id"] == "ch01")
        sub_p2 = next(x["submission_id"] for x in pub_cands if x["chapter_id"] == "ch02")
        sub_d = client.get("/api/v1/tasks", headers=peer_hdr, params={"role": "reviewer", "book_slug": "dalio-thread-book"}).json()["peer_review_candidates"][0]["submission_id"]

        long_pub_rev = "S-0001 verified with legacy verbose commentary. " + ("detail " * 80) + "\n- Fidelity: 4.9\n- Fluency: 4.8\n- Terminology: 4.9\n- Register: 4.8\n- Structure: 5.0\n- Completeness: 5.0\n"
        scores_tail = "\n- Fidelity: 3.5\n- Fluency: 3.5\n- Terminology: 3.5\n- Register: 3.5\n- Structure: 4.0\n- Completeness: 4.0"
        rev_400 = ("S-0001 tighten phrasing. " + ("z" * (400 - len("S-0001 tighten phrasing. ") - len(scores_tail)))) + scores_tail
        assert len(rev_400) == 400
        short_dalio_rev = "S-0001 concise Dalio review.\n- Fidelity: 4.9\n- Fluency: 4.9\n- Terminology: 4.9\n- Register: 4.9\n- Structure: 5.0\n- Completeness: 5.0\n"
        client.post(f"/api/v1/submissions/{sub_p}/reviews", headers=peer_hdr, json={"verdict": "APPROVE", "body_md": long_pub_rev})
        client.post(f"/api/v1/submissions/{sub_p2}/reviews", headers=peer_hdr, json={"verdict": "CHANGES_REQUESTED", "body_md": rev_400})
        client.post(f"/api/v1/submissions/{sub_d}/reviews", headers=peer_hdr, json={"verdict": "APPROVE", "body_md": short_dalio_rev})
        client.post(
            "/api/v1/bugs",
            headers=peer_hdr,
            json={"title": "Private bug title", "body_md": "Bug in private Dalio book.", "book_slug": "dalio-thread-book"},
        )
        client.post(
            "/api/v1/bugs",
            headers=peer_hdr,
            json={"title": "Public bug title <script>alert(2)</script>", "body_md": "Bug in public book.", "book_slug": "pub-thread-book"},
        )

        # 1. Public /threads shows public review (collapsed >400 in <details>, exact 400 NOT collapsed) & public bug, hides owner-permission!
        th_pub = client.get("/threads")
        assert th_pub.status_code == 200
        assert "Public Thread Book" in th_pub.text
        assert "<details>" in th_pub.text
        assert "Expand full entry (400 chars)" not in th_pub.text
        assert "badge-ok" in th_pub.text
        assert "badge-warn" in th_pub.text
        assert '<a class="btn" href="/threads?kind=all"' in th_pub.text
        assert '<a class="btn btn-sec" href="/threads?kind=reviews"' in th_pub.text
        assert "Public bug title &lt;script&gt;alert(2)&lt;/script&gt;" in th_pub.text
        assert "<script>alert(2)</script>" not in th_pub.text
        assert "Dalio Thread Book" not in th_pub.text
        assert "Private bug title" not in th_pub.text

        # Filtered /threads?kind=reviews&book_slug=pub-thread-book selects active tab & book option cleanly
        th_filt = client.get("/threads", params={"kind": "reviews", "book_slug": "pub-thread-book"})
        assert th_filt.status_code == 200
        assert '<option value="pub-thread-book" selected>' in th_filt.text
        assert '<a class="btn" href="/threads?kind=reviews&book_slug=pub-thread-book"' in th_filt.text
        assert '<a class="btn btn-sec" href="/threads?kind=all&book_slug=pub-thread-book"' in th_filt.text

        # 2. /admin/threads requires SSO/ADMIN (403) and shows ALL books incl. Dalio + bugs
        assert client.get("/admin/threads").status_code == 403
        th_adm = client.get("/admin/threads", headers=sso_hdr)
        assert th_adm.status_code == 200
        assert "Dalio Thread Book" in th_adm.text
        assert "Private bug title" in th_adm.text

        # 3. '/' links Read + Threads & advertises Invite-Only (no "Connect Any AI Agent in 10 Seconds")
        home = client.get("/")
        assert home.status_code == 200
        assert "/read/pub-thread-book" in home.text
        assert "/threads" in home.text
        assert "Connect Any AI Agent in 10 Seconds" not in home.text
        assert "Invite-Only" in home.text

        # 4. /admin links /admin/threads, /admin/read/<slug>, #btn-purge-tests, passes node --check
        adm_html = client.get("/admin", headers=sso_hdr).text
        assert "/admin/threads" in adm_html
        assert "/admin/read/dalio-thread-book" in adm_html
        assert "btn-purge-tests" in adm_html
        script_m = re.search(r"<script>([\s\S]*?)</script>\s*</body>", adm_html)
        assert script_m is not None
        js_file = tmp_path / "admin_v440_check.js"
        js_file.write_text(script_m.group(1), encoding="utf-8")
        assert subprocess.run(["node", "--check", str(js_file)], capture_output=True, text=True).returncode == 0

        # 5. Version 4.5.0 across /api/health, /llms.txt, plugin.json, MCP initialize
        assert client.get("/api/health").json()["version"] == "4.8.0"
        assert "v4.8.0" in client.get("/llms.txt").text
        repo_root = Path(__file__).resolve().parent.parent
        assert json.loads((repo_root / "plugin.json").read_text(encoding="utf-8"))["version"] == "4.8.0"
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
        assert init_rpc["result"]["serverInfo"]["version"] == "4.8.0"
    store.close()
