"""Book publication completeness verification (REQ-029)."""

import json
from pathlib import Path
import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple, Union

from scripts.collab_ledger import read_ledger, verify_ledger_integrity
from scripts.helper import parse_segments, validate_segment_parity

ALLOWED_DONE_STATUSES = {"accepted", "done", "completed"}
DISALLOWED_BINARY_EXTENSIONS = (".epub", ".pdf", ".docx")

_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```")
_IMAGE_ANCHOR_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_SEGMENT_BLOCK_RE = re.compile(r"<segment\b[^>]*>([\s\S]*?)</segment>")
_RAW_ORIGINAL_RE = re.compile(r"<original>([\s\S]*?)</original>")


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
            lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            return [f for f in lines if f.lower().endswith(DISALLOWED_BINARY_EXTENSIONS)]
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"git command failed inside repository: {exc}") from exc
    return []


def _check_xml_code_and_image_invariance(source_xml: str, target_xml: str) -> List[str]:
    """Verify <original> byte-identity, non-empty <translate>, and code/image byte-invariance."""
    errors: List[str] = []
    src_segs = parse_segments(source_xml)
    tgt_segs = parse_segments(target_xml)
    src_blocks = _SEGMENT_BLOCK_RE.findall(source_xml)
    tgt_blocks = _SEGMENT_BLOCK_RE.findall(target_xml)

    for idx, (s_seg, t_seg, s_blk, t_blk) in enumerate(zip(src_segs, tgt_segs, src_blocks, tgt_blocks)):
        seg_id = s_seg.get("id") or f"index-{idx}"

        s_orig_m = _RAW_ORIGINAL_RE.search(s_blk)
        t_orig_m = _RAW_ORIGINAL_RE.search(t_blk)
        raw_src_orig = s_orig_m.group(1) if s_orig_m else None
        raw_tgt_orig = t_orig_m.group(1) if t_orig_m else None
        if raw_src_orig != raw_tgt_orig:
            errors.append(f"Byte-invariance violation in <original> block of segment '{seg_id}'")

        orig_text = str(s_seg.get("original") or "")
        trans_text = t_seg.get("translate")
        if trans_text is None or not str(trans_text).strip():
            errors.append(f"Empty <translate> block in segment '{seg_id}'")
            continue

        trans_str = str(trans_text)
        orig_codes = _FENCED_CODE_RE.findall(orig_text)
        trans_codes = _FENCED_CODE_RE.findall(trans_str)
        if orig_codes != trans_codes:
            errors.append(
                f"Markdown fenced code block byte-invariance parity mismatch in segment '{seg_id}'"
            )

        orig_without_code = _FENCED_CODE_RE.sub("", orig_text)
        trans_without_code = _FENCED_CODE_RE.sub("", trans_str)
        orig_images = _IMAGE_ANCHOR_RE.findall(orig_without_code)
        trans_images = _IMAGE_ANCHOR_RE.findall(trans_without_code)
        if orig_images != trans_images:
            errors.append(
                f"Markdown image anchor byte-invariance parity mismatch in segment '{seg_id}'"
            )

    return errors


def _extract_chapters_and_seed_status(
    checklist_data: Any,
) -> Tuple[Dict[str, Any], Optional[str], Optional[str]]:
    """Extract (chapters_map, seed_status, seed_error) from envelope or legacy list/dict checklist."""
    chapters_map: Dict[str, Any] = {}
    seed_status: Optional[str] = None
    seed_error: Optional[str] = None

    if isinstance(checklist_data, list):
        for item in checklist_data:
            if isinstance(item, dict):
                cid = item.get("chapter") or item.get("id")
                if cid:
                    chapters_map[str(cid)] = item
    elif isinstance(checklist_data, dict):
        if checklist_data.get("seed_status") is not None:
            seed_status = str(checklist_data["seed_status"]).strip().lower()
        if checklist_data.get("seed_error") is not None:
            seed_error = str(checklist_data["seed_error"])

        raw_chapters = checklist_data.get("chapters")
        if isinstance(raw_chapters, list):
            for item in raw_chapters:
                if isinstance(item, dict):
                    cid = item.get("chapter") or item.get("id")
                    if cid:
                        chapters_map[str(cid)] = item
        elif isinstance(raw_chapters, dict):
            chapters_map = {str(k): v for k, v in raw_chapters.items()}
        elif "chapters" not in checklist_data:
            meta_keys = {
                "spec_version",
                "book_slug",
                "source_lang",
                "target_lang",
                "seed_status",
                "seed_error",
            }
            chapters_map = {
                str(k): v for k, v in checklist_data.items() if k not in meta_keys
            }

    return chapters_map, seed_status, seed_error


def verify_book_completeness(
    run_dir: Union[Path, str],
    checklist_data: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]] = None,
    ledger_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Verify that a translated book satisfies REQ-029 completeness and release gates."""
    p_run = Path(run_dir)
    incomplete_chapters: List[str] = []
    defects: List[str] = []

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
        except Exception as exc:
            return {
                "ready_to_publish": False,
                "incomplete_chapters": [],
                "defects": [f"Malformed or unreadable checklist.json in run_dir: {exc}"],
            }

    chapters_map, seed_status, seed_error = _extract_chapters_and_seed_status(checklist_data)

    if seed_status == "blocked_failed":
        detail = f": {seed_error}" if seed_error else ""
        defects.append(f"Book seeding is quarantined with seed_status='blocked_failed'{detail}")
    elif seed_status == "seeding":
        defects.append("Book seeding is still in progress (seed_status='seeding')")

    if not chapters_map:
        defects.append("No chapters found in checklist")
        return {
            "ready_to_publish": False,
            "incomplete_chapters": incomplete_chapters,
            "defects": defects,
        }

    for ch_id, ch_info in chapters_map.items():
        if isinstance(ch_info, dict):
            status = str(ch_info.get("status", "")).strip().lower()
        else:
            status = str(ch_info).strip().lower()

        if status not in ALLOWED_DONE_STATUSES:
            if ch_id not in incomplete_chapters:
                incomplete_chapters.append(ch_id)
            defects.append(f"Chapter {ch_id} has incomplete status: '{status}'")

        src_path = p_run / ch_id / "source.txt"
        if not src_path.is_file() or src_path.stat().st_size == 0:
            if ch_id not in incomplete_chapters:
                incomplete_chapters.append(ch_id)
            defects.append(f"Chapter {ch_id} missing or empty source.txt")

        final_path = p_run / ch_id / "final.txt"
        if not final_path.is_file() or final_path.stat().st_size == 0:
            if ch_id not in incomplete_chapters:
                incomplete_chapters.append(ch_id)
            defects.append(f"Chapter {ch_id} missing or empty final.txt")

        xml_path = p_run / ch_id / "translation.xml"
        if xml_path.is_file():
            if xml_path.stat().st_size == 0:
                if ch_id not in incomplete_chapters:
                    incomplete_chapters.append(ch_id)
                defects.append(f"Chapter {ch_id} has empty translation.xml")
            elif src_path.is_file():
                try:
                    src_content = src_path.read_text(encoding="utf-8")
                    xml_content = xml_path.read_text(encoding="utf-8")
                    if "<segment" in src_content or "<segment" in xml_content:
                        is_valid, parity_errors = validate_segment_parity(src_content, xml_content)
                        invariance_errors = (
                            _check_xml_code_and_image_invariance(src_content, xml_content)
                            if is_valid
                            else []
                        )
                        all_xml_errors = list(parity_errors) + invariance_errors
                        if all_xml_errors:
                            if ch_id not in incomplete_chapters:
                                incomplete_chapters.append(ch_id)
                            for err in all_xml_errors:
                                defects.append(f"Chapter {ch_id} segment parity error: {err}")
                except Exception as exc:
                    if ch_id not in incomplete_chapters:
                        incomplete_chapters.append(ch_id)
                    defects.append(f"Chapter {ch_id} error checking translation.xml: {exc}")

    if ledger_data is None:
        ledger_path = p_run / "collab_ledger.json"
        if not ledger_path.is_file():
            for ch_id in chapters_map:
                if ch_id not in incomplete_chapters:
                    incomplete_chapters.append(ch_id)
            defects.append("Missing collab_ledger.json in run_dir")
            return {
                "ready_to_publish": False,
                "incomplete_chapters": incomplete_chapters,
                "defects": defects,
            }
        try:
            ledger_data = read_ledger(ledger_path)
        except Exception as exc:
            defects.append(f"Malformed or unreadable collab_ledger.json in run_dir: {exc}")
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

    try:
        tracked_bins = check_git_tracked_binaries(p_run)
        if tracked_bins:
            defects.append(
                f"Release binaries (.epub, .pdf, .docx) are tracked in git under run_dir: {', '.join(tracked_bins)}"
            )
    except Exception as exc:
        defects.append(f"Error checking git-tracked binaries: {exc}")

    ready = len(incomplete_chapters) == 0 and len(defects) == 0
    return {
        "ready_to_publish": ready,
        "incomplete_chapters": incomplete_chapters,
        "defects": defects,
    }
