"""
scripts/collab_pr_validate.py — Translation PR validation, peer review validation,
canonical proposal schema validation, and 4-class pre-LLM prompt injection detection.

Implements REQ-021, REQ-022, REQ-023, and REQ-025:
- Parse and cross-check PR body fields against branch, manifest, issue, glossary_sha, and source.txt
- Enforce file allowlist and byte/segment caps (5 MiB / 2,000 segments translation.xml, 512 KiB analyze_additions.json)
- Enforce XML segment parity, <original> byte-identity, zh <hanviet>, and <translate> code/image byte-invariance
- Validate canonical analyze_additions.json {"proposals": [...]} schema
- Detect 4-class prompt injection payloads across <original>, <hanviet>, <translate>, PR body, proposals, and reviews
- Validate independent peer reviews (reviewer != author, inline or S-NNNN comment, 6-dimension [1.0, 5.0] rubric)
"""

import json
import re
from typing import Any, Collection, Dict, List, Optional, Tuple, Union

from scripts.helper import parse_segments, validate_segment_parity

MAX_TRANSLATION_XML_BYTES: int = 5_242_880
MAX_TRANSLATION_SEGMENTS: int = 2_000
MAX_ADDITIONS_JSON_BYTES: int = 524_288

SLUG_RE: re.Pattern = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CHAPTER_RE: re.Pattern = re.compile(r"^ch\d{2,}$")
LOGIN_RE: re.Pattern = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")

TRANSLATE_BRANCH_RE: re.Pattern = re.compile(
    r"^translate/(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)-(?P<ch>ch\d{2,})-(?P<login>[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)$"
)
SLUG_CH_PREFIX_RE: re.Pattern = re.compile(
    r"^(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)-(?P<ch>ch\d{2,})$"
)
SUBCHAPTER_BRANCH_TOKEN_RE: re.Pattern = re.compile(
    r"-ch\d{2,}-(?:p|part)\d+(?:-|$)",
    re.IGNORECASE,
)

SEGMENTS_RANGE_RE: re.Pattern = re.compile(r"^S-\d{4}(?:\.\.S-\d{4})?$")
ISSUE_REF_RE: re.Pattern = re.compile(r"^#([1-9]\d*)$")
GLOSSARY_SHA_RE: re.Pattern = re.compile(r"^(?:[0-9a-f]{64}|[0-9a-f]{40})$")

FENCED_CODE_RE: re.Pattern = re.compile(r"(`{3,})[\s\S]*?\1")
IMAGE_ANCHOR_RE: re.Pattern = re.compile(
    r"!\[(?:[^\[\]\\]|\\.)*\]\((?:[^()\\]|\\.|\([^()]*\))*\)"
)
SEGMENT_BLOCK_RE: re.Pattern = re.compile(
    r'<segment\b([^>]*)>([\s\S]*?)</segment>'
)
ORIG_TAG_RE: re.Pattern = re.compile(r"<original>([\s\S]*?)</original>")
TRANS_TAG_RE: re.Pattern = re.compile(r"<translate>([\s\S]*?)</translate>")
HANVIET_TAG_RE: re.Pattern = re.compile(r"<hanviet>([\s\S]*?)</hanviet>")

SEGMENT_REF_RE: re.Pattern = re.compile(r"\bS-\d{4}\b")
REVIEW_SCORE_SPEC_RE: re.Pattern = re.compile(
    r"(?i)\b(Fidelity|Fluency|Terminology|Register|Structure|Completeness)\b[^0-9\n]*([1-5](?:\.\d+)?)(?:\s*/\s*5(?:\.0)?)?"
)
RAW_REVIEW_SCORE_RE: re.Pattern = re.compile(
    r"(?i)\b(Fidelity|Fluency|Terminology|Register|Structure|Completeness)\b([^0-9\n]*?)(-\d+(?:\.\d+)?|\d+(?:\.\d+)?)(?:\s*/\s*(\d+(?:\.\d+)?))?(?![0-9.])"
)
RUBRIC_FIVE_SPAN_RE: re.Pattern = re.compile(
    r"(?i)\b(?:fidelity|fluency|terminology|register|structure|completeness)\b[\s*_:|-]{0,12}5(?:\.0+)?\s*/\s*5(?:\.0+)?(?!(?:\.\d|[0-9/A-Za-z]))"
)
FIVE_OUT_OF_FIVE_RE: re.Pattern = re.compile(
    r"(?<![0-9/])(?<!\d\.)5(?:\.0+)?\s*/\s*5(?:\.0+)?(?!(?:\.\d|[0-9/A-Za-z]))"
)

# 4-class pre-LLM prompt-injection patterns (REQ-023)
INJECTION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # Class 1: Instruction overrides
    (
        re.compile(
            r"(?i)\b(?:ignore|disregard|forget|override|bypass)\s+"
            r"(?:(?:all|any|the|your|previous|prior|above|earlier|preceding|system|developer|original|initial|safety)\s+)*"
            r"(?:instructions?|prompts?|rules?|constraints?|directives?|guidelines?|filters?)\b"
        ),
        "instruction override directive",
    ),
    (re.compile(r"(?i)\byou\s+are\s+now\s+a\b"), "you are now a persona override"),
    # Class 2: Role-tag spoofing
    (re.compile(r"(?i)<\s*/?\s*system\b[^>]*>"), "<system> role tag spoofing"),
    (re.compile(r"(?i)\bsystem\s+prompt\s*:"), "system prompt: role spoofing"),
    (
        re.compile(
            r"(?im)(?:^\s*system\s*:|\bsystem\s*:\s*"
            r"(?:you\s+are|ignore|disregard|forget|score|override|act\s+as|prompt|instruction|approve|verdict|assign|give))"
        ),
        "system: role prefix",
    ),
    (re.compile(r"(?i)\[\s*/?\s*inst\s*\]"), "[INST] role tag spoofing"),
    (
        re.compile(r"(?i)(?:<\|im_start\|>\s*(?:system|developer|user|assistant)?|<\|im_end\|>|<\|system\|>)"),
        "<|im_start|>system role tag spoofing",
    ),
    # Class 3: Score / verdict forcing
    (
        re.compile(r"(?i)\bverdict\s*[:=]\s*[\"']?(?:pass|approved?|candidate)\b"),
        "verdict: pass forcing",
    ),
    (
        re.compile(r"(?i)\b(?:overall[_\s]+|total[_\s]+|final[_\s]+)?score\s*[:=]\s*5(?:\.0+)?\b"),
        "score: 5 forcing",
    ),
    (
        re.compile(r"(?i)\bscore\s+5(?:\.0+)?\s+on\s+all\s+dimensions\b"),
        "score 5.0 on all dimensions",
    ),
    (
        re.compile(
            r"(?i)\b(?:assign|give|award|grant|set|force|output|return)\s+"
            r"(?:(?:a|an|the|this|full|perfect|maximum|max|highest|top)\s+)*"
            r"(?:maximum|max|perfect|full|highest|top)\s+(?:score|marks?|rating|grade|points?)\b"
        ),
        "assign maximum score forcing",
    ),
    # Class 4: Delimiter escapes
    (
        re.compile(r"(?i)<\s*/?\s*(?:target_artifact|governing_specification|prior_review_context)\b[^>]*>"),
        "prompt delimiter escape tag",
    ),
    (
        re.compile(r"(?i)</translate>[\s\S]*?<translate>"),
        "translate tag breakout / injection",
    ),
]

REQUIRED_BODY_FIELDS: Tuple[str, ...] = (
    "agent",
    "engine_model",
    "source_language",
    "target_language",
    "book",
    "chunk_id",
    "issue",
    "glossary_sha",
    "segments",
)

REVIEW_DIMENSIONS: Tuple[str, ...] = (
    "fidelity",
    "fluency",
    "terminology",
    "register",
    "structure",
    "completeness",
)

CANONICAL_PROPOSAL_KEYS = frozenset({"source", "target", "status", "category", "note"})


def detect_prompt_injection(text: str, *, allow_rubric_lines: bool = True) -> List[str]:
    """Scans text case-insensitively across all 4 REQ-023 prompt-injection classes.

    Classes:
      1. Instruction overrides (ignore/disregard/forget previous/all/system instructions)
      2. Role-tag spoofing (<system>, </system>, system prompt:, [inst], <|im_start|>system)
      3. Score/verdict forcing (5/5, 5.0/5, score: 5, verdict: pass, assign maximum score)
      4. Delimiter escapes (</target_artifact>, </governing_specification>, </prior_review_context>)
    """
    flags: List[str] = []
    if not text:
        return flags

    for pattern, label in INJECTION_PATTERNS:
        if pattern.search(text):
            flags.append(f"Prompt injection detected: '{label}'")

    scan_for_five = RUBRIC_FIVE_SPAN_RE.sub("", text) if allow_rubric_lines else text
    if FIVE_OUT_OF_FIVE_RE.search(scan_for_five):
        flags.append("Prompt injection detected: '5/5 score forcing'")

    return flags


def parse_pr_body(body: str) -> Dict[str, str]:
    """Parses markdown key-value pairs from a PR body.

    Matches lines like `- **Key:** Value` (and plain `Key: Value` for known PR body fields)
    and normalizes keys to snake_case.
    """
    fields: Dict[str, str] = {}
    if not body:
        return fields

    bold_pattern = re.compile(r"^\s*[-*+]\s*\*\*([^*:\n]+)(?::\*\*|\*\*\s*:?)\s*(.*)$")
    plain_pattern = re.compile(r"^\s*(?:[-*+]\s*)?([A-Za-z][A-Za-z0-9 /_-]{0,30}):\s*(.+)$")

    for line in body.splitlines():
        m = bold_pattern.match(line)
        if m:
            raw_key = m.group(1).strip().lower()
            val = m.group(2).strip()
            norm_key = re.sub(r"[^a-z0-9]+", "_", raw_key).strip("_")
            fields[norm_key] = val
            continue
        m_plain = plain_pattern.match(line)
        if m_plain:
            raw_key = m_plain.group(1).strip().lower()
            norm_key = re.sub(r"[^a-z0-9]+", "_", raw_key).strip("_")
            if norm_key in REQUIRED_BODY_FIELDS and norm_key not in fields:
                fields[norm_key] = m_plain.group(2).strip()

    return fields


def _extract_fenced_code_blocks(text: str) -> Tuple[List[str], bool]:
    """Returns (closed_fenced_blocks, has_unclosed_fence)."""
    if not text:
        return [], False
    blocks = [m.group(0) for m in FENCED_CODE_RE.finditer(text)]
    remainder = FENCED_CODE_RE.sub("", text)
    has_unclosed = "```" in remainder
    return blocks, has_unclosed


def _extract_image_anchors(text: str) -> List[str]:
    """Extracts Markdown ![...](...) image anchors outside fenced code blocks."""
    if not text:
        return []
    without_code = FENCED_CODE_RE.sub("", text)
    return [m.group(0) for m in IMAGE_ANCHOR_RE.finditer(without_code)]


def validate_additions_schema(
    additions_data: Any,
    allow_legacy: bool = False,
) -> Dict[str, Any]:
    """Validates analyze_additions.json against the REQ-025 canonical proposals schema.

    Canonical schema:
      {"proposals": [{"source": non-empty str, "target": non-empty str, "status": "proposed",
                      "category"?: str, "note"?: str}]}

    When allow_legacy=True, also accepts legacy book_record_additions.terms payloads.
    """
    defects: List[str] = []
    normalized_proposals: List[Dict[str, Any]] = []

    if not allow_legacy:
        if not isinstance(additions_data, dict):
            defects.append(
                f"Invalid analyze_additions.json top-level type: expected dict, got {type(additions_data).__name__}"
            )
            return {"valid": False, "defects": defects, "proposals": []}

        if "proposals" not in additions_data:
            defects.append("Invalid analyze_additions.json schema: missing required 'proposals' list")
            return {"valid": False, "defects": defects, "proposals": []}

        extra_top = set(additions_data.keys()) - {"proposals"}
        if extra_top:
            defects.append(
                f"Invalid analyze_additions.json schema: unexpected top-level keys {sorted(extra_top)}"
            )

        raw_proposals = additions_data.get("proposals")
        if not isinstance(raw_proposals, list):
            defects.append(
                f"Invalid analyze_additions.json schema: 'proposals' must be a list, got {type(raw_proposals).__name__}"
            )
            return {"valid": False, "defects": defects, "proposals": []}

        for idx, item in enumerate(raw_proposals):
            if not isinstance(item, dict):
                defects.append(
                    f"Invalid proposal entry at index {idx} in analyze_additions.json: expected dict, got {type(item).__name__}"
                )
                continue

            extra_keys = set(item.keys()) - CANONICAL_PROPOSAL_KEYS
            if extra_keys:
                defects.append(
                    f"Invalid proposal entry at index {idx}: unexpected keys {sorted(extra_keys)}"
                )

            src = item.get("source")
            if not isinstance(src, str) or not src.strip():
                defects.append(f"Proposal at index {idx} must have a non-empty string 'source'")

            tgt = item.get("target")
            if not isinstance(tgt, str) or not tgt.strip():
                defects.append(f"Proposal at index {idx} must have a non-empty string 'target'")

            st = item.get("status")
            if st != "proposed":
                defects.append(
                    f"Analyze addition proposal status must be 'proposed', got '{st}'"
                )

            if "category" in item and not isinstance(item["category"], str):
                defects.append(f"Proposal at index {idx} optional 'category' must be a string")

            if "note" in item and not isinstance(item["note"], str):
                defects.append(f"Proposal at index {idx} optional 'note' must be a string")

            normalized_proposals.append(item)

        return {
            "valid": len(defects) == 0,
            "defects": defects,
            "proposals": normalized_proposals,
        }

    # Legacy mode (allow_legacy=True)
    if isinstance(additions_data, dict) and "proposals" in additions_data:
        return validate_additions_schema(additions_data, allow_legacy=False)

    if isinstance(additions_data, list):
        legacy_items = additions_data
    elif (
        isinstance(additions_data, dict)
        and "book_record_additions" in additions_data
        and isinstance(additions_data["book_record_additions"], dict)
    ):
        bra = additions_data["book_record_additions"]
        terms = bra.get("terms", [])
        names = bra.get("names", [])
        if not isinstance(terms, list) or not isinstance(names, list):
            defects.append("Invalid legacy book_record_additions: 'terms' and 'names' must be lists")
            return {"valid": False, "defects": defects, "proposals": []}
        legacy_items = list(terms) + list(names)
    else:
        defects.append("Invalid analyze_additions.json schema")
        return {"valid": False, "defects": defects, "proposals": []}

    for idx, item in enumerate(legacy_items):
        if not isinstance(item, dict):
            defects.append(
                f"Invalid proposal entry at index {idx} in analyze_additions.json: expected dict, got {type(item).__name__}"
            )
            continue
        src = item.get("source") if "source" in item else item.get("term")
        tgt = item.get("target") if "target" in item else item.get("translation")
        if not isinstance(src, str) or not src.strip():
            defects.append(f"Legacy proposal at index {idx} must have a non-empty 'source'")
        if not isinstance(tgt, str) or not tgt.strip():
            defects.append(f"Legacy proposal at index {idx} must have a non-empty 'target'")
        st = item.get("status")
        if st != "proposed":
            defects.append(f"Analyze addition proposal status must be 'proposed', got '{st}'")
        normalized_proposals.append(item)

    return {
        "valid": len(defects) == 0,
        "defects": defects,
        "proposals": normalized_proposals,
    }


def _record_injection_flags(
    surface_label: str,
    text: str,
    defects: List[str],
    injection_flags: List[str],
    *,
    allow_rubric_lines: bool = False,
) -> None:
    found = detect_prompt_injection(text, allow_rubric_lines=allow_rubric_lines)
    if found:
        injection_flags.extend(found)
        for flag in found:
            if surface_label:
                defects.append(f"{surface_label}: {flag}")
            else:
                defects.append(flag)


def validate_translation_pr(
    head_ref: str,
    author_login: str,
    body: str,
    changed_files: List[str],
    source_xml_text: str,
    translation_xml_text: str,
    expected_glossary_sha: Optional[str] = None,
    additions_data: Optional[Any] = None,
    manifest_source_lang: Optional[str] = None,
    manifest_target_lang: Optional[str] = None,
    expected_source_lang: Optional[str] = None,
    expected_target_lang: Optional[str] = None,
    expected_issue_number: Optional[Union[int, str]] = None,
    bot_login: Optional[str] = None,
    is_seed_pr: bool = False,
    manifest: Optional[Dict[str, Any]] = None,
    translation_xml_bytes: Optional[bytes] = None,
    additions_raw: Optional[Union[bytes, str]] = None,
    existing_slugs: Optional[Collection[str]] = None,
    allow_legacy_additions: bool = False,
) -> Dict[str, Any]:
    """Validates a translation PR per REQ-021, REQ-023, and REQ-025."""
    defects: List[str] = []
    injection_flags: List[str] = []

    if isinstance(manifest, dict):
        manifest_source_lang = manifest_source_lang or manifest.get("source_lang")
        manifest_target_lang = manifest_target_lang or manifest.get("target_lang")

    # 1. Pre-LLM prompt-injection check on PR body
    _record_injection_flags("", body or "", defects, injection_flags, allow_rubric_lines=False)

    # 2. Branch grammar & author match (REQ-021)
    branch_slug = ""
    branch_ch = ""
    branch_login = ""
    book_chunk = ""

    if head_ref:
        if (
            author_login
            and head_ref.startswith("translate/")
            and head_ref.endswith(f"-{author_login}")
            and LOGIN_RE.match(author_login)
        ):
            prefix = head_ref[len("translate/") : -len(f"-{author_login}")]
            pm = SLUG_CH_PREFIX_RE.match(prefix)
            if pm:
                branch_slug = pm.group("slug")
                branch_ch = pm.group("ch")
                branch_login = author_login
                book_chunk = f"{branch_slug}-{branch_ch}"
            else:
                defects.append(
                    f"Branch name does not follow 'translate/<slug>-<chNN>-<login>' format: '{head_ref}'"
                )
        else:
            m = TRANSLATE_BRANCH_RE.match(head_ref)
            if m and not (not author_login and SUBCHAPTER_BRANCH_TOKEN_RE.search(head_ref)):
                branch_slug = m.group("slug")
                branch_ch = m.group("ch")
                branch_login = m.group("login")
                book_chunk = f"{branch_slug}-{branch_ch}"
                if SUBCHAPTER_BRANCH_TOKEN_RE.search(head_ref):
                    defects.append(
                        f"Branch name does not follow 'translate/<slug>-<chNN>-<login>' format (sub-chapter branches are unsupported): '{head_ref}'"
                    )
                if author_login and branch_login != author_login:
                    defects.append(
                        f"Branch agent '{branch_login}' does not match PR author '{author_login}'"
                    )
            else:
                defects.append(
                    f"Branch name does not follow 'translate/<slug>-<chNN>-<login>' format: '{head_ref}'"
                )
                if author_login and head_ref.startswith("translate/"):
                    remainder = head_ref[len("translate/") :]
                    if remainder.endswith(f"-{author_login}"):
                        inferred_agent = author_login
                    else:
                        last_hyphen = remainder.rfind("-")
                        inferred_agent = remainder[last_hyphen + 1 :] if last_hyphen != -1 else remainder
                    if inferred_agent != author_login:
                        defects.append(
                            f"Branch agent '{inferred_agent}' does not match PR author '{author_login}'"
                        )
    else:
        defects.append("Missing PR head branch reference")

    if branch_slug and len(branch_slug) > 64:
        defects.append(f"Book slug '{branch_slug}' exceeds maximum length of 64 characters")

    if branch_slug and existing_slugs is not None and branch_slug not in existing_slugs:
        defects.append(
            f"Book slug '{branch_slug}' does not match an existing runs/<slug>/ directory on main"
        )

    # 3. File allowlist & byte caps (REQ-021)
    has_translation_xml = False
    has_additions_file = False
    changed_chapters = set()

    for f in changed_files or []:
        f_clean = f.replace("\\", "/")
        parts = f_clean.split("/")
        if (
            len(parts) == 4
            and parts[0] == "runs"
            and SLUG_RE.match(parts[1])
            and len(parts[1]) <= 64
            and CHAPTER_RE.match(parts[2])
        ):
            file_slug, file_ch, filename = parts[1], parts[2], parts[3]
            changed_chapters.add((file_slug, file_ch))
            if filename == "translation.xml":
                has_translation_xml = True
            elif filename == "analyze_additions.json":
                has_additions_file = True
            else:
                defects.append(f"Disallowed file touched in chapter folder: {f}")
        else:
            defects.append(f"Disallowed file touched outside chapter allowlist: {f}")

    if len(changed_chapters) > 1:
        defects.append(
            f"PR violates single-chapter diff constraint: changed files span multiple chapters {sorted(changed_chapters)}"
        )

    if not has_translation_xml:
        defects.append("Missing required translation file: runs/<slug>/<chNN>/translation.xml")

    trans_byte_len = (
        len(translation_xml_bytes)
        if translation_xml_bytes is not None
        else len((translation_xml_text or "").encode("utf-8"))
    )
    trans_exceeds_cap = trans_byte_len > MAX_TRANSLATION_XML_BYTES
    if trans_exceeds_cap:
        defects.append(
            f"translation.xml size ({trans_byte_len} UTF-8 bytes) exceeds maximum cap of {MAX_TRANSLATION_XML_BYTES} bytes"
        )

    # 4. Cross-checked PR body fields (REQ-021)
    parsed_body = parse_pr_body(body)
    for req_field in REQUIRED_BODY_FIELDS:
        val = parsed_body.get(req_field)
        if not val:
            defects.append(f"Missing or empty required PR body field: '{req_field}'")

    body_agent = parsed_body.get("agent", "").strip()
    if body_agent and author_login and body_agent != author_login:
        defects.append(
            f"PR body Agent '{body_agent}' does not match PR author '{author_login}'"
        )

    auth_source_lang = (manifest_source_lang or expected_source_lang or "").strip().lower() or None
    auth_target_lang = (manifest_target_lang or expected_target_lang or "").strip().lower() or None

    body_src_lang = parsed_body.get("source_language", "").strip().lower()
    if auth_source_lang and body_src_lang and body_src_lang != auth_source_lang:
        defects.append(
            f"PR body Source language '{parsed_body.get('source_language')}' does not match manifest source_lang '{auth_source_lang}'"
        )
    elif body_src_lang and body_src_lang not in ("en", "zh") and not auth_source_lang:
        defects.append(
            f"Unsupported PR body Source language '{body_src_lang}'; expected 'en' or 'zh'"
        )

    body_tgt_lang = parsed_body.get("target_language", "").strip().lower()
    if auth_target_lang and body_tgt_lang and body_tgt_lang != auth_target_lang:
        defects.append(
            f"PR body Target language '{parsed_body.get('target_language')}' does not match manifest target_lang '{auth_target_lang}'"
        )
    elif body_tgt_lang and body_tgt_lang != "vi" and not auth_target_lang:
        defects.append(
            f"Unsupported PR body Target language '{body_tgt_lang}'; expected 'vi'"
        )

    ch_slug = ""
    ch_name = ""
    if len(changed_chapters) == 1:
        ch_slug, ch_name = next(iter(changed_chapters))
        expected_chunk_from_files = f"{ch_slug}-{ch_name}"
        if book_chunk and book_chunk != expected_chunk_from_files:
            defects.append(
                f"Branch chunk '{book_chunk}' does not match changed files chapter '{expected_chunk_from_files}'"
            )

    target_slug = branch_slug or ch_slug
    target_ch = branch_ch or ch_name
    expected_chunk_full = (
        f"{ch_slug}-{ch_name}"
        if (ch_slug and ch_name)
        else (f"{target_slug}-{target_ch}" if (target_slug and target_ch) else "")
    )

    body_book = parsed_body.get("book", "").strip()
    if target_slug and body_book and body_book != target_slug:
        defects.append(
            f"PR body Book '{body_book}' does not match expected slug '{target_slug}'"
        )

    body_chunk = parsed_body.get("chunk_id", "").strip()
    if expected_chunk_full and body_chunk and body_chunk != expected_chunk_full:
        defects.append(
            f"PR body chunk_id '{body_chunk}' does not match expected chunk '{expected_chunk_full}'"
        )

    # ch01 seed PR rule vs ch02..chNN Issue & Glossary SHA checks
    seed_pr_eligible = (
        target_ch == "ch01"
        and (not ch_name or ch_name == "ch01")
        and (
            bool(is_seed_pr)
            or (bool(bot_login) and bool(author_login) and author_login == bot_login)
        )
    )

    if target_ch == "ch01" and bot_login and author_login and author_login != bot_login and not is_seed_pr:
        defects.append(
            f"Chapter ch01 seed PR is restricted to bot_login '{bot_login}', got '{author_login}'"
        )

    body_issue = parsed_body.get("issue", "").strip()
    body_sha = parsed_body.get("glossary_sha", "").strip()

    if seed_pr_eligible:
        if body_issue and body_issue != "seed":
            defects.append(f"Bot ch01 seed PR Issue must be 'seed', got '{body_issue}'")
        if body_sha and body_sha != "seed":
            defects.append(f"Bot ch01 seed PR Glossary SHA must be 'seed', got '{body_sha}'")
    else:
        if body_issue:
            m_iss = ISSUE_REF_RE.match(body_issue)
            if not m_iss:
                defects.append(
                    f"Invalid PR body Issue '{body_issue}': expected '#<issue_number>'"
                )
            elif expected_issue_number is not None:
                exp_iss_str = str(expected_issue_number).strip().lstrip("#")
                if m_iss.group(1) != exp_iss_str:
                    defects.append(
                        f"PR body Issue '{body_issue}' does not match expected chapter tracking issue '#{exp_iss_str}'"
                    )

        if body_sha:
            if not GLOSSARY_SHA_RE.match(body_sha):
                defects.append(
                    f"Invalid Glossary SHA '{body_sha}': expected 64-char SHA-256 or legacy 40-char lowercase hex"
                )
            if expected_glossary_sha is not None and body_sha != expected_glossary_sha:
                defects.append(
                    f"Glossary SHA mismatch: PR has '{body_sha}', expected '{expected_glossary_sha}'"
                )

    body_segments = parsed_body.get("segments", "").strip()
    segments_format_ok = False
    if body_segments:
        if SEGMENTS_RANGE_RE.match(body_segments):
            segments_format_ok = True
        else:
            defects.append(
                f"Invalid PR body Segments format '{body_segments}': expected 'S-NNNN..S-NNNN' or 'S-NNNN'"
            )

    # 5. XML parity, segment bounds, <original> byte-identity, <hanviet>, and <translate> code/image invariance
    if not source_xml_text or not source_xml_text.strip():
        defects.append("source_xml_text is empty or contains only whitespace")
    if not translation_xml_text or not translation_xml_text.strip():
        defects.append("translation_xml_text is empty or contains only whitespace")

    # Scan any stray content outside <segment> blocks for prompt injection
    if source_xml_text:
        outside_src = SEGMENT_BLOCK_RE.sub("", source_xml_text)
        _record_injection_flags("source.txt", outside_src, defects, injection_flags, allow_rubric_lines=False)
    if translation_xml_text and not trans_exceeds_cap:
        outside_trans = SEGMENT_BLOCK_RE.sub("", translation_xml_text)
        _record_injection_flags("translation.xml", outside_trans, defects, injection_flags, allow_rubric_lines=False)

    source_segs: List[Dict[str, Any]] = []
    trans_segs: List[Dict[str, Any]] = []

    if source_xml_text and source_xml_text.strip():
        source_segs = parse_segments(source_xml_text)
        if len(source_segs) == 0:
            defects.append("source_xml_text contains 0 parsed segments")
        elif len(source_segs) > MAX_TRANSLATION_SEGMENTS:
            defects.append(
                f"source_xml_text segment count ({len(source_segs)}) exceeds maximum cap of {MAX_TRANSLATION_SEGMENTS}"
            )
        # Scan <original> blocks in source_xml_text for prompt injection (fast batch pre-check)
        src_seg_raw_pairs = [
            (dict(re.findall(r'(\w+)="([^"]*)"', m_seg.group(1))).get("id", "unknown"), m_seg.group(2))
            for m_seg in SEGMENT_BLOCK_RE.finditer(source_xml_text)
        ]
        joined_src_orig = "\n".join(
            orig_txt for _, body_raw in src_seg_raw_pairs for orig_txt in ORIG_TAG_RE.findall(body_raw)
        )
        if detect_prompt_injection(joined_src_orig, allow_rubric_lines=False):
            for s_id, body_raw in src_seg_raw_pairs:
                for orig_txt in ORIG_TAG_RE.findall(body_raw):
                    _record_injection_flags(
                        f"Source segment {s_id} <original>",
                        orig_txt,
                        defects,
                        injection_flags,
                        allow_rubric_lines=False,
                    )

    if translation_xml_text and translation_xml_text.strip() and not trans_exceeds_cap:
        trans_segs = parse_segments(translation_xml_text)
        if len(trans_segs) == 0:
            defects.append("translation_xml_text contains 0 parsed segments")
        elif len(trans_segs) > MAX_TRANSLATION_SEGMENTS:
            defects.append(
                f"translation.xml segment count ({len(trans_segs)}) exceeds maximum cap of {MAX_TRANSLATION_SEGMENTS}"
            )

    if source_segs and segments_format_ok:
        first_id = source_segs[0]["id"]
        last_id = source_segs[-1]["id"]
        valid_segment_specs = (
            {first_id, f"{first_id}..{last_id}"}
            if len(source_segs) == 1
            else {f"{first_id}..{last_id}"}
        )
        if body_segments not in valid_segment_specs:
            defects.append(
                f"PR body Segments '{body_segments}' does not match source.txt segment bounds '{first_id}..{last_id}'"
            )

    # Authoritative source language for <hanviet> obligation: NEVER trust spoofed PR body 'en' on a 'zh' book
    effective_source_lang = auth_source_lang if auth_source_lang is not None else body_src_lang

    if source_segs and trans_segs:
        is_parity_valid, parity_errors = validate_segment_parity(source_xml_text, translation_xml_text)
        if not is_parity_valid:
            defects.extend(parity_errors)

        src_seg_matches = list(SEGMENT_BLOCK_RE.finditer(source_xml_text))
        trans_seg_matches = list(SEGMENT_BLOCK_RE.finditer(translation_xml_text))
        trans_has_any_inj = bool(
            detect_prompt_injection(
                "\n".join(m_s.group(2) for m_s in trans_seg_matches),
                allow_rubric_lines=False,
            )
        )

        limit = min(len(source_segs), len(trans_segs), len(src_seg_matches), len(trans_seg_matches))
        for i in range(limit):
            s_seg = source_segs[i]
            t_seg = trans_segs[i]
            seg_id = t_seg.get("id") or s_seg.get("id") or f"index-{i}"
            s_body_raw = src_seg_matches[i].group(2)
            t_body_raw = trans_seg_matches[i].group(2)

            s_orig_list = ORIG_TAG_RE.findall(s_body_raw)
            t_orig_list = ORIG_TAG_RE.findall(t_body_raw)
            t_trans_list = TRANS_TAG_RE.findall(t_body_raw)
            t_hv_list = HANVIET_TAG_RE.findall(t_body_raw)

            # <original> byte-identity
            if len(t_orig_list) != 1 or len(s_orig_list) != 1 or s_orig_list[0] != t_orig_list[0]:
                defects.append(f"Byte invariance violation in <original> text for segment {seg_id}")

            # Non-empty <translate>
            if len(t_trans_list) == 0:
                defects.append(f"Missing <translate> block in segment {seg_id}")
            elif len(t_trans_list) > 1:
                defects.append(f"Duplicate <translate> blocks in segment {seg_id}")
            elif not t_trans_list[0].strip():
                defects.append(f"Empty <translate> block in segment {seg_id}")

            # Code block & image anchor byte-invariance between <original> and <translate>
            if len(s_orig_list) == 1 and len(t_trans_list) == 1:
                orig_codes, orig_unclosed = _extract_fenced_code_blocks(s_orig_list[0])
                trans_codes, trans_unclosed = _extract_fenced_code_blocks(t_trans_list[0])
                if orig_codes != trans_codes or orig_unclosed != trans_unclosed:
                    defects.append(
                        f"Byte invariance violation in fenced code block for segment {seg_id}"
                    )

                orig_images = _extract_image_anchors(s_orig_list[0])
                trans_images = _extract_image_anchors(t_trans_list[0])
                if orig_images != trans_images:
                    defects.append(
                        f"Byte invariance violation in Markdown image anchor for segment {seg_id}"
                    )

            # Scan <original>, <hanviet>, <translate>, and raw segment body for prompt injection
            if trans_has_any_inj:
                if t_orig_list and (not s_orig_list or t_orig_list[0] != s_orig_list[0]):
                    for orig_txt in t_orig_list:
                        _record_injection_flags(
                            f"Segment {seg_id} <original>",
                            orig_txt,
                            defects,
                            injection_flags,
                            allow_rubric_lines=False,
                        )
                for hv_txt in t_hv_list:
                    _record_injection_flags(
                        f"Segment {seg_id} <hanviet>",
                        hv_txt,
                        defects,
                        injection_flags,
                        allow_rubric_lines=False,
                    )
                for tr_txt in t_trans_list:
                    _record_injection_flags(
                        f"Segment {seg_id}",
                        tr_txt,
                        defects,
                        injection_flags,
                        allow_rubric_lines=False,
                    )
                if re.search(r"(?i)</translate>[\s\S]*?<translate>", t_body_raw):
                    _record_injection_flags(
                        f"Segment {seg_id}",
                        t_body_raw,
                        defects,
                        injection_flags,
                        allow_rubric_lines=False,
                    )
                stripped_seg_remainder = HANVIET_TAG_RE.sub(
                    "", TRANS_TAG_RE.sub("", ORIG_TAG_RE.sub("", t_body_raw))
                )
                if stripped_seg_remainder.strip():
                    _record_injection_flags(
                        f"Segment {seg_id}",
                        stripped_seg_remainder,
                        defects,
                        injection_flags,
                        allow_rubric_lines=False,
                    )

    elif translation_xml_text and translation_xml_text.strip():
        # Still scan translation_xml_text segments for prompt injection even if source_xml was empty/invalid
        for m_seg in SEGMENT_BLOCK_RE.finditer(translation_xml_text):
            attrs = dict(re.findall(r'(\w+)="([^"]*)"', m_seg.group(1)))
            seg_id = attrs.get("id", "unknown")
            t_body_raw = m_seg.group(2)
            for orig_txt in ORIG_TAG_RE.findall(t_body_raw):
                _record_injection_flags(f"Segment {seg_id} <original>", orig_txt, defects, injection_flags, allow_rubric_lines=False)
            for hv_txt in HANVIET_TAG_RE.findall(t_body_raw):
                _record_injection_flags(f"Segment {seg_id} <hanviet>", hv_txt, defects, injection_flags, allow_rubric_lines=False)
            for tr_txt in TRANS_TAG_RE.findall(t_body_raw):
                _record_injection_flags(f"Segment {seg_id}", tr_txt, defects, injection_flags, allow_rubric_lines=False)

    # Mandatory <hanviet> check for zh translations
    if effective_source_lang == "zh" and translation_xml_text and translation_xml_text.strip():
        for m_seg in SEGMENT_BLOCK_RE.finditer(translation_xml_text):
            attrs = dict(re.findall(r'(\w+)="([^"]*)"', m_seg.group(1)))
            seg_id = attrs.get("id", "unknown")
            hv_blocks = HANVIET_TAG_RE.findall(m_seg.group(2))
            if len(hv_blocks) != 1 or not hv_blocks[0].strip():
                defects.append(
                    f"Segment {seg_id} missing required non-empty <hanviet> block for zh translation"
                )

    # 6. Optional analyze_additions.json cap, injection scan, and canonical schema check (REQ-025)
    parsed_additions: Any = additions_data
    raw_additions_str: Optional[str] = None
    additions_byte_len: Optional[int] = None

    if additions_raw is not None:
        if isinstance(additions_raw, (bytes, bytearray)):
            additions_byte_len = len(additions_raw)
            try:
                raw_additions_str = bytes(additions_raw).decode("utf-8")
            except UnicodeDecodeError:
                defects.append("analyze_additions.json is not valid UTF-8")
                raw_additions_str = None
        else:
            raw_additions_str = str(additions_raw)
            additions_byte_len = len(raw_additions_str.encode("utf-8"))

        if not parsed_additions and raw_additions_str:
            try:
                parsed_additions = json.loads(raw_additions_str)
            except json.JSONDecodeError:
                defects.append("Invalid JSON in analyze_additions.json")

    elif parsed_additions is not None:
        if isinstance(parsed_additions, (bytes, bytearray)):
            additions_byte_len = len(parsed_additions)
            try:
                raw_additions_str = bytes(parsed_additions).decode("utf-8")
                parsed_additions = json.loads(raw_additions_str)
            except (UnicodeDecodeError, json.JSONDecodeError):
                defects.append("Invalid UTF-8 or JSON in analyze_additions.json")
                parsed_additions = None
        elif isinstance(parsed_additions, str):
            raw_additions_str = parsed_additions
            additions_byte_len = len(raw_additions_str.encode("utf-8"))
            if raw_additions_str.strip().startswith(("{", "[")):
                try:
                    parsed_additions = json.loads(raw_additions_str)
                except json.JSONDecodeError:
                    defects.append("Invalid JSON in analyze_additions.json")
                    parsed_additions = None
        else:
            try:
                raw_additions_str = json.dumps(parsed_additions, ensure_ascii=False)
            except (TypeError, ValueError):
                raw_additions_str = str(parsed_additions)
            additions_byte_len = len(raw_additions_str.encode("utf-8"))

    if additions_byte_len is not None and additions_byte_len > MAX_ADDITIONS_JSON_BYTES:
        defects.append(
            f"analyze_additions.json size ({additions_byte_len} UTF-8 bytes) exceeds maximum cap of {MAX_ADDITIONS_JSON_BYTES} bytes"
        )

    if raw_additions_str:
        _record_injection_flags(
            "analyze_additions.json",
            raw_additions_str,
            defects,
            injection_flags,
            allow_rubric_lines=False,
        )

    if parsed_additions is not None:
        schema_res = validate_additions_schema(parsed_additions, allow_legacy=allow_legacy_additions)
        if not schema_res["valid"]:
            defects.extend(schema_res["defects"])
    elif has_additions_file and not raw_additions_str and additions_raw is None:
        defects.append("Changed files include analyze_additions.json but no additions content was provided")

    return {
        "valid": len(defects) == 0,
        "defects": defects,
        "injection_flags": injection_flags,
        "parsed_body": parsed_body,
    }


def validate_review(
    pr_author_login: str,
    review_author_login: str = "",
    review_body: str = "",
    line_comment_count: int = 0,
    *,
    reviewer_login: Optional[str] = None,
) -> Dict[str, Any]:
    """Validates an independent peer review per REQ-022 and REQ-023."""
    eff_reviewer = (reviewer_login if reviewer_login is not None else review_author_login or "").strip()
    eff_pr_author = (pr_author_login or "").strip()

    defects: List[str] = []
    scores: Dict[str, float] = {}

    # 1. Non-author reviewer independence
    if not eff_reviewer:
        defects.append("Missing review author login")
    if not eff_pr_author:
        defects.append("Missing PR author login")
    if eff_pr_author and eff_reviewer and eff_pr_author == eff_reviewer:
        defects.append(
            f"Review author '{eff_reviewer}' cannot review their own PR (author: '{eff_pr_author}')"
        )

    # 2. Inline diff comment OR S-NNNN segment reference in review body
    has_segment_ref = bool(review_body and SEGMENT_REF_RE.search(review_body))
    if line_comment_count < 1 and not has_segment_ref:
        defects.append(
            "Review must include at least 1 inline line comment or segment-anchored note matching S-NNNN"
        )

    # 3. Pre-LLM prompt injection scan on review body
    injection_flags = detect_prompt_injection(review_body or "", allow_rubric_lines=True)
    if injection_flags:
        defects.extend(injection_flags)

    # 4. 6-dimension rubric score block in [1.0, 5.0]
    spec_valid_dims = {
        m.group(1).lower()
        for m in REVIEW_SCORE_SPEC_RE.finditer(review_body or "")
    }

    for m in RAW_REVIEW_SCORE_RE.finditer(review_body or ""):
        dim_name = m.group(1).lower()
        sep = m.group(2)
        score_str = m.group(3)
        denom_str = m.group(4)

        # Skip segment references like "Fidelity in S-0002"
        if re.search(r"\bS-$", sep):
            continue

        try:
            score_val = float(score_str)
        except ValueError:
            defects.append(f"Invalid numeric score for '{dim_name}': '{score_str}'")
            continue

        if denom_str is not None and denom_str not in ("5", "5.0"):
            defects.append(
                f"Invalid rubric scale '/{denom_str}' for '{dim_name}'; expected '/5' or '/5.0'"
            )
            continue

        scores[dim_name] = score_val
        if not (1.0 <= score_val <= 5.0):
            defects.append(f"Score for '{dim_name}' ({score_val}) is outside [1.0, 5.0]")
        elif dim_name not in spec_valid_dims:
            defects.append(f"Score format for '{dim_name}' does not match REQ-022 rubric grammar")

    for dim in REVIEW_DIMENSIONS:
        if dim not in scores:
            defects.append(f"Missing required rubric score dimension: '{dim}'")

    return {
        "valid": len(defects) == 0,
        "defects": defects,
        "scores": scores,
        "injection_flags": injection_flags,
    }
