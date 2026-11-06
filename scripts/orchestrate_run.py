#!/usr/bin/env python3
"""
scripts/orchestrate_run.py — Unattended Multi-Agent Master Orchestrator CLI.
Executes the full book translation lifecycle end-to-end:
1. Ingestion / Extraction (extract_pdf.py)
2. Book Analysis & Canonical Terms (book_record.json)
3. Chapter Translation & Evaluation Loop (batch_size, concurrency, window_size)
4. Compilation (compile_pdf.py / compile_book)
5. Summary Report (summarize_book.py)
Supports resumability from checklist.json, sliding window evaluation, and failure isolation.
"""

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.translate_chapter import translate_chapter, normalize_chapter_id, TranslationCacheManager
from scripts.evaluate_workbuddy import run_workbuddy_evaluation, evaluate_workbuddy
from scripts.helper import (
    bind_staged_proposals,
    parse_segments,
    validate_segment_parity,
    update_book_record_from_eval,
    aggregate_multi_pass,
    check_critical_quorum,
    check_redraft as helper_check_redraft,
    sync_book_record_views,
)
from scripts.utils.language import derive_book_slug
from scripts.utils.paths import get_runs_dir, resolve_run_dir
from scripts.utils.schemas import TECHNICAL_SEED_GLOSSARY_V1
from scripts.utils.terminology import (
    atomic_write_json,
    atomic_write_text,
    interprocess_lock,
    read_json_state,
    snapshot_backup,
    StateFileCorruptError,
)

TECHNICAL_GENRE_TOKENS = (
    "computer science", "software", "distributed systems", "programming",
    "engineering", "technical", "database", "machine learning", "ai",
    "system design", "networking", "security",
)

FICTION_GENRE_TOKENS = (
    "fiction", "novel", "literary", "literature", "sci-fi", "science fiction",
    "fantasy", "wuxia", "xianxia", "historical fiction", "romance", "mystery",
    "thriller", "narrative", "tiểu thuyết", "khoa học viễn tưởng", "văn học",
    "truyện", "kiếm hiệp", "tiên hiệp",
)
NON_FICTION_EXCLUSION_TOKENS = (
    "non-fiction", "nonfiction", "phi hư cấu", "textbook", "technical manual",
    "computer science", "software", "distributed systems", "database", "programming",
)


def ensure_technical_seed_glossary(
    book_record: Dict[str, Any],
    sample_text: str = "",
) -> bool:
    """
    REQ-005 / AC-005.1:
    When a book's brief.genre (or brief / opening chapter corpus containing >= 2 fenced code blocks
    or >= 5 seed terms) matches technical domains, additively appends missing entries from the
    16-entry TECHNICAL_SEED_GLOSSARY_V1 table into both top-level and nested terms and verbatim_terms
    without overwriting or duplicating existing terms.
    Returns True if book_record was modified, else False.
    """
    brief = book_record.get("brief", {})
    genre_text = (brief.get("genre", "") if isinstance(brief, dict) else str(brief)).lower()
    is_tech = any(tok in genre_text for tok in TECHNICAL_GENRE_TOKENS)

    if not is_tech and sample_text:
        fences = len(re.findall(r"```", sample_text)) // 2
        hits = sum(
            1 for s in TECHNICAL_SEED_GLOSSARY_V1
            if re.search(r"\b" + re.escape(s["source"]) + r"\b", sample_text, re.IGNORECASE)
        )
        if fences >= 2 or hits >= 5:
            is_tech = True

    if not is_tech:
        return False

    modified = False
    br_nested = book_record.setdefault("book_record", {})
    if "terms" in book_record and isinstance(book_record["terms"], dict):
        book_record["terms"] = [{"source": k, "target": str(v)} for k, v in book_record["terms"].items()]
    if isinstance(br_nested, dict) and isinstance(br_nested.get("terms"), dict):
        br_nested["terms"] = [{"source": k, "target": str(v)} for k, v in br_nested["terms"].items()]

    top_terms = book_record.setdefault("terms", [])
    nested_terms = br_nested.setdefault("terms", [])

    existing_sources: Dict[str, Dict[str, Any]] = {}
    for t in top_terms + nested_terms:
        if isinstance(t, dict) and "source" in t:
            src_lower = t["source"].strip().lower()
            if src_lower not in existing_sources:
                existing_sources[src_lower] = t

    top_verbatim = book_record.setdefault("verbatim_terms", [])
    nested_verbatim = br_nested.setdefault("verbatim_terms", [])
    top_verbatim_set = {v.strip().lower() for v in top_verbatim if isinstance(v, str)}
    nested_verbatim_set = {v.strip().lower() for v in nested_verbatim if isinstance(v, str)}

    for seed in TECHNICAL_SEED_GLOSSARY_V1:
        s_src = seed["source"].strip()
        s_lower = s_src.lower()
        if s_lower not in existing_sources:
            new_entry = dict(seed)
            top_terms.append(new_entry)
            nested_terms.append(dict(seed))
            existing_sources[s_lower] = new_entry
            modified = True

        if seed.get("verbatim") is True:
            if s_lower not in top_verbatim_set:
                top_verbatim.append(s_src)
                top_verbatim_set.add(s_lower)
                modified = True
            if s_lower not in nested_verbatim_set:
                nested_verbatim.append(s_src)
                nested_verbatim_set.add(s_lower)
                modified = True

    # Synchronize all terms across top-level and nested tables
    for s_lower, entry in existing_sources.items():
        top_has = any(t.get("source", "").strip().lower() == s_lower for t in top_terms if isinstance(t, dict))
        nested_has = any(t.get("source", "").strip().lower() == s_lower for t in nested_terms if isinstance(t, dict))
        if not top_has:
            top_terms.append(dict(entry))
            modified = True
        if not nested_has:
            nested_terms.append(dict(entry))
            modified = True

    return modified


def is_fiction_book(book_record: Dict[str, Any], sample_chapters_text: str = "") -> bool:
    """
    REQ-001 / AC-001.1:
    Determines whether a book belongs to narrative/fiction genres.
    """
    brief = book_record.get("brief", {})
    if isinstance(brief, dict):
        genre_text = (brief.get("genre", "") or "").lower()
        if any(tok in genre_text for tok in NON_FICTION_EXCLUSION_TOKENS):
            return False
        if any(tok in genre_text for tok in FICTION_GENRE_TOKENS):
            return True
        # If no explicit genre tokens match in the dict, fall through to dialogue quote check below
        brief_str = str(brief.get("summary", "") or "")
    else:
        # Plain string brief on a fresh run
        brief_str = str(brief) if brief is not None else ""
        genre_text = brief_str.lower()
        if any(tok in genre_text for tok in NON_FICTION_EXCLUSION_TOKENS):
            return False

    if sample_chapters_text:
        fences = len(re.findall(r"```", sample_chapters_text)) // 2
        hits = sum(
            1 for s in TECHNICAL_SEED_GLOSSARY_V1
            if re.search(r"\b" + re.escape(s["source"]) + r"\b", sample_chapters_text, re.IGNORECASE)
        )
        if fences >= 2 or hits >= 5:
            return False

        quote_count = len(re.findall(r'[“”「」]|\n—', sample_chapters_text))
        if quote_count >= 5:
            if isinstance(book_record.get("brief"), dict):
                book_record["brief"]["genre"] = "fiction"
            else:
                book_record["brief"] = {"genre": "fiction", "summary": brief_str}
            return True

    return False


def _is_pair_attested_in_source(pair: List[str], names: List[Dict[str, Any]], sample_text: str) -> bool:
    """
    REQ-001 / AC-001.2:
    Verifies deterministically that both participants in pair appear in sample_text.
    """
    if not pair or len(pair) < 2 or not sample_text:
        return False

    name_map: Dict[str, List[str]] = {}
    for n in names:
        if isinstance(n, dict):
            src = n.get("source", "").strip()
            tgt = n.get("target", "").strip()
            aliases = list(n.get("also_referred_to_as") or []) + list(n.get("aliases") or []) + list(n.get("variants") or [])
            all_forms = [src, tgt] + [a.strip() for a in aliases if a and isinstance(a, str) and a.strip()]
            all_forms = [f for f in all_forms if f]
            for form in all_forms:
                name_map.setdefault(form.lower(), []).extend(all_forms)

    for pt in pair:
        pt_clean = str(pt).strip()
        candidates = set(name_map.get(pt_clean.lower(), [pt_clean]))
        candidates.add(pt_clean)
        found = False
        for cand in candidates:
            if not cand:
                continue
            if re.search(r"\b" + re.escape(cand) + r"\b", sample_text, re.IGNORECASE) or cand in sample_text:
                found = True
                break
        if not found:
            return False
    return True


def assert_fiction_pronouns_populated(
    workspace_dir: Path,
    book_record_path: Path,
    book_record: Dict[str, Any],
    offline: bool = False,
) -> Dict[str, Any]:
    """
    REQ-001 / AC-001.2 / REQ-TXN-003:
    Ensures fiction books have a populated pronouns table synchronized across
    top-level and nested book_record before chapter translation under an exclusive lock.
    """
    ch_sources = sorted(workspace_dir.glob("ch*/source.txt"))[:3]
    sample_text = "\n\n".join(cs.read_text(encoding="utf-8") for cs in ch_sources if cs.exists())

    if is_fiction_book(book_record, sample_text):
        with interprocess_lock(book_record_path.with_name(f".{book_record_path.name}.lock"), timeout=30.0):
            pronouns = book_record.get("pronouns") or []
            br_nested = book_record.setdefault("book_record", {})
            nested_pronouns = br_nested.setdefault("pronouns", [])
            if not pronouns and not nested_pronouns:
                if offline:
                    names = book_record.get("names") or br_nested.get("names") or []
                    created_pairs = []
                    if len(names) >= 2:
                        p1 = names[0].get("source") or names[0].get("target") or "A"
                        p2 = names[1].get("source") or names[1].get("target") or "B"
                        created_pairs.append({
                            "pair": [p1, p2],
                            "a_to_b": "tôi/anh",
                            "b_to_a": "tôi/anh",
                            "basis": "offline_synthetic_fallback",
                            "changes": [],
                            "provisional": True,
                        })
                    else:
                        created_pairs.append({
                            "pair": ["A", "B"],
                            "a_to_b": "tôi/anh",
                            "b_to_a": "tôi/anh",
                            "basis": "offline_synthetic_fallback",
                            "changes": [],
                            "provisional": True,
                        })
                    book_record["pronouns"] = created_pairs
                    br_nested["pronouns"] = list(created_pairs)
                    sync_book_record_views(book_record)
                    snapshot_backup(book_record_path)
                    atomic_write_json(book_record_path, book_record)
                    print("[WARN] provisional offline pronoun fallback")
                else:
                    cbc_bin = shutil.which("cbc") or ("/opt/homebrew/bin/cbc" if os.path.isfile("/opt/homebrew/bin/cbc") else None)
                    if not cbc_bin:
                        raise RuntimeError("Live pronoun extraction requires cbc CLI")
                    prompt = (
                        "Nhiệm vụ: Trích xuất các nhân vật chính và bảng quy ước đại từ xưng hô (pronouns) giữa các cặp nhân vật từ đoạn trích tiểu thuyết sau:\n\n"
                        f"{sample_text[:4000]}\n\n"
                        "Trả về JSON định dạng:\n"
                        '{\n  "names": [{"source": "Name", "target": "Tên", "also_referred_to_as": ["alias"]}],\n'
                        '  "pronouns": [{"pair": ["A", "B"], "a_to_b": "tôi/anh", "b_to_a": "em/anh", "basis": "quan hệ", "changes": []}]\n}'
                    )
                    proc = subprocess.run([cbc_bin, "-p", "--model", "deepseek-v4.1-flash"], input=prompt, text=True, capture_output=True, timeout=60)
                    if proc.returncode != 0:
                        raise RuntimeError(f"Live pronoun extraction failed: {proc.stderr}")
                    match = re.search(r"\{.*\}", proc.stdout, re.DOTALL)
                    if not match:
                        raise RuntimeError("Live pronoun extraction returned invalid JSON")
                    extracted = json.loads(match.group(0))
                    ext_pronouns = extracted.get("pronouns", [])
                    ext_names = extracted.get("names", [])
                    all_names = ext_names + (book_record.get("names") or [])
                    valid_pairs = []
                    for p in ext_pronouns:
                        pr = p.get("pair", [])
                        if len(pr) == 2:
                            attested = _is_pair_attested_in_source(pr, all_names, sample_text)
                            p["provisional"] = not attested
                            if attested:
                                valid_pairs.append(p)
                    if not valid_pairs:
                        raise RuntimeError("Live pronoun extraction yielded 0 source-attested pairs")
                    book_record["pronouns"] = valid_pairs
                    br_nested["pronouns"] = list(valid_pairs)
                    sync_book_record_views(book_record)
                    snapshot_backup(book_record_path)
                    atomic_write_json(book_record_path, book_record)

    return book_record

    return book_record


def check_redraft(
    issues: List[Dict[str, Any]],
    draft_text_or_len: Any,
    score: float,
    redraft_threshold: float = 2.5,
    coverage_threshold: float = 0.4,
    **kwargs: Any,
) -> bool:
    """Wrapper around helper.check_redraft supporting text or length and both parameter naming styles (FLOW-7)."""
    if isinstance(draft_text_or_len, str):
        total_len = len(draft_text_or_len.split()) if draft_text_or_len else 1
    else:
        try:
            total_len = float(draft_text_or_len)
        except (ValueError, TypeError):
            total_len = 1.0
    score_thresh = kwargs.get("score_thresh", redraft_threshold)
    coverage_thresh = kwargs.get("coverage_thresh", coverage_threshold)
    return helper_check_redraft(
        issues=issues,
        total_length=total_len,
        weighted_total=score,
        coverage_thresh=coverage_thresh,
        score_thresh=score_thresh,
    )


def parse_chapter_filter(filter_str: Optional[str]) -> Optional[Set[str]]:
    """
    Parses chapter filter strings like '1-3', '1..3', '1', 'ch01,ch02' into a set of canonical chapter IDs.
    Returns None if filter_str is None or empty.
    """
    if not filter_str or not filter_str.strip():
        return None

    raw = filter_str.strip()
    selected: Set[str] = set()

    parts = [p.strip() for p in raw.split(",") if p.strip()]
    for p in parts:
        # Check range syntax '1-3' or '1..3'
        range_match = re.match(r"^(\d+)(?:\.\.|\-)(\d+)$", p)
        if range_match:
            start_idx = int(range_match.group(1))
            end_idx = int(range_match.group(2))
            for i in range(start_idx, end_idx + 1):
                selected.add(f"ch{i:02d}")
                selected.add(str(i))
            continue

        if p.isdigit():
            selected.add(f"ch{int(p):02d}")
            selected.add(str(int(p)))
            continue

        norm = normalize_chapter_id(p)
        selected.add(norm)
        # Also add digit if applicable
        if norm.startswith("ch") and norm[2:].isdigit():
            selected.add(str(int(norm[2:])))

    return selected


def initialize_book_record(run_dir: Path, slug: str, source_lang: str = "en") -> Path:
    """Ensures book_record.json exists in run_dir with standard schema under an exclusive file lock."""
    record_path = run_dir / "book_record.json"
    with interprocess_lock(record_path.with_name(f".{record_path.name}.lock"), timeout=30.0):
        if not record_path.exists():
            default_record = {
                "book_slug": slug,
                "source_lang": source_lang,
                "target_lang": "vi",
                "title": slug.replace("_", " ").title(),
                "brief": {
                    "genre": "non-fiction",
                    "summary": f"Vietnamese translation of {slug}",
                },
                "book_record": {
                    "terms": [],
                    "names": [],
                    "pronouns": [],
                },
                "terms": [],
                "names": [],
                "pronouns": [],
                "term_table": {},
                "name_table": {},
                "pronoun_table": {},
            }
            ensure_technical_seed_glossary(default_record)
            sync_book_record_views(default_record)
            record_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(record_path, default_record)
        else:
            data = read_json_state(record_path, {}, expected_type=dict)
            changed = False
            if isinstance(data.get("brief"), str):
                data["brief"] = {
                    "genre": "non-fiction",
                    "summary": data["brief"],
                }
                changed = True
            if "source_lang" not in data or (source_lang != "en" and data.get("source_lang") == "en"):
                data["source_lang"] = source_lang
                changed = True
            if ensure_technical_seed_glossary(data):
                changed = True
            sync_book_record_views(data)
            snapshot_backup(record_path)
            atomic_write_json(record_path, data)
    return record_path


STATUS_RANK: Dict[str, int] = {
    "pending": 0,
    "in-progress": 1,
    "awaiting-agent": 1,
    "translating": 1,
    "translated": 1,
    "evaluating": 1,
    "refining": 1,
    "reviewed-r1": 1,
    "redrafted-round1": 1,
    "failed-validation": 1,
    "failed": 1,
    "error": 1,
    "released-with-warning": 2,
    "accepted": 3,
    "completed": 4,
}


def _ch_key(entry_or_id: Any) -> str:
    """Normalizes chapter key to canonical format 'chNN' or stripped string."""
    if isinstance(entry_or_id, dict):
        raw = entry_or_id.get("chapter") or entry_or_id.get("chapter_num") or entry_or_id.get("num") or ""
    else:
        raw = str(entry_or_id or "")
    m = re.search(r"\d+", str(raw))
    return f"ch{int(m.group(0)):02d}" if m else str(raw).strip()


def load_checklist(checklist_path: Union[Path, str]) -> List[Dict[str, Any]]:
    """Loads chapter checklist from checklist.json, propagating StateFileCorruptError on corrupt JSON (REQ-INT-003)."""
    p = Path(checklist_path).resolve()
    if p.is_dir():
        p = p / "checklist.json"
    raw = read_json_state(p, {"chapters": []}, expected_type=(dict, list))
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        ch_list = raw.get("chapters", [])
        if isinstance(ch_list, list) and ch_list:
            return ch_list
        cl_list = []
        for k, v in raw.items():
            if k == "chapters":
                continue
            entry = dict(v) if isinstance(v, dict) else {}
            entry["chapter"] = k
            cl_list.append(entry)
        return cl_list
    return []


def save_checklist(
    checklist_path: Union[Path, str],
    checklist: List[Dict[str, Any]],
    changed: Optional[Iterable[Any]] = None,
    force: bool = False,
) -> None:
    """
    REQ-INT-004 / ADR-001:
    Saves checklist data atomically to disk under an exclusive file lock, merging
    per-chapter records respecting changed and STATUS_RANK lattice unless force=True.
    """
    p = Path(checklist_path).resolve()
    if p.is_dir():
        p = p / "checklist.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    changed_keys = {_ch_key(x) for x in changed} if changed is not None else None

    with interprocess_lock(p.with_name(f".{p.name}.lock"), timeout=30.0):
        raw = read_json_state(p, [], expected_type=(list, dict))
        on_disk_cl = raw if isinstance(raw, list) else (raw.get("chapters", []) if isinstance(raw.get("chapters"), list) else [])
        disk_map: Dict[str, Dict[str, Any]] = {}
        for c in on_disk_cl:
            if isinstance(c, dict):
                k = _ch_key(c)
                if k:
                    disk_map[k] = c

        for c in checklist:
            if not isinstance(c, dict):
                continue
            k = _ch_key(c)
            if not k:
                continue
            if changed_keys is not None and k not in changed_keys:
                continue
            old = disk_map.get(k)
            if old is not None and not force:
                old_rank = STATUS_RANK.get(str(old.get("status", "pending")).strip(), 0)
                new_rank = STATUS_RANK.get(str(c.get("status", "pending")).strip(), 0)
                if new_rank < old_rank:
                    continue
            disk_map[k] = dict(c)

        snapshot_backup(p)
        atomic_write_json(p, list(disk_map.values()))


def discover_or_initialize_chapters(
    run_dir: Path,
    book_path: Path,
    slug: str,
    chapters_filter: Optional[str] = None,
    source_lang: str = "en",
) -> List[Dict[str, Any]]:
    """Discovers extracted chapters in run_dir or initializes checklist.json."""
    checklist_path = run_dir / "checklist.json"
    checklist = load_checklist(checklist_path)
    if checklist:
        return checklist

    # Check for existing ch* directories
    ch_dirs = sorted([
        d for d in run_dir.iterdir()
        if d.is_dir() and d.name.startswith("ch") and (d / "source.txt").exists()
    ])

    if ch_dirs:
        checklist = []
        for idx, d in enumerate(ch_dirs, start=1):
            num = int(d.name[2:]) if d.name[2:].isdigit() else idx
            checklist.append({
                "chapter": d.name,
                "chapter_num": num,
                "title": f"Chapter {num}",
                "source_lang": source_lang,
                "status": "pending",
                "round": 0,
                "score": None,
                "draft_exists": False,
            })
        save_checklist(checklist_path, checklist)
        return checklist

    # If extraction is needed and book_path is a supported format
    supported_exts = {".pdf", ".epub", ".docx", ".mobi", ".azw", ".azw3", ".md", ".markdown", ".txt", ".text"}
    if book_path.exists() and book_path.suffix.lower() in supported_exts:
        extract_script = REPO_ROOT / "scripts" / "extract.py"
        if not extract_script.exists():
            extract_script = REPO_ROOT / "scripts" / "extract_pdf.py"
        if extract_script.exists():
            cmd = []
            if shutil.which("uv"):
                cmd = ["uv", "run", "python3", str(extract_script), str(book_path), "--output-dir", str(run_dir), "--xml-segments", "--slug", slug, "--source-lang", source_lang]
            else:
                cmd = [sys.executable, str(extract_script), str(book_path), "--output-dir", str(run_dir), "--xml-segments", "--slug", slug, "--source-lang", source_lang]
            if chapters_filter:
                cmd.extend(["--chapters", chapters_filter])

            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
                if proc.returncode != 0:
                    print(f"Warning: Extraction exited with code {proc.returncode}: {proc.stderr}")
            except Exception as e:
                print(f"Warning: Failed to execute extractor: {e}")

            # Re-check checklist after extraction
            checklist = load_checklist(checklist_path)
            if checklist:
                return checklist

            # Discover newly extracted directories
            ch_dirs = sorted([
                d for d in run_dir.iterdir()
                if d.is_dir() and d.name.startswith("ch") and (d / "source.txt").exists()
            ])
            if ch_dirs:
                checklist = []
                for idx, d in enumerate(ch_dirs, start=1):
                    num = int(d.name[2:]) if d.name[2:].isdigit() else idx
                    checklist.append({
                        "chapter": d.name,
                        "chapter_num": num,
                        "title": f"Chapter {num}",
                        "source_lang": source_lang,
                        "status": "pending",
                        "round": 0,
                        "score": None,
                        "draft_exists": False,
                    })
                save_checklist(checklist_path, checklist)
                return checklist

    # Default fallback checklist if no chapters found yet
    checklist = [
        {
            "chapter": "ch01",
            "chapter_num": 1,
            "title": "Chapter 1",
            "source_lang": source_lang,
            "status": "pending",
            "round": 0,
            "score": None,
            "draft_exists": False,
        }
    ]
    save_checklist(checklist_path, checklist)
    return checklist


def record_problem(
    run_dir: Union[Path, str],
    chapter_id: str,
    round_num: int,
    score: float,
    verdict: str,
    issues: List[Dict[str, Any]],
    diagnostic: str,
):
    """Appends problem entry to runs/<slug>/problem_report.json under interprocess lock with atomic write (REQ-INT-003, REQ-INT-004)."""
    report_path = Path(run_dir) / "problem_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with interprocess_lock(report_path.with_name(f".{report_path.name}.lock"), timeout=30.0):
        raw = read_json_state(report_path, [], expected_type=(list, dict))
        problems: List[Dict[str, Any]] = raw if isinstance(raw, list) else (raw.get("problems", []) if isinstance(raw.get("problems"), list) else [])

        problems.append({
            "chapter": chapter_id,
            "round": round_num,
            "score": score,
            "verdict": verdict,
            "issues": issues,
            "diagnostic": diagnostic,
        })
        snapshot_backup(report_path)
        atomic_write_json(report_path, problems)


def orchestrate_run(
    book_path: str | Path,
    slug: Optional[str] = None,
    run_dir: Optional[str | Path] = None,
    chapters: Optional[str] = None,
    formats: str = "epub,pdf",
    batch_size: int = 15,
    concurrency: int = 4,
    window_size: int = 250,
    max_refine_rounds: int = 2,
    fail_fast: bool = False,
    dry_run: bool = False,
    offline: bool = False,
    engine: str = "workbuddy",
    source_lang: str = "auto",
    threshold: Optional[float] = None,
    passes: int = 1,
    translation_engine: str = "agent",
) -> Dict[str, Any]:
    """
    Autonomous master orchestrator running the full book translation lifecycle.
    """
    book_path = Path(book_path).resolve()
    if run_dir:
        workspace_dir = resolve_run_dir(run_dir)
        book_slug = slug.strip() if (slug and slug.strip()) else workspace_dir.name
    else:
        book_slug = derive_book_slug(book_path, slug)
        workspace_dir = get_runs_dir() / book_slug
    workspace_dir.mkdir(parents=True, exist_ok=True)

    filter_set = parse_chapter_filter(chapters)

    # Resolve effective pass threshold (AC-ARCH-2.6, AC-ARCH-7.2)
    effective_threshold: float = 4.0
    if threshold is not None:
        effective_threshold = float(threshold)
    else:
        config_path = workspace_dir / "config.json"
        if config_path.exists():
            cfg = read_json_state(config_path, {}, expected_type=dict)
            if "quality_threshold" in cfg:
                effective_threshold = float(cfg["quality_threshold"])
        else:
            record_path = workspace_dir / "book_record.json"
            if record_path.exists():
                rec_data = read_json_state(record_path, {}, expected_type=dict)
                brief = rec_data.get("brief", {})
                genre = (brief.get("genre") or "").lower() if isinstance(brief, dict) else ""
                if any(g in genre for g in ("fiction", "literary", "novel", "văn học", "tiểu thuyết")):
                    effective_threshold = 3.5
                else:
                    effective_threshold = 4.0

    # Resolve source language
    effective_source_lang = source_lang
    if effective_source_lang == "auto":
        record_path = workspace_dir / "book_record.json"
        if record_path.exists():
            rec_data = read_json_state(record_path, {}, expected_type=dict)
            if rec_data.get("source_lang"):
                effective_source_lang = rec_data["source_lang"]
        if effective_source_lang == "auto":
            sample_source = workspace_dir / "ch01" / "source.txt"
            if sample_source.exists():
                from scripts.utils.language import detect_source_language
                effective_source_lang = detect_source_language(sample_source.read_text(encoding="utf-8"))
            else:
                from scripts.utils.language import detect_source_language
                effective_source_lang = detect_source_language(book_path.stem)

    # 1. Inspect or initialize chapters
    checklist_path = workspace_dir / "checklist.json"
    checklist = discover_or_initialize_chapters(workspace_dir, book_path, book_slug, chapters, source_lang=effective_source_lang)

    # Filter chapters if filter requested
    target_chapters = []
    for entry in checklist:
        ch_id = entry.get("chapter", "")
        ch_num = str(entry.get("chapter_num", ""))
        if filter_set is None or ch_id in filter_set or ch_num in filter_set:
            target_chapters.append(entry)

    # Handle --dry-run
    if dry_run:
        print("=" * 60)
        print(f"[DRY RUN] Book Input: {book_path}")
        print(f"[DRY RUN] Book Slug: {book_slug}")
        print(f"[DRY RUN] Source Language: {effective_source_lang}")
        print(f"[DRY RUN] Pass Threshold: {effective_threshold}")
        print(f"[DRY RUN] Passes: {passes}")
        print(f"[DRY RUN] Workspace Directory: {workspace_dir}")
        print(f"[DRY RUN] Formats: {formats}")
        print(f"[DRY RUN] Discovered / Target Chapters: {[c.get('chapter') for c in target_chapters]}")
        print("[DRY RUN] Planned Execution Stages:")
        print("  1. Ingestion: extract.py (--xml-segments)")
        print(f"  2. Book Analysis: {workspace_dir / 'book_record.json'}")
        print(f"  3. Chapter Loop: translate_chapter (batch_size={batch_size}, concurrency={concurrency})")
        print(f"     -> evaluate_workbuddy (window_size={window_size}, passes={passes}) -> Quorum -> Splicer")
        print(f"  4. Compilation: formats={formats}")
        print(f"  5. Summary: summarize_book.py")
        print("=" * 60)
        return {
            "status": "dry_run",
            "slug": book_slug,
            "source_lang": effective_source_lang,
            "run_dir": str(workspace_dir),
            "target_chapters": [c.get("chapter") for c in target_chapters],
        }

    # Step 2: Ensure book record exists
    record_path = initialize_book_record(workspace_dir, book_slug, source_lang=effective_source_lang)
    rec_data = read_json_state(record_path, {}, expected_type=dict)
    ch01_source = workspace_dir / "ch01" / "source.txt"
    sample_txt = ch01_source.read_text(encoding="utf-8") if ch01_source.exists() else ""
    if ensure_technical_seed_glossary(rec_data, sample_text=sample_txt):
        with interprocess_lock(record_path.with_name(f".{record_path.name}.lock"), timeout=30.0):
            atomic_write_json(record_path, rec_data)
    assert_fiction_pronouns_populated(
        workspace_dir=workspace_dir,
        book_record_path=record_path,
        book_record=rec_data,
        offline=offline,
    )

    # Step 3: Chapter Loop
    accepted_chapters: List[str] = []
    failed_chapters: List[str] = []
    awaiting_agent_chapters: List[str] = []

    for entry in checklist:
        ch_id = entry.get("chapter", "")
        ch_num = str(entry.get("chapter_num", ""))
        if filter_set is not None and ch_id not in filter_set and ch_num not in filter_set:
            continue

        # Check resumability
        if entry.get("status") in ("accepted", "released-with-warning", "completed"):
            print(f"[SKIP] Chapter {ch_id} already {entry.get('status')} (score: {entry.get('score')})")
            if entry.get("status") in ("accepted", "completed"):
                accepted_chapters.append(ch_id)
            else:
                failed_chapters.append(ch_id)
            continue

        cur_round = int(entry.get("round") or 0)
        start_round = 1
        if entry.get("status") in (
            "awaiting-agent",
            "in-progress",
            "translated",
            "refining",
            "reviewed-r1",
            "redrafted-round1",
        ) and cur_round >= 2:
            prev_eval = workspace_dir / ch_id / f"round{cur_round - 1}_evaluate.txt"
            if prev_eval.exists():
                start_round = cur_round

        print(f"[PROCESSING] Starting Chapter {ch_id}")
        entry["status"] = "in-progress"
        save_checklist(checklist_path, checklist, changed=[ch_id], force=True)

        ch_accepted = False
        latest_eval_res: Dict[str, Any] = {}
        best_score = -1.0
        best_xml = None
        best_md = None
        prev_score = -1.0
        round_candidates: List[Tuple[float, int, int, str, Path]] = []

        if start_round > 1:
            for r in range(1, start_round):
                r_eval_file = workspace_dir / ch_id / f"round{r}_evaluate.txt"
                r_draft_file = workspace_dir / ch_id / f"round{r}_refine.txt"
                if not r_draft_file.exists():
                    r_draft_file = workspace_dir / ch_id / f"round{r}_translate.txt"
                if r_eval_file.exists():
                    try:
                        r_eval_data = read_json_state(r_eval_file, {}, expected_type=dict)
                        r_score = float(r_eval_data.get("weighted_total", 0.0))
                        prev_score = r_score
                        r_crit = len([i for i in r_eval_data.get("issues", []) if isinstance(i, dict) and i.get("severity") == "critical"])
                        r_text = r_draft_file.read_text(encoding="utf-8") if r_draft_file.exists() else ""
                        round_candidates.append((r_score, -r_crit, r, r_text, r_draft_file))
                        if r_score > best_score:
                            best_score = r_score
                            best_md = r_text
                    except Exception as e:
                        if isinstance(e, StateFileCorruptError):
                            raise
                        print(f"  [WARN] Failed reading prior round {r} state: {e}")

        for round_num in range(start_round, max_refine_rounds + 1):
            entry["round"] = round_num

            # 3A. Translate or Refine Chapter
            if round_num > 1:
                prev_eval_file = workspace_dir / ch_id / f"round{round_num-1}_evaluate.txt"
                prev_draft_file = workspace_dir / ch_id / f"round{round_num-1}_refine.txt"
                if not prev_draft_file.exists():
                    prev_draft_file = workspace_dir / ch_id / f"round{round_num-1}_translate.txt"
                should_redraft = False
                prev_issues: List[Dict[str, Any]] = []
                p_score = 5.0
                prev_draft_text = ""
                if prev_eval_file.exists():
                    try:
                        prev_eval_data = read_json_state(prev_eval_file, {}, expected_type=dict)
                        prev_issues = prev_eval_data.get("issues", [])
                        p_score = float(prev_eval_data.get("weighted_total", 5.0))
                        prev_draft_text = prev_draft_file.read_text(encoding="utf-8") if prev_draft_file.exists() else ""
                        should_redraft = check_redraft(
                            issues=prev_issues,
                            draft_text_or_len=prev_draft_text,
                            score=p_score,
                            redraft_threshold=2.5,
                            coverage_threshold=0.4,
                        )
                    except Exception as e:
                        if isinstance(e, StateFileCorruptError):
                            raise
                        print(f"  [WARN] Failed to check redraft on {prev_eval_file}: {e}")

                if should_redraft:
                    from scripts.refine_chapter import map_issues_to_segments
                    prev_segments = parse_segments(prev_draft_text)
                    if not prev_segments:
                        source_file_fallback = workspace_dir / ch_id / "source.txt"
                        if source_file_fallback.exists():
                            prev_segments = parse_segments(source_file_fallback.read_text(encoding="utf-8"))
                    crit_or_major = [i for i in prev_issues if isinstance(i, dict) and i.get("severity") in ("critical", "major")]
                    mapped = map_issues_to_segments(crit_or_major if crit_or_major else prev_issues, prev_segments)
                    is_pervasive = any(bool(i.get("pervasive")) for i in prev_issues if isinstance(i, dict))
                    if p_score < 2.5 or is_pervasive or not mapped:
                        bypass_ids = {s["id"] for s in prev_segments if s.get("type") not in ("code", "figure")}
                    else:
                        bypass_ids = {str(k) for k in mapped.keys() if k}

                    if translation_engine == "agent":
                        print(f"  [REDRAFT] Chapter {ch_id} (Round {round_num}, batch_size={batch_size}, concurrency={concurrency})")
                        target_xml, final_md = translate_chapter(
                            run_dir=workspace_dir,
                            chapter=ch_id,
                            round_num=round_num,
                            batch_size=batch_size,
                            concurrency=concurrency,
                            offline=True,
                            source_lang=effective_source_lang,
                            promote_final=False,
                            bypass_cache_ids=bypass_ids,
                            redraft_issues=prev_issues,
                        )
                        candidate_file = workspace_dir / ch_id / f"round{round_num}_translate.txt"
                        untranslated_file = workspace_dir / ch_id / f"round{round_num}_untranslated.json"
                        if untranslated_file.exists():
                            u_data = read_json_state(untranslated_file, {}, expected_type=dict)
                            seg_ids = u_data.get("segment_ids", [])
                            if seg_ids:
                                entry["status"] = "awaiting-agent"
                                entry["pending_segment_ids"] = seg_ids
                                save_checklist(checklist_path, checklist, changed=[ch_id], force=True)
                                awaiting_agent_chapters.append(ch_id)
                                print(f"  [AWAITING AGENT] Chapter {ch_id} round {round_num}: {len(seg_ids)} redraft segment(s) pending. Run: python3 scripts/agent_run/agent_state.py cursor --run-dir {workspace_dir}")
                                break
                    else:
                        print(f"  [REDRAFT] Chapter {ch_id} (Round {round_num}, batch_size={batch_size}, concurrency={concurrency})")
                        target_xml, final_md = translate_chapter(
                            run_dir=workspace_dir,
                            chapter=ch_id,
                            round_num=round_num,
                            batch_size=batch_size,
                            concurrency=concurrency,
                            offline=offline,
                            source_lang=effective_source_lang,
                            promote_final=False,
                            bypass_cache_ids=bypass_ids,
                            redraft_issues=prev_issues,
                        )
                        candidate_file = workspace_dir / ch_id / f"round{round_num}_translate.txt"
                else:
                    # Round > 1 REFINE
                    if translation_engine == "agent":
                        from scripts.refine_chapter import map_issues_to_segments, refine_chapter
                        prev_segments = parse_segments(prev_draft_text)
                        if not prev_segments:
                            source_file_fallback = workspace_dir / ch_id / "source.txt"
                            if source_file_fallback.exists():
                                prev_segments = parse_segments(source_file_fallback.read_text(encoding="utf-8"))
                        seg_to_issues = map_issues_to_segments(prev_issues, prev_segments)
                        flagged_ids = sorted([str(k) for k in seg_to_issues.keys() if k])
                        cache_mgr = TranslationCacheManager(workspace_dir / ch_id / "translation_cache.json")
                        prev_seg_translate = {s["id"]: s.get("translate", "") for s in prev_segments if "id" in s}

                        all_updated = True
                        if not flagged_ids:
                            all_updated = True
                        else:
                            for sid in flagged_ids:
                                s_orig = next((s.get("original", "") for s in prev_segments if s.get("id") == sid), "")
                                cached_val = cache_mgr.get_valid(sid, s_orig)
                                if cached_val is None:
                                    all_updated = False
                                    break
                                prov = cache_mgr.get_provenance(sid) or {}
                                prov_round = int(prov.get("round", 0))
                                prev_val = prev_seg_translate.get(sid, "")
                                if cached_val == prev_val and prov_round < round_num:
                                    all_updated = False
                                    break

                        if not all_updated:
                            refine_reqs = []
                            for sid in flagged_ids:
                                s_obj = next((s for s in prev_segments if s.get("id") == sid), {})
                                refine_reqs.append({
                                    "segment_id": sid,
                                    "original": s_obj.get("original", ""),
                                    "translate": s_obj.get("translate", ""),
                                    "issues": seg_to_issues.get(sid, []),
                                })
                            req_file = workspace_dir / ch_id / f"round{round_num}_refine_requests.json"
                            atomic_write_json(req_file, {
                                "chapter": ch_id,
                                "round": round_num,
                                "segments": refine_reqs,
                            })
                            entry["status"] = "awaiting-agent"
                            entry["pending_segment_ids"] = flagged_ids
                            save_checklist(checklist_path, checklist, changed=[ch_id], force=True)
                            awaiting_agent_chapters.append(ch_id)
                            print(f"  [AWAITING AGENT] Chapter {ch_id} round {round_num}: {len(flagged_ids)} refine segment(s) pending. Run: python3 scripts/agent_run/agent_state.py cursor --run-dir {workspace_dir}")
                            break
                        else:
                            print(f"  [REFINE] Chapter {ch_id} (Round {round_num}, refining from {prev_eval_file.name})")
                            target_xml, final_md = refine_chapter(
                                run_dir=workspace_dir,
                                chapter=ch_id,
                                round_num=round_num,
                                eval_file=prev_eval_file,
                                offline=True,
                                source_lang=effective_source_lang,
                                concurrency=concurrency,
                                promote_final=False,
                            )
                            candidate_file = workspace_dir / ch_id / f"round{round_num}_refine.txt"
                    else:
                        from scripts.refine_chapter import refine_chapter
                        print(f"  [REFINE] Chapter {ch_id} (Round {round_num}, refining from {prev_eval_file.name})")
                        target_xml, final_md = refine_chapter(
                            run_dir=workspace_dir,
                            chapter=ch_id,
                            round_num=round_num,
                            eval_file=prev_eval_file,
                            offline=offline,
                            source_lang=effective_source_lang,
                            concurrency=concurrency,
                            promote_final=False,
                        )
                        candidate_file = workspace_dir / ch_id / f"round{round_num}_refine.txt"
            else:
                # Round 1
                if translation_engine == "agent":
                    print(f"  [TRANSLATE] Chapter {ch_id} (Round 1, batch_size={batch_size}, concurrency={concurrency})")
                    target_xml, final_md = translate_chapter(
                        run_dir=workspace_dir,
                        chapter=ch_id,
                        round_num=1,
                        batch_size=batch_size,
                        concurrency=concurrency,
                        offline=True,
                        source_lang=effective_source_lang,
                        promote_final=False,
                    )
                    candidate_file = workspace_dir / ch_id / "round1_translate.txt"
                    untranslated_file = workspace_dir / ch_id / "round1_untranslated.json"
                    if untranslated_file.exists():
                        u_data = read_json_state(untranslated_file, {}, expected_type=dict)
                        seg_ids = u_data.get("segment_ids", [])
                        if seg_ids:
                            entry["status"] = "awaiting-agent"
                            entry["pending_segment_ids"] = seg_ids
                            save_checklist(checklist_path, checklist, changed=[ch_id], force=True)
                            awaiting_agent_chapters.append(ch_id)
                            print(f"  [AWAITING AGENT] Chapter {ch_id} round 1: {len(seg_ids)} segment(s) pending. Run: python3 scripts/agent_run/agent_state.py cursor --run-dir {workspace_dir}")
                            break
                else:
                    print(f"  [TRANSLATE] Chapter {ch_id} (Round 1, batch_size={batch_size}, concurrency={concurrency})")
                    target_xml, final_md = translate_chapter(
                        run_dir=workspace_dir,
                        chapter=ch_id,
                        round_num=1,
                        batch_size=batch_size,
                        concurrency=concurrency,
                        offline=offline,
                        source_lang=effective_source_lang,
                        promote_final=False,
                    )
                    candidate_file = workspace_dir / ch_id / "round1_translate.txt"

            # 3B. Parity Validation
            source_file = workspace_dir / ch_id / "source.txt"
            draft_file = candidate_file
            if source_file.exists():
                is_valid, parity_errors = validate_segment_parity(
                    source_file.read_text(encoding="utf-8"),
                    target_xml,
                )
                if not is_valid:
                    print(f"  [PARITY WARNING] Chapter {ch_id} parity discrepancies: {parity_errors}")

            # 3C. Evaluate via WorkBuddy
            print(f"  [EVALUATE] Chapter {ch_id} (Round {round_num}, window_size={window_size}, passes={passes})")
            eval_output = workspace_dir / ch_id / f"round{round_num}_evaluate.txt"

            if passes > 1:
                def _run_single_pass(p_idx: int) -> Dict[str, Any]:
                    p_output = workspace_dir / ch_id / f"round{round_num}_evaluate_p{p_idx}.txt"
                    for attempt in range(2):
                        try:
                            return run_workbuddy_evaluation(
                                source_path=str(source_file) if source_file.exists() else str(draft_file),
                                draft_path=str(draft_file),
                                book_record_path=str(workspace_dir / "book_record.json"),
                                output_path=str(p_output),
                                force_mock=offline,
                                window_size=window_size,
                                engine=engine,
                                source_lang=effective_source_lang,
                                pass_threshold=effective_threshold,
                            )
                        except Exception:
                            if attempt == 0:
                                time.sleep(2)
                            else:
                                raise

                with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, passes)) as executor:
                    futures = [executor.submit(_run_single_pass, p_idx) for p_idx in range(1, passes + 1)]
                    eval_passes = [f.result() for f in futures]

                agg_res = aggregate_multi_pass(eval_passes)
                score = agg_res["weighted_total"]
                selected_pass = agg_res["selected_pass"]

                quorum_verdict = check_critical_quorum(eval_passes)
                if quorum_verdict in ("FAIL", "CONTESTED"):
                    verdict = "FAIL"
                else:
                    verdict = selected_pass.get("verdict", "PASS")
                    if score < effective_threshold:
                        verdict = "FAIL"

                eval_res = dict(selected_pass)
                eval_res["weighted_total"] = score
                eval_res["verdict"] = verdict
                eval_res["multi_pass"] = {
                    "passes": passes,
                    "spread": agg_res.get("spread", 0.0),
                    "quorum": quorum_verdict,
                }
                if any(bool(p.get("fallback_unverified")) for p in eval_passes if isinstance(p, dict)):
                    eval_res["fallback_unverified"] = True
                with open(eval_output, "w", encoding="utf-8") as f:
                    json.dump(eval_res, f, ensure_ascii=False, indent=2)
            else:
                eval_res = run_workbuddy_evaluation(
                    source_path=str(source_file) if source_file.exists() else str(draft_file),
                    draft_path=str(draft_file),
                    book_record_path=str(workspace_dir / "book_record.json"),
                    output_path=str(eval_output),
                    force_mock=offline,
                    window_size=window_size,
                    engine=engine,
                    source_lang=effective_source_lang,
                    pass_threshold=effective_threshold,
                )
                score = eval_res.get("weighted_total", 5.0)
                verdict = eval_res.get("verdict", "PASS")

            latest_eval_res = eval_res
            critical_count = len([i for i in eval_res.get("issues", []) if i.get("severity") == "critical"])
            round_candidates.append((score, -critical_count, round_num, final_md, candidate_file))

            # Update best draft tracking (FLOW-4)
            if score > best_score:
                best_score = score
                best_xml = target_xml
                best_md = final_md

            # 3D. Quorum Gate against effective_threshold
            is_unverified_fallback = bool(eval_res.get("fallback_unverified")) and not offline
            if score >= effective_threshold and verdict == "PASS" and not is_unverified_fallback:
                ch_accepted = True
                entry["status"] = "accepted"
                entry["score"] = score
                entry["draft_exists"] = True
                if final_md:
                    atomic_write_text(workspace_dir / ch_id / "final.txt", final_md)
                    heading_m = re.search(r"^#\s+(.+)$", final_md, re.MULTILINE)
                    if heading_m:
                        entry["title"] = heading_m.group(1).strip()
                save_checklist(checklist_path, checklist, changed=[ch_id], force=True)
                print(f"  [ACCEPTED] Chapter {ch_id} passed round {round_num} with score {score} (threshold: {effective_threshold})")

                # Prospective book_record update from accepted chapter review (REQ-COR-005, bind=True)
                added_terms = update_book_record_from_eval(
                    book_record_path=workspace_dir / "book_record.json",
                    eval_data=eval_res,
                    chapter_dir=workspace_dir / ch_id,
                    bind=True,
                )
                bind_staged_proposals(
                    book_record_path=workspace_dir / "book_record.json",
                    chapter_dir=workspace_dir / ch_id,
                )
                if added_terms:
                    added_names = [t.get("source") for t in added_terms]
                    print(f"  [BOOK RECORD UPDATED] Added/Updated {len(added_terms)} prospective term(s): {added_names}")

                accepted_chapters.append(ch_id)
                break
            else:
                if is_unverified_fallback:
                    print(f"  [FALLBACK BLOCKED] Chapter {ch_id} evaluation is an unverified fallback in live mode (score: {score}). Promotion to accepted blocked.")
                else:
                    print(f"  [RETRY] Chapter {ch_id} round {round_num} scored {score} ({verdict}, threshold: {effective_threshold})")

                # Check regression early stop (FLOW-3)
                if round_num > 1 and score <= prev_score:
                    print(f"  [REGRESSION STOP] Chapter {ch_id} round {round_num} score ({score}) <= previous round score ({prev_score}). Stopping early.")
                    break

            prev_score = score

        if not ch_accepted and entry.get("status") != "awaiting-agent":
            failed_chapters.append(ch_id)
            final_ch_score = best_score if best_score > 0 else latest_eval_res.get("weighted_total", 3.0)
            entry["status"] = "released-with-warning"
            entry["score"] = final_ch_score
            save_checklist(checklist_path, checklist, changed=[ch_id], force=True)

            # Restore best_md to final.txt (FLOW-4 / REQ-TXN-004)
            if round_candidates:
                best_cand = max(round_candidates, key=lambda c: (c[0], c[1], c[2]))
                best_md = best_cand[3]
            if best_md:
                atomic_write_text(workspace_dir / ch_id / "final.txt", best_md)

            # Prospective book_record update from released-with-warning chapter review (REQ-COR-005, bind=False)
            if latest_eval_res:
                update_book_record_from_eval(
                    book_record_path=workspace_dir / "book_record.json",
                    eval_data=latest_eval_res,
                    chapter_dir=workspace_dir / ch_id,
                    bind=False,
                )

            diag = f"Chapter {ch_id} did not reach pass threshold (>= {effective_threshold}) within {entry.get('round', max_refine_rounds)} rounds (best score: {final_ch_score})."
            record_problem(
                run_dir=workspace_dir,
                chapter_id=ch_id,
                round_num=entry.get("round", max_refine_rounds),
                score=final_ch_score,
                verdict=latest_eval_res.get("verdict", "FAIL"),
                issues=latest_eval_res.get("issues", []),
                diagnostic=diag,
            )

            if fail_fast:
                raise RuntimeError(f"Chapter {ch_id} failed quorum with score {final_ch_score} and --fail-fast is enabled.")

    # Step 4: Compilation
    deliverables = []
    compile_script = REPO_ROOT / "scripts" / "compile_book.py"
    if not compile_script.exists():
        compile_script = REPO_ROOT / "scripts" / "compile_pdf.py"

    if compile_script.exists():
        cmd = []
        if compile_script.name == "compile_book.py":
            if shutil.which("uv"):
                cmd = ["uv", "run", "python3", str(compile_script), "--run-dir", str(workspace_dir), "--formats", formats]
            else:
                cmd = [sys.executable, str(compile_script), "--run-dir", str(workspace_dir), "--formats", formats]
        else:
            output_pdf = workspace_dir / f"{book_slug}_vietnamese.pdf"
            if shutil.which("uv"):
                cmd = ["uv", "run", "python3", str(compile_script), "--run-dir", str(workspace_dir), "--output-pdf", str(output_pdf)]
            else:
                cmd = [sys.executable, str(compile_script), "--run-dir", str(workspace_dir), "--output-pdf", str(output_pdf)]

        compilation_skipped = False
        checklist_data = load_checklist(checklist_path)
        compilable = [
            str(c.get("chapter_num") or c.get("num") or c.get("chapter"))
            for c in checklist_data
            if c.get("status") in ("accepted", "released-with-warning", "completed")
            and (workspace_dir / normalize_chapter_id(c.get("chapter")) / "final.txt").is_file()
        ]
        if not compilable:
            print("  [WARN] No compilable chapters with terminal status and final.txt found. Skipping compilation.")
            compilation_skipped = True
        else:
            if chapters:
                cmd.extend(["--chapters", str(chapters)])
            else:
                cmd.extend(["--chapters", ",".join(compilable)])

        if not compilation_skipped:
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
                if proc.returncode != 0:
                    sys.stderr.write(f"Compilation failed (code {proc.returncode}):\n{proc.stderr}\n")
                    record_problem(
                        run_dir=workspace_dir,
                        chapter_id="__book__",
                        round_num=0,
                        score=0.0,
                        verdict="COMPILE_ERROR",
                        issues=[],
                        diagnostic=f"Compilation failed with exit code {proc.returncode}: {proc.stderr[-2000:]}",
                    )
                else:
                    format_list = [f.strip().lower() for f in formats.split(",") if f.strip()]
                    for fmt in format_list:
                        cand = workspace_dir / f"{book_slug}_vietnamese.{fmt}"
                        if cand.exists():
                            deliverables.append(str(cand))
            except Exception as e:
                sys.stderr.write(f"Warning: Compilation failed: {e}\n")
                record_problem(
                    run_dir=workspace_dir,
                    chapter_id="__book__",
                    round_num=0,
                    score=0.0,
                    verdict="COMPILE_ERROR",
                    issues=[],
                    diagnostic=f"Compilation exception: {e}",
                )

    # Step 5: Summary Report
    summarize_script = REPO_ROOT / "scripts" / "summarize_book.py"
    if summarize_script.exists():
        try:
            proc = subprocess.run(
                [sys.executable, str(summarize_script), "--run-dir", str(workspace_dir)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode != 0:
                sys.stderr.write(f"Summary generation failed (code {proc.returncode}):\n{proc.stderr}\n")
                record_problem(
                    run_dir=workspace_dir,
                    chapter_id="__book__",
                    round_num=0,
                    score=0.0,
                    verdict="SUMMARY_ERROR",
                    issues=[],
                    diagnostic=f"Summary generation failed with exit code {proc.returncode}: {proc.stderr[-2000:]}",
                )
        except Exception as e:
            sys.stderr.write(f"Warning: Summary generation failed: {e}\n")
            record_problem(
                run_dir=workspace_dir,
                chapter_id="__book__",
                round_num=0,
                score=0.0,
                verdict="SUMMARY_ERROR",
                issues=[],
                diagnostic=f"Summary generation exception: {e}",
            )

    print("=" * 60)
    print(f"Orchestration completed for {book_slug}.")
    print(f"Accepted chapters: {accepted_chapters}")
    if failed_chapters:
        print(f"Chapters released with warning: {failed_chapters}")
    if awaiting_agent_chapters:
        print(f"Chapters awaiting agent: {awaiting_agent_chapters}")
    print("=" * 60)

    return {
        "status": "completed",
        "slug": book_slug,
        "run_dir": str(workspace_dir),
        "accepted_chapters": accepted_chapters,
        "failed_chapters": failed_chapters,
        "awaiting_agent_chapters": awaiting_agent_chapters,
        "deliverables": deliverables,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Autonomous Multi-Agent Book Translation Master Orchestrator CLI."
    )
    parser.add_argument("book_path", help="Path to input book (PDF, EPUB, TXT)")
    parser.add_argument("--slug", default=None, help="Book slug (default: derived from filename)")
    parser.add_argument("--run-dir", default=None, help="Root directory for run (defaults to runs/<slug>)")
    parser.add_argument("--chapters", default=None, help="Chapter filter (e.g. '1-3', '1', 'ch01')")
    parser.add_argument("--formats", default="epub,pdf", help="Target deliverable formats (default: epub,pdf)")
    parser.add_argument("--batch-size", type=int, default=15, help="Number of segments per translation batch")
    parser.add_argument("--concurrency", type=int, default=4, help="Number of concurrent translation workers")
    parser.add_argument("--window-size", type=int, default=250, help="Maximum segments per evaluation window")
    parser.add_argument("--max-refine-rounds", type=int, default=2, help="Maximum refinement attempts per chapter")
    parser.add_argument("--fail-fast", action="store_true", help="Abort immediately if any chapter fails quorum")
    parser.add_argument("--dry-run", action="store_true", help="Inspect and display planned stages without mutating state")
    parser.add_argument("--offline", action="store_true", help="Run translation and evaluation offline in mock mode")
    parser.add_argument(
        "--engine",
        choices=["workbuddy", "fallback"],
        default="workbuddy",
        help="Evaluation engine to use (default: workbuddy)",
    )
    parser.add_argument(
        "--source-lang",
        choices=["auto", "en", "zh"],
        default="auto",
        help="Source language ('auto', 'en', or 'zh', default: auto)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Target quality score threshold (default: 4.0, or 3.5 for literary/fiction)",
    )
    parser.add_argument(
        "--passes",
        type=int,
        default=1,
        help="Number of evaluation passes per chapter round (default: 1)",
    )
    parser.add_argument(
        "--translation-engine",
        choices=["agent", "cbc"],
        default="agent",
        help="Translation engine to use ('agent' for Primary Agent, 'cbc' for CBC CLI; default: agent)",
    )
    return parser.parse_args(argv)


def main():
    args = parse_args()
    res = orchestrate_run(
        book_path=args.book_path,
        slug=args.slug,
        run_dir=args.run_dir,
        chapters=args.chapters,
        formats=args.formats,
        batch_size=args.batch_size,
        concurrency=args.concurrency,
        window_size=args.window_size,
        max_refine_rounds=args.max_refine_rounds,
        fail_fast=args.fail_fast,
        dry_run=args.dry_run,
        offline=args.offline,
        engine=args.engine,
        source_lang=args.source_lang,
        threshold=args.threshold,
        passes=args.passes,
        translation_engine=args.translation_engine,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
