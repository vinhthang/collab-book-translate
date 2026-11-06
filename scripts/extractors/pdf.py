"""
scripts/extractors/pdf.py — Backward-compatible module alias delegating to pdf_extractor.py.
"""

from scripts.extractors.pdf_extractor import (
    PDFExtractor,
    roman_to_int,
    parse_chapters_filter,
    detect_optimal_toc_level,
    discover_chapters,
    is_running_header_or_footer,
    strip_header_footer_lines,
    segment_chapter_content,
    extract_chapter_content,
)
from scripts.helper import dehyphenate_text

__all__ = [
    "PDFExtractor",
    "roman_to_int",
    "parse_chapters_filter",
    "detect_optimal_toc_level",
    "discover_chapters",
    "is_running_header_or_footer",
    "strip_header_footer_lines",
    "segment_chapter_content",
    "extract_chapter_content",
    "dehyphenate_text",
]
