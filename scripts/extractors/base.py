#!/usr/bin/env python3
"""
scripts/extractors/base.py — Abstract Base Class and common utilities for format extractors.
"""

from abc import ABC, abstractmethod
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

MAX_ASSET_BYTES = 50 * 1024 * 1024      # 50 MB per member
MAX_ARCHIVE_BYTES = 500 * 1024 * 1024   # 500 MB total archive limit


def safe_extract_archive_member(
    zip_file: zipfile.ZipFile,
    member_name: str,
    target_dir: Path,
    running_total: int = 0,
    cumulative_bytes: Optional[int] = None,
) -> Tuple[Path, int]:
    """Extracts a zip archive member safely, preventing Zip-Slip and decompression bombs.

    Preserves member subpath while verifying destination remains strictly inside target_dir.
    Enforces per-member limit (MAX_ASSET_BYTES) and cumulative limit (MAX_ARCHIVE_BYTES).
    """
    if cumulative_bytes is not None:
        running_total = cumulative_bytes

    target_dir_resolved = target_dir.resolve()
    # PRESERVE member subpath while checking path escape
    dest_path = (target_dir / member_name).resolve()
    if not dest_path.is_relative_to(target_dir_resolved):
        raise PermissionError(f"Blocked Zip-Slip path traversal attempt: {member_name}")

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    info = zip_file.getinfo(member_name)
    if info.is_dir():
        return dest_path, running_total

    extracted_bytes = 0
    with zip_file.open(info) as src, open(dest_path, "wb") as dst:
        while True:
            chunk = src.read(65536)
            if not chunk:
                break
            extracted_bytes += len(chunk)
            running_total += len(chunk)
            if extracted_bytes > MAX_ASSET_BYTES:
                dest_path.unlink(missing_ok=True)
                raise ValueError(f"Archive member exceeds {MAX_ASSET_BYTES} bytes limit")
            if running_total > MAX_ARCHIVE_BYTES:
                dest_path.unlink(missing_ok=True)
                raise ValueError(f"Total archive decompression exceeds {MAX_ARCHIVE_BYTES} bytes limit")
            dst.write(chunk)

    return dest_path, running_total



class BaseExtractor(ABC):
    """Abstract base class for document format extractors."""

    @abstractmethod
    def extract(
        self,
        input_path: Path,
        output_dir: Path,
        chapters: Optional[str] = None,
        slug: Optional[str] = None,
        xml_segments: bool = True,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """
        Extracts chapters and images from the input document into output_dir.
        Initializes checklist.json in output_dir.
        Returns a list of chapter metadata dictionaries.
        """
        pass

    @staticmethod
    def parse_chapters_filter(chapters_str: Optional[str]) -> Optional[Set[int]]:
        """
        Parses chapter filter string into a set of integers.
        Supports:
          - "1-3" -> {1, 2, 3}
          - "1,3,5" -> {1, 3, 5}
          - "2" -> {2}
          - "all", "", None -> None
        """
        if not chapters_str or str(chapters_str).strip().lower() in ("all", "none", ""):
            return None

        result: Set[int] = set()
        parts = str(chapters_str).split(",")
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if ".." in part:
                sub = part.split("..", 1)
                try:
                    start_ch = int(sub[0].strip())
                    end_ch = int(sub[1].strip())
                    result.update(range(start_ch, end_ch + 1))
                except ValueError:
                    continue
            elif "-" in part:
                sub = part.split("-", 1)
                try:
                    start_ch = int(sub[0].strip())
                    end_ch = int(sub[1].strip())
                    result.update(range(start_ch, end_ch + 1))
                except ValueError:
                    continue
            else:
                try:
                    result.add(int(part))
                except ValueError:
                    continue
        return result if result else None

    @staticmethod
    def segment_chapter_content(text: str) -> str:
        """
        Wraps markdown content into canonical indexed XML segments:
        <segment id="S-0001" type="heading|paragraph|code|figure">
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

            # 4. Paragraph block (collect until blank line, code fence, heading, figure, or new footnote item)
            para_lines = [line]
            i += 1
            while i < n:
                curr = lines[i]
                if not curr.strip():
                    break
                if curr.strip().startswith("```") or curr.strip().startswith("#") or re.match(r'^\s*!\[.*?\]\(.*?\)\s*$', curr):
                    break
                if re.match(r'^\(\d+\)\s', curr) and re.match(r'^\(\d+\)\s', para_lines[0]):
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

    @staticmethod
    def write_chapter_source(
        output_dir: Path,
        ch_num: int,
        content: str,
        xml_segments: bool = True,
    ) -> Path:
        """Writes chapter source.txt in output_dir/ch<NN>/source.txt."""
        ch_dir = output_dir / f"ch{ch_num:02d}"
        ch_dir.mkdir(parents=True, exist_ok=True)
        source_path = ch_dir / "source.txt"

        if xml_segments:
            content = BaseExtractor.segment_chapter_content(content)

        with open(source_path, "w", encoding="utf-8") as f:
            f.write(content)
        return source_path

    @staticmethod
    def init_checklist(
        output_dir: Path,
        chapters: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Initializes or updates checklist.json in output_dir."""
        checklist_path = output_dir / "checklist.json"
        checklist: List[Dict[str, Any]] = []

        if checklist_path.exists():
            try:
                with open(checklist_path, "r", encoding="utf-8") as f:
                    checklist = json.load(f)
            except Exception:
                checklist = []

        existing_chapters = {entry.get("chapter"): entry for entry in checklist if isinstance(entry, dict)}

        updated_checklist = []
        for ch in chapters:
            ch_num = ch.get("ch_num", 1)
            ch_id = f"ch{ch_num:02d}"
            title = ch.get("title", f"Chapter {ch_num}")

            if ch_id in existing_chapters:
                updated_checklist.append(existing_chapters[ch_id])
            else:
                updated_checklist.append({
                    "chapter": ch_id,
                    "chapter_num": ch_num,
                    "title": title,
                    "status": "pending",
                    "round": 0,
                    "score": None,
                    "draft_exists": False,
                })

        with open(checklist_path, "w", encoding="utf-8") as f:
            json.dump(updated_checklist, f, indent=2, ensure_ascii=False)

        return updated_checklist
