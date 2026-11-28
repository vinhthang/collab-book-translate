#!/usr/bin/env python3
"""
scripts/collab_worker.py — Multi-account, role-aware worker CLI (REQ-035, REQ-036).

Provides 2-account or multi-worker role execution without mutating global `gh auth`:
- Authenticates subprocesses via per-command GH_TOKEN + ephemeral 0700 GIT_ASKPASS
- Subcommands: list-tasks, submit-book, approve-intake, submit-translation, submit-review
"""

import argparse
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.collab_config import CollabConfig, ConfigError, load_collab_config
from scripts.collab_pr_validate import validate_review, validate_translation_pr
import httpx

logger = logging.getLogger(__name__)


def _get_mcp_http_client() -> Any:
    return httpx.Client(timeout=180.0)


def _normalize_mcp_base_url(raw_url: str) -> str:
    clean = raw_url.strip().rstrip("/")
    if clean.endswith("/mcp"):
        clean = clean[:-4]
    return clean


def resolve_worker_mode(gh_user: Optional[str], mcp_url: Optional[str]) -> Dict[str, str]:
    eff_mcp = (mcp_url or os.environ.get("COLLAB_MCP_URL") or "").strip()
    eff_gh = (gh_user or "").strip()
    if eff_mcp and not eff_gh:
        return {"mode": "mcp", "target": _normalize_mcp_base_url(eff_mcp)}
    if eff_gh and not mcp_url:
        return {"mode": "github", "target": eff_gh}
    if mcp_url:
        return {"mode": "mcp", "target": _normalize_mcp_base_url(mcp_url)}
    return {
        "mode": "error",
        "error": "Provide either --mcp-url (or COLLAB_MCP_URL) for remote MCP/REST mode or --gh-user for GitHub PR mode",
    }


def run_mcp_command(
    *,
    subcommand: str,
    mcp_url: str,
    agent_key: str = "",
    role: str = "all",
    book_slug: Optional[str] = None,
    chapter: Optional[str] = None,
    handle: str = "",
    display_name: str = "",
    model_info: str = "",
    slug: str = "",
    source_file: str = "",
    title: str = "",
    author: str = "",
    source_lang: str = "en",
    target_lang: str = "vi",
    license_name: str = "public-domain",
    license_url: str = "",
    genre: str = "fiction",
    parse_file: str = "",
    translation_file: str = "",
    additions_file: Optional[str] = None,
    engine_model: str = "mcp-agent",
    submission_id: Optional[str] = None,
    revision_token: Optional[str] = None,
    rebuttals_file: Optional[str] = None,
    review_file: str = "",
    verdict: str = "COMMENT",
    issues_file: Optional[str] = None,
    status: str = "",
    author_handle: str = "",
    output_file: Optional[str] = None,
    body_md: str = "",
    body_file: str = "",
    severity: str = "P1",
    tool_name: str = "",
    error_code: int = 0,
    bug_id: str = "",
    resolution_note: str = "",
    fixed_in_version: str = "",
    pr_url: str = "",
    issue_url: str = "",
    create_github_issue: bool = False,
    import_file: str = "",
    limit: int = 200,
    offset: int = 0,
    section_type: str = "",
    include_matter: bool = True,
    include_xml: bool = True,
    include_context: bool = True,
    include_workstream: bool = False,
) -> Dict[str, Any]:
    """Execute worker subcommands over HTTP against collab-mcp (ANONYMOUS or AGENT_KEY)."""
    base = _normalize_mcp_base_url(mcp_url)
    headers: Dict[str, str] = {}
    eff_key = (agent_key or os.environ.get("COLLAB_AGENT_KEY") or "").strip()
    if eff_key:
        headers["Authorization"] = f"Bearer {eff_key}"

    client = _get_mcp_http_client()
    close_after = isinstance(client, httpx.Client)
    try:
        if subcommand == "register-agent":
            resp = client.post(
                f"{base}/api/v1/agents/register",
                headers=headers,
                json={"handle": handle, "display_name": display_name, "model_info": model_info},
            )
        elif subcommand == "heartbeat":
            resp = client.post(
                f"{base}/api/v1/agents/heartbeat",
                headers=headers,
                json={"status_note": resolution_note, "client_name": display_name or "collab-worker"},
            )
        elif subcommand == "list-tasks":
            params: Dict[str, str] = {
                "role": role,
                "limit": str(limit),
                "offset": str(offset),
                "include_matter": "true" if include_matter else "false",
            }
            if book_slug:
                params["book_slug"] = book_slug
            if section_type:
                params["section_type"] = section_type
            resp = client.get(f"{base}/api/v1/tasks", headers=headers, params=params)
        elif subcommand == "get-chapter":
            resp = client.get(
                f"{base}/api/v1/books/{book_slug}/chapters/{chapter}",
                headers=headers,
            )
        elif subcommand == "get-book-source":
            resp = client.get(
                f"{base}/api/v1/books/{book_slug}/source",
                headers=headers,
            )
        elif subcommand == "submit-book-parse":
            pf = Path(parse_file)
            if not pf.is_file():
                return {"success": False, "error": f"Parse package file not found: {parse_file}"}
            parse_payload = json.loads(pf.read_text(encoding="utf-8"))
            resp = client.post(
                f"{base}/api/v1/books/{book_slug}/parse",
                headers=headers,
                json=parse_payload,
            )
        elif subcommand == "import-translations":
            ipf = Path(import_file)
            if not ipf.is_file():
                return {"success": False, "error": f"Import package file not found: {import_file}"}
            imp_payload = json.loads(ipf.read_text(encoding="utf-8"))
            resp = client.post(
                f"{base}/api/v1/books/{book_slug}/import-translations",
                headers=headers,
                json=imp_payload,
            )
        elif subcommand == "query-submissions":
            q_params: Dict[str, str] = {
                "limit": str(limit),
                "offset": str(offset),
                "include_xml": "true" if include_xml else "false",
            }
            if book_slug:
                q_params["book_slug"] = book_slug
            if chapter:
                q_params["chapter_id"] = chapter
            if status:
                q_params["status"] = status
            if section_type:
                q_params["section_type"] = section_type
            if author_handle or author:
                q_params["author_handle"] = author_handle or author
            resp = client.get(
                f"{base}/api/v1/submissions",
                headers=headers,
                params=q_params,
            )
        elif subcommand == "get-submission":
            if not submission_id:
                return {"success": False, "error": "--submission-id is required"}
            gs_params = {
                "include_context": "true" if include_context else "false",
                "include_workstream": "true" if include_workstream else "false",
            }
            resp = client.get(
                f"{base}/api/v1/submissions/{submission_id}",
                headers=headers,
                params=gs_params,
            )
        elif subcommand == "submit-book":
            sf = Path(source_file)
            if not sf.is_file():
                return {"success": False, "error": f"Source file not found: {source_file}"}
            resp = client.post(
                f"{base}/api/v1/books/propose",
                headers=headers,
                json={
                    "slug": slug,
                    "title": title,
                    "author": author,
                    "source_lang": source_lang,
                    "target_lang": target_lang,
                    "genre": genre,
                    "license": license_name,
                    "license_url": license_url,
                    "source_filename": sf.name,
                    "source_text": sf.read_text(encoding="utf-8"),
                },
            )
        elif subcommand == "submit-translation":
            tf = Path(translation_file)
            if not tf.is_file():
                return {"success": False, "error": f"Translation file not found: {translation_file}"}
            payload: Dict[str, Any] = {
                "book_slug": book_slug,
                "chapter_id": chapter,
                "translation_xml": tf.read_text(encoding="utf-8"),
                "engine_model": engine_model,
            }
            if additions_file:
                payload["additions"] = json.loads(Path(additions_file).read_text(encoding="utf-8"))
            if submission_id:
                payload["submission_id"] = submission_id
            if revision_token:
                payload["revision_token"] = revision_token
            if rebuttals_file:
                payload["rebuttals"] = json.loads(Path(rebuttals_file).read_text(encoding="utf-8"))
            resp = client.post(f"{base}/api/v1/submissions", headers=headers, json=payload)
        elif subcommand == "submit-review":
            rf = Path(review_file)
            if not rf.is_file():
                return {"success": False, "error": f"Review file not found: {review_file}"}
            eff_verdict = (verdict or "COMMENT").strip().upper()
            if eff_verdict == "REQUEST_CHANGES":
                eff_verdict = "CHANGES_REQUESTED"
            rev_payload: Dict[str, Any] = {
                "body_md": rf.read_text(encoding="utf-8"),
                "verdict": eff_verdict,
            }
            if issues_file:
                rev_payload["issues"] = json.loads(Path(issues_file).read_text(encoding="utf-8"))
            resp = client.post(
                f"{base}/api/v1/submissions/{submission_id}/reviews",
                headers=headers,
                json=rev_payload,
            )
        elif subcommand == "submit-bug":
            eff_body = body_md
            if body_file:
                bf = Path(body_file)
                if not bf.is_file():
                    return {"success": False, "error": f"Bug body file not found: {body_file}"}
                eff_body = bf.read_text(encoding="utf-8")
            bug_payload: Dict[str, Any] = {
                "title": title,
                "body_md": eff_body,
                "severity": severity,
                "book_slug": book_slug or "",
                "chapter_id": chapter or "",
                "submission_id": submission_id or "",
                "tool_name": tool_name,
                "error_code": int(error_code or 0),
            }
            resp = client.post(f"{base}/api/v1/bugs", headers=headers, json=bug_payload)
        elif subcommand == "query-bugs":
            if bug_id:
                resp = client.get(f"{base}/api/v1/bugs/{bug_id}", headers=headers)
            else:
                b_params: Dict[str, str] = {}
                if status:
                    b_params["status"] = status
                if book_slug:
                    b_params["book_slug"] = book_slug
                if author_handle:
                    b_params["reporter_handle"] = author_handle
                resp = client.get(f"{base}/api/v1/bugs", headers=headers, params=b_params)
        elif subcommand == "claim-bug":
            resp = client.post(
                f"{base}/api/v1/bugs/{bug_id}/claim",
                headers=headers,
                json={"note": resolution_note},
            )
        elif subcommand == "triage-bug":
            resp = client.post(
                f"{base}/api/v1/bugs/{bug_id}/triage",
                headers=headers,
                json={
                    "status": status,
                    "resolution_note": resolution_note,
                    "fixed_in_version": fixed_in_version,
                    "pr_url": pr_url,
                    "issue_url": issue_url,
                    "create_github_issue": bool(create_github_issue),
                },
            )
        else:
            return {"success": False, "error": f"Unsupported MCP subcommand: {subcommand}"}

        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            return {
                "success": False,
                "status_code": resp.status_code,
                **data,
            }
        if subcommand == "get-chapter" and output_file and "source_xml" in data:
            Path(output_file).write_text(str(data["source_xml"]), encoding="utf-8")
        if subcommand == "get-book-source" and output_file and "source_text" in data:
            Path(output_file).write_text(str(data["source_text"]), encoding="utf-8")
        return {"success": True, **data}
    finally:
        if close_after:
            client.close()


def _load_optional_config(config_path: Optional[str] = None) -> Optional[CollabConfig]:
    """Load CollabConfig if explicitly requested or available at default location."""
    has_default = bool(os.environ.get("COLLAB_CONFIG_PATH")) or (REPO_ROOT / "config.json").is_file()
    if not config_path and not has_default:
        return None
    try:
        return load_collab_config(config_path)
    except ConfigError as exc:
        logger.debug("Optional collab config unavailable: %s", exc)
        return None


def _get_gh_token(gh_user: str) -> str:
    """Fetch GitHub auth token for a specific user without switching global auth."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return "mock-gh-token"
    clean_user = (gh_user or "").strip()
    if not clean_user and os.environ.get("GH_TOKEN"):
        return os.environ["GH_TOKEN"]
    clean_env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    cmd = ["gh", "auth", "token"] + (["-u", clean_user] if clean_user else [])
    try:
        proc = subprocess.run(
            cmd,
            env=clean_env,
            capture_output=True,
            text=True,
            check=True,
        )
        tok = proc.stdout.strip()
        if not tok:
            raise ValueError(f"Empty token returned for user '{gh_user}'")
        return tok
    except subprocess.CalledProcessError as e:
        raise ValueError(
            f"Failed to retrieve auth token for user '{gh_user}': {e.stderr.strip()}"
        ) from e


class GitHubAccountContext:
    """Isolates git and gh operations for a specific GitHub user via GH_TOKEN and GIT_ASKPASS."""

    def __init__(self, gh_user: str):
        self.gh_user = gh_user.strip()
        self.token: str = ""
        self.temp_dir: str = ""
        self.askpass_path: str = ""

    def __enter__(self) -> "GitHubAccountContext":
        self.token = _get_gh_token(self.gh_user)
        self.temp_dir = tempfile.mkdtemp(prefix=f"collab_askpass_{self.gh_user}_")
        self.askpass_path = os.path.join(self.temp_dir, "askpass.sh")
        script_content = (
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  *Username*) echo \"x-access-token\" ;;\n"
            "  *Password*) echo \"$GH_TOKEN\" ;;\n"
            "  *) echo \"$GH_TOKEN\" ;;\n"
            "esac\n"
        )
        with open(self.askpass_path, "w", encoding="utf-8") as f:
            f.write(script_content)
        os.chmod(self.askpass_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)  # 0700
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def run_gh(
        self,
        args: List[str],
        cwd: Optional[str] = None,
        repo: Optional[str] = None,
        check: bool = True,
        text: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["GH_TOKEN"] = self.token
        full_args = list(args)
        if repo and "--repo" not in full_args and "-R" not in full_args:
            full_args.extend(["--repo", repo])
        return subprocess.run(
            ["gh"] + full_args,
            cwd=cwd,
            env=env,
            check=check,
            text=text,
            capture_output=capture_output,
        )

    def run_git(
        self,
        args: List[str],
        cwd: Optional[str] = None,
        check: bool = True,
        text: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["GH_TOKEN"] = self.token
        base_cmd = [
            "git",
            "-c",
            "credential.helper=",
            "-c",
            f"core.askPass={self.askpass_path}",
        ]
        return subprocess.run(
            base_cmd + args,
            cwd=cwd,
            env=env,
            check=check,
            text=text,
            capture_output=capture_output,
        )


def _fetch_open_issues(ctx: GitHubAccountContext, repo: Optional[str] = None) -> List[Dict[str, Any]]:
    cmd = ["issue", "list", "--state", "open", "--json", "number,title,labels,body"]
    if repo:
        cmd.extend(["-R", repo])
    proc = ctx.run_gh(cmd)
    try:
        return json.loads(proc.stdout)
    except Exception:
        return []


def _fetch_open_prs(ctx: GitHubAccountContext, repo: Optional[str] = None) -> List[Dict[str, Any]]:
    cmd = [
        "pr",
        "list",
        "--state",
        "open",
        "--json",
        "number,title,headRefName,headRefOid,author,labels,reviews,comments",
    ]
    if repo:
        cmd.extend(["-R", repo])
    proc = ctx.run_gh(cmd)
    try:
        return json.loads(proc.stdout)
    except Exception:
        return []


def _fetch_pr_metadata(ctx: GitHubAccountContext, pr_number: int, repo: Optional[str] = None) -> Dict[str, Any]:
    cmd = [
        "pr",
        "view",
        str(pr_number),
        "--json",
        "number,title,headRefName,headRefOid,author,labels,reviews,comments",
    ]
    if repo:
        cmd.extend(["-R", repo])
    proc = ctx.run_gh(cmd)
    return json.loads(proc.stdout)


def list_role_tasks(
    role: str,
    gh_user: str,
    book_slug: Optional[str] = None,
    workers: Optional[List[str]] = None,
    mode: str = "sharded",
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    """List open tasks for a user filtered by role and sharding (REQ-035)."""
    cfg = _load_optional_config(config_path)
    repo = cfg.repo_full_name if cfg else None

    with GitHubAccountContext(gh_user) as ctx:
        all_issues = _fetch_open_issues(ctx, repo=repo)
        all_prs = _fetch_open_prs(ctx, repo=repo)

    # 1. Translate tasks from open chapter issues
    translate_issues: List[Dict[str, Any]] = []
    chapter_issues: List[Tuple[int, Dict[str, Any]]] = []
    for iss in all_issues:
        labels = [l.get("name", "") for l in iss.get("labels", [])]
        if "status:open" not in labels:
            continue
        if book_slug and f"book:{book_slug}" not in labels:
            continue
        m = re.search(r"\[ch(\d+)\]", iss.get("title", ""))
        ch_idx = int(m.group(1)) if m else 999
        chapter_issues.append((ch_idx, iss))

    chapter_issues.sort(key=lambda pair: pair[0])

    if mode == "sharded" and workers and gh_user in workers:
        w_idx = workers.index(gh_user)
        n_workers = len(workers)
        for seq, (ch_idx, iss) in enumerate(chapter_issues):
            if (seq % n_workers) == w_idx:
                translate_issues.append(iss)
    else:
        translate_issues = [iss for _, iss in chapter_issues]

    def _matches_book(pr_obj: Dict[str, Any], pr_labels: List[str]) -> bool:
        if not book_slug:
            return True
        if f"book:{book_slug}" in pr_labels:
            return True
        head_ref = str(pr_obj.get("headRefName") or "").strip()
        return head_ref.startswith(f"translate/{book_slug}-")

    # 2. Revise PRs: open translation PRs authored by gh_user with status:revise
    # 3. Review tasks: open translation PRs authored by someone else needing fresh review
    _non_reviewable_statuses = (
        "status:revise",
        "status:candidate",
        "status:merged",
        "status:reassign",
        "status:not-selected",
        "status:flagged",
    )
    revise_prs: List[Dict[str, Any]] = []
    review_prs: List[Dict[str, Any]] = []
    for pr in all_prs:
        head_ref = str(pr.get("headRefName") or "").strip()
        if head_ref and not head_ref.startswith("translate/"):
            continue
        author = (pr.get("author") or {}).get("login", "")
        labels = [l.get("name", "") for l in pr.get("labels", [])]
        if not _matches_book(pr, labels):
            continue

        if author == gh_user and "status:revise" in labels:
            revise_prs.append(pr)

        if author != gh_user and not any(s in labels for s in _non_reviewable_statuses):
            head_oid = pr.get("headRefOid", "")
            # Check if gh_user already posted a fresh review for head_oid
            already_reviewed = False
            for rev in pr.get("reviews", []):
                rev_login = (rev.get("author") or {}).get("login", "")
                rev_oid = (rev.get("commit") or {}).get("oid", "")
                if rev_login == gh_user and (not rev_oid or rev_oid == head_oid):
                    already_reviewed = True
                    break
            if not already_reviewed:
                review_prs.append(pr)

    return {
        "role": role,
        "gh_user": gh_user,
        "mode": mode,
        "translate_issues": translate_issues if role in ("translator", "all") else [],
        "revise_prs": revise_prs if role in ("translator", "all") else [],
        "review_prs": review_prs if role in ("reviewer", "all") else [],
    }


def submit_book(
    gh_user: str,
    slug: str,
    source_file: str,
    title: str,
    author: str,
    source_lang: str,
    target_lang: str = "vi",
    license_name: str = "public-domain",
    license_url: str = "",
    genre: str = "fiction",
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Pre-screen validate and submit a book proposal PR (REQ-035)."""
    cfg = load_collab_config(config_path)
    src_path = Path(source_file).resolve()
    if not src_path.exists():
        return {"success": False, "error": f"Source file not found: {source_file}"}

    manifest_data = {
        "slug": slug,
        "title": title,
        "author": author,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "license": license_name,
        "license_url": license_url or "https://creativecommons.org/publicdomain/zero/1.0/",
        "submitter": gh_user,
        "genre": genre,
    }

    branch = f"book/{slug}"
    repo_root = Path(cfg.workspace_dir).resolve() if cfg.workspace_dir else REPO_ROOT

    # Preflight check (imported lazily so remote --mcp-url workflows avoid heavy extractors)
    from scripts.collab_intake import validate_submission_pr

    val_res = validate_submission_pr(
        changed_files=[f"books/{slug}/manifest.json", f"books/{slug}/source/{src_path.name}"],
        manifest_data=manifest_data,
        source_file_path=src_path,
        max_bytes=cfg.max_source_bytes,
        allowed_licenses=cfg.allowed_licenses,
        supported_pairs=cfg.supported_pairs,
        head_ref=branch,
        repo_root=repo_root,
    )
    if val_res.get("prescreen_status") == "FAIL":
        return {
            "success": False,
            "error": "Book submission pre-screen validation failed",
            "defects": val_res.get("defects", []),
        }

    worktree_dir = repo_root / ".worktrees" / f"submit_{slug}_{gh_user}"

    with GitHubAccountContext(gh_user) as ctx:
        try:
            if worktree_dir.exists():
                shutil.rmtree(worktree_dir, ignore_errors=True)
            worktree_dir.parent.mkdir(parents=True, exist_ok=True)

            ctx.run_git(["worktree", "add", "-B", branch, str(worktree_dir), "HEAD"], cwd=str(repo_root))

            target_book_dir = worktree_dir / "books" / slug
            target_source_dir = target_book_dir / "source"
            target_source_dir.mkdir(parents=True, exist_ok=True)

            with open(target_book_dir / "manifest.json", "w", encoding="utf-8") as f:
                json.dump(manifest_data, f, indent=2, ensure_ascii=False)
                f.write("\n")

            shutil.copy2(src_path, target_source_dir / src_path.name)

            ctx.run_git(["add", f"books/{slug}"], cwd=str(worktree_dir))
            ctx.run_git(
                ["commit", "-m", f"feat(book): propose intake for {slug}"],
                cwd=str(worktree_dir),
            )
            ctx.run_git(["push", "-u", "origin", branch, "--force"], cwd=str(worktree_dir))

            # Open PR via gh
            pr_body = (
                f"## Book Proposal: {title}\n\n"
                f"- **Slug**: `{slug}`\n"
                f"- **Author**: {author}\n"
                f"- **Language**: `{source_lang}` -> `{target_lang}`\n"
                f"- **License**: {license_name} ({manifest_data['license_url']})\n"
                f"- **Submitter**: @{gh_user}\n"
            )
            pr_cmd = [
                "pr",
                "create",
                "--title",
                f"book({slug}): propose intake for {title}",
                "--body",
                pr_body,
                "--head",
                branch,
                "--base",
                "main",
            ]
            if cfg.repo_full_name:
                pr_cmd.extend(["-R", cfg.repo_full_name])

            proc = ctx.run_gh(pr_cmd, cwd=str(worktree_dir))
            pr_url = proc.stdout.strip()
            return {"success": True, "branch": branch, "pr_url": pr_url}
        finally:
            ctx.run_git(["worktree", "remove", "--force", str(worktree_dir)], cwd=str(repo_root), check=False)
            if worktree_dir.exists():
                shutil.rmtree(worktree_dir, ignore_errors=True)


def approve_intake(
    gh_user: str,
    pr_number: int,
    config_path: Optional[str] = None,
    allow_warn: bool = False,
) -> Dict[str, Any]:
    """Approve book intake PR if prescreen passed and caller is human_approver (REQ-035)."""
    cfg = load_collab_config(config_path)
    if gh_user != cfg.human_approver:
        return {
            "success": False,
            "error": f"User '{gh_user}' is not configured human_approver ('{cfg.human_approver}')",
        }

    with GitHubAccountContext(gh_user) as ctx:
        meta = _fetch_pr_metadata(ctx, pr_number, repo=cfg.repo_full_name)
        head_oid = meta.get("headRefOid", "")
        if not head_oid:
            return {"success": False, "error": f"Could not determine headRefOid for PR #{pr_number}"}

        # Check for prescreen comment
        has_prescreen_pass = False
        prescreen_re = re.compile(
            rf"\[collab-prescreen\s+sha={head_oid}\s+status=(PASS|WARN)\]"
            if allow_warn
            else rf"\[collab-prescreen\s+sha={head_oid}\s+status=PASS\]"
        )
        for comment in meta.get("comments", []):
            author = (comment.get("author") or {}).get("login", "")
            if author == cfg.bot_login:
                body = comment.get("body", "")
                if prescreen_re.search(body):
                    has_prescreen_pass = True
                    break

        if not has_prescreen_pass:
            return {
                "success": False,
                "error": f"No valid [collab-prescreen sha={head_oid} status=PASS] found on PR #{pr_number}",
            }

        ctx.run_gh(
            ["pr", "comment", str(pr_number), "--body", f"/approve-intake {head_oid}"],
            repo=cfg.repo_full_name,
        )
        return {"success": True, "pr_number": pr_number, "head_oid": head_oid}


def submit_translation(
    gh_user: str,
    book_slug: str,
    chapter: str,
    translation_file: str,
    additions_file: Optional[str] = None,
    engine_model: str = "human",
    agent_name: Optional[str] = None,
    claim: bool = False,
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate and submit a chapter translation PR (REQ-035)."""
    from scripts.utils.pr_body import build_translation_pr_body
    from scripts.helper import parse_segments

    cfg = load_collab_config(config_path)
    tx_path = Path(translation_file).resolve()
    if not tx_path.exists():
        return {"success": False, "error": f"Translation file not found: {translation_file}"}

    tx_text = tx_path.read_text(encoding="utf-8")
    branch = f"translate/{book_slug}-{chapter}-{gh_user}"
    agent_tag = agent_name or gh_user

    repo_root = Path(cfg.workspace_dir).resolve() if cfg.workspace_dir else REPO_ROOT
    ch_dir = repo_root / "runs" / book_slug / chapter
    source_file = ch_dir / "source.txt"
    manifest_file = repo_root / "books" / book_slug / "manifest.json"
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8")) if manifest_file.exists() else {}

    src_lang = str(manifest_data.get("source_lang", "en"))
    tgt_lang = str(manifest_data.get("target_lang", "vi"))

    br_file = repo_root / "runs" / book_slug / "book_record.json"
    glossary_sha = "seed"
    if br_file.is_file():
        try:
            glossary_sha = json.loads(br_file.read_text(encoding="utf-8")).get("glossary_sha", "")
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("Failed reading glossary_sha from %s: %s", br_file, exc)

    segs = parse_segments(tx_text)
    seg_first = segs[0]["id"] if segs else "S-0001"
    seg_last = segs[-1]["id"] if segs else "S-0001"

    with GitHubAccountContext(gh_user) as ctx:
        # Find issue number
        issue_num = None
        try:
            proc = ctx.run_gh(
                ["issue", "list", "--repo", cfg.repo_full_name, "--label", f"book:{book_slug}", "--search", chapter, "--json", "number,title"]
            )
            issues = json.loads(proc.stdout)
            for iss in issues:
                if chapter in iss.get("title", ""):
                    issue_num = iss.get("number")
                    break
        except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            logger.debug("Issue lookup skipped for %s/%s: %s", book_slug, chapter, exc)

        if claim and issue_num:
            try:
                ctx.run_gh(["issue", "comment", str(issue_num), "--repo", cfg.repo_full_name, "--body", "/claim"])
            except subprocess.CalledProcessError as exc:
                logger.debug("Issue claim comment skipped for #%s: %s", issue_num, exc)

        issue_ref = f"#{issue_num}" if issue_num else "#0"

        pr_body = build_translation_pr_body(
            agent_login=gh_user,
            engine_model=engine_model,
            source_lang=src_lang,
            target_lang=tgt_lang,
            book_slug=book_slug,
            ch_name=chapter,
            seg_first=seg_first,
            seg_last=seg_last,
            issue_ref=issue_ref,
            glossary_sha=glossary_sha,
        )

        changed_files = [f"runs/{book_slug}/{chapter}/translation.xml"]
        additions_raw: Optional[bytes] = None
        if additions_file and Path(additions_file).exists():
            changed_files.append(f"runs/{book_slug}/{chapter}/analyze_additions.json")
            additions_raw = Path(additions_file).read_bytes()

        expected_sha = glossary_sha if glossary_sha and glossary_sha != "seed" else None
        val_res = validate_translation_pr(
            head_ref=branch,
            author_login=gh_user,
            body=pr_body,
            changed_files=changed_files,
            source_xml_text=source_file.read_text(encoding="utf-8") if source_file.exists() else tx_text,
            translation_xml_text=tx_text,
            expected_glossary_sha=expected_sha,
            expected_issue_number=issue_num,
            additions_raw=additions_raw,
            bot_login=cfg.bot_login,
            is_seed_pr=False,
            manifest=manifest_data,
            existing_slugs={book_slug},
        )

        if not val_res.get("valid", False):
            return {
                "success": False,
                "error": "Translation pre-flight validation failed",
                "defects": val_res.get("defects", []),
            }

        worktree_dir = repo_root / ".worktrees" / f"trans_{book_slug}_{chapter}_{gh_user}"

        try:
            if worktree_dir.exists():
                shutil.rmtree(worktree_dir, ignore_errors=True)
            worktree_dir.parent.mkdir(parents=True, exist_ok=True)

            ctx.run_git(["worktree", "add", "-B", branch, str(worktree_dir), "HEAD"], cwd=str(repo_root))

            target_ch_dir = worktree_dir / "runs" / book_slug / chapter
            target_ch_dir.mkdir(parents=True, exist_ok=True)

            shutil.copy2(tx_path, target_ch_dir / "translation.xml")
            if additions_file and Path(additions_file).exists():
                shutil.copy2(additions_file, target_ch_dir / "analyze_additions.json")

            ctx.run_git(["add", f"runs/{book_slug}/{chapter}"], cwd=str(worktree_dir))
            ctx.run_git(
                ["commit", "-m", f"feat(translate): submit translation for {book_slug} {chapter}"],
                cwd=str(worktree_dir),
            )
            ctx.run_git(["push", "-u", "origin", branch, "--force"], cwd=str(worktree_dir))

            labels = [f"book:{book_slug}", f"lang:{src_lang}-{tgt_lang}", f"agent:{agent_tag}"]
            for lbl in labels:
                try:
                    ctx.run_gh(["label", "create", lbl, "--color", "c2e0c6", "--force", "-R", cfg.repo_full_name], check=False)
                except subprocess.CalledProcessError as exc:
                    logger.debug("Label ensure ignored for '%s': %s", lbl, exc)
            list_cmd = [
                "pr",
                "list",
                "--state",
                "open",
                "--head",
                branch,
                "--json",
                "number,url",
            ] + (["-R", cfg.repo_full_name] if cfg.repo_full_name else [])
            list_proc = ctx.run_gh(list_cmd, cwd=str(worktree_dir), check=False)
            existing_prs = (
                json.loads(list_proc.stdout)
                if list_proc.returncode == 0 and (list_proc.stdout or "").strip()
                else []
            )
            if existing_prs:
                existing_num = existing_prs[0].get("number")
                edit_cmd = [
                    "pr",
                    "edit",
                    str(existing_num),
                    "--title",
                    f"translate({book_slug}): {chapter} by @{gh_user}",
                    "--body",
                    pr_body,
                ] + (["-R", cfg.repo_full_name] if cfg.repo_full_name else [])
                ctx.run_gh(edit_cmd, cwd=str(worktree_dir))
                return {
                    "success": True,
                    "branch": branch,
                    "pr_url": str(existing_prs[0].get("url") or "").strip(),
                }

            pr_cmd = [
                "pr",
                "create",
                "--title",
                f"translate({book_slug}): {chapter} by @{gh_user}",
                "--body",
                pr_body,
                "--head",
                branch,
                "--base",
                "main",
            ]
            for lbl in labels:
                pr_cmd.extend(["--label", lbl])
            if cfg.repo_full_name:
                pr_cmd.extend(["-R", cfg.repo_full_name])

            proc = ctx.run_gh(pr_cmd, cwd=str(worktree_dir))
            pr_url = proc.stdout.strip()
            return {"success": True, "branch": branch, "pr_url": pr_url}
        finally:
            ctx.run_git(["worktree", "remove", "--force", str(worktree_dir)], cwd=str(repo_root), check=False)
            if worktree_dir.exists():
                shutil.rmtree(worktree_dir, ignore_errors=True)


def submit_review(
    gh_user: str,
    pr_number: int,
    review_file: str,
    event: str = "COMMENT",
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate review rubric and post peer review as gh_user (REQ-035)."""
    cfg = _load_optional_config(config_path)
    repo = cfg.repo_full_name if cfg else None

    with GitHubAccountContext(gh_user) as ctx:
        meta = _fetch_pr_metadata(ctx, pr_number, repo=repo)
        pr_author = (meta.get("author") or {}).get("login", "")
        head_oid = meta.get("headRefOid", "")

        if gh_user == pr_author:
            return {"success": False, "error": f"Self-review is strictly blocked (@{gh_user} is PR author)"}

        rf_path = Path(review_file).resolve()
        if not rf_path.exists():
            return {"success": False, "error": f"Review file not found: {review_file}"}

        review_body = rf_path.read_text(encoding="utf-8")

        val_res = validate_review(
            pr_author_login=pr_author,
            reviewer_login=gh_user,
            review_body=review_body,
            line_comment_count=1 if re.search(r"\bS-\d{4}\b", review_body) else 0,
        )

        if not val_res.get("valid", False):
            return {
                "success": False,
                "error": "Review rubric validation failed",
                "defects": val_res.get("defects", []),
            }

        cmd = ["pr", "review", str(pr_number), "-F", str(rf_path)]
        ev = event.upper()
        if ev == "APPROVE":
            cmd.append("--approve")
        elif ev in ("REQUEST_CHANGES", "CHANGES_REQUESTED"):
            cmd.append("--request-changes")
        else:
            cmd.append("--comment")

        if repo:
            cmd.extend(["-R", repo])

        ctx.run_gh(cmd)
        return {"success": True, "pr_number": pr_number, "reviewer": gh_user, "head_oid": head_oid}


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-account, role-aware worker CLI (collab-book-translate).")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # register-agent (MCP mode)
    p_reg = subparsers.add_parser("register-agent", help="Self-serve agent handle registration on collab-mcp.")
    p_reg.add_argument("--mcp-url", default="")
    p_reg.add_argument("--handle", required=True)
    p_reg.add_argument("--display-name", default="")
    p_reg.add_argument("--model-info", default="")

    # get-chapter (MCP mode)
    p_getc = subparsers.add_parser("get-chapter", help="Fetch chapter source XML and book_record from collab-mcp.")
    p_getc.add_argument("--mcp-url", default="")
    p_getc.add_argument("--agent-key", default="")
    p_getc.add_argument("--book-slug", required=True)
    p_getc.add_argument("--chapter", required=True)
    p_getc.add_argument("--output-file")

    # get-book-source (MCP mode)
    p_gets = subparsers.add_parser("get-book-source", help="Fetch full raw manuscript source from collab-mcp.")
    p_gets.add_argument("--mcp-url", default="")
    p_gets.add_argument("--agent-key", default="")
    p_gets.add_argument("--book-slug", required=True)
    p_gets.add_argument("--output-file")

    # submit-book-parse (MCP mode, agent_admin / ADMIN)
    p_parse = subparsers.add_parser("submit-book-parse", help="Upload parsed chapters, brief, and book_record to collab-mcp.")
    p_parse.add_argument("--mcp-url", default="")
    p_parse.add_argument("--agent-key", default="")
    p_parse.add_argument("--book-slug", required=True)
    p_parse.add_argument("--parse-file", required=True)

    # import-translations (MCP mode, agent_admin / ADMIN)
    p_imp = subparsers.add_parser("import-translations", help="Batch-import pre-translated chapter XMLs into pending_review.")
    p_imp.add_argument("--mcp-url", default="")
    p_imp.add_argument("--agent-key", default="")
    p_imp.add_argument("--book-slug", required=True)
    p_imp.add_argument("--import-file", required=True)

    # query-submissions (MCP mode)
    p_qsub = subparsers.add_parser("query-submissions", help="Query chapter submissions across books/chapters/statuses.")
    p_qsub.add_argument("--mcp-url", default="")
    p_qsub.add_argument("--agent-key", default="")
    p_qsub.add_argument("--book-slug")
    p_qsub.add_argument("--chapter")
    p_qsub.add_argument("--status", default="")
    p_qsub.add_argument("--author-handle", default="")
    p_qsub.add_argument("--section-type", default="")
    p_qsub.add_argument("--limit", type=int, default=100)
    p_qsub.add_argument("--offset", type=int, default=0)
    p_qsub.add_argument("--no-xml", action="store_true")

    # get-submission (MCP mode)
    p_gsub = subparsers.add_parser("get-submission", help="Fetch submission status with unified reviewer context and peer reviews.")
    p_gsub.add_argument("--mcp-url", default="")
    p_gsub.add_argument("--agent-key", default="")
    p_gsub.add_argument("--submission-id", required=True)
    p_gsub.add_argument("--no-context", action="store_true")
    p_gsub.add_argument("--include-workstream", action="store_true")

    # list-tasks
    p_list = subparsers.add_parser("list-tasks", help="List tasks filtered by role.")
    p_list.add_argument("--role", choices=["translator", "reviewer", "agent_admin", "tech_admin", "all"], default="all")
    p_list.add_argument("--gh-user")
    p_list.add_argument("--mcp-url", default="")
    p_list.add_argument("--agent-key", default="")
    p_list.add_argument("--book-slug")
    p_list.add_argument("--section-type", default="")
    p_list.add_argument("--no-matter", action="store_true")
    p_list.add_argument("--limit", type=int, default=200)
    p_list.add_argument("--offset", type=int, default=0)
    p_list.add_argument("--workers", help="Comma-separated logins for sharding.")
    p_list.add_argument("--mode", choices=["sharded", "compete"], default="sharded")
    p_list.add_argument("--config")

    # submit-book
    p_book = subparsers.add_parser("submit-book", help="Propose a book intake PR or MCP proposal.")
    p_book.add_argument("--gh-user")
    p_book.add_argument("--mcp-url", default="")
    p_book.add_argument("--agent-key", default="")
    p_book.add_argument("--slug", required=True)
    p_book.add_argument("--source-file", required=True)
    p_book.add_argument("--title", required=True)
    p_book.add_argument("--author", required=True)
    p_book.add_argument("--source-lang", required=True)
    p_book.add_argument("--target-lang", default="vi")
    p_book.add_argument("--license", default="public-domain")
    p_book.add_argument("--license-url", default="")
    p_book.add_argument("--genre", default="fiction")
    p_book.add_argument("--config")

    # approve-intake
    p_app = subparsers.add_parser("approve-intake", help="Approve book intake PR.")
    p_app.add_argument("--gh-user", required=True)
    p_app.add_argument("--pr", type=int, required=True)
    p_app.add_argument("--allow-warn", action="store_true")
    p_app.add_argument("--config")

    # submit-translation
    p_trans = subparsers.add_parser("submit-translation", help="Submit chapter translation via MCP or PR.")
    p_trans.add_argument("--gh-user")
    p_trans.add_argument("--mcp-url", default="")
    p_trans.add_argument("--agent-key", default="")
    p_trans.add_argument("--book-slug", required=True)
    p_trans.add_argument("--chapter", required=True)
    p_trans.add_argument("--translation-file", required=True)
    p_trans.add_argument("--additions-file")
    p_trans.add_argument("--engine-model", default="human")
    p_trans.add_argument("--agent-name")
    p_trans.add_argument("--submission-id")
    p_trans.add_argument("--revision-token")
    p_trans.add_argument("--rebuttals-file")
    p_trans.add_argument("--claim", action="store_true")
    p_trans.add_argument("--config")

    # submit-review
    p_rev = subparsers.add_parser("submit-review", help="Submit peer review via MCP or PR.")
    p_rev.add_argument("--gh-user")
    p_rev.add_argument("--mcp-url", default="")
    p_rev.add_argument("--agent-key", default="")
    p_rev.add_argument("--pr", type=int)
    p_rev.add_argument("--submission-id")
    p_rev.add_argument("--review-file", required=True)
    p_rev.add_argument("--event", choices=["COMMENT", "APPROVE", "REQUEST_CHANGES"], default="COMMENT")
    p_rev.add_argument("--verdict", choices=["", "COMMENT", "APPROVE", "CHANGES_REQUESTED", "REQUEST_CHANGES"], default="")
    p_rev.add_argument("--issues-file")
    p_rev.add_argument("--config")

    # submit-bug (MCP mode)
    p_bug = subparsers.add_parser("submit-bug", help="Submit a bug report to collab-mcp.")
    p_bug.add_argument("--mcp-url", default="")
    p_bug.add_argument("--agent-key", default="")
    p_bug.add_argument("--title", required=True)
    p_bug.add_argument("--body-md", default="")
    p_bug.add_argument("--body-file", default="")
    p_bug.add_argument("--severity", choices=["P0", "P1", "P2", "P3"], default="P1")
    p_bug.add_argument("--book-slug", default="")
    p_bug.add_argument("--chapter", default="")
    p_bug.add_argument("--submission-id", default="")
    p_bug.add_argument("--tool-name", default="")
    p_bug.add_argument("--error-code", type=int, default=0)

    # query-bugs (MCP mode)
    p_qbug = subparsers.add_parser("query-bugs", help="Query bug reports and triage resolutions.")
    p_qbug.add_argument("--mcp-url", default="")
    p_qbug.add_argument("--agent-key", default="")
    p_qbug.add_argument("--bug-id", default="")
    p_qbug.add_argument("--status", default="")
    p_qbug.add_argument("--book-slug", default="")
    p_qbug.add_argument("--reporter-handle", default="")

    # heartbeat (MCP mode)
    p_hb = subparsers.add_parser("heartbeat", help="Send a lightweight liveness heartbeat + optional status note to collab.vinhthang.dev.")
    p_hb.add_argument("--mcp-url", default="")
    p_hb.add_argument("--agent-key", default="")
    p_hb.add_argument("--status-note", default="")
    p_hb.add_argument("--client-name", default="collab-worker")

    # claim-bug (MCP mode, tech_admin / agent_admin / ADMIN)
    p_cbug = subparsers.add_parser("claim-bug", help="Claim an accepted bug report exclusively (status='in_progress').")
    p_cbug.add_argument("--mcp-url", default="")
    p_cbug.add_argument("--agent-key", default="")
    p_cbug.add_argument("--bug-id", required=True)
    p_cbug.add_argument("--resolution-note", default="")

    # triage-bug (MCP mode, tech_admin / agent_admin / ADMIN)
    p_tbug = subparsers.add_parser("triage-bug", help="Triage an agent bug report (accepted|in_progress|fixed|declined).")
    p_tbug.add_argument("--mcp-url", default="")
    p_tbug.add_argument("--agent-key", default="")
    p_tbug.add_argument("--bug-id", required=True)
    p_tbug.add_argument("--status", choices=["open", "accepted", "in_progress", "fixed", "declined"], required=True)
    p_tbug.add_argument("--resolution-note", default="")
    p_tbug.add_argument("--fixed-in-version", default="")
    p_tbug.add_argument("--pr-url", default="")
    p_tbug.add_argument("--issue-url", default="")
    p_tbug.add_argument("--create-github-issue", action="store_true")

    args = parser.parse_args()

    if args.subcommand in (
        "register-agent",
        "get-chapter",
        "get-book-source",
        "submit-book-parse",
        "import-translations",
        "query-submissions",
        "get-submission",
        "submit-bug",
        "query-bugs",
        "heartbeat",
        "claim-bug",
        "triage-bug",
    ):
        mode_info = resolve_worker_mode(None, args.mcp_url)
        if mode_info["mode"] != "mcp":
            print(json.dumps({"success": False, "error": mode_info["error"]}, indent=2))
            sys.exit(1)
        res = run_mcp_command(
            subcommand=args.subcommand,
            mcp_url=mode_info["target"],
            agent_key=getattr(args, "agent_key", ""),
            book_slug=getattr(args, "book_slug", None),
            chapter=getattr(args, "chapter", None),
            handle=getattr(args, "handle", ""),
            display_name=getattr(args, "display_name", "") or getattr(args, "client_name", ""),
            model_info=getattr(args, "model_info", ""),
            parse_file=getattr(args, "parse_file", ""),
            import_file=getattr(args, "import_file", ""),
            status=getattr(args, "status", ""),
            author_handle=getattr(args, "author_handle", "") or getattr(args, "reporter_handle", ""),
            output_file=getattr(args, "output_file", None),
            title=getattr(args, "title", ""),
            body_md=getattr(args, "body_md", ""),
            body_file=getattr(args, "body_file", ""),
            severity=getattr(args, "severity", "P1"),
            submission_id=getattr(args, "submission_id", None),
            tool_name=getattr(args, "tool_name", ""),
            error_code=getattr(args, "error_code", 0),
            bug_id=getattr(args, "bug_id", ""),
            resolution_note=getattr(args, "resolution_note", "") or getattr(args, "status_note", ""),
            fixed_in_version=getattr(args, "fixed_in_version", ""),
            pr_url=getattr(args, "pr_url", ""),
            issue_url=getattr(args, "issue_url", ""),
            create_github_issue=getattr(args, "create_github_issue", False),
            limit=getattr(args, "limit", 100),
            offset=getattr(args, "offset", 0),
            section_type=getattr(args, "section_type", ""),
            include_xml=not getattr(args, "no_xml", False),
            include_context=not getattr(args, "no_context", False),
            include_workstream=getattr(args, "include_workstream", False),
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
        if not res.get("success"):
            sys.exit(1)
        return

    if args.subcommand != "approve-intake":
        mode_info = resolve_worker_mode(getattr(args, "gh_user", None), getattr(args, "mcp_url", None))
        if mode_info["mode"] == "error":
            print(json.dumps({"success": False, "error": mode_info["error"]}, indent=2))
            sys.exit(1)
        if mode_info["mode"] == "mcp":
            eff_verdict = getattr(args, "verdict", "") or getattr(args, "event", "COMMENT")
            res = run_mcp_command(
                subcommand=args.subcommand,
                mcp_url=mode_info["target"],
                agent_key=getattr(args, "agent_key", ""),
                role=getattr(args, "role", "all"),
                book_slug=getattr(args, "book_slug", None),
                chapter=getattr(args, "chapter", None),
                slug=getattr(args, "slug", ""),
                source_file=getattr(args, "source_file", ""),
                title=getattr(args, "title", ""),
                author=getattr(args, "author", ""),
                source_lang=getattr(args, "source_lang", "en"),
                target_lang=getattr(args, "target_lang", "vi"),
                license_name=getattr(args, "license", "public-domain"),
                license_url=getattr(args, "license_url", ""),
                genre=getattr(args, "genre", "fiction"),
                translation_file=getattr(args, "translation_file", ""),
                additions_file=getattr(args, "additions_file", None),
                engine_model=getattr(args, "engine_model", "mcp-agent"),
                submission_id=getattr(args, "submission_id", None),
                revision_token=getattr(args, "revision_token", None),
                rebuttals_file=getattr(args, "rebuttals_file", None),
                review_file=getattr(args, "review_file", ""),
                verdict=eff_verdict,
                issues_file=getattr(args, "issues_file", None),
                limit=getattr(args, "limit", 200),
                offset=getattr(args, "offset", 0),
                section_type=getattr(args, "section_type", ""),
                include_matter=not getattr(args, "no_matter", False),
            )
            print(json.dumps(res, indent=2, ensure_ascii=False))
            if not res.get("success"):
                sys.exit(1)
            return

    if args.subcommand == "list-tasks":
        workers = [w.strip() for w in args.workers.split(",")] if args.workers else None
        res = list_role_tasks(
            role=args.role,
            gh_user=args.gh_user,
            book_slug=args.book_slug,
            workers=workers,
            mode=args.mode,
            config_path=args.config,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))

    elif args.subcommand == "submit-book":
        res = submit_book(
            gh_user=args.gh_user,
            slug=args.slug,
            source_file=args.source_file,
            title=args.title,
            author=args.author,
            source_lang=args.source_lang,
            target_lang=args.target_lang,
            license_name=args.license,
            license_url=args.license_url,
            genre=args.genre,
            config_path=args.config,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
        if not res.get("success"):
            sys.exit(1)

    elif args.subcommand == "approve-intake":
        res = approve_intake(
            gh_user=args.gh_user,
            pr_number=args.pr,
            config_path=args.config,
            allow_warn=args.allow_warn,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
        if not res.get("success"):
            sys.exit(1)

    elif args.subcommand == "submit-translation":
        res = submit_translation(
            gh_user=args.gh_user,
            book_slug=args.book_slug,
            chapter=args.chapter,
            translation_file=args.translation_file,
            additions_file=args.additions_file,
            engine_model=args.engine_model,
            agent_name=args.agent_name,
            claim=args.claim,
            config_path=args.config,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
        if not res.get("success"):
            sys.exit(1)

    elif args.subcommand == "submit-review":
        res = submit_review(
            gh_user=args.gh_user,
            pr_number=args.pr,
            review_file=args.review_file,
            event=args.event,
            config_path=args.config,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
        if not res.get("success"):
            sys.exit(1)


if __name__ == "__main__":
    main()
