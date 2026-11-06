"""
scripts/extractors package — Modular multi-format document extractors.
"""

from scripts.extractors.base import BaseExtractor
from scripts.extractors.pdf_extractor import PDFExtractor
from scripts.extractors.epub_extractor import EPUBExtractor
from scripts.extractors.docx_extractor import DOCXExtractor
from scripts.extractors.markdown_extractor import MarkdownExtractor
from scripts.extractors.mobi_extractor import MOBIExtractor
from scripts.extractors.text_extractor import TextExtractor

__all__ = [
    "BaseExtractor",
    "PDFExtractor",
    "EPUBExtractor",
    "DOCXExtractor",
    "MarkdownExtractor",
    "MOBIExtractor",
    "TextExtractor",
]
