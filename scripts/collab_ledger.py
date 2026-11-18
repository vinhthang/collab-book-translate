"""Collaborative translation audit ledger with v2.0 rules and v1.0 pilot compatibility (REQ-028)."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

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


def _is_strict_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def _is_valid_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def compute_reviewer_score(
    chapter: str,
    score_of_record: float,
    reviewer_logins: Optional[List[str]] = None,
    valid_reviews: Optional[List[Dict[str, Any]]] = None,
    reviewer_scores_by_login: Optional[Dict[str, Dict[str, float]]] = None,
) -> float:
    """Compute deterministic REQ-028 reviewer_score.

    - On ch01 without independent reviewers: equals float(score_of_record).
    - On ch02..chNN: arithmetic mean across all reviewers in sorted reviewer_logins
      of each reviewer's chronologically latest valid 6-dimension mean score.
    """
    logins = sorted({str(r).strip() for r in (reviewer_logins or []) if str(r).strip()})
    if chapter == "ch01" and not logins:
        return float(score_of_record)

    latest_by_login: Dict[str, Dict[str, float]] = {}
    if reviewer_scores_by_login:
        for k, v in reviewer_scores_by_login.items():
            if isinstance(v, dict) and v:
                latest_by_login[str(k).strip()] = v

    if valid_reviews:
        for rev in valid_reviews:
            if not isinstance(rev, dict):
                continue
            login = str(
                rev.get("author_login")
                or (rev.get("author") or {}).get("login")
                or (rev.get("user") or {}).get("login")
                or ""
            ).strip()
            scores = rev.get("scores")
            if login and isinstance(scores, dict) and scores:
                latest_by_login[login] = scores

    target_logins = logins if logins else sorted(latest_by_login.keys())
    per_reviewer_means: List[float] = []
    for login in target_logins:
        scores_map = latest_by_login.get(login)
        if isinstance(scores_map, dict) and scores_map:
            vals = [float(v) for v in scores_map.values()]
            per_reviewer_means.append(sum(vals) / len(vals))

    if not per_reviewer_means:
        return float(score_of_record)

    return float(sum(per_reviewer_means) / len(per_reviewer_means))


def read_ledger(ledger_path: Union[Path, str]) -> Dict[str, Any]:
    """Read the collab ledger from disk, or return the canonical empty v2.0 structure."""
    p = Path(ledger_path)
    if not p.is_file():
        return {"book_slug": "", "chapters": {}, "released_tag": None}

    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
        if not isinstance(data, dict):
            return {"book_slug": "", "chapters": {}, "released_tag": None}
        data.setdefault("book_slug", "")
        data.setdefault("chapters", {})
        data.setdefault("released_tag", None)
        return data


def _is_grandfathered_pre_v2_ch01(
    ch: str,
    selected_pr: Any,
    released_tag: Any,
    merge_sha: Any,
    glossary_sha: Any,
) -> bool:
    """Return True iff ch01 selected_pr == 0 matches pre-v2.0 published pilot grandfather rule."""
    if ch != "ch01":
        return False
    if not (_is_strict_int(selected_pr) and selected_pr == 0):
        return False
    if not (isinstance(released_tag, str) and bool(released_tag.strip())):
        return False
    if not (isinstance(merge_sha, str) and bool(merge_sha.strip())):
        return False
    return merge_sha == glossary_sha


def append_ledger_entry(
    ledger_path: Union[Path, str],
    book_slug: str,
    entry: Dict[str, Any],
    *,
    valid_reviews: Optional[List[Dict[str, Any]]] = None,
    reviewer_scores_by_login: Optional[Dict[str, Dict[str, float]]] = None,
) -> Dict[str, Any]:
    """Validate, normalize deterministic fields, and atomically write a chapter entry to collab_ledger.json."""
    if not isinstance(book_slug, str) or not book_slug.strip():
        raise ValueError("book_slug must be a non-empty string")
    if not isinstance(entry, dict):
        raise ValueError("entry must be a dict")

    p = Path(ledger_path)
    ledger = read_ledger(p)

    normalized = dict(entry)
    ch = normalized.get("chapter")
    if not isinstance(ch, str) or not ch.strip():
        raise ValueError("Field 'chapter' must be a non-empty string")
    ch = ch.strip()
    normalized["chapter"] = ch

    if (
        (valid_reviews or reviewer_scores_by_login or normalized.get("reviewer_score") is None)
        and normalized.get("score_of_record") is not None
    ):
        raw_revs = normalized.get("reviewer_logins")
        normalized["reviewer_score"] = compute_reviewer_score(
            chapter=ch,
            score_of_record=float(normalized["score_of_record"]),
            reviewer_logins=raw_revs if isinstance(raw_revs, list) else None,
            valid_reviews=valid_reviews,
            reviewer_scores_by_login=reviewer_scores_by_login,
        )

    for field in REQUIRED_LEDGER_FIELDS:
        if field not in normalized or normalized[field] is None:
            raise ValueError(f"Entry missing required field '{field}'")

    translator_login = normalized.get("translator_login")
    if not isinstance(translator_login, str) or not translator_login.strip():
        raise ValueError("Field 'translator_login' must be a non-empty string")
    translator_login = translator_login.strip()
    normalized["translator_login"] = translator_login

    raw_reviewer_logins = normalized.get("reviewer_logins")
    if not isinstance(raw_reviewer_logins, list) or any(
        not isinstance(r, str) or not r.strip() for r in raw_reviewer_logins
    ):
        raise ValueError("Field 'reviewer_logins' must be a list of non-empty strings")
    sorted_reviewers = sorted({r.strip() for r in raw_reviewer_logins})
    if translator_login in sorted_reviewers:
        raise ValueError(f"translator_login '{translator_login}' cannot appear in 'reviewer_logins'")
    if ch != "ch01" and len(sorted_reviewers) == 0:
        raise ValueError(f"Chapter '{ch}' requires at least one reviewer in 'reviewer_logins'")
    normalized["reviewer_logins"] = sorted_reviewers

    raw_rejected = normalized.get("rejected_prs")
    if not isinstance(raw_rejected, list) or any(
        not _is_strict_int(pr_num) or pr_num <= 0 for pr_num in raw_rejected
    ):
        raise ValueError("Field 'rejected_prs' must be a list of positive integers")
    normalized["rejected_prs"] = sorted(set(raw_rejected))

    merge_sha = normalized.get("merge_sha")
    if not isinstance(merge_sha, str) or not merge_sha.strip():
        raise ValueError("Field 'merge_sha' must be a non-empty string")
    normalized["merge_sha"] = merge_sha.strip()

    glossary_sha = normalized.get("glossary_sha")
    if not isinstance(glossary_sha, str) or not glossary_sha.strip():
        raise ValueError("Field 'glossary_sha' must be a non-empty string")
    normalized["glossary_sha"] = glossary_sha.strip()

    if normalized.get("verdict") != "PASS":
        raise ValueError(f"Field 'verdict' must be 'PASS', got '{normalized.get('verdict')}'")

    issue_num = normalized.get("issue_number")
    if ch == "ch01":
        if issue_num != "seed" and not (_is_strict_int(issue_num) and issue_num >= 0):
            raise ValueError(f"Chapter ch01 has invalid issue_number: {issue_num}")
    else:
        if not (_is_strict_int(issue_num) and issue_num > 0):
            raise ValueError(f"Chapter '{ch}' issue_number must be > 0 (got {issue_num})")

    selected_pr = normalized.get("selected_pr")
    released_tag = ledger.get("released_tag")
    if not (_is_strict_int(selected_pr) and selected_pr > 0):
        if not _is_grandfathered_pre_v2_ch01(
            ch, selected_pr, released_tag, normalized["merge_sha"], normalized["glossary_sha"]
        ):
            raise ValueError(f"Chapter '{ch}' selected_pr must be > 0 (got {selected_pr})")

    if normalized["selected_pr"] in normalized["rejected_prs"]:
        raise ValueError("selected_pr cannot also appear in rejected_prs")

    if not _is_valid_number(normalized.get("score_of_record")) or not (0.0 <= float(normalized["score_of_record"]) <= 5.0):
        raise ValueError("Field 'score_of_record' must be a float in [0.0, 5.0]")
    if not _is_valid_number(normalized.get("reviewer_score")) or not (0.0 <= float(normalized["reviewer_score"]) <= 5.0):
        raise ValueError("Field 'reviewer_score' must be a float in [0.0, 5.0]")

    normalized["score_of_record"] = float(normalized["score_of_record"])
    normalized["reviewer_score"] = float(normalized["reviewer_score"])

    if ch == "ch01" and len(sorted_reviewers) == 0:
        if abs(normalized["reviewer_score"] - normalized["score_of_record"]) > 1e-6:
            raise ValueError("On ch01 without reviewers, reviewer_score must equal score_of_record")

    ledger["book_slug"] = book_slug.strip()
    ledger.setdefault("released_tag", None)
    ledger["chapters"][ch] = normalized

    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(p, ledger)
    return ledger


def verify_ledger_integrity(
    ledger_data: Dict[str, Any],
    expected_chapters: List[str],
) -> Dict[str, Any]:
    """Verify top-level schema, v2.0/v1.0 chapter provenance rules, and completeness of collab_ledger.json."""
    unaudited_chapters: List[str] = []
    defects: List[str] = []

    def _flag_chapter(ch_id: str) -> None:
        if ch_id not in unaudited_chapters:
            unaudited_chapters.append(ch_id)

    if not isinstance(ledger_data, dict):
        for ch in expected_chapters:
            _flag_chapter(ch)
        return {
            "valid": False,
            "unaudited_chapters": unaudited_chapters,
            "defects": ["Ledger top-level payload must be a JSON object."],
        }

    book_slug = ledger_data.get("book_slug")
    if not isinstance(book_slug, str) or not book_slug.strip():
        defects.append("Ledger top-level 'book_slug' must be a non-empty string.")
        for ch in expected_chapters:
            _flag_chapter(ch)

    released_tag = ledger_data.get("released_tag")
    if released_tag is not None and (not isinstance(released_tag, str) or not released_tag.strip()):
        defects.append("Ledger top-level 'released_tag' must be null or a non-empty string.")
        for ch in expected_chapters:
            _flag_chapter(ch)

    chapters = ledger_data.get("chapters")
    if not isinstance(chapters, dict):
        defects.append("Ledger top-level 'chapters' must be a JSON object.")
        for ch in expected_chapters:
            _flag_chapter(ch)
        return {
            "valid": False,
            "unaudited_chapters": unaudited_chapters,
            "defects": defects,
        }

    for ch in expected_chapters:
        if ch not in chapters:
            _flag_chapter(ch)
            defects.append(f"Chapter {ch} is missing from ledger.")
            continue

        entry = chapters[ch]
        if not isinstance(entry, dict):
            _flag_chapter(ch)
            defects.append(f"Chapter {ch} ledger entry must be an object.")
            continue

        ch_defects_before = len(defects)

        for field in REQUIRED_LEDGER_FIELDS:
            if field not in entry or entry[field] is None:
                defects.append(f"Chapter {ch} missing required field '{field}'")

        if entry.get("chapter") != ch:
            defects.append(f"Chapter {ch} entry field 'chapter' mismatch: {entry.get('chapter')}")

        if entry.get("verdict") != "PASS":
            defects.append(f"Chapter {ch} verdict must be 'PASS' (got {entry.get('verdict')})")

        m_sha = entry.get("merge_sha")
        if not isinstance(m_sha, str) or not m_sha.strip():
            defects.append(f"Chapter {ch} has empty or invalid merge_sha")

        g_sha = entry.get("glossary_sha")
        if not isinstance(g_sha, str) or not g_sha.strip():
            defects.append(f"Chapter {ch} has empty or invalid glossary_sha")

        translator_login = entry.get("translator_login")
        if not isinstance(translator_login, str) or not translator_login.strip():
            defects.append(f"Chapter {ch} has empty or invalid translator_login")

        rejected_prs = entry.get("rejected_prs")
        if not isinstance(rejected_prs, list) or any(
            not _is_strict_int(p) or p <= 0 for p in rejected_prs
        ):
            defects.append(f"Chapter {ch} rejected_prs must be a list of positive integers")
        elif rejected_prs != sorted(set(rejected_prs)):
            defects.append(f"Chapter {ch} rejected_prs must be sorted ascending without duplicates")

        score_rec = entry.get("score_of_record")
        rev_score = entry.get("reviewer_score")
        if not _is_valid_number(score_rec) or not (0.0 <= float(score_rec) <= 5.0):
            defects.append(f"Chapter {ch} has invalid score_of_record: {score_rec}")
        if not _is_valid_number(rev_score) or not (0.0 <= float(rev_score) <= 5.0):
            defects.append(f"Chapter {ch} has invalid reviewer_score: {rev_score}")

        issue_num = entry.get("issue_number")
        selected_pr = entry.get("selected_pr")
        reviewer_logins = entry.get("reviewer_logins")

        if not isinstance(reviewer_logins, list) or any(
            not isinstance(r, str) or not r.strip() for r in reviewer_logins
        ):
            defects.append(f"Chapter {ch} reviewer_logins must be a list of non-empty strings")
        else:
            if isinstance(translator_login, str) and translator_login in reviewer_logins:
                defects.append(
                    f"Chapter {ch} translator_login '{translator_login}' cannot review own PR"
                )
            if reviewer_logins != sorted(set(reviewer_logins)):
                defects.append(f"Chapter {ch} reviewer_logins must be sorted ascending without duplicates")
            if ch != "ch01" and len(reviewer_logins) == 0:
                defects.append(f"Chapter {ch} must have non-empty reviewer_logins")
            if (
                ch == "ch01"
                and len(reviewer_logins) == 0
                and _is_valid_number(score_rec)
                and _is_valid_number(rev_score)
                and abs(float(rev_score) - float(score_rec)) > 1e-6
            ):
                defects.append(f"Chapter ch01 reviewer_score ({rev_score}) must equal score_of_record ({score_rec})")

        if ch == "ch01":
            if issue_num != "seed" and not (_is_strict_int(issue_num) and issue_num >= 0):
                defects.append(f"Chapter ch01 has invalid issue_number: {issue_num}")
            if not (_is_strict_int(selected_pr) and selected_pr > 0):
                if not _is_grandfathered_pre_v2_ch01(ch, selected_pr, released_tag, m_sha, g_sha):
                    defects.append(
                        f"Chapter ch01 selected_pr must be > 0 unless grandfathered by non-empty released_tag and merge_sha == glossary_sha (got {selected_pr})"
                    )
        else:
            if not (_is_strict_int(issue_num) and issue_num > 0):
                defects.append(f"Chapter {ch} issue_number must be > 0 (got {issue_num})")
            if not (_is_strict_int(selected_pr) and selected_pr > 0):
                defects.append(f"Chapter {ch} selected_pr must be > 0 (got {selected_pr})")

        if (
            isinstance(rejected_prs, list)
            and _is_strict_int(selected_pr)
            and selected_pr > 0
            and selected_pr in rejected_prs
        ):
            defects.append(f"Chapter {ch} selected_pr ({selected_pr}) cannot appear in rejected_prs")

        if len(defects) > ch_defects_before:
            _flag_chapter(ch)

    return {
        "valid": len(unaudited_chapters) == 0 and len(defects) == 0,
        "unaudited_chapters": unaudited_chapters,
        "defects": defects,
    }
