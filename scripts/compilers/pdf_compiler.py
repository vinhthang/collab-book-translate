#!/usr/bin/env python3
"""
scripts/compilers/pdf_compiler.py — PDF Compiler subclassing BaseCompiler.
Wraps WeasyPrint publication engine with CSS Paged Media leaders, dynamic TOC, and index.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from scripts.compile_pdf import build_safe_url_fetcher, compile_book
from scripts.compilers.base import BaseCompiler


class PDFCompiler(BaseCompiler):
    """Compiles translated book into publication-ready Vietnamese PDF via WeasyPrint."""

    def compile(
        self,
        run_dir: Path,
        output_file: Optional[Path] = None,
        title: Optional[str] = None,
        author: Optional[str] = None,
        include_toc: bool = True,
        include_index: bool = True,
        chapters: Optional[str] = None,
        allow_incomplete: bool = False,
        **kwargs,
    ) -> Path:
        run_dir = Path(run_dir).resolve()
        if not run_dir.is_dir():
            raise FileNotFoundError(f"Run directory not found: {run_dir}")

        slug = run_dir.name
        if not output_file:
            output_file = run_dir / f"{slug}_vietnamese.pdf"
        output_file = Path(output_file).resolve()
        output_file.parent.mkdir(parents=True, exist_ok=True)

        ch_filter = self.parse_chapters_filter(chapters)

        # Delegate to proven WeasyPrint pipeline in compile_pdf
        from scripts.compile_pdf import compile_book
        result_path_str = compile_book(
            run_dir=str(run_dir),
            output_pdf=str(output_file),
            chapters_filter=ch_filter,
            title=title,
            author=author,
            include_toc=include_toc,
            include_index=include_index,
            allow_incomplete=allow_incomplete,
        )
        return Path(result_path_str)
