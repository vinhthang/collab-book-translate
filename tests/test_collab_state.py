import pytest
from scripts.collab_state import (
    CHAPTER_STATES,
    PR_STATES,
    CHAPTER_STATUS_LABELS,
    PR_STATUS_LABELS,
    advance_chapter_state,
    advance_pr_state,
    reconcile_status_labels,
)


def test_constants():
    assert CHAPTER_STATES == ("Seeding", "Open", "Candidates", "Done")
    assert PR_STATES == (
        "NeedsChecks",
        "NeedsReview",
        "Evaluating",
        "Revise",
        "Reassign",
        "Candidate",
        "Merged",
        "NotSelected",
    )
    assert CHAPTER_STATUS_LABELS["Open"] == "status:open"
    assert CHAPTER_STATUS_LABELS["Candidates"] == "status:candidates"
    assert CHAPTER_STATUS_LABELS["Done"] == "status:done"
    assert PR_STATUS_LABELS["NeedsChecks"] == "status:needs-checks"
    assert PR_STATUS_LABELS["NeedsReview"] == "status:needs-review"
    assert PR_STATUS_LABELS["Revise"] == "status:revise"
    assert PR_STATUS_LABELS["Reassign"] == "status:reassign"
    assert PR_STATUS_LABELS["Candidate"] == "status:candidate"


def test_advance_chapter_state():
    # Seeding -> Open when seed_merged
    assert advance_chapter_state("Seeding", seed_merged=False) == "Seeding"
    assert advance_chapter_state("Seeding", seed_merged=True) == "Open"

    # Open -> Candidates when has_pass_candidate
    assert advance_chapter_state("Open", has_pass_candidate=False) == "Open"
    assert advance_chapter_state("Open", has_pass_candidate=True) == "Candidates"

    # Candidates -> Done when winner_merged
    assert advance_chapter_state("Candidates", winner_merged=False) == "Candidates"
    assert advance_chapter_state("Candidates", winner_merged=True) == "Done"

    # Done stays Done
    assert advance_chapter_state("Done") == "Done"


def test_advance_pr_state_happy_path_to_candidate():
    # NeedsChecks -> NeedsReview when checks_passed=True
    r1 = advance_pr_state("NeedsChecks", checks_passed=False)
    assert r1["state"] == "NeedsChecks"
    assert r1["label"] == "status:needs-checks"
    assert r1["should_close"] is False

    r2 = advance_pr_state("NeedsChecks", checks_passed=True)
    assert r2["state"] == "NeedsReview"
    assert r2["label"] == "status:needs-review"
    assert r2["should_close"] is False

    # NeedsReview -> Evaluating when has_valid_review=True
    r3 = advance_pr_state("NeedsReview", has_valid_review=False)
    assert r3["state"] == "NeedsReview"

    r4 = advance_pr_state("NeedsReview", has_valid_review=True)
    assert r4["state"] == "Evaluating"
    assert r4["label"] is None  # Evaluating has no status label in PR_STATUS_LABELS

    # Evaluating -> Candidate when eval_outcome="PASS"
    r5 = advance_pr_state("Evaluating", eval_outcome="PASS")
    assert r5["state"] == "Candidate"
    assert r5["label"] == "status:candidate"
    assert r5["should_close"] is False

    # Candidate -> Merged when selected_as_winner=True
    r6 = advance_pr_state("Candidate", selected_as_winner=True)
    assert r6["state"] == "Merged"
    assert r6["should_close"] is False


def test_advance_pr_state_not_selected():
    r = advance_pr_state("Candidate", selected_as_winner=False)
    assert r["state"] == "NotSelected"
    assert r["should_close"] is True


def test_advance_pr_state_evaluating_failures():
    # Evaluating -> Revise when eval_outcome="REVISE"
    r_rev = advance_pr_state("Evaluating", eval_outcome="REVISE")
    assert r_rev["state"] == "Revise"
    assert r_rev["label"] == "status:revise"
    assert r_rev["should_close"] is False

    # Evaluating -> Reassign when eval_outcome="REASSIGN"
    r_rea = advance_pr_state("Evaluating", eval_outcome="REASSIGN")
    assert r_rea["state"] == "Reassign"
    assert r_rea["label"] == "status:reassign"
    assert r_rea["should_close"] is True


def test_advance_pr_state_revise_rounds():
    # Revise with new_push and round_count < max (e.g. 1 < 3) -> NeedsChecks
    r1 = advance_pr_state(
        "Revise",
        revise_round_count=1,
        max_revise_rounds=3,
        new_push_after_revise=True,
    )
    assert r1["state"] == "NeedsChecks"
    assert r1["label"] == "status:needs-checks"
    assert r1["should_close"] is False

    # Revise with new_push and round_count >= max (e.g. 3 >= 3) -> Reassign
    r2 = advance_pr_state(
        "Revise",
        revise_round_count=3,
        max_revise_rounds=3,
        new_push_after_revise=True,
    )
    assert r2["state"] == "Reassign"
    assert r2["label"] == "status:reassign"
    assert r2["should_close"] is True

    # Revise without push but round_count >= max (exact boundary 3 >= 3) -> Reassign
    r3 = advance_pr_state(
        "Revise",
        revise_round_count=3,
        max_revise_rounds=3,
        new_push_after_revise=False,
    )
    assert r3["state"] == "Reassign"
    assert r3["should_close"] is True


def test_reconcile_status_labels():
    current = ["book:sanguo", "status:needs-review", "agent:qoder", "lang:zh-vi"]
    res = reconcile_status_labels(current, "status:candidate")

    assert res["to_add"] == ["status:candidate"]
    assert res["to_remove"] == ["status:needs-review"]
    assert "status:candidate" in res["final_labels"]
    assert "status:needs-review" not in res["final_labels"]
    assert "book:sanguo" in res["final_labels"]
    assert "agent:qoder" in res["final_labels"]
    assert "lang:zh-vi" in res["final_labels"]


def test_reconcile_status_labels_already_present():
    current = ["book:sanguo", "status:candidate"]
    res = reconcile_status_labels(current, "status:candidate")
    assert res["to_add"] == []
    assert res["to_remove"] == []
    assert res["final_labels"] == ["book:sanguo", "status:candidate"]


def test_reconcile_status_labels_clear_authoritative():
    current = ["book:sanguo", "status:needs-checks"]
    res = reconcile_status_labels(current, None)
    assert res["to_add"] == []
    assert res["to_remove"] == ["status:needs-checks"]
    assert res["final_labels"] == ["book:sanguo"]
