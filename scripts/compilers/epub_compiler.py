#!/usr/bin/env python3
"""
scripts/compilers/epub_compiler.py — EPUB 3 Compiler subclassing BaseCompiler.
Generates publication-ready EPUB 3 archives with navigation (nav.xhtml),
back-of-the-book term index (index.xhtml), cover/styles, and embedded figures.
"""

import collections
import datetime
import html
import json
import mimetypes
import os
import re
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import markdown

from scripts.compilers.base import BaseCompiler
from scripts.utils.language import get_pinyin_initial, is_cjk_codepoint


def format_smil_clock(sec: float) -> str:
    """Formats float seconds into SMIL clock format 0:00:00.000."""
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:06.3f}"


class EPUBCompiler(BaseCompiler):
    """Compiles translated chapters into a publication-grade EPUB 3 archive."""

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
            output_file = run_dir / f"{slug}_vietnamese.epub"
        output_file = Path(output_file).resolve()
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # 1. Load book record & metadata
        book_record = self.load_book_record(run_dir)
        br_meta = book_record.get("book_record", {})

        if not title:
            title = (
                book_record.get("title_vietnamese")
                or book_record.get("title")
                or br_meta.get("title_vietnamese")
                or br_meta.get("title")
                or f"{slug.replace('_', ' ').title()} (Bản dịch tiếng Việt)"
            )
        if not author:
            author = (
                book_record.get("author")
                or br_meta.get("author")
                or "Tác giả"
            )

        ch_filter = self.parse_chapters_filter(chapters)
        chapter_list = self.discover_chapters(run_dir, ch_filter)
        if not chapter_list:
            raise RuntimeError(f"No chapters found to compile in {run_dir}")

        terms = self.extract_terms_from_book_record(book_record)

        # 2. Process chapters, inject term anchors, track anchor locations
        anchor_tracker: Dict[str, List[Tuple[str, str, str]]] = collections.defaultdict(list) # term -> [(anchor_id, ch_filename, ch_title)]
        global_term_counts: Dict[str, List[str]] = collections.defaultdict(list)
        processed_chapters: List[Dict[str, Any]] = []
        audio_overlays: Dict[str, Dict[str, Any]] = {}
        audio_dir = run_dir / "audio"

        for ch in chapter_list:
            ch_num = ch["ch_num"]
            ch_file = f"ch{ch_num:02d}.xhtml"
            content_md = ch["content"]

            ch_term_counts: Dict[str, int] = {}
            prev_counts = {term: len(ids) for term, ids in global_term_counts.items()}

            if include_index and terms:
                # Inject term anchors into markdown before converting
                content_md = self.inject_term_anchors(
                    content_md,
                    terms=terms,
                    anchor_tracker=global_term_counts,
                    ch_term_counts=ch_term_counts,
                )
                for src_term, a_ids in global_term_counts.items():
                    start_idx = prev_counts.get(src_term, 0)
                    new_ids = a_ids[start_idx:]
                    for a_id in new_ids:
                        anchor_tracker[src_term].append((a_id, ch_file, ch["title"]))

            # Convert markdown to XHTML body
            html_body = self.markdown_to_xhtml(content_md)

            # Check for chapter audio manifest (REQ-007)
            ch_manifest_p = audio_dir / f"ch{ch_num:02d}.manifest.json"
            ch_audio_p = audio_dir / f"ch{ch_num:02d}.m4a"
            if ch_manifest_p.is_file() and ch_audio_p.is_file():
                try:
                    with open(ch_manifest_p, "r", encoding="utf-8") as mf:
                        m_data = json.load(mf)
                    utts = m_data.get("utterances", [])
                    html_body, matched_utt_ids = self._inject_utterance_span_anchors(html_body, utts, return_matched_set=True)

                    smil_xml = self._build_smil(
                        ch_num=ch_num,
                        ch_file=ch_file,
                        audio_filename=f"ch{ch_num:02d}.m4a",
                        utterances=[u for u in utts if u.get("utt_id") in matched_utt_ids],
                    )
                    tot_sec = m_data.get("total_duration_ms", 0) / 1000.0
                    if not tot_sec and utts:
                        tot_sec = utts[-1].get("clip_end_sec", 0.0)

                    audio_overlays[ch_file] = {
                        "smil_id": f"smil_ch{ch_num:02d}",
                        "smil_file": f"smil/ch{ch_num:02d}.smil",
                        "audio_id": f"audio_ch{ch_num:02d}",
                        "audio_file": f"audio/ch{ch_num:02d}.m4a",
                        "audio_local_path": ch_audio_p,
                        "smil_xml": smil_xml,
                        "duration_sec": tot_sec,
                    }
                except Exception as ex:
                    print(f"Warning: Failed to process audio overlay for ch{ch_num:02d}: {ex}")

            # Wrap in full valid XHTML5 document
            xhtml_doc = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<!DOCTYPE html>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="vi" lang="vi">\n'
                '<head>\n'
                f'  <title>{html.escape(ch["title"])}</title>\n'
                '  <meta charset="utf-8"/>\n'
                '  <link rel="stylesheet" type="text/css" href="style.css"/>\n'
                '</head>\n'
                '<body>\n'
                f'{html_body}\n'
                '</body>\n'
                '</html>\n'
            )

            try:
                import xml.etree.ElementTree as ET
                ET.fromstring(xhtml_doc)
            except ET.ParseError:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(xhtml_doc, "html.parser")
                xhtml_doc = str(soup)

            processed_chapters.append({
                "ch_num": ch_num,
                "ch_file": ch_file,
                "title": ch["title"],
                "xhtml": xhtml_doc,
                "subheadings": ch.get("subheadings", []),
            })

        # 3. Build Stylesheet (style.css)
        style_css = self._build_style_css()

        # Shared unique publication identifier for OPF and NCX
        pub_id = f"urn:uuid:{uuid.uuid4()}"

        # 4. Build Navigation Documents (nav.xhtml & toc.ncx)
        nav_xhtml = self._build_nav_xhtml(title, processed_chapters, include_index and bool(terms))
        toc_ncx = self._build_toc_ncx(title, author, processed_chapters, include_index and bool(terms), pub_id=pub_id)

        # 5. Build Back-of-the-Book Index (index.xhtml)
        index_xhtml = ""
        if include_index and terms:
            index_xhtml = self._build_index_xhtml(terms, anchor_tracker)

        # 6. Gather images from run_dir/images
        images_dir = run_dir / "images"
        image_files = []
        if images_dir.is_dir():
            for img in images_dir.iterdir():
                if img.is_file() and img.suffix.lower() in (
                    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"
                ):
                    image_files.append(img)

        # 7. Build OPF Package Document (content.opf)
        content_opf = self._build_content_opf(
            title=title,
            author=author,
            slug=slug,
            chapters=processed_chapters,
            has_index=bool(index_xhtml),
            image_files=image_files,
            pub_id=pub_id,
            include_toc=include_toc,
            audio_overlays=audio_overlays,
        )

        # 8. Package valid EPUB 3 zip archive
        # Must write 'mimetype' uncompressed first
        with zipfile.ZipFile(output_file, "w") as zf:
            # 8a. mimetype (uncompressed, must be first entry)
            mimetype_bytes = b"application/epub+zip"
            zf.writestr("mimetype", mimetype_bytes, compress_type=zipfile.ZIP_STORED)

            # 8b. META-INF/container.xml
            container_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                '  <rootfiles>\n'
                '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
                '  </rootfiles>\n'
                '</container>\n'
            )
            zf.writestr("META-INF/container.xml", container_xml, compress_type=zipfile.ZIP_DEFLATED)

            # 8c. OEBPS contents
            zf.writestr("OEBPS/content.opf", content_opf, compress_type=zipfile.ZIP_DEFLATED)
            zf.writestr("OEBPS/style.css", style_css, compress_type=zipfile.ZIP_DEFLATED)
            zf.writestr("OEBPS/nav.xhtml", nav_xhtml, compress_type=zipfile.ZIP_DEFLATED)
            zf.writestr("OEBPS/toc.ncx", toc_ncx, compress_type=zipfile.ZIP_DEFLATED)

            # Chapters
            for ch in processed_chapters:
                zf.writestr(f"OEBPS/{ch['ch_file']}", ch["xhtml"], compress_type=zipfile.ZIP_DEFLATED)

            # Audio overlays (REQ-007)
            if audio_overlays:
                for ov in audio_overlays.values():
                    zf.writestr(f"OEBPS/{ov['smil_file']}", ov["smil_xml"], compress_type=zipfile.ZIP_DEFLATED)
                    if Path(ov["audio_local_path"]).is_file():
                        zf.write(ov["audio_local_path"], arcname=f"OEBPS/{ov['audio_file']}", compress_type=zipfile.ZIP_DEFLATED)

            # Index
            if index_xhtml:
                zf.writestr("OEBPS/index.xhtml", index_xhtml, compress_type=zipfile.ZIP_DEFLATED)

            # Images
            for img in image_files:
                img_data = img.read_bytes()
                zf.writestr(f"OEBPS/images/{img.name}", img_data, compress_type=zipfile.ZIP_DEFLATED)

        return output_file

    def _build_style_css(self) -> str:
        """Constructs clean Vietnamese typography stylesheet."""
        return (
            '@charset "UTF-8";\n'
            'html, body {\n'
            '  margin: 1.5em 2em;\n'
            '  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;\n'
            '  line-height: 1.7;\n'
            '  color: #2c3e50;\n'
            '  text-rendering: optimizeLegibility;\n'
            '}\n'
            'h1, h2, h3, h4 {\n'
            '  color: #1a252f;\n'
            '  line-height: 1.3;\n'
            '  margin-top: 1.5em;\n'
            '  margin-bottom: 0.6em;\n'
            '}\n'
            'h1 { font-size: 2em; border-bottom: 2px solid #eaecef; padding-bottom: 0.3em; }\n'
            'h2 { font-size: 1.5em; border-bottom: 1px solid #eaecef; padding-bottom: 0.2em; }\n'
            'h3 { font-size: 1.25em; }\n'
            'p { margin: 0.8em 0; text-align: justify; }\n'
            'code {\n'
            '  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, Courier, monospace;\n'
            '  font-size: 0.9em;\n'
            '  background-color: #f6f8fa;\n'
            '  padding: 0.2em 0.4em;\n'
            '  border-radius: 3px;\n'
            '}\n'
            'pre {\n'
            '  background-color: #f6f8fa;\n'
            '  padding: 1em;\n'
            '  border-radius: 6px;\n'
            '  overflow-x: auto;\n'
            '}\n'
            'pre code { padding: 0; background: none; }\n'
            'blockquote {\n'
            '  margin: 1em 0;\n'
            '  padding: 0 1em;\n'
            '  color: #6a737d;\n'
            '  border-left: 0.25em solid #dfe2e5;\n'
            '}\n'
            'img {\n'
            '  max-width: 100%;\n'
            '  height: auto;\n'
            '  display: block;\n'
            '  margin: 1.5em auto;\n'
            '}\n'
            '/* Back-of-the-book Alphabetical Index Styles */\n'
            '.index-section {\n'
            '  column-count: 2;\n'
            '  column-gap: 2.5em;\n'
            '  margin-top: 1.5em;\n'
            '}\n'
            '.index-group {\n'
            '  break-inside: avoid;\n'
            '  margin-bottom: 1.5em;\n'
            '}\n'
            '.index-letter {\n'
            '  font-size: 1.3em;\n'
            '  font-weight: bold;\n'
            '  color: #2980b9;\n'
            '  border-bottom: 1px solid #bdc3c7;\n'
            '  margin-bottom: 0.4em;\n'
            '}\n'
            '.index-entry {\n'
            '  font-size: 0.95em;\n'
            '  margin: 0.35em 0;\n'
            '  line-height: 1.4;\n'
            '}\n'
            '.index-source { font-weight: 600; color: #2c3e50; }\n'
            '.index-target { font-style: italic; color: #555; }\n'
            '.index-links a { color: #2980b9; text-decoration: none; margin-left: 0.3em; }\n'
            '.index-links a:hover { text-decoration: underline; }\n'
            '/* EPUB 3 Media Overlays Active Audio Highlight (REQ-007) */\n'
            '.-epub-media-overlay-active {\n'
            '  background-color: #fef08a;\n'
            '  color: #1a252f;\n'
            '  border-radius: 2px;\n'
            '}\n'
        )

    def markdown_to_xhtml(self, content_md: str) -> str:
        """Converts Markdown to XHTML body, converting footnotes to semantic <aside> and assigns id="sub-{idx}" to <h2> tags."""
        fn_map: Dict[str, str] = {}
        fn_style: Dict[str, str] = {}
        footnote_section_title = "Chú thích:"

        # Match ### Chú thích:, ### Tài liệu tham khảo, ## References, etc.
        sec_m = re.search(
            r'^(#{2,3})\s+(Chú thích|Tài liệu tham khảo|References):?\s*$',
            content_md,
            re.MULTILINE | re.IGNORECASE,
        )
        if sec_m:
            split_idx = sec_m.start()
            footnote_section_title = sec_m.group(2)
            body_md = content_md[:split_idx]
            chu_thich_section = content_md[split_idx:]

            # Parse footnote definitions line by line to support multi-line items
            current_num = None
            for line in chu_thich_section.splitlines():
                line_str = line.strip()
                if not line_str or sec_m.group(0).strip().lower() in line_str.lower():
                    continue

                m_paren = re.match(r'^\((\d{1,4})\)\s*(.+)$', line_str)
                m_bracket = re.match(r'^\[(\d{1,4})\]\s*(.+)$', line_str)
                if m_paren:
                    current_num = m_paren.group(1)
                    fn_map[current_num] = m_paren.group(2).strip()
                    fn_style[current_num] = "paren"
                elif m_bracket:
                    current_num = m_bracket.group(1)
                    fn_map[current_num] = m_bracket.group(2).strip()
                    fn_style[current_num] = "bracket"
                elif current_num is not None:
                    # Continuation line for the current footnote
                    fn_map[current_num] += " " + line_str
        else:
            body_md = content_md

        seen_fnrefs: Set[str] = set()

        def _replace_paren_fnref(match: re.Match) -> str:
            num = match.group(1) or match.group(2)
            if num in fn_map and fn_style.get(num) == "paren":
                plain_text = re.sub(r'[*_`#\[\]]', '', fn_map[num]).strip()
                plain_title = html.escape(plain_text, quote=True)
                if num not in seen_fnrefs:
                    seen_fnrefs.add(num)
                    id_attr = f' id="fnref-{num}"'
                else:
                    id_attr = ''
                return f'<sup class="footnote-ref"><a{id_attr} href="#fn-{num}" epub:type="noteref" role="doc-noteref" title="{plain_title}">({num})</a></sup>'
            return match.group(0)

        def _replace_bracket_fnref(match: re.Match) -> str:
            num = match.group(1)
            if num in fn_map and fn_style.get(num) == "bracket":
                plain_text = re.sub(r'[*_`#\[\]]', '', fn_map[num]).strip()
                plain_title = html.escape(plain_text, quote=True)
                if num not in seen_fnrefs:
                    seen_fnrefs.add(num)
                    id_attr = f' id="fnref-{num}"'
                else:
                    id_attr = ''
                return f'<sup class="footnote-ref"><a{id_attr} href="#fn-{num}" epub:type="noteref" role="doc-noteref" title="{plain_title}">[{num}]</a></sup>'
            return match.group(0)

        # Replace parenthetical footnotes (N)
        body_md = re.sub(
            r'(?<=[a-zA-ZÀ-Ỹa-ỹ0-9_])\((\d{1,4})\)|\((\d{1,4})\)(?=\s|[.,;:!?]|$)',
            _replace_paren_fnref,
            body_md,
        )

        # Replace bracketed citations [N] (excluding markdown links [N](url), images ![N], and defs [N]:)
        body_md = re.sub(
            r'(?<!\!)\[(\d{1,4})\](?!\s*[\(:])',
            _replace_bracket_fnref,
            body_md,
        )

        html_body = markdown.markdown(
            body_md,
            extensions=["tables", "fenced_code"],
            output_format="xhtml",
        )
        # Ensure image paths point to local relative images/ directory
        html_body = re.sub(r'src="(?:\.\./)?images/', 'src="images/', html_body)

        # Assign id="sub-{idx}" to <h2> tags in sequential order
        h2_counter = 1
        def _replace_h2(match: re.Match) -> str:
            nonlocal h2_counter
            attrs = match.group(1) or ""
            if 'id="' in attrs:
                new_attrs = re.sub(r'id="[^"]*"', f'id="sub-{h2_counter}"', attrs)
            else:
                new_attrs = f' id="sub-{h2_counter}"' + attrs
            h2_counter += 1
            return f'<h2{new_attrs}>'

        html_body = re.sub(r'<h2(\b[^>]*)>', _replace_h2, html_body)

        # Append structured footnotes if present
        if fn_map:
            aside_items = []
            for num, text in fn_map.items():
                style = fn_style.get(num, "paren")
                lbl = f"({num})" if style == "paren" else f"[{num}]"
                formatted = html.escape(text, quote=False)
                formatted = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', formatted)
                formatted = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', formatted)
                formatted = re.sub(r'\*([^*]+?)\*', r'<em>\1</em>', formatted)
                aside_items.append(
                    f'<aside epub:type="footnote" role="doc-footnote" id="fn-{num}" class="footnote-item">'
                    f'<p><a href="#fnref-{num}" epub:type="backlink" role="doc-backlink" class="footnote-backref">{lbl}</a> {formatted}</p>'
                    f'</aside>'
                )
            asides_str = "\n".join(aside_items)
            sec_heading = footnote_section_title.strip()
            if not sec_heading.endswith(":"):
                sec_heading += ":"
            html_body += f'\n<section class="footnotes" epub:type="footnotes">\n<h3>{html.escape(sec_heading)}</h3>\n{asides_str}\n</section>'

        return html_body

    def _build_nav_xhtml(
        self,
        title: str,
        chapters: List[Dict[str, Any]],
        has_index: bool,
    ) -> str:
        """Builds HTML5 nav.xhtml Table of Contents with nested Level 2 subheadings."""
        items = []
        for ch in chapters:
            subs = [s for s in ch.get("subheadings", []) if not s.get("title", "").strip().startswith("Chú thích")]
            if subs:
                sub_items = "\n".join(
                    f'            <li><a href="{ch["ch_file"]}#{s["id"]}">{html.escape(s["title"])}</a></li>'
                    for s in subs
                )
                items.append(
                    f'        <li>\n'
                    f'          <a href="{ch["ch_file"]}">{html.escape(ch["title"])}</a>\n'
                    f'          <ol>\n{sub_items}\n          </ol>\n'
                    f'        </li>'
                )
            else:
                items.append(f'        <li><a href="{ch["ch_file"]}">{html.escape(ch["title"])}</a></li>')
        if has_index:
            items.append('        <li><a href="index.xhtml">Bảng Tra Cứu Thuật Ngữ</a></li>')

        nav_items_str = "\n".join(items)
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="vi" lang="vi">\n'
            '<head>\n'
            f'  <title>{html.escape(title)} - Mục Lục</title>\n'
            '  <meta charset="utf-8"/>\n'
            '  <link rel="stylesheet" type="text/css" href="style.css"/>\n'
            '</head>\n'
            '<body>\n'
            '  <nav epub:type="toc" id="toc">\n'
            '    <h1>Mục Lục</h1>\n'
            '    <ol>\n'
            f'{nav_items_str}\n'
            '    </ol>\n'
            '  </nav>\n'
            '</body>\n'
            '</html>\n'
        )

    def _build_toc_ncx(
        self,
        title: str,
        author: str,
        chapters: List[Dict[str, Any]],
        has_index: bool,
        pub_id: str = "urn:uuid:agent-translator-book",
    ) -> str:
        """Builds EPUB 2 backward-compatible toc.ncx with nested subheadings."""
        nav_points = []
        play_order = 1
        has_subheadings = False
        for ch in chapters:
            subs = [s for s in ch.get("subheadings", []) if not s.get("title", "").strip().startswith("Chú thích")]
            ch_play_order = play_order
            play_order += 1
            if subs:
                has_subheadings = True
                sub_navs = []
                for s in subs:
                    sub_navs.append(
                        f'      <navPoint id="navpoint-{play_order}" playOrder="{play_order}">\n'
                        f'        <navLabel><text>{html.escape(s["title"])}</text></navLabel>\n'
                        f'        <content src="{ch["ch_file"]}#{s["id"]}"/>\n'
                        f'      </navPoint>'
                    )
                    play_order += 1
                sub_navs_str = "\n".join(sub_navs)
                nav_points.append(
                    f'    <navPoint id="navpoint-{ch_play_order}" playOrder="{ch_play_order}">\n'
                    f'      <navLabel><text>{html.escape(ch["title"])}</text></navLabel>\n'
                    f'      <content src="{ch["ch_file"]}"/>\n'
                    f'{sub_navs_str}\n'
                    f'    </navPoint>'
                )
            else:
                nav_points.append(
                    f'    <navPoint id="navpoint-{ch_play_order}" playOrder="{ch_play_order}">\n'
                    f'      <navLabel><text>{html.escape(ch["title"])}</text></navLabel>\n'
                    f'      <content src="{ch["ch_file"]}"/>\n'
                    f'    </navPoint>'
                )

        if has_index:
            nav_points.append(
                f'    <navPoint id="navpoint-{play_order}" playOrder="{play_order}">\n'
                f'      <navLabel><text>Bảng Tra Cứu Thuật Ngữ</text></navLabel>\n'
                f'      <content src="index.xhtml"/>\n'
                f'    </navPoint>'
            )
            play_order += 1

        depth = 2 if has_subheadings else 1
        nav_points_str = "\n".join(nav_points)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
            '  <head>\n'
            f'    <meta name="dtb:uid" content="{pub_id}"/>\n'
            f'    <meta name="dtb:depth" content="{depth}"/>\n'
            '    <meta name="dtb:totalPageCount" content="0"/>\n'
            '    <meta name="dtb:maxPageNumber" content="0"/>\n'
            '  </head>\n'
            f'  <docTitle><text>{html.escape(title)}</text></docTitle>\n'
            f'  <docAuthor><text>{html.escape(author)}</text></docAuthor>\n'
            '  <navMap>\n'
            f'{nav_points_str}\n'
            '  </navMap>\n'
            '</ncx>\n'
        )

    def _build_index_xhtml(
        self,
        terms: List[Dict[str, str]],
        anchor_tracker: Dict[str, List[Tuple[str, str, str]]],
    ) -> str:
        """Builds two-column alphabetical back-of-the-book index."""
        # Group terms by initial English letter A-Z
        groups: Dict[str, List[Dict[str, str]]] = collections.defaultdict(list)
        for t in terms:
            src = t.get("source", "").strip()
            if not src:
                continue
            first_char = src[0].upper()
            if is_cjk_codepoint(ord(first_char)):
                letter = get_pinyin_initial(first_char)
            else:
                letter = first_char if ("A" <= first_char <= "Z") else "#"
            groups[letter].append(t)

        sorted_letters = sorted([k for k in groups.keys() if k != "#"])
        if "#" in groups:
            sorted_letters.append("#")

        group_htmls = []
        for letter in sorted_letters:
            entry_htmls = []
            letter_terms = sorted(groups[letter], key=lambda x: x["source"].lower())
            for t in letter_terms:
                src = t["source"]
                tgt = t.get("target", "")
                occurrences = anchor_tracker.get(src, [])

                links_html = ""
                if occurrences:
                    link_parts = []
                    # Deduplicate links per chapter
                    seen_chapters: Set[str] = set()
                    link_num = 1
                    for anchor_id, ch_file, ch_title in occurrences:
                        if ch_file not in seen_chapters:
                            seen_chapters.add(ch_file)
                            ch_label = ch_file.replace(".xhtml", "").upper()
                            link_parts.append(f'<a href="{ch_file}#{anchor_id}">[{ch_label}]</a>')
                            link_num += 1
                    links_html = f'<span class="index-links">{", ".join(link_parts)}</span>'
                else:
                    links_html = '<span class="index-links" style="color:#888;"></span>'

                target_disp = f' <span class="index-target">({html.escape(tgt)})</span>' if tgt else ""
                entry_htmls.append(
                    f'      <div class="index-entry">'
                    f'<span class="index-source">{html.escape(src)}</span>{target_disp}: {links_html}'
                    f'</div>'
                )

            group_htmls.append(
                f'    <div class="index-group">\n'
                f'      <div class="index-letter">{letter}</div>\n'
                f'{"".join(entry_htmls)}\n'
                f'    </div>'
            )

        content = "\n".join(group_htmls)
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="vi" lang="vi">\n'
            '<head>\n'
            '  <title>Bảng Tra Cứu Thuật Ngữ (Index)</title>\n'
            '  <meta charset="utf-8"/>\n'
            '  <link rel="stylesheet" type="text/css" href="style.css"/>\n'
            '</head>\n'
            '<body>\n'
            '  <h1>Bảng Tra Cứu Thuật Ngữ</h1>\n'
            '  <p>Thuật ngữ gốc sắp xếp theo thứ tự chữ cái kèm theo đối ứng dịch tiếng Việt và liên kết đến các chương tương ứng.</p>\n'
            '  <div class="index-section">\n'
            f'{content}\n'
            '  </div>\n'
            '</body>\n'
            '</html>\n'
        )

    def _inject_utterance_span_anchors(
        self,
        html_body: str,
        utterances: List[Dict[str, Any]],
        return_matched_set: bool = False,
    ) -> Any:
        """
        Injects <span id="S-XXXX_uNN" class="..."> anchors into html_body for EPUB 3 Media Overlays.
        Tracks a forward search_cursor so repeated phrases are anchored sequentially in document order.
        Converts inline footnote markers (N)/[N] to match either literal or converted <sup ...> tags.
        Strips leading markdown heading markers (#+\s*) from display_text for heading match.
        """
        search_cursor = 0
        matched_utt_ids: Set[str] = set()

        for u in utterances:
            uid = u.get("utt_id")
            raw_disp = u.get("display_text", "").strip()
            disp = re.sub(r'^#+\s*', '', raw_disp).strip()
            layer = u.get("layer", "chinh_van")
            role = u.get("role", "narrator")
            if not uid or not disp:
                continue
            if f'id="{uid}"' in html_body:
                matched_utt_ids.add(uid)
                continue

            wrap_open = f'<span id="{uid}" class="utt-{layer} utt-role-{role}">'

            tokens = re.split(r'(\(\d{1,4}\)|\[\^?\d{1,4}\])', disp)
            pattern_parts = []
            for token in tokens:
                if not token:
                    continue
                if re.match(r'^(\(\d{1,4}\)|\[\^?\d{1,4}\])$', token):
                    pattern_parts.append(r'(?:' + re.escape(token) + r'|<sup\b[^>]*>.*?</sup>)')
                else:
                    token_esc = html.escape(token)
                    words_raw = [re.escape(w) for w in re.split(r'\s+', token) if w]
                    words_esc = [re.escape(w) for w in re.split(r'\s+', token_esc) if w]
                    raw_str = r'\s+'.join(words_raw) if words_raw else ""
                    esc_str = r'\s+'.join(words_esc) if words_esc else ""
                    if raw_str and esc_str and raw_str != esc_str:
                        pattern_parts.append(r'(?:' + raw_str + r'|' + esc_str + r')')
                    elif raw_str:
                        pattern_parts.append(raw_str)
                    elif esc_str:
                        pattern_parts.append(esc_str)

            if not pattern_parts:
                continue

            pattern = re.compile(r'\s*'.join(pattern_parts), re.DOTALL)
            m = pattern.search(html_body, pos=search_cursor)
            if not m and search_cursor > 0:
                m = pattern.search(html_body, pos=0)

            if m:
                matched_str = m.group(0)
                wrapped = f'{wrap_open}{matched_str}</span>'
                html_body = html_body[:m.start()] + wrapped + html_body[m.end():]
                search_cursor = m.start() + len(wrapped)
                matched_utt_ids.add(uid)
            else:
                target_str = disp
                idx = html_body.find(target_str, search_cursor)
                if idx == -1:
                    target_str = html.escape(disp)
                    idx = html_body.find(target_str, search_cursor)
                if idx == -1 and search_cursor > 0:
                    target_str = disp
                    idx = html_body.find(target_str, 0)
                    if idx == -1:
                        target_str = html.escape(disp)
                        idx = html_body.find(target_str, 0)
                if idx != -1:
                    wrapped = f'{wrap_open}{target_str}</span>'
                    html_body = html_body[:idx] + wrapped + html_body[idx + len(target_str):]
                    search_cursor = idx + len(wrapped)
                    matched_utt_ids.add(uid)

        if return_matched_set:
            return html_body, matched_utt_ids
        return html_body

    def _build_smil(
        self,
        ch_num: int,
        ch_file: str,
        audio_filename: str,
        utterances: List[Dict[str, Any]],
    ) -> str:
        """Constructs EPUB 3.3 Media Overlays SMIL 3.0 document."""
        pars = []
        for u in utterances:
            utt_id = u.get("utt_id")
            begin_sec = u.get("clip_begin_sec", 0.0)
            end_sec = u.get("clip_end_sec", 0.0)
            begin_clk = format_smil_clock(begin_sec)
            end_clk = format_smil_clock(end_sec)
            pars.append(
                f'      <par id="par_{utt_id}">\n'
                f'        <text src="../{ch_file}#{utt_id}"/>\n'
                f'        <audio src="../audio/{audio_filename}" clipBegin="{begin_clk}" clipEnd="{end_clk}"/>\n'
                f'      </par>'
            )
        pars_str = "\n".join(pars)
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<smil xmlns="http://www.w3.org/ns/SMIL" xmlns:epub="http://www.idpf.org/2007/ops" version="3.0">\n'
            '  <body>\n'
            f'    <seq id="seq_ch{ch_num:02d}" epub:textref="../{ch_file}">\n'
            f'{pars_str}\n'
            '    </seq>\n'
            '  </body>\n'
            '</smil>\n'
        )

    def _build_content_opf(
        self,
        title: str,
        author: str,
        slug: str,
        chapters: List[Dict[str, Any]],
        has_index: bool,
        image_files: List[Path],
        pub_id: Optional[str] = None,
        include_toc: bool = True,
        audio_overlays: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> str:
        """Constructs EPUB 3 OEBPS/content.opf package definition."""
        now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not pub_id:
            pub_id = f"urn:uuid:{uuid.uuid4()}"

        manifest_items = [
            '    <item id="style" href="style.css" media-type="text/css"/>',
            '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        ]

        spine_itemrefs = []
        if include_toc:
            spine_itemrefs.append('    <itemref idref="nav"/>')

        for ch in chapters:
            ch_id = f"item_{ch['ch_file'].replace('.', '_')}"
            ch_file = ch["ch_file"]
            ov = audio_overlays.get(ch_file) if audio_overlays else None
            if ov:
                manifest_items.append(
                    f'    <item id="{ch_id}" href="{ch_file}" media-type="application/xhtml+xml" media-overlay="{ov["smil_id"]}"/>'
                )
            else:
                manifest_items.append(
                    f'    <item id="{ch_id}" href="{ch_file}" media-type="application/xhtml+xml"/>'
                )
            spine_itemrefs.append(f'    <itemref idref="{ch_id}"/>')

        if audio_overlays:
            for ov in audio_overlays.values():
                manifest_items.append(f'    <item id="{ov["smil_id"]}" href="{ov["smil_file"]}" media-type="application/smil+xml"/>')
                manifest_items.append(f'    <item id="{ov["audio_id"]}" href="{ov["audio_file"]}" media-type="audio/mp4"/>')

        if has_index:
            manifest_items.append('    <item id="index_item" href="index.xhtml" media-type="application/xhtml+xml"/>')
            spine_itemrefs.append('    <itemref idref="index_item"/>')

        for idx, img in enumerate(image_files):
            mime_type = mimetypes.guess_type(img.name)[0] or "image/png"
            img_id = f"img_{idx:03d}_{img.stem}"
            manifest_items.append(f'    <item id="{img_id}" href="images/{img.name}" media-type="{mime_type}"/>')

        manifest_str = "\n".join(manifest_items)
        spine_str = "\n".join(spine_itemrefs)

        audio_metadata_lines = []
        if audio_overlays:
            for ov in audio_overlays.values():
                clk = format_smil_clock(ov["duration_sec"])
                audio_metadata_lines.append(f'    <meta property="media:duration" refines="#{ov["smil_id"]}">{clk}</meta>')
            tot_clk = format_smil_clock(sum(ov["duration_sec"] for ov in audio_overlays.values()))
            audio_metadata_lines.append(f'    <meta property="media:duration">{tot_clk}</meta>')
            audio_metadata_lines.append('    <meta property="media:active-class">-epub-media-overlay-active</meta>')

        audio_meta_str = ("\n" + "\n".join(audio_metadata_lines)) if audio_metadata_lines else ""

        guide_str = ""
        if include_toc:
            guide_str = (
                '  <guide>\n'
                '    <reference type="toc" title="Mục Lục" href="nav.xhtml"/>\n'
                '  </guide>\n'
            )

        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="pub-id" version="3.0">\n'
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            f'    <dc:identifier id="pub-id">{pub_id}</dc:identifier>\n'
            f'    <dc:title>{html.escape(title)}</dc:title>\n'
            f'    <dc:creator>{html.escape(author)}</dc:creator>\n'
            '    <dc:language>vi</dc:language>\n'
            f'    <meta property="dcterms:modified">{now_utc}</meta>{audio_meta_str}\n'
            '  </metadata>\n'
            '  <manifest>\n'
            f'{manifest_str}\n'
            '  </manifest>\n'
            '  <spine toc="ncx">\n'
            f'{spine_str}\n'
            '  </spine>\n'
            f'{guide_str}'
            '</package>\n'
        )
