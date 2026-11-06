#!/usr/bin/env python3
"""
scripts/summarize_book.py — Book Translation Summary & Metrics Engine.
Generates comprehensive post-translation analytical summary reports for books:
editorial profile, execution duration, review rounds, content volume, and compiled PDF deliverables.
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.helper import parse_segments
from scripts.translate_chapter import ensure_xml_segments
from scripts.utils.paths import get_runs_dir, resolve_run_dir


def format_bytes(bytes_count: int) -> str:
    """Formats bytes into human-readable string (B, KB, MB)."""
    if bytes_count < 1024:
        return f"{bytes_count} B"
    elif bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f} KB"
    else:
        return f"{bytes_count / (1024 * 1024):.1f} MB"


def format_duration(seconds: float) -> str:
    """Formats seconds into human-readable duration (e.g. 1h 12m 45s)."""
    s = int(round(seconds))
    if s < 0:
        s = 0
    hours, remainder = divmod(s, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    elif minutes > 0:
        return f"{minutes}m {secs:02d}s"
    else:
        return f"{secs}s"


def format_timestamp(ts: float) -> str:
    """Formats a unix timestamp into ISO-like readable string."""
    if ts <= 0:
        return "N/A"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def get_pdf_page_count_and_meta(pdf_path: Optional[Path]) -> Tuple[int, Dict[str, Any]]:
    """Extracts total page count and metadata dictionary from a PDF file."""
    if not pdf_path or not pdf_path.exists():
        return 0, {}
    try:
        import pymupdf
        doc = pymupdf.open(str(pdf_path))
        pages = doc.page_count
        meta = dict(doc.metadata or {})
        doc.close()
        return pages, meta
    except Exception:
        try:
            import fitz
            doc = fitz.open(str(pdf_path))
            pages = doc.page_count
            meta = dict(doc.metadata or {})
            doc.close()
            return pages, meta
        except Exception:
            try:
                import pypdf
                reader = pypdf.PdfReader(str(pdf_path))
                pages = len(reader.pages)
                meta = dict(getattr(reader, "metadata", {}) or {})
                return pages, meta
            except Exception:
                return 0, {}


def find_deliverable(run_dir: Path, ext: str) -> Optional[Path]:
    """Finds compilation deliverable of given extension in the run directory."""
    canonical = run_dir / f"{run_dir.name}_vietnamese.{ext}"
    if canonical.exists():
        return canonical

    v_files = list(run_dir.glob(f"*_vietnamese.{ext}"))
    if v_files:
        return v_files[0]

    all_files = list(run_dir.glob(f"*.{ext}"))
    if all_files:
        return all_files[0]

    return None


def find_pdf_deliverable(run_dir: Path) -> Optional[Path]:
    """Finds the primary Vietnamese compilation PDF in the run directory."""
    return find_deliverable(run_dir, "pdf")


def collect_book_metrics(run_dir: Path) -> Dict[str, Any]:
    """Inspects a book run directory and compiles structured analytical metrics."""
    run_dir = run_dir.resolve()
    book_slug = run_dir.name

    # 1. Load book_record.json
    br_path = run_dir / "book_record.json"
    book_record: Dict[str, Any] = {}
    if br_path.exists():
        try:
            book_record = json.loads(br_path.read_text(encoding="utf-8"))
        except Exception:
            book_record = {}

    brief = book_record.get("brief", {})
    br_data = book_record.get("book_record", {})
    terms = br_data.get("terms", [])
    names = br_data.get("names", [])
    pronouns = br_data.get("pronouns", [])

    # 2. Load checklist.json
    cl_path = run_dir / "checklist.json"
    checklist: List[Dict[str, Any]] = []
    if cl_path.exists():
        try:
            checklist = json.loads(cl_path.read_text(encoding="utf-8"))
            if not isinstance(checklist, list):
                checklist = []
        except Exception:
            checklist = []

    # Map checklist entries by chapter ID
    cl_map: Dict[str, Dict[str, Any]] = {}
    for entry in checklist:
        ch_id = entry.get("chapter", "").strip()
        if ch_id:
            cl_map[ch_id] = entry

    # 3. Deliverables inspection
    pdf_path = find_pdf_deliverable(run_dir)
    pdf_pages, pdf_meta = get_pdf_page_count_and_meta(pdf_path)
    pdf_size_bytes = pdf_path.stat().st_size if pdf_path and pdf_path.exists() else 0

    epub_path = find_deliverable(run_dir, "epub")
    epub_size_bytes = epub_path.stat().st_size if epub_path and epub_path.exists() else 0

    docx_path = find_deliverable(run_dir, "docx")
    docx_size_bytes = docx_path.stat().st_size if docx_path and docx_path.exists() else 0

    # 4. Resolve Title & Author
    title = (
        book_record.get("title")
        or brief.get("title")
        or (pdf_meta.get("title") if pdf_meta.get("title") else None)
        or book_slug.replace("_", " ").title()
    )

    author = book_record.get("author") or brief.get("author")
    if not author:
        for n in names:
            notes = n.get("notes", "").lower()
            if "tác giả" in notes or "author" in notes:
                author = n.get("target") or n.get("source")
                break
    if not author and pronouns:
        pair = pronouns[0].get("pair", [])
        if pair:
            author = pair[0]
    if not author and names:
        author = names[0].get("source")
    if not author and pdf_meta.get("author"):
        author = pdf_meta.get("author")
    if not author:
        author = "Unknown"

    genre = brief.get("genre", "N/A")
    register = brief.get("register", "N/A")
    audience = brief.get("audience", "N/A")

    # 5. Inspect chapter directories
    dir_ch_names = set(
        d.name for d in run_dir.iterdir()
        if d.is_dir() and (d.name.startswith("ch") or d.name in cl_map)
    )
    all_ch_keys = sorted(
        dir_ch_names.union(cl_map.keys()),
        key=lambda x: (int(re.search(r'\d+', x).group()) if re.search(r'\d+', x) else 999, x)
    )

    chapters_detail: List[Dict[str, Any]] = []
    total_segments_counter: Counter = Counter()
    total_en_words = 0
    total_vi_words = 0
    total_toc_headings = 0
    all_file_mtimes: List[float] = []

    if pdf_path and pdf_path.exists():
        all_file_mtimes.append(pdf_path.stat().st_mtime)
    if epub_path and epub_path.exists():
        all_file_mtimes.append(epub_path.stat().st_mtime)
    if docx_path and docx_path.exists():
        all_file_mtimes.append(docx_path.stat().st_mtime)

    for ch_id in all_ch_keys:
        ch_dir = run_dir / ch_id
        cl_entry = cl_map.get(ch_id, {})

        ch_title = cl_entry.get("title") or ch_id
        ch_status = cl_entry.get("status", "pending")
        ch_score = cl_entry.get("score")

        ch_mtimes: List[float] = []
        ch_seg_counter: Counter = Counter()
        ch_en_words = 0
        ch_vi_words = 0
        ch_toc = 0
        round_count = cl_entry.get("round", 1)
        eval_passes_count = 0
        eval_verdict = "PASS"
        eval_dimensions: Dict[str, float] = {}

        if ch_dir.exists() and ch_dir.is_dir():
            # Mtimes of files in chapter dir
            for f in ch_dir.glob("*.*"):
                try:
                    mt = f.stat().st_mtime
                    ch_mtimes.append(mt)
                    all_file_mtimes.append(mt)
                except OSError:
                    pass

            # Parse source segments
            src_file = ch_dir / "source.txt"
            if src_file.exists():
                try:
                    src_text = src_file.read_text(encoding="utf-8")
                    xml_text = ensure_xml_segments(src_text)
                    segs = parse_segments(xml_text)
                    for s in segs:
                        stype = s.get("type", "paragraph")
                        ch_seg_counter[stype] += 1
                        orig = s.get("original", "")
                        ch_en_words += len(orig.split())
                except Exception:
                    pass

            # Target words & TOC headings from final.txt or round translate
            final_file = ch_dir / "final.txt"
            if final_file.exists():
                try:
                    final_text = final_file.read_text(encoding="utf-8")
                    ch_vi_words = len(final_text.split())
                    for line in final_text.splitlines():
                        s_line = line.strip()
                        if s_line.startswith("# ") or s_line.startswith("## "):
                            ch_toc += 1
                except Exception:
                    pass
            else:
                r1_file = ch_dir / "round1_translate.txt"
                if r1_file.exists():
                    try:
                        r1_text = r1_file.read_text(encoding="utf-8")
                        r1_segs = parse_segments(r1_text)
                        for s in r1_segs:
                            tr = s.get("translate", "")
                            ch_vi_words += len(tr.split())
                    except Exception:
                        pass

            # Review rounds and evaluation files
            tr_files = list(ch_dir.glob("round*_translate.txt"))
            if tr_files:
                round_count = max(round_count, len(tr_files))

            ev_files = list(ch_dir.glob("round*_evaluate*.txt"))
            eval_passes_count = len(ev_files)

            # Check aggregate evaluation (IMP-P2-03: read latest round evaluate file)
            eval_files = []
            for f in ch_dir.glob("round*_evaluate.txt"):
                m = re.match(r"^round(\d+)_evaluate\.txt$", f.name)
                if m:
                    eval_files.append((int(m.group(1)), f))
            fallback_unverified = False
            if eval_files:
                eval_files.sort(key=lambda x: x[0])
                latest_eval = eval_files[-1][1]
                try:
                    ev_data = json.loads(latest_eval.read_text(encoding="utf-8"))
                    eval_verdict = ev_data.get("verdict", "PASS")
                    fallback_unverified = bool(ev_data.get("fallback_unverified", False))
                    if "weighted_total" in ev_data and ch_score is None:
                        ch_score = ev_data["weighted_total"]
                    if "dimensions" in ev_data:
                        eval_dimensions = ev_data["dimensions"]
                except Exception:
                    pass

        # Chapter timing
        if ch_mtimes:
            ch_start_ts = min(ch_mtimes)
            ch_end_ts = max(ch_mtimes)
            ch_dur_sec = max(0.0, ch_end_ts - ch_start_ts)
        else:
            ch_start_ts = 0.0
            ch_end_ts = 0.0
            ch_dur_sec = 0.0

        ch_ratio = ch_vi_words / ch_en_words if ch_en_words > 0 else 1.0

        chapter_info = {
            "chapter_id": ch_id,
            "title": ch_title,
            "status": ch_status,
            "round": round_count,
            "eval_passes": eval_passes_count,
            "score": ch_score,
            "verdict": eval_verdict,
            "fallback_unverified": fallback_unverified,
            "dimensions": eval_dimensions,
            "segments_count": sum(ch_seg_counter.values()),
            "segments_by_type": dict(ch_seg_counter),
            "en_words": ch_en_words,
            "vi_words": ch_vi_words,
            "expansion_ratio": round(ch_ratio, 2),
            "toc_headings": ch_toc,
            "start_time": format_timestamp(ch_start_ts),
            "completion_time": format_timestamp(ch_end_ts),
            "duration_seconds": round(ch_dur_sec, 1),
            "duration_formatted": format_duration(ch_dur_sec),
        }
        chapters_detail.append(chapter_info)

        total_segments_counter.update(ch_seg_counter)
        total_en_words += ch_en_words
        total_vi_words += ch_vi_words
        total_toc_headings += ch_toc

    # 6. Overall timings & throughput
    if all_file_mtimes:
        start_ts = min(all_file_mtimes)
        end_ts = max(all_file_mtimes)
        total_duration_sec = max(0.0, end_ts - start_ts)
    else:
        start_ts = 0.0
        end_ts = 0.0
        total_duration_sec = 0.0

    duration_minutes = total_duration_sec / 60.0 if total_duration_sec > 0 else 0.0
    throughput_wpm = round(total_vi_words / duration_minutes, 1) if duration_minutes > 0 else 0.0
    total_segs_num = sum(total_segments_counter.values())
    throughput_spm = round(total_segs_num / duration_minutes, 1) if duration_minutes > 0 else 0.0

    # 7. QA ledger
    accepted_count = sum(1 for c in chapters_detail if c["status"] == "accepted")
    pending_count = sum(1 for c in chapters_detail if c["status"] == "pending")
    in_progress_count = sum(1 for c in chapters_detail if c["status"] == "in_progress")
    unverified_fallback_chapters = sum(1 for c in chapters_detail if c.get("fallback_unverified"))

    valid_scores = [c["score"] for c in chapters_detail if c["score"] is not None]
    avg_score = round(sum(valid_scores) / len(valid_scores), 2) if valid_scores else 5.0

    overall_ratio = round(total_vi_words / total_en_words, 2) if total_en_words > 0 else 1.0

    # 8. Provenance & Execution Audit (REQ-014)
    audit_files = sorted(run_dir.glob("ch*/audit_trail.jsonl"))
    audit_groups: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for af in audit_files:
        try:
            with open(af, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    who = rec.get("who", {})
                    agent = str(who.get("agent") or "unknown")
                    model = str(who.get("model") or "unknown")
                    engine = str(who.get("engine") or "unknown")
                    key = (agent, model, engine)

                    if key not in audit_groups:
                        audit_groups[key] = {
                            "agent": agent,
                            "model": model,
                            "engine": engine,
                            "trunks": 0,
                            "segments": 0,
                            "volume_chars": 0,
                            "duration_ms": 0,
                            "started_ats": [],
                            "completed_ats": [],
                        }

                    g = audit_groups[key]
                    g["trunks"] += 1
                    what = rec.get("what", {})
                    g["segments"] += what.get("segment_count", len(rec.get("segments", [])))
                    g["volume_chars"] += what.get("target_chars", 0)
                    when = rec.get("when", {})
                    g["duration_ms"] += when.get("duration_ms", 0)
                    if when.get("started_at"):
                        g["started_ats"].append(when["started_at"])
                    if when.get("completed_at"):
                        g["completed_ats"].append(when["completed_at"])
        except Exception:
            pass

    audit_summary: List[Dict[str, Any]] = []
    for (agent, model, engine), g in sorted(audit_groups.items()):
        min_start = min(g["started_ats"]) if g["started_ats"] else "N/A"
        max_end = max(g["completed_ats"]) if g["completed_ats"] else "N/A"
        time_bounds = f"{min_start} to {max_end}" if min_start != "N/A" else "N/A"
        audit_summary.append({
            "agent": agent,
            "model": model,
            "engine": engine,
            "trunks": g["trunks"],
            "segments": g["segments"],
            "volume_chars": g["volume_chars"],
            "duration_ms": g["duration_ms"],
            "duration_formatted": format_duration(g["duration_ms"] / 1000.0),
            "time_bounds": time_bounds,
        })

    return {
        "run_dir": str(run_dir),
        "book_slug": book_slug,
        "metadata": {
            "title": title,
            "author": author,
            "genre": genre,
            "register": register,
            "audience": audience,
            "terms_count": len(terms),
            "names_count": len(names),
        },
        "timing": {
            "start_time": format_timestamp(start_ts),
            "completion_time": format_timestamp(end_ts),
            "total_duration_seconds": round(total_duration_sec, 1),
            "total_duration_formatted": format_duration(total_duration_sec),
            "throughput_wpm": throughput_wpm,
            "throughput_spm": throughput_spm,
        },
        "qa": {
            "total_chapters": len(chapters_detail),
            "accepted_count": accepted_count,
            "pending_count": pending_count,
            "in_progress_count": in_progress_count,
            "average_score": avg_score,
            "unverified_fallback_chapters": unverified_fallback_chapters,
        },
        "volume": {
            "total_segments": total_segs_num,
            "segments_by_type": {
                "paragraph": total_segments_counter.get("paragraph", 0),
                "heading": total_segments_counter.get("heading", 0),
                "figure": total_segments_counter.get("figure", 0),
                "code": total_segments_counter.get("code", 0),
            },
            "source_words_en": total_en_words,
            "target_words_vi": total_vi_words,
            "expansion_ratio": overall_ratio,
            "chapters": chapters_detail,
        },
        "audit_provenance": audit_summary,
        "deliverables": {
            "pdf_path": str(pdf_path) if pdf_path else None,
            "pdf_filename": pdf_path.name if pdf_path else None,
            "pdf_exists": bool(pdf_path and pdf_path.exists()),
            "pdf_size_bytes": pdf_size_bytes,
            "pdf_size_formatted": format_bytes(pdf_size_bytes),
            "page_count": pdf_pages,
            "epub_path": str(epub_path) if epub_path else None,
            "epub_filename": epub_path.name if epub_path else None,
            "epub_exists": bool(epub_path and epub_path.exists()),
            "epub_size_bytes": epub_size_bytes,
            "epub_size_formatted": format_bytes(epub_size_bytes),
            "docx_path": str(docx_path) if docx_path else None,
            "docx_filename": docx_path.name if docx_path else None,
            "docx_exists": bool(docx_path and docx_path.exists()),
            "docx_size_bytes": docx_size_bytes,
            "docx_size_formatted": format_bytes(docx_size_bytes),
            "toc_headings_count": total_toc_headings,
            "index_terms_count": len(terms),
        },
    }


def generate_markdown_report(metrics: Dict[str, Any]) -> str:
    """Renders structured metrics dictionary into a GitHub-flavored Markdown report."""
    meta = metrics["metadata"]
    timing = metrics["timing"]
    qa = metrics["qa"]
    vol = metrics["volume"]
    deliv = metrics["deliverables"]
    chapters = vol["chapters"]

    lines: List[str] = [
        f"# Book Translation Summary Report: {meta['title']}",
        "",
        f"**Book Slug:** `{metrics['book_slug']}`  ",
        f"**Run Directory:** `{metrics['run_dir']}`  ",
        f"**Report Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"**Editorial Status:** {'COMPLETED' if qa['accepted_count'] == qa['total_chapters'] and qa['total_chapters'] > 0 else 'IN PROGRESS'}",
        "",
        "---",
        "",
        "## 1. Book Metadata & Editorial Profile",
        "",
        "| Property | Value |",
        "| :--- | :--- |",
        f"| **Title** | {meta['title']} |",
        f"| **Author** | {meta['author']} |",
        f"| **Genre** | {meta['genre']} |",
        f"| **Target Register** | {meta['register']} |",
        f"| **Audience** | {meta['audience']} |",
        f"| **Canonical Terms Count** | {meta['terms_count']} registered terms |",
        f"| **Named Entities Count** | {meta['names_count']} registered entities |",
        "",
        "---",
        "",
        "## 2. Execution Timings & Throughput",
        "",
        "| Timing Metric | Measurement |",
        "| :--- | :--- |",
        f"| **Pipeline Start Time** | {timing['start_time']} |",
        f"| **Completion Time** | {timing['completion_time']} |",
        f"| **Total Elapsed Duration** | {timing['total_duration_formatted']} |",
        f"| **Translation Throughput** | {timing['throughput_wpm']} words/min ({timing['throughput_spm']} segments/min) |",
        "",
        "### Per-Chapter Timing Breakdown",
        "",
        "| Chapter | Start Time | Completion Time | Elapsed Duration |",
        "| :--- | :--- | :--- | :--- |",
    ]

    for c in chapters:
        lines.append(f"| {c['chapter_id']} | {c['start_time']} | {c['completion_time']} | {c['duration_formatted']} |")

    lines.extend([
        "",
        "---",
        "",
        "## 3. Quality Assurance & Review Rounds",
        "",
        "| Quality Indicator | Score / Count |",
        "| :--- | :--- |",
        f"| **Total Chapters** | {qa['total_chapters']} chapters |",
        f"| **Accepted Chapters** | {qa['accepted_count']} / {qa['total_chapters']} |",
        f"| **Pending / In Progress** | {qa['pending_count'] + qa['in_progress_count']} chapters |",
        f"| **Average Evaluation Score** | {qa['average_score']:.2f} / 5.00 |",
        f"| **Unverified Fallback Evaluations** | {qa.get('unverified_fallback_chapters', 0)} chapters |",
        "",
        "### Chapter Evaluation Ledger",
        "",
        "| Chapter | Title | Status | Rounds | Eval Passes | Score | Verdict |",
        "| :--- | :--- | :--- | :---: | :---: | :---: | :---: |",
    ])

    for c in chapters:
        score_str = f"{c['score']:.2f}" if c['score'] is not None else "N/A"
        verdict_str = f"{c['verdict']} [UNVERIFIED-FALLBACK]" if c.get("fallback_unverified") else str(c['verdict'])
        lines.append(
            f"| {c['chapter_id']} | {c['title']} | `{c['status']}` | {c['round']} | {c['eval_passes']} | {score_str} | {verdict_str} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## 4. Content & Structural Volume",
        "",
        "| Volume Dimension | Measure |",
        "| :--- | :--- |",
        f"| **Total XML Segments** | {vol['total_segments']:,} |",
        f"| **Paragraph Segments (`paragraph`)** | {vol['segments_by_type']['paragraph']:,} |",
        f"| **Heading Segments (`heading`)** | {vol['segments_by_type']['heading']:,} |",
        f"| **Figure Anchors (`figure`)** | {vol['segments_by_type']['figure']:,} |",
        f"| **Code Blocks (`code`)** | {vol['segments_by_type']['code']:,} |",
        f"| **English Source Words** | {vol['source_words_en']:,} words |",
        f"| **Vietnamese Target Words** | {vol['target_words_vi']:,} words |",
        f"| **Language Expansion Ratio** | {vol['expansion_ratio']:.2f}x |",
        "",
        "### Per-Chapter Content Volume",
        "",
        "| Chapter | Segments | Headings | Paragraphs | Figures | Code | EN Words | VI Words | Ratio |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ])

    for c in chapters:
        st = c["segments_by_type"]
        lines.append(
            f"| {c['chapter_id']} | {c['segments_count']} | {st.get('heading', 0)} | {st.get('paragraph', 0)} | "
            f"{st.get('figure', 0)} | {st.get('code', 0)} | {c['en_words']:,} | {c['vi_words']:,} | {c['expansion_ratio']:.2f}x |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## 5. Publication Deliverables",
        "",
        "| Deliverable Specification | Value |",
        "| :--- | :--- |",
        f"| **Compiled EPUB Deliverable** | `{deliv.get('epub_filename') or 'N/A'}` |",
        f"| **EPUB File Size** | {deliv.get('epub_size_formatted', '0 B')} |",
        f"| **Compiled PDF Deliverable** | `{deliv['pdf_filename'] or 'N/A'}` |",
        f"| **PDF File Size** | {deliv['pdf_size_formatted']} |",
        f"| **Compiled Word (DOCX)** | `{deliv.get('docx_filename') or 'N/A'}` |",
        f"| **DOCX File Size** | {deliv.get('docx_size_formatted', '0 B')} |",
        f"| **Total Rendered Page Count** | {deliv['page_count']} pages |",
        f"| **Table of Contents (Mục Lục)** | {deliv['toc_headings_count']} indexed heading entries |",
        f"| **Alphabetical Index (Bảng Tra Cứu)** | {deliv['index_terms_count']} indexed terminology entries |",
    ])

    if metrics.get("audit_provenance"):
        lines.extend([
            "",
            "---",
            "",
            "## Provenance & Execution Audit (Who, What, When)",
            "",
            "| Agent | Model | Engine | Trunks | Segments | Volume (Chars) | Duration | Time Bounds (UTC) |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ])
        for row in metrics["audit_provenance"]:
            lines.append(
                f"| `{row['agent']}` | `{row['model']}` | `{row['engine']}` | "
                f"{row['trunks']} | {row['segments']} | {row['volume_chars']:,} | "
                f"{row['duration_formatted']} | {row['time_bounds']} |"
            )

    lines.extend([
        "",
        "---",
        "",
        "*Report automatically generated by `scripts/summarize_book.py` (agent-translator pipeline).*",
        "",
    ])

    return "\n".join(lines)


def print_console_summary(metrics: Dict[str, Any], output_path: Optional[Path] = None) -> None:
    """Prints a clean, informative console summary table to standard output."""
    meta = metrics["metadata"]
    timing = metrics["timing"]
    qa = metrics["qa"]
    vol = metrics["volume"]
    deliv = metrics["deliverables"]

    border = "=" * 80
    div = "-" * 80

    print(border)
    print(f" BOOK TRANSLATION SUMMARY: {meta['title']}")
    print(border)
    print(f" Author:     {meta['author']}")
    print(f" Genre:      {meta['genre']}")
    print(f" Run Dir:    {metrics['run_dir']}")
    print(div)
    print(" Execution Timing:")
    print(f"   • Total Duration:  {timing['total_duration_formatted']} ({timing['start_time']} → {timing['completion_time']})")
    print(f"   • Throughput:      {timing['throughput_wpm']} words/min ({timing['throughput_spm']} segments/min)")
    print(div)
    print(" Quality Assurance:")
    print(f"   • Chapters:        {qa['accepted_count']}/{qa['total_chapters']} accepted")
    print(f"   • Average Score:   {qa['average_score']:.2f} / 5.00")
    print(div)
    print(" Content Volume:")
    print(f"   • XML Segments:    {vol['total_segments']:,} (Para: {vol['segments_by_type']['paragraph']}, Head: {vol['segments_by_type']['heading']}, Fig: {vol['segments_by_type']['figure']}, Code: {vol['segments_by_type']['code']})")
    print(f"   • Word Counts:     {vol['source_words_en']:,} EN → {vol['target_words_vi']:,} VI ({vol['expansion_ratio']:.2f}x expansion)")
    print(div)
    print(" Publication Deliverables:")
    epub_info = f"{deliv.get('epub_filename')} ({deliv.get('epub_size_formatted')})" if deliv.get('epub_exists') else "Not compiled"
    pdf_info = f"{deliv['pdf_filename']} ({deliv['pdf_size_formatted']}, {deliv['page_count']} pages)" if deliv['pdf_exists'] else "Not compiled yet"
    docx_info = f"{deliv.get('docx_filename')} ({deliv.get('docx_size_formatted')})" if deliv.get('docx_exists') else "Not compiled"
    print(f"   • Vietnamese EPUB: {epub_info}")
    print(f"   • Vietnamese PDF:  {pdf_info}")
    print(f"   • Vietnamese DOCX: {docx_info}")
    print(f"   • TOC Headings:    {deliv['toc_headings_count']} entries")
    print(f"   • Index Terms:     {deliv['index_terms_count']} terms")
    print(border)
    if output_path:
        print(f" Report written to: {output_path}")
        print(border)


def find_all_runs(runs_root: Path) -> List[Path]:
    """Discovers all valid book run directories under runs/."""
    candidates = []
    if not runs_root.exists() or not runs_root.is_dir():
        return []
    for d in sorted(runs_root.iterdir()):
        if d.is_dir():
            if (d / "book_record.json").exists() or (d / "checklist.json").exists() or list(d.glob("ch*")):
                candidates.append(d)
    return candidates


def parse_args(args: Optional[List[str]] = None) -> argparse.Namespace:
    """Parses CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Book Translation Summary & Metrics Engine for agent-translator."
    )
    parser.add_argument("book_slug", nargs="?", default=None, help="Book slug or path under runs/")
    parser.add_argument("--slug", dest="slug_opt", default=None, help="Book slug under runs/")
    parser.add_argument("--run-dir", default=None, help="Root directory of a specific book run")
    parser.add_argument("--all", action="store_true", help="Summarize all book runs in runs/")
    parser.add_argument("--output", default=None, help="Custom output path for markdown report")
    parser.add_argument("--json", action="store_true", help="Emit JSON output to stdout")
    return parser.parse_args(args)


def main(cli_args: Optional[List[str]] = None) -> int:
    args = parse_args(cli_args)
    runs_dir = get_runs_dir()

    if args.all:
        all_runs = find_all_runs(runs_dir)
        if not all_runs:
            sys.stderr.write(f"No book runs found in {runs_dir}\n")
            return 1

        all_metrics = []
        for r in all_runs:
            m = collect_book_metrics(r)
            all_metrics.append(m)
            out_file = r / "summary.md"
            out_file.write_text(generate_markdown_report(m), encoding="utf-8")
            if not args.json:
                print_console_summary(m, out_file)
                print()

        if args.json:
            print(json.dumps(all_metrics, ensure_ascii=False, indent=2))
        return 0

    # Single run mode
    target = args.run_dir or args.slug_opt or args.book_slug
    if target:
        run_path = resolve_run_dir(target)
    else:
        # Default to the most recently modified run
        all_runs = find_all_runs(runs_dir)
        if not all_runs:
            sys.stderr.write(f"No book runs found in {runs_dir}\n")
            return 1
        run_path = max(all_runs, key=lambda d: d.stat().st_mtime)

    if not run_path.exists() or not run_path.is_dir():
        sys.stderr.write(f"Error: run directory does not exist: {run_path}\n")
        return 1

    metrics = collect_book_metrics(run_path)

    if args.json:
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
        return 0

    out_path = Path(args.output) if args.output else run_path / "summary.md"
    md_content = generate_markdown_report(metrics)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md_content, encoding="utf-8")

    print_console_summary(metrics, out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
