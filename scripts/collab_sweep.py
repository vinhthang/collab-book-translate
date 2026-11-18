"""Idempotent local main-agent sweep orchestrator for collaborative book translation.

Implements REQ-018, REQ-020, REQ-023, REQ-024, REQ-025, REQ-026, REQ-027, REQ-029, and REQ-030:
- Single-instance POSIX fcntl.flock on <workspace_dir>/.sweep.lock
- Clean-main verification (--recover-runs for runs/**) and fast-forward origin/main sync
- Commit-bound bot_login [collab-prescreen sha=<oid> status=PASS|WARN|FAIL] & /approve-intake <oid>
- Missing ch02..chNN issue reconciliation for seeded books and retry of seeding books
- Pre-LLM 4-class injection gating, /clear-flag, /reject-flag, /dismiss-stale-review, /retry-fallback
- Authoritative SHA-keyed [collab-revise sha=<40-hex>] round counting and Evaluating state hold
- Cohort [collab-window-start epoch=<float>] selection, strict <winner_head_oid> proposal binding, and runs/<slug>/** confinement
- Bounded 2-attempt 48 h starved-chapter bot fallback PRs (with <hanviet> injection for zh->vi)
- Asset-verified release backfill and GitHub Release publication
"""

import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.collab_completeness import verify_book_completeness
from scripts.collab_intake import format_prescreen_comment, validate_submission_pr
from scripts.collab_ledger import (
    append_ledger_entry,
    compute_reviewer_score,
    read_ledger,
)
from scripts.collab_pr_validate import (
    SEGMENT_BLOCK_RE,
    detect_prompt_injection,
    parse_pr_body,
    validate_additions_schema,
    validate_review,
    validate_translation_pr,
)
from scripts.collab_seed import (
    build_translation_pr_body,
    ensure_hanviet_segments,
    reconcile_missing_chapter_issues,
    sync_collab_book_record,
)
from scripts.collab_select import (
    evaluate_starved_chapter,
    format_non_selected_comment,
    parse_latest_candidate_score,
    parse_latest_window_start,
    select_winning_pr,
)
from scripts.collab_state import count_bot_revise_rounds, reconcile_status_labels
from scripts.collab_verdict import (
    _check_human_flag_commands,
    compute_collab_verdict,
    has_blocking_changes_requested,
)
from scripts.helper import (
    atomic_write_json,
    parse_segments,
    strip_segments_to_final,
    update_book_record_from_eval,
)

logger = logging.getLogger(__name__)

_PRESCREEN_RE = re.compile(
    r"\[collab-prescreen\s+sha=([^\s\]]*)\s+status=(PASS|WARN|FAIL)\]"
)
_APPROVE_INTAKE_RE = re.compile(r"(?:^|\n)\s*/approve-intake\s+(\S+)(?:\s|$)")
_FLAGGED_MARKER_RE = re.compile(r"\[collab-flagged\s+sha=([^\s\]]*)\]")
_EVALUATING_HOLD_RE = re.compile(r"\[collab-evaluating-hold\s+sha=([^\s\]]*)\]")
_HEX40_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def _parse_timestamp(val: Any, default: float) -> float:
    """Parse numeric timestamp or ISO-8601 string to a float epoch timestamp."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        val_str = val.strip()
        if not val_str:
            return default
        try:
            return float(val_str)
        except ValueError:
            pass
        try:
            dt = datetime.datetime.fromisoformat(val_str.replace("Z", "+00:00"))
            return dt.timestamp()
        except ValueError:
            pass
    return default


def _normalize_comment_author(c: Dict[str, Any]) -> str:
    auth = str(
        c.get("author_login")
        or (c.get("author") or {}).get("login")
        or (c.get("user") or {}).get("login")
        or ""
    ).strip()
    c["author_login"] = auth
    return auth


def _extract_review_commit_oid(review: Dict[str, Any]) -> str:
    if not isinstance(review, dict):
        return ""
    return str(
        review.get("commit_oid")
        or review.get("head_oid")
        or (review.get("commit") or {}).get("oid")
        or ""
    ).strip()


def _canonical_revise_sha(pr_num: int, head_oid: str) -> str:
    clean = (head_oid or "").strip()
    if _HEX40_RE.match(clean):
        return clean.lower()
    return hashlib.sha1(f"pr:{pr_num}:{clean or 'empty'}".encode("utf-8")).hexdigest()


@contextlib.contextmanager
def non_blocking_sweep_lock(lock_path: Union[Path, str]):
    """Non-blocking POSIX flock context manager yielding True if acquired, False if held (REQ-030)."""
    p = Path(lock_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR, 0o644)
    locked = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except (BlockingIOError, OSError):
            locked = False
        yield locked
    finally:
        if locked:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError as exc:
                logger.debug("Failed flock unlock: %s", exc)
        try:
            os.close(fd)
        except OSError as exc:
            logger.debug("Failed close fd: %s", exc)


def sync_clean_main(root: Union[Path, str], *, recover_runs: bool = False) -> None:
    """Verify clean 'main' checkout (with optional --recover-runs) and fast-forward to origin/main (REQ-030)."""
    root_path = Path(root).resolve()
    is_git = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=str(root_path),
        capture_output=True,
        text=True,
        check=False,
    )
    if is_git.returncode != 0 or is_git.stdout.strip() != "true":
        return

    branch_res = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=str(root_path),
        capture_output=True,
        text=True,
        check=False,
    )
    current_branch = branch_res.stdout.strip() if branch_res.returncode == 0 else ""
    if current_branch != "main":
        raise RuntimeError(
            f"Sweep requires local checkout on branch 'main', got '{current_branch or 'DETACHED'}'"
        )

    status_res = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=str(root_path),
        capture_output=True,
        text=True,
        check=True,
    )
    outside_dirty: List[str] = []
    runs_dirty: List[str] = []
    for raw_line in status_res.stdout.splitlines():
        if not raw_line.strip():
            continue
        rel = raw_line[3:].strip()
        if " -> " in rel:
            parts = [p.strip().strip('"') for p in rel.split(" -> ", 1)]
        else:
            parts = [rel.strip('"')]
        for p_str in parts:
            norm_p = p_str.replace("\\", "/").lstrip("./")
            if norm_p.startswith("runs/"):
                runs_dirty.append(norm_p)
            else:
                outside_dirty.append(norm_p)

    if outside_dirty:
        raise RuntimeError(
            f"Dirty tracked files detected outside runs/: {', '.join(sorted(set(outside_dirty)))}"
        )

    if runs_dirty:
        if not recover_runs:
            raise RuntimeError(
                f"Dirty tracked files under runs/ detected ({', '.join(sorted(set(runs_dirty)))}); "
                f"pass --recover-runs to restore uncommitted runs/** edits to HEAD"
            )
        subprocess.run(
            ["git", "checkout", "HEAD", "--", "runs"],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            check=True,
        )

    rem_res = subprocess.run(
        ["git", "remote"],
        cwd=str(root_path),
        capture_output=True,
        text=True,
        check=False,
    )
    if rem_res.returncode == 0 and "origin" in rem_res.stdout.split():
        fetch_res = subprocess.run(
            ["git", "fetch", "origin", "main"],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if fetch_res.returncode != 0:
            raise RuntimeError(f"Failed fetching origin/main: {fetch_res.stderr.strip()}")

        ff_res = subprocess.run(
            ["git", "merge", "--ff-only", "origin/main"],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if ff_res.returncode != 0:
            raise RuntimeError(
                f"Cannot fast-forward local main to origin/main: {ff_res.stderr.strip()}"
            )


def _assert_runs_confinement(
    root: Path,
    paths: Sequence[Union[Path, str]],
    book_slug: Optional[str] = None,
) -> List[Path]:
    """Enforce REQ-023 confinement: every path staged by the bot must resolve strictly inside runs/<slug>/**."""
    root_res = Path(root).resolve()
    runs_root = (root_res / "runs").resolve()
    slug_root = (runs_root / book_slug).resolve() if book_slug else None

    validated: List[Path] = []
    for p in paths:
        raw_p = Path(p)
        abs_p = (raw_p if raw_p.is_absolute() else (root_res / raw_p)).resolve()
        if slug_root is not None:
            if not abs_p.is_relative_to(slug_root) or abs_p == slug_root:
                raise ValueError(
                    f"Path confinement violation: '{p}' resolves outside runs/{book_slug}/**"
                )
        else:
            if not abs_p.is_relative_to(runs_root):
                raise ValueError(
                    f"Path confinement violation: '{p}' resolves outside runs/<slug>/**"
                )
            rel_parts = abs_p.relative_to(runs_root).parts
            if len(rel_parts) < 2:
                raise ValueError(
                    f"Path confinement violation: '{p}' must reside inside runs/<slug>/**"
                )
        validated.append(abs_p)
    return validated


def commit_and_push(
    root: Path,
    paths: List[Union[Path, str]],
    message: str,
    client: Any = None,
    *,
    book_slug: Optional[str] = None,
    bot_git_name: Optional[str] = None,
    bot_git_email: Optional[str] = None,
) -> None:
    """Stage confined runs/<slug>/** files, commit, and push to origin/main (REQ-023, REQ-030)."""
    root_res = Path(root).resolve()
    abs_paths = _assert_runs_confinement(root_res, paths, book_slug=book_slug)

    if client is not None and not isinstance(client, GitHubCLIClient) and hasattr(client, "commit_and_push"):
        client.commit_and_push([str(p) for p in abs_paths], message)
        return

    is_git = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=str(root_res),
        capture_output=True,
        text=True,
        check=False,
    )
    if is_git.returncode != 0 or is_git.stdout.strip() != "true":
        return

    existing_rel_paths = [
        str(p.relative_to(root_res)) for p in abs_paths if p.exists()
    ]
    if not existing_rel_paths:
        return

    for rel_p in existing_rel_paths:
        subprocess.run(["git", "add", rel_p], cwd=str(root_res), check=True, capture_output=True)

    diff_cached = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=str(root_res),
        capture_output=True,
        check=False,
    )
    if diff_cached.returncode != 0:
        git_cmd = ["git"]
        if bot_git_name:
            git_cmd.extend(["-c", f"user.name={bot_git_name}"])
        if bot_git_email:
            git_cmd.extend(["-c", f"user.email={bot_git_email}"])
        git_cmd.extend(["commit", "-m", message])
        subprocess.run(git_cmd, cwd=str(root_res), check=True, capture_output=True)

    rem_res = subprocess.run(
        ["git", "remote"],
        cwd=str(root_res),
        capture_output=True,
        text=True,
        check=False,
    )
    if rem_res.returncode == 0 and "origin" in rem_res.stdout.split():
        subprocess.run(["git", "push", "origin", "main"], cwd=str(root_res), check=True, capture_output=True)


def read_pr_file_bytes(
    client: Any,
    pr_num: int,
    head_ref: str,
    rel_path: Union[str, Path],
    root: Path,
    *,
    head_oid: str = "",
    allow_local_fallback: bool = True,
) -> bytes:
    """Read a file as bytes from PR head_oid / pull refspec (falling back to local root only when permitted)."""
    rel_str = str(rel_path).lstrip("/")
    clean_oid = (head_oid or "").strip()

    if client is not None and not isinstance(client, GitHubCLIClient):
        if hasattr(client, "get_pr_file_at_oid"):
            val = client.get_pr_file_at_oid(pr_num, clean_oid, rel_str)
            if val is not None:
                return val if isinstance(val, bytes) else str(val).encode("utf-8")
        if hasattr(client, "get_pr_file_bytes"):
            val = client.get_pr_file_bytes(pr_num, rel_str)
            if val is not None:
                return val if isinstance(val, bytes) else str(val).encode("utf-8")
        if hasattr(client, "get_pr_file"):
            val = client.get_pr_file(pr_num, rel_str)
            if val is not None:
                return val if isinstance(val, bytes) else str(val).encode("utf-8")
        if hasattr(client, "read_pr_file"):
            val = client.read_pr_file(pr_num, head_ref, rel_str, root)
            if val is not None:
                return val if isinstance(val, bytes) else str(val).encode("utf-8")

    is_git = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if is_git.returncode == 0 and is_git.stdout.strip() == "true":
        if clean_oid:
            show_oid = subprocess.run(
                ["git", "show", f"{clean_oid}:{rel_str}"],
                cwd=str(root),
                capture_output=True,
                check=False,
            )
            if show_oid.returncode == 0:
                return show_oid.stdout

        fetched = False
        if pr_num:
            f_pr = subprocess.run(
                ["git", "fetch", "origin", f"pull/{pr_num}/head"],
                cwd=str(root),
                capture_output=True,
                check=False,
            )
            fetched = f_pr.returncode == 0
        if not fetched and head_ref:
            f_ref = subprocess.run(
                ["git", "fetch", "origin", head_ref],
                cwd=str(root),
                capture_output=True,
                check=False,
            )
            fetched = f_ref.returncode == 0

        if fetched:
            target_rev = clean_oid if clean_oid else "FETCH_HEAD"
            show_res = subprocess.run(
                ["git", "show", f"{target_rev}:{rel_str}"],
                cwd=str(root),
                capture_output=True,
                check=False,
            )
            if show_res.returncode == 0:
                return show_res.stdout

    if allow_local_fallback:
        local_p = Path(root) / rel_str
        if local_p.is_file():
            return local_p.read_bytes()
    return b""


def read_pr_file(
    client: Any,
    pr_num: int,
    head_ref: str,
    rel_path: Union[str, Path],
    root: Path,
    *,
    head_oid: str = "",
    allow_local_fallback: bool = True,
) -> str:
    """Read a UTF-8 text file from PR head_oid / pull refspec."""
    raw = read_pr_file_bytes(
        client,
        pr_num,
        head_ref,
        rel_path,
        root,
        head_oid=head_oid,
        allow_local_fallback=allow_local_fallback,
    )
    if not raw:
        return ""
    return raw.decode("utf-8", errors="replace")


def bind_winning_proposals(
    book_record_path: Path,
    additions_bytes: bytes,
    *,
    chapter_additions_path: Optional[Path] = None,
    apply: bool = False,
) -> Dict[str, Any]:
    """Validate and check/bind analyze_additions.json proposals from <winner_head_oid> (REQ-025)."""
    try:
        raw_text = additions_bytes.decode("utf-8")
        additions_obj = json.loads(raw_text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"valid": False, "conflict": False, "defects": [f"Invalid analyze_additions.json: {exc}"], "conflicts": []}

    schema_res = validate_additions_schema(additions_obj, allow_legacy=False)
    if not schema_res["valid"]:
        return {"valid": False, "conflict": False, "defects": list(schema_res["defects"]), "conflicts": []}

    proposals = list(additions_obj.get("proposals") or [])
    br_data = json.loads(book_record_path.read_text(encoding="utf-8"))
    frozen_glossary_sha = br_data.get("glossary_sha")

    # Build case-folded map across glossary, terms, and names on main
    existing_cf: Dict[str, str] = {}
    raw_glossary = br_data.get("glossary") or {}
    if isinstance(raw_glossary, dict):
        for k, v in raw_glossary.items():
            if isinstance(v, str) and str(k).strip() and v.strip():
                existing_cf[str(k).strip().casefold()] = v.strip()
            elif isinstance(v, dict):
                tgt = str(v.get("target") or v.get("target_vi") or v.get("rendering") or "").strip()
                if str(k).strip() and tgt:
                    existing_cf[str(k).strip().casefold()] = tgt

    for container_key in ("terms", "names"):
        container = br_data.get(container_key)
        if isinstance(container, list):
            for item in container:
                if isinstance(item, dict):
                    s_val = str(item.get("source") or "").strip()
                    t_val = str(item.get("target") or "").strip()
                    if s_val and t_val:
                        existing_cf[s_val.casefold()] = t_val
        elif isinstance(container, dict):
            for k, v in container.items():
                if isinstance(v, str) and str(k).strip() and v.strip():
                    existing_cf[str(k).strip().casefold()] = v.strip()

    conflicts: List[str] = []
    for prop in proposals:
        src = str(prop["source"]).strip()
        tgt = str(prop["target"]).strip()
        cf_key = src.casefold()
        if cf_key in existing_cf and existing_cf[cf_key].casefold() != tgt.casefold():
            conflicts.append(
                f"Term '{src}' proposes '{tgt}', conflicting with bound target '{existing_cf[cf_key]}'"
            )

    if conflicts:
        return {"valid": True, "conflict": True, "defects": [], "conflicts": conflicts}

    if apply:
        glossary = br_data.get("glossary")
        if not isinstance(glossary, dict):
            glossary = {}
            br_data["glossary"] = glossary
        terms_list = br_data.get("terms")
        if not isinstance(terms_list, list):
            terms_list = []
            br_data["terms"] = terms_list

        existing_term_by_cf = {
            str(t.get("source", "")).strip().casefold(): t
            for t in terms_list
            if isinstance(t, dict) and str(t.get("source", "")).strip()
        }

        bound_proposals: List[Dict[str, Any]] = []
        for prop in proposals:
            src = str(prop["source"]).strip()
            tgt = str(prop["target"]).strip()
            glossary[src] = tgt
            cf_key = src.casefold()
            if cf_key in existing_term_by_cf:
                existing_term_by_cf[cf_key]["target"] = tgt
                existing_term_by_cf[cf_key].setdefault("match", "word")
                existing_term_by_cf[cf_key]["case_sensitive"] = False
            else:
                new_t = {
                    "source": src,
                    "target": tgt,
                    "match": "word",
                    "case_sensitive": False,
                }
                terms_list.append(new_t)
                existing_term_by_cf[cf_key] = new_t

            updated_p = dict(prop)
            updated_p["status"] = "bound"
            bound_proposals.append(updated_p)

        sync_collab_book_record(br_data)
        # Enforce match="word", case_sensitive=False on newly bound terms & preserve immutable glossary_sha
        for prop in proposals:
            src_cf = str(prop["source"]).strip().casefold()
            for t in br_data.get("terms", []):
                if isinstance(t, dict) and str(t.get("source", "")).strip().casefold() == src_cf:
                    t["match"] = "word"
                    t["case_sensitive"] = False
        if frozen_glossary_sha is not None:
            br_data["glossary_sha"] = frozen_glossary_sha
        atomic_write_json(book_record_path, br_data)

        if chapter_additions_path is not None:
            chapter_additions_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(chapter_additions_path, {"proposals": bound_proposals})

    return {"valid": True, "conflict": False, "defects": [], "conflicts": []}


def _extract_ch_id_and_slug(
    head_ref: str, chunk_id: Optional[str] = None
) -> Tuple[Optional[str], Optional[str]]:
    """Extract canonical ch_id and book slug from head_ref and chunk_id."""
    slug: Optional[str] = None
    ch_id: Optional[str] = None
    if head_ref.startswith("translate/"):
        ref_rest = head_ref[len("translate/") :]
        m = re.search(r"-(ch\d{2,})-", ref_rest)
        if m:
            ch_id = m.group(1)
            slug = ref_rest[: m.start()]
        else:
            parts = ref_rest.split("-")
            if len(parts) >= 3:
                ch_id = parts[-2]
                slug = "-".join(parts[:-2])
            elif len(parts) == 2:
                slug = parts[0]
                ch_id = parts[1]

    if chunk_id:
        if slug and chunk_id.startswith(f"{slug}-"):
            ch_id = chunk_id[len(f"{slug}-") :]
        elif not ch_id:
            m_c = re.match(r"^([a-z0-9-]+)-(ch\d{2,})$", chunk_id)
            if m_c:
                slug = slug or m_c.group(1)
                ch_id = m_c.group(2)
            else:
                ch_id = chunk_id

    return ch_id, slug


def _extract_issue_metadata(issue: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Extract canonical ch_id and book slug from tracking issue."""
    title = str(issue.get("title") or "")
    body = str(issue.get("body") or "")
    labels = issue.get("labels") or []

    ch_id: Optional[str] = None
    slug: Optional[str] = None

    for l in labels:
        l_str = str(l)
        if l_str.startswith("book:"):
            slug = l_str.split("book:", 1)[1].strip()

    if not slug and "- **Book:**" in body:
        m = re.search(r"-\s*\*\*Book:\*\*\s*([^\s\n\r]+)", body)
        if m:
            slug = m.group(1).strip()

    if "[" in title and "]" in title:
        cand = title.split("[", 1)[1].split("]", 1)[0].strip()
        if re.match(r"^ch\d{2,}$", cand):
            ch_id = cand

    if not ch_id:
        m = re.search(r"\b(ch\d{2,})\b", title)
        if m:
            ch_id = m.group(1)

    if not slug:
        m = re.search(r"Translate\s+(?:\[[^\]]+\]\s+)?([a-z0-9\-]+)-(ch\d{2,})", title)
        if m:
            slug = m.group(1)
            if not ch_id:
                ch_id = m.group(2)

    return ch_id, slug


def _update_checklist_chapter_done(cl_file: Path, ch_id: str) -> None:
    if not cl_file.is_file():
        return
    cl_data = json.loads(cl_file.read_text(encoding="utf-8"))
    if isinstance(cl_data, list):
        for item in cl_data:
            if isinstance(item, dict) and (
                item.get("chapter") == ch_id
                or item.get("id") == ch_id
                or item.get("chunk_id") == ch_id
            ):
                item["status"] = "done"
        atomic_write_json(cl_file, cl_data)
    elif isinstance(cl_data, dict):
        chapters = cl_data.get("chapters")
        if isinstance(chapters, list):
            for item in chapters:
                if isinstance(item, dict) and item.get("chapter") == ch_id:
                    item["status"] = "done"
        elif isinstance(chapters, dict):
            if isinstance(chapters.get(ch_id), dict):
                chapters[ch_id]["status"] = "done"
            else:
                chapters[ch_id] = {"status": "done"}
        elif ch_id in cl_data and isinstance(cl_data[ch_id], dict):
            cl_data[ch_id]["status"] = "done"
        atomic_write_json(cl_file, cl_data)


class GitHubCLIClient:
    """Production GitHub client using the official gh CLI."""

    def __init__(
        self,
        bot_login: Optional[str] = None,
        repo_root: Optional[Union[Path, str]] = None,
        bot_git_name: Optional[str] = None,
        bot_git_email: Optional[str] = None,
    ):
        if bot_login is None or repo_root is None or bot_git_name is None or bot_git_email is None:
            from scripts.collab_config import load_collab_config

            cfg = load_collab_config()
            bot_login = bot_login or cfg.bot_login
            repo_root = repo_root or cfg.workspace_dir
            bot_git_name = bot_git_name or cfg.bot_git_name
            bot_git_email = bot_git_email or cfg.bot_git_email

        self.bot_login = bot_login
        self.root = Path(repo_root)
        self.bot_git_name = bot_git_name
        self.bot_git_email = bot_git_email
        self._bot_token: Optional[str] = None
        self._token_resolved = False

    def _run_gh(self, args: List[str], *, check: bool = True) -> str:
        cmd = ["gh"] + args
        env = os.environ.copy()
        if "GH_TOKEN" not in env and self.bot_login:
            if not self._token_resolved:
                token_proc = subprocess.run(
                    ["gh", "auth", "token", "-u", self.bot_login],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if token_proc.returncode == 0 and token_proc.stdout.strip():
                    self._bot_token = token_proc.stdout.strip()
                self._token_resolved = True
            if self._bot_token:
                env["GH_TOKEN"] = self._bot_token
        res = subprocess.run(cmd, env=env, cwd=str(self.root), capture_output=True, text=True, check=check)
        if check and res.returncode != 0:
            raise subprocess.CalledProcessError(res.returncode, cmd, output=res.stdout, stderr=res.stderr)
        return res.stdout if res.returncode == 0 else ""

    def commit_and_push(
        self,
        paths: List[Union[Path, str]],
        message: str,
        *,
        book_slug: Optional[str] = None,
    ) -> None:
        commit_and_push(
            self.root,
            paths,
            message,
            book_slug=book_slug,
            bot_git_name=self.bot_git_name,
            bot_git_email=self.bot_git_email,
        )

    def _normalize_prs(self, prs: List[Dict[str, Any]], *, count_inline: bool = True) -> List[Dict[str, Any]]:
        repo_name: Optional[str] = None
        for pr in prs:
            pr["head_ref"] = pr.get("headRefName", "") or pr.get("head_ref", "")
            pr["head_oid"] = pr.get("head_oid") or pr.get("headRefOid", "")
            pr["author_login"] = pr.get("author_login") or (pr.get("author") or {}).get("login", "")
            pr["labels"] = [l.get("name", "") if isinstance(l, dict) else str(l) for l in pr.get("labels", [])]
            pr["changed_files"] = [f.get("path", "") if isinstance(f, dict) else str(f) for f in pr.get("files", [])]
            if pr.get("closedAt") and "closed_at" not in pr:
                pr["closed_at"] = _parse_timestamp(pr.get("closedAt"), 0.0)
            for c in pr.get("comments", []):
                _normalize_comment_author(c)
            reviews = pr.get("reviews", [])
            for r in reviews:
                r["author_login"] = r.get("author_login") or (r.get("author") or {}).get("login", "")
                if count_inline and "line_comment_count" not in r:
                    line_count = 0
                    num = pr.get("number")
                    if num:
                        try:
                            if repo_name is None:
                                repo_out = self._run_gh(["repo", "view", "--json", "nameWithOwner"])
                                repo_name = json.loads(repo_out).get("nameWithOwner", "")
                            if repo_name:
                                comments_out = self._run_gh(["api", f"repos/{repo_name}/pulls/{num}/comments"])
                                pull_comments = json.loads(comments_out)
                                rev_user = r["author_login"]
                                line_count = sum(
                                    1 for c in pull_comments if (c.get("user") or {}).get("login") == rev_user
                                )
                        except Exception as exc:
                            logger.debug("Failed counting line comments: %s", exc)
                    if line_count == 0:
                        body = r.get("body", "")
                        for marker in ("<!-- segment:", "<!-- line:", "segment_id", "line_comment", "diff:"):
                            line_count += body.count(marker)
                    r["line_comment_count"] = line_count
            if "unresolved_threads" not in pr:
                pr["unresolved_threads"] = 0
        return prs

    def list_open_prs(self) -> List[Dict[str, Any]]:
        fields = "number,title,body,headRefName,headRefOid,author,labels,files,reviews,comments"
        out = self._run_gh(["pr", "list", "--state", "open", "--json", fields])
        return self._normalize_prs(json.loads(out) if out.strip() else [], count_inline=True)

    def list_closed_prs(self) -> List[Dict[str, Any]]:
        fields = "number,title,body,headRefName,headRefOid,author,labels,closedAt,state"
        out = self._run_gh(["pr", "list", "--state", "closed", "--limit", "200", "--json", fields], check=False)
        return self._normalize_prs(json.loads(out) if out.strip() else [], count_inline=False)

    def list_open_issues(self) -> List[Dict[str, Any]]:
        fields = "number,title,body,labels,comments,createdAt,state"
        out = self._run_gh(["issue", "list", "--state", "open", "--limit", "500", "--json", fields])
        issues = json.loads(out) if out.strip() else []
        for issue in issues:
            issue["labels"] = [l.get("name", "") if isinstance(l, dict) else str(l) for l in issue.get("labels", [])]
            issue["created_at"] = _parse_timestamp(issue.get("createdAt"), time.time())
            for c in issue.get("comments", []):
                _normalize_comment_author(c)
        return issues

    def list_all_issues(self) -> List[Dict[str, Any]]:
        fields = "number,title,body,labels,state"
        out = self._run_gh(["issue", "list", "--state", "all", "--limit", "500", "--json", fields], check=False)
        issues = json.loads(out) if out.strip() else []
        for issue in issues:
            issue["labels"] = [l.get("name", "") if isinstance(l, dict) else str(l) for l in issue.get("labels", [])]
        return issues

    def comment_pr(self, pr_num: int, body: str) -> None:
        self._run_gh(["pr", "comment", str(pr_num), "--body", body])

    def close_pr(self, pr_num: int, comment: Optional[str] = None) -> None:
        if comment:
            self.comment_pr(pr_num, comment)
        self._run_gh(["pr", "close", str(pr_num)])

    def merge_pr(self, pr_num: int, head_oid: Optional[str] = None) -> str:
        args = ["pr", "merge", str(pr_num), "--merge", "--auto=false"]
        if head_oid:
            args.extend(["--match-head-commit", str(head_oid).strip()])
        self._run_gh(args)
        pull_res = subprocess.run(
            ["git", "pull", "--ff-only", "origin", "main"],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            check=False,
        )
        if pull_res.returncode != 0:
            logger.debug("git pull --ff-only origin main returned %s", pull_res.returncode)

        merge_sha = ""
        try:
            out = self._run_gh(["pr", "view", str(pr_num), "--json", "mergeCommit"])
            data = json.loads(out)
            merge_sha = (data.get("mergeCommit") or {}).get("oid", "")
        except Exception as exc:
            logger.debug("Failed getting mergeCommit: %s", exc)
        if not merge_sha:
            try:
                res = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=str(self.root),
                    capture_output=True,
                    text=True,
                    check=True,
                )
                merge_sha = res.stdout.strip()
            except Exception as exc:
                logger.debug("Failed git rev-parse HEAD: %s", exc)
        return merge_sha

    def set_pr_labels(self, pr_num: int, add: List[str], remove: List[str]) -> None:
        if not add and not remove:
            return
        args = ["pr", "edit", str(pr_num)]
        if add:
            args.extend(["--add-label", ",".join(add)])
        if remove:
            args.extend(["--remove-label", ",".join(remove)])
        self._run_gh(args)

    def comment_issue(self, issue_num: int, body: str) -> None:
        self._run_gh(["issue", "comment", str(issue_num), "--body", body])

    def close_issue(self, issue_num: int) -> None:
        self._run_gh(["issue", "close", str(issue_num)])

    def set_issue_labels(self, issue_num: int, add: List[str], remove: List[str]) -> None:
        if not add and not remove:
            return
        args = ["issue", "edit", str(issue_num)]
        if add:
            args.extend(["--add-label", ",".join(add)])
        if remove:
            args.extend(["--remove-label", ",".join(remove)])
        self._run_gh(args)

    def create_issue(self, title: str, body: str, labels: List[str]) -> Dict[str, Any]:
        args = ["issue", "create", "--title", title, "--body", body]
        for lbl in labels:
            args.extend(["--label", lbl])
        out = self._run_gh(args).strip()
        m_num = re.search(r"/issues/(\d+)", out)
        return {
            "number": int(m_num.group(1)) if m_num else 0,
            "url": out,
            "title": title,
            "body": body,
            "labels": list(labels),
        }

    def create_fallback_pr(
        self,
        *,
        head_ref: str,
        title: str,
        body: str,
        changed_files: List[str],
        translation_xml: str,
        labels: List[str],
    ) -> Dict[str, Any]:
        subprocess.run(
            ["git", "checkout", "-B", head_ref, "main"],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            check=True,
        )
        try:
            for rel_p in changed_files:
                abs_p = self.root / rel_p
                abs_p.parent.mkdir(parents=True, exist_ok=True)
                abs_p.write_text(translation_xml, encoding="utf-8")
                subprocess.run(["git", "add", rel_p], cwd=str(self.root), check=True, capture_output=True)
            subprocess.run(
                [
                    "git",
                    "-c", f"user.name={self.bot_git_name}",
                    "-c", f"user.email={self.bot_git_email}",
                    "commit",
                    "-m", title,
                ],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                check=True,
            )
            head_oid = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "push", "--force-with-lease", "origin", head_ref],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                check=True,
            )
        finally:
            subprocess.run(
                ["git", "checkout", "main"],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                check=True,
            )

        args = ["pr", "create", "--base", "main", "--head", head_ref, "--title", title, "--body", body]
        for lbl in labels:
            args.extend(["--label", lbl])
        out = self._run_gh(args).strip()
        m_num = re.search(r"/pull/(\d+)", out)
        if not m_num:
            raise RuntimeError(f"Failed parsing fallback PR number from gh output: {out}")
        return {
            "number": int(m_num.group(1)),
            "head_ref": head_ref,
            "head_oid": head_oid,
            "title": title,
            "body": body,
            "changed_files": list(changed_files),
            "labels": list(labels),
        }

    def get_release(self, tag: str) -> Optional[Dict[str, Any]]:
        out = self._run_gh(["release", "view", tag, "--json", "tagName,assets"], check=False)
        if not out.strip():
            return None
        data = json.loads(out)
        return {
            "tag": data.get("tagName", tag),
            "assets": [
                {"name": a.get("name", ""), "size": int(a.get("size", 0))}
                for a in (data.get("assets") or [])
                if isinstance(a, dict)
            ],
        }

    def upload_release_assets(self, tag: str, assets: List[str]) -> None:
        if not assets:
            return
        self._run_gh(["release", "upload", tag, "--clobber"] + [str(a) for a in assets])

    def create_release(self, tag: str, title: str, notes: str, assets: List[str]) -> None:
        args = ["release", "create", tag, "--title", title, "--notes", notes]
        for a in assets:
            args.append(str(a))
        self._run_gh(args)


def _default_evaluate_pr(
    pr: Dict[str, Any],
    root: Path,
) -> Dict[str, Any]:
    """Offline fallback evaluator returning fallback_unverified=True (holds PR in Evaluating per REQ-024)."""
    return {
        "verdict": "FAIL",
        "weighted_total": 0.0,
        "fallback_unverified": True,
        "dimension_scores": {},
        "issues": [],
    }


def _find_matching_issue(
    issues: List[Dict[str, Any]],
    *,
    slug: Optional[str],
    ch_id: Optional[str],
    body_issue_ref: Optional[str],
) -> Optional[Dict[str, Any]]:
    if body_issue_ref:
        m = re.match(r"^#?(\d+)$", str(body_issue_ref).strip())
        if m:
            num = int(m.group(1))
            for iss in issues:
                if iss.get("number") == num:
                    iss_ch, iss_slug = _extract_issue_metadata(iss)
                    if (not slug or not iss_slug or iss_slug == slug) and (not ch_id or not iss_ch or iss_ch == ch_id):
                        return iss
    for iss in issues:
        iss_ch, iss_slug = _extract_issue_metadata(iss)
        if ch_id and iss_ch == ch_id and (not slug or not iss_slug or iss_slug == slug):
            return iss
    return None


def _latest_bot_prescreen_for_sha(
    comments: List[Dict[str, Any]],
    bot_login: str,
    head_oid: str,
) -> Tuple[Optional[str], int]:
    """Return (status, comment_index) of chronologically latest bot_login [collab-prescreen] for head_oid."""
    latest_status: Optional[str] = None
    latest_idx = -1
    for idx, c in enumerate(comments):
        if _normalize_comment_author(c) != bot_login:
            continue
        body = str(c.get("body") or "")
        for m in _PRESCREEN_RE.finditer(body):
            if m.group(1).strip() == head_oid:
                latest_status = m.group(2).strip()
                latest_idx = idx
    return latest_status, latest_idx


def _has_post_prescreen_approve_intake(
    comments: List[Dict[str, Any]],
    prescreen_idx: int,
    human_approver: str,
    head_oid: str,
) -> bool:
    for c in comments[prescreen_idx + 1 :]:
        if _normalize_comment_author(c) != human_approver:
            continue
        for m in _APPROVE_INTAKE_RE.finditer(str(c.get("body") or "")):
            if m.group(1).strip() == head_oid:
                return True
    return False


def _has_commit_bound_approval_review(
    reviews: List[Dict[str, Any]],
    pr_author: str,
    human_approver: str,
    head_oid: str,
) -> bool:
    if pr_author == human_approver:
        return False
    for r in reviews:
        r_auth = str(r.get("author_login") or (r.get("author") or {}).get("login") or "").strip()
        if r_auth != human_approver:
            continue
        if str(r.get("state") or "").strip().upper() != "APPROVED":
            continue
        rev_oid = _extract_review_commit_oid(r)
        if head_oid:
            if rev_oid == head_oid:
                return True
        else:
            return True
    return False


def _invoke_seed_fn(
    seed_fn: Callable[..., Any],
    root: Path,
    slug: str,
    *,
    source_injection_cleared: bool = False,
) -> Any:
    try:
        return seed_fn(root, slug, source_injection_cleared=source_injection_cleared)
    except TypeError:
        return seed_fn(root, slug)


def run_sweep(
    repo_root: Optional[Union[Path, str]] = None,
    *,
    client: Optional[Any] = None,
    evaluate_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    seed_fn: Optional[Callable[..., Any]] = None,
    compile_fn: Optional[Callable[[Path, str], List[str]]] = None,
    fallback_translate_fn: Optional[Callable[[str, Dict[str, Any], Dict[str, Any]], str]] = None,
    now: Optional[float] = None,
    window_hours: Optional[float] = None,
    fallback_hours: Optional[float] = None,
    max_revise_rounds: Optional[int] = None,
    bot_login: Optional[str] = None,
    human_approver: Optional[str] = None,
    release_formats: Optional[Sequence[str]] = None,
    max_source_bytes: Optional[int] = None,
    allowed_licenses: Optional[Sequence[str]] = None,
    supported_pairs: Optional[Sequence[Tuple[str, str]]] = None,
    lock_path: Optional[Union[Path, str]] = None,
    recover_runs: bool = False,
) -> Dict[str, Any]:
    """Execute an idempotent sweep across book submissions, translation PRs, starved chapters, and releases."""
    cfg = None
    if (
        repo_root is None
        or window_hours is None
        or fallback_hours is None
        or max_revise_rounds is None
        or bot_login is None
        or human_approver is None
        or release_formats is None
    ):
        from scripts.collab_config import load_collab_config

        cfg = load_collab_config()
        repo_root = repo_root or cfg.workspace_dir
        window_hours = window_hours if window_hours is not None else cfg.window_hours
        fallback_hours = fallback_hours if fallback_hours is not None else cfg.fallback_hours
        max_revise_rounds = max_revise_rounds if max_revise_rounds is not None else cfg.max_revise_rounds
        bot_login = bot_login or cfg.bot_login
        human_approver = human_approver or cfg.human_approver
        release_formats = release_formats or cfg.release_formats
        max_source_bytes = max_source_bytes if max_source_bytes is not None else cfg.max_source_bytes
        allowed_licenses = allowed_licenses or cfg.allowed_licenses
        supported_pairs = supported_pairs or cfg.supported_pairs

    root = Path(repo_root).resolve()
    eff_lock = Path(lock_path) if lock_path is not None else (root / ".sweep.lock")

    with non_blocking_sweep_lock(eff_lock) as acquired:
        if not acquired:
            logger.info("sweep already running")
            return {
                "status": "skipped_locked",
                "reason": "sweep already running",
            }

        # REQ-030: Verify clean main checkout (with optional --recover-runs) & fast-forward to origin/main
        sync_clean_main(root, recover_runs=recover_runs)

        cur_now = float(now) if now is not None else time.time()
        gh = client if client is not None else GitHubCLIClient(bot_login=bot_login, repo_root=root)

        sweep_result: Dict[str, Any] = {
            "status": "completed",
            "processed_submissions": [],
            "processed_translations": [],
            "selected_winners": [],
            "starved_chapters": [],
            "releases_created": [],
        }

        prs = gh.list_open_prs()
        issues = gh.list_open_issues()

        # ------------------------------------------------------------------
        # (a) Process open book submission PRs (head_ref starts with 'book/')
        # ------------------------------------------------------------------
        for pr in prs:
            head_ref = str(pr.get("head_ref") or pr.get("headRefName") or "")
            if not head_ref.startswith("book/"):
                continue

            head_oid = str(pr.get("head_oid") or pr.get("headRefOid") or "").strip()
            pr_author = str(pr.get("author_login") or (pr.get("author") or {}).get("login") or "").strip()
            comments = pr.get("comments", [])
            reviews = pr.get("reviews", [])

            prescreen_status, prescreen_idx = _latest_bot_prescreen_for_sha(
                comments, str(bot_login), head_oid
            )

            manifest_data: Optional[Dict[str, Any]] = None
            val_res: Optional[Dict[str, Any]] = None

            if prescreen_status is None:
                changed_files = pr.get("changed_files", [])
                source_file_path: Optional[str] = None
                source_content: Optional[str] = None
                temp_files_to_cleanup: List[Path] = []

                for f in changed_files:
                    if f.endswith("manifest.json"):
                        manifest_raw = read_pr_file(
                            gh, pr["number"], head_ref, f, root, head_oid=head_oid
                        )
                        if manifest_raw:
                            try:
                                manifest_data = json.loads(manifest_raw)
                            except json.JSONDecodeError:
                                manifest_data = None
                    elif "/source/" in f"/{f}":
                        source_bytes = read_pr_file_bytes(
                            gh, pr["number"], head_ref, f, root, head_oid=head_oid
                        )
                        if source_bytes:
                            suffix = Path(f).suffix or ".txt"
                            tmp = tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=False)
                            tmp.write(source_bytes)
                            tmp.flush()
                            tmp.close()
                            source_file_path = tmp.name
                            temp_files_to_cleanup.append(Path(tmp.name))
                            if suffix.lower() in (".md", ".txt", ".xml", ".html", ".htm"):
                                source_content = source_bytes.decode("utf-8", errors="ignore")
                            else:
                                source_content = source_bytes[:4000].decode("utf-8", errors="ignore")
                        else:
                            local_f = root / f
                            if local_f.is_file():
                                source_file_path = str(local_f)

                try:
                    val_res = validate_submission_pr(
                        changed_files=changed_files,
                        manifest_data=manifest_data,
                        source_file_path=source_file_path,
                        source_text_sample=source_content[:4000] if source_content else None,
                        max_bytes=max_source_bytes,
                        allowed_licenses=allowed_licenses,
                        supported_pairs=supported_pairs,
                        head_ref=head_ref,
                        repo_root=root,
                    )
                finally:
                    for tmp_p in temp_files_to_cleanup:
                        tmp_p.unlink(missing_ok=True)

                comment_body = format_prescreen_comment(
                    val_res,
                    manifest_data or {},
                    human_approver=human_approver,
                    head_oid=head_oid,
                )
                gh.comment_pr(pr["number"], comment_body)
                prescreen_status = str(val_res.get("prescreen_status") or "FAIL")
                prescreen_idx = len(pr.get("comments", [])) - 1
            else:
                for f in pr.get("changed_files", []):
                    if f.endswith("manifest.json"):
                        manifest_raw = read_pr_file(
                            gh, pr["number"], head_ref, f, root, head_oid=head_oid
                        )
                        if manifest_raw:
                            try:
                                manifest_data = json.loads(manifest_raw)
                            except json.JSONDecodeError:
                                manifest_data = None

            if prescreen_status == "FAIL":
                continue

            post_cmd_approved = _has_post_prescreen_approve_intake(
                pr.get("comments", []), prescreen_idx, str(human_approver), head_oid
            )
            review_approved = (
                prescreen_status == "PASS"
                and _has_commit_bound_approval_review(
                    reviews, pr_author, str(human_approver), head_oid
                )
            )

            if review_approved or post_cmd_approved:
                gh.merge_pr(pr["number"], head_oid=head_oid if head_oid else None)
                slug = (
                    (val_res or {}).get("slug")
                    or ((manifest_data or {}).get("slug"))
                    or head_ref[len("book/") :]
                )
                if slug and seed_fn is not None:
                    _invoke_seed_fn(
                        seed_fn,
                        root,
                        slug,
                        source_injection_cleared=(prescreen_status == "WARN" and post_cmd_approved),
                    )
                sweep_result["processed_submissions"].append({
                    "pr": pr["number"],
                    "slug": slug,
                    "action": "merged_and_seeded",
                })

        # ------------------------------------------------------------------
        # (a.2) Reconcile seeding / seeded books in runs/ (REQ-020)
        # ------------------------------------------------------------------
        runs_root = root / "runs"
        existing_slugs: Set[str] = set()
        if runs_root.is_dir():
            all_issues_cache: Optional[List[Dict[str, Any]]] = None
            for slug_dir in sorted(runs_root.iterdir(), key=lambda p: p.name):
                if not slug_dir.is_dir():
                    continue
                slug_name = slug_dir.name
                existing_slugs.add(slug_name)
                cl_path = slug_dir / "checklist.json"
                if not cl_path.is_file():
                    continue
                try:
                    cl_obj = json.loads(cl_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue

                if isinstance(cl_obj, dict):
                    s_status = cl_obj.get("seed_status")
                    if s_status == "seeding" and seed_fn is not None:
                        _invoke_seed_fn(seed_fn, root, slug_name)
                    elif s_status == "seeded" and hasattr(gh, "create_issue"):
                        man_path = root / "books" / slug_name / "manifest.json"
                        br_path = slug_dir / "book_record.json"
                        if man_path.is_file() and br_path.is_file():
                            man_obj = json.loads(man_path.read_text(encoding="utf-8"))
                            br_obj = json.loads(br_path.read_text(encoding="utf-8"))
                            g_sha = str(br_obj.get("glossary_sha") or "")
                            if g_sha:
                                if all_issues_cache is None:
                                    all_issues_cache = (
                                        gh.list_all_issues()
                                        if hasattr(gh, "list_all_issues")
                                        else list(issues)
                                    )
                                created_recs = reconcile_missing_chapter_issues(
                                    runs_dir=slug_dir,
                                    book_slug=slug_name,
                                    manifest=man_obj,
                                    glossary_sha=g_sha,
                                    existing_issues=all_issues_cache,
                                    create_issue_fn=gh.create_issue,
                                )
                                for rec in created_recs:
                                    if rec.get("created") and isinstance(rec.get("issue_ref"), dict):
                                        new_iss = rec["issue_ref"]
                                        if new_iss not in issues:
                                            issues.append(new_iss)
                                        if all_issues_cache is not None and new_iss not in all_issues_cache:
                                            all_issues_cache.append(new_iss)

        # ------------------------------------------------------------------
        # (b) Process open translation PRs (head_ref starts with 'translate/')
        # ------------------------------------------------------------------
        for pr in prs:
            if pr.get("state", "open") != "open":
                continue
            head_ref = str(pr.get("head_ref") or pr.get("headRefName") or "")
            if not head_ref.startswith("translate/"):
                continue

            head_oid = str(pr.get("head_oid") or pr.get("headRefOid") or "").strip()
            revise_sha_token = _canonical_revise_sha(int(pr["number"]), head_oid)
            pr_author = str(pr.get("author_login") or (pr.get("author") or {}).get("login") or "").strip()
            current_labels = list(pr.get("labels", []))
            changed_files = list(pr.get("changed_files", []))
            comments = pr.get("comments", [])
            reviews = pr.get("reviews", [])

            recorded_revise_shas = count_bot_revise_rounds(comments, str(bot_login))
            flag_cleared, flag_rejected = _check_human_flag_commands(
                comments, str(human_approver), head_oid
            )

            # Immediate /reject-flag <head_oid> handling (REQ-023)
            if flag_rejected:
                recon = reconcile_status_labels(current_labels, "status:reassign")
                gh.set_pr_labels(pr["number"], recon["to_add"], recon["to_remove"])
                gh.close_pr(
                    pr["number"],
                    comment=f"Flagged PR rejected by @{human_approver} via /reject-flag; closing as reassign.",
                )
                continue

            # FlaggedForHuman idle hold vs author new-commit push (REQ-023)
            if "status:flagged" in current_labels and not flag_cleared:
                latest_flagged_sha: Optional[str] = None
                for c in comments:
                    if _normalize_comment_author(c) != bot_login:
                        continue
                    for m_f in _FLAGGED_MARKER_RE.finditer(str(c.get("body") or "")):
                        latest_flagged_sha = m_f.group(1).strip()
                if latest_flagged_sha is not None and latest_flagged_sha == head_oid:
                    continue
                # Author pushed a new commit while in FlaggedForHuman -> re-enter NeedsChecks without changing revise_round_count
                recon_nc = reconcile_status_labels(current_labels, "status:needs-checks")
                if recon_nc["to_add"] or recon_nc["to_remove"]:
                    gh.set_pr_labels(pr["number"], recon_nc["to_add"], recon_nc["to_remove"])
                    current_labels = recon_nc["final_labels"]

            # Candidate idle check: skip section (b) if current head_oid already has a valid candidate score and zero blocking reviews
            if "status:candidate" in current_labels:
                cand_score_rec = parse_latest_candidate_score(comments, str(bot_login), head_oid)
                is_blocked_now = has_blocking_changes_requested(
                    reviews=reviews,
                    comments=comments,
                    pr_author=pr_author,
                    bot_login=str(bot_login),
                    human_approver=str(human_approver),
                    current_head_oid=head_oid,
                )
                if cand_score_rec is not None and not is_blocked_now:
                    continue

            # Revise idle check: if head_oid already recorded in bot [collab-revise sha=<40-hex>], re-sweeping is a no-op (REQ-027)
            if revise_sha_token in recorded_revise_shas:
                if "status:revise" not in current_labels:
                    recon_r = reconcile_status_labels(current_labels, "status:revise")
                    gh.set_pr_labels(pr["number"], recon_r["to_add"], recon_r["to_remove"])
                continue
            elif "status:revise" in current_labels:
                # New commit pushed after Revise -> re-enter NeedsChecks
                recon_nc = reconcile_status_labels(current_labels, "status:needs-checks")
                if recon_nc["to_add"] or recon_nc["to_remove"]:
                    gh.set_pr_labels(pr["number"], recon_nc["to_add"], recon_nc["to_remove"])
                    current_labels = recon_nc["final_labels"]

            parsed_body = parse_pr_body(pr.get("body", ""))
            raw_chunk = parsed_body.get("chunk_id") or parsed_body.get("chunk")
            ch_id, slug = _extract_ch_id_and_slug(head_ref, raw_chunk)

            source_text = ""
            trans_bytes = b""
            trans_text = ""
            additions_bytes: Optional[bytes] = None
            expected_glossary_sha: Optional[str] = None
            book_record: Optional[Dict[str, Any]] = None
            manifest_obj: Optional[Dict[str, Any]] = None

            if slug and ch_id:
                s_file = root / "runs" / slug / ch_id / "source.txt"
                if s_file.is_file():
                    source_text = s_file.read_text(encoding="utf-8")
                else:
                    source_text = read_pr_file(
                        gh, pr["number"], head_ref, f"runs/{slug}/{ch_id}/source.txt", root, head_oid=head_oid
                    )

                t_rel = f"runs/{slug}/{ch_id}/translation.xml"
                trans_bytes = read_pr_file_bytes(
                    gh, pr["number"], head_ref, t_rel, root, head_oid=head_oid
                )
                trans_text = trans_bytes.decode("utf-8", errors="replace") if trans_bytes else ""

                br_file = root / "runs" / slug / "book_record.json"
                if br_file.is_file():
                    try:
                        book_record = json.loads(br_file.read_text(encoding="utf-8"))
                        expected_glossary_sha = (
                            book_record.get("glossary_sha") or book_record.get("expected_glossary_sha")
                        )
                    except json.JSONDecodeError:
                        book_record = None

                man_file = root / "books" / slug / "manifest.json"
                if man_file.is_file():
                    try:
                        manifest_obj = json.loads(man_file.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        manifest_obj = None

                if any(f.endswith("/analyze_additions.json") for f in changed_files):
                    add_rel = f"runs/{slug}/{ch_id}/analyze_additions.json"
                    additions_bytes = read_pr_file_bytes(
                        gh,
                        pr["number"],
                        head_ref,
                        add_rel,
                        root,
                        head_oid=head_oid,
                        allow_local_fallback=True,
                    )

            target_issue = _find_matching_issue(
                issues,
                slug=slug,
                ch_id=ch_id,
                body_issue_ref=parsed_body.get("issue"),
            )
            expected_issue_num = target_issue["number"] if target_issue else None

            val_pr = validate_translation_pr(
                head_ref=head_ref,
                author_login=pr_author,
                body=pr.get("body", ""),
                changed_files=changed_files,
                source_xml_text=source_text,
                translation_xml_text=trans_text,
                expected_glossary_sha=expected_glossary_sha,
                expected_issue_number=expected_issue_num,
                bot_login=bot_login,
                manifest=manifest_obj,
                translation_xml_bytes=trans_bytes if trans_bytes else None,
                additions_raw=additions_bytes,
                existing_slugs=existing_slugs if existing_slugs else None,
            )

            pr_inj_flags = list(val_pr.get("injection_flags") or [])
            structural_defects = [
                d for d in val_pr.get("defects", [])
                if "Prompt injection detected:" not in d
            ]

            # 1. Deterministic structural failure -> Revise / Reassign without LLM evaluation (REQ-021, REQ-027)
            if structural_defects:
                prior_rounds = len(recorded_revise_shas)
                if prior_rounds >= int(max_revise_rounds):
                    recon = reconcile_status_labels(current_labels, "status:reassign")
                    gh.set_pr_labels(pr["number"], recon["to_add"], recon["to_remove"])
                    gh.close_pr(
                        pr["number"],
                        comment=f"Revision limit ({max_revise_rounds} rounds) reached. Closing PR as reassign.",
                    )
                else:
                    recon = reconcile_status_labels(current_labels, "status:revise")
                    gh.set_pr_labels(pr["number"], recon["to_add"], recon["to_remove"])
                    defect_msg = (
                        f"[collab-revise sha={revise_sha_token}]\n"
                        "Deterministic checks failed:\n"
                        + "\n".join(f"- {d}" for d in structural_defects)
                    )
                    gh.comment_pr(pr["number"], defect_msg)
                continue

            # 2. Pre-LLM prompt-injection gate on PR / additions / source / translation (REQ-023)
            if pr_inj_flags and not flag_cleared:
                recon = reconcile_status_labels(current_labels, "status:flagged")
                gh.set_pr_labels(pr["number"], recon["to_add"], recon["to_remove"])
                gh.comment_pr(
                    pr["number"],
                    f"[collab-flagged sha={head_oid}]\n"
                    f"Alert @{human_approver}: PR #{pr['number']} flagged for human review "
                    f"(pre-LLM prompt injection): {'; '.join(pr_inj_flags)}",
                )
                continue

            # 3. Validate peer reviews & scan review bodies for prompt injection (REQ-022, REQ-023)
            review_inj_flags: List[str] = []
            valid_reviews_by_login: Dict[str, Dict[str, Any]] = {}
            for r in reviews:
                r_author = str(r.get("author_login") or (r.get("author") or {}).get("login") or "").strip()
                v_rev = validate_review(
                    pr_author_login=pr_author,
                    review_author_login=r_author,
                    review_body=str(r.get("body") or ""),
                    line_comment_count=int(r.get("line_comment_count") or 0),
                )
                if v_rev.get("injection_flags"):
                    review_inj_flags.extend(v_rev["injection_flags"])
                if v_rev.get("valid"):
                    v_rev["author_login"] = r_author
                    valid_reviews_by_login[r_author] = v_rev

            if review_inj_flags and not flag_cleared:
                recon = reconcile_status_labels(current_labels, "status:flagged")
                gh.set_pr_labels(pr["number"], recon["to_add"], recon["to_remove"])
                gh.comment_pr(
                    pr["number"],
                    f"[collab-flagged sha={head_oid}]\n"
                    f"Alert @{human_approver}: PR #{pr['number']} flagged for human review "
                    f"(review prompt injection): {'; '.join(review_inj_flags)}",
                )
                continue

            valid_reviews = list(valid_reviews_by_login.values())
            is_seed_ch01 = (ch_id == "ch01" and pr_author == bot_login)

            if len(valid_reviews) < 1 and not is_seed_ch01:
                recon = reconcile_status_labels(current_labels, "status:needs-review")
                if recon["to_add"] or recon["to_remove"]:
                    gh.set_pr_labels(pr["number"], recon["to_add"], recon["to_remove"])
                continue

            # 4. Enter Evaluating and invoke live evaluator (REQ-024, REQ-027)
            recon_eval = reconcile_status_labels(current_labels, "status:evaluating")
            if recon_eval["to_add"] or recon_eval["to_remove"]:
                gh.set_pr_labels(pr["number"], recon_eval["to_add"], recon_eval["to_remove"])
                current_labels = recon_eval["final_labels"]

            eval_exc: Optional[BaseException] = None
            eval_res: Optional[Dict[str, Any]] = None
            try:
                if evaluate_fn is not None:
                    eval_res = evaluate_fn(pr, root)
                else:
                    eval_res = _default_evaluate_pr(pr, root)
            except Exception as exc:
                eval_exc = exc

            verdict_res = compute_collab_verdict(
                eval_result=eval_res,
                reviewer_scores=[rv["scores"] for rv in valid_reviews if rv.get("scores")],
                book_record=book_record,
                revise_round_count=len(recorded_revise_shas),
                max_revise_rounds=int(max_revise_rounds),
                draft_text_or_len=trans_text or 100,
                eval_error=eval_exc,
                reviews=reviews,
                comments=comments,
                pr_author=pr_author,
                bot_login=str(bot_login),
                human_approver=str(human_approver),
                current_head_oid=head_oid,
                flag_cleared=flag_cleared,
            )
            outcome = verdict_res.get("outcome")

            if outcome == "EVALUATING":
                has_hold_msg = any(
                    _normalize_comment_author(c) == bot_login
                    and f"[collab-evaluating-hold sha={head_oid}]" in str(c.get("body") or "")
                    for c in pr.get("comments", [])
                )
                if not has_hold_msg:
                    gh.comment_pr(
                        pr["number"],
                        f"[collab-evaluating-hold sha={head_oid}]\n{verdict_res.get('reason')}",
                    )

            elif outcome == "FLAGGED_FOR_HUMAN":
                recon = reconcile_status_labels(current_labels, "status:flagged")
                gh.set_pr_labels(pr["number"], recon["to_add"], recon["to_remove"])
                gh.comment_pr(
                    pr["number"],
                    f"[collab-flagged sha={head_oid}]\n"
                    f"Alert @{human_approver}: PR #{pr['number']} has been flagged for human review: "
                    f"{verdict_res.get('reason')}",
                )

            elif outcome == "CANDIDATE":
                w_tot = float(verdict_res.get("weighted_total") or 0.0)
                maj_cnt = int(verdict_res.get("major_issue_count") or 0)
                min_cnt = int(verdict_res.get("minor_issue_count") or 0)
                pr["score_of_record"] = w_tot
                pr["weighted_total"] = w_tot
                pr["major_issue_count"] = maj_cnt
                pr["minor_issue_count"] = min_cnt
                pr["eval_res"] = eval_res
                pr["valid_reviews"] = valid_reviews

                recon = reconcile_status_labels(current_labels, "status:candidate")
                gh.set_pr_labels(pr["number"], recon["to_add"], recon["to_remove"])
                gh.comment_pr(
                    pr["number"],
                    f"[collab-candidate-score sha={head_oid} score={w_tot} major={maj_cnt} minor={min_cnt}]\n"
                    f"PR accepted as candidate with score {w_tot:.2f}.",
                )

                if target_issue is not None:
                    was_candidates = "status:candidates" in target_issue.get("labels", [])
                    recon_iss = reconcile_status_labels(target_issue.get("labels", []), "status:candidates")
                    if recon_iss["to_add"] or recon_iss["to_remove"]:
                        gh.set_issue_labels(target_issue["number"], recon_iss["to_add"], recon_iss["to_remove"])
                    if not was_candidates:
                        gh.comment_issue(
                            target_issue["number"],
                            f"[collab-window-start epoch={cur_now}]\n"
                            f"Candidate window started at {cur_now} for {slug}-{ch_id} (PR #{pr['number']}).",
                        )

            elif outcome == "REVISE":
                recon = reconcile_status_labels(current_labels, "status:revise")
                gh.set_pr_labels(pr["number"], recon["to_add"], recon["to_remove"])
                gh.comment_pr(
                    pr["number"],
                    f"[collab-revise sha={revise_sha_token}]\n"
                    f"Evaluation outcome REVISE: {verdict_res.get('reason')}",
                )

            elif outcome == "REASSIGN":
                recon = reconcile_status_labels(current_labels, "status:reassign")
                gh.set_pr_labels(pr["number"], recon["to_add"], recon["to_remove"])
                gh.close_pr(
                    pr["number"],
                    comment=f"Evaluation outcome REASSIGN: {verdict_res.get('reason')}",
                )

        # ------------------------------------------------------------------
        # (c) Process chapter issues in status:candidates (REQ-025, REQ-026, REQ-027)
        # ------------------------------------------------------------------
        for issue in issues:
            if issue.get("state", "open") != "open":
                continue
            labels = list(issue.get("labels", []))
            if "status:candidates" not in labels:
                continue

            ch_id, slug = _extract_issue_metadata(issue)

            chapter_candidates: List[Dict[str, Any]] = []
            for pr in prs:
                if pr.get("state", "open") != "open":
                    continue
                if "status:candidate" not in pr.get("labels", []):
                    continue

                pr_head = str(pr.get("head_ref") or pr.get("headRefName") or "")
                pr_body = parse_pr_body(pr.get("body", ""))
                pr_ch, pr_slug = _extract_ch_id_and_slug(pr_head, pr_body.get("chunk_id") or pr_body.get("chunk"))
                if not (ch_id and pr_ch == ch_id and (not slug or not pr_slug or pr_slug == slug)):
                    continue

                current_oid = str(pr.get("head_oid") or pr.get("headRefOid") or "").strip()
                pr_author = str(pr.get("author_login") or (pr.get("author") or {}).get("login") or "").strip()

                score_rec = parse_latest_candidate_score(pr.get("comments", []), str(bot_login), current_oid)
                if score_rec is not None:
                    s_val = float(score_rec["score_of_record"])
                    maj_val = int(score_rec["major_issue_count"])
                    min_val = int(score_rec["minor_issue_count"])
                elif pr.get("score_of_record") is not None:
                    s_val = float(pr["score_of_record"])
                    maj_val = int(pr.get("major_issue_count", 0))
                    min_val = int(pr.get("minor_issue_count", 0))
                else:
                    continue

                is_blocking = has_blocking_changes_requested(
                    reviews=pr.get("reviews", []),
                    comments=pr.get("comments", []),
                    pr_author=pr_author,
                    bot_login=str(bot_login),
                    human_approver=str(human_approver),
                    current_head_oid=current_oid,
                )

                chapter_candidates.append({
                    "pr_number": pr["number"],
                    "score_of_record": s_val,
                    "weighted_total": s_val,
                    "major_issue_count": maj_val,
                    "minor_issue_count": min_val,
                    "blocking_changes_requested": is_blocking,
                    "unresolved_threads": int(pr.get("unresolved_threads", 0)),
                    "author_login": pr_author,
                    "eval_res": pr.get("eval_res"),
                    "valid_reviews": pr.get("valid_reviews", []),
                    "pr_dict": pr,
                })

            # Cohort reversion: if 0 open Candidate PRs remain, revert issue to status:open (REQ-027 / AC-026-2)
            if not chapter_candidates:
                recon_open = reconcile_status_labels(labels, "status:open")
                if recon_open["to_add"] or recon_open["to_remove"]:
                    gh.set_issue_labels(issue["number"], recon_open["to_add"], recon_open["to_remove"])
                continue

            # Resolve window_started_at strictly from latest bot_login [collab-window-start] (never issue.created_at!)
            window_started_at = parse_latest_window_start(issue.get("comments", []), str(bot_login))
            if window_started_at is None:
                gh.comment_issue(
                    issue["number"],
                    f"[collab-window-start epoch={cur_now}]\n"
                    f"Initialized candidate window clock at {cur_now}.",
                )
                window_started_at = cur_now

            sel_res = select_winning_pr(
                chapter_candidates,
                window_started_at=window_started_at,
                now=cur_now,
                window_hours=float(window_hours),
            )

            if not (sel_res.get("window_closed") and sel_res.get("winner")):
                continue

            winner = sel_res["winner"]
            winner_pr = winner["pr_dict"]
            winner_num = int(winner["pr_number"])
            winner_oid = str(winner_pr.get("head_oid") or winner_pr.get("headRefOid") or "").strip()
            winner_author = str(winner.get("author_login") or "")

            if not (slug and ch_id):
                continue

            run_dir = root / "runs" / slug
            ch_dir = run_dir / ch_id
            br_file = run_dir / "book_record.json"
            additions_file = ch_dir / "analyze_additions.json"

            # Re-verify REQ-022 reviews on winner
            valid_revs = list(winner.get("valid_reviews") or [])
            if not valid_revs:
                by_rev: Dict[str, Dict[str, Any]] = {}
                for r in winner_pr.get("reviews", []):
                    r_auth = str(r.get("author_login") or (r.get("author") or {}).get("login") or "").strip()
                    v_rev = validate_review(
                        pr_author_login=winner_author,
                        review_author_login=r_auth,
                        review_body=str(r.get("body") or ""),
                        line_comment_count=int(r.get("line_comment_count") or 0),
                    )
                    if v_rev.get("valid"):
                        v_rev["author_login"] = r_auth
                        by_rev[r_auth] = v_rev
                valid_revs = list(by_rev.values())

            reviewer_logins = sorted({
                str(r["author_login"])
                for r in valid_revs
                if r.get("author_login") and r.get("author_login") != winner_author
            })
            if not reviewer_logins and ch_id != "ch01":
                logger.warning("Chapter %s PR #%s has no verified REQ-022 reviewers; blocking merge", ch_id, winner_num)
                continue

            # REQ-025: Read analyze_additions.json strictly from <winner_head_oid> (NEVER local remnants!)
            winner_changed = winner_pr.get("changed_files", [])
            has_additions_in_pr = any(f.endswith("/analyze_additions.json") for f in winner_changed)
            winner_additions_bytes = b""
            if has_additions_in_pr:
                winner_additions_bytes = read_pr_file_bytes(
                    gh,
                    winner_num,
                    str(winner_pr.get("head_ref") or ""),
                    f"runs/{slug}/{ch_id}/analyze_additions.json",
                    root,
                    head_oid=winner_oid,
                    allow_local_fallback=False,
                )
                if not winner_additions_bytes or not br_file.is_file():
                    logger.warning("Missing analyze_additions blob or book_record for PR #%s", winner_num)
                    continue

                pre_bind = bind_winning_proposals(
                    br_file,
                    winner_additions_bytes,
                    apply=False,
                )
                if not pre_bind["valid"] or pre_bind["conflict"]:
                    rev_token = _canonical_revise_sha(winner_num, winner_oid)
                    prior_r = len(count_bot_revise_rounds(winner_pr.get("comments", []), str(bot_login)))
                    details_str = "; ".join(pre_bind["conflicts"] or pre_bind["defects"])
                    if prior_r >= int(max_revise_rounds):
                        recon = reconcile_status_labels(winner_pr.get("labels", []), "status:reassign")
                        gh.set_pr_labels(winner_num, recon["to_add"], recon["to_remove"])
                        gh.close_pr(winner_num, comment=f"Glossary proposal conflict at revision limit: {details_str}")
                    else:
                        recon = reconcile_status_labels(winner_pr.get("labels", []), "status:revise")
                        gh.set_pr_labels(winner_num, recon["to_add"], recon["to_remove"])
                        gh.comment_pr(
                            winner_num,
                            f"[collab-revise sha={rev_token}]\n"
                            f"Glossary conflict detected during merge-time proposal check: {details_str}",
                        )
                    continue

            # Merge winner PR with --match-head-commit
            merge_ret = gh.merge_pr(winner_num, head_oid=winner_oid if winner_oid else None)
            recon_merged = reconcile_status_labels(winner_pr.get("labels", []), "status:merged")
            gh.set_pr_labels(winner_num, recon_merged["to_add"], recon_merged["to_remove"])

            merge_sha = (
                merge_ret
                if isinstance(merge_ret, str) and len(merge_ret) in (40, 64)
                else hashlib.sha1(f"merge:{winner_num}:{winner_oid}".encode("utf-8")).hexdigest()
            )

            # Close non-selected PRs with status:not-selected
            rejected_pr_nums: List[int] = []
            for non_sel in sel_res.get("non_selected", []):
                ns_num = int(non_sel["pr_number"])
                rejected_pr_nums.append(ns_num)
                ns_pr = next((p for p in prs if p["number"] == ns_num), None)
                if ns_pr is not None:
                    recon_ns = reconcile_status_labels(ns_pr.get("labels", []), "status:not-selected")
                    gh.set_pr_labels(ns_num, recon_ns["to_add"], recon_ns["to_remove"])
                gh.close_pr(ns_num, comment=format_non_selected_comment(non_sel, winner))

            # Apply non-conflicting proposals post-merge preserving immutable glossary_sha
            if has_additions_in_pr and winner_additions_bytes and br_file.is_file():
                bind_winning_proposals(
                    br_file,
                    winner_additions_bytes,
                    chapter_additions_path=additions_file,
                    apply=True,
                )

            if winner.get("eval_res") and br_file.is_file():
                frozen_sha = json.loads(br_file.read_text(encoding="utf-8")).get("glossary_sha")
                update_book_record_from_eval(
                    book_record_path=br_file,
                    chapter_dir=ch_dir,
                    bind=True,
                    chapter_id=ch_id,
                    eval_data=winner["eval_res"],
                )
                if frozen_sha is not None:
                    br_after = json.loads(br_file.read_text(encoding="utf-8"))
                    br_after["glossary_sha"] = frozen_sha
                    atomic_write_json(br_file, br_after)

            # Write chNN/final.txt from <winner_head_oid> translation.xml
            xml_text = read_pr_file(
                gh,
                winner_num,
                str(winner_pr.get("head_ref") or ""),
                f"runs/{slug}/{ch_id}/translation.xml",
                root,
                head_oid=winner_oid,
            )
            if xml_text:
                ch_dir.mkdir(parents=True, exist_ok=True)
                (ch_dir / "final.txt").write_text(strip_segments_to_final(xml_text), encoding="utf-8")

            cl_file = run_dir / "checklist.json"
            _update_checklist_chapter_done(cl_file, ch_id)

            real_glossary_sha = ""
            if br_file.is_file():
                real_glossary_sha = str(
                    json.loads(br_file.read_text(encoding="utf-8")).get("glossary_sha") or ""
                )

            score_of_rec = float(winner["score_of_record"])
            rev_score = compute_reviewer_score(
                chapter=ch_id,
                score_of_record=score_of_rec,
                reviewer_logins=reviewer_logins,
                valid_reviews=valid_revs,
            )
            ledger_file = run_dir / "collab_ledger.json"
            ledger_entry = {
                "chapter": ch_id,
                "issue_number": int(issue["number"]),
                "selected_pr": winner_num,
                "rejected_prs": sorted(rejected_pr_nums),
                "translator_login": winner_author,
                "reviewer_logins": reviewer_logins,
                "reviewer_score": rev_score,
                "score_of_record": score_of_rec,
                "verdict": "PASS",
                "merge_sha": merge_sha,
                "glossary_sha": real_glossary_sha,
            }
            append_ledger_entry(
                ledger_file,
                slug,
                ledger_entry,
                valid_reviews=valid_revs,
            )

            staged_paths: List[Path] = [
                ch_dir / "final.txt",
                cl_file,
                br_file,
                ledger_file,
            ]
            if has_additions_in_pr and additions_file.is_file():
                staged_paths.append(additions_file)

            commit_and_push(
                root,
                staged_paths,
                f"chore({slug}): merge winning translation for {ch_id} (PR #{winner_num})",
                client=gh,
                book_slug=slug,
            )

            recon_done = reconcile_status_labels(issue.get("labels", []), "status:done")
            gh.set_issue_labels(issue["number"], recon_done["to_add"], recon_done["to_remove"])
            gh.close_issue(issue["number"])

            sweep_result["selected_winners"].append({
                "chapter": ch_id,
                "winner_pr": winner_num,
                "slug": slug,
            })

        # ------------------------------------------------------------------
        # (d) Starved chapters in status:open — bounded 2-attempt 48 h bot fallback (REQ-026)
        # ------------------------------------------------------------------
        closed_prs_cache: Optional[List[Dict[str, Any]]] = None
        for issue in issues:
            if issue.get("state", "open") != "open":
                continue
            labels = list(issue.get("labels", []))
            if "status:open" not in labels:
                continue

            ch_id, slug = _extract_issue_metadata(issue)
            if not ch_id:
                continue

            issue_created = _parse_timestamp(issue.get("created_at"), cur_now)

            # Count ALL open PRs of ANY state targeting <slug>-<chNN>
            open_matching_prs = []
            for pr in prs:
                if pr.get("state", "open") != "open":
                    continue
                p_head = str(pr.get("head_ref") or pr.get("headRefName") or "")
                p_body = parse_pr_body(pr.get("body", ""))
                p_ch, p_slug = _extract_ch_id_and_slug(p_head, p_body.get("chunk_id") or p_body.get("chunk"))
                if p_ch == ch_id and (not slug or not p_slug or p_slug == slug):
                    open_matching_prs.append(pr)

            has_candidate = any("status:candidate" in p.get("labels", []) for p in open_matching_prs)

            # Determine latest_pr_closed_at across closed PRs targeting <slug>-<chNN>
            latest_closed_ts: Optional[float] = None
            if hasattr(gh, "list_closed_prs"):
                if closed_prs_cache is None:
                    closed_prs_cache = gh.list_closed_prs()
                for cpr in closed_prs_cache:
                    c_head = str(cpr.get("head_ref") or cpr.get("headRefName") or "")
                    c_body = parse_pr_body(cpr.get("body", ""))
                    c_ch, c_slug = _extract_ch_id_and_slug(c_head, c_body.get("chunk_id") or c_body.get("chunk"))
                    if c_ch == ch_id and (not slug or not c_slug or c_slug == slug):
                        ts_val = cpr.get("closed_at") or cpr.get("closedAt")
                        if ts_val is not None:
                            parsed_c = _parse_timestamp(ts_val, 0.0)
                            if parsed_c > 0 and (latest_closed_ts is None or parsed_c > latest_closed_ts):
                                latest_closed_ts = parsed_c

            # Pre-LLM injection scan on chNN/source.txt before any fallback translation
            has_src_injection = False
            src_xml_text = ""
            if slug:
                s_p = root / "runs" / slug / ch_id / "source.txt"
                if s_p.is_file():
                    src_xml_text = s_p.read_text(encoding="utf-8")
                    for seg in parse_segments(src_xml_text):
                        if detect_prompt_injection(str(seg.get("original", "")), allow_rubric_lines=False):
                            has_src_injection = True
                            break
                    if not has_src_injection:
                        outside = SEGMENT_BLOCK_RE.sub("", src_xml_text)
                        if detect_prompt_injection(outside, allow_rubric_lines=False):
                            has_src_injection = True

            starved_state = evaluate_starved_chapter(
                issue_opened_at=issue_created,
                has_pass_candidate=has_candidate,
                now=cur_now,
                fallback_hours=float(fallback_hours),
                latest_pr_closed_at=latest_closed_ts,
                open_pr_count=len(open_matching_prs),
                comments=issue.get("comments", []),
                bot_login=str(bot_login),
                human_approver=str(human_approver),
                max_fallback_attempts=2,
                has_source_injection=has_src_injection,
            )

            if starved_state["should_escalate"]:
                gh.comment_issue(issue["number"], starved_state["escalation_marker"])
                continue

            if starved_state["should_open_fallback"]:
                sweep_result["starved_chapters"].append({
                    "issue": issue["number"],
                    "chapter": ch_id,
                    "slug": slug,
                    "attempt": starved_state["next_attempt"],
                })

                if slug and src_xml_text and fallback_translate_fn is not None and hasattr(gh, "create_fallback_pr"):
                    br_p = root / "runs" / slug / "book_record.json"
                    man_p = root / "books" / slug / "manifest.json"
                    if br_p.is_file() and man_p.is_file():
                        br_obj = json.loads(br_p.read_text(encoding="utf-8"))
                        man_obj = json.loads(man_p.read_text(encoding="utf-8"))
                        g_sha = str(br_obj.get("glossary_sha") or "")
                        src_lang = str(man_obj.get("source_lang", "en")).strip()
                        tgt_lang = str(man_obj.get("target_lang", "vi")).strip()
                        segs = parse_segments(src_xml_text)
                        if segs and g_sha:
                            try:
                                fb_xml = fallback_translate_fn(src_xml_text, br_obj, man_obj)
                                if fb_xml and fb_xml.strip():
                                    if src_lang == "zh":
                                        fb_xml = ensure_hanviet_segments(fb_xml, book_record=br_obj)
                                    fb_head = f"translate/{slug}-{ch_id}-{bot_login}"
                                    fb_body = build_translation_pr_body(
                                        agent_login=str(bot_login),
                                        engine_model="fallback-bot",
                                        source_lang=src_lang,
                                        target_lang=tgt_lang,
                                        book_slug=slug,
                                        ch_name=ch_id,
                                        seg_first=segs[0]["id"],
                                        seg_last=segs[-1]["id"],
                                        issue_ref=f"#{issue['number']}",
                                        glossary_sha=g_sha,
                                    )
                                    fb_pr = gh.create_fallback_pr(
                                        head_ref=fb_head,
                                        title=f"feat({slug}-{ch_id}): automated 48h fallback translation",
                                        body=fb_body,
                                        changed_files=[f"runs/{slug}/{ch_id}/translation.xml"],
                                        translation_xml=fb_xml,
                                        labels=[
                                            f"agent:{bot_login}",
                                            f"book:{slug}",
                                            f"lang:{src_lang}-{tgt_lang}",
                                        ],
                                    )
                                    gh.comment_issue(
                                        issue["number"],
                                        f"[collab-fallback pr=#{fb_pr['number']} attempt={starved_state['next_attempt']}]\n"
                                        f"Opened automated fallback PR #{fb_pr['number']}.",
                                    )
                            except Exception as exc:
                                logger.warning("Fallback PR generation failed for %s-%s (will retry): %s", slug, ch_id, exc)

        # ------------------------------------------------------------------
        # (e) Completeness verification, asset-verified backfill, & release (REQ-029)
        # ------------------------------------------------------------------
        if isinstance(release_formats, str):
            eff_formats = tuple(tok.strip().lower() for tok in release_formats.split(",") if tok.strip())
        else:
            eff_formats = tuple(str(f).strip().lower() for f in (release_formats or ("epub", "pdf", "docx")) if str(f).strip())
        if runs_root.is_dir():
            for slug_dir in sorted(runs_root.iterdir(), key=lambda p: p.name):
                if not slug_dir.is_dir():
                    continue
                slug_name = slug_dir.name
                cl_file = slug_dir / "checklist.json"
                if not cl_file.is_file():
                    continue

                comp_check = verify_book_completeness(slug_dir)
                if not comp_check.get("ready_to_publish"):
                    continue

                tag = f"{slug_name}-v1.0"
                ledger_file = slug_dir / "collab_ledger.json"
                ledger_data = read_ledger(ledger_file)
                if ledger_data.get("released_tag") == tag:
                    continue

                existing_rel = gh.get_release(tag) if hasattr(gh, "get_release") else None
                if existing_rel is not None:
                    attached = existing_rel.get("assets") or []
                    present_fmts = {
                        fmt
                        for fmt in eff_formats
                        if any(
                            str(a.get("name", "")).lower().endswith(f".{fmt}") and int(a.get("size", 0)) > 0
                            for a in attached
                        )
                    }
                    missing_fmts = [fmt for fmt in eff_formats if fmt not in present_fmts]
                    if missing_fmts:
                        if not compile_fn:
                            continue
                        compiled_paths = compile_fn(root, slug_name) or []
                        to_upload = [
                            p for p in compiled_paths
                            if any(str(p).lower().endswith(f".{fmt}") for fmt in missing_fmts)
                            and Path(p).is_file()
                            and Path(p).stat().st_size > 0
                        ]
                        if len(to_upload) < len(missing_fmts):
                            continue
                        if hasattr(gh, "upload_release_assets"):
                            gh.upload_release_assets(tag, to_upload)

                    ledger_data["book_slug"] = slug_name
                    ledger_data["released_tag"] = tag
                    atomic_write_json(ledger_file, ledger_data)
                    commit_and_push(
                        root,
                        [ledger_file],
                        f"chore({slug_name}): backfill verified release tag {tag}",
                        client=gh,
                        book_slug=slug_name,
                    )
                    sweep_result["releases_created"].append({"slug": slug_name, "tag": tag, "backfilled": True})
                    continue

                assets: List[str] = []
                if compile_fn:
                    assets = compile_fn(root, slug_name) or []

                gh.create_release(
                    tag,
                    title=f"Release {slug_name} v1.0",
                    notes=f"Automated release for {slug_name} upon 100% chapter completion.",
                    assets=assets,
                )
                ledger_data["book_slug"] = slug_name
                ledger_data["released_tag"] = tag
                atomic_write_json(ledger_file, ledger_data)
                commit_and_push(
                    root,
                    [ledger_file],
                    f"chore({slug_name}): record published release tag {tag}",
                    client=gh,
                    book_slug=slug_name,
                )
                sweep_result["releases_created"].append({"slug": slug_name, "tag": tag})

        return sweep_result


def live_evaluate_pr(pr: Dict[str, Any], root: Path) -> Dict[str, Any]:
    """Production evaluation helper for a translation PR via configured evaluation_engine."""
    from scripts.collab_config import load_collab_config
    from scripts.evaluate import run_evaluation

    cfg = load_collab_config()
    root_path = Path(root).resolve()
    head_ref = str(pr.get("head_ref") or pr.get("headRefName") or "")
    head_oid = str(pr.get("head_oid") or pr.get("headRefOid") or "").strip()
    raw_chunk = parse_pr_body(pr.get("body", "")).get("chunk_id")
    ch_id, slug = _extract_ch_id_and_slug(head_ref, raw_chunk)

    s_file = root_path / "runs" / str(slug) / str(ch_id) / "source.txt"
    source_xml = s_file.read_text(encoding="utf-8") if s_file.exists() else ""

    gh = GitHubCLIClient(repo_root=root_path, bot_login=cfg.bot_login)
    trans_xml = read_pr_file(
        gh,
        int(pr.get("number", 0)),
        head_ref,
        f"runs/{slug}/{ch_id}/translation.xml",
        root_path,
        head_oid=head_oid,
    )

    rec_path = root_path / "runs" / str(slug) / "book_record.json"
    book_record = json.loads(rec_path.read_text(encoding="utf-8")) if rec_path.exists() else {}

    with tempfile.TemporaryDirectory() as td:
        t_dir = Path(td)
        s_p = t_dir / "source.txt"
        d_p = t_dir / "translation.xml"
        b_p = t_dir / "book_record.json"
        o_p = t_dir / "eval.json"

        s_p.write_text(source_xml, encoding="utf-8")
        d_p.write_text(trans_xml, encoding="utf-8")
        b_p.write_text(json.dumps(book_record, ensure_ascii=False, indent=2), encoding="utf-8")

        return run_evaluation(
            source_path=str(s_p),
            draft_path=str(d_p),
            book_record_path=str(b_p),
            output_path=str(o_p),
            force_mock=False,
            engine=cfg.evaluation_engine,
        )


def default_compile_book(root: Path, slug: str) -> List[str]:
    """Compile configured release_formats for a completed book into gitignored release_assets/."""
    from scripts.collab_config import load_collab_config
    from scripts.compile_book import compile_book_formats

    cfg = load_collab_config()
    run_dir = Path(root).resolve() / "runs" / slug
    out_dir = run_dir / "release_assets"
    out_dir.mkdir(parents=True, exist_ok=True)
    res = compile_book_formats(
        run_dir=run_dir,
        output_dir=out_dir,
        formats=cfg.release_formats,
        allow_incomplete=False,
    )
    return [str(p) for p in res.values()]


def default_translate_chapter(source_xml: str, rec: Dict[str, Any], man: Dict[str, Any]) -> str:
    """Translate a chapter XML payload using scripts/translate_chapter.py."""
    from scripts.translate_chapter import translate_chapter

    with tempfile.TemporaryDirectory() as td:
        t_dir = Path(td)
        s_p = t_dir / "source.txt"
        b_p = t_dir / "book_record.json"
        out_p = t_dir / "translation.xml"
        s_p.write_text(source_xml, encoding="utf-8")
        b_p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        source_lang = str(man.get("source_lang", "auto"))
        translate_chapter(
            run_dir=t_dir,
            chapter="ch01",
            source=s_p,
            output=out_p,
            book_record=b_p,
            source_lang=source_lang,
        )
        return out_p.read_text(encoding="utf-8") if out_p.exists() else ""


def default_seed_book(
    root: Path,
    slug: str,
    translate_fn: Optional[Callable] = None,
    evaluate_fn: Optional[Callable] = None,
    *,
    source_injection_cleared: bool = False,
) -> Dict[str, Any]:
    """Seed a merged book using the authoritative collab_seed pipeline."""
    from scripts.collab_config import load_collab_config
    from scripts.collab_seed import seed_merged_book

    cfg = load_collab_config()

    if translate_fn is None:
        translate_fn = default_translate_chapter

    if evaluate_fn is None:
        def real_evaluate_fn(source_xml: str, trans_xml: str, rec: Dict[str, Any]) -> Dict[str, Any]:
            from scripts.evaluate import run_evaluation

            with tempfile.TemporaryDirectory() as td:
                t_dir = Path(td)
                s_p = t_dir / "source.txt"
                d_p = t_dir / "translation.xml"
                b_p = t_dir / "book_record.json"
                o_p = t_dir / "eval.json"
                s_p.write_text(source_xml, encoding="utf-8")
                d_p.write_text(trans_xml, encoding="utf-8")
                b_p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
                return run_evaluation(
                    source_path=str(s_p),
                    draft_path=str(d_p),
                    book_record_path=str(b_p),
                    output_path=str(o_p),
                    force_mock=False,
                    engine=cfg.evaluation_engine,
                )

        evaluate_fn = real_evaluate_fn

    return seed_merged_book(
        repo_root=Path(root).resolve(),
        book_slug=slug,
        repo_full_name=cfg.repo_full_name,
        gh_user=cfg.bot_login,
        bot_git_name=cfg.bot_git_name,
        bot_git_email=cfg.bot_git_email,
        translate_fn=translate_fn,
        evaluate_fn=evaluate_fn,
        source_injection_cleared=source_injection_cleared,
    )


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint for running an autonomous collaboration sweep."""
    parser = argparse.ArgumentParser(description="Run autonomous collaboration sweep on GitHub PRs.")
    parser.add_argument("--config", default=None, help="Path to config.json")
    parser.add_argument("--repo-root", default=None, help="Root of git repository")
    parser.add_argument("--window-hours", type=float, default=None, help="Candidate collection window in hours")
    parser.add_argument("--fallback-hours", type=float, default=None, help="Fallback window in hours")
    parser.add_argument("--bot-login", default=None, help="Bot GitHub username")
    parser.add_argument("--human-approver", default=None, help="Human approver username")
    parser.add_argument("--lock-path", default=None, help="Path to lock file")
    parser.add_argument("--offline", action="store_true", help="Run in offline mode without live LLM evaluation")
    parser.add_argument(
        "--recover-runs",
        action="store_true",
        help="Restore dirty tracked files under runs/** to HEAD before sweeping",
    )
    args = parser.parse_args(argv)

    from scripts.collab_config import ConfigError, load_collab_config

    try:
        cfg = load_collab_config(args.config)
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1

    repo_root = Path(args.repo_root or cfg.workspace_dir).resolve()
    eval_fn = None if args.offline else live_evaluate_pr
    fb_fn = None if args.offline else default_translate_chapter
    lock_path = Path(args.lock_path).resolve() if args.lock_path else None

    try:
        result = run_sweep(
            repo_root=repo_root,
            evaluate_fn=eval_fn,
            compile_fn=default_compile_book,
            seed_fn=default_seed_book,
            fallback_translate_fn=fb_fn,
            window_hours=args.window_hours if args.window_hours is not None else cfg.window_hours,
            fallback_hours=args.fallback_hours if args.fallback_hours is not None else cfg.fallback_hours,
            max_revise_rounds=cfg.max_revise_rounds,
            bot_login=args.bot_login or cfg.bot_login,
            human_approver=args.human_approver or cfg.human_approver,
            release_formats=cfg.release_formats,
            max_source_bytes=cfg.max_source_bytes,
            allowed_licenses=cfg.allowed_licenses,
            supported_pairs=cfg.supported_pairs,
            lock_path=lock_path,
            recover_runs=args.recover_runs,
        )
    except RuntimeError as exc:
        logger.error("Sweep synchronization error: %s", exc)
        print(json.dumps({"status": "sync_error", "error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
