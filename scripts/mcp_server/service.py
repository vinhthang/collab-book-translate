"""Core Book Collaboration domain service shared by MCP tools and REST /api/v1/* endpoints."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import threading
import time
from typing import Any, Optional

from scripts.collab_pr_validate import (
    SLUG_RE,
    TRANS_TAG_RE,
    detect_prompt_injection,
    validate_additions_schema,
    validate_review,
    validate_translation_pr,
)
from scripts.evaluate import enforce_language_detection
from scripts.evaluators.scoring import compute_weighted_total
from scripts.helper import parse_segments
from scripts.mcp_server.auth import (
    AuthContext,
    AuthError,
    SlidingWindowRateLimiter,
    assert_admin,
    assert_agent_admin_or_admin,
    assert_not_self_review,
    mint_revision_token,
    sha256_hex,
    verify_revision_token,
)
from scripts.mcp_server.config import McpServerConfig
from scripts.mcp_server.db import CollabStore
from scripts.mcp_server.git_sync import CollabBooksGitSync

MAX_MCP_XML_BYTES = 524_288  # 512 KB cap per REQ-040
MAX_PROPOSED_CHAPTERS = 200
MAX_BOOK_PROPOSAL_BYTES = 5_242_880  # 5 MiB cap
_PROPOSED_CHAPTER_ID_RE = re.compile(r"^ch\d{2,4}$")
ALLOWED_LICENSES = frozenset(
    {"public-domain", "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "owner-permission"}
)
SUPPORTED_SOURCE_LANGS = frozenset({"en", "zh"})
VALID_AGENT_ROLES = frozenset({"contributor", "agent_admin"})
VALID_APPROVAL_MODES = frozenset({"peer", "auto", "manual"})
VALID_STRATEGIES = frozenset({"faithful", "idiomatic", "register-matched"})
VALID_REVIEW_VERDICTS = frozenset({"COMMENT", "APPROVE", "CHANGES_REQUESTED"})
ACTIONABLE_SUBMISSION_STATUSES = frozenset(
    {"pending_review", "pending_admin_approval", "flagged_security", "escalated_to_admin"}
)
_SUBMISSION_SORT_RANK = {
    "pending_review": 0,
    "pending_admin_approval": 1,
    "flagged_security": 2,
    "escalated_to_admin": 3,
    "remediation_required": 4,
    "accepted": 5,
    "rejected": 6,
}


def _normalize_approval_mode(approval_mode: str) -> str:
    mode = (approval_mode or "").strip().lower()
    if mode not in VALID_APPROVAL_MODES:
        raise ServiceError("approval_mode must be 'peer', 'auto', or 'manual'", status_code=400)
    return mode


def _normalize_brief(
    supplied: Any,
    *,
    default_genre: str = "fiction",
) -> tuple[dict[str, Any], list[str]]:
    """Validate optional book Brief and scan it for prompt injections."""
    if supplied is None:
        return {}, []
    if not isinstance(supplied, dict):
        raise ServiceError("brief must be a JSON object", status_code=400)

    strategy = str(supplied.get("strategy") or "idiomatic").strip().lower()
    if strategy not in VALID_STRATEGIES:
        strategy = "idiomatic"
    raw_rules = supplied.get("style_rules")
    style_rules = (
        [str(r).strip() for r in raw_rules if str(r).strip()]
        if isinstance(raw_rules, list)
        else []
    )
    norm: dict[str, Any] = {
        "genre": str(supplied.get("genre") or default_genre).strip() or default_genre,
        "register": str(supplied.get("register") or "").strip(),
        "audience": str(supplied.get("audience") or "").strip(),
        "style_rules": style_rules[:50],
        "strategy": strategy,
        "notes": str(supplied.get("notes") or "").strip(),
    }
    inj = detect_prompt_injection(json.dumps(norm, ensure_ascii=False)[:16000])
    return norm, inj


def _extract_segment_slice(
    xml_text: str,
    *,
    count: int,
    from_end: bool,
    field: str = "original",
) -> str:
    """Extract first or last N segment bodies from <segment> XML."""
    if not xml_text or count <= 0:
        return ""
    segs = parse_segments(xml_text)
    if not segs:
        return ""
    picked = segs[-count:] if from_end else segs[:count]
    chunks: list[str] = []
    for s in picked:
        val = str(s.get(field) or "").strip()
        if val:
            chunks.append(f"[{s.get('id', '')}] {val}")
    return "\n\n".join(chunks)


def _normalize_proposed_chapters(
    chapters: Any,
) -> tuple[list[dict[str, str]], int, str]:
    """Validate and normalize pre-split chapters submitted from local agent preprocessing."""
    if chapters is None:
        return [], 0, ""
    if not isinstance(chapters, list):
        raise ServiceError("chapters must be a list of chapter objects", status_code=400)
    if len(chapters) > MAX_PROPOSED_CHAPTERS:
        raise ServiceError(f"chapters exceeds maximum of {MAX_PROPOSED_CHAPTERS}", status_code=400)

    normalized: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    total_bytes = 0
    scan_chunks: list[str] = []

    for idx, raw in enumerate(chapters, start=1):
        if not isinstance(raw, dict):
            raise ServiceError(f"chapters[{idx - 1}] must be an object", status_code=400)
        ch_id = str(raw.get("chapter_id") or f"ch{idx:02d}").strip().lower()
        if not _PROPOSED_CHAPTER_ID_RE.match(ch_id) or ch_id in seen_ids:
            raise ServiceError(
                f"Invalid or duplicate chapter_id '{ch_id}' (expected unique ch01..ch9999)",
                status_code=400,
            )
        seen_ids.add(ch_id)

        title = str(raw.get("title") or f"Chapter {idx}").strip() or f"Chapter {idx}"
        src_text = str(raw.get("source_text") or "")
        src_xml = str(raw.get("source_xml") or "")
        if not src_text.strip() and not src_xml.strip():
            raise ServiceError(
                f"Chapter '{ch_id}' must supply non-empty source_text or source_xml",
                status_code=400,
            )
        if src_xml.strip():
            segs = parse_segments(src_xml)
            seg_ids = [s.get("id", "") for s in segs]
            if (
                not segs
                or len(set(seg_ids)) != len(seg_ids)
                or any(not str(s.get("original") or "").strip() for s in segs)
            ):
                raise ServiceError(
                    f"Chapter '{ch_id}' source_xml contains invalid, duplicate, or empty <segment> blocks",
                    status_code=422,
                )

        total_bytes += len(src_text.encode("utf-8")) + len(src_xml.encode("utf-8"))
        scan_chunks.append(f"{title}\n{src_text[:4000]}\n{src_xml[:4000]}")
        normalized.append(
            {
                "chapter_id": ch_id,
                "title": title,
                "source_text": src_text,
                "source_xml": src_xml,
            }
        )

    return normalized, total_bytes, "\n".join(scan_chunks)


def _merge_initial_book_record(
    base: dict[str, Any],
    supplied: Any,
) -> tuple[dict[str, Any], list[str]]:
    """Validate agent-provided book_record (flat maps + analyze.md names/terms/pronouns) and scan for injections."""
    if supplied is None:
        return base, []
    if not isinstance(supplied, dict):
        raise ServiceError("book_record must be a JSON object", status_code=400)

    merged = json.loads(json.dumps(base))
    chars_map: dict[str, str] = dict(merged.get("characters") or {})
    terms_map: dict[str, str] = dict(merged.get("terms") or {})
    pronoun_pairs: list[dict[str, Any]] = list(merged.get("pronoun_pairs") or [])

    chars = supplied.get("characters")
    if isinstance(chars, dict):
        chars_map.update(
            {
                str(k).strip(): str(v).strip()
                for k, v in chars.items()
                if str(k).strip() and str(v).strip()
            }
        )

    names = supplied.get("names")
    if isinstance(names, list):
        clean_names: list[dict[str, Any]] = []
        for item in names:
            if not isinstance(item, dict):
                continue
            src = str(item.get("source") or "").strip()
            tgt = str(item.get("target") or "").strip()
            if not src or not tgt:
                continue
            chars_map[src] = tgt
            aliases = item.get("also_referred_to_as")
            clean_names.append(
                {
                    "source": src,
                    "target": tgt,
                    "also_referred_to_as": [str(a).strip() for a in aliases if str(a).strip()]
                    if isinstance(aliases, list)
                    else [],
                    "notes": str(item.get("notes") or "").strip(),
                }
            )
        merged["names"] = clean_names

    terms = supplied.get("terms")
    if isinstance(terms, dict):
        terms_map.update(
            {
                str(k).strip(): str(v).strip()
                for k, v in terms.items()
                if str(k).strip() and str(v).strip()
            }
        )
    elif isinstance(terms, list):
        clean_term_entries: list[dict[str, Any]] = []
        for item in terms:
            if not isinstance(item, dict):
                continue
            src = str(item.get("source") or "").strip()
            tgt = str(item.get("target") or "").strip()
            if not src or not tgt:
                continue
            terms_map[src] = tgt
            variants = item.get("variants")
            clean_term_entries.append(
                {
                    "source": src,
                    "target": tgt,
                    "match": str(item.get("match") or "word").strip(),
                    "case_sensitive": bool(item.get("case_sensitive", False)),
                    "variants": [str(v).strip() for v in variants if str(v).strip()]
                    if isinstance(variants, list)
                    else [],
                }
            )
        merged["term_entries"] = clean_term_entries

    pairs = supplied.get("pronoun_pairs")
    if isinstance(pairs, list):
        pronoun_pairs = [p for p in pairs if isinstance(p, dict)]

    pronouns = supplied.get("pronouns")
    if isinstance(pronouns, list):
        clean_pronouns: list[dict[str, Any]] = []
        for item in pronouns:
            if not isinstance(item, dict):
                continue
            raw_pair = item.get("pair")
            a_name = str(raw_pair[0]).strip() if isinstance(raw_pair, list) and len(raw_pair) >= 2 else str(item.get("a") or "").strip()
            b_name = str(raw_pair[1]).strip() if isinstance(raw_pair, list) and len(raw_pair) >= 2 else str(item.get("b") or "").strip()
            a_to_b = str(item.get("a_to_b") or "").strip()
            b_to_a = str(item.get("b_to_a") or "").strip()
            if not (a_name and b_name and a_to_b and b_to_a):
                continue
            entry = {
                "pair": [a_name, b_name],
                "a_to_b": a_to_b,
                "b_to_a": b_to_a,
                "basis": str(item.get("basis") or "").strip(),
                "changes": [c for c in (item.get("changes") or []) if isinstance(c, dict)]
                if isinstance(item.get("changes"), list)
                else [],
                "provisional": bool(item.get("provisional", False)),
            }
            clean_pronouns.append(entry)
            pronoun_pairs.append(
                {
                    "a": a_name,
                    "b": b_name,
                    "a_to_b": a_to_b,
                    "b_to_a": b_to_a,
                    "basis": entry["basis"],
                }
            )
        merged["pronouns"] = clean_pronouns

    merged["characters"] = chars_map
    merged["terms"] = terms_map
    merged["pronoun_pairs"] = pronoun_pairs
    if isinstance(supplied.get("style_notes"), str) and supplied["style_notes"].strip():
        merged["style_notes"] = supplied["style_notes"].strip()

    inj = detect_prompt_injection(json.dumps(merged, ensure_ascii=False)[:16000])
    return merged, inj


class ServiceError(RuntimeError):
    """Structured domain error carrying an HTTP status code and optional details."""

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


def _render_final_txt(translation_xml: str) -> str:
    blocks = [b.strip() for b in TRANS_TAG_RE.findall(translation_xml) if b.strip()]
    return ("\n\n".join(blocks) + "\n") if blocks else ""


def _merge_proposals_into_record(
    book_record: dict[str, Any],
    proposals: list[dict[str, Any]],
) -> dict[str, Any]:
    merged = json.loads(json.dumps(book_record or {}))
    chars = merged.setdefault("characters", {})
    terms = merged.setdefault("terms", {})
    for item in proposals:
        if not isinstance(item, dict):
            continue
        src = str(item.get("source") or "").strip()
        tgt = str(item.get("target") or item.get("vietnamese") or "").strip()
        if not src or not tgt:
            continue
        cat = str(item.get("category") or "term").lower()
        if "char" in cat or "name" in cat or "person" in cat:
            chars.setdefault(src, tgt)
        else:
            terms.setdefault(src, tgt)
    return merged


def _sanitize_submission_public(sub: dict[str, Any]) -> dict[str, Any]:
    out = dict(sub)
    out.pop("revision_token_sha256", None)
    out.pop("author_ip_hash", None)
    return out


class CollabMcpService:
    """Domain orchestrator for book queries, 3-tier submissions, review-server rounds, and git sync."""

    def __init__(
        self,
        *,
        cfg: McpServerConfig,
        store: CollabStore,
        review_client: Any,
        git_sync: CollabBooksGitSync,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.review_client = review_client
        self.git_sync = git_sync
        self.rate_limiter = SlidingWindowRateLimiter()
        self._seed_lock = threading.Lock()
        self._active_seeds: set[str] = set()
        self._seed_errors: dict[str, str] = {}

    def _check_read_rate(self, ctx: AuthContext) -> None:
        if ctx.tier == "ANONYMOUS":
            self.rate_limiter.check(
                f"read:{ctx.actor_id}",
                limit=self.cfg.anon_read_rpm,
                window_seconds=60.0,
            )

    def _check_write_rate(self, ctx: AuthContext) -> None:
        if ctx.tier == "ADMIN":
            return
        limit = self.cfg.anon_write_rpm if ctx.tier == "ANONYMOUS" else self.cfg.agent_write_rpm
        self.rate_limiter.check(
            f"write:{ctx.actor_id}",
            limit=limit,
            window_seconds=60.0,
        )

    def _can_access_book_work(self, ctx: AuthContext, book: dict[str, Any]) -> bool:
        if ctx.tier == "ADMIN":
            return True
        if str(book.get("access_mode") or "public") == "public":
            return True
        allowed = set(book.get("allowed_agents") or [])
        return ctx.tier == "AGENT_KEY" and ctx.handle in allowed

    def _assert_book_work_access(self, ctx: AuthContext, book: dict[str, Any]) -> None:
        if not self._can_access_book_work(ctx, book):
            raise ServiceError(
                (
                    f"Book '{book['slug']}' is restricted to allowlisted AGENT_KEYs. "
                    "Call register_agent() to get your 3-word AGENT_KEY and share it with the admin to request access."
                ),
                status_code=403,
            )

    def _maybe_auto_assign_agent(
        self,
        ctx: AuthContext,
        *,
        model_info: str = "",
    ) -> tuple[AuthContext, Optional[dict[str, str]]]:
        if ctx.tier != "ANONYMOUS":
            return ctx, None
        reg = self.store.register_agent(handle="", display_name="", model_info=model_info)
        new_ctx = AuthContext(
            tier="AGENT_KEY",
            actor_id=f"agent:{reg['handle']}",
            handle=reg["handle"],
            client_ip_hash=ctx.client_ip_hash,
        )
        return new_ctx, {
            "assigned_agent_key": reg["agent_key"],
            "assigned_handle": reg["handle"],
            "auth_notice": (
                "An easy-to-read 3-word AGENT_KEY was automatically assigned to you. "
                "Save it and pass agent_key=... (or Authorization: Bearer ...) on future calls."
            ),
        }

    def register_agent(
        self,
        ctx: AuthContext,
        *,
        handle: str = "",
        display_name: str = "",
        model_info: str = "",
    ) -> dict[str, str]:
        self._check_write_rate(ctx)
        try:
            return self.store.register_agent(
                handle=handle,
                display_name=display_name,
                model_info=model_info,
            )
        except ValueError as exc:
            status = 409 if "already registered" in str(exc) else 400
            raise ServiceError(str(exc), status_code=status) from exc

    def list_books(self, ctx: AuthContext, *, include_pending: bool = False) -> list[dict[str, Any]]:
        self._check_read_rate(ctx)
        books = self.store.list_books(include_pending=include_pending or ctx.tier == "ADMIN")
        summary: list[dict[str, Any]] = []
        for b in books:
            chapters = self.store.list_chapters(b["slug"])
            subs = self.store.list_submissions(book_slug=b["slug"])
            done_cnt = sum(1 for c in chapters if c["status"] == "accepted")
            open_cnt = sum(1 for c in chapters if c["status"] == "open")
            pending_approvals = sum(
                1 for s in subs if s["status"] in ACTIONABLE_SUBMISSION_STATUSES
            )
            allowed = list(b.get("allowed_agents") or [])
            proposed_ch = list(b.get("proposed_chapters") or [])
            item: dict[str, Any] = {
                "slug": b["slug"],
                "title": b["title"],
                "author": b["author"],
                "source_lang": b["source_lang"],
                "target_lang": b["target_lang"],
                "genre": b["genre"],
                "license": b["license_id"],
                "status": b["status"],
                "access_mode": str(b.get("access_mode") or "public"),
                "approval_mode": str(b.get("approval_mode") or "auto"),
                "allowed_agent_count": len(allowed),
                "pending_approvals": pending_approvals,
                "proposed_chapter_count": len(proposed_ch),
                "glossary_sha": b["glossary_sha"],
                "total_chapters": len(chapters),
                "accepted_chapters": done_cnt,
                "open_chapters": open_cnt,
                "release_tag": b["release_tag"],
                "release_url": b["release_url"],
            }
            if ctx.tier == "ADMIN":
                br = b.get("book_record") or {}
                item["allowed_agents"] = allowed
                item["proposed_by"] = b["proposed_by"]
                item["license_url"] = b["license_url"]
                item["source_filename"] = b.get("source_filename") or ""
                item["source_preview"] = (b.get("source_text") or "")[:2000]
                item["proposed_chapters_summary"] = [
                    {
                        "chapter_id": str(c.get("chapter_id") or ""),
                        "title": str(c.get("title") or ""),
                    }
                    for c in proposed_ch[:25]
                ]
                item["initial_glossary_counts"] = {
                    "characters": len(br.get("characters") or {}),
                    "terms": len(br.get("terms") or {}),
                    "pronoun_pairs": len(br.get("pronoun_pairs") or []),
                }
            summary.append(item)
        return summary

    def get_book(self, ctx: AuthContext, *, book_slug: str) -> dict[str, Any]:
        self._check_read_rate(ctx)
        book = self.store.get_book(book_slug)
        if book is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        can_work = self._can_access_book_work(ctx, book)
        subs = self.store.list_submissions(book_slug=book_slug) if can_work else []
        subs_by_ch: dict[str, int] = {}
        pending_additions: list[dict[str, Any]] = []
        for s in subs:
            subs_by_ch[s["chapter_id"]] = subs_by_ch.get(s["chapter_id"], 0) + 1
            if s["status"] in {"pending_review", "pending_admin_approval", "remediation_required"}:
                props = (s.get("additions") or {}).get("proposals") or []
                for p in props:
                    if isinstance(p, dict):
                        pending_additions.append({"chapter_id": s["chapter_id"], "submission_id": s["id"], **p})

        chapters = [
            {
                "chapter_id": c["chapter_id"],
                "chapter_num": c["chapter_num"],
                "title": c["title"],
                "status": c["status"],
                "claimed_by": c["claimed_by"],
                "claim_expires_at": c["claim_expires_at"],
                "accepted_submission_id": c.get("accepted_submission_id") or "",
                "submission_count": subs_by_ch.get(c["chapter_id"], 0),
            }
            for c in self.store.list_chapters(book_slug)
        ]
        out = dict(book)
        out.pop("source_text", None)
        proposed_ch = list(out.pop("proposed_chapters", None) or [])
        out["proposed_chapter_count"] = len(proposed_ch)
        allowed = list(out.get("allowed_agents") or [])
        out["allowed_agent_count"] = len(allowed)
        if ctx.tier != "ADMIN":
            out.pop("allowed_agents", None)
        with self._seed_lock:
            out["seeding_in_progress"] = book_slug in self._active_seeds
            last_seed_err = self._seed_errors.get(book_slug, "")
        if ctx.tier == "ADMIN":
            out["last_seed_error"] = last_seed_err
        out["brief"] = book.get("brief") or {}
        out["collab_ledger"] = self.git_sync.read_ledger(book_slug) if can_work else {}
        out["pending_additions"] = pending_additions
        out["chapters"] = chapters
        return out

    def get_book_source(self, ctx: AuthContext, *, book_slug: str) -> dict[str, Any]:
        """Return full raw source text, pre-split proposal chapters, manifest, brief, and book_record."""
        self._check_read_rate(ctx)
        book = self.store.get_book(book_slug)
        if book is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        self._assert_book_work_access(ctx, book)
        return {
            "slug": book["slug"],
            "title": book["title"],
            "author": book["author"],
            "source_lang": book["source_lang"],
            "target_lang": book["target_lang"],
            "genre": book["genre"],
            "status": book["status"],
            "approval_mode": book["approval_mode"],
            "source_filename": book.get("source_filename") or "source.md",
            "source_text": book.get("source_text") or "",
            "proposed_chapters": book.get("proposed_chapters") or [],
            "manifest": book.get("manifest") or {},
            "brief": book.get("brief") or {},
            "book_record": book.get("book_record") or {},
            "glossary_sha": book.get("glossary_sha") or "",
        }

    def list_tasks(
        self,
        ctx: AuthContext,
        *,
        role: str = "all",
        book_slug: Optional[str] = None,
    ) -> dict[str, Any]:
        self._check_read_rate(ctx)
        now = time.time()
        is_coord = ctx.tier == "ADMIN" or (ctx.tier == "AGENT_KEY" and ctx.agent_role == "agent_admin")
        books = [self.store.get_book(book_slug)] if book_slug else self.store.list_books(include_pending=is_coord)
        open_chapters: list[dict[str, Any]] = []
        review_targets: list[dict[str, Any]] = []
        revisions_needed: list[dict[str, Any]] = []
        books_pending_parse: list[dict[str, Any]] = []
        books_pending_seed: list[dict[str, Any]] = []

        for b in books:
            if not b:
                continue
            if not self._can_access_book_work(ctx, b):
                continue
            slug = b["slug"]
            subs = self.store.list_submissions(book_slug=slug)

            if is_coord and role in {"all", "agent_admin"}:
                if b["status"] == "approved_pending_parse":
                    books_pending_parse.append(
                        {
                            "slug": slug,
                            "title": b["title"],
                            "author": b["author"],
                            "source_lang": b["source_lang"],
                            "target_lang": b["target_lang"],
                            "genre": b["genre"],
                            "source_bytes": len((b.get("source_text") or "").encode("utf-8")),
                            "proposed_chapter_count": len(b.get("proposed_chapters") or []),
                        }
                    )
                elif b["status"] == "seeding":
                    ch01_subs = [s for s in subs if s["chapter_id"] == "ch01" and s["status"] in {"pending_review", "pending_admin_approval", "accepted"}]
                    if not ch01_subs:
                        books_pending_seed.append(
                            {
                                "slug": slug,
                                "title": b["title"],
                                "author": b["author"],
                                "source_lang": b["source_lang"],
                                "total_chapters": len(self.store.list_chapters(slug)),
                            }
                        )

            if b["status"] not in {"active", "published", "seeding"}:
                continue
            pending_gate_chapters = {
                s["chapter_id"]
                for s in subs
                if s["status"] in {"evaluating", "pending_review", "pending_admin_approval"}
            }
            if b["status"] in {"active", "published"} and role in {"all", "translator"}:
                for c in self.store.list_chapters(slug):
                    if c["status"] != "open" or c["chapter_id"] in pending_gate_chapters:
                        continue
                    claimed_other = (
                        bool(c["claimed_by"])
                        and c["claimed_by"] != ctx.actor_id
                        and float(c["claim_expires_at"]) > now
                    )
                    open_chapters.append(
                        {
                            "book_slug": slug,
                            "title": b["title"],
                            "source_lang": b["source_lang"],
                            "approval_mode": b["approval_mode"],
                            "chapter_id": c["chapter_id"],
                            "chapter_title": c["title"],
                            "claimed": claimed_other,
                            "claimed_by": c["claimed_by"] if claimed_other else "",
                        }
                    )
            for sub in subs:
                if (
                    b["status"] in {"active", "published", "seeding"}
                    and role in {"all", "translator"}
                    and sub["author_actor_id"] == ctx.actor_id
                    and sub["status"] == "remediation_required"
                ):
                    revisions_needed.append(_sanitize_submission_public(sub))
                if (
                    role in {"all", "reviewer"}
                    and sub["author_actor_id"] != ctx.actor_id
                    and sub["status"] in {"pending_review", "remediation_required", "pending_admin_approval", "accepted"}
                ):
                    review_targets.append(_sanitize_submission_public(sub))

        review_targets.sort(key=lambda s: _SUBMISSION_SORT_RANK.get(str(s.get("status") or ""), 99))
        return {
            "caller": {"tier": ctx.tier, "handle": ctx.handle, "agent_role": ctx.agent_role},
            "books_pending_parse": books_pending_parse,
            "books_pending_seed": books_pending_seed,
            "open_chapters": open_chapters,
            "revisions_needed": revisions_needed,
            "peer_review_candidates": review_targets[:25],
        }

    def _build_chapter_neighbors(
        self,
        *,
        book_slug: str,
        chapter_id: str,
        neighbor_segments: int = 5,
    ) -> dict[str, Any]:
        n_segs = max(1, min(int(neighbor_segments), 25))
        all_chs = self.store.list_chapters(book_slug)
        idx = next((i for i, c in enumerate(all_chs) if c["chapter_id"] == chapter_id), -1)
        prev_info: Optional[dict[str, Any]] = None
        next_info: Optional[dict[str, Any]] = None
        if idx > 0:
            p_ch = all_chs[idx - 1]
            prev_info = {
                "chapter_id": p_ch["chapter_id"],
                "title": p_ch["title"],
                "status": p_ch["status"],
                "source_tail": _extract_segment_slice(p_ch["source_xml"], count=n_segs, from_end=True, field="original"),
                "translated_tail": _extract_segment_slice(p_ch.get("translation_xml") or "", count=n_segs, from_end=True, field="translate"),
            }
        if 0 <= idx < len(all_chs) - 1:
            n_ch = all_chs[idx + 1]
            next_info = {
                "chapter_id": n_ch["chapter_id"],
                "title": n_ch["title"],
                "status": n_ch["status"],
                "source_head": _extract_segment_slice(n_ch["source_xml"], count=n_segs, from_end=False, field="original"),
            }
        seed_excerpt = ""
        ch01 = next((c for c in all_chs if c["chapter_id"] == "ch01"), None)
        if ch01 and ch01["status"] == "accepted" and ch01.get("translation_xml"):
            seed_excerpt = _extract_segment_slice(ch01["translation_xml"], count=n_segs, from_end=False, field="translate")
        return {
            "prev_chapter": prev_info,
            "next_chapter": next_info,
            "seed_ch01_excerpt": seed_excerpt,
        }

    def get_chapter(
        self,
        ctx: AuthContext,
        *,
        book_slug: str,
        chapter_id: str,
        include_neighbors: bool = True,
        neighbor_segments: int = 5,
    ) -> dict[str, Any]:
        self._check_read_rate(ctx)
        book = self.store.get_book(book_slug)
        if book is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        self._assert_book_work_access(ctx, book)
        ch = self.store.get_chapter(book_slug, chapter_id)
        if ch is None:
            raise ServiceError(f"Chapter '{book_slug}/{chapter_id}' not found", status_code=404)
        ch_subs = [
            {
                "id": s["id"],
                "author_handle": s["author_handle"],
                "engine_model": s["engine_model"],
                "status": s["status"],
                "round_number": s["round_number"],
                "judge_summary": s["judge_summary"],
                "updated_at": s["updated_at"],
            }
            for s in self.store.list_submissions(book_slug=book_slug, chapter_id=chapter_id)
        ]
        out: dict[str, Any] = {
            "book_slug": book_slug,
            "book_title": book["title"],
            "source_lang": book["source_lang"],
            "target_lang": book["target_lang"],
            "approval_mode": book["approval_mode"],
            "glossary_sha": book["glossary_sha"],
            "brief": book.get("brief") or {},
            "book_record": book["book_record"],
            "chapter_id": ch["chapter_id"],
            "chapter_num": ch["chapter_num"],
            "title": ch["title"],
            "status": ch["status"],
            "claimed_by": ch.get("claimed_by") or "",
            "claim_expires_at": ch.get("claim_expires_at") or 0,
            "source_xml": ch["source_xml"],
            "translation_xml": ch.get("translation_xml") or "",
            "final_txt": ch["final_txt"],
            "submissions": ch_subs,
        }
        if include_neighbors:
            out["neighbors"] = self._build_chapter_neighbors(
                book_slug=book_slug,
                chapter_id=chapter_id,
                neighbor_segments=neighbor_segments,
            )
        return out

    def claim_chapter(
        self,
        ctx: AuthContext,
        *,
        book_slug: str,
        chapter_id: str,
    ) -> dict[str, Any]:
        self._check_write_rate(ctx)
        book = self.store.get_book(book_slug)
        if book is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        self._assert_book_work_access(ctx, book)
        eff_ctx, auto_meta = self._maybe_auto_assign_agent(ctx)

        claim_token = f"cbt_cl_{secrets.token_hex(16)}"
        expires_at = time.time() + self.cfg.claim_ttl_seconds
        try:
            updated = self.store.claim_chapter(
                book_slug=book_slug,
                chapter_id=chapter_id,
                actor_id=eff_ctx.actor_id,
                claim_token_sha256=sha256_hex(claim_token),
                expires_at=expires_at,
            )
        except KeyError as exc:
            raise ServiceError(str(exc), status_code=404) from exc
        except PermissionError as exc:
            raise ServiceError(str(exc), status_code=409) from exc
        except ValueError as exc:
            raise ServiceError(str(exc), status_code=400) from exc

        result: dict[str, Any] = {
            "book_slug": book_slug,
            "chapter_id": chapter_id,
            "claimed_by": updated["claimed_by"],
            "claim_token": claim_token,
            "claim_expires_at": updated["claim_expires_at"],
        }
        if auto_meta:
            result.update(auto_meta)
        return result

    def submit_book_proposal(
        self,
        ctx: AuthContext,
        *,
        slug: str,
        title: str,
        author: str,
        source_lang: str,
        target_lang: str = "vi",
        genre: str = "fiction",
        license_id: str,
        license_url: str,
        source_filename: str,
        source_text: str = "",
        chapters: Optional[list[dict[str, Any]]] = None,
        book_record: Optional[dict[str, Any]] = None,
        brief: Optional[dict[str, Any]] = None,
        approval_mode: Optional[str] = None,
    ) -> dict[str, Any]:
        self._check_write_rate(ctx)
        clean_slug = slug.strip().lower()
        if not SLUG_RE.match(clean_slug) or len(clean_slug) > 64:
            raise ServiceError("Invalid book slug (expected lowercase kebab-case <= 64 chars)", status_code=400)
        if source_lang not in SUPPORTED_SOURCE_LANGS or target_lang != "vi":
            raise ServiceError("Unsupported language pair (supported: en->vi, zh->vi)", status_code=400)
        if license_id not in ALLOWED_LICENSES:
            raise ServiceError(f"Disallowed license '{license_id}'", status_code=400)
        if not license_url.strip().startswith("https://"):
            raise ServiceError("license_url must be a valid https:// URL", status_code=400)
        eff_approval_mode = _normalize_approval_mode(approval_mode) if approval_mode else "peer"

        norm_chapters, ch_bytes, ch_scan_text = _normalize_proposed_chapters(chapters)
        raw_bytes = len(source_text.encode("utf-8"))
        total_bytes = raw_bytes + ch_bytes
        if total_bytes == 0:
            raise ServiceError("Either source_text or non-empty chapters is required", status_code=400)
        if total_bytes > MAX_BOOK_PROPOSAL_BYTES:
            raise ServiceError("Book proposal payload exceeds 5 MiB cap via MCP", status_code=413)
        if self.store.get_book(clean_slug) is not None:
            raise ServiceError(f"Book '{clean_slug}' already exists", status_code=409)

        base_record = {
            "title": title.strip(),
            "author": author.strip(),
            "source_lang": source_lang,
            "target_lang": target_lang,
            "genre": genre.strip() or "fiction",
            "characters": {},
            "terms": {},
            "pronoun_pairs": [],
        }
        merged_record, rec_inj = _merge_initial_book_record(base_record, book_record)
        norm_brief, brief_inj = _normalize_brief(brief, default_genre=genre.strip() or "fiction")
        inj = detect_prompt_injection(f"{title}\n{author}\n{source_text[:32000]}\n{ch_scan_text}") + rec_inj + brief_inj
        init_status = "flagged_security" if inj else "pending_approval"
        manifest = {
            "slug": clean_slug,
            "title": title.strip(),
            "author": author.strip(),
            "source_lang": source_lang,
            "target_lang": target_lang,
            "genre": genre.strip() or "fiction",
            "license": license_id,
            "license_url": license_url.strip(),
        }
        book = self.store.upsert_book(
            slug=clean_slug,
            title=title.strip(),
            author=author.strip(),
            source_lang=source_lang,
            target_lang=target_lang,
            genre=genre.strip() or "fiction",
            license_id=license_id,
            license_url=license_url.strip(),
            status=init_status,
            glossary_sha="",
            book_record=merged_record,
            brief=norm_brief,
            approval_mode=eff_approval_mode,
            manifest=manifest,
            proposed_by=ctx.handle,
            source_text=source_text,
            source_filename=source_filename.strip() or f"{clean_slug}.md",
            proposed_chapters=norm_chapters,
        )
        out = dict(book)
        out.pop("source_text", None)
        out.pop("proposed_chapters", None)
        out["proposed_chapter_count"] = len(norm_chapters)
        return out

    def submit_book_parse(
        self,
        ctx: AuthContext,
        *,
        book_slug: str,
        chapters: Any,
        brief: Any = None,
        book_record: Any = None,
        parse_notes: str = "",
    ) -> dict[str, Any]:
        """Accept parsed chapters, Brief, and initial book_record from agent_admin or ADMIN."""
        self._check_write_rate(ctx)
        assert_agent_admin_or_admin(ctx)
        book = self.store.get_book(book_slug)
        if book is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        self._assert_book_work_access(ctx, book)
        if book["status"] not in {"approved_pending_parse", "seeding", "seed_failed"}:
            raise ServiceError(
                f"Book '{book_slug}' in status '{book['status']}' cannot be parsed",
                status_code=409,
            )
        existing_chs = self.store.list_chapters(book_slug)
        if book.get("glossary_sha") or any(c["status"] == "accepted" for c in existing_chs):
            raise ServiceError(
                f"Cannot re-parse '{book_slug}': ch01 is already accepted and glossary_sha is locked",
                status_code=409,
            )

        norm_chapters, ch_bytes, ch_scan_text = _normalize_proposed_chapters(chapters)
        if not norm_chapters:
            raise ServiceError("chapters must contain at least one chapter object", status_code=400)
        if ch_bytes > MAX_BOOK_PROPOSAL_BYTES:
            raise ServiceError("Parsed chapters payload exceeds 5 MiB cap", status_code=413)

        norm_brief, brief_inj = _normalize_brief(brief, default_genre=book.get("genre") or "fiction")
        merged_record, rec_inj = _merge_initial_book_record(book.get("book_record") or {}, book_record)
        inj = detect_prompt_injection(f"{ch_scan_text}\n{parse_notes[:4000]}") + brief_inj + rec_inj
        if inj:
            raise ServiceError(
                "Parsed book package rejected by prompt-injection scanner",
                status_code=422,
                details={"defects": inj},
            )

        extracted = self.git_sync.prepare_book_files(
            slug=book_slug,
            manifest=book["manifest"],
            source_filename=book.get("source_filename") or f"{book_slug}.md",
            source_text=book.get("source_text") or "",
            proposed_chapters=norm_chapters,
            initial_book_record=merged_record,
            initial_brief=norm_brief,
            overwrite_record=True,
        )
        if not extracted:
            raise ServiceError(f"Zero chapters extracted for '{book_slug}'", status_code=422)

        self.store.replace_unaccepted_chapters(book_slug=book_slug, chapters=extracted)
        self.store.set_book_brief_and_record(
            slug=book_slug,
            brief=norm_brief,
            book_record=merged_record,
        )
        self.store.set_book_status(slug=book_slug, status="seeding")
        res = self.get_book(ctx, book_slug=book_slug)
        res["parse_notes"] = parse_notes.strip()
        return res

    def update_book_record(
        self,
        ctx: AuthContext,
        *,
        book_slug: str,
        brief: Any = None,
        book_record: Any = None,
        additions: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Curate Brief and book_record (full update before ch01 lock; additive additions afterward)."""
        self._check_write_rate(ctx)
        assert_agent_admin_or_admin(ctx)
        book = self.store.get_book(book_slug)
        if book is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        self._assert_book_work_access(ctx, book)

        locked = bool(book.get("glossary_sha")) or any(
            c["status"] == "accepted" for c in self.store.list_chapters(book_slug)
        )
        if locked and ctx.tier != "ADMIN" and (brief is not None or book_record is not None):
            raise ServiceError(
                "glossary_sha is already locked; agent_admin may only submit additive additions",
                status_code=409,
            )

        eff_brief = book.get("brief") or {}
        inj_all: list[str] = []
        if brief is not None:
            eff_brief, b_inj = _normalize_brief(brief, default_genre=book.get("genre") or "fiction")
            inj_all.extend(b_inj)

        eff_record = json.loads(json.dumps(book.get("book_record") or {}))
        if book_record is not None:
            base_rec = {
                "title": book["title"],
                "author": book["author"],
                "source_lang": book["source_lang"],
                "target_lang": book["target_lang"],
                "genre": book["genre"],
                "characters": {},
                "terms": {},
                "pronoun_pairs": [],
            }
            eff_record, r_inj = _merge_initial_book_record(base_rec, book_record)
            inj_all.extend(r_inj)

        if additions is not None:
            if not isinstance(additions, dict):
                raise ServiceError("additions must be a JSON object", status_code=400)
            inj_all.extend(detect_prompt_injection(json.dumps(additions, ensure_ascii=False)[:16000]))
            proposals = additions.get("proposals") or []
            if isinstance(proposals, list):
                eff_record = _merge_proposals_into_record(eff_record, proposals)

        if inj_all:
            raise ServiceError(
                "Book record update rejected by prompt-injection scanner",
                status_code=422,
                details={"defects": inj_all},
            )

        self.store.set_book_brief_and_record(
            slug=book_slug,
            brief=eff_brief,
            book_record=eff_record,
        )
        self.git_sync.write_brief_and_record(slug=book_slug, brief=eff_brief, book_record=eff_record)
        return self.get_book(ctx, book_slug=book_slug)

    def submit_translation(
        self,
        ctx: AuthContext,
        *,
        book_slug: str,
        chapter_id: str,
        translation_xml: str,
        engine_model: str = "mcp-agent",
        additions: Optional[dict[str, Any]] = None,
        submission_id: Optional[str] = None,
        revision_token: Optional[str] = None,
        rebuttals: Optional[list[dict[str, str]]] = None,
        hold_for_review: bool = False,
        background: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """Validate, scan, run review.vinhthang.dev round, and auto-sync on PASS."""
        self._check_write_rate(ctx)

        xml_bytes = translation_xml.encode("utf-8")
        if len(xml_bytes) > MAX_MCP_XML_BYTES:
            raise ServiceError(
                f"translation_xml size ({len(xml_bytes)} bytes) exceeds {MAX_MCP_XML_BYTES}-byte cap",
                status_code=413,
            )

        book = self.store.get_book(book_slug)
        if book is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        self._assert_book_work_access(ctx, book)
        ch = self.store.get_chapter(book_slug, chapter_id)
        if ch is None:
            raise ServiceError(f"Chapter '{book_slug}/{chapter_id}' not found", status_code=404)
        if ch["status"] != "open":
            raise ServiceError(
                f"Chapter '{book_slug}/{chapter_id}' is '{ch['status']}', not 'open'",
                status_code=409,
            )

        # Verify ownership on R2+ BEFORE touching LLM!
        existing_sub: Optional[dict[str, Any]] = None
        returned_rev_token: Optional[str] = None
        if submission_id:
            existing_sub = self.store.get_submission(submission_id)
            if existing_sub is None:
                raise ServiceError(f"Submission '{submission_id}' not found", status_code=404)
            if existing_sub["book_slug"] != book_slug or existing_sub["chapter_id"] != chapter_id:
                raise ServiceError("submission_id does not match book_slug/chapter_id", status_code=400)
            if ctx.tier != "ADMIN":
                is_same_agent = (
                    ctx.tier == "AGENT_KEY"
                    and existing_sub["author_actor_id"] == ctx.actor_id
                )
                if not is_same_agent:
                    verify_revision_token(revision_token, existing_sub["revision_token_sha256"])
                if existing_sub["status"] in {"evaluating", "escalated_to_admin", "pending_review", "pending_admin_approval", "accepted", "rejected"} and ctx.tier != "ADMIN":
                    raise ServiceError(
                        f"Submission is in terminal/review state '{existing_sub['status']}'",
                        status_code=409,
                    )
            if int(existing_sub["round_number"]) >= int(existing_sub["max_rounds"]) and ctx.tier != "ADMIN":
                raise ServiceError(
                    f"Submission exhausted max_rounds ({existing_sub['max_rounds']})",
                    status_code=409,
                )
        elif ctx.tier != "ADMIN":
            existing_for_ch = self.store.list_submissions(book_slug=book_slug, chapter_id=chapter_id)
            if any(s["status"] in {"evaluating", "pending_review", "pending_admin_approval"} for s in existing_for_ch):
                raise ServiceError(
                    f"Chapter '{book_slug}/{chapter_id}' already has a passing translation awaiting review or approval",
                    status_code=409,
                )

        norm_additions = additions if additions is not None else {"proposals": []}

        # Deterministic XML parity, code/image byte-invariance, additions schema, and 4-class prompt-injection scan
        segs = parse_segments(ch["source_xml"])
        seg_range = f"{segs[0]['id']}..{segs[-1]['id']}" if len(segs) > 1 else (segs[0]["id"] if segs else "S-0001")
        is_seed = chapter_id == "ch01"
        expected_sha = "seed" if is_seed else book["glossary_sha"]
        issue_val = "seed" if is_seed else "#1"
        synthetic_body = (
            "- **Agent**: mcpbot\n"
            f"- **Engine/Model**: {engine_model}\n"
            f"- **Source Language**: {book['source_lang']}\n"
            f"- **Target Language**: {book['target_lang']}\n"
            f"- **Book**: {book_slug}\n"
            f"- **Chunk ID**: {book_slug}-{chapter_id}\n"
            f"- **Issue**: {issue_val}\n"
            f"- **Glossary SHA**: {expected_sha}\n"
            f"- **Segments**: {seg_range}\n"
        )
        changed = [f"runs/{book_slug}/{chapter_id}/translation.xml"]
        if additions and additions.get("proposals"):
            changed.append(f"runs/{book_slug}/{chapter_id}/analyze_additions.json")

        val_res = validate_translation_pr(
            head_ref=f"translate/{book_slug}-{chapter_id}-mcpbot",
            author_login="mcpbot",
            body=synthetic_body,
            changed_files=changed,
            source_xml_text=ch["source_xml"],
            translation_xml_text=translation_xml,
            expected_glossary_sha=None if is_seed else expected_sha,
            additions_data=norm_additions if (additions and additions.get("proposals")) else None,
            manifest=book["manifest"],
            expected_issue_number=None if is_seed else 1,
            bot_login="mcpbot" if is_seed else None,
            is_seed_pr=is_seed,
            translation_xml_bytes=xml_bytes,
        )

        inj_flags: list[str] = list(val_res.get("injection_flags") or [])
        if rebuttals:
            inj_flags.extend(detect_prompt_injection(json.dumps(rebuttals, ensure_ascii=False)))

        if inj_flags:
            if existing_sub is None:
                raw_rt, rt_sha = mint_revision_token()
                returned_rev_token = raw_rt
                quarantined = self.store.create_submission(
                    book_slug=book_slug,
                    chapter_id=chapter_id,
                    author_actor_id=ctx.actor_id,
                    author_handle=ctx.handle,
                    engine_model=engine_model,
                    status="flagged_security",
                    round_number=0,
                    max_rounds=self.cfg.max_revise_rounds,
                    workstream_id="",
                    revision_token_sha256=rt_sha,
                    translation_xml=translation_xml,
                    final_txt="",
                    additions=norm_additions,
                    open_findings=[],
                    security_flags=inj_flags,
                    judge_summary="Quarantined by pre-LLM 4-class prompt-injection scanner.",
                )
            else:
                quarantined = self.store.update_submission(
                    existing_sub["id"],
                    status="flagged_security",
                    translation_xml=translation_xml,
                    additions=norm_additions,
                    security_flags=inj_flags,
                )
            resp: dict[str, Any] = {
                "submission": _sanitize_submission_public(quarantined),
                "quarantined": True,
            }
            if returned_rev_token:
                resp["revision_token"] = returned_rev_token
            return 202, resp

        if not val_res["valid"]:
            raise ServiceError(
                "Translation XML failed deterministic structural validation",
                status_code=422,
                details={"defects": val_res["defects"]},
            )

        lang_probe = enforce_language_detection(
            {"verdict": "PASS", "scores": {"fidelity": 5.0, "fluency": 5.0, "register": 5.0, "completeness": 5.0}, "issues": []},
            translation_xml,
            source_lang=book["source_lang"],
        )
        if lang_probe.get("verdict") != "PASS":
            raise ServiceError(
                "Translation failed Vietnamese target-language detection",
                status_code=422,
                details={"defects": [str(i) for i in lang_probe.get("issues", [])]},
            )

        # Auto-assign a readable 3-word AGENT_KEY on first anonymous Round-1 submission (only after pre-LLM gates pass!)
        eff_ctx = ctx
        auto_meta: Optional[dict[str, str]] = None
        if existing_sub is None and ctx.tier == "ANONYMOUS":
            eff_ctx, auto_meta = self._maybe_auto_assign_agent(ctx, model_info=engine_model)

        # Create or resume review.vinhthang.dev TRANSLATION_REVIEW workstream
        if existing_sub is None:
            ws = self.review_client.create_workstream(
                title=f"Translation Review: {book_slug}/{chapter_id} ({eff_ctx.handle})",
                max_rounds=self.cfg.max_revise_rounds,
            )
            ws_id = str(ws["id"])
            raw_rt, rt_sha = mint_revision_token()
            returned_rev_token = raw_rt
        else:
            ws_id = existing_sub["workstream_id"]
            if not ws_id:
                ws = self.review_client.create_workstream(
                    title=f"Translation Review: {book_slug}/{chapter_id} ({ctx.handle})",
                    max_rounds=self.cfg.max_revise_rounds,
                )
                ws_id = str(ws["id"])
            rt_sha = existing_sub["revision_token_sha256"]

        brief_section = ""
        if book.get("brief"):
            brief_section = (
                f"## Book Brief\n```json\n{json.dumps(book['brief'], ensure_ascii=False, indent=2)}\n```\n\n"
            )
        review_bundle = (
            f"# Chapter Translation Review — {book['title']} ({book_slug} / {chapter_id})\n\n"
            f"{brief_section}"
            f"## Canonical book_record.json\n```json\n{json.dumps(book['book_record'], ensure_ascii=False, indent=2)}\n```\n\n"
            f"## Submitted Bilingual Segments (translation.xml)\n```xml\n{translation_xml}\n```\n"
        )
        final_txt = _render_final_txt(translation_xml)
        app_mode = str(book.get("approval_mode") or "auto")
        prior_history = list(existing_sub.get("rounds_history") or []) if existing_sub else []

        def _persist_round_outcome(target_sub_id: Optional[str], round_res: dict[str, Any]) -> dict[str, Any]:
            verdict = round_res["verdict"]
            round_num = int(round_res["roundNumber"])
            if verdict == "PASS":
                hold_admin_seed = bool(hold_for_review) and ctx.tier == "ADMIN"
                if hold_admin_seed or (app_mode == "manual" and ctx.tier != "ADMIN"):
                    sub_status = "pending_admin_approval"
                elif app_mode == "peer" and ctx.tier != "ADMIN":
                    sub_status = "pending_review"
                else:
                    self.review_client.approve_workstream(workstream_id=ws_id)
                    sub_status = "accepted"
            else:
                sub_status = (
                    "escalated_to_admin"
                    if round_num >= self.cfg.max_revise_rounds or round_res["stageStatus"] == "ESCALATED_TO_HUMAN"
                    else "remediation_required"
                )

            updated_history = list(prior_history)
            updated_history.append(
                {
                    "round_number": round_num,
                    "type": "translation_round",
                    "engine_model": engine_model,
                    "rebuttals": rebuttals or [],
                    "verdict": verdict,
                    "stage_status": round_res["stageStatus"],
                    "p0_count": round_res["p0Count"],
                    "p1_count": round_res["p1Count"],
                    "p2_count": round_res["p2Count"],
                    "judge_summary": round_res["judgeSummary"],
                    "open_findings": round_res["openFindings"],
                    "translation_xml": translation_xml,
                }
            )

            if target_sub_id is None:
                saved = self.store.create_submission(
                    book_slug=book_slug,
                    chapter_id=chapter_id,
                    author_actor_id=eff_ctx.actor_id,
                    author_handle=eff_ctx.handle,
                    author_ip_hash=ctx.client_ip_hash,
                    engine_model=engine_model,
                    status=sub_status,
                    round_number=round_num,
                    max_rounds=self.cfg.max_revise_rounds,
                    workstream_id=ws_id,
                    revision_token_sha256=rt_sha,
                    translation_xml=translation_xml,
                    final_txt=final_txt,
                    additions=norm_additions,
                    open_findings=round_res["openFindings"],
                    security_flags=[],
                    judge_summary=round_res["judgeSummary"],
                    rounds_history=updated_history,
                )
            else:
                saved = self.store.update_submission(
                    target_sub_id,
                    status=sub_status,
                    round_number=round_num,
                    workstream_id=ws_id,
                    translation_xml=translation_xml,
                    final_txt=final_txt,
                    additions=norm_additions,
                    open_findings=round_res["openFindings"],
                    judge_summary=round_res["judgeSummary"],
                    rounds_history=updated_history,
                )

            if sub_status == "accepted":
                fresh_book = self.store.get_book(book_slug) or book
                fresh_ch = self.store.get_chapter(book_slug, chapter_id) or ch
                self._finalize_accepted_chapter(
                    book=fresh_book,
                    ch=fresh_ch,
                    submission=saved,
                )
            return saved

        if background:
            next_round_est = int(existing_sub["round_number"]) + 1 if existing_sub else 1
            if existing_sub is None:
                pending_sub = self.store.create_submission(
                    book_slug=book_slug,
                    chapter_id=chapter_id,
                    author_actor_id=eff_ctx.actor_id,
                    author_handle=eff_ctx.handle,
                    author_ip_hash=ctx.client_ip_hash,
                    engine_model=engine_model,
                    status="evaluating",
                    round_number=next_round_est,
                    max_rounds=self.cfg.max_revise_rounds,
                    workstream_id=ws_id,
                    revision_token_sha256=rt_sha,
                    translation_xml=translation_xml,
                    final_txt=final_txt,
                    additions=norm_additions,
                    open_findings=[],
                    security_flags=[],
                    judge_summary="Running TRANSLATION_REVIEW specialists on review.vinhthang.dev...",
                    rounds_history=prior_history,
                )
            else:
                pending_sub = self.store.update_submission(
                    existing_sub["id"],
                    status="evaluating",
                    workstream_id=ws_id,
                    translation_xml=translation_xml,
                    final_txt=final_txt,
                    additions=norm_additions,
                    judge_summary="Running TRANSLATION_REVIEW specialists on review.vinhthang.dev...",
                )
            target_id = str(pending_sub["id"])

            def _bg_worker() -> None:
                try:
                    bg_round = self.review_client.run_translation_round(
                        workstream_id=ws_id,
                        content=review_bundle,
                        rebuttals=rebuttals,
                    )
                    _persist_round_outcome(target_id, bg_round)
                except Exception as exc:
                    self.store.update_submission(
                        target_id,
                        status="remediation_required",
                        judge_summary=f"Background review round error: {exc}",
                    )

            threading.Thread(target=_bg_worker, daemon=True).start()
            bg_payload: dict[str, Any] = {
                "submission": _sanitize_submission_public(pending_sub),
                "evaluating_in_background": True,
            }
            if returned_rev_token:
                bg_payload["revision_token"] = returned_rev_token
            if auto_meta:
                bg_payload.update(auto_meta)
            return 202, bg_payload

        round_res = self.review_client.run_translation_round(
            workstream_id=ws_id,
            content=review_bundle,
            rebuttals=rebuttals,
        )
        saved_sub = _persist_round_outcome(existing_sub["id"] if existing_sub else None, round_res)

        out_payload: dict[str, Any] = {
            "submission": _sanitize_submission_public(saved_sub),
            "round": round_res,
        }
        if returned_rev_token:
            out_payload["revision_token"] = returned_rev_token
        if auto_meta:
            out_payload.update(auto_meta)
        return 200, out_payload

    def _finalize_accepted_chapter(
        self,
        *,
        book: dict[str, Any],
        ch: dict[str, Any],
        submission: dict[str, Any],
    ) -> None:
        slug = book["slug"]
        chapter_id = ch["chapter_id"]
        proposals = (submission.get("additions") or {}).get("proposals") or []
        updated_record = _merge_proposals_into_record(book["book_record"], proposals)
        glossary_sha = book["glossary_sha"]
        if chapter_id == "ch01" or not glossary_sha:
            canonical_bytes = json.dumps(updated_record, sort_keys=True, ensure_ascii=False).encode("utf-8")
            glossary_sha = hashlib.sha256(canonical_bytes).hexdigest()

        self.store.mark_chapter_accepted(
            book_slug=slug,
            chapter_id=chapter_id,
            submission_id=submission["id"],
            translation_xml=submission["translation_xml"],
            final_txt=submission["final_txt"],
        )

        # Unlock ch02..chNN if ch01 just finished seeding
        if chapter_id == "ch01":
            for other in self.store.list_chapters(slug):
                if other["chapter_id"] != "ch01" and other["status"] == "locked_seed":
                    self.store.upsert_chapter(
                        book_slug=slug,
                        chapter_id=other["chapter_id"],
                        chapter_num=int(other["chapter_num"]),
                        title=other["title"],
                        source_xml=other["source_xml"],
                        status="open",
                        opened_at=time.time(),
                    )

        all_chapters = self.store.list_chapters(slug)
        all_done = bool(all_chapters) and all(c["status"] == "accepted" for c in all_chapters)
        new_book_status = "published" if all_done else "active"

        peer_revs = self.store.list_peer_reviews(submission["id"])
        rev_handles = [r["reviewer_handle"] for r in peer_revs] + ["review-vinhthang-dev"]
        avg_score = (
            sum(float(r["weighted_score"]) for r in peer_revs) / len(peer_revs)
            if peer_revs
            else 4.5
        )

        self.store.upsert_book(
            slug=slug,
            title=book["title"],
            author=book["author"],
            source_lang=book["source_lang"],
            target_lang=book["target_lang"],
            genre=book["genre"],
            license_id=book["license_id"],
            license_url=book["license_url"],
            status=new_book_status,
            glossary_sha=glossary_sha,
            book_record=updated_record,
            manifest=book["manifest"],
            proposed_by=book["proposed_by"],
        )

        self.git_sync.sync_accepted_chapter(
            slug=slug,
            chapter_id=chapter_id,
            chapter_num=int(ch["chapter_num"]),
            source_xml=ch["source_xml"],
            translation_xml=submission["translation_xml"],
            final_txt=submission["final_txt"],
            book_record=updated_record,
            brief=book.get("brief") or None,
            glossary_sha=glossary_sha,
            translator_handle=submission["author_handle"],
            reviewer_handles=rev_handles,
            score_of_record=avg_score,
            all_chapters_status=all_chapters,
        )

        if all_done:
            rel_tag = f"{slug}-v1.0"
            rel_info = self.git_sync.compile_and_publish_release(
                slug=slug,
                title=book["title"],
                author=book["author"],
                release_tag=rel_tag,
            )
            latest_book = self.store.get_book(slug)
            if latest_book:
                self.store.upsert_book(
                    slug=slug,
                    title=latest_book["title"],
                    author=latest_book["author"],
                    source_lang=latest_book["source_lang"],
                    target_lang=latest_book["target_lang"],
                    genre=latest_book["genre"],
                    license_id=latest_book["license_id"],
                    license_url=latest_book["license_url"],
                    status="published",
                    glossary_sha=latest_book["glossary_sha"],
                    book_record=latest_book["book_record"],
                    manifest=latest_book["manifest"],
                    proposed_by=latest_book["proposed_by"],
                    release_tag=rel_info["release_tag"],
                    release_url=rel_info["release_url"],
                )

    def query_submissions(
        self,
        ctx: AuthContext,
        *,
        book_slug: Optional[str] = None,
        chapter_id: Optional[str] = None,
        status: Optional[str] = None,
        author_handle: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query sanitized submissions across books accessible to the caller."""
        self._check_read_rate(ctx)
        rows = self.store.list_submissions(
            book_slug=book_slug,
            chapter_id=chapter_id,
            status=status,
        )
        book_cache: dict[str, Optional[dict[str, Any]]] = {}
        capped = max(1, min(int(limit), 200))
        out: list[dict[str, Any]] = []
        for s in rows:
            slug = s["book_slug"]
            if slug not in book_cache:
                book_cache[slug] = self.store.get_book(slug)
            bk = book_cache[slug]
            if bk is None or not self._can_access_book_work(ctx, bk):
                continue
            if author_handle and s.get("author_handle") != author_handle.strip():
                continue
            item = _sanitize_submission_public(s)
            revs = self.store.list_peer_reviews(s["id"])
            item["peer_reviews"] = revs
            item["peer_review_count"] = len(revs)
            out.append(item)
            if len(out) >= capped:
                break
        return out

    def get_submission_status(
        self,
        ctx: AuthContext,
        *,
        submission_id: str,
        include_context: bool = True,
        include_workstream: bool = False,
    ) -> dict[str, Any]:
        self._check_read_rate(ctx)
        sub = self.store.get_submission(submission_id)
        if sub is None:
            raise ServiceError(f"Submission '{submission_id}' not found", status_code=404)
        book = self.store.get_book(sub["book_slug"])
        if book is not None:
            self._assert_book_work_access(ctx, book)
        reviews = self.store.list_peer_reviews(submission_id)
        out: dict[str, Any] = {
            "submission": _sanitize_submission_public(sub),
            "peer_reviews": reviews,
        }
        if include_context and book is not None:
            ch = self.store.get_chapter(sub["book_slug"], sub["chapter_id"])
            out["book_context"] = {
                "slug": book["slug"],
                "title": book["title"],
                "author": book["author"],
                "source_lang": book["source_lang"],
                "target_lang": book["target_lang"],
                "approval_mode": book["approval_mode"],
                "glossary_sha": book["glossary_sha"],
                "brief": book.get("brief") or {},
                "book_record": book["book_record"],
            }
            if ch is not None:
                out["chapter_context"] = {
                    "chapter_id": ch["chapter_id"],
                    "chapter_num": ch["chapter_num"],
                    "title": ch["title"],
                    "status": ch["status"],
                    "source_xml": ch["source_xml"],
                    "neighbors": self._build_chapter_neighbors(
                        book_slug=sub["book_slug"],
                        chapter_id=sub["chapter_id"],
                    ),
                }
        if include_workstream and sub.get("workstream_id") and hasattr(self.review_client, "get_workstream"):
            try:
                ws_raw = self.review_client.get_workstream(workstream_id=sub["workstream_id"])
                out["workstream"] = ws_raw.get("workstream") if isinstance(ws_raw, dict) and "workstream" in ws_raw and len(ws_raw) == 1 else ws_raw
            except Exception as exc:
                out["workstream"] = {"error": str(exc)}
        return out

    def submit_review(
        self,
        ctx: AuthContext,
        *,
        submission_id: str,
        body_md: str,
        verdict: str = "COMMENT",
        issues: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        self._check_write_rate(ctx)
        raw_v = (verdict or "COMMENT").strip().upper()
        if raw_v == "REQUEST_CHANGES":
            raw_v = "CHANGES_REQUESTED"
        if raw_v not in VALID_REVIEW_VERDICTS:
            raise ServiceError(
                "verdict must be 'COMMENT', 'APPROVE', or 'CHANGES_REQUESTED'",
                status_code=400,
            )
        if raw_v in {"APPROVE", "CHANGES_REQUESTED"} and ctx.tier == "ANONYMOUS":
            raise AuthError(
                "Register a 3-word AGENT_KEY before submitting APPROVE or CHANGES_REQUESTED verdicts",
                status_code=403,
            )

        sub = self.store.get_submission(submission_id)
        if sub is None:
            raise ServiceError(f"Submission '{submission_id}' not found", status_code=404)
        book = self.store.get_book(sub["book_slug"])
        if book is not None:
            self._assert_book_work_access(ctx, book)
        assert_not_self_review(ctx.actor_id, sub["author_actor_id"])
        if (
            ctx.tier == "ANONYMOUS"
            and sub.get("author_ip_hash")
            and sub["author_ip_hash"] == ctx.client_ip_hash
        ):
            raise AuthError(
                "Self-review is prohibited: anonymous reviewer matches submission origin IP",
                status_code=403,
            )

        norm_issues: list[dict[str, Any]] = []
        if issues is not None:
            if not isinstance(issues, list):
                raise ServiceError("issues must be a list of issue objects", status_code=400)
            for item in issues[:50]:
                if isinstance(item, dict):
                    norm_issues.append(
                        {
                            "segment_id": str(item.get("segment_id") or item.get("id") or "").strip(),
                            "severity": str(item.get("severity") or "major").strip().lower(),
                            "symptom": str(item.get("symptom") or item.get("problem") or "").strip(),
                            "suggestion": str(item.get("suggestion") or "").strip(),
                        }
                    )

        val = validate_review(
            pr_author_login=sub["author_actor_id"],
            review_author_login=ctx.actor_id,
            review_body=body_md,
        )
        inj_flags = list(val["injection_flags"]) + detect_prompt_injection(json.dumps(norm_issues, ensure_ascii=False))
        if inj_flags:
            raise ServiceError(
                "Peer review blocked by prompt-injection scanner",
                status_code=422,
                details={"defects": inj_flags},
            )
        if not val["valid"]:
            raise ServiceError(
                "Peer review failed 6-dimension rubric or segment-anchor validation",
                status_code=422,
                details={"defects": val["defects"]},
            )

        weighted = compute_weighted_total(val["scores"])
        has_critical = any(i.get("severity") == "critical" for i in norm_issues) or ("[critical]" in body_md.lower())
        app_mode = str((book or {}).get("approval_mode") or "auto")

        if raw_v == "APPROVE":
            if weighted < 4.0 or has_critical:
                raise ServiceError(
                    f"APPROVE verdict requires weighted_score >= 4.0 (got {weighted:.2f}) and zero critical issues",
                    status_code=422,
                )
            if sub["status"] == "pending_admin_approval" and app_mode == "manual" and ctx.tier != "ADMIN":
                raise ServiceError(
                    "Book requires Human /admin approval (approval_mode='manual')",
                    status_code=409,
                )

        saved = self.store.add_peer_review(
            submission_id=submission_id,
            reviewer_actor_id=ctx.actor_id,
            reviewer_handle=ctx.handle,
            reviewer_role=ctx.agent_role,
            verdict=raw_v,
            scores=val["scores"],
            weighted_score=weighted,
            issues=norm_issues,
            body_md=body_md,
        )

        latest_sub = sub
        if raw_v == "APPROVE" and book is not None:
            can_merge = sub["status"] == "pending_review" or (
                sub["status"] == "pending_admin_approval" and (app_mode == "peer" or ctx.tier == "ADMIN")
            )
            if can_merge:
                ch = self.store.get_chapter(sub["book_slug"], sub["chapter_id"])
                if ch is not None and ch["status"] != "accepted":
                    if sub.get("workstream_id"):
                        self.review_client.approve_workstream(
                            workstream_id=sub["workstream_id"],
                            override_escalated=False,
                        )
                    final_txt = sub["final_txt"] or _render_final_txt(sub["translation_xml"])
                    latest_sub = self.store.update_submission(
                        submission_id,
                        status="accepted",
                        final_txt=final_txt,
                    )
                    self._finalize_accepted_chapter(book=book, ch=ch, submission=latest_sub)
        elif raw_v == "CHANGES_REQUESTED":
            can_return = sub["status"] in {"pending_review", "remediation_required"} or (
                sub["status"] == "pending_admin_approval" and (app_mode == "peer" or ctx.tier == "ADMIN")
            )
            if can_return:
                peer_findings: list[dict[str, Any]] = []
                for idx, iss in enumerate(norm_issues, start=1):
                    peer_findings.append(
                        {
                            "findingKey": f"peer-review-{idx}",
                            "severity": "P0" if iss.get("severity") == "critical" else "P1",
                            "symptom": f"[{iss.get('segment_id') or 'chapter'}] {iss.get('symptom') or 'Peer review requested revision'}",
                            "fixRecommendation": iss.get("suggestion") or "Address peer reviewer critique.",
                            "status": "OPEN",
                        }
                    )
                if not peer_findings:
                    first_line = next((ln.strip() for ln in body_md.splitlines() if ln.strip()), "Peer review requested changes.")
                    peer_findings.append(
                        {
                            "findingKey": "peer-review-1",
                            "severity": "P1",
                            "symptom": first_line[:300],
                            "fixRecommendation": "Revise chapter segments per peer review notes.",
                            "status": "OPEN",
                        }
                    )
                hist = list(sub.get("rounds_history") or [])
                hist.append(
                    {
                        "round_number": sub["round_number"],
                        "type": "peer_review",
                        "reviewer_handle": ctx.handle,
                        "reviewer_role": ctx.agent_role,
                        "verdict": "CHANGES_REQUESTED",
                        "weighted_score": weighted,
                        "open_findings": peer_findings,
                    }
                )
                latest_sub = self.store.update_submission(
                    submission_id,
                    status="remediation_required",
                    open_findings=peer_findings,
                    judge_summary=f"Peer review by {ctx.handle} ({weighted:.2f}/5.0) requested changes.",
                    rounds_history=hist,
                )

        return {
            "review": saved,
            "submission": _sanitize_submission_public(latest_sub),
        }

    # --- Admin Operations ---

    def set_agent_role(
        self,
        ctx: AuthContext,
        *,
        identifier: str,
        role: str,
    ) -> dict[str, Any]:
        """Promote or demote a registered agent between 'contributor' and 'agent_admin' (Human ADMIN only)."""
        assert_admin(ctx)
        clean_role = (role or "").strip().lower()
        if clean_role not in VALID_AGENT_ROLES:
            raise ServiceError("role must be 'contributor' or 'agent_admin'", status_code=400)
        resolved_handle = self.store.resolve_agent_identifier(identifier)
        if resolved_handle is None:
            raise ServiceError(f"Agent '{identifier}' not found", status_code=404)
        return self.store.set_agent_role(handle=resolved_handle, role=clean_role)

    def list_agents(self, ctx: AuthContext) -> list[dict[str, Any]]:
        assert_admin(ctx)
        return self.store.list_agents()

    def admin_mint_agent(
        self,
        ctx: AuthContext,
        *,
        handle: str = "",
        display_name: str = "",
        model_info: str = "",
    ) -> dict[str, str]:
        assert_admin(ctx)
        try:
            return self.store.register_agent(
                handle=handle,
                display_name=display_name,
                model_info=model_info,
            )
        except ValueError as exc:
            status = 409 if "already registered" in str(exc) else 400
            raise ServiceError(str(exc), status_code=status) from exc

    def set_book_access(
        self,
        ctx: AuthContext,
        *,
        book_slug: str,
        access_mode: str,
        allowed_agents: Optional[list[str]] = None,
        approval_mode: Optional[str] = None,
    ) -> dict[str, Any]:
        assert_admin(ctx)
        mode = (access_mode or "").strip().lower()
        if mode not in {"public", "restricted"}:
            raise ServiceError("access_mode must be 'public' or 'restricted'", status_code=400)
        norm_approval: Optional[str] = None
        if approval_mode is not None and str(approval_mode).strip() != "":
            norm_approval = _normalize_approval_mode(str(approval_mode))
        book = self.store.get_book(book_slug)
        if book is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)

        canonical_handles: list[str] = []
        seen: set[str] = set()
        for raw_entry in allowed_agents or []:
            ident = str(raw_entry or "").strip()
            if not ident:
                continue
            resolved = self.store.resolve_agent_identifier(ident)
            if resolved is None:
                raise ServiceError(
                    f"Unknown agent handle or AGENT_KEY '{ident}'",
                    status_code=400,
                )
            if resolved not in seen:
                seen.add(resolved)
                canonical_handles.append(resolved)

        updated = self.store.set_book_access(
            slug=book_slug,
            access_mode=mode,
            allowed_agents=canonical_handles,
        )
        if norm_approval is not None:
            updated = self.store.set_book_approval_mode(slug=book_slug, approval_mode=norm_approval)
        out = dict(updated)
        out.pop("source_text", None)
        return out

    def set_book_approval_mode(
        self,
        ctx: AuthContext,
        *,
        book_slug: str,
        approval_mode: str,
    ) -> dict[str, Any]:
        """Toggle a book's chapter approval policy ('auto' or 'manual')."""
        assert_admin(ctx)
        norm = _normalize_approval_mode(approval_mode)
        if self.store.get_book(book_slug) is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        updated = self.store.set_book_approval_mode(slug=book_slug, approval_mode=norm)
        out = dict(updated)
        out.pop("source_text", None)
        return out

    def approve_book_intake(
        self,
        ctx: AuthContext,
        *,
        book_slug: str,
        auto_seed: bool = True,
        hold_for_review: bool = False,
        background: bool = False,
        queue_for_parse: bool = False,
    ) -> dict[str, Any]:
        assert_admin(ctx)
        book = self.store.get_book(book_slug)
        if book is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        if book["status"] == "published":
            raise ServiceError(f"Book '{book_slug}' is already published", status_code=409)

        if queue_for_parse:
            if any(c["status"] == "accepted" for c in self.store.list_chapters(book_slug)):
                raise ServiceError(
                    f"Book '{book_slug}' already has accepted chapters",
                    status_code=409,
                )
            self.store.set_book_status(slug=book_slug, status="approved_pending_parse")
            return self.get_book(ctx, book_slug=book_slug)

        existing_chapters = self.store.list_chapters(book_slug)
        if not existing_chapters:
            extracted = self.git_sync.prepare_book_files(
                slug=book_slug,
                manifest=book["manifest"],
                source_filename=book.get("source_filename") or f"{book_slug}.md",
                source_text=book.get("source_text") or "",
                proposed_chapters=book.get("proposed_chapters") or None,
                initial_book_record=book.get("book_record") or None,
                initial_brief=book.get("brief") or None,
            )
            if not extracted:
                raise ServiceError(f"Zero chapters extracted for '{book_slug}'", status_code=422)

            for ch_item in extracted:
                st = "open" if ch_item["chapter_id"] == "ch01" else "locked_seed"
                self.store.upsert_chapter(
                    book_slug=book_slug,
                    chapter_id=ch_item["chapter_id"],
                    chapter_num=int(ch_item["chapter_num"]),
                    title=ch_item["title"],
                    source_xml=ch_item["source_xml"],
                    status=st,
                )

        if auto_seed:
            return self.seed_book(
                ctx,
                book_slug=book_slug,
                hold_for_review=hold_for_review,
                background=background,
            )
        self.store.set_book_status(slug=book_slug, status="active")
        return self.get_book(ctx, book_slug=book_slug)

    def reject_book_proposal(
        self,
        ctx: AuthContext,
        *,
        book_slug: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """Reject a proposed or seed-failed book and hide it from public catalogs."""
        assert_admin(ctx)
        book = self.store.get_book(book_slug)
        if book is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        if book["status"] in {"active", "published"}:
            raise ServiceError(
                f"Cannot reject book '{book_slug}' in status '{book['status']}'",
                status_code=409,
            )
        with self._seed_lock:
            if book_slug in self._active_seeds:
                raise ServiceError(
                    f"Book '{book_slug}' is currently seeding in the background; wait for it to finish before rejecting",
                    status_code=409,
                )
        updated = self.store.set_book_status(slug=book_slug, status="rejected")
        self.git_sync.cleanup_untracked_book_files(slug=book_slug)
        with self._seed_lock:
            self._seed_errors.pop(book_slug, None)
        out = dict(updated)
        out.pop("source_text", None)
        out.pop("proposed_chapters", None)
        out["rejection_reason"] = reason.strip()
        return out

    def _execute_seed_book(
        self,
        ctx: AuthContext,
        *,
        book: dict[str, Any],
        ch01: dict[str, Any],
        hold_for_review: bool,
    ) -> dict[str, Any]:
        """Run blocking ch01 review-server translation + review round, recording failures cleanly."""
        book_slug = str(book["slug"])
        try:
            brief_obj = book.get("brief") or {}
            brief_str = (
                json.dumps(brief_obj, ensure_ascii=False)
                if brief_obj
                else f"Opening chapter seed for {book['title']} by {book['author']}."
            )
            tr_out = self.review_client.translate_chapter(
                book_slug=book_slug,
                chapter_id="ch01",
                source_lang=book["source_lang"],
                source_xml=ch01["source_xml"],
                book_record=book["book_record"],
                brief=brief_str,
            )
            _, sub_payload = self.submit_translation(
                ctx,
                book_slug=book_slug,
                chapter_id="ch01",
                translation_xml=str(tr_out["translationXml"]),
                engine_model=f"seed:{tr_out.get('engineUsed', 'gemini')}",
                additions=tr_out.get("additions") or {"proposals": []},
                hold_for_review=hold_for_review,
            )
            sub_st = sub_payload["submission"]["status"]
            valid_seed_statuses = {"pending_admin_approval", "pending_review"} if hold_for_review or ctx.tier != "ADMIN" else {"accepted"}
            if sub_st not in valid_seed_statuses:
                raise RuntimeError("ch01 seed translation did not pass TRANSLATION_REVIEW")
        except Exception as exc:
            logging.getLogger(__name__).exception("ch01 seed failed for '%s'", book_slug)
            with self._seed_lock:
                self._seed_errors[book_slug] = str(exc)
            self.store.set_book_status(slug=book_slug, status="seed_failed")
            raise ServiceError(f"ch01 seed failed: {exc}", status_code=422) from exc

        with self._seed_lock:
            self._active_seeds.discard(book_slug)
            self._seed_errors.pop(book_slug, None)
        res = self.get_book(ctx, book_slug=book_slug)
        res["seed_submission_id"] = sub_payload["submission"]["id"]
        res["seed_submission_status"] = sub_payload["submission"]["status"]
        return res

    def seed_book(
        self,
        ctx: AuthContext,
        *,
        book_slug: str,
        hold_for_review: bool = False,
        background: bool = False,
    ) -> dict[str, Any]:
        assert_agent_admin_or_admin(ctx)
        book = self.store.get_book(book_slug)
        if book is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        self._assert_book_work_access(ctx, book)
        ch01 = self.store.get_chapter(book_slug, "ch01")
        if ch01 is None:
            raise ServiceError(f"Book '{book_slug}' has no extracted ch01", status_code=400)
        if ch01["status"] == "accepted":
            return self.get_book(ctx, book_slug=book_slug)
        existing_ch01_subs = self.store.list_submissions(book_slug=book_slug, chapter_id="ch01")
        if any(s["status"] in {"pending_review", "pending_admin_approval"} for s in existing_ch01_subs):
            raise ServiceError(
                f"Book '{book_slug}' already has a ch01 seed awaiting review",
                status_code=409,
            )

        with self._seed_lock:
            if book_slug in self._active_seeds:
                raise ServiceError(
                    f"Book '{book_slug}' is already seeding in the background",
                    status_code=409,
                )
            self._active_seeds.add(book_slug)
            self._seed_errors.pop(book_slug, None)

        try:
            self.store.set_book_status(slug=book_slug, status="seeding")
        except Exception:
            with self._seed_lock:
                self._active_seeds.discard(book_slug)
            raise

        if not background:
            try:
                return self._execute_seed_book(
                    ctx,
                    book=book,
                    ch01=ch01,
                    hold_for_review=hold_for_review,
                )
            finally:
                with self._seed_lock:
                    self._active_seeds.discard(book_slug)

        def _bg_worker() -> None:
            try:
                self._execute_seed_book(
                    ctx,
                    book=book,
                    ch01=ch01,
                    hold_for_review=hold_for_review,
                )
            except Exception as exc:
                logging.getLogger(__name__).warning("Background ch01 seed failed for '%s': %s", book_slug, exc)
            finally:
                with self._seed_lock:
                    self._active_seeds.discard(book_slug)

        res = self.get_book(ctx, book_slug=book_slug)
        res["seeding_started"] = True
        res["hold_for_review"] = hold_for_review
        worker = threading.Thread(target=_bg_worker, name=f"seed-{book_slug}", daemon=True)
        try:
            worker.start()
        except Exception:
            with self._seed_lock:
                self._active_seeds.discard(book_slug)
            raise
        return res

    def trigger_starved_fallback(
        self,
        ctx: AuthContext,
        *,
        book_slug: Optional[str] = None,
    ) -> dict[str, Any]:
        assert_admin(ctx)
        now = time.time()
        threshold_sec = self.cfg.fallback_hours * 3600.0
        books = [self.store.get_book(book_slug)] if book_slug else self.store.list_books()
        translated: list[str] = []
        errors: list[str] = []

        for b in books:
            if not b or b["status"] != "active":
                continue
            slug = b["slug"]
            for ch in self.store.list_chapters(slug):
                if ch["status"] != "open":
                    continue
                # Skip if unexpired claim or younger than fallback_hours
                if ch["claimed_by"] and float(ch["claim_expires_at"]) > now:
                    continue
                if (now - float(ch["opened_at"])) < threshold_sec:
                    continue
                active_subs = [
                    s
                    for s in self.store.list_submissions(book_slug=slug, chapter_id=ch["chapter_id"])
                    if s["status"] in {"remediation_required", "pending_review", "pending_admin_approval"}
                ]
                if active_subs:
                    continue

                try:
                    latest_book = self.store.get_book(slug) or b
                    tr_out = self.review_client.translate_chapter(
                        book_slug=slug,
                        chapter_id=ch["chapter_id"],
                        source_lang=latest_book["source_lang"],
                        source_xml=ch["source_xml"],
                        book_record=latest_book["book_record"],
                        brief=f"Starved chapter fallback for {latest_book['title']} {ch['chapter_id']}.",
                    )
                    _, sub_payload = self.submit_translation(
                        ctx,
                        book_slug=slug,
                        chapter_id=ch["chapter_id"],
                        translation_xml=str(tr_out["translationXml"]),
                        engine_model=f"fallback:{tr_out.get('engineUsed', 'gemini')}",
                        additions=tr_out.get("additions") or {"proposals": []},
                    )
                    if sub_payload["submission"]["status"] == "accepted":
                        translated.append(f"{slug}/{ch['chapter_id']}")
                except Exception as exc:
                    errors.append(f"{slug}/{ch['chapter_id']}: {exc}")

        return {"translated_chapters": translated, "errors": errors}

    def list_admin_submissions(
        self,
        ctx: AuthContext,
        *,
        book_slug: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List chapter submissions for Admin review, prioritized by actionable status."""
        assert_admin(ctx)
        rows = self.store.list_submissions(book_slug=book_slug, status=status)
        rows.sort(key=lambda s: _SUBMISSION_SORT_RANK.get(str(s.get("status") or ""), 99))
        capped = max(1, min(int(limit), 200))
        out: list[dict[str, Any]] = []
        for s in rows[:capped]:
            item = _sanitize_submission_public(s)
            item["peer_reviews"] = self.store.list_peer_reviews(s["id"])
            out.append(item)
        return out

    def manage_flagged_submission(
        self,
        ctx: AuthContext,
        *,
        submission_id: str,
        action: str,
    ) -> dict[str, Any]:
        assert_agent_admin_or_admin(ctx)
        sub = self.store.get_submission(submission_id)
        if sub is None:
            raise ServiceError(f"Submission '{submission_id}' not found", status_code=404)
        if action not in {"reject", "approve", "approve_override"}:
            raise ServiceError("action must be 'reject', 'approve', or 'approve_override'", status_code=400)
        if ctx.tier != "ADMIN":
            # agent_admin can only approve/reject non-self pending_review submissions
            if action == "approve_override" or sub["status"] != "pending_review":
                raise AuthError("Admin privileges required for this submission state or action", status_code=403)
            assert_not_self_review(ctx.actor_id, sub["author_actor_id"])
        if sub["status"] == "accepted":
            raise ServiceError("Submission is already accepted", status_code=409)
        book = self.store.get_book(sub["book_slug"])
        if book is not None:
            self._assert_book_work_access(ctx, book)
        if action == "reject":
            updated = self.store.update_submission(submission_id, status="rejected")
            return _sanitize_submission_public(updated)
        ch = self.store.get_chapter(sub["book_slug"], sub["chapter_id"])
        if not book or not ch:
            raise ServiceError("Book or chapter missing", status_code=404)
        if ch["status"] == "accepted":
            raise ServiceError(
                f"Chapter '{sub['book_slug']}/{sub['chapter_id']}' is already accepted",
                status_code=409,
            )
        if sub["workstream_id"]:
            self.review_client.approve_workstream(
                workstream_id=sub["workstream_id"],
                override_escalated=(sub["status"] not in {"pending_review", "pending_admin_approval"}),
            )
        final_txt = sub["final_txt"] or _render_final_txt(sub["translation_xml"])
        updated = self.store.update_submission(
            submission_id,
            status="accepted",
            final_txt=final_txt,
        )
        self._finalize_accepted_chapter(book=book, ch=ch, submission=updated)
        return _sanitize_submission_public(updated)

    def publish_release(self, ctx: AuthContext, *, book_slug: str) -> dict[str, Any]:
        assert_admin(ctx)
        book = self.store.get_book(book_slug)
        if book is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        chapters = self.store.list_chapters(book_slug)
        if not chapters or any(c["status"] != "accepted" for c in chapters):
            raise ServiceError(f"Book '{book_slug}' has incomplete chapters", status_code=409)
        rel_tag = book["release_tag"] or f"{book_slug}-v1.0"
        return self.git_sync.compile_and_publish_release(
            slug=book_slug,
            title=book["title"],
            author=book["author"],
            release_tag=rel_tag,
        )
