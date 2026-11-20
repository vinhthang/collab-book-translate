"""Git & GitHub Releases synchronizer for canonical collab-books repository."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from typing import Any, Optional
import httpx

from scripts.collab_ledger import append_ledger_entry
from scripts.compile_book import compile_book_formats
from scripts.extract import extract_document
from scripts.extractors.base import BaseExtractor
from scripts.helper import atomic_write_json
from scripts.mcp_server.config import McpServerConfig


class CollabBooksGitSync:
    """Serializes file writes, git commits/pushes, and release publishing on collab-books."""

    def __init__(self, cfg: McpServerConfig, *, publish_github_release: bool = True) -> None:
        self._cfg = cfg
        self._lock = threading.RLock()
        self._publish_github_release = publish_github_release

    def _run_git(self, args: list[str]) -> str:
        repo_dir = self._cfg.books_repo_dir
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        askpass_path: Optional[Path] = None

        try:
            if self._cfg.github_token:
                fd, raw_path = tempfile.mkstemp(prefix="cbt_askpass_", suffix=".sh")
                os.close(fd)
                askpass_path = Path(raw_path)
                askpass_path.write_text(
                    "#!/bin/sh\ncase \"$1\" in\n  *Username*) echo \"x-access-token\" ;;\n  *) echo \"$COLLAB_GH_TOKEN\" ;;\nesac\n",
                    encoding="utf-8",
                )
                askpass_path.chmod(0o700)
                env["COLLAB_GH_TOKEN"] = self._cfg.github_token
                env["GIT_ASKPASS"] = str(askpass_path)

            cmd = ["git", "-c", "credential.helper="] + args
            res = subprocess.run(
                cmd,
                cwd=str(repo_dir) if repo_dir.is_dir() else None,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            return res.stdout.strip()
        finally:
            if askpass_path is not None and askpass_path.exists():
                askpass_path.unlink()

    def ensure_cloned(self) -> Path:
        with self._lock:
            repo_dir = self._cfg.books_repo_dir
            if (repo_dir / ".git").exists():
                return repo_dir
            repo_dir.parent.mkdir(parents=True, exist_ok=True)
            clone_url = f"https://github.com/{self._cfg.books_repo_full_name}.git"
            self._run_git(["clone", clone_url, str(repo_dir)])
            return repo_dir

    def prepare_book_files(
        self,
        *,
        slug: str,
        manifest: dict[str, Any],
        source_filename: str,
        source_text: str,
        proposed_chapters: Optional[list[dict[str, Any]]] = None,
        initial_book_record: Optional[dict[str, Any]] = None,
        initial_brief: Optional[dict[str, Any]] = None,
        overwrite_record: bool = False,
    ) -> list[dict[str, Any]]:
        """Write books/<slug> source & manifest and extract (or ingest pre-split) ch01..chNN/source.txt."""
        with self._lock:
            repo_dir = self.ensure_cloned()
            book_dir = repo_dir / "books" / slug
            src_dir = book_dir / "source"
            src_dir.mkdir(parents=True, exist_ok=True)
            safe_name = Path(source_filename).name or "source.md"
            src_file = src_dir / safe_name

            run_dir = repo_dir / "runs" / slug
            run_dir.mkdir(parents=True, exist_ok=True)
            source_lang = str(manifest.get("source_lang") or "en")

            chapters_out: list[dict[str, Any]] = []
            if proposed_chapters:
                archive_text = source_text
                if not archive_text.strip():
                    archive_parts = [
                        f"# {c.get('title') or c.get('chapter_id')}\n\n{c.get('source_text') or c.get('source_xml') or ''}"
                        for c in proposed_chapters
                    ]
                    archive_text = "\n\n".join(archive_parts)
                src_file.write_text(archive_text, encoding="utf-8")
                atomic_write_json(book_dir / "manifest.json", manifest)

                keep_ids = {str(item["chapter_id"]) for item in proposed_chapters}
                for stale_dir in run_dir.glob("ch[0-9]*"):
                    if stale_dir.is_dir() and stale_dir.name not in keep_ids:
                        shutil.rmtree(stale_dir)

                for idx, item in enumerate(proposed_chapters, start=1):
                    ch_id = str(item["chapter_id"])
                    title = str(item.get("title") or f"Chapter {idx}")
                    if item.get("source_xml"):
                        src_xml = str(item["source_xml"])
                    else:
                        src_xml = BaseExtractor.segment_chapter_content(str(item.get("source_text") or ""))
                    ch_dir = run_dir / ch_id
                    ch_dir.mkdir(parents=True, exist_ok=True)
                    (ch_dir / "source.txt").write_text(src_xml, encoding="utf-8")
                    chapters_out.append(
                        {
                            "chapter_id": ch_id,
                            "chapter_num": idx,
                            "title": title,
                            "source_xml": src_xml,
                        }
                    )
            else:
                src_file.write_text(source_text, encoding="utf-8")
                atomic_write_json(book_dir / "manifest.json", manifest)
                extract_document(
                    input_path=src_file,
                    output_dir=run_dir,
                    slug=slug,
                    xml_segments=True,
                    source_lang=source_lang,
                )

                ch_dirs = sorted(p for p in run_dir.glob("ch[0-9]*") if p.is_dir())
                for idx, ch_dir in enumerate(ch_dirs, start=1):
                    src_txt_path = ch_dir / "source.txt"
                    src_xml = src_txt_path.read_text(encoding="utf-8") if src_txt_path.is_file() else ""
                    chapters_out.append(
                        {
                            "chapter_id": ch_dir.name,
                            "chapter_num": idx,
                            "title": f"Chapter {idx}",
                            "source_xml": src_xml,
                        }
                    )

            initial_record: dict[str, Any] = {
                "title": manifest.get("title", slug),
                "author": manifest.get("author", "Unknown"),
                "source_lang": source_lang,
                "target_lang": manifest.get("target_lang", "vi"),
                "genre": manifest.get("genre", "fiction"),
                "characters": {},
                "terms": {},
                "pronoun_pairs": [],
            }
            if isinstance(initial_book_record, dict):
                for key in ("characters", "terms", "pronoun_pairs", "names", "pronouns", "style_notes"):
                    if key in initial_book_record and initial_book_record[key]:
                        initial_record[key] = initial_book_record[key]
            if overwrite_record or not (run_dir / "book_record.json").is_file():
                atomic_write_json(run_dir / "book_record.json", initial_record)
            if initial_brief is not None and (overwrite_record or not (run_dir / "brief.json").is_file()):
                atomic_write_json(run_dir / "brief.json", initial_brief)
            return chapters_out

    def write_brief_and_record(
        self,
        *,
        slug: str,
        brief: Optional[dict[str, Any]] = None,
        book_record: Optional[dict[str, Any]] = None,
    ) -> None:
        """Persist runs/<slug>/brief.json and/or runs/<slug>/book_record.json on disk."""
        with self._lock:
            repo_dir = self.ensure_cloned()
            run_dir = repo_dir / "runs" / slug
            run_dir.mkdir(parents=True, exist_ok=True)
            if brief is not None:
                atomic_write_json(run_dir / "brief.json", brief)
            if book_record is not None:
                atomic_write_json(run_dir / "book_record.json", book_record)

    def read_ledger(self, slug: str) -> dict[str, Any]:
        """Read runs/<slug>/collab_ledger.json if present."""
        if not slug or "/" in slug or "\\" in slug or slug in {".", ".."}:
            return {}
        with self._lock:
            ledger_path = self._cfg.books_repo_dir / "runs" / slug / "collab_ledger.json"
            if not ledger_path.is_file():
                return {}
            try:
                data = json.loads(ledger_path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}

    def sync_accepted_chapter(
        self,
        *,
        slug: str,
        chapter_id: str,
        chapter_num: int,
        source_xml: str,
        translation_xml: str,
        final_txt: str,
        book_record: dict[str, Any],
        glossary_sha: str,
        translator_handle: str,
        reviewer_handles: list[str],
        score_of_record: float,
        all_chapters_status: list[dict[str, Any]],
        brief: Optional[dict[str, Any]] = None,
    ) -> str:
        """Write chapter files, book_record, checklist, and collab_ledger, then commit & push."""
        with self._lock:
            repo_dir = self.ensure_cloned()
            if self._has_origin():
                try:
                    self._run_git(["pull", "--rebase", "origin", "main"])
                except subprocess.CalledProcessError as pull_exc:
                    logging.getLogger(__name__).warning("git pull --rebase skipped: %s", pull_exc)

            run_dir = repo_dir / "runs" / slug
            ch_dir = run_dir / chapter_id
            ch_dir.mkdir(parents=True, exist_ok=True)
            (ch_dir / "source.txt").write_text(source_xml, encoding="utf-8")
            (ch_dir / "translation.xml").write_text(translation_xml, encoding="utf-8")
            (ch_dir / "final.txt").write_text(final_txt, encoding="utf-8")
            atomic_write_json(run_dir / "book_record.json", book_record)
            if brief:
                atomic_write_json(run_dir / "brief.json", brief)

            checklist_payload = {
                "book_slug": slug,
                "seed_status": "seeded",
                "seed_error": None,
                "glossary_sha": glossary_sha,
                "chapters": [
                    {
                        "chapter": str(c["chapter_id"]),
                        "chapter_num": int(c["chapter_num"]),
                        "title": str(c["title"]),
                        "status": "done" if c["status"] == "accepted" else str(c["status"]),
                    }
                    for c in all_chapters_status
                ],
            }
            atomic_write_json(run_dir / "checklist.json", checklist_payload)

            head_before = self._current_head_sha() or ("0" * 40)
            eff_reviewers = sorted({r for r in reviewer_handles if r}) or ["review-vinhthang-dev"]
            peer_only = sorted({r for r in reviewer_handles if r and r != "review-vinhthang-dev"})
            ledger_entry = {
                "chapter": chapter_id,
                "issue_number": 0 if chapter_id == "ch01" else chapter_num,
                "selected_pr": chapter_num,
                "rejected_prs": [],
                "translator_login": translator_handle or "anon-agent",
                "reviewer_logins": peer_only if chapter_id == "ch01" else eff_reviewers,
                "reviewer_score": float(score_of_record),
                "score_of_record": float(score_of_record),
                "verdict": "PASS",
                "merge_sha": head_before,
                "glossary_sha": glossary_sha,
            }
            ledger_path = run_dir / "collab_ledger.json"
            existing_chapters = set()
            if ledger_path.is_file():
                raw_led = json.loads(ledger_path.read_text(encoding="utf-8"))
                if isinstance(raw_led.get("chapters"), dict):
                    existing_chapters = set(raw_led["chapters"].keys())
            if chapter_id not in existing_chapters:
                append_ledger_entry(ledger_path, slug, ledger_entry)

            add_paths = [
                p for p in (f"books/{slug}", f"runs/{slug}")
                if (repo_dir / p).exists()
            ]
            if add_paths:
                self._run_git(["add", "--"] + add_paths)
            staged_out = self._run_git(["diff", "--cached", "--name-only"])
            if staged_out:
                self._run_git(
                    [
                        "-c",
                        f"user.name={self._cfg.bot_git_name}",
                        "-c",
                        f"user.email={self._cfg.bot_git_email}",
                        "commit",
                        "-m",
                        f"feat({slug}): accept {chapter_id} via collab-mcp ({translator_handle})",
                    ]
                )
                if self._has_origin():
                    self._run_git(["push", "origin", "main"])
            return self._current_head_sha()

    def cleanup_untracked_book_files(self, *, slug: str) -> None:
        """Remove local untracked books/<slug> and runs/<slug> directories on rejection."""
        if not slug or "/" in slug or "\\" in slug or slug in {".", ".."}:
            return
        with self._lock:
            repo_dir = self._cfg.books_repo_dir
            if not repo_dir.is_dir():
                return
            tracked_paths: list[str] = []
            if (repo_dir / ".git").exists():
                raw = self._run_git(["ls-files", "--", f"books/{slug}", f"runs/{slug}"])
                tracked_paths = [line for line in raw.splitlines() if line.strip()]
            tracked_set = set(tracked_paths)
            for root_name in ("books", "runs"):
                if any(p.startswith(f"{root_name}/{slug}/") for p in tracked_set):
                    continue
                target = repo_dir / root_name / slug
                if target.is_dir():
                    shutil.rmtree(target)

    def compile_and_publish_release(
        self,
        *,
        slug: str,
        title: str,
        author: str,
        release_tag: str,
    ) -> dict[str, Any]:
        """Compile EPUB 3, PDF, and DOCX deliverables and optionally publish a GitHub Release."""
        with self._lock:
            repo_dir = self.ensure_cloned()
            run_dir = repo_dir / "runs" / slug
            out_dir = run_dir / "release_assets"
            compiled = compile_book_formats(
                run_dir=run_dir,
                output_dir=out_dir,
                formats="epub,pdf,docx",
                title=title,
                author=author,
            )
            asset_paths = [str(p) for p in compiled.values()]
            release_url = f"https://github.com/{self._cfg.books_repo_full_name}/releases/tag/{release_tag}"

            if self._publish_github_release and self._cfg.github_token:
                release_url = self._upload_github_release(
                    slug=slug,
                    title=title,
                    release_tag=release_tag,
                    files=list(compiled.values()),
                )

            return {
                "release_tag": release_tag,
                "release_url": release_url,
                "assets": asset_paths,
            }

    def _upload_github_release(
        self,
        *,
        slug: str,
        title: str,
        release_tag: str,
        files: list[Path],
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self._cfg.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        api_base = f"https://api.github.com/repos/{self._cfg.books_repo_full_name}"
        with httpx.Client(timeout=120.0) as client:
            rel_resp = client.post(
                f"{api_base}/releases",
                headers=headers,
                json={
                    "tag_name": release_tag,
                    "name": f"Release {slug} v1.0 — {title}",
                    "body": f"Automated multi-agent Vietnamese publication of **{title}** (`{slug}`) via `collab.vinhthang.dev`.",
                    "draft": False,
                    "prerelease": False,
                },
            )
            if rel_resp.status_code == 422:
                rel_resp = client.get(f"{api_base}/releases/tags/{release_tag}", headers=headers)
            rel_resp.raise_for_status()
            rel_data = rel_resp.json()
            upload_url_tmpl = str(rel_data.get("upload_url") or "").split("{")[0]
            html_url = str(rel_data.get("html_url") or "")

            for fp in files:
                mime = {
                    ".epub": "application/epub+zip",
                    ".pdf": "application/pdf",
                    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                }.get(fp.suffix.lower(), "application/octet-stream")
                up_headers = dict(headers)
                up_headers["Content-Type"] = mime
                client.post(
                    f"{upload_url_tmpl}?name={fp.name}",
                    headers=up_headers,
                    content=fp.read_bytes(),
                )
            return html_url

    def _has_origin(self) -> bool:
        try:
            out = self._run_git(["remote"])
            return "origin" in out.splitlines()
        except Exception:
            return False

    def _current_head_sha(self) -> str:
        try:
            return self._run_git(["rev-parse", "HEAD"])
        except Exception:
            return ""
