#!/usr/bin/env python3
"""
scripts/extractors/markdown_extractor.py — Markdown extractor subclassing BaseExtractor.
Extracts chapters split at H1 headings, resolves figure references, and wraps into XML segments.
"""

import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from scripts.extractors.base import BaseExtractor


class MarkdownExtractor(BaseExtractor):
    """Extracts chapters and images from Markdown (.md) documents."""

    def extract(
        self,
        input_path: Path,
        output_dir: Path,
        chapters: Optional[str] = None,
        slug: Optional[str] = None,
        xml_segments: bool = True,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        input_path = Path(input_path).resolve()
        if not input_path.is_file():
            raise FileNotFoundError(f"Markdown file not found: {input_path}")

        output_dir = Path(output_dir).resolve()
        images_dir = output_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        with open(input_path, "r", encoding="utf-8") as f:
            raw_text = f.read()

        # 1. Split into chapters at H1 boundaries
        lines = raw_text.splitlines()
        raw_chapters: List[Dict[str, Any]] = []
        current_title = "Chapter 1"
        current_lines: List[str] = []
        ch_counter = 1

        for line in lines:
            if line.startswith("# ") and not line.startswith("## "):
                if current_lines and any(l.strip() for l in current_lines):
                    raw_chapters.append({
                        "ch_num": ch_counter,
                        "title": current_title,
                        "content": "\n".join(current_lines),
                    })
                    ch_counter += 1
                    current_lines = []
                current_title = line[2:].strip() or f"Chapter {ch_counter}"
                current_lines.append(line)
            else:
                current_lines.append(line)

        if current_lines and any(l.strip() for l in current_lines):
            raw_chapters.append({
                "ch_num": ch_counter,
                "title": current_title,
                "content": "\n".join(current_lines),
            })

        if not raw_chapters:
            raw_chapters = [{
                "ch_num": 1,
                "title": "Chapter 1",
                "content": "# Chapter 1\n\nNo content found in Markdown document.",
            }]

        # Re-number chapters 1..N
        for idx, ch in enumerate(raw_chapters, start=1):
            ch["ch_num"] = idx

        # 2. Process image anchors and copy images
        img_counter = 0
        input_parent = input_path.parent

        for ch in raw_chapters:
            def replace_img(match: re.Match) -> str:
                nonlocal img_counter
                alt = match.group(1)
                img_src = match.group(2)

                # Resolve relative or absolute path
                src_candidate = Path(img_src)
                if not src_candidate.is_absolute():
                    src_candidate = input_parent / img_src

                src_resolved = src_candidate.resolve()
                if not src_resolved.is_relative_to(input_parent.resolve()):
                    raise PermissionError(
                        f"Blocked path traversal in image reference: {img_src} outside {input_parent}"
                    )

                if src_candidate.exists() and src_candidate.is_file():
                    ext = src_candidate.suffix or ".png"
                    fig_name = f"fig_md_{img_counter:03d}{ext}"
                    dest = images_dir / fig_name
                    shutil.copy2(src_candidate, dest)
                    img_counter += 1
                    return f"![{alt}](images/{fig_name})"

                # If already starts with images/fig_ or images/, copy if exists in output
                return match.group(0)

            ch["content"] = re.sub(r'!\[(.*?)\]\((.*?)\)', replace_img, ch["content"])

        # 3. Filter chapters
        ch_filter = self.parse_chapters_filter(chapters)
        if ch_filter:
            target_chapters = [ch for ch in raw_chapters if ch["ch_num"] in ch_filter]
        else:
            target_chapters = raw_chapters

        # 4. Write source.txt and checklist.json
        results = []
        for ch in target_chapters:
            ch_num = ch["ch_num"]
            source_path = self.write_chapter_source(
                output_dir=output_dir,
                ch_num=ch_num,
                content=ch["content"],
                xml_segments=xml_segments,
            )
            results.append({
                "ch_num": ch_num,
                "title": ch["title"],
                "source_path": str(source_path),
            })

        self.init_checklist(output_dir, results)
        return results
