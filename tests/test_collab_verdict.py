"""
tests/test_collab_verdict.py — Unit tests for collaborative translation verdict computation.
Tests REQ-023, REQ-024, REQ-027.
"""

import unittest

from scripts.collab_verdict import (
    compute_collab_verdict,
    evaluate_pr_from_reviews,
    filter_fresh_valid_reviews,
    has_blocking_changes_requested,
    normalize_evaluation_result,
)


VALID_RUBRIC_BODY = (
    "Segment S-0001 looks solid overall.\n"
    "- Fidelity: 4.5/5\n"
    "- Fluency: 4.5/5\n"
    "- Terminology: 4.5/5\n"
    "- Register: 4.5/5\n"
    "- Structure: 4.5/5\n"
    "- Completeness: 4.5/5\n"
)


class TestCollabVerdict(unittest.TestCase):

    def test_normalize_evaluation_result_threshold_and_schema(self):
        # Engine returned internal FAIL at 3.7, but fiction threshold is 3.5 with 0 critical issues -> PASS
        raw_fiction = {
            "verdict": "FAIL",
            "weighted_total": 3.7,
            "fallback_unverified": False,
            "scores": {
                "fidelity": 3.8,
                "fluency": 3.7,
                "terminology": 3.6,
                "register": 3.7,
                "structure": 3.7,
                "completeness": 3.7,
            },
            "issues": [
                {"segment_id": "S-0002", "dimension": "fluency", "severity": "major", "description": "stiff phrasing"},
                {"segment_id": "S-0003", "dimension": "register", "severity": "minor", "problem": "slight tone shift"},
            ],
        }
        norm_pass = normalize_evaluation_result(raw_fiction, effective_threshold=3.5)
        self.assertEqual(norm_pass["verdict"], "PASS")
        self.assertEqual(norm_pass["weighted_total"], 3.7)
        self.assertFalse(norm_pass["fallback_unverified"])
        self.assertEqual(norm_pass["critical_count"], 0)
        self.assertEqual(norm_pass["major_count"], 1)
        self.assertEqual(norm_pass["minor_count"], 1)
        self.assertEqual(
            set(norm_pass["dimension_scores"].keys()),
            {"fidelity", "fluency", "terminology", "register", "structure", "completeness"},
        )
        self.assertEqual(norm_pass["issues"][0]["problem"], "stiff phrasing")
        self.assertIn("suggestion", norm_pass["issues"][0])

        # Same 3.7 against general 4.0 threshold -> normalized to FAIL even if raw said PASS
        raw_false_pass = dict(raw_fiction, verdict="PASS")
        norm_fail = normalize_evaluation_result(raw_false_pass, effective_threshold=4.0)
        self.assertEqual(norm_fail["verdict"], "FAIL")

        # Critical issue forces FAIL even when weighted_total >= threshold
        raw_crit = {
            "verdict": "PASS",
            "weighted_total": 4.8,
            "fallback_unverified": False,
            "dimension_scores": {"fidelity": 4.8},
            "issues": [{"segment_id": "S-0001", "dimension": "fidelity", "severity": "critical", "problem": "omission"}],
        }
        norm_crit = normalize_evaluation_result(raw_crit, effective_threshold=4.0)
        self.assertEqual(norm_crit["verdict"], "FAIL")
        self.assertEqual(norm_crit["critical_count"], 1)

        # CONTESTED stays CONTESTED even when score >= threshold and 0 critical issues
        raw_contested = {
            "verdict": "CONTESTED",
            "weighted_total": 4.7,
            "fallback_unverified": False,
            "issues": [],
        }
        norm_contested = normalize_evaluation_result(raw_contested, effective_threshold=4.0)
        self.assertEqual(norm_contested["verdict"], "CONTESTED")

        # fallback_unverified is preserved and never normalized to live PASS when raw is FAIL
        raw_fallback = {
            "verdict": "FAIL",
            "weighted_total": 4.9,
            "fallback_unverified": True,
            "issues": [],
        }
        norm_fb = normalize_evaluation_result(raw_fallback, effective_threshold=4.0)
        self.assertTrue(norm_fb["fallback_unverified"])
        self.assertEqual(norm_fb["verdict"], "FAIL")

    def test_has_blocking_changes_requested_per_reviewer_and_dismissal(self):
        sha_old = "1" * 40
        sha_new = "2" * 40

        # Active CHANGES_REQUESTED from bob blocks
        reviews_blocking = [
            {"author_login": "bob", "state": "CHANGES_REQUESTED", "commit": {"oid": sha_old}, "body": "Fix S-0001"},
        ]
        self.assertTrue(
            has_blocking_changes_requested(
                reviews=reviews_blocking,
                comments=[],
                pr_author="alice",
                bot_login="bot",
                human_approver="owner",
                current_head_oid=sha_new,
            )
        )

        # Self or bot CHANGES_REQUESTED is ignored
        reviews_self_bot = [
            {"author_login": "alice", "state": "CHANGES_REQUESTED", "commit": {"oid": sha_old}},
            {"author_login": "bot", "state": "CHANGES_REQUESTED", "commit": {"oid": sha_old}},
        ]
        self.assertFalse(
            has_blocking_changes_requested(
                reviews=reviews_self_bot,
                comments=[],
                pr_author="alice",
                bot_login="bot",
                human_approver="owner",
                current_head_oid=sha_new,
            )
        )

        # Subsequent APPROVED by bob clears bob
        reviews_approved = reviews_blocking + [
            {"author_login": "bob", "state": "APPROVED", "commit": {"oid": sha_new}, "body": VALID_RUBRIC_BODY},
        ]
        self.assertFalse(
            has_blocking_changes_requested(
                reviews=reviews_approved,
                comments=[],
                pr_author="alice",
                bot_login="bot",
                human_approver="owner",
                current_head_oid=sha_new,
            )
        )

        # Subsequent valid REQ-022 COMMENTED review by bob on newer head_oid clears bob
        reviews_valid_re_review = reviews_blocking + [
            {"author_login": "bob", "state": "COMMENTED", "commit": {"oid": sha_new}, "body": VALID_RUBRIC_BODY},
        ]
        self.assertFalse(
            has_blocking_changes_requested(
                reviews=reviews_valid_re_review,
                comments=[],
                pr_author="alice",
                bot_login="bot",
                human_approver="owner",
                current_head_oid=sha_new,
            )
        )

        # Plain non-rubric COMMENTED review on newer head_oid does NOT clear bob
        reviews_plain_comment = reviews_blocking + [
            {"author_login": "bob", "state": "COMMENTED", "commit": {"oid": sha_new}, "body": "Still looking"},
        ]
        self.assertTrue(
            has_blocking_changes_requested(
                reviews=reviews_plain_comment,
                comments=[],
                pr_author="alice",
                bot_login="bot",
                human_approver="owner",
                current_head_oid=sha_new,
            )
        )

        # Two blocking reviewers (bob and carol): dismissing ONLY bob leaves carol blocking!
        two_blockers = [
            {"author_login": "bob", "state": "CHANGES_REQUESTED", "commit": {"oid": sha_old}},
            {"author_login": "carol", "state": "CHANGES_REQUESTED", "commit": {"oid": sha_old}},
        ]
        dismiss_bob_only = [
            {"author_login": "owner", "body": f"/dismiss-stale-review bob {sha_new}"},
        ]
        self.assertTrue(
            has_blocking_changes_requested(
                reviews=two_blockers,
                comments=dismiss_bob_only,
                pr_author="alice",
                bot_login="bot",
                human_approver="owner",
                current_head_oid=sha_new,
            )
        )

        # Unauthorized user or wrong SHA cannot dismiss carol
        bad_dismissals = dismiss_bob_only + [
            {"author_login": "mallory", "body": f"/dismiss-stale-review carol {sha_new}"},
            {"author_login": "owner", "body": f"/dismiss-stale-review carol {sha_old}"},
        ]
        self.assertTrue(
            has_blocking_changes_requested(
                reviews=two_blockers,
                comments=bad_dismissals,
                pr_author="alice",
                bot_login="bot",
                human_approver="owner",
                current_head_oid=sha_new,
            )
        )

        # Dismissing carol as well on current_head_oid clears all blocks
        dismiss_both = dismiss_bob_only + [
            {"author_login": "owner", "body": f"/dismiss-stale-review carol {sha_new}"},
        ]
        self.assertFalse(
            has_blocking_changes_requested(
                reviews=two_blockers,
                comments=dismiss_both,
                pr_author="alice",
                bot_login="bot",
                human_approver="owner",
                current_head_oid=sha_new,
            )
        )

        # Out-of-order ISO timestamps, line_comment_count == 1 boundary, and [1.0, 5.0] score bounds
        out_of_order_ts_reviews = [
            {
                "author_login": "bob",
                "state": "COMMENTED",
                "commit": {"oid": sha_new},
                "submittedAt": "2026-11-18T12:00:00Z",
                "line_comment_count": 1,
                "scores": {
                    "fidelity": 1.0,
                    "fluency": 5.0,
                    "terminology": 3.0,
                    "register": 3.0,
                    "structure": 3.0,
                    "completeness": 3.0,
                },
                "body": "Inline feedback provided.",
            },
            {
                "author_login": "bob",
                "state": "CHANGES_REQUESTED",
                "commit": {"oid": sha_old},
                "submittedAt": "2026-11-18T10:00:00Z",
                "body": "Earlier block",
            },
        ]
        self.assertFalse(
            has_blocking_changes_requested(
                reviews=out_of_order_ts_reviews,
                comments=[],
                pr_author="alice",
                bot_login="bot",
                human_approver="owner",
                current_head_oid=sha_new,
            )
        )

        # Out-of-range score (< 1.0 or > 5.0) in subsequent review does NOT clear block
        for bad_val in (0.9, 5.1):
            bad_score_revs = [
                {"author_login": "bob", "state": "CHANGES_REQUESTED", "commit": {"oid": sha_old}},
                {
                    "author_login": "bob",
                    "state": "COMMENTED",
                    "commit": {"oid": sha_new},
                    "line_comment_count": 1,
                    "scores": {
                        "fidelity": bad_val,
                        "fluency": 4.0,
                        "terminology": 4.0,
                        "register": 4.0,
                        "structure": 4.0,
                        "completeness": 4.0,
                    },
                    "body": "Invalid score range",
                },
            ]
            self.assertTrue(
                has_blocking_changes_requested(
                    reviews=bad_score_revs,
                    comments=[],
                    pr_author="alice",
                    bot_login="bot",
                    human_approver="owner",
                    current_head_oid=sha_new,
                )
            )

    def test_verdict_candidate_pass(self):
        eval_result = {
            "weighted_total": 4.5,
            "verdict": "PASS",
            "scores": {
                "fidelity": 4.5,
                "fluency": 4.5,
                "terminology": 4.5,
                "register": 4.5,
                "structure": 4.5,
                "completeness": 4.5,
            },
            "issues": [],
        }
        res = compute_collab_verdict(eval_result, pass_threshold=4.0)
        self.assertEqual(res["outcome"], "CANDIDATE")
        self.assertEqual(res["weighted_total"], 4.5)
        self.assertEqual(res["effective_threshold"], 4.0)
        self.assertEqual(res["next_round"], 0)

    def test_verdict_candidate_literary_genre_threshold_and_normalization(self):
        book_record = {"brief": {"genre": "fiction"}}
        # Even if evaluator returned internal FAIL for 3.6, normalize_evaluation_result upgrades to PASS >= 3.5
        eval_result = {
            "weighted_total": 3.6,
            "verdict": "FAIL",
            "dimension_scores": {
                "fidelity": 3.6,
                "fluency": 3.6,
                "terminology": 3.6,
                "register": 3.6,
                "structure": 3.6,
                "completeness": 3.6,
            },
            "issues": [],
        }
        res = compute_collab_verdict(eval_result, book_record=book_record)
        self.assertEqual(res["outcome"], "CANDIDATE")
        self.assertEqual(res["effective_threshold"], 3.5)

        book_record_root = {"genre": "literary"}
        res_root = compute_collab_verdict(eval_result, book_record=book_record_root)
        self.assertEqual(res_root["outcome"], "CANDIDATE")
        self.assertEqual(res_root["effective_threshold"], 3.5)

    def test_verdict_revise_minor_defects(self):
        eval_result = {
            "weighted_total": 3.3,
            "verdict": "FAIL",
            "scores": {
                "fidelity": 3.3,
                "fluency": 3.3,
                "terminology": 3.3,
                "register": 3.3,
                "structure": 3.3,
                "completeness": 3.3,
            },
            "issues": [{"severity": "minor", "description": "typo in heading"}],
        }
        res = compute_collab_verdict(eval_result, pass_threshold=4.0, revise_round_count=0)
        self.assertEqual(res["outcome"], "REVISE")
        self.assertEqual(res["next_round"], 1)

    def test_verdict_blocking_changes_requested_forces_revise_until_dismissed(self):
        sha = "a" * 40
        eval_result = {
            "weighted_total": 4.6,
            "verdict": "PASS",
            "dimension_scores": {
                "fidelity": 4.6,
                "fluency": 4.6,
                "terminology": 4.6,
                "register": 4.6,
                "structure": 4.6,
                "completeness": 4.6,
            },
            "issues": [],
        }
        reviews = [{"author_login": "bob", "state": "CHANGES_REQUESTED", "commit": {"oid": sha}}]
        res_blocked = compute_collab_verdict(
            eval_result,
            pass_threshold=4.0,
            reviews=reviews,
            comments=[],
            pr_author="alice",
            bot_login="bot",
            human_approver="owner",
            current_head_oid=sha,
        )
        self.assertEqual(res_blocked["outcome"], "REVISE")

        res_dismissed = compute_collab_verdict(
            eval_result,
            pass_threshold=4.0,
            reviews=reviews,
            comments=[{"author_login": "owner", "body": f"/dismiss-stale-review bob {sha}"}],
            pr_author="alice",
            bot_login="bot",
            human_approver="owner",
            current_head_oid=sha,
        )
        self.assertEqual(res_dismissed["outcome"], "CANDIDATE")

    def test_verdict_reassign_low_score_redraft(self):
        eval_result = {
            "weighted_total": 2.0,
            "verdict": "FAIL",
            "scores": {"fidelity": 2.0},
            "issues": [{"severity": "major", "description": "hallucinated paragraph"}],
        }
        res = compute_collab_verdict(eval_result, pass_threshold=4.0, revise_round_count=1)
        self.assertEqual(res["outcome"], "REASSIGN")

    def test_verdict_reassign_max_rounds_reached(self):
        eval_result = {
            "weighted_total": 3.5,
            "verdict": "FAIL",
            "scores": {"fidelity": 3.5},
            "issues": [{"severity": "minor", "description": "minor punctuation"}],
        }
        res = compute_collab_verdict(
            eval_result,
            pass_threshold=4.0,
            revise_round_count=3,
            max_revise_rounds=3,
        )
        self.assertEqual(res["outcome"], "REASSIGN")
        self.assertIn("maximum revision rounds", res["reason"].lower())

    def test_verdict_critical_issue_forces_fail(self):
        eval_result = {
            "weighted_total": 4.6,
            "verdict": "PASS",
            "scores": {"fidelity": 4.6},
            "issues": [{"severity": "critical", "description": "missing chapter section"}],
        }
        res = compute_collab_verdict(eval_result, pass_threshold=4.0, revise_round_count=0)
        self.assertEqual(res["outcome"], "REVISE")

    def test_verdict_transient_errors_and_fallback_keep_evaluating(self):
        # 1. fallback_unverified == True keeps PR in EVALUATING without incrementing round or closing
        eval_fallback = {
            "weighted_total": 4.5,
            "fallback_unverified": True,
            "verdict": "PASS",
            "scores": {"fidelity": 4.5},
            "issues": [],
        }
        res_fb = compute_collab_verdict(eval_fallback, pass_threshold=4.0, revise_round_count=2)
        self.assertEqual(res_fb["outcome"], "EVALUATING")
        self.assertEqual(res_fb["next_round"], 2)
        self.assertFalse(res_fb["should_close"])

        # 2. Evaluator exception / timeout / missing binary keeps PR in EVALUATING
        res_exc = compute_collab_verdict(
            None,
            eval_error=TimeoutError("evaluator timed out after 60s"),
            pass_threshold=4.0,
            revise_round_count=1,
        )
        self.assertEqual(res_exc["outcome"], "EVALUATING")
        self.assertEqual(res_exc["next_round"], 1)
        self.assertFalse(res_exc["should_close"])

        # 3. Passing exception directly as eval_result or error dict also keeps EVALUATING
        res_err_dict = compute_collab_verdict({"error": "JSONDecodeError"}, pass_threshold=4.0, revise_round_count=1)
        self.assertEqual(res_err_dict["outcome"], "EVALUATING")
        self.assertEqual(res_err_dict["next_round"], 1)
        self.assertFalse(res_err_dict["should_close"])

    def test_verdict_flagged_for_human_injection_and_clear_reject(self):
        sha = "f" * 40
        eval_result = {
            "weighted_total": 4.5,
            "verdict": "PASS",
            "scores": {"fidelity": 4.5},
            "issues": [],
        }
        res = compute_collab_verdict(
            eval_result,
            pass_threshold=4.0,
            injection_flags=["Prompt injection detected: 'ignore previous instructions'"],
        )
        self.assertEqual(res["outcome"], "FLAGGED_FOR_HUMAN")
        self.assertIn("injection", res["reason"].lower())

        # /clear-flag <sha> by human_approver resumes routing to CANDIDATE
        res_cleared = compute_collab_verdict(
            eval_result,
            pass_threshold=4.0,
            injection_flags=["Prompt injection detected: 'ignore previous instructions'"],
            comments=[{"author_login": "owner", "body": f"/clear-flag {sha}"}],
            human_approver="owner",
            current_head_oid=sha,
        )
        self.assertEqual(res_cleared["outcome"], "CANDIDATE")

        # /reject-flag <sha> by human_approver closes as REASSIGN
        res_rejected = compute_collab_verdict(
            eval_result,
            pass_threshold=4.0,
            injection_flags=["Prompt injection detected: 'ignore previous instructions'"],
            comments=[{"author_login": "owner", "body": f"/reject-flag {sha}"}],
            human_approver="owner",
            current_head_oid=sha,
        )
        self.assertEqual(res_rejected["outcome"], "REASSIGN")
        self.assertTrue(res_rejected["should_close"])

    def test_verdict_flagged_for_human_reviewer_divergence(self):
        eval_result = {
            "weighted_total": 4.0,
            "verdict": "PASS",
            "dimension_scores": {
                "fidelity": 3.2,
                "fluency": 4.0,
                "terminology": 4.0,
                "register": 4.0,
                "structure": 4.0,
                "completeness": 4.0,
            },
            "issues": [],
        }
        reviewer_scores = [
            {"fidelity": 3.8, "fluency": 4.0, "terminology": 4.0, "register": 4.0, "structure": 4.0, "completeness": 4.0},
            {"fidelity": 4.8, "fluency": 4.0, "terminology": 4.0, "register": 4.0, "structure": 4.0, "completeness": 4.0},
        ]
        res = compute_collab_verdict(
            eval_result,
            reviewer_scores=reviewer_scores,
            pass_threshold=4.0,
        )
        self.assertEqual(res["outcome"], "FLAGGED_FOR_HUMAN")
        self.assertTrue("divergence" in res["reason"].lower() or "fidelity" in res["reason"].lower())

    def test_score_exactly_equal_threshold_passes(self):
        eval_result = {
            "weighted_total": 4.0,
            "verdict": "PASS",
            "scores": {"fidelity": 4.0},
            "issues": [],
        }
        res = compute_collab_verdict(eval_result, pass_threshold=4.0)
        self.assertEqual(res["outcome"], "CANDIDATE")

    def test_reviewer_divergence_exactly_one_point_does_not_flag(self):
        eval_result = {
            "weighted_total": 4.0,
            "verdict": "PASS",
            "scores": {"fidelity": 3.0},
            "issues": [],
        }
        reviewer_scores = {"fidelity": 4.0}
        res = compute_collab_verdict(eval_result, reviewer_scores=reviewer_scores, pass_threshold=4.0)
        self.assertEqual(res["outcome"], "CANDIDATE")

    def test_major_issue_does_not_count_as_critical_when_score_passes(self):
        eval_result = {
            "weighted_total": 4.2,
            "verdict": "PASS",
            "scores": {"fidelity": 4.2},
            "issues": [{"severity": "major", "description": "minor omission"}],
        }
        res = compute_collab_verdict(eval_result, pass_threshold=4.0)
        self.assertEqual(res["outcome"], "CANDIDATE")

    def test_contested_verdict_forces_failure_even_if_score_passes(self):
        eval_result = {
            "weighted_total": 4.8,
            "verdict": "CONTESTED",
            "scores": {"fidelity": 4.8},
            "issues": [{"severity": "minor", "description": "peer debate on terminology"}],
        }
        res = compute_collab_verdict(eval_result, pass_threshold=4.0, draft_text_or_len=1000)
        self.assertEqual(res["outcome"], "REVISE")
        self.assertEqual(res["next_round"], 1)

    def test_filter_fresh_valid_reviews_filters_author_and_stale_commit(self):
        reviews = [
            # Self-review: must be excluded
            {"author_login": "alice", "body": VALID_RUBRIC_BODY, "commit_oid": "head123"},
            # Valid review on stale commit: must be excluded
            {"author_login": "bob", "body": VALID_RUBRIC_BODY, "commit_oid": "old123", "submitted_at": 1000},
            # Valid review on head commit: must be kept
            {"author_login": "bob", "body": VALID_RUBRIC_BODY, "commit_oid": "head123", "submitted_at": 2000},
            # Invalid review (no rubric): must be excluded
            {"author_login": "carol", "body": "looks fine S-0001", "commit_oid": "head123"},
        ]
        fresh = filter_fresh_valid_reviews(reviews, pr_author="alice", head_oid="head123")
        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0]["author_login"], "bob")
        self.assertEqual(fresh[0]["submitted_at"], 2000)

    def test_evaluate_pr_from_reviews_pass_clean(self):
        rev1 = {
            "author_login": "bob",
            "body": (
                "Review S-0001:\n"
                "- Fidelity: 4.8/5\n"
                "- Fluency: 4.6/5\n"
                "- Terminology: 4.7/5\n"
                "- Register: 4.5/5\n"
                "- Structure: 4.5/5\n"
                "- Completeness: 4.5/5\n"
            ),
            "commit_oid": "head123",
        }
        xml = '<segment id="S-0001" type="paragraph"><original>Hello</original><translate>Xin chào bạn.</translate></segment>'
        res = evaluate_pr_from_reviews(
            reviews=[rev1],
            pr_author="alice",
            head_oid="head123",
            translation_xml=xml,
            pass_threshold=4.0,
        )
        self.assertEqual(res["verdict"], "PASS")
        self.assertFalse(res["fallback_unverified"])
        self.assertGreaterEqual(res["score_of_record"], 4.5)
        self.assertEqual(res["critical_issue_count"], 0)

    def test_evaluate_pr_from_reviews_fails_on_untranslated_or_critical(self):
        rev_crit = {
            "author_login": "bob",
            "body": (
                "Review S-0001:\n"
                "- Fidelity: 4.5/5\n"
                "- Fluency: 4.5/5\n"
                "- Terminology: 4.5/5\n"
                "- Register: 4.5/5\n"
                "- Structure: 4.5/5\n"
                "- Completeness: 4.5/5\n"
                "[critical] S-0001 Missing entire sentence!\n"
            ),
            "commit_oid": "head123",
        }
        xml = '<segment id="S-0001" type="paragraph"><original>Hello</original><translate>Xin chào.</translate></segment>'
        res = evaluate_pr_from_reviews(
            reviews=[rev_crit],
            pr_author="alice",
            head_oid="head123",
            translation_xml=xml,
        )
        self.assertEqual(res["verdict"], "FAIL")
        self.assertGreaterEqual(res["critical_issue_count"], 1)

    def test_evaluate_pr_from_reviews_fails_on_language_detection(self):
        rev1 = {
            "author_login": "bob",
            "body": VALID_RUBRIC_BODY,
            "commit_oid": "head123",
        }
        # English text left untranslated
        xml = '<segment id="S-0001" type="paragraph"><original>The Prince and Princess sat at the top.</original><translate>The Prince and Princess sat at the top.</translate></segment>'
        res = evaluate_pr_from_reviews(
            reviews=[rev1],
            pr_author="alice",
            head_oid="head123",
            translation_xml=xml,
        )
        self.assertEqual(res["verdict"], "FAIL")
        self.assertGreaterEqual(res["critical_issue_count"], 1)

    def test_evaluate_pr_from_reviews_needs_review_when_empty(self):
        res = evaluate_pr_from_reviews(
            reviews=[],
            pr_author="alice",
            head_oid="head123",
            translation_xml="<translate>Xin chào</translate>",
        )
        self.assertEqual(res["verdict"], "NEEDS_REVIEW")


if __name__ == "__main__":
    unittest.main()
