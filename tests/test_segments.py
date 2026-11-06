#!/usr/bin/env python3
"""
tests/test_segments.py — Unit tests for bilingual XML segmentation and parity validation.
Tests ALIGN-1, ALIGN-2, ALIGN-3, FINAL-1.
"""

import unittest
from scripts.extract_pdf import segment_chapter_content
from scripts.helper import (
    parse_segments,
    validate_segment_parity,
    strip_segments_to_final,
)


class TestSegments(unittest.TestCase):

    def setUp(self):
        self.sample_markdown = (
            "# Chapter 1. Basics of Concurrency\n\n"
            "Concurrency is the execution of multiple tasks.\n"
            "Threads allow concurrent execution.\n\n"
            "```rust\n"
            "fn main() {\n"
            "    println!(\"hello world\");\n"
            "}\n"
            "```\n\n"
            "![Figure](images/fig_01_0.png)\n\n"
            "## Section 1.1 Threads\n\n"
            "Here is another paragraph about threads.\n"
        )

    def test_segment_chapter_content(self):
        """Test ALIGN-1: Wrapping markdown content into typed, indexed XML segments."""
        segmented = segment_chapter_content(self.sample_markdown)
        self.assertIn('<segment id="S-0001" type="heading">', segmented)
        self.assertIn("<original>\n# Chapter 1. Basics of Concurrency\n</original>", segmented)
        self.assertIn('<segment id="S-0002" type="paragraph">', segmented)
        self.assertIn('<segment id="S-0003" type="code">', segmented)
        self.assertIn('println!("hello world");', segmented)
        self.assertIn('<segment id="S-0004" type="figure">', segmented)
        self.assertIn("![Figure](images/fig_01_0.png)", segmented)
        self.assertIn('<segment id="S-0005" type="heading">', segmented)
        self.assertIn('<segment id="S-0006" type="paragraph">', segmented)

    def test_parse_segments_source(self):
        """Test ALIGN-1: Parsing source segments without translate tag."""
        segmented = segment_chapter_content(self.sample_markdown)
        segs = parse_segments(segmented)
        self.assertEqual(len(segs), 6)
        self.assertEqual(segs[0]["id"], "S-0001")
        self.assertEqual(segs[0]["type"], "heading")
        self.assertEqual(segs[0]["original"], "# Chapter 1. Basics of Concurrency")
        self.assertIsNone(segs[0].get("translate"))

        self.assertEqual(segs[2]["type"], "code")
        self.assertIn("fn main()", segs[2]["original"])

    def test_parse_bilingual_segments(self):
        """Test ALIGN-2: Parsing bilingual segments with <translate> tags."""
        bilingual_xml = (
            '<segment id="S-0001" type="heading">\n'
            '<original>\n# Chapter 1\n</original>\n'
            '<translate>\n# Chương 1\n</translate>\n'
            '</segment>\n\n'
            '<segment id="S-0002" type="paragraph">\n'
            '<original>\nHello world.\n</original>\n'
            '<translate>\nXin chào thế giới.\n</translate>\n'
            '</segment>'
        )
        segs = parse_segments(bilingual_xml)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0]["id"], "S-0001")
        self.assertEqual(segs[0]["original"], "# Chapter 1")
        self.assertEqual(segs[0]["translate"], "# Chương 1")
        self.assertEqual(segs[1]["id"], "S-0002")
        self.assertEqual(segs[1]["original"], "Hello world.")
        self.assertEqual(segs[1]["translate"], "Xin chào thế giới.")

    def test_validate_segment_parity_success(self):
        """Test ALIGN-3: Parity validation passes on matching segments with preserved code/figure."""
        source_xml = (
            '<segment id="S-0001" type="heading">\n'
            '<original>\n# Chapter 1\n</original>\n'
            '</segment>\n\n'
            '<segment id="S-0002" type="paragraph">\n'
            '<original>\nThreads run concurrently.\n</original>\n'
            '</segment>\n\n'
            '<segment id="S-0003" type="code">\n'
            '<original>\n```rust\nlet x = 1;\n```\n</original>\n'
            '</segment>\n\n'
            '<segment id="S-0004" type="figure">\n'
            '<original>\n![Figure](images/fig_01.png)\n</original>\n'
            '</segment>'
        )
        target_xml = (
            '<segment id="S-0001" type="heading">\n'
            '<original>\n# Chapter 1\n</original>\n'
            '<translate>\n# Chương 1\n</translate>\n'
            '</segment>\n\n'
            '<segment id="S-0002" type="paragraph">\n'
            '<original>\nThreads run concurrently.\n</original>\n'
            '<translate>\nCác luồng chạy đồng thời.\n</translate>\n'
            '</segment>\n\n'
            '<segment id="S-0003" type="code">\n'
            '<original>\n```rust\nlet x = 1;\n```\n</original>\n'
            '<translate>\n```rust\nlet x = 1;\n```\n</translate>\n'
            '</segment>\n\n'
            '<segment id="S-0004" type="figure">\n'
            '<original>\n![Figure](images/fig_01.png)\n</original>\n'
            '<translate>\n![Figure](images/fig_01.png)\n</translate>\n'
            '</segment>'
        )
        is_valid, errors = validate_segment_parity(source_xml, target_xml)
        self.assertTrue(is_valid)
        self.assertEqual(errors, [])

    def test_validate_segment_parity_count_mismatch(self):
        """Test ALIGN-3: Fails when target drops a segment."""
        source_xml = (
            '<segment id="S-0001" type="heading">\n<original>\n# Ch 1\n</original>\n</segment>\n\n'
            '<segment id="S-0002" type="paragraph">\n<original>\nPara 2\n</original>\n</segment>'
        )
        target_xml = (
            '<segment id="S-0001" type="heading">\n'
            '<original>\n# Ch 1\n</original>\n'
            '<translate>\n# Chương 1\n</translate>\n'
            '</segment>'
        )
        is_valid, errors = validate_segment_parity(source_xml, target_xml)
        self.assertFalse(is_valid)
        self.assertTrue(any("count mismatch" in e for e in errors))

    def test_validate_segment_parity_id_mismatch(self):
        """Test ALIGN-3: Fails when IDs are reordered or wrong."""
        source_xml = (
            '<segment id="S-0001" type="paragraph">\n<original>\nP1\n</original>\n</segment>\n\n'
            '<segment id="S-0002" type="paragraph">\n<original>\nP2\n</original>\n</segment>'
        )
        target_xml = (
            '<segment id="S-0002" type="paragraph">\n'
            '<original>\nP2\n</original>\n'
            '<translate>\nP2 translated\n</translate>\n'
            '</segment>\n\n'
            '<segment id="S-0001" type="paragraph">\n'
            '<original>\nP1\n</original>\n'
            '<translate>\nP1 translated\n</translate>\n'
            '</segment>'
        )
        is_valid, errors = validate_segment_parity(source_xml, target_xml)
        self.assertFalse(is_valid)
        self.assertTrue(any("Segment ID mismatch" in e for e in errors))

    def test_validate_segment_parity_code_mutation(self):
        """Test ALIGN-3 & ASSET-2: Fails when code block inside translate is mutated."""
        source_xml = (
            '<segment id="S-0001" type="code">\n'
            '<original>\n```rust\nfn main() {}\n```\n</original>\n'
            '</segment>'
        )
        target_xml = (
            '<segment id="S-0001" type="code">\n'
            '<original>\n```rust\nfn main() {}\n```\n</original>\n'
            '<translate>\n```rust\nfn main() { /* translated comment */ }\n```\n</translate>\n'
            '</segment>'
        )
        is_valid, errors = validate_segment_parity(source_xml, target_xml)
        self.assertFalse(is_valid)
        self.assertTrue(any("Byte invariance violation in code segment" in e for e in errors))

    def test_validate_segment_parity_figure_mutation(self):
        """Test ALIGN-3 & ASSET-1: Fails when figure anchor is altered."""
        source_xml = (
            '<segment id="S-0001" type="figure">\n'
            '<original>\n![Figure](images/fig_01.png)\n</original>\n'
            '</segment>'
        )
        target_xml = (
            '<segment id="S-0001" type="figure">\n'
            '<original>\n![Figure](images/fig_01.png)\n</original>\n'
            '<translate>\n![Hình](images/fig_01_vi.png)\n</translate>\n'
            '</segment>'
        )
        is_valid, errors = validate_segment_parity(source_xml, target_xml)
        self.assertFalse(is_valid)
        self.assertTrue(any("Byte invariance violation in figure segment" in e for e in errors))

    def test_strip_segments_to_final(self):
        """Test FINAL-1: Extracts solely translate text into clean Markdown."""
        bilingual_xml = (
            '<segment id="S-0001" type="heading">\n'
            '<original>\n# Chapter 1\n</original>\n'
            '<translate>\n# Chương 1: Cơ bản về Đa luồng\n</translate>\n'
            '</segment>\n\n'
            '<segment id="S-0002" type="paragraph">\n'
            '<original>\nConcurrency allows multiple threads.\n</original>\n'
            '<translate>\nĐồng thời cho phép nhiều luồng chạy cùng lúc.\n</translate>\n'
            '</segment>\n\n'
            '<segment id="S-0003" type="code">\n'
            '<original>\n```rust\nfn main() {}\n```\n</original>\n'
            '<translate>\n```rust\nfn main() {}\n```\n</translate>\n'
            '</segment>\n\n'
            '<segment id="S-0004" type="figure">\n'
            '<original>\n![Figure](images/fig_01.png)\n</original>\n'
            '<translate>\n![Figure](images/fig_01.png)\n</translate>\n'
            '</segment>'
        )
        final_md = strip_segments_to_final(bilingual_xml)
        self.assertNotIn("<segment", final_md)
        self.assertNotIn("<translate>", final_md)
        self.assertNotIn("<original>", final_md)
        self.assertIn("# Chương 1: Cơ bản về Đa luồng", final_md)
        self.assertIn("Đồng thời cho phép nhiều luồng chạy cùng lúc.", final_md)
        self.assertIn("```rust\nfn main() {}\n```", final_md)
        self.assertIn("![Figure](images/fig_01.png)", final_md)

    def test_strip_segments_to_final_passthrough(self):
        """Test FINAL-1: Plain markdown passes through unchanged."""
        plain_md = "# Title\n\nParagraph text.\n"
        self.assertEqual(strip_segments_to_final(plain_md), plain_md)


if __name__ == "__main__":
    unittest.main()
