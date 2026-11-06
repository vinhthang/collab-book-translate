"""Candidate PR selection and tie-breaking for collaborative book translation (REQ-COLLAB-013, D3)."""

from typing import Any, Dict, List, Optional


def select_winning_pr(
    candidates: List[Dict[str, Any]],
    window_started_at: float,
    now: float,
    window_hours: float = 24.0,
) -> Dict[str, Any]:
    """Select the winning candidate PR after the submission window has elapsed.

    Ranks eligible candidates deterministically by:
      1. highest weighted_total
      2. fewest critical + major issues
      3. earliest PR number

    Candidates with unresolved_threads > 0 are ineligible to win.
    """
    if (now - window_started_at) < (window_hours * 3600.0):
        return {
            "window_closed": False,
            "winner": None,
            "non_selected": [],
        }

    eligible = [
        c for c in candidates
        if c.get("unresolved_threads", 0) == 0
    ]

    if not eligible:
        return {
            "window_closed": True,
            "winner": None,
            "non_selected": [],
        }

    # Deterministic ranking:
    # 1. -weighted_total (descending score)
    # 2. critical_count + major_count (ascending issues)
    # 3. pr_number (ascending PR number)
    ranked = sorted(
        eligible,
        key=lambda c: (
            -float(c.get("weighted_total", 0.0)),
            int(c.get("critical_count", 0)) + int(c.get("major_count", 0)),
            int(c.get("pr_number", 0)),
        ),
    )

    winner = ranked[0]

    non_selected: List[Dict[str, Any]] = []
    for c in candidates:
        if c.get("pr_number") == winner.get("pr_number"):
            continue

        c_score = float(c.get("weighted_total", 0.0))
        w_score = float(winner.get("weighted_total", 0.0))
        c_issues = int(c.get("critical_count", 0)) + int(c.get("major_count", 0))
        w_issues = int(winner.get("critical_count", 0)) + int(winner.get("major_count", 0))

        if c.get("unresolved_threads", 0) > 0:
            reason = f"Candidate has {c.get('unresolved_threads')} unresolved conversation thread(s)."
        elif c_score < w_score:
            reason = f"Lower score ({c_score:.2f}) than winning PR #{winner['pr_number']} ({w_score:.2f})."
        elif c_issues > w_issues:
            reason = f"Tied score ({c_score:.2f}) but more critical/major issues ({c_issues} vs {w_issues}) than winning PR #{winner['pr_number']}."
        else:
            reason = f"Tied score ({c_score:.2f}) and issues ({c_issues}) but later PR number (#{c['pr_number']} vs #{winner['pr_number']})."

        non_selected.append({
            "pr_number": c["pr_number"],
            "weighted_total": c_score,
            "reason": reason,
        })

    return {
        "window_closed": True,
        "winner": winner,
        "non_selected": non_selected,
    }


def check_starved_chapter(
    issue_opened_at: float,
    has_pass_candidate: bool,
    now: float,
    fallback_hours: float = 48.0,
) -> bool:
    """Check if a chapter is starved of PASS candidates and requires bot fallback (D3)."""
    if has_pass_candidate:
        return False
    return (now - issue_opened_at) >= (fallback_hours * 3600.0)


def format_non_selected_comment(candidate: Dict[str, Any], winner: Dict[str, Any]) -> str:
    """Format closure comment for non-selected candidate PRs."""
    c_score = float(candidate.get("weighted_total", 0.0))
    w_score = float(winner.get("weighted_total", 0.0))
    c_issues = int(candidate.get("critical_count", 0)) + int(candidate.get("major_count", 0))
    w_issues = int(winner.get("critical_count", 0)) + int(winner.get("major_count", 0))

    if candidate.get("unresolved_threads", 0) > 0:
        reason = f"unresolved discussion threads ({candidate.get('unresolved_threads')} remaining)"
    elif c_score < w_score:
        reason = f"score ranking ({c_score:.2f} vs {w_score:.2f})"
    elif c_issues > w_issues:
        reason = f"tie-break issue count ({c_issues} vs {w_issues})"
    else:
        reason = f"tie-break earlier PR number (#{winner['pr_number']} vs #{candidate['pr_number']})"

    return (
        f"Thank you for your contribution to this chapter (PR #{candidate['pr_number']})!\n\n"
        f"Your translation received a weighted score of {c_score:.2f}.\n"
        f"PR #{winner['pr_number']} has been selected as the winning translation with a score of {w_score:.2f}.\n"
        f"Selection decision: {reason}.\n\n"
        f"Closing this PR in accordance with the collaborative translation protocol."
    )
