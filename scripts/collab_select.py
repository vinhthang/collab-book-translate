"""Candidate PR selection, severity tie-breaks, and bounded starved-chapter fallback (REQ-026)."""

import re
from typing import Any, Dict, List, Optional, Union

_WINDOW_START_RE = re.compile(r"\[collab-window-start\s+epoch=([0-9]+(?:\.[0-9]+)?)\]")
_CANDIDATE_SCORE_RE = re.compile(
    r"\[collab-candidate-score\s+sha=([0-9a-fA-F]+)\s+score=([0-9]+(?:\.[0-9]+)?)\s+major=(\d+)\s+minor=(\d+)\]"
)
_FALLBACK_MARKER_RE = re.compile(r"\[collab-fallback\s+pr=#(\d+)\s+attempt=(\d+)\]")
_ESCALATED_MARKER_RE = re.compile(r"\[collab-starved-escalated\]")
_RETRY_FALLBACK_RE = re.compile(r"(?:^|\n)\s*/retry-fallback(?:\s|$)")


def _comment_author(comment: Dict[str, Any]) -> str:
    if not isinstance(comment, dict):
        return ""
    return str(
        comment.get("author_login")
        or (comment.get("author") or {}).get("login")
        or (comment.get("user") or {}).get("login")
        or ""
    ).strip()


def _candidate_score(candidate: Dict[str, Any]) -> float:
    if candidate.get("score_of_record") is not None:
        return float(candidate["score_of_record"])
    return float(candidate.get("weighted_total", 0.0))


def _major_issue_count(candidate: Dict[str, Any]) -> int:
    if candidate.get("major_issue_count") is not None:
        return int(candidate["major_issue_count"])
    if candidate.get("critical_major_count") is not None:
        return int(candidate["critical_major_count"])
    return int(candidate.get("critical_count", 0)) + int(candidate.get("major_count", 0))


def _minor_issue_count(candidate: Dict[str, Any]) -> int:
    if candidate.get("minor_issue_count") is not None:
        return int(candidate["minor_issue_count"])
    return int(candidate.get("minor_count", 0))


def _is_eligible_candidate(candidate: Dict[str, Any]) -> bool:
    if candidate.get("blocking_changes_requested") is True or candidate.get("has_blocking_review") is True:
        return False
    return int(candidate.get("unresolved_threads", 0)) == 0


def parse_latest_window_start(
    comments: Optional[List[Dict[str, Any]]],
    bot_login: str,
) -> Optional[float]:
    """Read chronologically latest bot_login [collab-window-start epoch=<float>] timestamp."""
    bot = (bot_login or "").strip()
    if not bot or not comments:
        return None
    latest_epoch: Optional[float] = None
    for comment in comments:
        if not isinstance(comment, dict) or _comment_author(comment) != bot:
            continue
        for match in _WINDOW_START_RE.finditer(str(comment.get("body") or "")):
            latest_epoch = float(match.group(1))
    return latest_epoch


def parse_latest_candidate_score(
    comments: Optional[List[Dict[str, Any]]],
    bot_login: str,
    head_oid: str,
) -> Optional[Dict[str, Any]]:
    """Read chronologically latest bot_login [collab-candidate-score ...] marker for head_oid."""
    bot = (bot_login or "").strip()
    oid = (head_oid or "").strip()
    if not bot or not oid or not comments:
        return None
    latest: Optional[Dict[str, Any]] = None
    for comment in comments:
        if not isinstance(comment, dict) or _comment_author(comment) != bot:
            continue
        for match in _CANDIDATE_SCORE_RE.finditer(str(comment.get("body") or "")):
            if match.group(1) == oid:
                latest = {
                    "sha": match.group(1),
                    "score_of_record": float(match.group(2)),
                    "major_issue_count": int(match.group(3)),
                    "minor_issue_count": int(match.group(4)),
                }
    return latest


def select_winning_pr(
    candidates: List[Dict[str, Any]],
    window_started_at: Optional[float] = None,
    now: Optional[float] = None,
    window_hours: Optional[float] = None,
) -> Dict[str, Any]:
    """Select the winning Candidate PR immediately without waiting for a candidate window (REQ-033).

    Ranks eligible candidates strictly by:
      (-score_of_record, major_issue_count, minor_issue_count, pr_number)
    """
    eligible = [c for c in candidates if _is_eligible_candidate(c)]

    if not eligible:
        return {
            "selected": False,
            "window_closed": True,
            "winner": None,
            "non_selected": [],
        }

    ranked = sorted(
        eligible,
        key=lambda c: (
            -_candidate_score(c),
            _major_issue_count(c),
            _minor_issue_count(c),
            int(c.get("pr_number", 0)),
        ),
    )

    winner = ranked[0]
    w_score = _candidate_score(winner)
    w_major = _major_issue_count(winner)
    w_minor = _minor_issue_count(winner)

    non_selected: List[Dict[str, Any]] = []
    for c in candidates:
        if c.get("pr_number") == winner.get("pr_number"):
            continue

        c_score = _candidate_score(c)
        c_major = _major_issue_count(c)
        c_minor = _minor_issue_count(c)

        if c.get("blocking_changes_requested") is True or c.get("has_blocking_review") is True:
            reason = "Candidate has un-cleared CHANGES_REQUESTED review feedback."
        elif int(c.get("unresolved_threads", 0)) > 0:
            reason = f"Candidate has {c.get('unresolved_threads')} unresolved conversation thread(s)."
        elif c_score < w_score:
            reason = f"Lower score ({c_score:.2f}) than winning PR #{winner['pr_number']} ({w_score:.2f})."
        elif c_major > w_major:
            reason = (
                f"Tied score ({c_score:.2f}) but more major issues "
                f"({c_major} vs {w_major}) than winning PR #{winner['pr_number']}."
            )
        elif c_minor > w_minor:
            reason = (
                f"Tied score ({c_score:.2f}) and major issues ({c_major}) but more minor issues "
                f"({c_minor} vs {w_minor}) than winning PR #{winner['pr_number']}."
            )
        else:
            reason = (
                f"Tied score ({c_score:.2f}) and issue counts ({c_major} major, {c_minor} minor) "
                f"but later PR number (#{c['pr_number']} vs #{winner['pr_number']})."
            )

        non_selected.append({
            "pr_number": c["pr_number"],
            "weighted_total": c_score,
            "score_of_record": c_score,
            "major_issue_count": c_major,
            "minor_issue_count": c_minor,
            "reason": reason,
        })

    return {
        "selected": bool(winner),
        "window_closed": True,
        "winner": winner,
        "non_selected": non_selected,
    }


def evaluate_starved_chapter(
    issue_opened_at: float,
    has_pass_candidate: bool = False,
    now: float = 0.0,
    fallback_hours: float = 48.0,
    *,
    starvation_since_utc: Optional[float] = None,
    latest_pr_closed_at: Optional[float] = None,
    open_pr_count: int = 0,
    comments: Optional[List[Dict[str, Any]]] = None,
    bot_login: str = "",
    human_approver: str = "",
    max_fallback_attempts: int = 2,
    has_source_injection: bool = False,
) -> Dict[str, Any]:
    """Evaluate 48 h starved-chapter fallback eligibility and bounded retry state (REQ-026)."""
    effective_since = float(issue_opened_at)
    if latest_pr_closed_at is not None:
        effective_since = max(effective_since, float(latest_pr_closed_at))
    if starvation_since_utc is not None:
        effective_since = max(effective_since, float(starvation_since_utc))

    bot = (bot_login or "").strip()
    approver = (human_approver or "").strip()

    # Slice comments after latest /retry-fallback from human_approver
    comment_list = [c for c in (comments or []) if isinstance(c, dict)]
    reset_idx = -1
    if approver:
        for idx, c in enumerate(comment_list):
            if _comment_author(c) == approver and _RETRY_FALLBACK_RE.search(str(c.get("body") or "")):
                reset_idx = idx

    post_reset_comments = comment_list[reset_idx + 1:]
    fallback_prs: List[int] = []
    already_escalated = False

    if bot:
        for c in post_reset_comments:
            if _comment_author(c) != bot:
                continue
            body = str(c.get("body") or "")
            for m in _FALLBACK_MARKER_RE.finditer(body):
                pr_num = int(m.group(1))
                if pr_num not in fallback_prs:
                    fallback_prs.append(pr_num)
            if _ESCALATED_MARKER_RE.search(body):
                already_escalated = True

    post_reset_attempts = len(fallback_prs)
    escalation_marker = (
        f"[collab-starved-escalated] @{approver}" if approver else "[collab-starved-escalated]"
    )

    if has_pass_candidate or int(open_pr_count) > 0:
        return {
            "starved": False,
            "action": "none",
            "should_open_fallback": False,
            "should_escalate": False,
            "already_escalated": already_escalated,
            "post_reset_attempts": post_reset_attempts,
            "next_attempt": None,
            "starvation_since_utc": effective_since,
            "escalation_marker": escalation_marker,
        }

    if (float(now) - effective_since) < (float(fallback_hours) * 3600.0):
        return {
            "starved": False,
            "action": "none",
            "should_open_fallback": False,
            "should_escalate": False,
            "already_escalated": already_escalated,
            "post_reset_attempts": post_reset_attempts,
            "next_attempt": None,
            "starvation_since_utc": effective_since,
            "escalation_marker": escalation_marker,
        }

    if already_escalated:
        return {
            "starved": False,
            "action": "already_escalated",
            "should_open_fallback": False,
            "should_escalate": False,
            "already_escalated": True,
            "post_reset_attempts": post_reset_attempts,
            "next_attempt": None,
            "starvation_since_utc": effective_since,
            "escalation_marker": escalation_marker,
        }

    if has_source_injection or post_reset_attempts >= int(max_fallback_attempts):
        return {
            "starved": False,
            "action": "escalate",
            "should_open_fallback": False,
            "should_escalate": True,
            "already_escalated": False,
            "post_reset_attempts": post_reset_attempts,
            "next_attempt": None,
            "starvation_since_utc": effective_since,
            "escalation_marker": escalation_marker,
        }

    return {
        "starved": True,
        "action": "open_fallback",
        "should_open_fallback": True,
        "should_escalate": False,
        "already_escalated": False,
        "post_reset_attempts": post_reset_attempts,
        "next_attempt": post_reset_attempts + 1,
        "starvation_since_utc": effective_since,
        "escalation_marker": escalation_marker,
    }


def check_starved_chapter(
    issue_opened_at: float,
    has_pass_candidate: bool = False,
    now: float = 0.0,
    fallback_hours: float = 48.0,
    *,
    starvation_since_utc: Optional[float] = None,
    latest_pr_closed_at: Optional[float] = None,
    open_pr_count: int = 0,
    comments: Optional[List[Dict[str, Any]]] = None,
    bot_login: str = "",
    human_approver: str = "",
    max_fallback_attempts: int = 2,
    has_source_injection: bool = False,
    return_details: bool = False,
) -> Union[bool, Dict[str, Any]]:
    """Return whether an Open chapter should open a bot fallback PR (or full state when return_details=True)."""
    details = evaluate_starved_chapter(
        issue_opened_at=issue_opened_at,
        has_pass_candidate=has_pass_candidate,
        now=now,
        fallback_hours=fallback_hours,
        starvation_since_utc=starvation_since_utc,
        latest_pr_closed_at=latest_pr_closed_at,
        open_pr_count=open_pr_count,
        comments=comments,
        bot_login=bot_login,
        human_approver=human_approver,
        max_fallback_attempts=max_fallback_attempts,
        has_source_injection=has_source_injection,
    )
    if return_details:
        return details
    return bool(details["should_open_fallback"])


def format_non_selected_comment(candidate: Dict[str, Any], winner: Dict[str, Any]) -> str:
    """Format closure comment for non-selected candidate PRs."""
    c_score = _candidate_score(candidate)
    w_score = _candidate_score(winner)
    c_major = _major_issue_count(candidate)
    w_major = _major_issue_count(winner)
    c_minor = _minor_issue_count(candidate)
    w_minor = _minor_issue_count(winner)

    if candidate.get("blocking_changes_requested") is True or candidate.get("has_blocking_review") is True:
        reason = "un-cleared changes requested review"
    elif int(candidate.get("unresolved_threads", 0)) > 0:
        reason = f"unresolved discussion threads ({candidate.get('unresolved_threads')} remaining)"
    elif c_score < w_score:
        reason = f"score ranking ({c_score:.2f} vs {w_score:.2f})"
    elif c_major > w_major:
        reason = f"tie-break issue count ({c_major} vs {w_major})"
    elif c_minor > w_minor:
        reason = f"tie-break minor issue count ({c_minor} vs {w_minor})"
    else:
        reason = f"tie-break earlier PR number (#{winner['pr_number']} vs #{candidate['pr_number']})"

    return (
        f"Thank you for your contribution to this chapter (PR #{candidate['pr_number']})!\n\n"
        f"Your translation received a weighted score of {c_score:.2f}.\n"
        f"PR #{winner['pr_number']} has been selected as the winning translation with a score of {w_score:.2f}.\n"
        f"Selection decision: {reason}.\n\n"
        f"Closing this PR in accordance with the collaborative translation protocol."
    )
