#!/usr/bin/env python3
"""
scripts/extract_pdf.py — Backward-compatible facade delegating to PDFExtractor / extract.py.
Preserves all legacy public function exports for backward compatibility.
"""

import argparse
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from typing import Any, Dict, List, Optional, Tuple

import pymupdf

# Re-export core functions from modular PDFExtractor
from scripts.extractors.pdf_extractor import (
    MAX_CELL_CHARS,
    MAX_FOOTNOTES_PER_PAGE,
    MAX_TABLE_COLS,
    MAX_TABLE_ROWS,
    PDFExtractor,
    detect_optimal_toc_level,
    discover_chapters,
    extract_chapter_content,
    extract_page_footnotes,
    extract_tables_from_page,
    is_running_header_or_footer,
    parse_chapters_filter,
    roman_to_int,
    segment_chapter_content,
    strip_header_footer_lines,
)
from scripts.helper import dehyphenate_text


def main():
    parser = argparse.ArgumentParser(description="Extract chapters and images from PDF.")
    parser.add_argument("input_pdf", help="Path to input PDF file")
    parser.add_argument("--output-dir", default=None, help="Output directory (defaults to runs/<slug>)")
    parser.add_argument("--chapters", default=None, help="Chapter filter, e.g. '1-3' or '1'")
    parser.add_argument("--slug", default=None, help="Book slug (default: derived from pdf filename)")
    parser.add_argument("--xml-segments", action="store_true", help="Wrap extracted content in indexed XML segments")
    parser.add_argument("--toc-level", choices=["auto", "1", "2"], default="auto", help="TOC level to discover chapters from ('auto', '1', or '2')")

    args = parser.parse_args()

    pdf_path = os.path.abspath(args.input_pdf)
    if not os.path.isfile(pdf_path):
        sys.stderr.write(f"Error: PDF file not found: {pdf_path}\n")
        sys.exit(1)

    slug = args.slug
    if not slug:
        slug = re.sub(r'[^a-zA-Z0-9]+', '_', Path(pdf_path).stem).strip('_').lower()

    if args.output_dir:
        output_dir = os.path.abspath(args.output_dir)
    else:
        output_dir = os.path.abspath(os.path.join("runs", slug))

    extractor = PDFExtractor()
    extractor.extract(
        input_path=Path(pdf_path),
        output_dir=Path(output_dir),
        chapters=args.chapters,
        slug=slug,
        xml_segments=args.xml_segments,
        toc_level=args.toc_level,
    )
    print(f"Extraction complete. Output directory: {output_dir}")


if __name__ == "__main__":
    main()
