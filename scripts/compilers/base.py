#!/usr/bin/env python3
"""
scripts/compilers/base.py — Abstract Base Class and shared utilities for book compilers.
"""

from abc import ABC, abstractmethod
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


class BaseCompiler(ABC):
    """Abstract base class for document/book compilers."""

    @abstractmethod
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
        """
        Compiles the translated book in run_dir into the output deliverable.
        Returns the Path to the compiled file.
        """
        pass

    @staticmethod
    def slugify(text: str) -> str:
        """Converts a string into a clean lowercase URL/anchor slug."""
        if not text or not str(text).strip():
            return "item"
        s = str(text).strip()
        slug = re.sub(r'[^a-zA-Z0-9]+', '-', s.lower()).strip('-')
        if slug:
            return slug
        hex_parts = [f"u{ord(c):04x}" for c in s if not c.isspace()]
        return "-".join(hex_parts) if hex_parts else "item"

    @staticmethod
    def parse_chapters_filter(chapters_str: Optional[str]) -> Optional[Set[int]]:
        """Parses chapter filter string into a set of integers."""
        if not chapters_str or str(chapters_str).strip().lower() in ("all", "none", ""):
            return None

        def _extract_int(tok: str) -> Optional[int]:
            m = re.search(r"\d+", str(tok).strip())
            return int(m.group(0)) if m else None

        result: Set[int] = set()
        parts = str(chapters_str).split(",")
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if ".." in part:
                sub = part.split("..", 1)
                start_ch = _extract_int(sub[0])
                end_ch = _extract_int(sub[1])
                if start_ch is not None and end_ch is not None:
                    result.update(range(min(start_ch, end_ch), max(start_ch, end_ch) + 1))
            elif "-" in part:
                sub = part.split("-", 1)
                start_ch = _extract_int(sub[0])
                end_ch = _extract_int(sub[1])
                if start_ch is not None and end_ch is not None:
                    result.update(range(min(start_ch, end_ch), max(start_ch, end_ch) + 1))
            else:
                ch_num = _extract_int(part)
                if ch_num is not None:
                    result.add(ch_num)
        return result if result else None

    @staticmethod
    def strip_xml_segments(text: str) -> str:
        """Strips XML segment tags (<segment ...><translate>...</segment> or <s id="...">...</s>) to extract clean markdown."""
        if not text:
            return ""
        if "<segment" not in text:
            return re.sub(
                r'</?(?:s|segment|original|translate|translated|target|changelog|context_before|hanviet)\b[^>]*>',
                '',
                text,
            ).strip()

        # If segmented, extract <translate> (first preference), then <translated>, then <original>
        lines = []
        seg_pattern = re.compile(r'<segment\b[^>]*>(.*?)</segment>', re.DOTALL)
        segments = seg_pattern.findall(text)
        if not segments:
            cleaned = re.sub(
                r'</?(?:s|segment|original|translate|translated|target|changelog|context_before|hanviet)\b[^>]*>',
                '',
                text,
            )
            return cleaned.strip()

        for seg_body in segments:
            tr_match = re.search(r'<translate>(.*?)</translate>', seg_body, re.DOTALL)
            if tr_match:
                cleaned = re.sub(
                    r'</?(?:s|segment|original|translate|translated|target|changelog|context_before|hanviet)\b[^>]*>',
                    '',
                    tr_match.group(1).strip(),
                ).strip()
                lines.append(cleaned)
                continue
            tr_leg_match = re.search(r'<translated>(.*?)</translated>', seg_body, re.DOTALL)
            if tr_leg_match:
                cleaned = re.sub(
                    r'</?(?:s|segment|original|translate|translated|target|changelog|context_before|hanviet)\b[^>]*>',
                    '',
                    tr_leg_match.group(1).strip(),
                ).strip()
                lines.append(cleaned)
                continue
            orig_match = re.search(r'<original>(.*?)</original>', seg_body, re.DOTALL)
            if orig_match:
                cleaned = re.sub(
                    r'</?(?:s|segment|original|translate|translated|target|changelog|context_before|hanviet)\b[^>]*>',
                    '',
                    orig_match.group(1).strip(),
                ).strip()
                lines.append(cleaned)
                continue
            cleaned_body = re.sub(
                r'</?(?:s|segment|original|translate|translated|target|changelog|context_before|hanviet)\b[^>]*>',
                '',
                seg_body,
            ).strip()
            if cleaned_body:
                lines.append(cleaned_body)

        return "\n\n".join(lines).strip()

    def load_book_record(self, run_dir: Path) -> Dict[str, Any]:
        """Loads book_record.json from run_dir."""
        br_path = run_dir / "book_record.json"
        if br_path.is_file():
            try:
                with open(br_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def extract_terms_from_book_record(self, book_record_or_path: Any) -> List[Dict[str, str]]:
        """
        Extracts terms and names from book_record.json or parsed dict.
        Returns sorted list of dicts: [{'source': ..., 'target': ...}, ...]
        """
        data = None
        if isinstance(book_record_or_path, (str, Path)):
            p = Path(book_record_or_path)
            if p.is_dir():
                cand = p / "book_record.json"
                if cand.is_file():
                    p = cand
            if p.is_file():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    return []
            else:
                return []
        elif isinstance(book_record_or_path, dict):
            data = book_record_or_path

        if not data:
            return []

        collected: Dict[str, tuple] = {}

        def process_container(container):
            if isinstance(container, list):
                for it in container:
                    if isinstance(it, dict) and "source" in it:
                        src = str(it["source"]).strip()
                        tgt = str(it.get("target", src)).strip()
                        if src and src.lower() not in collected:
                            collected[src.lower()] = (src, tgt)
            elif isinstance(container, dict):
                for k, v in container.items():
                    src = str(k).strip()
                    if isinstance(v, dict):
                        tgt = str(v.get("target", src)).strip()
                    else:
                        tgt = str(v).strip()
                    if src and src.lower() not in collected:
                        collected[src.lower()] = (src, tgt)

        keys = ["terms", "term_table", "names", "name_table"]
        for k in keys:
            if k in data:
                process_container(data[k])

        if "book_record" in data and isinstance(data["book_record"], dict):
            for k in keys:
                if k in data["book_record"]:
                    process_container(data["book_record"][k])

        result = [{"source": src, "target": tgt} for src, tgt in collected.values()]
        result.sort(key=lambda x: x["source"].lower())
        return result

    def discover_chapters(
        self,
        run_dir: Path,
        chapters_filter: Optional[Set[int]] = None,
        allow_incomplete: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Discovers translated chapter directories in run_dir.
        When allow_incomplete is False and chapters_filter is None:
          - Requires checklist status in ("accepted", "released-with-warning", "completed")
          - Requires final.txt to exist (never falls back to draft or source)
        Otherwise collects content in order of priority:
          1. final.txt
          2. highest round_refine.txt
          3. highest round_translate.txt
          4. source.txt (stripped of XML)
        """
        run_dir = Path(run_dir).resolve()
        checklist_titles: Dict[int, str] = {}
        checklist_statuses: Dict[int, str] = {}
        checklist_path = run_dir / "checklist.json"
        if checklist_path.is_file():
            try:
                with open(checklist_path, "r", encoding="utf-8") as f:
                    cl = json.load(f)
                    items = cl if isinstance(cl, list) else cl.get("chapters", [])
                    for item in items:
                        if isinstance(item, dict):
                            num = item.get("chapter_num") or item.get("chapter") or item.get("ch_num")
                            title = item.get("title")
                            status = str(item.get("status", "")).strip().lower()
                            if num is not None:
                                num_match = re.search(r'\d+', str(num))
                                num_int = int(num_match.group(0)) if num_match else int(num)
                                if title:
                                    checklist_titles[num_int] = title
                                if status:
                                    checklist_statuses[num_int] = status
            except Exception:
                pass

        ch_dirs = sorted([
            d for d in run_dir.iterdir()
            if d.is_dir() and d.name.startswith("ch")
        ], key=lambda d: d.name)

        chapters: List[Dict[str, Any]] = []

        for ch_dir in ch_dirs:
            ch_name = ch_dir.name
            num_match = re.search(r'\d+', ch_name)
            ch_num = int(num_match.group(0)) if num_match else len(chapters) + 1

            if chapters_filter is not None and ch_num not in chapters_filter:
                continue

            if not allow_incomplete:
                if checklist_path.is_file():
                    if ch_num not in checklist_statuses or checklist_statuses[ch_num] not in (
                        "accepted",
                        "released-with-warning",
                        "completed",
                        "done",
                    ):
                        continue
                if not (ch_dir / "final.txt").is_file():
                    continue

            content = ""
            # Priority 1: final.txt
            final_file = ch_dir / "final.txt"
            if final_file.is_file():
                content = self.strip_xml_segments(final_file.read_text(encoding="utf-8"))
            elif allow_incomplete:
                # Priority 2: highest refine
                refine_files = sorted(ch_dir.glob("round*_refine.txt"))
                if refine_files:
                    content = self.strip_xml_segments(refine_files[-1].read_text(encoding="utf-8"))
                else:
                    # Priority 3: highest translate
                    translate_files = sorted(ch_dir.glob("round*_translate.txt"))
                    if translate_files:
                        content = self.strip_xml_segments(translate_files[-1].read_text(encoding="utf-8"))
                    else:
                        # Priority 4: source.txt
                        source_file = ch_dir / "source.txt"
                        if source_file.is_file():
                            content = self.strip_xml_segments(source_file.read_text(encoding="utf-8"))

            if not content.strip():
                continue

            # Extract title (REQ-001: prioritize translated # H1 heading in content over checklist title)
            title = None
            for line in content.splitlines():
                stripped_line = line.strip()
                if stripped_line.startswith("# "):
                    raw_h1 = stripped_line[2:].strip()
                    # Strip markdown link wrapper if present: [Title](href) -> Title
                    raw_h1 = re.sub(r'^\[([^\]]+)\]\([^)]*\)$', r'\1', raw_h1).strip()
                    if raw_h1:
                        title = raw_h1
                        break

            if not title:
                title = checklist_titles.get(ch_num)
            if not title:
                title = f"Chương {ch_num}"

            # Extract Level 2 headings (excluding ### and Chú thích)
            subheadings = []
            sub_idx = 1
            for line in content.splitlines():
                m = re.match(r'^##\s+(.+)$', line.strip())
                if m:
                    sub_title = m.group(1).strip()
                    if not sub_title.startswith("#") and not sub_title.startswith("Chú thích"):
                        subheadings.append({
                            "title": sub_title,
                            "id": f"sub-{sub_idx}",
                        })
                        sub_idx += 1

            chapters.append({
                "ch_num": ch_num,
                "chapter_num": ch_num,
                "num": ch_num,
                "ch_id": f"ch{ch_num:02d}",
                "title": title,
                "content": content,
                "subheadings": subheadings,
                "dir": ch_dir,
            })

        chapters.sort(key=lambda x: x["ch_num"])
        return chapters

    @classmethod
    def inject_term_anchors(
        cls,
        text: str,
        terms: List[Dict[str, str]],
        anchor_tracker: Dict[str, List[str]],
        ch_term_counts: Dict[str, int],
        max_per_chapter: int = 2,
    ) -> str:
        """Safely injects <span id="idx-{term_slug}-{i}"></span> anchors into text without corrupting HTML tags."""
        tags: List[str] = []

        def stash_tag(m):
            tags.append(m.group(0))
            return f"__TAG_{len(tags)-1}__"

        protected_text = re.sub(r'<[^>]+>', stash_tag, text)
        sorted_terms = sorted(terms, key=lambda x: len(x.get("source", "")), reverse=True)

        for item in sorted_terms:
            source = item.get("source", "").strip()
            target = item.get("target", "").strip()
            if not source:
                continue
            if ch_term_counts.get(source, 0) >= max_per_chapter:
                continue

            slug = cls.slugify(source)
            patterns = []
            if len(source) >= 2:
                patterns.append(re.compile(r'(?<![\w])' + re.escape(source) + r'(?![\w])', re.IGNORECASE))
            if target and target.lower() != source.lower() and len(target) >= 2:
                patterns.append(re.compile(r'(?<![\w])' + re.escape(target) + r'(?![\w])', re.IGNORECASE))

            matched = False
            for pat in patterns:
                if pat.search(protected_text):
                    def replace_first(m):
                        nonlocal matched
                        if matched:
                            return m.group(0)
                        matched = True
                        idx = len(anchor_tracker.get(source, [])) + 1
                        anchor_id = f"idx-{slug}-{idx}"
                        anchor_tracker.setdefault(source, []).append(anchor_id)
                        ch_term_counts[source] = ch_term_counts.get(source, 0) + 1
                        new_tag = f'<span id="{anchor_id}"></span>'
                        tags.append(new_tag)
                        tag_placeholder = f"__TAG_{len(tags)-1}__"
                        return f"{tag_placeholder}{m.group(0)}"

                    protected_text = pat.sub(replace_first, protected_text, count=1)
                    if matched:
                        break

        for i, tag in enumerate(tags):
            protected_text = protected_text.replace(f"__TAG_{i}__", tag)

        return protected_text
