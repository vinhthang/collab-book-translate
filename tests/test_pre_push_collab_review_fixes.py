"""Regression tests for pre-push code review P1 fixes (collab-book-translate)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import docx
import pytest

from scripts.collab_intake import validate_submission_pr
from scripts.collab_pr_validate import validate_review
from scripts.collab_seed import main as collab_seed_main, seed_merged_book
from scripts.collab_sweep import (
    GitHubCLIClient,
    default_compile_book,
    default_seed_book,
    run_sweep,
)
from scripts.collab_verdict import evaluate_pr_from_reviews
from scripts.collab_worker import (
    _get_gh_token,
    list_role_tasks,
    submit_book,
    submit_translation,
)
from scripts.compilers.docx_compiler import DOCXCompiler
from scripts.compilers.epub_compiler import EPUBCompiler
from scripts.extract import extract_book


SHA_1 = "1" * 40
SHA_2 = "2" * 40
GLOSSARY_SHA_64 = "a" * 64


def _setup_seeded_book(root: Path, slug: str = "test-book") -> Path:
    book_dir = root / "books" / slug
    (book_dir / "source").mkdir(parents=True, exist_ok=True)
    manifest = {
        "slug": slug,
        "title": "Test Book",
        "author": "Author A",
        "genre": "non-fiction",
        "source_lang": "en",
        "target_lang": "vi",
        "license": "public-domain",
        "license_url": "https://creativecommons.org/publicdomain/mark/1.0/",
        "submitter": "contributor",
    }
    (book_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (book_dir / "source" / "book.txt").write_text("Chapter 1\n\nHello world\n", encoding="utf-8")

    run_dir = root / "runs" / slug
    run_dir.mkdir(parents=True, exist_ok=True)
    ch_entries = []
    for idx, ch in enumerate(("ch01", "ch02", "ch03"), start=1):
        ch_d = run_dir / ch
        ch_d.mkdir(parents=True, exist_ok=True)
        src_xml = '<segment id="S-0001" type="paragraph">\n<original>\nHello world\n</original>\n</segment>\n'
        (ch_d / "source.txt").write_text(src_xml, encoding="utf-8")
        if ch == "ch01":
            (ch_d / "final.txt").write_text("Chương 1 hoàn tất\n", encoding="utf-8")
            (ch_d / "translation.xml").write_text(
                '<segment id="S-0001" type="paragraph">\n<original>\nHello world\n</original>\n'
                '<translate>\nXin chào thế giới\n</translate>\n</segment>\n',
                encoding="utf-8",
            )
            ch_entries.append({"chapter": ch, "chapter_num": idx, "title": ch, "status": "done"})
        else:
            ch_entries.append({"chapter": ch, "chapter_num": idx, "title": ch, "status": "pending"})

    (run_dir / "checklist.json").write_text(
        json.dumps({"book_slug": slug, "seed_status": "seeded", "seed_error": None, "chapters": ch_entries}),
        encoding="utf-8",
    )
    (run_dir / "book_record.json").write_text(
        json.dumps({
            "book_slug": slug,
            "source_lang": "en",
            "target_lang": "vi",
            "title": "Test Book",
            "brief": {"genre": "non-fiction", "author": "Author A", "summary": "Summary"},
            "glossary_sha": GLOSSARY_SHA_64,
            "glossary": {"Archive": "Kho lưu trữ"},
            "terms": [{"source": "Archive", "target": "Kho lưu trữ", "match": "word", "case_sensitive": False}],
            "names": [],
            "pronouns": [],
        }),
        encoding="utf-8",
    )
    (run_dir / "collab_ledger.json").write_text(
        json.dumps({
            "book_slug": slug,
            "released_tag": None,
            "chapters": {
                "ch01": {
                    "chapter": "ch01",
                    "issue_number": 0,
                    "selected_pr": 1,
                    "rejected_prs": [],
                    "translator_login": "1quachuoi9vang",
                    "reviewer_logins": [],
                    "reviewer_score": 4.8,
                    "score_of_record": 4.8,
                    "verdict": "PASS",
                    "merge_sha": "f" * 40,
                    "glossary_sha": GLOSSARY_SHA_64,
                }
            },
        }),
        encoding="utf-8",
    )
    return run_dir


class _MockSweepClient:
    def __init__(self, prs, issues, pr_blobs):
        self.prs = prs
        self.issues = issues
        self.pr_blobs = pr_blobs
        self.bot_login = "1quachuoi9vang"
        self.pr_comments: Dict[int, List[str]] = {}
        self.issue_comments: Dict[int, List[str]] = {}
        self.merged_prs: List[int] = []
        self.closed_prs: List[int] = []
        self.closed_issues: List[int] = []

    def list_open_prs(self):
        return [p for p in self.prs if p.get("state", "open") == "open"]

    def list_open_issues(self):
        return [i for i in self.issues if i.get("state", "open") == "open"]

    def list_all_issues(self):
        return list(self.issues)

    def get_pr_file_at_oid(self, pr_num: int, head_oid: str, rel_path: str):
        return self.pr_blobs.get((pr_num, head_oid, rel_path))

    def comment_pr(self, pr_num: int, body: str) -> None:
        self.pr_comments.setdefault(pr_num, []).append(body)
        for pr in self.prs:
            if pr["number"] == pr_num:
                pr.setdefault("comments", []).append({"body": body, "author_login": self.bot_login})

    def set_pr_labels(self, pr_num: int, add: List[str], remove: List[str]) -> None:
        for pr in self.prs:
            if pr["number"] == pr_num:
                cur = [l for l in pr.get("labels", []) if l not in remove]
                for a in add:
                    if a not in cur:
                        cur.append(a)
                pr["labels"] = cur

    def close_pr(self, pr_num: int, comment: Optional[str] = None) -> None:
        self.closed_prs.append(pr_num)
        if comment:
            self.comment_pr(pr_num, comment)
        for pr in self.prs:
            if pr["number"] == pr_num:
                pr["state"] = "closed"

    def merge_pr(self, pr_num: int, head_oid: Optional[str] = None) -> str:
        self.merged_prs.append(pr_num)
        for pr in self.prs:
            if pr["number"] == pr_num:
                pr["state"] = "merged"
        return hashlib.sha1(f"merge:{pr_num}:{head_oid}".encode("utf-8")).hexdigest()

    def set_issue_labels(self, issue_num: int, add: List[str], remove: List[str]) -> None:
        for iss in self.issues:
            if iss["number"] == issue_num:
                cur = [l for l in iss.get("labels", []) if l not in remove]
                for a in add:
                    if a not in cur:
                        cur.append(a)
                iss["labels"] = cur

    def comment_issue(self, issue_num: int, body: str) -> None:
        self.issue_comments.setdefault(issue_num, []).append(body)

    def close_issue(self, issue_num: int) -> None:
        self.closed_issues.append(issue_num)
        for iss in self.issues:
            if iss["number"] == issue_num:
                iss["state"] = "closed"

    def get_release(self, tag: str) -> Optional[Dict[str, Any]]:
        return None

    def upload_release_assets(self, tag: str, assets: List[str]) -> None:
        return None

    def create_release(self, tag: str, title: str, notes: str, assets: List[str]) -> None:
        return None


def _pr_body(agent: str = "alice", slug: str = "test-book", ch: str = "ch02", issue: str = "#20") -> str:
    return (
        f"- **Agent:** {agent}\n"
        f"- **Engine/Model:** claude\n"
        f"- **Source language:** en\n"
        f"- **Target language:** vi\n"
        f"- **Book:** {slug}\n"
        f"- **Chunk ID:** {slug}-{ch}\n"
        f"- **Segments:** S-0001\n"
        f"- **Issue:** {issue}\n"
        f"- **Glossary SHA:** {GLOSSARY_SHA_64}\n"
    )


def _review_dict(reviewer: str, score: float, oid: str, state: str = "COMMENTED") -> Dict[str, Any]:
    return {
        "author_login": reviewer,
        "state": state,
        "commit": {"oid": oid},
        "line_comment_count": 1,
        "body": (
            f"Checked S-0001 carefully.\n"
            f"- **Fidelity:** {score}/5\n"
            f"- **Fluency:** {score}/5\n"
            f"- **Terminology:** {score}/5\n"
            f"- **Register:** {score}/5\n"
            f"- **Structure:** {score}/5\n"
            f"- **Completeness:** {score}/5\n"
        ),
    }


def test_p1_1_stale_reviews_wait_in_needs_review_not_flagged(tmp_path: Path):
    run_dir = _setup_seeded_book(tmp_path)
    clean_xml = (
        '<segment id="S-0001" type="paragraph">\n'
        '<original>\nHello world\n</original>\n'
        '<translate>\nXin chào thế giới thân yêu.\n</translate>\n'
        '</segment>\n'
    )
    issue = {
        "number": 20,
        "title": "Translate [ch02] test-book-ch02: S-0001",
        "body": "- **Book:** test-book\n- **Chunk ID:** test-book-ch02\n",
        "labels": ["status:open", "book:test-book"],
        "comments": [],
        "created_at": 100.0,
    }
    pr_revised = {
        "number": 10,
        "head_ref": "translate/test-book-ch02-alice",
        "head_oid": SHA_2,
        "author_login": "alice",
        "title": "Translate ch02",
        "body": _pr_body(agent="alice", ch="ch02", issue="#20"),
        "changed_files": ["runs/test-book/ch02/translation.xml"],
        "labels": ["status:revise"],
        "reviews": [_review_dict("rev_bob", 3.0, SHA_1, state="COMMENTED")],
        "comments": [{"author_login": "1quachuoi9vang", "body": f"[collab-revise sha={SHA_1}]"}],
    }
    client = _MockSweepClient(
        prs=[pr_revised],
        issues=[issue],
        pr_blobs={(10, SHA_2, "runs/test-book/ch02/translation.xml"): clean_xml.encode("utf-8")},
    )
    run_sweep(
        tmp_path,
        client=client,
        evaluate_fn=lambda pr, r: evaluate_pr_from_reviews(
            reviews=pr.get("reviews", []),
            pr_author=pr.get("author_login", ""),
            head_oid=pr.get("head_oid", ""),
            translation_xml=clean_xml,
        ),
        compile_fn=lambda r, s: [],
        human_approver="vinhthang",
        bot_login="1quachuoi9vang",
    )
    assert "status:flagged" not in pr_revised["labels"]
    assert "status:reassign" not in pr_revised["labels"]
    assert "status:needs-review" in pr_revised["labels"]

    # Phase (c) ledger attribution must exclude stale SHA_1 reviewer when SHA_2 is merged
    pr_merge = {
        "number": 11,
        "head_ref": "translate/test-book-ch02-alice",
        "head_oid": SHA_2,
        "author_login": "alice",
        "title": "Translate ch02",
        "body": _pr_body(agent="alice", ch="ch02", issue="#20"),
        "changed_files": ["runs/test-book/ch02/translation.xml"],
        "labels": ["status:candidate"],
        "reviews": [
            _review_dict("stale_bob", 4.5, SHA_1, state="APPROVED"),
            _review_dict("fresh_carol", 4.9, SHA_2, state="APPROVED"),
        ],
        "comments": [
            {"author_login": "1quachuoi9vang", "body": f"[collab-candidate-score sha={SHA_2} score=4.9 major=0 minor=0]"}
        ],
    }
    client2 = _MockSweepClient(
        prs=[pr_merge],
        issues=[issue],
        pr_blobs={(11, SHA_2, "runs/test-book/ch02/translation.xml"): clean_xml.encode("utf-8")},
    )
    run_sweep(
        tmp_path,
        client=client2,
        compile_fn=lambda r, s: [],
        human_approver="vinhthang",
        bot_login="1quachuoi9vang",
    )
    assert 11 in client2.merged_prs
    ledger = json.loads((run_dir / "collab_ledger.json").read_text(encoding="utf-8"))
    assert ledger["chapters"]["ch02"]["reviewer_logins"] == ["fresh_carol"]


def test_p1_2_rubric_parser_ignores_prose_numbers_and_rejects_bad_denominator():
    review_body = (
        "## Peer Review\n"
        "- **Fidelity:** 4.8/5\n"
        "- **Fluency:** 4.7/5\n"
        "- **Terminology:** 5.0/5\n"
        "- **Register:** 4.6/5\n"
        "- **Structure:** 4.9/5\n"
        "- **Completeness:** 5.0/5\n\n"
        "### Line Comments\n"
        "- S-0005: Structure across the 2 stanzas is preserved faithfully.\n"
        "- S-0012: Terminology matches all 6 canonical glossary entries.\n"
    )
    val = validate_review("alice", "bob", review_body, line_comment_count=1)
    assert val["valid"] is True, val["defects"]
    assert val["scores"]["structure"] == pytest.approx(4.9)
    assert val["scores"]["terminology"] == pytest.approx(5.0)

    ev = evaluate_pr_from_reviews(
        [{"author_login": "bob", "body": review_body, "commit_oid": "abc"}],
        pr_author="alice",
        head_oid="abc",
        translation_xml='<segment id="S-0001" type="paragraph"><original>Hi</original><translate>Xin chào các bạn thân mến.</translate></segment>',
    )
    assert ev["verdict"] == "PASS"
    assert ev["dimension_scores"]["structure"] == pytest.approx(4.9)
    assert ev["dimension_scores"]["terminology"] == pytest.approx(5.0)

    bad_scale_body = review_body.replace("4.8/5", "4.5/10")
    assert validate_review("alice", "bob", bad_scale_body, line_comment_count=1)["valid"] is False
    ev_bad = evaluate_pr_from_reviews(
        [{"author_login": "bob", "body": bad_scale_body, "commit_oid": "abc"}],
        pr_author="alice",
        head_oid="abc",
    )
    assert ev_bad["verdict"] == "NEEDS_REVIEW"


def test_p1_3_sweep_and_seed_preserve_canonical_additions_and_no_untracked_legacy_file(tmp_path: Path):
    run_dir = _setup_seeded_book(tmp_path)
    clean_xml = (
        '<segment id="S-0001" type="paragraph">\n'
        '<original>\nHello world\n</original>\n'
        '<translate>\nXin chào thế giới thân yêu.\n</translate>\n'
        '</segment>\n'
    )
    additions_payload = json.dumps({
        "proposals": [{"source": "Lantern", "target": "Đèn lồng", "status": "proposed"}]
    }).encode("utf-8")

    issues = [
        {
            "number": 20,
            "title": "Translate [ch02] test-book-ch02: S-0001",
            "body": "- **Book:** test-book\n- **Chunk ID:** test-book-ch02\n",
            "labels": ["status:open", "book:test-book"],
            "comments": [],
            "created_at": 100.0,
        },
        {
            "number": 21,
            "title": "Translate [ch03] test-book-ch03: S-0001",
            "body": "- **Book:** test-book\n- **Chunk ID:** test-book-ch03\n",
            "labels": ["status:open", "book:test-book"],
            "comments": [],
            "created_at": 100.0,
        },
    ]
    pr_ch2 = {
        "number": 30,
        "head_ref": "translate/test-book-ch02-alice",
        "head_oid": SHA_1,
        "author_login": "alice",
        "title": "Translate ch02",
        "body": _pr_body(agent="alice", ch="ch02", issue="#20"),
        "changed_files": ["runs/test-book/ch02/translation.xml", "runs/test-book/ch02/analyze_additions.json"],
        "labels": ["status:needs-checks"],
        "reviews": [_review_dict("rev_bob", 4.8, SHA_1, state="APPROVED")],
        "comments": [],
    }
    pr_ch3 = {
        "number": 31,
        "head_ref": "translate/test-book-ch03-bob",
        "head_oid": SHA_2,
        "author_login": "bob",
        "title": "Translate ch03",
        "body": _pr_body(agent="bob", ch="ch03", issue="#21"),
        "changed_files": ["runs/test-book/ch03/translation.xml"],
        "labels": ["status:needs-checks"],
        "reviews": [_review_dict("rev_alice", 4.8, SHA_2, state="APPROVED")],
        "comments": [],
    }
    client = _MockSweepClient(
        prs=[pr_ch2, pr_ch3],
        issues=issues,
        pr_blobs={
            (30, SHA_1, "runs/test-book/ch02/translation.xml"): clean_xml.encode("utf-8"),
            (30, SHA_1, "runs/test-book/ch02/analyze_additions.json"): additions_payload,
            (31, SHA_2, "runs/test-book/ch03/translation.xml"): clean_xml.encode("utf-8"),
        },
    )
    run_sweep(
        tmp_path,
        client=client,
        evaluate_fn=lambda pr, r: evaluate_pr_from_reviews(
            reviews=pr.get("reviews", []),
            pr_author=pr.get("author_login", ""),
            head_oid=pr.get("head_oid", ""),
            translation_xml=clean_xml,
        ),
        compile_fn=lambda r, s: [],
        human_approver="vinhthang",
        bot_login="1quachuoi9vang",
    )
    assert 30 in client.merged_prs and 31 in client.merged_prs

    ch2_add = json.loads((run_dir / "ch02" / "analyze_additions.json").read_text(encoding="utf-8"))
    assert "proposals" in ch2_add
    assert ch2_add["proposals"][0]["status"] == "bound"
    assert "book_record_additions" not in ch2_add
    assert not (run_dir / "ch03" / "analyze_additions.json").exists()


def test_p1_4_collab_worker_additions_revise_update_defects_and_list_tasks(
    tmp_path: Path, monkeypatch
):
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(
        json.dumps({
            "workspace_dir": str(tmp_path),
            "repo_full_name": "vinhthang/collab-book-translate",
            "bot_login": "1quachuoi9vang",
            "bot_git_name": "1quachuoi9vang",
            "bot_git_email": "bot@example.com",
            "human_approver": "vinhthang",
            "fallback_hours": 48.0,
            "max_revise_rounds": 3,
            "release_formats": "epub,pdf,docx",
            "max_source_bytes": 52428800,
            "allowed_licenses": ["public-domain"],
            "supported_pairs": [["en", "vi"]],
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("COLLAB_CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr("scripts.collab_worker._get_gh_token", lambda u: "mock-token")

    src = tmp_path / "book.md"
    src.write_text("# Chapter 1\n\nOnce upon a time.\n", encoding="utf-8")

    # 1. submit_book returns non-empty defects & uses cfg.workspace_dir even when config_path=None
    res_bad = submit_book(
        gh_user="vinhthang",
        slug="bad-lang",
        source_file=str(src),
        title="Bad Lang",
        author="Author",
        source_lang="fr",
        config_path=None,
    )
    assert res_bad["success"] is False
    assert len(res_bad["defects"]) > 0

    (tmp_path / "books" / "existing-slug").mkdir(parents=True)
    res_dup = submit_book(
        gh_user="vinhthang",
        slug="existing-slug",
        source_file=str(src),
        title="Dup",
        author="Author",
        source_lang="en",
        config_path=None,
    )
    assert res_dup["success"] is False
    assert any("duplicate" in d.lower() or "already exists" in d.lower() for d in res_dup["defects"])

    # 2. submit_translation with valid/invalid additions_file and existing open PR (status:revise)
    slug = "demo-book"
    (tmp_path / "books" / slug).mkdir(parents=True)
    (tmp_path / "books" / slug / "manifest.json").write_text(
        json.dumps({"slug": slug, "source_lang": "en", "target_lang": "vi"}),
        encoding="utf-8",
    )
    ch_dir = tmp_path / "runs" / slug / "ch02"
    ch_dir.mkdir(parents=True)
    (ch_dir / "source.txt").write_text(
        '<segment id="S-0001" type="paragraph"><original>Hello</original></segment>\n',
        encoding="utf-8",
    )
    (tmp_path / "runs" / slug / "book_record.json").write_text(
        json.dumps({"glossary_sha": "b" * 64}), encoding="utf-8"
    )
    tx_xml = tmp_path / "tx.xml"
    tx_xml.write_text(
        '<segment id="S-0001" type="paragraph"><original>Hello</original><translate>Xin chào</translate></segment>\n',
        encoding="utf-8",
    )
    good_add = tmp_path / "good_add.json"
    good_add.write_text(
        json.dumps({"proposals": [{"source": "Reed", "target": "Cây Sậy", "status": "proposed"}]}),
        encoding="utf-8",
    )
    bad_add = tmp_path / "bad_add.json"
    bad_add.write_text(json.dumps({"bad": 1}), encoding="utf-8")

    gh_calls: List[List[str]] = []

    def fake_run_git(self, args, cwd=None, check=True, text=True, capture_output=True):
        if args[:2] == ["worktree", "add"]:
            Path(args[4]).mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(["git"] + args, 0, "", "")

    def fake_run_gh_existing(self, args, cwd=None, repo=None, check=True, text=True, capture_output=True):
        gh_calls.append(args)
        if args[:2] == ["issue", "list"]:
            return subprocess.CompletedProcess(["gh"] + args, 0, json.dumps([{"number": 21, "title": "Translate [ch02] demo-book-ch02"}]), "")
        if args[:2] == ["pr", "list"]:
            return subprocess.CompletedProcess(
                ["gh"] + args,
                0,
                json.dumps([{"number": 55, "url": "https://github.com/o/r/pull/55"}]),
                "",
            )
        if args[:2] == ["pr", "create"]:
            raise AssertionError("Should not invoke gh pr create when an open PR already exists")
        return subprocess.CompletedProcess(["gh"] + args, 0, "", "")

    with patch("scripts.collab_worker.GitHubAccountContext.run_git", fake_run_git), \
         patch("scripts.collab_worker.GitHubAccountContext.run_gh", fake_run_gh_existing):
        bad_add_res = submit_translation(
            gh_user="vinhthang",
            book_slug=slug,
            chapter="ch02",
            translation_file=str(tx_xml),
            additions_file=str(bad_add),
            config_path=None,
        )
        assert bad_add_res["success"] is False

        ok_update = submit_translation(
            gh_user="vinhthang",
            book_slug=slug,
            chapter="ch02",
            translation_file=str(tx_xml),
            additions_file=str(good_add),
            config_path=None,
        )
    assert ok_update["success"] is True, ok_update
    assert ok_update["pr_url"] == "https://github.com/o/r/pull/55"
    assert any(c[:2] == ["pr", "edit"] for c in gh_calls)

    # 3. list_role_tasks filters by book_slug and review status
    mock_prs = [
        {
            "number": 10,
            "headRefName": "translate/book-a-ch02-alice",
            "author": {"login": "alice"},
            "headRefOid": "sha10",
            "labels": [{"name": "status:revise"}, {"name": "book:book-a"}],
            "reviews": [],
        },
        {
            "number": 11,
            "headRefName": "translate/book-b-ch02-alice",
            "author": {"login": "alice"},
            "headRefOid": "sha11",
            "labels": [{"name": "status:revise"}, {"name": "book:book-b"}],
            "reviews": [],
        },
        {
            "number": 12,
            "headRefName": "translate/book-a-ch03-bob",
            "author": {"login": "bob"},
            "headRefOid": "sha12",
            "labels": [{"name": "book:book-a"}, {"name": "lang:en-vi"}, {"name": "agent:bob"}],
            "reviews": [],
        },
        {
            "number": 13,
            "headRefName": "translate/book-a-ch04-bob",
            "author": {"login": "bob"},
            "headRefOid": "sha13",
            "labels": [{"name": "status:revise"}, {"name": "book:book-a"}],
            "reviews": [],
        },
    ]
    with patch("scripts.collab_worker._fetch_open_issues", return_value=[]), \
         patch("scripts.collab_worker._fetch_open_prs", return_value=mock_prs):
        tasks_alice = list_role_tasks(role="all", gh_user="alice", book_slug="book-a", config_path=None)
    assert [p["number"] for p in tasks_alice["revise_prs"]] == [10]
    assert [p["number"] for p in tasks_alice["review_prs"]] == [12]


def test_p1_5_seed_prepare_only_no_cbc_push_origin_and_no_fake_seed_review(
    tmp_path: Path, monkeypatch
):
    slug = "fiction-demo"
    books_dir = tmp_path / "books" / slug
    (books_dir / "source").mkdir(parents=True)
    (books_dir / "manifest.json").write_text(
        json.dumps({
            "slug": slug,
            "title": "Fiction Demo",
            "author": "Author",
            "genre": "fiction",
            "source_lang": "en",
            "target_lang": "vi",
            "license": "public-domain",
            "license_url": "https://creativecommons.org/publicdomain/mark/1.0/",
            "submitter": "vinhthang",
        }),
        encoding="utf-8",
    )
    (books_dir / "source" / "book.md").write_text(
        "# Chapter 1\n\n“Hello,” Alice said to Bob.\n\n# Chapter 2\n\n“Goodbye,” Bob replied.\n",
        encoding="utf-8",
    )

    # 1. --prepare-only must succeed on fiction book without cbc
    orig_isfile = os.path.isfile
    with patch("scripts.orchestrate_run.shutil.which", return_value=None), \
         patch(
             "scripts.orchestrate_run.os.path.isfile",
             side_effect=lambda p: False if str(p).endswith("/cbc") else orig_isfile(p),
         ):
        prep = seed_merged_book(
            repo_root=tmp_path,
            book_slug=slug,
            gh_user="1quachuoi9vang",
            bot_git_name="bot",
            bot_git_email="bot@example.com",
            prepare_only=True,
        )
    assert prep.get("prepared") is True, prep
    assert prep["seed_status"] == "seeding"

    # 2. collab_seed.main without --ch01-eval-file must push_origin=True and keep unreviewed seed PR in seeding
    cfg_p = tmp_path / "cfg.json"
    cfg_p.write_text(
        json.dumps({
            "workspace_dir": str(tmp_path),
            "repo_full_name": "vinhthang/collab-book-translate",
            "bot_login": "1quachuoi9vang",
            "bot_git_name": "1quachuoi9vang",
            "bot_git_email": "bot@example.com",
            "human_approver": "vinhthang",
            "fallback_hours": 48.0,
            "max_revise_rounds": 3,
            "release_formats": "epub",
            "max_source_bytes": 5000000,
            "allowed_licenses": ["public-domain"],
            "supported_pairs": [["en", "vi"]],
        }),
        encoding="utf-8",
    )
    trans_vi = tmp_path / "ch01_vi.xml"
    trans_vi.write_text(
        '<segment id="S-0001" type="heading">\n<original>\n# Chapter 1\n</original>\n<translate>\n# Chương 1\n</translate>\n</segment>\n\n'
        '<segment id="S-0002" type="paragraph">\n<original>\n“Hello,” Alice said to Bob.\n</original>\n<translate>\n“Xin chào,” Alice nói với Bob.\n</translate>\n</segment>\n',
        encoding="utf-8",
    )
    (tmp_path / "runs" / slug / "book_record.json").write_text(
        json.dumps({
            "book_slug": slug,
            "source_lang": "en",
            "target_lang": "vi",
            "title": "Fiction Demo",
            "brief": {"genre": "fiction", "author": "Author", "summary": "Test"},
            "terms": [],
            "names": [{"source": "Alice", "target": "Alice"}, {"source": "Bob", "target": "Bob"}],
            "pronouns": [{"pair": ["Alice", "Bob"], "a_to_b": "tôi/anh", "b_to_a": "anh/em", "basis": "peers", "provisional": False}],
        }),
        encoding="utf-8",
    )

    with patch("scripts.collab_seed.seed_merged_book", return_value={"seeded": False, "seed_status": "seeding"}) as m_seed:
        rc = collab_seed_main([
            "--config", str(cfg_p),
            "--repo-root", str(tmp_path),
            "--book-slug", slug,
            "--ch01-translation-file", str(trans_vi),
        ])
        assert rc == 0
        _, kwargs = m_seed.call_args
        assert kwargs.get("push_origin") is True
        assert kwargs.get("evaluate_fn") is None

    # With FakeSeedPRClient returning no reviews, seed_merged_book(evaluate_fn=None) stays seeding (not fake 4.8 merge)
    class _EmptyPRClient:
        def __init__(self):
            self.merged = []
            self.closed = []
            self.open_pr = None

        def find_merged_seed_pr(self, head_ref):
            return None

        def find_open_seed_pr(self, head_ref):
            return self.open_pr

        def create_seed_pr(self, head_ref, title, body, changed_files, translation_xml, labels=None):
            self.open_pr = {
                "number": 88,
                "head_ref": head_ref,
                "head_oid": SHA_1,
                "title": title,
                "body": body,
                "changed_files": changed_files,
                "translation_xml": translation_xml,
                "reviews": [],
            }
            return self.open_pr

        def close_pr(self, pr_num, comment=None):
            self.closed.append(pr_num)

        def merge_pr(self, pr_num, head_oid=None):
            self.merged.append(pr_num)
            return SHA_2

    fake_c = _EmptyPRClient()
    res_awaiting = seed_merged_book(
        repo_root=tmp_path,
        book_slug=slug,
        repo_full_name="vinhthang/collab-book-translate",
        gh_user="1quachuoi9vang",
        bot_git_name="1quachuoi9vang",
        bot_git_email="bot@example.com",
        translate_fn=lambda s, r, m: trans_vi.read_text(encoding="utf-8"),
        evaluate_fn=None,
        pr_client=fake_c,
    )
    assert res_awaiting["seeded"] is False
    assert res_awaiting["seed_status"] == "seeding"
    assert fake_c.merged == []
    assert fake_c.closed == []

    # 3. Untranslated ch01 with evaluate_fn=None must quarantine into blocked_failed
    trans_untranslated = (
        '<segment id="S-0001" type="heading">\n<original>\n# Chapter 1\n</original>\n<translate>\n# Chapter 1\n</translate>\n</segment>\n\n'
        '<segment id="S-0002" type="paragraph">\n<original>\n“Hello,” Alice said to Bob.\n</original>\n'
        '<translate>\n“Hello,” Alice said to Bob and smiled warmly today.\n</translate>\n</segment>\n'
    )
    fake_c2 = _EmptyPRClient()
    res_quar = seed_merged_book(
        repo_root=tmp_path,
        book_slug=slug,
        repo_full_name="vinhthang/collab-book-translate",
        gh_user="1quachuoi9vang",
        bot_git_name="1quachuoi9vang",
        bot_git_email="bot@example.com",
        translate_fn=lambda s, r, m: trans_untranslated,
        evaluate_fn=None,
        pr_client=fake_c2,
    )
    assert res_quar["seed_status"] == "blocked_failed"
    assert fake_c2.closed == [88]

    # 4. default_seed_book must prepare without calling cbc or quarantining
    monkeypatch.setenv("COLLAB_CONFIG_PATH", str(cfg_p))
    slug2 = "sweep-prep-book"
    (tmp_path / "books" / slug2 / "source").mkdir(parents=True)
    (tmp_path / "books" / slug2 / "manifest.json").write_text(
        json.dumps({
            "slug": slug2,
            "title": "Sweep Prep",
            "author": "Author",
            "genre": "non-fiction",
            "source_lang": "en",
            "target_lang": "vi",
            "license": "public-domain",
            "license_url": "https://creativecommons.org/publicdomain/mark/1.0/",
            "submitter": "vinhthang",
        }),
        encoding="utf-8",
    )
    (tmp_path / "books" / slug2 / "source" / "book.md").write_text("# Chapter 1\n\nBody.\n", encoding="utf-8")
    with patch("scripts.collab_sweep.GitHubCLIClient") as m_cli_cls:
        res_def = default_seed_book(tmp_path, slug2)
        assert not m_cli_cls.called or res_def["seed_status"] != "blocked_failed"
    assert res_def["seed_status"] == "seeding"


def test_p1_6_fallback_pr_uses_worktree_and_account_context_and_token_isolation(tmp_path: Path):
    # 1. _get_gh_token ignores ambient parent GH_TOKEN when gh_user is given
    with patch.dict(os.environ, {"GH_TOKEN": "wrong-parent-token", "GITHUB_TOKEN": "wrong-parent-token"}, clear=False):
        os.environ.pop("PYTEST_CURRENT_TEST", None)
        seen_envs: List[Dict[str, str]] = []

        def fake_sub(cmd, *a, **kw):
            seen_envs.append(dict(kw.get("env") or {}))
            return subprocess.CompletedProcess(cmd, 0, "tok-1quachuoi9vang\n", "")

        with patch("scripts.collab_worker.subprocess.run", side_effect=fake_sub):
            tok = _get_gh_token("1quachuoi9vang")
        assert tok == "tok-1quachuoi9vang"
        assert seen_envs and "GH_TOKEN" not in seen_envs[0] and "GITHUB_TOKEN" not in seen_envs[0]

    # 2. GitHubCLIClient.create_fallback_pr isolates branch inside .worktrees and pushes via GitHubAccountContext
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=repo, check=True, capture_output=True)

    ctx_push_calls: List[List[str]] = []

    class _SpyCtx:
        def __init__(self, gh_user: str):
            self.gh_user = gh_user

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def run_git(self, args, cwd=None, check=True, text=True, capture_output=True):
            ctx_push_calls.append(list(args))
            return subprocess.run(["git"] + args, cwd=cwd, check=check, text=text, capture_output=capture_output)

    cli = GitHubCLIClient(
        bot_login="1quachuoi9vang",
        repo_root=repo,
        bot_git_name="1quachuoi9vang",
        bot_git_email="bot@example.com",
    )
    with patch("scripts.collab_worker.GitHubAccountContext", _SpyCtx), \
         patch.object(cli, "_run_gh", side_effect=["[]", "https://github.com/vinhthang/collab-book-translate/pull/77"]):
        pr_res = cli.create_fallback_pr(
            head_ref="translate/demo-ch02-1quachuoi9vang",
            title="fallback ch02",
            body="body",
            changed_files=["runs/demo/ch02/translation.xml"],
            translation_xml="<segment id='S-0001'><original>Hi</original><translate>Chào</translate></segment>\n",
            labels=["book:demo"],
        )
    assert pr_res["number"] == 77
    assert any(c[:2] == ["push", "--force-with-lease"] for c in ctx_push_calls)
    assert not (repo / "runs" / "demo" / "ch02" / "translation.xml").exists()
    cur_branch = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert cur_branch == "main"


def test_p1_7_english_txt_extracts_ch01_and_passes_intake_and_seed(tmp_path: Path):
    en_txt = tmp_path / "story.txt"
    en_txt.write_text(
        "Chapter 1\n\nOnce upon a time in London.\n\nChapter 2\n\nThe fog lifted.\n",
        encoding="utf-8",
    )
    val_en = validate_submission_pr(
        changed_files=["books/en-story/manifest.json", "books/en-story/source/story.txt"],
        manifest_data={
            "slug": "en-story",
            "title": "English Story",
            "author": "Anon",
            "source_lang": "en",
            "target_lang": "vi",
            "license": "public-domain",
            "license_url": "https://creativecommons.org/publicdomain/mark/1.0/",
            "submitter": "alice",
        },
        source_file_path=en_txt,
        max_bytes=5_000_000,
        allowed_licenses={"public-domain"},
        supported_pairs={("en", "vi"), ("zh", "vi")},
    )
    assert val_en["valid"] is True, val_en["defects"]

    out_en = tmp_path / "runs_en"
    chs_en = extract_book(en_txt, out_en, slug="en-story", source_lang="en")
    assert [c["ch_num"] for c in chs_en] == [1, 2]
    assert (out_en / "ch01" / "source.txt").is_file()
    ch01_src_text = (out_en / "ch01" / "source.txt").read_text(encoding="utf-8")
    assert "Once upon a time in London." in ch01_src_text

    # Chapterless English and Chinese .txt promote to ch01 with localized default titles
    en_plain = tmp_path / "plain_en.txt"
    en_plain.write_text("Single short story without chapter headings.\n", encoding="utf-8")
    chs_plain_en = extract_book(en_plain, tmp_path / "runs_plain_en", slug="plain-en", source_lang="en")
    assert len(chs_plain_en) == 1 and chs_plain_en[0]["ch_num"] == 1
    assert chs_plain_en[0]["title"] == "Chapter 1"

    zh_plain = tmp_path / "plain_zh.txt"
    zh_plain.write_text("无章节标题的短篇正文。\n", encoding="utf-8")
    chs_plain_zh = extract_book(zh_plain, tmp_path / "runs_plain_zh", slug="plain-zh", source_lang="zh")
    assert len(chs_plain_zh) == 1 and chs_plain_zh[0]["ch_num"] == 1
    assert chs_plain_zh[0]["title"] == "Chương 1"

    # Preface before first chapter uses '# Preface' when source_lang=='en' vs '# 序言' otherwise
    en_preface = tmp_path / "en_preface.txt"
    en_preface.write_text("Author note before chapter one.\n\nChapter 1\n\nBody text.\n", encoding="utf-8")
    chs_pref_en = extract_book(en_preface, tmp_path / "runs_pref_en", slug="pref-en", source_lang="en")
    assert [c["ch_num"] for c in chs_pref_en] == [0, 1]
    assert "# Preface" in (tmp_path / "runs_pref_en" / "ch00" / "source.txt").read_text(encoding="utf-8")

    # Filtered extraction writes only requested chapters to checklist.json
    filtered_dir = tmp_path / "runs_en_filtered"
    extract_book(en_txt, filtered_dir, slug="en-story-filt", source_lang="en", chapters="1")
    filtered_cl = json.loads((filtered_dir / "checklist.json").read_text(encoding="utf-8"))
    assert [c["chapter"] for c in filtered_cl] == ["ch01"]

    # Volume header between Chinese chapters must not mutate the previous chapter's volume
    zh_txt = tmp_path / "zh_vol.txt"
    zh_txt.write_text(
        "第一卷 初入江湖\n第一章 少年\n正文第一回\n第二卷 风云再起\n第二章 远行\n正文第二回\n",
        encoding="utf-8",
    )
    out_zh = tmp_path / "runs_zh"
    chs_zh = extract_book(zh_txt, out_zh, slug="zh-vol", source_lang="zh")
    assert chs_zh[0]["volume"] == "第一卷 初入江湖"
    assert chs_zh[1]["volume"] == "第二卷 风云再起"


def test_p1_8_compilers_preserve_manifest_and_brief_title_and_author(tmp_path: Path, monkeypatch):
    slug = "demo-book"
    book_dir = tmp_path / "books" / slug
    book_dir.mkdir(parents=True)
    (book_dir / "manifest.json").write_text(
        json.dumps({"slug": slug, "title": "The Happy Prince", "author": "Oscar Wilde"}),
        encoding="utf-8",
    )

    run_dir = tmp_path / "runs" / slug
    (run_dir / "ch01").mkdir(parents=True)
    (run_dir / "ch02").mkdir(parents=True)
    (run_dir / "ch01" / "final.txt").write_text(
        "# Chương 1\n\nNội dung **chương một** với *alpha* `code` **** ** `` và Alpha Zulu 42 (1) [2].\n\n"
        "---\n\n### Chú thích\n(1) Ghi chú một\ndòng nối tiếp.\n[2] Ghi chú hai.\n",
        encoding="utf-8",
    )
    (run_dir / "ch02" / "round1_translate.txt").write_text(
        '<segment id="S-0001" type="heading"><original># Ch 2</original><translate># Chương 2</translate></segment>\n'
        '<segment id="S-0002" type="paragraph"><original>Body 2</original><translate>Nội dung hai.</translate></segment>\n',
        encoding="utf-8",
    )
    (run_dir / "checklist.json").write_text(
        json.dumps({
            "book_slug": slug,
            "seed_status": "seeded",
            "chapters": [
                {"chapter": "ch01", "chapter_num": 1, "title": "Ch 1", "status": "done"},
                {"chapter": "ch02", "chapter_num": 2, "title": "Ch 2", "status": "pending"},
            ],
        }),
        encoding="utf-8",
    )
    (run_dir / "book_record.json").write_text(
        json.dumps({
            "book_slug": slug,
            "source_lang": "en",
            "target_lang": "vi",
            "title": "The Happy Prince",
            "brief": {"author": "Oscar Wilde", "genre": "literary", "summary": "S"},
            "book_record": {"terms": [], "names": [], "pronouns": []},
            "terms": [
                {"source": "Alpha", "target": "Anpha"},
                {"source": "Zulu", "target": "Dulu"},
                {"source": "42", "target": "Bốn hai"},
            ],
            "names": [],
            "pronouns": [],
        }),
        encoding="utf-8",
    )

    docx_out = tmp_path / "out.docx"
    DOCXCompiler().compile(run_dir, output_file=docx_out, allow_incomplete=True, include_toc=False)
    doc = docx.Document(str(docx_out))
    doc_text = "\n".join(p.text for p in doc.paragraphs)
    assert "The Happy Prince" in doc_text
    assert "Oscar Wilde" in doc_text
    assert "Tác giả: Tác giả" not in doc_text
    assert "Chương 2" in doc_text
    assert "****" not in doc_text
    assert "**" not in doc_text
    assert "``" not in doc_text
    # With include_toc=False, index 3 paragraph after title page break has no duplicate page break
    assert doc.paragraphs[3].text == "Chương 1"

    epub_comp = EPUBCompiler()
    epub_out = tmp_path / "out.epub"
    epub_comp.compile(run_dir, output_file=epub_out, allow_incomplete=True)
    with zipfile.ZipFile(epub_out, "r") as zf:
        opf = zf.read("OEBPS/content.opf").decode("utf-8")
        assert "The Happy Prince" in opf
        assert "Oscar Wilde" in opf
        assert ">Tác giả<" not in opf
        assert "OEBPS/ch02.xhtml" in zf.namelist()
        ch01_xhtml = zf.read("OEBPS/ch01.xhtml").decode("utf-8")
        assert "Ghi chú một dòng nối tiếp." in ch01_xhtml
        assert ">(1)</a>" in ch01_xhtml and ">[2]</a>" in ch01_xhtml
        index_xhtml = zf.read("OEBPS/index.xhtml").decode("utf-8")
        assert index_xhtml.index(">Z<") < index_xhtml.index(">#<")

    # Audio utterance span wrapping: cursor wrap-around and unmatched utterances
    html_sample = "<p>Alpha  &amp;  Omega.</p><p>Second sentence.</p>"
    anchored, matched_ids = epub_comp._inject_utterance_span_anchors(
        html_sample,
        [
            {"utt_id": "u2", "display_text": "Second sentence."},
            {"utt_id": "u1", "display_text": "Alpha & Omega."},
            {"utt_id": "u_missing", "display_text": "Totally absent text"},
        ],
        return_matched_set=True,
    )
    assert matched_ids == {"u1", "u2"}
    assert 'id="u1"' in anchored and 'id="u2"' in anchored and 'id="u_missing"' not in anchored

    # default_compile_book passes manifest title/author into compile_book_formats
    cfg_p = tmp_path / "cfg.json"
    cfg_p.write_text(
        json.dumps({
            "workspace_dir": str(tmp_path),
            "repo_full_name": "vinhthang/collab-book-translate",
            "bot_login": "1quachuoi9vang",
            "bot_git_name": "1quachuoi9vang",
            "bot_git_email": "bot@example.com",
            "human_approver": "vinhthang",
            "fallback_hours": 48.0,
            "max_revise_rounds": 3,
            "release_formats": "epub,docx",
            "max_source_bytes": 5000000,
            "allowed_licenses": ["public-domain"],
            "supported_pairs": [["en", "vi"]],
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("COLLAB_CONFIG_PATH", str(cfg_p))
    seen_compile_kwargs: Dict[str, Any] = {}

    def spy_compile(**kwargs):
        seen_compile_kwargs.update(kwargs)
        return {"epub": epub_out}

    with patch("scripts.compile_book.compile_book_formats", side_effect=spy_compile):
        default_compile_book(tmp_path, slug)
    assert seen_compile_kwargs.get("title") == "The Happy Prince"
    assert seen_compile_kwargs.get("author") == "Oscar Wilde"
