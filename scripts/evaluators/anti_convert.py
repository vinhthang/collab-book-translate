"""scripts/evaluators/anti_convert.py — Anti-convert catalog and evaluation rules for Chinese translations."""

import re
from typing import Any, Dict, List, Optional, Tuple

ANTI_CONVERT_RULES = [
    {
        "id": "AC-01",
        "pattern": re.compile(r"\bbất\s+quá\b(?!\s+(?:tam|chỉ|cũng\s+chỉ|là|khoảng|chừng|độ|tầm|hơn|gần|mấy|vài|dăm|đôi|mươi|nửa|chục|tá|một|hai|ba|bốn|năm|sáu|bảy|tám|chín|mười|trăm|nghìn|ngàn|vạn|\d+)\b)", re.IGNORECASE),
        "replacement": "tuy nhiên / nhưng / chẳng qua chỉ là",
        "reason": "Lạm dụng Hán-Việt convert; thay bằng liên từ thuần Việt tự nhiên.",
    },
    {
        "id": "AC-02",
        "pattern": re.compile(r"\bđốn thời\b", re.IGNORECASE),
        "replacement": "ngay lập tức / tức thì / bỗng chốc",
        "reason": "Từ Hán-Việt cổ; thay bằng từ ngữ hiện đại.",
    },
    {
        "id": "AC-03",
        "pattern": re.compile(r"\bphất thủ\b", re.IGNORECASE),
        "replacement": "xua tay / vung tay",
        "reason": "Hành động thô kiểu convert kiếm hiệp; thay bằng động từ thuần Việt.",
    },
    {
        "id": "AC-04",
        "pattern": re.compile(r"\bhạ nhất khắc\b", re.IGNORECASE),
        "replacement": "ngay sau đó / khoảnh khắc tiếp theo",
        "reason": "Ngữ pháp gốc Trung '下一刻' dịch thô; dùng cụm từ tiếng Việt tự nhiên.",
    },
    {
        "id": "AC-05",
        "pattern": re.compile(r"\bsắc mặt ngưng trọng\b", re.IGNORECASE),
        "replacement": "vẻ mặt nghiêm nghị / nét mặt căng thẳng",
        "reason": "Cụm từ Hán-Việt sáo rỗng; thay bằng diễn đạt tự nhiên.",
    },
    {
        "id": "AC-06",
        "pattern": re.compile(r"\bchỉ kiến\b", re.IGNORECASE),
        "replacement": "chỉ thấy / trông thấy",
        "reason": "Từ Hán-Việt thô '只见'; thay bằng 'chỉ thấy'.",
    },
    {
        "id": "AC-07",
        "pattern": re.compile(r"\bnhãn mâu\b", re.IGNORECASE),
        "replacement": "ánh mắt / đôi mắt",
        "reason": "Lạm dụng 'nhãn mâu' thay vì 'đôi mắt' hoặc 'ánh mắt'.",
    },
    {
        "id": "AC-08",
        "pattern": re.compile(r"\bkhóe miệng co giật\b", re.IGNORECASE),
        "replacement": "khóe môi giật giật / khóe miệng run run",
        "reason": "Cụm từ sáo rỗng dịch máy thô '嘴角抽搐'.",
    },
    {
        "id": "AC-09",
        "pattern": re.compile(r"(?<!điều\s)(?<!niềm\s)(?<!sự\s)\btâm đắc\b", re.IGNORECASE),
        "replacement": "kinh nghiệm tâm huyết / điều tâm đắc",
        "reason": "Dùng 'tâm đắc' sai ngữ pháp như động từ/tính từ thô kiểu Trung; chỉ giữ khi dùng như danh từ ('điều tâm đắc', 'niềm tâm đắc').",
    },
]

ANTI_CONVERT_REWRITE_MAP: List[Tuple[re.Pattern, str]] = [
    # Only clause-initial "Bất quá," followed by comma (preserving "bất quá chỉ...", "bất quá tam")
    (re.compile(r"(?:^|(?<=[.!?]\s))(bất quá)(?=\s*,)", re.IGNORECASE), "tuy nhiên"),
    (re.compile(r"\bđốn thời\b", re.IGNORECASE), "lập tức"),
    (re.compile(r"\bphất thủ\b", re.IGNORECASE), "vung tay"),
    (re.compile(r"\bhạ nhất khắc\b", re.IGNORECASE), "ngay khoảnh khắc tiếp theo"),
    (re.compile(r"\bsắc mặt ngưng trọng\b", re.IGNORECASE), "vẻ mặt nghiêm trọng"),
    (re.compile(r"\bchỉ kiến\b", re.IGNORECASE), "chỉ thấy"),
    (re.compile(r"\bnhãn mâu\b", re.IGNORECASE), "ánh mắt"),
    (re.compile(r"\bkhóe miệng co giật\b", re.IGNORECASE), "khóe môi giật nhẹ"),
    (re.compile(r"\bvẫn là\b(?=\s+(?:nên|phải|không nên|đừng|cần|hãy)\b)", re.IGNORECASE), "tốt hơn vẫn nên"),
    (re.compile(r"\bliên tục không ngừng\b", re.IGNORECASE), "không ngừng"),
    (re.compile(r"\bmột mặt\s+([^,.!?;:]{2,30}?)\s+dáng vẻ\b", re.IGNORECASE), r"vẻ mặt \1"),
    (re.compile(r"\bhít một ngụm khí lạnh\b", re.IGNORECASE), "hít sâu một hơi lạnh"),
    (re.compile(r"\btrong lòng dâng lên một cỗ\b", re.IGNORECASE), "trong lòng trào dâng nỗi"),
    (re.compile(r"\bnội bộ\s+(cấu trúc|logic|cơ chế|trạng thái|thuật toán)\b", re.IGNORECASE), r"\1 bên trong"),
    (re.compile(r"\bcờ răng\b", re.IGNORECASE), "cờ hiệu"),
]


def _apply_rewrite(text: str, pattern: re.Pattern, repl: str) -> Tuple[str, int]:
    count = 0
    has_backref = bool(re.search(r'\\[1-9]|\\g<', repl))

    def replacer(m: re.Match) -> str:
        nonlocal count
        count += 1
        if has_backref:
            expanded = m.expand(repl)
            matched_str = m.group(0)
            if matched_str and matched_str[0].isupper():
                expanded = expanded[0].upper() + expanded[1:]
            return expanded
        elif m.lastindex and m.group(1):
            matched_target = m.group(1)
            expanded = m.expand(repl)
            if matched_target[0].isupper():
                expanded = expanded[0].upper() + expanded[1:]
            full_match = m.group(0)
            g1_start = m.start(1) - m.start(0)
            g1_end = m.end(1) - m.start(0)
            return full_match[:g1_start] + expanded + full_match[g1_end:]
        else:
            expanded = m.expand(repl)
            matched_str = m.group(0)
            if matched_str and matched_str[0].isupper():
                expanded = expanded[0].upper() + expanded[1:]
            return expanded

    new_text = pattern.sub(replacer, text)
    return new_text, count


def sanitize_chinese_translation(
    text: str,
    source_lang: str = "zh",
    book_record: Optional[Dict[str, Any]] = None,
) -> Tuple[str, int]:
    """
    REQ-004 / AC-004.1..3:
    Deterministically sanitizes Chinese-to-Vietnamese translation outputs:
    1. Replaces context-guarded anti-convert calques with natural Vietnamese phrasing.
    2. Strips radical decompositions and normalizes rare-character glosses.
    3. Replaces non-canonical variants declared in book_record on word boundaries.
    """
    if source_lang != "zh" or not text:
        return text, 0

    total_replacements = 0
    cur_text = text

    # 1. AC-004.3: Hallucinated parenthetical character-component breakdowns
    ch_hiem_re = re.compile(r"\(chữ hiếm:[^)]*\)", re.IGNORECASE)
    cur_text, n1 = ch_hiem_re.subn("(chữ hiếm)", cur_text)
    total_replacements += n1

    radical_re = re.compile(r"\([^)]*bộ\s+[^)]*ghép chữ[^)]*\)", re.IGNORECASE)
    cur_text, n2 = radical_re.subn("", cur_text)
    total_replacements += n2

    # 2. AC-004.1: Deterministic single-target rewrite map
    for pattern, repl in ANTI_CONVERT_REWRITE_MAP:
        cur_text, cnt = _apply_rewrite(cur_text, pattern, repl)
        total_replacements += cnt

    # 3. AC-004.2: Data-driven proper-noun canonicalization from book_record
    if book_record and isinstance(book_record, dict):
        variant_map: Dict[str, str] = {}
        for item in book_record.get("names", []):
            if isinstance(item, dict) and item.get("target"):
                tgt = item["target"]
                all_vars = list(item.get("variants", []) or []) + list(item.get("incorrect_variants", []) or [])
                for v in all_vars:
                    if v and v != tgt and isinstance(v, str):
                        variant_map[v] = tgt

        for item in book_record.get("terms", []):
            if isinstance(item, dict) and item.get("target"):
                tgt = item["target"].split("(")[0].strip()
                all_vars = list(item.get("variants", []) or []) + list(item.get("incorrect_variants", []) or [])
                for v in all_vars:
                    if v and v != tgt and isinstance(v, str):
                        variant_map[v] = tgt

        for corr in book_record.get("corrections", []):
            if isinstance(corr, dict) and corr.get("was") and corr.get("now"):
                variant_map[corr["was"]] = corr["now"]

        for v, tgt in sorted(variant_map.items(), key=lambda x: len(x[0]), reverse=True):
            p = re.compile(r"\b" + re.escape(v) + r"\b", re.IGNORECASE)
            cur_text, cnt = _apply_rewrite(cur_text, p, tgt)
            total_replacements += cnt

    return cur_text, total_replacements


def detect_anti_convert_terms(text: str) -> List[Dict[str, Any]]:
    """Convenience alias for detecting anti-convert terms in Vietnamese text."""
    return check_anti_convert_issues(text, source_lang="zh")


def check_anti_convert_issues(draft_text: str, source_lang: str) -> List[Dict[str, Any]]:
    """Checks draft text for Chinese-Vietnamese convert / calque artifacts.

    Strictly gated to run only when source_lang == 'zh'. Returns empty list for any other language.
    """
    if source_lang != "zh":
        return []
    issues = []
    for rule in ANTI_CONVERT_RULES:
        for match in rule["pattern"].finditer(draft_text):
            issues.append({
                "id": f"ac_{rule['id']}_{match.start()}",
                "dimension": "fluency",
                "severity": "major",
                "span_target": match.group(0),
                "problem": f"Phát hiện từ ngữ Hán-Việt thô/convert: '{match.group(0)}'. {rule['reason']}",
                "suggestion": f"Thay bằng: {rule['replacement']}",
            })
    return issues
