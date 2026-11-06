"""
scripts/utils/schemas.py — Canonical Data Contracts & Schema Validation
Implements REQ-ARCH-1 (AC-ARCH-1.1..1.4):
- Canonical checklist.json normalization and validation
- Canonical book_record.json normalization and validation
- Canonical round<R>_evaluate.txt validation
- Terminal warning state normalization
"""

from typing import Any, Dict, List, Optional, Union

TECHNICAL_SEED_GLOSSARY_V1: List[Dict[str, Any]] = [
    {"source": "shard", "target": "shard", "verbatim": True, "match": "word", "case_sensitive": False},
    {"source": "replica", "target": "replica", "verbatim": True, "match": "word", "case_sensitive": False},
    {"source": "pipeline", "target": "pipeline", "verbatim": True, "match": "word", "case_sensitive": False},
    {"source": "leader-follower", "target": "leader-follower", "verbatim": True, "match": "word", "case_sensitive": False},
    {"source": "commit log", "target": "commit log", "verbatim": True, "match": "phrase", "case_sensitive": False},
    {"source": "quorum", "target": "quorum", "verbatim": True, "match": "word", "case_sensitive": False},
    {"source": "throughput", "target": "thông lượng (throughput)", "verbatim": True, "match": "word", "case_sensitive": False},
    {"source": "latency", "target": "độ trễ (latency)", "verbatim": True, "match": "word", "case_sensitive": False},
    {"source": "idempotence", "target": "tính lũy đẳng (idempotence)", "verbatim": True, "match": "word", "case_sensitive": False},
    {"source": "backpressure", "target": "áp lực ngược (backpressure)", "verbatim": True, "match": "word", "case_sensitive": False},
    {"source": "consensus", "target": "đồng thuận (consensus)", "verbatim": True, "match": "word", "case_sensitive": False},
    {"source": "linearizability", "target": "tính tuần tự hóa tuyến tính (linearizability)", "verbatim": True, "match": "word", "case_sensitive": False},
    {"source": "serializability", "target": "tính khả tuần tự (serializability)", "verbatim": True, "match": "word", "case_sensitive": False},
    {"source": "eventual consistency", "target": "tính nhất quán cuối cùng (eventual consistency)", "verbatim": True, "match": "phrase", "case_sensitive": False},
    {"source": "write-ahead log", "target": "nhật ký ghi trước (write-ahead log)", "verbatim": True, "match": "phrase", "case_sensitive": False},
    {"source": "partition", "target": "phân vùng (partition)", "verbatim": True, "match": "word", "case_sensitive": False},
]


def normalize_warning_state(state: str) -> str:
    """
    Normalizes terminal chapter warning states (AC-ARCH-1.3).
    Converts 'released_with_warning' and 'released-with-warning' to canonical 'released-with-warning'.
    """
    if not isinstance(state, str):
        return str(state)
    s = state.strip().lower()
    if s in ("released_with_warning", "released-with-warning"):
        return "released-with-warning"
    return state


def normalize_book_record(
    data: Dict[str, Any],
    book_slug: str = "unknown",
) -> Dict[str, Any]:
    """
    Normalizes book_record data to canonical spec_version: '1.1' (AC-ARCH-1.1).
    Preserves top-level book_slug, source_lang, target_lang, brief, verbatim_terms,
    corrections, and book_record (name_table, term_table, pronoun_table arrays).
    Preserves/populates legacy top-level convenience keys (names, terms, pronouns).
    """
    if not isinstance(data, dict):
        data = {}

    normalized: Dict[str, Any] = {
        "spec_version": "1.1",
        "book_slug": data.get("book_slug", book_slug),
        "source_lang": data.get("source_lang", "en"),
        "target_lang": data.get("target_lang", "vi"),
        "brief": data.get("brief", {}),
        "verbatim_terms": list(data.get("verbatim_terms", [])),
        "corrections": list(data.get("corrections", [])),
    }

    # Preserve optional metadata if present
    for extra_key in ("title", "author", "translator", "publisher", "year", "genre", "language", "domain_lexicon"):
        if extra_key in data:
            normalized[extra_key] = data[extra_key]

    raw_rec = data.get("book_record")
    if not isinstance(raw_rec, dict):
        raw_rec = data

    # 1. Normalize name_table / names
    raw_names = raw_rec.get("name_table") or raw_rec.get("names") or data.get("name_table") or data.get("names") or []
    norm_names: List[Dict[str, Any]] = []
    if isinstance(raw_names, list):
        for item in raw_names:
            if isinstance(item, dict) and "source" in item:
                entry = {
                    "source": item["source"],
                    "target": item.get("target", ""),
                }
                if "aliases" in item:
                    entry["aliases"] = list(item["aliases"])
                if "also_referred_to_as" in item:
                    entry["also_referred_to_as"] = list(item["also_referred_to_as"])
                if "variants" in item:
                    entry["variants"] = list(item["variants"])
                if "notes" in item:
                    entry["notes"] = str(item["notes"])
                norm_names.append(entry)
            elif isinstance(item, str):
                norm_names.append({"source": item, "target": item})
    elif isinstance(raw_names, dict):
        for k, v in raw_names.items():
            norm_names.append({"source": str(k), "target": str(v)})

    # 2. Normalize term_table / terms
    raw_terms = raw_rec.get("term_table") or raw_rec.get("terms") or data.get("term_table") or data.get("terms") or []
    norm_terms: List[Dict[str, Any]] = []
    if isinstance(raw_terms, list):
        for item in raw_terms:
            if isinstance(item, dict) and "source" in item:
                entry = {
                    "source": item["source"],
                    "target": item.get("target", ""),
                }
                if "variants" in item:
                    entry["variants"] = list(item["variants"])
                if "match" in item:
                    entry["match"] = item["match"]
                if "case_sensitive" in item:
                    entry["case_sensitive"] = item["case_sensitive"]
                if "verbatim" in item:
                    entry["verbatim"] = bool(item["verbatim"])
                norm_terms.append(entry)
            elif isinstance(item, str):
                norm_terms.append({"source": item, "target": item})
    elif isinstance(raw_terms, dict):
        for k, v in raw_terms.items():
            norm_terms.append({"source": str(k), "target": str(v)})

    # 3. Normalize pronoun_table / pronouns
    raw_pronouns = raw_rec.get("pronoun_table") or raw_rec.get("pronouns") or data.get("pronoun_table") or data.get("pronouns") or []
    norm_pronouns: List[Dict[str, Any]] = []
    if isinstance(raw_pronouns, list):
        for item in raw_pronouns:
            if isinstance(item, dict):
                src = item.get("source") or ("_to_".join(item["pair"]) if "pair" in item and len(item["pair"]) == 2 else "")
                tgt = item.get("target") or (f"{item.get('a_to_b', '')}_{item.get('b_to_a', '')}" if "a_to_b" in item else "")
                entry = {
                    "source": src,
                    "target": tgt,
                }
                if "pair" in item:
                    entry["pair"] = list(item["pair"])
                if "a_to_b" in item:
                    entry["a_to_b"] = str(item["a_to_b"])
                if "b_to_a" in item:
                    entry["b_to_a"] = str(item["b_to_a"])
                if "basis" in item:
                    entry["basis"] = str(item["basis"])
                if "changes" in item:
                    entry["changes"] = list(item["changes"])
                if "provisional" in item:
                    entry["provisional"] = bool(item["provisional"])
                if "participants" in item:
                    entry["participants"] = list(item["participants"])
                norm_pronouns.append(entry)
            elif isinstance(item, str):
                norm_pronouns.append({"source": item, "target": item})
    elif isinstance(raw_pronouns, dict):
        for k, v in raw_pronouns.items():
            norm_pronouns.append({"source": str(k), "target": str(v)})

    # Canonical nested book_record
    normalized["book_record"] = {
        "name_table": norm_names,
        "term_table": norm_terms,
        "pronoun_table": norm_pronouns,
        # Legacy nested access compatibility
        "names": norm_names,
        "terms": norm_terms,
        "pronouns": norm_pronouns,
    }

    # Top-level legacy convenience keys
    normalized["name_table"] = norm_names
    normalized["term_table"] = norm_terms
    normalized["pronoun_table"] = norm_pronouns
    normalized["names"] = norm_names
    normalized["terms"] = norm_terms
    normalized["pronouns"] = norm_pronouns

    return normalized


def validate_book_record(
    data: Dict[str, Any],
    book_slug: str = "unknown",
) -> Dict[str, Any]:
    """Validates book_record against canonical spec_version: '1.1'."""
    normalized = normalize_book_record(data, book_slug=book_slug)
    if normalized.get("spec_version") != "1.1":
        raise ValueError("Invalid book_record: spec_version must be '1.1'")
    if not isinstance(normalized.get("book_record"), dict):
        raise ValueError("Invalid book_record: missing nested book_record section")
    return normalized


def normalize_checklist(
    data: Union[List[Dict[str, Any]], Dict[str, Any]],
    book_slug: str = "unknown",
    source_lang: str = "en",
    target_lang: str = "vi",
) -> Dict[str, Any]:
    """
    Normalizes flat lists or legacy dictionaries to canonical checklist structure (AC-ARCH-1.2, 1.3):
    {
      "spec_version": "1.1",
      "book_slug": str,
      "source_lang": str,
      "target_lang": str,
      "chapters": {
        "ch00": { ... },
        "ch01": { ... }
      }
    }
    Preserves ch00 (preface/intro) and normalizes warning states.
    """
    normalized: Dict[str, Any] = {
        "spec_version": "1.1",
        "book_slug": book_slug,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "chapters": {},
    }

    if isinstance(data, dict):
        normalized["book_slug"] = data.get("book_slug", book_slug)
        normalized["source_lang"] = data.get("source_lang", source_lang)
        normalized["target_lang"] = data.get("target_lang", target_lang)

        # Check if already enveloped with 'chapters'
        raw_chapters = data.get("chapters")
        if isinstance(raw_chapters, dict):
            for ch_id, ch_entry in raw_chapters.items():
                if isinstance(ch_entry, dict):
                    entry = dict(ch_entry)
                    entry["chapter"] = entry.get("chapter", ch_id)
                    entry["status"] = normalize_warning_state(entry.get("status", "pending"))
                    entry["current_round"] = entry.get("current_round", entry.get("round", 1))
                    entry.setdefault("score", None)
                    entry.setdefault("spread", 0.0)
                    entry.setdefault("history", [])
                    normalized["chapters"][ch_id] = entry
            return normalized

        # Or dictionary keyed by chapter IDs directly
        for k, v in data.items():
            if k in ("spec_version", "book_slug", "source_lang", "target_lang"):
                continue
            if isinstance(v, dict):
                ch_id = k
                entry = dict(v)
                entry["chapter"] = entry.get("chapter", ch_id)
                entry["status"] = normalize_warning_state(entry.get("status", "pending"))
                entry["current_round"] = entry.get("current_round", entry.get("round", 1))
                entry.setdefault("score", None)
                entry.setdefault("spread", 0.0)
                entry.setdefault("history", [])
                normalized["chapters"][ch_id] = entry
        return normalized

    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            raw_ch = item.get("chapter", "")
            if isinstance(raw_ch, int):
                ch_id = f"ch{raw_ch:02d}"
            elif isinstance(raw_ch, str) and raw_ch:
                ch_id = raw_ch if raw_ch.startswith("ch") else f"ch{raw_ch}"
            else:
                ch_id = f"ch{len(normalized['chapters']):02d}"

            entry = dict(item)
            entry["chapter"] = ch_id
            entry["status"] = normalize_warning_state(entry.get("status", "pending"))
            entry["current_round"] = entry.get("current_round", entry.get("round", 1))
            entry.setdefault("score", None)
            entry.setdefault("spread", 0.0)
            entry.setdefault("history", [])
            normalized["chapters"][ch_id] = entry

    return normalized


def validate_checklist(
    data: Union[List[Dict[str, Any]], Dict[str, Any]],
    book_slug: str = "unknown",
    source_lang: str = "en",
    target_lang: str = "vi",
) -> Dict[str, Any]:
    """Validates checklist data against canonical spec_version: '1.1'."""
    normalized = normalize_checklist(data, book_slug=book_slug, source_lang=source_lang, target_lang=target_lang)
    if normalized.get("spec_version") != "1.1":
        raise ValueError("Invalid checklist: spec_version must be '1.1'")
    if not isinstance(normalized.get("chapters"), dict):
        raise ValueError("Invalid checklist: chapters must be a dictionary")
    return normalized


def validate_round_evaluation(
    data: Dict[str, Any],
    chapter: str = "ch01",
    round_num: int = 1,
    evaluator_id: str = "workbuddy",
) -> Dict[str, Any]:
    """
    Validates evaluation outputs (round<R>_evaluate.txt) against canonical schema (AC-ARCH-1.4).
    Ensures spec_version: '1.1', evaluator_id, chapter, round, target_syllable_count,
    llm_dimensions, and issues[] (with id, segment_id, dimension, severity, span_source,
    span_target, problem, suggestion).
    """
    if not isinstance(data, dict):
        data = {}

    validated: Dict[str, Any] = {
        "spec_version": "1.1",
        "evaluator_id": str(data.get("evaluator_id", evaluator_id)),
        "chapter": str(data.get("chapter", chapter)),
        "round": int(data.get("round", data.get("round_num", round_num))),
        "target_syllable_count": int(data.get("target_syllable_count", data.get("syllable_count", 0))),
    }

    # Normalize llm_dimensions
    raw_dims = data.get("llm_dimensions") or data.get("dimensions") or data.get("scores") or {}
    norm_dims: Dict[str, float] = {}
    standard_dims = ("fidelity", "fluency", "terminology", "register", "structure", "completeness")
    if isinstance(raw_dims, dict):
        for d in standard_dims:
            if d in raw_dims:
                try:
                    norm_dims[d] = float(raw_dims[d])
                except (ValueError, TypeError):
                    norm_dims[d] = 5.0
            else:
                norm_dims[d] = 5.0
    else:
        norm_dims = {d: 5.0 for d in standard_dims}
    validated["llm_dimensions"] = norm_dims

    # Normalize issues list
    raw_issues = data.get("issues", [])
    norm_issues: List[Dict[str, Any]] = []
    if isinstance(raw_issues, list):
        for idx, iss in enumerate(raw_issues):
            if not isinstance(iss, dict):
                continue
            item = dict(iss)
            item["id"] = str(item.get("id", f"ISSUE-{idx+1:03d}"))
            item["segment_id"] = str(item.get("segment_id", ""))
            item["dimension"] = str(item.get("dimension", "fidelity")).lower()
            item["severity"] = str(item.get("severity", "minor")).lower()
            item["span_source"] = str(item.get("span_source", ""))
            item["span_target"] = str(item.get("span_target", ""))
            item["problem"] = str(item.get("problem", ""))
            item["suggestion"] = str(item.get("suggestion", ""))
            norm_issues.append(item)
    validated["issues"] = norm_issues

    # Retain optional calculated metrics if already present
    for k in ("issue_derived_total", "weighted_total", "has_critical", "verdict", "needs_redraft"):
        if k in data:
            validated[k] = data[k]

    return validated
