import pytest
from scripts.collab_select import (
    select_winning_pr,
    check_starved_chapter,
    evaluate_starved_chapter,
    format_non_selected_comment,
    parse_latest_window_start,
    parse_latest_candidate_score,
)


def test_parse_latest_window_start_and_candidate_score():
    win_comments = [
        {"author_login": "mallory", "body": "[collab-window-start epoch=9999.0]"},
        {"author_login": "bot", "body": "[collab-window-start epoch=1000.5]"},
        {"author_login": "bot", "body": "New cohort: [collab-window-start epoch=2500.25]"},
        {"author_login": "mallory", "body": "[collab-window-start epoch=8888.0]"},
    ]
    assert parse_latest_window_start(win_comments, "bot") == pytest.approx(2500.25)
    assert parse_latest_window_start(win_comments, "") is None

    sha_target = "a" * 40
    sha_other = "b" * 40
    score_comments = [
        {"author_login": "mallory", "body": f"[collab-candidate-score sha={sha_target} score=5.0 major=0 minor=0]"},
        {"author_login": "bot", "body": f"[collab-candidate-score sha={sha_other} score=4.9 major=0 minor=0]"},
        {"author_login": "bot", "body": f"[collab-candidate-score sha={sha_target} score=4.25 major=1 minor=3]"},
        {"author_login": "bot", "body": f"[collab-candidate-score sha={sha_target} score=4.55 major=0 minor=2]"},
    ]
    parsed = parse_latest_candidate_score(score_comments, "bot", sha_target)
    assert parsed == {
        "sha": sha_target,
        "score_of_record": 4.55,
        "major_issue_count": 0,
        "minor_issue_count": 2,
    }
    assert parse_latest_candidate_score(score_comments, "bot", "c" * 40) is None


def test_select_winning_pr_window_open():
    candidates = [
        {
            "pr_number": 10,
            "weighted_total": 4.5,
            "critical_count": 0,
            "major_count": 0,
            "author_login": "alice",
            "unresolved_threads": 0,
        }
    ]
    window_started_at = 1000.0
    now = 1000.0 + (12 * 3600)
    result = select_winning_pr(candidates, window_started_at, now, window_hours=24.0)

    assert result["window_closed"] is False
    assert result["winner"] is None
    assert result["non_selected"] == []


def test_select_winning_pr_window_closed_no_candidates():
    window_started_at = 1000.0
    now = 1000.0 + (25 * 3600)
    result = select_winning_pr([], window_started_at, now, window_hours=24.0)

    assert result["window_closed"] is True
    assert result["winner"] is None
    assert result["non_selected"] == []


def test_select_winning_pr_window_closed_ineligible_blocking_or_threads():
    candidates = [
        {
            "pr_number": 12,
            "score_of_record": 4.8,
            "major_issue_count": 0,
            "minor_issue_count": 0,
            "author_login": "bob",
            "unresolved_threads": 2,
        },
        {
            "pr_number": 13,
            "score_of_record": 4.9,
            "major_issue_count": 0,
            "minor_issue_count": 0,
            "author_login": "carol",
            "blocking_changes_requested": True,
        },
    ]
    window_started_at = 1000.0
    now = 1000.0 + (25 * 3600)
    result = select_winning_pr(candidates, window_started_at, now, window_hours=24.0)

    assert result["window_closed"] is True
    assert result["winner"] is None
    assert result["non_selected"] == []


def test_select_winning_pr_highest_score():
    c1 = {
        "pr_number": 15,
        "weighted_total": 4.2,
        "critical_count": 0,
        "major_count": 1,
        "author_login": "alice",
        "unresolved_threads": 0,
    }
    c2 = {
        "pr_number": 16,
        "weighted_total": 4.7,
        "critical_count": 0,
        "major_count": 0,
        "author_login": "bob",
        "unresolved_threads": 0,
    }
    window_started_at = 1000.0
    now = 1000.0 + (24 * 3600)
    result = select_winning_pr([c1, c2], window_started_at, now, window_hours=24.0)

    assert result["window_closed"] is True
    assert result["winner"]["pr_number"] == 16
    assert len(result["non_selected"]) == 1
    assert result["non_selected"][0]["pr_number"] == 15
    assert result["non_selected"][0]["weighted_total"] == 4.2
    assert "score" in result["non_selected"][0]["reason"].lower()


def test_select_winning_pr_tie_break_major_then_minor_then_pr_number():
    # 1. Fewer major_issue_count wins even with higher minor_issue_count and higher pr_number
    c_more_major = {
        "pr_number": 10,
        "score_of_record": 4.6,
        "major_issue_count": 2,
        "minor_issue_count": 0,
        "critical_count": 0,
        "major_count": 99,  # explicit major_issue_count takes precedence
        "author_login": "alice",
    }
    c_fewer_major_more_minor = {
        "pr_number": 25,
        "score_of_record": 4.6,
        "major_issue_count": 1,
        "minor_issue_count": 5,
        "author_login": "bob",
    }
    c_fewer_major_fewer_minor_later_pr = {
        "pr_number": 30,
        "score_of_record": 4.6,
        "major_issue_count": 1,
        "minor_issue_count": 2,
        "author_login": "carol",
    }
    c_fewer_major_fewer_minor_earlier_pr = {
        "pr_number": 22,
        "score_of_record": 4.6,
        "major_issue_count": 1,
        "minor_issue_count": 2,
        "author_login": "dave",
    }
    window_started_at = 1000.0
    now = 1000.0 + (24 * 3600)
    res = select_winning_pr(
        [c_more_major, c_fewer_major_more_minor, c_fewer_major_fewer_minor_later_pr, c_fewer_major_fewer_minor_earlier_pr],
        window_started_at,
        now,
        window_hours=24.0,
    )
    assert res["window_closed"] is True
    assert res["winner"]["pr_number"] == 22
    non_sel_by_pr = {item["pr_number"]: item for item in res["non_selected"]}
    assert set(non_sel_by_pr.keys()) == {10, 25, 30}
    assert "major" in non_sel_by_pr[10]["reason"].lower() or "issue" in non_sel_by_pr[10]["reason"].lower()
    assert "minor" in non_sel_by_pr[25]["reason"].lower()
    assert "pr" in non_sel_by_pr[30]["reason"].lower()


def test_select_winning_pr_legacy_critical_major_fallback():
    c1 = {
        "pr_number": 20,
        "weighted_total": 4.5,
        "critical_count": 0,
        "major_count": 2,
        "author_login": "alice",
        "unresolved_threads": 0,
    }
    c2 = {
        "pr_number": 21,
        "weighted_total": 4.5,
        "critical_count": 0,
        "major_count": 1,
        "author_login": "bob",
        "unresolved_threads": 0,
    }
    window_started_at = 1000.0
    now = 1000.0 + (24.1 * 3600)
    result = select_winning_pr([c1, c2], window_started_at, now, window_hours=24.0)

    assert result["window_closed"] is True
    assert result["winner"]["pr_number"] == 21
    assert len(result["non_selected"]) == 1
    assert result["non_selected"][0]["pr_number"] == 20


def test_check_starved_chapter_legacy_and_v2_starvation_rules():
    opened = 1000.0
    # Legacy calls
    assert check_starved_chapter(opened, has_pass_candidate=False, now=opened + 47 * 3600) is False
    assert check_starved_chapter(opened, has_pass_candidate=True, now=opened + 49 * 3600) is False
    assert check_starved_chapter(opened, has_pass_candidate=False, now=opened + 48 * 3600) is True
    assert check_starved_chapter(opened, has_pass_candidate=False, now=opened + 25 * 3600, fallback_hours=24.0) is True

    # 1. starvation_since_utc uses later of issue_opened_at and latest_pr_closed_at
    latest_closed = opened + 40 * 3600
    assert check_starved_chapter(
        opened,
        now=opened + 60 * 3600,  # 60h after issue open, but only 20h after latest PR closed!
        latest_pr_closed_at=latest_closed,
        fallback_hours=48.0,
    ) is False
    assert check_starved_chapter(
        opened,
        now=latest_closed + 48 * 3600,  # 48h after latest PR closed
        latest_pr_closed_at=latest_closed,
        fallback_hours=48.0,
    ) is True

    # 2. Any open PR of any state for that chapter blocks fallback
    assert check_starved_chapter(
        opened,
        now=opened + 100 * 3600,
        open_pr_count=1,
        fallback_hours=48.0,
    ) is False

    # 3. Post-reset fallback attempt cap (max 2) + spoof immunity + escalation + /retry-fallback reset
    starved_now = opened + 72 * 3600
    comments_1 = [
        {"author_login": "spoof-user", "body": "[collab-fallback pr=#50 attempt=1]"},
        {"author_login": "spoof-user", "body": "[collab-fallback pr=#51 attempt=2]"},
        {"author_login": "bot", "body": "[collab-fallback pr=#60 attempt=1]"},
    ]
    d1 = evaluate_starved_chapter(
        opened,
        now=starved_now,
        open_pr_count=0,
        comments=comments_1,
        bot_login="bot",
        human_approver="owner",
    )
    assert d1["should_open_fallback"] is True
    assert d1["next_attempt"] == 2
    assert d1["action"] == "open_fallback"
    assert check_starved_chapter(
        opened,
        now=starved_now,
        open_pr_count=0,
        comments=comments_1,
        bot_login="bot",
        human_approver="owner",
    ) is True

    # After 2 bot attempts have closed -> escalate once, do not open 3rd PR
    comments_2 = comments_1 + [
        {"author_login": "bot", "body": "[collab-fallback pr=#61 attempt=2]"},
    ]
    d2 = evaluate_starved_chapter(
        opened,
        now=starved_now,
        open_pr_count=0,
        comments=comments_2,
        bot_login="bot",
        human_approver="owner",
    )
    assert d2["should_open_fallback"] is False
    assert d2["should_escalate"] is True
    assert d2["action"] == "escalate"
    assert check_starved_chapter(
        opened,
        now=starved_now,
        open_pr_count=0,
        comments=comments_2,
        bot_login="bot",
        human_approver="owner",
    ) is False

    # Once [collab-starved-escalated] is posted by bot -> already_escalated (no duplicate escalation)
    comments_escalated = comments_2 + [
        {"author_login": "bot", "body": "[collab-starved-escalated] @owner"},
        # Unauthorized /retry-fallback is ignored
        {"author_login": "mallory", "body": "/retry-fallback"},
    ]
    d_esc = evaluate_starved_chapter(
        opened,
        now=starved_now,
        open_pr_count=0,
        comments=comments_escalated,
        bot_login="bot",
        human_approver="owner",
    )
    assert d_esc["should_open_fallback"] is False
    assert d_esc["should_escalate"] is False
    assert d_esc["already_escalated"] is True
    assert d_esc["action"] == "already_escalated"

    # Authorized /retry-fallback from human_approver resets post-reset attempts to 0 -> attempt=1!
    comments_reset = comments_escalated + [
        {"author_login": "owner", "body": "/retry-fallback"},
    ]
    d_reset = evaluate_starved_chapter(
        opened,
        now=starved_now,
        open_pr_count=0,
        comments=comments_reset,
        bot_login="bot",
        human_approver="owner",
    )
    assert d_reset["should_open_fallback"] is True
    assert d_reset["post_reset_attempts"] == 0
    assert d_reset["next_attempt"] == 1
    assert d_reset["action"] == "open_fallback"

    # Un-cleared source.txt prompt injection escalates immediately without opening fallback PR
    d_inj = evaluate_starved_chapter(
        opened,
        now=starved_now,
        open_pr_count=0,
        comments=[],
        bot_login="bot",
        human_approver="owner",
        has_source_injection=True,
    )
    assert d_inj["should_open_fallback"] is False
    assert d_inj["should_escalate"] is True
    assert d_inj["action"] == "escalate"


def test_format_non_selected_comment():
    winner = {
        "pr_number": 100,
        "score_of_record": 4.6,
        "major_issue_count": 0,
        "minor_issue_count": 1,
    }

    candidate_low_score = {
        "pr_number": 101,
        "score_of_record": 4.1,
        "major_issue_count": 2,
        "minor_issue_count": 0,
        "unresolved_threads": 0,
    }
    comment1 = format_non_selected_comment(candidate_low_score, winner)
    assert "#100" in comment1
    assert "score ranking (4.10 vs 4.60)" in comment1

    candidate_unresolved = {
        "pr_number": 102,
        "weighted_total": 4.8,
        "critical_count": 0,
        "major_count": 0,
        "unresolved_threads": 2,
    }
    comment2 = format_non_selected_comment(candidate_unresolved, winner)
    assert "unresolved discussion threads (2 remaining)" in comment2

    candidate_more_major = {
        "pr_number": 103,
        "weighted_total": 4.6,
        "critical_count": 1,
        "major_count": 1,
        "unresolved_threads": 0,
    }
    comment3 = format_non_selected_comment(candidate_more_major, winner)
    assert "tie-break issue count (2 vs 0)" in comment3

    candidate_more_minor = {
        "pr_number": 105,
        "score_of_record": 4.6,
        "major_issue_count": 0,
        "minor_issue_count": 4,
    }
    comment_minor = format_non_selected_comment(candidate_more_minor, winner)
    assert "minor issue" in comment_minor.lower()

    candidate_tied_all = {
        "pr_number": 104,
        "score_of_record": 4.6,
        "major_issue_count": 0,
        "minor_issue_count": 1,
    }
    comment4 = format_non_selected_comment(candidate_tied_all, winner)
    assert "tie-break earlier PR number (#100 vs #104)" in comment4
