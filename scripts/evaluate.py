"""
scripts/evaluate.py — Unified Modular Translation Evaluator
Primary entrypoint and sliding-window orchestrator for evaluating English-to-Vietnamese
chapter translations across multiple engines (WorkBuddy CBC, Argon Web, Fallback).
"""

import argparse
import concurrent.futures
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.evaluators import (
    BaseEvaluator,
    get_evaluator,
)
from scripts.evaluators.fallback import (
    COMMON_EN_WORDS,
    DEFAULT_RUBRIC_WEIGHTS,
    VI_DIACRITICS,
    compute_weighted_total,
)
from scripts.helper import (
    calculate_dimension_score,
    count_syllables,
    parse_segments,
    resolve_pass_threshold,
)
from scripts.utils.language import detect_source_language

count_syllables_vietnamese = count_syllables

from scripts.evaluators.scoring import (
    DIMENSION_WEIGHTS,
    SEVERITY_WEIGHTS,
    UNTRANSLATED_PENALTY_RE,
    MISTRANSLATION_EXCLUSION_RE,
    _normalize_for_span_search,
    compute_hybrid_weighted_total,
    compute_penalty_dimension_scores,
    filter_shielded_verbatim_issues,
    find_normalized_span_in_text,
    normalize_span,
    validate_and_realign_issue_spans,
)


def normalize_dimension_score(score: Any) -> float:
    """Normalizes score > 5.0 (divides by 2.0 if <= 10.0, clamps to [0.0, 5.0])."""
    try:
        val = float(score)
        if val > 5.0 and val <= 10.0:
            val = val / 2.0
        return max(0.0, min(5.0, val))
    except (ValueError, TypeError):
        return 5.0


def partition_segments_into_windows(
    segments: List[Dict[str, Any]],
    window_size: int,
    overlap: int = 0,
) -> List[List[Dict[str, Any]]]:
    """
    Splits segments into contiguous chunks of core size window_size.
    Subsequent windows include up to `overlap` preceding segments tagged with is_context=True.
    """
    if not segments:
        return []
    if window_size <= 0 or len(segments) <= window_size:
        return [segments]

    windows: List[List[Dict[str, Any]]] = []
    for i in range(0, len(segments), window_size):
        core = [dict(s, is_context=False) for s in segments[i : i + window_size]]
        if i > 0 and overlap > 0:
            prefix_start = max(0, i - overlap)
            ctx = [dict(s, is_context=True) for s in segments[prefix_start : i]]
            windows.append(ctx + core)
        else:
            windows.append(core)
    return windows


def enforce_language_detection(
    result_data: Dict[str, Any],
    draft_text: str,
    segments: Optional[List[Dict[str, str]]] = None,
    source_lang: str = "en",
) -> Dict[str, Any]:
    """
    Inspects draft text for untranslated source content (English or Chinese).
    If detected, lowers fidelity/fluency/register/completeness to 1.0, forces FAIL verdict.
    """
    trans_texts: List[str] = []
    segs = segments if segments is not None else (parse_segments(draft_text) if "<segment " in draft_text else [])

    if segs:
        for s in segs:
            if s.get("type") not in ("figure", "code"):
                t = s.get("translate", "")
                if t:
                    trans_texts.append(t)
    else:
        trans_texts.append(draft_text)

    all_trans = " ".join(trans_texts).strip()
    alpha_chars = [c for c in all_trans if c.isalpha()]
    vi_diacritic_count = sum(1 for c in alpha_chars if c in VI_DIACRITICS)
    vi_ratio = vi_diacritic_count / len(alpha_chars) if alpha_chars else 0.0

    words = re.findall(r"\b[a-zA-Z]+\b", all_trans.lower())
    en_word_count = sum(1 for w in words if w in COMMON_EN_WORDS)
    en_ratio = en_word_count / len(words) if words else 0.0

    ref_segs = sum(1 for s in segs if re.match(r"^\s*\[\d+\]", s.get("original", "")))
    is_reference_block = len(segs) > 0 and (ref_segs / len(segs) >= 0.5)

    if source_lang == "zh":
        cjk_chars = sum(
            1 for c in all_trans
            if (0x4E00 <= ord(c) <= 0x9FFF) or (0x3400 <= ord(c) <= 0x4DBF) or (0xF900 <= ord(c) <= 0xFAFF)
        )
        non_space_chars = sum(1 for c in all_trans if not c.isspace())
        if non_space_chars > 20 and (cjk_chars / non_space_chars) > 0.15:
            if not any(i.get("id") == "lang-1" for i in result_data.get("issues", [])):
                result_data.setdefault("issues", []).insert(
                    0,
                    {
                        "id": "lang-1",
                        "dimension": "completeness",
                        "severity": "critical",
                        "pervasive": True,
                        "span_source": "",
                        "span_target": "",
                        "problem": "Draft contains untranslated Chinese text; no Vietnamese translation was produced.",
                        "suggestion": "Translate all Chinese content into natural, high-quality Vietnamese.",
                    },
                )
            dims = result_data.setdefault("dimensions", {})
            all_dims = ["fidelity", "fluency", "terminology", "register", "structure", "completeness"]
            for d in all_dims:
                if d == "terminology":
                    dims[d] = min(dims.get(d, 5.0), 1.0)
                else:
                    dims[d] = 1.0
            result_data["weighted_total"] = compute_weighted_total(dims)
            result_data["verdict"] = "FAIL"
    else:
        if not is_reference_block and len(alpha_chars) > 20 and (vi_ratio < 0.05 or (en_ratio > 0.35 and vi_ratio < 0.10)):
            if not any(i.get("id") == "lang-1" for i in result_data.get("issues", [])):
                result_data.setdefault("issues", []).insert(
                    0,
                    {
                        "id": "lang-1",
                        "dimension": "completeness",
                        "severity": "critical",
                        "pervasive": True,
                        "span_source": "",
                        "span_target": "",
                        "problem": "Draft contains untranslated English text; no Vietnamese translation was produced.",
                        "suggestion": "Translate all English content into natural, high-quality Vietnamese.",
                    },
                )
            dims = result_data.setdefault("dimensions", {})
            all_dims = ["fidelity", "fluency", "terminology", "register", "structure", "completeness"]
            for d in all_dims:
                if d == "terminology":
                    dims[d] = min(dims.get(d, 5.0), 1.0)
                else:
                    dims[d] = 1.0
            result_data["weighted_total"] = compute_weighted_total(dims)
            result_data["verdict"] = "FAIL"

    return result_data


def run_evaluation(
    source_path: str,
    draft_path: str,
    book_record_path: Optional[str] = None,
    output_path: Optional[str] = None,
    force_mock: bool = False,
    timeout: int = 300,
    window_size: int = 250,
    engine: str = "workbuddy",
    source_lang: str = "auto",
    pass_threshold: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Evaluates source vs draft using selected engine with window partitioning and issue aggregation.

    Args:
        source_path: Path to source text or segmented chapter file
        draft_path: Path to translated draft chapter file
        book_record_path: Optional path to book_record.json
        output_path: Optional path where output JSON is written
        force_mock: If True, forces deterministic fallback evaluation
        timeout: Evaluation timeout in seconds
        window_size: Maximum segments per evaluation window
        engine: 'workbuddy' or 'fallback'
        source_lang: 'auto', 'en', or 'zh'

    Returns:
        Structured evaluation envelope dictionary
    """
    with open(source_path, "r", encoding="utf-8") as f:
        source_text = f.read()

    with open(draft_path, "r", encoding="utf-8") as f:
        draft_text = f.read()

    book_record = None
    if book_record_path and os.path.isfile(book_record_path):
        try:
            with open(book_record_path, "r", encoding="utf-8") as f:
                book_record = json.load(f)
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to load book_record: {e}\n")

    effective_source_lang = source_lang
    if effective_source_lang == "auto":
        if book_record and isinstance(book_record, dict) and book_record.get("source_lang"):
            effective_source_lang = book_record["source_lang"]
        else:
            effective_source_lang = detect_source_language(source_text)

    effective_pass_threshold = (
        float(pass_threshold)
        if pass_threshold is not None
        else resolve_pass_threshold(book_record=book_record)
    )

    selected_engine = "fallback" if force_mock else engine
    evaluator: BaseEvaluator = get_evaluator(selected_engine)

    segments = parse_segments(draft_text)

    # Single pass if <= window_size or no XML segments parsed
    if not segments or window_size <= 0 or len(segments) <= window_size:
        result_data = evaluator.evaluate(source_text, draft_text, book_record, timeout=timeout, source_lang=effective_source_lang)
        if "issues" in result_data:
            result_data["issues"], _ = validate_and_realign_issue_spans(result_data["issues"], segments or draft_text)
            result_data["issues"], suppressed_cnt = filter_shielded_verbatim_issues(result_data["issues"], book_record)
            if suppressed_cnt:
                result_data["suppressed_verbatim_count"] = suppressed_cnt
        dims = result_data.get("dimensions", {})
        penalty_dims = compute_penalty_dimension_scores(result_data.get("issues", []), segments or draft_text)
        norm_dims: Dict[str, float] = {}
        if isinstance(dims, dict):
            for k in DIMENSION_WEIGHTS:
                llm_v = normalize_dimension_score(dims.get(k, 5.0)) if k in dims else 5.0
                norm_dims[k] = round(min(float(penalty_dims[k]), float(llm_v)), 2)
            for k, v in dims.items():
                if k not in norm_dims:
                    norm_dims[k] = round(normalize_dimension_score(v), 2)
        else:
            norm_dims = {k: round(float(penalty_dims[k]), 2) for k in DIMENSION_WEIGHTS}
        result_data["dimensions"] = norm_dims
        result_data["weighted_total"] = compute_weighted_total(norm_dims)
        result_data = enforce_language_detection(result_data, draft_text, segments, source_lang=effective_source_lang)
        wt = result_data.get("weighted_total", 5.0)
        crit = [
            i for i in result_data.get("issues", [])
            if isinstance(i, dict) and i.get("severity") == "critical"
        ]
        result_data["verdict"] = "FAIL" if (crit or wt < effective_pass_threshold) else "PASS"
    else:
        # Multi-window sliding evaluation
        windows = partition_segments_into_windows(segments, window_size, overlap=3)

        def _seg_draft(s: Dict[str, Any]) -> str:
            ctx_attr = ' context="true"' if s.get("is_context") else ""
            return (
                f'<segment id="{s["id"]}" type="{s.get("type", "paragraph")}"{ctx_attr}>\n'
                f'<original>\n{s.get("original", "")}\n</original>\n'
                f'<translate>\n{s.get("translate", "")}\n</translate>\n'
                f'</segment>'
            )

        def _seg_source(s: Dict[str, Any]) -> str:
            ctx_attr = ' context="true"' if s.get("is_context") else ""
            return (
                f'<segment id="{s["id"]}" type="{s.get("type", "paragraph")}"{ctx_attr}>\n'
                f'<original>\n{s.get("original", "")}\n</original>\n'
                f'</segment>'
            )

        def _eval_window(item: Any) -> Dict[str, Any]:
            if isinstance(item, tuple):
                w_idx, win = item
            else:
                w_idx, win = 0, item
            sys.stderr.write(f"Starting evaluation of window {w_idx+1}/{len(windows)} ({len(win)} segments)...\n")
            sys.stderr.flush()
            win_draft = "\n\n".join(_seg_draft(s) for s in win)
            win_source = "\n\n".join(_seg_source(s) for s in win)
            res = evaluator.evaluate(win_source, win_draft, book_record, timeout=timeout, source_lang=effective_source_lang)
            sys.stderr.write(f"Completed evaluation of window {w_idx+1}/{len(windows)}.\n")
            sys.stderr.flush()
            return res

        indexed_windows = list(enumerate(windows))
        if len(windows) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(windows))) as executor:
                window_results = list(executor.map(_eval_window, indexed_windows))
        else:
            window_results = [_eval_window(w) for w in indexed_windows]

        # 1. Aggregate and deduplicate issues, discarding issues targeting context segments
        seen_issue_keys = set()
        deduped_issues: List[Dict[str, Any]] = []
        for win, win_res in zip(windows, window_results):
            context_ids = {s.get("id") for s in win if s.get("is_context")}
            for iss in win_res.get("issues", []):
                seg_id = str(iss.get("segment_id", ""))
                if seg_id in context_ids:
                    continue
                dim = str(iss.get("dimension", ""))
                target_str = iss.get("span_target") or iss.get("unverified_span_target") or ""
                span_target = normalize_span(str(target_str))
                sig = (seg_id, dim, span_target)
                if sig in seen_issue_keys:
                    continue
                seen_issue_keys.add(sig)
                deduped_issues.append(dict(iss))

        # Validate and realign issue spans across chapter segments
        deduped_issues, _ = validate_and_realign_issue_spans(deduped_issues, segments or draft_text)

        # Ensure unique issue IDs
        used_ids = set()
        for idx, iss in enumerate(deduped_issues, start=1):
            curr_id = iss.get("id")
            if not curr_id or curr_id in used_ids:
                curr_id = f"i{idx}"
                iss["id"] = curr_id
            used_ids.add(curr_id)

        # REQ-005: Shielded verbatim term filtering
        deduped_issues, suppressed_cnt = filter_shielded_verbatim_issues(deduped_issues, book_record)

        # 2. Syllable-weighted dimension scores (excluding context="true" segments)
        win_core_syllables = []
        for win in windows:
            core_text = " ".join(s.get("translate", "") for s in win if not s.get("is_context"))
            syl_count = count_syllables_vietnamese(core_text)
            win_core_syllables.append(max(1, syl_count))

        total_core_syllables = sum(win_core_syllables)
        core_segs = [s for s in segments if not s.get("is_context")]
        penalty_dims = compute_penalty_dimension_scores(deduped_issues, core_segs or draft_text)
        all_dimensions = ["fidelity", "fluency", "terminology", "register", "structure", "completeness"]
        dim_scores: Dict[str, float] = {}
        for d in all_dimensions:
            weighted_score = sum(
                normalize_dimension_score(win_res.get("dimensions", {}).get(d, 5.0)) * w_syl
                for win_res, w_syl in zip(window_results, win_core_syllables)
            ) / max(1, total_core_syllables)
            llm_d = normalize_dimension_score(weighted_score)
            p_d = float(penalty_dims.get(d, 5.0))
            dim_scores[d] = round(min(p_d, llm_d), 2)

        # 3. Calculate weighted total via rubric weights
        weighted_total = compute_weighted_total(dim_scores)

        critical_issues = [
            i for i in deduped_issues
            if isinstance(i, dict) and i.get("severity") == "critical"
        ]
        verdict = "FAIL" if (critical_issues or weighted_total < effective_pass_threshold) else "PASS"

        total_segs = len(segments)
        result_data = {
            "issues": deduped_issues,
            "dimensions": dim_scores,
            "weighted_total": weighted_total,
            "verdict": verdict,
            "notes": f"Aggregated evaluation across {len(windows)} sliding windows (total segments: {total_segs}).",
            "window_count": len(windows),
            "suppressed_verbatim_count": suppressed_cnt,
        }

        # Propagate fallback_unverified=True if any window has fallback_unverified=True
        if any(bool(win_res.get("fallback_unverified")) for win_res in window_results):
            result_data["fallback_unverified"] = True

        # Enforce chapter-wide language detection guard
        result_data = enforce_language_detection(result_data, draft_text, segments, source_lang=effective_source_lang)
        wt = result_data.get("weighted_total", 5.0)
        crit = [
            i for i in result_data.get("issues", [])
            if isinstance(i, dict) and i.get("severity") == "critical"
        ]
        result_data["verdict"] = "FAIL" if (crit or wt < effective_pass_threshold) else "PASS"

    # Write output file if specified
    if output_path:
        out_dir = os.path.dirname(os.path.abspath(output_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)

    return result_data


def evaluate_file(*args, **kwargs) -> Dict[str, Any]:
    """Flexible wrapper for run_evaluation supporting varied parameter conventions."""
    if "draft" in kwargs and "draft_path" not in kwargs:
        kwargs["draft_path"] = kwargs.pop("draft")
    if "source" in kwargs and "source_path" not in kwargs:
        kwargs["source_path"] = kwargs.pop("source")
    if "book_record" in kwargs and "book_record_path" not in kwargs:
        kwargs["book_record_path"] = kwargs.pop("book_record")
    if "output" in kwargs and "output_path" not in kwargs:
        kwargs["output_path"] = kwargs.pop("output")
    return run_evaluation(*args, **kwargs)


def parse_args():
    parser = argparse.ArgumentParser(description="Unified Modular Translation Evaluation Runner.")
    parser.add_argument("--draft", required=True, help="Path to draft translation")
    parser.add_argument("--source", required=True, help="Path to source chapter")
    parser.add_argument("--book-record", default=None, help="Path to book_record.json")
    parser.add_argument("--output", required=True, help="Path to output evaluation JSON")
    parser.add_argument("--mock", action="store_true", help="Force mock/fallback evaluation")
    parser.add_argument("--timeout", type=int, default=300, help="Evaluation timeout in seconds")
    parser.add_argument("--window-size", type=int, default=250, help="Maximum segments per evaluation window")
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
        help="Source language ('auto', 'en', or 'zh')",
    )
    parser.add_argument(
        "--pass-threshold",
        type=float,
        default=None,
        help="Pass threshold for verdict calculation (defaults to resolve_pass_threshold)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    result = run_evaluation(
        source_path=args.source,
        draft_path=args.draft,
        book_record_path=args.book_record,
        output_path=args.output,
        force_mock=args.mock,
        timeout=args.timeout,
        window_size=args.window_size,
        engine=args.engine,
        source_lang=args.source_lang,
        pass_threshold=args.pass_threshold,
    )

    print(f"Evaluation completed. Output written to: {args.output}")
    print(f"Dimensions: {json.dumps(result.get('dimensions', {}))}")
    print(f"Issues count: {len(result.get('issues', []))}")


if __name__ == "__main__":
    main()
