"""
scripts/evaluators/fallback.py — Deterministic Fallback & Rule-Based Evaluator
Encapsulates offline heuristic checks:
- Language detection (untranslated English content vs Vietnamese diacritics)
- Byte-invariance and segment parity (figures, code blocks, segment tags)
- Vietnamese grammar checks (passive voice calques, topic-comment structure)
- Multi-option canonical terminology validation (REQ-EVAL-2)
"""

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.evaluators.base import BaseEvaluator
from scripts.evaluators.anti_convert import check_anti_convert_issues
from scripts.evaluators.scoring import (
    compute_weighted_total,
    filter_shielded_verbatim_issues,
    validate_and_realign_issue_spans,
)
from scripts.helper import parse_segments, validate_segment_parity
from scripts.utils.terminology import extract_verbatim_terms, terms_present_in

DEFAULT_RUBRIC_WEIGHTS: Dict[str, float] = {
    "fidelity": 0.25,
    "fluency": 0.20,
    "terminology": 0.20,
    "register": 0.15,
    "structure": 0.10,
    "completeness": 0.10,
}

VI_DIACRITICS: Set[str] = set("àáảãạâầấẩẫậăằắẳẵặèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđÀÁẢÃẠÂẦẤẨẪẬĂẰẮẲẴẶÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ")

COMMON_EN_WORDS: Set[str] = {
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "i", "it", "for", "not", "on", "with",
    "he", "as", "you", "do", "at", "this", "but", "his", "by", "from", "they", "we", "say", "her",
    "she", "or", "an", "will", "my", "one", "all", "would", "there", "their", "what", "so", "up",
    "out", "if", "about", "who", "get", "which", "go", "me", "when", "make", "can", "like", "time",
    "no", "just", "him", "know", "take", "people", "into", "year", "your", "good", "some", "could",
    "them", "see", "other", "than", "then", "now", "look", "only", "come", "its", "over", "think",
    "also", "back", "after", "use", "two", "how", "our", "work", "first", "well", "way", "even",
    "new", "want", "because", "any", "these", "give", "day", "most", "us", "is", "are", "was", "were"
}


def _extract_table(book_record: Optional[Dict[str, Any]], keys: List[str]) -> Dict[str, str]:
    """Extracts lookup dictionary from book_record supporting dict or list of {source, target}."""
    if not book_record:
        return {}
    raw = None
    for k in keys:
        if k in book_record:
            raw = book_record[k]
            break
    if not raw and "book_record" in book_record and isinstance(book_record["book_record"], dict):
        for k in keys:
            if k in book_record["book_record"]:
                raw = book_record["book_record"][k]
                break
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        return {
            item["source"]: item["target"]
            for item in raw
            if isinstance(item, dict) and "source" in item and "target" in item
        }
    return {}


def is_vietnamese_text(text: str) -> bool:
    """Checks whether the text has sufficient Vietnamese diacritics."""
    alpha_chars = [c for c in text if c.isalpha()]
    if not alpha_chars:
        return True
    vi_diacritic_count = sum(1 for c in alpha_chars if c in VI_DIACRITICS)
    return (vi_diacritic_count / len(alpha_chars)) >= 0.05


def check_term_match(target_term: str, draft_lower: str) -> bool:
    """
    Checks if target_term matches draft text (REQ-EVAL-2).
    Splits slash-delimited options (/) and strips parentheticals (e.g. '(Hard Money)').
    Returns True if ANY valid candidate is present in draft_lower.
    """
    if not target_term or not isinstance(target_term, str):
        return True
    raw_options = [opt.strip() for opt in target_term.split("/") if opt.strip()]
    candidates: Set[str] = set()
    for opt in raw_options:
        candidates.add(opt.lower())
        base = re.sub(r"\(.*?\)", "", opt).strip()
        if base:
            candidates.add(base.lower())
    return any(c in draft_lower for c in candidates if c)


def evaluate_fallback(
    source_text: str,
    draft_text: str,
    book_record: Optional[Dict[str, Any]] = None,
    source_lang: Optional[str] = "en",
) -> Dict[str, Any]:
    """
    Deterministic rule-based fallback evaluation for offline or unauthenticated runs.
    Checks segment parity, language detection (Vietnamese diacritics), code/figure byte-invariance,
    passive voice calques, anti-convert rules (Chinese), and terminology.
    """
    effective_lang = source_lang or "en"
    if book_record and isinstance(book_record, dict) and book_record.get("source_lang"):
        effective_lang = book_record["source_lang"]

    issues: List[Dict[str, Any]] = []
    dim_scores: Dict[str, float] = {
        "fidelity": 5.0,
        "fluency": 5.0,
        "terminology": 5.0,
        "register": 5.0,
        "structure": 5.0,
        "completeness": 5.0,
    }

    # 0. Vietnamese language detection check
    trans_texts: List[str] = []
    if "<segment " in draft_text:
        draft_segs = parse_segments(draft_text)
        for s in draft_segs:
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

    words = re.findall(r'\b[a-zA-Z]+\b', all_trans.lower())
    en_word_count = sum(1 for w in words if w in COMMON_EN_WORDS)
    en_ratio = en_word_count / len(words) if words else 0.0

    segs_list = parse_segments(draft_text) if "<segment " in draft_text else []
    ref_segs = sum(1 for s in segs_list if re.match(r'^\s*\[\d+\]', s.get('original', '')))
    is_reference_block = len(segs_list) > 0 and (ref_segs / len(segs_list) >= 0.5)

    is_zh_source = (
        source_lang == "zh"
        or effective_lang == "zh"
        or bool(re.search(r"[\u4e00-\u9fff]", source_text))
    )
    target_to_check = all_trans if all_trans else draft_text

    if is_zh_source:
        cjk_matches = re.findall(r"[\u4e00-\u9fff]{2,}", target_to_check)
        if cjk_matches:
            count = len(cjk_matches)
            sample = cjk_matches[0]
            issues.append({
                "id": "lang-cjk-leak",
                "dimension": "completeness",
                "severity": "critical",
                "pervasive": count > 5,
                "span_source": "",
                "span_target": sample,
                "problem": f"Untranslated Chinese text detected in Vietnamese draft ({count} occurrences, e.g. {sample})",
                "suggestion": "Translate all raw Chinese text into fluent Vietnamese.",
            })
            dim_scores["completeness"] = min(dim_scores["completeness"], 1.0)
            dim_scores["fidelity"] = min(dim_scores["fidelity"], 1.0)
        else:
            cjk_chars = sum(
                1 for c in all_trans
                if (0x4E00 <= ord(c) <= 0x9FFF) or (0x3400 <= ord(c) <= 0x4DBF) or (0xF900 <= ord(c) <= 0xFAFF)
            )
            non_space_chars = sum(1 for c in all_trans if not c.isspace())
            if non_space_chars > 20 and (cjk_chars / non_space_chars) > 0.15:
                issues.append({
                    "id": "lang-1",
                    "dimension": "completeness",
                    "severity": "critical",
                    "pervasive": True,
                    "span_source": "",
                    "span_target": "",
                    "problem": "Draft contains untranslated Chinese text; no Vietnamese translation was produced.",
                    "suggestion": "Translate all Chinese content into natural, high-quality Vietnamese.",
                })
                dim_scores["fidelity"] = 1.0
                dim_scores["fluency"] = 1.0
                dim_scores["register"] = 1.0
                dim_scores["completeness"] = 1.0
                dim_scores["terminology"] = 1.0
    else:
        if not is_reference_block and len(alpha_chars) > 20 and (vi_ratio < 0.05 or (en_ratio > 0.35 and vi_ratio < 0.10)):
            issues.append({
                "id": "lang-1",
                "dimension": "completeness",
                "severity": "critical",
                "pervasive": True,
                "span_source": "",
                "span_target": "",
                "problem": "Draft contains untranslated English text; no Vietnamese translation was produced.",
                "suggestion": "Translate all English content into natural, high-quality Vietnamese.",
            })
            dim_scores["fidelity"] = 1.0
            dim_scores["fluency"] = 1.0
            dim_scores["register"] = 1.0
            dim_scores["completeness"] = 1.0
            dim_scores["terminology"] = 1.0

    # 1. Segment parity & byte-invariance check
    if "<segment " in draft_text:
        is_valid, parity_errors = validate_segment_parity(source_text, draft_text)
        if not is_valid:
            for idx, err in enumerate(parity_errors, start=1):
                issues.append({
                    "id": f"struct-{idx}",
                    "dimension": "structure",
                    "severity": "critical",
                    "pervasive": False,
                    "span_source": "",
                    "span_target": "",
                    "problem": err,
                    "suggestion": "Restore exact segment parity and code/figure byte invariance.",
                })
            dim_scores["structure"] = max(1.0, 5.0 - len(parity_errors) * 1.5)

    # 2. Vietnamese grammar checks (passive voice calques)
    passive_calques = [
        "được thực hiện bởi",
        "được tạo ra bởi",
        "được viết bởi",
        "trong một nỗ lực để",
    ]
    for idx, calque in enumerate(passive_calques, start=1):
        if calque in draft_text.lower():
            issues.append({
                "id": f"grammar-{idx}",
                "dimension": "fluency",
                "severity": "major",
                "pervasive": False,
                "span_source": "",
                "span_target": calque,
                "problem": f"Unnatural passive voice calque '{calque}'.",
                "suggestion": "Use active voice or natural Vietnamese topic-comment construction.",
            })

    # 2.5 Anti-convert checks for Chinese translations (REQ-ZH-3)
    if effective_lang == "zh":
        ac_issues = check_anti_convert_issues(draft_text, effective_lang)
        if ac_issues:
            issues.extend(ac_issues)

    # 3. Terminology check with Multi-Option matching (REQ-EVAL-2 / REQ-QUAL-003)
    draft_lower = draft_text.lower()
    if book_record:
        verbatim_terms = extract_verbatim_terms(book_record)
        verbatim_set = set(v.strip().lower() for v in verbatim_terms if v and v.strip())
        terms = _extract_table(book_record, ["term_table", "terms"])
        term_pairs: List[Tuple[str, str]] = []
        if isinstance(terms, dict):
            for orig_term, target_term in terms.items():
                if str(orig_term).strip().lower() in verbatim_set:
                    continue
                term_pairs.append((str(orig_term).strip(), str(target_term).strip()))
        elif isinstance(terms, list):
            for t in terms:
                if isinstance(t, dict):
                    orig_term = t.get("source", "").strip()
                    target_term = t.get("target", "").strip()
                    if not orig_term or orig_term.lower() in verbatim_set or t.get("verbatim") is True:
                        continue
                    term_pairs.append((orig_term, target_term))

        if term_pairs:
            glossary_map = {orig: tgt for orig, tgt in term_pairs if orig}
            present_hits = terms_present_in(source_text, glossary_map, min_han_len=1)
            present_sources = {h["source"] for h in present_hits}
            for orig_term, target_term in term_pairs:
                if orig_term in present_sources:
                    if target_term and not check_term_match(target_term, draft_lower):
                        issues.append({
                            "id": f"term-{len(issues)+1}",
                            "dimension": "terminology",
                            "severity": "critical",
                            "pervasive": False,
                            "span_source": orig_term,
                            "span_target": "",
                            "problem": f"Canonical term '{orig_term}' -> '{target_term}' not found in draft.",
                            "suggestion": f"Use canonical rendering '{target_term}'.",
                        })

    issues, _ = validate_and_realign_issue_spans(issues, draft_segs if "<segment " in draft_text else draft_text)
    issues, suppressed_cnt = filter_shielded_verbatim_issues(issues, book_record)

    # Compute deductions from validated, non-suppressed issues
    ac_count = 0
    for iss in issues:
        iss_id = str(iss.get("id", ""))
        iss_dim = str(iss.get("dimension", ""))
        if iss_id.startswith("grammar-"):
            dim_scores["fluency"] = max(2.0, dim_scores["fluency"] - 0.5)
        elif iss_id.startswith(("ac-", "ac_")):
            ac_count += 1
            dim_scores["fluency"] = max(1.0, dim_scores["fluency"] - 0.5)
            dim_scores["register"] = max(1.0, dim_scores["register"] - 0.25)
            if ac_count >= 5:
                dim_scores["fidelity"] = max(1.0, dim_scores["fidelity"] - 0.25)
                iss["pervasive"] = True
            if ac_count >= 6:
                iss["severity"] = "critical"
        elif iss_id.startswith("term-") or iss_dim == "terminology":
            dim_scores["terminology"] = max(1.0, dim_scores["terminology"] - 1.5)

    weighted = compute_weighted_total(dim_scores)
    critical_issues = [
        i for i in issues
        if isinstance(i, dict) and i.get("severity") == "critical"
    ]
    verdict = "FAIL" if (critical_issues or weighted < 4.0) else "PASS"

    return {
        "issues": issues,
        "dimensions": dim_scores,
        "weighted_total": weighted,
        "verdict": verdict,
        "notes": "Evaluation generated via WorkBuddy fallback evaluator.",
        "fallback_unverified": True,
        "suppressed_verbatim_count": suppressed_cnt,
    }


class FallbackEvaluator(BaseEvaluator):
    """Deterministic, rule-based evaluator used as standalone engine or fallback."""

    def evaluate(
        self,
        source_text: str,
        draft_text: str,
        book_record: Optional[Dict[str, Any]] = None,
        timeout: int = 300,
        source_lang: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        effective_source_lang = source_lang or (book_record.get("source_lang", "en") if book_record else "en")
        res = evaluate_fallback(source_text, draft_text, book_record, source_lang=effective_source_lang)
        res["fallback_unverified"] = True
        return res
