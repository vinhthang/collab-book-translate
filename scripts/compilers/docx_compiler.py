#!/usr/bin/env python3
"""
scripts/compilers/docx_compiler.py — DOCX Compiler subclassing BaseCompiler.
Generates styled Microsoft Word (.docx) publication documents from translated chapters.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import docx
from docx.document import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor

from scripts.compilers.base import BaseCompiler


class DOCXCompiler(BaseCompiler):
    """Compiles translated book into a styled Word (.docx) deliverable."""

    def compile(
        self,
        run_dir: Path,
        output_file: Optional[Path] = None,
        title: Optional[str] = None,
        author: Optional[str] = None,
        include_toc: bool = True,
        include_index: bool = True,
        chapters: Optional[str] = None,
        **kwargs,
    ) -> Path:
        run_dir = Path(run_dir).resolve()
        if not run_dir.is_dir():
            raise FileNotFoundError(f"Run directory not found: {run_dir}")

        slug = run_dir.name
        if not output_file:
            output_file = run_dir / f"{slug}_vietnamese.docx"
        output_file = Path(output_file).resolve()
        output_file.parent.mkdir(parents=True, exist_ok=True)

        book_record = self.load_book_record(run_dir)
        br_meta = book_record.get("book_record", {})

        if not title:
            title = (
                br_meta.get("title_vietnamese")
                or br_meta.get("title")
                or f"{slug.replace('_', ' ').title()} (Bản dịch tiếng Việt)"
            )
        if not author:
            author = (
                br_meta.get("author")
                or "Tác giả"
            )

        ch_filter = self.parse_chapters_filter(chapters)
        chapter_list = self.discover_chapters(run_dir, ch_filter)
        if not chapter_list:
            raise RuntimeError(f"No chapters found to compile in {run_dir}")

        doc = docx.Document()

        # 1. Title page
        title_p = doc.add_heading(title, level=0)
        title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER

        author_p = doc.add_paragraph(f"Tác giả: {author}\nBản dịch tiếng Việt")
        author_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        author_p.runs[0].font.size = Pt(13)
        author_p.runs[0].font.italic = True

        doc.add_page_break()

        # 2. Table of Contents Summary
        if include_toc:
            doc.add_heading("Mục Lục", level=1)
            for ch in chapter_list:
                p_toc = doc.add_paragraph(ch["title"], style="List Bullet")
                p_toc.paragraph_format.space_after = Pt(4)
            doc.add_page_break()

        # 3. Chapters
        for idx, ch in enumerate(chapter_list):
            if idx > 0 or include_toc:
                doc.add_page_break()

            doc.add_heading(ch["title"], level=1)
            self._render_markdown_into_docx(doc, ch["content"], run_dir)

        # 4. Save deliverable
        doc.save(str(output_file))
        return output_file

    def _render_markdown_into_docx(self, doc: Document, md_text: str, run_dir: Path) -> None:
        """Parses chapter Markdown and writes paragraphs, headings, code, and images into docx."""
        lines = md_text.splitlines()
        i = 0
        n = len(lines)

        while i < n:
            line = lines[i]

            if not line.strip():
                i += 1
                continue

            # Code fence
            if line.strip().startswith("```"):
                code_lines = []
                i += 1
                while i < n and not lines[i].strip().startswith("```"):
                    code_lines.append(lines[i])
                    i += 1
                i += 1  # skip closing fence

                code_block = "\n".join(code_lines)
                p_code = doc.add_paragraph()
                p_code.paragraph_format.left_indent = Inches(0.4)
                p_code.paragraph_format.space_before = Pt(6)
                p_code.paragraph_format.space_after = Pt(6)
                run = p_code.add_run(code_block)
                run.font.name = "Courier New"
                run.font.size = Pt(9.5)
                run.font.color.rgb = RGBColor(40, 40, 40)
                continue

            # Headings
            if line.startswith("# ") and not line.startswith("## "):
                # Already rendered as chapter heading, skip duplicate
                i += 1
                continue
            elif line.startswith("## "):
                doc.add_heading(line[3:].strip(), level=2)
                i += 1
                continue
            elif line.startswith("### "):
                doc.add_heading(line[4:].strip(), level=3)
                i += 1
                continue

            # Figure / Image
            fig_match = re.match(r'^\s*!\[(.*?)\]\((.*?)\)\s*$', line)
            if fig_match:
                alt = fig_match.group(1) or "Figure"
                src = fig_match.group(2)
                img_path = run_dir / src
                if not img_path.exists():
                    img_path = run_dir / "images" / Path(src).name

                if img_path.exists() and img_path.is_file():
                    try:
                        p_img = doc.add_paragraph()
                        p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        p_img.paragraph_format.space_before = Pt(12)
                        p_img.paragraph_format.space_after = Pt(4)
                        doc.add_picture(str(img_path), width=Inches(5.0))

                        p_cap = doc.add_paragraph(f"Hình: {alt}")
                        p_cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        p_cap.paragraph_format.space_after = Pt(12)
                        p_cap.runs[0].font.size = Pt(9.5)
                        p_cap.runs[0].font.italic = True
                    except Exception:
                        pass
                i += 1
                continue

            # Blockquote
            if line.startswith(">"):
                quote_text = line[1:].strip()
                p_quote = doc.add_paragraph()
                p_quote.paragraph_format.left_indent = Inches(0.5)
                p_quote.paragraph_format.space_before = Pt(6)
                p_quote.paragraph_format.space_after = Pt(6)
                run = p_quote.add_run(quote_text)
                run.font.italic = True
                run.font.color.rgb = RGBColor(90, 90, 90)
                i += 1
                continue

            # Lists
            if line.startswith(("- ", "* ")):
                p_list = doc.add_paragraph(style="List Bullet")
                self._add_formatted_runs(p_list, line[2:].strip())
                i += 1
                continue

            num_list_match = re.match(r'^\d+\.\s+(.*)$', line)
            if num_list_match:
                p_list = doc.add_paragraph(style="List Number")
                self._add_formatted_runs(p_list, num_list_match.group(1).strip())
                i += 1
                continue

            # Paragraph (collect until blank line or special line)
            para_lines = [line]
            i += 1
            while i < n:
                curr = lines[i]
                if not curr.strip():
                    break
                if (
                    curr.strip().startswith("```")
                    or curr.strip().startswith("#")
                    or curr.strip().startswith(">")
                    or curr.strip().startswith(("- ", "* "))
                    or re.match(r'^\s*!\[.*?\]\(.*?\)\s*$', curr)
                ):
                    break
                para_lines.append(curr)
                i += 1

            p_body = doc.add_paragraph()
            p_body.paragraph_format.space_after = Pt(8)
            p_body.paragraph_format.line_spacing = 1.2
            full_para = " ".join(l.strip() for l in para_lines)
            self._add_formatted_runs(p_body, full_para)

    def _add_formatted_runs(self, paragraph: Any, text: str) -> None:
        """Parses **bold**, *italic*, `code` tokens and appends formatted runs to paragraph."""
        tokens = re.split(r'(\*\*.*?\*\*|\*.*?\*|`.*?`)', text)
        for tok in tokens:
            if not tok:
                continue
            if tok.startswith("**") and tok.endswith("**") and len(tok) >= 4:
                run = paragraph.add_run(tok[2:-2])
                run.bold = True
            elif tok.startswith("*") and tok.endswith("*") and len(tok) >= 2:
                run = paragraph.add_run(tok[1:-1])
                run.italic = True
            elif tok.startswith("`") and tok.endswith("`") and len(tok) >= 2:
                run = paragraph.add_run(tok[1:-1])
                run.font.name = "Courier New"
                run.font.size = Pt(9.5)
            else:
                paragraph.add_run(tok)
