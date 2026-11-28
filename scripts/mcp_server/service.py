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
import httpx

from scripts.collab_pr_validate import (
    HANVIET_TAG_RE,
    ORIG_TAG_RE,
    SEGMENT_BLOCK_RE,
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
    assert_bug_handler_or_admin,
    assert_not_self_review,
    mint_revision_token,
    sha256_hex,
    verify_revision_token,
)
from scripts.mcp_server.config import McpServerConfig
from scripts.mcp_server.db import CollabStore, parse_utc_timestamp
from scripts.mcp_server.git_sync import PUBLIC_CORPUS_LICENSES, CollabBooksGitSync, is_public_corpus_book
from scripts.mcp_server.presence import AgentPresenceTracker, compute_agent_presence, enrich_and_sort_agents

MAX_MCP_XML_BYTES = 524_288  # 512 KB cap per REQ-040
MAX_PROPOSED_CHAPTERS = 200
MAX_BOOK_PROPOSAL_BYTES = 5_242_880  # 5 MiB cap
MAX_TRUSTED_BOOK_PROPOSAL_BYTES = 10_485_760  # 10 MiB cap for trusted ADMIN / agent_admin proposals
MAX_IMPORT_CHAPTERS_BATCH = 50
EVALUATING_STALE_SECONDS = 480.0
MAX_BUG_BODY_BYTES = 65_536
MAX_REVIEW_BODY_CHARS = 2_000
_PROPOSED_CHAPTER_ID_RE = re.compile(r"^ch\d{2,4}$")
ALLOWED_LICENSES = frozenset(
    {"public-domain", "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "owner-permission"}
)
SUPPORTED_SOURCE_LANGS = frozenset({"en", "zh"})
VALID_AGENT_ROLES = frozenset({"contributor", "agent_admin", "tech_admin"})
VALID_APPROVAL_MODES = frozenset({"peer", "auto", "manual"})
VALID_STRATEGIES = frozenset({"faithful", "idiomatic", "register-matched"})
VALID_REVIEW_VERDICTS = frozenset({"COMMENT", "APPROVE", "CHANGES_REQUESTED"})
VALID_BUG_SEVERITIES = frozenset({"P0", "P1", "P2", "P3"})
VALID_BUG_STATUSES = frozenset({"open", "accepted", "in_progress", "fixed", "declined"})
VALID_SECTION_TYPES = frozenset({"body", "front_matter", "back_matter", "boilerplate"})
_SECTION_SORT_RANK = {
    "body": 0,
    "front_matter": 1,
    "back_matter": 2,
    "boilerplate": 3,
}
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
    "superseded": 7,
}


_XML_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_IMAGE_ONLY_LINE_RE = re.compile(r"^(?:!\[[^\]]*\]\([^)]+\)\s*)+$")


def _is_operational_specialist_failure_only(open_findings: list[dict[str, Any]]) -> bool:
    """Return True iff every open finding is an operational specialist execution/timeout failure."""
    if not open_findings:
        return False
    for item in open_findings:
        if not isinstance(item, dict):
            return False
        fkey = str(item.get("findingKey") or "")
        if not (fkey.startswith("CONTRACT::global::specialist-") and "-failed-" in fkey):
            return False
    return True


def _active_claim_fields(ch: dict[str, Any], now: float) -> tuple[str, Any]:
    """Return (claimed_by, claim_expires_at) only while a claim is unexpired."""
    claimed_by = str(ch.get("claimed_by") or "")
    expires_at = ch.get("claim_expires_at") or 0
    if claimed_by and float(expires_at) > now:
        return claimed_by, expires_at
    return "", 0


def _classify_chapter_section_type(
    chapter_id: str,
    title: str,
    source_xml: str,
    *,
    explicit: str = "",
) -> str:
    """Deterministically classify a chapter into body, front_matter, back_matter, or boilerplate."""
    clean_exp = (explicit or "").strip().lower()
    if clean_exp:
        if clean_exp not in VALID_SECTION_TYPES:
            raise ServiceError(
                f"Invalid section_type '{explicit}' on '{chapter_id}' (expected one of body, front_matter, back_matter, boilerplate)",
                status_code=400,
            )
        return clean_exp

    clean_title = (title or "").strip()
    low_title = clean_title.lower().strip("*_# ")
    plain = " ".join(_XML_TAG_STRIP_RE.sub(" ", source_xml or "").split())
    low_plain = plain.lower()
    words = len(plain.split())

    # 1. Pure publishing boilerplate (low-value to human/agent peer review)
    if (
        low_title in {"cover", "bìa sách", "bìa sách (cover)"}
        or low_title.startswith("cover:")
        or low_title.startswith("cover ")
        or "title page" in low_title
        or "trang nhan đề" in low_title
        or _IMAGE_ONLY_LINE_RE.match(plain.strip())
        or ("![cover:" in low_plain and words <= 60)
    ):
        return "boilerplate"

    if (
        "thank you for downloading this" in low_title
        or "we hope you enjoyed reading this" in low_title
        or "thank you for downloading this simon & schuster" in low_plain
        or "we hope you enjoyed reading this simon & schuster" in low_plain
        or "ebook-signup" in low_plain
    ) and words <= 250:
        return "boilerplate"

    if (
        low_title.startswith("avid reader press")
        or ("isbn" in low_plain and ("copyright ©" in low_plain or "simon & schuster" in low_plain) and words <= 450)
        or (low_title == "copyright" and words <= 350)
    ):
        return "boilerplate"

    if (low_title.startswith("also from ") or low_title.startswith("also by ")) and words <= 350:
        return "boilerplate"

    if (
        words <= 40
        and re.match(r"^(?:part|phần)\s+[ivxlcdm0-9]+\b", low_title)
        and "chapter" not in low_title
        and "chương" not in low_title
    ):
        return "boilerplate"

    # 2. Substantive front & back matter (still reviewed, ordered after body)
    if (
        low_title in {"dedication", "with appreciation", "how to read this book", "foreword", "preface"}
        or low_title.startswith("why i’m sharing")
        or low_title.startswith("why i'm sharing")
        or low_title.startswith("note: my failure to cover")
        or low_title == "the past is prologue"
        or (
            words <= 120
            and low_title.startswith("dedication")
            and "introduction" not in low_title
        )
    ):
        return "front_matter"

    if (
        low_title.startswith("about the author")
        or low_title.startswith("về tác giả")
        or low_title in {"image descriptions", "acknowledgments", "acknowledgements", "bibliography", "index", "glossary", "endnotes"}
    ):
        return "back_matter"

    return "body"


def _attach_chapter_section_types(
    extracted: list[dict[str, Any]],
    proposed_chapters: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    explicit_map = {
        str(item.get("chapter_id") or ""): str(item.get("section_type") or "")
        for item in (proposed_chapters or [])
        if isinstance(item, dict) and item.get("chapter_id")
    }
    enriched: list[dict[str, Any]] = []
    for item in extracted:
        row = dict(item)
        ch_id = str(row.get("chapter_id") or "")
        row["section_type"] = _classify_chapter_section_type(
            ch_id,
            str(row.get("title") or ""),
            str(row.get("source_xml") or ""),
            explicit=explicit_map.get(ch_id, ""),
        )
        enriched.append(row)
    return enriched


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


def _extract_scannable_xml_text(xml_text: str) -> tuple[str, list[str]]:
    """Extract segment inner texts for prompt-injection scan and check per-segment breakout tags."""
    if not xml_text:
        return "", []
    direct_flags: list[str] = []
    chunks: list[str] = []
    matches = list(SEGMENT_BLOCK_RE.finditer(xml_text))
    if not matches:
        return xml_text[:4000], []
    for m in matches:
        seg_body = m.group(2)
        if re.search(r"(?i)</translate>[\s\S]*?<translate>", seg_body):
            direct_flags.append("Prompt injection detected: 'translate tag breakout / injection'")
        for tag_re in (ORIG_TAG_RE, HANVIET_TAG_RE, TRANS_TAG_RE):
            chunks.extend(tag_re.findall(seg_body))
        rem = HANVIET_TAG_RE.sub("", TRANS_TAG_RE.sub("", ORIG_TAG_RE.sub("", seg_body))).strip()
        if rem:
            chunks.append(rem)
    outside = SEGMENT_BLOCK_RE.sub("", xml_text).strip()
    if outside:
        chunks.append(outside)
    return "\n".join(chunks)[:8000], direct_flags


def _normalize_proposed_chapters(
    chapters: Any,
) -> tuple[list[dict[str, str]], int, str, list[str]]:
    """Validate and normalize pre-split chapters submitted from local agent preprocessing."""
    if chapters is None:
        return [], 0, "", []
    if not isinstance(chapters, list):
        raise ServiceError("chapters must be a list of chapter objects", status_code=400)
    if len(chapters) > MAX_PROPOSED_CHAPTERS:
        raise ServiceError(f"chapters exceeds maximum of {MAX_PROPOSED_CHAPTERS}", status_code=400)

    normalized: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    total_bytes = 0
    scan_chunks: list[str] = []
    direct_inj_flags: list[str] = []

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
        tr_xml = str(raw.get("translation_xml") or "")
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

        tr_bytes = tr_xml.encode("utf-8")
        if tr_xml.strip() and len(tr_bytes) > MAX_MCP_XML_BYTES:
            raise ServiceError(
                f"Chapter '{ch_id}' translation_xml exceeds {MAX_MCP_XML_BYTES}-byte cap",
                status_code=413,
            )

        src_scan, src_flags = _extract_scannable_xml_text(src_xml)
        tr_scan, tr_flags = _extract_scannable_xml_text(tr_xml)
        direct_inj_flags.extend(src_flags)
        direct_inj_flags.extend(tr_flags)

        total_bytes += len(src_text.encode("utf-8")) + len(src_xml.encode("utf-8")) + len(tr_bytes)
        scan_chunks.append(f"{title}\n{src_text[:4000]}\n{src_scan}\n{tr_scan}")
        explicit_sec = str(raw.get("section_type") or "").strip().lower()
        if explicit_sec and explicit_sec not in VALID_SECTION_TYPES:
            raise ServiceError(
                f"Chapter '{ch_id}' has invalid section_type '{raw.get('section_type')}'",
                status_code=400,
            )
        entry: dict[str, str] = {
            "chapter_id": ch_id,
            "title": title,
            "source_text": src_text,
            "source_xml": src_xml,
        }
        if explicit_sec:
            entry["section_type"] = explicit_sec
        if tr_xml.strip():
            entry["translation_xml"] = tr_xml
        normalized.append(entry)

    return normalized, total_bytes, "\n".join(scan_chunks), direct_inj_flags


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
    names_list = merged.get("names") if isinstance(merged.get("names"), list) else None
    term_entries = merged.get("term_entries") if isinstance(merged.get("term_entries"), list) else None
    seen_names = {str(n.get("source") or "").strip() for n in (names_list or []) if isinstance(n, dict)}
    seen_terms = {str(t.get("source") or "").strip() for t in (term_entries or []) if isinstance(t, dict)}

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
            if names_list is None:
                names_list = merged.setdefault("names", [])
            if src not in seen_names:
                names_list.append(
                    {
                        "source": src,
                        "target": tgt,
                        "also_referred_to_as": [],
                        "notes": str(item.get("notes") or "").strip(),
                    }
                )
                seen_names.add(src)
        else:
            terms.setdefault(src, tgt)
            if term_entries is None:
                term_entries = merged.setdefault("term_entries", [])
            if src not in seen_terms:
                term_entries.append(
                    {
                        "source": src,
                        "target": tgt,
                        "match": str(item.get("match") or "word"),
                        "case_sensitive": bool(item.get("case_sensitive", False)),
                        "variants": [],
                    }
                )
                seen_terms.add(src)
    return merged


def _sanitize_submission_public(sub: dict[str, Any], *, include_xml: bool = True) -> dict[str, Any]:
    out = dict(sub)
    out.pop("revision_token_sha256", None)
    out.pop("author_ip_hash", None)
    out["submission_id"] = str(out.get("id") or "")
    hist = out.get("rounds_history")
    if isinstance(hist, list):
        cleaned_hist: list[Any] = []
        for entry in hist:
            if isinstance(entry, dict):
                c_entry = dict(entry)
                c_entry.pop("translation_xml", None)
                c_entry.pop("final_txt", None)
                cleaned_hist.append(c_entry)
            else:
                cleaned_hist.append(entry)
        out["rounds_history"] = cleaned_hist
    if not include_xml:
        out.pop("translation_xml", None)
        out.pop("final_txt", None)
        out.pop("rounds_history", None)
    return out


def _summarize_submission_task(
    sub: dict[str, Any],
    book: dict[str, Any],
    ch: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Return compact submission task metadata with full 36-char UUIDs and zero bulky XML payloads."""
    xml_blob = str(sub.get("translation_xml") or (ch or {}).get("source_xml") or "")
    ch_num = int((ch or {}).get("chapter_num") or 0)
    if ch_num <= 0:
        m_num = re.search(r"(\d+)", str(sub.get("chapter_id") or ""))
        if m_num:
            ch_num = int(m_num.group(1))
    return {
        "id": str(sub["id"]),
        "submission_id": str(sub["id"]),
        "book_slug": str(sub["book_slug"]),
        "book_title": str(book.get("title") or sub["book_slug"]),
        "chapter_id": str(sub["chapter_id"]),
        "chapter_num": ch_num,
        "chapter_title": str((ch or {}).get("title") or sub["chapter_id"]),
        "section_type": str((ch or {}).get("section_type") or "body"),
        "author_actor_id": str(sub.get("author_actor_id") or ""),
        "author_handle": str(sub.get("author_handle") or ""),
        "engine_model": str(sub.get("engine_model") or ""),
        "status": str(sub.get("status") or ""),
        "round_number": int(sub.get("round_number") or 0),
        "max_rounds": int(sub.get("max_rounds") or 3),
        "workstream_id": str(sub.get("workstream_id") or ""),
        "segment_count": len(SEGMENT_BLOCK_RE.findall(xml_blob)),
        "open_findings": list(sub.get("open_findings") or []),
        "judge_summary": str(sub.get("judge_summary") or ""),
        "created_at": str(sub.get("created_at") or ""),
        "updated_at": str(sub.get("updated_at") or ""),
    }


def _sanitize_bug_public(bug: dict[str, Any], *, include_body: bool = True) -> dict[str, Any]:
    out = dict(bug)
    out.pop("reporter_ip_hash", None)
    if not include_body:
        out.pop("body_md", None)
    return out


def _is_coord(ctx: AuthContext) -> bool:
    return ctx.tier == "ADMIN" or (ctx.tier == "AGENT_KEY" and ctx.agent_role == "agent_admin")


def _is_tech_admin(ctx: AuthContext) -> bool:
    return ctx.tier == "ADMIN" or (ctx.tier == "AGENT_KEY" and ctx.agent_role == "tech_admin")


def _validate_optional_http_url(url: str, field_name: str) -> str:
    clean = (url or "").strip()
    if not clean:
        return ""
    if len(clean) > 500 or not clean.lower().startswith(("https://", "http://")):
        raise ServiceError(f"{field_name} must be an http:// or https:// URL (<= 500 chars)", status_code=400)
    if detect_prompt_injection(clean):
        raise ServiceError(f"{field_name} blocked by prompt-injection scanner", status_code=422)
    return clean


def _is_trusted_submitter(ctx: AuthContext) -> bool:
    return _is_coord(ctx)


def _has_curated_book_record(record: dict[str, Any] | None) -> bool:
    if not isinstance(record, dict):
        return False
    return any(
        bool(record.get(k))
        for k in ("characters", "terms", "pronoun_pairs", "names", "term_entries", "pronouns")
    )


def _compute_glossary_sha(record: dict[str, Any] | None) -> str:
    canonical_bytes = json.dumps(record or {}, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def _book_is_public_corpus(book: dict[str, Any]) -> bool:
    """Return True iff the book satisfies public GitHub corpus publishing eligibility."""
    return is_public_corpus_book(
        license_id=str(book.get("license_id") or book.get("license") or ""),
        access_mode=str(book.get("access_mode") or "public"),
    )


class CollabMcpService:
    """Domain orchestrator for book queries, 3-tier submissions, review-server rounds, and git sync."""

    def __init__(
        self,
        *,
        cfg: McpServerConfig,
        store: CollabStore,
        review_client: Any,
        git_sync: CollabBooksGitSync,
        presence: Optional[AgentPresenceTracker] = None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.review_client = review_client
        self.git_sync = git_sync
        self.presence = presence or AgentPresenceTracker()
        self.rate_limiter = SlidingWindowRateLimiter()
        self._seed_lock = threading.Lock()
        self._active_seeds: set[str] = set()
        self._seed_errors: dict[str, str] = {}
        self.store.recover_stale_evaluating_submissions(
            max_age_seconds=0.0,
            reason="Evaluation interrupted by server restart; please re-submit.",
        )
        self.store.supersede_all_accepted_chapter_drafts()
        self._backfill_chapter_section_types()
        self._evict_non_public_books()
        self.store.purge_test_artifacts()

    def _evict_non_public_books(self) -> None:
        for bk in self.store.list_books(include_pending=True):
            if not _book_is_public_corpus(bk):
                self.git_sync.evict_to_private_storage(slug=str(bk["slug"]))

    def _backfill_chapter_section_types(self) -> int:
        updated = 0
        for bk in self.store.list_books(include_pending=True):
            for ch in self.store.list_chapters(bk["slug"]):
                cur_sec = str(ch.get("section_type") or "body").strip() or "body"
                if cur_sec != "body":
                    continue
                inferred = _classify_chapter_section_type(
                    str(ch["chapter_id"]),
                    str(ch.get("title") or ""),
                    str(ch.get("source_xml") or ""),
                )
                if inferred != cur_sec:
                    self.store.update_chapter_section_type(
                        book_slug=bk["slug"],
                        chapter_id=str(ch["chapter_id"]),
                        section_type=inferred,
                    )
                    updated += 1
        return updated

    def _sweep_stale_evaluating(self) -> int:
        return self.store.recover_stale_evaluating_submissions(
            max_age_seconds=EVALUATING_STALE_SECONDS,
            reason=f"Evaluation timed out (> {int(EVALUATING_STALE_SECONDS)}s); please re-submit.",
        )

    def _assert_seeding_access(self, ctx: AuthContext, book: dict[str, Any]) -> None:
        if str(book.get("status") or "") == "seeding" and not _is_coord(ctx):
            raise ServiceError(
                (
                    f"Book '{book['slug']}' is currently in 'seeding' (ch01 reserved for agent_admin/ADMIN); "
                    "ch02..chNN unlock automatically once ch01 is accepted"
                ),
                status_code=409,
            )

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

    def _assert_write_identity(self, ctx: AuthContext) -> None:
        if ctx.tier == "ANONYMOUS" and not self.cfg.allow_anonymous_writes:
            raise AuthError(
                "Invite-only hub: an admin-minted 3-word AGENT_KEY is required for registration or write operations",
                status_code=403,
            )

    def _can_access_book_work(self, ctx: AuthContext, book: dict[str, Any]) -> bool:
        if ctx.tier == "ADMIN":
            return True
        if ctx.tier == "ANONYMOUS" and str(book.get("license_id") or book.get("license") or "") not in PUBLIC_CORPUS_LICENSES:
            return False
        if str(book.get("access_mode") or "public") == "public":
            return True
        allowed = set(book.get("allowed_agents") or [])
        return ctx.tier == "AGENT_KEY" and ctx.handle in allowed

    def _assert_book_work_access(self, ctx: AuthContext, book: dict[str, Any]) -> None:
        if not self._can_access_book_work(ctx, book):
            if ctx.tier == "ANONYMOUS" and str(book.get("license_id") or book.get("license") or "") not in PUBLIC_CORPUS_LICENSES:
                raise AuthError(
                    f"Book '{book['slug']}' is a private owner-permission book; a valid AGENT_KEY is required",
                    status_code=403,
                )
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
        if not self.cfg.allow_anonymous_writes:
            assert_admin(ctx)
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
        is_admin = ctx.tier == "ADMIN"
        books = self.store.list_books_summary(
            include_pending=include_pending or is_admin,
            include_admin_fields=is_admin,
        )
        chapter_counts = self.store.get_book_chapter_counts()
        pending_counts = self.store.get_book_actionable_submission_counts(
            tuple(ACTIONABLE_SUBMISSION_STATUSES)
        )
        summary: list[dict[str, Any]] = []
        for b in books:
            if ctx.tier == "ANONYMOUS" and str(b.get("license_id") or "") not in PUBLIC_CORPUS_LICENSES:
                continue
            slug = str(b["slug"])
            ch_stats = chapter_counts.get(slug, {})
            done_cnt = int(ch_stats.get("accepted", 0))
            open_cnt = int(ch_stats.get("open", 0))
            total_ch = int(ch_stats.get("total", 0))
            pending_approvals = int(pending_counts.get(slug, 0))
            allowed = list(b.get("allowed_agents") or [])
            proposed_ch = list(b.get("proposed_chapters") or [])
            item: dict[str, Any] = {
                "slug": slug,
                "title": b["title"],
                "author": b["author"],
                "source_lang": b["source_lang"],
                "target_lang": b["target_lang"],
                "genre": b["genre"],
                "license": b["license_id"],
                "status": b["status"],
                "access_mode": str(b.get("access_mode") or "public"),
                "public_corpus": bool(b.get("public_corpus", _book_is_public_corpus(b))),
                "approval_mode": str(b.get("approval_mode") or "auto"),
                "allowed_agent_count": len(allowed),
                "pending_approvals": pending_approvals,
                "proposed_chapter_count": len(proposed_ch),
                "glossary_sha": b["glossary_sha"],
                "total_chapters": total_ch,
                "accepted_chapters": done_cnt,
                "open_chapters": open_cnt,
                "release_tag": b["release_tag"],
                "release_url": b["release_url"],
            }
            if is_admin:
                br = b.get("book_record") or {}
                item["allowed_agents"] = allowed
                item["proposed_by"] = b["proposed_by"]
                item["license_url"] = b["license_url"]
                item["source_filename"] = b.get("source_filename") or ""
                item["source_preview"] = str(b.get("source_preview") or "")[:2000]
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
        if str(book.get("license_id") or "") not in PUBLIC_CORPUS_LICENSES:
            self._assert_book_work_access(ctx, book)
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

        now = time.time()
        chapters = []
        for c in self.store.list_chapters(book_slug):
            active_claimed_by, active_claim_exp = _active_claim_fields(c, now)
            chapters.append(
                {
                    "chapter_id": c["chapter_id"],
                    "chapter_num": c["chapter_num"],
                    "title": c["title"],
                    "section_type": str(c.get("section_type") or "body"),
                    "status": c["status"],
                    "claimed_by": active_claimed_by,
                    "claim_expires_at": active_claim_exp,
                    "accepted_submission_id": c.get("accepted_submission_id") or "",
                    "submission_count": subs_by_ch.get(c["chapter_id"], 0),
                }
            )
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
        out["public_corpus"] = _book_is_public_corpus(book)
        out["collab_ledger"] = (
            self.git_sync.read_ledger(book_slug, public_corpus=_book_is_public_corpus(book))
            if can_work
            else {}
        )
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
        section_type: Optional[str] = None,
        include_matter: bool = True,
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        self._check_read_rate(ctx)
        self._sweep_stale_evaluating()
        clean_sec = (section_type or "").strip().lower()
        if clean_sec and clean_sec not in VALID_SECTION_TYPES:
            raise ServiceError(
                f"Invalid section_type '{section_type}' (expected body, front_matter, back_matter, or boilerplate)",
                status_code=400,
            )
        eff_limit = max(1, min(int(limit), 500))
        eff_offset = max(0, int(offset))
        now = time.time()
        is_coord = _is_coord(ctx)
        books = [self.store.get_book(book_slug)] if book_slug else self.store.list_books(include_pending=is_coord)
        open_chapters: list[dict[str, Any]] = []
        review_targets: list[dict[str, Any]] = []
        revisions_needed: list[dict[str, Any]] = []
        books_pending_parse: list[dict[str, Any]] = []
        books_pending_seed: list[dict[str, Any]] = []
        open_bug_reports: list[dict[str, Any]] = []
        accepted_bug_reports: list[dict[str, Any]] = []
        if is_coord and role in {"all", "agent_admin"}:
            open_bug_reports = [
                _sanitize_bug_public(r)
                for r in self.store.list_bug_reports(
                    statuses=("open", "accepted", "in_progress"),
                    book_slug=book_slug,
                    include_flagged=False,
                    limit=50,
                )
            ]
        if _is_tech_admin(ctx) and role in {"all", "tech_admin"}:
            tech_rows = self.store.list_bug_reports(
                statuses=("accepted", "in_progress"),
                book_slug=book_slug,
                include_flagged=False,
                limit=50,
            )
            tech_rows.sort(key=lambda r: 0 if r.get("status") == "accepted" else 1)
            accepted_bug_reports = [_sanitize_bug_public(r) for r in tech_rows]

        for b in books:
            if not b:
                continue
            if not self._can_access_book_work(ctx, b):
                continue
            slug = b["slug"]
            subs = self.store.list_submissions(book_slug=slug)
            book_chapters = self.store.list_chapters(slug)
            ch_by_id = {c["chapter_id"]: c for c in book_chapters}
            ch_status_by_id = {c["chapter_id"]: c["status"] for c in book_chapters}

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
                                "total_chapters": len(book_chapters),
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
                for c in book_chapters:
                    if c["status"] != "open" or c["chapter_id"] in pending_gate_chapters:
                        continue
                    c_sec = str(c.get("section_type") or "body")
                    if clean_sec and c_sec != clean_sec:
                        continue
                    if not include_matter and c_sec != "body":
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
                            "section_type": c_sec,
                            "claimed": claimed_other,
                            "claimed_by": c["claimed_by"] if claimed_other else "",
                        }
                    )
            for sub in subs:
                ch_row = ch_by_id.get(sub["chapter_id"])
                ch_sec = str((ch_row or {}).get("section_type") or "body")
                if clean_sec and ch_sec != clean_sec:
                    continue
                if not include_matter and ch_sec != "body":
                    continue
                ch_open = ch_status_by_id.get(sub["chapter_id"]) == "open"
                if (
                    b["status"] in {"active", "published", "seeding"}
                    and ch_open
                    and role in {"all", "translator"}
                    and sub["author_actor_id"] == ctx.actor_id
                    and sub["status"] == "remediation_required"
                ):
                    revisions_needed.append(_summarize_submission_task(sub, b, ch_row))
                if (
                    role in {"all", "reviewer"}
                    and sub["author_actor_id"] != ctx.actor_id
                    and (
                        (ch_open and sub["status"] in {"pending_review", "remediation_required", "pending_admin_approval"})
                        or (sub["status"] == "accepted" and ch_sec != "boilerplate")
                    )
                ):
                    review_targets.append(_summarize_submission_task(sub, b, ch_row))

        review_targets.sort(
            key=lambda s: (
                _SUBMISSION_SORT_RANK.get(str(s.get("status") or ""), 99),
                _SECTION_SORT_RANK.get(str(s.get("section_type") or "body"), 99),
                str(s.get("book_slug") or ""),
                int(s.get("chapter_num") or 0),
            )
        )
        total_reviews = len(review_targets)
        paged_reviews = review_targets[eff_offset : eff_offset + eff_limit]
        return {
            "caller": {"tier": ctx.tier, "handle": ctx.handle, "agent_role": ctx.agent_role},
            "books_pending_parse": books_pending_parse,
            "books_pending_seed": books_pending_seed,
            "open_chapters": open_chapters,
            "revisions_needed": revisions_needed,
            "peer_review_candidates": paged_reviews,
            "peer_review_total": total_reviews,
            "peer_review_limit": eff_limit,
            "peer_review_offset": eff_offset,
            "peer_review_has_more": (eff_offset + len(paged_reviews)) < total_reviews,
            "open_bug_reports": open_bug_reports,
            "accepted_bug_reports": accepted_bug_reports,
        }

    def _effective_chapter_translation_xml(self, book_slug: str, chapter_row: dict[str, Any]) -> str:
        direct_xml = str(chapter_row.get("translation_xml") or "")
        if direct_xml:
            return direct_xml
        subs = self.store.list_submissions(book_slug=book_slug, chapter_id=chapter_row["chapter_id"])
        for status_target in ("accepted", "pending_review", "pending_admin_approval"):
            match = next((s for s in subs if s["status"] == status_target and s.get("translation_xml")), None)
            if match is not None:
                return str(match["translation_xml"])
        return ""

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
            p_tr_xml = self._effective_chapter_translation_xml(book_slug, p_ch)
            prev_info = {
                "chapter_id": p_ch["chapter_id"],
                "title": p_ch["title"],
                "status": p_ch["status"],
                "source_tail": _extract_segment_slice(p_ch["source_xml"], count=n_segs, from_end=True, field="original"),
                "translated_tail": _extract_segment_slice(p_tr_xml, count=n_segs, from_end=True, field="translate"),
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
        if ch01:
            ch01_xml = self._effective_chapter_translation_xml(book_slug, ch01)
            if ch01_xml:
                seed_excerpt = _extract_segment_slice(ch01_xml, count=n_segs, from_end=False, field="translate")
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
        active_claimed_by, active_claim_exp = _active_claim_fields(ch, time.time())
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
            "section_type": str(ch.get("section_type") or "body"),
            "status": ch["status"],
            "claimed_by": active_claimed_by,
            "claim_expires_at": active_claim_exp,
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
        self._assert_write_identity(ctx)
        self._check_write_rate(ctx)
        book = self.store.get_book(book_slug)
        if book is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        self._assert_book_work_access(ctx, book)
        self._assert_seeding_access(ctx, book)
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

    def _validate_chapter_import_item(
        self,
        *,
        book_slug: str,
        chapter_id: str,
        source_lang: str,
        manifest: dict[str, Any],
        glossary_sha: str,
        source_xml: str,
        translation_xml: str,
    ) -> str:
        """Validate pre-translated XML parity, byte-invariance, injection safety, and target language."""
        xml_bytes = translation_xml.encode("utf-8")
        if len(xml_bytes) > MAX_MCP_XML_BYTES:
            raise ServiceError(
                f"Chapter '{book_slug}/{chapter_id}' translation_xml exceeds {MAX_MCP_XML_BYTES}-byte cap",
                status_code=413,
            )
        segs = parse_segments(source_xml)
        seg_range = (
            f"{segs[0]['id']}..{segs[-1]['id']}"
            if len(segs) > 1
            else (segs[0]["id"] if segs else "S-0001")
        )
        sha_val = glossary_sha or _compute_glossary_sha({})
        synthetic_body = (
            "- **Agent**: mcpbot\n"
            "- **Engine/Model**: trusted-import\n"
            f"- **Source Language**: {source_lang}\n"
            "- **Target Language**: vi\n"
            f"- **Book**: {book_slug}\n"
            f"- **Chunk ID**: {book_slug}-{chapter_id}\n"
            "- **Issue**: #1\n"
            f"- **Glossary SHA**: {sha_val}\n"
            f"- **Segments**: {seg_range}\n"
        )
        val_res = validate_translation_pr(
            head_ref=f"translate/{book_slug}-{chapter_id}-mcpbot",
            author_login="mcpbot",
            body=synthetic_body,
            changed_files=[f"runs/{book_slug}/{chapter_id}/translation.xml"],
            source_xml_text=source_xml,
            translation_xml_text=translation_xml,
            expected_glossary_sha=sha_val,
            additions_data=None,
            manifest=manifest,
            expected_issue_number=1,
            bot_login=None,
            is_seed_pr=False,
            translation_xml_bytes=xml_bytes,
        )
        inj_flags = list(val_res.get("injection_flags") or [])
        if inj_flags:
            raise ServiceError(
                f"Chapter '{book_slug}/{chapter_id}' rejected by prompt-injection scanner",
                status_code=422,
                details={"defects": inj_flags, "chapter_id": chapter_id},
            )
        if not val_res["valid"]:
            raise ServiceError(
                f"Chapter '{book_slug}/{chapter_id}' failed deterministic structural validation",
                status_code=422,
                details={"defects": list(val_res["defects"]), "chapter_id": chapter_id},
            )
        lang_probe = enforce_language_detection(
            {"verdict": "PASS", "scores": {"fidelity": 5.0, "fluency": 5.0, "register": 5.0, "completeness": 5.0}, "issues": []},
            translation_xml,
            source_lang=source_lang,
        )
        if lang_probe.get("verdict") != "PASS":
            raise ServiceError(
                f"Chapter '{book_slug}/{chapter_id}' failed Vietnamese target-language detection",
                status_code=422,
                details={"defects": [str(i) for i in lang_probe.get("issues", [])], "chapter_id": chapter_id},
            )
        return _render_final_txt(translation_xml)

    def _persist_imported_submission(
        self,
        ctx: AuthContext,
        *,
        book_slug: str,
        chapter_id: str,
        translation_xml: str,
        final_txt: str,
        engine_model: str,
        approval_mode: str,
        existing_sub: Optional[dict[str, Any]] = None,
        auto_accept: bool = False,
    ) -> dict[str, Any]:
        """Insert or idempotently update an imported pre-translated chapter into peer/admin review or accepted."""
        target_status = (
            "accepted"
            if auto_accept
            else ("pending_admin_approval" if approval_mode == "manual" else "pending_review")
        )
        summary_msg = (
            f"Auto-accepted low-value boilerplate by {ctx.handle} ({engine_model}); skipped peer review queue."
            if auto_accept
            else f"Imported curated pre-translation by {ctx.handle} ({engine_model}); awaiting {target_status.replace('_', ' ')}."
        )
        hist_entry = {
            "round_number": (int(existing_sub.get("round_number") or 0) + 1) if existing_sub is not None else 1,
            "type": "auto_boilerplate" if auto_accept else "trusted_import",
            "engine_model": engine_model,
            "verdict": "AUTO_ACCEPTED_BOILERPLATE" if auto_accept else "IMPORTED",
            "stage_status": target_status.upper(),
            "judge_summary": summary_msg,
            "open_findings": [],
        }
        if existing_sub is not None:
            hist = list(existing_sub.get("rounds_history") or [])
            next_round = int(hist_entry["round_number"])
            hist.append(hist_entry)
            return self.store.update_submission(
                existing_sub["id"],
                status=target_status,
                round_number=next_round,
                workstream_id="",
                translation_xml=translation_xml,
                final_txt=final_txt,
                additions={"proposals": []},
                open_findings=[],
                security_flags=[],
                judge_summary=summary_msg,
                rounds_history=hist,
            )

        _raw_rt, rt_sha = mint_revision_token()
        hist = [hist_entry]
        return self.store.create_submission(
            book_slug=book_slug,
            chapter_id=chapter_id,
            author_actor_id=ctx.actor_id,
            author_handle=ctx.handle,
            author_ip_hash=ctx.client_ip_hash,
            engine_model=engine_model,
            status=target_status,
            round_number=1,
            max_rounds=self.cfg.max_revise_rounds,
            workstream_id="",
            revision_token_sha256=rt_sha,
            translation_xml=translation_xml,
            final_txt=final_txt,
            additions={"proposals": []},
            open_findings=[],
            security_flags=[],
            judge_summary=summary_msg,
            rounds_history=hist,
        )

    def _persist_and_maybe_auto_accept_import(
        self,
        ctx: AuthContext,
        *,
        book_slug: str,
        ch_row: dict[str, Any],
        translation_xml: str,
        final_txt: str,
        engine_model: str,
        approval_mode: str,
        auto_approve_boilerplate: bool,
        existing_sub: Optional[dict[str, Any]] = None,
    ) -> tuple[dict[str, Any], bool]:
        should_auto = (
            bool(auto_approve_boilerplate)
            and approval_mode != "manual"
            and str(ch_row.get("section_type") or "body") == "boilerplate"
        )
        saved = self._persist_imported_submission(
            ctx,
            book_slug=book_slug,
            chapter_id=str(ch_row["chapter_id"]),
            translation_xml=translation_xml,
            final_txt=final_txt,
            engine_model=engine_model,
            approval_mode=approval_mode,
            existing_sub=existing_sub,
            auto_accept=False,
        )
        if should_auto:
            fresh_book = self.store.get_book(book_slug)
            if fresh_book is not None:
                saved = self._finalize_accepted_chapter(
                    book=fresh_book,
                    ch=ch_row,
                    submission=saved,
                    default_reviewer="auto-boilerplate",
                    mark_submission_accepted=True,
                    final_txt=final_txt,
                )
        return saved, should_auto

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
        unlock_all_chapters: bool = False,
        queue_for_parse: bool = False,
        auto_approve_boilerplate: bool = True,
    ) -> dict[str, Any]:
        self._assert_write_identity(ctx)
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

        trusted = _is_trusted_submitter(ctx)
        norm_chapters, ch_bytes, ch_scan_text, ch_direct_inj = _normalize_proposed_chapters(chapters)
        if not trusted and any(c.get("translation_xml") for c in norm_chapters):
            raise AuthError(
                "Only ADMIN or promoted agent_admin callers may import pre-translated chapters",
                status_code=403,
            )

        raw_bytes = len(source_text.encode("utf-8"))
        total_bytes = raw_bytes + ch_bytes
        if total_bytes == 0:
            raise ServiceError("Either source_text or non-empty chapters is required", status_code=400)
        cap_bytes = MAX_TRUSTED_BOOK_PROPOSAL_BYTES if trusted else MAX_BOOK_PROPOSAL_BYTES
        if total_bytes > cap_bytes:
            cap_mb = cap_bytes // (1024 * 1024)
            raise ServiceError(f"Book proposal payload exceeds {cap_mb} MiB cap via MCP", status_code=413)
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
        inj = detect_prompt_injection(f"{title}\n{author}\n{source_text[:32000]}\n{ch_scan_text}") + rec_inj + brief_inj + ch_direct_inj
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

        if inj or not trusted:
            init_status = "flagged_security" if inj else "pending_approval"
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

        # Trusted submitter (ADMIN or promoted agent_admin) without security flags -> auto-approve
        imported_rows: list[tuple[str, str, str]] = []
        glossary_sha = ""
        extracted: list[dict[str, Any]] = []
        should_unlock = False
        init_status = "approved_pending_parse"

        if norm_chapters and not queue_for_parse:
            pub_corpus = is_public_corpus_book(license_id=license_id, access_mode="public")
            extracted = self.git_sync.prepare_book_files(
                slug=clean_slug,
                manifest=manifest,
                source_filename=source_filename.strip() or f"{clean_slug}.md",
                source_text=source_text,
                proposed_chapters=norm_chapters,
                initial_book_record=merged_record,
                initial_brief=norm_brief,
                overwrite_record=True,
                public_corpus=pub_corpus,
            )
            if not extracted:
                raise ServiceError(f"Zero chapters extracted for '{clean_slug}'", status_code=422)
            extracted = _attach_chapter_section_types(extracted, norm_chapters)
            has_translations = any(bool(c.get("translation_xml")) for c in norm_chapters)
            should_unlock = bool(unlock_all_chapters or _has_curated_book_record(merged_record) or has_translations)
            glossary_sha = _compute_glossary_sha(merged_record) if should_unlock else ""
            src_by_ch = {str(item["chapter_id"]): str(item["source_xml"]) for item in extracted}
            for item in norm_chapters:
                tr_xml = str(item.get("translation_xml") or "")
                if not tr_xml:
                    continue
                ch_id = str(item["chapter_id"])
                try:
                    final_txt = self._validate_chapter_import_item(
                        book_slug=clean_slug,
                        chapter_id=ch_id,
                        source_lang=source_lang,
                        manifest=manifest,
                        glossary_sha=glossary_sha,
                        source_xml=src_by_ch[ch_id],
                        translation_xml=tr_xml,
                    )
                except ServiceError:
                    self.git_sync.cleanup_untracked_book_files(slug=clean_slug)
                    raise
                imported_rows.append((ch_id, tr_xml, final_txt))
            init_status = "active" if should_unlock else "seeding"

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
            glossary_sha=glossary_sha,
            book_record=merged_record,
            brief=norm_brief,
            approval_mode=eff_approval_mode,
            manifest=manifest,
            proposed_by=ctx.handle,
            source_text=source_text,
            source_filename=source_filename.strip() or f"{clean_slug}.md",
            proposed_chapters=norm_chapters,
        )
        if extracted:
            self.store.replace_unaccepted_chapters(
                book_slug=clean_slug,
                chapters=extracted,
                unlock_all=should_unlock,
            )
        ch_row_by_id = {c["chapter_id"]: c for c in self.store.list_chapters(clean_slug)}
        imported_ids: list[str] = []
        auto_accepted: list[str] = []
        for ch_id, tr_xml, final_txt in imported_rows:
            ch_row = ch_row_by_id[ch_id]
            saved_sub, did_auto = self._persist_and_maybe_auto_accept_import(
                ctx,
                book_slug=clean_slug,
                ch_row=ch_row,
                translation_xml=tr_xml,
                final_txt=final_txt,
                engine_model="trusted-import",
                approval_mode=eff_approval_mode,
                auto_approve_boilerplate=auto_approve_boilerplate,
            )
            imported_ids.append(str(saved_sub["id"]))
            if did_auto:
                auto_accepted.append(ch_id)

        out = dict(self.store.get_book(clean_slug) or book)
        out.pop("source_text", None)
        out.pop("proposed_chapters", None)
        out["proposed_chapter_count"] = len(norm_chapters)
        if imported_ids:
            out["imported_submission_ids"] = imported_ids
        if auto_accepted:
            out["auto_accepted_chapters"] = auto_accepted
            out["auto_accepted_count"] = len(auto_accepted)
        return out

    def submit_book_parse(
        self,
        ctx: AuthContext,
        *,
        book_slug: str,
        chapters: Any = None,
        brief: Any = None,
        book_record: Any = None,
        parse_notes: str = "",
        unlock_all_chapters: bool = False,
    ) -> dict[str, Any]:
        """Accept parsed chapters, Brief, and initial book_record from agent_admin or ADMIN."""
        self._check_write_rate(ctx)
        assert_agent_admin_or_admin(ctx)
        book = self.store.get_book(book_slug)
        if book is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        self._assert_book_work_access(ctx, book)
        if book["status"] == "pending_approval":
            if ctx.tier != "ADMIN" and book.get("proposed_by") != ctx.handle:
                raise AuthError(
                    "Only ADMIN or the agent_admin who proposed this book may parse a pending_approval proposal directly",
                    status_code=403,
                )
        elif book["status"] not in {"approved_pending_parse", "seeding", "seed_failed"}:
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

        effective_chapters = book.get("proposed_chapters") or [] if chapters is None else chapters
        norm_chapters, ch_bytes, ch_scan_text, ch_direct_inj = _normalize_proposed_chapters(effective_chapters)
        if not norm_chapters:
            raise ServiceError("chapters must contain at least one chapter object", status_code=400)
        if ch_bytes > MAX_TRUSTED_BOOK_PROPOSAL_BYTES:
            raise ServiceError("Parsed chapters payload exceeds 10 MiB cap", status_code=413)

        norm_brief, brief_inj = _normalize_brief(brief, default_genre=book.get("genre") or "fiction")
        merged_record, rec_inj = _merge_initial_book_record(book.get("book_record") or {}, book_record)
        inj = detect_prompt_injection(f"{ch_scan_text}\n{parse_notes[:4000]}") + brief_inj + rec_inj + ch_direct_inj
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
            public_corpus=_book_is_public_corpus(book),
        )
        if not extracted:
            raise ServiceError(f"Zero chapters extracted for '{book_slug}'", status_code=422)
        extracted = _attach_chapter_section_types(extracted, norm_chapters)

        has_translations = any(bool(c.get("translation_xml")) for c in norm_chapters)
        should_unlock = bool(unlock_all_chapters or has_translations)
        new_sha = _compute_glossary_sha(merged_record) if should_unlock else ""
        src_by_ch = {str(item["chapter_id"]): str(item["source_xml"]) for item in extracted}
        imported_rows: list[tuple[str, str, str]] = []
        for item in norm_chapters:
            tr_xml = str(item.get("translation_xml") or "")
            if not tr_xml:
                continue
            ch_id = str(item["chapter_id"])
            final_txt = self._validate_chapter_import_item(
                book_slug=book_slug,
                chapter_id=ch_id,
                source_lang=str(book["source_lang"]),
                manifest=book["manifest"],
                glossary_sha=new_sha,
                source_xml=src_by_ch[ch_id],
                translation_xml=tr_xml,
            )
            imported_rows.append((ch_id, tr_xml, final_txt))

        self.store.replace_unaccepted_chapters(
            book_slug=book_slug,
            chapters=extracted,
            unlock_all=should_unlock,
        )
        self.store.set_book_brief_and_record(
            slug=book_slug,
            brief=norm_brief,
            book_record=merged_record,
            glossary_sha=new_sha if should_unlock else None,
        )
        self.store.set_book_status(slug=book_slug, status="active" if should_unlock else "seeding")
        app_mode = str(book.get("approval_mode") or "peer")
        ch_row_by_id = {c["chapter_id"]: c for c in self.store.list_chapters(book_slug)}
        imported_ids: list[str] = []
        for ch_id, tr_xml, final_txt in imported_rows:
            saved_sub, _ = self._persist_and_maybe_auto_accept_import(
                ctx,
                book_slug=book_slug,
                ch_row=ch_row_by_id[ch_id],
                translation_xml=tr_xml,
                final_txt=final_txt,
                engine_model="trusted-import",
                approval_mode=app_mode,
                auto_approve_boilerplate=True,
            )
            imported_ids.append(str(saved_sub["id"]))

        res = self.get_book(ctx, book_slug=book_slug)
        res["parse_notes"] = parse_notes.strip()
        if imported_ids:
            res["imported_submission_ids"] = imported_ids
        return res

    def import_chapter_translations(
        self,
        ctx: AuthContext,
        *,
        book_slug: str,
        chapters: Any,
        engine_model: str = "trusted-import",
        unlock_all_chapters: bool = True,
        auto_approve_boilerplate: bool = True,
    ) -> dict[str, Any]:
        """Validate and import a batch of pre-translated chapters directly into peer/admin review."""
        self._check_write_rate(ctx)
        self._sweep_stale_evaluating()
        assert_agent_admin_or_admin(ctx)

        book = self.store.get_book(book_slug)
        if book is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        self._assert_book_work_access(ctx, book)
        if book["status"] == "rejected":
            raise ServiceError(f"Book '{book_slug}' is rejected", status_code=409)
        if book["status"] == "pending_approval" and ctx.tier != "ADMIN" and book.get("proposed_by") != ctx.handle:
            raise AuthError(
                "Only ADMIN or the agent_admin who proposed this book may import translations before intake approval",
                status_code=403,
            )

        if not isinstance(chapters, list) or not chapters:
            raise ServiceError("chapters must be a non-empty list of chapter objects", status_code=400)
        if len(chapters) > MAX_IMPORT_CHAPTERS_BATCH:
            raise ServiceError(
                f"chapters batch exceeds maximum of {MAX_IMPORT_CHAPTERS_BATCH} items",
                status_code=400,
            )

        normalized_items: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        batch_bytes = 0
        for idx, raw in enumerate(chapters):
            if not isinstance(raw, dict):
                raise ServiceError(f"chapters[{idx}] must be an object", status_code=400)
            ch_id = str(raw.get("chapter_id") or "").strip().lower()
            if not _PROPOSED_CHAPTER_ID_RE.match(ch_id) or ch_id in seen_ids:
                raise ServiceError(
                    f"Invalid or duplicate chapter_id '{ch_id}' in batch",
                    status_code=400,
                )
            seen_ids.add(ch_id)
            tr_xml = str(raw.get("translation_xml") or "")
            if not tr_xml.strip():
                raise ServiceError(
                    f"Chapter '{ch_id}' must supply non-empty translation_xml",
                    status_code=400,
                )
            tr_bytes = len(tr_xml.encode("utf-8"))
            if tr_bytes > MAX_MCP_XML_BYTES:
                raise ServiceError(
                    f"Chapter '{book_slug}/{ch_id}' translation_xml exceeds {MAX_MCP_XML_BYTES}-byte cap",
                    status_code=413,
                )
            batch_bytes += tr_bytes
            if batch_bytes > MAX_BOOK_PROPOSAL_BYTES:
                raise ServiceError("Batch import payload exceeds 5 MiB cap", status_code=413)
            explicit_sec = str(raw.get("section_type") or "").strip().lower()
            if explicit_sec and explicit_sec not in VALID_SECTION_TYPES:
                raise ServiceError(
                    f"Chapter '{ch_id}' has invalid section_type '{raw.get('section_type')}'",
                    status_code=400,
                )
            normalized_items.append({"chapter_id": ch_id, "translation_xml": tr_xml, "section_type": explicit_sec})

        # Auto-unpack proposed chapters if book was waiting in approved_pending_parse or own pending_approval
        existing_chapters = self.store.list_chapters(book_slug)
        if not existing_chapters:
            prop_chs = book.get("proposed_chapters") or []
            if prop_chs or (book.get("source_text") or "").strip():
                extracted = self.git_sync.prepare_book_files(
                    slug=book_slug,
                    manifest=book["manifest"],
                    source_filename=book.get("source_filename") or f"{book_slug}.md",
                    source_text=book.get("source_text") or "",
                    proposed_chapters=prop_chs or None,
                    initial_book_record=book.get("book_record") or None,
                    initial_brief=book.get("brief") or None,
                    public_corpus=_book_is_public_corpus(book),
                )
                if not extracted:
                    raise ServiceError(f"Zero chapters extracted for '{book_slug}'", status_code=422)
                extracted = _attach_chapter_section_types(extracted, prop_chs or None)
                self.store.replace_unaccepted_chapters(
                    book_slug=book_slug,
                    chapters=extracted,
                    unlock_all=bool(unlock_all_chapters),
                )
                existing_chapters = self.store.list_chapters(book_slug)

        ch_by_id = {c["chapter_id"]: c for c in existing_chapters}
        eff_sha = str(book.get("glossary_sha") or "") or _compute_glossary_sha(book.get("book_record") or {})
        eff_engine = (engine_model or "trusted-import").strip() or "trusted-import"

        # Phase 1: Validate ALL chapters before mutating any submission row (atomic rollback on error)
        staged: list[tuple[dict[str, Any], str, str, Optional[dict[str, Any]]]] = []
        for item in normalized_items:
            ch_id = item["chapter_id"]
            ch_row = ch_by_id.get(ch_id)
            if ch_row is None:
                raise ServiceError(f"Chapter '{book_slug}/{ch_id}' not found", status_code=404)
            if ch_row["status"] == "accepted":
                raise ServiceError(f"Chapter '{book_slug}/{ch_id}' is already accepted", status_code=409)

            subs_for_ch = self.store.list_submissions(book_slug=book_slug, chapter_id=ch_id)
            eval_sub = next((s for s in subs_for_ch if s["status"] == "evaluating"), None)
            if eval_sub is not None:
                raise ServiceError(
                    f"Chapter '{book_slug}/{ch_id}' is actively evaluating in submission '{eval_sub['id']}'",
                    status_code=409,
                )
            other_active = next(
                (
                    s
                    for s in subs_for_ch
                    if s["status"] in {"pending_review", "pending_admin_approval"}
                    and s["author_actor_id"] != ctx.actor_id
                ),
                None,
            )
            if other_active is not None:
                raise ServiceError(
                    f"Chapter '{book_slug}/{ch_id}' already has an active submission '{other_active['id']}' by '{other_active['author_handle']}'",
                    status_code=409,
                )

            final_txt = self._validate_chapter_import_item(
                book_slug=book_slug,
                chapter_id=ch_id,
                source_lang=str(book["source_lang"]),
                manifest=book["manifest"],
                glossary_sha=eff_sha,
                source_xml=str(ch_row["source_xml"]),
                translation_xml=item["translation_xml"],
            )
            if item.get("section_type") and item["section_type"] != ch_row.get("section_type"):
                ch_row = dict(ch_row)
                ch_row["section_type"] = item["section_type"]
                self.store.update_chapter_section_type(
                    book_slug=book_slug,
                    chapter_id=ch_id,
                    section_type=item["section_type"],
                )
            own_existing = next(
                (
                    s
                    for s in subs_for_ch
                    if s["author_actor_id"] == ctx.actor_id
                    and s["status"] in {"pending_review", "pending_admin_approval", "remediation_required"}
                ),
                None,
            )
            staged.append((ch_row, item["translation_xml"], final_txt, own_existing))

        # Phase 2: Lock glossary, unlock locked_seed chapters if requested, activate book, and persist submissions
        if not book.get("glossary_sha"):
            self.store.set_book_brief_and_record(slug=book_slug, glossary_sha=eff_sha)
        if unlock_all_chapters:
            for c in existing_chapters:
                if c["status"] == "locked_seed":
                    self.store.upsert_chapter(
                        book_slug=book_slug,
                        chapter_id=c["chapter_id"],
                        chapter_num=int(c["chapter_num"]),
                        title=c["title"],
                        source_xml=c["source_xml"],
                        status="open",
                        section_type=str(c.get("section_type") or "body"),
                        opened_at=time.time(),
                    )
        if book["status"] not in {"published", "completed"}:
            self.store.set_book_status(slug=book_slug, status="active")

        app_mode = str(book.get("approval_mode") or "peer")
        imported_cnt = 0
        updated_cnt = 0
        auto_accepted_chapters: list[str] = []
        saved_subs: list[dict[str, Any]] = []
        for ch_row, tr_xml, final_txt, own_existing in staged:
            saved, did_auto = self._persist_and_maybe_auto_accept_import(
                ctx,
                book_slug=book_slug,
                ch_row=ch_row,
                translation_xml=tr_xml,
                final_txt=final_txt,
                engine_model=eff_engine,
                approval_mode=app_mode,
                auto_approve_boilerplate=auto_approve_boilerplate,
                existing_sub=own_existing,
            )
            if own_existing is not None:
                updated_cnt += 1
            else:
                imported_cnt += 1
            if did_auto:
                auto_accepted_chapters.append(str(ch_row["chapter_id"]))
            saved_subs.append(_sanitize_submission_public(saved))

        fresh_book = self.store.get_book(book_slug) or book
        return {
            "book_slug": book_slug,
            "book_status": fresh_book["status"],
            "glossary_sha": fresh_book["glossary_sha"],
            "imported_count": imported_cnt,
            "updated_count": updated_cnt,
            "auto_accepted_count": len(auto_accepted_chapters),
            "auto_accepted_chapters": auto_accepted_chapters,
            "submissions": saved_subs,
        }

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
        if locked and ctx.tier != "ADMIN" and book_record is not None:
            raise ServiceError(
                "glossary_sha is already locked; agent_admin may update brief or submit additive additions",
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
        self.git_sync.write_brief_and_record(
            slug=book_slug,
            brief=eff_brief,
            book_record=eff_record,
            public_corpus=_book_is_public_corpus(book),
        )
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
        self._assert_write_identity(ctx)
        self._check_write_rate(ctx)
        self._sweep_stale_evaluating()

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
            self._assert_seeding_access(ctx, book)
            existing_for_ch = self.store.list_submissions(book_slug=book_slug, chapter_id=chapter_id)
            eval_sub = next((s for s in existing_for_ch if s["status"] == "evaluating"), None)
            if eval_sub is not None:
                upd_ts = parse_utc_timestamp(str(eval_sub.get("updated_at") or ""))
                elapsed_s = int(max(0.0, time.time() - upd_ts)) if upd_ts is not None else 0
                raise ServiceError(
                    (
                        f"Chapter '{book_slug}/{chapter_id}' has submission '{eval_sub['id']}' currently actively evaluating "
                        f"on {self.cfg.review_server_url} (elapsed {elapsed_s}s); wait for evaluation to settle"
                    ),
                    status_code=409,
                    details={
                        "blocking_submission_id": eval_sub["id"],
                        "blocking_status": "evaluating",
                        "elapsed_seconds": elapsed_s,
                    },
                )
            passing_sub = next(
                (s for s in existing_for_ch if s["status"] in {"pending_review", "pending_admin_approval"}),
                None,
            )
            if passing_sub is not None:
                raise ServiceError(
                    (
                        f"Chapter '{book_slug}/{chapter_id}' already has a passing submission '{passing_sub['id']}' "
                        f"in '{passing_sub['status']}' awaiting peer/admin review"
                    ),
                    status_code=409,
                    details={
                        "blocking_submission_id": passing_sub["id"],
                        "blocking_status": passing_sub["status"],
                    },
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
                f"## Book Brief\n```json\n{json.dumps(book['brief'], ensure_ascii=False)}\n```\n\n"
            )
        compact_record = {
            "characters": (book.get("book_record") or {}).get("characters") or {},
            "terms": (book.get("book_record") or {}).get("terms") or {},
            "pronoun_pairs": (book.get("book_record") or {}).get("pronoun_pairs") or [],
        }
        review_bundle = (
            f"# Chapter Translation Review — {book['title']} ({book_slug} / {chapter_id})\n\n"
            f"{brief_section}"
            f"## Canonical book_record.json\n```json\n{json.dumps(compact_record, ensure_ascii=False)}\n```\n\n"
            f"## Submitted Bilingual Segments (translation.xml)\n```xml\n{translation_xml}\n```\n"
        )
        final_txt = _render_final_txt(translation_xml)
        app_mode = str(book.get("approval_mode") or "auto")
        prior_history = list(existing_sub.get("rounds_history") or []) if existing_sub else []

        def _persist_round_outcome(target_sub_id: str, round_res: dict[str, Any]) -> dict[str, Any]:
            verdict = round_res["verdict"]
            round_num = int(round_res["roundNumber"])
            open_findings = list(round_res.get("openFindings") or [])
            op_failure_only = verdict != "PASS" and _is_operational_specialist_failure_only(open_findings)
            persisted_ws_id = ws_id
            persisted_round_num = round_num
            persisted_summary = str(round_res.get("judgeSummary") or "")

            if verdict == "PASS":
                hold_admin_seed = bool(hold_for_review) and ctx.tier == "ADMIN"
                if hold_admin_seed or (app_mode == "manual" and ctx.tier != "ADMIN"):
                    sub_status = "pending_admin_approval"
                elif app_mode == "peer" and ctx.tier != "ADMIN":
                    sub_status = "pending_review"
                else:
                    self.review_client.approve_workstream(workstream_id=ws_id)
                    sub_status = "accepted"
            elif op_failure_only:
                sub_status = "remediation_required"
                persisted_round_num = int(existing_sub["round_number"]) if existing_sub else 0
                persisted_ws_id = ""
                persisted_summary = (
                    "[Operational specialist timeout/failure — round not counted; retry opens a fresh workstream] "
                    f"{persisted_summary}"
                )
            else:
                sub_status = (
                    "escalated_to_admin"
                    if round_num >= self.cfg.max_revise_rounds or round_res["stageStatus"] == "ESCALATED_TO_HUMAN"
                    else "remediation_required"
                )

            updated_history = list(prior_history)
            updated_history.append(
                {
                    "round_number": persisted_round_num if op_failure_only else round_num,
                    "type": "translation_round",
                    "engine_model": engine_model,
                    "rebuttals": rebuttals or [],
                    "verdict": verdict,
                    "stage_status": round_res["stageStatus"],
                    "p0_count": round_res["p0Count"],
                    "p1_count": round_res["p1Count"],
                    "p2_count": round_res["p2Count"],
                    "judge_summary": persisted_summary,
                    "open_findings": open_findings,
                }
            )

            persist_status = "pending_admin_approval" if sub_status == "accepted" else sub_status
            saved = self.store.update_submission(
                target_sub_id,
                status=persist_status,
                round_number=persisted_round_num,
                workstream_id=persisted_ws_id,
                translation_xml=translation_xml,
                final_txt=final_txt,
                additions=norm_additions,
                open_findings=open_findings,
                judge_summary=persisted_summary,
                rounds_history=updated_history,
            )

            if sub_status == "accepted":
                fresh_book = self.store.get_book(book_slug) or book
                fresh_ch = self.store.get_chapter(book_slug, chapter_id) or ch
                saved = self._finalize_accepted_chapter(
                    book=fresh_book,
                    ch=fresh_ch,
                    submission=saved,
                    mark_submission_accepted=True,
                    final_txt=final_txt,
                )
            return saved

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
            self.store.supersede_author_remediation_drafts(
                book_slug=book_slug,
                chapter_id=chapter_id,
                author_actor_id=eff_ctx.actor_id,
                keep_submission_id=str(pending_sub["id"]),
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

        if background:
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
                        workstream_id="",
                        round_number=int(existing_sub["round_number"]) if existing_sub else 0,
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

        try:
            round_res = self.review_client.run_translation_round(
                workstream_id=ws_id,
                content=review_bundle,
                rebuttals=rebuttals,
            )
        except Exception as exc:
            self.store.update_submission(
                target_id,
                status="remediation_required",
                workstream_id="",
                round_number=int(existing_sub["round_number"]) if existing_sub else 0,
                judge_summary=f"Review round error: {exc}",
            )
            raise
        saved_sub = _persist_round_outcome(target_id, round_res)

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
        default_reviewer: str = "admin-override",
        mark_submission_accepted: bool = False,
        final_txt: Optional[str] = None,
    ) -> dict[str, Any]:
        slug = book["slug"]
        chapter_id = ch["chapter_id"]
        eff_final_txt = final_txt if final_txt is not None else str(submission.get("final_txt") or "")
        proposals = (submission.get("additions") or {}).get("proposals") or []
        updated_record = _merge_proposals_into_record(book["book_record"], proposals)
        glossary_sha = book["glossary_sha"]
        if not glossary_sha:
            glossary_sha = _compute_glossary_sha(updated_record)

        existing_chapters = self.store.list_chapters(slug)
        predicted_chapters: list[dict[str, Any]] = []
        for row in existing_chapters:
            copy_row = dict(row)
            if copy_row["chapter_id"] == chapter_id:
                copy_row["status"] = "accepted"
            elif chapter_id == "ch01" and copy_row["status"] == "locked_seed":
                copy_row["status"] = "open"
            predicted_chapters.append(copy_row)
        all_done = bool(predicted_chapters) and all(c["status"] == "accepted" for c in predicted_chapters)
        pub_corpus = _book_is_public_corpus(book)
        new_book_status = ("published" if pub_corpus else "completed") if all_done else "active"

        peer_revs = self.store.list_peer_reviews(submission["id"])
        rev_handles = [r["reviewer_handle"] for r in peer_revs]
        if submission.get("workstream_id"):
            rev_handles.append("review-vinhthang-dev")
        elif not rev_handles:
            rev_handles.append(default_reviewer)
        avg_score = (
            sum(float(r["weighted_score"]) for r in peer_revs) / len(peer_revs)
            if peer_revs
            else 4.5
        )

        # Sync Git BEFORE opening DB transaction so network Git I/O never holds store._lock.
        self.git_sync.sync_accepted_chapter(
            slug=slug,
            chapter_id=chapter_id,
            chapter_num=int(ch["chapter_num"]),
            source_xml=ch["source_xml"],
            translation_xml=submission["translation_xml"],
            final_txt=eff_final_txt,
            book_record=updated_record,
            brief=book.get("brief") or None,
            glossary_sha=glossary_sha,
            translator_handle=submission["author_handle"],
            reviewer_handles=rev_handles,
            score_of_record=avg_score,
            all_chapters_status=predicted_chapters,
            public_corpus=pub_corpus,
        )

        result_sub = submission
        with self.store.transaction():
            if mark_submission_accepted:
                result_sub = self.store.update_submission(
                    str(submission["id"]),
                    status="accepted",
                    final_txt=eff_final_txt,
                )
            self.store.mark_chapter_accepted(
                book_slug=slug,
                chapter_id=chapter_id,
                submission_id=str(result_sub["id"]),
                translation_xml=result_sub["translation_xml"],
                final_txt=eff_final_txt,
            )
            self.store.supersede_other_chapter_submissions(
                book_slug=slug,
                chapter_id=chapter_id,
                accepted_submission_id=str(result_sub["id"]),
            )

            # Unlock ch02..chNN if ch01 just finished seeding
            if chapter_id == "ch01":
                for other in existing_chapters:
                    if other["chapter_id"] != "ch01" and other["status"] == "locked_seed":
                        self.store.upsert_chapter(
                            book_slug=slug,
                            chapter_id=other["chapter_id"],
                            chapter_num=int(other["chapter_num"]),
                            title=other["title"],
                            source_xml=other["source_xml"],
                            status="open",
                            section_type=str(other.get("section_type") or "body"),
                            opened_at=time.time(),
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

        if all_done and pub_corpus:
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
        return result_sub

    def query_submissions_page(
        self,
        ctx: AuthContext,
        *,
        book_slug: Optional[str] = None,
        chapter_id: Optional[str] = None,
        status: Optional[str] = None,
        author_handle: Optional[str] = None,
        section_type: Optional[str] = None,
        include_xml: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Query paginated sanitized submissions across books accessible to the caller."""
        self._check_read_rate(ctx)
        self._sweep_stale_evaluating()
        clean_sec = (section_type or "").strip().lower()
        if clean_sec and clean_sec not in VALID_SECTION_TYPES:
            raise ServiceError(
                f"Invalid section_type '{section_type}' (expected body, front_matter, back_matter, or boilerplate)",
                status_code=400,
            )
        rows = self.store.list_submissions(
            book_slug=book_slug,
            chapter_id=chapter_id,
            status=status,
        )
        book_cache: dict[str, Optional[dict[str, Any]]] = {}
        ch_cache: dict[tuple[str, str], Optional[dict[str, Any]]] = {}
        eff_limit = max(1, min(int(limit), 500))
        eff_offset = max(0, int(offset))
        filtered: list[dict[str, Any]] = []
        for s in rows:
            slug = s["book_slug"]
            if slug not in book_cache:
                book_cache[slug] = self.store.get_book(slug)
            bk = book_cache[slug]
            if bk is None or not self._can_access_book_work(ctx, bk):
                continue
            if author_handle and s.get("author_handle") != author_handle.strip():
                continue
            c_key = (slug, str(s["chapter_id"]))
            if c_key not in ch_cache:
                ch_cache[c_key] = self.store.get_chapter(slug, str(s["chapter_id"]))
            ch_row = ch_cache[c_key]
            ch_sec = str((ch_row or {}).get("section_type") or "body")
            if clean_sec and ch_sec != clean_sec:
                continue
            item = _sanitize_submission_public(s, include_xml=include_xml)
            item["section_type"] = ch_sec
            revs = self.store.list_peer_reviews(s["id"])
            item["peer_reviews"] = revs
            item["peer_review_count"] = len(revs)
            filtered.append(item)

        total = len(filtered)
        paged = filtered[eff_offset : eff_offset + eff_limit]
        return {
            "submissions": paged,
            "total": total,
            "limit": eff_limit,
            "offset": eff_offset,
            "has_more": (eff_offset + len(paged)) < total,
        }

    def query_submissions(
        self,
        ctx: AuthContext,
        *,
        book_slug: Optional[str] = None,
        chapter_id: Optional[str] = None,
        status: Optional[str] = None,
        author_handle: Optional[str] = None,
        section_type: Optional[str] = None,
        include_xml: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query sanitized submissions as a flat list for direct callers."""
        return self.query_submissions_page(
            ctx,
            book_slug=book_slug,
            chapter_id=chapter_id,
            status=status,
            author_handle=author_handle,
            section_type=section_type,
            include_xml=include_xml,
            limit=limit,
            offset=offset,
        )["submissions"]

    def get_submission_status(
        self,
        ctx: AuthContext,
        *,
        submission_id: str,
        include_context: bool = True,
        include_workstream: bool = False,
    ) -> dict[str, Any]:
        self._check_read_rate(ctx)
        self._sweep_stale_evaluating()
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
            eff_brief = book.get("brief") or {}
            eff_rec = book.get("book_record") or {}
            out["book_context"] = {
                "slug": book["slug"],
                "title": book["title"],
                "author": book["author"],
                "source_lang": book["source_lang"],
                "target_lang": book["target_lang"],
                "approval_mode": book["approval_mode"],
                "glossary_sha": book["glossary_sha"],
                "brief": eff_brief,
                "book_record": eff_rec,
            }
            out["book_record"] = eff_rec
            out["brief"] = eff_brief
            ch_ctx: Optional[dict[str, Any]] = None
            if ch is not None:
                ch_ctx = {
                    "chapter_id": ch["chapter_id"],
                    "chapter_num": ch["chapter_num"],
                    "title": ch["title"],
                    "section_type": str(ch.get("section_type") or "body"),
                    "status": ch["status"],
                    "source_xml": ch["source_xml"],
                    "neighbors": self._build_chapter_neighbors(
                        book_slug=sub["book_slug"],
                        chapter_id=sub["chapter_id"],
                    ),
                }
                out["chapter_context"] = ch_ctx
            out["context"] = {
                "book_slug": book["slug"],
                "book_title": book["title"],
                "author": book["author"],
                "source_lang": book["source_lang"],
                "target_lang": book["target_lang"],
                "approval_mode": book["approval_mode"],
                "glossary_sha": book["glossary_sha"],
                "brief": eff_brief,
                "book_record": eff_rec,
                "chapter_id": ch_ctx["chapter_id"] if ch_ctx else str(sub["chapter_id"]),
                "chapter_num": ch_ctx["chapter_num"] if ch_ctx else 0,
                "chapter_title": ch_ctx["title"] if ch_ctx else str(sub["chapter_id"]),
                "section_type": ch_ctx["section_type"] if ch_ctx else "body",
                "chapter_status": ch_ctx["status"] if ch_ctx else "",
                "source_xml": ch_ctx["source_xml"] if ch_ctx else "",
                "neighbors": ch_ctx["neighbors"] if ch_ctx else {},
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
        self._assert_write_identity(ctx)
        self._check_write_rate(ctx)
        if len((body_md or "").strip()) > MAX_REVIEW_BODY_CHARS:
            raise ServiceError(
                (
                    f"Peer review body exceeds concise {MAX_REVIEW_BODY_CHARS}-character cap "
                    f"(got {len((body_md or '').strip())}); keep reviews concise with verdict, 6 scores, and bulleted S-NNNN findings"
                ),
                status_code=422,
            )
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
                    latest_sub = self._finalize_accepted_chapter(
                        book=book,
                        ch=ch,
                        submission=sub,
                        mark_submission_accepted=True,
                        final_txt=final_txt,
                    )
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
        """Promote or demote a registered agent across 'contributor', 'agent_admin', and 'tech_admin' (Human ADMIN only)."""
        assert_admin(ctx)
        clean_role = (role or "").strip().lower()
        if clean_role not in VALID_AGENT_ROLES:
            raise ServiceError("role must be 'contributor', 'agent_admin', or 'tech_admin'", status_code=400)
        resolved_handle = self.store.resolve_agent_identifier(identifier)
        if resolved_handle is None:
            raise ServiceError(f"Agent '{identifier}' not found", status_code=404)
        return self.store.set_agent_role(handle=resolved_handle, role=clean_role)

    def list_agents_with_summary(self, ctx: AuthContext) -> dict[str, Any]:
        """Return registered agents enriched with live presence, sorted presence-first, and summary counts."""
        assert_admin(ctx)
        raw = self.store.list_agents_with_stats()
        agents, summary = enrich_and_sort_agents(raw, tracker=self.presence)
        return {"agents": agents, "summary": summary}

    def list_agents(self, ctx: AuthContext) -> list[dict[str, Any]]:
        return self.list_agents_with_summary(ctx)["agents"]

    def rotate_agent_key(self, ctx: AuthContext, *, identifier: str) -> dict[str, Any]:
        """Rotate an existing agent's 3-word AGENT_KEY and invalidate the old key immediately (ADMIN)."""
        assert_admin(ctx)
        resolved = self.store.resolve_agent_identifier(identifier)
        if resolved is None:
            raise ServiceError(f"Agent '{identifier}' not found", status_code=404)
        return self.store.rotate_agent_key(resolved)

    def revoke_agent(self, ctx: AuthContext, *, identifier: str, reactivate: bool = False) -> dict[str, Any]:
        """Soft-revoke (or reactivate) an agent with history, or hard-delete an unused agent (ADMIN)."""
        assert_admin(ctx)
        resolved = self.store.resolve_agent_identifier(identifier)
        if resolved is None:
            raise ServiceError(f"Agent '{identifier}' not found", status_code=404)
        return self.store.revoke_or_delete_agent(resolved, reactivate=reactivate)

    def update_agent_profile(
        self,
        ctx: AuthContext,
        *,
        identifier: str,
        display_name: Optional[str] = None,
        model_info: Optional[str] = None,
    ) -> dict[str, Any]:
        """Update human-readable display_name and/or model_info on an existing agent (ADMIN)."""
        assert_admin(ctx)
        resolved = self.store.resolve_agent_identifier(identifier)
        if resolved is None:
            raise ServiceError(f"Agent '{identifier}' not found", status_code=404)
        clean_name: Optional[str] = None
        if display_name is not None:
            clean_name = display_name.strip()
            if not clean_name or len(clean_name) > 80:
                raise ServiceError("display_name must be 1 to 80 characters", status_code=400)
            if detect_prompt_injection(clean_name):
                raise ServiceError("display_name blocked by prompt-injection scanner", status_code=422)
        clean_model: Optional[str] = None
        if model_info is not None:
            clean_model = model_info.strip()
            if len(clean_model) > 80:
                raise ServiceError("model_info must be at most 80 characters", status_code=400)
            if detect_prompt_injection(clean_model):
                raise ServiceError("model_info blocked by prompt-injection scanner", status_code=422)
        updated = self.store.update_agent_profile(
            handle=resolved,
            display_name=clean_name,
            model_info=clean_model,
        )
        return compute_agent_presence(
            updated,
            live_connections=self.presence.active_connection_count(resolved),
        )

    def agent_heartbeat(
        self,
        ctx: AuthContext,
        *,
        status_note: str = "",
        transport: str = "mcp-http",
        client_name: str = "",
    ) -> dict[str, Any]:
        """Record a lightweight liveness heartbeat and optional status_note for an authenticated AGENT_KEY."""
        if ctx.tier != "AGENT_KEY":
            raise AuthError("Authenticated 3-word AGENT_KEY is required for agent_heartbeat", status_code=403)
        self.rate_limiter.check(
            f"heartbeat:{ctx.actor_id}",
            limit=self.cfg.agent_heartbeat_rpm,
            window_seconds=60.0,
        )
        clean_note = (status_note or "").strip()
        if len(clean_note) > 140:
            raise ServiceError("status_note must be at most 140 characters", status_code=400)
        if clean_note and detect_prompt_injection(clean_note):
            raise ServiceError("status_note blocked by prompt-injection scanner", status_code=422)
        clean_client = (client_name or "").strip()
        if len(clean_client) > 80:
            clean_client = clean_client[:80]
        if clean_client and detect_prompt_injection(clean_client):
            raise ServiceError("client_name blocked by prompt-injection scanner", status_code=422)
        self.store.touch_agent_last_seen(
            ctx.handle,
            transport=transport or "mcp-http",
            action="heartbeat",
            client_name=clean_client,
            status_note=clean_note,
        )
        refreshed = self.store.get_agent(ctx.handle)
        if refreshed is None:
            raise ServiceError(f"Agent '{ctx.handle}' not found", status_code=404)
        return {
            "agent": compute_agent_presence(
                refreshed,
                live_connections=self.presence.active_connection_count(ctx.handle),
            )
        }

    def admin_mint_agent(
        self,
        ctx: AuthContext,
        *,
        handle: str = "",
        display_name: str = "",
        model_info: str = "",
        role: str = "contributor",
    ) -> dict[str, str]:
        assert_admin(ctx)
        clean_role = (role or "contributor").strip().lower() or "contributor"
        if clean_role not in VALID_AGENT_ROLES:
            raise ServiceError("role must be 'contributor', 'agent_admin', or 'tech_admin'", status_code=400)
        try:
            return self.store.register_agent(
                handle=handle,
                display_name=display_name,
                model_info=model_info,
                role=clean_role,
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
        if not _book_is_public_corpus(updated):
            self.git_sync.evict_to_private_storage(slug=book_slug)
        out = dict(updated)
        out.pop("source_text", None)
        out["public_corpus"] = _book_is_public_corpus(updated)
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
        unlock_all_chapters: bool = False,
    ) -> dict[str, Any]:
        assert_admin(ctx)
        book = self.store.get_book(book_slug)
        if book is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        if book["status"] in {"published", "completed"}:
            raise ServiceError(f"Book '{book_slug}' is already {book['status']}", status_code=409)

        if queue_for_parse:
            if any(c["status"] == "accepted" for c in self.store.list_chapters(book_slug)):
                raise ServiceError(
                    f"Book '{book_slug}' already has accepted chapters",
                    status_code=409,
                )
            self.store.set_book_status(slug=book_slug, status="approved_pending_parse")
            return self.get_book(ctx, book_slug=book_slug)

        should_unlock = bool(unlock_all_chapters and not auto_seed)
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
                public_corpus=_book_is_public_corpus(book),
            )
            if not extracted:
                raise ServiceError(f"Zero chapters extracted for '{book_slug}'", status_code=422)
            extracted = _attach_chapter_section_types(extracted, book.get("proposed_chapters") or None)

            for ch_item in extracted:
                st = "open" if (should_unlock or ch_item["chapter_id"] == "ch01") else "locked_seed"
                self.store.upsert_chapter(
                    book_slug=book_slug,
                    chapter_id=ch_item["chapter_id"],
                    chapter_num=int(ch_item["chapter_num"]),
                    title=ch_item["title"],
                    source_xml=ch_item["source_xml"],
                    status=st,
                    section_type=str(ch_item.get("section_type") or "body"),
                )
        elif should_unlock:
            for ch_item in existing_chapters:
                if ch_item["status"] == "locked_seed":
                    self.store.upsert_chapter(
                        book_slug=book_slug,
                        chapter_id=ch_item["chapter_id"],
                        chapter_num=int(ch_item["chapter_num"]),
                        title=ch_item["title"],
                        source_xml=ch_item["source_xml"],
                        status="open",
                        section_type=str(ch_item.get("section_type") or "body"),
                        opened_at=time.time(),
                    )

        if auto_seed:
            return self.seed_book(
                ctx,
                book_slug=book_slug,
                hold_for_review=hold_for_review,
                background=background,
            )
        if should_unlock and not book.get("glossary_sha"):
            self.store.set_book_brief_and_record(
                slug=book_slug,
                glossary_sha=_compute_glossary_sha(book.get("book_record") or {}),
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
        if book["status"] in {"active", "published", "completed"}:
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
        ch_cache: dict[tuple[str, str], Optional[dict[str, Any]]] = {}
        for s in rows[:capped]:
            item = _sanitize_submission_public(s)
            c_key = (str(s["book_slug"]), str(s["chapter_id"]))
            if c_key not in ch_cache:
                ch_cache[c_key] = self.store.get_chapter(c_key[0], c_key[1])
            item["section_type"] = str((ch_cache[c_key] or {}).get("section_type") or "body")
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
        if action not in {"reject", "approve", "approve_override", "rerun"}:
            raise ServiceError("action must be 'reject', 'approve', 'approve_override', or 'rerun'", status_code=400)
        if ctx.tier != "ADMIN":
            # agent_admin can only approve/reject non-self pending_review submissions
            if action in {"approve_override", "rerun"} or sub["status"] != "pending_review":
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
        if action == "rerun":
            self.store.update_submission(
                submission_id,
                workstream_id="",
                round_number=0,
                status="remediation_required",
            )
            _, sub_payload = self.submit_translation(
                ctx,
                book_slug=sub["book_slug"],
                chapter_id=sub["chapter_id"],
                translation_xml=sub["translation_xml"],
                engine_model=sub["engine_model"],
                additions=sub.get("additions"),
                submission_id=submission_id,
                hold_for_review=(str(book.get("approval_mode") or "auto") != "auto"),
            )
            return _sanitize_submission_public(sub_payload["submission"])
        if sub["workstream_id"]:
            self.review_client.approve_workstream(
                workstream_id=sub["workstream_id"],
                override_escalated=(sub["status"] not in {"pending_review", "pending_admin_approval"}),
            )
        final_txt = sub["final_txt"] or _render_final_txt(sub["translation_xml"])
        updated = self._finalize_accepted_chapter(
            book=book,
            ch=ch,
            submission=sub,
            mark_submission_accepted=True,
            final_txt=final_txt,
        )
        return _sanitize_submission_public(updated)

    def publish_release(self, ctx: AuthContext, *, book_slug: str) -> dict[str, Any]:
        assert_admin(ctx)
        book = self.store.get_book(book_slug)
        if book is None:
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        if not _book_is_public_corpus(book):
            raise ServiceError(
                (
                    f"Book '{book_slug}' uses license '{book['license_id']}' "
                    f"(access_mode='{book.get('access_mode', 'public')}') and cannot be published to the public GitHub repository"
                ),
                status_code=409,
            )
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

    def get_admin_submission_detail(self, ctx: AuthContext, *, submission_id: str) -> dict[str, Any]:
        """Return full submission inspection payload including XML, final text, findings, and peer critiques."""
        assert_admin(ctx)
        sub = self.store.get_submission(submission_id)
        if sub is None:
            raise ServiceError(f"Submission '{submission_id}' not found", status_code=404)
        item = _sanitize_submission_public(sub)
        ch = self.store.get_chapter(str(sub["book_slug"]), str(sub["chapter_id"]))
        book = self.store.get_book(str(sub["book_slug"]))
        item["section_type"] = str((ch or {}).get("section_type") or "body")
        item["chapter_title"] = str((ch or {}).get("title") or sub["chapter_id"])
        item["book_title"] = str((book or {}).get("title") or sub["book_slug"])
        item["peer_reviews"] = self.store.list_peer_reviews(sub["id"])
        return item

    def bulk_manage_submissions(
        self,
        ctx: AuthContext,
        *,
        submission_ids: list[str],
        action: str,
    ) -> dict[str, Any]:
        """Approve or reject up to 50 chapter submissions in one Admin batch."""
        assert_admin(ctx)
        clean_action = (action or "").strip()
        if clean_action not in {"approve", "approve_override", "reject"}:
            raise ServiceError("action must be 'approve', 'approve_override', or 'reject'", status_code=400)
        clean_ids = [str(s or "").strip() for s in (submission_ids or []) if str(s or "").strip()]
        if not clean_ids or len(clean_ids) > 50:
            raise ServiceError("submission_ids must contain 1 to 50 non-empty IDs", status_code=400)
        processed: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for sid in clean_ids:
            try:
                res = self.manage_flagged_submission(ctx, submission_id=sid, action=clean_action)
                processed.append(res)
            except (ServiceError, AuthError) as exc:
                errors.append({"submission_id": sid, "error": str(exc)})
        return {"processed": processed, "errors": errors}

    def export_book_deliverable(
        self,
        ctx: AuthContext,
        *,
        book_slug: str,
        fmt: str,
    ) -> tuple[Path, str, str]:
        """Compile and return (path, media_type, filename) for any completed book behind ADMIN/SSO."""
        assert_admin(ctx)
        clean_fmt = (fmt or "").strip().lower()
        mime_map = {
            "epub": "application/epub+zip",
            "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
        if clean_fmt not in mime_map:
            raise ServiceError("format must be 'epub', 'pdf', or 'docx'", status_code=400)
        book = self.store.get_book(book_slug)
        if book is None or str(book.get("status") or "") == "rejected":
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        chapters = self.store.list_chapters(book_slug)
        if not chapters or any(c["status"] != "accepted" for c in chapters):
            raise ServiceError(
                f"Book '{book_slug}' must have all chapters accepted before exporting",
                status_code=409,
            )
        export_chapters: list[dict[str, Any]] = []
        for ch in chapters:
            final_txt = str(ch.get("final_txt") or "").strip()
            if not final_txt and (ch.get("translation_xml") or "").strip():
                final_txt = _render_final_txt(str(ch["translation_xml"]))
            item = dict(ch)
            item["final_txt"] = final_txt
            export_chapters.append(item)

        out_path = self.git_sync.compile_private_export(
            slug=book_slug,
            title=str(book["title"]),
            author=str(book["author"]),
            book_record=dict(book.get("book_record") or {}),
            chapters=export_chapters,
            fmt=clean_fmt,
        )
        return out_path, mime_map[clean_fmt], f"{book_slug}_vietnamese.{clean_fmt}"

    def submit_bug_report(
        self,
        ctx: AuthContext,
        *,
        title: str,
        body_md: str,
        severity: str = "P1",
        book_slug: str = "",
        chapter_id: str = "",
        submission_id: str = "",
        tool_name: str = "",
        error_code: int = 0,
    ) -> tuple[int, dict[str, Any]]:
        """Validate, scan for prompt-injection, and persist an agent bug report."""
        self._assert_write_identity(ctx)
        self._check_write_rate(ctx)
        clean_title = (title or "").strip()
        if not clean_title or len(clean_title) > 200:
            raise ServiceError("title must be between 1 and 200 characters", status_code=400)
        clean_body = (body_md or "").strip()
        body_bytes = len(clean_body.encode("utf-8"))
        if not clean_body or body_bytes > MAX_BUG_BODY_BYTES:
            raise ServiceError(
                f"body_md must be non-empty and at most {MAX_BUG_BODY_BYTES} bytes",
                status_code=400,
            )
        clean_sev = (severity or "P1").strip().upper()
        if clean_sev not in VALID_BUG_SEVERITIES:
            raise ServiceError("severity must be one of P0, P1, P2, P3", status_code=400)
        try:
            clean_code = int(error_code or 0)
        except (TypeError, ValueError) as exc:
            raise ServiceError("error_code must be an integer >= 0", status_code=400) from exc
        if clean_code < 0:
            raise ServiceError("error_code must be >= 0", status_code=400)

        clean_slug = (book_slug or "").strip()
        clean_ch = (chapter_id or "").strip()
        clean_sub = (submission_id or "").strip()
        clean_tool = (tool_name or "").strip()

        inj_flags = detect_prompt_injection(f"{clean_title}\n{clean_body}")
        if inj_flags:
            quarantined = self.store.create_bug_report(
                title=clean_title,
                severity=clean_sev,
                status="flagged_security",
                reporter_actor_id=ctx.actor_id,
                reporter_handle=ctx.handle,
                reporter_ip_hash=ctx.client_ip_hash,
                book_slug=clean_slug,
                chapter_id=clean_ch,
                submission_id=clean_sub,
                tool_name=clean_tool,
                error_code=clean_code,
                body_md=clean_body,
                security_flags=inj_flags,
            )
            return 202, {
                "bug_report": _sanitize_bug_public(quarantined),
                "quarantined": True,
            }

        eff_ctx, auto_meta = self._maybe_auto_assign_agent(ctx)
        saved = self.store.create_bug_report(
            title=clean_title,
            severity=clean_sev,
            status="open",
            reporter_actor_id=eff_ctx.actor_id,
            reporter_handle=eff_ctx.handle,
            reporter_ip_hash=ctx.client_ip_hash,
            book_slug=clean_slug,
            chapter_id=clean_ch,
            submission_id=clean_sub,
            tool_name=clean_tool,
            error_code=clean_code,
            body_md=clean_body,
            security_flags=[],
        )
        payload: dict[str, Any] = {"bug_report": _sanitize_bug_public(saved)}
        if auto_meta:
            payload.update(auto_meta)
        return 201, payload

    def query_bug_reports(
        self,
        ctx: AuthContext,
        *,
        status: Optional[str] = None,
        book_slug: Optional[str] = None,
        reporter_handle: Optional[str] = None,
        include_body: bool = True,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List non-quarantined bug reports (or all including flagged_security for ADMIN)."""
        self._check_read_rate(ctx)
        clean_status = (status or "").strip().lower() or None
        rows = self.store.list_bug_reports(
            status=clean_status,
            book_slug=(book_slug or "").strip() or None,
            reporter_handle=(reporter_handle or "").strip().lower() or None,
            include_flagged=(ctx.tier == "ADMIN"),
            limit=limit,
        )
        return [_sanitize_bug_public(r, include_body=include_body) for r in rows]

    def get_bug_report(self, ctx: AuthContext, *, bug_id: str) -> dict[str, Any]:
        """Fetch one bug report by ID, hiding flagged_security items from non-ADMIN callers."""
        self._check_read_rate(ctx)
        bug = self.store.get_bug_report(bug_id.strip())
        if bug is None or (bug["status"] == "flagged_security" and ctx.tier != "ADMIN"):
            raise ServiceError(f"Bug report '{bug_id}' not found", status_code=404)
        return _sanitize_bug_public(bug)

    def list_admin_bug_reports(self, ctx: AuthContext, *, limit: int = 100) -> list[dict[str, Any]]:
        """List all bug reports including quarantined items for the Human /admin console."""
        assert_admin(ctx)
        rows = self.store.list_bug_reports(include_flagged=True, limit=limit)
        return [_sanitize_bug_public(r) for r in rows]

    def claim_bug_report(
        self,
        ctx: AuthContext,
        *,
        bug_id: str,
        note: str = "",
    ) -> dict[str, Any]:
        """Claim an open/accepted bug report exclusively for engineering resolution (tech_admin / agent_admin / ADMIN)."""
        assert_bug_handler_or_admin(ctx)
        bug = self.store.get_bug_report(bug_id.strip())
        if bug is None:
            raise ServiceError(f"Bug report '{bug_id}' not found", status_code=404)
        if bug["status"] == "flagged_security":
            assert_admin(ctx)
        if bug["status"] in {"fixed", "declined"}:
            raise ServiceError(
                f"Bug report '{bug_id}' is already terminal ({bug['status']})",
                status_code=409,
            )

        clean_note = (note or "").strip()
        if clean_note and detect_prompt_injection(clean_note):
            raise ServiceError("Claim note blocked by prompt-injection scanner", status_code=422)

        holder = str(bug.get("claimed_by") or "").strip()
        claimed_ts = parse_utc_timestamp(str(bug.get("claimed_at") or ""))
        if (
            bug["status"] == "in_progress"
            and holder
            and holder != ctx.actor_id
            and ctx.tier != "ADMIN"
            and claimed_ts is not None
            and (time.time() - claimed_ts) < self.cfg.claim_ttl_seconds
        ):
            raise ServiceError(
                f"Bug report '{bug_id}' is actively claimed by '{holder}'",
                status_code=409,
            )

        updated = self.store.claim_bug_report(
            bug["id"],
            claimed_by=ctx.actor_id,
            resolution_note=clean_note or None,
        )
        return _sanitize_bug_public(updated)

    def _dispatch_accepted_bug(
        self,
        bug: dict[str, Any],
        *,
        create_github_issue: bool,
    ) -> dict[str, Any]:
        """Run non-blocking webhook and GitHub Issue triggers when a bug is accepted."""
        parts: list[str] = []
        new_issue_url: Optional[str] = None
        want_hook = bool(self.cfg.bug_accept_webhook_url)
        want_gh = bool(
            (self.cfg.bug_auto_github_issue or create_github_issue)
            and self.cfg.github_token
            and not str(bug.get("issue_url") or "").strip()
        )
        if not want_hook and not want_gh:
            return bug

        with httpx.Client(timeout=10.0) as client:
            if want_hook:
                hdrs = {"Content-Type": "application/json"}
                if self.cfg.bug_accept_webhook_secret:
                    hdrs["Authorization"] = f"Bearer {self.cfg.bug_accept_webhook_secret}"
                try:
                    resp = client.post(
                        self.cfg.bug_accept_webhook_url,
                        headers=hdrs,
                        json={
                            "event": "bug.accepted",
                            "hub_url": self.cfg.public_base_url,
                            "bug_report": _sanitize_bug_public(bug),
                        },
                    )
                    if resp.status_code < 400:
                        parts.append("webhook:ok")
                    else:
                        parts.append(f"webhook:error:{resp.status_code}")
                except Exception as exc:
                    parts.append(f"webhook:error:{type(exc).__name__}")

            if want_gh:
                repo = self.cfg.bug_github_repo or "vinhthang/collab-book-translate"
                issue_body = (
                    f"Reported by `{bug.get('reporter_handle') or bug.get('reporter_actor_id')}` "
                    f"(`{bug.get('severity', 'P1')}`)\n\n"
                    f"- **Bug ID**: `{bug['id']}`\n"
                    f"- **Book/Chapter**: `{bug.get('book_slug') or '-'}` / `{bug.get('chapter_id') or '-'}`\n"
                    f"- **Tool / Code**: `{bug.get('tool_name') or '-'}` (`{bug.get('error_code') or 0}`)\n\n"
                    f"### Details\n\n{bug.get('body_md') or ''}\n"
                )
                try:
                    gh_resp = client.post(
                        f"https://api.github.com/repos/{repo}/issues",
                        headers={
                            "Authorization": f"Bearer {self.cfg.github_token}",
                            "Accept": "application/vnd.github+json",
                            "X-GitHub-Api-Version": "2022-11-28",
                        },
                        json={
                            "title": f"[{bug.get('severity', 'P1')}] {bug.get('title', 'Bug report')}",
                            "body": issue_body[:60000],
                            "labels": ["bug", str(bug.get("severity") or "P1")],
                        },
                    )
                    if gh_resp.status_code < 400:
                        html_url = str((gh_resp.json() or {}).get("html_url") or "").strip()
                        if html_url:
                            new_issue_url = html_url
                        parts.append("github_issue:ok")
                    else:
                        parts.append(f"github_issue:error:{gh_resp.status_code}")
                except Exception as exc:
                    parts.append(f"github_issue:error:{type(exc).__name__}")

        if new_issue_url is not None or parts:
            return self.store.update_bug_report(
                bug["id"],
                status=bug["status"],
                triaged_by=str(bug.get("triaged_by") or ""),
                resolution_note=str(bug.get("resolution_note") or ""),
                fixed_in_version=str(bug.get("fixed_in_version") or ""),
                issue_url=new_issue_url,
                dispatch_status="; ".join(parts)[:300],
            )
        return bug

    def triage_bug_report(
        self,
        ctx: AuthContext,
        *,
        bug_id: str,
        status: str,
        resolution_note: str,
        fixed_in_version: str = "",
        pr_url: str = "",
        issue_url: str = "",
        create_github_issue: bool = False,
    ) -> dict[str, Any]:
        """Triage a bug report as accepted, in_progress, fixed, or declined (tech_admin / agent_admin / ADMIN)."""
        assert_bug_handler_or_admin(ctx)
        bug = self.store.get_bug_report(bug_id.strip())
        if bug is None:
            raise ServiceError(f"Bug report '{bug_id}' not found", status_code=404)
        if bug["status"] == "flagged_security":
            assert_admin(ctx)

        clean_status = (status or "").strip().lower()
        if clean_status not in VALID_BUG_STATUSES:
            raise ServiceError("status must be one of: open, accepted, in_progress, fixed, declined", status_code=400)
        clean_note = (resolution_note or "").strip()
        if clean_status in {"fixed", "declined"} and not clean_note:
            raise ServiceError(
                "resolution_note is required when marking a bug fixed or declined",
                status_code=400,
            )
        clean_ver = (fixed_in_version or "").strip()
        if detect_prompt_injection(f"{clean_note}\n{clean_ver}"):
            raise ServiceError("Triage note blocked by prompt-injection scanner", status_code=422)
        clean_pr = _validate_optional_http_url(pr_url, "pr_url")
        clean_issue = _validate_optional_http_url(issue_url, "issue_url")

        updated = self.store.update_bug_report(
            bug["id"],
            status=clean_status,
            triaged_by=ctx.actor_id,
            resolution_note=clean_note,
            fixed_in_version=clean_ver,
            pr_url=clean_pr or None,
            issue_url=clean_issue or None,
        )
        if clean_status == "accepted":
            updated = self._dispatch_accepted_bug(updated, create_github_issue=bool(create_github_issue))
        return _sanitize_bug_public(updated)

    def purge_test_artifacts(self, ctx: AuthContext) -> dict[str, Any]:
        """ADMIN: Delete rejected smoke/e2e test books and orphan test agent rows."""
        assert_admin(ctx)
        purged = self.store.purge_test_artifacts()
        for slug in purged["purged_books"]:
            self.git_sync.cleanup_untracked_book_files(slug=slug)
        return purged

    def get_reader_book_view(
        self,
        ctx: AuthContext,
        *,
        book_slug: str,
        chapter_id: Optional[str] = None,
        admin_view: bool = False,
    ) -> dict[str, Any]:
        """Return structured chapter segments, TOC, and navigation for Public or Admin Web Reader."""
        self._check_read_rate(ctx)
        if admin_view:
            assert_admin(ctx)
        book = self.store.get_book(book_slug)
        if book is None or str(book.get("status") or "") == "rejected":
            raise ServiceError(f"Book '{book_slug}' not found", status_code=404)
        pub_corpus = _book_is_public_corpus(book)
        if not admin_view and not pub_corpus:
            raise ServiceError(f"Book '{book_slug}' is not available on the public reader", status_code=404)

        chapters = sorted(
            self.store.list_chapters(book_slug),
            key=lambda c: (int(c.get("chapter_num") or 0), str(c.get("chapter_id") or "")),
        )
        if not chapters:
            raise ServiceError(f"Book '{book_slug}' has no extracted chapters yet", status_code=404)

        draft_sub_by_ch: dict[str, dict[str, Any]] = {}
        if admin_view:
            for sub in self.store.list_submissions(book_slug=book_slug):
                if sub["status"] in {"pending_review", "pending_admin_approval"} and sub["chapter_id"] not in draft_sub_by_ch:
                    draft_sub_by_ch[str(sub["chapter_id"])] = sub

        toc: list[dict[str, Any]] = []
        for ch in chapters:
            cid = str(ch["chapter_id"])
            has_accepted = ch["status"] == "accepted" and bool((ch.get("translation_xml") or "").strip())
            has_draft = admin_view and cid in draft_sub_by_ch
            toc.append(
                {
                    "chapter_id": cid,
                    "chapter_num": int(ch["chapter_num"]),
                    "title": str(ch["title"]),
                    "section_type": str(ch.get("section_type") or "body"),
                    "status": str(ch["status"]),
                    "has_accepted": has_accepted,
                    "has_draft": has_draft,
                    "readable": has_accepted or has_draft,
                }
            )

        selected_ch: Optional[dict[str, Any]] = None
        if chapter_id:
            clean_cid = chapter_id.strip().lower()
            selected_ch = next((c for c in chapters if str(c["chapter_id"]).lower() == clean_cid), None)
            if selected_ch is None:
                raise ServiceError(f"Chapter '{book_slug}/{chapter_id}' not found", status_code=404)
        else:
            readable_ids = {t["chapter_id"] for t in toc if t["readable"]}
            selected_ch = (
                next((c for c in chapters if str(c["chapter_id"]) in readable_ids and str(c.get("section_type") or "body") == "body"), None)
                or next((c for c in chapters if str(c["chapter_id"]) in readable_ids), None)
                or next((c for c in chapters if str(c.get("section_type") or "body") == "body"), None)
                or chapters[0]
            )

        sel_id = str(selected_ch["chapter_id"])
        sel_idx = next(i for i, c in enumerate(chapters) if str(c["chapter_id"]) == sel_id)
        prev_ch = toc[sel_idx - 1] if sel_idx > 0 else None
        next_ch = toc[sel_idx + 1] if (sel_idx + 1) < len(toc) else None

        content_source = "pending"
        xml_to_render = ""
        draft_meta: dict[str, Any] = {}
        if selected_ch["status"] == "accepted" and (selected_ch.get("translation_xml") or "").strip():
            content_source = "accepted"
            xml_to_render = str(selected_ch["translation_xml"])
        elif admin_view and sel_id in draft_sub_by_ch:
            d_sub = draft_sub_by_ch[sel_id]
            content_source = "draft"
            xml_to_render = str(d_sub.get("translation_xml") or "")
            draft_meta = {
                "submission_id": str(d_sub["id"]),
                "status": str(d_sub["status"]),
                "author_handle": str(d_sub.get("author_handle") or ""),
                "engine_model": str(d_sub.get("engine_model") or ""),
            }

        segments = parse_segments(xml_to_render) if xml_to_render else []
        return {
            "book": {
                "slug": str(book["slug"]),
                "title": str(book["title"]),
                "author": str(book["author"]),
                "source_lang": str(book["source_lang"]),
                "target_lang": str(book["target_lang"]),
                "status": str(book["status"]),
                "license": str(book["license_id"]),
                "public_corpus": pub_corpus,
                "release_tag": str(book.get("release_tag") or ""),
                "release_url": str(book.get("release_url") or ""),
            },
            "toc": toc,
            "active_chapter": {
                "chapter_id": sel_id,
                "chapter_num": int(selected_ch["chapter_num"]),
                "title": str(selected_ch["title"]),
                "section_type": str(selected_ch.get("section_type") or "body"),
                "status": str(selected_ch["status"]),
                "content_source": content_source,
                "draft": draft_meta,
                "segments": segments,
            },
            "prev_chapter": prev_ch,
            "next_chapter": next_ch,
            "admin_view": admin_view,
        }

    def list_activity_threads(
        self,
        ctx: AuthContext,
        *,
        book_slug: Optional[str] = None,
        kind: str = "all",
        admin_view: bool = False,
        limit: int = 80,
    ) -> dict[str, Any]:
        """List read-only review & bug activity threads filtered by public corpus vs admin SSO."""
        self._check_read_rate(ctx)
        if admin_view:
            assert_admin(ctx)
        clean_kind = (kind or "all").strip().lower()
        if clean_kind not in {"all", "reviews", "bugs"}:
            clean_kind = "all"
        clean_slug = (book_slug or "").strip().lower()
        capped = max(1, min(int(limit), 200))

        all_books = self.store.list_books(include_pending=admin_view)
        visible_books = [
            b
            for b in all_books
            if str(b.get("status") or "") != "rejected"
            and (admin_view or _book_is_public_corpus(b))
        ]
        book_map = {str(b["slug"]): b for b in visible_books}
        visible_slugs = set(book_map.keys())
        if clean_slug and clean_slug not in visible_slugs:
            if admin_view:
                raise ServiceError(f"Book '{clean_slug}' not found", status_code=404)
            clean_slug = ""

        items: list[dict[str, Any]] = []
        if clean_kind in {"all", "reviews"}:
            revs = self.store.list_recent_peer_reviews(
                book_slug=clean_slug or None,
                limit=capped,
            )
            for r in revs:
                b_slug = str(r.get("book_slug") or "")
                if b_slug not in visible_slugs:
                    continue
                bk = book_map[b_slug]
                items.append(
                    {
                        "thread_type": "review",
                        "id": str(r["id"]),
                        "created_at": str(r.get("created_at") or ""),
                        "book_slug": b_slug,
                        "book_title": str(bk["title"]),
                        "chapter_id": str(r.get("chapter_id") or ""),
                        "submission_id": str(r.get("submission_id") or ""),
                        "submission_status": str(r.get("submission_status") or ""),
                        "author_handle": str(r.get("author_handle") or ""),
                        "actor_handle": str(r.get("reviewer_handle") or ""),
                        "verdict": str(r.get("verdict") or "COMMENT"),
                        "weighted_score": float(r.get("weighted_score") or 0.0),
                        "scores": r.get("scores") or {},
                        "body_md": str(r.get("body_md") or ""),
                    }
                )

        if clean_kind in {"all", "bugs"}:
            bugs = self.store.list_bug_reports(
                book_slug=clean_slug or None,
                include_flagged=admin_view,
                limit=capped,
            )
            for bg in bugs:
                b_slug = str(bg.get("book_slug") or "")
                if not admin_view and b_slug and b_slug not in visible_slugs:
                    continue
                bk = book_map.get(b_slug)
                items.append(
                    {
                        "thread_type": "bug",
                        "id": str(bg["id"]),
                        "created_at": str(bg.get("created_at") or ""),
                        "book_slug": b_slug,
                        "book_title": str(bk["title"]) if bk else (b_slug or "Global Hub"),
                        "chapter_id": str(bg.get("chapter_id") or ""),
                        "title": str(bg.get("title") or ""),
                        "severity": str(bg.get("severity") or "P1"),
                        "status": str(bg.get("status") or "open"),
                        "actor_handle": str(bg.get("reporter_handle") or ""),
                        "resolution_note": str(bg.get("resolution_note") or ""),
                        "fixed_in_version": str(bg.get("fixed_in_version") or ""),
                        "body_md": str(bg.get("body_md") or ""),
                    }
                )

        items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        return {
            "items": items[:capped],
            "books": [
                {"slug": str(b["slug"]), "title": str(b["title"]), "public_corpus": _book_is_public_corpus(b)}
                for b in visible_books
            ],
            "selected_book": clean_slug,
            "kind": clean_kind,
            "admin_view": admin_view,
        }
