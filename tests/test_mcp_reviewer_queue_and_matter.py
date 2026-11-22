"""TDD tests for REQ-064..REQ-067: Reviewer queue pagination, matter flow, full IDs, & context bundle."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from typing import Any
import pytest
from starlette.testclient import TestClient

import scripts.collab_worker as worker_mod
from scripts.mcp_server.app import create_app
from scripts.mcp_server.auth import AuthContext
from scripts.mcp_server.config import McpServerConfig
from scripts.mcp_server.db import CollabStore
from scripts.mcp_server.git_sync import CollabBooksGitSync
from scripts.mcp_server.service import CollabMcpService, ServiceError


class _FakeReviewClient:
    """Deterministic stub for review-server translation calls."""

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
            "judgeSummary": "Clean literary Vietnamese translation.",
            "openFindings": [],
        }

    def approve_workstream(self, *, workstream_id: str, override_escalated: bool = False) -> dict[str, Any]:
        return {"id": workstream_id, "stageStatus": "APPROVED"}

    def get_workstream(self, *, workstream_id: str) -> dict[str, Any]:
        return {"workstream": {"id": workstream_id, "stageStatus": "GOVERNANCE_CLEAN"}}


def _init_git_repo(tmp_path: Path) -> Path:
    bare_remote = tmp_path / "remote" / "collab-books.git"
    bare_remote.mkdir(parents=True)
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_remote)], check=True, capture_output=True)

    work_repo = tmp_path / "work" / "collab-books"
    work_repo.mkdir(parents=True)
    subprocess.run(["git", "-C", str(work_repo), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(work_repo), "remote", "add", "origin", str(bare_remote)],
        check=True,
        capture_output=True,
    )
    (work_repo / "README.md").write_text("# Collab Books\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work_repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(work_repo),
            "-c",
            "user.name=Test Bot",
            "-c",
            "user.email=bot@example.com",
            "commit",
            "-m",
            "init",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(work_repo), "push", "-u", "origin", "main"], check=True, capture_output=True)
    return work_repo


def _make_cfg(tmp_path: Path) -> McpServerConfig:
    work_repo = _init_git_repo(tmp_path)
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


def _make_service(tmp_path: Path) -> tuple[McpServerConfig, CollabStore, CollabMcpService]:
    cfg = _make_cfg(tmp_path)
    store = CollabStore(cfg)
    store.init_db()
    sync = CollabBooksGitSync(cfg, publish_github_release=False)
    svc = CollabMcpService(
        cfg=cfg,
        store=store,
        review_client=_FakeReviewClient(),
        git_sync=sync,
    )
    return cfg, store, svc


def _xml_pair(orig: str, vi: str, *, seg_type: str = "paragraph") -> tuple[str, str]:
    src = f'<segment id="S-0001" type="{seg_type}">\n<original>\n{orig}\n</original>\n</segment>\n'
    tr = (
        f'<segment id="S-0001" type="{seg_type}">\n<original>\n{orig}\n</original>\n'
        f'<translate>\n{vi}\n</translate>\n</segment>\n'
    )
    return src, tr


def _auth(tier: str, handle: str, *, role: str = "contributor", ip: str = "ip-hash") -> AuthContext:
    actor = "admin:test" if tier == "ADMIN" else (f"agent:{handle}" if tier == "AGENT_KEY" else f"anon:{handle}")
    return AuthContext(tier=tier, actor_id=actor, handle=handle, client_ip_hash=ip, agent_role=role)


def test_req_064_section_type_classification_backfill_and_boilerplate_auto_accept(tmp_path: Path) -> None:
    """REQ-064: Chapter section_type tagging, startup backfill, and boilerplate auto-accept."""
    from scripts.mcp_server.service import VALID_SECTION_TYPES, _classify_chapter_section_type

    assert VALID_SECTION_TYPES == frozenset({"body", "front_matter", "back_matter", "boilerplate"})

    # 1. Deterministic classification across boilerplate, front_matter, back_matter, and body
    cover_src, cover_tr = _xml_pair(
        "![Cover: How Countries Go Broke: The Big Cycle, by Ray Dalio.](images/9781501124075.jpg)",
        "![Cover: How Countries Go Broke: The Big Cycle, by Ray Dalio.](images/9781501124075.jpg)",
        seg_type="figure",
    )
    assert _classify_chapter_section_type("ch01", "Bìa sách (Cover)", cover_src) == "boilerplate"

    promo_src, promo_tr = _xml_pair(
        "Thank you for downloading this Simon & Schuster ebook. Get a FREE ebook when you join our mailing list.",
        "Cảm ơn bạn đã tải xuống cuốn sách điện tử của Simon & Schuster. Nhận sách miễn phí qua bản tin email.",
    )
    assert _classify_chapter_section_type("ch02", "Thank you for downloading this Simon & Schuster ebook.", promo_src) == "boilerplate"

    title_src, title_tr = _xml_pair(
        "HOW COUNTRIES GO BROKE: THE BIG CYCLE by Ray Dalio",
        "CÁC QUỐC GIA PHÁ SẢN NHƯ THẾ NÀO: ĐẠI CHU KỲ của Ray Dalio",
    )
    assert _classify_chapter_section_type("ch03", "Trang nhan đề (Title Page)", title_src) == "boilerplate"

    copy_src, copy_tr = _xml_pair(
        "AVID READER PRESS Copyright © 2025 by Ray Dalio ISBN 978-1-5011-2407-5 (ebook)",
        "AVID READER PRESS Bản quyền © 2025 thuộc về Ray Dalio ISBN 978-1-5011-2407-5 (bản điện tử).",
    )
    assert _classify_chapter_section_type("ch41", "AVID READER PRESS", copy_src) == "boilerplate"

    part_div_src, part_div_tr = _xml_pair(
        "# PART I: OVERVIEW OF THE BIG DEBT CYCLE",
        "# PHẦN I: TỔNG QUAN VỀ CHU KỲ NỢ LỚN",
    )
    assert _classify_chapter_section_type("ch08", "PART I: OVERVIEW OF THE BIG DEBT CYCLE", part_div_src) == "boilerplate"

    # Substantive Part I (>40 words) MUST remain body, never boilerplate!
    long_part_prose = " ".join(["history"] * 60)
    long_part_src, _ = _xml_pair(long_part_prose, "Lịch sử " * 30)
    assert _classify_chapter_section_type("ch02", "Part I: Where I'm Coming From", long_part_src) == "body"

    front_src, front_tr = _xml_pair(
        "How to Read This Book. This book has three levels of depth for different readers.",
        "Hướng dẫn đọc cuốn sách này với ba cấp độ sâu khác nhau dành cho độc giả.",
    )
    assert _classify_chapter_section_type("ch06", "HOW TO READ THIS BOOK", front_src) == "front_matter"

    back_src, back_tr = _xml_pair(
        "About the Author. Ray Dalio is the founder of Bridgewater Associates and author of Principles.",
        "Về tác giả. Ray Dalio là nhà sáng lập Bridgewater Associates và tác giả cuốn Nguyên lý.",
    )
    assert _classify_chapter_section_type("ch37", "ABOUT THE AUTHOR", back_src) == "back_matter"

    body_src, body_tr = _xml_pair(
        "When central governments borrow too much over a long-term debt cycle, the central bank prints money.",
        "Khi chính phủ trung ương vay nợ quá mức trong chu kỳ nợ dài hạn, ngân hàng trung ương sẽ in tiền.",
    )
    assert _classify_chapter_section_type("ch10", "CHAPTER 1: THE BIG DEBT CYCLE", body_src) == "body"

    # 2. Startup backfill of pre-existing SQLite chapters
    cfg, store, svc = _make_service(tmp_path)
    admin = _auth("ADMIN", "admin", role="admin")
    aa = svc.register_agent(_auth("ANONYMOUS", "anon-aa"), handle="admin-helper")
    svc.set_agent_role(admin, identifier=aa["handle"], role="agent_admin")
    aa_ctx = _auth("AGENT_KEY", aa["handle"], role="agent_admin")

    # Seed a legacy book row + chapters lacking section_type classification
    store.upsert_book(
        slug="legacy-backfill",
        title="Legacy",
        author="Ray Dalio",
        source_lang="en",
        target_lang="vi",
        genre="non-fiction",
        license_id="owner-permission",
        license_url="https://example.com",
        status="active",
        glossary_sha="abc",
        book_record={"terms": {"Hard Money": "tiền cứng"}},
        manifest={"slug": "legacy-backfill", "source_lang": "en", "target_lang": "vi"},
        proposed_by=aa["handle"],
    )
    store.upsert_chapter(
        book_slug="legacy-backfill",
        chapter_id="ch01",
        chapter_num=1,
        title="Bìa sách (Cover)",
        source_xml=cover_src,
        status="open",
    )
    store.upsert_chapter(
        book_slug="legacy-backfill",
        chapter_id="ch02",
        chapter_num=2,
        title="CHAPTER 1: REAL CONTENT",
        source_xml=body_src,
        status="open",
    )
    svc._backfill_chapter_section_types()
    assert store.get_chapter("legacy-backfill", "ch01")["section_type"] == "boilerplate"
    assert store.get_chapter("legacy-backfill", "ch02")["section_type"] == "body"

    # 3. Trusted submit_book_proposal + import_chapter_translations auto-accepts boilerplate only
    svc.submit_book_proposal(
        aa_ctx,
        slug="dalio-matter-auto",
        title="How Countries Go Broke",
        author="Ray Dalio",
        source_lang="en",
        genre="non-fiction",
        license_id="owner-permission",
        license_url="https://example.com/perm",
        source_filename="hcgb.epub",
        book_record={"terms": {"Big Cycle": "Chu kỳ lớn"}},
        chapters=[
            {"chapter_id": "ch01", "title": "Bìa sách (Cover)", "source_xml": cover_src, "translation_xml": cover_tr},
            {"chapter_id": "ch02", "title": "HOW TO READ THIS BOOK", "source_xml": front_src},
            {"chapter_id": "ch03", "title": "CHAPTER 1: BODY", "source_xml": body_src},
            {"chapter_id": "ch04", "title": "ABOUT THE AUTHOR", "source_xml": back_src},
            {"chapter_id": "ch05", "title": "AVID READER PRESS", "source_xml": copy_src},
        ],
    )
    bk = svc.get_book(aa_ctx, book_slug="dalio-matter-auto")
    sec_by_ch = {c["chapter_id"]: c for c in bk["chapters"]}
    assert sec_by_ch["ch01"]["section_type"] == "boilerplate"
    assert sec_by_ch["ch01"]["status"] == "accepted"
    assert sec_by_ch["ch02"]["section_type"] == "front_matter"
    assert sec_by_ch["ch03"]["section_type"] == "body"
    assert sec_by_ch["ch04"]["section_type"] == "back_matter"
    assert sec_by_ch["ch05"]["section_type"] == "boilerplate"

    # Import remaining ch02..ch05 (including an unaccepted existing pending_review boilerplate upgrade test on ch05)
    svc.import_chapter_translations(
        aa_ctx,
        book_slug="dalio-matter-auto",
        chapters=[
            {"chapter_id": "ch05", "translation_xml": copy_tr},
        ],
        auto_approve_boilerplate=False,
    )
    ch05_sub = svc.query_submissions(aa_ctx, book_slug="dalio-matter-auto", chapter_id="ch05")[0]
    assert ch05_sub["status"] == "pending_review"

    imp_res = svc.import_chapter_translations(
        aa_ctx,
        book_slug="dalio-matter-auto",
        chapters=[
            {"chapter_id": "ch02", "translation_xml": front_tr},
            {"chapter_id": "ch03", "translation_xml": body_tr},
            {"chapter_id": "ch04", "translation_xml": back_tr},
            {"chapter_id": "ch05", "translation_xml": copy_tr},
        ],
    )
    assert imp_res["auto_accepted_count"] == 1
    assert "ch05" in imp_res["auto_accepted_chapters"]

    bk_after = svc.get_book(aa_ctx, book_slug="dalio-matter-auto")
    ch_after = {c["chapter_id"]: c for c in bk_after["chapters"]}
    assert ch_after["ch05"]["status"] == "accepted"
    assert ch_after["ch02"]["status"] == "open"
    assert ch_after["ch03"]["status"] == "open"
    assert ch_after["ch04"]["status"] == "open"

    # Invalid explicit section_type raises 400 (AC-064-2)
    with pytest.raises(ServiceError) as bad_exc:
        _classify_chapter_section_type("ch09", "Real Chapter", body_src, explicit="invalid_section")
    assert bad_exc.value.status_code == 400

    # approval_mode='manual' keeps boilerplate in pending_admin_approval
    svc.submit_book_proposal(
        aa_ctx,
        slug="manual-matter",
        title="Manual Matter",
        author="Ray Dalio",
        source_lang="en",
        license_id="owner-permission",
        license_url="https://example.com/perm",
        source_filename="man.epub",
        approval_mode="manual",
        book_record={"terms": {"Big Cycle": "Chu kỳ lớn"}},
        chapters=[
            {"chapter_id": "ch01", "title": "Bìa sách (Cover)", "source_xml": cover_src, "translation_xml": cover_tr},
        ],
    )
    assert store.get_chapter("manual-matter", "ch01")["status"] == "open"
    assert svc.query_submissions(aa_ctx, book_slug="manual-matter", chapter_id="ch01")[0]["status"] == "pending_admin_approval"


def test_req_065_reviewer_queue_pagination_full_uuid_and_body_first_ordering(tmp_path: Path) -> None:
    """REQ-065: Default limit=200, offset pagination, full 36-char submission_id, compact summaries, body-first natural sort."""
    cfg, store, svc = _make_service(tmp_path)
    admin = _auth("ADMIN", "admin", role="admin")
    aa = svc.register_agent(_auth("ANONYMOUS", "anon-aa"), handle="aa-queue")
    svc.set_agent_role(admin, identifier=aa["handle"], role="agent_admin")
    aa_ctx = _auth("AGENT_KEY", aa["handle"], role="agent_admin")
    peer_ctx = _auth("AGENT_KEY", "peer-rev", role="contributor")

    # Create 2 books totaling 90 pending_review chapters (mixing front_matter, body, and back_matter)
    front_src, front_tr = _xml_pair(
        "How to Read This Book explains the reading depth levels.",
        "Hướng dẫn đọc cuốn sách này giải thích các cấp độ đọc.",
    )
    back_src, back_tr = _xml_pair(
        "About the Author. Ray Dalio founded Bridgewater Associates.",
        "Về tác giả. Ray Dalio sáng lập Bridgewater Associates.",
    )
    body_src, body_tr = _xml_pair(
        "The big cycle of money and credit unfolds repeatedly.",
        "Chu kỳ lớn của tiền tệ và tín dụng diễn ra lặp đi lặp lại.",
    )

    for b_idx, (slug, total) in enumerate([("book-alpha", 45), ("book-beta", 45)]):
        chs = []
        for i in range(1, total + 1):
            ch_id = f"ch{i:02d}"
            if i == 1:
                chs.append({"chapter_id": ch_id, "title": "HOW TO READ THIS BOOK", "source_xml": front_src, "translation_xml": front_tr})
            elif i == total:
                chs.append({"chapter_id": ch_id, "title": "ABOUT THE AUTHOR", "source_xml": back_src, "translation_xml": back_tr})
            else:
                chs.append({"chapter_id": ch_id, "title": f"CHAPTER {i}: CONTENT", "source_xml": body_src, "translation_xml": body_tr})
        svc.submit_book_proposal(
            aa_ctx,
            slug=slug,
            title=f"Book {b_idx}",
            author="Ray Dalio",
            source_lang="en",
            license_id="owner-permission",
            license_url="https://example.com/perm",
            source_filename=f"{slug}.epub",
            book_record={"terms": {"Big Cycle": "Chu kỳ lớn"}},
            chapters=chs,
        )

    # 1. Single default list_tasks(role='reviewer') returns all 90!
    q_all = svc.list_tasks(peer_ctx, role="reviewer")
    cands = q_all["peer_review_candidates"]
    assert len(cands) == 90
    assert q_all["peer_review_total"] == 90
    assert q_all["peer_review_limit"] == 200
    assert q_all["peer_review_offset"] == 0
    assert q_all["peer_review_has_more"] is False

    # 2. Compact payload + full 36-char UUID in BOTH submission_id and id
    uuid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    for item in cands:
        assert uuid_re.match(item["id"])
        assert item["submission_id"] == item["id"]
        assert "translation_xml" not in item
        assert "final_txt" not in item
        assert "rounds_history" not in item
        assert item["segment_count"] == 1
        assert item["book_title"] in {"Book 0", "Book 1"}
        assert item["chapter_title"]

    # 3. Body chapters come BEFORE front_matter and back_matter, sorted naturally ascending by chapter_num!
    alpha_cands = [c for c in cands if c["book_slug"] == "book-alpha"]
    sections_seen = [c["section_type"] for c in alpha_cands]
    first_front_idx = sections_seen.index("front_matter")
    first_back_idx = sections_seen.index("back_matter")
    assert all(s == "body" for s in sections_seen[:first_front_idx])
    assert first_front_idx < first_back_idx
    alpha_body_nums = [c["chapter_num"] for c in alpha_cands if c["section_type"] == "body"]
    assert alpha_body_nums == sorted(alpha_body_nums)

    # 4. Pagination via limit & offset
    page1 = svc.list_tasks(peer_ctx, role="reviewer", limit=40, offset=40)
    assert len(page1["peer_review_candidates"]) == 40
    assert page1["peer_review_total"] == 90
    assert page1["peer_review_limit"] == 40
    assert page1["peer_review_offset"] == 40
    assert page1["peer_review_has_more"] is True

    page2 = svc.list_tasks(peer_ctx, role="reviewer", limit=40, offset=80)
    assert len(page2["peer_review_candidates"]) == 10
    assert page2["peer_review_has_more"] is False

    # 5. Filtering via include_matter=False and section_type='body' / invalid 400
    body_only = svc.list_tasks(peer_ctx, role="reviewer", include_matter=False)
    assert body_only["peer_review_total"] == 86
    assert all(c["section_type"] == "body" for c in body_only["peer_review_candidates"])

    front_only = svc.list_tasks(peer_ctx, role="reviewer", section_type="front_matter")
    assert front_only["peer_review_total"] == 2
    assert all(c["section_type"] == "front_matter" for c in front_only["peer_review_candidates"])

    with pytest.raises(ServiceError) as sec_exc:
        svc.list_tasks(peer_ctx, role="reviewer", section_type="bogus")
    assert sec_exc.value.status_code == 400

    # 6. query_submissions_page defaults limit=100, supports offset, section_type, and include_xml=False
    sub_page = svc.query_submissions_page(peer_ctx, limit=100, offset=0, include_xml=False)
    assert sub_page["total"] == 90
    assert len(sub_page["submissions"]) == 90
    assert sub_page["has_more"] is False
    assert all("translation_xml" not in s and "submission_id" in s for s in sub_page["submissions"])


def test_req_066_submission_context_bundle_and_post_lock_brief_and_additions(tmp_path: Path) -> None:
    """REQ-066: Unified context + book_record + brief in get_submission_status and post-lock agent_admin brief + additions sync."""
    cfg, store, svc = _make_service(tmp_path)
    admin = _auth("ADMIN", "admin", role="admin")
    aa = svc.register_agent(_auth("ANONYMOUS", "anon-aa"), handle="aa-ctx")
    svc.set_agent_role(admin, identifier=aa["handle"], role="agent_admin")
    aa_ctx = _auth("AGENT_KEY", aa["handle"], role="agent_admin")
    peer_ctx = _auth("AGENT_KEY", "peer-ctx", role="contributor")

    src1, tr1 = _xml_pair(
        "The United States has dominated the global reserve currency system.",
        "Hoa Kỳ đã chi phối hệ thống đồng tiền dự trữ toàn cầu.",
    )
    src2, tr2 = _xml_pair(
        "In the US, Quantitative Easing changed debt monetization.",
        "Tại Hoa Kỳ, nới lỏng định lượng (Quantitative Easing) đã thay đổi cơ chế tiền tệ hóa nợ.",
    )

    svc.submit_book_proposal(
        aa_ctx,
        slug="ctx-bundle-book",
        title="Context Bundle Book",
        author="Ray Dalio",
        source_lang="en",
        genre="non-fiction",
        license_id="owner-permission",
        license_url="https://example.com/perm",
        source_filename="ctx.epub",
        brief={
            "genre": "non-fiction",
            "register": "Academic macro",
            "style_rules": ["Use Hoa Kỳ."],
        },
        book_record={
            "names": [{"source": "United States", "target": "Hoa Kỳ", "also_referred_to_as": ["US"]}],
            "terms": [{"source": "reserve currency", "target": "đồng tiền dự trữ", "match": "word"}],
        },
        chapters=[
            {"chapter_id": "ch01", "title": "CHAPTER 1", "source_xml": src1, "translation_xml": tr1},
            {"chapter_id": "ch02", "title": "CHAPTER 2", "source_xml": src2, "translation_xml": tr2},
        ],
    )
    sub2_id = svc.query_submissions(peer_ctx, book_slug="ctx-bundle-book", chapter_id="ch02")[0]["id"]

    # 1. Re-import ch02 once to verify rounds_history has ZERO duplicated translation_xml
    svc.import_chapter_translations(
        aa_ctx,
        book_slug="ctx-bundle-book",
        chapters=[{"chapter_id": "ch02", "translation_xml": tr2}],
    )

    detail = svc.get_submission_status(peer_ctx, submission_id=sub2_id, include_context=True)
    assert detail["submission"]["submission_id"] == sub2_id
    for h_item in detail["submission"].get("rounds_history", []):
        assert "translation_xml" not in h_item

    # Unified context + top-level book_record/brief + backward-compatible book_context/chapter_context
    assert isinstance(detail.get("context"), dict) and detail["context"]
    assert detail["context"]["book_record"]["terms"]["reserve currency"] == "đồng tiền dự trữ"
    assert detail["context"]["brief"]["register"] == "Academic macro"
    assert detail["context"]["section_type"] == "body"
    assert "<original>" in detail["context"]["source_xml"]
    assert detail["book_record"] == detail["context"]["book_record"]
    assert detail["brief"] == detail["context"]["brief"]
    assert detail["book_context"]["book_record"] == detail["context"]["book_record"]
    assert detail["chapter_context"]["section_type"] == "body"

    # include_context=False omits all context bundles
    minimal = svc.get_submission_status(peer_ctx, submission_id=sub2_id, include_context=False)
    for omitted in ("context", "book_context", "chapter_context", "book_record", "brief"):
        assert omitted not in minimal

    # 2. Post-lock agent_admin brief update + bidirectional additions sync (terms + term_entries + characters + names)
    locked_sha = svc.get_book(aa_ctx, book_slug="ctx-bundle-book")["glossary_sha"]
    assert locked_sha

    updated = svc.update_book_record(
        aa_ctx,
        book_slug="ctx-bundle-book",
        brief={
            "genre": "non-fiction",
            "register": "Academic macro",
            "style_rules": [
                "Pin 'the US' / 'US' -> 'Hoa Kỳ'.",
                "Keep English terms in parentheses on first mention only.",
            ],
        },
        additions={
            "proposals": [
                {"source": "the US", "target": "Hoa Kỳ", "category": "term"},
                {"source": "Richard Nixon", "target": "Richard Nixon", "category": "name"},
            ]
        },
    )
    assert updated["glossary_sha"] == locked_sha
    assert any("parentheses" in r for r in updated["brief"]["style_rules"])
    assert updated["book_record"]["terms"]["the US"] == "Hoa Kỳ"
    assert any(e["source"] == "the US" and e["target"] == "Hoa Kỳ" for e in updated["book_record"]["term_entries"])
    assert updated["book_record"]["characters"]["Richard Nixon"] == "Richard Nixon"
    assert any(e["source"] == "Richard Nixon" for e in updated["book_record"]["names"])

    # Destructive full book_record overwrite after lock still returns 409 for agent_admin (AC-066-2)
    with pytest.raises(ServiceError) as lock_exc:
        svc.update_book_record(aa_ctx, book_slug="ctx-bundle-book", book_record={"terms": {"Only": "Mỗi"}})
    assert lock_exc.value.status_code == 409


def test_req_067_mcp_v430_rest_admin_and_worker_cli_parity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-067: MCP v4.3.0, REST pagination/matter params, full-UUID /admin cards + node --check, and collab_worker CLI parity."""
    cfg, store, svc = _make_service(tmp_path)
    app = create_app(cfg=cfg, service=svc)
    sso_hdr = {"X-Auth-Request-Email": "admin@vinhthang.dev"}
    repo_root = Path(__file__).resolve().parent.parent

    front_src, front_tr = _xml_pair("With Appreciation. Thank you.", "Lời cảm ơn chân thành.")
    body_src, body_tr = _xml_pair("The US reserve currency cycle.", "Chu kỳ đồng tiền dự trữ của Hoa Kỳ.")
    cover_src, cover_tr = _xml_pair(
        "![Cover: Test](images/cover.jpg)",
        "![Cover: Test](images/cover.jpg)",
        seg_type="figure",
    )

    with TestClient(app) as client:
        assert client.get("/api/health").json()["version"] == "4.3.0"
        assert "v4.3.0" in client.get("/llms.txt").text
        assert json.loads((repo_root / "plugin.json").read_text(encoding="utf-8"))["version"] == "4.3.0"

        init_rpc = client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
            },
        ).json()
        assert init_rpc["result"]["serverInfo"]["version"] == "4.3.0"

        reg = client.post("/api/v1/agents/register", json={"handle": "aa-v43"}).json()
        client.post(
            "/admin/api/agents/role",
            headers=sso_hdr,
            json={"identifier": reg["handle"], "role": "agent_admin"},
        )
        aa_hdr = {"Authorization": f"Bearer {reg['agent_key']}"}
        peer = client.post("/api/v1/agents/register", json={"handle": "peer-v43"}).json()
        peer_hdr = {"Authorization": f"Bearer {peer['agent_key']}"}

        # Submit via REST with boilerplate + front_matter + body
        prop = client.post(
            "/api/v1/books/propose",
            headers=aa_hdr,
            json={
                "slug": "v43-matter-book",
                "title": "V4.3 Matter Book",
                "author": "Ray Dalio",
                "source_lang": "en",
                "license": "owner-permission",
                "license_url": "https://example.org/perm",
                "source_filename": "v43.epub",
                "book_record": {"terms": {"reserve currency": "đồng tiền dự trữ"}},
                "chapters": [
                    {"chapter_id": "ch01", "title": "Bìa sách (Cover)", "source_xml": cover_src, "translation_xml": cover_tr},
                    {"chapter_id": "ch02", "title": "WITH APPRECIATION", "source_xml": front_src, "translation_xml": front_tr},
                    {"chapter_id": "ch03", "title": "CHAPTER 1: REAL", "source_xml": body_src, "translation_xml": body_tr},
                ],
            },
        )
        assert prop.status_code == 201
        assert prop.json()["book"].get("auto_accepted_count") == 1

        # REST /api/v1/tasks pagination + include_matter + full submission_id
        t_all = client.get("/api/v1/tasks", headers=peer_hdr, params={"role": "reviewer", "limit": 100}).json()
        assert t_all["peer_review_total"] == 2
        assert t_all["peer_review_candidates"][0]["chapter_id"] == "ch03"
        assert t_all["peer_review_candidates"][0]["section_type"] == "body"
        sub3_id = t_all["peer_review_candidates"][0]["submission_id"]
        assert len(sub3_id) == 36
        assert t_all["peer_review_candidates"][1]["chapter_id"] == "ch02"

        t_body = client.get("/api/v1/tasks", headers=peer_hdr, params={"role": "reviewer", "include_matter": "false"}).json()
        assert t_body["peer_review_total"] == 1

        # REST /api/v1/submissions paginated envelope + /api/v1/submissions/{id} context bundle
        q_res = client.get("/api/v1/submissions", headers=peer_hdr, params={"limit": 1, "offset": 0, "include_xml": "false"}).json()
        assert q_res["total"] >= 2
        assert q_res["has_more"] is True
        assert "translation_xml" not in q_res["submissions"][0]

        sub_det = client.get(f"/api/v1/submissions/{sub3_id}", headers=peer_hdr).json()
        assert sub_det["context"]["book_record"]["terms"]["reserve currency"] == "đồng tiền dự trữ"
        assert sub_det["book_record"] == sub_det["context"]["book_record"]
        assert "brief" in sub_det

        # /admin shows full 36-char UUID + section_type badge & passes node --check
        admin_html = client.get("/admin", headers=sso_hdr).text
        assert sub3_id in admin_html
        assert f"<code>{sub3_id[:8]}</code>" not in admin_html
        script_m = re.search(r"<script>([\s\S]*?)</script>\s*</body>", admin_html)
        assert script_m is not None
        js_file = tmp_path / "admin_v43.js"
        js_file.write_text(script_m.group(1), encoding="utf-8")
        node_chk = subprocess.run(["node", "--check", str(js_file)], capture_output=True, text=True)
        assert node_chk.returncode == 0, node_chk.stderr

        # collab_worker.py CLI parity: list-tasks --limit --offset --no-matter and get-submission
        monkeypatch.setattr(worker_mod, "_get_mcp_http_client", lambda: client)
        w_tasks = worker_mod.run_mcp_command(
            subcommand="list-tasks",
            mcp_url="http://testserver/mcp",
            agent_key=peer["agent_key"],
            role="reviewer",
            limit=50,
            offset=0,
            include_matter=False,
        )
        assert w_tasks["success"] is True
        assert w_tasks["peer_review_total"] == 1

        w_sub = worker_mod.run_mcp_command(
            subcommand="get-submission",
            mcp_url="http://testserver/mcp",
            agent_key=peer["agent_key"],
            submission_id=sub3_id,
        )
        assert w_sub["success"] is True
        assert w_sub["context"]["chapter_id"] == "ch03"



