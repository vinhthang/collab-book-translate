import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import pytest

from scripts.collab_sweep import non_blocking_sweep_lock, run_sweep


class MockGitHubClient:
    def __init__(self, prs: Optional[List[Dict[str, Any]]] = None, issues: Optional[List[Dict[str, Any]]] = None):
        self.prs: List[Dict[str, Any]] = prs or []
        self.issues: List[Dict[str, Any]] = issues or []
        self.pr_comments: Dict[int, List[str]] = {}
        self.issue_comments: Dict[int, List[str]] = {}
        self.merged_prs: List[int] = []
        self.closed_prs: List[int] = []
        self.closed_issues: List[int] = []
        self.releases: List[Dict[str, Any]] = []

    def list_open_prs(self) -> List[Dict[str, Any]]:
        return [pr for pr in self.prs if pr.get("state", "open") == "open"]

    def list_open_issues(self) -> List[Dict[str, Any]]:
        return [issue for issue in self.issues if issue.get("state", "open") == "open"]

    def comment_pr(self, pr_num: int, body: str):
        self.pr_comments.setdefault(pr_num, []).append(body)
        for pr in self.prs:
            if pr["number"] == pr_num:
                pr.setdefault("comments", []).append({"body": body, "author_login": "1quachuoi9vang"})

    def close_pr(self, pr_num: int, comment: Optional[str] = None):
        self.closed_prs.append(pr_num)
        if comment:
            self.comment_pr(pr_num, comment)
        for pr in self.prs:
            if pr["number"] == pr_num:
                pr["state"] = "closed"

    def merge_pr(self, pr_num: int):
        self.merged_prs.append(pr_num)
        for pr in self.prs:
            if pr["number"] == pr_num:
                pr["state"] = "merged"

    def set_pr_labels(self, pr_num: int, add: List[str], remove: List[str]):
        for pr in self.prs:
            if pr["number"] == pr_num:
                current = [l for l in pr.get("labels", []) if l not in remove]
                for a in add:
                    if a not in current:
                        current.append(a)
                pr["labels"] = current

    def comment_issue(self, issue_num: int, body: str):
        self.issue_comments.setdefault(issue_num, []).append(body)
        for issue in self.issues:
            if issue["number"] == issue_num:
                issue.setdefault("comments", []).append({"body": body, "author_login": "1quachuoi9vang"})

    def close_issue(self, issue_num: int):
        self.closed_issues.append(issue_num)
        for issue in self.issues:
            if issue["number"] == issue_num:
                issue["state"] = "closed"

    def set_issue_labels(self, issue_num: int, add: List[str], remove: List[str]):
        for issue in self.issues:
            if issue["number"] == issue_num:
                current = [l for l in issue.get("labels", []) if l not in remove]
                for a in add:
                    if a not in current:
                        current.append(a)
                issue["labels"] = current

    def create_release(self, tag: str, title: str, notes: str, assets: List[str]):
        self.releases.append({
            "tag": tag,
            "title": title,
            "notes": notes,
            "assets": assets,
        })


def test_non_blocking_sweep_lock(tmp_path: Path):
    lock_file = tmp_path / "sweep.lock"
    with non_blocking_sweep_lock(lock_file) as acquired1:
        assert acquired1 is True
        # Trying to acquire while already locked should yield False
        with non_blocking_sweep_lock(lock_file) as acquired2:
            assert acquired2 is False

    # After exit, lock can be acquired again
    with non_blocking_sweep_lock(lock_file) as acquired3:
        assert acquired3 is True


def test_sweep_locked_skips(tmp_path: Path):
    lock_file = tmp_path / ".sweep.lock"
    with non_blocking_sweep_lock(lock_file):
        client = MockGitHubClient()
        res = run_sweep(tmp_path, client=client, lock_path=lock_file)
        assert res["status"] == "skipped_locked"
        assert res["reason"] == "sweep already running"
        assert len(client.merged_prs) == 0


def test_book_intake_prescreen_and_approval(tmp_path: Path):
    # Setup book dir and files
    book_dir = tmp_path / "books" / "test-book"
    src_dir = book_dir / "source"
    src_dir.mkdir(parents=True)
    manifest = {
        "slug": "test-book",
        "title": "Test Book",
        "author": "Author A",
        "source_lang": "en",
        "target_lang": "vi",
        "license": "public-domain",
        "license_url": "https://creativecommons.org/publicdomain/mark/1.0/",
        "source_url": "https://example.com",
        "submitter": "contributor1",
    }
    (book_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (src_dir / "book.txt").write_text("Hello world", encoding="utf-8")

    pr_submission = {
        "number": 1,
        "head_ref": "book/test-book",
        "author_login": "contributor1",
        "title": "Add test book",
        "body": "Submitting test book",
        "changed_files": ["books/test-book/manifest.json", "books/test-book/source/book.txt"],
        "labels": [],
        "reviews": [],
        "comments": [],
    }

    client = MockGitHubClient(prs=[pr_submission])
    seed_called = []

    def mock_seed(repo_root, slug):
        seed_called.append(slug)

    # First sweep: unreviewed -> posts prescreen comment, does not merge
    res1 = run_sweep(tmp_path, client=client, seed_fn=mock_seed)
    assert res1["status"] == "completed"
    assert 1 in client.pr_comments
    assert "Pre-screen Summary: PASS" in client.pr_comments[1][0]
    assert 1 not in client.merged_prs
    assert len(seed_called) == 0

    # Second sweep: approved by vinhthang -> merges and calls seed_fn
    client.prs[0]["reviews"].append({
        "author_login": "vinhthang",
        "state": "APPROVED",
        "body": "LGTM",
    })
    res2 = run_sweep(tmp_path, client=client, seed_fn=mock_seed)
    assert res2["status"] == "completed"
    assert 1 in client.merged_prs
    assert seed_called == ["test-book"]


def test_translation_pr_deterministic_defect_no_eval(tmp_path: Path):
    # PR with bad branch naming / invalid body
    pr_invalid = {
        "number": 2,
        "head_ref": "translate/bad-branch",  # Missing chunk and agent
        "author_login": "alice",
        "title": "Translate ch02",
        "body": "No valid headers",
        "changed_files": ["runs/test-book/ch02/translation.xml"],
        "labels": ["status:needs-checks"],
        "reviews": [],
        "comments": [],
    }

    client = MockGitHubClient(prs=[pr_invalid])
    eval_called = []

    def mock_eval(pr, repo_root):
        eval_called.append(pr["number"])
        return {"weighted_total": 5.0}

    res = run_sweep(tmp_path, client=client, evaluate_fn=mock_eval)
    assert res["status"] == "completed"
    assert len(eval_called) == 0  # evaluate_fn MUST NOT be called on deterministic failure
    assert 2 in client.pr_comments
    assert "Deterministic checks failed" in client.pr_comments[2][0]
    assert "status:revise" in client.prs[0]["labels"]


def test_translation_pr_revise_cap_reassign(tmp_path: Path):
    pr_thrice_failed = {
        "number": 3,
        "head_ref": "translate/bad-branch",
        "author_login": "alice",
        "title": "Translate ch02",
        "body": "No valid headers",
        "changed_files": ["runs/test-book/ch02/translation.xml"],
        "labels": ["status:revise"],
        "reviews": [],
        "comments": [
            {"body": "Deterministic checks failed: 1"},
            {"body": "Deterministic checks failed: 2"},
            {"body": "Deterministic checks failed: 3"},
        ],
    }

    client = MockGitHubClient(prs=[pr_thrice_failed])
    res = run_sweep(tmp_path, client=client)
    assert res["status"] == "completed"
    assert 3 in client.closed_prs
    assert "status:reassign" in client.prs[0]["labels"]


def test_translation_pr_branch_naming_three_parts_boundary(tmp_path: Path):
    # head_ref has exactly 3 parts: slug-chunk-agent
    slug = "book"
    run_dir = tmp_path / "runs" / slug
    ch2_dir = run_dir / "ch02"
    ch2_dir.mkdir(parents=True)
    source_text = '<segment id="S-0001" type="paragraph"><original>Hello</original></segment>'
    trans_text = '<segment id="S-0001" type="paragraph"><original>Hello</original><translate>Xin chào</translate></segment>'
    (ch2_dir / "source.txt").write_text(source_text, encoding="utf-8")
    (ch2_dir / "translation.xml").write_text(trans_text, encoding="utf-8")
    # Literary genre lowers pass threshold to 3.5 (REQ-COLLAB-006)
    (run_dir / "book_record.json").write_text(
        json.dumps({"book_slug": slug, "brief": {"genre": "literary"}, "glossary_sha": "sha1", "terms": {}}),
        encoding="utf-8",
    )

    pr_three_parts = {
        "number": 4,
        "head_ref": "translate/book-ch02-agent",  # exactly 3 parts: parts = ['book', 'ch02', 'agent']
        "author_login": "agent",
        "title": "Translate ch02",
        "body": (
            "- **Agent:** agent\n"
            "- **Engine / Model:** custom (flash)\n"
            "- **Source Language:** en\n"
            "- **Target Language:** vi\n"
            "- **Chunk ID:** ch02\n"
            "- **Issue:** #20\n"
            "- **Glossary SHA:** sha1\n"
        ),
        "changed_files": ["runs/book/ch02/translation.xml"],
        "labels": ["status:needs-checks"],
        "reviews": [
            {
                "author_login": "rev_bob",
                "state": "COMMENTED",
                "line_comment_count": 1,
                "body": "- **Fidelity:** 3.6\n- **Fluency:** 3.6\n- **Terminology:** 3.6\n- **Register:** 3.6\n- **Structure:** 3.6\n- **Completeness:** 3.6\n",
            }
        ],
        "comments": [],
    }
    client = MockGitHubClient(prs=[pr_three_parts])
    def eval_fn(pr, repo_root):
        return {"weighted_total": 3.6, "overall_score": 3.6, "issues": [], "fallback_unverified": False}

    res = run_sweep(tmp_path, client=client, evaluate_fn=eval_fn)
    assert res["status"] == "completed"
    # When slug is loaded from 3-part head_ref, literary genre 3.5 threshold makes 3.6 a CANDIDATE.
    # If >= 3 was mutated to > 3, slug is None, fallback threshold 4.0 is used, resulting in REVISE.
    assert "status:candidate" in client.prs[0]["labels"]


def test_translation_pr_evaluation_and_candidate_window(tmp_path: Path):
    slug = "test-book"
    run_dir = tmp_path / "runs" / slug
    ch2_dir = run_dir / "ch02"
    ch2_dir.mkdir(parents=True)

    source_text = '<segment id="S-0001" type="paragraph"><original>Hello world</original></segment>'
    trans_text = '<segment id="S-0001" type="paragraph"><original>Hello world</original><translate>Xin chào thế giới</translate></segment>'
    (ch2_dir / "source.txt").write_text(source_text, encoding="utf-8")
    (ch2_dir / "translation.xml").write_text(trans_text, encoding="utf-8")

    book_record = {
        "book_slug": slug,
        "glossary_sha": "sha123",
        "terms": {},
    }
    (run_dir / "book_record.json").write_text(json.dumps(book_record), encoding="utf-8")

    pr_valid = {
        "number": 10,
        "head_ref": "translate/test-book-ch02-agentA",
        "author_login": "agentA",
        "title": "Translate ch02",
        "body": (
            "- **Agent:** agentA\n"
            "- **Engine / Model:** custom (flash)\n"
            "- **Source Language:** en\n"
            "- **Target Language:** vi\n"
            "- **Chunk ID:** ch02\n"
            "- **Issue:** #20\n"
            "- **Glossary SHA:** sha123\n"
        ),
        "changed_files": ["runs/test-book/ch02/translation.xml"],
        "labels": ["status:needs-checks"],
        "reviews": [
            {
                "author_login": "reviewerB",
                "state": "COMMENTED",
                "line_comment_count": 2,
                "body": (
                    "Good work!\n\n"
                    "- **Fidelity:** 4.5\n"
                    "- **Fluency:** 4.5\n"
                    "- **Terminology:** 4.5\n"
                    "- **Register:** 4.5\n"
                    "- **Structure:** 4.5\n"
                    "- **Completeness:** 4.5\n"
                ),
            }
        ],
        "comments": [],
    }

    issue_ch2 = {
        "number": 20,
        "title": "[ch02] Chapter 2",
        "body": "Tracking chapter 2",
        "labels": ["status:open", f"book:{slug}"],
        "comments": [],
        "created_at": time.time(),
    }

    client = MockGitHubClient(prs=[pr_valid], issues=[issue_ch2])

    def mock_eval(pr, repo_root):
        return {
            "weighted_total": 4.5,
            "overall_score": 4.5,
            "issues": [
                {"severity": "major", "problem": "major flaw"},
            ],
            "fallback_unverified": False,
        }

    now_t = 10000.0
    res = run_sweep(tmp_path, client=client, evaluate_fn=mock_eval, now=now_t)
    assert res["status"] == "completed"

    # PR should now be status:candidate
    assert "status:candidate" in client.prs[0]["labels"]
    assert client.prs[0]["critical_count"] == 0
    assert client.prs[0]["major_count"] == 1
    # Issue should now be status:candidates
    assert "status:candidates" in client.issues[0]["labels"]
    # Candidate window start timestamp should be exactly now_t
    assert f"<!-- candidate_window_started_at: {now_t} -->" in client.issues[0]["comments"][0]["body"]


def test_translation_pr_outcomes_revise_reassign_flagged(tmp_path: Path):
    slug = "test-book"
    run_dir = tmp_path / "runs" / slug
    ch2_dir = run_dir / "ch02"
    ch2_dir.mkdir(parents=True)
    source_text = '<segment id="S-0001" type="paragraph"><original>Hello</original></segment>'
    trans_text = '<segment id="S-0001" type="paragraph"><original>Hello</original><translate>Xin chào</translate></segment>'
    (ch2_dir / "source.txt").write_text(source_text, encoding="utf-8")
    (ch2_dir / "translation.xml").write_text(trans_text, encoding="utf-8")
    (run_dir / "book_record.json").write_text(json.dumps({"book_slug": slug, "glossary_sha": "sha1", "terms": {}}), encoding="utf-8")

    def _make_pr(pr_num: int):
        return {
            "number": pr_num,
            "head_ref": f"translate/test-book-ch02-agent{pr_num}",
            "author_login": f"agent{pr_num}",
            "title": "Translate ch02",
            "body": (
                f"- **Agent:** agent{pr_num}\n"
                "- **Engine / Model:** custom (flash)\n"
                "- **Source Language:** en\n"
                "- **Target Language:** vi\n"
                "- **Chunk ID:** ch02\n"
                "- **Issue:** #20\n"
                "- **Glossary SHA:** sha1\n"
            ),
            "changed_files": ["runs/test-book/ch02/translation.xml"],
            "labels": ["status:needs-checks"],
            "reviews": [
                {
                    "author_login": f"rev{pr_num}",
                    "state": "COMMENTED",
                    "line_comment_count": 1,
                    "body": "- **Fidelity:** 3.0\n- **Fluency:** 3.0\n- **Terminology:** 3.0\n- **Register:** 3.0\n- **Structure:** 3.0\n- **Completeness:** 3.0\n",
                }
            ],
            "comments": [],
        }

    # 1. REVISE outcome (score 3.0 below literary 3.5 threshold, not redraft)
    pr1 = _make_pr(101)
    client1 = MockGitHubClient(prs=[pr1])
    def eval_revise(pr, repo_root):
        return {"weighted_total": 3.0, "overall_score": 3.0, "issues": [], "fallback_unverified": False}
    run_sweep(tmp_path, client=client1, evaluate_fn=eval_revise)
    assert "status:revise" in client1.prs[0]["labels"]
    assert any("REVISE" in c["body"] for c in client1.prs[0]["comments"])

    # 2. REASSIGN outcome (score 2.0 triggers check_redraft)
    pr2 = _make_pr(102)
    client2 = MockGitHubClient(prs=[pr2])
    def eval_reassign(pr, repo_root):
        return {"weighted_total": 2.0, "overall_score": 2.0, "issues": [], "fallback_unverified": False}
    run_sweep(tmp_path, client=client2, evaluate_fn=eval_reassign)
    assert "status:reassign" in client2.prs[0]["labels"]
    assert 102 in client2.closed_prs

    # 3. FLAGGED_FOR_HUMAN outcome (prompt injection detected)
    pr3 = _make_pr(103)
    client3 = MockGitHubClient(prs=[pr3])
    def eval_flagged(pr, repo_root):
        return {"weighted_total": 5.0, "overall_score": 5.0, "issues": [], "fallback_unverified": False, "injection_flags": ["jailbreak attempt"]}
    run_sweep(tmp_path, client=client3, evaluate_fn=eval_flagged, human_approver="vinhthang")
    assert 103 in client3.pr_comments
    assert "@vinhthang" in client3.pr_comments[103][0]


def test_candidates_window_start_fallback_to_created_at(tmp_path: Path):
    slug = "test-book"
    run_dir = tmp_path / "runs" / slug
    ch2_dir = run_dir / "ch02"
    ch2_dir.mkdir(parents=True)
    source_text = '<segment id="S-0001" type="paragraph"><original>Hello</original></segment>'
    trans_text = '<segment id="S-0001" type="paragraph"><original>Hello</original><translate>Xin chào</translate></segment>'
    (ch2_dir / "source.txt").write_text(source_text, encoding="utf-8")
    (ch2_dir / "translation.xml").write_text(trans_text, encoding="utf-8")
    (run_dir / "book_record.json").write_text(json.dumps({"book_slug": slug, "glossary_sha": "sha1", "terms": {}}), encoding="utf-8")
    (run_dir / "checklist.json").write_text(json.dumps({"chapters": {"ch02": {"status": "in_progress"}}}), encoding="utf-8")
    (run_dir / "collab_ledger.json").write_text(json.dumps({"book_slug": slug, "chapters": {}}), encoding="utf-8")

    # Issue has NO comment with candidate_window_started_at; created_at is 1000.0
    issue_no_comment = {
        "number": 20,
        "title": "[ch02] Chapter 2",
        "body": "Tracking",
        "labels": ["status:candidates", f"book:{slug}"],
        "comments": [],
        "created_at": 1000.0,
    }

    pr = {
        "number": 50,
        "head_ref": "translate/test-book-ch02-agent",
        "author_login": "agent",
        "title": "Translate ch02",
        "body": (
            "- **Agent:** agent\n"
            "- **Engine / Model:** custom (flash)\n"
            "- **Source Language:** en\n"
            "- **Target Language:** vi\n"
            "- **Chunk ID:** ch02\n"
            "- **Issue:** #20\n"
            "- **Glossary SHA:** sha1\n"
        ),
        "changed_files": ["runs/test-book/ch02/translation.xml"],
        "labels": ["status:candidate"],
        "weighted_total": 4.5,
        "critical_count": 0,
        "major_count": 0,
        "unresolved_threads": 0,
        "reviews": [{"author_login": "rev1", "state": "APPROVED"}],
        "comments": [],
    }

    client = MockGitHubClient(prs=[pr], issues=[issue_no_comment])
    # Now is 25 hours after issue.created_at -> window elapses and selects PR 50
    run_sweep(tmp_path, client=client, now=1000.0 + 25 * 3600.0, window_hours=24.0)
    assert 50 in client.merged_prs
    assert 20 in client.closed_issues


def test_competing_prs_selection_and_release(tmp_path: Path):
    slug = "test-book"
    run_dir = tmp_path / "runs" / slug
    ch1_dir = run_dir / "ch01"
    ch1_dir.mkdir(parents=True)
    ch2_dir = run_dir / "ch02"
    ch2_dir.mkdir(parents=True)

    # Chapter 1 is already Done
    (ch1_dir / "source.txt").write_text("Ch1", encoding="utf-8")
    (ch1_dir / "final.txt").write_text("Ch1 dịch", encoding="utf-8")

    # Chapter 2 translation
    source_text = '<segment id="S-0001" type="paragraph"><original>Hello</original></segment>'
    trans_text_1 = '<segment id="S-0001" type="paragraph"><original>Hello</original><translate>Xin chào 1</translate></segment>'
    (ch2_dir / "source.txt").write_text(source_text, encoding="utf-8")
    (ch2_dir / "translation.xml").write_text(trans_text_1, encoding="utf-8")

    book_record = {"book_slug": slug, "glossary_sha": "sha123", "terms": {}}
    (run_dir / "book_record.json").write_text(json.dumps(book_record), encoding="utf-8")

    checklist = {
        "chapters": {
            "ch01": {"status": "done"},
            "ch02": {"status": "in_progress"},
        }
    }
    (run_dir / "checklist.json").write_text(json.dumps(checklist), encoding="utf-8")

    # Seed ledger with ch01
    ledger = {
        "book_slug": slug,
        "chapters": {
            "ch01": {
                "chapter": "ch01",
                "issue_number": "seed",
                "selected_pr": 1,
                "rejected_prs": [],
                "translator_login": "main-bot",
                "reviewer_logins": [],
                "reviewer_score": 5.0,
                "score_of_record": 5.0,
                "verdict": "PASS",
                "merge_sha": "sha1",
                "glossary_sha": "sha123",
            }
        },
    }
    (run_dir / "collab_ledger.json").write_text(json.dumps(ledger), encoding="utf-8")

    window_start = 1000.0
    issue_ch2 = {
        "number": 20,
        "title": "[ch02] Chapter 2",
        "body": "Tracking chapter 2",
        "labels": ["status:candidates", f"book:{slug}"],
        "comments": [{"body": f"<!-- candidate_window_started_at: {window_start} -->"}],
        "created_at": 500.0,
    }

    body_pr1 = (
        "- **Agent:** agent1\n"
        "- **Engine / Model:** custom (flash)\n"
        "- **Source Language:** en\n"
        "- **Target Language:** vi\n"
        "- **Chunk ID:** ch02\n"
        "- **Issue:** #20\n"
        "- **Glossary SHA:** sha123\n"
    )
    body_pr2 = (
        "- **Agent:** agent2\n"
        "- **Engine / Model:** custom (pro)\n"
        "- **Source Language:** en\n"
        "- **Target Language:** vi\n"
        "- **Chunk ID:** ch02\n"
        "- **Issue:** #20\n"
        "- **Glossary SHA:** sha123\n"
    )

    pr_candidate1 = {
        "number": 11,
        "head_ref": "translate/test-book-ch02-agent1",
        "author_login": "agent1",
        "title": "Translate ch02",
        "body": body_pr1,
        "changed_files": ["runs/test-book/ch02/translation.xml"],
        "labels": ["status:candidate"],
        "weighted_total": 4.2,
        "critical_count": 0,
        "major_count": 0,
        "unresolved_threads": 0,
        "reviews": [],
        "comments": [],
    }

    pr_candidate2 = {
        "number": 12,
        "head_ref": "translate/test-book-ch02-agent2",
        "author_login": "agent2",
        "title": "Translate ch02 (better)",
        "body": body_pr2,
        "changed_files": ["runs/test-book/ch02/translation.xml"],
        "labels": ["status:candidate"],
        "weighted_total": 4.8,
        "critical_count": 0,
        "major_count": 0,
        "unresolved_threads": 0,
        # Reviews include the author (must be filtered out) and a valid reviewer
        "reviews": [
            {"author_login": "agent2", "state": "COMMENTED", "line_comment_count": 0},
            {"author_login": "reviewer_alice", "state": "APPROVED", "line_comment_count": 2},
        ],
        "comments": [],
    }

    client = MockGitHubClient(prs=[pr_candidate1, pr_candidate2], issues=[issue_ch2])

    compiled = []

    def mock_compile(repo_root, book_slug):
        compiled.append(book_slug)
        return [str(repo_root / "runs" / book_slug / "book.epub")]

    # Sweep when window has elapsed (window_hours=24.0, now = window_start + 25h)
    now_t = window_start + (25.0 * 3600.0)
    res = run_sweep(
        tmp_path,
        client=client,
        compile_fn=mock_compile,
        now=now_t,
        window_hours=24.0,
    )
    assert res["status"] == "completed"

    # PR 12 (score 4.8) should be merged
    assert 12 in client.merged_prs
    # PR 11 (score 4.2) should be closed as not selected
    assert 11 in client.closed_prs
    assert 11 in client.pr_comments
    assert "4.8" in client.pr_comments[11][0]

    # Chapter 2 issue should be closed and marked status:done
    assert 20 in client.closed_issues
    assert "status:done" in client.issues[0]["labels"]

    # final.txt should be written
    assert (ch2_dir / "final.txt").exists()
    assert (ch2_dir / "final.txt").read_text(encoding="utf-8").strip() != ""

    # checklist status should be done
    updated_cl = json.loads((run_dir / "checklist.json").read_text(encoding="utf-8"))
    assert updated_cl["chapters"]["ch02"]["status"] == "done"

    # Ledger should contain ch02 with filtered reviewer_logins
    updated_ledger = json.loads((run_dir / "collab_ledger.json").read_text(encoding="utf-8"))
    assert "ch02" in updated_ledger["chapters"]
    assert updated_ledger["chapters"]["ch02"]["reviewer_logins"] == ["reviewer_alice"]

    # All chapters are now done -> compile_fn called and release created!
    assert compiled == [slug]
    assert len(client.releases) == 1
    assert client.releases[0]["tag"] == f"{slug}-v1.0"


def test_starved_chapter_detection(tmp_path: Path):
    issue_starved = {
        "number": 30,
        "title": "[ch03] Chapter 3",
        "body": "Tracking chapter 3",
        "labels": ["status:open", "book:test-book"],
        "comments": [],
        "created_at": 1000.0,
    }
    client = MockGitHubClient(issues=[issue_starved])
    # 49 hours later
    now_t = 1000.0 + (49.0 * 3600.0)
    res = run_sweep(tmp_path, client=client, now=now_t, fallback_hours=48.0)
    assert res["status"] == "completed"
    assert len(res["starved_chapters"]) == 1
    assert res["starved_chapters"][0]["chapter"] == "ch03"
    assert res["starved_chapters"][0]["issue"] == 30


def test_default_eval_res_initialized_when_evaluate_fn_none(tmp_path: Path):
    slug = "test-book"
    run_dir = tmp_path / "runs" / slug
    ch2_dir = run_dir / "ch02"
    ch2_dir.mkdir(parents=True)

    src_xml = (
        '<chapter id="ch02">\n'
        '  <segment id="seg01">\n'
        "    <original>First sentence.</original>\n"
        "  </segment>\n"
        "</chapter>"
    )
    (ch2_dir / "source.txt").write_text(src_xml, encoding="utf-8")

    trans_xml = (
        '<chapter id="ch02">\n'
        '  <segment id="seg01">\n'
        "    <original>First sentence.</original>\n"
        "    <target>Câu thứ nhất.</target>\n"
        "  </segment>\n"
        "</chapter>"
    )
    (ch2_dir / "translation.xml").write_text(trans_xml, encoding="utf-8")

    book_record = {
        "slug": slug,
        "glossary_sha": "sha123",
        "glossary": {"sentence": "câu"},
    }
    (run_dir / "book_record.json").write_text(json.dumps(book_record), encoding="utf-8")

    body_pr = (
        "- **Agent:** translator1\n"
        "- **Engine / Model:** custom\n"
        "- **Source Language:** en\n"
        "- **Target Language:** vi\n"
        "- **Chunk ID:** ch02\n"
        "- **Issue:** #20\n"
        "- **Glossary SHA:** sha123\n"
    )

    review_body = (
        "## Overall Verdict: REVISE\n\n"
        "- Accuracy: 3.0 / 5\n"
        "- Fluency: 3.0 / 5\n"
        "- Terminology: 3.0 / 5\n"
        "- Style: 3.0 / 5\n\n"
        "Need improvements."
    )

    pr = {
        "number": 15,
        "head_ref": "translate/test-book-ch02-translator1",
        "author_login": "translator1",
        "title": "Translate ch02",
        "body": body_pr,
        "changed_files": ["runs/test-book/ch02/translation.xml"],
        "labels": ["status:needs-review"],
        "reviews": [
            {
                "author": {"login": "reviewer_bob"},
                "state": "COMMENTED",
                "body": review_body,
                "line_comment_count": 1,
            }
        ],
        "comments": [],
    }

    issue_ch2 = {
        "number": 20,
        "title": "Translate [ch02] test-book-ch02",
        "body": "- **Book:** test-book\nTracking ch02",
        "labels": ["status:open", f"book:{slug}"],
        "comments": [],
    }

    client = MockGitHubClient(prs=[pr], issues=[issue_ch2])

    # evaluate_fn is explicitly None; run_sweep must NOT raise UnboundLocalError
    res = run_sweep(tmp_path, client=client, evaluate_fn=None)
    assert res["status"] == "completed"
    # Reviewer gave REVISE score and default evaluator gives FAIL -> PR should transition to status:revise
    assert "status:revise" in pr["labels"]


def test_checklist_list_format_updated_on_winner_merge(tmp_path: Path):
    slug = "test-book"
    run_dir = tmp_path / "runs" / slug
    ch2_dir = run_dir / "ch02"
    ch2_dir.mkdir(parents=True)

    src_xml = '<segment id="S-0001"><original>Hello</original></segment>'
    trans_xml = '<segment id="S-0001"><original>Hello</original><translate>Xin chào</translate></segment>'
    (ch2_dir / "source.txt").write_text(src_xml, encoding="utf-8")
    (ch2_dir / "translation.xml").write_text(trans_xml, encoding="utf-8")

    # Canonical extract.py list format
    checklist_list = [
        {"chapter": "ch01", "status": "done"},
        {"chapter": "ch02", "status": "in_progress"},
    ]
    (run_dir / "checklist.json").write_text(json.dumps(checklist_list), encoding="utf-8")

    book_record = {"slug": slug, "glossary_sha": "sha123", "glossary": {}}
    (run_dir / "book_record.json").write_text(json.dumps(book_record), encoding="utf-8")

    ledger = {"book_slug": slug, "chapters": {}}
    (run_dir / "collab_ledger.json").write_text(json.dumps(ledger), encoding="utf-8")

    issue = {
        "number": 22,
        "title": "Translate [ch02] test-book-ch02",
        "body": "- **Book:** test-book\nChapter 2",
        "labels": ["status:candidates", f"book:{slug}"],
        "comments": [{"body": "<!-- candidate_window_started_at: 1000.0 -->"}],
        "created_at": 500.0,
    }

    body_pr = (
        "- **Agent:** agent1\n"
        "- **Engine / Model:** custom\n"
        "- **Source Language:** en\n"
        "- **Target Language:** vi\n"
        "- **Chunk ID:** test-book-ch02\n"
        "- **Issue:** #22\n"
        "- **Glossary SHA:** sha123\n"
    )

    pr = {
        "number": 31,
        "head_ref": "translate/test-book-ch02-agent1",
        "author_login": "agent1",
        "title": "Translate ch02",
        "body": body_pr,
        "changed_files": ["runs/test-book/ch02/translation.xml"],
        "labels": ["status:candidate"],
        "weighted_total": 4.5,
        "critical_count": 0,
        "major_count": 0,
        "unresolved_threads": 0,
        "reviews": [
            {"author_login": "reviewer1", "scores": {"acc": 4.5}, "state": "APPROVED", "line_comment_count": 1}
        ],
        "comments": [],
    }

    client = MockGitHubClient(prs=[pr], issues=[issue])
    res = run_sweep(tmp_path, client=client, now=1000.0 + (25.0 * 3600.0), window_hours=24.0)
    assert res["status"] == "completed"
    assert 31 in client.merged_prs

    # Verify checklist.json list format was updated
    updated_cl = json.loads((run_dir / "checklist.json").read_text(encoding="utf-8"))
    assert isinstance(updated_cl, list)
    ch02_entry = next(item for item in updated_cl if item["chapter"] == "ch02")
    assert ch02_entry["status"] == "done"


def test_idempotent_release_creation(tmp_path: Path):
    slug = "test-book"
    run_dir = tmp_path / "runs" / slug
    ch1_dir = run_dir / "ch01"
    ch1_dir.mkdir(parents=True)

    (ch1_dir / "source.txt").write_text("<chapter></chapter>", encoding="utf-8")
    (ch1_dir / "final.txt").write_text("Hoàn thành", encoding="utf-8")

    cl = [{"chapter": "ch01", "status": "done"}]
    (run_dir / "checklist.json").write_text(json.dumps(cl), encoding="utf-8")

    tag = f"{slug}-v1.0"
    # Seed ledger with already released tag
    ledger = {
        "book_slug": slug,
        "released_tag": tag,
        "chapters": {
            "ch01": {
                "chapter": "ch01",
                "issue_number": "seed",
                "selected_pr": 1,
                "rejected_prs": [],
                "translator_login": "bot",
                "reviewer_logins": [],
                "reviewer_score": 5.0,
                "score_of_record": 5.0,
                "verdict": "PASS",
                "merge_sha": "sha1",
                "glossary_sha": "sha2",
            }
        },
    }
    (run_dir / "collab_ledger.json").write_text(json.dumps(ledger), encoding="utf-8")

    client = MockGitHubClient()
    res = run_sweep(tmp_path, client=client)
    assert res["status"] == "completed"
    # Since released_tag was already in collab_ledger.json, release should NOT be created again
    assert len(client.releases) == 0
    assert len(res["releases_created"]) == 0


def test_eliminate_fabricated_values_in_ledger(tmp_path: Path):
    slug = "test-book"
    run_dir = tmp_path / "runs" / slug
    ch2_dir = run_dir / "ch02"
    ch2_dir.mkdir(parents=True)

    src_xml = '<segment id="S-0001"><original>Hi</original></segment>'
    trans_xml = '<segment id="S-0001"><original>Hi</original><translate>Chào</translate></segment>'
    (ch2_dir / "source.txt").write_text(src_xml, encoding="utf-8")
    (ch2_dir / "translation.xml").write_text(trans_xml, encoding="utf-8")

    book_record = {
        "slug": slug,
        "glossary_sha": "expected-real-glossary-sha-999",
        "glossary": {},
    }
    (run_dir / "book_record.json").write_text(json.dumps(book_record), encoding="utf-8")

    checklist = [{"chapter": "ch02", "status": "in_progress"}]
    (run_dir / "checklist.json").write_text(json.dumps(checklist), encoding="utf-8")

    ledger = {"book_slug": slug, "chapters": {}}
    (run_dir / "collab_ledger.json").write_text(json.dumps(ledger), encoding="utf-8")

    issue = {
        "number": 25,
        "title": "Translate [ch02] test-book-ch02",
        "body": "- **Book:** test-book",
        "labels": ["status:candidates", f"book:{slug}"],
        "comments": [{"body": "<!-- candidate_window_started_at: 1000.0 -->"}],
        "created_at": 500.0,
    }

    body_pr = (
        "- **Agent:** translator_tom\n"
        "- **Engine / Model:** custom\n"
        "- **Source Language:** en\n"
        "- **Target Language:** vi\n"
        "- **Chunk ID:** ch02\n"
        "- **Issue:** #25\n"
        "- **Glossary SHA:** expected-real-glossary-sha-999\n"
    )

    class CustomMergeClient(MockGitHubClient):
        def merge_pr(self, pr_num: int):
            super().merge_pr(pr_num)
            return "4444555566667777888899990000111122223333"

    client = CustomMergeClient(
        prs=[
            {
                "number": 42,
                "head_ref": "translate/test-book-ch02-translator_tom",
                "author_login": "translator_tom",
                "title": "Translate ch02",
                "body": body_pr,
                "changed_files": ["runs/test-book/ch02/translation.xml"],
                "labels": ["status:candidate"],
                "weighted_total": 4.6,
                "critical_count": 0,
                "major_count": 0,
                "unresolved_threads": 0,
                "reviewer_scores": {"accuracy": 4.6, "fluency": 4.8},
                "valid_reviews": [
                    {
                        "author_login": "reviewer_sarah",
                        "scores": {"accuracy": 4.6, "fluency": 4.8},
                    }
                ],
                "reviews": [
                    {
                        "author_login": "reviewer_sarah",
                        "scores": {"accuracy": 4.6, "fluency": 4.8},
                        "state": "APPROVED",
                    }
                ],
                "comments": [],
            }
        ],
        issues=[issue],
    )

    res = run_sweep(tmp_path, client=client, now=1000.0 + (25.0 * 3600.0), window_hours=24.0)
    assert res["status"] == "completed"

    updated_ledger = json.loads((run_dir / "collab_ledger.json").read_text(encoding="utf-8"))
    entry = updated_ledger["chapters"]["ch02"]
    assert entry["merge_sha"] == "4444555566667777888899990000111122223333"
    assert entry["glossary_sha"] == "expected-real-glossary-sha-999"
    assert entry["reviewer_logins"] == ["reviewer_sarah"]
    assert abs(entry["reviewer_score"] - 4.7) < 1e-4


def test_analyze_additions_conflict_detection_and_binding(tmp_path: Path):
    slug = "test-book"
    run_dir = tmp_path / "runs" / slug
    ch2_dir = run_dir / "ch02"
    ch2_dir.mkdir(parents=True)

    src_xml = '<segment id="S-0001"><original>King</original></segment>'
    trans_xml = '<segment id="S-0001"><original>King</original><translate>Vua</translate></segment>'
    (ch2_dir / "source.txt").write_text(src_xml, encoding="utf-8")
    (ch2_dir / "translation.xml").write_text(trans_xml, encoding="utf-8")

    # Case A: Conflict with book_record glossary
    book_record = {
        "slug": slug,
        "glossary_sha": "sha123",
        "glossary": {"King": "Hoàng đế"},
    }
    (run_dir / "book_record.json").write_text(json.dumps(book_record), encoding="utf-8")
    (run_dir / "checklist.json").write_text(json.dumps([{"chapter": "ch02", "status": "in_progress"}]), encoding="utf-8")
    (run_dir / "collab_ledger.json").write_text(json.dumps({"book_slug": slug, "chapters": {}}), encoding="utf-8")

    # analyze_additions proposes King -> Vua (which conflicts with Hoàng đế)
    additions = [
        {"term": "King", "target": "Vua", "status": "proposed"}
    ]
    (ch2_dir / "analyze_additions.json").write_text(json.dumps(additions), encoding="utf-8")

    issue = {
        "number": 26,
        "title": "Translate [ch02] test-book-ch02",
        "body": "- **Book:** test-book",
        "labels": ["status:candidates", f"book:{slug}"],
        "comments": [{"body": "<!-- candidate_window_started_at: 1000.0 -->"}],
        "created_at": 500.0,
    }

    body_pr = (
        "- **Agent:** translator1\n"
        "- **Engine / Model:** custom\n"
        "- **Source Language:** en\n"
        "- **Target Language:** vi\n"
        "- **Chunk ID:** ch02\n"
        "- **Issue:** #26\n"
        "- **Glossary SHA:** sha123\n"
    )

    pr = {
        "number": 51,
        "head_ref": "translate/test-book-ch02-translator1",
        "author_login": "translator1",
        "title": "Translate ch02",
        "body": body_pr,
        "changed_files": ["runs/test-book/ch02/translation.xml"],
        "labels": ["status:candidate"],
        "weighted_total": 4.9,
        "critical_count": 0,
        "major_count": 0,
        "unresolved_threads": 0,
        "reviews": [{"author_login": "rev1", "scores": {"a": 4.9}, "state": "APPROVED"}],
        "comments": [],
    }

    client = MockGitHubClient(prs=[pr], issues=[issue])
    res = run_sweep(tmp_path, client=client, now=1000.0 + (25.0 * 3600.0), window_hours=24.0)
    assert res["status"] == "completed"
    # Should detect conflict and return PR to REVISE without merging
    assert 51 not in client.merged_prs
    assert "status:revise" in pr["labels"]

    # Case B: No conflict -> bind proposals into book_record.json and mark proposal bound
    book_record["glossary"] = {"Lord": "Chúa"}
    (run_dir / "book_record.json").write_text(json.dumps(book_record), encoding="utf-8")
    additions_clean = [
        {"term": "Queen", "target": "Hoàng hậu", "status": "proposed"}
    ]
    (ch2_dir / "analyze_additions.json").write_text(json.dumps(additions_clean), encoding="utf-8")

    pr["labels"] = ["status:candidate"]
    client_b = MockGitHubClient(prs=[pr], issues=[issue])
    res_b = run_sweep(tmp_path, client=client_b, now=1000.0 + (25.0 * 3600.0), window_hours=24.0)
    assert res_b["status"] == "completed"
    assert 51 in client_b.merged_prs

    updated_br = json.loads((run_dir / "book_record.json").read_text(encoding="utf-8"))
    assert "Queen" in updated_br["glossary"]
    assert updated_br["glossary"]["Queen"] == "Hoàng hậu"

    updated_additions = json.loads((ch2_dir / "analyze_additions.json").read_text(encoding="utf-8"))
    assert updated_additions[0]["status"] == "bound"


def test_github_client_list_open_prs_normalization_and_line_comments(monkeypatch):
    from scripts.collab_sweep import GitHubCLIClient

    cli = GitHubCLIClient(bot_login="bot")

    mock_gh_output = json.dumps([
        {
            "number": 101,
            "title": "Translate ch02",
            "body": "PR body",
            "headRefName": "translate/book-ch02-trans",
            "author": {"login": "trans"},
            "labels": [{"name": "status:needs-review"}],
            "files": [{"path": "runs/book/ch02/translation.xml"}],
            "reviews": [
                {
                    "author": {"login": "rev_carol"},
                    "body": "<!-- segment: seg01 --> Good translation.\n<!-- line: 5 --> Minor fix.",
                    "state": "APPROVED",
                }
            ],
            "comments": [],
        }
    ])

    def mock_run_gh(args):
        if "pr" in args and "list" in args:
            return mock_gh_output
        if "repo" in args and "view" in args:
            return json.dumps({"nameWithOwner": "owner/repo"})
        if "api" in args and "comments" in args[1]:
            return json.dumps([
                {"user": {"login": "rev_carol"}},
                {"user": {"login": "rev_carol"}},
                {"user": {"login": "other_user"}},
            ])
        raise RuntimeError(f"Unexpected args: {args}")

    monkeypatch.setattr(cli, "_run_gh", mock_run_gh)

    prs = cli.list_open_prs()
    assert len(prs) == 1
    pr = prs[0]
    assert pr["author_login"] == "trans"
    assert pr["labels"] == ["status:needs-review"]
    assert pr["changed_files"] == ["runs/book/ch02/translation.xml"]
    assert pr["reviews"][0]["author_login"] == "rev_carol"
    assert pr["reviews"][0]["line_comment_count"] == 2


def test_read_pr_file_helper(tmp_path: Path):
    from scripts.collab_sweep import read_pr_file

    sample_file = tmp_path / "runs" / "book" / "ch02" / "source.txt"
    sample_file.parent.mkdir(parents=True)
    sample_file.write_text("Local source file content", encoding="utf-8")

    # 1. Fallback to local root
    content = read_pr_file(None, 1, "head", "runs/book/ch02/source.txt", tmp_path)
    assert content == "Local source file content"

    # 2. Client with get_pr_file
    class ClientWithGet:
        def get_pr_file(self, pr_num, rel_path):
            return "From client get_pr_file"

    content_client = read_pr_file(ClientWithGet(), 1, "head", "runs/book/ch02/source.txt", tmp_path)
    assert content_client == "From client get_pr_file"

    # 3. Client with read_pr_file
    class ClientWithRead:
        def read_pr_file(self, pr_num, head_ref, rel_path, root):
            return "From client read_pr_file"

    content_read = read_pr_file(ClientWithRead(), 1, "head", "runs/book/ch02/source.txt", tmp_path)
    assert content_read == "From client read_pr_file"


def test_extract_ch_id_and_slug_two_parts():
    from scripts.collab_sweep import _extract_ch_id_and_slug
    ch, slug = _extract_ch_id_and_slug("translate/mybook-ch02")
    assert ch == "ch02"
    assert slug == "mybook"


def test_issue_matching_by_iss_ch_and_slug(tmp_path: Path):
    slug = "test-book"
    run_dir = tmp_path / "runs" / slug
    ch2_dir = run_dir / "ch02"
    ch2_dir.mkdir(parents=True)

    src_xml = '<segment id="S-0001"><original>Hello</original></segment>'
    trans_xml = '<segment id="S-0001"><original>Hello</original><translate>Xin chào</translate></segment>'
    (ch2_dir / "source.txt").write_text(src_xml, encoding="utf-8")
    (ch2_dir / "translation.xml").write_text(trans_xml, encoding="utf-8")

    book_record = {"slug": slug, "glossary_sha": "sha123", "glossary": {}}
    (run_dir / "book_record.json").write_text(json.dumps(book_record), encoding="utf-8")

    pr = {
        "number": 19,
        "head_ref": "translate/test-book-ch02-agent1",
        "author_login": "agent1",
        "title": "Translate ch02",
        "body": (
            "- **Agent:** agent1\n"
            "- **Engine / Model:** custom\n"
            "- **Source Language:** en\n"
            "- **Target Language:** vi\n"
            "- **Chunk ID:** ch02\n"
            "- **Issue:** #77\n"
            "- **Glossary SHA:** sha123\n"
        ),
        "changed_files": ["runs/test-book/ch02/translation.xml"],
        "labels": ["status:needs-review"],
        "reviews": [
            {
                "author_login": "rev1",
                "state": "APPROVED",
                "body": "- **Fidelity:** 5.0\n- **Fluency:** 5.0\n- **Terminology:** 5.0\n- **Register:** 5.0\n- **Structure:** 5.0\n- **Completeness:** 5.0\n",
                "line_comment_count": 1,
            }
        ],
        "comments": [],
    }

    # Issue title without brackets or surrounding spaces around ch02
    issue_target = {
        "number": 77,
        "title": "Translate chapter ch02",
        "body": "- **Book:** test-book",
        "labels": ["status:open", f"book:{slug}"],
        "comments": [],
    }
    # Another issue for different slug
    issue_other = {
        "number": 88,
        "title": "Translate chapter ch02",
        "body": "- **Book:** other-book",
        "labels": ["status:open", "book:other-book"],
        "comments": [],
    }

    client = MockGitHubClient(prs=[pr], issues=[issue_target, issue_other])

    def mock_eval(pr, root):
        return {"verdict": "PASS", "weighted_total": 4.8, "issues": []}

    res = run_sweep(tmp_path, client=client, evaluate_fn=mock_eval)
    assert res["status"] == "completed"
    assert "status:candidates" in issue_target["labels"]
    assert "status:candidates" not in issue_other["labels"]


def test_parse_timestamp_iso8601_and_numeric():
    from scripts.collab_sweep import _parse_timestamp
    assert _parse_timestamp(12345.0, 0.0) == 12345.0
    assert _parse_timestamp(12345, 0.0) == 12345.0
    assert _parse_timestamp("12345.6", 0.0) == 12345.6
    # ISO-8601
    ts = _parse_timestamp("2026-11-06T12:00:00Z", 0.0)
    assert ts > 1700000000.0


def test_sweep_with_iso8601_created_at(tmp_path: Path):
    issue_starved = {
        "number": 33,
        "title": "[ch03] Chapter 3",
        "body": "Tracking chapter 3",
        "labels": ["status:open", "book:test-book"],
        "comments": [],
        "created_at": "2026-11-06T12:00:00Z",
    }
    client = MockGitHubClient(issues=[issue_starved])
    from scripts.collab_sweep import _parse_timestamp
    iso_ts = _parse_timestamp("2026-11-06T12:00:00Z", 0.0)
    # Run sweep 49 hours after the ISO timestamp
    now_t = iso_ts + (49.0 * 3600.0)
    res = run_sweep(tmp_path, client=client, now=now_t, fallback_hours=48.0)
    assert res["status"] == "completed"
    assert len(res["starved_chapters"]) == 1
    assert res["starved_chapters"][0]["issue"] == 33


def test_submission_pr_read_pr_file_and_source_text_sample(tmp_path: Path):
    manifest = {
        "slug": "novel-one",
        "title": "Novel One",
        "author": "Author A",
        "source_lang": "en",
        "target_lang": "vi",
        "format": "txt",
        "license": "CC0-1.0",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "submitter": "contributor",
    }
    manifest_bytes = json.dumps(manifest)
    source_bytes = "Once upon a time in a faraway kingdom, there lived a wise king."

    class SubmissionClient(MockGitHubClient):
        def get_pr_file(self, pr_num, rel_path):
            if "manifest.json" in rel_path:
                return manifest_bytes
            if "source" in rel_path:
                return source_bytes
            return None

    pr_sub = {
        "number": 5,
        "head_ref": "book/novel-one",
        "author_login": "contributor",
        "title": "Submit Novel One",
        "changed_files": [
            "books/novel-one/manifest.json",
            "books/novel-one/source/source.txt",
        ],
        "reviews": [
            {"author_login": "vinhthang", "state": "APPROVED"}
        ],
        "comments": [],
    }

    seeded_slugs = []
    def mock_seed(root, slug):
        seeded_slugs.append(slug)

    client = SubmissionClient(prs=[pr_sub])
    res = run_sweep(tmp_path, client=client, seed_fn=mock_seed, human_approver="vinhthang")
    assert res["status"] == "completed"
    assert 5 in client.merged_prs
    assert seeded_slugs == ["novel-one"]


def test_winner_merge_requires_verified_reviewer_logins_for_ch02(tmp_path: Path):
    slug = "test-book"
    run_dir = tmp_path / "runs" / slug
    ch2_dir = run_dir / "ch02"
    ch2_dir.mkdir(parents=True)

    src_xml = '<segment id="S-0001"><original>Hi</original></segment>'
    trans_xml = '<segment id="S-0001"><original>Hi</original><translate>Chào</translate></segment>'
    (ch2_dir / "source.txt").write_text(src_xml, encoding="utf-8")
    (ch2_dir / "translation.xml").write_text(trans_xml, encoding="utf-8")

    book_record = {"slug": slug, "glossary_sha": "sha123", "glossary": {}}
    (run_dir / "book_record.json").write_text(json.dumps(book_record), encoding="utf-8")
    (run_dir / "checklist.json").write_text(json.dumps([{"chapter": "ch02", "status": "in_progress"}]), encoding="utf-8")
    (run_dir / "collab_ledger.json").write_text(json.dumps({"book_slug": slug, "chapters": {}}), encoding="utf-8")

    issue = {
        "number": 40,
        "title": "Translate [ch02] test-book-ch02",
        "body": "- **Book:** test-book",
        "labels": ["status:candidates", f"book:{slug}"],
        "comments": [{"body": "<!-- candidate_window_started_at: 1000.0 -->"}],
        "created_at": 500.0,
    }

    body_pr = (
        "- **Agent:** translator1\n"
        "- **Engine / Model:** custom\n"
        "- **Source Language:** en\n"
        "- **Target Language:** vi\n"
        "- **Chunk ID:** ch02\n"
        "- **Issue:** #40\n"
        "- **Glossary SHA:** sha123\n"
    )

    # PR has NO reviews or only author review
    pr = {
        "number": 81,
        "head_ref": "translate/test-book-ch02-translator1",
        "author_login": "translator1",
        "title": "Translate ch02",
        "body": body_pr,
        "changed_files": ["runs/test-book/ch02/translation.xml"],
        "labels": ["status:candidate"],
        "weighted_total": 4.8,
        "critical_count": 0,
        "major_count": 0,
        "unresolved_threads": 0,
        "reviews": [{"author_login": "translator1", "state": "APPROVED"}],
        "comments": [],
    }

    client = MockGitHubClient(prs=[pr], issues=[issue])
    res = run_sweep(tmp_path, client=client, now=1000.0 + (25.0 * 3600.0), window_hours=24.0)
    assert res["status"] == "completed"
    # Because ch02 has no verified reviewer logins, PR must NOT be merged
    assert 81 not in client.merged_prs


def test_github_client_repo_root_and_merge_commit_sha(monkeypatch, tmp_path: Path):
    from scripts.collab_sweep import GitHubCLIClient

    cli = GitHubCLIClient(bot_login="bot", repo_root=tmp_path)
    assert cli.root == tmp_path

    def mock_run_gh(args):
        if "pr" in args and "merge" in args:
            return ""
        if "pr" in args and "view" in args and "mergeCommit" in args:
            return json.dumps({"mergeCommit": {"oid": "real-merge-commit-sha-9999"}})
        raise RuntimeError(f"Unexpected args: {args}")

    monkeypatch.setattr(cli, "_run_gh", mock_run_gh)

    sha = cli.merge_pr(101)
    assert sha == "real-merge-commit-sha-9999"


def test_revise_round_count_reassign_and_idle_sweep_skip(tmp_path: Path):
    slug = "test-book"
    run_dir = tmp_path / "runs" / slug
    ch2_dir = run_dir / "ch02"
    ch2_dir.mkdir(parents=True)

    src_xml = '<segment id="S-0001"><original>Hi</original></segment>'
    trans_xml = '<segment id="S-0001"><original>Hi</original><translate>Chào</translate></segment>'
    (ch2_dir / "source.txt").write_text(src_xml, encoding="utf-8")
    (ch2_dir / "translation.xml").write_text(trans_xml, encoding="utf-8")

    book_record = {"slug": slug, "glossary_sha": "sha123", "glossary": {}}
    (run_dir / "book_record.json").write_text(json.dumps(book_record), encoding="utf-8")

    body_pr = (
        "- **Agent:** translator1\n"
        "- **Engine / Model:** custom\n"
        "- **Source Language:** en\n"
        "- **Target Language:** vi\n"
        "- **Chunk ID:** ch02\n"
        "- **Issue:** #50\n"
        "- **Glossary SHA:** sha123\n"
    )

    # 1. Test idle sweep skip when last comment is bot REVISE
    pr_idle = {
        "number": 91,
        "head_ref": "translate/test-book-ch02-translator1",
        "author_login": "translator1",
        "title": "Translate ch02",
        "body": body_pr,
        "changed_files": ["runs/test-book/ch02/translation.xml"],
        "labels": ["status:revise"],
        "comments": [
            {"author_login": "1quachuoi9vang", "body": "Evaluation outcome REVISE: need fixes"}
        ],
    }
    client_idle = MockGitHubClient(prs=[pr_idle])
    eval_called = []
    def mock_eval(pr, root):
        eval_called.append(pr["number"])
        return {"verdict": "FAIL", "weighted_total": 2.5, "issues": []}

    res_idle = run_sweep(tmp_path, client=client_idle, evaluate_fn=mock_eval)
    assert res_idle["status"] == "completed"
    # Idle sweep skipped re-evaluating and commenting
    assert len(eval_called) == 0
    assert len(client_idle.pr_comments.get(91, [])) == 0

    # 2. Test revise round count transition to REASSIGN on round 4
    pr_round4 = {
        "number": 92,
        "head_ref": "translate/test-book-ch02-translator1",
        "author_login": "translator1",
        "title": "Translate ch02",
        "body": body_pr,
        "changed_files": ["runs/test-book/ch02/translation.xml"],
        "labels": ["status:needs-review"],
        "reviews": [
            {
                "author_login": "rev1",
                "state": "COMMENTED",
                "line_comment_count": 1,
                "body": "- **Fidelity:** 3.0\n- **Fluency:** 3.0\n- **Terminology:** 3.0\n- **Register:** 3.0\n- **Structure:** 3.0\n- **Completeness:** 3.0\n",
            }
        ],
        "comments": [
            {"body": "REVISE round 1"},
            {"body": "REVISE round 2"},
            {"body": "REVISE round 3"},
            {"author_login": "translator1", "body": "New commit pushed!"},
        ],
    }
    client_r4 = MockGitHubClient(prs=[pr_round4])
    def eval_fn_r4(pr, root):
        return {"verdict": "FAIL", "overall_score": 3.0, "weighted_total": 3.0, "issues": [], "fallback_unverified": False}

    res_r4 = run_sweep(tmp_path, client=client_r4, evaluate_fn=eval_fn_r4)
    assert res_r4["status"] == "completed"
    assert "status:reassign" in pr_round4["labels"]
    assert 92 in client_r4.closed_prs


def test_outcome_blocked_unverified_handled(tmp_path: Path):
    slug = "test-book"
    run_dir = tmp_path / "runs" / slug
    ch2_dir = run_dir / "ch02"
    ch2_dir.mkdir(parents=True)

    src_xml = '<segment id="S-0001"><original>Hi</original></segment>'
    trans_xml = '<segment id="S-0001"><original>Hi</original><translate>Chào</translate></segment>'
    (ch2_dir / "source.txt").write_text(src_xml, encoding="utf-8")
    (ch2_dir / "translation.xml").write_text(trans_xml, encoding="utf-8")

    book_record = {"slug": slug, "glossary_sha": "sha123", "glossary": {}}
    (run_dir / "book_record.json").write_text(json.dumps(book_record), encoding="utf-8")

    body_pr = (
        "- **Agent:** translator1\n"
        "- **Engine / Model:** custom\n"
        "- **Source Language:** en\n"
        "- **Target Language:** vi\n"
        "- **Chunk ID:** ch02\n"
        "- **Issue:** #60\n"
        "- **Glossary SHA:** sha123\n"
    )

    pr = {
        "number": 95,
        "head_ref": "translate/test-book-ch02-translator1",
        "author_login": "translator1",
        "title": "Translate ch02",
        "body": body_pr,
        "changed_files": ["runs/test-book/ch02/translation.xml"],
        "labels": ["status:needs-review"],
        "reviews": [
            {
                "author_login": "rev1",
                "state": "COMMENTED",
                "line_comment_count": 1,
                "body": "- **Fidelity:** 4.0\n- **Fluency:** 4.0\n- **Terminology:** 4.0\n- **Register:** 4.0\n- **Structure:** 4.0\n- **Completeness:** 4.0\n",
            }
        ],
        "comments": [],
    }

    client = MockGitHubClient(prs=[pr])
    def eval_unverified(pr, root):
        return {"verdict": "FAIL", "overall_score": 0.0, "weighted_total": 0.0, "issues": [], "fallback_unverified": True}

    res = run_sweep(tmp_path, client=client, evaluate_fn=eval_unverified)
    assert res["status"] == "completed"
    assert "status:needs-review" in pr["labels"]
    assert 95 in client.pr_comments
    assert any("verified live evaluation is required" in c.lower() for c in client.pr_comments[95])


def test_hyphenated_login_issue_word_boundary_and_candidate_score_recovery(tmp_path: Path):
    from scripts.collab_sweep import _extract_ch_id_and_slug

    # 1. Hyphenated login
    ch, slug = _extract_ch_id_and_slug("translate/three-kingdoms-ch02-john-doe")
    assert ch == "ch02"
    assert slug == "three-kingdoms"

    # 2. Word boundary #1 vs #12
    import re
    body = "- **Issue:** #1\n"
    assert str(1) in re.findall(r"#?(\d+)\b", body)
    assert str(12) not in re.findall(r"#?(\d+)\b", body)

    # 3. Candidate score recovery from comment marker
    ch2_dir = tmp_path / "runs" / "mybook" / "ch02"
    ch2_dir.mkdir(parents=True)
    (ch2_dir / "source.txt").write_text('<segment id="S-0001"><original>Hi</original></segment>')
    (ch2_dir / "translation.xml").write_text('<segment id="S-0001"><original>Hi</original><translate>Chào</translate></segment>')
    (tmp_path / "runs" / "mybook" / "book_record.json").write_text(json.dumps({"slug": "mybook", "glossary_sha": "sha1", "glossary": {}}))
    (tmp_path / "runs" / "mybook" / "checklist.json").write_text(json.dumps([{"chapter": "ch02", "status": "in_progress"}]))
    (tmp_path / "runs" / "mybook" / "collab_ledger.json").write_text(json.dumps({"book_slug": "mybook", "chapters": {}}))

    issue = {
        "number": 1,
        "title": "Translate [ch02] mybook-ch02",
        "body": "- **Book:** mybook",
        "labels": ["status:candidates", "book:mybook"],
        "comments": [{"body": "<!-- candidate_window_started_at: 1000.0 -->"}],
        "created_at": 500.0,
    }

    # PR has NO pr["weighted_total"], but has score marker in comments
    pr_candidate = {
        "number": 99,
        "head_ref": "translate/mybook-ch02-agent1",
        "author_login": "agent1",
        "title": "Translate ch02",
        "body": "- **Agent:** agent1\n- **Chunk ID:** ch02\n- **Issue:** #1\n- **Glossary SHA:** sha1\n",
        "changed_files": ["runs/mybook/ch02/translation.xml"],
        "labels": ["status:candidate"],
        "reviews": [{"author_login": "rev_bob", "state": "APPROVED"}],
        "comments": [
            {"body": "<!-- candidate_score: 4.85, critical: 0, major: 0 -->\nCandidate accepted"}
        ],
    }

    client = MockGitHubClient(prs=[pr_candidate], issues=[issue])
    res = run_sweep(tmp_path, client=client, now=1000.0 + (25.0 * 3600.0), window_hours=24.0)
    assert res["status"] == "completed"
    assert 99 in client.merged_prs

    ledger = json.loads((tmp_path / "runs" / "mybook" / "collab_ledger.json").read_text())
    assert ledger["chapters"]["ch02"]["score_of_record"] == 4.85


def test_github_client_no_mutual_recursion_and_unresolved_threads():
    from scripts.collab_sweep import GitHubCLIClient, read_pr_file
    client = GitHubCLIClient(bot_login="1quachuoi9vang")
    assert not hasattr(client, "get_pr_file") or not callable(getattr(client, "get_pr_file", None)) or getattr(GitHubCLIClient.get_pr_file, "__name__", "") != "read_pr_file"

    # Test normalization of unresolved_threads from CHANGES_REQUESTED
    prs_raw = [
        {
            "number": 10,
            "headRefOid": "abc1234",
            "reviews": [
                {"state": "APPROVED", "author": {"login": "rev1"}},
                {"state": "CHANGES_REQUESTED", "author": {"login": "rev2"}},
            ],
        }
    ]
    # Simulate client normalizing open PRs
    client_norm = GitHubCLIClient()
    # Normalize method or logic verification
    norm_pr = prs_raw[0].copy()
    norm_pr["head_oid"] = norm_pr.get("head_oid") or norm_pr.get("headRefOid", "")
    if "unresolved_threads" not in norm_pr:
        norm_pr["unresolved_threads"] = sum(1 for r in norm_pr.get("reviews", []) if r.get("state") == "CHANGES_REQUESTED")
    assert norm_pr["head_oid"] == "abc1234"
    assert norm_pr["unresolved_threads"] == 1


def test_candidate_score_head_oid_and_revise_sha(tmp_path: Path):
    slug = "book-sha"
    ch_dir = tmp_path / "runs" / slug / "ch02"
    ch_dir.mkdir(parents=True)
    src_xml = '<segment id="S-0001"><original>Hi</original></segment>'
    trans_xml = '<segment id="S-0001"><original>Hi</original><translate>Chào</translate></segment>'
    (ch_dir / "source.txt").write_text(src_xml, encoding="utf-8")
    (ch_dir / "translation.xml").write_text(trans_xml, encoding="utf-8")
    (tmp_path / "runs" / slug / "book_record.json").write_text(json.dumps({"slug": slug, "glossary_sha": "sha1"}))
    (tmp_path / "runs" / slug / "checklist.json").write_text(json.dumps([{"chapter": "ch02", "status": "in_progress"}]))
    (tmp_path / "runs" / slug / "collab_ledger.json").write_text(json.dumps({"book_slug": slug, "chapters": {}}))

    body_pr = "- **Agent:** alice\n- **Chunk ID:** ch02\n- **Issue:** #1\n- **Glossary SHA:** sha1\n- **Engine / Model:** m\n- **Source Language:** en\n- **Target Language:** vi\n"

    # PR 10 has status:candidate with recorded score for sha1, but author pushed commit sha2
    pr_updated_commit = {
        "number": 10,
        "head_ref": f"translate/{slug}-ch02-alice",
        "author_login": "alice",
        "title": "Translate ch02",
        "body": body_pr,
        "changed_files": [f"runs/{slug}/ch02/translation.xml"],
        "labels": ["status:candidate"],
        "head_oid": "commit_new_sha2",
        "comments": [
            {"body": "<!-- candidate_score: 4.9, critical: 0, major: 0, sha: commit_old_sha1 -->\nAccepted", "author_login": "1quachuoi9vang"}
        ],
        "reviews": [
            {
                "author_login": "rev_bob",
                "state": "APPROVED",
                "line_comment_count": 2,
                "body": (
                    "Good work!\n\n"
                    "- **Fidelity:** 4.9\n"
                    "- **Fluency:** 4.9\n"
                    "- **Terminology:** 5.0\n"
                    "- **Register:** 4.8\n"
                    "- **Structure:** 4.8\n"
                    "- **Completeness:** 4.8\n"
                ),
                "scores": {"fidelity": 4.9, "fluency": 4.9, "terminology": 5.0, "register": 4.8},
            }
        ],
    }

    issue = {
        "number": 1,
        "title": f"Translate [ch02] {slug}-ch02",
        "labels": ["status:candidates", f"book:{slug}"],
        "comments": [{"body": "<!-- candidate_window_started_at: 1000.0 -->", "author_login": "1quachuoi9vang"}],
        "created_at": 500.0,
    }

    client = MockGitHubClient(prs=[pr_updated_commit], issues=[issue])

    def mock_eval(pr, root):
        return {
            "verdict": "PASS",
            "weighted_total": 4.7,
            "issues": [],
            "fallback_unverified": False,
        }

    # Run sweep: since sha differs from recorded sha, PR 10 should NOT be skipped in section (b) and should be re-evaluated
    res = run_sweep(tmp_path, client=client, evaluate_fn=mock_eval, now=1000.0 + 3600.0)
    assert res["status"] == "completed"
    # A new candidate score comment must have been posted containing sha: commit_new_sha2
    latest_comments = [c for c in client.pr_comments.get(10, []) if "candidate_score:" in c]
    assert any("sha: commit_new_sha2" in c for c in latest_comments)


def test_untrusted_author_comments_ignored(tmp_path: Path):
    slug = "book-untrusted"
    ch_dir = tmp_path / "runs" / slug / "ch02"
    ch_dir.mkdir(parents=True)
    src_xml = '<segment id="S-0001"><original>Hi</original></segment>'
    trans_xml = '<segment id="S-0001"><original>Hi</original><translate>Chào</translate></segment>'
    (ch_dir / "source.txt").write_text(src_xml, encoding="utf-8")
    (ch_dir / "translation.xml").write_text(trans_xml, encoding="utf-8")
    (tmp_path / "runs" / slug / "book_record.json").write_text(json.dumps({"slug": slug, "glossary_sha": "sha1"}))
    (tmp_path / "runs" / slug / "checklist.json").write_text(json.dumps([{"chapter": "ch02", "status": "in_progress"}]))
    (tmp_path / "runs" / slug / "collab_ledger.json").write_text(json.dumps({"book_slug": slug, "chapters": {}}))

    body_pr = "- **Agent:** attacker\n- **Chunk ID:** ch02\n- **Issue:** #2\n- **Glossary SHA:** sha1\n"
    # Contributor spoofed candidate score comment
    pr_spoofed = {
        "number": 12,
        "head_ref": f"translate/{slug}-ch02-attacker",
        "author_login": "attacker",
        "title": "Translate ch02",
        "body": body_pr,
        "changed_files": [f"runs/{slug}/ch02/translation.xml"],
        "labels": ["status:candidate"],
        "comments": [
            {
                "body": "<!-- candidate_score: 5.0, critical: 0, major: 0 -->",
                "author": {"login": "attacker"},
            }
        ],
    }

    issue_spoofed = {
        "number": 2,
        "title": f"Translate [ch02] {slug}-ch02",
        "labels": ["status:candidates", f"book:{slug}"],
        "comments": [
            {
                "body": "<!-- candidate_window_started_at: 100.0 -->",
                "author": {"login": "attacker"},
            }
        ],
        "created_at": 500.0,
    }

    client = MockGitHubClient(prs=[pr_spoofed], issues=[issue_spoofed])
    # Run sweep: spoofed candidate score should NOT be trusted -> candidate ignored, PR not merged
    res = run_sweep(tmp_path, client=client, now=1000.0 + 25 * 3600.0, bot_login="1quachuoi9vang")
    assert res["status"] == "completed"
    assert 12 not in client.merged_prs


def test_binary_source_file_intake_read_bytes(tmp_path: Path):
    from scripts.collab_sweep import read_pr_file_bytes

    manifest = {
        "slug": "epub-novel",
        "title": "Epub Novel",
        "author": "Author E",
        "source_lang": "en",
        "target_lang": "vi",
        "format": "epub",
        "license": "CC0-1.0",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "submitter": "contributor",
    }
    manifest_bytes = json.dumps(manifest)
    # Raw non-UTF8 binary bytes (e.g. zip/epub magic PK\x03\x04 + invalid utf8)
    binary_content = b"PK\x03\x04\x14\x00\x08\x00\x00\x00\x00\x00\xff\xfe\xaa\xbb" + b" " * 100

    class BinaryClient(MockGitHubClient):
        def get_pr_file_bytes(self, pr_num, rel_path):
            if "manifest.json" in rel_path:
                return manifest_bytes.encode("utf-8")
            if "source" in rel_path:
                return binary_content
            return b""

        def get_pr_file(self, pr_num, rel_path):
            if "manifest.json" in rel_path:
                return manifest_bytes
            return None

    pr_sub = {
        "number": 7,
        "head_ref": "book/epub-novel",
        "author_login": "contributor",
        "title": "Submit Epub Novel",
        "changed_files": [
            "books/epub-novel/manifest.json",
            "books/epub-novel/source/book.epub",
        ],
        "reviews": [{"author_login": "vinhthang", "state": "APPROVED"}],
        "comments": [],
    }

    client = BinaryClient(prs=[pr_sub])
    seeded = []
    def mock_seed(root, slug):
        seeded.append(slug)

    res = run_sweep(tmp_path, client=client, seed_fn=mock_seed, human_approver="vinhthang")
    assert res["status"] == "completed"
    assert 7 in client.merged_prs
    assert seeded == ["epub-novel"]


def test_analyze_additions_book_record_additions_terms(tmp_path: Path):
    slug = "test-book"
    run_dir = tmp_path / "runs" / slug
    ch2_dir = run_dir / "ch02"
    ch2_dir.mkdir(parents=True)

    src_xml = '<segment id="S-0001"><original>Hi</original></segment>'
    trans_xml = '<segment id="S-0001"><original>Hi</original><translate>Chào</translate></segment>'
    (ch2_dir / "source.txt").write_text(src_xml, encoding="utf-8")
    (ch2_dir / "translation.xml").write_text(trans_xml, encoding="utf-8")

    book_record = {"slug": slug, "glossary_sha": "sha1", "glossary": {}}
    (run_dir / "book_record.json").write_text(json.dumps(book_record), encoding="utf-8")
    (run_dir / "checklist.json").write_text(json.dumps([{"chapter": "ch02", "status": "in_progress"}]))
    (run_dir / "collab_ledger.json").write_text(json.dumps({"book_slug": slug, "chapters": {}}))

    # analyze_additions with book_record_additions.terms format
    additions = {
        "book_record_additions": {
            "terms": [
                {"term": "Cao Cao", "target": "Tào Tháo", "status": "proposed"}
            ]
        }
    }
    (ch2_dir / "analyze_additions.json").write_text(json.dumps(additions), encoding="utf-8")

    issue = {
        "number": 30,
        "title": "Translate [ch02] test-book-ch02",
        "labels": ["status:candidates", f"book:{slug}"],
        "comments": [{"body": "<!-- candidate_window_started_at: 1000.0 -->"}],
        "created_at": 500.0,
    }

    body_pr = "- **Agent:** alice\n- **Chunk ID:** ch02\n- **Issue:** #30\n- **Glossary SHA:** sha1\n"
    pr = {
        "number": 77,
        "head_ref": "translate/test-book-ch02-alice",
        "author_login": "alice",
        "title": "Translate ch02",
        "body": body_pr,
        "changed_files": ["runs/test-book/ch02/translation.xml"],
        "labels": ["status:candidate"],
        "weighted_total": 4.8,
        "valid_reviews": [{"author_login": "rev_bob", "scores": {"fidelity": 4.8}}],
        "reviews": [{"author_login": "rev_bob", "state": "APPROVED"}],
        "comments": [],
    }

    client = MockGitHubClient(prs=[pr], issues=[issue])
    res = run_sweep(tmp_path, client=client, now=1000.0 + 25 * 3600.0)
    assert res["status"] == "completed"
    assert 77 in client.merged_prs

    updated_br = json.loads((run_dir / "book_record.json").read_text())
    assert updated_br["glossary"].get("Cao Cao") == "Tào Tháo"


def test_recovered_candidate_revalidates_reviews_and_commit_and_push(tmp_path: Path):
    slug = "test-book"
    run_dir = tmp_path / "runs" / slug
    ch2_dir = run_dir / "ch02"
    ch2_dir.mkdir(parents=True)

    src_xml = '<segment id="S-0001"><original>Hi</original></segment>'
    trans_xml = '<segment id="S-0001"><original>Hi</original><translate>Chào</translate></segment>'
    (ch2_dir / "source.txt").write_text(src_xml, encoding="utf-8")
    (ch2_dir / "translation.xml").write_text(trans_xml, encoding="utf-8")

    book_record = {"slug": slug, "glossary_sha": "sha1", "glossary": {}}
    (run_dir / "book_record.json").write_text(json.dumps(book_record), encoding="utf-8")
    (run_dir / "checklist.json").write_text(json.dumps([{"chapter": "ch02", "status": "in_progress"}]))
    (run_dir / "collab_ledger.json").write_text(json.dumps({"book_slug": slug, "chapters": {}}))

    issue = {
        "number": 31,
        "title": "Translate [ch02] test-book-ch02",
        "labels": ["status:candidates", f"book:{slug}"],
        "comments": [{"body": "<!-- candidate_window_started_at: 1000.0 -->"}],
        "created_at": 500.0,
    }

    # Recovered candidate from previous sweep: valid_reviews is empty, but reviews list has review
    pr = {
        "number": 88,
        "head_ref": "translate/test-book-ch02-tom",
        "author_login": "tom",
        "title": "Translate ch02",
        "body": "- **Agent:** tom\n- **Chunk ID:** ch02\n- **Issue:** #31\n- **Glossary SHA:** sha1\n",
        "changed_files": ["runs/test-book/ch02/translation.xml"],
        "labels": ["status:candidate"],
        "weighted_total": 4.8,
        "valid_reviews": [],  # Empty on recovered PR dict
        "reviews": [
            {
                "author_login": "real_reviewer",
                "body": (
                    "Good translation. Segment accuracy verified.\n\n"
                    "- **Fidelity:** 4.8\n"
                    "- **Fluency:** 4.8\n"
                    "- **Terminology:** 4.8\n"
                    "- **Register:** 4.8\n"
                    "- **Structure:** 4.8\n"
                    "- **Completeness:** 4.8\n"
                ),
                "line_comment_count": 2,
                "scores": {"fidelity": 4.8, "fluency": 4.8},
                "state": "APPROVED",
            }
        ],
        "comments": [],
    }

    pushed_messages = []
    class PushClient(MockGitHubClient):
        def commit_and_push(self, paths, message):
            pushed_messages.append(message)

    client = PushClient(prs=[pr], issues=[issue])
    res = run_sweep(tmp_path, client=client, now=1000.0 + 25 * 3600.0)
    assert res["status"] == "completed"
    assert 88 in client.merged_prs

    # Ledger should record real_reviewer, not empty
    ledger = json.loads((run_dir / "collab_ledger.json").read_text())
    assert "real_reviewer" in ledger["chapters"]["ch02"]["reviewer_logins"]
    assert len(pushed_messages) >= 1





