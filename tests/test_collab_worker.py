"""Unit tests for scripts/collab_worker.py (REQ-035, REQ-036)."""

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scripts.collab_config import CollabConfig


class TestCollabWorkerAuthContext(unittest.TestCase):
    def test_git_askpass_script_creation_and_execution(self):
        from scripts.collab_worker import GitHubAccountContext

        with patch("scripts.collab_worker._get_gh_token", return_value="ghp_testtoken123"):
            ctx = GitHubAccountContext("vinhthang")
            with ctx:
                self.assertEqual(ctx.token, "ghp_testtoken123")
                self.assertTrue(os.path.exists(ctx.askpass_path))
                # Check 0700 permissions
                mode = os.stat(ctx.askpass_path).st_mode
                self.assertTrue(mode & stat.S_IXUSR)

                # Test executing the askpass script
                env = {"GH_TOKEN": "ghp_testtoken123"}
                out_u = subprocess.check_output([ctx.askpass_path, "Username for 'https://github.com':"], env=env, text=True).strip()
                out_p = subprocess.check_output([ctx.askpass_path, "Password for 'https://github.com':"], env=env, text=True).strip()
                self.assertEqual(out_u, "x-access-token")
                self.assertEqual(out_p, "ghp_testtoken123")

            # Cleaned up after context exit
            self.assertFalse(os.path.exists(ctx.askpass_path))


class TestCollabWorkerPreflights(unittest.TestCase):
    def setUp(self):
        self.cfg = CollabConfig(
            workspace_dir="/tmp/test_workspace",
            repo_full_name="owner/repo",
            bot_login="collab-bot",
            bot_git_name="Collab Bot",
            bot_git_email="bot@example.com",
            human_approver="vinhthang",
            fallback_hours=48.0,
            max_revise_rounds=3,
            release_formats=["epub", "pdf", "docx"],
            max_source_bytes=5000000,
            allowed_licenses=["public-domain", "CC0-1.0"],
            supported_pairs=[["en", "vi"], ["zh", "vi"]],
        )

    @patch("scripts.collab_worker._get_gh_token", return_value="mock_token")
    def test_approve_intake_fails_if_not_human_approver(self, _mock_tok):
        from scripts.collab_worker import approve_intake

        # 1quachuoi9vang is not human_approver (vinhthang is)
        cfg_dict = dict(self.cfg.__dict__)
        cfg_dict["release_formats"] = "epub,pdf,docx"
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            json.dump(cfg_dict, f)
            cfg_path = f.name

        try:
            res = approve_intake(
                gh_user="1quachuoi9vang",
                pr_number=10,
                config_path=cfg_path,
            )
            self.assertFalse(res["success"])
            self.assertIn("human_approver", res["error"])
        finally:
            os.unlink(cfg_path)

    @patch("scripts.collab_worker._get_gh_token", return_value="mock_token")
    def test_submit_review_blocks_self_review(self, _mock_tok):
        from scripts.collab_worker import submit_review

        # Fetching PR metadata returns author == alice
        mock_pr_meta = {
            "author": {"login": "alice"},
            "headRefOid": "head_sha_123",
        }

        with patch("scripts.collab_worker._fetch_pr_metadata", return_value=mock_pr_meta):
            res = submit_review(
                gh_user="alice",
                pr_number=5,
                review_file="/tmp/dummy_review.md",
            )
            self.assertFalse(res["success"])
            self.assertIn("self-review", res["error"].lower())

    @patch("scripts.collab_worker._get_gh_token", return_value="mock_token")
    def test_submit_review_blocks_missing_rubric(self, _mock_tok):
        from scripts.collab_worker import submit_review

        mock_pr_meta = {
            "author": {"login": "alice"},
            "headRefOid": "head_sha_123",
        }
        with tempfile.NamedTemporaryFile("w", delete=False) as rf:
            rf.write("Looks good! No rubric scores here.")
            rev_path = rf.name

        try:
            with patch("scripts.collab_worker._fetch_pr_metadata", return_value=mock_pr_meta):
                res = submit_review(
                    gh_user="bob",
                    pr_number=5,
                    review_file=rev_path,
                )
                self.assertFalse(res["success"])
                self.assertIn("rubric", res["error"].lower())
        finally:
            os.unlink(rev_path)


class TestCollabWorkerListTasks(unittest.TestCase):
    @patch("scripts.collab_worker._get_gh_token", return_value="mock_token")
    def test_list_tasks_sharded_mode(self, _mock_tok):
        from scripts.collab_worker import list_role_tasks

        mock_issues = [
            {"number": 1, "title": "[ch02] Chapter 2", "labels": [{"name": "status:open"}, {"name": "book:test"}]},
            {"number": 2, "title": "[ch03] Chapter 3", "labels": [{"name": "status:open"}, {"name": "book:test"}]},
            {"number": 3, "title": "[ch04] Chapter 4", "labels": [{"name": "status:open"}, {"name": "book:test"}]},
            {"number": 4, "title": "[ch05] Chapter 5", "labels": [{"name": "status:open"}, {"name": "book:test"}]},
        ]

        with patch("scripts.collab_worker._fetch_open_issues", return_value=mock_issues), \
             patch("scripts.collab_worker._fetch_open_prs", return_value=[]):
            # vinhthang index 0 in [vinhthang, 1quachuoi9vang]: ch02 (idx 0), ch04 (idx 2)
            tasks_u1 = list_role_tasks(
                role="translator",
                gh_user="vinhthang",
                workers=["vinhthang", "1quachuoi9vang"],
                mode="sharded",
            )
            # 1quachuoi9vang index 1: ch03 (idx 1), ch05 (idx 3)
            tasks_u2 = list_role_tasks(
                role="translator",
                gh_user="1quachuoi9vang",
                workers=["vinhthang", "1quachuoi9vang"],
                mode="sharded",
            )

            u1_nums = [t["number"] for t in tasks_u1.get("translate_issues", [])]
            u2_nums = [t["number"] for t in tasks_u2.get("translate_issues", [])]

            self.assertEqual(u1_nums, [1, 3])
            self.assertEqual(u2_nums, [2, 4])

    @patch("scripts.collab_worker._get_gh_token", return_value="mock_token")
    def test_list_tasks_revise_and_review_filters(self, _mock_tok):
        from scripts.collab_worker import list_role_tasks

        mock_prs = [
            {
                "number": 10,
                "author": {"login": "alice"},
                "headRefOid": "sha10",
                "labels": [{"name": "status:revise"}],
                "reviews": [],
            },
            {
                "number": 11,
                "author": {"login": "bob"},
                "headRefOid": "sha11",
                "labels": [{"name": "status:needs-review"}],
                "reviews": [{"author": {"login": "alice"}, "commit": {"oid": "stale_sha"}}],
            },
            {
                "number": 12,
                "author": {"login": "bob"},
                "headRefOid": "sha12",
                "labels": [{"name": "status:open"}],
                "reviews": [{"author": {"login": "alice"}, "commit": {"oid": "sha12"}}],
            },
        ]

        with patch("scripts.collab_worker._fetch_open_issues", return_value=[]), \
             patch("scripts.collab_worker._fetch_open_prs", return_value=mock_prs):
            tr_tasks = list_role_tasks(role="translator", gh_user="alice")
            self.assertEqual([p["number"] for p in tr_tasks["revise_prs"]], [10])
            self.assertEqual(tr_tasks["review_prs"], [])

            rv_tasks = list_role_tasks(role="reviewer", gh_user="alice")
            self.assertEqual(rv_tasks["revise_prs"], [])
            # PR 11 has only a stale review from alice; PR 12 already has fresh review for sha12
            self.assertEqual([p["number"] for p in rv_tasks["review_prs"]], [11])


class TestCollabWorkerSubcommandsAndCLI(unittest.TestCase):
    def _write_cfg(self, tmp_dir: Path) -> str:
        cfg_p = tmp_dir / "cfg.json"
        cfg_p.write_text(
            json.dumps({
                "workspace_dir": str(tmp_dir),
                "repo_full_name": "vinhthang/collab-book-translate",
                "bot_login": "1quachuoi9vang",
                "bot_git_name": "1quachuoi9vang",
                "bot_git_email": "bot@example.com",
                "human_approver": "vinhthang",
                "fallback_hours": 48.0,
                "max_revise_rounds": 3,
                "release_formats": "epub,pdf,docx",
                "max_source_bytes": 5000000,
                "allowed_licenses": ["public-domain", "CC0-1.0"],
                "supported_pairs": [["en", "vi"], ["zh", "vi"]],
            }),
            encoding="utf-8",
        )
        return str(cfg_p)

    @patch("scripts.collab_worker._get_gh_token", return_value="mock_token")
    def test_submit_book_preflight_fail_and_success(self, _mock_tok):
        from scripts.collab_worker import submit_book

        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            cfg_path = self._write_cfg(tmp_dir)

            # Missing source file fails fast
            missing_res = submit_book(
                gh_user="vinhthang",
                slug="demo-book",
                source_file=str(tmp_dir / "nope.md"),
                title="Demo Book",
                author="Author",
                source_lang="en",
                config_path=cfg_path,
            )
            self.assertFalse(missing_res["success"])

            src = tmp_dir / "demo.txt"
            src.write_text("Chapter 1\n\nOnce upon a time.", encoding="utf-8")

            git_calls = []
            gh_calls = []

            def fake_run_git(self, args, cwd=None, check=True, text=True, capture_output=True):
                git_calls.append(args)
                if args[:2] == ["worktree", "add"]:
                    Path(args[4]).mkdir(parents=True, exist_ok=True)
                return subprocess.CompletedProcess(["git"] + args, 0, "", "")

            def fake_run_gh(self, args, cwd=None, repo=None, check=True, text=True, capture_output=True):
                gh_calls.append(args)
                return subprocess.CompletedProcess(["gh"] + args, 0, "https://github.com/vinhthang/collab-book-translate/pull/99\n", "")

            with patch("scripts.collab_worker.GitHubAccountContext.run_git", fake_run_git), \
                 patch("scripts.collab_worker.GitHubAccountContext.run_gh", fake_run_gh):
                # Unsupported source language fails pre-screen before touching git/gh
                bad_res = submit_book(
                    gh_user="vinhthang",
                    slug="demo-book",
                    source_file=str(src),
                    title="Demo Book",
                    author="Author",
                    source_lang="fr",
                    config_path=cfg_path,
                )
                self.assertFalse(bad_res["success"])
                self.assertIn("defects", bad_res)
                self.assertEqual(git_calls, [])
                self.assertEqual(gh_calls, [])

                ok_res = submit_book(
                    gh_user="vinhthang",
                    slug="demo-book",
                    source_file=str(src),
                    title="Demo Book",
                    author="Author",
                    source_lang="en",
                    license_name="public-domain",
                    license_url="https://creativecommons.org/publicdomain/mark/1.0/",
                    config_path=cfg_path,
                )
            self.assertTrue(ok_res["success"])
            self.assertEqual(ok_res["branch"], "book/demo-book")
            self.assertEqual(ok_res["pr_url"], "https://github.com/vinhthang/collab-book-translate/pull/99")
            self.assertTrue(any(c[:2] == ["worktree", "remove"] for c in git_calls))
            self.assertTrue(any(c[:2] == ["pr", "create"] for c in gh_calls))

    @patch("scripts.collab_worker._get_gh_token", return_value="mock_token")
    def test_approve_intake_blocks_spoofed_author_and_handles_warn_and_pass(self, _mock_tok):
        from scripts.collab_worker import approve_intake

        with tempfile.TemporaryDirectory() as td:
            cfg_path = self._write_cfg(Path(td))
            sha = "a" * 40

            # Spoofed prescreen comment from non-bot author must be rejected
            spoofed_meta = {
                "headRefOid": sha,
                "comments": [{"author": {"login": "mallory"}, "body": f"[collab-prescreen sha={sha} status=PASS]"}],
            }
            with patch("scripts.collab_worker._fetch_pr_metadata", return_value=spoofed_meta):
                spoof_res = approve_intake(gh_user="vinhthang", pr_number=19, config_path=cfg_path)
            self.assertFalse(spoof_res["success"])

            # WARN status blocked unless allow_warn=True
            warn_meta = {
                "headRefOid": sha,
                "comments": [{"author": {"login": "1quachuoi9vang"}, "body": f"[collab-prescreen sha={sha} status=WARN]"}],
            }
            with patch("scripts.collab_worker._fetch_pr_metadata", return_value=warn_meta):
                warn_blocked = approve_intake(gh_user="vinhthang", pr_number=19, config_path=cfg_path, allow_warn=False)
            self.assertFalse(warn_blocked["success"])

            gh_calls = []
            def fake_run_gh(self, args, cwd=None, repo=None, check=True, text=True, capture_output=True):
                gh_calls.append(args)
                return subprocess.CompletedProcess(["gh"] + args, 0, "", "")

            with patch("scripts.collab_worker._fetch_pr_metadata", return_value=warn_meta), \
                 patch("scripts.collab_worker.GitHubAccountContext.run_gh", fake_run_gh):
                warn_ok = approve_intake(gh_user="vinhthang", pr_number=19, config_path=cfg_path, allow_warn=True)
            self.assertTrue(warn_ok["success"])
            self.assertEqual(gh_calls[0][:4], ["pr", "comment", "19", "--body"])
            self.assertIn(f"/approve-intake {sha}", gh_calls[0])

    @patch("scripts.collab_worker._get_gh_token", return_value="mock_token")
    def test_submit_translation_and_submit_review_paths(self, _mock_tok):
        from scripts.collab_worker import submit_review, submit_translation

        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            cfg_path = self._write_cfg(tmp_dir)
            slug = "demo-book"
            (tmp_dir / "books" / slug).mkdir(parents=True)
            (tmp_dir / "books" / slug / "manifest.json").write_text(
                json.dumps({"slug": slug, "source_lang": "en", "target_lang": "vi"}),
                encoding="utf-8",
            )
            ch_dir = tmp_dir / "runs" / slug / "ch02"
            ch_dir.mkdir(parents=True)
            (ch_dir / "source.txt").write_text(
                '<segment id="S-0001" type="paragraph">\n<original>\nHello world\n</original>\n</segment>\n',
                encoding="utf-8",
            )
            (tmp_dir / "runs" / slug / "book_record.json").write_text(
                json.dumps({"glossary_sha": "b" * 64}), encoding="utf-8"
            )

            tx_xml = tmp_dir / "tx.xml"
            tx_xml.write_text(
                '<segment id="S-0001" type="paragraph">\n<original>\nHello world\n</original>\n<translate>\nXin chào thế giới\n</translate>\n</segment>\n',
                encoding="utf-8",
            )
            bad_tx = tmp_dir / "bad_tx.xml"
            bad_tx.write_text('<segment id="S-0001" type="paragraph">\n<original>\nMismatch\n</original>\n<translate>\nSai\n</translate>\n</segment>\n', encoding="utf-8")

            git_calls = []
            gh_calls = []
            def fake_run_git(self, args, cwd=None, check=True, text=True, capture_output=True):
                git_calls.append(args)
                if args[:2] == ["worktree", "add"]:
                    Path(args[4]).mkdir(parents=True, exist_ok=True)
                return subprocess.CompletedProcess(["git"] + args, 0, "", "")

            def fake_run_gh(self, args, cwd=None, repo=None, check=True, text=True, capture_output=True):
                gh_calls.append(args)
                if args[:2] == ["issue", "list"]:
                    return subprocess.CompletedProcess(["gh"] + args, 0, json.dumps([{"number": 21, "title": "Translate [ch02]"}]), "")
                if args[:2] == ["pr", "create"]:
                    return subprocess.CompletedProcess(["gh"] + args, 0, "https://github.com/vinhthang/collab-book-translate/pull/101\n", "")
                return subprocess.CompletedProcess(["gh"] + args, 0, "", "")

            with patch("scripts.collab_worker.GitHubAccountContext.run_git", fake_run_git), \
                 patch("scripts.collab_worker.GitHubAccountContext.run_gh", fake_run_gh):
                fail_preflight = submit_translation(
                    gh_user="vinhthang",
                    book_slug=slug,
                    chapter="ch02",
                    translation_file=str(bad_tx),
                    config_path=cfg_path,
                )
                self.assertFalse(fail_preflight["success"])
                self.assertEqual(git_calls, [])

                ok_tx = submit_translation(
                    gh_user="vinhthang",
                    book_slug=slug,
                    chapter="ch02",
                    translation_file=str(tx_xml),
                    agent_name="custom-tag",
                    claim=True,
                    config_path=cfg_path,
                )
            self.assertTrue(ok_tx["success"])
            self.assertEqual(ok_tx["pr_url"], "https://github.com/vinhthang/collab-book-translate/pull/101")
            pr_create_cmd = next(c for c in gh_calls if c[:2] == ["pr", "create"])
            self.assertIn("agent:custom-tag", pr_create_cmd)

            # Test submit_review events (APPROVE, REQUEST_CHANGES, COMMENT)
            rev_file = tmp_dir / "rev.md"
            rev_file.write_text(
                "S-0001 accurate.\n- Fidelity: 4.8/5\n- Fluency: 4.8/5\n- Terminology: 4.8/5\n"
                "- Register: 4.8/5\n- Structure: 4.8/5\n- Completeness: 4.8/5\n",
                encoding="utf-8",
            )
            mock_meta = {"author": {"login": "vinhthang"}, "headRefOid": "c" * 40}
            for ev_in, expected_flag in [
                ("APPROVE", "--approve"),
                ("REQUEST_CHANGES", "--request-changes"),
                ("COMMENT", "--comment"),
            ]:
                rev_cmds = []
                with patch("scripts.collab_worker._fetch_pr_metadata", return_value=mock_meta), \
                     patch(
                         "scripts.collab_worker.GitHubAccountContext.run_gh",
                         lambda self, args, **kw: rev_cmds.append(args) or subprocess.CompletedProcess(["gh"] + args, 0, "", ""),
                     ):
                    r_res = submit_review(
                        gh_user="1quachuoi9vang",
                        pr_number=101,
                        review_file=str(rev_file),
                        event=ev_in,
                        config_path=cfg_path,
                    )
                self.assertTrue(r_res["success"])
                self.assertIn(expected_flag, rev_cmds[0])

    def test_main_cli_dispatch_and_nonzero_exits(self):
        import sys
        from scripts.collab_worker import main as worker_main

        with patch("scripts.collab_worker.list_role_tasks", return_value={"translate_issues": []}), \
             patch.object(sys, "argv", ["collab_worker.py", "list-tasks", "--gh-user", "vinhthang"]):
            worker_main()

        for argv, target in [
            (["collab_worker.py", "submit-book", "--gh-user", "vinhthang", "--slug", "s", "--source-file", "f", "--title", "t", "--author", "a", "--source-lang", "en"], "submit_book"),
            (["collab_worker.py", "approve-intake", "--gh-user", "vinhthang", "--pr", "1"], "approve_intake"),
            (["collab_worker.py", "submit-translation", "--gh-user", "vinhthang", "--book-slug", "s", "--chapter", "ch02", "--translation-file", "f"], "submit_translation"),
            (["collab_worker.py", "submit-review", "--gh-user", "vinhthang", "--pr", "1", "--review-file", "r"], "submit_review"),
        ]:
            with patch(f"scripts.collab_worker.{target}", return_value={"success": True}), \
                 patch.object(sys, "argv", argv):
                worker_main()
            with patch(f"scripts.collab_worker.{target}", return_value={"success": False}), \
                 patch.object(sys, "argv", argv):
                with self.assertRaises(SystemExit) as ctx:
                    worker_main()
                self.assertEqual(ctx.exception.code, 1)


class TestDocAndPluginConformance(unittest.TestCase):
    def test_readme_and_contributing_roles_and_zero_vendor(self):
        repo_root = Path(__file__).resolve().parent.parent
        readme_text = (repo_root / "README.md").read_text(encoding="utf-8")
        contrib_text = (repo_root / "CONTRIBUTING.md").read_text(encoding="utf-8")

        for role in ("Role 1: Submitter", "Role 2: Translator", "Role 3: Reviewer", "Role 4: Coordinator"):
            self.assertIn(role, readme_text)

        for text in (readme_text, contrib_text):
            self.assertNotIn("workbuddy", text.lower())
            self.assertNotIn("24-hour candidate window", text.lower())
            self.assertNotIn("24h candidate window", text.lower())

    def test_skills_and_agents_frontmatter(self):
        repo_root = Path(__file__).resolve().parent.parent
        skill_file = repo_root / "skills" / "collab-translate-book" / "SKILL.md"
        translator_agent = repo_root / "agents" / "collab_translator.md"
        reviewer_agent = repo_root / "agents" / "collab_reviewer.md"

        for p in (skill_file, translator_agent, reviewer_agent):
            self.assertTrue(p.exists(), f"File missing: {p}")
            content = p.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("---"), f"{p} missing YAML frontmatter start")
            self.assertIn("name:", content)
            self.assertIn("description:", content)


if __name__ == "__main__":
    unittest.main()
