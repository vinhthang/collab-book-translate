"""
scripts/utils/hanviet.py — Deterministic Sino-Vietnamese (Hán-Việt) Transcriber
Converts Classical Chinese (Hanzi) to standard Sino-Vietnamese reading (âm Hán-Việt)
using local lexicon `hanviet_dict.json` and canonical Three Kingdoms terminology.
Enforces byte-invariance for non-CJK tokens, Markdown syntax, and XML tags.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

DICT_FILE = Path(__file__).resolve().parent / "hanviet_dict.json"
LEXICONS_DIR = Path(__file__).resolve().parent / "lexicons"

_HANVIET_DICT: Optional[Dict[str, str]] = None

def _load_lexicon(filename: str) -> Dict[str, str]:
    path = LEXICONS_DIR / filename
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

UNIVERSAL_LEXICON: Dict[str, str] = _load_lexicon("universal.json")
THREE_KINGDOMS_LEXICON: Dict[str, str] = _load_lexicon("three_kingdoms.json")
BUILTIN_COMPOUNDS: Dict[str, str] = {**UNIVERSAL_LEXICON, **THREE_KINGDOMS_LEXICON}

CJK_PUNCT_MAP: Dict[str, str] = {
    "，": ",",
    "、": ",",
    "。": ".",
    "：": ":",
    "；": ";",
    "！": "!",
    "？": "?",
    "（": "(",
    "）": ")",
    "「": "“",
    "」": "”",
    "〈": "<",
    "〉": ">",
    "《": "“",
    "》": "”",
    "〔": "[",
    "〕": "]",
    "\u3000": " ",
}


def load_hanviet_dict() -> Dict[str, str]:
    """Loads and caches the 4,185 character Sino-Vietnamese dictionary."""
    global _HANVIET_DICT
    if _HANVIET_DICT is None:
        if DICT_FILE.exists():
            try:
                with open(DICT_FILE, "r", encoding="utf-8") as f:
                    _HANVIET_DICT = json.load(f)
            except Exception:
                _HANVIET_DICT = {}
        else:
            _HANVIET_DICT = {}
    return _HANVIET_DICT


def is_cjk(ch: str) -> bool:
    """Returns True if character is a CJK unified ideograph."""
    if not ch:
        return False
    return ("\u4e00" <= ch <= "\u9fff") or ("\u3400" <= ch <= "\u4dbf")


def _resolve_base_compounds(
    book_record: Optional[Union[Dict[str, Any], str, Path]] = None,
    domain: str = "default",
) -> Dict[str, str]:
    if domain == "universal":
        return dict(UNIVERSAL_LEXICON)
    if domain == "three_kingdoms":
        return dict(BUILTIN_COMPOUNDS)

    # domain == "default"
    rec_data: Optional[Dict[str, Any]] = None
    if isinstance(book_record, (str, Path)):
        p = Path(book_record)
        if p.exists():
            try:
                rec_data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    elif isinstance(book_record, dict):
        rec_data = book_record

    if isinstance(rec_data, dict):
        if "domain_lexicon" in rec_data:
            lex = rec_data["domain_lexicon"]
            return dict(BUILTIN_COMPOUNDS) if lex == "three_kingdoms" else dict(UNIVERSAL_LEXICON)
        nested = rec_data.get("book_record")
        if isinstance(nested, dict) and "domain_lexicon" in nested:
            lex = nested["domain_lexicon"]
            return dict(BUILTIN_COMPOUNDS) if lex == "three_kingdoms" else dict(UNIVERSAL_LEXICON)

        title_str = (str(rec_data.get("title", "")) + " " + str(rec_data.get("title_vietnamese", ""))).lower()
        names_str = str(rec_data.get("names", ""))
        slug_str = str(rec_data.get("book_slug", "")).lower()
        if isinstance(nested, dict):
            title_str += " " + (str(nested.get("title", "")) + " " + str(nested.get("title_vietnamese", ""))).lower()
            names_str += " " + str(nested.get("names", ""))
            slug_str += " " + str(nested.get("book_slug", "")).lower()

        is_3k = (
            "tam quốc" in title_str
            or "三国" in title_str
            or "tam_quoc" in slug_str
            or "sanguo" in slug_str
            or "Tào Tháo" in names_str
            or "曹操" in names_str
        )
        return dict(BUILTIN_COMPOUNDS) if is_3k else dict(UNIVERSAL_LEXICON)

    # Legacy standalone call with book_record=None and domain="default"
    return dict(BUILTIN_COMPOUNDS)


def _extract_book_record_compounds(
    book_record: Optional[Union[Dict[str, Any], str, Path]] = None,
    custom_compounds: Optional[Dict[str, str]] = None,
    domain: str = "default",
) -> Dict[str, str]:
    """Extracts 1-to-1 phonetic compound names from book_record and custom_compounds."""
    res = _resolve_base_compounds(book_record, domain=domain)
    if custom_compounds and isinstance(custom_compounds, dict):
        res.update(custom_compounds)

    rec_data: Optional[Dict[str, Any]] = None
    if isinstance(book_record, (str, Path)):
        p = Path(book_record)
        if p.exists():
            try:
                rec_data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    elif isinstance(book_record, dict):
        rec_data = book_record

    if rec_data:
        # Only extract from name_table to preserve 1-to-1 phonetic correspondence
        for tab in [rec_data.get("name_table", {}), rec_data.get("book_record", {}).get("name_table", [])]:
            if isinstance(tab, dict):
                for k, v in tab.items():
                    if isinstance(v, str) and len(k) >= 2:
                        words = v.split("(")[0].strip().split()
                        if len(k) == len(words):
                            res[k] = " ".join(words)
            elif isinstance(tab, list):
                for item in tab:
                    if isinstance(item, dict) and "source" in item and "target" in item:
                        k = item["source"]
                        v = item["target"]
                        if isinstance(v, str) and len(k) >= 2:
                            words = v.split("(")[0].strip().split()
                            if len(k) == len(words):
                                res[k] = " ".join(words)
    return res


def transcribe_to_hanviet(
    text: str,
    book_record: Optional[Union[Dict[str, Any], str, Path]] = None,
    custom_compounds: Optional[Dict[str, str]] = None,
    domain: str = "default",
) -> str:
    """
    Converts a Classical Chinese string to Sino-Vietnamese (Hán-Việt) reading.
    Preserves non-CJK punctuation, Latin characters, and XML tags byte-identically.
    Separates consecutive Vietnamese syllables with spaces and applies sentence capitalization.
    """
    if not text:
        return ""

    hv_dict = load_hanviet_dict()
    compounds = _extract_book_record_compounds(
        book_record=book_record,
        custom_compounds=custom_compounds,
        domain=domain,
    )
    sorted_compounds = sorted(compounds.keys(), key=len, reverse=True)

    i = 0
    n = len(text)
    tokens: list[Tuple[str, str]] = []

    while i < n:
        # 1. Byte-invariance for XML tags
        if text[i] == "<":
            m = re.match(r"</?[a-zA-Z0-9_\-]+(?:\s+[^>]*?)?>", text[i:])
            if m:
                tokens.append((m.group(0), "raw"))
                i += len(m.group(0))
                continue

        # 2. Byte-invariance for Markdown image anchors
        if text[i:i + 2] == "![":
            m = re.match(r"!\[.*?\]\(.*?\)", text[i:])
            if m:
                tokens.append((m.group(0), "raw"))
                i += len(m.group(0))
                continue

        # 3. Byte-invariance for Markdown code blocks
        if text[i:i + 3] == "```":
            end_code = text.find("```", i + 3)
            if end_code != -1:
                end_code += 3
                tokens.append((text[i:end_code], "raw"))
                i = end_code
                continue

        # 4. Longest compound matching
        matched_comp = False
        for comp in sorted_compounds:
            if text.startswith(comp, i):
                tokens.append((compounds[comp], "word"))
                i += len(comp)
                matched_comp = True
                break
        if matched_comp:
            continue

        c = text[i]

        # 5. Single CJK character lookup
        if is_cjk(c):
            reading = hv_dict.get(c, c)
            tokens.append((reading if reading else c, "word"))
            i += 1
            continue

        # 6. CJK punctuation mapping
        if c in CJK_PUNCT_MAP:
            tokens.append((CJK_PUNCT_MAP[c], "punct"))
            i += 1
            continue

        # 7. Whitespace & Newlines
        if c in " \t":
            tokens.append((c, "space"))
            i += 1
            continue
        if c == "\n":
            tokens.append(("\n", "newline"))
            i += 1
            continue

        # 8. Non-CJK tokens (Latin words, digits, ASCII punctuation)
        j = i
        while j < n and not is_cjk(text[j]) and text[j] not in CJK_PUNCT_MAP and text[j] not in " \t\n<":
            if text[j:j + 2] == "![" or text[j:j + 3] == "```":
                break
            j += 1
        tokens.append((text[i:j], "raw"))
        i = j

    # Assemble tokens with correct spacing
    out: list[str] = []
    for idx, (val, t_type) in enumerate(tokens):
        prev_type = tokens[idx - 1][1] if idx > 0 else None
        prev_val = tokens[idx - 1][0] if idx > 0 else None

        if t_type == "word":
            if prev_type in ("word", "raw"):
                out.append(" ")
            elif prev_type == "punct" and prev_val in (",", ".", ":", ";", "!", "?", ")", "]", "}", "”", ">"):
                out.append(" ")
            out.append(val)
        elif t_type == "punct":
            if val in ("(", "[", "{", "“", "<"):
                if prev_type in ("word", "raw"):
                    out.append(" ")
            out.append(val)
        elif t_type == "raw":
            if prev_type in ("word",):
                out.append(" ")
            elif prev_type == "punct" and prev_val in (",", ".", ":", ";", "!", "?", ")", "]", "}", "”", ">"):
                out.append(" ")
            out.append(val)
        elif t_type in ("space", "newline"):
            out.append(val)

    assembled = "".join(out)

    # Sentence capitalization
    lines = assembled.split("\n")
    cap_lines = []
    for line in lines:
        def _cap(m: re.Match) -> str:
            return m.group(1) + m.group(2).upper()

        line = re.sub(r"(^|[\.\!\?]\s+)([a-zà-ỹ])", _cap, line)
        cap_lines.append(line)

    return "\n".join(cap_lines)
