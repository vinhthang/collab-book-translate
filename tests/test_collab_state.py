import pytest
from scripts.collab_state import (
    PRE_ISSUE_CHAPTER_STATES,
    ISSUE_CHAPTER_STATES,
    CHAPTER_STATES,
    CHECKLIST_SEED_STATUS_MAP,
    PR_STATES,
    CHAPTER_STATUS_LABELS,
    PR_STATUS_LABELS,
    resolve_pre_issue_state,
    count_bot_revise_rounds,
    advance_chapter_state,
    advance_pr_state,
    reconcile_status_labels,
)


def test_constants():
    assert PRE_ISSUE_CHAPTER_STATES == ("Seeding", "SeedBlocked")
    assert ISSUE_CHAPTER_STATES == ("Open", "Candidates", "Done")
    assert CHAPTER_STATES == ("Seeding", "SeedBlocked", "Open", "Candidates", "Done")
    assert CHECKLIST_SEED_STATUS_MAP == {
        "seeding": "Seeding",
        "blocked_failed": "SeedBlocked",
    }
    assert "Seeding" not in CHAPTER_STATUS_LABELS
    assert "SeedBlocked" not in CHAPTER_STATUS_LABELS
    assert CHAPTER_STATUS_LABELS == {
        "Open": "status:open",
        "Candidates": "status:candidates",
        "Done": "status:done",
    }

    assert PR_STATES == (
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
    assert len(PR_STATES) == 9
    assert PR_STATUS_LABELS == {
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


def test_resolve_pre_issue_state():
    assert resolve_pre_issue_state("seeding") == "Seeding"
    assert resolve_pre_issue_state("blocked_failed") == "SeedBlocked"
    assert resolve_pre_issue_state("seeded") is None
    assert resolve_pre_issue_state(None) is None
    assert resolve_pre_issue_state({"seed_status": "seeding"}) == "Seeding"
    assert resolve_pre_issue_state({"seed_status": "blocked_failed"}) == "SeedBlocked"


def test_advance_chapter_state():
    # Pre-issue states: Seeding <-> SeedBlocked -> Open
    assert advance_chapter_state("Seeding", seed_merged=False) == "Seeding"
    assert advance_chapter_state("Seeding", seed_failed=True) == "SeedBlocked"
    assert advance_chapter_state("Seeding", seed_status="blocked_failed") == "SeedBlocked"
    assert advance_chapter_state("SeedBlocked", seed_merged=True) == "SeedBlocked"
    assert advance_chapter_state("SeedBlocked", retry_blocked=True) == "Seeding"
    assert advance_chapter_state("SeedBlocked", seed_status="seeding") == "Seeding"
    assert advance_chapter_state("Seeding", seed_merged=True) == "Open"
    assert advance_chapter_state("Seeding", seed_status="seeded") == "Open"

    # Open <-> Candidates -> Done
    assert advance_chapter_state("Open", has_pass_candidate=False) == "Open"
    assert advance_chapter_state("Open", open_candidate_count=0) == "Open"
    assert advance_chapter_state("Open", has_pass_candidate=True) == "Candidates"
    assert advance_chapter_state("Open", open_candidate_count=1) == "Candidates"

    # Candidates stays Candidates when >=1 candidate remains or count unspecified without merge
    assert advance_chapter_state("Candidates", winner_merged=False) == "Candidates"
    assert advance_chapter_state("Candidates", open_candidate_count=2, winner_merged=False) == "Candidates"

    # Candidates reverts to Open when 0 open Candidate PRs remain without merge
    assert advance_chapter_state("Candidates", open_candidate_count=0, winner_merged=False) == "Open"
    assert advance_chapter_state("Candidates", has_pass_candidate=False, winner_merged=False) == "Open"

    # Candidates -> Done when winner_merged
    assert advance_chapter_state("Candidates", winner_merged=True) == "Done"

    # Done stays Done
    assert advance_chapter_state("Done", open_candidate_count=0) == "Done"


def test_count_bot_revise_rounds():
    sha_a = "a" * 40
    sha_b = "b" * 40
    sha_spoof = "c" * 40
    sha_upper = "D" * 40
    sha_short = "e" * 39

    comments = [
        {"author_login": "translator-bot", "body": f"[collab-revise sha={sha_a}]\nChecks failed."},
        # Duplicate marker on same SHA should not increase count
        {"author": {"login": "translator-bot"}, "body": f"Re-check [collab-revise sha={sha_a}]"},
        # Spoofed marker by PR author / third party / missing author must be ignored
        {"author_login": "mallory", "body": f"[collab-revise sha={sha_spoof}]"},
        {"author": {"login": "human-owner"}, "body": f"[collab-revise sha={sha_spoof}]"},
        {"body": f"[collab-revise sha={sha_spoof}]"},
        # Invalid SHA formats ignored
        {"author_login": "translator-bot", "body": f"[collab-revise sha={sha_upper}]"},
        {"author_login": "translator-bot", "body": f"[collab-revise sha={sha_short}]"},
        # Second valid distinct SHA from bot
        {"user": {"login": "translator-bot"}, "body": f"Round 2: [collab-revise sha={sha_b}]"},
    ]

    rounds = count_bot_revise_rounds(comments, "translator-bot")
    assert rounds == {sha_a, sha_b}
    assert len(rounds) == 2
    assert count_bot_revise_rounds(comments, "") == set()


def test_advance_pr_state_happy_path_to_candidate_and_merged():
    # NeedsChecks pending (checks_passed=None) -> stays NeedsChecks
    r0 = advance_pr_state("NeedsChecks", checks_passed=None)
    assert r0["state"] == "NeedsChecks"
    assert r0["label"] == "status:needs-checks"
    assert r0["should_close"] is False

    # NeedsChecks -> NeedsReview when checks_passed=True and has_valid_review=False
    r2 = advance_pr_state("NeedsChecks", checks_passed=True)
    assert r2["state"] == "NeedsReview"
    assert r2["label"] == "status:needs-review"
    assert r2["should_close"] is False

    # NeedsChecks -> Evaluating directly when checks_passed=True and has_valid_review=True
    r2_direct = advance_pr_state("NeedsChecks", checks_passed=True, has_valid_review=True)
    assert r2_direct["state"] == "Evaluating"
    assert r2_direct["label"] == "status:evaluating"
    assert r2_direct["should_close"] is False

    # NeedsReview -> Evaluating when has_valid_review=True
    r3 = advance_pr_state("NeedsReview", has_valid_review=False)
    assert r3["state"] == "NeedsReview"
    assert r3["label"] == "status:needs-review"

    r4 = advance_pr_state("NeedsReview", has_valid_review=True)
    assert r4["state"] == "Evaluating"
    assert r4["label"] == "status:evaluating"
    assert r4["should_close"] is False

    # Evaluating -> stays Evaluating across transient errors
    r4_transient = advance_pr_state("Evaluating", eval_outcome="EVALUATING")
    assert r4_transient["state"] == "Evaluating"
    assert r4_transient["label"] == "status:evaluating"
    assert r4_transient["should_close"] is False

    # Evaluating -> Candidate when eval_outcome="PASS" or "CANDIDATE"
    r5 = advance_pr_state("Evaluating", eval_outcome="PASS")
    assert r5["state"] == "Candidate"
    assert r5["label"] == "status:candidate"
    assert r5["should_close"] is False

    # Candidate -> Merged when selected_as_winner=True
    r6 = advance_pr_state("Candidate", selected_as_winner=True)
    assert r6["state"] == "Merged"
    assert r6["label"] == "status:merged"
    assert r6["should_close"] is False


def test_advance_pr_state_not_selected_and_flagged():
    r = advance_pr_state("Candidate", selected_as_winner=False)
    assert r["state"] == "NotSelected"
    assert r["label"] == "status:not-selected"
    assert r["should_close"] is True

    # FlaggedForHuman entry and exits
    f_enter = advance_pr_state("Evaluating", eval_outcome="FLAGGED_FOR_HUMAN")
    assert f_enter["state"] == "FlaggedForHuman"
    assert f_enter["label"] == "status:flagged"
    assert f_enter["should_close"] is False

    f_hold = advance_pr_state("FlaggedForHuman")
    assert f_hold["state"] == "FlaggedForHuman"
    assert f_hold["label"] == "status:flagged"
    assert f_hold["should_close"] is False

    f_push = advance_pr_state("FlaggedForHuman", new_commit_pushed=True, revise_round_count=2)
    assert f_push["state"] == "NeedsChecks"
    assert f_push["label"] == "status:needs-checks"
    assert f_push["should_close"] is False

    f_clear = advance_pr_state("FlaggedForHuman", clear_flag=True, has_valid_review=True)
    assert f_clear["state"] == "Evaluating"
    assert f_clear["label"] == "status:evaluating"
    assert f_clear["should_close"] is False

    f_reject = advance_pr_state("FlaggedForHuman", reject_flag=True)
    assert f_reject["state"] == "Reassign"
    assert f_reject["label"] == "status:reassign"
    assert f_reject["should_close"] is True


def test_advance_pr_state_evaluating_failures():
    # Evaluating -> Revise when eval_outcome="REVISE" and prior rounds < max
    r_rev = advance_pr_state("Evaluating", eval_outcome="REVISE", revise_round_count=0, max_revise_rounds=3)
    assert r_rev["state"] == "Revise"
    assert r_rev["label"] == "status:revise"
    assert r_rev["should_close"] is False

    # Evaluating -> Reassign when eval_outcome="REASSIGN" (e.g. redraft)
    r_rea = advance_pr_state("Evaluating", eval_outcome="REASSIGN")
    assert r_rea["state"] == "Reassign"
    assert r_rea["label"] == "status:reassign"
    assert r_rea["should_close"] is True


def test_advance_pr_state_sha_keyed_revise_cap_fourth_commit_closes():
    sha1 = "1" * 40
    sha2 = "2" * 40
    sha3 = "3" * 40
    sha4 = "4" * 40

    # 1st commit fails deterministic checks (prior rounds = 0 < 3) -> Revise
    c1 = advance_pr_state("NeedsChecks", checks_passed=False, revise_round_count=0, max_revise_rounds=3, head_oid=sha1)
    assert c1["state"] == "Revise"
    assert c1["label"] == "status:revise"
    assert c1["should_close"] is False
    assert c1["record_revise_sha"] is True

    # Re-sweeping already recorded sha1 is a no-op (stays Revise, does not record again)
    c1_resweep = advance_pr_state(
        "NeedsChecks",
        checks_passed=False,
        head_oid=sha1,
        recorded_revise_shas={sha1},
        max_revise_rounds=3,
    )
    assert c1_resweep["state"] == "Revise"
    assert c1_resweep["should_close"] is False
    assert c1_resweep["record_revise_sha"] is False

    # Pushes 1..max_revise_rounds (while revise_round_count <= 3) re-enter NeedsChecks
    for round_cnt in (1, 2, 3):
        pushed = advance_pr_state(
            "Revise",
            revise_round_count=round_cnt,
            max_revise_rounds=3,
            new_push_after_revise=True,
        )
        assert pushed["state"] == "NeedsChecks"
        assert pushed["label"] == "status:needs-checks"
        assert pushed["should_close"] is False

    # Waiting in Revise after 3rd revise marker (revise_round_count == 3, no new push yet) stays Revise
    waiting_r3 = advance_pr_state(
        "Revise",
        revise_round_count=3,
        max_revise_rounds=3,
        new_push_after_revise=False,
    )
    assert waiting_r3["state"] == "Revise"
    assert waiting_r3["should_close"] is False

    # 4th distinct commit (max_revise_rounds + 1) fails deterministic checks -> Reassign & close!
    c4_det = advance_pr_state(
        "NeedsChecks",
        checks_passed=False,
        head_oid=sha4,
        recorded_revise_shas={sha1, sha2, sha3},
        max_revise_rounds=3,
    )
    assert c4_det["state"] == "Reassign"
    assert c4_det["label"] == "status:reassign"
    assert c4_det["should_close"] is True
    assert c4_det["record_revise_sha"] is False

    # 4th distinct commit fails evaluation -> Reassign & close!
    c4_eval = advance_pr_state(
        "Evaluating",
        eval_outcome="REVISE",
        head_oid=sha4,
        recorded_revise_shas={sha1, sha2, sha3},
        max_revise_rounds=3,
    )
    assert c4_eval["state"] == "Reassign"
    assert c4_eval["label"] == "status:reassign"
    assert c4_eval["should_close"] is True


def test_reconcile_status_labels():
    current = ["book:sanguo", "status:needs-review", "status:open", "agent:qoder", "lang:zh-vi"]
    res = reconcile_status_labels(current, "status:candidate")

    assert res["to_add"] == ["status:candidate"]
    assert set(res["to_remove"]) == {"status:needs-review", "status:open"}
    assert "status:candidate" in res["final_labels"]
    assert "status:needs-review" not in res["final_labels"]
    assert "status:open" not in res["final_labels"]
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
    current = ["book:sanguo", "status:needs-checks", "lang:en-vi", "agent:alice"]
    res = reconcile_status_labels(current, None)
    assert res["to_add"] == []
    assert res["to_remove"] == ["status:needs-checks"]
    assert res["final_labels"] == ["book:sanguo", "lang:en-vi", "agent:alice"]
