"""State machine and label governance for collaborative translation (REQ-COLLAB-014)."""

from typing import Any, Dict, List, Optional, Tuple

CHAPTER_STATES: Tuple[str, ...] = ("Seeding", "Open", "Candidates", "Done")

PR_STATES: Tuple[str, ...] = (
    "NeedsChecks",
    "NeedsReview",
    "Evaluating",
    "Revise",
    "Reassign",
    "Candidate",
    "Merged",
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
    "Revise": "status:revise",
    "Reassign": "status:reassign",
    "Candidate": "status:candidate",
}


def advance_chapter_state(
    current_state: str,
    *,
    seed_merged: bool = False,
    has_pass_candidate: bool = False,
    winner_merged: bool = False,
) -> str:
    """Advance chapter issue state based on workflow events."""
    if current_state == "Seeding" and seed_merged:
        return "Open"
    if current_state == "Open" and has_pass_candidate:
        return "Candidates"
    if current_state == "Candidates" and winner_merged:
        return "Done"
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
    selected_as_winner: Optional[bool] = None,
) -> Dict[str, Any]:
    """Advance candidate PR state through the validation and review pipeline.

    States:
      NeedsChecks -> NeedsReview -> Evaluating -> Candidate / Revise / Reassign
      Revise -> NeedsChecks (if push and rounds < max) else Reassign
      Candidate -> Merged / NotSelected
    """
    next_state = current_state

    if current_state == "NeedsChecks":
        if checks_passed is True:
            next_state = "NeedsReview"
        else:
            next_state = "NeedsChecks"

    elif current_state == "NeedsReview":
        if has_valid_review:
            next_state = "Evaluating"
        else:
            next_state = "NeedsReview"

    elif current_state == "Evaluating":
        if eval_outcome == "PASS":
            next_state = "Candidate"
        elif eval_outcome == "REVISE":
            next_state = "Revise"
        elif eval_outcome == "REASSIGN":
            next_state = "Reassign"
        else:
            next_state = "Evaluating"

    elif current_state == "Revise":
        if new_push_after_revise:
            if revise_round_count < max_revise_rounds:
                next_state = "NeedsChecks"
            else:
                next_state = "Reassign"
        elif revise_round_count >= max_revise_rounds:
            next_state = "Reassign"
        else:
            next_state = "Revise"

    elif current_state == "Candidate":
        if selected_as_winner is True:
            next_state = "Merged"
        elif selected_as_winner is False:
            next_state = "NotSelected"
        else:
            next_state = "Candidate"

    return {
        "state": next_state,
        "label": PR_STATUS_LABELS.get(next_state),
        "should_close": next_state in ("Reassign", "NotSelected"),
    }


def reconcile_status_labels(
    current_labels: List[str],
    authoritative_status_label: Optional[str],
) -> Dict[str, List[str]]:
    """Reconcile issue or PR labels so only the authoritative status label is present.

    Preserves informational and taxonomy labels (e.g. book:*, lang:*, agent:*).
    """
    to_remove: List[str] = [
        lbl for lbl in current_labels
        if lbl.startswith("status:") and lbl != authoritative_status_label
    ]

    to_add: List[str] = []
    if authoritative_status_label and authoritative_status_label not in current_labels:
        to_add.append(authoritative_status_label)

    final_labels: List[str] = [
        lbl for lbl in current_labels if lbl not in to_remove
    ]
    for lbl in to_add:
        if lbl not in final_labels:
            final_labels.append(lbl)

    return {
        "to_add": to_add,
        "to_remove": to_remove,
        "final_labels": final_labels,
    }
