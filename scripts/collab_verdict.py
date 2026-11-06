"""
scripts/collab_verdict.py — Collaborative translation verdict computation and state mapping.

Implements REQ-COLLAB-006, REQ-COLLAB-014:
- Maps evaluation and reviewer scores to workflow outcomes:
  CANDIDATE, REVISE, REASSIGN, FLAGGED_FOR_HUMAN, BLOCKED_UNVERIFIED
- Applies resolve_pass_threshold (literary genre 3.5, default 4.0)
- Applies check_redraft to route failures to surgical REVISE vs complete REASSIGN
- Enforces 3-round revision cap
"""

from typing import Any, Dict, List, Optional, Union

from scripts.helper import check_redraft, resolve_pass_threshold


def compute_collab_verdict(
    eval_result: Dict[str, Any],
    reviewer_scores: Optional[Dict[str, float]] = None,
    pass_threshold: Optional[float] = None,
    book_record: Optional[Dict[str, Any]] = None,
    revise_round_count: int = 0,
    max_revise_rounds: int = 3,
    injection_flags: Optional[List[str]] = None,
    draft_text_or_len: Union[int, float, str] = 100,
) -> Dict[str, Any]:
    """Computes the authoritative workflow verdict for a translation PR.

    Returns:
        {
            "outcome": "CANDIDATE" | "REVISE" | "REASSIGN" | "FLAGGED_FOR_HUMAN" | "BLOCKED_UNVERIFIED",
            "reason": str,
            "weighted_total": float,
            "effective_threshold": float,
            "next_round": int
        }
    """
    effective_threshold = resolve_pass_threshold(
        cli_threshold=pass_threshold,
        book_record=book_record,
    )

    weighted_total = float(
        eval_result.get("weighted_total")
        or eval_result.get("overall_score")
        or 0.0
    )

    # 1. Gate unverified offline fallback evaluations
    if eval_result.get("fallback_unverified") is True or eval_result.get("provenance") == "fallback":
        return {
            "outcome": "BLOCKED_UNVERIFIED",
            "reason": "Evaluation used heuristic fallback; verified live evaluation required.",
            "weighted_total": weighted_total,
            "effective_threshold": effective_threshold,
            "next_round": revise_round_count,
        }

    # 2. Flag prompt injection
    active_injections = list(injection_flags or [])
    if eval_result.get("injection_flags"):
        active_injections.extend(eval_result["injection_flags"])

    if active_injections:
        return {
            "outcome": "FLAGGED_FOR_HUMAN",
            "reason": f"Prompt injection flagged: {', '.join(active_injections)}",
            "weighted_total": weighted_total,
            "effective_threshold": effective_threshold,
            "next_round": revise_round_count,
        }

    # 3. Flag large divergence between reviewer score and score of record (> 1.0)
    if reviewer_scores and eval_result.get("scores"):
        eval_scores = eval_result["scores"]
        for dim, r_score in reviewer_scores.items():
            norm_dim = dim.lower()
            if norm_dim in eval_scores:
                e_score = float(eval_scores[norm_dim])
                if abs(float(r_score) - e_score) > 1.0:
                    return {
                        "outcome": "FLAGGED_FOR_HUMAN",
                        "reason": (
                            f"Significant score divergence on '{norm_dim}': "
                            f"reviewer={r_score:.2f}, evaluator={e_score:.2f} (diff > 1.0)"
                        ),
                        "weighted_total": weighted_total,
                        "effective_threshold": effective_threshold,
                        "next_round": revise_round_count,
                    }

    # 4. Check critical issues and evaluation verdict
    issues = eval_result.get("issues", [])
    critical_issues = [
        iss for iss in issues
        if str(iss.get("severity", "")).lower() == "critical"
    ]
    eval_verdict = str(eval_result.get("verdict", "")).upper()

    is_passing = (
        weighted_total >= effective_threshold
        and len(critical_issues) == 0
        and eval_verdict not in ("FAIL", "CONTESTED")
    )

    if is_passing:
        return {
            "outcome": "CANDIDATE",
            "reason": (
                f"Score of record ({weighted_total:.2f}) meets effective threshold "
                f"({effective_threshold:.2f}) with zero critical issues."
            ),
            "weighted_total": weighted_total,
            "effective_threshold": effective_threshold,
            "next_round": revise_round_count,
        }

    # 5. Handle Failure: Check Revision Cap then Redraft
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
        }

    total_len = (
        float(draft_text_or_len)
        if isinstance(draft_text_or_len, (int, float))
        else float(len(str(draft_text_or_len)))
    )

    is_redraft = check_redraft(
        issues=issues,
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
        }

    if weighted_total < effective_threshold:
        fail_reason = (
            f"Score of record ({weighted_total:.2f}) below threshold ({effective_threshold:.2f}); "
            f"revision requested (round {next_round}/{max_revise_rounds})."
        )
    elif eval_verdict in ("FAIL", "CONTESTED"):
        fail_reason = (
            f"Evaluation verdict '{eval_verdict}' requires revision "
            f"(round {next_round}/{max_revise_rounds})."
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
    }
