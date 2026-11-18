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
