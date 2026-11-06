#!/usr/bin/env python3
"""
scripts/extract.py — Unified Multi-Format Extractor Router CLI.
Ingests PDF, EPUB, DOCX, MOBI, and Markdown documents into standardized XML segments.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from typing import Any, Dict, List, Optional, Type, Union

from scripts.extractors.base import BaseExtractor
from scripts.extractors.pdf_extractor import PDFExtractor
from scripts.extractors.epub_extractor import EPUBExtractor
from scripts.extractors.docx_extractor import DOCXExtractor
from scripts.extractors.mobi_extractor import MOBIExtractor
from scripts.extractors.markdown_extractor import MarkdownExtractor
from scripts.extractors.text_extractor import TextExtractor
from scripts.utils.language import derive_book_slug
from scripts.utils.paths import get_inputs_dir, get_runs_dir, resolve_run_dir

EXTRACTOR_REGISTRY: Dict[str, Type[BaseExtractor]] = {
    ".pdf": PDFExtractor,
    ".epub": EPUBExtractor,
    ".docx": DOCXExtractor,
    ".mobi": MOBIExtractor,
    ".azw": MOBIExtractor,
    ".azw3": MOBIExtractor,
    ".md": MarkdownExtractor,
    ".markdown": MarkdownExtractor,
    ".txt": TextExtractor,
    ".text": TextExtractor,
}


def resolve_input_source(
    input_arg: Union[str, Path],
    clone_base_dir: Optional[Path] = None,
) -> Path:
    """Resolves an input argument (path, directory, or GitHub repo) to a local Path.

    If given a GitHub repo URL (https://github.com/<org>/<repo>) or shorthand (Ancient-China-Books/<repo>),
    it shallow-clones the repo into (clone_base_dir or REPO_ROOT / "inputs") / repo_name
    if not already present.
    """
    if isinstance(input_arg, Path):
        return input_arg.resolve()

    arg_str = str(input_arg).strip()

    # If it's an existing file or directory on disk, return it directly
    p = Path(arg_str)
    if p.exists():
        return p.resolve()

    # Check for GitHub URL or shorthand
    match_url = re.match(r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", arg_str)
    match_acb = re.match(r"^Ancient-China-Books/([A-Za-z0-9_.-]+?)(?:\.git)?/?$", arg_str)
    match_shorthand = re.match(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$", arg_str)

    if match_url:
        org, repo = match_url.group(1), match_url.group(2)
        clone_url = f"https://github.com/{org}/{repo}.git"
        repo_name = repo
    elif match_acb:
        repo = match_acb.group(1)
        clone_url = f"https://github.com/Ancient-China-Books/{repo}.git"
        repo_name = repo
    elif match_shorthand:
        org, repo = match_shorthand.group(1), match_shorthand.group(2)
        clone_url = f"https://github.com/{org}/{repo}.git"
        repo_name = repo
    else:
        return p.resolve()

    base_dir = Path(clone_base_dir).resolve() if clone_base_dir else get_inputs_dir().resolve()
    target_dir = base_dir / repo_name

    if not (target_dir / ".git").exists():
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", clone_url, str(target_dir)],
            check=True,
        )

    return target_dir.resolve()


def get_extractor_for_file(file_path: Path) -> BaseExtractor:
    """Returns the appropriate extractor instance based on file extension or directory layout."""
    if file_path.is_dir():
        return EPUBExtractor()
    ext = file_path.suffix.lower()
    extractor_cls = EXTRACTOR_REGISTRY.get(ext)
    if not extractor_cls:
        supported = ", ".join(sorted(EXTRACTOR_REGISTRY.keys()))
        raise ValueError(f"Unsupported file format '{ext}' for file {file_path}. Supported: {supported}")
    return extractor_cls()


def extract_document(
    input_path: Union[str, Path],
    output_dir: Optional[Path] = None,
    chapters: Optional[str] = None,
    slug: Optional[str] = None,
    xml_segments: bool = True,
    toc_level: str = "auto",
    source_lang: str = "auto",
    **kwargs,
) -> List[Dict[str, Any]]:
    """Programmatic entry point to extract a document."""
    input_source = resolve_input_source(input_path)
    if not input_source.exists():
        raise FileNotFoundError(f"Input file not found: {input_source}")

    if not slug:
        slug = derive_book_slug(input_source)

    if not output_dir:
        output_dir = get_runs_dir() / slug
    else:
        output_dir = resolve_run_dir(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    extractor = get_extractor_for_file(input_source)
    if isinstance(extractor, PDFExtractor):
        kwargs["toc_level"] = toc_level

    results = extractor.extract(
        input_path=input_source,
        output_dir=output_dir,
        chapters=chapters,
        slug=slug,
        xml_segments=xml_segments,
        **kwargs,
    )
    return results


def main():
    parser = argparse.ArgumentParser(description="Unified Multi-Format Document Extractor (PDF, EPUB, DOCX, MOBI, MD)")
    parser.add_argument("input_path", help="Path to input document (.pdf, .epub, .docx, .mobi, .md), directory, or GitHub repo")
    parser.add_argument("--output-dir", default=None, help="Output directory (defaults to runs/<slug>)")
    parser.add_argument("--chapters", default=None, help="Chapter filter, e.g. '1-3' or '1,3,5'")
    parser.add_argument("--slug", default=None, help="Book slug (default: derived from input filename)")
    parser.add_argument("--xml-segments", dest="xml_segments", action="store_true", default=True, help="Wrap extracted content in indexed XML segments (default: True)")
    parser.add_argument("--no-xml-segments", dest="xml_segments", action="store_false", help="Do not wrap in XML segments")
    parser.add_argument("--toc-level", choices=["auto", "1", "2"], default="auto", help="TOC level to discover chapters from for PDFs ('auto', '1', or '2')")
    parser.add_argument("--source-lang", choices=["auto", "en", "zh"], default="auto", help="Source language ('auto', 'en', or 'zh')")

    args = parser.parse_args()

    input_source = resolve_input_source(args.input_path)
    if not input_source.exists():
        sys.stderr.write(f"Error: Input path not found: {input_source}\n")
        sys.exit(1)

    slug = args.slug
    if not slug:
        slug = derive_book_slug(input_source)

    if args.output_dir:
        output_dir = resolve_run_dir(args.output_dir)
    else:
        output_dir = get_runs_dir() / slug

    fmt = "directory (unpacked EPUB)" if input_source.is_dir() else input_source.suffix.lower()
    print(f"Extracting document: {input_source}")
    print(f"Format detected: {fmt}")
    print(f"Target directory: {output_dir}")

    results = extract_document(
        input_path=input_source,
        output_dir=output_dir,
        chapters=args.chapters,
        slug=slug,
        xml_segments=args.xml_segments,
        toc_level=args.toc_level,
        source_lang=args.source_lang,
    )

    print(f"Extracted {len(results)} chapters successfully to {output_dir}")


if __name__ == "__main__":
    main()
