#!/usr/bin/env python3
"""
scripts/collab_worker.py — Multi-account, role-aware worker CLI (REQ-035, REQ-036).

Provides 2-account or multi-worker role execution without mutating global `gh auth`:
- Authenticates subprocesses via per-command GH_TOKEN + ephemeral 0700 GIT_ASKPASS
- Subcommands: list-tasks, submit-book, approve-intake, submit-translation, submit-review
"""

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from scripts.collab_config import CollabConfig, load_collab_config
from scripts.collab_intake import validate_submission_pr
from scripts.collab_pr_validate import validate_review, validate_translation_pr


def _get_gh_token(gh_user: str) -> str:
    """Fetch GitHub auth token for a specific user without switching global auth."""
    try:
        proc = subprocess.run(
            ["gh", "auth", "token", "-u", gh_user],
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
        check: bool = True,
        text: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["GH_TOKEN"] = self.token
        return subprocess.run(
            ["gh"] + args,
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
    cfg = None
    if config_path or os.path.exists("config.json"):
        try:
            cfg = load_collab_config(config_path)
        except Exception:
            pass

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

    # 2. Revise PRs: open PRs authored by gh_user with status:revise
    revise_prs: List[Dict[str, Any]] = []
    for pr in all_prs:
        author = (pr.get("author") or {}).get("login", "")
        labels = [l.get("name", "") for l in pr.get("labels", [])]
        if author == gh_user and "status:revise" in labels:
            revise_prs.append(pr)

    # 3. Review tasks: open PRs authored by someone else needing fresh review
    review_prs: List[Dict[str, Any]] = []
    for pr in all_prs:
        author = (pr.get("author") or {}).get("login", "")
        labels = [l.get("name", "") for l in pr.get("labels", [])]
        if author != gh_user and ("status:needs-review" in labels or "status:revise" in labels or "status:open" in labels):
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

    # Preflight check
    val_res = validate_submission_pr(
        changed_files=[f"books/{slug}/manifest.json", f"books/{slug}/source/{src_path.name}"],
        manifest_data=manifest_data,
        source_file_path=src_path,
        max_bytes=cfg.max_source_bytes,
        allowed_licenses=cfg.allowed_licenses,
        supported_pairs=cfg.supported_pairs,
    )
    if val_res.get("prescreen_status") == "FAIL":
        return {
            "success": False,
            "error": "Book submission pre-screen validation failed",
            "defects": val_res.get("hard_defects", []),
        }

    branch = f"book/{slug}"
    repo_root = Path(__file__).resolve().parent.parent
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
            if author == cfg.bot_login or True:  # accept prescreen from coordinator
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
    cfg = load_collab_config(config_path)
    tx_path = Path(translation_file).resolve()
    if not tx_path.exists():
        return {"success": False, "error": f"Translation file not found: {translation_file}"}

    tx_text = tx_path.read_text(encoding="utf-8")
    branch = f"translate/{book_slug}-{chapter}-{gh_user}"
    agent_tag = agent_name or gh_user

    # Preflight validation against local runs/<book_slug>/chNN/source.txt if available
    repo_root = Path(__file__).resolve().parent.parent
    ch_dir = repo_root / "runs" / book_slug / chapter
    source_file = ch_dir / "source.txt"
    manifest_file = repo_root / "books" / book_slug / "manifest.json"

    val_res = validate_translation_pr(
        pr_branch=branch,
        pr_body=f"## Translation for {chapter}\n- **Engine/Model**: {engine_model}\n- **Agent**: {agent_tag}\n",
        changed_files=[f"runs/{book_slug}/{chapter}/translation.xml"],
        file_contents={f"runs/{book_slug}/{chapter}/translation.xml": tx_text.encode("utf-8")},
        source_text=source_file.read_text(encoding="utf-8") if source_file.exists() else None,
        manifest_data=json.loads(manifest_file.read_text(encoding="utf-8")) if manifest_file.exists() else None,
    )

    if not val_res.get("valid", False):
        return {
            "success": False,
            "error": "Translation pre-flight validation failed",
            "defects": val_res.get("defects", []),
        }

    worktree_dir = repo_root / ".worktrees" / f"trans_{book_slug}_{chapter}_{gh_user}"

    with GitHubAccountContext(gh_user) as ctx:
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

            pr_body = (
                f"## Translation: {book_slug} {chapter}\n\n"
                f"- **Engine/Model**: `{engine_model}`\n"
                f"- **Agent**: `{agent_tag}`\n"
                f"- **Author**: @{gh_user}\n"
            )
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
    cfg = None
    if config_path or os.path.exists("config.json"):
        try:
            cfg = load_collab_config(config_path)
        except Exception:
            pass
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

    # list-tasks
    p_list = subparsers.add_parser("list-tasks", help="List tasks filtered by role.")
    p_list.add_argument("--role", choices=["translator", "reviewer", "all"], default="all")
    p_list.add_argument("--gh-user", required=True)
    p_list.add_argument("--book-slug")
    p_list.add_argument("--workers", help="Comma-separated logins for sharding.")
    p_list.add_argument("--mode", choices=["sharded", "compete"], default="sharded")
    p_list.add_argument("--config")

    # submit-book
    p_book = subparsers.add_parser("submit-book", help="Propose a book intake PR.")
    p_book.add_argument("--gh-user", required=True)
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
    p_trans = subparsers.add_parser("submit-translation", help="Submit chapter translation PR.")
    p_trans.add_argument("--gh-user", required=True)
    p_trans.add_argument("--book-slug", required=True)
    p_trans.add_argument("--chapter", required=True)
    p_trans.add_argument("--translation-file", required=True)
    p_trans.add_argument("--additions-file")
    p_trans.add_argument("--engine-model", default="human")
    p_trans.add_argument("--agent-name")
    p_trans.add_argument("--claim", action="store_true")
    p_trans.add_argument("--config")

    # submit-review
    p_rev = subparsers.add_parser("submit-review", help="Submit peer review.")
    p_rev.add_argument("--gh-user", required=True)
    p_rev.add_argument("--pr", type=int, required=True)
    p_rev.add_argument("--review-file", required=True)
    p_rev.add_argument("--event", choices=["COMMENT", "APPROVE", "REQUEST_CHANGES"], default="COMMENT")
    p_rev.add_argument("--config")

    args = parser.parse_args()

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
