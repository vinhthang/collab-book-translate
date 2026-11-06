"""scripts/utils/language.py — Language detection, slugging, and CJK utilities."""

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Optional, Union

# Strips XML tags (<segment ...>, <original>, </translate>) and Markdown code fences before analysis
CLEAN_TEXT_RE = re.compile(r'<[^>]+>|```[\s\S]*?```', re.MULTILINE)

# Unified Chinese chapter heading matcher supporting standard numerals, CJK numerals, and introductory headings
CHINESE_CHAPTER_HEADING_RE = re.compile(
    r"^\s*(?:(?:第\s*[零〇一二三四五六七八九十百千万两廿卅0-9]+\s*[章节回卷部篇集节])|(?:[卷部篇]\s*[零〇一二三四五六七八九十百千万两廿卅0-9]+)|(?:序章|前言|楔子|引子|尾声|后记|番外))(?:[\s:：、.\-—]+.*|$)",
    re.IGNORECASE,
)


def is_cjk_codepoint(cp: int) -> bool:
    """Checks whether a Unicode codepoint falls into CJK character ranges.

    Includes Unified Ideographs, Extension A, Compatibility Ideographs, and Extension B.
    """
    return (
        (0x4E00 <= cp <= 0x9FFF)       # CJK Unified Ideographs
        or (0x3400 <= cp <= 0x4DBF)    # Extension A
        or (0xF900 <= cp <= 0xFAFF)    # Compatibility Ideographs
        or (0x20000 <= cp <= 0x2A6DF)  # Extension B
    )


def is_chinese_chapter_heading(text: str) -> bool:
    """Checks whether a heading string matches standard Chinese chapter heading patterns."""
    return bool(CHINESE_CHAPTER_HEADING_RE.match(text.strip()))


def detect_source_language(text: str) -> str:
    """Detects whether text is primarily Chinese (CJK) or Latin (English/Vietnamese).

    Strips XML tags and code fences to prevent dilution.
    Checks CJK Unified Ideographs, Extension A, Compatibility Ideographs, and Extension B.
    Returns 'zh' if CJK density > 15%, else 'en'.
    """
    clean_text = CLEAN_TEXT_RE.sub('', text)
    total_chars = 0
    cjk_chars = 0

    for char in clean_text:
        if char.isspace():
            continue
        total_chars += 1
        if is_cjk_codepoint(ord(char)):
            cjk_chars += 1

    if total_chars > 0 and (cjk_chars / total_chars) > 0.15:
        return "zh"
    return "en"


def derive_book_slug(
    input_path: Union[Path, str],
    slug_override: Optional[Union[str, bytes]] = None,
    source_bytes: Optional[bytes] = None,
    append_hash: bool = False,
) -> str:
    """Derives a deterministic, filesystem-safe ASCII slug from an input book path.

    Converts CJK Hanzi to Pinyin *before* ASCII stripping to prevent collisions or 'book' fallbacks.
    When source_bytes is supplied or append_hash=True, appends a 4-char hex hash of
    (source_bytes[:4096] + stem) to disambiguate homophonous CJK titles.
    """
    if isinstance(slug_override, bytes):
        source_bytes = slug_override
        slug_override = None

    if slug_override and isinstance(slug_override, str) and slug_override.strip():
        return slug_override.strip()

    stem = Path(input_path).stem.strip()
    has_cjk = any(is_cjk_codepoint(ord(c)) for c in stem)

    seed = (source_bytes[:4096] if source_bytes else b"") + stem.encode("utf-8")
    short_hash = hashlib.sha256(seed).hexdigest()[:4]

    if has_cjk:
        try:
            import pypinyin
            words = pypinyin.lazy_pinyin(stem)
            base = re.sub(r'[^a-zA-Z0-9]+', '_', "_".join(words)).strip('_').lower()
            slug = base or "zh_book"
        except ImportError:
            slug = f"zh_book_{hashlib.md5(stem.encode('utf-8')).hexdigest()[:8]}"

        if source_bytes is not None or append_hash:
            return f"{slug}_{short_hash}"
        return slug

    slug = re.sub(r'[^a-zA-Z0-9]+', '_', stem).strip('_').lower()
    if not slug:
        slug = "unnamed_book"

    if (source_bytes is not None or append_hash) and slug != "unnamed_book":
        return f"{slug}_{short_hash}"
    return slug


_HV_DICT_CACHE: Optional[dict] = None


def _get_hanviet_dict() -> dict:
    global _HV_DICT_CACHE
    if _HV_DICT_CACHE is None:
        dict_file = Path(__file__).resolve().parent / "hanviet_dict.json"
        if dict_file.exists():
            try:
                with open(dict_file, "r", encoding="utf-8") as f:
                    _HV_DICT_CACHE = json.load(f)
            except Exception:
                _HV_DICT_CACHE = {}
        else:
            _HV_DICT_CACHE = {}
    return _HV_DICT_CACHE


def get_pinyin_initial(text: str) -> str:
    """Returns the uppercase ASCII initial (A-Z) for CJK strings or '#' for non-alphabetic symbols.

    Attempts pypinyin first, falls back to hanviet_dict.json reading initial.
    """
    if not text:
        return "#"
    clean = text.strip()
    if not clean:
        return "#"

    first_char = clean[0]
    if not is_cjk_codepoint(ord(first_char)):
        if first_char.isascii() and first_char.isalpha():
            return first_char.upper()
        return "#"

    # 1. Try pypinyin
    try:
        import pypinyin
        py = pypinyin.lazy_pinyin(first_char)
        if py and py[0]:
            initial = py[0][0].upper()
            if "A" <= initial <= "Z":
                return initial
    except Exception:
        pass

    # 2. Fallback to hanviet_dict.json reading initial
    hv_dict = _get_hanviet_dict()
    hv = hv_dict.get(first_char, "")
    if hv:
        first_hv_letter = hv.strip()[0]
        # Strip diacritics
        normalized = unicodedata.normalize("NFD", first_hv_letter)
        base_char = normalized[0].upper()
        if "A" <= base_char <= "Z":
            return base_char

    return "#"
