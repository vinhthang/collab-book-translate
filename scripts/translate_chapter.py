#!/usr/bin/env python3
"""
scripts/translate_chapter.py — Unified, generic book- and chapter-agnostic translation pipeline.
Translates chapter XML segments, preserves byte-for-byte image anchors and code blocks,
injects dynamic canonical terminology from book_record.json, enforces segment parity,
and produces clean final Markdown.
"""

import argparse
import concurrent.futures
import contextlib
try:
    import fcntl
except ImportError:
    fcntl = None  # Non-posix fallback
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.helper import (
    mask_math_formulas,
    parse_segments,
    strip_segments_to_final,
    unmask_math_formulas,
    validate_and_repair_gfm_table,
    validate_segment_parity,
)
from scripts.utils.language import detect_source_language
from scripts.utils.punctuation import normalize_cjk_punctuation, ensure_footnote_numbering
from scripts.utils.hanviet import transcribe_to_hanviet
from scripts.utils.terminology import (
    apply_dynamic_terminology,
    atomic_write_json,
    atomic_write_text,
    build_term_rules,
    deduplicate_parentheticals,
    extract_verbatim_terms,
    find_cbc_binary,
    interprocess_lock,
    load_book_record,
    read_json_state,
    snapshot_backup,
    StateFileCorruptError,
)

DEFAULT_TRANSLATE_MODEL: str = "hy3"


@contextlib.contextmanager
def _interprocess_lock(lock_path: Path | str, timeout: float = 30.0):
    """
    REQ-011: Acquires an exclusive inter-process lock on lock_path using fcntl.flock on POSIX.
    Blocks with polling up to timeout seconds. Raises TimeoutError on expiration.
    """
    lpath = Path(lock_path).resolve()
    lpath.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lpath), os.O_CREAT | os.O_RDWR, 0o666)
    start_time = time.time()
    acquired = False
    try:
        while True:
            try:
                if fcntl and hasattr(fcntl, "flock"):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                else:
                    acquired = True
                    break
            except (BlockingIOError, OSError):
                if time.time() - start_time >= timeout:
                    raise TimeoutError(f"Timed out after {timeout}s acquiring lock on {lpath}")
                time.sleep(0.01)
        yield
    finally:
        if acquired:
            try:
                if fcntl and hasattr(fcntl, "flock"):
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            os.close(fd)
        except OSError:
            pass


class TranslationCacheManager:
    """
    Thread-safe and process-safe, durable cache manager for translation segments (REQ-011, REQ-014).
    Synchronizes in-memory mutations with a threading.Lock, tracks dirty entries, and persists under
    an inter-process lock with a read-merge-write transaction on disk state.
    Maintains translation_cache.json strictly as Dict[str, str] and writes segment provenance
    metadata to companion sidecar <cache_path>.provenance.json.
    """
    def __init__(self, cache_path: Path | str):
        self.cache_path = Path(cache_path).resolve()
        self.lock_path = self.cache_path.with_name(f".{self.cache_path.name}.lock")
        self.sidecar_path = self.cache_path.with_name(f"{self.cache_path.name}.provenance.json")
        self._lock = threading.Lock()
        self._dirty: Set[str] = set()
        self._dirty_provenance: Set[str] = set()
        self._purged_keys: Set[str] = set()
        self._provenance: Dict[str, Dict[str, Any]] = {}
        self._data: Dict[str, str] = self._load()

    def migrate_positional_keys(self, segments: List[Dict[str, Any]]) -> int:
        """
        REQ-TXN-001 / AC-TXN-001-1:
        Migrates legacy positional numeric keys ('0', '1', ...) to canonical segment IDs ('S-XXXX')
        and marks numeric keys for deletion upon persistence.
        """
        migrated = 0
        with self._lock:
            for idx, seg in enumerate(segments):
                str_idx = str(idx)
                if str_idx in self._data:
                    val = self._data.get(str_idx)
                    seg_id = seg["id"]
                    if isinstance(val, str) and val.strip() and val != seg.get("original"):
                        if self._data.get(seg_id) is None or self._data.get(seg_id) == seg.get("original"):
                            self._data[seg_id] = val
                            self._dirty.add(seg_id)
                            migrated += 1
                            if str_idx in self._provenance and seg_id not in self._provenance:
                                self._provenance[seg_id] = self._provenance[str_idx]
                                self._dirty_provenance.add(seg_id)

            for k in list(self._data.keys()):
                if re.fullmatch(r"\d+", str(k)):
                    self._data.pop(k, None)
                    self._dirty.discard(k)
                    self._purged_keys.add(k)

            for k in list(self._provenance.keys()):
                if re.fullmatch(r"\d+", str(k)):
                    self._provenance.pop(k, None)
                    self._dirty_provenance.discard(k)
                    self._purged_keys.add(k)
        return migrated

    VALID_PROVENANCE_ENGINES = ("agent-offline", "agent-refine", "cbc", "custom")

    def _load(self) -> Dict[str, str]:
        raw = read_json_state(self.cache_path, {}, expected_type=dict)
        data = {str(k): str(v) for k, v in raw.items()}

        raw_prov = read_json_state(self.sidecar_path, {}, expected_type=dict)
        self._provenance = raw_prov

        return data

    def get_valid(self, seg_id: str, orig_text: str) -> Optional[str]:
        with self._lock:
            hit_key: Optional[str] = None
            for k in (str(seg_id), str(orig_text)):
                if k in self._data:
                    hit_key = k
                    break
            if hit_key is None:
                return None
            val = self._data[hit_key]
            if val == orig_text:
                prov = self._provenance.get(str(seg_id)) or self._provenance.get(hit_key)
                if not isinstance(prov, dict) or prov.get("engine") not in self.VALID_PROVENANCE_ENGINES:
                    return None
            return val

    def get(self, key: Any) -> Optional[str]:
        with self._lock:
            return self._data.get(str(key))

    def get_any(self, keys: List[Any]) -> Optional[str]:
        with self._lock:
            for k in keys:
                if str(k) in self._data:
                    return self._data[str(k)]
            return None

    def get_provenance(self, key: Any) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._provenance.get(str(key))

    def set_provenance(self, key: Any, prov: Dict[str, Any]) -> None:
        with self._lock:
            self._provenance[str(key)] = prov
            self._dirty_provenance.add(str(key))

    def set(self, key: Any, value: str, provenance: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            self._data[str(key)] = str(value)
            self._dirty.add(str(key))
            if provenance:
                self._provenance[str(key)] = provenance
                self._dirty_provenance.add(str(key))

    def set_and_save(self, key: Any, value: str, provenance: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            self._data[str(key)] = str(value)
            self._dirty.add(str(key))
            if provenance:
                self._provenance[str(key)] = provenance
                self._dirty_provenance.add(str(key))
            self._persist_locked()

    def update_and_save(self, updates: Dict[str, Any]) -> None:
        with self._lock:
            for k, v in updates.items():
                self._data[str(k)] = str(v)
                self._dirty.add(str(k))
            self._persist_locked()

    def save(self, chapter_dir: Optional[Path | str] = None) -> None:
        with self._lock:
            self._persist_locked()

    def _persist_locked(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tid = threading.get_ident()
        pid = os.getpid()

        with interprocess_lock(self.lock_path, timeout=30.0):
            # 1. Read latest on-disk state to merge dirty keys
            raw = read_json_state(self.cache_path, {}, expected_type=dict)
            disk_data: Dict[str, str] = {str(k): str(v) for k, v in raw.items()}

            # Merge only dirty keys
            for k in self._dirty:
                if k in self._data:
                    disk_data[k] = self._data[k]

            # Purge deleted positional keys
            for k in self._purged_keys:
                disk_data.pop(k, None)

            # Update in-memory view to include additions from other processes
            self._data = disk_data

            # Atomically replace translation_cache.json
            fd, tmp = tempfile.mkstemp(
                dir=str(self.cache_path.parent),
                prefix=f".{self.cache_path.name}.tmp.{pid}.{tid}.",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(disk_data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.cache_path)
                self._dirty.clear()
            except Exception:
                if os.path.exists(tmp):
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                raise

            # Persist sidecar provenance if dirty or purged
            if self._dirty_provenance or self._purged_keys:
                raw_p = read_json_state(self.sidecar_path, {}, expected_type=dict)
                disk_prov: Dict[str, Dict[str, Any]] = raw_p

                for k in self._dirty_provenance:
                    if k in self._provenance:
                        disk_prov[k] = self._provenance[k]

                for k in self._purged_keys:
                    disk_prov.pop(k, None)

                self._provenance = disk_prov

                fd_p, tmp_p = tempfile.mkstemp(
                    dir=str(self.cache_path.parent),
                    prefix=f".{self.sidecar_path.name}.tmp.{pid}.{tid}.",
                    suffix=".tmp",
                )
                try:
                    with os.fdopen(fd_p, "w", encoding="utf-8") as f:
                        json.dump(disk_prov, f, ensure_ascii=False, indent=2)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp_p, self.sidecar_path)
                    self._dirty_provenance.clear()
                except Exception:
                    if os.path.exists(tmp_p):
                        try:
                            os.unlink(tmp_p)
                        except OSError:
                            pass
                    raise

            self._purged_keys.clear()

    @property
    def data(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._data)


def append_trunk_audit_record(chapter_dir: Path | str, record: Dict[str, Any]) -> None:
    """
    REQ-014 / AC-014.2:
    Atomically appends a structured JSONL record to runs/<slug>/<ch_id>/audit_trail.jsonl
    under an inter-process file lock.
    """
    ch_path = Path(chapter_dir).resolve()
    ch_path.mkdir(parents=True, exist_ok=True)
    audit_file = ch_path / "audit_trail.jsonl"
    lock_file = ch_path / ".audit_trail.jsonl.lock"

    with _interprocess_lock(lock_file, timeout=30.0):
        with open(audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())


def load_cache(cache_path: Path | str) -> Dict[str, Any]:
    """Loads translation cache dict from disk."""
    return TranslationCacheManager(cache_path).data


def save_cache(cache_path: Path | str, data: Dict[str, Any]) -> None:
    """Saves translation cache dict atomically with fsync and os.replace."""
    mgr = TranslationCacheManager(cache_path)
    mgr.update_and_save(data)


def build_batch_with_context(
    segments: List[Any],
    start_idx: int,
    batch_size: int,
    context_count: int = 2,
) -> Tuple[List[Any], List[Any]]:
    """
    AC-ARCH-6.2: Returns (batch_segments, context_segments).
    context_segments contains up to context_count preceding segments before start_idx
    to preserve narrative and pronoun continuity.
    """
    ctx_start = max(0, start_idx - context_count)
    context_segs = segments[ctx_start:start_idx]
    batch_segs = segments[start_idx : start_idx + batch_size]
    return batch_segs, context_segs


def normalize_chapter_id(ch: str) -> str:
    """Normalizes chapter identifier into canonical form (e.g. '1' -> 'ch01', 'ch1' -> 'ch01', '02' -> 'ch02')."""
    s = ch.strip()
    if s.lower().startswith("ch"):
        num_part = s[2:]
        if num_part.isdigit():
            return f"ch{int(num_part):02d}"
        return s
    if s.isdigit():
        return f"ch{int(s):02d}"
    return s


def ensure_xml_segments(text: str) -> str:
    """Wraps raw Markdown text into XML segments if not already segmented."""
    if not text or not text.strip():
        return ""
    if "<segment " in text and "</segment>" in text:
        return text

    blocks: List[Tuple[str, str]] = []
    lines = text.splitlines()
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue

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

        if line.strip().startswith("#"):
            blocks.append(("heading", line.strip()))
            i += 1
            continue

        fig_match = re.match(r'^\s*!\[.*?\]\(.*?\)\s*$', line)
        if fig_match:
            blocks.append(("figure", line.strip()))
            i += 1
            continue

        para_lines = [line]
        i += 1
        while i < n:
            curr = lines[i]
            if not curr.strip():
                break
            if curr.strip().startswith("```") or curr.strip().startswith("#") or re.match(r'^\s*!\[.*?\]\(.*?\)\s*$', curr):
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


def _format_pronoun_instructions(pronouns: Any) -> List[str]:
    if not isinstance(pronouns, list) or not pronouns:
        return []
    lines = []
    for p in pronouns[:25]:
        if not isinstance(p, dict):
            continue
        if "pair" in p and isinstance(p["pair"], list) and len(p["pair"]) == 2:
            a, b = p["pair"][0], p["pair"][1]
            a_to_b = p.get("a_to_b", "")
            b_to_a = p.get("b_to_a", "")
            basis = p.get("basis", "")
            changes = p.get("changes", [])
            item_str = f"{a} <-> {b}: {a} gọi {b} là \"{a_to_b}\", {b} gọi {a} là \"{b_to_a}\""
            if basis:
                item_str += f" (quan hệ: {basis})"
            if changes:
                item_str += f" [thay đổi: {json.dumps(changes, ensure_ascii=False)}]"
            lines.append(item_str)
        elif p.get("source") and p.get("target"):
            lines.append(f"{p.get('source')}: {p.get('target')}")
    return lines


DEFAULT_TRANSLATE_MODEL = "hy3"
MAX_CBC_SUB_BATCH = 15


def _build_classical_persona(book_record: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """Dynamically builds classical persona descriptor and example names from book_record (REQ-COR-004)."""
    if not isinstance(book_record, dict):
        return "", ""
    rec = book_record.get("book_record", book_record)
    if not isinstance(rec, dict):
        rec = {}
    brief = book_record.get("brief") or rec.get("brief")
    brief_dict = brief if isinstance(brief, dict) else {}

    title = str(rec.get("title") or brief_dict.get("title") or "").strip()
    author = str(rec.get("author") or brief_dict.get("author") or "").strip()
    commentator = str(rec.get("commentator") or brief_dict.get("commentator") or "").strip()

    desc_items = []
    if title:
        desc_items.append(title)
    if author:
        desc_items.append(f"của {author}")
    work_str = " ".join(desc_items) if desc_items else ""
    if commentator:
        work_str = f"{work_str}, {commentator} chú" if work_str else f"{commentator} chú"
    desc_suffix = f" ({work_str})" if work_str else ""

    names = rec.get("names") or rec.get("name_table") or book_record.get("name_table") or []
    ex_pairs: List[str] = []
    if isinstance(names, list):
        for n in names:
            if isinstance(n, dict):
                tgt = n.get("target")
                if tgt and str(tgt).strip():
                    ex_pairs.append(str(tgt).strip())
            elif isinstance(n, str) and n.strip():
                ex_pairs.append(n.strip())
            if len(ex_pairs) >= 3:
                break
    elif isinstance(names, dict):
        for k, v in names.items():
            tgt = v.get("target") if isinstance(v, dict) else v
            if tgt and str(tgt).strip():
                ex_pairs.append(str(tgt).strip())
            if len(ex_pairs) >= 3:
                break

    ex_suffix = f" ({', '.join(ex_pairs)}...)" if ex_pairs else ""
    return desc_suffix, ex_suffix


def translate_batch_cbc(
    batch_segs: List[Dict[str, Any]],
    book_record: Optional[Dict[str, Any]] = None,
    timeout: int = 180,
    source_lang: str = "en",
    context_segs: Optional[List[Dict[str, Any]]] = None,
    seg_issues_map: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    cbc_bin: Optional[str] = None,
) -> Dict[str, str]:
    """
    Translates a batch of XML segments using cbc CLI with deepseek-v4.1-flash.
    Returns mapping of seg_id -> translated_text.
    Includes preceding segments tagged as <context_before> for narrative continuity (AC-ARCH-6.2).
    """
    cbc_bin = cbc_bin or find_cbc_binary()
    if not cbc_bin:
        raise RuntimeError("cbc binary not found on system.")

    max_sub_batch_count = 10 if source_lang == "zh" else MAX_CBC_SUB_BATCH
    max_sub_batch_chars = 2800 if source_lang == "zh" else 6000

    needs_split = False
    if len(batch_segs) > max_sub_batch_count:
        needs_split = True
    elif len(batch_segs) > 1 and sum(len(s.get("original", "")) for s in batch_segs) > max_sub_batch_chars:
        needs_split = True

    if needs_split:
        sub_batches: List[List[Dict[str, Any]]] = []
        cur_batch: List[Dict[str, Any]] = []
        cur_chars = 0
        for seg in batch_segs:
            s_len = len(seg.get("original", ""))
            if cur_batch and (len(cur_batch) >= max_sub_batch_count or (cur_chars + s_len > max_sub_batch_chars)):
                sub_batches.append(cur_batch)
                cur_batch = [seg]
                cur_chars = s_len
            else:
                cur_batch.append(seg)
                cur_chars += s_len
        if cur_batch:
            sub_batches.append(cur_batch)

        sub_results: Dict[str, str] = {}
        for idx, sub_batch in enumerate(sub_batches):
            sub_ctx = context_segs if idx == 0 else sub_batches[idx - 1][-2:]
            sub_map = translate_batch_cbc(
                sub_batch,
                book_record=book_record,
                timeout=timeout,
                source_lang=source_lang,
                context_segs=sub_ctx,
                seg_issues_map=seg_issues_map,
                cbc_bin=cbc_bin,
            )
            sub_results.update(sub_map)
        return sub_results

    if source_lang == "zh":
        is_classical = False
        if book_record is None:
            is_classical = True
        else:
            brief = book_record.get("brief", {})
            genre = (brief.get("genre") or "").lower() if isinstance(brief, dict) else ""
            lexicon = str(book_record.get("domain_lexicon") or "").lower()
            if any(kw in genre for kw in ("chronicle", "sử", "lịch sử", "cổ điển", "classical")) or any(kw in lexicon for kw in ("chronicle", "history")):
                is_classical = True

        if is_classical:
            desc_suffix, ex_suffix = _build_classical_persona(book_record)
            system_instructions = [
                f"Bạn là một học giả dịch thuật Hán-Việt xuất sắc, chuyên dịch chính sử cổ điển Trung Quốc{desc_suffix} sang văn xuôi lịch sử Việt Nam trang trọng.",
                "Nhiệm vụ: Dịch các phân đoạn văn bản chữ Hán (Hanzi) sau đây sang tiếng Việt trang trọng, chuẩn mực theo phong cách chính sử.",
                "Mỗi phân đoạn gồm:",
                "1. <original>: Nguyên văn chữ Hán cổ điển.",
                "2. <hanviet>: Phiên âm Hán-Việt tương ứng (làm chuẩn âm đọc tên người, địa danh, chức quan, niên hiệu).",
                "Quy tắc bắt buộc:",
                f"1. BẮT BUỘC dùng âm Hán-Việt chuẩn cho toàn bộ tên nhân vật, địa danh, chức quan, niên hiệu{ex_suffix}.",
                "2. Tuyệt đối KHÔNG dùng pinyin tiếng Anh (cấm viết Wan, Guisi, Ruxukou, Ba, Lu Ziba...).",
                "3. Tuyệt đối KHÔNG dịch thô, convert máy móc sáo rỗng (như 'bất quá', 'đốn thời', 'người đăng ký chính', 'bảo vệ Wan', 'phất thủ', 'hạ nhất khắc', 'sắc mặt ngưng trọng', 'chỉ kiến', 'nhãn mâu', 'khóe miệng co giật', 'tâm đắc').",
                "4. Diễn giải sang văn xuôi tiếng Việt trang trọng, khúc chiết, uy nghiêm theo đúng phong cách chính sử.",
                "5. Giữ nguyên 100% từng byte mã nguồn trong code blocks (```...```) và thẻ ảnh markdown (![Figure](images/...)). Không dịch bên trong code block hay figure tag.",
                "6. Bảo toàn nguyên vẹn cấu trúc XML của từng phân đoạn: mỗi phân đoạn gồm thẻ <segment id=\"...\" type=\"...\"> chứa <original>...</original>, <hanviet>...</hanviet> và <translate>[bản dịch tiếng Việt]</translate>.",
                "7. Phân đoạn trong thẻ <context_before>...</context_before> chỉ dùng làm ngữ cảnh tham khảo (read-only context), KHÔNG được dịch và KHÔNG được xuất lại trong kết quả.",
                "8. Không đưa ra bất kỳ lời chào, giải thích, chú thích ngoài các thẻ <segment>.",
            ]
        else:
            system_instructions = [
                "Bạn là một dịch giả Hán-Việt chuyên nghiệp xuất sắc, chuyển ngữ văn học và phi hư cấu hiện đại Trung Quốc sang tiếng Việt tự nhiên, chuẩn mực.",
                "Nhiệm vụ: Dịch các phân đoạn văn bản chữ Hán (Hanzi) sau đây sang tiếng Việt tự nhiên, chuẩn mực, giàu cảm xúc và bảo đảm tính chính xác học thuật/văn học.",
                "Mỗi phân đoạn gồm:",
                "1. <original>: Nguyên văn chữ Hán.",
                "2. <hanviet>: Phiên âm Hán-Việt tham khảo.",
                "Quy tắc bắt buộc:",
                "1. BẮT BUỘC dùng âm Hán-Việt chuẩn cho tên nhân vật, địa danh, thuật ngữ gốc Hán.",
                "2. Tuyệt đối KHÔNG dùng pinyin tiếng Anh cho tên nhân vật, địa danh Trung Quốc.",
                "3. Tuyệt đối KHÔNG dịch thô, convert máy móc sáo rỗng (như 'bất quá', 'đốn thời', 'phất thủ', 'hạ nhất khắc', 'sắc mặt ngưng trọng', 'chỉ kiến', 'nhãn mâu', 'khóe miệng co giật', 'tâm đắc').",
                "4. Diễn giải sang tiếng Việt hiện đại, trôi chảy, tự nhiên theo đúng thể loại tác phẩm. Lời thoại tự nhiên, gãy gọn, giàu tính cách nhân vật (đại từ khẩu ngữ như '老子' dịch thành 'ông đây' hoặc đại từ phù hợp bối cảnh ngang tàng; các biểu đạt khẩu ngữ như '死' dịch tự nhiên thành 'cười chết', 'tức chết', 'đói chết'). Tránh lạm dụng hư từ dịch thuật sáo rỗng ('của', 'rằng', 'vô cùng').",
                "5. Giữ nguyên 100% từng byte mã nguồn trong code blocks (```...```) và thẻ ảnh markdown (![Figure](images/...)). Không dịch bên trong code block hay figure tag.",
                "6. Bảo toàn nguyên vẹn cấu trúc XML của từng phân đoạn: mỗi phân đoạn gồm thẻ <segment id=\"...\" type=\"...\"> chứa <original>...</original>, <hanviet>...</hanviet> và <translate>[bản dịch tiếng Việt]</translate>.",
                "7. Phân đoạn trong thẻ <context_before>...</context_before> chỉ dùng làm ngữ cảnh tham khảo (read-only context), KHÔNG được dịch và KHÔNG được xuất lại trong kết quả.",
                "8. Không đưa ra bất kỳ lời chào, giải thích, chú thích ngoài các thẻ <segment>.",
            ]
        if book_record:
            orig_full_text = " ".join(seg.get("original", "") for seg in batch_segs)
            br = book_record.get("book_record", book_record)
            names = br.get("names") or br.get("name_table") or book_record.get("name_table")
            if isinstance(names, list) and names:
                matched_n = [n for n in names if isinstance(n, dict) and n.get("source") and n.get("target") and n["source"] in orig_full_text]
                other_n = [n for n in names if isinstance(n, dict) and n.get("source") and n.get("target") and n["source"] not in orig_full_text]
                name_items = (matched_n + other_n)[:50]
                name_pairs = [f"{n.get('source')}: {n.get('target')}" for n in name_items]
                if name_pairs:
                    system_instructions.append("Bảng tên nhân vật & địa danh chuẩn hóa:\n" + "\n".join(f"- {np}" for np in name_pairs))

            terms = br.get("terms") or br.get("term_table") or book_record.get("term_table")
            if isinstance(terms, list) and terms:
                matched_t = [t for t in terms if isinstance(t, dict) and t.get("source") and t.get("target") and t["source"] in orig_full_text]
                other_t = [t for t in terms if isinstance(t, dict) and t.get("source") and t.get("target") and t["source"] not in orig_full_text]
                term_items = (matched_t + other_t)[:60]
                term_pairs = [f"{t.get('source')}: {t.get('target')}" for t in term_items]
                if term_pairs:
                    system_instructions.append("Thuật ngữ chuẩn hóa:\n" + "\n".join(f"- {tp}" for tp in term_pairs))
            elif isinstance(terms, dict) and terms:
                matched_t = [f"{k}: {v}" for k, v in terms.items() if k in orig_full_text]
                other_t = [f"{k}: {v}" for k, v in terms.items() if k not in orig_full_text]
                term_pairs = (matched_t + other_t)[:60]
                system_instructions.append("Thuật ngữ chuẩn hóa:\n" + "\n".join(f"- {tp}" for tp in term_pairs))

            pronouns = br.get("pronouns") or br.get("pronoun_table") or book_record.get("pronoun_table")
            formatted_p = _format_pronoun_instructions(pronouns)
            if formatted_p:
                system_instructions.append("Bảng đại từ xưng hô:\n" + "\n".join(f"- {pp}" for pp in formatted_p))
    else:
        system_instructions = [
            "Bạn là một dịch giả tiếng Việt chuyên nghiệp xuất sắc.",
            "Nhiệm vụ: Dịch các phân đoạn văn bản tiếng Anh sau đây sang tiếng Việt tự nhiên, chuẩn mực, giàu cảm xúc và bảo đảm tính chính xác học thuật/khoa học/văn học.",
            "Quy tắc bắt buộc:",
            "1. Dịch chuẩn xác sang tiếng Việt trong thẻ <translate>. Tuyệt đối KHÔNG giữ nguyên tiếng Anh trong thẻ <translate>.",
            "2. Giữ nguyên 100% từng byte mã nguồn trong code blocks (```...```) và thẻ ảnh markdown (![Figure](images/...)). Không dịch bên trong code block hay figure tag.",
            "3. Bảo toàn nguyên vẹn cấu trúc XML của từng phân đoạn: mỗi phân đoạn gồm thẻ <segment id=\"...\" type=\"...\"> chứa <original>...</original> và <translate>[bản dịch tiếng Việt]</translate>.",
            "4. Phân đoạn trong thẻ <context_before>...</context_before> chỉ dùng làm ngữ cảnh tham khảo (read-only context), KHÔNG được dịch và KHÔNG được xuất lại trong kết quả.",
            "5. Không đưa ra bất kỳ lời chào, giải thích, chú thích ngoài các thẻ <segment>.",
        ]

        if book_record:
            brief = book_record.get("brief", {})
            if isinstance(brief, dict):
                genre = brief.get("genre")
                if genre:
                    system_instructions.append(f"Thể loại: {genre}")
                register = brief.get("register")
                if register:
                    system_instructions.append(f"Văn phong: {register}")
                audience = brief.get("audience")
                if audience:
                    system_instructions.append(f"Đối tượng độc giả: {audience}")
                style_rules = brief.get("style_rules", [])
                if style_rules:
                    system_instructions.append("Quy tắc phong cách & đại từ:")
                    for rule in style_rules[:10]:
                        system_instructions.append(f"- {rule}")

            # Canonical terms & names
            br = book_record.get("book_record", book_record)
            names = br.get("names") or br.get("name_table") or book_record.get("name_table")
            if names:
                name_pairs = [f"{n.get('source')}: {n.get('target')}" for n in names if isinstance(n, dict) and n.get('source') and n.get('target')]
                if name_pairs:
                    system_instructions.append("Bảng tên nhân vật & địa danh chuẩn hóa (phiên âm Hán-Việt):\n" + "\n".join(f"- {np}" for np in name_pairs[:50]))
                    system_instructions.append(
                        "QUY CHUẨN DỊCH TÊN RIÊNG TIẾNG TRUNG: Toàn bộ tên riêng nhân vật Trung Quốc (viết dưới dạng Pinyin tiếng Anh), địa danh và thuật ngữ xuất phát từ Trung Quốc BẮT BUỘC phải dịch/phiên âm sang âm Hán-Việt chuẩn mực theo danh mục tên chuẩn hóa."
                    )

            terms = br.get("terms") or book_record.get("term_table")
            if isinstance(terms, list) and terms:
                term_pairs = [f"{t.get('source')}: {t.get('target')}" for t in terms[:30] if isinstance(t, dict) and t.get('source') and t.get('target')]
                if term_pairs:
                    system_instructions.append("Thuật ngữ chuẩn hóa:\n" + "\n".join(f"- {tp}" for tp in term_pairs))
            elif isinstance(terms, dict) and terms:
                term_pairs = [f"{k}: {v}" for k, v in list(terms.items())[:30]]
                system_instructions.append("Thuật ngữ chuẩn hóa:\n" + "\n".join(f"- {tp}" for tp in term_pairs))

            verbatim_terms = extract_verbatim_terms(book_record)
            if verbatim_terms:
                system_instructions.append(
                    f"QUY CHUẨN THUẬT NGỮ BẮT BUỘC: Giữ nguyên 100% tiếng Anh cho các danh từ riêng, thương hiệu và chuẩn công nghệ sau (không dịch, không chèn chú giải làm biến dạng): {', '.join(verbatim_terms[:40])}"
                )
            system_instructions.append(
                "QUY CHUẨN TÀI LIỆU THAM KHẢO (BIBLIOGRAPHY / REFERENCES): Đối với các trích dẫn tài liệu tham khảo ([1], [2], ...), BẮT BUỘC giữ nguyên 100% tiếng Anh cho tiêu đề bài báo/sách trong ngoặc kép (“...”, \"...\"), tên tác giả, tên nhà xuất bản, hội nghị, URL và domain. Tuyệt đối không dịch tên công trình khoa học được trích dẫn."
            )

            pronouns = br.get("pronouns") or br.get("pronoun_table") or book_record.get("pronouns") or book_record.get("pronoun_table")
            formatted_p = _format_pronoun_instructions(pronouns)
            if formatted_p:
                system_instructions.append("Bảng đại từ xưng hô:\n" + "\n".join(f"- {pp}" for pp in formatted_p))

    prompt_segments = []
    if context_segs:
        for cs in context_segs:
            c_orig = cs.get("original", "")
            seg_xml = (
                f'<context_before><segment id="{cs["id"]}" context="true">\n'
                f'<original>\n{c_orig}\n</original>\n'
                f'</segment></context_before>'
            )
            prompt_segments.append(seg_xml)

    for s in batch_segs:
        s_id = str(s["id"])
        orig = s.get("original", "")
        issues_xml = ""
        if seg_issues_map and s_id in seg_issues_map:
            issues = seg_issues_map[s_id]
            lines = []
            for iss in issues:
                sev = iss.get("severity", "issue")
                prob = iss.get("problem", "")
                sug = iss.get("suggestion", "")
                sug_part = f" ({sug})" if sug else ""
                lines.append(f"- [{sev}] {prob}{sug_part}")
            if lines:
                issues_xml = "\n<previous_issues>\n" + "\n".join(lines) + "\n</previous_issues>"

        if source_lang == "zh":
            hv_text = transcribe_to_hanviet(orig, book_record=book_record)
            seg_xml = (
                f'<segment id="{s["id"]}" type="{s.get("type", "paragraph")}">\n'
                f'<original>\n{orig}\n</original>\n'
                f'<hanviet>\n{hv_text}\n</hanviet>{issues_xml}\n'
                f'</segment>'
            )
        else:
            seg_xml = (
                f'<segment id="{s["id"]}" type="{s.get("type", "paragraph")}">\n'
                f'<original>\n{orig}\n</original>{issues_xml}\n'
                f'</segment>'
            )
        prompt_segments.append(seg_xml)

    full_prompt = (
        "\n".join(system_instructions)
        + "\n\nCác phân đoạn cần dịch:\n\n"
        + "\n\n".join(prompt_segments)
        + "\n\nHãy xuất ra đúng cấu trúc XML với thẻ <translate> cho từng phân đoạn:"
    )

    cmd = [cbc_bin, "-p", "--model", DEFAULT_TRANSLATE_MODEL, "--tools", "", "--no-session-persistence"]
    raw_output = ""
    for attempt in range(5):
        try:
            proc = subprocess.run(
                cmd,
                input=full_prompt,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            sys.stderr.write(f"Warning: cbc invocation timed out after {timeout}s on attempt {attempt+1}.\n")
            if attempt < 4:
                time.sleep(3 * (attempt + 1) + random.uniform(1.0, 3.0))
                continue
            raise

        if proc.returncode != 0:
            if attempt < 4:
                time.sleep(2 * (attempt + 1) + random.uniform(1.0, 2.0))
                continue
            raise RuntimeError(f"cbc invocation failed (exit {proc.returncode}): {proc.stderr}")

        raw_output = proc.stdout.strip() if proc.stdout else ""
        err_output = proc.stderr.strip() if proc.stderr else ""
        if "429" in err_output.lower() or "too many requests" in err_output.lower() or "429" in raw_output.lower() or "too many requests" in raw_output.lower():
            sys.stderr.write(f"HTTP 429 detected from cbc: {err_output or raw_output}\n")
            raise RuntimeError(f"HTTP 429 detected from cbc: {err_output or raw_output}")

        if not raw_output:
            if attempt < 4:
                sys.stderr.write(f"Warning: cbc returned empty stdout on attempt {attempt+1} (stderr: {err_output[:100]}). Retrying...\n")
                time.sleep(4 * (attempt + 1) + random.uniform(1.0, 3.0))
                continue

        # Strip any echoed <context_before> blocks
        cleaned_output = re.sub(r'<context_before>[\s\S]*?</context_before>', '', raw_output)
        cleaned_output = re.sub(r'<segment\b[^>]*context="true"[^>]*>[\s\S]*?</segment>', '', cleaned_output)

        parsed = parse_segments(cleaned_output)
        if not parsed and attempt < 4:
            sys.stderr.write(f"Warning: cbc returned non-parseable output on attempt {attempt+1}. Retrying...\n")
            time.sleep(3 * (attempt + 1) + random.uniform(1.0, 3.0))
            continue
        break

    if 'cleaned_output' not in locals() or not cleaned_output:
        cleaned_output = re.sub(r'<context_before>[\s\S]*?</context_before>', '', raw_output)
        cleaned_output = re.sub(r'<segment\b[^>]*context="true"[^>]*>[\s\S]*?</segment>', '', cleaned_output)
    if 'parsed' not in locals() or not parsed:
        parsed = parse_segments(cleaned_output)

    context_ids = {cs["id"] for cs in (context_segs or [])}
    parsed_map = {
        p["id"]: (normalize_cjk_punctuation(p["translate"]) if source_lang == "zh" and isinstance(p.get("translate"), str) else p["translate"])
        for p in parsed
        if "id" in p and p.get("translate") is not None and p["id"] not in context_ids
    }
    return parsed_map


def retry_single_segment_cbc(
    seg: Dict[str, Any],
    book_record: Optional[Dict[str, Any]] = None,
    max_attempts: int = 3,
) -> str:
    """Retries translating a single Chinese segment via cbc with exponential backoff, falling back to Hán-Việt."""
    orig = seg.get("original", "")
    cbc_bin = find_cbc_binary()
    if cbc_bin:
        for attempt in range(1, max_attempts + 1):
            try:
                single_map = translate_batch_cbc([seg], book_record=book_record, timeout=120, source_lang="zh")
                if seg["id"] in single_map and single_map[seg["id"]]:
                    return single_map[seg["id"]]
            except Exception as e:
                sys.stderr.write(f"Warning: cbc single segment {seg['id']} attempt {attempt} failed: {e}\n")
                if attempt < max_attempts:
                    time.sleep(attempt * 2)

    # Ultimate zero-MT fallback: deterministic Hán-Việt transcription (REQ-2STG-3.2)
    return transcribe_to_hanviet(orig, book_record=book_record)


def translate_batch(
    batch_segs: List[Dict[str, Any]],
    translate_fn: Optional[Any] = None,
    offline: bool = False,
    book_record: Optional[Dict[str, Any]] = None,
    source_lang: str = "en",
    context_segs: Optional[List[Dict[str, Any]]] = None,
    seg_issues_map: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, str]:
    """
    Translates a batch of segments preserving XML envelopes.
    Returns mapping of seg_id -> translated_text.
    Uses cbc (deepseek-v4.1-flash) as primary provider when available.
    Enforces strict Zero-Machine-Translation Fallback across all languages (AC-ARCH-7.3).
    """
    if not batch_segs:
        return {}

    if offline:
        return {s["id"]: s.get("original", "") for s in batch_segs}

    # If explicit custom translate_fn provided (e.g. mock in unit tests)
    if translate_fn is not None:
        prompt_blocks = []
        if context_segs:
            for cs in context_segs:
                prompt_blocks.append(
                    f'<context_before><segment id="{cs["id"]}" context="true">\n<original>\n{cs.get("original", "")}\n</original>\n</segment></context_before>'
                )
        for s in batch_segs:
            s_id = str(s["id"])
            issues_xml = ""
            if seg_issues_map and s_id in seg_issues_map:
                issues = seg_issues_map[s_id]
                lines = []
                for iss in issues:
                    sev = iss.get("severity", "issue")
                    prob = iss.get("problem", "")
                    sug = iss.get("suggestion", "")
                    sug_part = f" ({sug})" if sug else ""
                    lines.append(f"- [{sev}] {prob}{sug_part}")
                if lines:
                    issues_xml = "\n<previous_issues>\n" + "\n".join(lines) + "\n</previous_issues>"
            prompt_blocks.append(
                f'<segment id="{s["id"]}" type="{s.get("type", "paragraph")}">\n<original>\n{s.get("original", "")}\n</original>{issues_xml}\n</segment>'
            )
        batch_prompt = "\n\n".join(prompt_blocks)
        try:
            response_text = translate_fn(batch_prompt)
            clean_resp = re.sub(r'<context_before>[\s\S]*?</context_before>', '', response_text)
            clean_resp = re.sub(r'<segment\b[^>]*context="true"[^>]*>[\s\S]*?</segment>', '', clean_resp)
            parsed = parse_segments(clean_resp)
            ctx_ids = {cs["id"] for cs in (context_segs or [])}
            parsed_map = {
                p["id"]: (normalize_cjk_punctuation(p["translate"]) if source_lang == "zh" and isinstance(p.get("translate"), str) else p["translate"])
                for p in parsed
                if "id" in p and p.get("translate") is not None and p["id"] not in ctx_ids
            }
            if all(s["id"] in parsed_map for s in batch_segs):
                return {s["id"]: parsed_map[s["id"]] for s in batch_segs}
        except Exception:
            pass

        # Fallback 1-by-1 with custom translate_fn
        results: Dict[str, str] = {}
        for s in batch_segs:
            orig = s.get("original", "")
            t_res = translate_fn(orig)
            if source_lang == "zh" and isinstance(t_res, str):
                t_res = normalize_cjk_punctuation(t_res)
            results[s["id"]] = t_res
        return results

    # Primary translation engine: cbc with deepseek-v4.1-flash
    cbc_bin = find_cbc_binary()
    if cbc_bin:
        try:
            parsed_map = translate_batch_cbc(
                batch_segs,
                book_record=book_record,
                source_lang=source_lang,
                context_segs=context_segs,
                seg_issues_map=seg_issues_map,
            )
            if all(s["id"] in parsed_map and parsed_map[s["id"]] for s in batch_segs):
                return {s["id"]: parsed_map[s["id"]] for s in batch_segs}

            # If some segments were missing in the batch output, retry missing ones
            results = dict(parsed_map)
            missing_segs = [s for s in batch_segs if s["id"] not in results or not results[s["id"]]]
            if len(missing_segs) > 1:
                try:
                    batch_retry_map = translate_batch_cbc(
                        missing_segs,
                        book_record=book_record,
                        source_lang=source_lang,
                        timeout=180,
                    )
                    for ms_id, ms_val in batch_retry_map.items():
                        if ms_val:
                            results[ms_id] = ms_val
                except Exception as e_batch_retry:
                    sys.stderr.write(f"Warning: cbc batch retry for {len(missing_segs)} missing segs failed: {e_batch_retry}\n")
                missing_segs = [s for s in batch_segs if s["id"] not in results or not results[s["id"]]]

            for ms in missing_segs:
                if source_lang == "zh":
                    results[ms["id"]] = retry_single_segment_cbc(ms, book_record=book_record)
                else:
                    for retry_att in range(1, 4):
                        try:
                            single_map = translate_batch_cbc([ms], book_record=book_record, source_lang=source_lang, timeout=120)
                            if ms["id"] in single_map and single_map[ms["id"]]:
                                results[ms["id"]] = single_map[ms["id"]]
                                break
                        except Exception as e_retry:
                            sys.stderr.write(f"Warning: cbc single retry for {ms['id']} attempt {retry_att} failed: {e_retry}\n")
                            if retry_att < 3:
                                time.sleep(2 * retry_att)
            if all(s["id"] in results and results[s["id"]] for s in batch_segs):
                return {s["id"]: results[s["id"]] for s in batch_segs}
        except Exception as e:
            sys.stderr.write(f"Warning: cbc batch translation failed: {e}. Retrying individual segments via cbc.\n")
            results = {}
            for seg in batch_segs:
                if source_lang == "zh":
                    results[seg["id"]] = retry_single_segment_cbc(seg, book_record=book_record)
                else:
                    for retry_att in range(1, 4):
                        try:
                            single_map = translate_batch_cbc([seg], book_record=book_record, source_lang=source_lang, timeout=120)
                            if seg["id"] in single_map and single_map[seg["id"]]:
                                results[seg["id"]] = single_map[seg["id"]]
                                break
                        except Exception as e_retry:
                            sys.stderr.write(f"Warning: cbc single retry for {seg['id']} attempt {retry_att} failed: {e_retry}\n")
                            if retry_att < 3:
                                time.sleep(2 * retry_att)
            if all(s["id"] in results and results[s["id"]] for s in batch_segs):
                return {s["id"]: results[s["id"]] for s in batch_segs}

    # Strict Zero-Fallback Governance for Chinese (REQ-2STG-3): never call translate_text_online
    if source_lang == "zh":
        results = {}
        for seg in batch_segs:
            results[seg["id"]] = retry_single_segment_cbc(seg, book_record=book_record)
        return results

    if offline:
        return {s["id"]: s.get("original", "") for s in batch_segs}

    # AC-ARCH-7.3 Zero-MT Fallback: raise RuntimeError in live mode rather than falling back to translate_text_online
    raise RuntimeError(
        f"Zero-MT Fallback: Live translation for {len(batch_segs)} segments failed. "
        "cbc engine unavailable or failed across all retries, and offline=False."
    )


def translate_chapter(
    run_dir: str | Path,
    chapter: str,
    round_num: int = 1,
    source: Optional[str | Path] = None,
    output: Optional[str | Path] = None,
    final: Optional[str | Path] = None,
    book_record: Optional[str | Path] = None,
    cache_file: Optional[str | Path] = None,
    offline: bool = False,
    batch_size: int = 15,
    concurrency: int = 4,
    translate_fn: Optional[Any] = None,
    source_lang: str = "auto",
    promote_final: bool = True,
    bypass_cache_ids: Optional[Set[str]] = None,
    redraft_issues: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, str]:
    """
    Executes chapter translation:
    1. Loads source text and ensures XML segments.
    2. Byte-preserves figure and code segments.
    3. Translates headings and paragraphs using batched cache or MT/LLM API with concurrency.
    4. Injects dynamic terminology from book_record.json.
    5. Validates segment parity.
    6. Strips segments to clean markdown and writes outputs.
    Returns (target_xml, final_markdown).
    """
    import concurrent.futures
    import threading

    run_dir = Path(run_dir).resolve()
    ch_str = normalize_chapter_id(chapter)
    ch_dir = run_dir / ch_str

    source_path = Path(source).resolve() if source else ch_dir / "source.txt"
    output_path = Path(output).resolve() if output else ch_dir / f"round{round_num}_translate.txt"
    final_path = Path(final).resolve() if final else ch_dir / "final.txt"
    book_record_path = Path(book_record).resolve() if book_record else run_dir / "book_record.json"

    if cache_file:
        cache_path = Path(cache_file).resolve()
    else:
        cache_path = ch_dir / "translation_cache.json"

    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    with open(source_path, "r", encoding="utf-8") as f:
        raw_source = f.read()

    # Load book record and build dynamic rules
    full_book_record = None
    if book_record_path and book_record_path.exists():
        try:
            with open(book_record_path, "r", encoding="utf-8") as f:
                full_book_record = json.load(f)
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to load book record {book_record_path}: {e}\n")

    # Resolve source language
    effective_source_lang = source_lang
    if effective_source_lang == "auto":
        if full_book_record and isinstance(full_book_record, dict) and full_book_record.get("source_lang"):
            effective_source_lang = full_book_record["source_lang"]
        else:
            effective_source_lang = detect_source_language(raw_source)

    source_xml = ensure_xml_segments(raw_source)
    segments = parse_segments(source_xml)
    if not segments:
        raise ValueError(f"No segments parsed from source file: {source_path}")

    seg_issues_map: Optional[Dict[str, List[Dict[str, Any]]]] = None
    if redraft_issues:
        import importlib
        refine_mod = importlib.import_module("scripts.refine_chapter")
        map_issues_fn = getattr(refine_mod, "map_issues_to_segments")
        seg_issues_map = map_issues_fn(redraft_issues, segments)

    # Load cache via TranslationCacheManager (AC-ARCH-6.1)
    cache_mgr = TranslationCacheManager(cache_path)
    cache_mgr.migrate_positional_keys(segments)

    def save_cache():
        try:
            cache_mgr.save()
        except Exception as e:
            print(f"Warning: Failed to save translation cache to {cache_path}: {e}")

    # Load terms, names and build dynamic rules
    terms, names = load_book_record(book_record_path)
    verbatim_terms = extract_verbatim_terms(full_book_record)
    allow_unspecified_gloss = True
    if full_book_record and isinstance(full_book_record, dict):
        brief = full_book_record.get("brief", {})
        genre = brief.get("genre", "") if isinstance(brief, dict) else ""
        if any(g in genre.lower() for g in ["fantasy", "fiction", "myth"]):
            allow_unspecified_gloss = False
    rules = build_term_rules(terms, names, verbatim_terms=verbatim_terms, allow_unspecified_gloss=allow_unspecified_gloss)

    # Separate translatable segments from non-translatable
    uncached: List[Tuple[int, Dict[str, Any]]] = []
    translated_map: Dict[str, str] = {}
    untranslated_ids: List[str] = []

    for idx, seg in enumerate(segments):
        seg_id = seg["id"]
        seg_type = seg["type"]
        orig_text = seg["original"]

        if seg_type in ("figure", "code"):
            translated_map[seg_id] = orig_text
            continue

        if effective_source_lang == "zh" and seg_type == "heading" and not re.search(r'[\u4e00-\u9fff]', orig_text):
            translated_map[seg_id] = orig_text
            continue

        # Check cache via seg_id then orig_text (REQ-TXN-001, AC-INT-002-2, REQ-COR-001)
        if bypass_cache_ids and seg_id in bypass_cache_ids:
            cached_val: Optional[str] = None
        else:
            cached_val = cache_mgr.get_valid(seg_id, orig_text)

        if cached_val is not None:
            translated_map[seg_id] = cached_val
        elif offline and translate_fn is None:
            translated_map[seg_id] = orig_text
            untranslated_ids.append(seg_id)
        else:
            uncached.append((idx, seg))

    # Translate uncached segments in batches
    if uncached:
        if effective_source_lang == "zh":
            batches: List[List[Tuple[int, Dict[str, Any]]]] = []
            cur_b: List[Tuple[int, Dict[str, Any]]] = []
            cur_ch = 0
            for item in uncached:
                s_len = len(item[1].get("original", ""))
                if cur_b and (len(cur_b) >= max(10, batch_size) or (cur_ch + s_len > 2800)):
                    batches.append(cur_b)
                    cur_b = [item]
                    cur_ch = s_len
                else:
                    cur_b.append(item)
                    cur_ch += s_len
            if cur_b:
                batches.append(cur_b)
        else:
            effective_batch_size = max(1, batch_size)
            batches: List[List[Tuple[int, Dict[str, Any]]]] = [
                uncached[i : i + effective_batch_size]
                for i in range(0, len(uncached), effective_batch_size)
            ]

        def process_batch(batch_items: List[Tuple[int, Dict[str, Any]]]):
            from datetime import datetime, timezone
            t0 = time.time()
            t_start = datetime.now(timezone.utc)
            started_at_str = t_start.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

            segs_in_batch = [item[1] for item in batch_items]
            first_idx = batch_items[0][0]
            # Context before from preceding segments in the chapter (AC-ARCH-6.2)
            ctx_start = max(0, first_idx - 2)
            context_segs = segments[ctx_start:first_idx] if first_idx > 0 else []

            # Mask math formulas before sending segment text to LLM (REQ-009 / AC-009.1)
            math_maps: Dict[str, Dict[str, str]] = {}
            masked_segs = []
            for s in segs_in_batch:
                orig_s = s.get("original", "")
                if s.get("type") not in ("figure", "code"):
                    masked_text, mmap = mask_math_formulas(orig_s)
                    math_maps[s["id"]] = mmap
                    s_copy = dict(s)
                    s_copy["original"] = masked_text
                    masked_segs.append(s_copy)
                else:
                    masked_segs.append(s)

            batch_results = translate_batch(
                masked_segs,
                translate_fn=translate_fn,
                offline=offline,
                book_record=full_book_record,
                source_lang=effective_source_lang,
                context_segs=context_segs,
                seg_issues_map=seg_issues_map,
            )
            updates = {}
            for idx_b, seg_b in batch_items:
                res_text = batch_results.get(seg_b["id"], seg_b["original"])
                save_to_cache = True
                # Unmask math formulas immediately after receiving LLM translation (before saving to cache)
                if seg_b["id"] in math_maps and math_maps[seg_b["id"]]:
                    res_text, math_ok = unmask_math_formulas(
                        res_text, math_maps[seg_b["id"]], fallback_source=seg_b["original"]
                    )
                    if not math_ok:
                        sys.stderr.write(
                            f"Warning: Degraded math formula restoration in segment {seg_b['id']}; skipping cache persistence.\n"
                        )
                        save_to_cache = False
                # For <segment type="table">, run validate_and_repair_gfm_table after translation (REQ-007)
                if seg_b.get("type") == "table":
                    res_text, _ = validate_and_repair_gfm_table(seg_b["original"], res_text)

                # Sanitization for Chinese translation (REQ-004)
                if effective_source_lang == "zh" and seg_b.get("type") not in ("figure", "code"):
                    from scripts.evaluators.anti_convert import sanitize_chinese_translation
                    res_text, _ = sanitize_chinese_translation(
                        res_text, source_lang="zh", book_record=full_book_record
                    )

                if seg_b.get("type") not in ("figure", "code"):
                    res_text = ensure_footnote_numbering(seg_b.get("original", ""), res_text)

                translated_map[seg_b["id"]] = res_text
                if save_to_cache:
                    updates[seg_b["id"]] = res_text
                    updates[seg_b["original"]] = res_text

            t_end = datetime.now(timezone.utc)
            completed_at_str = t_end.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            dur_ms = max(0, int((time.time() - t0) * 1000))
            trunk_id = f"trunk-{ch_str}-r{round_num}-b{batch_items[0][0]:03d}"
            agent_name = "translate_zh" if effective_source_lang == "zh" else "translate"
            model_name = DEFAULT_TRANSLATE_MODEL
            engine_name = "custom" if (translate_fn is not None and offline) else ("cbc" if not offline else "agent-offline")

            if not offline or translate_fn is not None:
                for idx_b, seg_b in batch_items:
                    cache_mgr.set_provenance(seg_b["id"], {
                        "agent": agent_name,
                        "model": model_name,
                        "engine": engine_name,
                        "trunk_id": trunk_id,
                        "started_at": started_at_str,
                        "finished_at": completed_at_str,
                        "duration_ms": dur_ms,
                    })

                cache_mgr.update_and_save(updates)

            audit_rec = {
                "trunk_id": trunk_id,
                "chapter": ch_str,
                "round": round_num,
                "phase": "translate",
                "segments": [seg_b["id"] for _, seg_b in batch_items],
                "who": {
                    "agent": agent_name,
                    "model": model_name,
                    "engine": engine_name,
                },
                "what": {
                    "source_chars": sum(len(seg_b["original"]) for _, seg_b in batch_items),
                    "target_chars": sum(len(translated_map.get(seg_b["id"], "")) for _, seg_b in batch_items),
                    "segment_count": len(batch_items),
                    "source_lang": effective_source_lang,
                    "target_lang": "vi",
                },
                "when": {
                    "started_at": started_at_str,
                    "completed_at": completed_at_str,
                    "duration_ms": dur_ms,
                },
            }
            append_trunk_audit_record(ch_dir, audit_rec)
            print(f"Translated batch ({len(batch_items)} segs), progress: {len(translated_map)}/{len(segments)} segments.", flush=True)

        effective_workers = max(1, concurrency)
        if effective_workers > 1 and len(batches) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=effective_workers) as executor:
                list(executor.map(process_batch, batches))
        else:
            for b in batches:
                process_batch(b)

    cache = cache_mgr.data

    # Process all segments in exact original order
    chapter_seen_terms: Set[str] = set()
    blocks: List[str] = []
    for idx, seg in enumerate(segments):
        seg_id = seg["id"]
        seg_type = seg["type"]
        orig_text = seg["original"]

        if seg_type in ("figure", "code"):
            # Byte-for-byte invariance
            trans_text = orig_text
        else:
            trans_text = translated_map.get(seg_id)
            if trans_text is None:
                trans_text = cache.get(str(idx)) or cache.get(seg_id) or orig_text

            if effective_source_lang == "zh":
                trans_text = normalize_cjk_punctuation(trans_text)
                if seg_type in ("paragraph", "heading") and "\n" not in orig_text:
                    trans_text = re.sub(r'\s*\n+\s*', ' ', trans_text.strip())

            is_ref = bool(re.match(r'^\s*\[\d+\]', orig_text))
            if is_ref:
                # Keep English title in quotes verbatim for reference citations
                orig_m = re.search(r'[“"]([^”"]+)[”"]', orig_text)
                trans_m = re.search(r'[“"]([^”"]+)[”"]', trans_text)
                if orig_m and trans_m:
                    trans_text = trans_text[:trans_m.start()] + orig_m.group(0) + trans_text[trans_m.end():]
                trans_text = deduplicate_parentheticals(trans_text)
            else:
                if rules:
                    trans_text = apply_dynamic_terminology(trans_text, rules, verbatim_terms=verbatim_terms, seen_terms=chapter_seen_terms)
                elif verbatim_terms:
                    trans_text = apply_dynamic_terminology(trans_text, [], verbatim_terms=verbatim_terms, seen_terms=chapter_seen_terms)

            trans_text = ensure_footnote_numbering(orig_text, trans_text)

        prov = cache_mgr.get_provenance(seg_id)
        prov_attrs = ""
        if prov:
            for k in ("agent", "model", "engine", "trunk_id", "started_at", "finished_at", "completed_at", "duration_ms"):
                if k in prov:
                    prov_attrs += f' {k}="{prov[k]}"'

        block = (
            f'<segment id="{seg_id}" type="{seg_type}"{prov_attrs}>\n'
            f'<original>\n{orig_text}\n</original>\n'
            f'<translate>\n{trans_text}\n</translate>\n'
            f'</segment>'
        )
        blocks.append(block)

    target_xml = "\n\n".join(blocks) + "\n"

    # Blocking segment count parity check
    target_segments = parse_segments(target_xml)
    if len(segments) != len(target_segments):
        raise RuntimeError(f"Segment parity mismatch: {len(segments)} source vs {len(target_segments)} draft segments.")

    # Parity validation
    is_valid, errors = validate_segment_parity(source_xml, target_xml)
    if not is_valid:
        raise RuntimeError(f"Segment parity validation failed: {errors}")

    # Clean markdown extraction
    final_markdown = strip_segments_to_final(target_xml)

    # Write output artifacts
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output_path, target_xml)

    # Write untranslated segment IDs or unlink if all translated (REQ-INT-002, AC-INT-002-1)
    untranslated_path = ch_dir / f"round{round_num}_untranslated.json"
    if untranslated_ids:
        atomic_write_json(
            untranslated_path,
            {
                "chapter": ch_str,
                "round": round_num,
                "segment_ids": untranslated_ids,
            },
        )
    elif untranslated_path.exists():
        try:
            untranslated_path.unlink()
        except OSError:
            pass

    if promote_final:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(final_path, final_markdown)

    return target_xml, final_markdown


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Unified, generic chapter translation pipeline.")
    parser.add_argument("--run-dir", required=True, help="Root directory of book run (e.g. runs/principles_life_and_work)")
    parser.add_argument("--chapter", required=True, help="Chapter identifier (e.g. ch01, 1, 02)")
    parser.add_argument("--round", type=int, default=1, help="Translation round (default: 1)")
    parser.add_argument("--source", default=None, help="Override path to source text (defaults to <run_dir>/ch<NN>/source.txt)")
    parser.add_argument("--output", default=None, help="Override path to translated draft (defaults to <run_dir>/ch<NN>/round<R>_translate.txt)")
    parser.add_argument("--final", default=None, help="Override path to final text (defaults to <run_dir>/ch<NN>/final.txt)")
    parser.add_argument("--book-record", default=None, help="Override path to book record (defaults to <run_dir>/book_record.json)")
    parser.add_argument("--cache-file", default=None, help="Path to translation cache JSON (defaults to <run_dir>/ch<NN>/translation_cache.json if it exists)")
    parser.add_argument("--offline", action="store_true", help="Use cached segments or mock translation; fail or keep source if translation unavailable")
    parser.add_argument("--batch-size", type=int, default=15, help="Number of segments per translation batch")
    parser.add_argument("--concurrency", type=int, default=4, help="Number of concurrent translation workers")
    parser.add_argument(
        "--source-lang",
        choices=["auto", "en", "zh"],
        default="auto",
        help="Source language ('auto', 'en', or 'zh', default: auto)",
    )
    return parser.parse_args(argv)


def main():
    args = parse_args()
    print(f"Starting chapter translation: run_dir={args.run_dir}, chapter={args.chapter}, round={args.round}")
    target_xml, final_md = translate_chapter(
        run_dir=args.run_dir,
        chapter=args.chapter,
        round_num=args.round,
        source=args.source,
        output=args.output,
        final=args.final,
        book_record=args.book_record,
        cache_file=args.cache_file,
        offline=args.offline,
        batch_size=args.batch_size,
        concurrency=args.concurrency,
        source_lang=args.source_lang,
    )
    print("Chapter translation successfully completed.")


if __name__ == "__main__":
    main()

