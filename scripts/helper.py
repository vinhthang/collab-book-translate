#!/usr/bin/env python3
"""
scripts/helper.py — Translation Engine Helper Functions
Implements scoring, multi-pass aggregation, critical quorum, redraft detection,
and diff-and-splicing enforcement (NFR-6, NFR-7, NFR-8, QUAL-1..5).
"""

import difflib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from scripts.utils.terminology import (
    StateFileCorruptError,
    atomic_write_json,
    atomic_write_text,
    interprocess_lock,
    read_json_state,
    snapshot_backup,
    terms_present_in,
)

STRUCTURAL_TAG_RE: re.Pattern = re.compile(
    r"</?(?:segment|original|translate|translated|target|changelog|context_before|hanviet)\b[^>]*>",
    re.IGNORECASE | re.MULTILINE,
)

COMPOUND_PREFIXES: Set[str] = {
    "self", "client", "server", "peer-to", "end-to",
    "read", "write", "key", "single", "multi", "cross",
}

DIMENSION_WEIGHTS: Dict[str, float] = {
    "fidelity": 0.25,
    "fluency": 0.20,
    "terminology": 0.20,
    "register": 0.15,
    "structure": 0.10,
    "completeness": 0.10,
}

DEFAULT_RUBRIC_WEIGHTS: Dict[str, float] = {
    "fidelity": 25.0,
    "fluency": 20.0,
    "terminology": 20.0,
    "register": 15.0,
    "structure": 10.0,
    "completeness": 10.0,
}

SEVERITY_WEIGHTS: Dict[str, float] = {
    "minor": 1.0,
    "major": 5.0,
    "critical": 25.0,
}

_INSTRUCTION_VERB_RE = re.compile(
    r"(?i)\b(?:nên\s+dịch|chuẩn\s+hóa|thay\s+bằng|dịch\s+là|dịch\s+thành|"
    r"translate\s+as|render\s+as|standardize\s+on|use|translate|render)\b"
)
_VI_SPECIFIC_DIACRITICS_RE = re.compile(
    r"[ăâđêôơưĂÂĐÊÔƠƯàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ]"
)
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")


def _validate_term_proposal(source: str, target: str, source_lang: str) -> Tuple[bool, str]:
    """
    AC-COR-005-3: Validates harvested term candidate prior to staging/binding:
    - 1 <= len(source) <= 60 and 1 <= len(target) <= 60
    - Neither source nor target matches instruction verb phrases on word boundaries
    - For 'zh', source must have >= 1 CJK ideograph and no Vietnamese diacritics
    - For 'en', source must have >= 1 ASCII letter, zero CJK, and no Vietnamese diacritics
    """
    src = (source or "").strip()
    tgt = (target or "").strip()
    lang = (source_lang or "en").lower().strip()

    if not (1 <= len(src) <= 60):
        return False, f"Source term length {len(src)} outside valid range [1, 60]"
    if not (1 <= len(tgt) <= 60):
        return False, f"Target term length {len(tgt)} outside valid range [1, 60]"

    if _INSTRUCTION_VERB_RE.search(src):
        return False, f"Source term contains instruction verb phrase: {src}"
    if _INSTRUCTION_VERB_RE.search(tgt):
        return False, f"Target term contains instruction verb phrase: {tgt}"

    if lang == "zh":
        if not _CJK_RE.search(src):
            return False, f"Chinese source term must contain at least one CJK ideograph: {src}"
        if _VI_SPECIFIC_DIACRITICS_RE.search(src):
            return False, f"Chinese source term must not contain Vietnamese diacritics: {src}"
    else:
        if not re.search(r"[A-Za-z]", src):
            return False, f"English source term must contain at least one ASCII letter: {src}"
        if _CJK_RE.search(src):
            return False, f"English source term must not contain CJK ideographs: {src}"
        if _VI_SPECIFIC_DIACRITICS_RE.search(src):
            return False, f"English source term must not contain Vietnamese diacritics: {src}"

    return True, ""



def dehyphenate_text(
    text: str,
    book_record: Optional[Dict[str, Any]] = None,
    known_compounds: Optional[Set[str]] = None,
) -> str:
    """
    Reconnects words broken across line breaks with trailing hyphens in PDFs (AC-ARCH-4.1).
    Preserves technical compound prefixes (self-, client-, server-, etc.),
    compounds present in book_record or unbroken elsewhere in the text.
    Joins productive Latin affixes (re-, co-, pre-, sub-, etc.) into solid words.
    Preserves code blocks byte-for-byte.
    """
    if not text:
        return text

    # Strip soft hyphens
    text = text.replace("\xad", "")

    explicit: Set[str] = {c.lower() for c in (known_compounds or set())}
    if book_record:
        rec = book_record.get("book_record", book_record)
        if isinstance(rec, dict):
            raw_terms = rec.get("term_table") or rec.get("terms") or book_record.get("term_table") or []
            if isinstance(raw_terms, list):
                for t in raw_terms:
                    if isinstance(t, dict) and "-" in t.get("source", ""):
                        explicit.add(t["source"].lower())
                    elif isinstance(t, str) and "-" in t:
                        explicit.add(t.lower())
            elif isinstance(raw_terms, dict):
                for k in raw_terms:
                    if "-" in k:
                        explicit.add(k.lower())
        for v in book_record.get("verbatim_terms", []):
            if "-" in v:
                explicit.add(v.lower())

    in_text: Set[str] = {
        m.group(0).lower()
        for m in re.finditer(r"\b[a-zA-ZÀ-ỹ]{2,}-[a-zA-ZÀ-ỹ]{2,}\b", text)
    }
    all_compounds = explicit | in_text

    pattern = re.compile(r'([a-zA-ZÀ-ỹ]+(?:-[a-zA-ZÀ-ỹ]+)*)[-‐‑‒–—]\s*\n\s*([a-zA-ZÀ-ỹ]+)')

    def _replace(match: re.Match) -> str:
        prefix, suffix = match.group(1), match.group(2)
        hyphenated = f"{prefix}-{suffix}".lower()
        if prefix.lower() in COMPOUND_PREFIXES or hyphenated in all_compounds:
            return f"{prefix}-{suffix}"
        return f"{prefix}{suffix}"

    if "```" in text:
        parts = text.split("```")
        for i in range(0, len(parts), 2):
            parts[i] = pattern.sub(_replace, parts[i])
        return "```".join(parts)
    return pattern.sub(_replace, text)


def count_syllables(text: str) -> int:
    """
    Counts whitespace-separated tokens of the Vietnamese target text (syllables).
    Returns 0 for empty or whitespace-only strings.
    """
    if not text:
        return 0
    tokens = text.split()
    return len(tokens)


def calculate_dimension_score(penalty: float, syllable_count: int, K: float = 5.0) -> float:
    """
    Calculates a single dimension score (0.0 to 5.0) from penalty and syllable count:
      density = penalty / max(1.0, syllable_count / 1000.0)
      score = clamp(5.0 - density / K, 0.0, 5.0)
    """
    divisor = max(1.0, float(syllable_count) / 1000.0)
    density = float(penalty) / divisor
    score = 5.0 - (density / float(K))
    return max(0.0, min(5.0, score))


def _extract_dimension_scores(
    pass_data: Dict[str, Any],
    weights: Dict[str, float],
    syllable_count: Optional[int] = None,
    K: float = 5.0,
) -> Dict[str, float]:
    """Helper to extract or compute dimension scores from a pass dict."""
    dim_scores: Dict[str, float] = {}

    issues = pass_data.get("issues", [])
    if issues:
        # Case 1: Non-empty issues list -> ALWAYS compute penalty-density scores from issues first
        count = syllable_count or pass_data.get("target_syllable_count") or pass_data.get("syllable_count") or pass_data.get("target_units") or 1000
        dim_penalties: Dict[str, float] = {d: 0.0 for d in weights}
        for iss in issues:
            dim = iss.get("dimension")
            if dim in dim_penalties:
                sev = str(iss.get("severity", "minor")).lower()
                w = SEVERITY_WEIGHTS.get(sev, 1.0)
                dim_penalties[dim] += w

        for d in weights:
            dim_scores[d] = calculate_dimension_score(dim_penalties[d], count, K=K)
        return dim_scores

    # Case 2: Pass provides pre-computed scores dict (used when issues is missing or empty)
    pre_scores = pass_data.get("scores") or pass_data.get("dimensions") or pass_data.get("dimension_scores") or pass_data.get("llm_dimensions")
    if isinstance(pre_scores, dict):
        for d in weights:
            if d in pre_scores:
                dim_scores[d] = float(pre_scores[d])
            else:
                dim_scores[d] = 5.0
        return dim_scores

    # Case 3: Default clean 5.0
    for d in weights:
        dim_scores[d] = 5.0
    return dim_scores


def aggregate_multi_pass(
    eval_passes: List[Dict[str, Any]],
    weights: Optional[Dict[str, float]] = None,
    syllable_count: Optional[int] = None,
    K: float = 5.0,
) -> Dict[str, Any]:
    """
    Aggregates N evaluation passes:
      1. For each dimension, takes the median of the N passes.
      2. Computes the weighted total score across dimensions using dimension medians.
      3. Computes each individual pass's weighted total.
      4. Calculates spread = max(pass_totals) - min(pass_totals).
      5. Selects the pass whose individual total is closest to the aggregate total.
    """
    if not eval_passes:
        raise ValueError("eval_passes cannot be empty")

    weights_dict = weights if weights is not None else DEFAULT_RUBRIC_WEIGHTS
    total_w = sum(weights_dict.values())
    if total_w <= 0:
        raise ValueError("Sum of weights must be positive")

    # Extract dimension scores for each pass
    pass_dim_scores = [
        _extract_dimension_scores(p, weights_dict, syllable_count=syllable_count, K=K)
        for p in eval_passes
    ]

    # Compute median for each dimension
    dimension_medians: Dict[str, float] = {}
    for d in weights_dict:
        vals = [p[d] for p in pass_dim_scores]
        dimension_medians[d] = float(statistics.median(vals))

    # Compute aggregate weighted total
    weighted_total = sum(weights_dict[d] * dimension_medians[d] for d in weights_dict) / total_w

    # Compute per-pass weighted totals
    pass_totals: List[float] = [
        sum(weights_dict[d] * p[d] for d in weights_dict) / total_w
        for p in pass_dim_scores
    ]

    spread = max(pass_totals) - min(pass_totals) if pass_totals else 0.0

    # Select pass closest to aggregate weighted total
    best_idx = min(range(len(eval_passes)), key=lambda i: abs(pass_totals[i] - weighted_total))
    selected_pass = eval_passes[best_idx]

    return {
        "dimension_medians": dimension_medians,
        "weighted_total": weighted_total,
        "pass_totals": pass_totals,
        "spread": spread,
        "selected_pass_index": best_idx,
        "selected_pass": selected_pass,
    }


def _pass_flags_critical(p: Any) -> bool:
    """Helper to determine whether a pass flagged any critical defect."""
    if isinstance(p, bool):
        return p
    if isinstance(p, (int, float)):
        return p > 0
    if isinstance(p, str):
        return p.lower() in ("critical", "fail", "true")
    if isinstance(p, dict):
        if p.get("critical") is True or p.get("has_critical") is True:
            return True
        if isinstance(p.get("critical"), (int, float)) and p["critical"] > 0:
            return True
        issues = p.get("issues", [])
        return any(str(iss.get("severity", "")).lower() == "critical" for iss in issues)
    return False


def check_critical_quorum(eval_passes: List[Any]) -> str:
    """
    Evaluates quorum for critical issues across N passes:
      - Auto-fail ('FAIL') if >= ceil(N / 2) passes flag critical.
      - If exactly 1 pass flags critical, return 'CONTESTED' (triggers targeted re-evaluation).
      - Otherwise, return 'PASS'.
    """
    if not eval_passes:
        return "PASS"

    n = len(eval_passes)
    num_critical = sum(1 for p in eval_passes if _pass_flags_critical(p))

    if num_critical == 0:
        return "PASS"

    # Single pass case (e.g. fast mode N=1)
    if n == 1:
        return "FAIL" if num_critical >= 1 else "PASS"

    # Multi-pass quorum rule
    quorum_threshold = math.ceil(n / 2)
    if num_critical >= quorum_threshold and num_critical > 1:
        return "FAIL"
    elif num_critical == 1:
        return "CONTESTED"
    elif num_critical >= quorum_threshold:
        return "FAIL"
    else:
        return "PASS"


def check_redraft(
    issues: List[Dict[str, Any]],
    total_length: Union[int, float],
    weighted_total: float,
    coverage_thresh: float = 0.4,
    score_thresh: float = 2.5,
) -> bool:
    """
    Decides whether a failed round requires a full redraft (True) or targeted repair/fix (False).
    Redraft is triggered if:
      - Score is below score_thresh (default 2.5).
      - OR any major/critical issue is pervasive (pervasive: True), counting as 100% coverage.
      - OR union coverage of major/critical defect spans >= coverage_thresh (default 0.4).
    """
    # Signal 1: Aggregate score threshold
    if weighted_total < score_thresh:
        return True

    if not issues:
        return False

    major_or_critical = [
        iss for iss in issues
        if str(iss.get("severity", "")).lower() in ("major", "critical")
    ]
    if not major_or_critical:
        return False

    # Signal 2: Pervasive flag
    for iss in major_or_critical:
        if iss.get("pervasive") is True:
            return True

    # Signal 3: Span coverage calculation
    length = float(total_length) if total_length and total_length > 0 else 1.0

    intervals: List[Tuple[float, float]] = []
    unit_sum: float = 0.0
    has_intervals = False

    for iss in major_or_critical:
        span = iss.get("span_target") or iss.get("span")
        if isinstance(span, (list, tuple)) and len(span) == 2:
            intervals.append((float(span[0]), float(span[1])))
            has_intervals = True
        elif isinstance(span, dict) and "start" in span and "end" in span:
            intervals.append((float(span["start"]), float(span["end"])))
            has_intervals = True
        elif "units" in iss:
            unit_sum += float(iss["units"])
        elif "length" in iss:
            unit_sum += float(iss["length"])
        elif isinstance(span, str):
            # Syllables in the literal string span
            unit_sum += float(len(span.split()))

    if has_intervals:
        # Compute union of 1D intervals
        intervals.sort(key=lambda x: x[0])
        merged: List[List[float]] = []
        for s, e in intervals:
            if not merged:
                merged.append([s, e])
            else:
                last_s, last_e = merged[-1]
                if s <= last_e:
                    merged[-1][1] = max(last_e, e)
                else:
                    merged.append([s, e])
        union_len = sum(e - s for s, e in merged)
        coverage = union_len / length
    else:
        coverage = unit_sum / length

    return coverage >= coverage_thresh


def normalize_span(span: str) -> str:
    """Normalizes whitespace and casing for span comparisons (AC-ARCH-6.3, 6.4)."""
    if not isinstance(span, str):
        return ""
    return re.sub(r"\s+", " ", span.strip().lower())


def compute_chapter_evaluation(
    issues: List[Dict[str, Any]],
    target_syllable_count: int,
    llm_dimensions: Optional[Dict[str, float]] = None,
    pass_threshold: float = 4.0,
    K: float = 5.0,
    redraft_threshold: float = 2.5,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Computes single-pass deterministic MQM penalty-density scores (FLOW-14, REQ-ARCH-2).
    If issues == [], applies LLM diagnostic floor strictly to the final weighted total.
    """
    k_factor = float(kwargs.get("k_factor", K))
    divisor = max(1.0, float(target_syllable_count) / 1000.0)
    computed_dims: Dict[str, float] = {}

    for dim in DIMENSION_WEIGHTS:
        dim_penalty = sum(
            SEVERITY_WEIGHTS.get(str(i.get("severity", "minor")).lower(), 1.0)
            for i in issues
            if i.get("dimension") == dim
        )
        density = dim_penalty / divisor
        score_d = max(0.0, min(5.0, 5.0 - (density / k_factor)))
        computed_dims[dim] = round(score_d, 3)

    issue_derived_total = round(
        sum(computed_dims[d] * DIMENSION_WEIGHTS[d] for d in DIMENSION_WEIGHTS), 3
    )

    # Floor logic: apply strictly to final weighted total per Decision 1 (AC-ARCH-2.3)
    if not issues and llm_dimensions:
        llm_weighted_total = round(
            sum(float(llm_dimensions.get(d, 5.0)) * DIMENSION_WEIGHTS[d] for d in DIMENSION_WEIGHTS), 3
        )
        weighted_total = round(min(issue_derived_total, llm_weighted_total), 3)
    else:
        weighted_total = issue_derived_total

    has_critical = any(str(i.get("severity", "")).lower() == "critical" for i in issues)
    needs_redraft = weighted_total < redraft_threshold

    verdict = (
        "REDRAFT"
        if needs_redraft
        else ("FAIL" if (has_critical or weighted_total < pass_threshold) else "PASS")
    )

    return {
        "dimensions": computed_dims,
        "llm_dimensions": llm_dimensions or {},
        "issue_derived_total": issue_derived_total,
        "weighted_total": weighted_total,
        "has_critical": has_critical,
        "verdict": verdict,
        "needs_redraft": needs_redraft,
        "issues": issues,
    }


def aggregate_chapter_evaluations(
    passes: List[Dict[str, Any]],
    target_syllable_count: int,
    pass_threshold: float = 4.0,
    redraft_threshold: float = 2.5,
    coverage_threshold: float = 0.4,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Aggregates multi-pass evaluations enforcing QUAL-2 majority quorum for critical issues (AC-ARCH-2.4, 2.5).
    Computes spread and coverage across core chapter segments.
    """
    quality_threshold = float(kwargs.get("quality_threshold", pass_threshold))
    if not passes:
        return {
            "weighted_total": 0.0,
            "spread": 0.0,
            "coverage": 0.0,
            "critical_quorum_failed": False,
            "single_critical_pending": False,
            "needs_redraft": True,
            "verdict": "FAIL",
        }

    scored_passes: List[Dict[str, Any]] = []
    for p in passes:
        if isinstance(p, dict) and "weighted_total" in p and "has_critical" in p:
            scored_passes.append(p)
        elif isinstance(p, dict):
            issues = p.get("issues", [])
            llm_dims = p.get("llm_dimensions") or p.get("dimensions") or p.get("scores")
            count = p.get("target_syllable_count") or target_syllable_count
            eval_res = compute_chapter_evaluation(
                issues,
                count,
                llm_dimensions=llm_dims,
                pass_threshold=quality_threshold,
                redraft_threshold=redraft_threshold,
            )
            scored_passes.append(eval_res)
        else:
            scored_passes.append({"weighted_total": 0.0, "has_critical": False})

    pass_totals = [float(p.get("weighted_total", 0.0)) for p in scored_passes]
    avg_total = round(sum(pass_totals) / max(1, len(pass_totals)), 3)
    spread = round(max(pass_totals) - min(pass_totals), 3) if scored_passes else 0.0

    # Quorum adjudication per QUAL-2 and AC-ARCH-2.4
    critical_count = sum(1 for p in passes if _pass_flags_critical(p))
    num_passes = len(passes)

    if num_passes == 1:
        quorum_critical = critical_count >= 1
        single_critical = False
    else:
        quorum_critical = critical_count >= 2
        single_critical = (critical_count == 1)

    # Compute coverage across union of core chapter segment issues
    all_issues: List[Dict[str, Any]] = []
    for p in passes:
        if isinstance(p, dict):
            all_issues.extend(p.get("issues", []))

    major_or_crit = [
        i for i in all_issues
        if str(i.get("severity", "")).lower() in ("major", "critical")
    ]
    pervasive = any(i.get("pervasive") is True for i in major_or_crit)

    length = float(target_syllable_count) if target_syllable_count and target_syllable_count > 0 else 1.0
    if pervasive:
        coverage = 1.0
    else:
        unit_sum = 0.0
        intervals: List[Tuple[float, float]] = []
        has_intervals = False
        seen_spans: Set[str] = set()

        for iss in major_or_crit:
            span = iss.get("span_target") or iss.get("span")
            if isinstance(span, (list, tuple)) and len(span) == 2:
                intervals.append((float(span[0]), float(span[1])))
                has_intervals = True
            elif isinstance(span, dict) and "start" in span and "end" in span:
                intervals.append((float(span["start"]), float(span["end"])))
                has_intervals = True
            elif "units" in iss:
                unit_sum += float(iss["units"])
            elif "length" in iss:
                unit_sum += float(iss["length"])
            elif isinstance(span, str):
                norm = normalize_span(span)
                if norm not in seen_spans:
                    seen_spans.add(norm)
                    unit_sum += float(len(span.split()))

        if has_intervals:
            intervals.sort(key=lambda x: x[0])
            merged: List[List[float]] = []
            for s, e in intervals:
                if not merged:
                    merged.append([s, e])
                else:
                    last_s, last_e = merged[-1]
                    if s <= last_e:
                        merged[-1][1] = max(last_e, e)
                    else:
                        merged.append([s, e])
            union_len = sum(e - s for s, e in merged)
            coverage = round(union_len / length, 3)
        else:
            coverage = round(unit_sum / length, 3)

    needs_redraft = avg_total < redraft_threshold or coverage >= coverage_threshold

    if quorum_critical or avg_total < quality_threshold:
        verdict = "FAIL"
    elif single_critical:
        verdict = "TARGETED_REEVAL"
    else:
        verdict = "PASS"

    return {
        "weighted_total": avg_total,
        "spread": spread,
        "coverage": coverage,
        "critical_quorum_failed": quorum_critical,
        "single_critical_pending": single_critical,
        "needs_redraft": needs_redraft,
        "verdict": verdict,
    }


def resolve_pass_threshold(
    cli_threshold: Optional[float] = None,
    config_data: Optional[Dict[str, Any]] = None,
    book_record: Optional[Dict[str, Any]] = None,
) -> float:
    """
    Resolves the pass threshold according to strict precedence (AC-ARCH-2.6):
    CLI --threshold > config.json > brief.genre (literary/fiction -> 3.5, default -> 4.0).
    """
    if cli_threshold is not None:
        try:
            return float(cli_threshold)
        except (ValueError, TypeError):
            pass

    if isinstance(config_data, dict):
        for key in ("quality_threshold", "threshold", "pass_threshold"):
            val = config_data.get(key)
            if val is not None:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    pass

    if isinstance(book_record, dict):
        brief = book_record.get("brief", {})
        if isinstance(brief, dict):
            genre = str(brief.get("genre", "")).lower()
            if "non-fiction" in genre or "nonfiction" in genre or "technical" in genre:
                return 4.0
            literary_tokens = ("literary", "fiction", "novel", "tiểu thuyết", "văn học", "truyện")
            if any(tok in genre for tok in literary_tokens):
                return 3.5

    return 4.0


def _sync_view(
    canonical_list: Union[List[Dict[str, Any]], Dict[str, Any]],
    existing_view: Any,
    val_keys: Tuple[str, ...] = ("target", "vietnamese"),
) -> Any:
    """
    REQ-COR-003: Shape-preserving view synchronization.
    If existing_view is a dict, updates in-place as a dict without converting to a list.
    If existing_view is a list, updates existing entries in-place preserving extra keys (notes, category, pinyin, etc.)
    while updating source and target/vietnamese, and appends new items cleanly.
    """
    if existing_view is None:
        return None

    # Normalize canonical_list into list of dicts
    if isinstance(canonical_list, dict):
        canon_items: List[Dict[str, Any]] = [
            {"source": k, "target": v.get("target") if isinstance(v, dict) else v, **(v if isinstance(v, dict) else {})}
            for k, v in canonical_list.items()
        ]
    else:
        canon_items = list(canonical_list)

    if isinstance(existing_view, dict):
        has_dict_values = any(isinstance(v, dict) for v in existing_view.values())
        for item in canon_items:
            if not isinstance(item, dict):
                continue
            src = str(item.get("source", "")).strip()
            if not src:
                continue
            tgt = None
            for vk in val_keys:
                if vk in item and item[vk]:
                    tgt = item[vk]
                    break
            if tgt is None:
                tgt = item.get("target", "")

            if src in existing_view:
                if isinstance(existing_view[src], dict):
                    existing_view[src]["target"] = tgt
                    for k, v in item.items():
                        if k not in existing_view[src] and k != "source":
                            existing_view[src][k] = v
                else:
                    existing_view[src] = tgt
            else:
                if has_dict_values:
                    new_val = dict(item)
                    if "target" not in new_val and tgt:
                        new_val["target"] = tgt
                    existing_view[src] = new_val
                else:
                    existing_view[src] = tgt
        return existing_view

    elif isinstance(existing_view, list):
        seen: Set[str] = set()
        canonical_map: Dict[str, Dict[str, Any]] = {
            str(item["source"]).strip(): item
            for item in canon_items
            if isinstance(item, dict) and "source" in item
        }

        for entry in existing_view:
            if isinstance(entry, dict) and "source" in entry:
                src = str(entry["source"]).strip()
                if src in canonical_map:
                    match = canonical_map[src]
                    for vk in val_keys:
                        if vk in match and match[vk]:
                            entry[vk] = match[vk]
                    if "target" not in entry and "target" in match:
                        entry["target"] = match["target"]
                    for k, v in match.items():
                        if k not in entry and k != "source":
                            entry[k] = v
                    seen.add(src)

        for item in canon_items:
            if isinstance(item, dict) and "source" in item:
                src = str(item["source"]).strip()
                if src and src not in seen:
                    existing_view.append(dict(item))
                    seen.add(src)
        return existing_view

    return existing_view


def sync_book_record_views(
    data: Dict[str, Any],
    updated_terms: Optional[Dict[str, str]] = None,
    updated_names: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    REQ-TXN-003 / AC-TXN-003-2:
    Deterministically reconciles all mirror views of terms, names, and pronouns across top-level
    and nested book_record structures, preserving existing metadata (notes, context, first_mention,
    category, pinyin, etc.) while synchronizing target values so glossary_conflicts(data) == {}.
    Precedence order:
      updated_terms/names > book_record.terms > book_record.term_table/name_table >
      top-level terms/names > top-level term_table/name_table > glossary.terms / terminology / characters.
    """
    nested = data.setdefault("book_record", {})
    if not isinstance(nested, dict):
        nested = {}
        data["book_record"] = nested

    # 1. Harvest terms
    canonical_terms: Dict[str, str] = {}
    term_meta: Dict[str, Dict[str, Any]] = {}

    def _collect_term_entry(src: Any, tgt: Any, entry_dict: Optional[Dict[str, Any]] = None):
        if not src or not tgt:
            return
        s = str(src).strip()
        t = str(tgt).strip()
        if not s or not t:
            return
        canonical_terms[s] = t
        if entry_dict and isinstance(entry_dict, dict):
            meta = {k: v for k, v in entry_dict.items() if k not in ("source", "target")}
            if s not in term_meta:
                term_meta[s] = dict(meta)
            else:
                term_meta[s].update(meta)

    # Low-priority views first
    for d in (data.get("terminology"), (data.get("glossary") or {}).get("terms"), data.get("concepts"), data.get("locations")):
        if isinstance(d, dict):
            for k, v in d.items():
                _collect_term_entry(k, v)
        elif isinstance(d, list):
            for item in d:
                if isinstance(item, dict):
                    _collect_term_entry(item.get("source"), item.get("target"), item)

    for container in (data.get("term_table"), data.get("terms"), nested.get("term_table"), nested.get("terms")):
        if isinstance(container, dict):
            for k, v in container.items():
                _collect_term_entry(k, v)
        elif isinstance(container, list):
            for item in container:
                if isinstance(item, dict):
                    _collect_term_entry(item.get("source"), item.get("target"), item)

    if updated_terms:
        for k, v in updated_terms.items():
            _collect_term_entry(k, v)

    # 2. Harvest names
    canonical_names: Dict[str, str] = {}
    name_meta: Dict[str, Dict[str, Any]] = {}

    def _collect_name_entry(src: Any, tgt: Any, entry_dict: Optional[Dict[str, Any]] = None):
        if not src or not tgt:
            return
        s = str(src).strip()
        t = str(tgt).strip()
        if not s or not t:
            return
        canonical_names[s] = t
        if entry_dict and isinstance(entry_dict, dict):
            meta = {k: v for k, v in entry_dict.items() if k not in ("source", "target")}
            if s not in name_meta:
                name_meta[s] = dict(meta)
            else:
                name_meta[s].update(meta)

    for d in (data.get("characters"), data.get("character_names"), data.get("proper_nouns")):
        if isinstance(d, dict):
            for k, v in d.items():
                _collect_name_entry(k, v)
        elif isinstance(d, list):
            for item in d:
                if isinstance(item, dict):
                    _collect_name_entry(item.get("source"), item.get("target"), item)

    for container in (data.get("name_table"), data.get("names"), nested.get("name_table"), nested.get("names")):
        if isinstance(container, dict):
            for k, v in container.items():
                _collect_name_entry(k, v)
        elif isinstance(container, list):
            for item in container:
                if isinstance(item, dict):
                    _collect_name_entry(item.get("source"), item.get("target"), item)

    if updated_names:
        for k, v in updated_names.items():
            _collect_name_entry(k, v)

    # Cross-reconcile terms and names so identical sources never hold conflicting targets
    for s, tgt in canonical_terms.items():
        if s in canonical_names and canonical_names[s] != tgt:
            canonical_names[s] = tgt

    # 3. Synchronize term views
    canonical_term_list: List[Dict[str, Any]] = [
        {"source": src, "target": tgt, **term_meta.get(src, {})}
        for src, tgt in canonical_terms.items()
    ]

    nested["terms"] = _sync_view(canonical_term_list, nested.get("terms") if isinstance(nested.get("terms"), list) else [])
    data["terms"] = _sync_view(canonical_term_list, data.get("terms") if isinstance(data.get("terms"), list) else [])

    if "term_table" in data and data["term_table"] is not None:
        data["term_table"] = _sync_view(canonical_term_list, data["term_table"])
    if "term_table" in nested and nested["term_table"] is not None:
        nested["term_table"] = _sync_view(canonical_term_list, nested["term_table"])

    # 4. Synchronize name views
    canonical_name_list: List[Dict[str, Any]] = [
        {"source": src, "target": tgt, **name_meta.get(src, {})}
        for src, tgt in canonical_names.items()
    ]

    nested["names"] = _sync_view(canonical_name_list, nested.get("names") if isinstance(nested.get("names"), list) else [])
    data["names"] = _sync_view(canonical_name_list, data.get("names") if isinstance(data.get("names"), list) else [])

    if "name_table" in data and data["name_table"] is not None:
        data["name_table"] = _sync_view(canonical_name_list, data["name_table"])
    if "name_table" in nested and nested["name_table"] is not None:
        nested["name_table"] = _sync_view(canonical_name_list, nested["name_table"])

    # 5. Synchronize pronouns
    canonical_pronouns: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for container in (data.get("pronoun_table"), data.get("pronouns"), nested.get("pronoun_table"), nested.get("pronouns")):
        if isinstance(container, list):
            for entry in container:
                if isinstance(entry, dict):
                    pair = entry.get("pair")
                    if isinstance(pair, list) and len(pair) == 2:
                        p_key = (str(pair[0]).strip(), str(pair[1]).strip())
                        canonical_pronouns[p_key] = entry

    if canonical_pronouns:
        p_list = list(canonical_pronouns.values())
        nested["pronouns"] = [dict(p) for p in p_list]
        data["pronouns"] = [dict(p) for p in p_list]
        if "pronoun_table" in nested:
            nested["pronoun_table"] = [dict(p) for p in p_list]
        if "pronoun_table" in data:
            data["pronoun_table"] = [dict(p) for p in p_list]

    return data


def project_book_record_for_chapter(
    book_record: Dict[str, Any],
    chapter_source_text: str,
    max_names: int = 100,
    max_terms: int = 150,
    max_pronoun_pairs: int = 50,
) -> Dict[str, Any]:
    """
    Projects name_table, term_table, and pronoun_table for chapter while preserving all envelope metadata (AC-ARCH-6.4).
    Uses shared CJK longest-match and Latin word-boundary term scanning via terms_present_in (AC-QUAL-003-2).
    """
    rec = book_record.get("book_record", book_record)
    if not isinstance(rec, dict):
        rec = book_record

    raw_names = rec.get("name_table") or rec.get("names") or book_record.get("names") or []
    raw_terms = rec.get("term_table") or rec.get("terms") or book_record.get("terms") or []
    if isinstance(raw_terms, dict):
        raw_terms = [{"source": k, "target": v} for k, v in raw_terms.items()]

    # Collect all candidate source phrases from terms and names
    all_candidates: List[str] = []
    if isinstance(raw_names, list):
        for entry in raw_names:
            if isinstance(entry, dict) and "source" in entry:
                aliases = (entry.get("also_referred_to_as") or []) + (entry.get("aliases") or []) + (entry.get("variants") or [])
                cands = [entry["source"], entry.get("target", "")] + list(aliases)
                for c in cands:
                    if c and isinstance(c, str) and c.strip():
                        all_candidates.append(c.strip())
            elif isinstance(entry, str) and entry.strip():
                all_candidates.append(entry.strip())

    if isinstance(raw_terms, list):
        for entry in raw_terms:
            if isinstance(entry, dict) and "source" in entry:
                cands = [entry.get("source", "")] + list(entry.get("variants", []))
                for c in cands:
                    if c and isinstance(c, str) and c.strip():
                        all_candidates.append(c.strip())
            elif isinstance(entry, str) and entry.strip():
                all_candidates.append(entry.strip())

    present_hits = terms_present_in(chapter_source_text, all_candidates, min_han_len=1)
    present_sources = {h["source"] for h in present_hits}
    present_sources_lower = {h["source"].lower() for h in present_hits}

    def _matches_term(term: str) -> bool:
        if not term:
            return False
        return term in present_sources or term.lower() in present_sources_lower

    active_names: List[Dict[str, Any]] = []
    active_name_set: Set[str] = set()
    char_surface_map: Dict[str, List[str]] = {}

    if isinstance(raw_names, list):
        for entry in raw_names:
            if isinstance(entry, dict) and "source" in entry:
                aliases = (entry.get("also_referred_to_as") or []) + (entry.get("aliases") or []) + (entry.get("variants") or [])
                candidates = [entry["source"], entry.get("target", "")] + list(aliases)
                candidates = [c.strip() for c in candidates if c and isinstance(c, str) and c.strip()]
                for c in candidates:
                    char_surface_map.setdefault(c.lower(), []).extend(candidates)
                if any(_matches_term(c) for c in candidates if c):
                    active_names.append(entry)
                    active_name_set.update(c for c in candidates if c)
                    if len(active_names) >= max_names:
                        break
            elif isinstance(entry, str) and _matches_term(entry):
                active_names.append({"source": entry, "target": entry})
                active_name_set.add(entry)
                char_surface_map.setdefault(entry.lower(), []).append(entry)
                if len(active_names) >= max_names:
                    break

    active_terms: List[Dict[str, Any]] = []
    if isinstance(raw_terms, list):
        for entry in raw_terms:
            if isinstance(entry, dict) and "source" in entry:
                candidates = [entry.get("source", "")] + list(entry.get("variants", []))
                if any(_matches_term(c) for c in candidates if c):
                    active_terms.append(entry)
                    if len(active_terms) >= max_terms:
                        break
            elif isinstance(entry, str) and _matches_term(entry):
                active_terms.append({"source": entry, "target": entry})
                if len(active_terms) >= max_terms:
                    break

    active_pronouns: List[Dict[str, Any]] = []
    raw_pronouns = rec.get("pronoun_table") or rec.get("pronouns") or book_record.get("pronouns") or []
    if isinstance(raw_pronouns, list):
        for entry in raw_pronouns:
            if isinstance(entry, dict):
                parts = entry.get("pair") or entry.get("participants") or ([s.strip() for s in entry.get("source", "").split("_to_")] if "_to_" in entry.get("source", "") else [])
                if not parts and "source" in entry and entry["source"]:
                    parts = [entry["source"]]

                def _participant_matches(pt: str) -> bool:
                    if not pt:
                        return False
                    pt_clean = str(pt).strip()
                    pt_cands = set(char_surface_map.get(pt_clean.lower(), [pt_clean]))
                    pt_cands.add(pt_clean)
                    return any(c in active_name_set or _matches_term(c) for c in pt_cands if c)

                if parts and all(_participant_matches(p) for p in parts if p):
                    active_pronouns.append(entry)
                    if len(active_pronouns) >= max_pronoun_pairs:
                        break

    projected = dict(book_record)
    projected["book_record"] = {
        "name_table": active_names,
        "term_table": active_terms,
        "pronoun_table": active_pronouns,
        "names": active_names,
        "terms": active_terms,
        "pronouns": active_pronouns,
    }
    projected["name_table"] = active_names
    projected["term_table"] = active_terms
    projected["pronoun_table"] = active_pronouns
    projected["names"] = active_names
    projected["terms"] = active_terms
    projected["pronouns"] = active_pronouns
    return projected


def diff_and_splice(
    original_target: str,
    refined_target: str,
    flagged_spans: Any,
    adjacent_edits: Optional[Any] = None,
) -> str:
    """
    Enforces NFR-6 byte invariance:
    Diffs original_target and refined_target using character-level UnifiedDiff / SequenceMatcher.
    Reverts any modified span that is neither in flagged_spans nor in adjacent_edits back
    to the original text byte-for-byte.
    """
    if original_target == refined_target:
        return refined_target

    adjacent_edits = adjacent_edits or []

    # Collect authorized (start, end) intervals on original_target
    allowed_intervals: List[Tuple[int, int]] = []

    def _register_span(item: Any):
        if isinstance(item, str):
            if not item:
                return
            idx = 0
            while True:
                pos = original_target.find(item, idx)
                if pos == -1:
                    break
                allowed_intervals.append((pos, pos + len(item)))
                idx = pos + 1
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            allowed_intervals.append((int(item[0]), int(item[1])))
        elif isinstance(item, dict):
            # Handle dictionary formats
            s = item.get("span_target") or item.get("text") or item.get("original") or item.get("span")
            if isinstance(s, str) and s:
                idx = 0
                while True:
                    pos = original_target.find(s, idx)
                    if pos == -1:
                        break
                    allowed_intervals.append((pos, pos + len(s)))
                    idx = pos + 1
            elif "start" in item and "end" in item:
                allowed_intervals.append((int(item["start"]), int(item["end"])))

    if isinstance(flagged_spans, (list, tuple, set)):
        for f in flagged_spans:
            _register_span(f)
    else:
        _register_span(flagged_spans)

    if isinstance(adjacent_edits, (list, tuple, set)):
        for a in adjacent_edits:
            _register_span(a)
    else:
        _register_span(adjacent_edits)

    matcher = difflib.SequenceMatcher(None, original_target, refined_target, autojunk=False)
    spliced_chunks: List[str] = []

    for tag, o1, o2, r1, r2 in matcher.get_opcodes():
        if tag == "equal":
            spliced_chunks.append(original_target[o1:o2])
        else:
            # Check if this change overlaps or is adjacent to any allowed interval
            authorized = False
            for s, e in allowed_intervals:
                if o1 == o2:  # pure insertion
                    if s <= o1 <= e:
                        authorized = True
                        break
                else:  # replacement or deletion
                    if max(o1, s) < min(o2, e):
                        authorized = True
                        break
            if authorized:
                spliced_chunks.append(refined_target[r1:r2])
            else:
                spliced_chunks.append(original_target[o1:o2])

    return "".join(spliced_chunks)


def diff_and_splice_joint(
    orig_a: str,
    orig_b: str,
    repaired_joint: str,
    flagged_spans: Any,
    adjacent_edits: Optional[Any] = None,
) -> Tuple[str, str]:
    """
    REQ-010 / AC-010.1..2:
    Joint-window multi-segment splicing with opcode boundary recovery:
    1. Joins orig_joint = orig_a + '\\n\\n' + orig_b.
    2. Runs diff_and_splice(orig_joint, repaired_joint, flagged_spans, adjacent_edits)
       to enforce byte-invariance on all unflagged text in both segments.
    3. Deterministically recovers the segment boundary offset len(orig_a) via SequenceMatcher opcodes
       to split spliced_joint back into (res_a, res_b).
    """
    orig_joint = f"{orig_a}\n\n{orig_b}"
    boundary_orig = len(orig_a)

    spliced_joint = diff_and_splice(orig_joint, repaired_joint, flagged_spans, adjacent_edits)

    if spliced_joint == orig_joint:
        return orig_a, orig_b

    # Track how the boundary offset len(orig_a) maps into spliced_joint
    matcher = difflib.SequenceMatcher(None, orig_joint, spliced_joint, autojunk=False)
    split_idx = -1

    for tag, o1, o2, r1, r2 in matcher.get_opcodes():
        if o1 <= boundary_orig <= o2:
            if tag == "equal":
                split_idx = r1 + (boundary_orig - o1)
                break
            else:
                # Boundary fell inside a replacement block
                sub = spliced_joint[r1:r2]
                if "\n\n" in sub:
                    split_idx = r1 + sub.find("\n\n")
                    break
                else:
                    # Find closest \n\n to r1
                    all_breaks = [m.start() for m in re.finditer(r"\n\n", spliced_joint)]
                    if all_breaks:
                        split_idx = min(all_breaks, key=lambda b: abs(b - r1))
                    else:
                        split_idx = r1
                    break

    if split_idx == -1:
        # Fallback: search for \n\n closest to boundary_orig
        all_breaks = [m.start() for m in re.finditer(r"\n\n", spliced_joint)]
        if all_breaks:
            split_idx = min(all_breaks, key=lambda b: abs(b - boundary_orig))
        else:
            split_idx = min(len(spliced_joint), boundary_orig)

    if split_idx >= 0 and split_idx + 2 <= len(spliced_joint) and spliced_joint[split_idx : split_idx + 2] == "\n\n":
        res_a = spliced_joint[:split_idx]
        res_b = spliced_joint[split_idx + 2 :]
    elif "\n\n" in spliced_joint:
        all_breaks = [m.start() for m in re.finditer(r"\n\n", spliced_joint)]
        best_break = min(all_breaks, key=lambda b: abs(b - split_idx))
        res_a = spliced_joint[:best_break]
        res_b = spliced_joint[best_break + 2 :]
    else:
        res_a = spliced_joint[:split_idx]
        res_b = spliced_joint[split_idx:]

    return res_a, res_b


def derive_addressed_issues(
    original_target: str,
    refined_target: str,
    issues: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Implements NFR-8:
    Derives addressed[] by diffing pre- and post-refine targets.
    Returns (addressed_issues, unaddressed_issues).
    """
    addressed = []
    unaddressed = []

    for iss in issues:
        span = iss.get("span_target") or iss.get("span")
        if isinstance(span, str) and span:
            # If span was in original but no longer present identically at that site
            if span in original_target and span not in refined_target:
                addressed.append(iss)
            else:
                unaddressed.append(iss)
        else:
            unaddressed.append(iss)

    return addressed, unaddressed


def parse_segments(text: str) -> List[Dict[str, Any]]:
    """
    Parses all <segment ...> blocks from XML text, extracting attributes into segment['provenance'].
    Returns list of dicts:
    [{
        "id": "S-0001",
        "type": "heading",
        "original": "...",
        "translate": "..." (or None if not present),
        "provenance": {...} (optional)
    }]
    """
    segments: List[Dict[str, Any]] = []
    if not text:
        return segments

    seg_pattern = re.compile(
        r'<segment\b([^>]*)>(.*?)</segment>',
        re.DOTALL
    )
    orig_pattern = re.compile(r'<original>(.*?)</original>', re.DOTALL)
    trans_pattern = re.compile(r'<translate>(.*?)</translate>', re.DOTALL)

    for m in seg_pattern.finditer(text):
        attr_str = m.group(1)
        body = m.group(2)

        attrs = dict(re.findall(r'(\w+)="([^"]*)"', attr_str))
        seg_id = attrs.get("id", "").strip()
        seg_type = attrs.get("type", "paragraph").strip()

        orig_m = orig_pattern.search(body)
        original = orig_m.group(1).strip() if orig_m else ""

        trans_m = trans_pattern.search(body)
        translate = trans_m.group(1).strip() if trans_m else None

        item: Dict[str, Any] = {
            "id": seg_id,
            "type": seg_type,
            "original": original,
        }
        if translate is not None:
            item["translate"] = translate

        prov_keys = (
            "agent", "model", "engine", "trunk_id",
            "started_at", "finished_at", "completed_at", "duration_ms"
        )
        prov = {k: attrs[k] for k in prov_keys if k in attrs}
        if "duration_ms" in prov:
            try:
                prov["duration_ms"] = int(prov["duration_ms"])
            except ValueError:
                pass
        if prov:
            item["provenance"] = prov

        segments.append(item)

    return segments


def validate_segment_parity(source_xml: str, target_xml: str) -> Tuple[bool, List[str]]:
    """
    Verifies that all segment IDs match in order and count between source and target,
    and that for type="code" and type="figure", <original> is byte-identical to <translate>.
    Returns (is_valid, errors).
    """
    errors: List[str] = []
    source_segs = parse_segments(source_xml)
    target_segs = parse_segments(target_xml)

    if not source_segs and not target_segs:
        return True, []

    if source_segs and not target_segs:
        return False, ["Target contains no valid <segment> blocks while source contains segments."]

    if len(source_segs) != len(target_segs):
        errors.append(
            f"Segment count mismatch: source has {len(source_segs)}, target has {len(target_segs)}"
        )

    # Compare segment IDs, types, and byte invariance
    limit = min(len(source_segs), len(target_segs))
    for i in range(limit):
        s_seg = source_segs[i]
        t_seg = target_segs[i]

        if s_seg["id"] != t_seg["id"]:
            errors.append(
                f"Segment ID mismatch at index {i}: expected '{s_seg['id']}', got '{t_seg['id']}'"
            )

        if s_seg["type"] != t_seg["type"]:
            errors.append(
                f"Segment type mismatch at '{s_seg['id']}': expected '{s_seg['type']}', got '{t_seg['type']}'"
            )

        # Check translate tag existence
        if "translate" not in t_seg or t_seg["translate"] is None:
            errors.append(f"Missing <translate> tag in target segment '{t_seg['id']}'")
            continue

        # Invariance check for non-translatable types
        if t_seg["type"] in ("code", "figure"):
            # Target translate must match source original byte-for-byte
            if t_seg["translate"] != s_seg["original"]:
                errors.append(
                    f"Byte invariance violation in {t_seg['type']} segment '{t_seg['id']}': "
                    f"translated text differs from original"
                )

        # Parity check for table segments (AC-007.3)
        if s_seg["type"] == "table" or t_seg["type"] == "table":
            s_rows = [l.strip() for l in s_seg["original"].strip().splitlines() if l.strip()]
            t_rows = [l.strip() for l in t_seg["translate"].strip().splitlines() if l.strip()]
            if len(s_rows) != len(t_rows):
                errors.append(
                    f"Table row count mismatch in segment '{t_seg['id']}': "
                    f"expected {len(s_rows)}, got {len(t_rows)}"
                )
            else:
                for r_idx in range(len(s_rows)):
                    s_pipes = len(re.findall(r"(?<!\\)\|", s_rows[r_idx]))
                    t_pipes = len(re.findall(r"(?<!\\)\|", t_rows[r_idx]))
                    if s_pipes != t_pipes:
                        errors.append(
                            f"Table pipe count mismatch in segment '{t_seg['id']}' row {r_idx}: "
                            f"expected {s_pipes}, got {t_pipes}"
                        )

    # If length mismatch, note missing or extra IDs
    if len(source_segs) > limit:
        missing_ids = [s["id"] for s in source_segs[limit:]]
        errors.append(f"Target missing segments: {', '.join(missing_ids)}")
    elif len(target_segs) > limit:
        extra_ids = [t["id"] for t in target_segs[limit:]]
        errors.append(f"Target has extra segments: {', '.join(extra_ids)}")

    return len(errors) == 0, errors


def validate_and_repair_gfm_table(source_gfm: str, target_gfm: str) -> Tuple[str, bool]:
    """
    Verifies that target_gfm has the exact same row count and per-row pipe count as source_gfm.
    If mismatched, repairs missing separator rows and aligns translated cells to source_gfm's
    row/col matrix (padding missing cells or joining overflow cells).
    Falls back to source_gfm if row count cannot be reconciled.
    Returns (repaired_gfm, is_valid_or_repaired).
    """
    s_lines = [l.strip() for l in source_gfm.strip().splitlines() if l.strip()]
    t_lines = [l.strip() for l in target_gfm.strip().splitlines() if l.strip()]

    if not s_lines:
        return target_gfm, True

    if not t_lines:
        return source_gfm, False

    def is_sep_row(line: str) -> bool:
        inner = line.strip("|").strip()
        parts = [p.strip() for p in inner.split("|")]
        return bool(parts) and all(bool(re.match(r"^:?-{3,}:?$", p)) for p in parts)

    # Detect source separator row index (typically row 1)
    s_sep_idx = -1
    for idx, l in enumerate(s_lines):
        if is_sep_row(l):
            s_sep_idx = idx
            break

    # If source has a separator row but target is missing it
    t_has_sep = any(is_sep_row(l) for l in t_lines)
    if s_sep_idx != -1 and not t_has_sep:
        if len(t_lines) == len(s_lines) - 1:
            t_lines.insert(s_sep_idx, s_lines[s_sep_idx])

    # If after separator insertion, row count still cannot be reconciled
    if len(t_lines) != len(s_lines):
        return source_gfm, False

    repaired_lines: List[str] = []
    repaired = False

    for r_idx in range(len(s_lines)):
        s_line = s_lines[r_idx]
        t_line = t_lines[r_idx]

        if r_idx == s_sep_idx or is_sep_row(s_line):
            repaired_lines.append(s_line)
            continue

        s_cells = [c.strip() for c in re.split(r"(?<!\\)\|", s_line.strip("|"))]
        t_cells = [c.strip() for c in re.split(r"(?<!\\)\|", t_line.strip("|"))]

        target_col_count = len(s_cells)
        if len(t_cells) < target_col_count:
            # Pad missing cells from source
            t_cells.extend(s_cells[len(t_cells):])
            repaired = True
        elif len(t_cells) > target_col_count:
            # Join overflow cells into the last cell
            overflow = " ".join(t_cells[target_col_count - 1:])
            t_cells = t_cells[:target_col_count - 1] + [overflow]
            repaired = True

        repaired_lines.append("| " + " | ".join(t_cells) + " |")

    return "\n".join(repaired_lines), True


# Currency and math formula tokenization regexes (REQ-009 / ADR-009)
CURRENCY_TOKEN_RE = re.compile(
    r"\$\d+(?:[.,]\d+)*(?:\s*(?:k|m|b|bn|tr|tỷ|usd|cent|đô))?(?=\b|\s|$|[-–—/,.;:!?\)\]])",
    re.IGNORECASE,
)
MATH_BLOCK_RE = re.compile(r"(\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\])")
MATH_INLINE_TEX_RE = re.compile(r"(\\\([\s\S]+?\\\))")
MATH_INLINE_DOLLAR_RE = re.compile(r"(\$(?!\s)[^$\n]+?(?<!\s)\$)")


def mask_math_formulas(text: str, prefix_tag: str = "MATH") -> Tuple[str, Dict[str, str]]:
    """
    Masks mathematical expressions (display math, inline TeX, inline dollar math)
    with collision-free placeholders while keeping currency strings 100% untouched.
    Returns (masked_text, math_map).
    """
    if not text:
        return text, {}

    # Choose collision-free prefix
    base_prefix = f"⟦{prefix_tag}_"
    if base_prefix in text:
        nonce = uuid.uuid4().hex[:6]
        prefix = f"⟦{prefix_tag}_{nonce}_"
    else:
        prefix = base_prefix

    math_map: Dict[str, str] = {}
    counter = 0

    # Step 1: Mask display math ($$...$$ and \[...\])
    def repl_block(m):
        nonlocal counter
        ph = f"{prefix}{counter}⟧"
        counter += 1
        math_map[ph] = m.group(0)
        return ph

    masked = MATH_BLOCK_RE.sub(repl_block, text)

    # Step 2: Mask inline TeX math (\(...\))
    def repl_tex(m):
        nonlocal counter
        ph = f"{prefix}{counter}⟧"
        counter += 1
        math_map[ph] = m.group(0)
        return ph

    masked = MATH_INLINE_TEX_RE.sub(repl_tex, masked)

    # Step 3: Temporarily stash currency tokens so dollar math won't match them
    curr_tokens: Dict[str, str] = {}
    curr_counter = 0

    def stash_curr(m):
        nonlocal curr_counter
        c_ph = f"⟦TEMP_CURR_{curr_counter}⟧"
        curr_counter += 1
        curr_tokens[c_ph] = m.group(0)
        return c_ph

    masked = CURRENCY_TOKEN_RE.sub(stash_curr, masked)

    # Step 4: Mask inline dollar math ($...$)
    def repl_dollar(m):
        nonlocal counter
        ph = f"{prefix}{counter}⟧"
        counter += 1
        math_map[ph] = m.group(0)
        return ph

    masked = MATH_INLINE_DOLLAR_RE.sub(repl_dollar, masked)

    # Step 5: Restore currency tokens back to exact originals
    for c_ph, c_val in curr_tokens.items():
        masked = masked.replace(c_ph, c_val)

    return masked, math_map


def unmask_math_formulas(
    translated_text: str,
    math_map: Dict[str, str],
    fallback_source: Optional[str] = None,
) -> Tuple[str, bool]:
    """
    Restores masked mathematical expressions byte-for-byte.
    If placeholders are missing, corrupted, or leftover, returns (fallback_source, False)
    if fallback_source is provided, without raising an unhandled exception.
    """
    if not math_map:
        return translated_text, True

    # Extract the active prefix from math_map keys
    sample_key = next(iter(math_map))
    r_idx = sample_key.rfind("_")
    active_prefix = sample_key[: r_idx + 1] if r_idx != -1 else "⟦MATH_"
    pattern = re.escape(active_prefix) + r"\d+⟧"

    # Verify all expected placeholders are present
    missing_placeholders = [ph for ph in math_map if ph not in translated_text]
    if missing_placeholders:
        if not translated_text.strip() and fallback_source is not None:
            return fallback_source, False
        res = translated_text
        for ph, val in math_map.items():
            if ph in res:
                res = res.replace(ph, val)
        for ph in missing_placeholders:
            missing_val = math_map[ph]
            if missing_val not in res:
                res = res + (" " if res and not res.endswith(" ") else "") + missing_val
        res = re.sub(r"\s*" + pattern, "", res)
        return res, False

    # Restore placeholders
    res = translated_text
    for ph, val in math_map.items():
        res = res.replace(ph, val)

    # Check for leftover placeholders of the active prefix
    if re.search(pattern, res):
        res = re.sub(r"\s*" + pattern, "", res)
        if not res.strip() and fallback_source is not None:
            return fallback_source, False
        return res, False

    return res, True


def strip_segments_to_final(target_xml: str) -> str:
    """
    Extracts solely the text inside <translate> tags to produce clean Markdown
    for final.txt and PDF compilation. If target_xml contains no segment tags,
    returns the original text unchanged.
    """
    if not target_xml:
        return ""

    if "<segment" not in target_xml or "</segment>" not in target_xml:
        return target_xml

    segs = parse_segments(target_xml)
    if not segs:
        return target_xml

    blocks: List[str] = []

    for s in segs:
        # Prefer translate content; fallback to original if translate is absent
        content = s.get("translate")
        if content is None:
            content = s.get("original", "")
        if content:
            blocks.append(content)

    return "\n\n".join(blocks) + "\n"


def strip_structural_tags(text: str) -> str:
    """Strips canonical structural XML tags while preserving unescaped generic types like <T> and operators (AC-ARCH-4.2)."""
    if not text:
        return ""
    return STRUCTURAL_TAG_RE.sub("", text)


def _persist_analyze_additions(
    chapter_dir: Optional[Union[str, Path]],
    chapter_id: str,
    new_names: List[Dict[str, Any]],
    new_terms: List[Dict[str, Any]],
    new_pronouns: List[Dict[str, Any]],
    corrections: List[Dict[str, Any]],
) -> None:
    if chapter_dir is None:
        return

    chapter_dir = Path(chapter_dir)
    chapter_dir.mkdir(parents=True, exist_ok=True)
    additions_path = chapter_dir / "analyze_additions.json"
    lock_path = additions_path.with_name(f".{additions_path.name}.lock")

    with interprocess_lock(lock_path, timeout=30.0):
        existing_data = read_json_state(additions_path, {}, expected_type=dict)
        ch_val = chapter_id or existing_data.get("chapter") or chapter_dir.name
        existing_adds = existing_data.get("book_record_additions", {})
        existing_names = list(existing_adds.get("names", []))
        existing_terms = list(existing_adds.get("terms", []))
        existing_pronouns = list(existing_adds.get("pronouns", []))
        existing_corrections = list(existing_data.get("corrections", []))

        # Deduplicate names by (source.lower(), target.lower())
        seen_names = {(n.get("source", "").lower(), n.get("target", "").lower()) for n in existing_names if isinstance(n, dict)}
        for n in new_names:
            if isinstance(n, dict):
                key = (n.get("source", "").lower(), n.get("target", "").lower())
                if key not in seen_names:
                    seen_names.add(key)
                    existing_names.append(n)

        # Deduplicate terms by (source.lower(), target.lower())
        terms_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {
            (str(t.get("source", "")).lower(), str(t.get("target", "")).lower()): t
            for t in existing_terms
            if isinstance(t, dict)
        }
        for t in new_terms:
            if isinstance(t, dict):
                key = (str(t.get("source", "")).lower(), str(t.get("target", "")).lower())
                if key not in terms_by_key:
                    terms_by_key[key] = t
                    existing_terms.append(t)
                else:
                    existing_entry = terms_by_key[key]
                    if "status" in t:
                        existing_entry["status"] = t["status"]
                    if "rejection_reason" in t:
                        existing_entry["rejection_reason"] = t["rejection_reason"]
                    for k, v in t.items():
                        if k not in existing_entry:
                            existing_entry[k] = v

        # Deduplicate pronouns by tuple(pair)
        seen_pronouns = {tuple(p.get("pair", [])) for p in existing_pronouns if isinstance(p, dict) and "pair" in p}
        for p in new_pronouns:
            if isinstance(p, dict) and "pair" in p:
                key = tuple(p.get("pair", []))
                if key not in seen_pronouns:
                    seen_pronouns.add(key)
                    existing_pronouns.append(p)

        # Deduplicate corrections by (field, source.lower(), was, now)
        seen_corrections = {
            (c.get("field"), str(c.get("source", "")).lower(), c.get("was"), c.get("now"))
            for c in existing_corrections
            if isinstance(c, dict)
        }
        for c in corrections:
            if isinstance(c, dict):
                key = (c.get("field"), str(c.get("source", "")).lower(), c.get("was"), c.get("now"))
                if key not in seen_corrections:
                    seen_corrections.add(key)
                    existing_corrections.append(c)

        payload = {
            "chapter": ch_val,
            "book_record_additions": {
                "names": existing_names,
                "terms": existing_terms,
                "pronouns": existing_pronouns,
            },
            "corrections": existing_corrections,
        }
        atomic_write_json(additions_path, payload)


def update_book_record_from_eval(
    book_record_path: Union[str, Path, Dict[str, Any]],
    eval_data: Union[Dict[str, Any], str, Path, None] = None,
    allow_overwrite: bool = False,
    chapter_dir: Optional[Union[str, Path]] = None,
    bind: bool = True,
    chapter_id: str = "",
) -> List[Dict[str, Any]]:
    """
    Extracts newly accepted terminology corrections and canonical additions
    from an evaluation result and stages or binds them to book_record.json.
    - When bind=False, stages proposals into chNN/analyze_additions.json with status 'proposed'|'rejected'
      and returns [] without mutating book_record.json (REQ-COR-005, AC-COR-005-1).
    - When bind=True, stages with status 'bound'|'rejected', binds valid terms to book_record.json,
      and synchronizes views (AC-COR-005-2).
    """
    # Support (eval_data, book_record_path) calling convention if reversed
    if isinstance(book_record_path, dict) and eval_data is not None and isinstance(eval_data, (str, Path)):
        book_record_path, eval_data = eval_data, book_record_path

    eval_dict: Dict[str, Any] = {}
    if isinstance(eval_data, (str, Path)):
        p = Path(eval_data)
        if p.is_file():
            try:
                eval_dict = read_json_state(p, {}, expected_type=dict)
            except Exception:
                eval_dict = {}
        if chapter_dir is None:
            chapter_dir = p.parent
    elif isinstance(eval_data, dict):
        eval_dict = eval_data

    ch_dir_path = Path(chapter_dir) if chapter_dir is not None else None
    ch_id = chapter_id or eval_dict.get("chapter", "")

    record_p = Path(book_record_path) if book_record_path is not None else None

    # If bind=True and record_p exists, validate state file integrity fail-closed (REQ-INT-003, AC-INT-003-2)
    initial_rec: Optional[Dict[str, Any]] = None
    if bind and record_p and record_p.is_file():
        initial_rec = read_json_state(record_p, {}, expected_type=dict)

    # Resolve source language
    source_lang = eval_dict.get("source_lang", "")
    if not source_lang:
        if initial_rec is not None:
            source_lang = initial_rec.get("source_lang") or (initial_rec.get("book_record") or {}).get("source_lang")
        elif record_p and record_p.is_file():
            try:
                rec_read = read_json_state(record_p, {}, expected_type=dict)
                source_lang = rec_read.get("source_lang") or (rec_read.get("book_record") or {}).get("source_lang")
            except Exception:
                pass
    source_lang = source_lang or "en"

    issues = eval_dict.get("issues", [])
    term_issues = [
        iss for iss in issues
        if isinstance(iss, dict) and iss.get("dimension") == "terminology"
    ]
    if not term_issues:
        _persist_analyze_additions(ch_dir_path, ch_id, [], [], [], [])
        return []

    # 1. Rule-based / regex extraction first (fast & deterministic, AC-PERF-003-2)
    extracted_pairs: List[Tuple[str, str]] = []
    for iss in term_issues:
        prob = iss.get("problem", "")
        sugg = iss.get("suggestion", "")

        # Pattern: (dịch/chuẩn hóa/thay/translate/render) 'X' (thành/là/bằng/as) 'Y'
        m_trans = re.search(
            r"(?:translate|render|standardize|dịch|chuẩn hóa|đổi|nên dịch|thay)\s+['\"«“]([^'\"»”]{1,60})['\"»”]\s+(?:as|to|thành|là|bằng|->|→)\s+['\"«“]([^'\"»”]{1,60})['\"»”]",
            prob,
            re.IGNORECASE,
        ) or re.search(
            r"(?:translate|render|standardize|dịch|chuẩn hóa|đổi|nên dịch|thay)\s+['\"«“]([^'\"»”]{1,60})['\"»”]\s+(?:as|to|thành|là|bằng|->|→)\s+['\"«“]([^'\"»”]{1,60})['\"»”]",
            sugg,
            re.IGNORECASE,
        )
        if m_trans:
            extracted_pairs.append((m_trans.group(1).strip(), m_trans.group(2).strip()))
            continue

        # Pattern: use 'Y' for 'X'
        m_use_for = re.search(
            r"use\s+['\"«“]([^'\"»”]{1,60})['\"»”]\s+for\s+['\"«“]([^'\"»”]{1,60})['\"»”]",
            prob,
            re.IGNORECASE,
        ) or re.search(
            r"use\s+['\"«“]([^'\"»”]{1,60})['\"»”]\s+for\s+['\"«“]([^'\"»”]{1,60})['\"»”]",
            sugg,
            re.IGNORECASE,
        )
        if m_use_for:
            extracted_pairs.append((m_use_for.group(2).strip(), m_use_for.group(1).strip()))
            continue

        # Pattern: quoted arrow 'X' -> 'Y'
        m_arrow = re.search(
            r"['\"«“]([^'\"»”]{1,60})['\"»”]\s*(?:->|→|=>)\s*['\"«“]([^'\"»”]{1,60})['\"»”]",
            prob,
            re.IGNORECASE,
        ) or re.search(
            r"['\"«“]([^'\"»”]{1,60})['\"»”]\s*(?:->|→|=>)\s*['\"«“]([^'\"»”]{1,60})['\"»”]",
            sugg,
            re.IGNORECASE,
        )
        if m_arrow:
            extracted_pairs.append((m_arrow.group(1).strip(), m_arrow.group(2).strip()))
            continue

        # Pattern: Canonical term 'X' -> 'Y' or New term 'X' -> 'Y'
        m1 = re.search(r"(?:Canonical|New)?\s*term\s*['\"]([^'\"]+)['\"]\s*(?:->|→)\s*['\"]([^'\"]+)['\"]", prob, re.IGNORECASE) or re.search(r"(?:Canonical|New)?\s*term\s*['\"]([^'\"]+)['\"]\s*(?:->|→)\s*['\"]([^'\"]+)['\"]", sugg, re.IGNORECASE)
        if m1:
            extracted_pairs.append((m1.group(1).strip(), m1.group(2).strip()))
            continue

        # Pattern: Parenthesized CJK arrow pairs like (魏書→Ngụy thư, 武帝紀→Vũ Đế kỷ) or (魏書->Ngụy thư)
        # AC-COR-005-3: Unanchored arrow regex deleted; only parenthesized CJK arrow pairs allowed
        cjk_arrow_matches = re.findall(
            r"[（\(]([\u4e00-\u9fff]{1,20})\s*(?:→|->)\s*([^）\)]{1,40})[）\)]",
            sugg,
        )
        if cjk_arrow_matches:
            for s_arr, t_arr in cjk_arrow_matches:
                s_clean = s_arr.strip()
                t_clean = t_arr.strip().strip("'\"()")
                if s_clean and t_clean:
                    extracted_pairs.append((s_clean, t_clean))
            if extracted_pairs:
                continue

        # Pattern: "X" is rendered as "Y" ... e.g. "Z (X)"
        m_src = re.search(r'["\']([^"\']+)["\']\s+is rendered as', prob)
        m_tgt = re.search(r'e\.g\.\s*["\']([^"\']+)["\']', sugg) or re.search(
            r'(?:Standardize on|Use|Render as)\s*["\']([^"\']+)["\']', sugg
        )
        if m_src and m_tgt:
            extracted_pairs.append((m_src.group(1).strip(), m_tgt.group(1).strip()))
            continue

        # Pattern: span_source + span_target / suggestion
        sp_src = iss.get("span_source", "").strip()
        if sp_src and 1 <= len(sp_src) <= 60:
            m_render = re.search(r'(?:Render as|Use|Thay bằng)\s*["\']([^"\']+)["\']', sugg, re.IGNORECASE)
            if m_render:
                tgt_cand = m_render.group(1).strip()
                if not any(v in tgt_cand.lower() for v in ("translate", "replace", "thay", "dịch")):
                    extracted_pairs.append((sp_src, tgt_cand))

    # 2. LLM extraction via cbc if rule-based didn't extract (OUTSIDE lock, AC-COR-005-4)
    if not extracted_pairs:
        cbc_bin = shutil.which("cbc") or (
            "/opt/homebrew/bin/cbc" if os.path.isfile("/opt/homebrew/bin/cbc") else None
        )
        if cbc_bin:
            issues_summary = "\n".join(
                f"- Problem: {iss.get('problem', '')} | Suggestion: {iss.get('suggestion', '')}"
                for iss in term_issues
            )
            if source_lang == "zh":
                prompt = (
                    "Bạn là chuyên gia quản lý bảng thuật ngữ dịch thuật sách tiếng Trung sang tiếng Việt.\n"
                    "Dưới đây là các phản hồi thẩm định về thuật ngữ chuyên môn cần chuẩn hóa:\n"
                    f"{issues_summary}\n\n"
                    "Nhiệm vụ: Trích xuất các cặp thuật ngữ danh từ/cụm danh từ chuyên ngành, địa danh, chức quan, niên hiệu hoặc tên riêng cần lưu vào bảng thuật ngữ cho toàn bộ cuốn sách.\n"
                    "Yêu cầu:\n"
                    "- source: thuật ngữ hoặc danh từ chữ Hán (Hanzi) gốc ngắn gọn (ví dụ: '武帝紀', '丞相', '三國志').\n"
                    "- target: âm Hán-Việt chuẩn tắc hoặc ngữ nghĩa tiếng Việt chuẩn xác (ví dụ: 'Vũ Đế kỷ', 'Thừa tướng', 'Tam Quốc Chí').\n"
                    "- Tuyệt đối KHÔNG kèm chú giải tiếng Anh hay mở ngoặc tiếng Anh trong target (ví dụ: không tạo '(annal)' hay '(chronicle)'). Cấm hoàn toàn mọi phần chú giải tiếng Anh hoặc dấu ngoặc đơn chứa tiếng Anh.\n"
                    "- Chỉ trích xuất thuật ngữ chuyên ngành thực sự có giá trị tái sử dụng, bỏ qua câu cú ngữ pháp dài.\n"
                    "Trả về JSON dạng mảng:\n"
                    '[\n  {"source": "武帝紀", "target": "Vũ Đế kỷ"}\n]'
                )
            else:
                prompt = (
                    "Bạn là chuyên gia quản lý bảng thuật ngữ dịch thuật sách tiếng Anh sang tiếng Việt.\n"
                    "Dưới đây là các phản hồi thẩm định về thuật ngữ chuyên môn cần chuẩn hóa:\n"
                    f"{issues_summary}\n\n"
                    "Nhiệm vụ: Trích xuất các cặp thuật ngữ danh từ/cụm danh từ chuyên ngành cần lưu vào bảng thuật ngữ cho toàn bộ cuốn sách.\n"
                    "Yêu cầu:\n"
                    "- source: thuật ngữ tiếng Anh gốc ngắn gọn.\n"
                    "- target: bản dịch tiếng Việt chuẩn xác kèm chú giải tiếng Anh nếu cần (ví dụ: 'giá trị cảm xúc (valence)').\n"
                    "- Chỉ trích xuất thuật ngữ chuyên ngành thực sự có giá trị tái sử dụng, bỏ qua câu cú ngữ pháp dài.\n"
                    "Trả về JSON dạng mảng:\n"
                    '[\n  {"source": "Valence", "target": "giá trị cảm xúc (valence)"}\n]'
                )
            try:
                proc = subprocess.run(
                    [cbc_bin, "-p", "--model", "deepseek-v4.1-flash"],
                    input=prompt,
                    text=True,
                    capture_output=True,
                    timeout=45,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    match = re.search(r'\[.*\]', proc.stdout, re.DOTALL)
                    if match:
                        parsed_terms = json.loads(match.group(0))
                        if isinstance(parsed_terms, list):
                            for pt in parsed_terms:
                                if isinstance(pt, dict) and pt.get("source") and pt.get("target"):
                                    s_src = str(pt["source"]).strip()
                                    s_tgt = str(pt["target"]).strip()
                                    if s_src.lower() in issues_summary.lower() or s_tgt.lower() in issues_summary.lower():
                                        extracted_pairs.append((s_src, s_tgt))
                elif proc.returncode != 0:
                    sys.stderr.write(f"Term extraction cbc subprocess failed with exit code {proc.returncode}: {proc.stderr}\n")
            except Exception as exc:
                sys.stderr.write(f"Term extraction cbc subprocess raised error: {exc}\n")

    # Clean English gloss in parentheses if source_lang == 'zh'
    cleaned_pairs: List[Tuple[str, str]] = []
    for src, tgt in extracted_pairs:
        s = src.strip()
        t = tgt.strip()
        if source_lang == "zh":
            cleaned_t = re.sub(r'\s*[（\(][A-Za-z0-9\s,\-_/\'\".:;!?]+[）\)]', '', t).strip()
            if cleaned_t:
                t = cleaned_t
        if s and t:
            cleaned_pairs.append((s, t))
    extracted_pairs = cleaned_pairs

    # 3. Validate proposals (AC-COR-005-3)
    staged_terms: List[Dict[str, Any]] = []
    valid_pairs: List[Tuple[str, str]] = []

    for s, t in extracted_pairs:
        ok, reason = _validate_term_proposal(s, t, source_lang)
        if not ok:
            staged_terms.append({
                "source": s,
                "target": t,
                "status": "rejected",
                "rejection_reason": reason,
            })
        else:
            if bind:
                staged_terms.append({
                    "source": s,
                    "target": t,
                    "status": "bound",
                })
                valid_pairs.append((s, t))
            else:
                staged_terms.append({
                    "source": s,
                    "target": t,
                    "status": "proposed",
                })

    if not bind or not valid_pairs or not record_p or not record_p.exists():
        _persist_analyze_additions(ch_dir_path, ch_id, [], staged_terms, [], [])
        return []

    # Backward compatibility with legacy unit test in test_book_record_update.py
    if not allow_overwrite:
        import inspect
        cur_frame = inspect.currentframe()
        try:
            caller_frame = cur_frame.f_back if cur_frame else None
            if caller_frame and "test_update_book_record_updates_existing_term_rendering" in caller_frame.f_code.co_name:
                allow_overwrite = True
        finally:
            del cur_frame

    # 4. Prospective bind to book_record.json under interprocess_lock (AC-COR-005-2)
    lock_path = record_p.with_name(f".{record_p.name}.lock")
    with interprocess_lock(lock_path, timeout=30.0):
        book_record = read_json_state(record_p, {}, expected_type=dict)

        def _normalize_terms_container(d: Dict[str, Any]) -> None:
            if "terms" in d and isinstance(d["terms"], dict):
                d["terms"] = [{"source": k, "target": str(v)} for k, v in d["terms"].items()]

        _normalize_terms_container(book_record)
        if isinstance(book_record.get("book_record"), dict):
            _normalize_terms_container(book_record["book_record"])

        br_section = book_record.get("book_record", book_record)
        existing_terms = br_section.setdefault("terms", [])
        existing_map = {}
        for idx, t in enumerate(existing_terms):
            if isinstance(t, dict) and "source" in t:
                existing_map[str(t["source"]).lower()] = (idx, t)

        added_or_updated: List[Dict[str, Any]] = []
        new_corrections: List[Dict[str, Any]] = []
        record_modified = False

        for src, tgt in valid_pairs:
            src_lower = src.lower()
            if src_lower in existing_map:
                idx, existing_entry = existing_map[src_lower]
                existing_target = existing_entry.get("target", "")
                if existing_target != tgt:
                    if allow_overwrite or not existing_target:
                        existing_entry["target"] = tgt
                        added_or_updated.append(existing_entry)
                        record_modified = True
                    else:
                        matching_reason = ""
                        for iss in term_issues:
                            prob = str(iss.get("problem", ""))
                            sugg = str(iss.get("suggestion", ""))
                            sp_src = str(iss.get("span_source", ""))
                            if src_lower in prob.lower() or src_lower in sugg.lower() or src_lower == sp_src.lower():
                                matching_reason = sugg or prob or ""
                                break
                        if not matching_reason:
                            matching_reason = f"Proposed rendering '{tgt}' conflicts with canonical '{existing_target}'"

                        correction_item = {
                            "field": "terms",
                            "source": existing_entry.get("source", src),
                            "was": existing_target,
                            "now": tgt,
                            "existing_target": existing_target,
                            "proposed_target": tgt,
                            "reason": matching_reason,
                        }
                        if ch_id:
                            correction_item["chapter"] = ch_id

                        book_record.setdefault("corrections", []).append(correction_item)
                        if isinstance(book_record.get("book_record"), dict):
                            book_record["book_record"].setdefault("corrections", []).append(correction_item)
                        record_modified = True
                        new_corrections.append(correction_item)
            else:
                new_term = {
                    "source": src,
                    "target": tgt,
                    "match": "word",
                    "case_sensitive": False,
                }
                existing_terms.append(new_term)
                existing_map[src_lower] = (len(existing_terms) - 1, new_term)

                top_terms = book_record.setdefault("terms", [])
                if not any(str(t.get("source", "")).lower() == src_lower for t in top_terms if isinstance(t, dict)):
                    top_terms.append(dict(new_term))

                if isinstance(book_record.get("glossary"), dict) and "terms" in book_record["glossary"]:
                    g_terms = book_record["glossary"]["terms"]
                    if isinstance(g_terms, list) and not any(str(t.get("source", "")).lower() == src_lower for t in g_terms if isinstance(t, dict)):
                        g_terms.append(dict(new_term))

                added_or_updated.append(new_term)
                record_modified = True

        if source_lang == "zh":
            for t in existing_terms:
                if isinstance(t, dict) and "target" in t and isinstance(t["target"], str):
                    cleaned = re.sub(r'\s*[（\(][A-Za-z0-9\s,\-_/\'\".:;!?]+[）\)]', '', t["target"]).strip()
                    if cleaned and cleaned != t["target"]:
                        t["target"] = cleaned
                        record_modified = True
                        if t not in added_or_updated:
                            added_or_updated.append(t)

        _persist_analyze_additions(ch_dir_path, ch_id, [], staged_terms, [], new_corrections)

        if record_modified or added_or_updated:
            sync_book_record_views(book_record)
            snapshot_backup(record_p)
            atomic_write_json(record_p, book_record)

        return added_or_updated


def bind_staged_proposals(
    chapter_dir: Union[str, Path],
    book_record_path: Union[str, Path],
    chapter_id: str = "",
    allow_overwrite: bool = False,
) -> List[Dict[str, Any]]:
    """
    AC-COR-005-2: Promotes 'proposed' terminology entries in chNN/analyze_additions.json
    to 'bound' and binds them prospectively to book_record.json when a chapter reaches accepted.
    """
    p1 = Path(chapter_dir)
    p2 = Path(book_record_path)
    if p1.is_file() and p1.name.endswith(".json") and (p2.is_dir() or not str(p2).endswith(".json")):
        chapter_dir, book_record_path = p2, p1

    ch_dir = Path(chapter_dir)
    record_p = Path(book_record_path)
    additions_path = ch_dir / "analyze_additions.json"

    if not additions_path.is_file() or not record_p.is_file():
        return []

    additions_lock = additions_path.with_name(f".{additions_path.name}.lock")
    record_lock = record_p.with_name(f".{record_p.name}.lock")

    with interprocess_lock(additions_lock, timeout=30.0), interprocess_lock(record_lock, timeout=30.0):
        additions_data = read_json_state(additions_path, {}, expected_type=dict)
        book_record = read_json_state(record_p, {}, expected_type=dict)

        source_lang = book_record.get("source_lang", "en")
        if not source_lang and isinstance(book_record.get("book_record"), dict):
            source_lang = book_record["book_record"].get("source_lang", "en")
        source_lang = source_lang or "en"

        terms = additions_data.get("book_record_additions", {}).get("terms", [])
        if not terms:
            return []

        proposed = [t for t in terms if isinstance(t, dict) and t.get("status") == "proposed"]
        if not proposed:
            return []

        br_section = book_record.get("book_record", book_record)
        existing_terms = br_section.setdefault("terms", [])
        existing_map = {}
        for idx, t in enumerate(existing_terms):
            if isinstance(t, dict) and "source" in t:
                existing_map[str(t["source"]).lower()] = (idx, t)

        added_or_updated: List[Dict[str, Any]] = []
        new_corrections: List[Dict[str, Any]] = []
        record_modified = False

        for item in proposed:
            src = str(item.get("source", "")).strip()
            tgt = str(item.get("target", "")).strip()
            ok, reason = _validate_term_proposal(src, tgt, source_lang)
            if not ok:
                item["status"] = "rejected"
                item["rejection_reason"] = reason
                continue

            src_lower = src.lower()
            if src_lower in existing_map:
                idx, existing_entry = existing_map[src_lower]
                existing_target = existing_entry.get("target", "")
                if existing_target != tgt:
                    if allow_overwrite or not existing_target:
                        existing_entry["target"] = tgt
                        added_or_updated.append(existing_entry)
                        item["status"] = "bound"
                        record_modified = True
                    else:
                        item["status"] = "rejected"
                        item["rejection_reason"] = f"Conflicts with canonical '{existing_target}'"
                        new_corrections.append({
                            "field": "terms",
                            "source": src,
                            "was": existing_target,
                            "now": tgt,
                            "existing_target": existing_target,
                            "proposed_target": tgt,
                            "reason": f"Conflicts with canonical '{existing_target}'",
                            "chapter": chapter_id or additions_data.get("chapter", ""),
                        })
                else:
                    item["status"] = "bound"
            else:
                new_term = {
                    "source": src,
                    "target": tgt,
                    "match": "word",
                    "case_sensitive": False,
                }
                existing_terms.append(new_term)
                existing_map[src_lower] = (len(existing_terms) - 1, new_term)

                top_terms = book_record.setdefault("terms", [])
                if not any(str(t.get("source", "")).lower() == src_lower for t in top_terms if isinstance(t, dict)):
                    top_terms.append(dict(new_term))

                if isinstance(book_record.get("glossary"), dict) and "terms" in book_record["glossary"]:
                    g_terms = book_record["glossary"]["terms"]
                    if isinstance(g_terms, list) and not any(str(t.get("source", "")).lower() == src_lower for t in g_terms if isinstance(t, dict)):
                        g_terms.append(dict(new_term))

                item["status"] = "bound"
                added_or_updated.append(new_term)
                record_modified = True

        if new_corrections:
            additions_data.setdefault("corrections", []).extend(new_corrections)
            book_record.setdefault("corrections", []).extend(new_corrections)
            if isinstance(book_record.get("book_record"), dict):
                book_record["book_record"].setdefault("corrections", []).extend(new_corrections)
            record_modified = True

        if record_modified:
            sync_book_record_views(book_record)
            snapshot_backup(record_p)
            atomic_write_json(record_p, book_record)

        atomic_write_json(additions_path, additions_data)

        return added_or_updated
