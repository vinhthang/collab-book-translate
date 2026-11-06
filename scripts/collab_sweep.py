"""Idempotent local main-agent sweep orchestrator for collaborative book translation.

Governing specifications: REQ-COLLAB-015, R2-P2-5, R2-P2-6, R2-P2-7.
"""

import contextlib
import datetime
import fcntl
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from scripts.collab_completeness import verify_book_completeness
from scripts.collab_intake import format_prescreen_comment, validate_submission_pr
from scripts.collab_ledger import append_ledger_entry
from scripts.collab_pr_validate import parse_pr_body, validate_review, validate_translation_pr
from scripts.collab_select import (
    check_starved_chapter,
    format_non_selected_comment,
    select_winning_pr,
)
from scripts.collab_state import reconcile_status_labels
from scripts.collab_verdict import compute_collab_verdict
from scripts.helper import atomic_write_json, strip_segments_to_final, update_book_record_from_eval

logger = logging.getLogger(__name__)


def _parse_timestamp(val: Any, default: float) -> float:
    """Parse numeric timestamp or ISO-8601 string to a float epoch timestamp (R2-P0-1)."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        val_str = val.strip()
        try:
            return float(val_str)
        except ValueError:
            pass
        try:
            iso_str = val_str.replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(iso_str)
            return dt.timestamp()
        except Exception:
            pass
    return default


def _default_evaluate_pr(
    pr: Dict[str, Any],
    root: Path,
    source_text: str = "",
    trans_text: str = "",
    book_record: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Default fallback evaluator when evaluate_fn is not provided."""
    return {
        "verdict": "FAIL",
        "weighted_total": 0.0,
        "fallback_unverified": True,
        "issues": [],
    }


def read_pr_file(
    client: Any,
    pr_num: int,
    head_ref: str,
    rel_path: Union[str, Path],
    root: Path,
) -> str:
    """Read a changed file from an unmerged PR branch, pull refspec, or fallback to local repo root."""
    rel_str = str(rel_path).lstrip("/")
    if client is not None and not isinstance(client, GitHubCLIClient):
        if hasattr(client, "get_pr_file"):
            try:
                content = client.get_pr_file(pr_num, rel_str)
                if content is not None:
                    return content
            except Exception:
                pass
        elif hasattr(client, "read_pr_file"):
            try:
                content = client.read_pr_file(pr_num, head_ref, rel_str, root)
                if content is not None:
                    return content
            except Exception:
                pass

    fetched = False
    if pr_num:
        try:
            subprocess.run(
                ["git", "fetch", "origin", f"pull/{pr_num}/head"],
                cwd=str(root),
                capture_output=True,
                check=True,
            )
            fetched = True
        except Exception:
            pass

    if not fetched and head_ref:
        try:
            subprocess.run(
                ["git", "fetch", "origin", head_ref],
                cwd=str(root),
                capture_output=True,
                check=True,
            )
            fetched = True
        except Exception:
            pass

    if fetched:
        try:
            res = subprocess.run(
                ["git", "show", f"FETCH_HEAD:{rel_str}"],
                cwd=str(root),
                capture_output=True,
                text=True,
                check=True,
            )
            return res.stdout
        except Exception:
            pass

    local_p = root / rel_str
    if local_p.is_file():
        return local_p.read_text(encoding="utf-8")
    return ""


def read_pr_file_bytes(
    client: Any,
    pr_num: int,
    head_ref: str,
    rel_path: Union[str, Path],
    root: Path,
) -> bytes:
    """Read a changed file as bytes from an unmerged PR branch, pull refspec, or local repo root."""
    rel_str = str(rel_path).lstrip("/")
    if client is not None and not isinstance(client, GitHubCLIClient):
        if hasattr(client, "get_pr_file_bytes"):
            try:
                content = client.get_pr_file_bytes(pr_num, rel_str)
                if content is not None:
                    return content if isinstance(content, bytes) else str(content).encode("utf-8")
            except Exception:
                pass
        elif hasattr(client, "get_pr_file"):
            try:
                content = client.get_pr_file(pr_num, rel_str)
                if content is not None:
                    return content if isinstance(content, bytes) else str(content).encode("utf-8")
            except Exception:
                pass
        elif hasattr(client, "read_pr_file"):
            try:
                content = client.read_pr_file(pr_num, head_ref, rel_str, root)
                if content is not None:
                    return content if isinstance(content, bytes) else str(content).encode("utf-8")
            except Exception:
                pass

    fetched = False
    if pr_num:
        try:
            subprocess.run(
                ["git", "fetch", "origin", f"pull/{pr_num}/head"],
                cwd=str(root),
                capture_output=True,
                check=True,
            )
            fetched = True
        except Exception:
            pass

    if not fetched and head_ref:
        try:
            subprocess.run(
                ["git", "fetch", "origin", head_ref],
                cwd=str(root),
                capture_output=True,
                check=True,
            )
            fetched = True
        except Exception:
            pass

    if fetched:
        try:
            res = subprocess.run(
                ["git", "show", f"FETCH_HEAD:{rel_str}"],
                cwd=str(root),
                capture_output=True,
                check=True,
            )
            return res.stdout if isinstance(res.stdout, bytes) else res.stdout.encode("utf-8")
        except Exception:
            pass

    local_p = root / rel_str
    if local_p.is_file():
        return local_p.read_bytes()
    return b""


def _normalize_comment_author(c: Dict[str, Any]) -> str:
    auth = c.get("author_login") or (c.get("author") or {}).get("login", "")
    c["author_login"] = auth
    return auth


def commit_and_push(root: Path, paths: List[Union[Path, str]], message: str, client: Any = None) -> None:
    """Helper to stage files, commit, and push to origin if inside a git repository."""
    if client is not None and hasattr(client, "commit_and_push"):
        try:
            client.commit_and_push(paths, message)
            return
        except Exception:
            pass
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
        if res.returncode == 0 and res.stdout.strip() == "true":
            for p in paths:
                subprocess.run(["git", "add", str(p)], cwd=str(root), check=True)
            subprocess.run(["git", "commit", "-m", message], cwd=str(root), check=True)
            subprocess.run(["git", "push", "origin", "main"], cwd=str(root), check=True)
    except Exception as e:
        logger.warning("commit_and_push failed: %s", e)


def _extract_ch_id_and_slug(
    head_ref: str, chunk_id: Optional[str] = None
) -> Tuple[Optional[str], Optional[str]]:
    r"""Extract canonical ch_id and book slug from head_ref and chunk_id (anchored on -(ch\d+)-)."""
    slug: Optional[str] = None
    ch_id: Optional[str] = None
    if head_ref.startswith("translate/"):
        ref_rest = head_ref[len("translate/"):]
        m = re.search(r"-(ch\d+)-", ref_rest)
        if m:
            ch_id = m.group(1)
            slug = ref_rest[:m.start()]
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
            ch_id = chunk_id[len(f"{slug}-"):]
        elif not ch_id:
            ch_id = chunk_id

    return ch_id, slug


def _extract_issue_metadata(issue: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Extract canonical ch_id and book slug from tracking issue."""
    title = issue.get("title", "")
    body = issue.get("body", "")
    labels = issue.get("labels", [])

    ch_id: Optional[str] = None
    slug: Optional[str] = None

    for l in labels:
        if l.startswith("book:"):
            slug = l.split("book:")[1].strip()

    if not slug and "- **Book:**" in body:
        m = re.search(r"-\s*\*\*Book:\*\*\s*([^\s\n\r]+)", body)
        if m:
            slug = m.group(1).strip()

    if "[" in title and "]" in title:
        ch_id = title.split("[")[1].split("]")[0].strip()

    if not ch_id:
        m = re.search(r"\b(ch\d+)\b", title)
        if m:
            ch_id = m.group(1)

    if not slug:
        m = re.search(r"Translate\s+(?:\[[^\]]+\]\s+)?([a-zA-Z0-9_\-]+)-(ch\d+)", title)
        if m:
            slug = m.group(1)
            if not ch_id:
                ch_id = m.group(2)

    return ch_id, slug


@contextlib.contextmanager
def non_blocking_sweep_lock(lock_path: Union[Path, str]):
    """Non-blocking file lock context manager.

    Yields True if lock acquired, False if already held (R2-P2-5).
    """
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
            except OSError:
                pass
        try:
            os.close(fd)
        except OSError:
            pass


class GitHubCLIClient:
    """Production GitHub client using the official gh CLI."""

    def __init__(self, bot_login: str = "1quachuoi9vang", repo_root: Optional[Union[Path, str]] = None):
        self.bot_login = bot_login
        self.root = Path(repo_root) if repo_root is not None else Path.cwd()

    def _run_gh(self, args: List[str]) -> str:
        cmd = ["gh"] + args
        env = os.environ.copy()
        res = subprocess.run(cmd, env=env, cwd=str(self.root), capture_output=True, text=True, check=True)
        return res.stdout

    def commit_and_push(self, paths: List[Union[Path, str]], message: str) -> None:
        commit_and_push(self.root, paths, message)

    def list_open_prs(self) -> List[Dict[str, Any]]:
        fields = "number,title,body,headRefName,headRefOid,author,labels,files,reviews,comments"
        out = self._run_gh(["pr", "list", "--state", "open", "--json", fields])
        prs = json.loads(out)
        for pr in prs:
            pr["head_ref"] = pr.get("headRefName", "")
            pr["head_oid"] = pr.get("head_oid") or pr.get("headRefOid", "")
            pr["author_login"] = pr.get("author_login") or (pr.get("author") or {}).get("login", "")
            pr["labels"] = [l.get("name", "") if isinstance(l, dict) else str(l) for l in pr.get("labels", [])]
            pr["changed_files"] = [f.get("path", "") if isinstance(f, dict) else str(f) for f in pr.get("files", [])]
            for c in pr.get("comments", []):
                _normalize_comment_author(c)
            reviews = pr.get("reviews", [])
            for r in reviews:
                r["author_login"] = r.get("author_login") or (r.get("author") or {}).get("login", "")
                if "line_comment_count" not in r:
                    line_count = 0
                    num = pr.get("number")
                    if num:
                        try:
                            repo_out = self._run_gh(["repo", "view", "--json", "nameWithOwner"])
                            repo_name = json.loads(repo_out).get("nameWithOwner", "")
                            if repo_name:
                                comments_out = self._run_gh(["api", f"repos/{repo_name}/pulls/{num}/comments"])
                                pull_comments = json.loads(comments_out)
                                rev_user = r["author_login"]
                                line_count = sum(1 for c in pull_comments if (c.get("user") or {}).get("login") == rev_user)
                        except Exception:
                            pass
                    if line_count == 0:
                        body = r.get("body", "")
                        for marker in ["<!-- segment:", "<!-- line:", "segment_id", "line_comment", "diff:"]:
                            line_count += body.count(marker)
                    r["line_comment_count"] = line_count
            if "unresolved_threads" not in pr:
                pr["unresolved_threads"] = sum(1 for r in reviews if r.get("state") == "CHANGES_REQUESTED")
        return prs

    def list_open_issues(self) -> List[Dict[str, Any]]:
        fields = "number,title,body,labels,comments,createdAt"
        out = self._run_gh(["issue", "list", "--state", "open", "--json", fields])
        issues = json.loads(out)
        for issue in issues:
            issue["labels"] = [l.get("name", "") if isinstance(l, dict) else str(l) for l in issue.get("labels", [])]
            issue["created_at"] = _parse_timestamp(issue.get("createdAt"), time.time())
            for c in issue.get("comments", []):
                _normalize_comment_author(c)
        return issues

    def comment_pr(self, pr_num: int, body: str):
        self._run_gh(["pr", "comment", str(pr_num), "--body", body])

    def close_pr(self, pr_num: int, comment: Optional[str] = None):
        if comment:
            self.comment_pr(pr_num, comment)
        self._run_gh(["pr", "close", str(pr_num)])

    def merge_pr(self, pr_num: int) -> str:
        self._run_gh(["pr", "merge", str(pr_num), "--merge", "--auto=false"])
        try:
            subprocess.run(["git", "pull", "--ff-only"], cwd=str(self.root), capture_output=True, check=False)
        except Exception:
            pass
        merge_sha = ""
        try:
            out = self._run_gh(["pr", "view", str(pr_num), "--json", "mergeCommit"])
            data = json.loads(out)
            merge_sha = (data.get("mergeCommit") or {}).get("oid", "")
        except Exception:
            pass
        if not merge_sha:
            try:
                res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(self.root), capture_output=True, text=True, check=True)
                merge_sha = res.stdout.strip()
            except Exception:
                pass
        return merge_sha

    def set_pr_labels(self, pr_num: int, add: List[str], remove: List[str]):
        if not add and not remove:
            return
        args = ["pr", "edit", str(pr_num)]
        if add:
            args.extend(["--add-label", ",".join(add)])
        if remove:
            args.extend(["--remove-label", ",".join(remove)])
        self._run_gh(args)

    def comment_issue(self, issue_num: int, body: str):
        self._run_gh(["issue", "comment", str(issue_num), "--body", body])

    def close_issue(self, issue_num: int):
        self._run_gh(["issue", "close", str(issue_num)])

    def set_issue_labels(self, issue_num: int, add: List[str], remove: List[str]):
        if not add and not remove:
            return
        args = ["issue", "edit", str(issue_num)]
        if add:
            args.extend(["--add-label", ",".join(add)])
        if remove:
            args.extend(["--remove-label", ",".join(remove)])
        self._run_gh(args)

    def create_release(self, tag: str, title: str, notes: str, assets: List[str]):
        args = ["release", "create", tag, "--title", title, "--notes", notes]
        for a in assets:
            args.append(str(a))
        self._run_gh(args)


def run_sweep(
    repo_root: Union[Path, str],
    *,
    client: Optional[Any] = None,
    evaluate_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    seed_fn: Optional[Callable[[Path, str], None]] = None,
    compile_fn: Optional[Callable[[Path, str], List[str]]] = None,
    now: Optional[float] = None,
    window_hours: float = 24.0,
    fallback_hours: float = 48.0,
    bot_login: str = "1quachuoi9vang",
    human_approver: str = "vinhthang",
    lock_path: Optional[Union[Path, str]] = None,
) -> Dict[str, Any]:
    """Execute an idempotent sweep across open book submissions, translation PRs, and chapter issues.

    Advances each item until blocked on external input (R2-P2-6).
    """
    root = Path(repo_root)
    if lock_path is None:
        lock_path = root / ".sweep.lock"

    with non_blocking_sweep_lock(lock_path) as acquired:
        if not acquired:
            logger.info("sweep already running")
            return {
                "status": "skipped_locked",
                "reason": "sweep already running",
            }

        cur_now = now if now is not None else time.time()
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

        # ---------------------------------------------------------
        # (a) Process open book submission PRs (head_ref starts with 'book/')
        # ---------------------------------------------------------
        for pr in prs:
            head_ref = pr.get("head_ref") or pr.get("headRefName", "")
            if not head_ref.startswith("book/"):
                continue

            changed_files = pr.get("changed_files", [])
            manifest_data: Optional[Dict[str, Any]] = None
            source_file_path: Optional[str] = None
            source_content: Optional[str] = None
            temp_files_to_cleanup: List[Path] = []

            for f in changed_files:
                if f.endswith("manifest.json"):
                    manifest_raw = read_pr_file(gh, pr["number"], head_ref, f, root)
                    if manifest_raw:
                        try:
                            manifest_data = json.loads(manifest_raw)
                        except Exception:
                            pass
                elif "source" in f:
                    source_bytes = read_pr_file_bytes(gh, pr["number"], head_ref, f, root)
                    local_f = root / f
                    if local_f.is_file():
                        source_file_path = str(local_f)
                    elif source_bytes:
                        import tempfile
                        tmp = tempfile.NamedTemporaryFile("wb", delete=False)
                        tmp.write(source_bytes)
                        tmp.flush()
                        tmp.close()
                        source_file_path = tmp.name
                        temp_files_to_cleanup.append(Path(tmp.name))

                    if any(f.lower().endswith(ext) for ext in (".md", ".txt", ".xml", ".html", ".htm")):
                        source_content = source_bytes.decode("utf-8", errors="ignore")
                    else:
                        source_content = source_bytes[:4000].decode("utf-8", errors="ignore")

            source_text_sample = source_content[:4000] if source_content else None

            try:
                val_res = validate_submission_pr(
                    changed_files=changed_files,
                    manifest_data=manifest_data,
                    source_file_path=source_file_path,
                    source_text_sample=source_text_sample,
                )
            finally:
                for tmp_p in temp_files_to_cleanup:
                    try:
                        tmp_p.unlink(missing_ok=True)
                    except Exception:
                        pass

            # Post pre-screen comment if not already posted
            comments = pr.get("comments", [])
            has_prescreen = any("Pre-screen Summary:" in c.get("body", "") for c in comments)
            if not has_prescreen:
                comment_body = format_prescreen_comment(val_res, manifest_data or {})
                gh.comment_pr(pr["number"], comment_body)

            if val_res.get("valid"):
                # Check for approval by human_approver
                reviews = pr.get("reviews", [])
                is_approved = any(
                    r.get("author_login") == human_approver and r.get("state") == "APPROVED"
                    for r in reviews
                )
                if is_approved:
                    gh.merge_pr(pr["number"])
                    slug = val_res.get("slug") or (manifest_data.get("slug") if manifest_data else "")
                    if slug and seed_fn:
                        seed_fn(root, slug)
                    sweep_result["processed_submissions"].append({
                        "pr": pr["number"],
                        "slug": slug,
                        "action": "merged_and_seeded",
                    })

        # ---------------------------------------------------------
        # (b) Process open translation PRs (head_ref starts with 'translate/')
        # ---------------------------------------------------------
        for pr in prs:
            head_ref = pr.get("head_ref") or pr.get("headRefName", "")
            if not head_ref.startswith("translate/"):
                continue

            current_labels = pr.get("labels", [])
            changed_files = pr.get("changed_files", [])
            parsed_body = parse_pr_body(pr.get("body", ""))
            raw_chunk = parsed_body.get("chunk_id") or parsed_body.get("chunk")

            # Candidate PRs have already passed deterministic checks and review evaluation;
            # they are handled in section (c) for winner selection.
            # If both pr.get('head_oid') and recorded sha are non-empty and differ, do not treat as candidate (re-evaluate) (R3-P1-2).
            if "status:candidate" in current_labels:
                rec_sha = None
                current_head_oid = (pr.get("head_oid") or pr.get("headRefOid") or "").strip()
                for c in pr.get("comments", []):
                    c_auth = _normalize_comment_author(c)
                    if c_auth and c_auth != bot_login:
                        continue
                    c_body = c.get("body", "")
                    m_s = re.search(r"<!--\s*candidate_score:.*?sha:\s*([^\s>]+)", c_body)
                    if m_s:
                        rec_sha = m_s.group(1).strip()
                        break
                if current_head_oid and rec_sha and current_head_oid != rec_sha:
                    pass
                else:
                    continue

            # Idle sweep skip: if already labeled status:revise and last comment is bot diagnostic
            if "status:revise" in current_labels:
                comments = pr.get("comments", [])
                if comments:
                    revise_count = sum(
                        1 for c in comments
                        if "Deterministic checks failed" in c.get("body", "")
                        or "Evaluation outcome REVISE" in c.get("body", "")
                        or "REVISE" in c.get("body", "")
                    )
                    last_c = comments[-1]
                    last_body = last_c.get("body", "")
                    last_auth = _normalize_comment_author(last_c)
                    is_bot_author = not last_auth or last_auth == bot_login
                    is_bot_revise = (
                        "Deterministic checks failed" in last_body
                        or "Evaluation outcome REVISE" in last_body
                        or "REVISE" in last_body
                    )
                    current_head_oid = (pr.get("head_oid") or pr.get("headRefOid") or "").strip()
                    sha_matches = not current_head_oid or f"revise_sha: {current_head_oid}" in last_body
                    if is_bot_author and is_bot_revise and sha_matches and revise_count < 3:
                        continue

            ch_id, slug = _extract_ch_id_and_slug(head_ref, raw_chunk)
            chunk_id = ch_id

            source_text = ""
            trans_text = ""
            expected_glossary_sha: Optional[str] = None
            book_record: Optional[Dict[str, Any]] = None

            if slug and ch_id:
                s_rel = f"runs/{slug}/{ch_id}/source.txt"
                source_text = read_pr_file(gh, pr["number"], head_ref, s_rel, root)
                if not source_text:
                    s_file = root / "runs" / slug / ch_id / "source.txt"
                    if s_file.is_file():
                        source_text = s_file.read_text(encoding="utf-8")

                t_rel = f"runs/{slug}/{ch_id}/translation.xml"
                trans_text = read_pr_file(gh, pr["number"], head_ref, t_rel, root)
                if not trans_text:
                    t_file = root / "runs" / slug / ch_id / "translation.xml"
                    if t_file.is_file():
                        trans_text = t_file.read_text(encoding="utf-8")

                br_file = root / "runs" / slug / "book_record.json"
                if br_file.is_file():
                    try:
                        book_record = json.loads(br_file.read_text(encoding="utf-8"))
                        expected_glossary_sha = book_record.get("glossary_sha") or book_record.get("expected_glossary_sha")
                    except Exception:
                        pass

            val_pr = validate_translation_pr(
                head_ref=head_ref,
                author_login=pr.get("author_login", ""),
                body=pr.get("body", ""),
                changed_files=changed_files,
                source_xml_text=source_text,
                translation_xml_text=trans_text,
                expected_glossary_sha=expected_glossary_sha,
            )

            # If deterministic checks fail -> report as PR comment and label status:revise/reassign without LLM eval
            if not val_pr.get("valid"):
                comments = pr.get("comments", [])
                revise_count = sum(
                    1 for c in comments
                    if "Deterministic checks failed" in c.get("body", "") or "REVISE" in c.get("body", "")
                )
                if revise_count >= 3:
                    recon = reconcile_status_labels(current_labels, "status:reassign")
                    gh.set_pr_labels(pr["number"], recon["to_add"], recon["to_remove"])
                    gh.close_pr(pr["number"], comment="Revision limit (3 rounds) reached. Closing PR as reassign.")
                else:
                    head_oid = (pr.get("head_oid") or pr.get("headRefOid") or "").strip()
                    recon = reconcile_status_labels(current_labels, "status:revise")
                    gh.set_pr_labels(pr["number"], recon["to_add"], recon["to_remove"])
                    defect_msg = (
                        f"<!-- revise_sha: {head_oid} -->\n"
                        "Deterministic checks failed:\n"
                        + "\n".join(f"- {d}" for d in val_pr.get("defects", []))
                    )
                    gh.comment_pr(pr["number"], defect_msg)
                continue

            # Deterministic checks passed -> ensure status:needs-review if not candidate
            if "status:candidate" not in current_labels:
                recon = reconcile_status_labels(current_labels, "status:needs-review")
                if recon["to_add"] or recon["to_remove"]:
                    gh.set_pr_labels(pr["number"], recon["to_add"], recon["to_remove"])
                    current_labels = recon["final_labels"]

            # Validate reviews
            reviews = pr.get("reviews", [])
            valid_reviews: List[Dict[str, Any]] = []
            for r in reviews:
                r_author = r.get("author_login") or (r.get("author") or {}).get("login", "")
                v_rev = validate_review(
                    pr_author_login=pr.get("author_login", ""),
                    review_author_login=r_author,
                    review_body=r.get("body", ""),
                    line_comment_count=r.get("line_comment_count", 0),
                )
                if v_rev.get("valid"):
                    v_rev["author_login"] = r_author
                    if "scores" in r and "scores" not in v_rev:
                        v_rev["scores"] = r["scores"]
                    valid_reviews.append(v_rev)

            # When >= 1 valid review exists -> evaluate and compute verdict
            if len(valid_reviews) >= 1:
                if evaluate_fn is not None:
                    eval_res = evaluate_fn(pr, root)
                else:
                    eval_res = _default_evaluate_pr(pr, root, source_text, trans_text, book_record)

                comments = pr.get("comments", [])
                revise_count = sum(
                    1 for c in comments
                    if "Deterministic checks failed" in c.get("body", "") or "REVISE" in c.get("body", "")
                )

                verdict_res = compute_collab_verdict(
                    eval_result=eval_res,
                    reviewer_scores=valid_reviews[0].get("scores"),
                    book_record=book_record,
                    revise_round_count=revise_count,
                )
                outcome = verdict_res.get("outcome")

                if outcome == "CANDIDATE":
                    w_tot = float(verdict_res.get("weighted_total") or eval_res.get("weighted_total", 4.0))
                    crit_count = sum(1 for iss in eval_res.get("issues", []) if iss.get("severity") == "critical")
                    maj_count = sum(1 for iss in eval_res.get("issues", []) if iss.get("severity") == "major")
                    pr["weighted_total"] = w_tot
                    pr["critical_count"] = crit_count
                    pr["major_count"] = maj_count
                    pr["eval_res"] = eval_res
                    pr["reviewer_scores"] = valid_reviews[0].get("scores", {})
                    pr["valid_reviews"] = valid_reviews

                    recon = reconcile_status_labels(current_labels, "status:candidate")
                    gh.set_pr_labels(pr["number"], recon["to_add"], recon["to_remove"])

                    # Post candidate score structured marker in comment for cross-sweep recovery
                    head_oid = (pr.get("head_oid") or pr.get("headRefOid") or "").strip()
                    gh.comment_pr(
                        pr["number"],
                        f"<!-- candidate_score: {w_tot}, critical: {crit_count}, major: {maj_count}, sha: {head_oid} -->\n"
                        f"PR accepted as candidate with score {w_tot}."
                    )

                    # Transition chapter issue to status:candidates and record window clock (R2-P2-7)
                    target_issue: Optional[Dict[str, Any]] = None
                    for iss in issues:
                        iss_ch, iss_slug = _extract_issue_metadata(iss)
                        iss_title = iss.get("title", "")
                        iss_body = iss.get("body", "")

                        if parsed_body.get("issue"):
                            issue_nums = re.findall(r"#?(\d+)\b", str(parsed_body.get("issue")))
                            if str(iss.get("number")) in issue_nums:
                                target_issue = iss
                                break
                        if ch_id and (f"[{ch_id}]" in iss_title or f" {ch_id} " in iss_title or iss_ch == ch_id):
                            if not slug or not iss_slug or slug == iss_slug:
                                target_issue = iss
                                break
                        if raw_chunk and (raw_chunk in iss_title or raw_chunk in iss_body):
                            target_issue = iss
                            break

                    if target_issue:
                        recon_iss = reconcile_status_labels(target_issue.get("labels", []), "status:candidates")
                        gh.set_issue_labels(target_issue["number"], recon_iss["to_add"], recon_iss["to_remove"])

                        has_window = any("candidate_window_started_at" in c.get("body", "") for c in target_issue.get("comments", []))
                        if not has_window:
                            gh.comment_issue(
                                target_issue["number"],
                                f"<!-- candidate_window_started_at: {cur_now} -->\n"
                                f"Candidate window started at {cur_now} for chapter {ch_id} (PR #{pr['number']})."
                            )

                elif outcome == "REVISE":
                    head_oid = (pr.get("head_oid") or pr.get("headRefOid") or "").strip()
                    recon = reconcile_status_labels(current_labels, "status:revise")
                    gh.set_pr_labels(pr["number"], recon["to_add"], recon["to_remove"])
                    gh.comment_pr(
                        pr["number"],
                        f"<!-- revise_sha: {head_oid} -->\n"
                        f"Evaluation outcome REVISE: {verdict_res.get('reason')}"
                    )

                elif outcome == "REASSIGN":
                    recon = reconcile_status_labels(current_labels, "status:reassign")
                    gh.set_pr_labels(pr["number"], recon["to_add"], recon["to_remove"])
                    gh.close_pr(pr["number"], comment=f"Evaluation outcome REASSIGN: {verdict_res.get('reason')}")

                elif outcome == "FLAGGED_FOR_HUMAN":
                    gh.comment_pr(
                        pr["number"],
                        f"Alert @{human_approver}: PR #{pr['number']} has been flagged for human review: {verdict_res.get('reason')}"
                    )

                elif outcome == "BLOCKED_UNVERIFIED":
                    recon = reconcile_status_labels(current_labels, "status:needs-review")
                    gh.set_pr_labels(pr["number"], recon["to_add"], recon["to_remove"])
                    comments = pr.get("comments", [])
                    has_blocked = any("verified live evaluation is required" in c.get("body", "").lower() for c in comments)
                    if not has_blocked:
                        gh.comment_pr(
                            pr["number"],
                            f"Verified live evaluation is required (heuristic fallback is blocked). Keeping PR in status:needs-review: {verdict_res.get('reason')}"
                        )

        # ---------------------------------------------------------
        # (c) Process chapter issues in status:candidates
        # ---------------------------------------------------------
        for issue in issues:
            labels = issue.get("labels", [])
            if "status:candidates" not in labels:
                continue

            ch_id, slug = _extract_issue_metadata(issue)
            title = issue.get("title", "")
            if not ch_id and "[" in title and "]" in title:
                ch_id = title.split("[")[1].split("]")[0].strip()

            # Read candidate_window_started_at from comments
            window_started_at: Optional[float] = None
            for c in issue.get("comments", []):
                c_auth = _normalize_comment_author(c)
                if c_auth and c_auth != bot_login:
                    continue
                body = c.get("body", "")
                if "candidate_window_started_at:" in body:
                    try:
                        part = body.split("candidate_window_started_at:")[1].split("-->")[0].strip()
                        window_started_at = _parse_timestamp(part.split()[0], cur_now)
                        break
                    except Exception:
                        pass

            if window_started_at is None:
                window_started_at = _parse_timestamp(issue.get("created_at"), cur_now)

            # Find candidates for this chapter
            chapter_candidates: List[Dict[str, Any]] = []
            for pr in prs:
                pr_labels = pr.get("labels", [])
                if "status:candidate" in pr_labels:
                    pr_head = pr.get("head_ref") or pr.get("headRefName", "")
                    pr_body = parse_pr_body(pr.get("body", ""))
                    pr_raw_chunk = pr_body.get("chunk_id") or pr_body.get("chunk")
                    pr_ch, pr_slug = _extract_ch_id_and_slug(pr_head, pr_raw_chunk)

                    matches = False
                    if ch_id and pr_ch == ch_id:
                        if not slug or not pr_slug or slug == pr_slug:
                            matches = True
                    elif pr_raw_chunk and ch_id and (pr_raw_chunk == ch_id or pr_raw_chunk.endswith(f"-{ch_id}")):
                        matches = True

                    if matches:
                        w_tot = pr.get("weighted_total")
                        crit_cnt = pr.get("critical_count")
                        maj_cnt = pr.get("major_count")
                        current_head_oid = (pr.get("head_oid") or pr.get("headRefOid") or "").strip()
                        if w_tot is None:
                            for c in pr.get("comments", []):
                                c_auth = _normalize_comment_author(c)
                                if c_auth and c_auth != bot_login:
                                    continue
                                c_body = c.get("body", "")
                                m_score = re.search(
                                    r"<!--\s*candidate_score:\s*(\d+(?:\.\d+)?),\s*critical:\s*(\d+),\s*major:\s*(\d+)(?:,\s*sha:\s*([^\s>]+))?\s*-->",
                                    c_body,
                                )
                                if m_score:
                                    rec_sha = m_score.group(4)
                                    if current_head_oid and rec_sha and current_head_oid != rec_sha:
                                        w_tot = None
                                        break
                                    w_tot = float(m_score.group(1))
                                    crit_cnt = int(m_score.group(2))
                                    maj_cnt = int(m_score.group(3))
                                    break

                        # Skip candidates without verified score
                        if w_tot is None:
                            continue

                        cand_dict = {
                            "pr_number": pr["number"],
                            "weighted_total": float(w_tot),
                            "critical_count": int(crit_cnt or 0),
                            "major_count": int(maj_cnt or 0),
                            "author_login": pr.get("author_login", ""),
                            "unresolved_threads": int(pr.get("unresolved_threads", 0)),
                            "eval_res": pr.get("eval_res"),
                            "reviewer_scores": pr.get("reviewer_scores"),
                            "valid_reviews": pr.get("valid_reviews", []),
                            "reviews": pr.get("reviews", []),
                            "pr_dict": pr,
                        }
                        chapter_candidates.append(cand_dict)

            sel_res = select_winning_pr(
                chapter_candidates,
                window_started_at=window_started_at,
                now=cur_now,
                window_hours=window_hours,
            )

            if sel_res.get("window_closed") and sel_res.get("winner"):
                winner = sel_res["winner"]
                winner_pr = winner.get("pr_dict") or next((p for p in prs if p["number"] == winner["pr_number"]), {})

                if slug and ch_id:
                    run_dir = root / "runs" / slug
                    ch_dir = run_dir / ch_id
                    br_file = run_dir / "book_record.json"
                    additions_file = ch_dir / "analyze_additions.json"

                    # Extract reviewer_logins from verified reviews
                    # Re-validate winner_pr.get('reviews', []) via validate_review in section (c)
                    # when valid_revs is empty so recovered candidates only record verified reviewer_logins and authentic rubric scores (R3-P1-8).
                    valid_revs = winner.get("valid_reviews") or []
                    if not valid_revs:
                        for r in winner_pr.get("reviews", []):
                            r_auth = r.get("author_login") or (r.get("author") or {}).get("login", "")
                            v_rev = validate_review(
                                pr_author_login=winner.get("author_login", ""),
                                review_author_login=r_auth,
                                review_body=r.get("body", ""),
                                line_comment_count=r.get("line_comment_count", 0),
                            )
                            if v_rev.get("valid"):
                                v_rev["author_login"] = r_auth
                                if "scores" in r and "scores" not in v_rev:
                                    v_rev["scores"] = r["scores"]
                                valid_revs.append(v_rev)
                        winner["valid_reviews"] = valid_revs

                    reviewer_logins = [
                        r.get("author_login") for r in valid_revs
                        if r.get("author_login") and r.get("author_login") != winner.get("author_login")
                    ]
                    if not reviewer_logins:
                        for r in winner_pr.get("reviews", []):
                            r_auth = r.get("author_login") or (r.get("author") or {}).get("login", "")
                            if r_auth and r_auth != winner.get("author_login") and r_auth not in reviewer_logins:
                                reviewer_logins.append(r_auth)

                    # If reviewer_logins is empty for a non-ch01 chapter, do NOT merge (R2-P1-2)
                    if not reviewer_logins and ch_id != "ch01":
                        logger.warning("Chapter %s has no verified reviewer logins; blocking merge", ch_id)
                        continue

                    if not additions_file.is_file():
                        branch_additions = read_pr_file(
                            gh, winner["pr_number"], winner_pr.get("head_ref", ""),
                            f"runs/{slug}/{ch_id}/analyze_additions.json", root
                        )
                        if branch_additions:
                            ch_dir.mkdir(parents=True, exist_ok=True)
                            additions_file.write_text(branch_additions, encoding="utf-8")

                    has_conflict = False
                    if additions_file.is_file() and br_file.is_file():
                        try:
                            additions_raw = json.loads(additions_file.read_text(encoding="utf-8"))
                            if isinstance(additions_raw, list):
                                proposals = additions_raw
                                prop_container = "list"
                            elif isinstance(additions_raw, dict):
                                if "proposals" in additions_raw:
                                    proposals = additions_raw.get("proposals", [])
                                    prop_container = "dict"
                                elif "book_record_additions" in additions_raw:
                                    proposals = additions_raw.get("book_record_additions", {}).get("terms", [])
                                    prop_container = "book_record_additions"
                                else:
                                    proposals = []
                                    prop_container = "none"
                            else:
                                proposals = []
                                prop_container = "none"

                            br_data = json.loads(br_file.read_text(encoding="utf-8"))
                            glossary = br_data.get("glossary", {})
                            terms = br_data.get("terms", {})

                            existing_map: Dict[str, str] = {}
                            if isinstance(glossary, dict):
                                for k, v in glossary.items():
                                    if isinstance(v, str):
                                        existing_map[k.strip().lower()] = v.strip()
                                    elif isinstance(v, dict):
                                        existing_map[k.strip().lower()] = (v.get("target") or v.get("target_vi") or v.get("rendering") or "").strip()
                            if isinstance(terms, dict):
                                for k, v in terms.items():
                                    if isinstance(v, str):
                                        existing_map[k.strip().lower()] = v.strip()
                                    elif isinstance(v, dict):
                                        existing_map[k.strip().lower()] = (v.get("target") or v.get("target_vi") or v.get("rendering") or "").strip()
                            elif isinstance(terms, list):
                                for t in terms:
                                    if isinstance(t, dict):
                                        s_k = t.get("source") or t.get("term") or t.get("src")
                                        t_k = t.get("target") or t.get("target_vi") or t.get("rendering")
                                        if s_k and t_k:
                                            existing_map[str(s_k).strip().lower()] = str(t_k).strip()

                            for prop in proposals:
                                if not isinstance(prop, dict):
                                    continue
                                if prop.get("status", "proposed") == "proposed":
                                    src = prop.get("term") or prop.get("source") or prop.get("source_term")
                                    tgt = prop.get("target") or prop.get("translation") or prop.get("target_vi") or prop.get("rendering")
                                    if src and tgt:
                                        src_key = str(src).strip().lower()
                                        if src_key in existing_map:
                                            if existing_map[src_key].lower() != str(tgt).strip().lower():
                                                has_conflict = True
                                                break

                            if not has_conflict:
                                if not isinstance(glossary, dict):
                                    glossary = {}
                                    br_data["glossary"] = glossary

                                modified_proposals = False
                                for prop in proposals:
                                    if isinstance(prop, dict) and prop.get("status", "proposed") == "proposed":
                                        src = prop.get("term") or prop.get("source") or prop.get("source_term")
                                        tgt = prop.get("target") or prop.get("translation") or prop.get("target_vi") or prop.get("rendering")
                                        if src and tgt:
                                            glossary[str(src).strip()] = str(tgt).strip()
                                            prop["status"] = "bound"
                                            modified_proposals = True

                                atomic_write_json(br_file, br_data)

                                if modified_proposals:
                                    if prop_container == "list":
                                        atomic_write_json(additions_file, proposals)
                                    elif prop_container == "dict":
                                        additions_raw["proposals"] = proposals
                                        atomic_write_json(additions_file, additions_raw)
                                    elif prop_container == "book_record_additions":
                                        if "book_record_additions" not in additions_raw:
                                            additions_raw["book_record_additions"] = {}
                                        additions_raw["book_record_additions"]["terms"] = proposals
                                        atomic_write_json(additions_file, additions_raw)

                                eval_data = winner.get("eval_res")
                                try:
                                    update_book_record_from_eval(
                                        book_record_path=br_file,
                                        chapter_dir=ch_dir,
                                        bind=True,
                                        chapter_id=ch_id,
                                        eval_data=eval_data,
                                    )
                                except TypeError:
                                    update_book_record_from_eval(
                                        book_record_path=br_file,
                                        chapter_dir=ch_dir,
                                        bind=True,
                                        chapter_id=ch_id,
                                    )
                                except Exception:
                                    pass
                        except Exception as e:
                            logger.exception("Error checking/binding analyze_additions")

                    if has_conflict:
                        recon = reconcile_status_labels(winner_pr.get("labels", []), "status:revise")
                        gh.set_pr_labels(winner["pr_number"], recon["to_add"], recon["to_remove"])
                        gh.comment_pr(
                            winner["pr_number"],
                            "Glossary conflict detected during proposal binding. Returning PR to REVISE.",
                        )
                        continue

                    # Merge winning PR
                    merge_ret = gh.merge_pr(winner["pr_number"])
                    merge_sha: Optional[str] = None
                    if isinstance(merge_ret, str) and len(merge_ret) in (40, 64):
                        merge_sha = merge_ret
                    elif isinstance(merge_ret, dict) and "sha" in merge_ret:
                        merge_sha = merge_ret["sha"]
                    if not merge_sha:
                        try:
                            merge_sha = subprocess.check_output(
                                ["git", "rev-parse", "HEAD"], cwd=str(root), text=True
                            ).strip()
                        except Exception:
                            merge_sha = f"merge-commit-{winner['pr_number']}"

                    # Close non-selected PRs
                    for non_sel in sel_res.get("non_selected", []):
                        comment = format_non_selected_comment(non_sel, winner)
                        gh.close_pr(non_sel["pr_number"], comment=comment)

                    # Write final.txt
                    trans_xml_path = ch_dir / "translation.xml"
                    xml_text = read_pr_file(gh, winner["pr_number"], winner_pr.get("head_ref", ""), f"runs/{slug}/{ch_id}/translation.xml", root)
                    if not xml_text and trans_xml_path.is_file():
                        xml_text = trans_xml_path.read_text(encoding="utf-8")
                    if xml_text:
                        final_text = strip_segments_to_final(xml_text)
                        (ch_dir / "final.txt").write_text(final_text, encoding="utf-8")

                    # Update checklist.json (both list and dict formats)
                    cl_file = run_dir / "checklist.json"
                    if cl_file.is_file():
                        try:
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
                                if "chapters" in cl_data and isinstance(cl_data["chapters"], dict):
                                    if ch_id in cl_data["chapters"]:
                                        if isinstance(cl_data["chapters"][ch_id], dict):
                                            cl_data["chapters"][ch_id]["status"] = "done"
                                        else:
                                            cl_data["chapters"][ch_id] = {"status": "done"}
                                    else:
                                        cl_data["chapters"][ch_id] = {"status": "done"}
                                elif ch_id in cl_data:
                                    if isinstance(cl_data[ch_id], dict):
                                        cl_data[ch_id]["status"] = "done"
                                    else:
                                        cl_data[ch_id] = {"status": "done"}
                                atomic_write_json(cl_file, cl_data)
                        except Exception:
                            pass

                    # Append audit ledger entry
                    ledger_file = run_dir / "collab_ledger.json"

                    reviewer_score: float = float(winner.get("weighted_total", 4.0))
                    rev_scores = winner.get("reviewer_scores")
                    if not rev_scores and valid_revs and "scores" in valid_revs[0]:
                        rev_scores = valid_revs[0].get("scores")
                    if rev_scores and isinstance(rev_scores, dict) and len(rev_scores) > 0:
                        reviewer_score = float(sum(rev_scores.values()) / len(rev_scores))

                    real_glossary_sha: Optional[str] = None
                    if br_file.is_file():
                        try:
                            br_dict = json.loads(br_file.read_text(encoding="utf-8"))
                            real_glossary_sha = br_dict.get("glossary_sha") or br_dict.get("expected_glossary_sha")
                        except Exception:
                            pass
                    if not real_glossary_sha:
                        try:
                            real_glossary_sha = subprocess.check_output(
                                ["git", "rev-parse", "HEAD"], cwd=str(root), text=True
                            ).strip()
                        except Exception:
                            real_glossary_sha = "0" * 40

                    ledger_entry = {
                        "chapter": ch_id,
                        "issue_number": issue["number"],
                        "selected_pr": winner["pr_number"],
                        "rejected_prs": [ns["pr_number"] for ns in sel_res.get("non_selected", [])],
                        "translator_login": winner.get("author_login") or "translator",
                        "reviewer_logins": reviewer_logins,
                        "reviewer_score": reviewer_score,
                        "score_of_record": float(winner.get("weighted_total", 4.0)),
                        "verdict": "PASS",
                        "merge_sha": merge_sha,
                        "glossary_sha": real_glossary_sha,
                    }
                    append_ledger_entry(ledger_file, slug, ledger_entry)

                    # Commit and push chapter merge artifacts (R3-P1-8 & R3-P2-5)
                    staged_paths = [
                        ch_dir / "final.txt",
                        run_dir / "checklist.json",
                        br_file,
                        ledger_file,
                    ]
                    commit_and_push(
                        root,
                        staged_paths,
                        f"chore({slug}): merge winning translation for chapter {ch_id} (PR #{winner['pr_number']})",
                        client=gh,
                    )

                    # Mark issue status:done and close it
                    recon_iss = reconcile_status_labels(issue.get("labels", []), "status:done")
                    gh.set_issue_labels(issue["number"], recon_iss["to_add"], recon_iss["to_remove"])
                    gh.close_issue(issue["number"])

                    sweep_result["selected_winners"].append({
                        "chapter": ch_id,
                        "winner_pr": winner["pr_number"],
                        "slug": slug,
                    })

        # ---------------------------------------------------------
        # (d) Check starved chapters in status:open (48h)
        # ---------------------------------------------------------
        for issue in issues:
            labels = issue.get("labels", [])
            if "status:open" in labels:
                issue_created = _parse_timestamp(issue.get("created_at"), cur_now)
                ch_id, _ = _extract_issue_metadata(issue)
                if not ch_id:
                    t = issue.get("title", "")
                    if "[" in t and "]" in t:
                        ch_id = t.split("[")[1].split("]")[0].strip()

                has_candidate = any(
                    "status:candidate" in pr.get("labels", []) and ch_id and f"-{ch_id}-" in pr.get("head_ref", "")
                    for pr in prs
                )

                if check_starved_chapter(issue_created, has_candidate, now=cur_now, fallback_hours=fallback_hours):
                    sweep_result["starved_chapters"].append({
                        "issue": issue["number"],
                        "chapter": ch_id,
                    })

        # ---------------------------------------------------------
        # (e) When all chapters in checklist are done, verify and release
        # ---------------------------------------------------------
        runs_dir = root / "runs"
        if runs_dir.is_dir():
            for slug_dir in runs_dir.iterdir():
                if not slug_dir.is_dir():
                    continue
                slug_name = slug_dir.name
                cl_file = slug_dir / "checklist.json"
                if not cl_file.is_file():
                    continue

                comp_check = verify_book_completeness(slug_dir)
                if comp_check.get("ready_to_publish"):
                    tag = f"{slug_name}-v1.0"
                    ledger_file = slug_dir / "collab_ledger.json"
                    ledger_data: Dict[str, Any] = {}
                    if ledger_file.is_file():
                        try:
                            ledger_data = json.loads(ledger_file.read_text(encoding="utf-8"))
                        except Exception:
                            ledger_data = {}

                    if ledger_data.get("released_tag") == tag:
                        # Already released
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
                    ledger_data["released_tag"] = tag
                    atomic_write_json(ledger_file, ledger_data)
                    sweep_result["releases_created"].append({"slug": slug_name, "tag": tag})

        return sweep_result
