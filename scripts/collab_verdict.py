"""Collaborative translation verdict normalization, review-block clearing, and routing (REQ-023, REQ-024)."""

import datetime
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from scripts.collab_pr_validate import detect_prompt_injection, parse_review_rubric_scores
from scripts.evaluators.anti_convert import check_anti_convert_issues
from scripts.evaluators.scoring import compute_weighted_total
from scripts.evaluate import enforce_language_detection
from scripts.helper import check_redraft, resolve_pass_threshold

CANONICAL_DIMENSIONS: Tuple[str, ...] = (
    "fidelity",
    "fluency",
    "terminology",
    "register",
    "structure",
    "completeness",
)

_SEGMENT_ANCHOR_RE = re.compile(r"\bS-\d{4}\b")
_DISMISS_REVIEW_RE = re.compile(
    r"(?:^|\n)\s*/dismiss-stale-review\s+@?(\S+)\s+(\S+)(?:\s|$)"
)
_CLEAR_FLAG_RE = re.compile(r"(?:^|\n)\s*/clear-flag\s+(\S+)(?:\s|$)")
_REJECT_FLAG_RE = re.compile(r"(?:^|\n)\s*/reject-flag\s+(\S+)(?:\s|$)")


def _clamp_score(val: Any, default: float = 0.0) -> float:
    try:
        num = float(val)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(5.0, num))


def _extract_login(item: Dict[str, Any]) -> str:
    if not isinstance(item, dict):
        return ""
    return str(
        item.get("author_login")
        or (item.get("author") or {}).get("login")
        or (item.get("user") or {}).get("login")
        or ""
    ).strip()


def _extract_commit_oid(review: Dict[str, Any]) -> str:
    if not isinstance(review, dict):
        return ""
    return str(
        review.get("commit_oid")
        or review.get("head_oid")
        or (review.get("commit") or {}).get("oid")
        or ""
    ).strip()


def _parse_optional_ts(item: Dict[str, Any]) -> Optional[float]:
    if not isinstance(item, dict):
        return None
    for key in ("submitted_at", "submittedAt", "created_at", "createdAt", "timestamp"):
        raw = item.get(key)
        if raw is None:
            continue
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str) and raw.strip():
            s = raw.strip()
            try:
                return float(s)
            except ValueError:
                pass
            try:
                dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
                return dt.timestamp()
            except ValueError:
                pass
    return None


def _is_valid_req022_review(review: Dict[str, Any], pr_author: str) -> bool:
    """Check whether a review satisfies REQ-022 independent rubric completeness."""
    rev_login = _extract_login(review)
    if not rev_login or (pr_author and rev_login == pr_author):
        return False

    if review.get("valid") is True:
        return True

    body = str(review.get("body") or "")
    if detect_prompt_injection(body):
        return False

    line_comments = int(review.get("line_comment_count") or 0)
    has_anchor = line_comments >= 1 or bool(_SEGMENT_ANCHOR_RE.search(body))
    if not has_anchor:
        return False

    scores: Dict[str, float] = {}
    if isinstance(review.get("scores"), dict):
        for k, v in review["scores"].items():
            try:
                scores[str(k).lower()] = float(v)
            except (TypeError, ValueError):
                return False

    body_scores, body_defects = parse_review_rubric_scores(body)
    if any(
        "Invalid rubric scale" in d or "outside [1.0, 5.0]" in d or "Invalid numeric score" in d
        for d in body_defects
    ):
        return False
    scores.update(body_scores)

    for dim in CANONICAL_DIMENSIONS:
        if dim not in scores or not (1.0 <= scores[dim] <= 5.0):
            return False
    return True


_REVIEW_ISSUE_RE = re.compile(
    r"(?i)\[(critical|major|minor)\](?:\s*\[?(S-\d{4})\]?)?\s*([^\n]+)"
)


def filter_fresh_valid_reviews(
    reviews: Optional[List[Dict[str, Any]]],
    pr_author: str = "",
    head_oid: str = "",
) -> List[Dict[str, Any]]:
    """Filter to non-author REQ-022-valid reviews whose commit_oid matches head_oid, keeping latest per reviewer."""
    if not reviews:
        return []
    author = (pr_author or "").strip()
    head = (head_oid or "").strip()

    indexed = [r for r in reviews if isinstance(r, dict)]
    if not indexed:
        return []

    by_reviewer: Dict[str, List[Dict[str, Any]]] = {}
    for rev in indexed:
        login = _extract_login(rev)
        if not login or (author and login == author):
            continue
        if not _is_valid_req022_review(rev, author):
            continue
        r_oid = _extract_commit_oid(rev)
        if head and r_oid and r_oid != head:
            continue
        by_reviewer.setdefault(login, []).append(rev)

    fresh: List[Dict[str, Any]] = []
    for login, rev_list in by_reviewer.items():
        sorted_revs = sorted(
            enumerate(rev_list),
            key=lambda pair: (_parse_optional_ts(pair[1]) or 0.0, pair[0]),
        )
        latest_rev = sorted_revs[-1][1]
        fresh.append(latest_rev)

    return fresh


def evaluate_pr_from_reviews(
    reviews: Optional[List[Dict[str, Any]]],
    pr_author: str = "",
    head_oid: str = "",
    translation_xml: str = "",
    book_record: Optional[Dict[str, Any]] = None,
    source_lang: str = "en",
    pass_threshold: Optional[float] = None,
) -> Dict[str, Any]:
    """Compute EvaluationResult and score_of_record deterministically from fresh peer reviews (REQ-032)."""
    fresh_reviews = filter_fresh_valid_reviews(reviews, pr_author, head_oid)
    effective_threshold = resolve_pass_threshold(cli_threshold=pass_threshold, book_record=book_record)

    if not fresh_reviews:
        return {
            "verdict": "NEEDS_REVIEW",
            "outcome": "NEEDS_REVIEW",
            "weighted_total": 0.0,
            "score_of_record": 0.0,
            "effective_threshold": effective_threshold,
            "fallback_unverified": False,
            "dimension_scores": {d: 0.0 for d in CANONICAL_DIMENSIONS},
            "issues": [],
            "critical_issue_count": 0,
            "major_issue_count": 0,
            "minor_issue_count": 0,
            "critical_count": 0,
            "major_count": 0,
            "minor_count": 0,
            "reviews_count": 0,
            "fresh_reviews": [],
        }

    dim_sums: Dict[str, float] = {d: 0.0 for d in CANONICAL_DIMENSIONS}
    dim_counts: Dict[str, int] = {d: 0 for d in CANONICAL_DIMENSIONS}
    parsed_issues: List[Dict[str, Any]] = []

    for rev in fresh_reviews:
        body = str(rev.get("body") or "")
        scores: Dict[str, float] = {}
        if isinstance(rev.get("scores"), dict):
            for k, v in rev["scores"].items():
                try:
                    scores[str(k).lower()] = float(v)
                except (TypeError, ValueError):
                    pass
        body_scores, _ = parse_review_rubric_scores(body)
        scores.update(body_scores)

        for d in CANONICAL_DIMENSIONS:
            if d in scores:
                dim_sums[d] += _clamp_score(scores[d], default=0.0)
                dim_counts[d] += 1

        if isinstance(rev.get("issues"), list):
            for iss in rev["issues"]:
                if isinstance(iss, dict):
                    parsed_issues.append(dict(iss))

        for m in _REVIEW_ISSUE_RE.finditer(body):
            sev = m.group(1).lower()
            seg = m.group(2) or ""
            prob = m.group(3).strip()
            parsed_issues.append({
                "severity": sev,
                "segment_id": seg,
                "problem": prob,
                "suggestion": "",
                "dimension": "completeness" if sev == "critical" else "fidelity",
            })

    dimension_scores: Dict[str, float] = {}
    for d in CANONICAL_DIMENSIONS:
        if dim_counts[d] > 0:
            dimension_scores[d] = round(dim_sums[d] / dim_counts[d], 2)
        else:
            dimension_scores[d] = 0.0

    weighted_total = compute_weighted_total(dimension_scores)

    result_data: Dict[str, Any] = {
        "verdict": "PASS" if weighted_total >= effective_threshold else "FAIL",
        "weighted_total": weighted_total,
        "dimensions": dimension_scores,
        "issues": parsed_issues,
    }

    if translation_xml:
        enforce_language_detection(result_data, translation_xml, source_lang=source_lang)
        if source_lang == "zh":
            ac_issues = check_anti_convert_issues(translation_xml, source_lang="zh")
            if ac_issues:
                result_data.setdefault("issues", []).extend(ac_issues)
                dims = result_data.setdefault("dimensions", dimension_scores)
                dims["terminology"] = min(dims.get("terminology", 5.0), 2.0)
                result_data["weighted_total"] = compute_weighted_total(dims)
                result_data["verdict"] = "FAIL"

    final_dims = result_data.get("dimensions", dimension_scores)
    final_weighted = round(float(result_data.get("weighted_total", weighted_total)), 2)
    all_issues = result_data.get("issues", parsed_issues)

    critical_count = sum(1 for i in all_issues if str(i.get("severity") or "").lower() == "critical")
    major_count = sum(1 for i in all_issues if str(i.get("severity") or "").lower() == "major")
    minor_count = sum(1 for i in all_issues if str(i.get("severity") or "").lower() == "minor")

    if critical_count > 0 or final_weighted < effective_threshold or str(result_data.get("verdict")).upper() == "FAIL":
        verdict = "FAIL"
    else:
        verdict = "PASS"

    return {
        "verdict": verdict,
        "weighted_total": final_weighted,
        "score_of_record": final_weighted,
        "effective_threshold": effective_threshold,
        "fallback_unverified": False,
        "dimension_scores": final_dims,
        "issues": all_issues,
        "critical_issue_count": critical_count,
        "major_issue_count": major_count,
        "minor_issue_count": minor_count,
        "critical_count": critical_count,
        "major_count": major_count,
        "minor_count": minor_count,
        "reviews_count": len(fresh_reviews),
        "fresh_reviews": fresh_reviews,
    }


def normalize_evaluation_result(
    eval_res: Dict[str, Any],
    effective_threshold: float = 4.0,
) -> Dict[str, Any]:
    """Normalize an evaluator payload to the normative REQ-024 EvaluationResult contract."""
    if not isinstance(eval_res, dict):
        raise ValueError("eval_res must be a dict")

    raw_total = eval_res.get("weighted_total")
    if raw_total is None:
        raw_total = eval_res.get("score_of_record")
    if raw_total is None:
        raw_total = eval_res.get("overall_score")
    weighted_total = _clamp_score(raw_total, default=0.0)

    fallback_unverified = bool(
        eval_res.get("fallback_unverified") is True
        or eval_res.get("provenance") == "fallback"
    )

    raw_dims: Dict[str, Any] = {}
    for key in ("dimension_scores", "scores", "dimensions", "dimension_medians"):
        cand = eval_res.get(key)
        if isinstance(cand, dict) and cand:
            raw_dims = cand
            break

    lowered_dims = {str(k).strip().lower(): v for k, v in raw_dims.items()}
    dimension_scores: Dict[str, float] = {}
    for dim in CANONICAL_DIMENSIONS:
        if dim in lowered_dims and lowered_dims[dim] is not None:
            dimension_scores[dim] = _clamp_score(lowered_dims[dim], default=weighted_total)
        else:
            dimension_scores[dim] = weighted_total

    normalized_issues: List[Dict[str, Any]] = []
    raw_issues = eval_res.get("issues")
    if isinstance(raw_issues, list):
        for iss in raw_issues:
            if not isinstance(iss, dict):
                continue
            sev_raw = str(iss.get("severity") or "minor").strip().lower()
            sev = sev_raw if sev_raw in ("critical", "major", "minor") else "minor"
            prob = str(iss.get("problem") if iss.get("problem") is not None else (iss.get("description") or ""))
            sugg = str(iss.get("suggestion") or "")
            seg_id = str(iss.get("segment_id") or iss.get("id") or "")
            dim_val = str(iss.get("dimension") or "").strip().lower()

            norm_item = dict(iss)
            norm_item["segment_id"] = seg_id
            norm_item["dimension"] = dim_val
            norm_item["severity"] = sev
            norm_item["problem"] = prob
            norm_item["suggestion"] = sugg
            normalized_issues.append(norm_item)

    if isinstance(eval_res.get("critical_issues"), list):
        for c_iss in eval_res["critical_issues"]:
            if isinstance(c_iss, dict):
                normalized_issues.append({
                    **c_iss,
                    "segment_id": str(c_iss.get("segment_id") or ""),
                    "dimension": str(c_iss.get("dimension") or "").lower(),
                    "severity": "critical",
                    "problem": str(c_iss.get("problem") or c_iss.get("description") or "critical issue"),
                    "suggestion": str(c_iss.get("suggestion") or ""),
                })
            elif c_iss:
                normalized_issues.append({
                    "segment_id": "",
                    "dimension": "",
                    "severity": "critical",
                    "problem": str(c_iss),
                    "suggestion": "",
                })

    critical_count = sum(1 for i in normalized_issues if i["severity"] == "critical")
    major_count = sum(1 for i in normalized_issues if i["severity"] == "major")
    minor_count = sum(1 for i in normalized_issues if i["severity"] == "minor")

    raw_verdict = str(eval_res.get("verdict") or "").strip().upper()
    if raw_verdict == "CONTESTED":
        verdict = "CONTESTED"
    elif fallback_unverified:
        verdict = raw_verdict if raw_verdict in ("PASS", "FAIL", "CONTESTED") else "FAIL"
    else:
        if weighted_total >= float(effective_threshold) and critical_count == 0:
            verdict = "PASS"
        else:
            verdict = "FAIL"

    return {
        "verdict": verdict,
        "weighted_total": weighted_total,
        "fallback_unverified": fallback_unverified,
        "dimension_scores": dimension_scores,
        "issues": normalized_issues,
        "critical_count": critical_count,
        "major_count": major_count,
        "minor_count": minor_count,
        "critical_issue_count": critical_count,
        "major_issue_count": major_count,
        "minor_issue_count": minor_count,
    }


def has_blocking_changes_requested(
    reviews: Optional[List[Dict[str, Any]]],
    comments: Optional[List[Dict[str, Any]]],
    pr_author: str = "",
    bot_login: str = "",
    human_approver: str = "",
    current_head_oid: str = "",
) -> bool:
    """Determine if any non-author, non-bot reviewer holds an un-cleared CHANGES_REQUESTED block (REQ-024)."""
    if not reviews:
        return False

    author = (pr_author or "").strip()
    bot = (bot_login or "").strip()
    approver = (human_approver or "").strip()
    head_oid = (current_head_oid or "").strip()

    indexed_reviews = [r for r in reviews if isinstance(r, dict)]
    if not indexed_reviews:
        return False

    all_have_ts = all(_parse_optional_ts(r) is not None for r in indexed_reviews)
    if all_have_ts:
        ordered_reviews = [
            r for _, r in sorted(
                enumerate(indexed_reviews),
                key=lambda pair: (_parse_optional_ts(pair[1]) or 0.0, pair[0]),
            )
        ]
    else:
        ordered_reviews = indexed_reviews

    dismissed_by_login: Dict[str, Optional[float]] = {}
    if approver and head_oid and comments:
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            if _extract_login(comment) != approver:
                continue
            body = str(comment.get("body") or "")
            for m in _DISMISS_REVIEW_RE.finditer(body):
                target_login = m.group(1).strip()
                target_oid = m.group(2).strip()
                if target_oid == head_oid:
                    dismissed_by_login[target_login] = _parse_optional_ts(comment)

    by_reviewer: Dict[str, List[Dict[str, Any]]] = {}
    for rev in ordered_reviews:
        rev_login = _extract_login(rev)
        if not rev_login or rev_login == author or (bot and rev_login == bot):
            continue
        by_reviewer.setdefault(rev_login, []).append(rev)

    for rev_login, rev_list in by_reviewer.items():
        latest_non_commented_idx: Optional[int] = None
        for idx, rev in enumerate(rev_list):
            state = str(rev.get("state") or "").strip().upper()
            if state in ("", "COMMENTED", "PENDING"):
                continue
            latest_non_commented_idx = idx

        if latest_non_commented_idx is None:
            continue

        latest_rev = rev_list[latest_non_commented_idx]
        if str(latest_rev.get("state") or "").strip().upper() != "CHANGES_REQUESTED":
            continue

        block_oid = _extract_commit_oid(latest_rev)
        block_ts = _parse_optional_ts(latest_rev)

        # Cleared if human_approver dismissed <rev_login> on current_head_oid
        if rev_login in dismissed_by_login:
            dismiss_ts = dismissed_by_login[rev_login]
            if dismiss_ts is None or block_ts is None or dismiss_ts >= block_ts:
                continue

        # Cleared if <rev_login> subsequently posted a valid REQ-022 review on a newer <head_oid>
        cleared_by_subsequent_review = False
        for sub_rev in rev_list[latest_non_commented_idx + 1:]:
            sub_oid = _extract_commit_oid(sub_rev)
            if not sub_oid:
                continue
            is_newer_oid = (sub_oid != block_oid) if block_oid else (bool(head_oid) and sub_oid == head_oid)
            if is_newer_oid and _is_valid_req022_review(sub_rev, author):
                cleared_by_subsequent_review = True
                break

        if not cleared_by_subsequent_review:
            return True

    return False


def _check_human_flag_commands(
    comments: Optional[List[Dict[str, Any]]],
    human_approver: str,
    current_head_oid: str,
) -> Tuple[bool, bool]:
    """Return (flag_cleared, flag_rejected) for current_head_oid from human_approver comments."""
    approver = (human_approver or "").strip()
    head_oid = (current_head_oid or "").strip()
    if not approver or not head_oid or not comments:
        return False, False

    cleared = False
    rejected = False
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        if _extract_login(comment) != approver:
            continue
        body = str(comment.get("body") or "")
        for m in _CLEAR_FLAG_RE.finditer(body):
            if m.group(1).strip() == head_oid:
                cleared = True
                rejected = False
        for m in _REJECT_FLAG_RE.finditer(body):
            if m.group(1).strip() == head_oid:
                rejected = True
                cleared = False
    return cleared, rejected


def compute_collab_verdict(
    eval_result: Optional[Union[Dict[str, Any], BaseException]],
    reviewer_scores: Optional[Union[Dict[str, float], List[Dict[str, float]]]] = None,
    pass_threshold: Optional[float] = None,
    book_record: Optional[Dict[str, Any]] = None,
    revise_round_count: int = 0,
    max_revise_rounds: int = 3,
    injection_flags: Optional[List[str]] = None,
    draft_text_or_len: Union[int, float, str] = 100,
    *,
    eval_error: Optional[Union[BaseException, str]] = None,
    reviews: Optional[List[Dict[str, Any]]] = None,
    comments: Optional[List[Dict[str, Any]]] = None,
    pr_author: str = "",
    bot_login: str = "",
    human_approver: str = "",
    current_head_oid: str = "",
    flag_cleared: bool = False,
    blocking_changes_requested: Optional[bool] = None,
) -> Dict[str, Any]:
    """Compute the authoritative REQ-023/REQ-024 workflow verdict for a translation PR.

    Outcomes:
      CANDIDATE | REVISE | REASSIGN | FLAGGED_FOR_HUMAN | EVALUATING
    """
    effective_threshold = resolve_pass_threshold(
        cli_threshold=pass_threshold,
        book_record=book_record,
    )

    cmd_cleared, cmd_rejected = _check_human_flag_commands(comments, human_approver, current_head_oid)
    if cmd_rejected:
        return {
            "outcome": "REASSIGN",
            "reason": f"Flagged PR rejected by {human_approver} via /reject-flag; closing for reassignment.",
            "weighted_total": 0.0,
            "effective_threshold": effective_threshold,
            "next_round": revise_round_count,
            "should_close": True,
        }

    is_flag_cleared = bool(flag_cleared or cmd_cleared)

    # 1. Pre-LLM prompt injection gate (REQ-023)
    active_injections = list(injection_flags or [])
    if isinstance(eval_result, dict) and eval_result.get("injection_flags"):
        active_injections.extend(eval_result["injection_flags"])

    if active_injections and not is_flag_cleared:
        raw_wt = (
            _clamp_score(eval_result.get("weighted_total") or eval_result.get("overall_score"), 0.0)
            if isinstance(eval_result, dict)
            else 0.0
        )
        return {
            "outcome": "FLAGGED_FOR_HUMAN",
            "reason": f"Prompt injection flagged: {', '.join(active_injections)}",
            "weighted_total": raw_wt,
            "effective_threshold": effective_threshold,
            "next_round": revise_round_count,
            "should_close": False,
        }

    # 2. Transient evaluator error / exception / unverified fallback -> stay in Evaluating (REQ-024)
    is_transient_error = (
        eval_error is not None
        or eval_result is None
        or isinstance(eval_result, BaseException)
        or not isinstance(eval_result, dict)
        or len(eval_result) == 0
        or bool(eval_result.get("error"))
        or bool(eval_result.get("transient_error"))
        or str(eval_result.get("verdict") or "").strip().upper() in ("ERROR", "TIMEOUT", "UNAVAILABLE")
    )
    if is_transient_error:
        err_detail = str(eval_error or (eval_result.get("error") if isinstance(eval_result, dict) else eval_result) or "transient evaluator failure")
        return {
            "outcome": "EVALUATING",
            "reason": f"Transient evaluation error ({err_detail}); keeping PR in Evaluating without consuming a round.",
            "weighted_total": 0.0,
            "effective_threshold": effective_threshold,
            "next_round": revise_round_count,
            "should_close": False,
        }

    norm_eval = normalize_evaluation_result(eval_result, effective_threshold=effective_threshold)
    weighted_total = norm_eval["weighted_total"]

    if norm_eval["fallback_unverified"]:
        return {
            "outcome": "EVALUATING",
            "reason": "Evaluation used heuristic fallback; verified live evaluation required (keeping PR in Evaluating).",
            "weighted_total": weighted_total,
            "effective_threshold": effective_threshold,
            "next_round": revise_round_count,
            "should_close": False,
            "normalized_eval": norm_eval,
        }

    # 3. Reviewer vs evaluator dimension divergence (> 1.0) -> FlaggedForHuman unless cleared (REQ-023)
    if reviewer_scores and not is_flag_cleared:
        score_dicts: List[Dict[str, float]] = (
            [reviewer_scores] if isinstance(reviewer_scores, dict) else list(reviewer_scores)
        )
        eval_scores = norm_eval["dimension_scores"]
        for rev_map in score_dicts:
            if not isinstance(rev_map, dict):
                continue
            for dim, r_score in rev_map.items():
                norm_dim = str(dim).strip().lower()
                if norm_dim in eval_scores:
                    e_score = float(eval_scores[norm_dim])
                    if abs(float(r_score) - e_score) > 1.0:
                        return {
                            "outcome": "FLAGGED_FOR_HUMAN",
                            "reason": (
                                f"Significant score divergence on '{norm_dim}': "
                                f"reviewer={float(r_score):.2f}, evaluator={e_score:.2f} (diff > 1.0)"
                            ),
                            "weighted_total": weighted_total,
                            "effective_threshold": effective_threshold,
                            "next_round": revise_round_count,
                            "should_close": False,
                            "normalized_eval": norm_eval,
                        }

    # 4. Blocking CHANGES_REQUESTED check (REQ-024)
    is_blocking_review = (
        blocking_changes_requested
        if blocking_changes_requested is not None
        else has_blocking_changes_requested(
            reviews=reviews,
            comments=comments,
            pr_author=pr_author,
            bot_login=bot_login,
            human_approver=human_approver,
            current_head_oid=current_head_oid,
        )
    )

    # 5. Candidate eligibility: normalized verdict == PASS and zero blocking review feedback
    if norm_eval["verdict"] == "PASS" and not is_blocking_review:
        return {
            "outcome": "CANDIDATE",
            "reason": (
                f"Score of record ({weighted_total:.2f}) meets effective threshold "
                f"({effective_threshold:.2f}) with zero critical issues and zero blocking reviews."
            ),
            "weighted_total": weighted_total,
            "effective_threshold": effective_threshold,
            "next_round": revise_round_count,
            "should_close": False,
            "normalized_eval": norm_eval,
            "critical_issue_count": norm_eval["critical_count"],
            "major_issue_count": norm_eval["major_count"],
            "minor_issue_count": norm_eval["minor_count"],
        }

    # 6. Failure or blocking review routing: check revision cap, then redraft, else REVISE
    next_round = revise_round_count + 1

    if revise_round_count >= max_revise_rounds:
        return {
            "outcome": "REASSIGN",
            "reason": (
                f"Maximum revision rounds ({max_revise_rounds}) reached; "
                f"closing PR for reassignment."
            ),
            "weighted_total": weighted_total,
            "effective_threshold": effective_threshold,
            "next_round": next_round,
            "should_close": True,
            "normalized_eval": norm_eval,
        }

    total_len = (
        float(draft_text_or_len)
        if isinstance(draft_text_or_len, (int, float))
        else float(len(str(draft_text_or_len)))
    )

    if norm_eval["verdict"] != "PASS":
        is_redraft = check_redraft(
            issues=norm_eval["issues"],
            total_length=total_len,
            weighted_total=weighted_total,
        )
        if is_redraft:
            return {
                "outcome": "REASSIGN",
                "reason": (
                    f"Evaluation score ({weighted_total:.2f}) or pervasive defects require complete redraft; "
                    f"closing PR for reassignment."
                ),
                "weighted_total": weighted_total,
                "effective_threshold": effective_threshold,
                "next_round": next_round,
                "should_close": True,
                "normalized_eval": norm_eval,
            }

    if is_blocking_review and norm_eval["verdict"] == "PASS":
        fail_reason = (
            f"Reviewer requested changes (CHANGES_REQUESTED); "
            f"revision requested (round {next_round}/{max_revise_rounds})."
        )
    elif norm_eval["verdict"] == "CONTESTED":
        fail_reason = (
            f"Evaluation verdict 'CONTESTED' requires revision "
            f"(round {next_round}/{max_revise_rounds})."
        )
    elif weighted_total < effective_threshold:
        fail_reason = (
            f"Score of record ({weighted_total:.2f}) below threshold ({effective_threshold:.2f}); "
            f"revision requested (round {next_round}/{max_revise_rounds})."
        )
    else:
        fail_reason = (
            f"Critical issues require revision "
            f"(round {next_round}/{max_revise_rounds})."
        )

    return {
        "outcome": "REVISE",
        "reason": fail_reason,
        "weighted_total": weighted_total,
        "effective_threshold": effective_threshold,
        "next_round": next_round,
        "should_close": False,
        "normalized_eval": norm_eval,
    }
