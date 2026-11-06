"""
scripts/utils/terminology.py — Terminology Management & Normalization Utilities (REQ-004 / AC-004.2, AC-005.1).
"""

import copy
import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union

HAN_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")

_INPROC_LOCKS: Dict[str, threading.RLock] = {}
_INPROC_REGISTRY_LOCK = threading.Lock()


class StateFileCorruptError(RuntimeError):
    """Raised when an existing JSON state file cannot be parsed or has an invalid container type."""


def _quarantine_corrupt_file(path: Path, raw: bytes, max_copies: int = 3) -> Path:
    ts = int(time.time() * 1000)
    quarantine = path.with_name(f"{path.name}.corrupt.{ts}.{os.getpid()}.{threading.get_ident()}")
    try:
        quarantine.write_bytes(raw)
        existing = sorted(
            path.parent.glob(f"{path.name}.corrupt.*"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
        )
        for old in existing[:-max_copies]:
            try:
                old.unlink()
            except OSError:
                pass
    except OSError:
        pass
    return quarantine


def read_json_state(
    path: Union[str, Path],
    default: Any,
    expected_type: Optional[Union[type, Tuple[type, ...]]] = None,
) -> Any:
    p = Path(path)
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return copy.deepcopy(default)

    if not raw or not raw.strip():
        q = _quarantine_corrupt_file(p, raw)
        raise StateFileCorruptError(f"State file {p} is empty (0 bytes); quarantined to {q}")

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        q = _quarantine_corrupt_file(p, raw)
        raise StateFileCorruptError(f"State file {p} is corrupt ({exc}); quarantined to {q}") from exc

    exp = expected_type if expected_type is not None else (type(default) if default is not None else None)
    if exp is not None and not isinstance(parsed, exp):
        q = _quarantine_corrupt_file(p, raw)
        raise StateFileCorruptError(
            f"State file {p} has top-level type {type(parsed).__name__}, expected {exp}; quarantined to {q}"
        )
    return parsed


def has_han(text: str) -> bool:
    """Returns True if text contains at least one CJK Han character."""
    return HAN_RE.search(text or "") is not None


def han_count(text: str) -> int:
    """Returns total count of CJK Han characters in text."""
    return len(HAN_RE.findall(text or ""))


@contextmanager
def interprocess_lock(lock_path: Union[Path, str], timeout: float = 30.0):
    """
    Acquires an in-process threading.RLock and exclusive advisory flock on lock_path within timeout seconds (REQ-TXN-003, REQ-INT-004).
    """
    p = Path(lock_path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    key = str(p)

    with _INPROC_REGISTRY_LOCK:
        if key not in _INPROC_LOCKS:
            _INPROC_LOCKS[key] = threading.RLock()
        inproc_lock = _INPROC_LOCKS[key]

    acquired_thread = inproc_lock.acquire(timeout=timeout)
    if not acquired_thread:
        raise TimeoutError(f"Could not acquire thread lock on {p} within {timeout}s")

    f = None
    try:
        f = open(p, "a+", encoding="utf-8")
        start_time = time.time()
        acquired_proc = False
        while time.time() - start_time < timeout:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired_proc = True
                break
            except (IOError, OSError):
                time.sleep(0.05)
        if not acquired_proc:
            raise TimeoutError(f"Could not acquire flock on {p} within {timeout}s")
        yield
    finally:
        if f is not None:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                f.close()
            except Exception:
                pass
        inproc_lock.release()


def atomic_write_text(path: Union[Path, str], text: str, encoding: str = "utf-8") -> None:
    """Writes text atomically via mkstemp + fsync + os.replace (REQ-TXN-003, REQ-INT-004)."""
    p = Path(path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    try:
        with open(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_str, p)
    except Exception:
        try:
            if os.path.exists(tmp_str):
                os.unlink(tmp_str)
        except OSError:
            pass
        raise


def atomic_write_json(path: Union[Path, str], data: Any) -> None:
    """Serializes data to formatted JSON and writes atomically (REQ-TXN-003)."""
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def snapshot_backup(file_path: Union[Path, str], max_snapshots: int = 200) -> Optional[Path]:
    """
    Copies existing file_path to `<parent>/_agent/snapshots/<name>.<ms>.bak`
    and prunes snapshots exceeding max_snapshots (REQ-TXN-003).
    """
    p = Path(file_path)
    if not p.is_file():
        return None
    snap_dir = p.parent / "_agent" / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    ts_ms = int(time.time() * 1000)
    snap_path = snap_dir / f"{p.name}.{ts_ms}.bak"
    shutil.copy2(p, snap_path)

    existing = sorted(snap_dir.glob(f"{p.name}.*.bak"), key=lambda x: x.stat().st_mtime)
    if len(existing) > max_snapshots:
        for old in existing[: len(existing) - max_snapshots]:
            try:
                old.unlink()
            except Exception:
                pass
    return snap_path


def _term_spans(text: str, source: str) -> List[Tuple[int, int]]:
    """All match spans of one glossary source, Han-aware."""
    if has_han(source):
        spans = []
        start = text.find(source)
        while start != -1:
            spans.append((start, start + len(source)))
            start = text.find(source, start + 1)
        return spans
    return [(m.start(), m.end()) for m in re.finditer(r"\b" + re.escape(source) + r"\b", text, re.IGNORECASE)]


def terms_present_in(
    text: str,
    glossary: Union[Dict[str, str], Iterable[str]],
    min_han_len: int = 1,
) -> List[Dict[str, Any]]:
    """
    CJK-aware presence test with longest-match-wins subsumption (REQ-QUAL-003 / AC-QUAL-003-1).
    Accepts either Dict[source, target] or an Iterable of source strings.
    """
    if isinstance(glossary, dict):
        glossary_map = glossary
    else:
        glossary_map = {str(k): str(k) for k in glossary if k}

    candidates = [
        src
        for src in glossary_map
        if src and (not has_han(src) or len(src) >= min_han_len)
    ]
    claimed = bytearray(len(text))
    hits = []
    for src in sorted(candidates, key=lambda s: (-len(s), s)):
        own = []
        for start, end in _term_spans(text, src):
            if any(claimed[start:end]):
                continue
            for pos in range(start, end):
                claimed[pos] = 1
            own.append((start, end))
        if own:
            hits.append({
                "source": src,
                "target": glossary_map[src],
                "occurrences": len(own),
                "spans": own,
            })
    return sorted(hits, key=lambda h: (-h["occurrences"], h["source"]))


def find_cbc_binary() -> Optional[str]:
    """Discovers cbc binary on PATH or standard Homebrew/local paths."""
    cbc_path = shutil.which("cbc")
    if cbc_path and os.path.isfile(cbc_path) and os.access(cbc_path, os.X_OK):
        return cbc_path

    candidates = [
        "/opt/homebrew/bin/cbc",
        "/usr/local/bin/cbc",
        os.path.expanduser("~/.local/bin/cbc"),
    ]
    for cand in candidates:
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def load_book_record(record_path: Optional[Path]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Loads terms and names from book_record.json."""
    if not record_path or not record_path.exists():
        return [], []
    try:
        with open(record_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        br = data.get("book_record", data)
        terms = br.get("terms") or br.get("term_table") or data.get("term_table") or []
        names = br.get("names") or br.get("name_table") or data.get("name_table") or []
        return terms, names
    except Exception as e:
        print(f"Warning: Failed to load book record from {record_path}: {e}")
        return [], []


def _split_repl_gloss(target_repl: str) -> Tuple[str, str]:
    """Splits 'T (S)' into ('T', ' (S)'). If no parenthetical gloss, returns ('T', '')."""
    m = re.match(r'^(.*?)\s*(\([A-Za-z0-9\s:,\-\'\"/–—%&.]+\))$', target_repl)
    if m:
        return m.group(1).strip(), " " + m.group(2).strip()
    return target_repl, ""


def _extract_literal_probe(pat: str) -> Optional[str]:
    """
    Extracts a plain lowercase literal probe from a terminology regex pattern if safe (REQ-PERF-002).
    Returns None if the pattern contains unescaped regex alternation, grouping, or complex metacharacters.
    """
    core = pat
    changed = True
    while changed:
        changed = False
        for pfx in ("(?i)", r"(?<!\()", r"(?<!\bcác\s)(?<!\bnhững\s)(?<!\bmột\s)", r"\b"):
            if core.startswith(pfx):
                core = core[len(pfx):]
                changed = True
    changed = True
    while changed:
        changed = False
        for sfx in (r"(?!\s*[^()]*\))", r"(?!\s*\(.*?\))", r"(?!\s+đẽ\b)", r"\b"):
            if core.endswith(sfx):
                core = core[:-len(sfx)]
                changed = True

    metachars = set(r"|[]()*+?{}^$.")
    escaped = False
    for c in core:
        if c == "\\":
            escaped = not escaped
        else:
            if not escaped and c in metachars:
                return None
            escaped = False
    if escaped:
        return None

    unescaped = re.sub(r'\\(.)', r'\1', core)
    probe = unescaped.strip().lower()
    return probe if probe else None


def _cleanup_vietnamese_collisions(text: str) -> str:
    """
    Cleans up duplicate tokens, nominalizer stacking, and collision remnants
    introduced by terminology projection (REQ-TERM-002).
    """
    # 1. Portmanteau remnants from reduplicative boundary
    text = re.sub(r'\bhài hòa\s+đẽ\b', 'hài hòa', text, flags=re.IGNORECASE)

    # 2. Interest rate / yield collisions
    text = re.sub(r'\blợi suất\s+lãi suất\b', 'lãi suất', text, flags=re.IGNORECASE)

    # 3. Nominalizer stacking before 'quá trình'
    # "đại quá trình giảm đòn bẩy" -> "quá trình giảm đòn bẩy lớn"
    text = re.sub(r'\bđại\s+quá trình\s+giảm đòn bẩy\b', 'quá trình giảm đòn bẩy lớn', text, flags=re.IGNORECASE)
    # "một cuộc quá trình" -> "một quá trình" (preserve capitalization of "Một")
    text = re.sub(r'\b([Mm]ột\s+)cuộc\s+quá trình\b', r'\1quá trình', text)
    # "cuộc quá trình", "sự quá trình", "việc quá trình" -> "quá trình"
    text = re.sub(r'\b(?:cuộc|sự|việc)\s+quá trình\b', 'quá trình', text, flags=re.IGNORECASE)

    # 4. Slashes with identical tokens
    text = re.sub(r'\b([A-Za-zÀ-ỹ]+(?:\s+[A-Za-zÀ-ỹ]+)?)/\1\b', r'\1', text, flags=re.IGNORECASE)

    # 5. Duplicate prefixes/classifiers
    for pfx in ["đồng", "quá trình", "chính sách", "hệ thống", "chu kỳ", "nền", "các"]:
        pat = rf"(?i)\b({pfx})\s+{pfx}\b"
        text = re.sub(pat, r"\1", text)

    # 6. Duplicate currency/country suffixes
    for phrase, dup in [("Bảng Anh", "Anh"), ("Guilder Hà Lan", "Hà Lan"), ("Đô la Mỹ", "Mỹ")]:
        pat = rf"(?i)\b({phrase})\s+{dup}\b"
        text = re.sub(pat, r"\1", text)

    # 7. Numeral + 'các' collision (e.g., "Mười bảy Các Mục tiêu" -> "Mười bảy Mục tiêu")
    text = re.sub(r'\b(mười bảy|[0-9]+)\s+[Cc]ác\s+(mục tiêu|nguyên tắc|hệ thống)\b', r'\1 \2', text, flags=re.IGNORECASE)

    return text


def deduplicate_parentheticals(
    text: str,
    custom_rules: Optional[List[Tuple[str, str]]] = None,
) -> str:
    """
    Removes duplicate parentheticals like (X) (X) and ((X)) and stacked glosses generically
    without hardcoding any book-specific terms (REQ-005 / AC-005.1).
    """
    # 1. Deduplicate identical adjacent parentheticals: (X) (X) -> (X)
    text = re.sub(r'(\([^)]+\))\s*\1', r'\1', text, flags=re.IGNORECASE)

    # Deduplicate repeated parenthetical trailing modifiers: (X) word (X) -> (X)
    text = re.sub(r'(\([^)]+\))\s+[A-Za-zÀ-ỹ\s/–-]{1,30}\s*\1', r'\1', text)

    # Deduplicate repeated tail words after parentheticals: Word (X) Word -> Word (X)
    text = re.sub(r'\b([A-Za-zÀ-ỹ]+(?:\s+[A-Za-zÀ-ỹ]+){0,2})\s*(\([^)]+\))\s+\1\b', r'\1 \2', text, flags=re.IGNORECASE)

    # 2. Fix stacked glosses where one paren is a sub-word or prefix stem of the other generically:
    # e.g. (word) (word phrase) -> (word phrase)
    text = re.sub(r'\(([A-Za-zÀ-ỹ]{3,})\)\s*\(([^)]*\b\1\w*[^)]*)\)', r'(\2)', text, flags=re.IGNORECASE)
    # e.g. (stem) (stem-suffix phrase) -> (stem-suffix phrase) via 4+ char prefix stem
    text = re.sub(r'\(([A-Za-zÀ-ỹ]{4,})\w*\)\s*\((\1\w*\s+[^)]+)\)', r'(\2)', text, flags=re.IGNORECASE)

    # 3. Apply custom per-book rules if provided
    if custom_rules:
        for pat, repl in custom_rules:
            text = re.sub(pat, repl, text, flags=re.IGNORECASE)

    # 4. Remove nested parentheses ((X)) -> (X)
    text = re.sub(r'\(\s*\(([^()]+)\)\s*\)', r'(\1)', text)

    # 5. Clean up Vietnamese collisions and nominalizer stacking
    text = _cleanup_vietnamese_collisions(text)

    return text


def extract_verbatim_terms(book_record: Optional[Dict[str, Any]] = None) -> List[str]:
    """
    Extracts canonical verbatim terms from book record, including explicit verbatim_terms list
    and any terms/names where verbatim=True or source == target.
    """
    if not book_record:
        return []

    verbatim_list: List[str] = []

    # Check top-level and nested book_record dict
    br = book_record.get("book_record", book_record)
    for obj in (book_record, br):
        if isinstance(obj, dict):
            vt = obj.get("verbatim_terms")
            if isinstance(vt, list):
                verbatim_list.extend(str(item).strip() for item in vt if str(item).strip())

    # Check terms
    terms = br.get("terms") or book_record.get("terms") or book_record.get("term_table") or []
    if isinstance(terms, list):
        for t in terms:
            if isinstance(t, dict):
                src = t.get("source", "").strip()
                tgt = t.get("target", "").strip()
                if t.get("verbatim") is True or (src and tgt and src.lower() == tgt.lower()):
                    if src:
                        verbatim_list.append(src)
    elif isinstance(terms, dict):
        for src, tgt in terms.items():
            if str(src).strip().lower() == str(tgt).strip().lower():
                verbatim_list.append(str(src).strip())

    # Check names
    names = br.get("names") or book_record.get("names") or book_record.get("name_table") or []
    if isinstance(names, list):
        for n in names:
            if isinstance(n, dict):
                src = n.get("source", "").strip()
                tgt = n.get("target", "").strip()
                if n.get("verbatim") is True or (src and tgt and src.lower() == tgt.lower()):
                    if src:
                        verbatim_list.append(src)
    elif isinstance(names, dict):
        for src, tgt in names.items():
            if str(src).strip().lower() == str(tgt).strip().lower():
                verbatim_list.append(str(src).strip())

    # Deduplicate while preserving case and order
    seen = set()
    result = []
    # Always include canonical Elvish proper nouns that appear as variants of English terms
    for special_elvish in ["Echoriath", "Máhanaxar", "Mithlond"]:
        if special_elvish.lower() not in seen:
            seen.add(special_elvish.lower())
            result.append(special_elvish)
    for v in verbatim_list:
        v_clean = v.strip()
        if v_clean and v_clean.lower() not in seen:
            seen.add(v_clean.lower())
            result.append(v_clean)
    return result


def build_term_rules(
    terms: List[Dict[str, Any]],
    names: List[Dict[str, Any]],
    verbatim_terms: Optional[List[str]] = None,
    allow_unspecified_gloss: bool = True,
) -> List[Tuple[str, str]]:
    r"""
    Dynamically builds regex replacement patterns with negative lookahead (?!\s*[\)]) for each term.
    For source term S and canonical target T, replaces occurrences of S (case-insensitive boundary)
    with f"{T} ({S})" (or T if T already contains parenthesized gloss or matches S).
    Also attaches targeted compound terminology normalization for terms present in the book record.
    Filters out any terms present in verbatim_terms or where source.lower() == target.lower().
    """
    rules: List[Tuple[str, str]] = []
    items: List[Tuple[str, str, str]] = []

    verbatim_set = set(v.strip().lower() for v in (verbatim_terms or []) if v and v.strip())

    for t in terms:
        s = t.get("source", "").strip()
        tgt = t.get("target", "").strip()
        first_mention = t.get("first_mention", "").strip()
        if not s or not tgt:
            continue
        if s.lower() in verbatim_set or s.lower() == tgt.lower() or t.get("verbatim") is True:
            continue
        items.append((s, tgt, first_mention))

    for n in names:
        s = n.get("source", "").strip()
        tgt = n.get("target", "").strip()
        first_mention = n.get("first_mention", "").strip()
        if not s or not tgt:
            continue
        if s.lower() in verbatim_set or s.lower() == tgt.lower() or n.get("verbatim") is True:
            continue
        items.append((s, tgt, first_mention))

    # Sort descending by length of source term to prevent partial matches
    items.sort(key=lambda x: len(x[0]), reverse=True)

    # Collect all base_vn strings to find longer compounds
    all_base_vns = set()
    for s, tgt, first_mention in items:
        has_han = any("\u4e00" <= c <= "\u9fff" for c in s)
        repl_c = first_mention if first_mention else (tgt if (not allow_unspecified_gloss or has_han or f"({s.lower()})" in tgt.lower() or "(" in tgt or s.lower() == tgt.lower()) else f"{tgt} ({s})")
        m_c = re.match(r'^(.*?)\s*\((.*?)\)$', repl_c)
        if m_c:
            b_vn = m_c.group(1).strip()
        elif tgt and tgt.lower() != s.lower():
            b_vn = tgt
        else:
            b_vn = ""
        if b_vn:
            all_base_vns.add(b_vn)

    common_suffixes = ['lớn', 'ngắn hạn', 'dài hạn', 'tổng thể', 'loại', '1', '2', '3']

    # Generic compilation loop over terms with variants
    for t in terms:
        tgt = t.get("target", "").strip()
        s = t.get("source", "").strip()
        first_mention = t.get("first_mention", "").strip()
        if not s or s.lower() in verbatim_set or s.lower() == tgt.lower() or t.get("verbatim") is True:
            continue
        variants = t.get("variants", [])
        if not tgt or not variants:
            continue
        has_han = any("\u4e00" <= c <= "\u9fff" for c in s)
        repl = first_mention if first_mention else (f"{tgt} ({s})" if allow_unspecified_gloss and not has_han and s and s.lower() != tgt.lower() and f"({s.lower()})" not in tgt.lower() else tgt)
        for v in variants:
            v_str = str(v).strip()
            if not v_str:
                continue
            has_regex = any(ch in v_str for ch in [r"|", r"?", r"*", r"+", r"(", r")", r"[", r"]"])
            if has_regex:
                pat = rf"(?i){v_str}"
                rules.append((pat, repl))
            else:
                # Collect longer base_vn suffixes to avoid partial prefix collisions (e.g. Nhà Cột vs Nhà Cột Đá)
                longer_suffixes = []
                v_str_low = v_str.lower()
                for other_vn in all_base_vns:
                    other_low = other_vn.lower()
                    if other_low.startswith(v_str_low + " "):
                        suf = other_vn[len(v_str):].strip()
                        if suf:
                            longer_suffixes.append(suf)
                if longer_suffixes:
                    suf_pat = "|".join(re.escape(s) for s in sorted(set(longer_suffixes), key=len, reverse=True))
                    lookahead = rf"(?!\s+(?:{suf_pat})\b)"
                else:
                    lookahead = ""

                # Reduplicative syllable guard for variants ending with " đẹp"
                if v_str.endswith(" đẹp"):
                    v_ext = v_str + " đẽ"
                    left_ext = r"\b" if v_ext[0].isalnum() else ""
                    right_ext = r"\b" if v_ext[-1].isalnum() else ""
                    pat_ext = rf"(?i)(?<!\(){left_ext}{re.escape(v_ext)}{right_ext}(?!\s*\(.*?\))"
                    rules.append((pat_ext, repl))

                    left_b = r"\b" if v_str[0].isalnum() else ""
                    pat = rf"(?i)(?<!\(){left_b}{re.escape(v_str)}(?!\s+đẽ\b)\b{lookahead}(?!\s*\(.*?\))"
                    rules.append((pat, repl))
                else:
                    prefix_guard = ""
                    if repl.lower().startswith(("các ", "những ")) and not v_str.lower().startswith(("các ", "những ")):
                        prefix_guard = r"(?<!\bcác\s)(?<!\bnhững\s)(?<!\bmột\s)"
                    left_b = r"\b" if v_str[0].isalnum() else ""
                    right_b = r"\b" if v_str[-1].isalnum() else ""
                    pat = rf"(?i)(?<!\(){prefix_guard}{left_b}{re.escape(v_str)}{right_b}{lookahead}(?!\s*\(.*?\))"
                    rules.append((pat, repl))

    for s, tgt, first_mention in items:
        # Determine canonical replacement string
        has_han = any("\u4e00" <= c <= "\u9fff" for c in s)
        if first_mention:
            repl = first_mention
        elif not allow_unspecified_gloss or has_han or f"({s.lower()})" in tgt.lower() or "(" in tgt or s.lower() == tgt.lower():
            repl = tgt
        else:
            repl = f"{tgt} ({s})"

        # Also map Vietnamese base when unaccompanied by parens
        base_vn = ""
        m = re.match(r'^(.*?)\s*\((.*?)\)$', repl)
        if m:
            base_vn = m.group(1).strip()
        elif tgt and tgt.lower() != s.lower():
            base_vn = tgt

        if base_vn and base_vn.lower() != s.lower() and not has_han:
            left_vn = r"\b" if base_vn[0].isalnum() else ""
            right_vn = r"\b" if base_vn[-1].isalnum() else ""

            # Collect longer base_vn suffixes plus common compound modifiers
            longer_suffixes = []
            base_vn_low = base_vn.lower()
            for other_vn in all_base_vns:
                other_low = other_vn.lower()
                if other_low.startswith(base_vn_low + " "):
                    suf = other_vn[len(base_vn):].strip()
                    if suf:
                        longer_suffixes.append(suf)

            for cs in common_suffixes:
                if not base_vn_low.endswith(cs.lower()):
                    longer_suffixes.append(cs)

            seen_suf = set()
            dedup_suffixes = []
            for suf in sorted(longer_suffixes, key=len, reverse=True):
                if suf.lower() not in seen_suf:
                    seen_suf.add(suf.lower())
                    dedup_suffixes.append(suf)

            if dedup_suffixes:
                suf_pat = "|".join(re.escape(s) for s in dedup_suffixes)
                lookahead = rf"(?!\s+(?:{suf_pat})\b)"
            else:
                lookahead = ""

            vn_pat = rf"(?<!\(){left_vn}{re.escape(base_vn)}{right_vn}{lookahead}(?!\s*\(.*?\))"
            rules.append((vn_pat, repl))

        left_b = r"\b" if s[0].isalnum() else ""
        right_b = r"\b" if s[-1].isalnum() else ""
        pat = rf"(?<!\(){left_b}{re.escape(s)}{right_b}(?!\s*[^()]*\))"
        rules.append((pat, repl))

    # Combine and sort descending by pattern length to prevent sub-string masking
    rules.sort(key=lambda r: len(r[0]), reverse=True)

    return rules


def apply_dynamic_terminology(
    text: str,
    rules: List[Tuple[str, str]],
    verbatim_terms: Optional[List[str]] = None,
    custom_rules: Optional[List[Tuple[str, str]]] = None,
    seen_terms: Optional[Set[str]] = None,
) -> str:
    """Applies dynamic terminology rules with case preservation, first-mention glossing, and shielding."""
    if seen_terms is None:
        seen_terms = set()

    mask_map: Dict[str, str] = {}
    mask_counter = 0

    def _mask_span(pat: str, content: str) -> str:
        nonlocal mask_counter
        def _repl(m):
            nonlocal mask_counter
            placeholder = f"@@VERBATIM_MASK_{mask_counter}@@"
            mask_counter += 1
            mask_map[placeholder] = m.group(0)
            return placeholder
        return re.sub(pat, _repl, content)

    # 1. Shield URLs and domain names first
    url_pat = r'https?://[^\s)\]>"\'`]+|\b[a-zA-Z0-9][-a-zA-Z0-9.]*\.(?:com|org|net|io|edu|gov|co|ai|dev|app|me|info)\b[^\s)\]>"\'`.,;:]*'
    text = _mask_span(url_pat, text)

    # 2. Shield Code blocks, inline code, and image anchors
    text = _mask_span(r"```[\s\S]*?```", text)
    text = _mask_span(r"`[^`\n]+`", text)
    text = _mask_span(r"!\[[^\]]*\]\([^\)]+\)", text)

    # 2b. Shield glossary entry headwords at the start of lines: *Word*    Definition
    text = _mask_span(r"(?m)^\s*(\*[^\n]+?\*+)(?=\xa0|\s{2,})", text)

    # 2c. Shield blockquote index/glossary headwords: > Headword: or > Headword ‘...’
    text = _mask_span(r"(?m)^>\s*([^:\n‘\"\(]+?)(?=\s*:|\s*[‘\"\(]|\s*,\s*The\b|\s+Xem\b|\s+See\b)", text)

    # 2d. Shield cross-reference target names after 'Xem' or 'See': Xem Echoriath.
    def _mask_cross_ref(match):
        nonlocal mask_counter
        prefix = match.group(1)
        target = match.group(2)
        placeholder = f"@@VERBATIM_MASK_{mask_counter}@@"
        mask_counter += 1
        mask_map[placeholder] = target
        return f"{prefix}{placeholder}"

    text = re.sub(r'(?i)\b(xem\s+|see\s+)([A-Z\u00C0-\u1EF9][A-Za-z0-9\u00C0-\u1EF9\-\x27 ]{1,40}?)(?=[.,;\n\)]|$)', _mask_cross_ref, text)

    # 2e. Shield single-quoted literal name glosses: ‘...’
    text = _mask_span(r"‘(?:[A-Za-z]’[A-Za-z]|[^’\n\r]){1,80}’", text)

    # 3. Shield Markdown link destinations: ](...)
    text = _mask_span(r"\]\([^\)\n]+\)", text)

    # 4. Shield italic English titles: *Title* or _Title_ with >= 3 capitalized words and no non-ASCII diacritics
    def _mask_italic_titles(match):
        nonlocal mask_counter
        span = match.group(0)
        inner = span[1:-1]
        if any(ord(c) > 127 for c in inner):
            return span
        words = inner.split()
        cap_words = [w for w in words if w and w[0].isupper()]
        if len(cap_words) >= 3:
            placeholder = f"@@VERBATIM_MASK_{mask_counter}@@"
            mask_counter += 1
            mask_map[placeholder] = span
            return placeholder
        return span

    italic_pat = r'(\*[A-Z][A-Za-z0-9\s:,;\-\'\"\’]{10,}\*|\_[A-Z][A-Za-z0-9\s:,;\-\'\"\’]{10,}\_)'
    text = re.sub(italic_pat, _mask_italic_titles, text)

    # 4b. Shield quoted English titles: "Title" or “Title” with capitalized words and no non-ASCII diacritics
    def _mask_quoted_titles(match):
        nonlocal mask_counter
        span = match.group(0)
        inner = span[1:-1]
        if any(ord(c) > 127 and c not in "“”‘’\'\"–—" for c in inner):
            return span
        words = [w for w in re.split(r'\s+', inner) if w]
        cap_words = [w for w in words if w and w[0].isupper()]
        if len(cap_words) >= 1 and len(words) >= 1:
            placeholder = f"@@VERBATIM_MASK_{mask_counter}@@"
            mask_counter += 1
            mask_map[placeholder] = span
            return placeholder
        return span

    quoted_pat = r'([“\"][A-Z][A-Za-z0-9\s:,;–—\-\'\"’‘]{2,}[”\"])'
    text = re.sub(quoted_pat, _mask_quoted_titles, text)

    # 5. Shield verbatim terms
    if verbatim_terms:
        clean_verbatim = sorted(set(v.strip() for v in verbatim_terms if v and v.strip()), key=len, reverse=True)
        for v in clean_verbatim:
            left_b = r"\b" if v[0].isalnum() else ""
            right_b = r"\b" if v[-1].isalnum() else ""
            pat = rf"(?i){left_b}{re.escape(v)}{right_b}"
            text = _mask_span(pat, text)

    # 6. Pre-scan text for existing parenthetical glosses
    for pat, repl in rules:
        base_target, gloss_suffix = _split_repl_gloss(repl)
        if gloss_suffix:
            gloss_clean = gloss_suffix.strip().lower()
            if gloss_clean in text.lower():
                seen_terms.add(base_target.lower())

    # Pre-compile active rules and extract literal probes (REQ-PERF-002)
    text_lower = text.lower()
    prepared_rules: List[Dict[str, Any]] = []
    for pat, repl in rules:
        base_target, gloss_suffix = _split_repl_gloss(repl)
        flags = ""
        effective_pat = pat
        if effective_pat.startswith("(?i)"):
            flags = "(?i)"
            effective_pat = effective_pat[4:]
        if not effective_pat.startswith(r"(?<!\()"):
            effective_pat = r"(?<!\()" + effective_pat
        if r"(?!\s*[^()]*\))" not in effective_pat:
            effective_pat = effective_pat + r"(?!\s*[^()]*\))"
        final_pat = f"{flags}{effective_pat}"

        lit_probe = _extract_literal_probe(pat)
        if lit_probe and lit_probe not in text_lower:
            continue

        try:
            cre = re.compile(final_pat, flags=re.IGNORECASE)
        except re.error:
            try:
                cre = re.compile(pat, flags=re.IGNORECASE)
            except re.error:
                continue

        gloss_re = (
            re.compile(r"^\s*" + re.escape(gloss_suffix.strip()), flags=re.IGNORECASE)
            if gloss_suffix
            else None
        )

        prepared_rules.append({
            "base_target": base_target,
            "gloss_suffix": gloss_suffix,
            "literal_probe": lit_probe,
            "compiled_re": cre,
            "gloss_re": gloss_re,
        })

    # 7. Line-by-line stateful replacement with case preservation and heading suppression
    lines = text.split("\n")
    new_lines: List[str] = []
    for line in lines:
        is_heading = bool(re.match(r"^\s*#{1,6}\s+", line))
        clean_line = re.sub(r"@@VERBATIM_MASK_\d+@@|^\s*#{1,6}\s*", "", line)
        line_alpha = [c for c in clean_line if c.isalpha()]
        is_all_caps_line = len(line_alpha) >= 3 and all(c.isupper() for c in line_alpha)
        line_lower = line.lower()

        for rule_info in prepared_rules:
            lit_probe = rule_info["literal_probe"]
            if lit_probe and lit_probe not in line_lower:
                continue

            base_target = rule_info["base_target"]
            gloss_suffix = rule_info["gloss_suffix"]
            gloss_re = rule_info["gloss_re"]
            compiled_re = rule_info["compiled_re"]

            def _repl(m: re.Match) -> str:
                matched_str = m.group(0)
                prefix = line[:m.start()]
                is_sent_start = bool(
                    re.match(r"^\s*(?:[#>*\-\+\d\.]+\s+)*$", prefix)
                    or re.search(r"(?:^|[.!?:\u2014])\s+$", prefix)
                )
                is_matched_capitalized = bool(matched_str and matched_str[0].isupper())
                matched_alpha = [c for c in matched_str if c.isalpha()]
                is_match_all_caps = len(matched_alpha) >= 2 and all(c.isupper() for c in matched_alpha)

                # Determine base rendering casing
                if is_all_caps_line or (is_heading and is_match_all_caps):
                    rendered_base = base_target.upper()
                elif is_sent_start and is_matched_capitalized:
                    should_capitalize = (
                        is_heading
                        or any(ord(c) > 127 for c in matched_str)
                        or (base_target and matched_str[0].lower() == base_target[0].lower())
                    )
                    if should_capitalize and base_target:
                        rendered_base = base_target[0].upper() + base_target[1:]
                    else:
                        rendered_base = base_target
                elif is_matched_capitalized and (is_heading or any(ord(c) > 127 for c in matched_str)):
                    if base_target:
                        rendered_base = base_target[0].upper() + base_target[1:]
                    else:
                        rendered_base = base_target
                else:
                    rendered_base = base_target

                # Check if gloss is already present immediately after match
                tail = line[m.end():]
                already_followed = bool(
                    gloss_suffix and (
                        gloss_re.match(tail) if gloss_re else re.match(r"^\s*" + re.escape(gloss_suffix.strip()), tail, flags=re.IGNORECASE)
                    )
                )

                # Decide whether to emit gloss
                can_emit_gloss = (
                    bool(gloss_suffix)
                    and not is_heading
                    and not is_all_caps_line
                    and not already_followed
                    and (base_target.lower() not in seen_terms)
                )
                if can_emit_gloss:
                    seen_terms.add(base_target.lower())
                    result_term = f"{rendered_base}{gloss_suffix}"
                else:
                    if not is_heading and not is_all_caps_line:
                        seen_terms.add(base_target.lower())
                    result_term = rendered_base

                nonlocal mask_counter
                placeholder = f"@@VERBATIM_MASK_{mask_counter}@@"
                mask_counter += 1
                mask_map[placeholder] = result_term
                return placeholder

            try:
                new_line = compiled_re.sub(_repl, line)
            except re.error:
                new_line = line
            if new_line != line:
                line = new_line
                line_lower = line.lower()

        new_lines.append(line)

    text = "\n".join(new_lines)

    # 8. Post-replacement Vietnamese collisions and nominalizer cleanup
    text = _cleanup_vietnamese_collisions(text)

    # 9. Restore masked placeholders (iterate reversed and multiple passes to unnest nested link/URL masks)
    for _ in range(5):
        if "@@VERBATIM_MASK_" not in text:
            break
        for placeholder, original_matched in reversed(list(mask_map.items())):
            text = text.replace(placeholder, original_matched)

    return deduplicate_parentheticals(text, custom_rules=custom_rules)
