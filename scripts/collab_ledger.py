"""Collaborative translation audit ledger (REQ-COLLAB-012)."""

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

from scripts.helper import atomic_write_json

REQUIRED_LEDGER_FIELDS: Tuple[str, ...] = (
    "chapter",
    "issue_number",
    "selected_pr",
    "rejected_prs",
    "translator_login",
    "reviewer_logins",
    "reviewer_score",
    "score_of_record",
    "verdict",
    "merge_sha",
    "glossary_sha",
)


def read_ledger(ledger_path: Union[Path, str]) -> Dict[str, Any]:
    """Read the collab ledger from disk, or return a default empty structure."""
    p = Path(ledger_path)
    if not p.is_file():
        return {"book_slug": "", "chapters": {}}

    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
        if not isinstance(data, dict):
            return {"book_slug": "", "chapters": {}}
        data.setdefault("book_slug", "")
        data.setdefault("chapters", {})
        return data


def append_ledger_entry(
    ledger_path: Union[Path, str],
    book_slug: str,
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    """Validate and atomically append a chapter entry to the audit ledger."""
    p = Path(ledger_path)

    for field in REQUIRED_LEDGER_FIELDS:
        if field not in entry or entry[field] is None:
            raise ValueError(f"Entry missing required field '{field}'")

    ch = entry["chapter"]
    if not isinstance(ch, str) or not ch.strip():
        raise ValueError("Field 'chapter' must be a non-empty string")

    reviewer_logins = entry.get("reviewer_logins")
    if not isinstance(reviewer_logins, list):
        raise ValueError("Field 'reviewer_logins' must be a list")

    if ch != "ch01" and len(reviewer_logins) == 0:
        raise ValueError(f"Chapter '{ch}' requires at least one reviewer in 'reviewer_logins'")

    merge_sha = entry.get("merge_sha")
    if not isinstance(merge_sha, str) or not merge_sha.strip():
        raise ValueError("Field 'merge_sha' must be a non-empty string")

    glossary_sha = entry.get("glossary_sha")
    if not isinstance(glossary_sha, str) or not glossary_sha.strip():
        raise ValueError("Field 'glossary_sha' must be a non-empty string")

    ledger = read_ledger(p)
    if book_slug and not ledger.get("book_slug"):
        ledger["book_slug"] = book_slug

    ledger["chapters"][ch] = entry

    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(p, ledger)
    return ledger


def verify_ledger_integrity(
    ledger_data: Dict[str, Any],
    expected_chapters: List[str],
) -> Dict[str, Any]:
    """Verify the completeness and provenance integrity of the audit ledger."""
    chapters = ledger_data.get("chapters", {})
    unaudited_chapters: List[str] = []
    defects: List[str] = []

    for ch in expected_chapters:
        if ch not in chapters:
            unaudited_chapters.append(ch)
            defects.append(f"Chapter {ch} is missing from ledger.")
            continue

        entry = chapters[ch]
        for field in REQUIRED_LEDGER_FIELDS:
            if field not in entry or entry[field] is None:
                defects.append(f"Chapter {ch} missing required field '{field}'")

        # Validate merge_sha and glossary_sha
        m_sha = entry.get("merge_sha")
        if not isinstance(m_sha, str) or not m_sha.strip():
            defects.append(f"Chapter {ch} has empty or invalid merge_sha")

        g_sha = entry.get("glossary_sha")
        if not isinstance(g_sha, str) or not g_sha.strip():
            defects.append(f"Chapter {ch} has empty or invalid glossary_sha")

        # Validate chapter-specific rules
        issue_num = entry.get("issue_number")
        selected_pr = entry.get("selected_pr")
        reviewer_logins = entry.get("reviewer_logins", [])
        translator_login = entry.get("translator_login")

        if ch == "ch01":
            # ch01 seed: issue_number may be 0 or "seed" or positive int
            if issue_num != "seed" and not (isinstance(issue_num, int) and issue_num >= 0):
                defects.append(f"Chapter ch01 has invalid issue_number: {issue_num}")
        else:
            # ch02..chNN rules:
            # issue_number must be > 0
            if not (isinstance(issue_num, int) and issue_num > 0):
                defects.append(f"Chapter {ch} issue_number must be > 0 (got {issue_num})")

            # selected_pr must be > 0
            if not (isinstance(selected_pr, int) and selected_pr > 0):
                defects.append(f"Chapter {ch} selected_pr must be > 0 (got {selected_pr})")

            # reviewer_logins non-empty
            if not isinstance(reviewer_logins, list) or len(reviewer_logins) == 0:
                defects.append(f"Chapter {ch} must have non-empty reviewer_logins")
            elif translator_login in reviewer_logins:
                defects.append(
                    f"Chapter {ch} translator_login '{translator_login}' cannot review own PR"
                )

    return {
        "valid": len(unaudited_chapters) == 0 and len(defects) == 0,
        "unaudited_chapters": unaudited_chapters,
        "defects": defects,
    }
