from pathlib import Path
import pytest
from scripts.collab_ledger import (
    REQUIRED_LEDGER_FIELDS,
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
    assert data == {"book_slug": "", "chapters": {}}


def test_append_ledger_entry_success(tmp_path: Path):
    ledger_path = tmp_path / "collab_ledger.json"
    entry_ch01 = {
        "chapter": "ch01",
        "issue_number": 0,
        "selected_pr": 1,
        "rejected_prs": [],
        "translator_login": "main-bot",
        "reviewer_logins": [],  # Allowed empty for ch01 seed
        "reviewer_score": 5.0,
        "score_of_record": 5.0,
        "verdict": "PASS",
        "merge_sha": "abc1234",
        "glossary_sha": "def5678",
    }
    updated = append_ledger_entry(ledger_path, "test-book", entry_ch01)
    assert updated["book_slug"] == "test-book"
    assert "ch01" in updated["chapters"]
    assert updated["chapters"]["ch01"]["merge_sha"] == "abc1234"

    # Now read back from disk
    from_disk = read_ledger(ledger_path)
    assert from_disk == updated


def test_append_ledger_entry_ch02_validation(tmp_path: Path):
    ledger_path = tmp_path / "collab_ledger.json"

    # Missing field
    bad_entry = {
        "chapter": "ch02",
        "issue_number": 42,
    }
    with pytest.raises(ValueError, match="missing required field"):
        append_ledger_entry(ledger_path, "test-book", bad_entry)

    # Empty reviewer_logins on ch02
    bad_entry_ch02 = {
        "chapter": "ch02",
        "issue_number": 42,
        "selected_pr": 43,
        "rejected_prs": [44],
        "translator_login": "alice",
        "reviewer_logins": [],  # invalid for ch02!
        "reviewer_score": 4.5,
        "score_of_record": 4.6,
        "verdict": "PASS",
        "merge_sha": "abc1234",
        "glossary_sha": "def5678",
    }
    with pytest.raises(ValueError, match="reviewer_logins"):
        append_ledger_entry(ledger_path, "test-book", bad_entry_ch02)

    # Empty merge_sha
    bad_entry_sha = dict(bad_entry_ch02, reviewer_logins=["bob"], merge_sha="")
    with pytest.raises(ValueError, match="merge_sha"):
        append_ledger_entry(ledger_path, "test-book", bad_entry_sha)


def test_verify_ledger_integrity_pass():
    ledger = {
        "book_slug": "test-book",
        "chapters": {
            "ch01": {
                "chapter": "ch01",
                "issue_number": "seed",
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
                "issue_number": 10,
                "selected_pr": 12,
                "rejected_prs": [11],
                "translator_login": "alice",
                "reviewer_logins": ["bob"],
                "reviewer_score": 4.5,
                "score_of_record": 4.6,
                "verdict": "PASS",
                "merge_sha": "sha2",
                "glossary_sha": "gsha1",
            },
        },
    }
    res = verify_ledger_integrity(ledger, ["ch01", "ch02"])
    assert res["valid"] is True
    assert res["unaudited_chapters"] == []
    assert res["defects"] == []


def test_verify_ledger_integrity_defects():
    ledger = {
        "book_slug": "test-book",
        "chapters": {
            "ch01": {
                "chapter": "ch01",
                "issue_number": "seed",
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
                "issue_number": 0,  # Invalid: issue_number must be > 0 for ch02
                "selected_pr": 0,  # Invalid: selected_pr must be > 0
                "rejected_prs": [],
                "translator_login": "alice",
                "reviewer_logins": ["alice"],  # Invalid: translator cannot be reviewer
                "reviewer_score": 4.5,
                "score_of_record": 4.6,
                "verdict": "PASS",
                "merge_sha": "",  # Invalid: empty sha
                "glossary_sha": "gsha1",
            },
        },
    }
    res = verify_ledger_integrity(ledger, ["ch01", "ch02", "ch03"])
    assert res["valid"] is False
    assert "ch03" in res["unaudited_chapters"]
    assert any("ch03" in d for d in res["defects"])
    assert any("translator_login" in d for d in res["defects"])
    assert any("issue_number" in d for d in res["defects"])
    assert any("selected_pr" in d for d in res["defects"])
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
                "reviewer_logins": None,  # Non-list
                "reviewer_score": 4.5,
                "score_of_record": 4.5,
                "verdict": "PASS",
                "merge_sha": "sha2",
                "glossary_sha": "gsha1",
            }
        }
    }
    # Should not raise TypeError, but return valid=False with defect (P2-1)
    res = verify_ledger_integrity(ledger, ["ch02"])
    assert res["valid"] is False
    assert any("reviewer_logins" in d for d in res["defects"])


def test_verify_ledger_integrity_boundary_zeros():
    base_entry = {
        "chapter": "ch02",
        "issue_number": 10,
        "selected_pr": 12,
        "rejected_prs": [],
        "translator_login": "alice",
        "reviewer_logins": ["bob"],
        "reviewer_score": 4.5,
        "score_of_record": 4.5,
        "verdict": "PASS",
        "merge_sha": "sha2",
        "glossary_sha": "gsha1",
    }
    # Test issue_number == 0 specifically
    ledger1 = {"chapters": {"ch02": dict(base_entry, issue_number=0)}}
    res1 = verify_ledger_integrity(ledger1, ["ch02"])
    assert res1["valid"] is False
    assert any("issue_number must be > 0" in d for d in res1["defects"])

    # Test selected_pr == 0 specifically
    ledger2 = {"chapters": {"ch02": dict(base_entry, selected_pr=0)}}
    res2 = verify_ledger_integrity(ledger2, ["ch02"])
    assert res2["valid"] is False
    assert any("selected_pr must be > 0" in d for d in res2["defects"])

    # Test ch01 issue_number == 0 is valid for seed
    ch01_entry = dict(base_entry, chapter="ch01", issue_number=0, selected_pr=1, reviewer_logins=[])
    ledger_ch01 = {"chapters": {"ch01": ch01_entry}}
    res_ch01 = verify_ledger_integrity(ledger_ch01, ["ch01"])
    assert res_ch01["valid"] is True
    assert res_ch01["defects"] == []
