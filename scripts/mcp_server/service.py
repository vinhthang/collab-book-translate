"""Core Book Collaboration domain service shared by MCP tools and REST /api/v1/* endpoints."""

from __future__ import annotations

import hashlib
import json
import secrets
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
    assert_not_self_review,
    mint_revision_token,
    sha256_hex,
    verify_revision_token,
)
from scripts.mcp_server.config import McpServerConfig
from scripts.mcp_server.db import CollabStore
from scripts.mcp_server.git_sync import CollabBooksGitSync

MAX_MCP_XML_BYTES = 524_288  # 512 KB cap per REQ-040
ALLOWED_LICENSES = frozenset(
    {"public-domain", "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "owner-permission"}
)
SUPPORTED_SOURCE_LANGS = frozenset({"en", "zh"})


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

    def register_agent(
        self,
        ctx: AuthContext,
        *,
        handle: str,
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
            done_cnt = sum(1 for c in chapters if c["status"] == "accepted")
            open_cnt = sum(1 for c in chapters if c["status"] == "open")
            summary.append(
                {
                    "slug": b["slug"],
                    "title": b["title"],
                    "author": b["author"],
                    "source_lang": b["source_lang"],
                    "target_lang": b["target_lang"],
                    "genre": b["genre"],
                    "license": b["license_id"],
                    "status": b["status"],
                    "glossary_sha": b["glossary_sha"],
                    "total_chapters": len(chapters),
                    "accepted_chapters": done_cnt,
                    "open_chapters": open_cnt,
                    "release_tag": b["release_tag"],
                    "release_url": b["release_url"],
                }
            )
        return summary

    def get_book(self, ctx: AuthContext, *, book_slug: str) -> dict[str, Any]:
        self._check_read_rate(ctx)
        book = self.store.get_book(book_slug)
        if book is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        chapters = [
            {
                "chapter_id": c["chapter_id"],
                "chapter_num": c["chapter_num"],
                "title": c["title"],
                "status": c["status"],
                "claimed_by": c["claimed_by"],
                "claim_expires_at": c["claim_expires_at"],
            }
            for c in self.store.list_chapters(book_slug)
        ]
        out = dict(book)
        out.pop("source_text", None)
        out["chapters"] = chapters
        return out

    def list_tasks(
        self,
        ctx: AuthContext,
        *,
        role: str = "all",
        book_slug: Optional[str] = None,
    ) -> dict[str, Any]:
        self._check_read_rate(ctx)
        now = time.time()
        books = [self.store.get_book(book_slug)] if book_slug else self.store.list_books()
        open_chapters: list[dict[str, Any]] = []
        review_targets: list[dict[str, Any]] = []
        revisions_needed: list[dict[str, Any]] = []

        for b in books:
            if not b or b["status"] not in {"active", "published"}:
                continue
            slug = b["slug"]
            if role in {"all", "translator"}:
                for c in self.store.list_chapters(slug):
                    if c["status"] != "open":
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
                            "chapter_id": c["chapter_id"],
                            "claimed": claimed_other,
                            "claimed_by": c["claimed_by"] if claimed_other else "",
                        }
                    )
            for sub in self.store.list_submissions(book_slug=slug):
                if (
                    role in {"all", "translator"}
                    and sub["author_actor_id"] == ctx.actor_id
                    and sub["status"] == "remediation_required"
                ):
                    revisions_needed.append(_sanitize_submission_public(sub))
                if (
                    role in {"all", "reviewer"}
                    and sub["author_actor_id"] != ctx.actor_id
                    and sub["status"] in {"remediation_required", "accepted"}
                ):
                    review_targets.append(_sanitize_submission_public(sub))

        return {
            "caller": {"tier": ctx.tier, "handle": ctx.handle},
            "open_chapters": open_chapters,
            "revisions_needed": revisions_needed,
            "peer_review_candidates": review_targets[:25],
        }

    def get_chapter(self, ctx: AuthContext, *, book_slug: str, chapter_id: str) -> dict[str, Any]:
        self._check_read_rate(ctx)
        book = self.store.get_book(book_slug)
        if book is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        ch = self.store.get_chapter(book_slug, chapter_id)
        if ch is None:
            raise ServiceError(f"Chapter '{book_slug}/{chapter_id}' not found", status_code=404)
        return {
            "book_slug": book_slug,
            "book_title": book["title"],
            "source_lang": book["source_lang"],
            "target_lang": book["target_lang"],
            "glossary_sha": book["glossary_sha"],
            "book_record": book["book_record"],
            "chapter_id": ch["chapter_id"],
            "chapter_num": ch["chapter_num"],
            "status": ch["status"],
            "source_xml": ch["source_xml"],
            "final_txt": ch["final_txt"],
        }

    def claim_chapter(
        self,
        ctx: AuthContext,
        *,
        book_slug: str,
        chapter_id: str,
    ) -> dict[str, Any]:
        self._check_write_rate(ctx)
        claim_token = f"cbt_cl_{secrets.token_hex(16)}"
        expires_at = time.time() + self.cfg.claim_ttl_seconds
        try:
            updated = self.store.claim_chapter(
                book_slug=book_slug,
                chapter_id=chapter_id,
                actor_id=ctx.actor_id,
                claim_token_sha256=sha256_hex(claim_token),
                expires_at=expires_at,
            )
        except KeyError as exc:
            raise ServiceError(str(exc), status_code=404) from exc
        except PermissionError as exc:
            raise ServiceError(str(exc), status_code=409) from exc
        except ValueError as exc:
            raise ServiceError(str(exc), status_code=400) from exc

        return {
            "book_slug": book_slug,
            "chapter_id": chapter_id,
            "claimed_by": updated["claimed_by"],
            "claim_token": claim_token,
            "claim_expires_at": updated["claim_expires_at"],
        }

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
        source_text: str,
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
        raw_bytes = len(source_text.encode("utf-8"))
        if raw_bytes == 0 or raw_bytes > 5_242_880:
            raise ServiceError("source_text must be non-empty and <= 5 MiB via MCP", status_code=413)
        if self.store.get_book(clean_slug) is not None:
            raise ServiceError(f"Book '{clean_slug}' already exists", status_code=409)

        inj = detect_prompt_injection(f"{title}\n{author}\n{source_text[:32000]}")
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
            book_record={
                "title": title.strip(),
                "author": author.strip(),
                "source_lang": source_lang,
                "target_lang": target_lang,
                "genre": genre.strip() or "fiction",
                "characters": {},
                "terms": {},
                "pronoun_pairs": [],
            },
            manifest=manifest,
            proposed_by=ctx.handle,
            source_text=source_text,
            source_filename=source_filename.strip() or f"{clean_slug}.md",
        )
        out = dict(book)
        out.pop("source_text", None)
        return out

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
            if existing_sub["status"] in {"escalated_to_admin", "accepted", "rejected"} and ctx.tier != "ADMIN":
                raise ServiceError(
                    f"Submission is in terminal/escalated state '{existing_sub['status']}'",
                    status_code=409,
                )
            if int(existing_sub["round_number"]) >= int(existing_sub["max_rounds"]) and ctx.tier != "ADMIN":
                raise ServiceError(
                    f"Submission exhausted max_rounds ({existing_sub['max_rounds']})",
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

        # Create or resume review.vinhthang.dev TRANSLATION_REVIEW workstream
        if existing_sub is None:
            ws = self.review_client.create_workstream(
                title=f"Translation Review: {book_slug}/{chapter_id} ({ctx.handle})",
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

        review_bundle = (
            f"# Chapter Translation Review — {book['title']} ({book_slug} / {chapter_id})\n\n"
            f"## Canonical book_record.json\n```json\n{json.dumps(book['book_record'], ensure_ascii=False, indent=2)}\n```\n\n"
            f"## Submitted Bilingual Segments (translation.xml)\n```xml\n{translation_xml}\n```\n"
        )
        round_res = self.review_client.run_translation_round(
            workstream_id=ws_id,
            content=review_bundle,
            rebuttals=rebuttals,
        )

        verdict = round_res["verdict"]
        round_num = int(round_res["roundNumber"])
        final_txt = _render_final_txt(translation_xml)

        if verdict == "PASS":
            self.review_client.approve_workstream(workstream_id=ws_id)
            sub_status = "accepted"
        else:
            sub_status = (
                "escalated_to_admin"
                if round_num >= self.cfg.max_revise_rounds or round_res["stageStatus"] == "ESCALATED_TO_HUMAN"
                else "remediation_required"
            )

        if existing_sub is None:
            saved_sub = self.store.create_submission(
                book_slug=book_slug,
                chapter_id=chapter_id,
                author_actor_id=ctx.actor_id,
                author_handle=ctx.handle,
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
            )
        else:
            saved_sub = self.store.update_submission(
                existing_sub["id"],
                status=sub_status,
                round_number=round_num,
                workstream_id=ws_id,
                translation_xml=translation_xml,
                final_txt=final_txt,
                additions=norm_additions,
                open_findings=round_res["openFindings"],
                judge_summary=round_res["judgeSummary"],
            )

        if sub_status == "accepted":
            self._finalize_accepted_chapter(
                book=book,
                ch=ch,
                submission=saved_sub,
            )

        out_payload: dict[str, Any] = {
            "submission": _sanitize_submission_public(saved_sub),
            "round": round_res,
        }
        if returned_rev_token:
            out_payload["revision_token"] = returned_rev_token
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

    def get_submission_status(self, ctx: AuthContext, *, submission_id: str) -> dict[str, Any]:
        self._check_read_rate(ctx)
        sub = self.store.get_submission(submission_id)
        if sub is None:
            raise ServiceError(f"Submission '{submission_id}' not found", status_code=404)
        reviews = self.store.list_peer_reviews(submission_id)
        return {
            "submission": _sanitize_submission_public(sub),
            "peer_reviews": reviews,
        }

    def submit_review(
        self,
        ctx: AuthContext,
        *,
        submission_id: str,
        body_md: str,
    ) -> dict[str, Any]:
        self._check_write_rate(ctx)
        sub = self.store.get_submission(submission_id)
        if sub is None:
            raise ServiceError(f"Submission '{submission_id}' not found", status_code=404)
        assert_not_self_review(ctx.actor_id, sub["author_actor_id"])

        val = validate_review(
            pr_author_login=sub["author_actor_id"],
            review_author_login=ctx.actor_id,
            review_body=body_md,
        )
        if val["injection_flags"]:
            raise ServiceError(
                "Peer review blocked by prompt-injection scanner",
                status_code=422,
                details={"defects": val["injection_flags"]},
            )
        if not val["valid"]:
            raise ServiceError(
                "Peer review failed 6-dimension rubric or segment-anchor validation",
                status_code=422,
                details={"defects": val["defects"]},
            )

        weighted = compute_weighted_total(val["scores"])
        saved = self.store.add_peer_review(
            submission_id=submission_id,
            reviewer_actor_id=ctx.actor_id,
            reviewer_handle=ctx.handle,
            scores=val["scores"],
            weighted_score=weighted,
            issues=[],
            body_md=body_md,
        )
        return saved

    # --- Admin Operations ---

    def approve_book_intake(
        self,
        ctx: AuthContext,
        *,
        book_slug: str,
        auto_seed: bool = True,
    ) -> dict[str, Any]:
        assert_admin(ctx)
        book = self.store.get_book(book_slug)
        if book is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)

        extracted = self.git_sync.prepare_book_files(
            slug=book_slug,
            manifest=book["manifest"],
            source_filename=book.get("source_filename") or f"{book_slug}.md",
            source_text=book.get("source_text") or "",
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

        self.store.upsert_book(
            slug=book_slug,
            title=book["title"],
            author=book["author"],
            source_lang=book["source_lang"],
            target_lang=book["target_lang"],
            genre=book["genre"],
            license_id=book["license_id"],
            license_url=book["license_url"],
            status="seeding",
            glossary_sha="",
            book_record=book["book_record"],
            manifest=book["manifest"],
            proposed_by=book["proposed_by"],
        )

        if auto_seed:
            return self.seed_book(ctx, book_slug=book_slug)
        return self.get_book(ctx, book_slug=book_slug)

    def seed_book(self, ctx: AuthContext, *, book_slug: str) -> dict[str, Any]:
        assert_admin(ctx)
        book = self.store.get_book(book_slug)
        if book is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        ch01 = self.store.get_chapter(book_slug, "ch01")
        if ch01 is None:
            raise ServiceError(f"Book '{book_slug}' has no extracted ch01", status_code=400)
        if ch01["status"] == "accepted":
            return self.get_book(ctx, book_slug=book_slug)

        try:
            tr_out = self.review_client.translate_chapter(
                book_slug=book_slug,
                chapter_id="ch01",
                source_lang=book["source_lang"],
                source_xml=ch01["source_xml"],
                book_record=book["book_record"],
                brief=f"Opening chapter seed for {book['title']} by {book['author']}.",
            )
            _, sub_payload = self.submit_translation(
                ctx,
                book_slug=book_slug,
                chapter_id="ch01",
                translation_xml=str(tr_out["translationXml"]),
                engine_model=f"seed:{tr_out.get('engineUsed', 'gemini')}",
                additions=tr_out.get("additions") or {"proposals": []},
            )
            if sub_payload["submission"]["status"] != "accepted":
                raise RuntimeError("ch01 seed translation did not pass TRANSLATION_REVIEW")
        except Exception as exc:
            self.store.upsert_book(
                slug=book_slug,
                title=book["title"],
                author=book["author"],
                source_lang=book["source_lang"],
                target_lang=book["target_lang"],
                genre=book["genre"],
                license_id=book["license_id"],
                license_url=book["license_url"],
                status="seed_failed",
                glossary_sha="",
                book_record=book["book_record"],
                manifest=book["manifest"],
                proposed_by=book["proposed_by"],
            )
            raise ServiceError(f"ch01 seed failed: {exc}", status_code=422) from exc

        return self.get_book(ctx, book_slug=book_slug)

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
                    if s["status"] in {"remediation_required"}
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

    def manage_flagged_submission(
        self,
        ctx: AuthContext,
        *,
        submission_id: str,
        action: str,
    ) -> dict[str, Any]:
        assert_admin(ctx)
        sub = self.store.get_submission(submission_id)
        if sub is None:
            raise ServiceError(f"Submission '{submission_id}' not found", status_code=404)
        if action == "reject":
            updated = self.store.update_submission(submission_id, status="rejected")
            return _sanitize_submission_public(updated)
        if action == "approve_override":
            book = self.store.get_book(sub["book_slug"])
            ch = self.store.get_chapter(sub["book_slug"], sub["chapter_id"])
            if not book or not ch:
                raise ServiceError("Book or chapter missing", status_code=404)
            if sub["workstream_id"]:
                self.review_client.approve_workstream(
                    workstream_id=sub["workstream_id"],
                    override_escalated=True,
                )
            final_txt = sub["final_txt"] or _render_final_txt(sub["translation_xml"])
            updated = self.store.update_submission(
                submission_id,
                status="accepted",
                final_txt=final_txt,
            )
            self._finalize_accepted_chapter(book=book, ch=ch, submission=updated)
            return _sanitize_submission_public(updated)
        raise ServiceError("action must be 'reject' or 'approve_override'", status_code=400)

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
