"""Book publication completeness verification (REQ-COLLAB-010)."""

import json
from pathlib import Path
import subprocess
from typing import Any, Dict, List, Optional, Union

from scripts.collab_ledger import read_ledger, verify_ledger_integrity
from scripts.helper import validate_segment_parity

ALLOWED_DONE_STATUSES = {"accepted", "done", "completed"}
DISALLOWED_BINARY_EXTENSIONS = (".epub", ".pdf", ".docx")


def check_git_tracked_binaries(run_dir: Path) -> List[str]:
    """Check if any .epub, .pdf, or .docx binaries are tracked in git under run_dir."""
    try:
        is_git = subprocess.run(
            ["git", "-C", str(run_dir), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=False,
        )
        if is_git.returncode == 0 and is_git.stdout.strip() == "true":
            res = subprocess.run(
                ["git", "-C", str(run_dir), "ls-files", "."],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode != 0:
                raise RuntimeError(
                    f"git ls-files failed inside repository (code {res.returncode}): {res.stderr.strip()}"
                )
            lines = [l.strip() for l in res.stdout.splitlines() if l.strip()]
            return [f for f in lines if f.lower().endswith(DISALLOWED_BINARY_EXTENSIONS)]
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"git command failed inside repository: {e}")
    return []


def verify_book_completeness(
    run_dir: Union[Path, str],
    checklist_data: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]] = None,
    ledger_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Verify that a translated book is complete and ready for release.

    Checks:
      1. Every chapter in checklist has an accepted/done/completed status.
      2. Each chapter has non-empty source.txt and final.txt.
      3. Any translation.xml passes validate_segment_parity against source.txt.
      4. collab_ledger.json passes verify_ledger_integrity for all chapters.
      5. No .epub, .pdf, or .docx binaries are tracked in git under run_dir.
    """
    p_run = Path(run_dir)
    incomplete_chapters: List[str] = []
    defects: List[str] = []

    # 1. Load checklist data
    if checklist_data is None:
        cl_path = p_run / "checklist.json"
        if not cl_path.is_file():
            return {
                "ready_to_publish": False,
                "incomplete_chapters": [],
                "defects": ["Missing checklist.json in run_dir"],
            }
        try:
            with open(cl_path, "r", encoding="utf-8") as f:
                checklist_data = json.load(f)
        except Exception as e:
            return {
                "ready_to_publish": False,
                "incomplete_chapters": [],
                "defects": [f"Malformed or unreadable checklist.json in run_dir: {e}"],
            }

    # Normalize chapters dict
    chapters_map: Dict[str, Any] = {}
    if isinstance(checklist_data, list):
        for item in checklist_data:
            if isinstance(item, dict):
                cid = item.get("chapter") or item.get("id")
                if cid:
                    chapters_map[cid] = item
    elif isinstance(checklist_data, dict):
        if "chapters" in checklist_data and isinstance(checklist_data["chapters"], dict):
            chapters_map = checklist_data["chapters"]
        else:
            meta_keys = {"spec_version", "book_slug", "source_lang", "target_lang"}
            chapters_map = {k: v for k, v in checklist_data.items() if k not in meta_keys}

    if not chapters_map:
        return {
            "ready_to_publish": False,
            "incomplete_chapters": [],
            "defects": ["No chapters found in checklist"],
        }

    # 2. Check chapter status and required files
    for ch_id, ch_info in chapters_map.items():
        if isinstance(ch_info, dict):
            status = str(ch_info.get("status", "")).strip().lower()
        else:
            status = str(ch_info).strip().lower()

        if status not in ALLOWED_DONE_STATUSES:
            if ch_id not in incomplete_chapters:
                incomplete_chapters.append(ch_id)
            defects.append(f"Chapter {ch_id} has incomplete status: '{status}'")

        # Check source.txt
        src_path = p_run / ch_id / "source.txt"
        if not src_path.is_file() or src_path.stat().st_size == 0:
            if ch_id not in incomplete_chapters:
                incomplete_chapters.append(ch_id)
            defects.append(f"Chapter {ch_id} missing or empty source.txt")

        # Check final.txt
        final_path = p_run / ch_id / "final.txt"
        if not final_path.is_file() or final_path.stat().st_size == 0:
            if ch_id not in incomplete_chapters:
                incomplete_chapters.append(ch_id)
            defects.append(f"Chapter {ch_id} missing or empty final.txt")

        # Check translation.xml if present
        xml_path = p_run / ch_id / "translation.xml"
        if xml_path.is_file() and src_path.is_file():
            try:
                src_content = src_path.read_text(encoding="utf-8")
                xml_content = xml_path.read_text(encoding="utf-8")
                if "<segment" in src_content and "<segment" in xml_content:
                    is_valid, parity_errors = validate_segment_parity(src_content, xml_content)
                    if not is_valid:
                        if ch_id not in incomplete_chapters:
                            incomplete_chapters.append(ch_id)
                        for err in parity_errors:
                            defects.append(f"Chapter {ch_id} segment parity error: {err}")
            except Exception as e:
                if ch_id not in incomplete_chapters:
                    incomplete_chapters.append(ch_id)
                defects.append(f"Chapter {ch_id} error checking translation.xml: {e}")

    # 3. Check ledger integrity
    if ledger_data is None:
        ledger_path = p_run / "collab_ledger.json"
        try:
            ledger_data = read_ledger(ledger_path)
        except Exception as e:
            defects.append(f"Malformed or unreadable collab_ledger.json in run_dir: {e}")
            return {
                "ready_to_publish": False,
                "incomplete_chapters": incomplete_chapters,
                "defects": defects,
            }

    expected_chapters = list(chapters_map.keys())
    ledger_audit = verify_ledger_integrity(ledger_data, expected_chapters)
    if not ledger_audit.get("valid", False):
        for ch in ledger_audit.get("unaudited_chapters", []):
            if ch not in incomplete_chapters:
                incomplete_chapters.append(ch)
        defects.extend(ledger_audit.get("defects", []))

    # 4. Check git-tracked binaries
    try:
        tracked_bins = check_git_tracked_binaries(p_run)
        if tracked_bins:
            defects.append(
                f"Release binaries (.epub, .pdf, .docx) are tracked in git under run_dir: {', '.join(tracked_bins)}"
            )
    except Exception as e:
        defects.append(f"Error checking git-tracked binaries: {e}")

    ready = (len(incomplete_chapters) == 0 and len(defects) == 0)
    return {
        "ready_to_publish": ready,
        "incomplete_chapters": incomplete_chapters,
        "defects": defects,
    }
