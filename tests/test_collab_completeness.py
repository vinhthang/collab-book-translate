import json
import subprocess
from pathlib import Path
import pytest

from scripts.collab_completeness import (
    check_git_tracked_binaries,
    verify_book_completeness,
)


def _setup_chapter(run_dir: Path, ch_id: str, content: str = "Sample content"):
    ch_dir = run_dir / ch_id
    ch_dir.mkdir(parents=True, exist_ok=True)
    source_txt = (
        f'<segment id="S-0001" type="paragraph">\n'
        f'<original>{content}</original>\n'
        f'</segment>'
    )
    (ch_dir / "source.txt").write_text(source_txt, encoding="utf-8")
    (ch_dir / "final.txt").write_text(f"Dịch: {content}", encoding="utf-8")
    trans_xml = (
        f'<segment id="S-0001" type="paragraph">\n'
        f'<original>{content}</original>\n'
        f'<translate>Dịch: {content}</translate>\n'
        f'</segment>'
    )
    (ch_dir / "translation.xml").write_text(trans_xml, encoding="utf-8")


def test_verify_book_completeness_happy_path(tmp_path: Path):
    run_dir = tmp_path / "runs" / "test-slug"
    _setup_chapter(run_dir, "ch01", "Chapter one")
    _setup_chapter(run_dir, "ch02", "Chapter two")

    checklist = {
        "chapters": {
            "ch01": {"status": "done"},
            "ch02": {"status": "accepted"},
        }
    }
    (run_dir / "checklist.json").write_text(json.dumps(checklist), encoding="utf-8")

    ledger = {
        "book_slug": "test-slug",
        "chapters": {
            "ch01": {
                "chapter": "ch01",
                "issue_number": "seed",
                "selected_pr": 1,
                "rejected_prs": [],
                "translator_login": "bot",
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
                "rejected_prs": [],
                "translator_login": "alice",
                "reviewer_logins": ["bob"],
                "reviewer_score": 4.5,
                "score_of_record": 4.5,
                "verdict": "PASS",
                "merge_sha": "sha2",
                "glossary_sha": "gsha1",
            },
        },
    }
    (run_dir / "collab_ledger.json").write_text(json.dumps(ledger), encoding="utf-8")

    res = verify_book_completeness(run_dir)
    assert res["ready_to_publish"] is True
    assert res["incomplete_chapters"] == []
    assert res["defects"] == []


def test_verify_book_completeness_incomplete_status(tmp_path: Path):
    run_dir = tmp_path / "runs" / "test-slug"
    _setup_chapter(run_dir, "ch01", "Chapter one")
    _setup_chapter(run_dir, "ch02", "Chapter two")

    checklist = {
        "chapters": {
            "ch01": {"status": "done"},
            "ch02": {"status": "in_progress"},  # Not completed
        }
    }
    res = verify_book_completeness(run_dir, checklist_data=checklist, ledger_data={"chapters": {}})
    assert res["ready_to_publish"] is False
    assert "ch02" in res["incomplete_chapters"]
    assert any("in_progress" in d for d in res["defects"])


def test_verify_book_completeness_missing_files(tmp_path: Path):
    run_dir = tmp_path / "runs" / "test-slug"
    _setup_chapter(run_dir, "ch01", "Chapter one")
    # ch02 created without final.txt
    ch2 = run_dir / "ch02"
    ch2.mkdir(parents=True, exist_ok=True)
    (ch2 / "source.txt").write_text("Hello", encoding="utf-8")

    checklist = {
        "chapters": {
            "ch01": {"status": "done"},
            "ch02": {"status": "completed"},
        }
    }
    res = verify_book_completeness(run_dir, checklist_data=checklist, ledger_data={"chapters": {}})
    assert res["ready_to_publish"] is False
    assert "ch02" in res["incomplete_chapters"]
    assert any("final.txt" in d for d in res["defects"])


def test_verify_book_completeness_segment_parity_mismatch(tmp_path: Path):
    run_dir = tmp_path / "runs" / "test-slug"
    _setup_chapter(run_dir, "ch01", "Chapter one")

    # ch01 translation.xml with wrong ID
    bad_xml = (
        '<segment id="S-9999" type="paragraph">\n'
        '<original>Chapter one</original>\n'
        '<translate>Chương một</translate>\n'
        '</segment>'
    )
    (run_dir / "ch01" / "translation.xml").write_text(bad_xml, encoding="utf-8")

    checklist = {"chapters": {"ch01": {"status": "done"}}}
    res = verify_book_completeness(run_dir, checklist_data=checklist, ledger_data={"chapters": {}})
    assert res["ready_to_publish"] is False
    assert "ch01" in res["incomplete_chapters"]
    assert any("parity" in d.lower() or "mismatch" in d.lower() for d in res["defects"])


def test_verify_book_completeness_git_tracked_binary(tmp_path: Path):
    # Initialize a git repo in tmp_path
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    run_dir = tmp_path / "runs" / "test-slug"
    _setup_chapter(run_dir, "ch01", "Chapter one")

    # Create and stage a .epub binary in git
    epub_file = run_dir / "book.epub"
    epub_file.write_bytes(b"PK fake epub binary")
    subprocess.run(["git", "add", str(epub_file)], cwd=tmp_path, check=True, capture_output=True)

    checklist = {"chapters": {"ch01": {"status": "done"}}}
    ledger = {
        "book_slug": "test-slug",
        "chapters": {
            "ch01": {
                "chapter": "ch01",
                "issue_number": "seed",
                "selected_pr": 1,
                "rejected_prs": [],
                "translator_login": "bot",
                "reviewer_logins": [],
                "reviewer_score": 5.0,
                "score_of_record": 5.0,
                "verdict": "PASS",
                "merge_sha": "sha1",
                "glossary_sha": "gsha1",
            }
        },
    }
    res = verify_book_completeness(run_dir, checklist_data=checklist, ledger_data=ledger)
    assert res["ready_to_publish"] is False
    assert any("epub" in d.lower() for d in res["defects"])


def test_check_git_tracked_binaries_fails_closed_in_repo(tmp_path: Path, monkeypatch):
    # Initialize a git repo in tmp_path
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    run_dir = tmp_path / "runs" / "test-slug"
    run_dir.mkdir(parents=True, exist_ok=True)

    real_run = subprocess.run

    def mock_subprocess_run(cmd, *args, **kwargs):
        # Simulate git ls-files failure inside git repo
        if "ls-files" in cmd:
            return subprocess.CompletedProcess(cmd, returncode=128, stdout="", stderr="fatal: index corrupted")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    with pytest.raises(RuntimeError, match="git ls-files failed"):
        check_git_tracked_binaries(run_dir)


def test_verify_book_completeness_surfaces_git_error(tmp_path: Path, monkeypatch):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    run_dir = tmp_path / "runs" / "test-slug"
    _setup_chapter(run_dir, "ch01", "Chapter one")

    real_run = subprocess.run

    def mock_subprocess_run(cmd, *args, **kwargs):
        if "ls-files" in cmd:
            return subprocess.CompletedProcess(cmd, returncode=128, stdout="", stderr="fatal: index corrupted")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    checklist = {"chapters": {"ch01": {"status": "done"}}}
    ledger = {
        "book_slug": "test-slug",
        "chapters": {
            "ch01": {
                "chapter": "ch01",
                "issue_number": "seed",
                "selected_pr": 1,
                "rejected_prs": [],
                "translator_login": "bot",
                "reviewer_logins": [],
                "reviewer_score": 5.0,
                "score_of_record": 5.0,
                "verdict": "PASS",
                "merge_sha": "sha1",
                "glossary_sha": "gsha1",
            }
        },
    }
    res = verify_book_completeness(run_dir, checklist_data=checklist, ledger_data=ledger)
    assert res["ready_to_publish"] is False
    assert any("git" in d.lower() and "corrupted" in d.lower() for d in res["defects"])


def test_verify_book_completeness_malformed_checklist_json(tmp_path: Path):
    run_dir = tmp_path / "runs" / "test-slug"
    _setup_chapter(run_dir, "ch01", "Chapter one")
    (run_dir / "checklist.json").write_text("{ this is not valid json :", encoding="utf-8")

    res = verify_book_completeness(run_dir)
    assert res["ready_to_publish"] is False
    assert any("malformed" in d.lower() or "checklist.json" in d.lower() for d in res["defects"])


def test_verify_book_completeness_malformed_ledger_json(tmp_path: Path):
    run_dir = tmp_path / "runs" / "test-slug"
    _setup_chapter(run_dir, "ch01", "Chapter one")
    checklist = {"chapters": {"ch01": {"status": "done"}}}
    (run_dir / "checklist.json").write_text(json.dumps(checklist), encoding="utf-8")
    (run_dir / "collab_ledger.json").write_text("{ corrupt ledger json --", encoding="utf-8")

    res = verify_book_completeness(run_dir)
    assert res["ready_to_publish"] is False
    assert any("malformed" in d.lower() or "collab_ledger.json" in d.lower() for d in res["defects"])
