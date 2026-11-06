"""
tests/test_collab_intake.py — Unit tests for book submission intake and pre-screen validation.
Tests REQ-COLLAB-001, REQ-COLLAB-002, REQ-COLLAB-007.
"""

import os
import tempfile
import unittest

from scripts.collab_intake import (
    ALLOWED_LICENSES,
    REQUIRED_MANIFEST_FIELDS,
    SUPPORTED_PAIRS,
    format_prescreen_comment,
    validate_submission_pr,
)


class TestCollabIntake(unittest.TestCase):

    def setUp(self):
        self.valid_manifest_en = {
            "slug": "alice-in-wonderland",
            "title": "Alice's Adventures in Wonderland",
            "author": "Lewis Carroll",
            "source_lang": "en",
            "target_lang": "vi",
            "license": "public-domain",
            "license_url": "https://www.gutenberg.org/ebooks/11",
            "submitter": "contributor1",
        }
        self.valid_files = [
            "books/alice-in-wonderland/manifest.json",
            "books/alice-in-wonderland/source/alice.epub",
        ]

    def test_valid_submission_en_vi(self):
        result = validate_submission_pr(
            changed_files=self.valid_files,
            manifest_data=self.valid_manifest_en,
            source_text_sample="Alice was beginning to get very tired of sitting by her sister on the bank.",
        )
        self.assertTrue(result["valid"])
        self.assertEqual(len(result["defects"]), 0)
        self.assertEqual(result["slug"], "alice-in-wonderland")

    def test_valid_submission_zh_vi(self):
        manifest_zh = {
            "slug": "sanguozhi-ch01",
            "title": "Tam Quốc Chí",
            "author": "Trần Thọ",
            "source_lang": "zh",
            "target_lang": "vi",
            "license": "CC0-1.0",
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            "submitter": "vinhthang",
        }
        files = [
            "books/sanguozhi-ch01/manifest.json",
            "books/sanguozhi-ch01/source/ch01.htm",
        ]
        result = validate_submission_pr(
            changed_files=files,
            manifest_data=manifest_zh,
            source_text_sample="文皇帝諱丕，字子桓，武帝太子也。中平四年冬，生于譙。",
        )
        self.assertTrue(result["valid"])
        self.assertEqual(result["slug"], "sanguozhi-ch01")

    def test_missing_manifest_field(self):
        bad_manifest = self.valid_manifest_en.copy()
        del bad_manifest["license_url"]
        result = validate_submission_pr(
            changed_files=self.valid_files,
            manifest_data=bad_manifest,
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("license_url" in d for d in result["defects"]))

    def test_empty_manifest_field(self):
        bad_manifest = self.valid_manifest_en.copy()
        bad_manifest["submitter"] = "   "
        result = validate_submission_pr(
            changed_files=self.valid_files,
            manifest_data=bad_manifest,
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("submitter" in d for d in result["defects"]))

    def test_disallowed_license(self):
        bad_manifest = self.valid_manifest_en.copy()
        bad_manifest["license"] = "GPL-3.0"
        result = validate_submission_pr(
            changed_files=self.valid_files,
            manifest_data=bad_manifest,
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("license" in d.lower() for d in result["defects"]))

    def test_unsupported_pair_returns_unsupported_pair_defect(self):
        bad_manifest = self.valid_manifest_en.copy()
        bad_manifest["source_lang"] = "fr"
        result = validate_submission_pr(
            changed_files=self.valid_files,
            manifest_data=bad_manifest,
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("unsupported pair" in d.lower() for d in result["defects"]))

    def test_disallowed_file_path(self):
        files = self.valid_files + ["scripts/extract.py"]
        result = validate_submission_pr(
            changed_files=files,
            manifest_data=self.valid_manifest_en,
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("allowlist" in d.lower() or "outside" in d.lower() for d in result["defects"]))

    def test_missing_source_file(self):
        files = ["books/alice-in-wonderland/manifest.json"]
        result = validate_submission_pr(
            changed_files=files,
            manifest_data=self.valid_manifest_en,
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("source" in d.lower() for d in result["defects"]))

    def test_multiple_slugs(self):
        files = [
            "books/alice-in-wonderland/manifest.json",
            "books/alice-in-wonderland/source/alice.epub",
            "books/other-book/manifest.json",
        ]
        result = validate_submission_pr(
            changed_files=files,
            manifest_data=self.valid_manifest_en,
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("multiple slugs" in d.lower() or "outside" in d.lower() for d in result["defects"]))

    def test_oversize_source_file_rejected(self):
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(b"0" * 1024)
            temp_path = tf.name
        try:
            result = validate_submission_pr(
                changed_files=self.valid_files,
                manifest_data=self.valid_manifest_en,
                source_file_path=temp_path,
                max_bytes=512,
                is_lfs_pointer=False,
            )
            self.assertFalse(result["valid"])
            self.assertTrue(any("exceeds limit (512 bytes / 50 mb)" in d.lower() for d in result["defects"]))
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_lfs_pointer_allows_large_file(self):
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(b"0" * 1024)
            temp_path = tf.name
        try:
            result = validate_submission_pr(
                changed_files=self.valid_files,
                manifest_data=self.valid_manifest_en,
                source_file_path=temp_path,
                max_bytes=512,
                is_lfs_pointer=True,
            )
            self.assertTrue(result["valid"])
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_already_translated_vietnamese_rejected(self):
        # Already translated Vietnamese text declared as en
        vi_sample = "Alice bắt đầu cảm thấy vô cùng buồn chán khi phải ngồi cạnh chị gái trên bờ sông mà không có gì để làm."
        result = validate_submission_pr(
            changed_files=self.valid_files,
            manifest_data=self.valid_manifest_en,
            source_text_sample=vi_sample,
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("vietnamese" in d.lower() or "already" in d.lower() for d in result["defects"]))

    def test_source_language_mismatch_zh_declared_but_en_sample(self):
        manifest_zh = self.valid_manifest_en.copy()
        manifest_zh["source_lang"] = "zh"
        result = validate_submission_pr(
            changed_files=self.valid_files,
            manifest_data=manifest_zh,
            source_text_sample="This is plain English without any Chinese characters at all.",
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("text detected as en, expected zh" in d.lower() for d in result["defects"]))

    def test_format_prescreen_comment_pass(self):
        result = {"valid": True, "defects": [], "slug": "alice-in-wonderland"}
        comment = format_prescreen_comment(result, self.valid_manifest_en)
        self.assertIn("## Pre-screen Summary: PASS", comment)
        self.assertIn("alice-in-wonderland", comment)
        self.assertIn("public-domain", comment)
        self.assertIn("@vinhthang", comment)

    def test_format_prescreen_comment_fail(self):
        result = {"valid": False, "defects": ["unsupported pair: fr->vi"], "slug": "alice-in-wonderland"}
        comment = format_prescreen_comment(result, self.valid_manifest_en)
        self.assertIn("## Pre-screen Summary: FAIL", comment)
        self.assertIn("unsupported pair: fr->vi", comment)

    def test_manifest_slug_mismatch_path_slug(self):
        files = [
            "books/path-slug/manifest.json",
            "books/path-slug/source/alice.epub",
        ]
        result = validate_submission_pr(
            changed_files=files,
            manifest_data=self.valid_manifest_en,  # has slug alice-in-wonderland
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("does not match path slug" in d for d in result["defects"]))

    def test_file_size_exactly_max_bytes_passes(self):
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(b"x" * 512)
            temp_path = tf.name
        try:
            result = validate_submission_pr(
                changed_files=self.valid_files,
                manifest_data=self.valid_manifest_en,
                source_file_path=temp_path,
                max_bytes=512,
                is_lfs_pointer=False,
            )
            self.assertTrue(result["valid"])
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_vietnamese_diacritic_density_exactly_five_percent_triggers_rejection(self):
        # 1 diacritic char 'à' + 19 plain chars 'abcdefghijklmnopqrs' = 20 chars total (5.0%)
        sample_5_percent = "à" + "abcdefghijklmnopqrs"
        result = validate_submission_pr(
            changed_files=self.valid_files,
            manifest_data=self.valid_manifest_en,
            source_text_sample=sample_5_percent,
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("already translated" in d.lower() for d in result["defects"]))

    def test_no_detected_slugs_when_all_files_outside_books(self):
        result = validate_submission_pr(
            changed_files=["scripts/foo.py", "docs/bar.md"],
            manifest_data=self.valid_manifest_en,
        )
        self.assertFalse(result["valid"])
        self.assertTrue(any("allowlist" in d.lower() or "outside" in d.lower() for d in result["defects"]))



if __name__ == "__main__":
    unittest.main()
