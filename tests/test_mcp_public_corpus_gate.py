"""TDD tests for REQ-068..REQ-069: Public-corpus GitHub publishing gate & private-books storage isolation."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from typing import Any
import pytest
from starlette.testclient import TestClient
from unittest.mock import patch

from scripts.mcp_server.app import create_app
from scripts.mcp_server.auth import AuthContext
from scripts.mcp_server.config import McpServerConfig
from scripts.mcp_server.db import CollabStore
from scripts.mcp_server.git_sync import CollabBooksGitSync
from scripts.mcp_server.service import CollabMcpService, ServiceError


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


def _init_git_pair(tmp_path: Path) -> tuple[Path, Path]:
    bare_remote = tmp_path / "remote" / "collab-books.git"
    bare_remote.mkdir(parents=True)
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_remote)], check=True, capture_output=True)

    work_repo = tmp_path / "work" / "collab-books"
    work_repo.mkdir(parents=True)
    _git(work_repo, "init", "-b", "main")
    _git(work_repo, "remote", "add", "origin", str(bare_remote))
    (work_repo / "README.md").write_text("# Collab Books\n", encoding="utf-8")
    _git(work_repo, "add", "README.md")
    _git(
        work_repo,
        "-c",
        "user.name=Test Bot",
        "-c",
        "user.email=bot@example.com",
        "commit",
        "-m",
        "init",
    )
    _git(work_repo, "push", "-u", "origin", "main")
    return bare_remote, work_repo


def _make_cfg(tmp_path: Path, work_repo: Path) -> McpServerConfig:
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


def _xml_pair(orig: str, vi: str, *, seg_type: str = "paragraph") -> tuple[str, str]:
    src = f'<segment id="S-0001" type="{seg_type}">\n<original>\n{orig}\n</original>\n</segment>\n'
    tr = (
        f'<segment id="S-0001" type="{seg_type}">\n<original>\n{orig}\n</original>\n'
        f'<translate>\n{vi}\n</translate>\n</segment>\n'
    )
    return src, tr


def _auth(tier: str, handle: str, *, role: str = "contributor") -> AuthContext:
    actor = "admin:test" if tier == "ADMIN" else (f"agent:{handle}" if tier == "AGENT_KEY" else f"anon:{handle}")
    return AuthContext(tier=tier, actor_id=actor, handle=handle, client_ip_hash=f"ip-{handle}", agent_role=role)


def test_req_068_owner_permission_and_restricted_books_never_touch_git_or_publish_release(tmp_path: Path) -> None:
    """REQ-068: Non-public books isolate into private-books/, never commit/push/release to collab-books, and mark completed."""
    from scripts.mcp_server.git_sync import PUBLIC_CORPUS_LICENSES, is_public_corpus_book

    assert PUBLIC_CORPUS_LICENSES == frozenset({"public-domain", "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0"})
    assert is_public_corpus_book(license_id="public-domain", access_mode="public") is True
    assert is_public_corpus_book(license_id="owner-permission", access_mode="public") is False
    assert is_public_corpus_book(license_id="public-domain", access_mode="restricted") is False

    bare_remote, work_repo = _init_git_pair(tmp_path)
    cfg = _make_cfg(tmp_path, work_repo)
    store = CollabStore(cfg)
    store.init_db()
    sync = CollabBooksGitSync(cfg, publish_github_release=False)
    svc = CollabMcpService(cfg=cfg, store=store, review_client=_FakeReviewClient(), git_sync=sync)

    aa = store.register_agent(handle="coord-priv", model_info="opus")
    store.set_agent_role(handle=aa["handle"], role="agent_admin")
    peer = store.register_agent(handle="peer-priv", model_info="gemini")
    aa_ctx = _auth("AGENT_KEY", aa["handle"], role="agent_admin")
    peer_ctx = _auth("AGENT_KEY", peer["handle"], role="contributor")
    admin_ctx = _auth("ADMIN", "admin", role="admin")

    origin_head_before = _git(bare_remote, "rev-parse", "main")
    cover_src, cover_tr = _xml_pair("![Cover](images/cover.jpg)", "![Cover](images/cover.jpg)", seg_type="figure")
    body_src, body_tr = _xml_pair(
        "The cycle repeats across reserve currency empires.",
        "Chu kỳ lặp lại qua các đế chế nắm giữ đồng tiền dự trữ.",
    )

    # 1. Trusted proposal with owner-permission + auto-accepted boilerplate MUST NOT touch books_repo_dir or origin.git
    prop = svc.submit_book_proposal(
        aa_ctx,
        slug="dalio-private",
        title="Dalio Private Study",
        author="Ray Dalio",
        source_lang="en",
        genre="non-fiction",
        license_id="owner-permission",
        license_url="https://example.org/perm",
        source_filename="dalio.md",
        brief={"genre": "non-fiction", "style_rules": ["Initial analytical prose."]},
        book_record={"terms": {"reserve currency": "đồng tiền dự trữ"}},
        chapters=[
            {"chapter_id": "ch01", "title": "Cover", "source_xml": cover_src, "translation_xml": cover_tr},
            {"chapter_id": "ch02", "title": "Chapter 1: Cycles", "source_xml": body_src, "translation_xml": body_tr},
        ],
    )
    assert prop["public_corpus"] is False
    assert not (work_repo / "books" / "dalio-private").exists()
    assert not (work_repo / "runs" / "dalio-private").exists()
    priv_root = sync.private_books_dir
    assert (priv_root / "runs" / "dalio-private" / "brief.json").is_file()
    assert (priv_root / "runs" / "dalio-private" / "book_record.json").is_file()
    assert (priv_root / "runs" / "dalio-private" / "ch01" / "final.txt").is_file()
    assert (priv_root / "runs" / "dalio-private" / "collab_ledger.json").is_file()
    svc.update_book_record(
        aa_ctx,
        book_slug="dalio-private",
        brief={"genre": "non-fiction", "style_rules": ["Use empirical historical voice."]},
    )
    assert json.loads((priv_root / "runs" / "dalio-private" / "brief.json").read_text(encoding="utf-8"))["style_rules"] == ["Use empirical historical voice."]
    assert not (work_repo / "runs" / "dalio-private").exists()
    assert _git(bare_remote, "rev-parse", "main") == origin_head_before

    # Peer-review & accept ch02 -> book finishes as status="completed", release_url="", ZERO git push!
    tasks = svc.list_tasks(peer_ctx, role="reviewer", book_slug="dalio-private")
    assert len(tasks["peer_review_candidates"]) == 1
    sub_id = tasks["peer_review_candidates"][0]["submission_id"]
    rev_res = svc.submit_review(
        peer_ctx,
        submission_id=sub_id,
        verdict="APPROVE",
        body_md=(
            "S-0001 verified accurately.\n"
            "- Fidelity: 4.8/5\n- Fluency: 4.7/5\n- Terminology: 4.9/5\n"
            "- Register: 4.8/5\n- Structure: 5.0/5\n- Completeness: 5.0/5\n"
        ),
    )
    assert rev_res["submission"]["status"] == "accepted"

    done_book = svc.get_book(peer_ctx, book_slug="dalio-private")
    assert done_book["status"] == "completed"
    assert done_book["public_corpus"] is False
    assert done_book["release_tag"] == ""
    assert done_book["release_url"] == ""
    assert set(done_book["collab_ledger"]["chapters"].keys()) == {"ch01", "ch02"}
    assert not (work_repo / "books" / "dalio-private").exists()
    assert not (work_repo / "runs" / "dalio-private").exists()
    assert _git(bare_remote, "rev-parse", "main") == origin_head_before
    assert "dalio-private" not in _git(bare_remote, "ls-tree", "-r", "--name-only", "main")

    # Boundary: publish_release, reject_book_proposal, and import status preservation
    with pytest.raises(ServiceError) as pub_exc:
        svc.publish_release(admin_ctx, book_slug="dalio-private")
    assert pub_exc.value.status_code == 409

    with pytest.raises(ServiceError) as rej_exc:
        svc.reject_book_proposal(admin_ctx, book_slug="dalio-private", reason="completed")
    assert rej_exc.value.status_code == 409

    # Flipping a public-domain book to restricted evicts untracked files & blocks publish_release
    svc.submit_book_proposal(
        aa_ctx,
        slug="vip-public-license",
        title="VIP Public License",
        author="O. Writer",
        source_lang="en",
        license_id="public-domain",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        source_filename="vip.md",
        book_record={"terms": {"sky": "bầu trời"}},
        chapters=[
            {"chapter_id": "ch01", "title": "Ch 1", "source_xml": body_src},
            {"chapter_id": "ch02", "title": "Ch 2", "source_xml": body_src, "translation_xml": body_tr},
        ],
    )
    assert (work_repo / "books" / "vip-public-license").is_dir()
    svc.set_book_access(admin_ctx, book_slug="vip-public-license", access_mode="restricted", allowed_agents=[aa["handle"], peer["handle"]])
    assert not (work_repo / "books" / "vip-public-license").exists()
    assert (priv_root / "books" / "vip-public-license" / "manifest.json").is_file()
    vip_sub = svc.list_tasks(peer_ctx, role="reviewer", book_slug="vip-public-license")["peer_review_candidates"][0]["submission_id"]
    svc.submit_review(
        peer_ctx,
        submission_id=vip_sub,
        verdict="APPROVE",
        body_md=(
            "S-0001 verified.\n- Fidelity: 4.8/5\n- Fluency: 4.8/5\n- Terminology: 4.8/5\n"
            "- Register: 4.8/5\n- Structure: 5.0/5\n- Completeness: 5.0/5\n"
        ),
    )
    svc.import_chapter_translations(
        aa_ctx,
        book_slug="vip-public-license",
        chapters=[{"chapter_id": "ch01", "translation_xml": body_tr}],
    )
    vip_sub1 = svc.list_tasks(peer_ctx, role="reviewer", book_slug="vip-public-license")["peer_review_candidates"][0]["submission_id"]
    svc.submit_review(
        peer_ctx,
        submission_id=vip_sub1,
        verdict="APPROVE",
        body_md=(
            "S-0001 verified.\n- Fidelity: 4.8/5\n- Fluency: 4.8/5\n- Terminology: 4.8/5\n"
            "- Register: 4.8/5\n- Structure: 5.0/5\n- Completeness: 5.0/5\n"
        ),
    )
    assert svc.get_book(peer_ctx, book_slug="vip-public-license")["status"] == "completed"
    with pytest.raises(ServiceError) as vip_imp_exc:
        svc.import_chapter_translations(
            aa_ctx,
            book_slug="vip-public-license",
            chapters=[{"chapter_id": "ch01", "translation_xml": body_tr}],
        )
    assert vip_imp_exc.value.status_code == 409
    assert svc.get_book(peer_ctx, book_slug="vip-public-license")["status"] == "completed"
    with pytest.raises(ServiceError) as vip_pub_exc:
        svc.publish_release(admin_ctx, book_slug="vip-public-license")
    assert vip_pub_exc.value.status_code == 409

    # Positive control: public-domain + access_mode="public" STILL commits, pushes, and publishes!
    svc.submit_book_proposal(
        aa_ctx,
        slug="open-story",
        title="Open Story",
        author="Public Author",
        source_lang="en",
        license_id="public-domain",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        source_filename="open.md",
        book_record={"terms": {"empires": "đế chế"}},
        chapters=[{"chapter_id": "ch01", "title": "Only Chapter", "source_xml": body_src, "translation_xml": body_tr}],
    )
    open_sub = svc.list_tasks(peer_ctx, role="reviewer", book_slug="open-story")["peer_review_candidates"][0]["submission_id"]
    svc.submit_review(
        peer_ctx,
        submission_id=open_sub,
        verdict="APPROVE",
        body_md=(
            "S-0001 looks great.\n- Fidelity: 4.9/5\n- Fluency: 4.8/5\n- Terminology: 4.9/5\n"
            "- Register: 4.8/5\n- Structure: 5.0/5\n- Completeness: 5.0/5\n"
        ),
    )
    pub_book = svc.get_book(peer_ctx, book_slug="open-story")
    assert pub_book["status"] == "published"
    assert pub_book["public_corpus"] is True
    assert pub_book["release_tag"] == "open-story-v1.0"
    assert _git(bare_remote, "rev-parse", "main") != origin_head_before
    committed_files = _git(bare_remote, "ls-tree", "-r", "--name-only", "main")
    assert "runs/open-story/ch01/final.txt" in committed_files
    assert "dalio-private" not in committed_files
    assert "vip-public-license" not in committed_files
    store.close()


def test_req_069_public_bookshelf_filter_startup_origin_reconcile_and_v431(tmp_path: Path) -> None:
    """REQ-069: Startup aligns origin/main & evicts non-public dirs, '/' hides owner-permission, /admin & MCP v4.3.1."""
    bare_remote, work_repo = _init_git_pair(tmp_path)
    cfg = _make_cfg(tmp_path, work_repo)

    # Commit gift-of-the-magi (public-domain) cleanly on origin/main
    src_xml, tr_xml = _xml_pair("One dollar and eighty-seven cents.", "Một đô la tám mươi bảy xu.")
    (work_repo / "books" / "gift-of-the-magi" / "source").mkdir(parents=True)
    (work_repo / "books" / "gift-of-the-magi" / "manifest.json").write_text(
        json.dumps(
            {
                "slug": "gift-of-the-magi",
                "title": "The Gift of the Magi",
                "author": "O. Henry",
                "source_lang": "en",
                "target_lang": "vi",
                "license": "public-domain",
            }
        ),
        encoding="utf-8",
    )
    (work_repo / "runs" / "gift-of-the-magi" / "ch01").mkdir(parents=True)
    (work_repo / "runs" / "gift-of-the-magi" / "checklist.json").write_text(
        json.dumps({"book_slug": "gift-of-the-magi", "glossary_sha": "a" * 64}),
        encoding="utf-8",
    )
    (work_repo / "runs" / "gift-of-the-magi" / "ch01" / "source.txt").write_text(src_xml, encoding="utf-8")
    (work_repo / "runs" / "gift-of-the-magi" / "ch01" / "final.txt").write_text("Một đô la tám mươi bảy xu.\n", encoding="utf-8")
    (work_repo / "runs" / "gift-of-the-magi" / "ch02").mkdir(parents=True)
    (work_repo / "runs" / "gift-of-the-magi" / "ch02" / "source.txt").write_text(src_xml, encoding="utf-8")
    _git(work_repo, "add", "books/gift-of-the-magi", "runs/gift-of-the-magi")
    _git(work_repo, "-c", "user.name=Bot", "-c", "user.email=bot@e.com", "commit", "-m", "feat(gift-of-the-magi): accept ch01")
    _git(work_repo, "push", "origin", "main")
    public_head = _git(bare_remote, "rev-parse", "main")

    # Simulate dirty pod checkout BEFORE reset: local main has an extra commit for the-changing-world-order
    # plus untracked leftover owner-permission book_record & manifest!
    (work_repo / "books" / "the-changing-world-order" / "source").mkdir(parents=True)
    (work_repo / "books" / "the-changing-world-order" / "manifest.json").write_text(
        json.dumps({"slug": "the-changing-world-order", "title": "The Changing World Order", "author": "Ray Dalio", "license": "owner-permission"}),
        encoding="utf-8",
    )
    (work_repo / "runs" / "the-changing-world-order" / "ch01").mkdir(parents=True)
    (work_repo / "runs" / "the-changing-world-order" / "collab_ledger.json").write_text(
        json.dumps({"book_slug": "the-changing-world-order", "chapters": {"ch01": {"verdict": "PASS"}}}),
        encoding="utf-8",
    )
    _git(work_repo, "add", "books/the-changing-world-order", "runs/the-changing-world-order")
    _git(work_repo, "-c", "user.name=Bot", "-c", "user.email=bot@e.com", "commit", "-m", "feat(the-changing-world-order): accept ch01")

    (work_repo / "books" / "leftover-private" / "source").mkdir(parents=True)
    (work_repo / "books" / "leftover-private" / "manifest.json").write_text(
        json.dumps({"slug": "leftover-private", "title": "Leftover Private", "author": "Ray Dalio", "license": "owner-permission"}),
        encoding="utf-8",
    )

    # Pre-seed DB with the-changing-world-order (owner-permission) as live DB would have
    seed_store = CollabStore(cfg)
    seed_store.init_db()
    seed_store.upsert_book(
        slug="the-changing-world-order",
        title="The Changing World Order",
        author="Ray Dalio",
        source_lang="en",
        target_lang="vi",
        genre="non-fiction",
        license_id="owner-permission",
        license_url="https://example.org/perm",
        status="completed",
        glossary_sha="abc",
        book_record={},
        manifest={"slug": "the-changing-world-order", "license": "owner-permission"},
        proposed_by="antigravity-3f92",
    )
    seed_store.close()

    # Boot create_app -> aligns work_repo to origin/main, migrates ledger to private-books/, skips leftover-private manifest!
    with patch("scripts.mcp_server.app.ReviewServerClient", lambda _cfg: _FakeReviewClient()):
        app = create_app(cfg=cfg)
    sso_hdr = {"x-auth-request-email": "admin@vinhthang.dev"}
    admin_hdr = {"Authorization": f"Bearer {cfg.collab_admin_token}"}
    with TestClient(app) as client:
        assert _git(work_repo, "rev-parse", "main") == public_head
        assert not (work_repo / "runs" / "the-changing-world-order").exists()
        assert (cfg.books_repo_dir.parent / "private-books" / "runs" / "the-changing-world-order" / "collab_ledger.json").is_file()

        # 1. Health, /llms.txt, plugin.json, MCP initialize report "4.3.1"
        assert client.get("/api/health").json()["version"] == "4.3.1"
        assert "v4.3.1" in client.get("/llms.txt").text
        repo_root = Path(__file__).resolve().parent.parent
        assert json.loads((repo_root / "plugin.json").read_text(encoding="utf-8"))["version"] == "4.3.1"

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
        assert init_rpc["result"]["serverInfo"]["version"] == "4.3.1"

        # 2. Public HTML bookshelf '/' hides owner-permission book & unbootstrapped leftover-private, shows gift-of-the-magi
        shelf_html = client.get("/").text
        assert "gift-of-the-magi" in shelf_html
        assert "the-changing-world-order" not in shelf_html
        assert "leftover-private" not in shelf_html

        # 3. MCP/REST list_books still returns the-changing-world-order (with public_corpus=False) for peer review
        api_books = {b["slug"]: b for b in client.get("/api/v1/books").json()["books"]}
        assert "leftover-private" not in api_books
        assert api_books["gift-of-the-magi"]["public_corpus"] is True
        assert api_books["the-changing-world-order"]["public_corpus"] is False
        assert api_books["the-changing-world-order"]["status"] == "completed"

        # 4. /admin shows the-changing-world-order, no Compile & Publish Release button for it, and passes node --check
        admin_html = client.get("/admin", headers=sso_hdr).text
        assert "the-changing-world-order" in admin_html
        assert "private &middot; no GitHub sync" in admin_html
        assert 'class="btn btn-sec js-release-book" data-slug="the-changing-world-order"' not in admin_html
        script_m = re.search(r"<script>([\s\S]*?)</script>\s*</body>", admin_html)
        assert script_m is not None
        js_file = tmp_path / "admin_check.js"
        js_file.write_text(script_m.group(1), encoding="utf-8")
        assert subprocess.run(["node", "--check", str(js_file)], capture_output=True, text=True).returncode == 0

        # 5. Verify admin REST publish_release rejects owner-permission book cleanly with 409
        rel_409 = client.post("/api/v1/admin/books/the-changing-world-order/release", headers=admin_hdr)
        assert rel_409.status_code == 409

        # 6. Subsequent acceptance of gift-of-the-magi/ch02 pushes ONLY gift-of-the-magi (never re-introducing Dalio!)
        ch02_tr = (
            '<segment id="S-0001" type="paragraph">\n<original>\nOne dollar and eighty-seven cents.\n</original>\n'
            '<translate>\nMột đô la tám mươi bảy xu (chương 2).\n</translate>\n</segment>\n'
        )
        t_reg = client.post("/api/v1/agents/register", json={"handle": "tr-gift"}).json()
        client.post(
            f"/api/v1/admin/books/gift-of-the-magi/approval-mode",
            headers=admin_hdr,
            json={"approval_mode": "auto"},
        )
        sub2 = client.post(
            "/api/v1/submissions",
            headers={"Authorization": f"Bearer {t_reg['agent_key']}"},
            json={
                "book_slug": "gift-of-the-magi",
                "chapter_id": "ch02",
                "translation_xml": ch02_tr,
            },
        )
        assert sub2.status_code == 200
        assert _git(bare_remote, "rev-parse", "main") != public_head
        remote_files = _git(bare_remote, "ls-tree", "-r", "--name-only", "main")
        assert "runs/gift-of-the-magi/ch02/final.txt" in remote_files
        assert "the-changing-world-order" not in remote_files
        assert "leftover-private" not in remote_files

        # 7. Offline tolerance check: removing origin remote never crashes startup eviction
        _git(work_repo, "remote", "remove", "origin")
        sync_offline = CollabBooksGitSync(cfg, publish_github_release=False)
        sync_offline.reconcile_origin_and_evict_private(non_public_slugs={"the-changing-world-order"})
