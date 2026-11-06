import pytest
from scripts.collab_select import (
    select_winning_pr,
    check_starved_chapter,
    format_non_selected_comment,
)


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
    now = 1000.0 + (12 * 3600)  # 12 hours later, window is 24h
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


def test_select_winning_pr_window_closed_unresolved_threads_ineligible():
    candidates = [
        {
            "pr_number": 12,
            "weighted_total": 4.8,
            "critical_count": 0,
            "major_count": 0,
            "author_login": "bob",
            "unresolved_threads": 2,
        }
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
    assert "Lower score" in result["non_selected"][0]["reason"] or "score" in result["non_selected"][0]["reason"].lower()


def test_select_winning_pr_tie_break_fewer_issues():
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
    assert "issue" in result["non_selected"][0]["reason"].lower()


def test_select_winning_pr_tie_break_earliest_pr():
    c1 = {
        "pr_number": 30,
        "weighted_total": 4.5,
        "critical_count": 0,
        "major_count": 1,
        "author_login": "alice",
        "unresolved_threads": 0,
    }
    c2 = {
        "pr_number": 35,
        "weighted_total": 4.5,
        "critical_count": 0,
        "major_count": 1,
        "author_login": "bob",
        "unresolved_threads": 0,
    }
    window_started_at = 1000.0
    now = 1000.0 + (25 * 3600)
    result = select_winning_pr([c2, c1], window_started_at, now, window_hours=24.0)

    assert result["window_closed"] is True
    assert result["winner"]["pr_number"] == 30
    assert len(result["non_selected"]) == 1
    assert result["non_selected"][0]["pr_number"] == 35
    assert "pr" in result["non_selected"][0]["reason"].lower()


def test_check_starved_chapter():
    opened = 1000.0
    # < 48 hours, no candidate -> False
    assert check_starved_chapter(opened, has_pass_candidate=False, now=opened + 47 * 3600) is False
    # >= 48 hours, has candidate -> False
    assert check_starved_chapter(opened, has_pass_candidate=True, now=opened + 49 * 3600) is False
    # >= 48 hours, no candidate -> True
    assert check_starved_chapter(opened, has_pass_candidate=False, now=opened + 48 * 3600) is True
    assert check_starved_chapter(opened, has_pass_candidate=False, now=opened + 50 * 3600) is True
    # custom fallback_hours
    assert check_starved_chapter(opened, has_pass_candidate=False, now=opened + 25 * 3600, fallback_hours=24.0) is True


def test_format_non_selected_comment():
    winner = {
        "pr_number": 100,
        "weighted_total": 4.6,
        "critical_count": 0,
        "major_count": 0,
    }

    # Case 1: Lower score (unresolved_threads=0, c_score < w_score)
    candidate_low_score = {
        "pr_number": 101,
        "weighted_total": 4.1,
        "critical_count": 0,
        "major_count": 2,
        "unresolved_threads": 0,
    }
    comment1 = format_non_selected_comment(candidate_low_score, winner)
    assert "#100" in comment1
    assert "4.6" in comment1
    assert "4.1" in comment1
    assert "score ranking (4.10 vs 4.60)" in comment1
    assert "unresolved discussion threads" not in comment1

    # Case 2: Unresolved threads (unresolved_threads > 0)
    candidate_unresolved = {
        "pr_number": 102,
        "weighted_total": 4.8,
        "critical_count": 0,
        "major_count": 0,
        "unresolved_threads": 2,
    }
    comment2 = format_non_selected_comment(candidate_unresolved, winner)
    assert "unresolved discussion threads (2 remaining)" in comment2

    # Case 3: Tied score, higher issues (c_score == w_score, c_issues > w_issues)
    candidate_more_issues = {
        "pr_number": 103,
        "weighted_total": 4.6,
        "critical_count": 1,
        "major_count": 1,
        "unresolved_threads": 0,
    }
    comment3 = format_non_selected_comment(candidate_more_issues, winner)
    assert "tie-break issue count (2 vs 0)" in comment3
    assert "score ranking" not in comment3
    assert "unresolved discussion threads" not in comment3

    # Case 4: Tied score, tied issues, later PR (c_score == w_score, c_issues == w_issues)
    candidate_tied_all = {
        "pr_number": 104,
        "weighted_total": 4.6,
        "critical_count": 0,
        "major_count": 0,
        "unresolved_threads": 0,
    }
    comment4 = format_non_selected_comment(candidate_tied_all, winner)
    assert "tie-break earlier PR number (#100 vs #104)" in comment4
    assert "score ranking" not in comment4
    assert "tie-break issue count" not in comment4
