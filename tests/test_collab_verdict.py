"""
tests/test_collab_verdict.py — Unit tests for collaborative translation verdict computation.
Tests REQ-COLLAB-006, REQ-COLLAB-014.
"""

import unittest

from scripts.collab_verdict import compute_collab_verdict


class TestCollabVerdict(unittest.TestCase):

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

    def test_verdict_candidate_literary_genre_threshold(self):
        book_record = {"brief": {"genre": "literary fiction"}}
        eval_result = {
            "weighted_total": 3.7,
            "verdict": "PASS",
            "scores": {
                "fidelity": 3.7,
                "fluency": 3.7,
                "terminology": 3.7,
                "register": 3.7,
                "structure": 3.7,
                "completeness": 3.7,
            },
            "issues": [],
        }
        res = compute_collab_verdict(eval_result, book_record=book_record)
        self.assertEqual(res["outcome"], "CANDIDATE")
        self.assertEqual(res["effective_threshold"], 3.5)

        # Also verify root genre field
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

    def test_verdict_reassign_low_score_redraft(self):
        eval_result = {
            "weighted_total": 2.0,
            "verdict": "FAIL",
            "scores": {"fidelity": 2.0},
            "issues": [{"severity": "major", "description": "hallucinated paragraph"}],
        }
        res = compute_collab_verdict(eval_result, pass_threshold=4.0, revise_round_count=1)
        self.assertEqual(res["outcome"], "REASSIGN")
        self.assertTrue(res["next_round"] >= 2)

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
        self.assertTrue("maximum revision rounds" in res["reason"].lower())

    def test_verdict_critical_issue_forces_fail(self):
        eval_result = {
            "weighted_total": 4.6,
            "verdict": "FAIL",
            "scores": {"fidelity": 4.6},
            "issues": [{"severity": "critical", "description": "missing chapter section"}],
        }
        res = compute_collab_verdict(eval_result, pass_threshold=4.0, revise_round_count=0)
        self.assertIn(res["outcome"], ("REVISE", "REASSIGN"))

    def test_verdict_blocked_unverified_fallback(self):
        eval_result = {
            "weighted_total": 4.5,
            "fallback_unverified": True,
            "verdict": "PASS",
            "scores": {"fidelity": 4.5},
            "issues": [],
        }
        res = compute_collab_verdict(eval_result, pass_threshold=4.0)
        self.assertEqual(res["outcome"], "BLOCKED_UNVERIFIED")

    def test_verdict_flagged_for_human_injection(self):
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
        self.assertTrue("injection" in res["reason"].lower())

    def test_verdict_flagged_for_human_reviewer_divergence(self):
        eval_result = {
            "weighted_total": 4.0,
            "verdict": "PASS",
            "scores": {
                "fidelity": 3.2,
                "fluency": 4.0,
                "terminology": 4.0,
                "register": 4.0,
                "structure": 4.0,
                "completeness": 4.0,
            },
            "issues": [],
        }
        reviewer_scores = {
            "fidelity": 4.8,  # diff = 1.6 > 1.0
            "fluency": 4.0,
            "terminology": 4.0,
            "register": 4.0,
            "structure": 4.0,
            "completeness": 4.0,
        }
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
        reviewer_scores = {"fidelity": 4.0}  # diff = 1.0 (<= 1.0, does not exceed 1.0)
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



if __name__ == "__main__":
    unittest.main()
