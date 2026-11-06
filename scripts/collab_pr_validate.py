"""
scripts/collab_pr_validate.py — Translation PR validation, review validation, and prompt injection detection.

Implements REQ-COLLAB-004, REQ-COLLAB-005, REQ-COLLAB-009:
- Parse and validate PR body templates
- Detect prompt injection attack patterns
- Validate branch naming, file allowlists, and segment XML parity
- Validate independent reviews (author != reviewer, line comments, 6-dimension rubric)
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from scripts.helper import parse_segments, validate_segment_parity

INJECTION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)\bignore\s+(?:previous|all|prior)\s+instructions\b"), "ignore previous instructions"),
    (re.compile(r"(?i)\bdisregard\s+(?:previous|all|prior)?\s*instructions\b"), "disregard instructions"),
    (re.compile(r"(?im)(?:^\s*system\s*:|\bsystem\s*:\s*(?:you\s+are|ignore|disregard|score|override|act\s+as|prompt|instruction))"), "system: role prefix"),
    (re.compile(r"(?i)\bscore\s+5\.0\s+on\s+all\s+dimensions\b"), "score 5.0 on all dimensions"),
    (re.compile(r"(?i)\byou\s+are\s+now\s+a\b"), "you are now a persona override"),
    (re.compile(r"</translate>[\s\S]*?<translate>"), "translate tag breakout / injection"),
    (re.compile(r"(?i)<\s*system\b[^>]*>"), "<system> tag injection"),
]

REQUIRED_BODY_FIELDS: Tuple[str, ...] = (
    "agent",
    "engine_model",
    "source_language",
    "target_language",
    "chunk_id",
    "issue",
    "glossary_sha",
)

REVIEW_DIMENSIONS: Tuple[str, ...] = (
    "fidelity",
    "fluency",
    "terminology",
    "register",
    "structure",
    "completeness",
)


def detect_prompt_injection(text: str) -> List[str]:
    """Scans text for known prompt injection and jailbreak patterns.

    Returns a list of human-readable injection flag descriptions.
    """
    flags: List[str] = []
    if not text:
        return flags

    for pattern, label in INJECTION_PATTERNS:
        if pattern.search(text):
            flags.append(f"Prompt injection detected: '{label}'")

    return flags


def parse_pr_body(body: str) -> Dict[str, str]:
    """Parses markdown key-value pairs from a PR body.

    Matches lines like `- **Key:** Value` and normalizes keys to snake_case.
    """
    fields: Dict[str, str] = {}
    if not body:
        return fields

    line_pattern = re.compile(r"^\s*[-*]\s*\*\*([^*:]+)(?::\*\*|\*\*:?)\s*(.*)$")
    for line in body.splitlines():
        m = line_pattern.match(line)
        if m:
            raw_key = m.group(1).strip().lower()
            val = m.group(2).strip()
            # Normalize key: replace non-alphanumeric with underscore
            norm_key = re.sub(r"[^a-z0-9]+", "_", raw_key).strip("_")
            fields[norm_key] = val

    return fields


def validate_translation_pr(
    head_ref: str,
    author_login: str,
    body: str,
    changed_files: List[str],
    source_xml_text: str,
    translation_xml_text: str,
    expected_glossary_sha: Optional[str] = None,
    additions_data: Optional[Any] = None,
) -> Dict[str, Any]:
    """Validates a translation PR per REQ-COLLAB-004, 005, and 009.

    Returns:
        {
            "valid": bool,
            "defects": list[str],
            "injection_flags": list[str],
            "parsed_body": dict[str, str]
        }
    """
    defects: List[str] = []
    injection_flags: List[str] = []

    # 1. Prompt Injection Checks on PR body
    body_flags = detect_prompt_injection(body)
    if body_flags:
        injection_flags.extend(body_flags)
        defects.extend(body_flags)

    # 2. Branch Naming: translate/<slug>-<ch>-<agent>
    branch_pattern = re.compile(r"^translate/([a-z0-9-]+)-(ch\d+)-([A-Za-z0-9-]+)$")
    m = branch_pattern.match(head_ref)
    book_chunk = ""
    if m:
        branch_slug, branch_ch, branch_agent = m.group(1), m.group(2), m.group(3)
        book_chunk = f"{branch_slug}-{branch_ch}"
        if branch_agent != author_login:
            defects.append(
                f"Branch agent '{branch_agent}' does not match PR author '{author_login}'"
            )
    else:
        defects.append(
            f"Branch name does not follow 'translate/<slug>-<ch>-<agent>' format: '{head_ref}'"
        )
        if head_ref.startswith("translate/"):
            remainder = head_ref[len("translate/"):]
            if remainder.endswith(f"-{author_login}"):
                branch_agent = author_login
            else:
                last_hyphen = remainder.rfind("-")
                branch_agent = remainder[last_hyphen + 1:] if last_hyphen != -1 else remainder
            if branch_agent != author_login:
                defects.append(
                    f"Branch agent '{branch_agent}' does not match PR author '{author_login}'"
                )

    # 3. File Allowlist and Single-Chapter Diff Constraint (REQ-COLLAB-004, REQ-COLLAB-015)
    # Only runs/<slug>/<ch>/translation.xml and optional runs/<slug>/<ch>/analyze_additions.json
    has_translation_xml = False
    changed_chapters = set()
    for f in changed_files:
        f_clean = f.replace("\\", "/")
        parts = f_clean.split("/")
        if len(parts) == 4 and parts[0] == "runs":
            file_slug = parts[1]
            file_ch = parts[2]
            changed_chapters.add((file_slug, file_ch))
            if parts[3] == "translation.xml":
                has_translation_xml = True
            elif parts[3] == "analyze_additions.json":
                pass
            else:
                defects.append(f"Disallowed file touched in chapter folder: {f}")
        else:
            defects.append(f"Disallowed file touched outside chapter allowlist: {f}")

    if len(changed_chapters) > 1:
        defects.append(
            f"PR violates single-chapter diff constraint: changed files span multiple chapters {sorted(changed_chapters)}"
        )

    if not has_translation_xml:
        defects.append("Missing required translation file: runs/<slug>/<ch>/translation.xml")

    # 4. PR Body Fields Validation
    parsed_body = parse_pr_body(body)
    body_agent = parsed_body.get("agent")
    if not body_agent:
        defects.append("Missing required PR body field: 'Agent'")
    elif body_agent != author_login:
        defects.append(
            f"PR body Agent '{body_agent}' does not match PR author '{author_login}'"
        )

    for req_field in REQUIRED_BODY_FIELDS:
        val = parsed_body.get(req_field)
        if not val:
            defects.append(f"Missing or empty required PR body field: '{req_field}'")

    body_sha = parsed_body.get("glossary_sha")
    if expected_glossary_sha and body_sha != expected_glossary_sha:
        defects.append(
            f"Glossary SHA mismatch: PR has '{body_sha}', expected '{expected_glossary_sha}'"
        )

    # Cross-validate single chapter against branch head_ref and PR body chunk_id
    if len(changed_chapters) == 1:
        ch_slug, ch_name = next(iter(changed_chapters))
        expected_chunk_full = f"{ch_slug}-{ch_name}"
        if book_chunk and book_chunk != expected_chunk_full:
            defects.append(
                f"Branch chunk '{book_chunk}' does not match changed files chapter '{expected_chunk_full}'"
            )
        body_chunk = parsed_body.get("chunk_id", "").strip()
        if body_chunk and body_chunk not in (ch_name, expected_chunk_full):
            defects.append(
                f"PR body chunk_id '{body_chunk}' does not match changed files chapter '{expected_chunk_full}'"
            )

    # 5. XML Segment Parity, Content Existence and Byte-Invariance
    if not source_xml_text or not source_xml_text.strip():
        defects.append("source_xml_text is empty or contains only whitespace")
    if not translation_xml_text or not translation_xml_text.strip():
        defects.append("translation_xml_text is empty or contains only whitespace")

    if source_xml_text and source_xml_text.strip() and translation_xml_text and translation_xml_text.strip():
        source_segs = parse_segments(source_xml_text)
        trans_segs = parse_segments(translation_xml_text)

        if len(source_segs) == 0:
            defects.append("source_xml_text contains 0 parsed segments")
        if len(trans_segs) == 0:
            defects.append("translation_xml_text contains 0 parsed segments")

        if len(source_segs) > 0 and len(trans_segs) > 0:
            is_parity_valid, parity_errors = validate_segment_parity(source_xml_text, translation_xml_text)
            if not is_parity_valid:
                defects.extend(parity_errors)

            # Verify <original> byte-identity across all segments
            orig_pattern = re.compile(r"<original>([\s\S]*?)</original>")
            src_raw_originals = orig_pattern.findall(source_xml_text)
            trans_raw_originals = orig_pattern.findall(translation_xml_text)

            for i, (s_seg, t_seg) in enumerate(zip(source_segs, trans_segs)):
                seg_id = s_seg.get("id")
                violated = False
                if s_seg.get("original") != t_seg.get("original"):
                    violated = True
                elif i < len(src_raw_originals) and i < len(trans_raw_originals):
                    if src_raw_originals[i] != trans_raw_originals[i]:
                        violated = True

                if violated:
                    defects.append(
                        f"Byte invariance violation in <original> text for segment {seg_id}"
                    )
                # Check prompt injection in individual segment translated text
                trans_text = t_seg.get("translate") or ""
                seg_inj = detect_prompt_injection(trans_text)
                if seg_inj:
                    injection_flags.extend(seg_inj)
                    defects.extend([f"Segment {t_seg.get('id')}: {inj}" for inj in seg_inj])

    # Chinese Hanviet reading check
    src_lang = parsed_body.get("source_language")
    if src_lang == "zh":
        # Ensure every segment in translation_xml has a non-empty <hanviet> block
        seg_pattern = re.compile(r'<segment\b[^>]*id="([^"]+)"[^>]*>(.*?)</segment>', re.DOTALL)
        hanviet_pattern = re.compile(r'<hanviet>(.*?)</hanviet>', re.DOTALL)

        for m in seg_pattern.finditer(translation_xml_text):
            seg_id = m.group(1)
            body_content = m.group(2)
            hv_m = hanviet_pattern.search(body_content)
            if not hv_m or not hv_m.group(1).strip():
                defects.append(
                    f"Segment {seg_id} missing required non-empty <hanviet> block for zh translation"
                )

    # 6. Proposed Additions Validation
    if additions_data is not None:
        proposals = additions_data if isinstance(additions_data, list) else additions_data.get("proposals", [])
        for item in proposals:
            if isinstance(item, dict):
                st = item.get("status")
                if st != "proposed":
                    defects.append(
                        f"Analyze addition proposal status must be 'proposed', got '{st}'"
                    )

    return {
        "valid": len(defects) == 0,
        "defects": defects,
        "injection_flags": injection_flags,
        "parsed_body": parsed_body,
    }


def validate_review(
    pr_author_login: str,
    review_author_login: str,
    review_body: str,
    line_comment_count: int,
) -> Dict[str, Any]:
    """Validates an independent review per REQ-COLLAB-005.

    Returns:
        {
            "valid": bool,
            "defects": list[str],
            "scores": dict[str, float],
            "injection_flags": list[str]
        }
    """
    defects: List[str] = []
    scores: Dict[str, float] = {}

    # 1. Self-Review Invariant
    if pr_author_login and review_author_login and pr_author_login == review_author_login:
        defects.append(
            f"Review author '{review_author_login}' cannot review their own PR (author: '{pr_author_login}')"
        )

    # 2. Line Comments Requirement
    if line_comment_count < 1:
        defects.append("Review must include at least 1 inline line comment on specific segments")

    # 3. Prompt Injection Check on Review Body
    injection_flags = detect_prompt_injection(review_body)
    if injection_flags:
        defects.extend(injection_flags)

    # 4. Parse 6-Dimension Score Block
    score_pattern = re.compile(
        r"(?i)\b(fidelity|fluency|terminology|register|structure|completeness)\s*:\*{0,2}\s*([0-9]+(?:\.[0-9]+)?)"
    )
    matches = score_pattern.findall(review_body)
    for dim_name, score_str in matches:
        norm_dim = dim_name.lower()
        try:
            score_val = float(score_str)
            scores[norm_dim] = score_val
            if not (1.0 <= score_val <= 5.0):
                defects.append(f"Score for '{norm_dim}' ({score_val}) is outside [1.0, 5.0]")
        except ValueError:
            defects.append(f"Invalid numeric score for '{norm_dim}': '{score_str}'")

    for dim in REVIEW_DIMENSIONS:
        if dim not in scores:
            defects.append(f"Missing required rubric score dimension: '{dim}'")

    return {
        "valid": len(defects) == 0,
        "defects": defects,
        "scores": scores,
        "injection_flags": injection_flags,
    }
