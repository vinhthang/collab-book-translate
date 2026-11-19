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


def _valid_two_chapter_ledger(slug: str = "test-slug"):
    return {
        "book_slug": slug,
        "chapters": {
            "ch01": {
                "chapter": "ch01",
                "issue_number": 0,
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
        "released_tag": None,
    }


def test_verify_book_completeness_envelope_and_list_happy_paths(tmp_path: Path):
    run_dir = tmp_path / "runs" / "test-slug"
    _setup_chapter(run_dir, "ch01", "Chapter one")
    _setup_chapter(run_dir, "ch02", "Chapter two")

    envelope_checklist = {
        "book_slug": "test-slug",
        "seed_status": "seeded",
        "seed_error": None,
        "chapters": [
            {"chapter": "ch01", "chapter_num": 1, "title": "Ch 1", "status": "done"},
            {"chapter": "ch02", "chapter_num": 2, "title": "Ch 2", "status": "accepted"},
        ],
    }
    (run_dir / "checklist.json").write_text(json.dumps(envelope_checklist), encoding="utf-8")
    (run_dir / "collab_ledger.json").write_text(json.dumps(_valid_two_chapter_ledger()), encoding="utf-8")

    res_env = verify_book_completeness(run_dir)
    assert res_env["ready_to_publish"] is True, f"Defects: {res_env['defects']}"
    assert res_env["incomplete_chapters"] == []
    assert res_env["defects"] == []

    # Legacy list checklist format
    list_checklist = [
        {"chapter": "ch01", "status": "done"},
        {"chapter": "ch02", "status": "completed"},
    ]
    res_list = verify_book_completeness(run_dir, checklist_data=list_checklist)
    assert res_list["ready_to_publish"] is True
    assert res_list["incomplete_chapters"] == []
    assert res_list["defects"] == []


def test_verify_book_completeness_published_pilot_scandal_in_bohemia():
    pilot_run_dir = Path(__file__).resolve().parent / "fixtures" / "runs" / "scandal-in-bohemia"
    res = verify_book_completeness(pilot_run_dir)
    assert res["ready_to_publish"] is True, f"Pilot failed completeness: {res}"
    assert res["incomplete_chapters"] == []
    assert res["defects"] == []


def test_verify_book_completeness_rejects_blocked_failed_and_empty_chapters(tmp_path: Path):
    run_dir = tmp_path / "runs" / "test-slug"
    _setup_chapter(run_dir, "ch01", "Chapter one")
    _setup_chapter(run_dir, "ch02", "Chapter two")

    blocked_checklist = {
        "book_slug": "test-slug",
        "seed_status": "blocked_failed",
        "seed_error": "ch01 evaluation failed",
        "chapters": [
            {"chapter": "ch01", "status": "done"},
            {"chapter": "ch02", "status": "done"},
        ],
    }
    res_blocked = verify_book_completeness(
        run_dir,
        checklist_data=blocked_checklist,
        ledger_data=_valid_two_chapter_ledger(),
    )
    assert res_blocked["ready_to_publish"] is False
    assert any("blocked_failed" in d for d in res_blocked["defects"])

    empty_envelope = {
        "book_slug": "test-slug",
        "seed_status": "seeded",
        "seed_error": None,
        "chapters": [],
    }
    res_empty = verify_book_completeness(run_dir, checklist_data=empty_envelope, ledger_data=_valid_two_chapter_ledger())
    assert res_empty["ready_to_publish"] is False
    assert any("no chapters" in d.lower() for d in res_empty["defects"])


def test_verify_book_completeness_incomplete_status(tmp_path: Path):
    run_dir = tmp_path / "runs" / "test-slug"
    _setup_chapter(run_dir, "ch01", "Chapter one")
    _setup_chapter(run_dir, "ch02", "Chapter two")

    checklist = {
        "chapters": {
            "ch01": {"status": "done"},
            "ch02": {"status": "in_progress"},
        }
    }
    res = verify_book_completeness(run_dir, checklist_data=checklist, ledger_data=_valid_two_chapter_ledger())
    assert res["ready_to_publish"] is False
    assert "ch02" in res["incomplete_chapters"]
    assert any("in_progress" in d for d in res["defects"])


def test_verify_book_completeness_missing_files(tmp_path: Path):
    run_dir = tmp_path / "runs" / "test-slug"
    _setup_chapter(run_dir, "ch01", "Chapter one")
    ch2 = run_dir / "ch02"
    ch2.mkdir(parents=True, exist_ok=True)
    (ch2 / "source.txt").write_text("Hello", encoding="utf-8")

    checklist = {
        "chapters": {
            "ch01": {"status": "done"},
            "ch02": {"status": "completed"},
        }
    }
    res = verify_book_completeness(run_dir, checklist_data=checklist, ledger_data=_valid_two_chapter_ledger())
    assert res["ready_to_publish"] is False
    assert "ch02" in res["incomplete_chapters"]
    assert any("final.txt" in d for d in res["defects"])


def test_verify_book_completeness_code_and_image_byte_invariance(tmp_path: Path):
    run_dir = tmp_path / "runs" / "test-slug"
    ch_dir = run_dir / "ch01"
    ch_dir.mkdir(parents=True, exist_ok=True)

    orig_body = (
        "Intro paragraph with ![System Diagram](images/arch.png) and code:\n"
        "```python\n"
        "def add(a, b):\n"
        "    return a + b\n"
        "```"
    )
    src_xml = f'<segment id="S-0001" type="paragraph">\n<original>{orig_body}</original>\n</segment>'
    (ch_dir / "source.txt").write_text(src_xml, encoding="utf-8")
    (ch_dir / "final.txt").write_text("Bản dịch chương 1", encoding="utf-8")

    single_ledger = {
        "book_slug": "test-slug",
        "chapters": {"ch01": _valid_two_chapter_ledger()["chapters"]["ch01"]},
        "released_tag": None,
    }
    checklist = {"book_slug": "test-slug", "seed_status": "seeded", "seed_error": None, "chapters": [{"chapter": "ch01", "status": "done"}]}

    bad_code_trans = (
        "Đoạn mở đầu với ![System Diagram](images/arch.png) và mã:\n"
        "```python\n"
        "def cong(a, b):\n"
        "    return a + b\n"
        "```"
    )
    (ch_dir / "translation.xml").write_text(
        f'<segment id="S-0001" type="paragraph">\n<original>{orig_body}</original>\n<translate>{bad_code_trans}</translate>\n</segment>',
        encoding="utf-8",
    )
    res_code = verify_book_completeness(run_dir, checklist_data=checklist, ledger_data=single_ledger)
    assert res_code["ready_to_publish"] is False
    assert "ch01" in res_code["incomplete_chapters"]
    assert any("code" in d.lower() for d in res_code["defects"])

    bad_img_trans = (
        "Đoạn mở đầu với ![Sơ đồ hệ thống](images/arch.png) và mã:\n"
        "```python\n"
        "def add(a, b):\n"
        "    return a + b\n"
        "```"
    )
    (ch_dir / "translation.xml").write_text(
        f'<segment id="S-0001" type="paragraph">\n<original>{orig_body}</original>\n<translate>{bad_img_trans}</translate>\n</segment>',
        encoding="utf-8",
    )
    res_img = verify_book_completeness(run_dir, checklist_data=checklist, ledger_data=single_ledger)
    assert res_img["ready_to_publish"] is False
    assert "ch01" in res_img["incomplete_chapters"]
    assert any("image" in d.lower() for d in res_img["defects"])

    good_trans = (
        "Đoạn mở đầu với ![System Diagram](images/arch.png) và mã:\n"
        "```python\n"
        "def add(a, b):\n"
        "    return a + b\n"
        "```"
    )
    (ch_dir / "translation.xml").write_text(
        f'<segment id="S-0001" type="paragraph">\n<original>{orig_body}</original>\n<translate>{good_trans}</translate>\n</segment>',
        encoding="utf-8",
    )
    res_good = verify_book_completeness(run_dir, checklist_data=checklist, ledger_data=single_ledger)
    assert res_good["ready_to_publish"] is True

    # Mutated <original> text is also caught as a byte-invariance violation
    (ch_dir / "translation.xml").write_text(
        f'<segment id="S-0001" type="paragraph">\n<original>TAMPERED {orig_body}</original>\n<translate>{good_trans}</translate>\n</segment>',
        encoding="utf-8",
    )
    res_orig_mut = verify_book_completeness(run_dir, checklist_data=checklist, ledger_data=single_ledger)
    assert res_orig_mut["ready_to_publish"] is False
    assert "ch01" in res_orig_mut["incomplete_chapters"]
    assert any("original" in d.lower() for d in res_orig_mut["defects"])


def test_verify_book_completeness_segment_parity_mismatch(tmp_path: Path):
    run_dir = tmp_path / "runs" / "test-slug"
    _setup_chapter(run_dir, "ch01", "Chapter one")

    bad_xml = (
        '<segment id="S-9999" type="paragraph">\n'
        '<original>Chapter one</original>\n'
        '<translate>Chương một</translate>\n'
        '</segment>'
    )
    (run_dir / "ch01" / "translation.xml").write_text(bad_xml, encoding="utf-8")

    checklist = {"chapters": {"ch01": {"status": "done"}}}
    res = verify_book_completeness(run_dir, checklist_data=checklist, ledger_data={"book_slug": "test-slug", "chapters": {}})
    assert res["ready_to_publish"] is False
    assert "ch01" in res["incomplete_chapters"]
    assert any("parity" in d.lower() or "mismatch" in d.lower() for d in res["defects"])


def test_verify_book_completeness_git_tracked_binary(tmp_path: Path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    run_dir = tmp_path / "runs" / "test-slug"
    _setup_chapter(run_dir, "ch01", "Chapter one")

    epub_file = run_dir / "book.epub"
    epub_file.write_bytes(b"PK fake epub binary")
    subprocess.run(["git", "add", str(epub_file)], cwd=tmp_path, check=True, capture_output=True)

    checklist = {"chapters": {"ch01": {"status": "done"}}}
    ledger = {
        "book_slug": "test-slug",
        "chapters": {"ch01": _valid_two_chapter_ledger()["chapters"]["ch01"]},
    }
    res = verify_book_completeness(run_dir, checklist_data=checklist, ledger_data=ledger)
    assert res["ready_to_publish"] is False
    assert any("epub" in d.lower() for d in res["defects"])


def test_check_git_tracked_binaries_fails_closed_in_repo(tmp_path: Path, monkeypatch):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    run_dir = tmp_path / "runs" / "test-slug"
    run_dir.mkdir(parents=True, exist_ok=True)

    real_run = subprocess.run

    def mock_subprocess_run(cmd, *args, **kwargs):
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
        "chapters": {"ch01": _valid_two_chapter_ledger()["chapters"]["ch01"]},
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
