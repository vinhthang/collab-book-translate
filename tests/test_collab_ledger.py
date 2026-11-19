import json
from pathlib import Path
import pytest
from scripts.collab_ledger import (
    REQUIRED_LEDGER_FIELDS,
    compute_reviewer_score,
    read_ledger,
    append_ledger_entry,
    verify_ledger_integrity,
)


def test_required_ledger_fields():
    expected = (
        "chapter",
        "issue_number",
        "selected_pr",
        "rejected_prs",
        "translator_login",
        "reviewer_logins",
        "reviewer_score",
        "score_of_record",
        "verdict",
        "merge_sha",
        "glossary_sha",
    )
    assert REQUIRED_LEDGER_FIELDS == expected


def test_read_ledger_empty(tmp_path: Path):
    ledger_path = tmp_path / "collab_ledger.json"
    data = read_ledger(ledger_path)
    assert data == {"book_slug": "", "chapters": {}, "released_tag": None}


def test_compute_reviewer_score_deterministic():
    # ch01 with empty reviewer_logins ALWAYS returns score_of_record even if stray review dicts exist
    assert compute_reviewer_score(
        "ch01",
        score_of_record=4.65,
        reviewer_logins=[],
        valid_reviews=[{"author_login": "stray", "scores": {"fidelity": 2.0}}],
    ) == pytest.approx(4.65)

    # ch02 with multiple reviewers uses chronologically latest valid 6-dim mean per reviewer in reviewer_logins
    valid_reviews = [
        {
            "author_login": "bob",
            "scores": {
                "fidelity": 3.0,
                "fluency": 3.0,
                "terminology": 3.0,
                "register": 3.0,
                "structure": 3.0,
                "completeness": 3.0,
            },
        },
        {
            "author_login": "carol",
            "scores": {
                "fidelity": 4.0,
                "fluency": 4.0,
                "terminology": 4.0,
                "register": 4.0,
                "structure": 4.0,
                "completeness": 4.0,
            },
        },
        {
            "author_login": "bob",
            "scores": {
                "fidelity": 5.0,
                "fluency": 5.0,
                "terminology": 5.0,
                "register": 5.0,
                "structure": 5.0,
                "completeness": 5.0,
            },
        },
    ]
    score = compute_reviewer_score(
        "ch02",
        score_of_record=4.2,
        reviewer_logins=["carol", "bob"],
        valid_reviews=valid_reviews,
    )
    assert score == pytest.approx(4.5)


def test_append_ledger_entry_sorts_lists_and_enforces_v2_rules(tmp_path: Path):
    ledger_path = tmp_path / "collab_ledger.json"
    entry_ch01 = {
        "chapter": "ch01",
        "issue_number": 0,
        "selected_pr": 1,
        "rejected_prs": [],
        "translator_login": "main-bot",
        "reviewer_logins": [],
        "reviewer_score": 4.8,
        "score_of_record": 4.8,
        "verdict": "PASS",
        "merge_sha": "a" * 40,
        "glossary_sha": "b" * 64,
    }
    updated = append_ledger_entry(ledger_path, "test-book", entry_ch01)
    assert updated["book_slug"] == "test-book"
    assert updated["released_tag"] is None
    assert updated["chapters"]["ch01"]["selected_pr"] == 1

    entry_ch02 = {
        "chapter": "ch02",
        "issue_number": 12,
        "selected_pr": 20,
        "rejected_prs": [25, 18, 25],
        "translator_login": "alice",
        "reviewer_logins": ["zoe", "bob"],
        "reviewer_score": 4.5,
        "score_of_record": 4.6,
        "verdict": "PASS",
        "merge_sha": "c" * 40,
        "glossary_sha": "b" * 64,
    }
    updated2 = append_ledger_entry(ledger_path, "test-book", entry_ch02)
    assert updated2["chapters"]["ch02"]["rejected_prs"] == [18, 25]
    assert updated2["chapters"]["ch02"]["reviewer_logins"] == ["bob", "zoe"]

    bad_v2_ch01 = dict(entry_ch01, selected_pr=0)
    with pytest.raises(ValueError, match="selected_pr"):
        append_ledger_entry(tmp_path / "v2_bad.json", "test-book", bad_v2_ch01)

    # Boundary tests on append_ledger_entry: issue_number > 0, selected_pr > 0, rejected_prs > 0, scores in [0.0, 5.0], auto reviewer_score
    auto_entry = dict(entry_ch02, chapter="ch03", issue_number=1, selected_pr=1, rejected_prs=[2], reviewer_score=None, score_of_record=0.0)
    res_auto = append_ledger_entry(
        ledger_path,
        "test-book",
        auto_entry,
        valid_reviews=[{"author_login": "bob", "scores": {"fidelity": 5.0}}, {"author_login": "zoe", "scores": {"fidelity": 5.0}}],
    )
    assert res_auto["chapters"]["ch03"]["reviewer_score"] == pytest.approx(5.0)
    assert res_auto["chapters"]["ch03"]["score_of_record"] == pytest.approx(0.0)

    # Exact 0.0 reviewer_score boundary succeeds
    zero_rev_entry = dict(entry_ch02, chapter="ch04", issue_number=2, selected_pr=3, rejected_prs=[], reviewer_score=0.0, score_of_record=5.0)
    res_zero = append_ledger_entry(ledger_path, "test-book", zero_rev_entry)
    assert res_zero["chapters"]["ch04"]["reviewer_score"] == pytest.approx(0.0)

    with pytest.raises(ValueError, match="issue_number"):
        append_ledger_entry(ledger_path, "test-book", dict(entry_ch02, issue_number=0))
    with pytest.raises(ValueError, match="selected_pr"):
        append_ledger_entry(ledger_path, "test-book", dict(entry_ch02, selected_pr=0))
    with pytest.raises(ValueError, match="rejected_prs"):
        append_ledger_entry(ledger_path, "test-book", dict(entry_ch02, rejected_prs=[0]))
    for bad_score in (-0.1, 5.1):
        with pytest.raises(ValueError, match="score_of_record"):
            append_ledger_entry(ledger_path, "test-book", dict(entry_ch02, score_of_record=bad_score))
        with pytest.raises(ValueError, match="reviewer_score"):
            append_ledger_entry(ledger_path, "test-book", dict(entry_ch02, reviewer_score=bad_score))


def test_append_ledger_entry_ch02_validation(tmp_path: Path):
    ledger_path = tmp_path / "collab_ledger.json"

    with pytest.raises(ValueError, match="missing required field"):
        append_ledger_entry(ledger_path, "test-book", {"chapter": "ch02", "issue_number": 42})

    bad_entry_ch02 = {
        "chapter": "ch02",
        "issue_number": 42,
        "selected_pr": 43,
        "rejected_prs": [44],
        "translator_login": "alice",
        "reviewer_logins": [],
        "reviewer_score": 4.5,
        "score_of_record": 4.6,
        "verdict": "PASS",
        "merge_sha": "abc1234",
        "glossary_sha": "def5678",
    }
    with pytest.raises(ValueError, match="reviewer_logins"):
        append_ledger_entry(ledger_path, "test-book", bad_entry_ch02)

    bad_entry_sha = dict(bad_entry_ch02, reviewer_logins=["bob"], merge_sha="")
    with pytest.raises(ValueError, match="merge_sha"):
        append_ledger_entry(ledger_path, "test-book", bad_entry_sha)


def test_verify_ledger_integrity_published_pilot_scandal_in_bohemia():
    pilot_ledger_path = Path(__file__).resolve().parent / "fixtures" / "runs" / "scandal-in-bohemia" / "collab_ledger.json"
    pilot_ledger = json.loads(pilot_ledger_path.read_text(encoding="utf-8"))

    res = verify_ledger_integrity(pilot_ledger, ["ch01", "ch02", "ch03"])
    assert res["valid"] is True, f"Pilot ledger failed: {res}"
    assert res["unaudited_chapters"] == []
    assert res["defects"] == []


def test_verify_ledger_integrity_v2_and_grandfather_boundaries():
    ch01_zero_pr = {
        "chapter": "ch01",
        "issue_number": 0,
        "selected_pr": 0,
        "rejected_prs": [],
        "translator_login": "bot",
        "reviewer_logins": [],
        "reviewer_score": 4.6,
        "score_of_record": 4.6,
        "verdict": "PASS",
        "merge_sha": "same_sha_123",
        "glossary_sha": "same_sha_123",
    }

    unreleased = {
        "book_slug": "new-book",
        "chapters": {"ch01": ch01_zero_pr},
        "released_tag": None,
    }
    res_unrel = verify_ledger_integrity(unreleased, ["ch01"])
    assert res_unrel["valid"] is False
    assert "ch01" in res_unrel["unaudited_chapters"]
    assert any("selected_pr" in d for d in res_unrel["defects"])

    mismatch_sha = {
        "book_slug": "new-book",
        "chapters": {"ch01": dict(ch01_zero_pr, merge_sha="different_sha")},
        "released_tag": "new-book-v1.0",
    }
    res_mismatch = verify_ledger_integrity(mismatch_sha, ["ch01"])
    assert res_mismatch["valid"] is False
    assert "ch01" in res_mismatch["unaudited_chapters"]

    grandfathered = {
        "book_slug": "legacy-book",
        "chapters": {"ch01": ch01_zero_pr},
        "released_tag": "legacy-book-v1.0",
    }
    res_gf = verify_ledger_integrity(grandfathered, ["ch01"])
    assert res_gf["valid"] is True

    v2_valid = {
        "book_slug": "v2-book",
        "chapters": {"ch01": dict(ch01_zero_pr, selected_pr=5, merge_sha="m1", glossary_sha="g1")},
        "released_tag": None,
    }
    res_v2 = verify_ledger_integrity(v2_valid, ["ch01"])
    assert res_v2["valid"] is True


def test_verify_ledger_integrity_defects_and_top_level_schema():
    res_bad_top = verify_ledger_integrity({"book_slug": "", "chapters": {}}, ["ch01"])
    assert res_bad_top["valid"] is False
    assert "ch01" in res_bad_top["unaudited_chapters"]
    assert any("book_slug" in d for d in res_bad_top["defects"])

    ledger = {
        "book_slug": "test-book",
        "chapters": {
            "ch01": {
                "chapter": "ch01",
                "issue_number": 0,
                "selected_pr": 1,
                "rejected_prs": [],
                "translator_login": "main-bot",
                "reviewer_logins": [],
                "reviewer_score": 5.0,
                "score_of_record": 5.0,
                "verdict": "PASS",
                "merge_sha": "sha1",
                "glossary_sha": "gsha1",
            },
            "ch02": {
                "chapter": "ch02",
                "issue_number": 0,
                "selected_pr": 0,
                "rejected_prs": [20, 15],
                "translator_login": "alice",
                "reviewer_logins": ["alice"],
                "reviewer_score": 4.5,
                "score_of_record": 4.6,
                "verdict": "FAIL",
                "merge_sha": "",
                "glossary_sha": "gsha1",
            },
        },
        "released_tag": None,
    }
    res = verify_ledger_integrity(ledger, ["ch01", "ch02", "ch03"])
    assert res["valid"] is False
    assert "ch02" in res["unaudited_chapters"]
    assert "ch03" in res["unaudited_chapters"]
    assert any("translator_login" in d for d in res["defects"])
    assert any("issue_number" in d for d in res["defects"])
    assert any("selected_pr" in d for d in res["defects"])
    assert any("rejected_prs" in d for d in res["defects"])
    assert any("verdict" in d for d in res["defects"])
    assert any("merge_sha" in d for d in res["defects"])


def test_verify_ledger_integrity_non_list_reviewer_logins():
    ledger = {
        "book_slug": "test-book",
        "chapters": {
            "ch02": {
                "chapter": "ch02",
                "issue_number": 10,
                "selected_pr": 12,
                "rejected_prs": [],
                "translator_login": "alice",
                "reviewer_logins": None,
                "reviewer_score": 4.5,
                "score_of_record": 4.5,
                "verdict": "PASS",
                "merge_sha": "sha2",
                "glossary_sha": "gsha1",
            }
        },
    }
    res = verify_ledger_integrity(ledger, ["ch02"])
    assert res["valid"] is False
    assert "ch02" in res["unaudited_chapters"]
    assert any("reviewer_logins" in d for d in res["defects"])
