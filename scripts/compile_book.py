#!/usr/bin/env python3
"""
scripts/compile_book.py — Unified Multi-Format Publication Compiler CLI.
Compiles translated books to EPUB 3 (default), PDF, and Word DOCX deliverables.
"""

import argparse
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from typing import Any, Dict, List, Optional, Type

from scripts.compilers.base import BaseCompiler
from scripts.compilers.epub_compiler import EPUBCompiler
from scripts.compilers.pdf_compiler import PDFCompiler
from scripts.compilers.docx_compiler import DOCXCompiler
from scripts.compilers.web_compiler import WebCompiler

COMPILER_REGISTRY: Dict[str, Type[BaseCompiler]] = {
    "epub": EPUBCompiler,
    "pdf": PDFCompiler,
    "docx": DOCXCompiler,
    "web": WebCompiler,
}


def compile_book_formats(
    run_dir: Path,
    output_dir: Optional[Path] = None,
    formats: str = "epub",
    title: Optional[str] = None,
    author: Optional[str] = None,
    include_toc: bool = True,
    include_index: bool = True,
    chapters: Optional[str] = None,
    allow_incomplete: bool = False,
) -> Dict[str, Path]:
    """Programmatic entry point to compile book into one or more deliverable formats."""
    run_dir = Path(run_dir).resolve()
    if not run_dir.is_dir():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    if not output_dir:
        output_dir = run_dir
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    slug = run_dir.name
    format_list = [f.strip().lower() for f in formats.split(",") if f.strip()]
    if not format_list:
        format_list = ["epub"]

    results: Dict[str, Path] = {}

    for fmt in format_list:
        compiler_cls = COMPILER_REGISTRY.get(fmt)
        if not compiler_cls:
            supported = ", ".join(sorted(COMPILER_REGISTRY.keys()))
            raise ValueError(f"Unsupported format '{fmt}'. Supported formats: {supported}")

        compiler = compiler_cls()
        if fmt == "web":
            output_file = output_dir / f"{slug}_vietnamese.web.zip"
        else:
            output_file = output_dir / f"{slug}_vietnamese.{fmt}"
        print(f"Compiling [{fmt.upper()}] deliverable -> {output_file.name}...")

        compiled_path = compiler.compile(
            run_dir=run_dir,
            output_file=output_file,
            title=title,
            author=author,
            include_toc=include_toc,
            include_index=include_index,
            chapters=chapters,
            allow_incomplete=allow_incomplete,
        )
        results[fmt] = compiled_path
        print(f"  -> Generated: {compiled_path} ({compiled_path.stat().st_size} bytes)")

    return results


def main():
    parser = argparse.ArgumentParser(description="Unified Multi-Format Publication Compiler (EPUB 3, PDF, DOCX)")
    parser.add_argument("run_dir_pos", nargs="?", default=None, help="Path to book run workspace directory (runs/<slug>)")
    parser.add_argument("--run-dir", default=None, help="Path to book run workspace directory (runs/<slug>)")
    parser.add_argument("--output-dir", default=None, help="Target directory for compiled deliverables (defaults to run-dir)")
    parser.add_argument("--formats", default="epub", help="Target formats, comma-separated (e.g. 'epub', 'epub,pdf', 'docx'). Default: 'epub'")
    parser.add_argument("--title", default=None, help="Override book title (defaults to title in book_record.json)")
    parser.add_argument("--author", default=None, help="Override author name")
    parser.add_argument("--chapters", default=None, help="Chapter filter, e.g. '1-3' or '1,3,5'")
    parser.add_argument("--allow-incomplete", action="store_true", default=False, help="Allow chapters with incomplete/source drafts without final.txt")

    # TOC switches
    parser.add_argument("--toc", dest="include_toc", action="store_true", default=True, help="Include Table of Contents (default: True)")
    parser.add_argument("--no-toc", dest="include_toc", action="store_false", help="Exclude Table of Contents")

    # Index switches
    parser.add_argument("--index", dest="include_index", action="store_true", default=True, help="Include Alphabetical Term Index (default: True)")
    parser.add_argument("--no-index", dest="include_index", action="store_false", help="Exclude Alphabetical Term Index")

    args = parser.parse_args()

    run_dir_raw = args.run_dir or args.run_dir_pos
    if not run_dir_raw:
        parser.error("the following arguments are required: --run-dir (or positional run_dir)")

    run_dir = Path(run_dir_raw).resolve()
    if not run_dir.is_dir():
        sys.stderr.write(f"Error: Run directory not found: {run_dir}\n")
        sys.exit(1)

    print(f"Compiling book from: {run_dir}")
    print(f"Requested formats: {args.formats}")

    results = compile_book_formats(
        run_dir=run_dir,
        output_dir=Path(args.output_dir) if args.output_dir else run_dir,
        formats=args.formats,
        title=args.title,
        author=args.author,
        include_toc=args.include_toc,
        include_index=args.include_index,
        chapters=args.chapters,
        allow_incomplete=args.allow_incomplete,
    )

    print("\nCompilation Summary:")
    for fmt, p in results.items():
        print(f"  • {fmt.upper()}: {p}")


if __name__ == "__main__":
    main()
