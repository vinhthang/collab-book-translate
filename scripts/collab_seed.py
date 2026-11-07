"""
scripts/collab_seed.py — Book ingestion, ch01 reference seed, glossary freeze, and parallel issue creation.

Implements REQ-COLLAB-003, AC-COLLAB-003-1, AC-COLLAB-003-2, and D2:
- Ingests merged books and extracts chapters
- Validates extracted segments and records failed chapters
- Authoritatively seeds ch01 (translated by bot, merged only on live PASS)
- Freezes glossary SHA and commits ledger entry
- Opens parallel tracking issues for ch02..chNN
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from typing import Any, Callable, Dict, List, Optional, Union

from scripts.collab_ledger import append_ledger_entry
from scripts.extract import extract_book
from scripts.helper import (
    atomic_write_json,
    parse_segments,
    resolve_pass_threshold,
    strip_segments_to_final,
    update_book_record_from_eval,
    validate_segment_parity,
)

logger = logging.getLogger(__name__)


def seed_merged_book(
    repo_root: Optional[Union[Path, str]] = None,
    book_slug: str = "",
    repo_full_name: Optional[str] = None,
    gh_user: Optional[str] = None,
    bot_git_name: Optional[str] = None,
    bot_git_email: Optional[str] = None,
    translate_fn: Optional[Callable[[str, Dict[str, Any], Dict[str, Any]], str]] = None,
    evaluate_fn: Optional[Callable[[str, str, Dict[str, Any]], Dict[str, Any]]] = None,
    issue_creator_fn: Optional[Callable[[str, str, List[str]], Any]] = None,
    git_sha_fn: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    """Authoritatively seeds a newly merged book into the collab translation pipeline.

    Returns:
        {
            "seeded": bool,
            "book_slug": str,
            "glossary_sha": str,
            "ch01_score": float,
            "issues_created": list[dict],
            "failed_chapters": list[str],
            "reason": str (optional on failure)
        }
    """
    if None in (repo_root, repo_full_name, gh_user, bot_git_name, bot_git_email):
        from scripts.collab_config import load_collab_config
        cfg = load_collab_config()
        repo_root = repo_root or cfg.workspace_dir
        repo_full_name = repo_full_name or cfg.repo_full_name
        gh_user = gh_user or cfg.bot_login
        bot_git_name = bot_git_name or cfg.bot_git_name
        bot_git_email = bot_git_email or cfg.bot_git_email

    repo_root_path = Path(repo_root).resolve()
    books_dir = repo_root_path / "books" / book_slug
    manifest_path = books_dir / "manifest.json"
    source_dir = books_dir / "source"
    runs_dir = repo_root_path / "runs" / book_slug
    runs_dir.mkdir(parents=True, exist_ok=True)

    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found for book '{book_slug}': {manifest_path}")

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # 1. Extraction: if ch01/source.txt is missing, extract from books/<slug>/source/
    ch01_source_path = runs_dir / "ch01" / "source.txt"
    if not ch01_source_path.exists():
        source_files = [
            f for f in source_dir.iterdir()
            if f.is_file() and not f.name.startswith(".")
        ]
        if not source_files:
            raise FileNotFoundError(f"No source book file found in {source_dir}")
        source_file = source_files[0]
        extract_book(
            input_path=source_file,
            output_dir=runs_dir,
            slug=book_slug,
            source_lang=manifest.get("source_lang", "auto"),
        )

    # 2. Discover chapter directories
    chapter_dirs = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir() and re.match(r"^ch\d+", d.name)],
        key=lambda p: p.name,
    )

    checklist_path = runs_dir / "checklist.json"
    if not checklist_path.exists():
        initial_checklist = [{"chapter": d.name, "status": "pending"} for d in chapter_dirs]
        atomic_write_json(checklist_path, initial_checklist)

    # 3. Inspect all extracted chapters and detect 0-segment failures
    failed_chapters: List[str] = []
    valid_chapters: List[Path] = []
    for ch_d in chapter_dirs:
        ch_source = ch_d / "source.txt"
        if not ch_source.exists():
            failed_chapters.append(ch_d.name)
            continue
        try:
            src_text = ch_source.read_text(encoding="utf-8")
            segs = parse_segments(src_text)
            if not segs:
                failed_chapters.append(ch_d.name)
            else:
                valid_chapters.append(ch_d)
        except Exception as exc:
            logger.debug("Failed parsing segments for %s: %s", ch_d.name, exc)
            failed_chapters.append(ch_d.name)

    if failed_chapters:
        atomic_write_json(
            runs_dir / "seed_failures.json",
            {"book_slug": book_slug, "failed_chapters": failed_chapters},
        )

    ch01_dir = runs_dir / "ch01"
    if "ch01" in failed_chapters or not ch01_dir.exists():
        return {
            "seeded": False,
            "reason": "ch01 has 0 segments or failed extraction",
            "issues_created": [],
            "failed_chapters": failed_chapters,
        }

    # 4. Initialize / update book_record.json
    book_record_path = runs_dir / "book_record.json"
    if book_record_path.exists():
        with open(book_record_path, "r", encoding="utf-8") as f:
            book_record = json.load(f)
    else:
        book_record = {
            "book_slug": book_slug,
            "brief": {
                "title": manifest.get("title", ""),
                "author": manifest.get("author", ""),
                "genre": manifest.get("genre", "general"),
                "source_lang": manifest.get("source_lang", "en"),
                "target_lang": manifest.get("target_lang", "vi"),
            },
            "glossary": {},
            "characters": {},
        }
        atomic_write_json(book_record_path, book_record)

    # 5. Translate ch01
    if translate_fn is None or evaluate_fn is None:
        return {
            "seeded": False,
            "reason": "Live translate_fn and evaluate_fn callables are required for ch01 seeding (AC-COLLAB-003-2)",
            "issues_created": [],
            "failed_chapters": failed_chapters,
        }

    ch01_source_xml = (ch01_dir / "source.txt").read_text(encoding="utf-8")
    ch01_trans_xml = translate_fn(ch01_source_xml, book_record, manifest)
    (ch01_dir / "translation.xml").write_text(ch01_trans_xml, encoding="utf-8")

    # Segment parity validation
    is_parity, parity_errors = validate_segment_parity(ch01_source_xml, ch01_trans_xml)
    if not is_parity:
        return {
            "seeded": False,
            "reason": f"ch01 segment parity validation failed: {parity_errors}",
            "issues_created": [],
            "failed_chapters": failed_chapters,
        }

    # ch01 Live Evaluation
    eval_res = evaluate_fn(ch01_source_xml, ch01_trans_xml, book_record)

    # Enforce D2 & AC-COLLAB-003-2
    crit_issues = [
        iss for iss in eval_res.get("issues", [])
        if str(iss.get("severity", "")).lower() == "critical"
    ]
    if eval_res.get("critical_issues"):
        crit_issues.extend(eval_res["critical_issues"])

    threshold = resolve_pass_threshold(book_record=book_record)
    score = float(eval_res.get("weighted_total", 0.0))

    if (
        eval_res.get("fallback_unverified") is True
        or str(eval_res.get("verdict", "")).upper() != "PASS"
        or len(crit_issues) > 0
        or score < threshold
    ):
        return {
            "seeded": False,
            "reason": "ch01 seed evaluation did not pass live check",
            "issues_created": [],
            "failed_chapters": failed_chapters,
        }

    # 6. ch01 Promotion and Terminology Binding
    final_txt = strip_segments_to_final(ch01_trans_xml)
    (ch01_dir / "final.txt").write_text(final_txt, encoding="utf-8")

    update_book_record_from_eval(
        book_record_path=book_record_path,
        eval_data=eval_res,
        chapter_dir=ch01_dir,
        bind=True,
        chapter_id="ch01",
    )

    # Mark ch01 done in checklist.json
    if checklist_path.exists():
        with open(checklist_path, "r", encoding="utf-8") as f:
            chk = json.load(f)
        if isinstance(chk, list):
            for entry in chk:
                if isinstance(entry, dict) and entry.get("chapter") == "ch01":
                    entry["status"] = "done"
        elif isinstance(chk, dict):
            if "chapters" in chk and isinstance(chk["chapters"], dict):
                if "ch01" in chk["chapters"] and isinstance(chk["chapters"]["ch01"], dict):
                    chk["chapters"]["ch01"]["status"] = "done"
                else:
                    chk["chapters"]["ch01"] = {"status": "done"}
            elif "ch01" in chk and isinstance(chk["ch01"], dict):
                chk["ch01"]["status"] = "done"
            else:
                chk.setdefault("chapters", {})["ch01"] = {"status": "done"}
        atomic_write_json(checklist_path, chk)

    # Obtain glossary SHA
    if git_sha_fn is not None:
        glossary_sha = git_sha_fn()
    else:
        try:
            is_git = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=repo_root_path,
                capture_output=True,
                text=True,
            )
            if is_git.returncode == 0 and is_git.stdout.strip() == "true":
                subprocess.run(
                    ["git", "add", f"runs/{book_slug}"],
                    cwd=repo_root_path,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                commit_msg = f"seed({book_slug}): initialize ch01 reference and book_record"
                subprocess.run(
                    ["git", "-c", f"user.name={bot_git_name}", "-c", f"user.email={bot_git_email}", "commit", "-m", commit_msg],
                    cwd=repo_root_path,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_root_path,
                capture_output=True,
                text=True,
                check=True,
            )
            glossary_sha = res.stdout.strip()
        except Exception as exc:
            logger.debug("Failed getting git HEAD sha: %s", exc)
            glossary_sha = "0000000000000000000000000000000000000000"

    # Persist glossary_sha inside book_record.json
    try:
        with open(book_record_path, "r", encoding="utf-8") as f:
            rec = json.load(f)
    except Exception as exc:
        logger.debug("Failed reading book_record_path: %s", exc)
        rec = book_record
    rec["glossary_sha"] = glossary_sha
    atomic_write_json(book_record_path, rec)

    # Append ch01 entry to audit ledger
    ledger_entry = {
        "chapter": "ch01",
        "issue_number": 0,
        "selected_pr": 0,
        "rejected_prs": [],
        "translator_login": gh_user,
        "reviewer_logins": [],
        "reviewer_score": score,
        "score_of_record": score,
        "verdict": "PASS",
        "merge_sha": glossary_sha,
        "glossary_sha": glossary_sha,
    }
    append_ledger_entry(runs_dir / "collab_ledger.json", book_slug, ledger_entry)

    # Post-seed git commit & push if origin is configured (R3-P1-8-seed)
    if git_sha_fn is None:
        try:
            is_git = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=repo_root_path,
                capture_output=True,
                text=True,
            )
            if is_git.returncode == 0 and is_git.stdout.strip() == "true":
                subprocess.run(
                    [
                        "git",
                        "add",
                        f"runs/{book_slug}/book_record.json",
                        f"runs/{book_slug}/collab_ledger.json",
                    ],
                    cwd=repo_root_path,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                subprocess.run(
                    [
                        "git",
                        "-c",
                        f"user.name={bot_git_name}",
                        "-c",
                        f"user.email={bot_git_email}",
                        "commit",
                        "--amend",
                        "--no-edit",
                    ],
                    cwd=repo_root_path,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                remote_check = subprocess.run(
                    ["git", "remote"],
                    cwd=repo_root_path,
                    capture_output=True,
                    text=True,
                )
                if remote_check.returncode == 0 and "origin" in remote_check.stdout.split():
                    subprocess.run(
                        ["git", "push", "origin", "HEAD"],
                        cwd=repo_root_path,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
        except Exception as exc:
            logger.debug("Git commit/push hook failed: %s", exc)

    # 7. Create tracking issues for ch02..chNN
    issues_created: List[Dict[str, Any]] = []
    remaining_chapters = [d for d in valid_chapters if d.name != "ch01"]

    src_lang = manifest.get("source_lang", "en")
    tgt_lang = manifest.get("target_lang", "vi")

    for ch_d in remaining_chapters:
        ch_name = ch_d.name
        src_text = (ch_d / "source.txt").read_text(encoding="utf-8")
        segs = parse_segments(src_text)
        seg_first = segs[0]["id"]
        seg_last = segs[-1]["id"]
        chunk_id = f"{book_slug}-{ch_name}"

        title = f"Translate [{ch_name}] {chunk_id}: {seg_first}..{seg_last}"
        body = (
            f"## Translation Task: {chunk_id}\n\n"
            f"- **Book:** {book_slug}\n"
            f"- **Chapter:** {ch_name}\n"
            f"- **Chunk ID:** {chunk_id}\n"
            f"- **Source range:** {seg_first}..{seg_last}\n"
            f"- **Source language:** {src_lang}\n"
            f"- **Target language:** {tgt_lang}\n"
            f"- **Frozen Glossary SHA:** {glossary_sha}\n\n"
            f"### Instructions\n"
            f"1. Post `/claim` as a courtesy if you plan to translate this chapter.\n"
            f"2. Create branch `translate/{chunk_id}-<your_github_login>`.\n"
            f"3. Translate into `runs/{book_slug}/{ch_name}/translation.xml` keeping `<original>` byte-identical.\n"
            f"4. Open a Pull Request referencing this issue.\n"
        )
        labels = ["status:open", f"lang:{src_lang}-{tgt_lang}", f"book:{book_slug}"]

        if issue_creator_fn is not None:
            iss_info = issue_creator_fn(title, body, labels)
        else:
            if os.environ.get("PYTEST_CURRENT_TEST"):
                raise RuntimeError("Refusing to invoke live 'gh issue create' inside pytest without issue_creator_fn")
            try:
                cmd = [
                    "gh", "issue", "create",
                    "--repo", repo_full_name,
                    "--title", title,
                    "--body", body,
                    "--label", "status:open",
                    "--label", f"lang:{src_lang}-{tgt_lang}",
                    "--label", f"book:{book_slug}",
                ]
                env = os.environ.copy()
                if "GH_TOKEN" not in os.environ and gh_user:
                    token_proc = subprocess.run(
                        ["gh", "auth", "token", "-u", gh_user],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if token_proc.returncode == 0 and token_proc.stdout.strip():
                        env["GH_TOKEN"] = token_proc.stdout.strip()
                proc = subprocess.run(
                    cmd, env=env, cwd=repo_root_path, capture_output=True, text=True, check=True
                )
                iss_info = proc.stdout.strip()
            except Exception as e:
                logger.warning("Failed creating issue for %s: %s", ch_name, e)
                iss_info = f"error: {e}"

        issues_created.append({
            "chapter": ch_name,
            "chunk_id": chunk_id,
            "title": title,
            "range": f"{seg_first}..{seg_last}",
            "issue_ref": iss_info,
        })

    return {
        "seeded": True,
        "book_slug": book_slug,
        "glossary_sha": glossary_sha,
        "ch01_score": score,
        "issues_created": issues_created,
        "failed_chapters": failed_chapters,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Seed a newly merged book into the collab translation pipeline.")
    parser.add_argument("--config", default=None, help="Path to config.json")
    parser.add_argument("--repo-root", default=None, help="Root of git repository")
    parser.add_argument("--book-slug", required=True, help="Slug of the book to seed")
    parser.add_argument("--repo", default=None, help="Full repository name")
    parser.add_argument("--gh-user", default=None, help="GitHub user for auth token and attribution")
    parser.add_argument("--ch01-translation-file", default=None, help="Path to pre-translated ch01 translation XML")
    args = parser.parse_args(argv)

    from scripts.collab_config import load_collab_config
    cfg = load_collab_config(args.config)

    repo_root = Path(args.repo_root or cfg.workspace_dir).resolve()
    repo_full_name = args.repo or cfg.repo_full_name
    gh_user = args.gh_user or cfg.bot_login
    bot_git_name = cfg.bot_git_name
    bot_git_email = cfg.bot_git_email
    evaluation_engine = cfg.evaluation_engine

    trans_fn = None
    eval_fn = None
    if args.ch01_translation_file:
        trans_path = Path(args.ch01_translation_file).resolve()
        def file_trans_fn(src_xml: str, rec: Dict[str, Any], man: Dict[str, Any]) -> str:
            return trans_path.read_text(encoding="utf-8")
        trans_fn = file_trans_fn

        def default_eval_fn(source_xml: str, trans_xml: str, book_record: Dict[str, Any]) -> Dict[str, Any]:
            from scripts.evaluate import run_evaluation
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
                    engine=evaluation_engine,
                )
        eval_fn = default_eval_fn

    res = seed_merged_book(
        repo_root=repo_root,
        book_slug=args.book_slug,
        repo_full_name=repo_full_name,
        gh_user=gh_user,
        bot_git_name=bot_git_name,
        bot_git_email=bot_git_email,
        translate_fn=trans_fn,
        evaluate_fn=eval_fn,
    )
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res.get("seeded") else 1


if __name__ == "__main__":
    raise SystemExit(main())
