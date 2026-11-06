#!/usr/bin/env python3
"""
scripts/extractors/epub_extractor.py — EPUB extractor subclassing BaseExtractor.
Extracts chapters, TOC navigation, and images from EPUB containers into XML segments.
"""

import html
import logging
import os
import re
import shutil
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import warnings
from bs4 import BeautifulSoup, Comment, NavigableString, Tag, XMLParsedAsHTMLWarning
import ebooklib
from ebooklib import epub

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logger = logging.getLogger(__name__)

from scripts.extractors.base import BaseExtractor
from scripts.utils.language import CHINESE_CHAPTER_HEADING_RE

CHINESE_HEADING_RE = re.compile(
    r'<(?:h[1-3]|p)[^>]*>\s*(?:第\s*[\d零〇一二三四五六七八九十百千万两廿卅]+\s*[章节回卷部篇集]|卷\s*[\d零〇一二三四五六七八九十百千万两廿卅]+|(?:序章|前言|楔子|引子|尾声|后记|番外))[\s\S]*?</(?:h[1-3]|p)>',
    re.IGNORECASE
)


class EPUBExtractor(BaseExtractor):
    """Extracts chapters, embedded figures, and TOC from EPUB 2/3 books."""

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
        if input_path.is_dir():
            return self._extract_from_directory(
                input_dir=input_path,
                output_dir=output_dir,
                chapters=chapters,
                slug=slug,
                xml_segments=xml_segments,
                **kwargs,
            )
        if not input_path.is_file():
            raise FileNotFoundError(f"EPUB file not found: {input_path}")

        output_dir = Path(output_dir).resolve()
        images_dir = output_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        # 1. Read EPUB container
        try:
            book = epub.read_epub(str(input_path))
        except Exception:
            # Fallback to direct zipfile extraction if ebooklib fails
            return self._extract_from_zip(input_path, output_dir, chapters, xml_segments)

        # 2. Extract and index embedded images
        # Maps relative filenames / Basenames to "images/fig_..."
        image_map: Dict[str, str] = {}
        img_counter = 0

        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_IMAGE:
                img_name = item.get_name()
                img_bytes = item.get_content()
                ext = Path(img_name).suffix or ".png"
                img_filename = f"fig_epub_{img_counter:03d}{ext}"
                target_img_path = images_dir / img_filename
                with open(target_img_path, "wb") as f:
                    f.write(img_bytes)

                rel_img_path = f"images/{img_filename}"
                # Store multiple lookups for robust resolution
                image_map[img_name] = rel_img_path
                image_map[Path(img_name).name] = rel_img_path
                image_map[urllib.parse.unquote(Path(img_name).name)] = rel_img_path
                img_counter += 1

        # 3. Discover chapters
        # Check spine documents and build footnote lookup across all documents
        doc_items = []
        all_doc_tuples: List[Tuple[str, str]] = []

        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                c_str = item.get_content().decode("utf-8", errors="replace")
                all_doc_tuples.append((item.get_name(), c_str))

        footnote_lookup = self._build_footnote_lookup(all_doc_tuples)

        for spine_entry in book.spine:
            item_id = spine_entry[0] if isinstance(spine_entry, (tuple, list)) else spine_entry
            is_linear_no = (
                isinstance(spine_entry, (tuple, list))
                and len(spine_entry) > 1
                and str(spine_entry[1]).strip().lower() == "no"
            )
            if is_linear_no:
                continue

            item = book.get_item_with_id(item_id)
            if item and item.get_type() == ebooklib.ITEM_DOCUMENT:
                name_lower = item.get_name().lower()
                content_str = item.get_content().decode("utf-8", errors="replace")
                # Skip nav / cover / toc documents if they only contain metadata
                if "nav" in name_lower or "cover" in name_lower or "toc" in name_lower:
                    if "<nav" in content_str and len(content_str) < 5000:
                        continue

                # Skip standalone footnote documents (linear=no or footnote-only)
                is_footnote_only = (
                    ("footnote" in name_lower or "notes" in name_lower or "endnotes" in name_lower)
                    and ("backlink" in content_str or 'epub:type="footnote"' in content_str or len(content_str) < 30000)
                )
                if is_footnote_only:
                    continue

                doc_items.append(item)

        if not doc_items:
            # Fallback to all documents
            doc_items = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))

        raw_chapters: List[Dict[str, Any]] = []

        # If multiple document items exist in spine, each is candidate chapter
        if len(doc_items) > 1:
            for idx, item in enumerate(doc_items, start=1):
                raw_html = item.get_content().decode("utf-8", errors="replace")
                title, md_content = self._parse_html_to_markdown(
                    raw_html, image_map, idx, footnote_lookup=footnote_lookup
                )
                if not md_content.strip():
                    continue
                raw_chapters.append({
                    "ch_num": idx,
                    "title": title or f"Chapter {idx}",
                    "content": md_content,
                })
        elif len(doc_items) == 1:
            # Single document: split at <h1> or <h2 class="chapter">
            raw_html = doc_items[0].get_content().decode("utf-8", errors="replace")
            sub_chapters = self._split_single_html(raw_html, image_map)
            raw_chapters.extend(sub_chapters)

        # Fallback if no chapters parsed
        if not raw_chapters:
            raw_chapters = [{
                "ch_num": 1,
                "title": "Chapter 1",
                "content": "# Chapter 1\n\nNo text content found in EPUB.",
            }]

        # Re-number chapters sequentially 1..N
        for idx, ch in enumerate(raw_chapters, start=1):
            ch["ch_num"] = idx

        # 4. Filter chapters
        ch_filter = self.parse_chapters_filter(chapters)
        if ch_filter:
            target_chapters = [ch for ch in raw_chapters if ch["ch_num"] in ch_filter]
        else:
            target_chapters = raw_chapters

        # 5. Write source.txt and checklist.json
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

    def _extract_from_directory(
        self,
        input_dir: Path,
        output_dir: Path,
        chapters: Optional[str] = None,
        slug: Optional[str] = None,
        xml_segments: bool = True,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """Extracts chapters from an unpacked EPUB directory layout."""
        input_dir = Path(input_dir).resolve()
        output_dir = Path(output_dir).resolve()
        images_dir = output_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        # 1. Extract and index embedded images
        image_map: Dict[str, str] = {}
        img_counter = 0
        for ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
            for img_path in sorted(input_dir.rglob(f"*{ext}")):
                if output_dir in img_path.parents or img_path.is_dir():
                    continue
                target_filename = f"fig_epub_{img_counter:03d}{img_path.suffix.lower()}"
                target_file = images_dir / target_filename
                try:
                    shutil.copy2(img_path, target_file)
                except Exception as exc:
                    logger.warning("Failed to copy image %s to %s: %s", img_path, target_file, exc)
                rel_img_path = f"images/{target_filename}"
                image_map[img_path.name] = rel_img_path
                image_map[urllib.parse.unquote(img_path.name)] = rel_img_path
                try:
                    rel_to_input = str(img_path.relative_to(input_dir)).replace("\\", "/")
                    image_map[rel_to_input] = rel_img_path
                    image_map[urllib.parse.unquote(rel_to_input)] = rel_img_path
                except ValueError:
                    pass
                if (input_dir / "OEBPS").is_dir():
                    try:
                        rel_to_oebps = str(img_path.relative_to(input_dir / "OEBPS")).replace("\\", "/")
                        image_map[rel_to_oebps] = rel_img_path
                        image_map[urllib.parse.unquote(rel_to_oebps)] = rel_img_path
                    except ValueError:
                        pass
                img_counter += 1

        # 2. Parse TOC navigation (toc.ncx)
        toc_map: Dict[str, str] = {}
        ncx_candidates = list(input_dir.rglob("*.ncx"))
        if ncx_candidates:
            try:
                ncx_path = ncx_candidates[0]
                soup_ncx = BeautifulSoup(ncx_path.read_text(encoding="utf-8", errors="replace"), "xml")
                for nav in soup_ncx.find_all(["navpoint", "navPoint"]):
                    content_tag = nav.find("content")
                    label_tag = nav.find(["navlabel", "navLabel"])
                    if content_tag and label_tag:
                        src = content_tag.get("src", "").split("#")[0]
                        text = label_tag.get_text(strip=True)
                        if src and text:
                            toc_map[src] = text
                            toc_map[Path(src).name] = text
            except Exception as exc:
                logger.warning("Failed to parse TOC navigation from NCX %s: %s", ncx_path, exc)

        # 3. Discover chapter HTML files
        text_dir = input_dir / "OEBPS" / "Text"
        if not text_dir.is_dir():
            text_dir = input_dir / "Text"
        if text_dir.is_dir():
            raw_files = [
                p for p in text_dir.iterdir()
                if p.is_file() and p.suffix.lower() in (".htm", ".html", ".xhtml")
            ]
        else:
            raw_files = [
                p for p in input_dir.rglob("*")
                if p.is_file()
                and p.suffix.lower() in (".htm", ".html", ".xhtml")
                and output_dir not in p.parents
            ]

        html_files = []
        for f in raw_files:
            lower = f.name.lower()
            if lower.startswith(("index", "nav", "toc", "cover")):
                continue
            html_files.append(f)

        html_files.sort(key=lambda p: p.name)

        merge_000 = kwargs.get("merge_000_into_001", True)
        file_000 = next((f for f in html_files if f.stem == "000"), None)
        file_001 = next((f for f in html_files if f.stem == "001"), None)

        md_000_prefix = ""
        if merge_000 and file_000 and file_001:
            raw_html_000 = file_000.read_text(encoding="utf-8", errors="replace")
            _, md_000_prefix = self._parse_html_to_markdown(raw_html_000, image_map, 0)
            html_files = [f for f in html_files if f != file_000]

        raw_chapters: List[Dict[str, Any]] = []
        for idx, f in enumerate(html_files, start=1):
            raw_html = f.read_text(encoding="utf-8", errors="replace")
            parsed_title, md_content = self._parse_html_to_markdown(raw_html, image_map, idx)

            if f == file_001 and md_000_prefix.strip():
                if md_content.strip():
                    md_content = md_000_prefix.strip() + "\n\n" + md_content.strip()
                else:
                    md_content = md_000_prefix.strip()

            title = toc_map.get(f.name)
            if not title:
                try:
                    rel_to_oebps = str(f.relative_to(input_dir / "OEBPS")).replace("\\", "/")
                    title = toc_map.get(rel_to_oebps)
                except ValueError:
                    pass
            if not title:
                title = parsed_title or f"Chapter {idx}"

            raw_chapters.append({
                "ch_num": idx,
                "title": title,
                "content": md_content,
            })

        if not raw_chapters:
            raw_chapters = [{
                "ch_num": 1,
                "title": "Chapter 1",
                "content": "# Chapter 1\n\nNo text content found in directory.",
            }]

        # Re-number chapters sequentially 1..N
        for idx, ch in enumerate(raw_chapters, start=1):
            ch["ch_num"] = idx

        # 4. Filter chapters
        ch_filter = self.parse_chapters_filter(chapters)
        if ch_filter:
            target_chapters = [ch for ch in raw_chapters if ch["ch_num"] in ch_filter]
        else:
            target_chapters = raw_chapters

        # 5. Write source.txt and checklist.json
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

    def _build_footnote_lookup(
        self,
        doc_tuples: List[Tuple[str, str]],
    ) -> Dict[str, str]:
        """
        Builds a lookup table mapping element anchor identifiers to their footnote text
        across all document items in the EPUB.
        """
        lookup: Dict[str, str] = {}
        for doc_name, raw_html in doc_tuples:
            soup = BeautifulSoup(raw_html, "html.parser")
            doc_basename = Path(doc_name).name

            # Candidate footnote elements
            candidates = soup.find_all(lambda tag: (
                isinstance(tag, Tag)
                and tag.name in ("aside", "div", "p", "li", "dd", "section")
                and (
                    tag.get("id")
                    or tag.get("epub:type") in ("footnote", "endnote", "note")
                    or tag.get("role") in ("doc-footnote", "doc-endnote")
                    or any(c in (tag.get("class") or []) for c in ("footnote", "note", "endnote"))
                )
            ))

            for el in candidates:
                el_id = el.get("id") or ""
                # Create a copy to remove backlinks without mutating the soup
                clone = BeautifulSoup(str(el), "html.parser")
                for bl in clone.find_all(lambda a: a.name == "a" and (
                    a.get("role") == "doc-backlink"
                    or a.get("epub:type") == "backlink"
                    or "backlink" in " ".join(a.get("class", []))
                    or "↩" in a.get_text()
                    or "fnref" in a.get("href", "")
                )):
                    bl.decompose()

                fn_text = clone.get_text(strip=True)
                fn_text = re.sub(r'^\s*(?:\(\d+\)|\[\d+\]|\d+\.)\s*', '', fn_text).strip()
                if not fn_text:
                    continue

                if el_id:
                    lookup[f"{doc_basename}#{el_id}"] = fn_text
                    lookup[f"{doc_name}#{el_id}"] = fn_text
                    lookup[f"#{el_id}"] = fn_text
                    lookup[el_id] = fn_text

                # Check for child anchor tags with name or id
                for anchor in el.find_all("a"):
                    aid = anchor.get("id") or anchor.get("name")
                    if aid:
                        lookup[f"{doc_basename}#{aid}"] = fn_text
                        lookup[f"{doc_name}#{aid}"] = fn_text
                        lookup[f"#{aid}"] = fn_text
                        lookup[aid] = fn_text

        return lookup

    def _parse_html_to_markdown(
        self,
        raw_html: str,
        image_map: Dict[str, str],
        ch_num: int = 1,
        footnote_lookup: Optional[Dict[str, str]] = None,
        ch_idx: Optional[int] = None,
    ) -> Tuple[str, str]:
        """Converts XHTML body into clean Markdown with image anchors and resolved footnotes."""
        soup = BeautifulSoup(raw_html, "html.parser")
        body = soup.find("body") or soup

        # Extract title
        title = ""
        h1 = body.find("h1")
        if h1:
            title = h1.get_text(strip=True)
        elif soup.find("title"):
            title = soup.find("title").get_text(strip=True)

        md_blocks: List[str] = []
        chapter_footnotes: Dict[int, str] = {}

        # If heading wasn't in body, add title as H1
        if title and not h1:
            md_blocks.append(f"# {title}")

        self._extract_blocks_from_node(
            body,
            image_map,
            md_blocks,
            footnote_lookup=footnote_lookup,
            chapter_footnotes=chapter_footnotes,
        )

        md_content = "\n\n".join(md_blocks)

        if chapter_footnotes:
            fn_lines = [f"({num}) {text}" for num, text in chapter_footnotes.items()]
            md_content += "\n\n### Chú thích:\n" + "\n".join(fn_lines)

        return title, md_content

    def _extract_blocks_from_node(
        self,
        container: Tag,
        image_map: Dict[str, str],
        md_blocks: List[str],
        footnote_lookup: Optional[Dict[str, str]] = None,
        chapter_footnotes: Optional[Dict[int, str]] = None,
    ) -> None:
        """Recursively extracts block-level elements into md_blocks, skipping baihua translations."""
        block_tags = {
            "h1", "h2", "h3", "h4", "h5", "h6",
            "p", "pre", "blockquote", "ul", "ol",
            "div", "section", "article", "main", "figure", "hr",
        }

        for elem in container.children:
            if isinstance(elem, Comment):
                continue
            if isinstance(elem, NavigableString):
                text = str(elem).strip()
                if text:
                    md_blocks.append(text)
                continue

            if not isinstance(elem, Tag):
                continue

            classes = elem.get("class") or []
            if "translation" in classes:
                continue

            tag_name = elem.name.lower()

            # Headings
            if tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                level = int(tag_name[1])
                h_text = elem.get_text(strip=True)
                if tag_name == "h5" and any(k in h_text for k in ("尚書", "莊子", "三國志", "史記", "漢書", "後漢書", "資治通鑑", "戰國策", "三國演義")):
                    continue
                heading_prefix = "#" * level
                md_blocks.append(f"{heading_prefix} {h_text}")

            # Paragraphs
            elif tag_name == "p":
                # Check if paragraph is a sub-biography header:
                # starts with 4 fullwidth spaces \u3000\u3000\u3000\u3000 and has short title text (<= 30 chars)
                raw_text = elem.get_text()
                clean_raw = raw_text.lstrip(' ')
                if clean_raw.startswith("\u3000\u3000\u3000\u3000") and len(clean_raw.strip()) <= 30 and clean_raw.strip():
                    title_text = clean_raw.strip()
                    md_blocks.append(f"## {title_text}")
                else:
                    p_text = self._inline_to_markdown(
                        elem, image_map, footnote_lookup=footnote_lookup, chapter_footnotes=chapter_footnotes
                    )
                    if p_text.strip():
                        md_blocks.append(p_text.strip())

            # Code blocks
            elif tag_name == "pre":
                code_elem = elem.find("code")
                code_text = code_elem.get_text() if code_elem else elem.get_text()
                lang = ""
                classes_list = elem.get("class", []) + (code_elem.get("class", []) if code_elem else [])
                for cls in classes_list:
                    if cls.startswith("language-") or cls.startswith("lang-"):
                        lang = cls.split("-", 1)[1]
                        break
                md_blocks.append(f"```{lang}\n{code_text.strip()}\n```")

            # Blockquotes
            elif tag_name == "blockquote":
                bq_text = elem.get_text(strip=True)
                md_blocks.append(f"> {bq_text}")

            # Lists
            elif tag_name in ("ul", "ol"):
                items = []
                for li in elem.find_all("li", recursive=False):
                    li_text = self._inline_to_markdown(
                        li, image_map, footnote_lookup=footnote_lookup, chapter_footnotes=chapter_footnotes
                    ).strip()
                    if li_text:
                        prefix = "- " if tag_name == "ul" else "1. "
                        items.append(f"{prefix}{li_text}")
                if items:
                    md_blocks.append("\n".join(items))

            # Standalone images or figures
            elif tag_name in ("img", "figure"):
                img_tag = elem if tag_name == "img" else elem.find("img")
                if img_tag and img_tag.get("src"):
                    src = img_tag["src"]
                    res_path = self._resolve_image_src(src, image_map)
                    alt = img_tag.get("alt", "Figure") or "Figure"
                    md_blocks.append(f"![{alt}]({res_path})")

            # Containers (div, section, article, main)
            elif tag_name in ("div", "section", "article", "main"):
                has_blocks = any(
                    child.name.lower() in block_tags
                    for child in elem.find_all(True)
                )
                if has_blocks:
                    self._extract_blocks_from_node(
                        elem, image_map, md_blocks, footnote_lookup=footnote_lookup, chapter_footnotes=chapter_footnotes
                    )
                else:
                    container_md = self._inline_to_markdown(
                        elem, image_map, footnote_lookup=footnote_lookup, chapter_footnotes=chapter_footnotes
                    )
                    if container_md.strip():
                        md_blocks.append(container_md.strip())

            elif tag_name == "hr":
                continue

    def _inline_to_markdown(
        self,
        parent: Tag,
        image_map: Dict[str, str],
        footnote_lookup: Optional[Dict[str, str]] = None,
        chapter_footnotes: Optional[Dict[int, str]] = None,
    ) -> str:
        """Processes inline tags and text inside a container."""
        parts: List[str] = []
        for child in parent.children:
            if isinstance(child, NavigableString):
                parts.append(str(child))
            elif isinstance(child, Tag):
                classes = child.get("class") or []
                if "translation" in classes:
                    continue
                c_tag = child.name.lower()
                if c_tag in ("strong", "b"):
                    parts.append(f"**{child.get_text()}**")
                elif c_tag in ("em", "i"):
                    parts.append(f"*{child.get_text()}*")
                elif c_tag == "code":
                    parts.append(f"`{child.get_text()}`")
                elif c_tag == "a":
                    link_text = child.get_text(strip=True)
                    if not link_text:
                        continue
                    href = child.get("href", "").strip()
                    matched_fn = None
                    if footnote_lookup:
                        matched_fn = footnote_lookup.get(href)
                        if not matched_fn and "#" in href:
                            anchor_part = href.split("#", 1)[1]
                            matched_fn = (
                                footnote_lookup.get(f"#{anchor_part}")
                                or footnote_lookup.get(anchor_part)
                                or footnote_lookup.get(Path(href).name)
                            )

                    if matched_fn and chapter_footnotes is not None:
                        local_num = len(chapter_footnotes) + 1
                        chapter_footnotes[local_num] = matched_fn
                        parts.append(f"({local_num})")
                    else:
                        parts.append(f"[{child.get_text()}]({href})")
                elif c_tag == "img" and child.get("src"):
                    src = child["src"]
                    res_path = self._resolve_image_src(src, image_map)
                    alt = child.get("alt", "Figure") or "Figure"
                    parts.append(f"\n\n![{alt}]({res_path})\n\n")
                elif c_tag == "br":
                    parts.append("\n")
                elif c_tag == "span" and "style7" in (classes if isinstance(classes, list) else classes.split()):
                    span_text = child.get_text()
                    citation_markers = ("臣松之", "案", "云", "曰", "記", "傳", "書", "志", "論", "注", "評")
                    is_commentary = len(span_text) >= 26 or any(m in span_text for m in citation_markers)
                    if is_commentary:
                        converted = span_text.replace("\u3000", "\n\n").strip()
                        parts.append(f"\n\n{converted}\n\n")
                    else:
                        clean_gloss = span_text.strip()
                        if clean_gloss.startswith("（") and clean_gloss.endswith("）"):
                            parts.append(clean_gloss)
                        elif clean_gloss.startswith("(") and clean_gloss.endswith(")"):
                            parts.append(f"（{clean_gloss[1:-1].strip()}）")
                        else:
                            parts.append(f"（{clean_gloss}）")
                elif c_tag == "span" and any(cls in ("style3", "style12", "style19") for cls in (classes if isinstance(classes, list) else classes.split())):
                    clean_gloss = child.get_text().strip()
                    if clean_gloss.startswith("（") and clean_gloss.endswith("）"):
                        parts.append(clean_gloss)
                    elif clean_gloss.startswith("(") and clean_gloss.endswith(")"):
                        parts.append(f"（{clean_gloss[1:-1].strip()}）")
                    else:
                        parts.append(f"（{clean_gloss}）")
                # Ancient-China-Books commentary conversion (jiazhu -> chapter_footnotes) (REQ-001)
                elif c_tag == "span" and "jiazhu" in (classes if isinstance(classes, list) else classes.split()):
                    fn_text = child.get_text().strip()
                    if fn_text and chapter_footnotes is not None:
                        local_num = len(chapter_footnotes) + 1
                        chapter_footnotes[local_num] = fn_text
                        parts.append(f" ({local_num}) ")
                # Skip textual collation notes between editions (REQ-001)
                elif c_tag == "span" and "jiaozhusup" in (classes if isinstance(classes, list) else classes.split()):
                    continue
                else:
                    parts.append(
                        self._inline_to_markdown(
                            child, image_map, footnote_lookup=footnote_lookup, chapter_footnotes=chapter_footnotes
                        )
                    )
        return "".join(parts)

    def _resolve_image_src(self, src: str, image_map: Dict[str, str]) -> str:
        """Resolves image src to local images/fig_... path."""
        src_clean = urllib.parse.unquote(src.replace("\\", "/"))
        base_name = Path(src_clean).name

        if src_clean in image_map:
            return image_map[src_clean]
        if base_name in image_map:
            return image_map[base_name]

        # Check partial ends
        for k, v in image_map.items():
            if k.endswith(base_name) or base_name.endswith(Path(k).name):
                return v

        return f"images/{base_name}"

    def _split_single_html(
        self,
        raw_html: str,
        image_map: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        """Splits single large HTML file at H1 boundaries into chapters."""
        soup = BeautifulSoup(raw_html, "html.parser")
        body = soup.find("body") or soup

        chapters: List[Dict[str, Any]] = []
        current_title = "Chapter 1"
        current_blocks: List[str] = []
        ch_count = 1

        for elem in body.children:
            if not isinstance(elem, Tag):
                if isinstance(elem, NavigableString) and str(elem).strip():
                    current_blocks.append(str(elem).strip())
                continue

            elem_text = elem.get_text(strip=True) if isinstance(elem, Tag) else ""
            is_chapter_elem = (
                elem.name.lower() == "h1"
                or bool(CHINESE_HEADING_RE.match(str(elem).strip()))
                or bool(CHINESE_CHAPTER_HEADING_RE.match(elem_text))
                or (
                    elem.name.lower() in ("h2", "h3", "p")
                    and bool(CHINESE_CHAPTER_HEADING_RE.match(elem_text))
                )
            )

            if is_chapter_elem:
                if current_blocks:
                    chapters.append({
                        "ch_num": ch_count,
                        "title": current_title,
                        "content": "\n\n".join(current_blocks),
                    })
                    ch_count += 1
                    current_blocks = []
                current_title = elem_text or f"Chapter {ch_count}"
                current_blocks.append(f"# {current_title}")
            else:
                _, block_md = self._parse_html_to_markdown(str(elem), image_map, ch_count)
                if block_md.strip():
                    current_blocks.append(block_md.strip())

        if current_blocks:
            chapters.append({
                "ch_num": ch_count,
                "title": current_title,
                "content": "\n\n".join(current_blocks),
            })

        return chapters

    def _extract_from_zip(
        self,
        input_path: Path,
        output_dir: Path,
        chapters: Optional[str],
        xml_segments: bool,
    ) -> List[Dict[str, Any]]:
        """Fallback direct ZIP unpacker if ebooklib fails to parse EPUB container."""
        images_dir = output_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        image_map: Dict[str, str] = {}
        raw_chapters: List[Dict[str, Any]] = []

        with zipfile.ZipFile(input_path, "r") as zf:
            namelist = zf.namelist()
            img_idx = 0
            # 1. Extract images
            for name in namelist:
                ext = Path(name).suffix.lower()
                if ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
                    img_data = zf.read(name)
                    fig_name = f"fig_epub_{img_idx:03d}{ext}"
                    with open(images_dir / fig_name, "wb") as f:
                        f.write(img_data)
                    rel_path = f"images/{fig_name}"
                    image_map[name] = rel_path
                    image_map[Path(name).name] = rel_path
                    img_idx += 1

            # 2. Extract HTML files
            html_files = sorted([
                n for n in namelist
                if Path(n).suffix.lower() in (".html", ".xhtml", ".htm")
                and "nav" not in n.lower() and "toc" not in n.lower()
            ])

            for idx, h_name in enumerate(html_files, start=1):
                raw_html = zf.read(h_name).decode("utf-8", errors="replace")
                title, md_content = self._parse_html_to_markdown(raw_html, image_map, idx)
                if md_content.strip():
                    raw_chapters.append({
                        "ch_num": idx,
                        "title": title or f"Chapter {idx}",
                        "content": md_content,
                    })

        ch_filter = self.parse_chapters_filter(chapters)
        if ch_filter:
            target_chapters = [ch for ch in raw_chapters if ch["ch_num"] in ch_filter]
        else:
            target_chapters = raw_chapters

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
