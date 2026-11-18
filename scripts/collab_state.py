"""State machine, label reconciliation, and SHA-keyed revision governance (REQ-027)."""

import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

PRE_ISSUE_CHAPTER_STATES: Tuple[str, ...] = ("Seeding", "SeedBlocked")
ISSUE_CHAPTER_STATES: Tuple[str, ...] = ("Open", "Candidates", "Done")
CHAPTER_STATES: Tuple[str, ...] = ("Seeding", "SeedBlocked", "Open", "Candidates", "Done")

CHECKLIST_SEED_STATUS_MAP: Dict[str, str] = {
    "seeding": "Seeding",
    "blocked_failed": "SeedBlocked",
}

CHAPTER_STATE_TO_SEED_STATUS: Dict[str, str] = {
    "Seeding": "seeding",
    "SeedBlocked": "blocked_failed",
}

PR_STATES: Tuple[str, ...] = (
    "NeedsChecks",
    "NeedsReview",
    "Evaluating",
    "Revise",
    "FlaggedForHuman",
    "Candidate",
    "Merged",
    "Reassign",
    "NotSelected",
)

CHAPTER_STATUS_LABELS: Dict[str, str] = {
    "Open": "status:open",
    "Candidates": "status:candidates",
    "Done": "status:done",
}

PR_STATUS_LABELS: Dict[str, str] = {
    "NeedsChecks": "status:needs-checks",
    "NeedsReview": "status:needs-review",
    "Evaluating": "status:evaluating",
    "Revise": "status:revise",
    "FlaggedForHuman": "status:flagged",
    "Candidate": "status:candidate",
    "Merged": "status:merged",
    "Reassign": "status:reassign",
    "NotSelected": "status:not-selected",
}

_REVISE_MARKER_RE = re.compile(r"\[collab-revise sha=([0-9a-f]{40})\]")


def _comment_author_login(comment: Dict[str, Any]) -> str:
    if not isinstance(comment, dict):
        return ""
    return str(
        comment.get("author_login")
        or (comment.get("author") or {}).get("login")
        or (comment.get("user") or {}).get("login")
        or ""
    ).strip()


def resolve_pre_issue_state(
    seed_status_or_checklist: Union[Dict[str, Any], str, None],
) -> Optional[str]:
    """Map checklist seed_status ('seeding' | 'blocked_failed') to pre-issue chapter state."""
    raw: Any = seed_status_or_checklist
    if isinstance(seed_status_or_checklist, dict):
        raw = seed_status_or_checklist.get("seed_status")
    if not isinstance(raw, str):
        return None
    return CHECKLIST_SEED_STATUS_MAP.get(raw.strip().lower())


def count_bot_revise_rounds(
    comments: Optional[List[Dict[str, Any]]],
    bot_login: str,
) -> Set[str]:
    """Return distinct 40-char lowercase hex SHAs from bot_login [collab-revise sha=...] comments."""
    bot = (bot_login or "").strip()
    if not bot or not comments:
        return set()

    recorded_shas: Set[str] = set()
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        if _comment_author_login(comment) != bot:
            continue
        body = str(comment.get("body") or "")
        for sha in _REVISE_MARKER_RE.findall(body):
            recorded_shas.add(sha)
    return recorded_shas


def advance_chapter_state(
    current_state: str,
    *,
    seed_status: Optional[str] = None,
    seed_merged: bool = False,
    seed_failed: bool = False,
    retry_blocked: bool = False,
    has_pass_candidate: Optional[bool] = None,
    open_candidate_count: Optional[int] = None,
    winner_merged: bool = False,
) -> str:
    """Advance pre-issue or issue-bearing chapter state based on workflow transitions."""
    norm_seed = seed_status.strip().lower() if isinstance(seed_status, str) else ""

    if current_state == "Seeding":
        if seed_failed or norm_seed == "blocked_failed":
            return "SeedBlocked"
        if seed_merged or norm_seed == "seeded":
            return "Open"
        return "Seeding"

    if current_state == "SeedBlocked":
        if retry_blocked or norm_seed == "seeding":
            return "Seeding"
        return "SeedBlocked"

    if current_state == "Open":
        if winner_merged:
            return "Done"
        has_candidates = (
            (open_candidate_count is not None and open_candidate_count >= 1)
            or (has_pass_candidate is True)
        )
        if has_candidates:
            return "Candidates"
        return "Open"

    if current_state == "Candidates":
        if winner_merged:
            return "Done"
        no_candidates_remaining = (
            (open_candidate_count is not None and open_candidate_count <= 0)
            or (has_pass_candidate is False)
        )
        if no_candidates_remaining:
            return "Open"
        return "Candidates"

    return current_state


def advance_pr_state(
    current_state: str,
    *,
    checks_passed: Optional[bool] = None,
    has_valid_review: bool = False,
    eval_outcome: Optional[str] = None,
    revise_round_count: int = 0,
    max_revise_rounds: int = 3,
    new_push_after_revise: bool = False,
    new_commit_pushed: bool = False,
    selected_as_winner: Optional[bool] = None,
    flagged_for_human: bool = False,
    clear_flag: bool = False,
    reject_flag: bool = False,
    head_oid: Optional[str] = None,
    recorded_revise_shas: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Advance translation PR state across all 9 REQ-027 states and enforce SHA-keyed revision cap."""
    shas = set(recorded_revise_shas) if recorded_revise_shas is not None else set()
    effective_rounds = max(int(revise_round_count), len(shas))
    already_recorded = bool(head_oid and head_oid in shas)
    record_revise_sha = False
    norm_outcome = eval_outcome.strip().upper() if isinstance(eval_outcome, str) else None
    pushed = bool(new_push_after_revise or new_commit_pushed)

    next_state = current_state

    if current_state == "NeedsChecks":
        if flagged_for_human or norm_outcome == "FLAGGED_FOR_HUMAN":
            next_state = "FlaggedForHuman"
        elif checks_passed is True:
            next_state = "Evaluating" if has_valid_review else "NeedsReview"
        elif checks_passed is False:
            if already_recorded:
                next_state = "Revise"
            elif effective_rounds >= max_revise_rounds:
                next_state = "Reassign"
            else:
                next_state = "Revise"
                record_revise_sha = True
        else:
            next_state = "NeedsChecks"

    elif current_state == "NeedsReview":
        if pushed:
            next_state = "NeedsChecks"
        elif flagged_for_human or norm_outcome == "FLAGGED_FOR_HUMAN":
            next_state = "FlaggedForHuman"
        elif has_valid_review:
            next_state = "Evaluating"
        else:
            next_state = "NeedsReview"

    elif current_state == "Evaluating":
        if flagged_for_human or norm_outcome == "FLAGGED_FOR_HUMAN":
            next_state = "FlaggedForHuman"
        elif norm_outcome in ("PASS", "CANDIDATE"):
            next_state = "Candidate"
        elif norm_outcome == "REASSIGN":
            next_state = "Reassign"
        elif norm_outcome in ("REVISE", "FAIL", "CONTESTED"):
            if already_recorded:
                next_state = "Revise"
            elif effective_rounds >= max_revise_rounds:
                next_state = "Reassign"
            else:
                next_state = "Revise"
                record_revise_sha = True
        else:
            next_state = "Evaluating"

    elif current_state == "Revise":
        if effective_rounds > max_revise_rounds:
            next_state = "Reassign"
        elif pushed:
            next_state = "NeedsChecks"
        else:
            next_state = "Revise"

    elif current_state == "FlaggedForHuman":
        if reject_flag:
            next_state = "Reassign"
        elif pushed:
            next_state = "NeedsChecks"
        elif clear_flag:
            if norm_outcome in ("PASS", "CANDIDATE"):
                next_state = "Candidate"
            elif norm_outcome == "REASSIGN":
                next_state = "Reassign"
            elif norm_outcome in ("REVISE", "FAIL", "CONTESTED"):
                if already_recorded:
                    next_state = "Revise"
                elif effective_rounds >= max_revise_rounds:
                    next_state = "Reassign"
                else:
                    next_state = "Revise"
                    record_revise_sha = True
            elif has_valid_review:
                next_state = "Evaluating"
            elif checks_passed is True:
                next_state = "NeedsReview"
            else:
                next_state = "NeedsChecks"
        else:
            next_state = "FlaggedForHuman"

    elif current_state == "Candidate":
        if pushed:
            next_state = "NeedsChecks"
        elif flagged_for_human or norm_outcome == "FLAGGED_FOR_HUMAN":
            next_state = "FlaggedForHuman"
        elif norm_outcome in ("REVISE", "FAIL", "CONTESTED"):
            if effective_rounds >= max_revise_rounds and not already_recorded:
                next_state = "Reassign"
            else:
                next_state = "Revise"
                record_revise_sha = not already_recorded
        elif selected_as_winner is True:
            next_state = "Merged"
        elif selected_as_winner is False:
            next_state = "NotSelected"
        else:
            next_state = "Candidate"

    return {
        "state": next_state,
        "label": PR_STATUS_LABELS.get(next_state),
        "should_close": next_state in ("Reassign", "NotSelected"),
        "record_revise_sha": record_revise_sha,
    }


def reconcile_status_labels(
    current_labels: List[str],
    authoritative_status_label: Optional[str],
) -> Dict[str, List[str]]:
    """Reconcile issue or PR labels so only the authoritative status:* label is present.

    Preserves informational metadata labels (book:*, lang:*, agent:*).
    """
    to_remove: List[str] = [
        lbl for lbl in current_labels
        if lbl.startswith("status:") and lbl != authoritative_status_label
    ]

    to_add: List[str] = []
    if authoritative_status_label and authoritative_status_label not in current_labels:
        to_add.append(authoritative_status_label)

    final_labels: List[str] = []
    for lbl in current_labels:
        if lbl not in to_remove and lbl not in final_labels:
            final_labels.append(lbl)
    for lbl in to_add:
        if lbl not in final_labels:
            final_labels.append(lbl)

    return {
        "to_add": to_add,
        "to_remove": to_remove,
        "final_labels": final_labels,
    }
