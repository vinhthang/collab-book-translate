"""
scripts/collab_seed.py — Opening-sample book_record initialization, bot ch01 seed PR,
immutable 64-char NFC SHA-256 glossary_sha, quarantine, and parallel chapter issue creation.

Implements REQ-020 (AC-020-1, AC-020-2) and REQ-028 (AC-028-1):
- Extracts chapters into runs/<slug>/chNN/source.txt and verifies bounds (1..2,000 segs, <= 2.5 MiB)
- Scans source.txt pre-LLM across all 4 REQ-023 prompt-injection classes
- Writes envelope checklist.json (seed_status: seeding | seeded | blocked_failed) and supports --retry-blocked
- Initializes book_record.json over min(3, N) opening chapters with live fiction pronouns
- Opens/reuses/recovers bot-authored ch01 seed PR (Issue: seed, Glossary SHA: seed), injecting <hanviet> for zh->vi
- Evaluates live (keeping transient errors & fallback_unverified in "seeding", closing & quarantining on live FAIL)
- Posts bound-glossary/names/pronouns audit comment, merges ch01 PR (selected_pr > 0), computes 64-char SHA-256 glossary_sha
- Creates-if-missing ch02..chNN issues BEFORE setting seed_status == "seeded"
"""

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.collab_ledger import append_ledger_entry, read_ledger
from scripts.collab_pr_validate import (
    HANVIET_TAG_RE,
    ORIG_TAG_RE,
    SEGMENT_BLOCK_RE,
    detect_prompt_injection,
    validate_translation_pr,
)
from scripts.collab_verdict import (
    CANONICAL_DIMENSIONS,
    evaluate_pr_from_reviews,
    normalize_evaluation_result,
)
from scripts.evaluate import enforce_language_detection
from scripts.evaluators.anti_convert import check_anti_convert_issues
from scripts.extract import extract_book
from scripts.helper import (
    atomic_write_json,
    parse_segments,
    resolve_pass_threshold,
    strip_segments_to_final,
    sync_book_record_views,
    update_book_record_from_eval,
)
from scripts.orchestrate_run import (
    TECHNICAL_SEED_GLOSSARY_V1,
    _is_pair_attested_in_source,
    assert_fiction_pronouns_populated,
    ensure_technical_seed_glossary,
    initialize_book_record,
)
from scripts.utils.hanviet import transcribe_to_hanviet

logger = logging.getLogger(__name__)

MAX_CHAPTER_SOURCE_BYTES: int = 2_621_440  # 2.5 MiB per REQ-017 / REQ-020
MAX_CHAPTER_SEGMENTS: int = 2_000
CHAPTER_DIR_RE: re.Pattern = re.compile(r"^ch(\d{2,})$")
DIALOGUE_QUOTE_RE: re.Pattern = re.compile(r'[“”「」]|\n—')


def _nfc_str(val: Any) -> str:
    return unicodedata.normalize("NFC", str(val or "").strip())


def _normalize_pronouns_list(book_record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Read pronouns list (or legacy character_pronouns alias on pre-v2.0 pilot books without mutating disk)."""
    raw = book_record.get("pronouns")
    if not raw and isinstance(book_record.get("book_record"), dict):
        raw = book_record["book_record"].get("pronouns")
    if not raw:
        raw = book_record.get("character_pronouns")

    normalized: List[Dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            pair = item.get("pair")
            if isinstance(pair, list) and len(pair) == 2:
                normalized.append({
                    "pair": [_nfc_str(pair[0]), _nfc_str(pair[1])],
                    "a_to_b": _nfc_str(item.get("a_to_b", "")),
                    "b_to_a": _nfc_str(item.get("b_to_a", "")),
                    "basis": _nfc_str(item.get("basis", "")),
                    "provisional": bool(item.get("provisional", False)),
                })
    elif isinstance(raw, dict):
        for k, v in raw.items():
            parts = [p.strip() for p in re.split(r"<->|->|/|,|\|", str(k)) if p.strip()]
            pair_vals = [_nfc_str(parts[0]), _nfc_str(parts[1])] if len(parts) >= 2 else [_nfc_str(k), ""]
            if isinstance(v, dict):
                normalized.append({
                    "pair": pair_vals,
                    "a_to_b": _nfc_str(v.get("a_to_b", "")),
                    "b_to_a": _nfc_str(v.get("b_to_a", "")),
                    "basis": _nfc_str(v.get("basis", "")),
                    "provisional": bool(v.get("provisional", False)),
                })
            else:
                normalized.append({
                    "pair": pair_vals,
                    "a_to_b": _nfc_str(v),
                    "b_to_a": "",
                    "basis": "",
                    "provisional": False,
                })
    return normalized


def sync_collab_book_record(book_record: Dict[str, Any]) -> Dict[str, Any]:
    """Synchronize flat glossary dict, terms list (match='word', case_sensitive=bool), names, and pronouns."""
    raw_glossary = book_record.get("glossary")
    flat_glossary: Dict[str, str] = {}
    if isinstance(raw_glossary, dict):
        for k, v in raw_glossary.items():
            if k == "terms" and isinstance(v, dict):
                for tk, tv in v.items():
                    if tk and tv:
                        flat_glossary[str(tk).strip()] = str(tv).strip()
            elif isinstance(v, str) and k and v.strip():
                flat_glossary[str(k).strip()] = v.strip()
            elif isinstance(v, dict):
                tgt = v.get("target") or v.get("target_vi") or v.get("rendering") or ""
                if k and str(tgt).strip():
                    flat_glossary[str(k).strip()] = str(tgt).strip()

    sync_book_record_views(book_record, updated_terms=flat_glossary if flat_glossary else None)

    terms_list = book_record.get("terms")
    norm_terms: List[Dict[str, Any]] = []
    if isinstance(terms_list, list):
        for item in terms_list:
            if not isinstance(item, dict):
                continue
            src = str(item.get("source") or "").strip()
            tgt = str(item.get("target") or "").strip()
            if not src or not tgt:
                continue
            entry = dict(item)
            entry["source"] = src
            entry["target"] = tgt
            entry.setdefault("match", "word")
            entry["case_sensitive"] = bool(entry.get("case_sensitive", False))
            norm_terms.append(entry)
            flat_glossary[src] = tgt

    book_record["terms"] = norm_terms
    if isinstance(book_record.get("book_record"), dict):
        book_record["book_record"]["terms"] = [dict(t) for t in norm_terms]
    book_record["glossary"] = flat_glossary

    # Normalize names with also_referred_to_as list
    raw_names = book_record.get("names")
    norm_names: List[Dict[str, Any]] = []
    if isinstance(raw_names, list):
        for n in raw_names:
            if not isinstance(n, dict):
                continue
            src = str(n.get("source") or "").strip()
            tgt = str(n.get("target") or "").strip()
            if not src or not tgt:
                continue
            aliases = n.get("also_referred_to_as")
            if not isinstance(aliases, list):
                aliases = []
            entry = dict(n)
            entry["source"] = src
            entry["target"] = tgt
            entry["also_referred_to_as"] = [str(a).strip() for a in aliases if str(a).strip()]
            norm_names.append(entry)
    book_record["names"] = norm_names

    if "pronouns" not in book_record or not isinstance(book_record.get("pronouns"), list):
        book_record["pronouns"] = []

    return book_record


def compute_seed_glossary_sha(book_record: Dict[str, Any]) -> str:
    """Compute immutable 64-char lowercase SHA-256 digest of NFC-canonical seed glossary/names/pronouns (REQ-020)."""
    raw_glossary = book_record.get("glossary") or {}
    norm_glossary: Dict[str, str] = {}
    if isinstance(raw_glossary, dict):
        for k, v in raw_glossary.items():
            if isinstance(v, str):
                norm_glossary[_nfc_str(k)] = _nfc_str(v)
            elif isinstance(v, dict):
                tgt = v.get("target") or v.get("target_vi") or v.get("rendering") or ""
                norm_glossary[_nfc_str(k)] = _nfc_str(tgt)

    raw_names = book_record.get("names") or []
    names_items: List[Dict[str, Any]] = []
    if isinstance(raw_names, list):
        for n in raw_names:
            if not isinstance(n, dict):
                continue
            src = _nfc_str(n.get("source", ""))
            tgt = _nfc_str(n.get("target", ""))
            if not src and not tgt:
                continue
            aliases = sorted(
                {_nfc_str(a) for a in (n.get("also_referred_to_as") or []) if _nfc_str(a)}
            )
            names_items.append({
                "source": src,
                "target": tgt,
                "also_referred_to_as": aliases,
            })
    sorted_names = sorted(names_items, key=lambda x: (x["source"], x["target"]))

    pronouns_items = _normalize_pronouns_list(book_record)
    sorted_pronouns = sorted(pronouns_items, key=lambda p: tuple(p["pair"]))

    canonical_payload = {
        "glossary": norm_glossary,
        "names": sorted_names,
        "pronouns": sorted_pronouns,
    }
    serialized = unicodedata.normalize(
        "NFC",
        json.dumps(canonical_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def ensure_hanviet_segments(xml_text: str, book_record: Optional[Dict[str, Any]] = None) -> str:
    """Ensure every <segment> in a zh->vi translation.xml contains a non-empty <hanviet> block (REQ-020, REQ-026)."""
    if not xml_text:
        return xml_text

    def _inject(match: re.Match) -> str:
        attrs = match.group(1)
        inner = match.group(2)
        hv_match = HANVIET_TAG_RE.search(inner)
        if hv_match and hv_match.group(1).strip():
            return match.group(0)

        orig_match = ORIG_TAG_RE.search(inner)
        if not orig_match:
            return match.group(0)

        orig_text = orig_match.group(1).strip()
        hv_text = transcribe_to_hanviet(orig_text, book_record=book_record).strip()
        if not hv_text:
            hv_text = orig_text

        # Remove any empty <hanviet></hanviet> before inserting after </original>
        cleaned_inner = HANVIET_TAG_RE.sub("", inner)
        insert_pos = cleaned_inner.find("</original>")
        if insert_pos == -1:
            return match.group(0)
        insert_end = insert_pos + len("</original>")
        updated_inner = (
            cleaned_inner[:insert_end]
            + f"\n<hanviet>\n{hv_text}\n</hanviet>"
            + cleaned_inner[insert_end:]
        )
        return f"<segment{attrs}>{updated_inner}</segment>"

    return SEGMENT_BLOCK_RE.sub(_inject, xml_text)


from scripts.utils.pr_body import build_translation_pr_body


def build_chapter_issue_spec(
    *,
    book_slug: str,
    ch_name: str,
    source_xml: str,
    source_lang: str,
    target_lang: str,
    glossary_sha: str,
) -> Dict[str, Any]:
    """Build deterministic title, body, labels, and segment range for a ch02..chNN tracking issue."""
    segs = parse_segments(source_xml)
    seg_first = segs[0]["id"]
    seg_last = segs[-1]["id"]
    seg_range = seg_first if seg_first == seg_last else f"{seg_first}..{seg_last}"
    chunk_id = f"{book_slug}-{ch_name}"
    title = f"Translate [{ch_name}] {chunk_id}: {seg_range}"
    body = (
        f"## Translation Task: {chunk_id}\n\n"
        f"- **Book:** {book_slug}\n"
        f"- **Chapter:** {ch_name}\n"
        f"- **Chunk ID:** {chunk_id}\n"
        f"- **Segments:** {seg_range}\n"
        f"- **Source language:** {source_lang}\n"
        f"- **Target language:** {target_lang}\n"
        f"- **Frozen Glossary SHA:** {glossary_sha}\n\n"
        f"### Instructions\n"
        f"1. Post `/claim` as a courtesy if you plan to translate this chapter.\n"
        f"2. Create branch `translate/{chunk_id}-<your_github_login>`.\n"
        f"3. Translate into `runs/{book_slug}/{ch_name}/translation.xml` keeping `<original>` byte-identical.\n"
        f"4. Open a Pull Request referencing `#{chunk_id}` issue number and frozen `Glossary SHA`.\n"
    )
    labels = ["status:open", f"book:{book_slug}", f"lang:{source_lang}-{target_lang}"]
    return {
        "chapter": ch_name,
        "chunk_id": chunk_id,
        "title": title,
        "body": body,
        "labels": labels,
        "range": seg_range,
    }


def _extract_heading_title(source_xml: str, ch_name: str) -> str:
    segs = parse_segments(source_xml)
    for s in segs:
        if s.get("type") == "heading":
            txt = re.sub(r"^#+\s*", "", str(s.get("original", "")).strip())
            if txt:
                return txt.splitlines()[0].strip()
    return ch_name


def _read_existing_checklist_envelope(
    checklist_path: Path,
    book_slug: str,
) -> Tuple[Optional[str], Optional[str], Dict[str, Dict[str, Any]]]:
    """Return (seed_status, seed_error, existing_chapter_entries_by_name)."""
    if not checklist_path.exists():
        return None, None, {}

    raw = json.loads(checklist_path.read_text(encoding="utf-8"))
    seed_status: Optional[str] = None
    seed_error: Optional[str] = None
    by_ch: Dict[str, Dict[str, Any]] = {}

    if isinstance(raw, dict):
        if isinstance(raw.get("seed_status"), str):
            seed_status = raw["seed_status"].strip()
        if raw.get("seed_error") is not None:
            seed_error = str(raw["seed_error"])
        ch_container = raw.get("chapters")
        if isinstance(ch_container, list):
            for entry in ch_container:
                if isinstance(entry, dict) and entry.get("chapter"):
                    by_ch[str(entry["chapter"])] = dict(entry)
        elif isinstance(ch_container, dict):
            for k, v in ch_container.items():
                if isinstance(v, dict):
                    by_ch[str(k)] = {"chapter": str(k), **v}
    elif isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict) and entry.get("chapter"):
                by_ch[str(entry["chapter"])] = dict(entry)

    return seed_status, seed_error, by_ch


def _write_checklist_envelope(
    checklist_path: Path,
    book_slug: str,
    seed_status: str,
    chapters_list: List[Dict[str, Any]],
    seed_error: Optional[str] = None,
) -> Dict[str, Any]:
    payload = {
        "book_slug": book_slug,
        "seed_status": seed_status,
        "seed_error": seed_error,
        "chapters": chapters_list,
    }
    atomic_write_json(checklist_path, payload)
    return payload


def _requires_fiction_pronouns(manifest: Dict[str, Any], sample_text: str) -> bool:
    """REQ-020.2: mandatory for fiction/literary, or omitted/general with <2 code fences, <5 tech terms, >=5 dialogue quotes."""
    raw_genre = str(manifest.get("genre") or "").strip().lower()
    if raw_genre in ("fiction", "literary"):
        return True
    if raw_genre and raw_genre != "general":
        return False

    fences = len(re.findall(r"```", sample_text)) // 2
    tech_hits = sum(
        1
        for seed in TECHNICAL_SEED_GLOSSARY_V1
        if re.search(r"\b" + re.escape(seed["source"]) + r"\b", sample_text, re.IGNORECASE)
    )
    quote_count = len(DIALOGUE_QUOTE_RE.findall(sample_text))
    return fences < 2 and tech_hits < 5 and quote_count >= 5


def _format_seed_audit_comment(book_record: Dict[str, Any]) -> str:
    glossary = book_record.get("glossary") or {}
    names = book_record.get("names") or []
    pronouns = _normalize_pronouns_list(book_record)

    lines = [
        "## Bound Seed Glossary & Character Record (`ch01`)",
        "",
        f"### Glossary ({len(glossary)} entries)",
    ]
    if glossary:
        for k in sorted(glossary.keys()):
            lines.append(f"- `{k}` -> `{glossary[k]}`")
    else:
        lines.append("- *(none)*")

    lines.extend(["", f"### Names ({len(names)} entries)"])
    if names:
        for n in sorted(names, key=lambda x: (str(x.get("source", "")), str(x.get("target", "")))):
            lines.append(f"- `{n.get('source')}` -> `{n.get('target')}`")
    else:
        lines.append("- *(none)*")

    lines.extend(["", f"### Pronouns ({len(pronouns)} entries)"])
    if pronouns:
        for p in sorted(pronouns, key=lambda x: tuple(x["pair"])):
            pair = p["pair"]
            lines.append(f"- `{pair[0]} <-> {pair[1]}`: `{p['a_to_b']}` / `{p['b_to_a']}` ({p['basis']})")
    else:
        lines.append("- *(none)*")

    return "\n".join(lines) + "\n"


def _is_transient_eval_failure(
    eval_res: Optional[Dict[str, Any]],
    eval_exc: Optional[BaseException],
) -> bool:
    if eval_exc is not None:
        return True
    if eval_res is None or not isinstance(eval_res, dict) or len(eval_res) == 0:
        return True
    if eval_res.get("fallback_unverified") is True or eval_res.get("provenance") == "fallback":
        return True
    if eval_res.get("error") or eval_res.get("transient_error"):
        return True
    raw_verdict = str(eval_res.get("verdict") or "").strip().upper()
    return raw_verdict in ("ERROR", "TIMEOUT", "UNAVAILABLE", "NEEDS_REVIEW")


class GitHubSeedPRClient:
    """Production GitHub CLI client for ch01 seed PR lifecycle and ch02..chNN issue creation."""

    def __init__(
        self,
        repo_root: Path,
        repo_full_name: str,
        gh_user: str,
        bot_git_name: str,
        bot_git_email: str,
    ):
        self.root = repo_root
        self.repo_full_name = repo_full_name
        self.gh_user = gh_user
        self.bot_git_name = bot_git_name
        self.bot_git_email = bot_git_email
        self._bot_token: Optional[str] = None
        self._token_resolved = False

    def _guard_pytest(self) -> None:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            raise RuntimeError(
                "Refusing to invoke live 'gh' CLI inside pytest without pr_client"
            )

    def _run_gh(self, args: List[str]) -> str:
        self._guard_pytest()
        env = os.environ.copy()
        if "GH_TOKEN" not in env and self.gh_user:
            if not self._token_resolved:
                tok = subprocess.run(
                    ["gh", "auth", "token", "-u", self.gh_user],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if tok.returncode == 0 and tok.stdout.strip():
                    self._bot_token = tok.stdout.strip()
                self._token_resolved = True
            if self._bot_token:
                env["GH_TOKEN"] = self._bot_token

        proc = subprocess.run(
            ["gh"] + args,
            env=env,
            cwd=str(self.root),
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout

    def ensure_labels(self, labels: List[str]) -> None:
        """Idempotently ensure labels exist in GitHub repository (REQ-034)."""
        for lbl in labels:
            lbl_clean = str(lbl).strip()
            if not lbl_clean:
                continue
            try:
                color = "ededed"
                if lbl_clean.startswith("status:"):
                    color = "0e8a16" if "merged" in lbl_clean or "done" in lbl_clean else "fbca04"
                elif lbl_clean.startswith("book:"):
                    color = "1d76db"
                elif lbl_clean.startswith("lang:"):
                    color = "5319e7"
                elif lbl_clean.startswith("agent:"):
                    color = "c2e0c6"
                self._run_gh(["label", "create", lbl_clean, "--color", color, "--force"])
            except Exception as e:
                logger.debug("Failed creating label '%s': %s", lbl_clean, e)

    def find_merged_seed_pr(self, head_ref: str) -> Optional[Dict[str, Any]]:
        self._guard_pytest()
        out = self._run_gh([
            "pr", "list",
            "--repo", self.repo_full_name,
            "--state", "merged",
            "--head", head_ref,
            "--json", "number,headRefName,headRefOid,mergeCommit,body,title",
        ])
        items = json.loads(out) if out.strip() else []
        if not items:
            return None
        pr = items[0]
        return {
            "number": int(pr["number"]),
            "head_ref": pr.get("headRefName", head_ref),
            "head_oid": pr.get("headRefOid", ""),
            "merge_sha": (pr.get("mergeCommit") or {}).get("oid", ""),
            "body": pr.get("body", ""),
            "title": pr.get("title", ""),
        }

    def find_open_seed_pr(self, head_ref: str) -> Optional[Dict[str, Any]]:
        self._guard_pytest()
        out = self._run_gh([
            "pr", "list",
            "--repo", self.repo_full_name,
            "--state", "open",
            "--head", head_ref,
            "--json", "number,headRefName,headRefOid,body,title,files,reviews,comments",
        ])
        items = json.loads(out) if out.strip() else []
        if not items:
            return None
        pr = items[0]
        return {
            "number": int(pr["number"]),
            "head_ref": pr.get("headRefName", head_ref),
            "head_oid": pr.get("headRefOid", ""),
            "body": pr.get("body", ""),
            "title": pr.get("title", ""),
            "changed_files": [f.get("path", "") for f in pr.get("files", []) if isinstance(f, dict)],
            "reviews": pr.get("reviews") or [],
            "comments": pr.get("comments") or [],
        }

    def create_seed_pr(
        self,
        head_ref: str,
        title: str,
        body: str,
        changed_files: List[str],
        translation_xml: str,
        labels: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        self._guard_pytest()
        if labels:
            self.ensure_labels(labels)

        # Create isolated branch in ephemeral worktree so main stays clean (REQ-034)
        wt_dir = self.root / ".worktrees" / f"seed_{head_ref.replace('/', '_')}"
        if wt_dir.exists():
            shutil.rmtree(wt_dir, ignore_errors=True)
        wt_dir.parent.mkdir(parents=True, exist_ok=True)

        subprocess.run(
            ["git", "worktree", "add", "-B", head_ref, str(wt_dir), "main"],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            check=True,
        )
        try:
            for rel_path in changed_files:
                abs_p = wt_dir / rel_path
                abs_p.parent.mkdir(parents=True, exist_ok=True)
                abs_p.write_text(translation_xml, encoding="utf-8")
                subprocess.run(["git", "add", rel_path], cwd=str(wt_dir), check=True, capture_output=True)

            diff_cached = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=str(wt_dir),
                capture_output=True,
                check=False,
            )
            if diff_cached.returncode != 0:
                subprocess.run(
                    [
                        "git",
                        "-c", f"user.name={self.bot_git_name}",
                        "-c", f"user.email={self.bot_git_email}",
                        "commit",
                        "-m", title,
                    ],
                    cwd=str(wt_dir),
                    capture_output=True,
                    text=True,
                    check=True,
                )
            head_oid = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(wt_dir),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            from scripts.collab_worker import GitHubAccountContext

            with GitHubAccountContext(self.gh_user) as ctx:
                ctx.run_git(
                    ["push", "--force-with-lease", "origin", head_ref],
                    cwd=str(wt_dir),
                    check=True,
                )
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(wt_dir)], cwd=str(self.root), capture_output=True, check=False)
            if wt_dir.exists():
                shutil.rmtree(wt_dir, ignore_errors=True)

        args = [
            "pr", "create",
            "--repo", self.repo_full_name,
            "--base", "main",
            "--head", head_ref,
            "--title", title,
            "--body", body,
        ]
        for lbl in labels or []:
            args.extend(["--label", lbl])
        out = self._run_gh(args).strip()
        m_num = re.search(r"/pull/(\d+)", out)
        pr_num = int(m_num.group(1)) if m_num else 0
        if pr_num <= 0:
            found = self.find_open_seed_pr(head_ref)
            if found:
                pr_num = int(found["number"])
        if pr_num <= 0:
            raise RuntimeError(f"Failed to determine PR number from gh pr create output: {out}")

        return {
            "number": pr_num,
            "head_ref": head_ref,
            "head_oid": head_oid,
            "title": title,
            "body": body,
            "changed_files": list(changed_files),
            "translation_xml": translation_xml,
        }

    def comment_pr(self, pr_num: int, body: str) -> None:
        self._run_gh(["pr", "comment", str(pr_num), "--repo", self.repo_full_name, "--body", body])

    def merge_pr(self, pr_num: int, head_oid: Optional[str] = None) -> str:
        args = ["pr", "merge", str(pr_num), "--repo", self.repo_full_name, "--merge", "--auto=false"]
        if head_oid:
            args.extend(["--match-head-commit", head_oid])
        self._run_gh(args)

        # Unlink any local untracked files brought in by the PR so --ff-only cleanly succeeds
        try:
            view_files = self._run_gh(["pr", "view", str(pr_num), "--repo", self.repo_full_name, "--json", "files"])
            for f_info in json.loads(view_files).get("files", []):
                rel_f = f_info.get("path", "")
                p = self.root / rel_f
                if p.is_file():
                    tracked = subprocess.run(
                        ["git", "ls-files", "--error-unmatch", rel_f],
                        cwd=str(self.root),
                        capture_output=True,
                        check=False,
                    )
                    if tracked.returncode != 0:
                        p.unlink(missing_ok=True)
        except Exception:
            pass

        subprocess.run(
            ["git", "pull", "--ff-only", "origin", "main"],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            check=True,
        )
        view_out = self._run_gh(["pr", "view", str(pr_num), "--repo", self.repo_full_name, "--json", "mergeCommit"])
        merge_sha = (json.loads(view_out).get("mergeCommit") or {}).get("oid", "")
        if not merge_sha:
            merge_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        return merge_sha

    def close_pr(self, pr_num: int, comment: Optional[str] = None) -> None:
        if comment:
            self.comment_pr(pr_num, comment)
        self._run_gh(["pr", "close", str(pr_num), "--repo", self.repo_full_name])

    def list_book_issues(self, book_slug: str) -> List[Dict[str, Any]]:
        out = self._run_gh([
            "issue", "list",
            "--repo", self.repo_full_name,
            "--state", "all",
            "--label", f"book:{book_slug}",
            "--json", "number,title,body,labels,state",
        ])
        items = json.loads(out) if out.strip() else []
        for iss in items:
            iss["labels"] = [
                lbl.get("name", "") if isinstance(lbl, dict) else str(lbl)
                for lbl in iss.get("labels", [])
            ]
        return items

    def create_issue(self, title: str, body: str, labels: List[str]) -> Dict[str, Any]:
        self.ensure_labels(labels)
        args = [
            "issue", "create",
            "--repo", self.repo_full_name,
            "--title", title,
            "--body", body,
        ]
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


def reconcile_missing_chapter_issues(
    *,
    runs_dir: Path,
    book_slug: str,
    manifest: Dict[str, Any],
    glossary_sha: str,
    existing_issues: List[Dict[str, Any]],
    create_issue_fn: Callable[[str, str, List[str]], Any],
) -> List[Dict[str, Any]]:
    """Create-if-missing status:open tracking issues for ch02..chNN deduplicated by <slug>-chNN (REQ-020)."""
    src_lang = str(manifest.get("source_lang", "en")).strip()
    tgt_lang = str(manifest.get("target_lang", "vi")).strip()

    existing_by_chunk: Dict[str, Any] = {}
    for iss in existing_issues:
        if not isinstance(iss, dict):
            continue
        t = str(iss.get("title") or "")
        b = str(iss.get("body") or "")
        m = re.search(rf"\b({re.escape(book_slug)}-ch\d{{2,}})\b", f"{t}\n{b}")
        if m:
            existing_by_chunk[m.group(1)] = iss

    chapter_dirs = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir() and CHAPTER_DIR_RE.match(d.name)],
        key=lambda p: int(CHAPTER_DIR_RE.match(p.name).group(1)),
    )

    created_or_matched: List[Dict[str, Any]] = []
    for ch_d in chapter_dirs:
        if ch_d.name == "ch01":
            continue
        src_path = ch_d / "source.txt"
        if not src_path.is_file():
            continue
        src_xml = src_path.read_text(encoding="utf-8")
        spec = build_chapter_issue_spec(
            book_slug=book_slug,
            ch_name=ch_d.name,
            source_xml=src_xml,
            source_lang=src_lang,
            target_lang=tgt_lang,
            glossary_sha=glossary_sha,
        )
        chunk_id = spec["chunk_id"]
        if chunk_id in existing_by_chunk:
            created_or_matched.append({
                "chapter": ch_d.name,
                "chunk_id": chunk_id,
                "title": spec["title"],
                "range": spec["range"],
                "issue_ref": existing_by_chunk[chunk_id],
                "created": False,
            })
            continue

        iss_info = create_issue_fn(spec["title"], spec["body"], spec["labels"])
        created_or_matched.append({
            "chapter": ch_d.name,
            "chunk_id": chunk_id,
            "title": spec["title"],
            "range": spec["range"],
            "issue_ref": iss_info,
            "created": True,
        })

    return created_or_matched


def _git_commit_runs_if_repo(
    repo_root: Path,
    book_slug: str,
    bot_git_name: str,
    bot_git_email: str,
    message: str,
    push_origin: bool = False,
    gh_user: Optional[str] = None,
) -> Optional[str]:
    is_git = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if is_git.returncode != 0 or is_git.stdout.strip() != "true":
        return None

    subprocess.run(
        ["git", "add", f"runs/{book_slug}"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    if "extract chapters" in message:
        subprocess.run(
            ["git", "reset", "--", f"runs/{book_slug}/*/translation.xml"],
            cwd=str(repo_root),
            capture_output=True,
            check=False,
        )
    diff_cached = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=str(repo_root),
        capture_output=True,
        check=False,
    )
    if diff_cached.returncode != 0:
        subprocess.run(
            [
                "git",
                "-c", f"user.name={bot_git_name}",
                "-c", f"user.email={bot_git_email}",
                "commit",
                "-m", message,
            ],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
        )
    if push_origin:
        rem = subprocess.run(
            ["git", "remote"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if rem.returncode == 0 and "origin" in rem.stdout.split():
            if gh_user:
                from scripts.collab_worker import GitHubAccountContext

                with GitHubAccountContext(gh_user) as ctx:
                    ctx.run_git(["push", "origin", "HEAD"], cwd=str(repo_root), check=True)
            else:
                subprocess.run(
                    ["git", "push", "origin", "HEAD"],
                    cwd=str(repo_root),
                    capture_output=True,
                    text=True,
                    check=True,
                )

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    return head.stdout.strip()


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
    *,
    pr_client: Optional[Any] = None,
    pronoun_extract_fn: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
    retry_blocked: bool = False,
    source_injection_cleared: bool = False,
    prepare_only: bool = False,
    push_origin: bool = False,
) -> Dict[str, Any]:
    """Authoritatively seeds a newly merged book into the collab translation pipeline (REQ-020, REQ-028)."""
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

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    src_lang = str(manifest.get("source_lang", "en")).strip()
    tgt_lang = str(manifest.get("target_lang", "vi")).strip()

    checklist_path = runs_dir / "checklist.json"
    existing_status, existing_error, existing_chapters_by_name = _read_existing_checklist_envelope(
        checklist_path, book_slug
    )

    if existing_status == "seeded":
        br_path = runs_dir / "book_record.json"
        rec_sha = ""
        if br_path.exists():
            rec_sha = str(json.loads(br_path.read_text(encoding="utf-8")).get("glossary_sha", ""))
        return {
            "seeded": True,
            "skipped": True,
            "seed_status": "seeded",
            "book_slug": book_slug,
            "glossary_sha": rec_sha,
            "issues_created": [],
            "failed_chapters": [],
        }

    if existing_status == "blocked_failed" and not retry_blocked:
        return {
            "seeded": False,
            "skipped": True,
            "seed_status": "blocked_failed",
            "book_slug": book_slug,
            "reason": existing_error or "Seed quarantined in blocked_failed; pass --retry-blocked to reset",
            "issues_created": [],
            "failed_chapters": [],
        }

    client = pr_client or GitHubSeedPRClient(
        repo_root=repo_root_path,
        repo_full_name=str(repo_full_name),
        gh_user=str(gh_user),
        bot_git_name=str(bot_git_name),
        bot_git_email=str(bot_git_email),
    )

    # 1. Extract chapters if ch01/source.txt is missing
    ch01_source_path = runs_dir / "ch01" / "source.txt"
    if not ch01_source_path.exists():
        source_files = [
            f for f in source_dir.iterdir()
            if f.is_file() and not f.name.startswith(".")
        ] if source_dir.is_dir() else []
        if not source_files:
            err = f"No source book file found in {source_dir}"
            preserved = list(existing_chapters_by_name.values())
            _write_checklist_envelope(checklist_path, book_slug, "blocked_failed", preserved, seed_error=err)
            return {
                "seeded": False,
                "seed_status": "blocked_failed",
                "book_slug": book_slug,
                "reason": err,
                "issues_created": [],
                "failed_chapters": [],
            }
        try:
            extract_book(
                input_path=source_files[0],
                output_dir=runs_dir,
                slug=book_slug,
                source_lang=src_lang or "auto",
            )
        except Exception as exc:
            err = f"Extraction failed: {exc}"
            preserved = list(existing_chapters_by_name.values())
            _write_checklist_envelope(checklist_path, book_slug, "blocked_failed", preserved, seed_error=err)
            return {
                "seeded": False,
                "seed_status": "blocked_failed",
                "book_slug": book_slug,
                "reason": err,
                "issues_created": [],
                "failed_chapters": [],
            }

    chapter_dirs = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir() and CHAPTER_DIR_RE.match(d.name)],
        key=lambda p: int(CHAPTER_DIR_RE.match(p.name).group(1)),
    )

    chapters_list: List[Dict[str, Any]] = []
    failed_chapters: List[str] = []
    injection_errors: List[str] = []

    for idx, ch_d in enumerate(chapter_dirs, start=1):
        ch_name = ch_d.name
        m_num = CHAPTER_DIR_RE.match(ch_name)
        ch_num = int(m_num.group(1)) if m_num else idx
        prior = existing_chapters_by_name.get(ch_name, {})
        ch_source = ch_d / "source.txt"

        title_str = str(prior.get("title") or ch_name)
        if ch_source.is_file():
            raw_bytes = ch_source.read_bytes()
            try:
                src_text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                failed_chapters.append(ch_name)
                src_text = ""
            else:
                segs = parse_segments(src_text)
                if segs:
                    title_str = str(prior.get("title") or _extract_heading_title(src_text, ch_name))
                if (
                    len(raw_bytes) == 0
                    or len(raw_bytes) > MAX_CHAPTER_SOURCE_BYTES
                    or len(segs) < 1
                    or len(segs) > MAX_CHAPTER_SEGMENTS
                ):
                    failed_chapters.append(ch_name)
                elif not source_injection_cleared:
                    for seg in segs:
                        flags = detect_prompt_injection(str(seg.get("original", "")), allow_rubric_lines=False)
                        if flags:
                            injection_errors.append(f"{ch_name} ({seg.get('id')}): {flags[0]}")
                    outside = SEGMENT_BLOCK_RE.sub("", src_text)
                    out_flags = detect_prompt_injection(outside, allow_rubric_lines=False)
                    if out_flags:
                        injection_errors.append(f"{ch_name}: {out_flags[0]}")
        else:
            failed_chapters.append(ch_name)

        chapters_list.append({
            "chapter": ch_name,
            "chapter_num": ch_num,
            "title": title_str,
            "status": str(prior.get("status") or "pending"),
        })

    # Write initial envelope checklist.json with seed_status: "seeding"
    _write_checklist_envelope(checklist_path, book_slug, "seeding", chapters_list, seed_error=None)

    seed_head_ref = f"translate/{book_slug}-ch01-{gh_user}"

    def _quarantine(reason: str, open_pr_num: Optional[int] = None) -> Dict[str, Any]:
        if failed_chapters:
            atomic_write_json(
                runs_dir / "seed_failures.json",
                {"book_slug": book_slug, "failed_chapters": failed_chapters},
            )
        if open_pr_num is not None and open_pr_num > 0:
            client.close_pr(open_pr_num, comment=f"Closing ch01 seed PR due to seed quarantine: {reason}")
        _write_checklist_envelope(checklist_path, book_slug, "blocked_failed", chapters_list, seed_error=reason)
        return {
            "seeded": False,
            "seed_status": "blocked_failed",
            "book_slug": book_slug,
            "reason": reason,
            "issues_created": [],
            "failed_chapters": failed_chapters,
        }

    if not chapter_dirs or "ch01" not in {d.name for d in chapter_dirs} or failed_chapters:
        return _quarantine(
            f"Extracted chapter bounds/segment check failed (failed_chapters={failed_chapters})"
        )

    if injection_errors:
        return _quarantine(f"Pre-LLM prompt injection detected in source.txt: {'; '.join(injection_errors)}")

    # 2. Initialize book_record.json over min(3, N) opening chapters & enforce fiction pronouns
    book_record_path = initialize_book_record(runs_dir, book_slug, source_lang=src_lang)
    book_record = json.loads(book_record_path.read_text(encoding="utf-8"))

    opening_dirs = chapter_dirs[: min(3, len(chapter_dirs))]
    sample_text = "\n\n".join(
        (d / "source.txt").read_text(encoding="utf-8") for d in opening_dirs
    )

    book_record["book_slug"] = book_slug
    book_record["source_lang"] = src_lang
    book_record["target_lang"] = tgt_lang
    book_record["title"] = str(manifest.get("title") or book_record.get("title") or book_slug)

    raw_brief = book_record.get("brief") if isinstance(book_record.get("brief"), dict) else {}
    author_str = str(manifest.get("author") or book_record.get("author") or raw_brief.get("author") or "")
    book_record["author"] = author_str
    effective_genre = str(manifest.get("genre") or raw_brief.get("genre") or "non-fiction").strip()
    summary_str = str(
        raw_brief.get("summary")
        or f"Collaborative {src_lang}->{tgt_lang} translation of {book_record['title']} by {manifest.get('author', 'Unknown')}."
    ).strip()
    book_record["brief"] = {
        "genre": effective_genre,
        "author": author_str,
        "summary": summary_str,
        "source_lang": src_lang,
        "target_lang": tgt_lang,
        "title": book_record["title"],
    }

    ensure_technical_seed_glossary(book_record, sample_text=sample_text)

    if _requires_fiction_pronouns(manifest, sample_text):
        if manifest.get("genre") in (None, "", "general"):
            book_record["brief"]["genre"] = "fiction"

        if not prepare_only:
            existing_pronouns = [
                p for p in _normalize_pronouns_list(book_record)
                if not p.get("provisional")
                and _is_pair_attested_in_source(p["pair"], book_record.get("names") or [], sample_text)
            ]
            if not existing_pronouns:
                if pronoun_extract_fn is not None:
                    try:
                        ext = pronoun_extract_fn(sample_text, book_record)
                    except Exception as exc:
                        return _quarantine(f"Fiction pronoun extraction failed: {exc}")
                    ext_names = (ext or {}).get("names") or []
                    ext_pronouns = (ext or {}).get("pronouns") or []
                    merged_names = list(book_record.get("names") or []) + [
                        n for n in ext_names if isinstance(n, dict)
                    ]
                    book_record["names"] = merged_names
                    attested_pairs: List[Dict[str, Any]] = []
                    for p in ext_pronouns:
                        if not isinstance(p, dict):
                            continue
                        pair = p.get("pair")
                        if isinstance(pair, list) and len(pair) == 2:
                            if _is_pair_attested_in_source(pair, merged_names, sample_text):
                                attested_pairs.append({
                                    "pair": [str(pair[0]).strip(), str(pair[1]).strip()],
                                    "a_to_b": str(p.get("a_to_b") or ""),
                                    "b_to_a": str(p.get("b_to_a") or ""),
                                    "basis": str(p.get("basis") or ""),
                                    "provisional": False,
                                })
                    if not attested_pairs:
                        return _quarantine("Fiction pronoun extraction yielded 0 source-attested pairs")
                    book_record["pronouns"] = attested_pairs
                else:
                    if os.environ.get("PYTEST_CURRENT_TEST"):
                        raise RuntimeError(
                            "Refusing to invoke live cbc fiction pronoun CLI inside pytest without pronoun_extract_fn"
                        )
                    try:
                        atomic_write_json(book_record_path, book_record)
                        book_record = assert_fiction_pronouns_populated(
                            workspace_dir=runs_dir,
                            book_record_path=book_record_path,
                            book_record=book_record,
                            offline=False,
                        )
                    except Exception as exc:
                        return _quarantine(f"Live fiction pronoun extraction failed: {exc}")

    sync_collab_book_record(book_record)
    atomic_write_json(book_record_path, book_record)

    if git_sha_fn is None:
        _git_commit_runs_if_repo(
            repo_root_path,
            book_slug,
            str(bot_git_name),
            str(bot_git_email),
            f"seed({book_slug}): extract chapters and initialize book_record",
            push_origin=push_origin,
            gh_user=str(gh_user) if gh_user else None,
        )

    if prepare_only:
        return {
            "seeded": False,
            "prepared": True,
            "seed_status": "seeding",
            "book_slug": book_slug,
            "chapter_count": len(chapters_list),
            "issues_created": [],
            "failed_chapters": [],
        }

    # 3. Crash-recovery check or bot-authored ch01 seed PR + validation + live evaluation
    ch01_dir = runs_dir / "ch01"
    ch01_source_xml = (ch01_dir / "source.txt").read_text(encoding="utf-8")
    ch01_segs = parse_segments(ch01_source_xml)
    seg_first = ch01_segs[0]["id"]
    seg_last = ch01_segs[-1]["id"]
    ch01_trans_path = ch01_dir / "translation.xml"

    merged_pr = client.find_merged_seed_pr(seed_head_ref)
    already_merged = bool(merged_pr and ch01_trans_path.is_file())

    open_pr: Optional[Dict[str, Any]] = None
    selected_pr_num = 0
    merge_sha = ""
    score = 0.0
    eval_res: Dict[str, Any] = {}

    if already_merged and merged_pr is not None:
        selected_pr_num = int(merged_pr["number"])
        merge_sha = str(merged_pr.get("merge_sha") or "")
        ch01_trans_xml = ch01_trans_path.read_text(encoding="utf-8")
        existing_ledger = read_ledger(runs_dir / "collab_ledger.json")
        ch01_led = (existing_ledger.get("chapters") or {}).get("ch01") or {}
        if ch01_led.get("score_of_record") is not None:
            score = float(ch01_led["score_of_record"])
        elif evaluate_fn is not None:
            eval_res = evaluate_fn(ch01_source_xml, ch01_trans_xml, book_record)
            norm_rec = normalize_evaluation_result(
                eval_res, effective_threshold=resolve_pass_threshold(book_record=book_record)
            )
            score = float(norm_rec["weighted_total"])
        else:
            score = float(resolve_pass_threshold(book_record=book_record))
    else:
        open_pr = client.find_open_seed_pr(seed_head_ref)
        if open_pr is not None and open_pr.get("translation_xml"):
            ch01_trans_xml = str(open_pr["translation_xml"])
            ch01_trans_path.write_text(ch01_trans_xml, encoding="utf-8")
        elif open_pr is not None and ch01_trans_path.is_file():
            ch01_trans_xml = ch01_trans_path.read_text(encoding="utf-8")
        else:
            if translate_fn is None:
                _write_checklist_envelope(checklist_path, book_slug, "seeding", chapters_list, seed_error=None)
                return {
                    "seeded": False,
                    "seed_status": "seeding",
                    "book_slug": book_slug,
                    "reason": "Awaiting ch01 seed translation PR",
                    "issues_created": [],
                    "failed_chapters": failed_chapters,
                }
            ch01_trans_xml = translate_fn(ch01_source_xml, book_record, manifest)
            if not ch01_trans_xml or not ch01_trans_xml.strip():
                return _quarantine("ch01 translation_fn returned empty translation.xml")
            if src_lang == "zh":
                ch01_trans_xml = ensure_hanviet_segments(ch01_trans_xml, book_record=book_record)
            ch01_trans_path.write_text(ch01_trans_xml, encoding="utf-8")

        pr_body = (
            open_pr["body"]
            if (open_pr is not None and open_pr.get("body"))
            else build_translation_pr_body(
                agent_login=str(gh_user),
                engine_model="seed-bot",
                source_lang=src_lang,
                target_lang=tgt_lang,
                book_slug=book_slug,
                ch_name="ch01",
                seg_first=seg_first,
                seg_last=seg_last,
                issue_ref="seed",
                glossary_sha="seed",
            )
        )
        changed_files = [f"runs/{book_slug}/ch01/translation.xml"]

        if open_pr is None:
            open_pr = client.create_seed_pr(
                head_ref=seed_head_ref,
                title=f"seed({book_slug}): translate ch01 reference chapter",
                body=pr_body,
                changed_files=changed_files,
                translation_xml=ch01_trans_xml,
                labels=[f"book:{book_slug}", f"lang:{src_lang}-{tgt_lang}", f"agent:{gh_user}"],
            )

        open_pr_num = int(open_pr["number"])
        open_head_oid = str(open_pr.get("head_oid") or "").strip()

        val_pr = validate_translation_pr(
            head_ref=seed_head_ref,
            author_login=str(gh_user),
            body=pr_body,
            changed_files=changed_files,
            source_xml_text=ch01_source_xml,
            translation_xml_text=ch01_trans_xml,
            bot_login=str(gh_user),
            is_seed_pr=True,
            manifest=manifest,
            existing_slugs={book_slug},
        )
        if not val_pr["valid"] or val_pr.get("injection_flags"):
            return _quarantine(
                f"ch01 seed PR deterministic/injection validation failed: {val_pr['defects']}",
                open_pr_num=open_pr_num,
            )

        eval_exc: Optional[BaseException] = None
        eval_raw: Optional[Dict[str, Any]] = None
        if evaluate_fn is None:
            det_probe: Dict[str, Any] = {
                "verdict": "PASS",
                "dimensions": {d: 5.0 for d in CANONICAL_DIMENSIONS},
                "issues": [],
            }
            enforce_language_detection(det_probe, ch01_trans_xml, source_lang=src_lang)
            if src_lang == "zh":
                ac_issues = check_anti_convert_issues(ch01_trans_xml, source_lang="zh")
                if ac_issues:
                    det_probe.setdefault("issues", []).extend(ac_issues)
                    det_probe["verdict"] = "FAIL"
            if str(det_probe.get("verdict") or "").upper() == "FAIL":
                return _quarantine(
                    "ch01 seed deterministic language/anti-convert validation failed",
                    open_pr_num=open_pr_num,
                )
            eval_raw = evaluate_pr_from_reviews(
                reviews=open_pr.get("reviews") or [],
                pr_author=str(gh_user),
                head_oid=open_head_oid,
                translation_xml=ch01_trans_xml,
                book_record=book_record,
                source_lang=src_lang,
            )
        else:
            try:
                eval_raw = evaluate_fn(ch01_source_xml, ch01_trans_xml, book_record)
            except Exception as exc:
                eval_exc = exc

        if _is_transient_eval_failure(eval_raw, eval_exc):
            # REQ-020 / AC-020-2: keep seed_status == "seeding" without closing PR so next sweep retries
            reason_msg = (
                f"Transient or fallback ch01 evaluation ({eval_exc or 'fallback_unverified'}); "
                f"keeping seed_status='seeding'"
            )
            _write_checklist_envelope(checklist_path, book_slug, "seeding", chapters_list, seed_error=None)
            return {
                "seeded": False,
                "seed_status": "seeding",
                "book_slug": book_slug,
                "reason": reason_msg,
                "issues_created": [],
                "failed_chapters": failed_chapters,
            }

        assert eval_raw is not None
        eval_res = eval_raw
        threshold = resolve_pass_threshold(book_record=book_record)
        norm_eval = normalize_evaluation_result(eval_res, effective_threshold=threshold)
        score = float(norm_eval["weighted_total"])

        if norm_eval["verdict"] != "PASS":
            return _quarantine(
                f"ch01 seed live evaluation failed (verdict={norm_eval['verdict']}, score={score:.2f}, threshold={threshold:.2f})",
                open_pr_num=open_pr_num,
            )

        # 4. Bind ch01 terms, post bound-glossary audit comment, and merge ch01 seed PR
        update_book_record_from_eval(
            book_record_path=book_record_path,
            eval_data=eval_res,
            chapter_dir=None,
            bind=True,
            chapter_id="ch01",
        )
        book_record = json.loads(book_record_path.read_text(encoding="utf-8"))
        sync_collab_book_record(book_record)

        audit_comment = _format_seed_audit_comment(book_record)
        client.comment_pr(open_pr_num, audit_comment)
        merge_sha = client.merge_pr(open_pr_num, head_oid=open_head_oid or None)
        selected_pr_num = open_pr_num

    # Reload & compute immutable 64-char NFC SHA-256 seed glossary_sha
    book_record = json.loads(book_record_path.read_text(encoding="utf-8"))
    sync_collab_book_record(book_record)
    if book_record.get("glossary_sha") and re.match(r"^[0-9a-f]{64}$", str(book_record["glossary_sha"])):
        glossary_sha = str(book_record["glossary_sha"])
    else:
        glossary_sha = compute_seed_glossary_sha(book_record)
        book_record["glossary_sha"] = glossary_sha
        atomic_write_json(book_record_path, book_record)

    if not merge_sha:
        if git_sha_fn is not None:
            merge_sha = git_sha_fn()
        else:
            merge_sha = glossary_sha[:40]

    # Create-if-missing ch02..chNN issues BEFORE setting seed_status == "seeded" (REQ-020.4)
    def _issue_create(title: str, body: str, labels: List[str]) -> Any:
        if issue_creator_fn is not None:
            return issue_creator_fn(title, body, labels)
        return client.create_issue(title, body, labels)

    existing_issues = (
        client.list_book_issues(book_slug)
        if hasattr(client, "list_book_issues") and issue_creator_fn is None
        else []
    )
    try:
        issues_created = reconcile_missing_chapter_issues(
            runs_dir=runs_dir,
            book_slug=book_slug,
            manifest=manifest,
            glossary_sha=glossary_sha,
            existing_issues=existing_issues,
            create_issue_fn=_issue_create,
        )
    except Exception as exc:
        if isinstance(exc, RuntimeError) and "Refusing to invoke live" in str(exc):
            raise
        logger.warning("Issue creation interrupted for %s; keeping seed_status='seeding': %s", book_slug, exc)
        _write_checklist_envelope(checklist_path, book_slug, "seeding", chapters_list, seed_error=None)
        return {
            "seeded": False,
            "seed_status": "seeding",
            "book_slug": book_slug,
            "glossary_sha": glossary_sha,
            "reason": f"Issue creation incomplete: {exc}",
            "issues_created": [],
            "failed_chapters": failed_chapters,
        }

    # All ch02..chNN issues now exist -> write ch01/final.txt, mark ch01 done + seed_status: "seeded", write ledger
    final_txt = strip_segments_to_final(ch01_trans_xml)
    (ch01_dir / "final.txt").write_text(final_txt, encoding="utf-8")

    for entry in chapters_list:
        if entry.get("chapter") == "ch01":
            entry["status"] = "done"
    _write_checklist_envelope(checklist_path, book_slug, "seeded", chapters_list, seed_error=None)

    ledger_entry = {
        "chapter": "ch01",
        "issue_number": 0,
        "selected_pr": selected_pr_num,
        "rejected_prs": [],
        "translator_login": str(gh_user),
        "reviewer_logins": [],
        "reviewer_score": score,
        "score_of_record": score,
        "verdict": "PASS",
        "merge_sha": merge_sha,
        "glossary_sha": glossary_sha,
    }
    append_ledger_entry(runs_dir / "collab_ledger.json", book_slug, ledger_entry)

    if git_sha_fn is None:
        _git_commit_runs_if_repo(
            repo_root_path,
            book_slug,
            str(bot_git_name),
            str(bot_git_email),
            f"seed({book_slug}): finalize ch01 reference, glossary_sha, and collab_ledger",
            push_origin=True,
            gh_user=str(gh_user) if gh_user else None,
        )

    return {
        "seeded": True,
        "seed_status": "seeded",
        "book_slug": book_slug,
        "glossary_sha": glossary_sha,
        "ch01_score": score,
        "selected_pr": selected_pr_num,
        "issues_created": issues_created,
        "failed_chapters": failed_chapters,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed a newly merged book into the collab translation pipeline."
    )
    parser.add_argument("--config", default=None, help="Path to config.json")
    parser.add_argument("--repo-root", default=None, help="Root of git repository")
    parser.add_argument("--book-slug", required=True, help="Slug of the book to seed")
    parser.add_argument("--repo", default=None, help="Full repository name (owner/repo)")
    parser.add_argument("--gh-user", default=None, help="GitHub user for auth token and attribution")
    parser.add_argument("--ch01-translation-file", default=None, help="Path to pre-translated ch01 translation XML")
    parser.add_argument("--ch01-eval-file", default=None, help="Path to evaluation JSON payload for ch01")
    parser.add_argument("--prepare-only", action="store_true", help="Extract source and prepare book_record only without PR creation")
    parser.add_argument("--retry-blocked", action="store_true", help="Reset seed_status from blocked_failed to seeding")
    args = parser.parse_args(argv)

    from scripts.collab_config import load_collab_config

    cfg = load_collab_config(args.config)

    repo_root = Path(args.repo_root or cfg.workspace_dir).resolve()
    repo_full_name = args.repo or cfg.repo_full_name
    gh_user = args.gh_user or cfg.bot_login
    bot_git_name = cfg.bot_git_name
    bot_git_email = cfg.bot_git_email

    trans_fn = None
    eval_fn = None
    if args.ch01_eval_file:
        eval_path = Path(args.ch01_eval_file).resolve()

        def file_eval_fn(source_xml: str, trans_xml: str, book_record: Dict[str, Any]) -> Dict[str, Any]:
            return json.loads(eval_path.read_text(encoding="utf-8"))

        eval_fn = file_eval_fn

    if args.ch01_translation_file:
        trans_path = Path(args.ch01_translation_file).resolve()

        def file_trans_fn(src_xml: str, rec: Dict[str, Any], man: Dict[str, Any]) -> str:
            return trans_path.read_text(encoding="utf-8")

        trans_fn = file_trans_fn

    res = seed_merged_book(
        repo_root=repo_root,
        book_slug=args.book_slug,
        repo_full_name=repo_full_name,
        gh_user=gh_user,
        bot_git_name=bot_git_name,
        bot_git_email=bot_git_email,
        translate_fn=trans_fn,
        evaluate_fn=eval_fn,
        retry_blocked=args.retry_blocked,
        prepare_only=args.prepare_only,
        push_origin=True,
    )
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if (res.get("seeded") or res.get("prepared") or res.get("seed_status") == "seeding") else 1


if __name__ == "__main__":
    raise SystemExit(main())
