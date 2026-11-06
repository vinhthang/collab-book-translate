#!/usr/bin/env python3
"""
scripts/extractors/docx_extractor.py — DOCX extractor subclassing BaseExtractor.
Extracts chapters, headings, paragraphs, and embedded figures from Word documents into XML segments.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import docx
from docx.document import Document
from docx.text.paragraph import Paragraph

from scripts.extractors.base import BaseExtractor
from scripts.utils.language import CHINESE_CHAPTER_HEADING_RE, is_chinese_chapter_heading


def is_h1(para: Any) -> bool:
    """Checks if a paragraph or text represents a Heading 1 or chapter boundary."""
    if hasattr(para, "text"):
        text = para.text.strip()
        style_name = para.style.name if para.style else "Normal"
        if style_name.lower() in ("heading 1", "title"):
            return True
    else:
        text = str(para).strip()
    if is_chinese_chapter_heading(text) or CHINESE_CHAPTER_HEADING_RE.match(text):
        return True
    return bool(re.match(r'^(?:#\s+|Chapter|Chương|Part|Phần)', text, re.IGNORECASE))


class DOCXExtractor(BaseExtractor):
    """Extracts chapters and images from Microsoft Word (.docx) files."""

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
            raise FileNotFoundError(f"DOCX file not found: {input_path}")

        output_dir = Path(output_dir).resolve()
        images_dir = output_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        doc = docx.Document(str(input_path))

        # 1. Extract embedded images from document relationships
        image_map: Dict[str, str] = {}
        img_counter = 0

        for rel_id, rel in doc.part.rels.items():
            if "image" in rel.target_ref.lower() and hasattr(rel, "target_part"):
                try:
                    img_bytes = rel.target_part.blob
                    ext = Path(rel.target_ref).suffix or ".png"
                    fig_name = f"fig_docx_{img_counter:03d}{ext}"
                    target_path = images_dir / fig_name
                    with open(target_path, "wb") as f:
                        f.write(img_bytes)

                    rel_path = f"images/{fig_name}"
                    image_map[rel_id] = rel_path
                    image_map[rel.target_ref] = rel_path
                    img_counter += 1
                except Exception:
                    continue

        # 2. Iterate paragraphs and partition chapters
        raw_chapters: List[Dict[str, Any]] = []
        current_title = "Chapter 1"
        current_blocks: List[str] = []
        ch_counter = 1

        for p in doc.paragraphs:
            text = p.text.strip()
            style_name = p.style.name if p.style else "Normal"

            # Check for embedded image in paragraph
            embed_rids = p._element.xpath('.//@r:embed')
            fig_lines = []
            for r_id in embed_rids:
                if r_id in image_map:
                    fig_lines.append(f"![Figure]({image_map[r_id]})")

            # Check if paragraph is Heading 1 or chapter boundary
            if is_h1(p) and text:
                if current_blocks:
                    raw_chapters.append({
                        "ch_num": ch_counter,
                        "title": current_title,
                        "content": "\n\n".join(current_blocks),
                    })
                    ch_counter += 1
                    current_blocks = []

                clean_heading = re.sub(r'^#+\s*', '', text)
                current_title = clean_heading or f"Chapter {ch_counter}"
                current_blocks.append(f"# {current_title}")

                if fig_lines:
                    current_blocks.extend(fig_lines)
                continue

            # Handle Heading 2 and Heading 3
            if style_name.lower() == "heading 2" and text:
                clean_h2 = re.sub(r'^#+\s*', '', text)
                current_blocks.append(f"## {clean_h2}")
            elif style_name.lower() == "heading 3" and text:
                clean_h3 = re.sub(r'^#+\s*', '', text)
                current_blocks.append(f"### {clean_h3}")
            elif style_name.lower().startswith("list") and text:
                prefix = "- " if "bullet" in style_name.lower() else "1. "
                current_blocks.append(f"{prefix}{text}")
            elif text:
                # Format runs (bold, italic, code)
                formatted_p = self._format_paragraph_runs(p)
                if formatted_p:
                    current_blocks.append(formatted_p)

            if fig_lines:
                current_blocks.extend(fig_lines)

        # Append last chapter
        if current_blocks:
            raw_chapters.append({
                "ch_num": ch_counter,
                "title": current_title,
                "content": "\n\n".join(current_blocks),
            })

        if not raw_chapters:
            raw_chapters = [{
                "ch_num": 1,
                "title": "Chapter 1",
                "content": "# Chapter 1\n\nNo text content found in DOCX.",
            }]

        # Re-number chapters sequentially 1..N
        for idx, ch in enumerate(raw_chapters, start=1):
            ch["ch_num"] = idx

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

    def _format_paragraph_runs(self, p: Paragraph) -> str:
        """Converts paragraph runs to Markdown inline formatting."""
        run_texts = []
        for run in p.runs:
            t = run.text
            if not t:
                continue
            if run.bold and run.italic:
                t = f"***{t}***"
            elif run.bold:
                t = f"**{t}**"
            elif run.italic:
                t = f"*{t}*"
            elif run.font.name and "code" in run.font.name.lower():
                t = f"`{t}`"
            run_texts.append(t)
        return "".join(run_texts).strip() if run_texts else p.text.strip()
