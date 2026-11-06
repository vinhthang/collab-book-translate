"""
tests/test_collab_seed.py — Unit tests for book ingestion, ch01 reference seed, glossary freeze, and parallel chapter issue dispatch.
Tests REQ-COLLAB-003, AC-COLLAB-003-1, AC-COLLAB-003-2, and D2.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.collab_seed import seed_merged_book


class TestCollabSeed(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.repo_root = Path(self.test_dir)
        self.book_slug = "test-novella"

        # Create books/<slug>/manifest.json and source directory
        self.books_dir = self.repo_root / "books" / self.book_slug
        self.source_dir = self.books_dir / "source"
        self.source_dir.mkdir(parents=True, exist_ok=True)

        self.manifest = {
            "slug": self.book_slug,
            "title": "A Test Novella",
            "author": "Jane Doe",
            "source_lang": "en",
            "target_lang": "vi",
            "license": "public-domain",
            "license_url": "https://example.com/license",
            "submitter": "tester",
        }
        with open(self.books_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(self.manifest, f, indent=2)

        # Create dummy source book file
        (self.source_dir / "book.txt").write_text("Dummy source book content", encoding="utf-8")

        # Create pre-extracted runs/<slug>/ch01, ch02, ch03
        self.runs_dir = self.repo_root / "runs" / self.book_slug
        self.ch01_dir = self.runs_dir / "ch01"
        self.ch02_dir = self.runs_dir / "ch02"
        self.ch03_dir = self.runs_dir / "ch03"
        for d in (self.ch01_dir, self.ch02_dir, self.ch03_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.ch01_source = (
            '<segment id="S-0001" type="heading">\n'
            '<original>\n# Chapter 1. The Beginning\n</original>\n'
            '</segment>\n\n'
            '<segment id="S-0002" type="paragraph">\n'
            '<original>\nIt was a dark and stormy night.\n</original>\n'
            '</segment>\n'
        )
        self.ch02_source = (
            '<segment id="S-0003" type="heading">\n'
            '<original>\n# Chapter 2. The Journey\n</original>\n'
            '</segment>\n\n'
            '<segment id="S-0004" type="paragraph">\n'
            '<original>\nThey packed their bags and left early.\n</original>\n'
            '</segment>\n'
        )
        self.ch03_source = (
            '<segment id="S-0005" type="heading">\n'
            '<original>\n# Chapter 3. The Arrival\n</original>\n'
            '</segment>\n\n'
            '<segment id="S-0006" type="paragraph">\n'
            '<original>\nThe city gates opened wide.\n</original>\n'
            '</segment>\n'
        )
        (self.ch01_dir / "source.txt").write_text(self.ch01_source, encoding="utf-8")
        (self.ch02_dir / "source.txt").write_text(self.ch02_source, encoding="utf-8")
        (self.ch03_dir / "source.txt").write_text(self.ch03_source, encoding="utf-8")

        # Mock translate function
        def mock_translate(source_xml, book_record, manifest):
            return (
                '<segment id="S-0001" type="heading">\n'
                '<original>\n# Chapter 1. The Beginning\n</original>\n'
                '<translate>\n# Chương 1. Sự khởi đầu\n</translate>\n'
                '</segment>\n\n'
                '<segment id="S-0002" type="paragraph">\n'
                '<original>\nIt was a dark and stormy night.\n</original>\n'
                '<translate>\nĐó là một đêm tối tăm bão bùng.\n</translate>\n'
                '</segment>\n'
            )
        self.mock_translate = mock_translate

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_seed_merged_book_happy_path(self):
        issues_created = []

        def mock_issue_creator(title, body, labels):
            issue_id = len(issues_created) + 101
            issues_created.append({"id": issue_id, "title": title, "body": body, "labels": labels})
            return issue_id

        def mock_evaluate(source_xml, trans_xml, book_record):
            return {
                "verdict": "PASS",
                "weighted_total": 4.6,
                "fallback_unverified": False,
                "issues": [],
            }

        res = seed_merged_book(
            repo_root=self.repo_root,
            book_slug=self.book_slug,
            translate_fn=self.mock_translate,
            evaluate_fn=mock_evaluate,
            issue_creator_fn=mock_issue_creator,
            git_sha_fn=lambda: "abc123frozen456",
        )

        self.assertTrue(res["seeded"])
        self.assertEqual(res["glossary_sha"], "abc123frozen456")
        self.assertEqual(res["ch01_score"], 4.6)
        self.assertEqual(len(res["issues_created"]), 2)  # ch02 and ch03
        self.assertEqual(res["failed_chapters"], [])

        # Verify ch01 artifacts
        self.assertTrue((self.ch01_dir / "translation.xml").exists())
        self.assertTrue((self.ch01_dir / "final.txt").exists())
        final_content = (self.ch01_dir / "final.txt").read_text(encoding="utf-8")
        self.assertIn("# Chương 1. Sự khởi đầu", final_content)

        # Verify checklist.json
        checklist_path = self.runs_dir / "checklist.json"
        self.assertTrue(checklist_path.exists())
        with open(checklist_path, "r", encoding="utf-8") as f:
            checklist = json.load(f)
        ch01_entry = next((e for e in checklist if e.get("chapter") == "ch01"), None)
        self.assertIsNotNone(ch01_entry)
        self.assertEqual(ch01_entry.get("status"), "done")

        # Verify collab_ledger.json
        ledger_path = self.runs_dir / "collab_ledger.json"
        self.assertTrue(ledger_path.exists())
        with open(ledger_path, "r", encoding="utf-8") as f:
            ledger = json.load(f)
        self.assertIn("ch01", ledger["chapters"])
        self.assertEqual(ledger["chapters"]["ch01"]["verdict"], "PASS")
        self.assertEqual(ledger["chapters"]["ch01"]["issue_number"], 0)

        # Verify issue bodies and title for ch02
        ch02_issue = issues_created[0]
        self.assertEqual(ch02_issue["title"], "Translate [ch02] test-novella-ch02: S-0003..S-0004")
        self.assertIn("- **Chapter:** ch02", ch02_issue["body"])
        self.assertIn("abc123frozen456", ch02_issue["body"])
        self.assertIn("S-0003..S-0004", ch02_issue["body"])
        self.assertIn(f"book:{self.book_slug}", ch02_issue["labels"])

        # Verify book_record.json has persisted glossary_sha
        book_record_path = self.runs_dir / "book_record.json"
        self.assertTrue(book_record_path.exists())
        with open(book_record_path, "r", encoding="utf-8") as f:
            rec = json.load(f)
        self.assertEqual(rec.get("glossary_sha"), "abc123frozen456")

    def test_seed_merged_book_fails_closed_if_translate_fn_none(self):
        def mock_eval(s, t, b):
            return {"verdict": "PASS", "weighted_total": 5.0, "issues": []}

        res = seed_merged_book(
            repo_root=self.repo_root,
            book_slug=self.book_slug,
            translate_fn=None,
            evaluate_fn=mock_eval,
        )
        self.assertFalse(res["seeded"])
        self.assertEqual(
            res["reason"],
            "Live translate_fn and evaluate_fn callables are required for ch01 seeding (AC-COLLAB-003-2)",
        )
        self.assertEqual(res["issues_created"], [])

    def test_seed_merged_book_fails_closed_if_evaluate_fn_none(self):
        res = seed_merged_book(
            repo_root=self.repo_root,
            book_slug=self.book_slug,
            translate_fn=self.mock_translate,
            evaluate_fn=None,
        )
        self.assertFalse(res["seeded"])
        self.assertEqual(
            res["reason"],
            "Live translate_fn and evaluate_fn callables are required for ch01 seeding (AC-COLLAB-003-2)",
        )
        self.assertEqual(res["issues_created"], [])

    def test_seed_merged_book_ch01_fail_blocks_issues(self):
        issues_created = []

        def mock_evaluate_fail(source_xml, trans_xml, book_record):
            return {
                "verdict": "FAIL",
                "weighted_total": 2.4,
                "fallback_unverified": False,
                "issues": [{"severity": "major", "description": "untranslated terms"}],
            }

        res = seed_merged_book(
            repo_root=self.repo_root,
            book_slug=self.book_slug,
            translate_fn=self.mock_translate,
            evaluate_fn=mock_evaluate_fail,
            issue_creator_fn=lambda t, b, l: issues_created.append(t),
            git_sha_fn=lambda: "abc123frozen456",
        )

        self.assertFalse(res["seeded"])
        self.assertIn("live check", res["reason"])
        self.assertEqual(len(res["issues_created"]), 0)
        self.assertEqual(len(issues_created), 0)
        self.assertFalse((self.ch01_dir / "final.txt").exists())

    def test_seed_merged_book_ch01_fallback_unverified_blocks_issues(self):
        issues_created = []

        def mock_evaluate_fallback(source_xml, trans_xml, book_record):
            return {
                "verdict": "PASS",
                "weighted_total": 4.5,
                "fallback_unverified": True,
                "issues": [],
            }

        res = seed_merged_book(
            repo_root=self.repo_root,
            book_slug=self.book_slug,
            translate_fn=self.mock_translate,
            evaluate_fn=mock_evaluate_fallback,
            issue_creator_fn=lambda t, b, l: issues_created.append(t),
            git_sha_fn=lambda: "abc123frozen456",
        )

        self.assertFalse(res["seeded"])
        self.assertEqual(len(res["issues_created"]), 0)


    def test_seed_merged_book_ch01_critical_issue_blocks_issues(self):
        issues_created = []

        def mock_evaluate_critical(source_xml, trans_xml, book_record):
            return {
                "verdict": "FAIL",
                "weighted_total": 4.8,
                "fallback_unverified": False,
                "issues": [{"severity": "critical", "description": "corrupted segment"}],
            }

        res = seed_merged_book(
            repo_root=self.repo_root,
            book_slug=self.book_slug,
            translate_fn=self.mock_translate,
            evaluate_fn=mock_evaluate_critical,
            issue_creator_fn=lambda t, b, l: issues_created.append(t),
            git_sha_fn=lambda: "abc123frozen456",
        )

        self.assertFalse(res["seeded"])
        self.assertEqual(len(res["issues_created"]), 0)


    def test_seed_merged_book_skips_zero_segment_chapter(self):
        # Empty source.txt in ch03
        (self.ch03_dir / "source.txt").write_text("", encoding="utf-8")

        issues_created = []

        def mock_issue_creator(title, body, labels):
            issues_created.append(title)
            return len(issues_created)

        def mock_evaluate(source_xml, trans_xml, book_record):
            return {
                "verdict": "PASS",
                "weighted_total": 4.5,
                "fallback_unverified": False,
                "issues": [],
            }

        res = seed_merged_book(
            repo_root=self.repo_root,
            book_slug=self.book_slug,
            translate_fn=self.mock_translate,
            evaluate_fn=mock_evaluate,
            issue_creator_fn=mock_issue_creator,
            git_sha_fn=lambda: "abc123frozen456",
        )

        self.assertTrue(res["seeded"])
        self.assertEqual(res["failed_chapters"], ["ch03"])
        self.assertEqual(len(res["issues_created"]), 1)  # Only ch02
        self.assertIn("ch02", res["issues_created"][0]["title"])

        failures_file = self.runs_dir / "seed_failures.json"
        self.assertTrue(failures_file.exists())
        with open(failures_file, "r", encoding="utf-8") as f:
            failures_data = json.load(f)
        self.assertEqual(failures_data.get("failed_chapters"), ["ch03"])

    def test_seed_merged_book_minor_issue_does_not_block_seeding(self):
        issues_created = []

        def mock_evaluate_minor(source_xml, trans_xml, book_record):
            return {
                "verdict": "PASS",
                "weighted_total": 4.6,
                "fallback_unverified": False,
                "issues": [{"severity": "minor", "description": "small punctuation fix"}],
            }

        res = seed_merged_book(
            repo_root=self.repo_root,
            book_slug=self.book_slug,
            translate_fn=self.mock_translate,
            evaluate_fn=mock_evaluate_minor,
            issue_creator_fn=lambda t, b, l: issues_created.append(t),
            git_sha_fn=lambda: "abc123frozen456",
        )

        self.assertTrue(res["seeded"])
        self.assertEqual(len(issues_created), 2)

    def test_seed_merged_book_score_exact_threshold_passes(self):
        # Default pass threshold is 4.0
        issues_created = []

        def mock_evaluate_exact_threshold(source_xml, trans_xml, book_record):
            return {
                "verdict": "PASS",
                "weighted_total": 4.0,
                "fallback_unverified": False,
                "issues": [],
            }

        res = seed_merged_book(
            repo_root=self.repo_root,
            book_slug=self.book_slug,
            translate_fn=self.mock_translate,
            evaluate_fn=mock_evaluate_exact_threshold,
            issue_creator_fn=lambda t, b, l: issues_created.append(t),
            git_sha_fn=lambda: "abc123frozen456",
        )

        self.assertTrue(res["seeded"])
        self.assertEqual(res["ch01_score"], 4.0)
        self.assertEqual(len(issues_created), 2)

    def test_seed_merged_book_dict_format_checklist(self):
        # Create dict-format checklist before seeding
        checklist_path = self.runs_dir / "checklist.json"
        dict_checklist = {
            "chapters": {
                "ch01": {"status": "pending"},
                "ch02": {"status": "pending"},
            }
        }
        with open(checklist_path, "w", encoding="utf-8") as f:
            json.dump(dict_checklist, f, indent=2)

        def mock_eval(s, t, b):
            return {"verdict": "PASS", "weighted_total": 4.8, "issues": []}

        res = seed_merged_book(
            repo_root=self.repo_root,
            book_slug=self.book_slug,
            translate_fn=self.mock_translate,
            evaluate_fn=mock_eval,
            issue_creator_fn=lambda t, b, l: len(t),
            git_sha_fn=lambda: "dict_sha_123",
        )
        self.assertTrue(res["seeded"])
        with open(checklist_path, "r", encoding="utf-8") as f:
            updated = json.load(f)
        self.assertIsInstance(updated, dict)
        self.assertEqual(updated["chapters"]["ch01"]["status"], "done")

    def test_seed_merged_book_git_commit_before_sha(self):
        import subprocess

        # Initialize git repo in self.repo_root
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo_root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "CollabBot"], cwd=self.repo_root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "bot@collab.local"], cwd=self.repo_root, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "chore: initial repo state"], cwd=self.repo_root, check=True, capture_output=True)

        initial_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_root, capture_output=True, text=True, check=True
        ).stdout.strip()

        def mock_eval(s, t, b):
            return {"verdict": "PASS", "weighted_total": 4.9, "issues": []}

        res = seed_merged_book(
            repo_root=self.repo_root,
            book_slug=self.book_slug,
            translate_fn=self.mock_translate,
            evaluate_fn=mock_eval,
            issue_creator_fn=lambda t, b, l: len(t),
            git_sha_fn=None,  # Exercise real git commit and rev-parse HEAD
        )
        self.assertTrue(res["seeded"])
        self.assertNotEqual(res["glossary_sha"], initial_sha)

        # Verify the commit message
        last_commit_msg = subprocess.run(
            ["git", "log", "-1", "--pretty=%B"], cwd=self.repo_root, capture_output=True, text=True, check=True
        ).stdout.strip()
        self.assertEqual(last_commit_msg, f"seed({self.book_slug}): initialize ch01 reference and book_record")

        # Verify book_record.json and collab_ledger.json match
        with open(self.runs_dir / "book_record.json", "r", encoding="utf-8") as f:
            rec = json.load(f)
        self.assertEqual(rec.get("glossary_sha"), res["glossary_sha"])

    def test_seed_merged_book_pushes_to_origin_if_configured(self):
        import subprocess

        # Set up a bare remote repository
        remote_bare = Path(tempfile.mkdtemp()) / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote_bare)], check=True, capture_output=True)

        # Initialize local repo with origin remote
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo_root, check=True, capture_output=True)
        subprocess.run(["git", "remote", "add", "origin", str(remote_bare)], cwd=self.repo_root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "CollabBot"], cwd=self.repo_root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "bot@collab.local"], cwd=self.repo_root, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=self.repo_root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "chore: initial repo state"], cwd=self.repo_root, check=True, capture_output=True)
        subprocess.run(["git", "push", "-u", "origin", "main"], cwd=self.repo_root, check=True, capture_output=True)

        def mock_eval(s, t, b):
            return {"verdict": "PASS", "weighted_total": 4.9, "issues": []}

        res = seed_merged_book(
            repo_root=self.repo_root,
            book_slug=self.book_slug,
            translate_fn=self.mock_translate,
            evaluate_fn=mock_eval,
            issue_creator_fn=lambda t, b, l: len(t),
            git_sha_fn=None,
        )
        self.assertTrue(res["seeded"])

        local_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_root, capture_output=True, text=True, check=True
        ).stdout.strip()
        remote_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=remote_bare, capture_output=True, text=True, check=True
        ).stdout.strip()
        self.assertEqual(remote_head, local_head)



if __name__ == "__main__":
    unittest.main()
