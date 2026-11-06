#!/usr/bin/env python3
"""
scripts/extractors/mobi_extractor.py — MOBI/AZW extractor subclassing BaseExtractor.
Unpacks Kindle MOBI/AZW containers and routes through EPUBExtractor or HTML processor.
"""

import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import mobi
from bs4 import BeautifulSoup

from scripts.extractors.base import BaseExtractor
from scripts.extractors.epub_extractor import EPUBExtractor


class MOBIExtractor(BaseExtractor):
    """Extracts chapters and images from Kindle MOBI / AZW / AZW3 ebook files."""

    def _parse_ncx_filepos(self, ncx_path: Path) -> List[Tuple[str, str]]:
        """Parses toc.ncx and extracts list of (anchor, title) for navPoints targeting #filepos..."""
        try:
            tree = ET.parse(ncx_path)
            root = tree.getroot()
        except Exception:
            return []

        nav_entries: List[Tuple[str, str]] = []
        for np in root.findall(".//{*}navPoint"):
            text_el = np.find("./{*}navLabel/{*}text")
            if text_el is None:
                text_el = np.find(".//{*}navLabel/{*}text")
            title = (text_el.text if text_el is not None and text_el.text else "").strip()

            content_el = np.find("./{*}content")
            if content_el is None:
                content_el = np.find(".//{*}content")
            src = content_el.attrib.get("src", "").strip() if content_el is not None else ""

            if "#" in src:
                anchor = src.split("#", 1)[1].strip()
                if anchor:
                    nav_entries.append((anchor, title))

        return nav_entries

    def _split_html_by_anchors(
        self,
        raw_html: str,
        nav_entries: List[Tuple[str, str]],
        image_map: Dict[str, str],
        epub_ext: EPUBExtractor,
    ) -> List[Dict[str, Any]]:
        """Finds anchor tags matching nav_entries in raw_html, sorts by position, slices HTML, and parses chapters."""
        # 1. Pre-scan raw_html for Kindle MOBI #filepos footnote definitions
        filepos_lookup: Dict[str, str] = {}
        soup_all = BeautifulSoup(raw_html, "html.parser")
        for a_tag in soup_all.find_all("a"):
            pos_id = a_tag.get("id") or a_tag.get("name")
            if pos_id and "filepos" in pos_id.lower():
                container = None
                parent = a_tag.parent
                if parent and parent.name in ("p", "div", "li", "aside"):
                    container = parent
                else:
                    container = a_tag.find_next_sibling(["p", "div", "aside"])

                if container:
                    clone = BeautifulSoup(str(container), "html.parser")
                    for bl in clone.find_all("a"):
                        if "filepos" in bl.get("href", "") or "↩" in bl.get_text():
                            bl.decompose()
                    text = clone.get_text(strip=True)
                    clean_text = re.sub(r'^\s*(?:\(\d+\)|\[\d+\]|\d+\.)\s*', '', text).strip()
                    if clean_text:
                        filepos_lookup[pos_id] = clean_text
                        filepos_lookup[f"#{pos_id}"] = clean_text

        found_anchors: List[Tuple[int, str, str]] = []
        seen_anchors: Set[str] = set()

        for anchor, title in nav_entries:
            if anchor in seen_anchors:
                continue
            seen_anchors.add(anchor)
            pattern = rf"""<[a-zA-Z0-9]+[^>]*(?:id|name)=["\x27]?{re.escape(anchor)}["\x27]?(?:[\s/>]|$)"""
            m = re.search(pattern, raw_html, re.IGNORECASE)
            if m:
                found_anchors.append((m.start(), anchor, title))

        if not found_anchors:
            return []

        found_anchors.sort(key=lambda x: x[0])

        raw_chapters: List[Dict[str, Any]] = []
        for i in range(len(found_anchors)):
            start_pos = found_anchors[i][0]
            end_pos = found_anchors[i + 1][0] if i + 1 < len(found_anchors) else len(raw_html)
            slice_html = raw_html[start_pos:end_pos]
            ch_title = found_anchors[i][2]
            ch_idx = len(raw_chapters) + 1
            parsed_title, md_content = epub_ext._parse_html_to_markdown(
                slice_html, image_map, ch_idx
            )
            if md_content.strip():
                final_title = ch_title or parsed_title or f"Chapter {ch_idx}"

                # Resolve #filepos footnote links
                ch_footnotes: Dict[int, str] = {}
                def _replace_mobi_fn(match: re.Match) -> str:
                    target_id = match.group(2)
                    fn_body = filepos_lookup.get(target_id) or filepos_lookup.get(f"#{target_id}")
                    if fn_body:
                        local_n = len(ch_footnotes) + 1
                        ch_footnotes[local_n] = fn_body
                        return f"({local_n})"
                    return match.group(0)

                md_content = re.sub(
                    r'\[(\d+)\]\(#(filepos\d+)\)',
                    _replace_mobi_fn,
                    md_content,
                )

                if ch_footnotes:
                    fn_lines = [f"({n}) {body}" for n, body in ch_footnotes.items()]
                    md_content = md_content.strip() + "\n\n### Chú thích:\n" + "\n".join(fn_lines)

                # Strip remaining unresolved #filepos links [text](#filepos123) -> text
                md_content = re.sub(r'\[([^\]]*)\]\(#filepos\d+\)', r'\1', md_content)

                # Ensure H1 promotion: Each chapter starts with # <final_title>
                lines = md_content.splitlines()
                first_non_empty_idx = next((idx for idx, l in enumerate(lines) if l.strip()), None)
                if first_non_empty_idx is not None:
                    first_line = lines[first_non_empty_idx].strip()
                    if not first_line.startswith("# "):
                        # If leading bold like **1** or **Chapter 1**, replace it
                        if re.match(r'^\*\*(?:\d+|[^\*]+)\*\*$', first_line):
                            lines[first_non_empty_idx] = f"# {final_title}"
                        else:
                            lines.insert(0, f"# {final_title}")
                            lines.insert(1, "")
                        md_content = "\n".join(lines)
                else:
                    md_content = f"# {final_title}\n\n" + md_content

                raw_chapters.append({
                    "ch_num": ch_idx,
                    "title": final_title,
                    "content": md_content,
                })

        return raw_chapters

    # Alias for general NCX anchor splitting
    _split_html_by_ncx = _split_html_by_anchors

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
            raise FileNotFoundError(f"MOBI file not found: {input_path}")

        output_dir = Path(output_dir).resolve()
        images_dir = output_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        temp_dir = tempfile.mkdtemp(prefix="mobi_unpack_")
        try:
            # Unpack MOBI container
            extracted_path, _ = mobi.extract(str(input_path))
            extracted_target = Path(extracted_path)

            # 1. If an EPUB was extracted, route directly to EPUBExtractor
            if extracted_target.is_file() and extracted_target.suffix.lower() == ".epub":
                epub_extractor = EPUBExtractor()
                return epub_extractor.extract(
                    input_path=extracted_target,
                    output_dir=output_dir,
                    chapters=chapters,
                    slug=slug,
                    xml_segments=xml_segments,
                )

            # 2. Check if extracted_target is a directory containing an EPUB or HTML
            if extracted_target.is_dir():
                epubs = list(extracted_target.glob("*.epub")) + list(extracted_target.rglob("*.epub"))
                if epubs:
                    epub_extractor = EPUBExtractor()
                    return epub_extractor.extract(
                        input_path=epubs[0],
                        output_dir=output_dir,
                        chapters=chapters,
                        slug=slug,
                        xml_segments=xml_segments,
                    )

                # Look for HTML / XHTML files
                html_files = sorted(set(
                    list(extracted_target.glob("*.html"))
                    + list(extracted_target.glob("*.xhtml"))
                    + list(extracted_target.rglob("*.html"))
                    + list(extracted_target.rglob("*.xhtml"))
                ))
                if html_files:
                    # Copy images from unpacked directory to output_dir/images
                    image_map: Dict[str, str] = {}
                    img_idx = 0
                    for img_file in extracted_target.rglob("*"):
                        if img_file.is_file() and img_file.suffix.lower() in (
                            ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"
                        ):
                            fig_name = f"fig_mobi_{img_idx:03d}{img_file.suffix}"
                            dest = images_dir / fig_name
                            shutil.copy2(img_file, dest)
                            image_map[img_file.name] = f"images/{fig_name}"
                            img_idx += 1

                    epub_ext = EPUBExtractor()
                    raw_chapters: List[Dict[str, Any]] = []

                    # Check for NCX navigation file (e.g. toc.ncx)
                    ncx_files = sorted(set(
                        list(extracted_target.glob("*.ncx"))
                        + list(extracted_target.rglob("*.ncx"))
                    ))
                    if ncx_files:
                        filepos_navpoints = self._parse_ncx_filepos(ncx_files[0])
                        if filepos_navpoints:
                            target_html_content = None
                            for h_file in html_files:
                                with open(h_file, "r", encoding="utf-8", errors="replace") as f:
                                    h_content = f.read()
                                anchors_to_check = [a for a, _ in filepos_navpoints[:5]]
                                if any(a in h_content for a in anchors_to_check):
                                    target_html_content = h_content
                                    break

                            if target_html_content:
                                raw_chapters = self._split_html_by_anchors(
                                    target_html_content,
                                    filepos_navpoints,
                                    image_map,
                                    epub_ext,
                                )

                    # Fallback: parse each HTML file individually
                    if not raw_chapters:
                        for idx, h_file in enumerate(html_files, start=1):
                            with open(h_file, "r", encoding="utf-8", errors="replace") as f:
                                raw_html = f.read()
                            title, md_content = epub_ext._parse_html_to_markdown(raw_html, image_map, idx)
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

            # If single HTML file was returned directly
            if extracted_target.is_file() and extracted_target.suffix.lower() in (".html", ".htm"):
                epub_ext = EPUBExtractor()
                with open(extracted_target, "r", encoding="utf-8", errors="replace") as f:
                    raw_html = f.read()

                raw_chapters = []
                ncx_files = sorted(set(
                    list(extracted_target.parent.glob("*.ncx"))
                    + list(extracted_target.parent.rglob("*.ncx"))
                ))
                if ncx_files:
                    filepos_navpoints = self._parse_ncx_filepos(ncx_files[0])
                    if filepos_navpoints:
                        raw_chapters = self._split_html_by_anchors(
                            raw_html,
                            filepos_navpoints,
                            {},
                            epub_ext,
                        )

                if not raw_chapters:
                    raw_chapters = epub_ext._split_single_html(raw_html, {})

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

            raise RuntimeError(f"Could not parse unpacked MOBI contents at: {extracted_target}")

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            if "extracted_target" in locals() and extracted_target.exists():
                if extracted_target.is_dir():
                    shutil.rmtree(extracted_target, ignore_errors=True)
                elif extracted_target.is_file():
                    extracted_target.unlink(missing_ok=True)
