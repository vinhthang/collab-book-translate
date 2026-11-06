#!/usr/bin/env python3
"""
scripts/extractors/pdf_extractor.py — PyMuPDF extractor subclassing BaseExtractor.
"""

import os
import re
import sys
import itertools
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import pymupdf
from scripts.extractors.base import BaseExtractor
from scripts.utils.language import CHINESE_CHAPTER_HEADING_RE, is_chinese_chapter_heading

TOC_CHAPTER_PATTERN = re.compile(
    r'^\s*(?:(?:CHAPTER|CHƯƠNG|PHẦN|PART)\s*(\d+|[IVXLCDM]+)?|(?:第\s*[零〇一二三四五六七八九十百千万两廿卅0-9]+\s*[章节回卷部篇集节])|(?:[卷部篇]\s*[零〇一二三四五六七八九十百千万两廿卅0-9]+)|(?:序章|前言|楔子|引子|尾声|后记|番外))',
    re.IGNORECASE
)


def chinese_to_int(s: str) -> int:
    """Converts a Chinese numeral string or digit string to integer."""
    s = s.strip()
    if s.isdigit():
        return int(s)
    mapping = {'零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
    units = {'十': 10, '百': 100, '千': 1000}
    total = 0
    current = 0
    for char in s:
        if char in mapping:
            current = mapping[char]
        elif char in units:
            unit = units[char]
            if current == 0:
                current = 1
            total += current * unit
            current = 0
        elif char.isdigit():
            current = int(char)
    total += current
    return total if total > 0 else 1

#!/usr/bin/env python3
"""
scripts/extract_pdf.py — PDF Chapter & Asset Extractor
Extracts chapters into runs/<book_slug>/ch<NN>/source.txt with embedded images
extracted to runs/<book_slug>/images/fig_<ch>_<index>.<ext> and Markdown image anchors.
Uses PyMuPDF (pymupdf).
"""

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from scripts.helper import dehyphenate_text

try:
    import pymupdf
except ImportError:
    try:
        import fitz as pymupdf
    except ImportError:
        sys.stderr.write("Error: PyMuPDF is required. Run via 'uv run --with pymupdf python3 scripts/extract_pdf.py ...'\n")
        sys.exit(1)


def roman_to_int(roman_str: str) -> int:
    """Converts a Roman numeral string to an integer."""
    roman_map = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    val = 0
    s = roman_str.upper()
    for i, ch in enumerate(s):
        curr = roman_map.get(ch, 0)
        if i + 1 < len(s) and roman_map.get(s[i + 1], 0) > curr:
            val -= curr
        else:
            val += curr
    return val if val > 0 else 1


def parse_chapters_filter(filter_str: Optional[str]) -> Optional[set]:
    """
    Parses a chapter filter string like '1-3' or '1,2,5' or '1'.
    Returns a set of integer chapter numbers, or None for all chapters.
    """
    if not filter_str or filter_str.lower() in ("all", "*"):
        return None
    chapters = set()
    for part in filter_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            try:
                start = int(start_s.strip())
                end = int(end_s.strip())
                chapters.update(range(start, end + 1))
            except ValueError:
                pass
        else:
            try:
                chapters.add(int(part))
            except ValueError:
                pass
    return chapters if chapters else None


def detect_optimal_toc_level(doc: Any) -> int:
    """
    Analyzes the Table of Contents tree to determine whether chapters are at Level 1 or Level 2.
    """
    toc = doc.get_toc()
    if not toc:
        return 1
    lvl1 = [item for item in toc if item[0] == 1]
    lvl2 = [item for item in toc if item[0] == 2]
    if not lvl2:
        return 1

    ch_re = re.compile(r"\b(?:Chapter|Chương)\s+(\d+|[IVXLCDM]+)", re.I)
    part_re = re.compile(r"\b(?:Part|Phần|Book|Volume|Tập|Quyển)\s+(\d+|[IVXLCDM]+)", re.I)
    numbered_sec_re = re.compile(r"^\s*(\d+)\s+[A-Z]", re.I)

    lvl1_has_ch = any(ch_re.search(item[1]) for item in lvl1)
    lvl1_has_part = any(part_re.search(item[1]) for item in lvl1)
    lvl2_has_ch = any(ch_re.search(item[1]) for item in lvl2)
    lvl2_has_num = any(numbered_sec_re.search(item[1]) for item in lvl2)

    # If Level 1 explicitly contains "Chapter" / "Chương" (e.g. Rust Atomics, Projections), use Level 1
    if lvl1_has_ch:
        return 1

    # If Level 1 contains container Parts and Level 2 contains chapters or numbered items (e.g. Principles)
    if lvl1_has_part and (lvl2_has_ch or lvl2_has_num or len(lvl2) >= 6):
        return 2

    # Check average page span: if Level 1 has very few items with huge spans (>60 pages/item) and Level 2 has multiple items
    avg_l1_pages = len(doc) / max(1, len(lvl1))
    if len(lvl1) <= 4 and avg_l1_pages > 60 and len(lvl2) >= 6:
        return 2

    return 1


def _matches_chapter_heading(page_lines: Any, title: str = "") -> bool:
    """Checks whether the top lines of a page match a chapter title/heading."""
    if isinstance(page_lines, str):
        lines = page_lines.splitlines()
        top_block = page_lines
    else:
        lines = page_lines or []
        top_block = "\n".join(lines[:8])

    for line in lines[:5]:
        if CHINESE_CHAPTER_HEADING_RE.match(line):
            return True
    if title and CHINESE_CHAPTER_HEADING_RE.match(title):
        return True

    ch_re = re.compile(r"\b(?:Chapter|Chương|Part|Phần)\s+(\d+|[IVXLCDM]+)", re.IGNORECASE)
    part_re = re.compile(r"\b(?:Part|Phần)\s+(\d+|[IVXLCDM]+)", re.IGNORECASE)
    prologue_re = re.compile(r"\b(?:prologue|introduction|lời mở đầu)\b", re.IGNORECASE)
    epilogue_re = re.compile(r"\b(?:epilogue|conclusion|lời kết|kết luận)\b", re.IGNORECASE)

    m_ch = ch_re.search(title)
    if m_ch:
        base_word = m_ch.group(0)  # e.g. "Chapter 5"
        has_base = False
        for line in lines[:5]:
            if re.search(r"^\s*" + re.escape(base_word) + r"\b", line, re.IGNORECASE) or (
                len(line) < 80 and re.search(r"\b" + re.escape(base_word) + r"\b", line, re.IGNORECASE)
            ):
                has_base = True
                break
        if not has_base:
            return False

        m_part = part_re.search(title)
        if m_part and m_part.group(0).lower() != base_word.lower():
            part_str = m_part.group(0)  # e.g. "Part 1"
            if not re.search(r"\b" + re.escape(part_str) + r"\b", top_block, re.IGNORECASE):
                return False
        return True
    elif prologue_re.search(title):
        for line in page_lines[:5]:
            if re.search(r"^\s*(?:prologue|introduction|lời mở đầu)\b", line, re.IGNORECASE) or (
                len(line) < 50 and prologue_re.search(line)
            ):
                return True
        return False
    elif epilogue_re.search(title):
        for line in page_lines[:5]:
            if re.search(r"^\s*(?:epilogue|conclusion|lời kết|kết luận)\b", line, re.IGNORECASE) or (
                len(line) < 50 and epilogue_re.search(line)
            ):
                return True
        return False
    return False


def discover_chapters(doc: Any, toc_level: Optional[str | int] = "auto") -> List[Dict[str, Any]]:
    """
    Discovers chapters from PDF TOC bookmarks or page headings.
    Returns list of dicts: [{'ch_num': int, 'title': str, 'start_page': int, 'end_page': int}]
    """
    total_pages = len(doc)
    toc = doc.get_toc()

    if toc_level in (1, "1"):
        effective_level = 1
    elif toc_level in (2, "2"):
        effective_level = 2
    else:
        effective_level = detect_optimal_toc_level(doc)

    ch_candidates = []
    lvl1_items = [item for item in toc if item[0] == 1]

    if effective_level == 2 and len(toc) > 0:
        part_re = re.compile(r"\b(?:Part|Phần|Book|Volume|Tập|Quyển)\s+(\d+|[IVXLCDM]+)", re.IGNORECASE)
        front_back_patterns = [
            r"^cover\b",
            r"^copyright\b",
            r"^contents\b",
            r"^table of contents\b",
            r"^acknowledgments\b",
            r"^about the author\b",
            r"^index\b",
            r"^bibliography\b",
            r"^dedication\b",
            r"^title page\b",
        ]
        fb_re = re.compile("|".join(front_back_patterns), re.IGNORECASE)

        curr_part = None
        for i, item in enumerate(toc):
            lvl, title, pageno = item[0], item[1].strip(), item[2]
            if lvl == 1:
                if part_re.search(title):
                    curr_part = title
                else:
                    curr_part = None
                    if not fb_re.search(title):
                        ch_candidates.append({
                            "title": title,
                            "start_page": max(0, pageno - 1),
                            "toc_idx": i,
                        })
            elif lvl == 2 and curr_part:
                if not fb_re.search(title):
                    ch_candidates.append({
                        "title": f"{curr_part} — {title}",
                        "start_page": max(0, pageno - 1),
                        "toc_idx": i,
                    })

        if ch_candidates:
            ch_candidates.sort(key=lambda x: x["start_page"])
            prologue_pattern = re.compile(r"\b(?:prologue|introduction|lời mở đầu)\b", re.IGNORECASE)
            epilogue_pattern = re.compile(r"\b(?:epilogue|conclusion|lời kết|kết luận)\b", re.IGNORECASE)

            first_toc_idx = min(c["toc_idx"] for c in ch_candidates if "toc_idx" in c)
            last_toc_idx = max(c["toc_idx"] for c in ch_candidates if "toc_idx" in c)

            has_prologue = any(prologue_pattern.search(c["title"]) for c in ch_candidates)
            if not has_prologue:
                for i in range(first_toc_idx):
                    lvl, title, pageno = toc[i][0], toc[i][1].strip(), toc[i][2]
                    if prologue_pattern.search(title):
                        ch_candidates.insert(0, {
                            "title": title,
                            "start_page": max(0, pageno - 1),
                            "toc_idx": i,
                        })
                        break

            has_epilogue = any(epilogue_pattern.search(c["title"]) for c in ch_candidates)
            if not has_epilogue:
                for i in range(last_toc_idx + 1, len(toc)):
                    lvl, title, pageno = toc[i][0], toc[i][1].strip(), toc[i][2]
                    if epilogue_pattern.search(title):
                        ch_candidates.append({
                            "title": title,
                            "start_page": max(0, pageno - 1),
                            "toc_idx": i,
                        })
                        break

            ch_candidates.sort(key=lambda x: x["start_page"])

            ch_num = 1
            for c in ch_candidates:
                if prologue_pattern.search(c["title"]):
                    c["ch_num"] = 0
                else:
                    c["ch_num"] = ch_num
                    ch_num += 1

            chapters: List[Dict[str, Any]] = []
            for i, ch in enumerate(ch_candidates):
                start_p = ch["start_page"]
                if i + 1 < len(ch_candidates):
                    end_p = ch_candidates[i + 1]["start_page"]
                else:
                    end_p = total_pages
                    if "toc_idx" in ch:
                        curr_toc_idx = ch["toc_idx"]
                        for next_item in toc[curr_toc_idx + 1:]:
                            next_p = max(0, next_item[2] - 1)
                            if next_p > start_p:
                                end_p = next_p
                                break
                chapters.append({
                    "ch_num": ch["ch_num"],
                    "title": ch["title"],
                    "start_page": start_p,
                    "end_page": min(total_pages, max(start_p + 1, end_p)),
                })
            return chapters

    # Strategy 1: Check TOC for Chapter / Chương / Part / Phần / Chinese chapter
    ch_pattern = TOC_CHAPTER_PATTERN

    for i, (lvl, title, pageno) in enumerate(lvl1_items):
        m = ch_pattern.search(title)
        if m:
            num_str = m.group(1)
            if not num_str:
                ch_num_match = re.search(r'[\d一二三四五六七八九十百千]+', m.group(0))
                num_str = ch_num_match.group(0) if ch_num_match else str(i + 1)
            if num_str.isdigit():
                num = int(num_str)
            elif any(c in '一二三四五六七八九十百千' for c in num_str):
                num = chinese_to_int(num_str)
            else:
                num = roman_to_int(num_str)
            ch_candidates.append({
                "ch_num": num,
                "title": title.strip(),
                "start_page": max(0, pageno - 1),
                "toc_idx": i,
            })

    # If level 1 didn't find chapters, check level 2 (for books grouped under Parts)
    if not ch_candidates and len(toc) > 0:
        lvl2_items = [item for item in toc if item[0] == 2]
        for i, (lvl, title, pageno) in enumerate(lvl2_items):
            m = ch_pattern.search(title)
            if not m:
                # also check leading number: "1 Embrace Reality"
                m = re.match(r'^\s*(\d+)\s+([A-Z].*)', title)
            if m:
                num_str = m.group(1)
                if not num_str:
                    ch_num_match = re.search(r'[\d一二三四五六七八九十百千]+', m.group(0))
                    num_str = ch_num_match.group(0) if ch_num_match else str(i + 1)
                if num_str.isdigit():
                    num = int(num_str)
                elif any(c in '一二三四五六七八九十百千' for c in num_str):
                    num = chinese_to_int(num_str)
                else:
                    num = roman_to_int(num_str)
                ch_candidates.append({
                    "ch_num": num,
                    "title": title.strip(),
                    "start_page": max(0, pageno - 1),
                    "toc_idx": i,
                })

    # Discover Prologue (ch_num: 0) and Epilogue (ch_num: max_ch_num + 1) if present in TOC bookmarks
    if ch_candidates:
        toc_items = []
        if lvl1_items and any("toc_idx" in c and c["toc_idx"] < len(lvl1_items) and lvl1_items[c["toc_idx"]][1].strip() == c["title"] for c in ch_candidates):
            toc_items = lvl1_items
        elif 'lvl2_items' in locals() and lvl2_items and any("toc_idx" in c and c["toc_idx"] < len(lvl2_items) and lvl2_items[c["toc_idx"]][1].strip() == c["title"] for c in ch_candidates):
            toc_items = lvl2_items

        if toc_items:
            first_toc_idx = min(c["toc_idx"] for c in ch_candidates if "toc_idx" in c)
            last_toc_idx = max(c["toc_idx"] for c in ch_candidates if "toc_idx" in c)
            max_ch_num = max(c["ch_num"] for c in ch_candidates)

            has_explicit_chapters = any(re.search(r'\b(?:Chapter|Chương)\b', c["title"], re.IGNORECASE) for c in ch_candidates)
            if has_explicit_chapters:
                prologue_pattern = re.compile(r'\b(?:prologue|introduction|lời mở đầu)\b', re.IGNORECASE)
                epilogue_pattern = re.compile(r'\b(?:epilogue|conclusion|lời kết|kết luận)\b', re.IGNORECASE)
            else:
                prologue_pattern = re.compile(r'\bprologue\b', re.IGNORECASE)
                epilogue_pattern = re.compile(r'\bepilogue\b', re.IGNORECASE)

            # Check before first chapter for Prologue
            for i in range(first_toc_idx):
                lvl, title, pageno = toc_items[i]
                if prologue_pattern.search(title):
                    ch_candidates.append({
                        "ch_num": 0,
                        "title": title.strip(),
                        "start_page": max(0, pageno - 1),
                        "toc_idx": i,
                    })
                    break

            # Check after last chapter for Epilogue
            for i in range(last_toc_idx + 1, len(toc_items)):
                lvl, title, pageno = toc_items[i]
                if epilogue_pattern.search(title):
                    ch_candidates.append({
                        "ch_num": max_ch_num + 1,
                        "title": title.strip(),
                        "start_page": max(0, pageno - 1),
                        "toc_idx": i,
                    })
                    break

        # Verification of start_page and deduplication of chapter numbers for candidates gathered from TOC
        ch_candidates.sort(key=lambda x: (0 if x.get("ch_num") == 0 else 1, x.get("toc_idx", 0), x.get("start_page", 0)))

        prev_start = -1
        for ch in ch_candidates:
            cand_title = ch["title"]
            cand_start = ch["start_page"]
            min_page = prev_start + 1

            valid = False
            if cand_start >= min_page and cand_start < total_pages:
                page_lines = [l.strip() for l in doc[cand_start].get_text().splitlines() if l.strip()]
                if _matches_chapter_heading(page_lines, cand_title):
                    valid = True

            if not valid:
                search_start = max(min_page, cand_start)
                found_page = None
                for p in range(search_start, total_pages):
                    p_lines = [l.strip() for l in doc[p].get_text().splitlines() if l.strip()]
                    if _matches_chapter_heading(p_lines, cand_title):
                        found_page = p
                        break
                if found_page is not None:
                    ch["start_page"] = found_page
                else:
                    ch["start_page"] = max(min_page, cand_start)

            prev_start = ch["start_page"]

        # If candidates in ch_candidates have duplicate ch_num (e.g. Chapter 5a and Chapter 5b both having ch_num: 5):
        # Renumber chapters sequentially starting from 1 (preserving ch_num: 0 for prologue/introduction if present).
        non_zero_nums = [c["ch_num"] for c in ch_candidates if c.get("ch_num", 0) != 0]
        if len(non_zero_nums) != len(set(non_zero_nums)):
            next_ch_num = 1
            for c in ch_candidates:
                if c.get("ch_num") == 0:
                    continue
                c["ch_num"] = next_ch_num
                next_ch_num += 1

    # Strategy 2: If TOC has level 1 items but none matched chapter regex, take non-front-matter items
    if not ch_candidates and lvl1_items:
        front_matter = {"cover", "copyright", "table of contents", "contents", "foreword", "preface", "acknowledgments"}
        filtered = [item for item in lvl1_items if item[1].strip().lower() not in front_matter]
        if filtered:
            for idx, (lvl, title, pageno) in enumerate(filtered, start=1):
                ch_candidates.append({
                    "ch_num": idx,
                    "title": title.strip(),
                    "start_page": max(0, pageno - 1),
                    "toc_idx": idx - 1,
                })

    # Strategy 3: No TOC or empty: scan page texts for chapter headings
    if not ch_candidates:
        page_pattern = TOC_CHAPTER_PATTERN
        found_nums = set()
        for pno in range(total_pages):
            txt = doc[pno].get_text()
            m = page_pattern.search(txt)
            if m:
                num_str = m.group(1)
                if not num_str:
                    ch_num_match = re.search(r'[\d一二三四五六七八九十百千]+', m.group(0))
                    num_str = ch_num_match.group(0) if ch_num_match else str(pno + 1)
                if num_str.isdigit():
                    num = int(num_str)
                elif any(c in '一二三四五六七八九十百千' for c in num_str):
                    num = chinese_to_int(num_str)
                else:
                    num = roman_to_int(num_str)
                if num not in found_nums:
                    found_nums.add(num)
                    first_line = txt[m.start():].split("\n")[0].strip()
                    ch_candidates.append({
                        "ch_num": num,
                        "title": first_line,
                        "start_page": pno,
                    })

    # Sort candidates by start_page
    ch_candidates.sort(key=lambda x: x["start_page"])

    # Determine end_page for each chapter
    chapters: List[Dict[str, Any]] = []
    for i, ch in enumerate(ch_candidates):
        start_p = ch["start_page"]
        if i + 1 < len(ch_candidates):
            end_p = ch_candidates[i + 1]["start_page"]
        else:
            # Last chapter ends at next top-level item or doc end
            end_p = total_pages
            if lvl1_items and "toc_idx" in ch:
                curr_idx = ch["toc_idx"]
                if curr_idx + 1 < len(lvl1_items):
                    end_p = max(start_p + 1, lvl1_items[curr_idx + 1][2] - 1)

        chapters.append({
            "ch_num": ch["ch_num"],
            "title": ch["title"],
            "start_page": start_p,
            "end_page": min(total_pages, max(start_p + 1, end_p)),
        })

    return chapters


def is_running_header_or_footer(
    bbox: Any = None,
    page_height: float = 0.0,
    text: str = "",
    page_num: Optional[int] = None,
    total_pages: Optional[int] = None,
) -> bool:
    """Detects running headers and footers (page numbers, book/chapter titles at margins, pipe headers, aggregator watermarks)."""
    # Support callers passing (block_text, page_num, total_pages)
    if isinstance(bbox, str):
        text = bbox
        bbox = (0.0, 0.0, 0.0, 0.0)
        page_height = 1000.0
    elif bbox is None:
        bbox = (0.0, 0.0, 0.0, 0.0)

    stripped = text.strip() if text else ""
    if not stripped:
        return True

    # Check for pirate/aggregator watermarks anywhere on the page
    if re.search(r"(?i)\b(?:oceanofpdf\.com|_oceanofpdf\.com_|www\.oceanofpdf\.com)\b", stripped):
        return True

    y0, y1 = bbox[1], bbox[3]

    # Pattern for O'Reilly pipe headers/footers (single-line or multi-line)
    # Examples:
    # "2 | Chapter 1: Basics"
    # "Chapter 1: Basics | 2"
    # "2\n|\nChapter 1: Basics"
    # "Chapter 1: Basics\n|\n2"
    pipe_pattern = re.compile(
        r'(?:^\d+\s*(?:\n\s*)?\|\s*(?:\n\s*)?.+$)|(?:^.+\s*(?:\n\s*)?\|\s*(?:\n\s*)?\d+$)',
        re.DOTALL
    )

    # Check standard margin zones (top/bottom 65pt)
    is_in_margin = (y0 > page_height - 65) or (y1 < 65)

    if is_in_margin:
        # Standalone digits or Roman numerals
        if re.match(r'^\d+$', stripped) or re.match(r'^[ivxlcdmIVXLCDM]+$', stripped):
            return True
        # Pipe header/footer
        if pipe_pattern.match(stripped):
            return True
        # General short header/footer containing pipe
        if "|" in stripped and (re.search(r'\d+', stripped) or len(stripped.splitlines()) <= 3):
            return True

    # Wider margin check (up to 95pt) for explicit multi-line or single-line pipe headers
    is_near_edge = (y0 > page_height - 95) or (y1 < 95)
    if is_near_edge:
        # Multi-line pipe headers: \d+\s*\n\|\s*\n[^\n]+ or vice-versa
        if re.search(r'^\d+\s*\n\s*\|\s*\n\s*.+$', stripped, re.DOTALL) or re.search(r'^.+\s*\n\s*\|\s*\n\s*\d+$', stripped, re.DOTALL):
            return True
        if re.match(r'^\d+\s*\|\s*(?:Chapter|Chương|Part|Phần|[A-Za-z0-9\s]+).*$', stripped, re.IGNORECASE):
            return True
        if re.match(r'^(?:Chapter|Chương|Part|Phần|[A-Za-z0-9\s]+)\s*\|\s*\d+$', stripped, re.IGNORECASE):
            return True

    return False


def strip_header_footer_lines(text: str, bbox: Tuple[float, float, float, float], page_height: float) -> str:
    """Strips running header or footer lines from within a text block if present near margins."""
    lines = text.split("\n")
    if not lines:
        return text
    # Strip pirate/aggregator watermarks
    lines = [l for l in lines if not re.search(r"(?i)\b(?:oceanofpdf\.com|_oceanofpdf\.com_|www\.oceanofpdf\.com)\b", l.strip())]
    if not lines:
        return ""
    # If merged near top of page (header):
    if bbox[1] < 100 and len(lines) > 1:
        first_few = "\n".join(lines[:3])
        m = re.match(r'^\d+\s*\n\s*\|\s*\n\s*[^\n]+\n?', first_few) or re.match(r'^\d+\s*\|\s*[^\n]+\n?', first_few)
        if m:
            matched_len = len(m.group(0).strip().split("\n"))
            lines = lines[matched_len:]
    # If merged near bottom of page (footer):
    if bbox[3] > page_height - 100 and len(lines) > 1:
        last_few = "\n".join(lines[-3:])
        m = re.search(r'\n?(\d+\s*\n\s*\|\s*\n\s*[^\n]+)$', last_few) or re.search(r'\n?(\d+\s*\|\s*[^\n]+)$', last_few)
        if m:
            matched_len = len(m.group(0).strip().split("\n"))
            lines = lines[:-matched_len]
    return "\n".join(lines).strip()


# Table and footnote guardrails (REQ-007, REQ-008)
MAX_TABLE_ROWS = 200
MAX_TABLE_COLS = 30
MAX_CELL_CHARS = 2000
MAX_FOOTNOTES_PER_PAGE = 50

FN_MARKER_RE = re.compile(
    r"^(?:\[(?P<b_num>\d{1,3})\]|(?P<d_num>\d{1,3})[\.\)]|(?P<sup_num>[¹²³⁴⁵⁶⁷⁸⁹⁰]{1,3})|(?P<sym>[*†‡§])|(?P<raw_num>\d{1,3}))\s+(?P<body_text>.+)$",
    re.DOTALL,
)


def extract_tables_from_page(page: Any) -> List[Dict[str, Any]]:
    """
    Extracts tabular regions from a PyMuPDF page using page.find_tables().
    Enforces complexity guardrails (AC-007.1):
      - 2 <= rows <= MAX_TABLE_ROWS (200)
      - 2 <= cols <= MAX_TABLE_COLS (30)
      - cell length <= MAX_CELL_CHARS (2000)
      - valid non-degenerate bounding box (x1 > x0 and y1 > y0)
    Formats cells:
      - internal \\n collapsed to <br/>
      - unescaped | escaped as \\|
    Emits <!-- TABLE_START -->\\n<gfm_lines>\\n<!-- TABLE_END -->.
    """
    if not hasattr(page, "find_tables"):
        return []

    try:
        table_finder = page.find_tables()
    except Exception as e:
        sys.stderr.write(f"Warning: page.find_tables() failed: {e}\n")
        return []

    tables_found: List[Dict[str, Any]] = []
    tables_list = getattr(table_finder, "tables", None) or list(table_finder)

    for tab in tables_list:
        bbox = getattr(tab, "bbox", None)
        if not bbox or len(bbox) != 4:
            continue
        x0, y0, x1, y1 = bbox
        if x1 <= x0 or y1 <= y0:
            continue

        try:
            raw_rows = tab.extract()
        except Exception:
            continue

        if not raw_rows or not isinstance(raw_rows, list):
            continue

        num_rows = len(raw_rows)
        if num_rows < 2 or num_rows > MAX_TABLE_ROWS:
            continue

        # Determine column count from non-empty row lengths
        col_count = max(len(r) for r in raw_rows) if raw_rows else 0
        if col_count < 2 or col_count > MAX_TABLE_COLS:
            continue

        # Format rows
        formatted_gfm_lines: List[str] = []
        for r_idx, row in enumerate(raw_rows):
            # Pad row if shorter than col_count
            row_cells = list(row) if isinstance(row, (list, tuple)) else [str(row)]
            if len(row_cells) < col_count:
                row_cells.extend([""] * (col_count - len(row_cells)))
            elif len(row_cells) > col_count:
                row_cells = row_cells[:col_count]

            cleaned_cells: List[str] = []
            for cell in row_cells:
                c_str = "" if cell is None else str(cell)
                # Truncate oversized cells
                if len(c_str) > MAX_CELL_CHARS:
                    c_str = c_str[:MAX_CELL_CHARS]
                # Collapse internal \n to <br/>
                c_str = re.sub(r"\r?\n+", "<br/>", c_str.strip())
                # Escape unescaped pipe characters
                c_str = re.sub(r"(?<!\\)\|", r"\|", c_str)
                cleaned_cells.append(c_str)

            formatted_gfm_lines.append("| " + " | ".join(cleaned_cells) + " |")

            # Insert separator row after row 0 (header)
            if r_idx == 0:
                sep_cells = ["---"] * col_count
                formatted_gfm_lines.append("| " + " | ".join(sep_cells) + " |")

        gfm_table = "\n".join(formatted_gfm_lines)
        table_block = f"<!-- TABLE_START -->\n{gfm_table}\n<!-- TABLE_END -->"
        tables_found.append({
            "type": "table",
            "content": table_block,
            "bbox": (float(x0), float(y0), float(x1), float(y1)),
            "y0": float(y0),
            "x0": float(x0),
        })

    return tables_found


def extract_page_footnotes(
    page_dict: Dict[str, Any],
    page_height: float,
    median_body_size: float,
    chapter_fn_counter: Any,
) -> Tuple[List[str], List[Tuple[float, float, float, float]]]:
    """
    Detects bottom-zone (y0 > 0.78 * H) blocks with font size <= min(8.5, median_body_size - 1.0pt)
    and leading footnote markers (1, [1], *, †), renumbers chapter-globally ([^1], [^2], ...),
    and rewrites matching superscript in-body spans into [^N].
    Returns (footnote_defs, footnote_bboxes).
    """
    blocks = page_dict.get("blocks", [])
    max_fn_font_size = min(8.5, median_body_size - 1.0)
    candidate_footnotes: List[Tuple[Dict[str, Any], str, str]] = []

    for b in blocks:
        if b.get("type", 0) != 0:
            continue
        bbox = b.get("bbox", (0, 0, 0, 0))
        y0 = bbox[1]
        if y0 <= page_height * 0.78:
            continue

        spans = [s for l in b.get("lines", []) for s in l.get("spans", []) if s.get("text", "").strip()]
        if not spans:
            continue
        max_size = max(s.get("size", 10.0) for s in spans)
        if max_size > max_fn_font_size:
            continue

        raw_text = "\n".join("".join(s.get("text", "") for s in l.get("spans", [])) for l in b.get("lines", [])).strip()
        m = FN_MARKER_RE.match(raw_text)
        if m:
            loc_marker = m.group("b_num") or m.group("d_num") or m.group("sup_num") or m.group("sym") or m.group("raw_num")
            body_text = m.group("body_text").strip()
            candidate_footnotes.append((b, loc_marker, body_text))

    if len(candidate_footnotes) > MAX_FOOTNOTES_PER_PAGE:
        # Capped at MAX_FOOTNOTES_PER_PAGE; excess blocks kept in body stream (AC-008.1)
        candidate_footnotes = candidate_footnotes[:MAX_FOOTNOTES_PER_PAGE]

    footnote_defs: List[str] = []
    footnote_bboxes: List[Tuple[float, float, float, float]] = []
    marker_map: Dict[str, int] = {}

    for b, loc_marker, body_text in candidate_footnotes:
        bbox = b.get("bbox", (0, 0, 0, 0))
        footnote_bboxes.append(bbox)
        global_num = next(chapter_fn_counter) if hasattr(chapter_fn_counter, "__next__") else chapter_fn_counter
        marker_map[loc_marker] = global_num
        footnote_defs.append(f"[^{global_num}]: {body_text}")

    # Rewrite matching superscript in-body spans on this page
    if marker_map:
        for b in blocks:
            if b.get("bbox") in footnote_bboxes:
                continue
            for l in b.get("lines", []):
                for s in l.get("spans", []):
                    txt = s.get("text", "").strip()
                    clean_m = txt.strip("[]")
                    s_size = s.get("size", 10.0)
                    is_super = bool(s.get("flags", 0) & 1) or s_size <= max_fn_font_size or txt in ("*", "†", "‡", "§")
                    if (is_super and clean_m in marker_map) or (
                        txt.startswith("[") and txt.endswith("]") and clean_m in marker_map and s_size <= median_body_size - 0.5
                    ):
                        matched_num = marker_map[clean_m]
                        raw_s = s.get("text", "")
                        s["text"] = raw_s.replace(txt, f"[^{matched_num}]")

    return footnote_defs, footnote_bboxes


def _is_bbox_overlapping(b1: Tuple[float, float, float, float], b2: Tuple[float, float, float, float]) -> bool:
    """Checks if bounding box b1 significantly overlaps b2."""
    cx = (b1[0] + b1[2]) / 2
    cy = (b1[1] + b1[3]) / 2
    if b2[0] <= cx <= b2[2] and b2[1] <= cy <= b2[3]:
        return True
    inter_x0 = max(b1[0], b2[0])
    inter_y0 = max(b1[1], b2[1])
    inter_x1 = min(b1[2], b2[2])
    inter_y1 = min(b1[3], b2[3])
    if inter_x1 > inter_x0 and inter_y1 > inter_y0:
        inter_area = (inter_x1 - inter_x0) * (inter_y1 - inter_y0)
        b1_area = (b1[2] - b1[0]) * (b1[3] - b1[1])
        if b1_area > 0 and inter_area / b1_area > 0.3:
            return True
    return False


def segment_chapter_content(text: str) -> str:
    """
    Wraps chapter content into indexed XML segments:
    <segment id="S-0001" type="heading|paragraph|code|figure|table">
    <original>
    ...
    </original>
    </segment>
    """
    if not text or not text.strip():
        return ""

    # If already segmented, return as is
    if "<segment " in text and "</segment>" in text:
        return text

    text = dehyphenate_text(text)

    blocks: List[Tuple[str, str]] = []
    lines = text.splitlines()
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # Skip empty lines outside blocks
        if not line.strip():
            i += 1
            continue

        # 0. Table block (<!-- TABLE_START --> ... <!-- TABLE_END -->)
        if line.strip().startswith("<!-- TABLE_START -->"):
            table_lines = []
            i += 1
            while i < n:
                if lines[i].strip().startswith("<!-- TABLE_END -->"):
                    i += 1
                    break
                table_lines.append(lines[i])
                i += 1
            blocks.append(("table", "\n".join(table_lines).strip()))
            continue

        # 1. Code fence block
        if line.strip().startswith("```"):
            code_lines = [line]
            i += 1
            while i < n:
                code_lines.append(lines[i])
                if lines[i].strip().startswith("```"):
                    i += 1
                    break
                i += 1
            blocks.append(("code", "\n".join(code_lines).strip()))
            continue

        # 2. Heading block
        if line.strip().startswith("#"):
            blocks.append(("heading", line.strip()))
            i += 1
            continue

        # 3. Figure block
        fig_match = re.match(r'^\s*!\[.*?\]\(.*?\)\s*$', line)
        if fig_match:
            blocks.append(("figure", line.strip()))
            i += 1
            continue

        # 4. Paragraph block (collect until blank line, code fence, heading, figure, or table)
        para_lines = [line]
        i += 1
        while i < n:
            curr = lines[i]
            if not curr.strip():
                break
            if (
                curr.strip().startswith("```")
                or curr.strip().startswith("#")
                or curr.strip().startswith("<!-- TABLE_START -->")
                or re.match(r'^\s*!\[.*?\]\(.*?\)\s*$', curr)
            ):
                break
            para_lines.append(curr)
            i += 1
        blocks.append(("paragraph", "\n".join(para_lines).strip()))

    segments = []
    for idx, (b_type, content) in enumerate(blocks, start=1):
        seg_id = f"S-{idx:04d}"
        seg = (
            f'<segment id="{seg_id}" type="{b_type}">\n'
            f'<original>\n{content}\n</original>\n'
            f'</segment>'
        )
        segments.append(seg)

    return "\n\n".join(segments) + "\n"


def extract_chapter_content(
    doc: Any,
    ch_info: Dict[str, Any],
    images_dir: str,
    toc_titles: List[str],
    xml_segments: bool = False,
) -> str:
    """
    Extracts text, tables, footnotes, and figures for a chapter, placing markdown
    image anchors ![Figure](images/fig_<ch>_<index>.<ext>) at the image location.
    """
    ch_num = ch_info["ch_num"]
    start_p = ch_info["start_page"]
    end_p = ch_info["end_page"]

    image_counter = 0
    chapter_fn_counter = itertools.count(1)
    all_chapter_footnotes: List[str] = []
    extracted_elements: List[str] = []

    # Insert chapter main title header
    title = ch_info["title"]
    if not title.lower().startswith("#"):
        extracted_elements.append(f"# {title}\n")

    for pno in range(start_p, end_p):
        page = doc[pno]
        p_height = page.rect.height

        # 0. Collect tables on this page (AC-007.1)
        table_items = extract_tables_from_page(page)
        table_bboxes = [t["bbox"] for t in table_items]

        # 1. Collect images on this page
        img_items: List[Dict[str, Any]] = []
        page_images = page.get_image_info(xrefs=True) if hasattr(page, "get_image_info") else []
        for img in page_images:
            xref = img.get("xref", 0)
            if xref <= 0:
                continue
            try:
                img_data = doc.extract_image(xref)
                if not img_data or "image" not in img_data:
                    continue
                ext = img_data.get("ext", "png")
                img_filename = f"fig_{ch_num:02d}_{image_counter}.{ext}"
                img_path = os.path.join(images_dir, img_filename)

                with open(img_path, "wb") as f:
                    f.write(img_data["image"])

                img_anchor = f"![Figure](images/{img_filename})"
                bbox = img.get("bbox", (0, 0, 0, 0))
                img_items.append({
                    "type": "image",
                    "y0": bbox[1],
                    "x0": bbox[0],
                    "content": img_anchor,
                })
                image_counter += 1
            except Exception as e:
                sys.stderr.write(f"Warning: Failed to extract image xref {xref} on page {pno+1}: {e}\n")

        # 2. Collect text blocks on this page
        text_dict = page.get_text("dict") if hasattr(page, "get_text") else {"blocks": []}
        all_sizes = [
            s.get("size", 10.0)
            for b in text_dict.get("blocks", [])
            for line in b.get("lines", [])
            for s in line.get("spans", [])
            if s.get("text", "").strip()
        ]
        median_body_size = float(statistics.median(all_sizes)) if all_sizes else 10.0

        # Pre-footer footnote extraction (AC-008.1)
        page_fn_defs, fn_bboxes = extract_page_footnotes(
            page_dict=text_dict,
            page_height=p_height,
            median_body_size=median_body_size,
            chapter_fn_counter=chapter_fn_counter,
        )
        all_chapter_footnotes.extend(page_fn_defs)

        block_items: List[Dict[str, Any]] = []

        for b in text_dict.get("blocks", []):
            if b.get("type", 0) != 0:
                continue  # Skip non-text blocks
            bbox = b.get("bbox", (0, 0, 0, 0))

            # Exclude footnote blocks from body text stream
            if bbox in fn_bboxes:
                continue

            # Suppress text blocks overlapping extracted tables
            if any(_is_bbox_overlapping(bbox, tb) for tb in table_bboxes):
                continue

            lines = b.get("lines", [])
            if not lines:
                continue

            all_spans = [s for line in lines for s in line.get("spans", []) if s.get("text", "").strip()]
            if not all_spans:
                continue

            raw_text = "\n".join("".join(s.get("text", "") for s in line.get("spans", [])) for line in lines).strip()
            if not raw_text:
                continue

            # Filter running headers/footers
            if is_running_header_or_footer(bbox, p_height, raw_text):
                continue

            # Strip inline running header/footer lines if merged
            raw_text = strip_header_footer_lines(raw_text, bbox, p_height)
            if not raw_text:
                continue

            # Suppress any block matching aggregator watermark
            if re.search(r"(?i)\b(?:oceanofpdf\.com|_oceanofpdf\.com_|www\.oceanofpdf\.com)\b", raw_text):
                continue

            # Detect code block: all spans have monospace font
            mono_spans = [
                s for s in all_spans
                if any(m in s.get("font", "").lower() for m in ("mono", "courier", "consolas", "menlo"))
            ]
            is_code = len(mono_spans) == len(all_spans) and len(all_spans) > 0

            # Detect section heading
            is_heading = False
            for t in toc_titles:
                if raw_text == t or raw_text.startswith(t + "\n"):
                    is_heading = True
                    break

            block_items.append({
                "type": "code" if is_code else ("heading" if is_heading else "text"),
                "y0": bbox[1],
                "x0": bbox[0],
                "content": raw_text,
            })

        # 3. Merge items on this page in vertical order
        page_items = img_items + block_items + table_items
        page_items.sort(key=lambda item: (item["y0"], item["x0"]))

        # 4. Group consecutive code blocks and format markdown
        idx = 0
        while idx < len(page_items):
            item = page_items[idx]
            if item["type"] == "code":
                code_lines = [item["content"]]
                while idx + 1 < len(page_items) and page_items[idx + 1]["type"] == "code":
                    idx += 1
                    code_lines.append(page_items[idx]["content"])
                code_text = "\n".join(code_lines)
                is_rust = (
                    "rust" in str(getattr(doc, "metadata", {}) or {}).lower()
                    or (isinstance(ch_info, dict) and "rust" in str(ch_info.get("title", "")).lower())
                    or any(k in code_text for k in ("fn main", "thread::spawn", "use std::", "pub fn", "let mut "))
                )
                code_fence = "```rust" if is_rust else "```"
                extracted_elements.append(f"{code_fence}\n{code_text}\n```")
            elif item["type"] == "image":
                extracted_elements.append(item["content"])
            elif item["type"] == "heading":
                extracted_elements.append(f"## {item['content']}")
            elif item["type"] == "table":
                extracted_elements.append(item["content"])
            else:
                extracted_elements.append(item["content"])
            idx += 1

    # Append chapter footnotes as translatable paragraph segments at the end of chapter (AC-008.2)
    if all_chapter_footnotes:
        extracted_elements.extend(all_chapter_footnotes)

    res = "\n\n".join(extracted_elements) + "\n"
    res = dehyphenate_text(res)
    if xml_segments:
        res = segment_chapter_content(res)
    return res




class PDFExtractor(BaseExtractor):
    """Extracts chapters, TOC, and images from PDF documents using PyMuPDF."""

    def extract(
        self,
        input_path: Path,
        output_dir: Path,
        chapters: Optional[str] = None,
        slug: Optional[str] = None,
        xml_segments: bool = True,
        toc_level: str = "auto",
        **kwargs,
    ) -> List[Dict[str, Any]]:
        input_path = Path(input_path).resolve()
        if not input_path.is_file():
            raise FileNotFoundError(f"PDF file not found: {input_path}")

        output_dir = Path(output_dir).resolve()
        images_dir = output_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        doc = pymupdf.open(str(input_path))
        toc = doc.get_toc()
        toc_titles = [item[1].strip() for item in toc if item[0] >= 2]

        all_chapters = discover_chapters(doc, toc_level=toc_level)

        ch_filter = self.parse_chapters_filter(chapters)
        if ch_filter:
            target_chapters = [ch for ch in all_chapters if ch["ch_num"] in ch_filter]
        else:
            target_chapters = all_chapters

        results = []
        for ch in target_chapters:
            ch_num = ch["ch_num"]
            ch_dir = output_dir / f"ch{ch_num:02d}"
            ch_dir.mkdir(parents=True, exist_ok=True)

            source_txt_path = ch_dir / "source.txt"
            content = extract_chapter_content(
                doc,
                ch,
                str(images_dir),
                toc_titles,
                xml_segments=xml_segments,
            )
            with open(source_txt_path, "w", encoding="utf-8") as f:
                f.write(content)

            results.append({
                "ch_num": ch_num,
                "title": ch.get("title", f"Chapter {ch_num}"),
                "start_page": ch.get("start_page"),
                "end_page": ch.get("end_page"),
                "source_path": str(source_txt_path),
            })

        self.init_checklist(output_dir, results)
        return results
