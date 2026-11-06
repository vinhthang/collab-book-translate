"""
scripts/evaluators/scoring.py — Dimension Scoring & Issue Realignment (REQ-004 / AC-004.1).
"""

import re
import unicodedata
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from scripts.helper import (
    calculate_dimension_score,
    count_syllables,
    normalize_span,
    parse_segments,
)

count_syllables_vietnamese = count_syllables

DIMENSION_WEIGHTS: Dict[str, float] = {
    "fidelity": 0.25,
    "fluency": 0.20,
    "terminology": 0.20,
    "register": 0.15,
    "structure": 0.10,
    "completeness": 0.10,
}

SEVERITY_WEIGHTS: Dict[str, float] = {
    "minor": 1.0,
    "major": 5.0,
    "critical": 25.0,
}


def compute_weighted_total(
    dimensions: Dict[str, Any],
    weights: Optional[Dict[str, float]] = None,
) -> float:
    """Computes weighted total score from dimension scores and standard weights."""
    if not dimensions:
        return 5.0
    w_dict = weights if weights is not None else DIMENSION_WEIGHTS
    valid_dims = {
        d: float(dimensions[d])
        for d in w_dict
        if d in dimensions and isinstance(dimensions[d], (int, float))
    }
    if not valid_dims:
        return 5.0
    total_w = sum(w_dict[d] for d in valid_dims)
    if total_w <= 0:
        return 5.0
    weighted_sum = sum(valid_dims[d] * w_dict[d] for d in valid_dims)
    return round(weighted_sum / total_w, 2)


def _normalize_for_span_search(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    s = s.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def find_normalized_span_in_text(raw_text: str, target_span: str) -> Optional[str]:
    """Finds exact verbatim slice of target_span in raw_text using normalized comparison."""
    if not raw_text or not target_span:
        return None
    if target_span in raw_text:
        return target_span

    # Try case-insensitive exact substring
    idx = raw_text.lower().find(target_span.lower())
    if idx != -1:
        return raw_text[idx : idx + len(target_span)]

    norm_span = _normalize_for_span_search(target_span).strip(".,;:!?'\"()[]{}").lower()
    if not norm_span:
        return None

    # Sliding word window
    raw_words = list(re.finditer(r"\S+", raw_text))
    target_words = norm_span.split()
    n_tw = len(target_words)
    if n_tw > 0 and len(raw_words) >= n_tw:
        for i in range(len(raw_words) - n_tw + 1):
            window_raw_words = raw_words[i : i + n_tw]
            start_char = window_raw_words[0].start()
            end_char = window_raw_words[-1].end()
            candidate = raw_text[start_char:end_char]
            norm_cand = _normalize_for_span_search(candidate).strip(".,;:!?'\"()[]{}").lower()
            if norm_cand == norm_span:
                return candidate

    # Character-level non-space mapping
    norm_chars = []
    char_map = []
    for idx_c, c in enumerate(raw_text):
        c_nfc = unicodedata.normalize("NFC", c)
        c_uncurl = c_nfc.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
        for nc in c_uncurl:
            if not nc.isspace():
                norm_chars.append(nc.lower())
                char_map.append(idx_c)

    target_norm = "".join(
        c.lower()
        for c in unicodedata.normalize("NFC", target_span)
        .replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
        if not c.isspace()
    ).strip(".,;:!?'\"()[]{}")

    if target_norm and norm_chars:
        ns_full = "".join(norm_chars)
        pos = ns_full.find(target_norm)
        if pos != -1:
            start_idx = char_map[pos]
            end_idx = char_map[pos + len(target_norm) - 1] + 1
            return raw_text[start_idx:end_idx]

    return None


def validate_and_realign_issue_spans(
    issues: List[Dict[str, Any]],
    segments: Union[List[Dict[str, Any]], str],
    draft_text: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    REQ-003 / AC-003.1..3:
    Validates and snaps every non-empty span_target to a byte-for-byte substring of the chapter
    draft's <translate> segments before dimension score and verdict calculation.
    """
    if isinstance(segments, str):
        parsed_segs = parse_segments(segments) if "<segment " in segments else []
        if not parsed_segs:
            parsed_segs = [{"id": "S-0001", "translate": segments, "original": segments}]
    elif isinstance(segments, list):
        parsed_segs = segments
    else:
        parsed_segs = []

    if not parsed_segs and draft_text:
        parsed_segs = parse_segments(draft_text)

    seg_dict = {s.get("id"): s for s in parsed_segs if s.get("id")}
    realigned_or_unverified_count = 0

    for iss in issues:
        if not isinstance(iss, dict):
            continue

        dim = iss.get("dimension")
        target_span = iss.get("span_target")
        if dim == "completeness" or not target_span:
            continue

        target_span_str = str(target_span).strip()
        seg_id = iss.get("segment_id")

        # 1. Scoped first to issue.segment_id (if valid)
        if seg_id and seg_id in seg_dict:
            target_seg_text = seg_dict[seg_id].get("translate", "")
            found_match = find_normalized_span_in_text(target_seg_text, target_span_str)
            if found_match:
                if found_match != iss.get("span_target"):
                    iss["span_target"] = found_match
                    realigned_or_unverified_count += 1
                continue

        # 2. Scoped across chapter segments in ascending order (never crossing segment boundary)
        realigned = False
        if seg_id:
            for s in parsed_segs:
                s_id = s.get("id")
                if s_id == seg_id:
                    continue
                s_text = s.get("translate", "")
                found_match = find_normalized_span_in_text(s_text, target_span_str)
                if found_match:
                    iss["segment_id"] = s_id
                    iss["span_target"] = found_match
                    realigned_or_unverified_count += 1
                    realigned = True
                    break
        else:
            # Issue had no segment_id: search to snap span_target, but do not assign segment_id
            for s in parsed_segs:
                s_text = s.get("translate", "")
                found_match = find_normalized_span_in_text(s_text, target_span_str)
                if found_match:
                    if found_match != iss.get("span_target"):
                        iss["span_target"] = found_match
                        realigned_or_unverified_count += 1
                    realigned = True
                    break

        if realigned:
            continue

        # 3. Unanchored non-existent span
        iss["unverified_span"] = True
        iss["unverified_span_target"] = iss.get("span_target", "")
        iss["span_target"] = ""
        prob = str(iss.get("problem", ""))
        if not prob.startswith("[UNVERIFIED_SPAN] "):
            iss["problem"] = f"[UNVERIFIED_SPAN] {prob}"
        if iss.get("description") and not str(iss["description"]).startswith("[UNVERIFIED_SPAN] "):
            iss["description"] = f"[UNVERIFIED_SPAN] {iss['description']}"
        if iss.get("severity") == "critical":
            iss["severity"] = "major"
        realigned_or_unverified_count += 1

    return issues, realigned_or_unverified_count


UNTRANSLATED_PENALTY_RE = re.compile(
    r"(untranslated|chưa dịch|còn nguyên tiếng anh|từ tiếng anh|foreign word|english term|giữ nguyên từ gốc)",
    re.IGNORECASE,
)
MISTRANSLATION_EXCLUSION_RE = re.compile(
    r"(dịch sai|sai nghĩa|mistranslat|wrong meaning|inconsistent with glossary)",
    re.IGNORECASE,
)


def filter_shielded_verbatim_issues(
    issues: List[Dict[str, Any]],
    book_record: Optional[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Suppresses untranslated-loanword issues for shielded verbatim terms while
    preserving genuine mistranslations before score calculation.
    """
    if not issues or not book_record:
        return issues, 0

    shielded: Set[str] = set()
    v_terms = book_record.get("verbatim_terms", [])
    if isinstance(v_terms, list):
        for v in v_terms:
            if isinstance(v, str):
                shielded.add(v.strip().lower())

    br_nested = book_record.get("book_record", {})
    if isinstance(br_nested, dict):
        nv_terms = br_nested.get("verbatim_terms", [])
        if isinstance(nv_terms, list):
            for v in nv_terms:
                if isinstance(v, str):
                    shielded.add(v.strip().lower())

    all_terms = list(book_record.get("terms", []) or [])
    if isinstance(br_nested, dict):
        all_terms.extend(br_nested.get("terms", []) or [])
    for t in all_terms:
        if isinstance(t, dict) and t.get("verbatim") is True:
            src = t.get("source", "").strip().lower()
            if src:
                shielded.add(src)

    if not shielded:
        return issues, 0

    filtered: List[Dict[str, Any]] = []
    suppressed_count = 0

    for iss in issues:
        if not isinstance(iss, dict):
            filtered.append(iss)
            continue

        dim = iss.get("dimension")
        if dim in ("terminology", "fluency", "style"):
            target_val = iss.get("span_target") or iss.get("span_source") or ""
            norm_target = str(target_val).strip('\"\'()[] \t\n').lower()
            if norm_target in shielded:
                problem = str(iss.get("problem", ""))
                if UNTRANSLATED_PENALTY_RE.search(problem) and not MISTRANSLATION_EXCLUSION_RE.search(problem):
                    suppressed_count += 1
                    continue

        filtered.append(iss)

    return filtered, suppressed_count


def extract_translatable_prose(draft_text: Union[str, List[Dict[str, Any]]]) -> str:
    """
    Extracts translatable <translate> prose, stripping XML tags, <original> text,
    figure/code segments, and context="true" segments (REQ-QUAL-001 / AC-QUAL-001-1).
    Falls back to stripping code blocks and images from plain Markdown drafts.
    """
    if isinstance(draft_text, list):
        return "\n\n".join(
            str(s.get("translate", ""))
            for s in draft_text
            if isinstance(s, dict)
            and s.get("type") not in ("figure", "code")
            and not s.get("is_context")
            and s.get("translate")
        )

    if isinstance(draft_text, str) and "<segment" in draft_text:
        parts: List[str] = []
        for m in re.finditer(r'<segment\b([^>]*)>([\s\S]*?)</segment>', draft_text):
            attrs = m.group(1)
            body = m.group(2)
            if 'type="figure"' in attrs or 'type="code"' in attrs or "type='figure'" in attrs or "type='code'" in attrs:
                continue
            if 'context="true"' in attrs or "context='true'" in attrs or 'is_context="true"' in attrs or "is_context='true'" in attrs:
                continue
            tr_match = re.search(r'<translate>([\s\S]*?)</translate>', body)
            if tr_match:
                tr_text = tr_match.group(1).strip()
                if tr_text:
                    parts.append(tr_text)
        return "\n\n".join(parts)

    # Plain markdown string fallback
    text = str(draft_text or "")
    # Strip fenced code blocks
    text = re.sub(r'```[\s\S]*?```', '', text)
    # Strip inline images
    text = re.sub(r'!\[[^\]]*\]\([^\)]+\)', '', text)
    return text.strip()


def compute_penalty_dimension_scores(
    issues: List[Dict[str, Any]],
    draft_text: Union[str, List[Dict[str, Any]]],
) -> Dict[str, float]:
    """
    Computes penalty-derived dimension scores strictly over translatable prose (REQ-QUAL-001 / AC-QUAL-001-2).
    """
    prose = extract_translatable_prose(draft_text)
    syllable_count = max(1, count_syllables_vietnamese(prose))
    dim_penalties = {d: 0.0 for d in DIMENSION_WEIGHTS}
    for iss in (issues or []):
        if not isinstance(iss, dict):
            continue
        dim = iss.get("dimension")
        if dim in dim_penalties:
            sev = str(iss.get("severity", "minor")).lower()
            dim_penalties[dim] += SEVERITY_WEIGHTS.get(sev, 1.0)

    return {
        d: round(calculate_dimension_score(dim_penalties[d], syllable_count), 2)
        for d in DIMENSION_WEIGHTS
    }


def compute_hybrid_weighted_total(
    parsed_or_issues: Any,
    draft_text_or_dims: Any,
    draft_text: Optional[str] = None,
) -> float:
    """
    Computes hybrid weighted total score applying LLM floor when issues list is empty.
    Supports both calling conventions:
    1. evaluate.py: compute_hybrid_weighted_total(issues, dims, draft_text)
    2. workbuddy.py: compute_hybrid_weighted_total(parsed, window_draft)
    """
    if draft_text is None and isinstance(parsed_or_issues, dict):
        parsed = parsed_or_issues
        issues = parsed.get("issues", [])
        dims = parsed.get("scores", parsed.get("dimensions", {}))
        actual_draft = draft_text_or_dims
        parsed_weighted_total = parsed.get("weighted_total")
    else:
        issues = parsed_or_issues or []
        dims = draft_text_or_dims or {}
        actual_draft = draft_text
        parsed_weighted_total = None

    prose = extract_translatable_prose(actual_draft)
    syllable_count = max(1, count_syllables_vietnamese(prose))
    dim_penalties = {d: 0.0 for d in DIMENSION_WEIGHTS}
    for iss in issues:
        dim = iss.get("dimension")
        if dim in dim_penalties:
            sev = str(iss.get("severity", "minor")).lower()
            dim_penalties[dim] += SEVERITY_WEIGHTS.get(sev, 1.0)

    issue_dims = {
        d: calculate_dimension_score(dim_penalties[d], syllable_count)
        for d in DIMENSION_WEIGHTS
    }
    issue_derived_total = round(
        sum(issue_dims[d] * DIMENSION_WEIGHTS[d] for d in DIMENSION_WEIGHTS), 3
    )

    if not issues and dims:
        llm_weighted_total = round(
            sum(float(dims.get(d, 5.0)) * DIMENSION_WEIGHTS[d] for d in DIMENSION_WEIGHTS), 3
        )
        return min(issue_derived_total, llm_weighted_total)
    elif not issues and parsed_weighted_total is not None and not dims:
        return min(issue_derived_total, float(parsed_weighted_total))

    return issue_derived_total
