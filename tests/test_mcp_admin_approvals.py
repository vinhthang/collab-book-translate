"""TDD tests for REQ-047, REQ-048, REQ-049: /admin Approvals UI, Book Reject/Open Lifecycle, & Manual Chapter Approval."""

from __future__ import annotations

import json
import time
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from scripts.mcp_server.app import create_app
from tests.test_mcp_service_and_app import _build_env


def test_ac_047_book_intake_open_for_agents_and_reject_lifecycle(tmp_path: Path):
    cfg, store, rs_client, svc = _build_env(tmp_path)
    app = create_app(cfg=cfg, service=svc)
    admin_hdr = {"authorization": f"Bearer {cfg.collab_admin_token}"}
    sso_hdr = {"x-auth-request-email": "admin@vinhthang.dev"}

    md_book = (
        "# Chapter 1: Beginning\n\nOnce upon a midnight dreary.\n\n"
        "# Chapter 2: Ending\n\nQuoth the raven nevermore.\n"
    )

    with TestClient(app) as client:
        # 1. Propose book #1 (gift-of-the-magi style)
        p1 = client.post(
            "/api/v1/books/propose",
            json={
                "slug": "raven-tales",
                "title": "Raven Tales",
                "author": "E. A. Poe",
                "source_lang": "en",
                "target_lang": "vi",
                "license": "public-domain",
                "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                "source_filename": "raven.md",
                "source_text": md_book,
            },
        )
        assert p1.status_code == 201
        assert p1.json()["book"]["status"] == "pending_approval"

        # Non-admin cannot approve or reject (AC-047-2)
        assert client.post("/admin/books/raven-tales/approve", json={"auto_seed": False}).status_code == 403
        assert client.post("/admin/books/raven-tales/reject", json={"reason": "no"}).status_code == 403

        # 2. Approve with auto_seed=False -> immediately open for translators (status="active", ch01 open, ch02 locked_seed)
        app_open = client.post(
            "/admin/books/raven-tales/approve",
            headers=sso_hdr,
            json={"auto_seed": False},
        )
        assert app_open.status_code == 200
        book_data = app_open.json()["book"]
        assert book_data["status"] == "active"
        chapters = {c["chapter_id"]: c for c in book_data["chapters"]}
        assert chapters["ch01"]["status"] == "open"
        assert chapters["ch02"]["status"] == "locked_seed"

        tasks = client.get("/api/v1/tasks", params={"role": "translator"}).json()
        open_pairs = {(c["book_slug"], c["chapter_id"]) for c in tasks["open_chapters"]}
        assert ("raven-tales", "ch01") in open_pairs
        assert ("raven-tales", "ch02") not in open_pairs

        # Rejecting an active book returns 409 (AC-047-2)
        assert client.post("/admin/books/raven-tales/reject", headers=admin_hdr, json={}).status_code == 409

        # 3. Propose book #2, fail seed once, then retry approve(auto_seed=False) idempotently (AC-047-1)
        p2 = client.post(
            "/api/v1/books/propose",
            json={
                "slug": "retry-book",
                "title": "Retry Book",
                "author": "Writer",
                "source_lang": "en",
                "target_lang": "vi",
                "license": "public-domain",
                "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                "source_filename": "retry.md",
                "source_text": md_book,
            },
        )
        assert p2.status_code == 201
        rs_client.next_translate_result = RuntimeError("transient error")
        assert client.post("/admin/books/retry-book/approve", headers=admin_hdr, json={"auto_seed": True}).status_code == 422
        assert store.get_book("retry-book")["status"] == "seed_failed"

        retry_open = client.post("/admin/books/retry-book/approve", headers=admin_hdr, json={"auto_seed": False})
        assert retry_open.status_code == 200
        assert retry_open.json()["book"]["status"] == "active"
        assert len(store.list_chapters("retry-book")) == 2

        # 4. Propose book #3 and reject it -> status="rejected", hidden from public list_books()
        p3 = client.post(
            "/api/v1/books/propose",
            json={
                "slug": "spam-proposal",
                "title": "Spam Book",
                "author": "Spammer",
                "source_lang": "en",
                "target_lang": "vi",
                "license": "public-domain",
                "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                "source_filename": "spam.md",
                "source_text": md_book,
            },
        )
        assert p3.status_code == 201
        rej = client.post(
            "/admin/books/spam-proposal/reject",
            headers=sso_hdr,
            json={"reason": "Not suitable for corpus"},
        )
        assert rej.status_code == 200
        assert rej.json()["book"]["status"] == "rejected"
        assert rej.json()["book"]["rejection_reason"] == "Not suitable for corpus"

        pub_slugs = {b["slug"] for b in client.get("/api/v1/books").json()["books"]}
        assert "spam-proposal" not in pub_slugs

        # 5. Re-approving or rejecting a published book returns 409 (AC-047-2)
        store.set_book_status(slug="raven-tales", status="published")
        assert client.post("/admin/books/raven-tales/approve", headers=admin_hdr, json={}).status_code == 409
        assert client.post("/admin/books/raven-tales/reject", headers=admin_hdr, json={}).status_code == 409

    store.close()


def test_ac_048_manual_chapter_approval_gate_and_override_semantics(tmp_path: Path):
    cfg, store, rs_client, svc = _build_env(tmp_path)
    src_xml = '<segment id="S-0001" type="paragraph">\n<original>\nThe clock struck twelve.\n</original>\n</segment>\n'
    tr_xml = (
        '<segment id="S-0001" type="paragraph">\n<original>\nThe clock struck twelve.\n</original>\n'
        '<translate>\nChiếc đồng hồ điểm đúng mười hai giờ đêm.\n</translate>\n</segment>\n'
    )
    store.upsert_book(
        slug="manual-book",
        title="Manual Book",
        author="O. Henry",
        source_lang="en",
        target_lang="vi",
        genre="fiction",
        license_id="public-domain",
        license_url="https://example.org",
        status="active",
        glossary_sha="b" * 64,
        book_record={},
        manifest={"slug": "manual-book", "source_lang": "en", "target_lang": "vi"},
        proposed_by="admin",
    )
    store.upsert_chapter(
        book_slug="manual-book",
        chapter_id="ch01",
        chapter_num=1,
        title="Chapter 1",
        source_xml=src_xml,
        status="open",
        opened_at=time.time() - 72 * 3600,
    )
    store.upsert_chapter(
        book_slug="manual-book",
        chapter_id="ch02",
        chapter_num=2,
        title="Chapter 2",
        source_xml=src_xml,
        status="open",
        opened_at=time.time() - 72 * 3600,
    )

    app = create_app(cfg=cfg, service=svc)
    admin_hdr = {"authorization": f"Bearer {cfg.collab_admin_token}"}
    sso_hdr = {"x-auth-request-email": "admin@vinhthang.dev"}

    with TestClient(app) as client:
        # Default approval_mode is "auto"
        assert client.get("/api/v1/books/manual-book").json()["approval_mode"] == "auto"

        # Non-admin & invalid approval_mode boundaries (AC-048-2)
        assert client.post("/admin/api/books/manual-book/approval-mode", json={"approval_mode": "manual"}).status_code == 403
        assert client.post("/admin/api/books/manual-book/approval-mode", headers=sso_hdr, json={"approval_mode": "invalid"}).status_code == 400

        # Toggle book to manual chapter approval
        tog = client.post(
            "/admin/api/books/manual-book/approval-mode",
            headers=sso_hdr,
            json={"approval_mode": "manual"},
        )
        assert tog.status_code == 200
        assert tog.json()["book"]["approval_mode"] == "manual"

        # 1. Agent submits passing ch02 -> held in pending_admin_approval, NO git commit, NO approve_workstream yet!
        agent = store.register_agent(handle="agent-alice", model_info="test")
        sub_res = client.post(
            "/api/v1/submissions",
            json={
                "agent_key": agent["agent_key"],
                "book_slug": "manual-book",
                "chapter_id": "ch02",
                "translation_xml": tr_xml,
                "engine_model": "sonnet",
            },
        )
        assert sub_res.status_code == 200
        sub1 = sub_res.json()["submission"]
        assert sub1["status"] == "pending_admin_approval"
        assert store.get_chapter("manual-book", "ch02")["status"] == "open"
        assert not (cfg.books_repo_dir / "runs" / "manual-book" / "ch02" / "final.txt").exists()
        assert not any(c[0] == "approve_workstream" for c in rs_client.calls)

        # Hidden from translator list_tasks, visible in reviewer list_tasks, skipped by starved fallback (AC-048-1)
        tr_tasks = client.get("/api/v1/tasks", params={"role": "translator"}).json()
        assert ("manual-book", "ch02") not in {(c["book_slug"], c["chapter_id"]) for c in tr_tasks["open_chapters"]}

        rv_tasks = client.get("/api/v1/tasks", params={"role": "reviewer"}).json()
        assert sub1["id"] in {s["id"] for s in rv_tasks["peer_review_candidates"]}

        rs_client.next_translate_result = {
            "translationXml": tr_xml,
            "additions": {"proposals": []},
            "engineUsed": "gemini",
        }
        fb = client.post("/admin/fallback/run", headers=admin_hdr, json={"book_slug": "manual-book"})
        assert fb.status_code == 200
        # ch01 may be translated by fallback (admin bypass), but ch02 MUST be skipped!
        assert "manual-book/ch02" not in fb.json()["translated_chapters"]
        assert store.get_chapter("manual-book", "ch01")["status"] == "accepted"

        # Duplicate non-admin submit on ch02 returns 409 (AC-048-2)
        dup = client.post(
            "/api/v1/submissions",
            json={
                "agent_key": agent["agent_key"],
                "book_slug": "manual-book",
                "chapter_id": "ch02",
                "translation_xml": tr_xml,
            },
        )
        assert dup.status_code == 409

        # Attempting revision of pending_admin_approval by agent returns 409
        rev_dup = client.post(
            "/api/v1/submissions",
            json={
                "agent_key": agent["agent_key"],
                "book_slug": "manual-book",
                "chapter_id": "ch02",
                "submission_id": sub1["id"],
                "translation_xml": tr_xml,
            },
        )
        assert rev_dup.status_code == 409

        # 2. Admin rejects submission -> status="rejected", ch02 immediately reappears for translators!
        rej_sub = client.post(
            f"/admin/submissions/{sub1['id']}/manage",
            headers=sso_hdr,
            json={"action": "reject"},
        )
        assert rej_sub.status_code == 200
        assert rej_sub.json()["submission"]["status"] == "rejected"
        tr_tasks_after_rej = client.get("/api/v1/tasks", params={"role": "translator"}).json()
        assert ("manual-book", "ch02") in {(c["book_slug"], c["chapter_id"]) for c in tr_tasks_after_rej["open_chapters"]}

        # 3. Agent re-submits -> enters pending_admin_approval; Admin approves with action="approve"
        sub_res2 = client.post(
            "/api/v1/submissions",
            json={
                "agent_key": agent["agent_key"],
                "book_slug": "manual-book",
                "chapter_id": "ch02",
                "translation_xml": tr_xml,
            },
        )
        assert sub_res2.status_code == 200
        sub2 = sub_res2.json()["submission"]
        assert sub2["status"] == "pending_admin_approval"

        rs_client.calls.clear()
        app_sub = client.post(
            f"/admin/submissions/{sub2['id']}/manage",
            headers=sso_hdr,
            json={"action": "approve"},
        )
        assert app_sub.status_code == 200
        assert app_sub.json()["submission"]["status"] == "accepted"
        assert store.get_chapter("manual-book", "ch02")["status"] == "accepted"
        assert (cfg.books_repo_dir / "runs" / "manual-book" / "ch02" / "final.txt").is_file()

        approve_calls = [c[1] for c in rs_client.calls if c[0] == "approve_workstream"]
        assert approve_calls == [{"workstream_id": sub2["workstream_id"], "override_escalated": False}]

        # Re-approving an already-accepted submission returns 409 (AC-048-2)
        assert client.post(
            f"/admin/submissions/{sub2['id']}/manage",
            headers=sso_hdr,
            json={"action": "approve"},
        ).status_code == 409

        # Invalid action -> 400
        assert client.post(
            f"/admin/submissions/{sub2['id']}/manage",
            headers=sso_hdr,
            json={"action": "bogus"},
        ).status_code == 400

        # 4. Escalated/flagged submission override passes override_escalated=True
        esc_book = store.upsert_book(
            slug="esc-book",
            title="Esc Book",
            author="A",
            source_lang="en",
            target_lang="vi",
            genre="fiction",
            license_id="public-domain",
            license_url="https://example.org",
            status="active",
            glossary_sha="c" * 64,
            book_record={},
            manifest={"slug": "esc-book", "source_lang": "en", "target_lang": "vi"},
            proposed_by="admin",
        )
        store.upsert_chapter(
            book_slug=esc_book["slug"],
            chapter_id="ch01",
            chapter_num=1,
            title="Ch 1",
            source_xml=src_xml,
            status="open",
        )
        esc_sub = store.create_submission(
            book_slug="esc-book",
            chapter_id="ch01",
            author_actor_id="agent:agent-alice",
            author_handle="agent-alice",
            engine_model="sonnet",
            status="escalated_to_admin",
            round_number=3,
            max_rounds=3,
            workstream_id="ws-esc-99",
            revision_token_sha256="x" * 64,
            translation_xml=tr_xml,
            final_txt="",
            additions={"proposals": []},
            open_findings=[],
            security_flags=[],
            judge_summary="Max rounds exhausted.",
        )
        rs_client.calls.clear()
        ovr = client.post(
            f"/admin/submissions/{esc_sub['id']}/manage",
            headers=sso_hdr,
            json={"action": "approve_override"},
        )
        assert ovr.status_code == 200
        assert ovr.json()["submission"]["status"] == "accepted"
        assert [c[1] for c in rs_client.calls if c[0] == "approve_workstream"] == [
            {"workstream_id": "ws-esc-99", "override_escalated": True}
        ]

    store.close()


def test_ac_049_admin_approvals_ui_bookshelf_links_and_xss_safety(tmp_path: Path):
    cfg, store, rs_client, svc = _build_env(tmp_path)
    reg = store.register_agent(handle="helper-bot", display_name="Helper", model_info="claude")
    raw_agent_key = reg["agent_key"]

    src_xml = '<segment id="S-0001" type="paragraph">\n<original>\nHello world.\n</original>\n</segment>\n'
    tr_xml = (
        '<segment id="S-0001" type="paragraph">\n<original>\nHello world.\n</original>\n'
        '<translate>\nXin chào thế giới.\n</translate>\n</segment>\n'
    )

    store.upsert_book(
        slug="xss-pending",
        title='<script>alert(1)</script>Gift',
        author='O. Henry',
        source_lang="en",
        target_lang="vi",
        genre="fiction",
        license_id="public-domain",
        license_url="javascript:alert(1)",
        status="pending_approval",
        glossary_sha="",
        book_record={},
        manifest={"slug": "xss-pending"},
        proposed_by="proposer",
        source_text="# Chapter 1\n<script>alert('src')</script>Body",
        source_filename="gift.md",
    )
    store.upsert_book(
        slug="live-manual",
        title="Live Manual Book",
        author="Arthur",
        source_lang="en",
        target_lang="vi",
        genre="fiction",
        license_id="public-domain",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        status="active",
        glossary_sha="d" * 64,
        book_record={},
        manifest={"slug": "live-manual"},
        proposed_by="admin",
    )
    store.set_book_approval_mode(slug="live-manual", approval_mode="manual")
    store.upsert_chapter(
        book_slug="live-manual",
        chapter_id="ch02",
        chapter_num=2,
        title="Chapter 2",
        source_xml=src_xml,
        status="open",
    )
    store.create_submission(
        book_slug="live-manual",
        chapter_id="ch01",
        author_actor_id="agent:helper-bot",
        author_handle="helper-bot",
        engine_model="claude",
        status="accepted",
        round_number=1,
        max_rounds=3,
        workstream_id="ws-0",
        revision_token_sha256="y" * 64,
        translation_xml=tr_xml,
        final_txt="Chấp nhận.",
        additions={"proposals": []},
        open_findings=[],
        security_flags=[],
        judge_summary="Already accepted.",
    )
    flagged_sub = store.create_submission(
        book_slug="live-manual",
        chapter_id="ch02",
        author_actor_id="agent:helper-bot",
        author_handle="helper-bot",
        engine_model="claude",
        status="flagged_security",
        round_number=0,
        max_rounds=3,
        workstream_id="",
        revision_token_sha256="w" * 64,
        translation_xml=tr_xml,
        final_txt="",
        additions={"proposals": []},
        open_findings=[],
        security_flags=["override_prompt"],
        judge_summary="Quarantined.",
    )
    sub = store.create_submission(
        book_slug="live-manual",
        chapter_id="ch02",
        author_actor_id="agent:helper-bot",
        author_handle="helper-bot",
        engine_model="claude",
        status="pending_admin_approval",
        round_number=1,
        max_rounds=3,
        workstream_id="ws-1",
        revision_token_sha256="z" * 64,
        translation_xml=tr_xml,
        final_txt="Xin chào <script>alert(2)</script> thế giới.",
        additions={"proposals": []},
        open_findings=[{"severity": "P2", "symptom": "Minor tone check"}],
        security_flags=[],
        judge_summary="Summary <script>alert(3)</script> clean.",
    )
    store.add_peer_review(
        submission_id=sub["id"],
        reviewer_actor_id="agent:other",
        reviewer_handle="other-reviewer",
        scores={"Fidelity": 4.8, "Fluency": 4.7, "Terminology": 4.9, "Register": 4.8, "Structure": 5.0, "Completeness": 5.0},
        weighted_score=4.85,
        issues=[],
        body_md="S-0001 reads well.",
    )

    app = create_app(cfg=cfg, service=svc)
    sso_hdr = {"x-auth-request-email": "admin@vinhthang.dev"}

    with TestClient(app) as client:
        # 1. Homepage GET /
        home = client.get("/")
        assert home.status_code == 200
        home_html = home.text
        assert "Admin Approvals &amp; Access (SSO)" in home_html
        assert "1 pending" in home_html
        assert "Review in /admin" in home_html
        assert "manual approval" in home_html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;Gift" in home_html
        assert "<script>alert(1)</script>" not in home_html
        assert raw_agent_key not in home_html
        assert cfg.collab_admin_token not in home_html
        assert cfg.review_bearer_token not in home_html

        # 2. Non-admin Auth boundaries (AC-049-2)
        assert client.get("/admin").status_code == 403
        assert client.get("/admin/api/submissions").status_code == 403
        assert client.get("/api/v1/admin/submissions").status_code == 403

        # 3. Authenticated ADMIN GET /admin/api/submissions (priority-sorted + peer_reviews)
        sub_list = client.get("/admin/api/submissions", headers=sso_hdr)
        assert sub_list.status_code == 200
        items = sub_list.json()["submissions"]
        assert [i["status"] for i in items] == [
            "pending_admin_approval",
            "flagged_security",
            "accepted",
        ]
        assert items[0]["id"] == sub["id"]
        assert items[1]["id"] == flagged_sub["id"]
        assert len(items[0]["peer_reviews"]) == 1

        # 4. Authenticated ADMIN GET /admin HTML UI
        adm = client.get("/admin", headers=sso_hdr)
        assert adm.status_code == 200
        adm_html = adm.text
        for expected in (
            "Book Proposals &amp; Seeding Queue",
            "Chapter Translation Submissions Queue",
            "Approve &amp; Seed to Review",
            "Approve &amp; Seed (Auto-Merge)",
            "Approve &amp; Open ch01",
            "Reject Proposal",
            "Approve &amp; Merge",
            "Run Starved Fallback",
            "manual Admin approval",
            "approval_mode",
            "Save Settings",
        ):
            assert expected in adm_html
        # Escaped XSS checks and unsafe href sanitization
        assert "<script>alert(1)</script>" not in adm_html
        assert "<script>alert('src')</script>" not in adm_html
        assert "<script>alert(2)</script>" not in adm_html
        assert "<script>alert(3)</script>" not in adm_html
        assert 'href="javascript:' not in adm_html
        assert raw_agent_key not in adm_html
        assert cfg.collab_admin_token not in adm_html
        assert cfg.review_bearer_token not in adm_html

        # 5. Combined POST /admin/api/books/{slug}/access sets both access_mode and approval_mode
        save = client.post(
            "/admin/api/books/live-manual/access",
            headers=sso_hdr,
            json={
                "access_mode": "restricted",
                "allowed_agents": [raw_agent_key],
                "approval_mode": "auto",
            },
        )
        assert save.status_code == 200
        assert save.json()["book"]["access_mode"] == "restricted"
        assert save.json()["book"]["allowed_agents"] == ["helper-bot"]
        assert save.json()["book"]["approval_mode"] == "auto"

    store.close()


def test_db_submission_claim_and_bootstrap_boundaries(tmp_path: Path):
    cfg, store, _, _ = _build_env(tmp_path)
    src_xml = '<segment id="S-0001" type="paragraph">\n<original>\nLine.\n</original>\n</segment>\n'
    store.upsert_book(
        slug="db-edge",
        title="DB Edge",
        author="Author",
        source_lang="en",
        target_lang="vi",
        genre="fiction",
        license_id="public-domain",
        license_url="https://example.org",
        status="active",
        glossary_sha="",
        book_record={},
        manifest={"slug": "db-edge"},
        proposed_by="admin",
    )
    store.upsert_chapter(
        book_slug="db-edge",
        chapter_id="ch01",
        chapter_num=1,
        title="Chapter 1",
        source_xml=src_xml,
        status="open",
    )

    # claim_chapter: now=None, same actor re-claim, and exact now == expires_at boundary
    exp = time.time() + 120.0
    store.claim_chapter(
        book_slug="db-edge",
        chapter_id="ch01",
        actor_id="agent:a1",
        claim_token_sha256="t1",
        expires_at=exp,
    )
    store.claim_chapter(
        book_slug="db-edge",
        chapter_id="ch01",
        actor_id="agent:a1",
        claim_token_sha256="t1b",
        expires_at=exp,
    )
    with pytest.raises(PermissionError):
        store.claim_chapter(
            book_slug="db-edge",
            chapter_id="ch01",
            actor_id="agent:a2",
            claim_token_sha256="t2",
            expires_at=exp + 60.0,
        )
    # Exact expiry equality (now == exp) frees the claim
    reclaimed = store.claim_chapter(
        book_slug="db-edge",
        chapter_id="ch01",
        actor_id="agent:a2",
        claim_token_sha256="t2",
        expires_at=exp + 60.0,
        now=exp,
    )
    assert reclaimed["claimed_by"] == "agent:a2"

    # update_submission: preserves optional JSON fields when omitted, updates when supplied
    sub = store.create_submission(
        book_slug="db-edge",
        chapter_id="ch01",
        author_actor_id="agent:a2",
        author_handle="a2",
        engine_model="test",
        status="pending_admin_approval",
        round_number=1,
        max_rounds=3,
        workstream_id="ws-db",
        revision_token_sha256="r" * 64,
        translation_xml="<segment id='S-0001'/>",
        final_txt="Dòng.",
        additions={"proposals": [{"source": "Line", "target": "Dòng"}]},
        open_findings=[{"findingKey": "fk-1"}],
        security_flags=["flag-a"],
        judge_summary="initial",
    )
    kept = store.update_submission(sub["id"], status="accepted")
    assert kept["additions"] == {"proposals": [{"source": "Line", "target": "Dòng"}]}
    assert kept["open_findings"] == [{"findingKey": "fk-1"}]
    assert kept["security_flags"] == ["flag-a"]
    assert kept["judge_summary"] == "initial"

    cleared = store.update_submission(
        sub["id"],
        additions={"proposals": []},
        open_findings=[],
        security_flags=[],
        judge_summary="done",
    )
    assert cleared["additions"] == {"proposals": []}
    assert cleared["open_findings"] == []
    assert cleared["security_flags"] == []
    assert cleared["judge_summary"] == "done"

    # bootstrap_from_books_dir skips already-present book without overwriting its DB status
    bdir = tmp_path / "seed_repo" / "books" / "db-edge"
    bdir.mkdir(parents=True)
    (bdir / "manifest.json").write_text(
        json.dumps({"title": "Overwrite Attempt", "source_lang": "en"}),
        encoding="utf-8",
    )
    assert store.bootstrap_from_books_dir(tmp_path / "seed_repo") == 0
    assert store.get_book("db-edge")["title"] == "DB Edge"

    store.close()
    assert store._sqlite_conn is None
    store.close()
