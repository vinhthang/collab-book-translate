#!/usr/bin/env python3
"""
scripts/refine_chapter.py — Targeted Translation Refinement Subagent Runner
Implements the 'fix' path of the translation pipeline adhering to agents/refine.md:
- Loads previous round evaluation report (round<R-1>_evaluate.txt)
- Filters for critical and major issues (or minor if none)
- Maps issues to segment IDs in the bilingual draft (round<R-1>_translate.txt)
- Patches segments via WorkBuddy cbc (deepseek-v4.1-flash) or offline rules
- Enforces NFR-6 byte-invariance via helper.diff_and_splice
- Validates segment parity and strips to final clean markdown
- Writes output to round<R>_translate.txt and final.txt
"""

import argparse
import collections
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.helper import (
    diff_and_splice,
    diff_and_splice_joint,
    derive_addressed_issues,
    mask_math_formulas,
    parse_segments,
    project_book_record_for_chapter,
    strip_segments_to_final,
    unmask_math_formulas,
    validate_and_repair_gfm_table,
    validate_segment_parity,
)
from scripts.translate_chapter import TranslationCacheManager
from scripts.utils.punctuation import ensure_footnote_numbering
from scripts.utils.terminology import (
    apply_dynamic_terminology,
    atomic_write_json,
    atomic_write_text,
    build_term_rules,
    extract_verbatim_terms,
    find_cbc_binary,
    interprocess_lock,
    load_book_record,
    snapshot_backup,
)

DEFAULT_REFINE_MODEL: str = "hy3"


def _normalize_match_text(text: str) -> str:
    t = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r'[\u2010\u2011\u2012\u2013\u2014-]\s*\n\s*', '', t)
    t = re.sub(r'\s+', ' ', t)
    return t.strip().lower()


def _strip_stray_math(text: str, orig: str, trans: str) -> str:
    """Strips leftover ⟦MATH_...⟧ placeholders only if they were introduced during masking (not in source text)."""
    def repl(m: re.Match) -> str:
        ph = m.group(0).strip()
        if ph in orig or ph in trans:
            return m.group(0)
        return ""

    return re.sub(r'\s*⟦(?:ORIG_)?MATH_(?:[0-9a-f]+_)?\d+⟧', repl, text)


def map_issues_to_segments(
    issues: List[Dict[str, Any]],
    segments: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Maps evaluation issues to segment IDs based on:
    1. Direct 'segment_id' or 'seg_id' field in issue, or 'S-NNNN' references in problem text.
    2. Exact or normalized substring matching of 'span_target' in segment translation.
    3. Exact or normalized substring matching of 'span_source' in segment original text.
    4. Substring matching of key terms extracted from issue problem text.
    """
    mapping: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)

    # Pre-build normalized segments
    norm_segments = [
        {
            "id": s["id"],
            "raw": s,
            "trans_norm": _normalize_match_text(s.get("translate", "")),
            "orig_norm": _normalize_match_text(s.get("original", "")),
        }
        for s in segments
    ]

    seg_by_id = {s["id"]: s for s in segments if "id" in s}

    for issue in issues:
        direct_id = issue.get("segment_id") or issue.get("seg_id")
        span_target = (issue.get("span_target") or "").strip()
        span_source = (issue.get("span_source") or "").strip()

        # AC-010.1: Check if span_target crosses contiguous segment boundary
        matched_pair = False
        if span_target:
            # Check contiguous segment pairs joined by \n\n
            for i in range(len(segments) - 1):
                s1, s2 = segments[i], segments[i + 1]
                t1 = s1.get("translate", "")
                t2 = s2.get("translate", "")
                joint = f"{t1}\n\n{t2}"
                st_norm = _normalize_match_text(span_target)
                joint_norm = _normalize_match_text(joint)

                if (span_target.lower() in joint.lower() or (st_norm and st_norm in joint_norm)):
                    # Check that span is not strictly contained inside s1 alone or s2 alone
                    in_s1 = span_target.lower() in t1.lower() or (st_norm and st_norm in _normalize_match_text(t1))
                    in_s2 = span_target.lower() in t2.lower() or (st_norm and st_norm in _normalize_match_text(t2))
                    if "\n\n" in span_target or (not in_s1 and not in_s2):
                        mapping[(s1["id"], s2["id"])].append(issue)
                        matched_pair = True
                        break

        if matched_pair:
            continue

        if direct_id and direct_id in seg_by_id:
            mapping[direct_id].append(issue)
            continue

        # Check for explicit segment references in problem or suggestion text
        ref_text = (issue.get("problem") or "") + " " + (issue.get("suggestion") or "")
        ref_sids = re.findall(r'\b(S-\d{4})\b', ref_text)
        if ref_sids:
            for sid in ref_sids:
                mapping[sid].append(issue)
            continue

        matched = False
        span_target = (issue.get("span_target") or "").strip()
        span_source = (issue.get("span_source") or "").strip()

        if span_target:
            # 1. Exact substring
            for s in segments:
                trans = s.get("translate", "")
                if span_target.lower() in trans.lower():
                    mapping[s["id"]].append(issue)
                    matched = True
                    break

            # 2. Normalized substring
            if not matched:
                st_norm = _normalize_match_text(span_target)
                if st_norm:
                    for ns in norm_segments:
                        if st_norm in ns["trans_norm"] or (len(st_norm) > 15 and ns["trans_norm"] in st_norm):
                            mapping[ns["id"]].append(issue)
                            matched = True
                            break
                        # Prefix words match
                        words = st_norm.split()[:5]
                        if len(words) >= 4 and " ".join(words) in ns["trans_norm"]:
                            mapping[ns["id"]].append(issue)
                            matched = True
                            break

        if not matched and span_source:
            # 1. Exact substring
            for s in segments:
                orig = s.get("original", "")
                if span_source.lower() in orig.lower():
                    mapping[s["id"]].append(issue)
                    matched = True
                    break

            # 2. Normalized substring
            if not matched:
                ss_norm = _normalize_match_text(span_source)
                if ss_norm:
                    for ns in norm_segments:
                        if ss_norm in ns["orig_norm"] or (len(ss_norm) > 15 and ns["orig_norm"] in ss_norm):
                            mapping[ns["id"]].append(issue)
                            matched = True
                            break
                        words = ss_norm.split()[:5]
                        if len(words) >= 4 and " ".join(words) in ns["orig_norm"]:
                            mapping[ns["id"]].append(issue)
                            matched = True
                            break

        if not matched:
            problem_text = issue.get("problem", "")
            term_match = re.search(r"Canonical term ['\"](.*?)['\"]", problem_text)
            if term_match:
                term = term_match.group(1).strip()
                for s in segments:
                    if term.lower() in s.get("original", "").lower():
                        mapping[s["id"]].append(issue)
                        matched = True
                        break

    return mapping


def patch_segment_offline(
    seg_original: str,
    seg_translate: str,
    issues: List[Dict[str, Any]],
    book_record: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Rule-based local patcher for offline or fallback execution:
    - Applies dynamic terminology rules from book_record.
    - Replaces passive voice calques and explicit flagged spans from suggestions.
    """
    current_trans = seg_translate

    # 1. Apply book_record terminology rules if present
    if book_record:
        if isinstance(book_record, tuple):
            terms = book_record[0]
            names = book_record[1] if len(book_record) > 1 else []
            verbatim_terms = []
        else:
            terms = book_record.get("terms", [])
            names = book_record.get("names", [])
            verbatim_terms = extract_verbatim_terms(book_record)
        rules = build_term_rules(terms, names, verbatim_terms=verbatim_terms)
        current_trans = apply_dynamic_terminology(current_trans, rules, verbatim_terms=verbatim_terms)

    # 2. Address specific flagged issues
    for issue in issues:
        span_target = (issue.get("span_target") or "").strip()
        suggestion = (issue.get("suggestion") or "").strip()
        problem = (issue.get("problem") or "").strip()

        # Passive voice calque replacement table
        calque_fixes = {
            "được thực hiện bởi": "do",
            "được tạo ra bởi": "do",
            "được viết bởi": "do",
            "trong một nỗ lực để": "để",
        }
        for calque, replacement in calque_fixes.items():
            if calque in current_trans.lower():
                # Case-insensitive replacement preserving surrounding casing
                pattern = re.compile(re.escape(calque), re.IGNORECASE)
                current_trans = pattern.sub(replacement, current_trans)

        # Quoted suggestion replacement
        if span_target and span_target in current_trans:
            quoted = re.search(r"['\"](.*?)['\"]", suggestion)
            if quoted:
                repl = quoted.group(1).strip()
                if repl:
                    current_trans = current_trans.replace(span_target, repl)

        # Terminology replacement from problem
        term_match = re.search(r"Canonical term ['\"].*?['\"]\s*->\s*['\"](.*?)['\"]", problem)
        if term_match:
            canonical_tgt = term_match.group(1).strip()
            span_src = (issue.get("span_source") or "").strip()
            if span_src and span_src in current_trans:
                current_trans = current_trans.replace(span_src, canonical_tgt)

    return current_trans


def build_refine_prompt(
    seg_original: str,
    seg_translate: str,
    issues: List[Dict[str, Any]],
    source_lang: str = "en",
    context_before: Optional[List[str]] = None,
    book_record: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Builds refinement prompt with targeted spans, context, book record guidance,
    and anti-convert guidance if source_lang == 'zh' (AC-ARCH-3.7, REQ-QUAL-002, AC-QUAL-002-2).
    """
    has_raw_cjk = any(0x4e00 <= ord(c) <= 0x9fff for c in seg_translate)
    has_untranslated_issue = any(
        "untranslated" in (i.get("problem") or "").lower()
        or "completeness" in (i.get("dimension") or "").lower()
        for i in issues
    )

    cjk_notice = ""
    if has_raw_cjk or has_untranslated_issue:
        cjk_notice = (
            "Notice: The current translation contains untranslated raw Chinese text. "
            "You must retranslate the ENTIRE segment from the Original text into fluent Vietnamese from scratch. "
            "Do NOT preserve any untranslated Chinese text.\n\n"
        )

    ctx_block = ""
    if context_before:
        ctx_lines = "\n\n".join(c.strip() for c in context_before if c and c.strip())
        if ctx_lines:
            ctx_block = f"<context_before>\n{ctx_lines}\n</context_before>\n\n"

    br_guidance = ""
    if book_record and isinstance(book_record, dict):
        guidance_parts = []
        gloss = book_record.get("glossary") or book_record.get("terms") or []
        if isinstance(gloss, dict):
            gloss = [{"source": k, "target": v} for k, v in gloss.items()]
        if gloss and isinstance(gloss, list):
            g_lines = [f"- {e.get('source')}: {e.get('target')}" for e in gloss if isinstance(e, dict) and e.get("source") and e.get("target")]
            if g_lines:
                guidance_parts.append("Thuật ngữ bắt buộc (Glossary):\n" + "\n".join(g_lines))

        names = book_record.get("character_names") or book_record.get("names") or []
        if isinstance(names, dict):
            names = [{"source": k, "target": v} for k, v in names.items()]
        if names and isinstance(names, list):
            n_lines = [f"- {e.get('source')}: {e.get('target')}" for e in names if isinstance(e, dict) and e.get("source") and e.get("target")]
            if n_lines:
                guidance_parts.append("Tên nhân vật (Characters):\n" + "\n".join(n_lines))

        pronouns = book_record.get("pronoun_table") or book_record.get("pronouns") or []
        if pronouns and isinstance(pronouns, list):
            p_lines = [f"- {e.get('pair')}: {e.get('a_to_b')} / {e.get('b_to_a')} ({e.get('basis', '')})" for e in pronouns if isinstance(e, dict) and e.get("pair")]
            if p_lines:
                guidance_parts.append("Đại từ xưng hô (Pronouns):\n" + "\n".join(p_lines))

        if guidance_parts:
            br_guidance = "<book_record_guidance>\n" + "\n\n".join(guidance_parts) + "\n</book_record_guidance>\n\n"

    anti_convert_guidance = ""
    if source_lang == "zh":
        try:
            from scripts.evaluators.anti_convert import ANTI_CONVERT_RULES
            rules_lines = []
            for r in ANTI_CONVERT_RULES:
                pat = r["pattern"].pattern if hasattr(r.get("pattern"), "pattern") else str(r.get("pattern"))
                rules_lines.append(f"- Rule {r.get('id', 'AC')}: Thay '{pat}' bằng '{r.get('replacement')}' ({r.get('reason')})")
            anti_convert_guidance = (
                "\n\nQuy tắc Anti-Convert bắt buộc (tiếng Trung sang tiếng Việt):\n"
                + "\n".join(rules_lines)
                + "\n"
            )
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to load ANTI_CONVERT_RULES: {e}\n")

    problems = "; ".join(i.get("problem", "") for i in issues if i.get("problem"))
    targets = ", ".join(i.get("span_target", "") for i in issues if i.get("span_target"))
    suggestions = "; ".join(i.get("suggestion", "") for i in issues if i.get("suggestion"))

    prompt = (
        "You are the Refine Subagent. Revise this specific segment to resolve the flagged issue(s):\n"
        f"{cjk_notice}"
        f"{ctx_block}"
        f"{br_guidance}"
        f"Original text:\n{seg_original}\n\n"
        f"Current Translation:\n{seg_translate}\n\n"
        f"Flagged Issue: {problems}\n"
        f"Targeted Span to Fix: {targets}\n"
        f"Suggestion: {suggestions}\n\n"
        "Rules:\n"
        "1. Fix strictly the flagged problem and keep all unflagged spans accurate and intact.\n"
        "2. You MUST return the COMPLETE revised translation of the ENTIRE segment from start to finish. Never return only a fragment, clause, or single sentence.\n"
        "3. Emit strictly:\n"
        "<target>\n"
        "[complete revised segment translation]\n"
        "</target>"
        f"{anti_convert_guidance}"
    )
    return prompt


def _run_cbc_prompt(
    prompt: str,
    cbc_bin: str,
    timeout: int = 180,
    model: str = DEFAULT_REFINE_MODEL,
) -> str:
    """Executes a cbc subprocess with prompt and model."""
    cmd = [cbc_bin, "-p", "--model", model]
    proc = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"cbc invocation failed (exit {proc.returncode}): {proc.stderr}")
    return proc.stdout.strip()


def patch_segment_cbc(
    seg_original: str,
    seg_translate: str,
    issues: List[Dict[str, Any]],
    cbc_bin: str,
    timeout: int = 180,
    source_lang: str = "en",
    model: str = DEFAULT_REFINE_MODEL,
    context_before: Optional[List[str]] = None,
    book_record: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Calls WorkBuddy cbc with configured model (default: hy3) to revise a specific segment
    adhering strictly to agents/refine.md rules.
    """
    has_raw_cjk = any(0x4e00 <= ord(c) <= 0x9fff for c in seg_translate)

    prompt = build_refine_prompt(
        seg_original=seg_original,
        seg_translate=seg_translate,
        issues=issues,
        source_lang=source_lang,
        context_before=context_before,
        book_record=book_record,
    )

    out = _run_cbc_prompt(prompt, cbc_bin=cbc_bin, timeout=timeout, model=model)
    m = re.search(r"<target>(.*?)</target>", out, re.DOTALL)
    result = m.group(1).strip() if m else out

    # Safety guard: if result is suspiciously short (< 50% length of seg_translate when seg_original > 100 chars),
    # it likely truncated surrounding sentences; reject truncated output (unless fixing raw CJK leakage)
    if len(result) < 0.5 * len(seg_translate) and len(seg_original) > 100:
        if not (has_raw_cjk and not any(0x4e00 <= ord(c) <= 0x9fff for c in result)):
            sys.stderr.write(f"Warning: cbc returned truncated segment ({len(result)} vs {len(seg_translate)} chars). Retaining original translation.\n")
            return seg_translate

    return result

_DEFAULT_PATCH_SEGMENT_CBC = patch_segment_cbc


def build_batch_refine_prompt(
    batch_items: List[Dict[str, Any]],
    source_lang: str = "en",
    book_record: Optional[Dict[str, Any]] = None,
) -> str:
    """
    REQ-PERF-001 / AC-PERF-001-1:
    Packs multiple single-segment repair items into <segment id="S-XXXX"> blocks with shared book_record.
    """
    br_guidance = ""
    if book_record and isinstance(book_record, dict):
        guidance_parts = []
        gloss = book_record.get("glossary") or book_record.get("terms") or []
        if isinstance(gloss, dict):
            gloss = [{"source": k, "target": v} for k, v in gloss.items()]
        if gloss and isinstance(gloss, list):
            g_lines = [f"- {e.get('source')}: {e.get('target')}" for e in gloss if isinstance(e, dict) and e.get("source") and e.get("target")]
            if g_lines:
                guidance_parts.append("Thuật ngữ bắt buộc (Glossary):\n" + "\n".join(g_lines))
        names = book_record.get("character_names") or book_record.get("names") or []
        if isinstance(names, dict):
            names = [{"source": k, "target": v} for k, v in names.items()]
        if names and isinstance(names, list):
            n_lines = [f"- {e.get('source')}: {e.get('target')}" for e in names if isinstance(e, dict) and e.get("source") and e.get("target")]
            if n_lines:
                guidance_parts.append("Tên nhân vật (Characters):\n" + "\n".join(n_lines))
        pronouns = book_record.get("pronoun_table") or book_record.get("pronouns") or []
        if pronouns and isinstance(pronouns, list):
            p_lines = [f"- {e.get('pair')}: {e.get('a_to_b')} / {e.get('b_to_a')} ({e.get('basis', '')})" for e in pronouns if isinstance(e, dict) and e.get("pair")]
            if p_lines:
                guidance_parts.append("Đại từ xưng hô (Pronouns):\n" + "\n".join(p_lines))
        if guidance_parts:
            br_guidance = "<book_record_guidance>\n" + "\n\n".join(guidance_parts) + "\n</book_record_guidance>\n\n"

    blocks = []
    for item in batch_items:
        s_id = item["id"]
        s_orig = item.get("original", "")
        s_trans = item.get("translate", "")
        s_issues = item.get("issues", [])
        ctx = item.get("context_before")
        ctx_tag = ""
        if ctx:
            ctx_str = "\n\n".join(c.strip() for c in ctx if c and c.strip())
            if ctx_str:
                ctx_tag = f"<context_before>\n{ctx_str}\n</context_before>\n"
        probs = "; ".join(i.get("problem", "") for i in s_issues if i.get("problem"))
        targets = ", ".join(i.get("span_target", "") for i in s_issues if i.get("span_target"))
        suggs = "; ".join(i.get("suggestion", "") for i in s_issues if i.get("suggestion"))
        issue_block = f"<issues>\nProblem: {probs}\nTarget Span: {targets}\nSuggestion: {suggs}\n</issues>"

        block = (
            f'<segment id="{s_id}">\n'
            f'{ctx_tag}'
            f'<original>\n{s_orig}\n</original>\n'
            f'<target>\n{s_trans}\n</target>\n'
            f'{issue_block}\n'
            f'</segment>'
        )
        blocks.append(block)

    prompt = (
        "You are the Refine Subagent. Revise the following segments to resolve their flagged issues.\n"
        f"{br_guidance}"
        "For each segment, fix strictly the flagged issue and keep all unflagged spans accurate and intact.\n"
        "Return each revised segment in the format:\n"
        '<segment id="S-XXXX">\n'
        '<target>\n'
        '[complete revised translation of segment]\n'
        '</target>\n'
        '</segment>\n\n'
        + "\n\n".join(blocks)
    )
    return prompt


def _restore_math(
    rep: str,
    orig_mmap: Optional[Dict[str, str]] = None,
    trans_mmap: Optional[Dict[str, str]] = None,
    raw_orig: str = "",
    raw_trans: str = "",
) -> str:
    """Restores masked math formulas and strips stray delimiters (REQ-INT-001)."""
    if orig_mmap:
        for ph, val in orig_mmap.items():
            rep = rep.replace(ph, val)
    if trans_mmap:
        rep, _ = unmask_math_formulas(rep, trans_mmap, fallback_source=raw_trans)
    return _strip_stray_math(rep, raw_orig, raw_trans)


def patch_batch_cbc(
    batch_items: List[Dict[str, Any]],
    cbc_bin: str,
    timeout: int = 240,
    source_lang: str = "en",
    model: str = DEFAULT_REFINE_MODEL,
    book_record: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """
    REQ-PERF-001 / AC-PERF-001-1..4:
    Batches multiple segments into a single cbc prompt, parses per-segment responses,
    validates for truncation/omission with fallback to patch_segment_cbc, and applies diff_and_splice.
    """
    prompt = build_batch_refine_prompt(batch_items, source_lang=source_lang, book_record=book_record)
    raw_response = _run_cbc_prompt(prompt, cbc_bin=cbc_bin, timeout=timeout, model=model)

    # Parse segments from response
    parsed: Dict[str, str] = {}
    pattern = re.compile(r'<segment\s+id=["\']?(S-\d+)["\']?\s*>([\s\S]*?)</segment>', re.IGNORECASE)
    for m in pattern.finditer(raw_response):
        sid = m.group(1).upper()
        content = m.group(2)
        tgt_m = re.search(r'<(?:target|translate)>([\s\S]*?)</(?:target|translate)>', content, re.IGNORECASE)
        if tgt_m:
            parsed[sid] = tgt_m.group(1).strip()
        else:
            parsed[sid] = content.strip()

    results: Dict[str, str] = {}
    for item in batch_items:
        s_id = item["id"]
        s_orig = item.get("original", "")
        s_trans = item.get("translate", "")
        raw_orig = item.get("raw_original", s_orig)
        raw_trans = item.get("raw_translate", s_trans)
        orig_mmap = item.get("orig_mmap") or {}
        trans_mmap = item.get("trans_mmap") or {}
        s_issues = item.get("issues", [])
        rep = parsed.get(s_id, "").strip()

        # Validation per AC-PERF-001-2:
        needs_fallback = False
        if not rep:
            needs_fallback = True
        elif len(s_trans.strip()) >= 20 and len(rep) < 0.5 * len(s_trans.strip()):
            needs_fallback = True

        if needs_fallback:
            rep = patch_segment_cbc(
                seg_original=s_orig,
                seg_translate=s_trans,
                issues=s_issues,
                cbc_bin=cbc_bin,
                timeout=timeout,
                source_lang=source_lang,
                model=model,
                context_before=item.get("context_before"),
                book_record=book_record,
            )

        # Restore math immediately before diff_and_splice
        rep = _restore_math(rep, orig_mmap, trans_mmap, raw_orig, raw_trans)

        # Target spans for diff_and_splice against raw_trans
        target_spans = []
        for iss in s_issues:
            st = (iss.get("span_target") or "").strip()
            if st and st in raw_trans:
                target_spans.append(st)
        if not target_spans:
            target_spans = [raw_trans]

        spliced = diff_and_splice(raw_trans, rep, target_spans)
        spliced = _strip_stray_math(spliced, raw_orig, raw_trans)
        if item.get("type") not in ("code", "figure"):
            spliced = ensure_footnote_numbering(raw_orig, spliced)
        results[s_id] = spliced

    return results


def refine_chapter(
    run_dir: Union[str, Path],
    chapter: Union[str, int],
    round_num: int = 2,
    eval_file: Optional[Union[str, Path]] = None,
    draft: Optional[Union[str, Path]] = None,
    output: Optional[Union[str, Path]] = None,
    final: Optional[Union[str, Path]] = None,
    book_record: Optional[Union[str, Path, Dict[str, Any]]] = None,
    offline: bool = False,
    source_lang: str = "auto",
    concurrency: int = 8,
    model: str = DEFAULT_REFINE_MODEL,
    batch_size: int = 8,
    promote_final: bool = True,
    cbc_bin: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Programmatic entry point to refine a chapter.
    Returns (target_xml, final_md).
    """
    run_dir = Path(run_dir).resolve()

    # Format chapter directory
    ch_str = str(chapter).strip()
    if ch_str.isdigit():
        ch_id = f"ch{int(ch_str):02d}"
    elif ch_str.startswith("ch"):
        ch_id = ch_str
    else:
        ch_id = f"ch{ch_str:0>2}"

    ch_dir = run_dir / ch_id

    # Resolve default paths
    if eval_file:
        eval_path = Path(eval_file).resolve()
    else:
        eval_path = ch_dir / f"round{round_num - 1}_evaluate.txt"

    if draft:
        draft_path = Path(draft).resolve()
    else:
        draft_path = ch_dir / f"round{round_num - 1}_translate.txt"

    if output:
        output_path = Path(output).resolve()
    else:
        output_path = ch_dir / f"round{round_num}_translate.txt"

    if final:
        final_path = Path(final).resolve()
    else:
        final_path = ch_dir / "final.txt"

    # Load book record
    book_record_data: Optional[Dict[str, Any]] = None
    if isinstance(book_record, dict):
        book_record_data = book_record
    elif book_record:
        br_path = Path(book_record)
        if br_path.exists():
            try:
                with open(br_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                book_record_data = data.get("book_record", data) if isinstance(data, dict) else None
            except Exception:
                book_record_data = None
    else:
        default_br = run_dir / "book_record.json"
        if default_br.exists():
            try:
                with open(default_br, "r", encoding="utf-8") as f:
                    data = json.load(f)
                book_record_data = data.get("book_record", data) if isinstance(data, dict) else None
            except Exception:
                book_record_data = None

    # Resolve effective source language (AC-ARCH-3.7)
    effective_source_lang = source_lang
    if effective_source_lang == "auto":
        if book_record_data and isinstance(book_record_data, dict) and book_record_data.get("source_lang"):
            effective_source_lang = book_record_data["source_lang"]
        else:
            effective_source_lang = "en"

    # 1. Load evaluation issues
    issues: List[Dict[str, Any]] = []
    if eval_path.exists():
        try:
            content = eval_path.read_text(encoding="utf-8").strip()
            # Try parsing directly as JSON or extracting JSON block
            if content.startswith("{"):
                eval_json = json.loads(content)
            else:
                m = re.search(r"\{.*\}", content, re.DOTALL)
                eval_json = json.loads(m.group(0)) if m else {}
            raw_issues = eval_json.get("issues", [])
            if isinstance(raw_issues, list):
                issues = raw_issues
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to parse eval file {eval_path}: {e}\n")

    # Filter issues by severity: critical and major take precedence
    crit_or_major = [i for i in issues if i.get("severity") in ("critical", "major")]
    active_issues = crit_or_major if crit_or_major else issues

    # 2. Parse bilingual draft
    if not draft_path.exists():
        raise FileNotFoundError(f"Draft file not found: {draft_path}")

    draft_text = draft_path.read_text(encoding="utf-8")
    segments = parse_segments(draft_text)
    if not segments:
        raise ValueError(f"Draft file contains no XML segments: {draft_path}")

    # Initialize cache manager and migrate legacy positional keys (REQ-TXN-001, REQ-TXN-002)
    cache_path = ch_dir / "translation_cache.json"
    cache_mgr = TranslationCacheManager(cache_path)
    cache_mgr.migrate_positional_keys(segments)
    repaired_seg_ids: Set[str] = set()

    # Project book record for chapter
    all_source_text = "\n\n".join(s.get("original", "") for s in segments)
    projected_book_record = project_book_record_for_chapter(book_record_data or {}, all_source_text) if book_record_data else {}

    # Build context lookup
    translatable_segments = [s for s in segments if s.get("type") not in ("code", "figure") and not s.get("is_context")]
    seg_idx_map = {s["id"]: i for i, s in enumerate(translatable_segments)}

    def get_context_before(s_id: str) -> List[str]:
        idx = seg_idx_map.get(s_id)
        if idx is not None and idx > 0:
            start_i = max(0, idx - 2)
            return [translatable_segments[i].get("translate", "") for i in range(start_i, idx) if translatable_segments[i].get("translate")]
        return []

    # 3. Map issues to segments
    seg_to_issues = map_issues_to_segments(active_issues, segments)

    # Find cbc binary if not offline
    cbc_bin = None if offline else (cbc_bin or find_cbc_binary())

    patched_count = 0
    addressed_issue_ids = set()

    # 4. Repair flagged segments
    flagged_translatable = [
        s for s in segments
        if s["type"] not in ("code", "figure") and s["id"] in seg_to_issues
    ]

    repaired_by_id: Dict[str, str] = {}

    can_batch = (
        not offline
        and cbc_bin is not None
        and batch_size > 1
        and patch_segment_cbc is _DEFAULT_PATCH_SEGMENT_CBC
        and len(flagged_translatable) > 0
    )

    if can_batch:
        for b_idx in range(0, len(flagged_translatable), batch_size):
            chunk = flagged_translatable[b_idx : b_idx + batch_size]
            b_items = []
            for s in chunk:
                s_orig = s.get("original", "")
                s_trans = s.get("translate", s_orig)
                masked_orig, orig_mmap = mask_math_formulas(s_orig, prefix_tag="ORIG_MATH")
                masked_trans, trans_mmap = mask_math_formulas(s_trans, prefix_tag="MATH")
                b_items.append({
                    "id": s["id"],
                    "original": masked_orig,
                    "translate": masked_trans,
                    "orig_mmap": orig_mmap,
                    "trans_mmap": trans_mmap,
                    "raw_original": s_orig,
                    "raw_translate": s_trans,
                    "type": s.get("type", "paragraph"),
                    "issues": seg_to_issues[s["id"]],
                    "context_before": get_context_before(s["id"]),
                })
            b_results = patch_batch_cbc(
                batch_items=b_items,
                cbc_bin=cbc_bin,
                source_lang=effective_source_lang,
                model=model,
                book_record=projected_book_record,
            )
            for sid, rep_text in b_results.items():
                repaired_by_id[sid] = rep_text
    else:
        def _compute_repaired(s_item: Dict[str, Any]) -> Tuple[str, str]:
            s_id = s_item["id"]
            s_orig = s_item.get("original", "")
            s_trans = s_item.get("translate", s_orig)
            s_type = s_item.get("type", "paragraph")
            s_issues = seg_to_issues[s_id]

            # Mask math formulas before refinement (REQ-009)
            masked_orig, orig_mmap = mask_math_formulas(s_orig, prefix_tag="ORIG_MATH")
            masked_trans, trans_mmap = mask_math_formulas(s_trans, prefix_tag="MATH")

            seg_timeout = max(180, int(len(s_orig) * 0.3))
            if not offline and cbc_bin:
                try:
                    rep = patch_segment_cbc(
                        seg_original=masked_orig,
                        seg_translate=masked_trans,
                        issues=s_issues,
                        cbc_bin=cbc_bin,
                        timeout=seg_timeout,
                        source_lang=effective_source_lang,
                        model=model,
                        context_before=get_context_before(s_id),
                        book_record=projected_book_record,
                    )
                except Exception as e:
                    sys.stderr.write(f"Warning: cbc patch failed on segment {s_id}: {e}. Using offline patch.\n")
                    rep = patch_segment_offline(
                        seg_original=masked_orig,
                        seg_translate=masked_trans,
                        issues=s_issues,
                        book_record=book_record_data,
                    )
            else:
                rep_cached = cache_mgr.get_valid(s_id, s_orig)
                if rep_cached and rep_cached != s_trans:
                    rep = rep_cached
                else:
                    rep = patch_segment_offline(
                        seg_original=masked_orig,
                        seg_translate=masked_trans,
                        issues=s_issues,
                        book_record=book_record_data,
                    )

            # Unmask math formulas after refinement
            if orig_mmap:
                for ph, val in orig_mmap.items():
                    rep = rep.replace(ph, val)
            if trans_mmap:
                rep, _ = unmask_math_formulas(rep, trans_mmap, fallback_source=s_trans)
            rep = _strip_stray_math(rep, s_orig, s_trans)

            # Repair table structure if needed (REQ-007)
            if s_type == "table":
                rep, _ = validate_and_repair_gfm_table(s_orig, rep)

            if s_type not in ("code", "figure"):
                rep = ensure_footnote_numbering(s_orig, rep)

            return s_id, rep

        effective_workers = max(1, concurrency)
        if effective_workers > 1 and len(flagged_translatable) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=effective_workers) as executor:
                for s_id, rep_text in executor.map(_compute_repaired, flagged_translatable):
                    repaired_by_id[s_id] = rep_text
        else:
            for s_item in flagged_translatable:
                s_id, rep_text = _compute_repaired(s_item)
                repaired_by_id[s_id] = rep_text

    for s in segments:
        seg_id = s["id"]
        seg_type = s["type"]
        seg_orig = s.get("original", "")
        seg_trans = s.get("translate", seg_orig)

        # Enforce invariance for non-translatable segments
        if seg_type in ("code", "figure"):
            s["translate"] = seg_orig
            continue

        if seg_id in seg_to_issues:
            seg_issues = seg_to_issues[seg_id]
            repaired_text = repaired_by_id.get(seg_id, seg_trans)

            # Collect flagged spans for diff_and_splice byte invariance
            target_spans = []
            for iss in seg_issues:
                st = (iss.get("span_target") or "").strip()
                if st and st in seg_trans:
                    target_spans.append(st)
                ss = (iss.get("span_source") or "").strip()
                if ss and ss in seg_trans:
                    target_spans.append(ss)
                prob = iss.get("problem", "")
                m = re.search(r"Canonical term ['\"](.*?)['\"]", prob)
                if m:
                    term = m.group(1).strip()
                    if term and term in seg_trans:
                        target_spans.append(term)
                    if book_record_data:
                        terms_list = book_record_data.get("terms", [])
                        for t in terms_list:
                            if isinstance(t, dict) and t.get("source", "").lower() == term.lower():
                                tgt = t.get("target", "")
                                base = tgt.split("(")[0].strip()
                                if base and base in seg_trans:
                                    target_spans.append(base)

            target_spans = list(set(target_spans))
            if not target_spans:
                target_spans = [seg_trans]

            # Enforce NFR-6 byte invariance: IMP-P0-01 (AC-ARCH-7.1)
            spliced_trans = diff_and_splice(seg_trans, repaired_text, target_spans)
            if seg_type == "table":
                spliced_trans, _ = validate_and_repair_gfm_table(seg_orig, spliced_trans)
            s["translate"] = spliced_trans
            if spliced_trans != seg_trans:
                patched_count += 1
                repaired_seg_ids.add(seg_id)
                for iss in seg_issues:
                    if "id" in iss:
                        st = (iss.get("span_target") or iss.get("span") or "").strip()
                        if not st or st not in spliced_trans:
                            addressed_issue_ids.add(iss["id"])

    # 4.5. Repair cross-boundary joint segment pairs (REQ-010, REQ-QUAL-002, AC-QUAL-002-1)
    seg_dict = {s["id"]: s for s in segments if "id" in s}
    joint_pairs = [k for k in seg_to_issues if isinstance(k, tuple) and len(k) == 2]

    for id_a, id_b in joint_pairs:
        s_a = seg_dict.get(id_a)
        s_b = seg_dict.get(id_b)
        if not s_a or not s_b:
            continue
        p_issues = seg_to_issues[(id_a, id_b)]
        orig_a = s_a.get("original", "")
        orig_b = s_b.get("original", "")
        trans_a = s_a.get("translate", "")
        trans_b = s_b.get("translate", "")
        joint_orig = f"{orig_a}\n\n{orig_b}"
        joint_trans = f"{trans_a}\n\n{trans_b}"

        # Mask math formulas separately on original and translate
        masked_orig, joint_orig_mmap = mask_math_formulas(joint_orig, prefix_tag="ORIG_MATH")
        masked_trans, joint_trans_mmap = mask_math_formulas(joint_trans, prefix_tag="MATH")

        if not offline and cbc_bin:
            try:
                rep_joint = patch_segment_cbc(
                    seg_original=masked_orig,
                    seg_translate=masked_trans,
                    issues=p_issues,
                    cbc_bin=cbc_bin,
                    source_lang=effective_source_lang,
                    model=model,
                    context_before=get_context_before(id_a),
                    book_record=projected_book_record,
                )
            except Exception as e:
                rep_joint = patch_segment_offline(
                    seg_original=masked_orig,
                    seg_translate=masked_trans,
                    issues=p_issues,
                    book_record=book_record_data,
                )
        else:
            rep_joint = patch_segment_offline(
                seg_original=masked_orig,
                seg_translate=masked_trans,
                issues=p_issues,
                book_record=book_record_data,
            )

        if joint_orig_mmap:
            for ph, val in joint_orig_mmap.items():
                rep_joint = rep_joint.replace(ph, val)
        if joint_trans_mmap:
            rep_joint, _ = unmask_math_formulas(rep_joint, joint_trans_mmap, fallback_source=joint_trans)
        rep_joint = _strip_stray_math(rep_joint, joint_orig, joint_trans)

        target_spans = []
        for iss in p_issues:
            st = (iss.get("span_target") or "").strip()
            if st and st in joint_trans:
                target_spans.append(st)
        if not target_spans:
            target_spans = [joint_trans]

        res_a, res_b = diff_and_splice_joint(trans_a, trans_b, rep_joint, target_spans)
        s_a["translate"] = res_a
        s_b["translate"] = res_b
        patched_count += 2
        repaired_seg_ids.update([id_a, id_b])
        for iss in p_issues:
            if "id" in iss:
                addressed_issue_ids.add(iss["id"])

    # 4.6. Sanitize Chinese prose translations (REQ-004)
    if effective_source_lang == "zh":
        from scripts.evaluators.anti_convert import sanitize_chinese_translation
        for s in segments:
            if s.get("type") not in ("code", "figure") and s.get("translate"):
                prev = s["translate"]
                s["translate"], _ = sanitize_chinese_translation(
                    s["translate"], source_lang="zh", book_record=book_record_data
                )
                if s["translate"] != prev:
                    repaired_seg_ids.add(s["id"])

    for s in segments:
        if s.get("type") not in ("code", "figure") and s.get("translate"):
            prev = s["translate"]
            s["translate"] = ensure_footnote_numbering(s.get("original", ""), s["translate"])
            if s["translate"] != prev:
                repaired_seg_ids.add(s["id"])

    # Synchronize cache, provenance, and audit trail (REQ-TXN-002 / AC-TXN-002-1)
    agent_id = os.environ.get("AGENT_TRANSLATOR_AGENT_ID", "primary-agent")
    for s in segments:
        sid = s["id"]
        if sid in repaired_seg_ids:
            cache_mgr.set(
                sid,
                s["translate"],
                provenance={
                    "agent": agent_id,
                    "engine": "agent-refine",
                    "model": model or DEFAULT_REFINE_MODEL,
                    "trunk_id": f"trunk-{ch_dir.name}-r{round_num}-refine",
                },
            )
            cache_mgr.set(s["original"], s["translate"])
    cache_mgr.save()

    audit_rec = {
        "trunk_id": f"trunk-{ch_dir.name}-r{round_num}-refine",
        "chapter": ch_id,
        "round": round_num,
        "phase": "refine",
        "agent": agent_id,
        "model": model or DEFAULT_REFINE_MODEL,
        "engine": "agent-refine",
        "repaired_segments": sorted(repaired_seg_ids),
        "patched_count": len(repaired_seg_ids),
    }
    audit_file = ch_dir / "audit_trail.jsonl"
    with open(audit_file, "a", encoding="utf-8") as af:
        af.write(json.dumps(audit_rec, ensure_ascii=False) + "\n")

    # 5. Assemble complete XML draft
    assembled_segments = []
    for s in segments:
        seg_id = s["id"]
        seg_type = s["type"]
        seg_orig = s["original"]
        seg_trans = s.get("translate", seg_orig)
        prov = s.get("provenance")
        prov_attrs = ""
        if prov and isinstance(prov, dict):
            for k in ("agent", "model", "engine", "trunk_id", "started_at", "finished_at", "completed_at", "duration_ms"):
                if k in prov:
                    prov_attrs += f' {k}="{prov[k]}"'
        assembled_segments.append(
            f'<segment id="{seg_id}" type="{seg_type}"{prov_attrs}>\n'
            f'<original>\n{seg_orig}\n</original>\n'
            f'<translate>\n{seg_trans}\n</translate>\n'
            f'</segment>'
        )
    target_xml = "\n\n".join(assembled_segments) + "\n"

    # 6. Validate segment parity against source.txt
    source_path = ch_dir / "source.txt"
    if source_path.exists():
        source_xml = source_path.read_text(encoding="utf-8")
        is_valid, parity_errors = validate_segment_parity(source_xml, target_xml)
        if not is_valid:
            raise RuntimeError(f"Segment parity validation failed after refinement: {parity_errors}")

    # 7. Strip segments to clean markdown
    final_md = strip_segments_to_final(target_xml)

    # Additionally derive addressed issues across whole text via helper.derive_addressed_issues (IMP-P1-02, NFR-8)
    pre_refine_md = strip_segments_to_final(draft_text)
    derived_addressed, _ = derive_addressed_issues(pre_refine_md, final_md, active_issues)
    for d_iss in derived_addressed:
        if "id" in d_iss:
            addressed_issue_ids.add(d_iss["id"])

    # 8. Save outputs directly to disk atomically (REQ-TXN-004)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output_path, target_xml)

    # Persist round<R>_refine.txt for explicit artifact auditability (AC-ARCH-7.1, RULE[translation-governance])
    refine_audit_path = ch_dir / f"round{round_num}_refine.txt"
    atomic_write_text(refine_audit_path, target_xml)

    if promote_final:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(final_path, final_md)

    # 9. Print summary
    print(f"[REFINE] Chapter {ch_id} (Round {round_num}): Patched {patched_count} segment(s), addressed {len(addressed_issue_ids)} issue(s).")
    print(f"  Target Draft: {output_path}")
    print(f"  Final Markdown: {final_path}")

    return target_xml, final_md


def parse_args(args: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Targeted Translation Refinement Subagent Runner")
    parser.add_argument("--run-dir", required=True, help="Path to book run directory (e.g. runs/<slug>)")
    parser.add_argument("--chapter", required=True, help="Chapter ID (e.g. ch02, 2)")
    parser.add_argument("--round", dest="round_num", type=int, default=2, help="Current round number (default: 2)")
    parser.add_argument("--eval-file", default=None, help="Path to evaluation report (defaults to round<R-1>_evaluate.txt)")
    parser.add_argument("--draft", default=None, help="Path to input draft (defaults to round<R-1>_translate.txt)")
    parser.add_argument("--output", default=None, help="Path to output draft (defaults to round<R>_translate.txt)")
    parser.add_argument("--final", default=None, help="Path to output final markdown (defaults to final.txt)")
    parser.add_argument("--book-record", default=None, help="Path to book_record.json")
    parser.add_argument("--offline", action="store_true", help="Use local rule-based patching without calling cbc")
    parser.add_argument(
        "--source-lang",
        default="auto",
        choices=["auto", "en", "zh"],
        help="Source language ('auto', 'en', 'zh', default: auto)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="Number of concurrent refinement workers (default: 8)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_REFINE_MODEL,
        help=f"Model identifier to pass to cbc (default: {DEFAULT_REFINE_MODEL})",
    )
    return parser.parse_args(args)


def main():
    args = parse_args()
    refine_chapter(
        run_dir=args.run_dir,
        chapter=args.chapter,
        round_num=args.round_num,
        eval_file=args.eval_file,
        draft=args.draft,
        output=args.output,
        final=args.final,
        book_record=args.book_record,
        offline=args.offline,
        source_lang=args.source_lang,
        concurrency=args.concurrency,
        model=args.model,
    )


if __name__ == "__main__":
    main()
