#!/usr/bin/env python3
"""
scripts/extractors/text_extractor.py — Plain-Text novel extractor subclassing BaseExtractor.
Extracts Chinese web novels (.txt) with encoding autodetection (GB18030/GBK/UTF-8),
watermark banner filtering, preface (Chapter 0) extraction, and XML segmenting.
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from scripts.extractors.base import BaseExtractor


class TextExtractor(BaseExtractor):
    """Extracts chapters from plain text (.txt) documents into standard XML segments."""

    CHAPTER_REGEX = re.compile(r"^\s*(第[一二三四五六七八九十百千\d]+[章回节])\s*(.*)$")
    VOLUME_REGEX = re.compile(r"^\s*(第[一二三四五六七八九十百千\d]+卷)\s*(.*)$")
    VOLUME_END_REGEX = re.compile(r"^\s*[（(].*卷.*[终结][）)]\s*$")

    PROMO_PATTERNS = [
        re.compile(r"知轩藏书", re.IGNORECASE),
        re.compile(r"zxcs\.me", re.IGNORECASE),
        re.compile(r"^\s*={4,}\s*$"),
        re.compile(r"^\s*-{4,}\s*$"),
        re.compile(r"^\s*更多精校小说.*$"),
        re.compile(r"^\s*更多txt小说.*$"),
    ]

    def decode_text(self, raw_bytes: bytes) -> str:
        """Autodetects text encoding and decodes bytes into a clean Unicode string."""
        encodings = ["utf-8", "gb18030", "gbk", "utf-16"]
        for enc in encodings:
            try:
                return raw_bytes.decode(enc)
            except UnicodeDecodeError:
                continue
        # Fallback to gb18030 with replace
        return raw_bytes.decode("gb18030", errors="replace")

    def is_promo_line(self, line: str) -> bool:
        """Checks if a line matches known promotional watermark patterns."""
        for pattern in self.PROMO_PATTERNS:
            if pattern.search(line):
                return True
        return False

    def extract(
        self,
        input_path: Path,
        output_dir: Path,
        chapters: Optional[str] = None,
        slug: Optional[str] = None,
        source_lang: str = "zh",
        xml_segments: bool = True,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        input_path = Path(input_path).resolve()
        if not input_path.is_file():
            raise FileNotFoundError(f"Text file not found: {input_path}")

        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        images_dir = output_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        raw_bytes = input_path.read_bytes()
        full_text = self.decode_text(raw_bytes)
        lines = full_text.splitlines()

        # Parse into raw sections: Preface (ch00) and Chapters (ch01..N)
        raw_chapters: List[Dict[str, Any]] = []
        preface_lines: List[str] = []
        current_ch: Optional[Dict[str, Any]] = None
        current_vol_title: str = ""
        ch_counter = 1
        found_first_ch = False

        for line in lines:
            line_str = line.strip()

            # Filter promotional lines
            if self.is_promo_line(line_str):
                continue

            # Check volume marker
            vol_match = self.VOLUME_REGEX.match(line_str)
            if vol_match:
                current_vol_title = line_str
                if not found_first_ch:
                    # In preface, keep volume title as part of context if needed
                    continue
                else:
                    if current_ch:
                        current_ch["volume"] = current_vol_title
                    continue

            # Check volume end marker
            if self.VOLUME_END_REGEX.match(line_str):
                continue

            # Check chapter marker
            ch_match = self.CHAPTER_REGEX.match(line_str)
            if ch_match:
                found_first_ch = True
                if current_ch:
                    raw_chapters.append(current_ch)

                ch_marker = ch_match.group(1).strip()
                ch_name = ch_match.group(2).strip()
                full_title = f"{ch_marker} {ch_name}".strip()

                current_ch = {
                    "ch_num": ch_counter,
                    "title": full_title,
                    "volume": current_vol_title,
                    "lines": [f"# {full_title}"],
                }
                ch_counter += 1
            else:
                if not found_first_ch:
                    if line_str:
                        preface_lines.append(line_str)
                else:
                    if current_ch is not None and line_str:
                        current_ch["lines"].append(line_str)

        if current_ch:
            raw_chapters.append(current_ch)

        # Build final chapters list
        final_chapters: List[Dict[str, Any]] = []

        # If preface exists, add as Chapter 0
        if preface_lines and any(l.strip() for l in preface_lines):
            preface_content = "# 序言 / 内容简介\n\n" + "\n\n".join(preface_lines)
            final_chapters.append({
                "ch_num": 0,
                "title": "Lời tựa & Giới thiệu",
                "volume": "Tiền ngôn",
                "content": preface_content,
            })

        for ch in raw_chapters:
            content = "\n\n".join(ch["lines"])
            final_chapters.append({
                "ch_num": ch["ch_num"],
                "title": ch["title"],
                "volume": ch.get("volume", ""),
                "content": content,
            })

        # Apply chapter filter
        ch_filter = self.parse_chapters_filter(chapters)
        extracted: List[Dict[str, Any]] = []

        for ch in final_chapters:
            ch_num = ch["ch_num"]
            if ch_filter is not None and ch_num not in ch_filter:
                continue

            source_path = self.write_chapter_source(
                output_dir=output_dir,
                ch_num=ch_num,
                content=ch["content"],
                xml_segments=xml_segments,
            )

            extracted.append({
                "ch_num": ch_num,
                "title": ch["title"],
                "volume": ch.get("volume", ""),
                "source_path": str(source_path),
            })

        # Initialize checklist.json
        self.init_text_checklist(output_dir, final_chapters if ch_filter is None else extracted, source_lang)

        return extracted

    @staticmethod
    def init_text_checklist(
        output_dir: Path,
        chapters: List[Dict[str, Any]],
        source_lang: str = "zh",
    ) -> List[Dict[str, Any]]:
        """Initializes or updates checklist.json with chapter and language metadata."""
        checklist_path = output_dir / "checklist.json"
        checklist: List[Dict[str, Any]] = []

        if checklist_path.exists():
            try:
                with open(checklist_path, "r", encoding="utf-8") as f:
                    checklist = json.load(f)
            except Exception:
                checklist = []

        existing = {entry.get("chapter"): entry for entry in checklist if isinstance(entry, dict)}
        updated = []

        for ch in chapters:
            ch_num = ch.get("ch_num", 0)
            ch_id = f"ch{ch_num:02d}"
            title = ch.get("title", f"Chapter {ch_num}")

            if ch_id in existing:
                updated.append(existing[ch_id])
            else:
                updated.append({
                    "chapter": ch_id,
                    "chapter_num": ch_num,
                    "title": title,
                    "volume": ch.get("volume", ""),
                    "source_lang": source_lang,
                    "status": "pending",
                    "round": 0,
                    "score": None,
                    "draft_exists": False,
                })

        with open(checklist_path, "w", encoding="utf-8") as f:
            json.dump(updated, f, indent=2, ensure_ascii=False)

        return updated
