"""
scripts/collab_intake.py — Book submission intake and pre-screen validation.

Implements REQ-COLLAB-001, REQ-COLLAB-002, REQ-COLLAB-007:
- Path allowlist: books/<slug>/manifest.json and books/<slug>/source/**
- Manifest validation: required fields, allowed licences, supported pairs
- Source language validation: detect_source_language + Vietnamese diacritic density guard
- Pre-screen comment formatting for GitHub PRs
"""

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from scripts.evaluators.fallback import VI_DIACRITICS
from scripts.utils.language import detect_source_language

ALLOWED_LICENSES: Tuple[str, ...] = (
    "public-domain",
    "CC0-1.0",
    "CC-BY-4.0",
    "CC-BY-SA-4.0",
    "owner-permission",
)

SUPPORTED_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("en", "vi"),
    ("zh", "vi"),
)

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

DEFAULT_MAX_BYTES: int = 50 * 1024 * 1024  # 50 MB


def validate_submission_pr(
    changed_files: List[str],
    manifest_data: Optional[Dict[str, Any]] = None,
    source_file_path: Optional[str] = None,
    source_text_sample: Optional[str] = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    is_lfs_pointer: bool = False,
) -> Dict[str, Any]:
    """Validates a book submission PR according to REQ-COLLAB-001, 002, and 007.

    Returns:
        {
            "valid": bool,
            "defects": list[str],
            "slug": str | None
        }
    """
    defects: List[str] = []
    slug: Optional[str] = None

    if manifest_data and isinstance(manifest_data, dict):
        raw_slug = manifest_data.get("slug")
        if isinstance(raw_slug, str) and raw_slug.strip():
            slug = raw_slug.strip()

    # 1. Changed files path allowlist and slug consistency
    if not changed_files:
        defects.append("PR contains no changed files")
    else:
        detected_slugs = set()
        has_manifest = False
        has_source = False

        for file_path in changed_files:
            parts = file_path.replace("\\", "/").split("/")
            if len(parts) >= 3 and parts[0] == "books":
                path_slug = parts[1]
                detected_slugs.add(path_slug)
                if parts[2] == "manifest.json" and len(parts) == 3:
                    has_manifest = True
                elif parts[2] == "source" and len(parts) >= 4:
                    has_source = True
                    # Check for translated artifacts mistakenly included
                    fname = parts[-1].lower()
                    if "translation" in fname or "translated" in fname or fname.endswith(".xml"):
                        defects.append(f"PR includes prohibited translated file in source: {file_path}")
                else:
                    defects.append(f"PR touches disallowed path outside allowlist: {file_path}")
            else:
                defects.append(f"PR touches disallowed path outside books/<slug>/: {file_path}")

        if len(detected_slugs) > 1:
            defects.append(f"Multiple slugs detected in PR: {sorted(detected_slugs)}")
        elif len(detected_slugs) == 1:
            detected_slug = next(iter(detected_slugs))
            if slug and slug != detected_slug:
                defects.append(f"Manifest slug '{slug}' does not match path slug '{detected_slug}'")
            slug = slug or detected_slug

        if not has_manifest:
            defects.append("Missing required file: books/<slug>/manifest.json")
        if not has_source:
            defects.append("Missing required source book under books/<slug>/source/")

    # 2. Manifest Schema Validation
    if not manifest_data or not isinstance(manifest_data, dict):
        defects.append("Invalid or missing manifest data")
    else:
        for field in REQUIRED_MANIFEST_FIELDS:
            val = manifest_data.get(field)
            if val is None or (isinstance(val, str) and not val.strip()):
                defects.append(f"Missing required manifest field: '{field}'")

        license_val = manifest_data.get("license")
        if license_val not in ALLOWED_LICENSES:
            defects.append(
                f"Disallowed license: '{license_val}'. Allowed: {', '.join(ALLOWED_LICENSES)}"
            )

        source_lang = manifest_data.get("source_lang")
        target_lang = manifest_data.get("target_lang")
        pair = (source_lang, target_lang)
        if pair not in SUPPORTED_PAIRS:
            defects.append(
                f"unsupported pair: {source_lang}->{target_lang}. Supported: "
                + ", ".join(f"{s}->{t}" for s, t in SUPPORTED_PAIRS)
            )

    # 3. Source File Size Check
    if source_file_path and os.path.exists(source_file_path):
        size = os.path.getsize(source_file_path)
        if size > max_bytes and not is_lfs_pointer:
            defects.append(
                f"Source file size ({size} bytes) exceeds limit ({max_bytes} bytes / 50 MB) without Git LFS pointer"
            )

    # 4. Language & Vietnamese Diacritic Check
    if source_text_sample and manifest_data:
        src_lang = manifest_data.get("source_lang")
        if src_lang == "en":
            detected = detect_source_language(source_text_sample)
            if detected == "zh":
                defects.append("Source language mismatch: text detected as zh, expected en")
            else:
                non_space = [c for c in source_text_sample if not c.isspace()]
                if non_space:
                    vi_count = sum(1 for c in non_space if c in VI_DIACRITICS)
                    density = vi_count / len(non_space)
                    if density >= 0.05:
                        defects.append(
                            f"Source text appears to be already translated Vietnamese (diacritic density {density:.1%} >= 5%)"
                        )
        elif src_lang == "zh":
            detected = detect_source_language(source_text_sample)
            if detected != "zh":
                defects.append(f"Source language mismatch: text detected as {detected}, expected zh")

    return {
        "valid": len(defects) == 0,
        "defects": defects,
        "slug": slug,
    }


def format_prescreen_comment(result: Dict[str, Any], manifest_data: Dict[str, Any]) -> str:
    """Formats the automated pre-screen comment for a book submission PR."""
    slug = result.get("slug") or manifest_data.get("slug", "unknown")
    title = manifest_data.get("title", "Untitled")
    author = manifest_data.get("author", "Unknown")
    license_val = manifest_data.get("license", "Unknown")
    pair = f"{manifest_data.get('source_lang')} -> {manifest_data.get('target_lang')}"

    status = "PASS" if result.get("valid") else "FAIL"

    lines = [
        f"## Pre-screen Summary: {status}",
        "",
        f"- **Book:** {title} (`{slug}`)",
        f"- **Author:** {author}",
        f"- **Pair:** {pair}",
        f"- **License:** `{license_val}`",
        "",
        "### Checklist",
    ]

    defects = result.get("defects", [])
    if not defects:
        lines.extend([
            "- [x] Path allowlist satisfied (`books/<slug>/manifest.json` and `books/<slug>/source/**`)",
            "- [x] Manifest schema complete and valid",
            "- [x] License is on the approved list (public-domain, CC0-1.0, CC-BY-4.0, CC-BY-SA-4.0, owner-permission)",
            "- [x] Supported language pair (`en->vi` or `zh->vi`)",
            "- [x] Source language verified; no already-translated Vietnamese text detected",
            "",
            "> **Human Approval Gate**: Awaiting explicit review approval from @vinhthang before merge.",
        ])
    else:
        lines.extend([
            "- [ ] The submission has the following defects that must be resolved before review:",
            "",
        ])
        for defect in defects:
            lines.append(f"  - ❌ {defect}")
        lines.extend([
            "",
            "Please address the issues and push updates to this PR branch.",
        ])

    return "\n".join(lines)
