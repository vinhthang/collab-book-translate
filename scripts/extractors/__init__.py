"""
scripts/extractors package — Modular multi-format document extractors (lazy-loaded).
"""

from __future__ import annotations

import importlib
from typing import Any

from scripts.extractors.base import BaseExtractor

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "PDFExtractor": ("scripts.extractors.pdf_extractor", "PDFExtractor"),
    "EPUBExtractor": ("scripts.extractors.epub_extractor", "EPUBExtractor"),
    "DOCXExtractor": ("scripts.extractors.docx_extractor", "DOCXExtractor"),
    "MarkdownExtractor": ("scripts.extractors.markdown_extractor", "MarkdownExtractor"),
    "MOBIExtractor": ("scripts.extractors.mobi_extractor", "MOBIExtractor"),
    "TextExtractor": ("scripts.extractors.text_extractor", "TextExtractor"),
}

__all__ = [
    "BaseExtractor",
    "PDFExtractor",
    "EPUBExtractor",
    "DOCXExtractor",
    "MarkdownExtractor",
    "MOBIExtractor",
    "TextExtractor",
]


def __getattr__(name: str) -> Any:
    spec = _LAZY_EXPORTS.get(name)
    if spec is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_name, attr_name = spec
    mod = importlib.import_module(mod_name)
    val = getattr(mod, attr_name)
    globals()[name] = val
    return val
