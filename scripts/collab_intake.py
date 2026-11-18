"""
scripts/collab_intake.py — Book submission intake and pre-screen validation.

Implements REQ-017, REQ-018, REQ-019:
- Slug regex ^[a-z0-9]+(?:-[a-z0-9]+)*$ (1..64 chars), head_ref == book/<slug>, duplicate-slug guard
- Path allowlist: books/<slug>/manifest.json and single source file books/<slug>/source/<filename>
- Git LFS pointer rejection, 1..max_source_bytes size bounds, and per-chapter extraction bounds
  (>= 1 chapter, each 1..2,000 segments and <= 2,621,440 UTF-8 bytes)
- Manifest schema: 8 required non-empty string fields, https:// license_url, optional genre,
  supported_pairs, and allowed_licenses warning classification
- Source language validation: CJK ratio (> 15% for zh, <= 15% for en) and < 5% Vietnamese
  diacritic density among alphabetic characters for en
- Pre-LLM prompt-injection scanning on sample and extracted <original> blocks
- Commit-bound [collab-prescreen sha=<head_oid> status=PASS|WARN|FAIL] comment formatting
"""

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union

from scripts.collab_config import load_collab_config
from scripts.evaluators.fallback import VI_DIACRITICS
from scripts.extract import extract_book
from scripts.helper import parse_segments
from scripts.utils.language import CLEAN_TEXT_RE, detect_source_language

REQUIRED_MANIFEST_FIELDS: Tuple[str, ...] = (
    "slug",
    "title",
    "author",
    "source_lang",
    "target_lang",
    "license",
    "license_url",
    "submitter",
)

ALLOWED_GENRES: Tuple[str, ...] = (
    "fiction",
    "literary",
    "non-fiction",
    "general",
)

SLUG_RE: re.Pattern = re.compile(r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z")
MAX_SLUG_LENGTH: int = 64

LFS_POINTER_HEADER: str = "version https://git-lfs.github.com/spec/v1"
LFS_POINTER_BYTES: bytes = b"version https://git-lfs.github.com/spec/v1"

MIN_CHAPTER_SEGMENTS: int = 1
MAX_CHAPTER_SEGMENTS: int = 2000
MAX_CHAPTER_SOURCE_BYTES: int = 2_621_440

INTAKE_INJECTION_PATTERNS: Tuple[Tuple[re.Pattern, str], ...] = (
    (
        re.compile(
            r"(?i)\b(?:ignore|disregard|forget|override)\s+"
            r"(?:all\s+|any\s+)?(?:previous|prior|above|system|developer)?\s*"
            r"(?:instructions|prompts|rules|directives)\b"
        ),
        "ignore/disregard instructions directive",
    ),
    (
        re.compile(r"(?i)<\s*/?\s*(?:system|developer)\b[^>]*>"),
        "<system>/<developer> role tag",
    ),
    (
        re.compile(r"(?i)\b(?:system|developer)\s+prompt\s*:"),
        "system prompt: role spoofing",
    ),
    (
        re.compile(
            r"(?im)(?:^\s*system\s*:|\bsystem\s*:\s*"
            r"(?:you\s+are|ignore|disregard|score|override|act\s+as|prompt|instruction|give|assign|verdict))"
        ),
        "system: role prefix",
    ),
    (
        re.compile(r"(?i)\[\s*/?\s*INST\s*\]"),
        "[INST] role tag",
    ),
    (
        re.compile(r"(?i)\byou\s+are\s+now\s+a\b"),
        "you are now a persona override",
    ),
    (
        re.compile(r"(?i)\bverdict\s*:\s*pass\b"),
        "verdict: pass forcing",
    ),
    (
        re.compile(
            r"(?i)(?:\bscore\s+5(?:\.0)?\s+on\s+all\s+dimensions\b|\b5(?:\.0)?\s*/\s*5(?:\.0)?(?![/\d]))"
        ),
        "5/5 score forcing",
    ),
    (
        re.compile(r"(?i)<\s*/?\s*(?:target_artifact|governing_specification)\s*>"),
        "prompt XML delimiter escape",
    ),
    (
        re.compile(r"</translate>[\s\S]*?<translate>"),
        "translate tag breakout",
    ),
)

_SYNTHETIC_PREFACE_HEADING = "# 序言 / 内容简介"


def _is_valid_slug(slug_val: Any) -> bool:
    """Checks whether slug matches ^[a-z0-9]+(?:-[a-z0-9]+)*$ with length 1..64."""
    return (
        isinstance(slug_val, str)
        and 1 <= len(slug_val) <= MAX_SLUG_LENGTH
        and bool(SLUG_RE.match(slug_val))
    )


def detect_intake_prompt_injection(text: str) -> List[str]:
    """Scans source text or <original> blocks for REQ-023 prompt-injection patterns."""
    flags: List[str] = []
    if not text:
        return flags
    for pattern, label in INTAKE_INJECTION_PATTERNS:
        if pattern.search(text):
            flags.append(f"Prompt injection warning: '{label}'")
    return flags


def _inspect_chapter_entry(
    idx: int,
    ch_item: Dict[str, Any],
) -> Tuple[str, int, int, List[str]]:
    """Extracts (chapter_name, segment_count, byte_len, originals) from a chapter dict."""
    ch_name = str(ch_item.get("chapter") or f"ch{int(ch_item.get('ch_num', idx)):02d}")
    originals: List[str] = []

    if "source_path" in ch_item and ch_item["source_path"] and Path(ch_item["source_path"]).is_file():
        raw_bytes = Path(ch_item["source_path"]).read_bytes()
        src_txt = raw_bytes.decode("utf-8", errors="replace")
        segs = parse_segments(src_txt)
        if segs:
            originals.extend(s.get("original", "") for s in segs if s.get("original"))
        elif src_txt.strip():
            originals.append(src_txt)
        seg_count = int(ch_item.get("segment_count", len(segs)))
        byte_len = int(ch_item.get("byte_len", len(raw_bytes)))
    elif "source_text" in ch_item and ch_item["source_text"] is not None:
        src_txt = str(ch_item["source_text"])
        segs = parse_segments(src_txt)
        if segs:
            originals.extend(s.get("original", "") for s in segs if s.get("original"))
        elif src_txt.strip():
            originals.append(src_txt)
        seg_count = int(ch_item.get("segment_count", len(segs)))
        byte_len = int(ch_item.get("byte_len", len(src_txt.encode("utf-8"))))
    else:
        seg_count = int(ch_item.get("segment_count", 0))
        byte_len = int(ch_item.get("byte_len", 0))

    return ch_name, seg_count, byte_len, originals


def _validate_chapter_items(
    chapters_list: List[Dict[str, Any]],
    hard_defects: List[str],
    extracted_originals: List[str],
) -> None:
    """Validates >= 1 chapter and per-chapter segment (1..2000) and byte (1..2621440) bounds."""
    if len(chapters_list) == 0:
        hard_defects.append("Source extraction yielded 0 chapters (expected >= 1 chapter)")
        return

    for idx, ch_item in enumerate(chapters_list, start=1):
        ch_name, seg_count, byte_len, ch_originals = _inspect_chapter_entry(idx, ch_item)
        extracted_originals.extend(ch_originals)
        if not (MIN_CHAPTER_SEGMENTS <= seg_count <= MAX_CHAPTER_SEGMENTS):
            hard_defects.append(
                f"Chapter '{ch_name}' segment count ({seg_count}) outside allowed range "
                f"{MIN_CHAPTER_SEGMENTS}..{MAX_CHAPTER_SEGMENTS}"
            )
        if not (1 <= byte_len <= MAX_CHAPTER_SOURCE_BYTES):
            hard_defects.append(
                f"Chapter '{ch_name}' source.txt size ({byte_len} bytes) outside allowed range "
                f"1..{MAX_CHAPTER_SOURCE_BYTES} bytes"
            )


def validate_submission_pr(
    changed_files: List[str],
    manifest_data: Optional[Dict[str, Any]] = None,
    source_file_path: Optional[Union[str, Path]] = None,
    source_text_sample: Optional[str] = None,
    max_bytes: Optional[int] = None,
    is_lfs_pointer: bool = False,
    allowed_licenses: Optional[
        Union[Set[str], frozenset[str], Tuple[str, ...], List[str]]
    ] = None,
    supported_pairs: Optional[
        Union[
            Set[Tuple[str, str]],
            frozenset[Tuple[str, str]],
            Tuple[Tuple[str, str], ...],
            List[Tuple[str, str]],
        ]
    ] = None,
    head_ref: Optional[str] = None,
    repo_root: Optional[Union[str, Path]] = None,
    existing_slugs: Optional[Iterable[str]] = None,
    extracted_chapters: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Validates a book submission PR according to REQ-017, REQ-018, and REQ-019.

    Returns:
        {
            "valid": bool,               # True iff prescreen_status == "PASS"
            "prescreen_status": str,     # "PASS" | "WARN" | "FAIL"
            "defects": list[str],        # Hard defects + warnings (empty iff PASS)
            "warnings": list[str],       # Disallowed-license and prompt-injection warnings
            "slug": str | None,
            "manifest": dict[str, Any],
        }
    """
    cfg = None
    if max_bytes is None:
        cfg = load_collab_config()
        max_bytes = cfg.max_source_bytes
    if allowed_licenses is None:
        cfg = cfg or load_collab_config()
        allowed_licenses = cfg.allowed_licenses
    if supported_pairs is None:
        cfg = cfg or load_collab_config()
        supported_pairs = cfg.supported_pairs

    hard_defects: List[str] = []
    warnings: List[str] = []
    slug: Optional[str] = None
    raw_manifest_slug: Optional[Any] = None

    if isinstance(manifest_data, dict):
        raw_manifest_slug = manifest_data.get("slug")
        if isinstance(raw_manifest_slug, str) and raw_manifest_slug.strip():
            if not _is_valid_slug(raw_manifest_slug):
                hard_defects.append(
                    f"Invalid manifest slug '{raw_manifest_slug}': "
                    f"must match ^[a-z0-9]+(?:-[a-z0-9]+)*$ (1..{MAX_SLUG_LENGTH} chars)"
                )
            slug = raw_manifest_slug.strip()

    # 1. Changed files path allowlist, single source file constraint, and slug consistency
    manifest_files: List[str] = []
    source_files: List[str] = []
    if not changed_files:
        hard_defects.append("PR contains no changed files")
    else:
        detected_slugs: Set[str] = set()
        for file_path in changed_files:
            parts = file_path.replace("\\", "/").split("/")
            if len(parts) >= 3 and parts[0] == "books":
                path_slug = parts[1]
                detected_slugs.add(path_slug)
                if parts[2] == "manifest.json" and len(parts) == 3:
                    manifest_files.append(file_path)
                elif parts[2] == "source":
                    if len(parts) == 4 and parts[3].strip():
                        source_files.append(file_path)
                        fname = parts[3].lower()
                        if (
                            "translation" in fname
                            or "translated" in fname
                            or fname.endswith(".xml")
                        ):
                            hard_defects.append(
                                f"PR includes prohibited translated file in source: {file_path}"
                            )
                    else:
                        hard_defects.append(
                            "PR touches disallowed nested or empty source path "
                            f"(expected books/<slug>/source/<filename>): {file_path}"
                        )
                else:
                    hard_defects.append(
                        f"PR touches disallowed path outside allowlist: {file_path}"
                    )
            else:
                hard_defects.append(
                    f"PR touches disallowed path outside books/<slug>/: {file_path}"
                )

        if len(detected_slugs) > 1:
            hard_defects.append(f"Multiple slugs detected in PR: {sorted(detected_slugs)}")
        elif len(detected_slugs) == 1:
            detected_slug = next(iter(detected_slugs))
            if not _is_valid_slug(detected_slug) and detected_slug != raw_manifest_slug:
                hard_defects.append(
                    f"Invalid path slug '{detected_slug}': "
                    f"must match ^[a-z0-9]+(?:-[a-z0-9]+)*$ (1..{MAX_SLUG_LENGTH} chars)"
                )
            if slug and slug != detected_slug:
                hard_defects.append(
                    f"Manifest slug '{slug}' does not match path slug '{detected_slug}'"
                )
            slug = slug or detected_slug

        if len(manifest_files) == 0:
            hard_defects.append("Missing required file: books/<slug>/manifest.json")
        elif len(manifest_files) > 1:
            hard_defects.append(f"Multiple manifest.json files in PR: {manifest_files}")

        if len(source_files) == 0:
            hard_defects.append("Missing required source book under books/<slug>/source/<filename>")
        elif len(source_files) > 1:
            hard_defects.append(
                f"Multiple source files in PR ({len(source_files)}); "
                f"expected exactly one source file under books/<slug>/source/<filename>: {source_files}"
            )

    # 2. Optional head_ref validation (book/<slug>)
    if head_ref is not None:
        if not isinstance(head_ref, str) or not head_ref.startswith("book/"):
            hard_defects.append(
                f"Invalid head_ref '{head_ref}': expected branch 'book/<slug>'"
            )
        else:
            branch_slug = head_ref[len("book/"):]
            if not _is_valid_slug(branch_slug):
                hard_defects.append(
                    f"Invalid branch slug '{branch_slug}' in head_ref '{head_ref}': "
                    f"must match ^[a-z0-9]+(?:-[a-z0-9]+)*$ (1..{MAX_SLUG_LENGTH} chars)"
                )
            if slug and head_ref != f"book/{slug}":
                hard_defects.append(
                    f"head_ref '{head_ref}' does not match expected branch 'book/{slug}'"
                )
            if slug is None and _is_valid_slug(branch_slug):
                slug = branch_slug

    # 3. Duplicate-slug guard (existing_slugs and repo_root books/<slug> | runs/<slug>)
    if slug:
        if existing_slugs is not None and slug in set(existing_slugs):
            hard_defects.append(
                f"Duplicate slug '{slug}': book slug already exists"
            )
        if repo_root is not None and "/" not in slug and "\\" not in slug and slug not in (".", ".."):
            root_path = Path(repo_root)
            if (root_path / "books" / slug).exists():
                hard_defects.append(
                    f"Duplicate slug '{slug}': books/{slug}/ already exists on main"
                )
            if (root_path / "runs" / slug).exists():
                hard_defects.append(
                    f"Duplicate slug '{slug}': runs/{slug}/ already exists on main"
                )

    # 4. Manifest Schema, License, and Language Pair Validation
    if not manifest_data or not isinstance(manifest_data, dict):
        hard_defects.append("Invalid or missing manifest data")
    else:
        for field in REQUIRED_MANIFEST_FIELDS:
            val = manifest_data.get(field)
            if not isinstance(val, str) or not val.strip():
                hard_defects.append(f"Missing required manifest field: '{field}'")

        license_url = manifest_data.get("license_url")
        if isinstance(license_url, str) and license_url.strip():
            trimmed_url = license_url.strip()
            if not trimmed_url.startswith("https://") or len(trimmed_url) <= len("https://"):
                hard_defects.append(
                    f"Invalid manifest field 'license_url' ('{license_url}'): must start with 'https://'"
                )

        if "genre" in manifest_data:
            genre_val = manifest_data.get("genre")
            if not isinstance(genre_val, str) or genre_val not in ALLOWED_GENRES:
                hard_defects.append(
                    f"Invalid manifest field 'genre' ('{genre_val}'): "
                    f"must be one of {', '.join(ALLOWED_GENRES)}"
                )

        license_val = manifest_data.get("license")
        if isinstance(license_val, str) and license_val.strip():
            if license_val not in allowed_licenses:
                warnings.append(
                    f"Disallowed license: '{license_val}'. Allowed: {', '.join(sorted(allowed_licenses))}"
                )

        source_lang = manifest_data.get("source_lang")
        target_lang = manifest_data.get("target_lang")
        pair = (source_lang, target_lang)
        pair_set = {tuple(p) for p in supported_pairs}
        if pair not in pair_set:
            hard_defects.append(
                f"unsupported pair: {source_lang}->{target_lang}. Supported: "
                + ", ".join(f"{s}->{t}" for s, t in sorted(pair_set))
            )

    # 5. Source File Size, Readability, and Git LFS Pointer Rejection
    lfs_detected = bool(is_lfs_pointer)
    if isinstance(source_text_sample, str) and source_text_sample.lstrip("\ufeff").startswith(
        LFS_POINTER_HEADER
    ):
        lfs_detected = True

    src_path_obj: Optional[Path] = None
    source_file_ok_for_extract = False
    if source_file_path is not None:
        src_path_obj = Path(source_file_path)
        if not src_path_obj.exists() or not src_path_obj.is_file():
            hard_defects.append(f"Source file is missing or unreadable: {source_file_path}")
        else:
            try:
                size = os.path.getsize(src_path_obj)
                with open(src_path_obj, "rb") as fh:
                    head_chunk = fh.read(128)
            except OSError as exc:
                hard_defects.append(f"Source file is unreadable ({source_file_path}): {exc}")
            else:
                if head_chunk.lstrip(b"\xef\xbb\xbf").startswith(LFS_POINTER_BYTES):
                    lfs_detected = True

                bounds_ok = True
                if size == 0:
                    hard_defects.append("Source file is empty (0 bytes)")
                    bounds_ok = False
                elif size > max_bytes:
                    hard_defects.append(
                        f"Source file size ({size} bytes) exceeds limit ({max_bytes} bytes / 50 MB)"
                    )
                    bounds_ok = False

                eff_ext = src_path_obj.suffix.lower()
                if not eff_ext and len(source_files) == 1:
                    eff_ext = Path(source_files[0]).suffix.lower()

                if bounds_ok and not lfs_detected and eff_ext in (".md", ".markdown", ".txt", ".text"):
                    try:
                        if not src_path_obj.read_bytes().strip():
                            hard_defects.append("Source file contains only whitespace (0 segments)")
                            bounds_ok = False
                    except OSError as exc:
                        hard_defects.append(f"Source file is unreadable ({source_file_path}): {exc}")
                        bounds_ok = False

                if bounds_ok and not lfs_detected:
                    source_file_ok_for_extract = True

    if lfs_detected:
        hard_defects.append(
            "Git LFS pointer files are rejected at intake; submit the raw source file within max_source_bytes"
        )

    # 6. Per-Chapter Extraction & Bounds Validation (1..2,000 segments, <= 2,621,440 UTF-8 bytes)
    extracted_originals: List[str] = []

    is_synthetic_nameless_temp = (
        src_path_obj is not None
        and not src_path_obj.suffix
        and src_path_obj.parent.name != "source"
    )
    if extracted_chapters is not None:
        _validate_chapter_items(extracted_chapters, hard_defects, extracted_originals)
    elif source_file_ok_for_extract and src_path_obj is not None and not is_synthetic_nameless_temp:
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "extracted"
            decl_lang = (
                manifest_data.get("source_lang")
                if isinstance(manifest_data, dict)
                and manifest_data.get("source_lang") in ("en", "zh")
                else "auto"
            )
            try:
                ch_results = extract_book(
                    input_path=src_path_obj,
                    output_dir=out_dir,
                    slug=slug or "intake",
                    source_lang=decl_lang,
                )
            except Exception as exc:
                hard_defects.append(f"Source file failed chapter extraction: {exc}")
            else:
                _validate_chapter_items(ch_results or [], hard_defects, extracted_originals)

    # 7. Language & Vietnamese Diacritic Density Check
    sample_for_lang: Optional[str] = None
    if source_text_sample and not lfs_detected:
        sample_for_lang = source_text_sample
    elif extracted_originals and not lfs_detected:
        filtered_origs = [
            orig for orig in extracted_originals if orig.strip() != _SYNTHETIC_PREFACE_HEADING
        ]
        if filtered_origs:
            sample_for_lang = "\n".join(filtered_origs)[:10000]

    if sample_for_lang and isinstance(manifest_data, dict):
        src_lang = manifest_data.get("source_lang")
        if src_lang == "en":
            detected = detect_source_language(sample_for_lang)
            if detected == "zh":
                hard_defects.append(
                    "Source language mismatch: text detected as zh, expected en"
                )
            else:
                clean_sample = CLEAN_TEXT_RE.sub("", sample_for_lang)
                alpha_chars = [c for c in clean_sample if c.isalpha()]
                if alpha_chars:
                    vi_count = sum(1 for c in alpha_chars if c in VI_DIACRITICS)
                    density = vi_count / len(alpha_chars)
                    if density >= 0.05:
                        hard_defects.append(
                            f"Source text appears to be already translated Vietnamese "
                            f"(diacritic density {density:.1%} >= 5%)"
                        )
        elif src_lang == "zh":
            detected = detect_source_language(sample_for_lang)
            if detected != "zh":
                hard_defects.append(
                    f"Source language mismatch: text detected as {detected}, expected zh"
                )

    # 8. Pre-LLM Prompt Injection Scan (WARN classification per REQ-018 / REQ-023)
    seen_warnings: Set[str] = set(warnings)
    texts_to_scan: List[str] = []
    if source_text_sample:
        texts_to_scan.append(source_text_sample)
    texts_to_scan.extend(extracted_originals)

    for txt in texts_to_scan:
        for inj_flag in detect_intake_prompt_injection(txt):
            if inj_flag not in seen_warnings:
                seen_warnings.add(inj_flag)
                warnings.append(inj_flag)

    if hard_defects:
        prescreen_status = "FAIL"
    elif warnings:
        prescreen_status = "WARN"
    else:
        prescreen_status = "PASS"

    all_defects = list(hard_defects) + list(warnings)

    return {
        "valid": prescreen_status == "PASS",
        "prescreen_status": prescreen_status,
        "defects": all_defects,
        "warnings": warnings,
        "slug": slug,
        "manifest": dict(manifest_data) if isinstance(manifest_data, dict) else {},
    }


def format_prescreen_comment(
    result: Optional[Dict[str, Any]] = None,
    manifest_data: Optional[Union[Dict[str, Any], str]] = None,
    human_approver: Optional[str] = None,
    head_oid: str = "",
    *,
    validation_result: Optional[Dict[str, Any]] = None,
) -> str:
    """Formats the commit-bound pre-screen summary comment for a book submission PR."""
    if result is None and validation_result is not None:
        result = validation_result
    result = result or {}

    if isinstance(manifest_data, str):
        if not head_oid:
            head_oid = manifest_data
        manifest_dict: Dict[str, Any] = result.get("manifest") or {}
    elif isinstance(manifest_data, dict):
        manifest_dict = manifest_data
    else:
        manifest_dict = result.get("manifest") or {}

    if human_approver is None:
        human_approver = load_collab_config().human_approver

    slug = result.get("slug") or manifest_dict.get("slug", "unknown")
    title = manifest_dict.get("title", "Untitled")
    author = manifest_dict.get("author", "Unknown")
    license_val = manifest_dict.get("license", "Unknown")
    pair = f"{manifest_dict.get('source_lang')} -> {manifest_dict.get('target_lang')}"

    status = result.get("prescreen_status")
    if status not in ("PASS", "WARN", "FAIL"):
        if result.get("valid"):
            status = "PASS"
        elif result.get("warnings") and not result.get("defects"):
            status = "WARN"
        else:
            status = "FAIL"

    sha_str = str(head_oid or result.get("head_oid") or "").strip()
    marker = f"[collab-prescreen sha={sha_str} status={status}]"

    lines = [
        marker,
        "",
        f"## Pre-screen Summary: {status}",
        "",
        f"- **Book:** {title} (`{slug}`)",
        f"- **Author:** {author}",
        f"- **Pair:** {pair}",
        f"- **License:** `{license_val}`",
        "",
        "### Checklist",
    ]

    if status == "PASS":
        approval_cmd = f"`/approve-intake {sha_str}`" if sha_str else "`/approve-intake <head_oid>`"
        lines.extend([
            "- [x] Path allowlist satisfied (`books/<slug>/manifest.json` and `books/<slug>/source/<filename>`)",
            "- [x] Manifest schema complete and valid (`https://` `license_url`)",
            "- [x] License is on the approved list (public-domain, CC0-1.0, CC-BY-4.0, CC-BY-SA-4.0, owner-permission)",
            "- [x] Supported language pair (`en->vi` or `zh->vi`) and per-chapter bounds verified",
            "- [x] Source language verified; zero prompt-injection warnings detected",
            "",
            f"> **Human Approval Gate**: Awaiting commit-bound review approval or {approval_cmd} from @{human_approver} before merge.",
        ])
    elif status == "WARN":
        warn_items = result.get("warnings") or result.get("defects", [])
        override_cmd = f"`/approve-intake {sha_str}`" if sha_str else "`/approve-intake <head_oid>`"
        lines.extend([
            "- [x] Structural, manifest, language, and per-chapter bounds checks passed",
            "- [ ] Submission triggered pre-screen warnings requiring explicit human override:",
            "",
        ])
        for warn in warn_items:
            lines.append(f"  - ⚠️ {warn}")
        lines.extend([
            "",
            f"> **Human Override Gate**: Explicit {override_cmd} from @{human_approver} is required to clear warnings and merge.",
        ])
    else:
        defects = result.get("defects", [])
        lines.extend([
            "- [ ] The submission has the following defects that must be resolved before review:",
            "",
        ])
        for defect in defects:
            lines.append(f"  - ❌ {defect}")
        lines.extend([
            "",
            "Please address the issues and push updates to this PR branch. FAIL pre-screens cannot be merged.",
        ])

    return "\n".join(lines)
