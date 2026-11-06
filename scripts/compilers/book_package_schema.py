#!/usr/bin/env python3
"""
scripts/compilers/book_package_schema.py — Canonical BookPackage v1.0 Schema,
normalization helpers, quote/rich-block classifiers, and schema validation.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SCHEMA_VERSION: str = "1.0"

VALID_THEMES: Set[str] = {
    "classical-parchment",
    "cosmic-slate",
    "editorial-crisp",
    "literary-warm",
}

VALID_ORNAMENTS: Set[str] = {
    "classical-seal",
    "cosmic-constellation",
    "minimal-grid",
    "flourish",
}

VALID_GENRE_KEYS: Set[str] = {
    "historical-chronicle",
    "sci-fi",
    "technical",
    "general",
}

VALID_SEG_TYPES: Set[str] = {
    "heading",
    "paragraph",
    "blockquote",
    "code",
    "table",
    "figure",
}

VALID_BLOCK_KINDS: Set[str] = {
    "heading",
    "paragraph",
    "chronicle_citation",
    "epigraph",
    "principle_card",
    "technical_callout",
    "in_universe_doc",
    "blockquote",
    "code",
    "table",
    "figure",
    "divider",
}

VALID_SPAN_TYPES: Set[str] = {
    "text",
    "dialogue",
    "footnote_ref",
    "term_ref",
    "code_inline",
    "emphasis",
    "strong",
}

VALID_GLOSSARY_CATEGORIES: Set[str] = {
    "name",
    "term",
    "verbatim",
}

VALID_FEEDBACK_CATEGORIES: Set[str] = {
    "phrasing",
    "terminology",
    "fidelity",
    "typo",
}

VALID_FEEDBACK_STATUSES: Set[str] = {
    "open",
    "approved",
    "applied",
    "rejected",
}

DEFAULT_BLOCK_STYLES: Dict[str, Dict[str, Any]] = {
    "chronicle_citation": {
        "font_token": "serif-chronicle",
        "accent_style": "chronicle-border",
        "badge_label": "Sử liệu",
    },
    "epigraph": {
        "font_token": "display-epigraph",
        "accent_style": "pull-quote",
        "badge_label": None,
    },
    "principle_card": {
        "font_token": "sans-callout",
        "accent_style": "maxim-highlight",
        "badge_label": "Nguyên tắc",
    },
    "technical_callout": {
        "font_token": "sans-callout",
        "accent_style": "callout-card",
        "badge_label": "Thuật ngữ",
    },
    "in_universe_doc": {
        "font_token": "mono-code",
        "accent_style": "terminal-log",
        "badge_label": "Tài liệu",
    },
    "dialogue": {
        "font_token": "serif-dialogue",
        "accent_style": "subtle-italic",
        "badge_label": None,
    },
    "blockquote": {
        "font_token": "serif-body",
        "accent_style": "pull-quote",
        "badge_label": None,
    },
}

UPPER_VI: str = "A-ZÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚÝĂĐĨŨƠƯẠẢẤẦẨẪẬẮẰẲẴẶẸẺẼẾỀỂỄỆỈỊỌỎỐỒỔỖỘỚỜỞỠỢỤỦỨỪỬỮỰỲỴỶỸ"
LOWER_VI: str = "a-zàáâãèéêìíòóôõùúýăđĩũơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ"

COMMENTARY_SOURCES = (
    "Ngụy thư|Ngụy lược|Điển lược|Anh hùng ký|Tương Dương ký|Hán kỷ|Hán Tấn xuân thu|"
    "Sơn Dương công tải ký|Giang Biểu truyện|Ngô thư|Ngô lịch|Ngô lục|Hoa Dương quốc chí|"
    "Phó tử|Thế ngữ|Tấn thư|Tục Hán thư|Hậu Hán thư|Văn sĩ truyện|Ích Bộ kỳ cựu truyện|"
    "Chiến lược|Hiến Đế xuân thu|Hiến Đế khởi cư chú|Hiến Đế truyện|Tam phụ quyết lục|"
    "Bác vật chí|Dị vật chí|Sưu thần ký|Bão Phác Tử|Tào Man truyện|Cửu Châu xuân thu|"
    "Cửu Châu Xuân Thu|Dị đồng tạp ngữ|Ngụy thị xuân thu|Tạp ký|Ngụy mạt truyện|Tấn dương thu|Chí lâm|"
    "Linh Lăng tiên hiền truyện|Tương Dương kỳ cựu ký"
)

MID_PARAGRAPH_COMMENTARY_RE = re.compile(
    r'(?:'
    r'(?:\((?:[' + UPPER_VI + r'][\w\s]{1,35}?(?:\(\d+\)|\[\^\d+\])?)\s+(?:trong\s+[' + UPPER_VI + r'][\w\s]{1,30}?\s+)?(?:chép rằng|chép|nói rằng|nói|ghi|bàn|xét)\):?)|'
    # Compound commentary header e.g. Người nước Ngô khi làm Tào Man truyện và Quách Ban trong Thế ngữ đều nói:
    r'(?:Người\s+[^\n:]{1,25}\s+khi làm\s+(?:' + COMMENTARY_SOURCES + r')(?:\(\d+\)|\[\^\d+\])?\s+và\s+[^\n:]{1,35}\s+trong\s+(?:' + COMMENTARY_SOURCES + r')(?:\(\d+\)|\[\^\d+\])?\s+đều\s+nói\s*:?)|'
    r'(?:[' + UPPER_VI + r'][' + LOWER_VI + r']+(?:\s+[' + UPPER_VI + r'][' + LOWER_VI + r']+){1,3}\s+trong\s+(?:' + COMMENTARY_SOURCES + r')(?:\(\d+\)|\[\^\d+\])?\s+(?:nói|chép|viết|rằng|dẫn|xét)\s*:?)|'
    r'(?:(?:' + COMMENTARY_SOURCES + r')(?:\s*,\s*|\s+và\s+(?:' + COMMENTARY_SOURCES + r'))*(?:\(\d+\)|\[\^\d+\])?\s+(?:đều\s+|cùng\s+|lại\s+|có\s+)?(?:chép rằng|chép|nói rằng|nói|viết|ghi|rằng|dẫn|tải|bàn|xét|cho rằng|chê|kể)\s*:?)|'
    r'(?:(?:(?:Thần|Bùi|Bề tôi)\s+Tùng Chi|(?<!Thần\s)(?<!Bùi\s)(?<!Bề tôi\s)Tùng Chi)\s*(?:xét|cho rằng|nói|bàn rằng|nghĩ rằng|cẩn án|án|tâu rằng)\s*:?)|'
    r'(?:Xét sách\s+[^\n:]{1,30}:)'
    r')'
)

CHRONICLE_ATTRIBUTION_RE = re.compile(
    r'^\s*' + MID_PARAGRAPH_COMMENTARY_RE.pattern
)

TECHNICAL_CALLOUT_RE = re.compile(
    r'^(?:(?:Thuật ngữ|Định nghĩa|Hộp khái niệm|Ghi chú kỹ thuật)\s*:\s*)(.+?)(?:[\n.]|$)',
    re.IGNORECASE
)

PRINCIPLE_CARD_RE = re.compile(
    r'^(?:(\d+\.\d+(?:\.\d+)?)|(?:Nguyên tắc\s+(\d+(?:\.\d+)?))[:.]?)\s+',
    re.IGNORECASE
)


def build_book_profile(
    book_slug: str,
    source_lang: str,
    genre_key: str,
    profile_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Builds the canonical BookProfile structure."""
    override = profile_override or {}

    # 1. Theme preset
    if "theme_preset" in override and override["theme_preset"] in VALID_THEMES:
        theme = override["theme_preset"]
    elif genre_key == "historical-chronicle" or "tam_quoc_chi" in book_slug:
        theme = "classical-parchment"
    elif genre_key == "sci-fi":
        theme = "cosmic-slate"
    elif genre_key == "technical":
        theme = "editorial-crisp"
    else:
        theme = "literary-warm"

    # 2. Bilingual layers
    if "bilingual_layers" in override:
        layers = override["bilingual_layers"]
    elif source_lang == "zh":
        layers = ["original", "hanviet", "translate"]
    else:
        layers = ["original", "translate"]

    # 3. Ornament style
    if "ornament_style" in override and override["ornament_style"] in VALID_ORNAMENTS:
        ornament = override["ornament_style"]
    elif genre_key == "historical-chronicle":
        ornament = "classical-seal"
    elif genre_key == "sci-fi":
        ornament = "cosmic-constellation"
    elif genre_key == "technical":
        ornament = "minimal-grid"
    else:
        ornament = "flourish"

    # 4. TOC Groups
    if "toc_groups" in override:
        raw_groups = override["toc_groups"]
        toc_groups = []
        for g in raw_groups:
            if isinstance(g, dict):
                g_copy = dict(g)
                if "chapter_range" in g_copy and "chapter_ids" not in g_copy:
                    cr = g_copy["chapter_range"]
                    if isinstance(cr, (list, tuple)) and len(cr) == 2:
                        start, end = int(cr[0]), int(cr[1])
                        g_copy["chapter_ids"] = [f"ch{i:02d}" for i in range(start, end + 1)]
                toc_groups.append(g_copy)
            else:
                toc_groups.append(g)
    else:
        toc_groups = []

    # 5. Block styles
    block_styles = dict(DEFAULT_BLOCK_STYLES)
    if "block_styles" in override and isinstance(override["block_styles"], dict):
        block_styles.update(override["block_styles"])

    return {
        "theme_preset": theme,
        "bilingual_layers": layers,
        "has_drop_caps": bool(override.get("has_drop_caps", True)),
        "ornament_style": ornament,
        "toc_groups": toc_groups,
        "block_styles": block_styles,
    }


def normalize_glossary(
    book_record: Dict[str, Any],
    book_slug: str,
) -> Dict[str, Any]:
    """
    Normalizes glossary entries and pronouns from book_record.json
    into the canonical BookPackage v1.0 Glossary format.
    """
    entries: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    counter = 1

    def _add_entry(category: str, source: str, target: str, aliases: Optional[List[str]] = None, variants: Optional[List[str]] = None, notes: str = ""):
        nonlocal counter
        src_clean = str(source).strip()
        tgt_clean = str(target).strip() if target else src_clean
        if not src_clean:
            return

        entry_id = f"{category}_{counter:04d}"
        counter += 1

        entries.append({
            "id": entry_id,
            "category": category,
            "source": src_clean,
            "target": tgt_clean,
            "aliases": aliases or [],
            "variants": variants or [],
            "notes": str(notes).strip() if notes else "",
        })

    # 1. Rich entries in book_record or book_record.book_record
    containers = [book_record]
    if "book_record" in book_record and isinstance(book_record["book_record"], dict):
        containers.append(book_record["book_record"])

    for c in containers:
        for it in c.get("names", []):
            if isinstance(it, dict) and "source" in it:
                aliases = it.get("aliases") or it.get("also_referred_to_as") or []
                _add_entry("name", it["source"], it.get("target", it["source"]), aliases=aliases, notes=it.get("notes", ""))
        for it in c.get("terms", []):
            if isinstance(it, dict) and "source" in it:
                aliases = it.get("aliases") or it.get("also_referred_to_as") or []
                _add_entry("term", it["source"], it.get("target", it["source"]), aliases=aliases, notes=it.get("notes", ""))

    # 2. Table entries (name_table, term_table, verbatim_terms)
    for c in containers:
        name_tab = c.get("name_table", {})
        if isinstance(name_tab, dict):
            for k, v in name_tab.items():
                if not any(e["source"] == k for e in entries):
                    _add_entry("name", k, v)
        elif isinstance(name_tab, list):
            for it in name_tab:
                if isinstance(it, dict) and "source" in it:
                    if not any(e["source"] == it["source"] for e in entries):
                        _add_entry("name", it["source"], it.get("target", it["source"]))

        term_tab = c.get("term_table", {})
        if isinstance(term_tab, dict):
            for k, v in term_tab.items():
                if not any(e["source"] == k for e in entries):
                    _add_entry("term", k, v)
        elif isinstance(term_tab, list):
            for it in term_tab:
                if isinstance(it, dict) and "source" in it:
                    if not any(e["source"] == it["source"] for e in entries):
                        _add_entry("term", it["source"], it.get("target", it["source"]))

        for v in c.get("verbatim_terms", []):
            if isinstance(v, str) and not any(e["source"] == v for e in entries):
                _add_entry("verbatim", v, v)

    # 3. Pronoun rules
    pronoun_rules: List[Dict[str, Any]] = []
    raw_pronouns = []
    for c in containers:
        if "pronouns" in c and isinstance(c["pronouns"], list):
            raw_pronouns.extend(c["pronouns"])

    seen_pairs: Set[str] = set()
    for p in raw_pronouns:
        if isinstance(p, dict) and "pair" in p and len(p["pair"]) == 2:
            key = f"{p['pair'][0]}::{p['pair'][1]}"
            if key not in seen_pairs:
                seen_pairs.add(key)
                pronoun_rules.append({
                    "pair": [str(p["pair"][0]), str(p["pair"][1])],
                    "a_to_b": str(p.get("a_to_b", "")),
                    "b_to_a": str(p.get("b_to_a", "")),
                    "basis": str(p.get("basis", "")),
                })

    return {
        "schema_version": SCHEMA_VERSION,
        "book_slug": book_slug,
        "entries": entries,
        "pronouns": pronoun_rules,
    }


def normalize_book_metadata_and_glossary(
    run_dir: Path,
    profile_override: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Normalizes metadata and glossary across all run variations into canonical structures.
    Returns (metadata_dict, glossary_dict).
    """
    run_dir = Path(run_dir).resolve()
    book_slug = run_dir.name

    book_record: Dict[str, Any] = {}
    br_path = run_dir / "book_record.json"
    if br_path.is_file():
        try:
            with open(br_path, "r", encoding="utf-8") as f:
                book_record = json.load(f)
        except Exception:
            book_record = {}

    if "book_slug" in book_record:
        book_slug = book_record["book_slug"]

    # Read optional web_profile override if not explicitly provided
    if profile_override is None:
        web_profile_path = run_dir / "web_profile.json"
        if web_profile_path.is_file():
            try:
                with open(web_profile_path, "r", encoding="utf-8") as f:
                    profile_override = json.load(f)
            except Exception:
                pass

    br_nested = book_record.get("book_record", {})
    brief = book_record.get("brief", {})
    if not brief and isinstance(br_nested, dict):
        brief = br_nested.get("brief", {})

    title = (
        book_record.get("title_vietnamese")
        or book_record.get("title")
        or (br_nested.get("title") if isinstance(br_nested, dict) else None)
        or f"{book_slug.replace('_', ' ').title()}"
    )
    title_original = (
        book_record.get("title_original")
        or book_record.get("title")
    )
    author = (
        book_record.get("author")
        or (br_nested.get("author") if isinstance(br_nested, dict) else None)
        or "Tác giả"
    )

    source_lang = book_record.get("source_lang")
    if not source_lang:
        if "tam_quoc_chi" in book_slug or any("\u4e00" <= c <= "\u9fff" for c in title):
            source_lang = "zh"
        else:
            source_lang = "en"

    target_lang = "vi"

    raw_genre = brief.get("genre", "general").lower()
    if "chronicle" in raw_genre or "tam_quoc" in book_slug or "sử" in raw_genre:
        genre_key = "historical-chronicle"
        genre_label = "Chính sử & Biên niên"
    elif "sci-fi" in raw_genre or "khoa học viễn tưởng" in raw_genre or "three_body" in book_slug:
        genre_key = "sci-fi"
        genre_label = "Khoa học viễn tưởng"
    elif "technical" in raw_genre or "công nghệ" in raw_genre or "programming" in raw_genre:
        genre_key = "technical"
        genre_label = "Chuyên khảo kỹ thuật"
    else:
        genre_key = "general"
        genre_label = "Văn học & Chuyên khảo tổng quát"

    register = brief.get("register", "standard")
    style_rules = brief.get("style_rules", [])

    book_profile = build_book_profile(
        book_slug=book_slug,
        source_lang=source_lang,
        genre_key=genre_key,
        profile_override=profile_override,
    )

    metadata = {
        "book_slug": book_slug,
        "title": title,
        "title_original": title_original,
        "author": author,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "genre_key": genre_key,
        "genre_label": genre_label,
        "register": register,
        "style_rules": style_rules,
        "cover_image": None,
        "book_profile": book_profile,
    }

    glossary = normalize_glossary(book_record, book_slug)

    return metadata, glossary


def extract_chapter_footnotes(text: str) -> Dict[str, str]:
    """
    Extracts end-of-chapter or markdown footnotes into a structured map:
    {"1": "Explanation...", ...}
    """
    footnotes: Dict[str, str] = {}
    if not text:
        return footnotes

    # 1. Markdown style: [^1]: Explanation
    md_pattern = re.compile(r'^\[\^([a-zA-Z0-9_\-]+)\]:\s*(.+)$', re.MULTILINE)
    for m in md_pattern.finditer(text):
        ref_id = m.group(1).strip()
        content = m.group(2).strip()
        footnotes[ref_id] = content

    # 2. Classical style in "### Chú thích:", "### Tài liệu tham khảo", "## References", etc.
    note_section_m = re.search(r'(?:#{2,3}\s+(?:Chú thích|Tài liệu tham khảo|References):?)(.*)$', text, re.DOTALL | re.IGNORECASE)
    if note_section_m:
        section_text = note_section_m.group(1)
        # Match (1) Explanation, [1] Explanation, or 1. Explanation
        num_pattern = re.compile(
            r'^\s*(?:\((\d{1,4})\)|\[(\d{1,4})\]|(\d{1,4})\.)\s*(?:\*\*(.*?)\*\*:?\s*)?(.*)$',
            re.MULTILINE
        )
        for m in num_pattern.finditer(section_text):
            ref_id = m.group(1) or m.group(2) or m.group(3)
            title = m.group(4)
            desc = m.group(5)
            if title and desc:
                full_desc = f"{title.strip()}: {desc.strip()}"
            elif title:
                full_desc = title.strip()
            else:
                full_desc = desc.strip()
            if ref_id and full_desc:
                footnotes[ref_id] = full_desc

    return footnotes


def normalize_commentary_source_label(raw_label: str) -> str:
    """
    Normalizes a commentary source label by stripping parentheses, footnote markers ((1), [1]),
    colons, and trailing verbs ('chép rằng', 'chép lời ... rằng', 'chép', 'xét rằng', 'nói rằng', etc.)
    Returning a clean canonical badge like:
      - 'Bùi Tùng Chi chú • Tào Man truyện'
      - 'Bùi Tùng Chi chú • Ngụy thư'
      - 'Bùi Tùng Chi xét'
    """
    if not raw_label:
        return "Bùi Tùng Chi chú"

    label = raw_label.strip().rstrip(":").strip()
    if label.startswith("(") and label.endswith(")"):
        label = label[1:-1].strip()

    label = re.sub(r'[\(\[]\^?\d+[\)\]]', '', label).strip()

    if re.search(r'(?:(?:Thần|Bùi|Bề tôi)\s+Tùng Chi|(?<!Thần\s)(?<!Bùi\s)(?<!Bề tôi\s)Tùng Chi)', label, re.IGNORECASE):
        return "Bùi Tùng Chi xét"
    if label.startswith("Xét rằng") or label == "Xét":
        return "Bùi Tùng Chi xét"

    m_xet_sach = re.match(r'^Xét sách\s+(.+)$', label, re.IGNORECASE)
    if m_xet_sach:
        source_name = m_xet_sach.group(1).strip().rstrip(":")
        return f"Bùi Tùng Chi chú • {source_name}"

    label = re.sub(r'\s+chép lời\s+.+?\s+rằng$', '', label, flags=re.IGNORECASE)
    label = re.sub(r'\s+(?:đều\s+|cùng\s+|lại\s+|có\s+)?(?:chép rằng|chép|nói rằng|nói|viết|ghi|rằng|dẫn|tải|bàn rằng|bàn|xét rằng|xét|cho rằng|chê|kể|cẩn án|án|tâu rằng)\s*:?$', '', label, flags=re.IGNORECASE)
    label = label.strip().rstrip(":").strip()

    if label.startswith("Bùi Tùng Chi chú"):
        return label
    if not label:
        return "Bùi Tùng Chi chú"

    return f"Bùi Tùng Chi chú • {label}"


def parse_inline_spans(
    text: str,
    terms: Optional[List[Dict[str, Any]]] = None,
    utt_id: Optional[str] = None,
    seg_id: Optional[str] = None,
    start_utt_idx: int = 1,
    default_layer: str = "chinh_van",
    default_role: str = "narrator",
    default_speaker_id: str = "narrator_tran_tho",
    default_speaker_name: str = "Trần Thọ",
    default_voice_id: str = "narrator_tran_tho",
    default_emotion: str = "neutral_narrative",
    utterance_lookup: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Parses a string into a list of InlineSpan objects:
    dialogue, footnote_ref, term_ref, and plain text.
    Supports (N), [^N], and bracketed [N] citations.
    Splits dialogue spans around inner footnote_ref and term_ref tokens
    so embedded references remain interactive.
    Assigns incrementing S-XXXX_uNN IDs across narrative and dialogue transitions,
    and enriches spans with multi-character voice metadata from utterance_lookup.
    """
    if not text:
        return []

    # 1. Identify dialogue ranges
    dialogue_ranges: List[Tuple[int, int]] = []

    # Spoken dialogue quotes: “...”, "...", «...», 「...」, 『...』
    quote_pattern = re.compile(r'(?:“[^”]+”|"[^"\n]+"|«[^»]+»|「[^」]+」|『[^』]+』)')
    for m in quote_pattern.finditer(text):
        dialogue_ranges.append((m.start(), m.end()))

    # Leading dash dialogue line
    dash_m = re.match(r'^[—–―]\s+(.+)$', text)
    if dash_m:
        dialogue_ranges.append((0, len(text)))

    dialogue_ranges.sort(key=lambda r: r[0])

    # 2. Collect atomic spans: footnote_ref and term_ref
    fn_spans: List[Tuple[int, int, Dict[str, Any]]] = []

    # Match (N), [^N], and bracketed citations [N] (1-4 digits, excluding links [N](url) and images ![N])
    fn_pattern = re.compile(r'(?:\((\d{1,4})\)|\[\^(\d{1,4})\]|(?<!\!)\[(\d{1,4})\](?!\s*[\(:])|\((\d+)\)|\[\^(\d+)\])')
    for m in fn_pattern.finditer(text):
        ref_id = m.group(1) or m.group(2) or m.group(3) or m.group(4) or m.group(5)
        fn_spans.append((
            m.start(),
            m.end(),
            {
                "type": "footnote_ref",
                "text": m.group(0),
                "ref_id": ref_id,
            }
        ))

    term_spans: List[Tuple[int, int, Dict[str, Any]]] = []
    if terms:
        sorted_terms = sorted(
            [t for t in terms if t.get("target") or t.get("source")],
            key=lambda t: len(t.get("target") or t.get("source")),
            reverse=True,
        )
        for t in sorted_terms:
            target_str = t.get("target") or t.get("source")
            if not target_str or len(target_str) < 2:
                continue
            idx = 0
            while True:
                pos = text.find(target_str, idx)
                if pos == -1:
                    break
                term_spans.append((
                    pos,
                    pos + len(target_str),
                    {
                        "type": "term_ref",
                        "text": target_str,
                        "term_id": t.get("id", ""),
                        "source": t.get("source", target_str),
                        "target": target_str,
                    }
                ))
                idx = pos + len(target_str)

    # Sort atomic spans and resolve overlaps (footnote_ref always wins over term_ref)
    all_atomic: List[Tuple[int, int, int, Dict[str, Any]]] = []
    # priority: 0 for footnote_ref, 1 for term_ref
    for s, e, d in fn_spans:
        all_atomic.append((s, e, 0, d))
    for s, e, d in term_spans:
        all_atomic.append((s, e, 1, d))

    all_atomic.sort(key=lambda x: (x[0], x[2], -(x[1] - x[0])))

    atomic_non_overlapping: List[Tuple[int, int, Dict[str, Any]]] = []
    last_end = 0
    for s, e, prio, data in all_atomic:
        if s >= last_end:
            atomic_non_overlapping.append((s, e, data))
            last_end = e

    # 3. Partition text into contiguous utterance regions (narrative vs dialogue)
    regions: List[Tuple[str, int, int, int]] = []  # (kind, start, end, quote_ordinal)
    quote_ordinal = 0
    if not dialogue_ranges:
        regions.append(("narrative", 0, len(text), 0))
    else:
        last_pos = 0
        for d_s, d_e in dialogue_ranges:
            if d_s > last_pos:
                regions.append(("narrative", last_pos, d_s, 0))
            quote_ordinal += 1
            regions.append(("dialogue", d_s, d_e, quote_ordinal))
            last_pos = d_e
        if last_pos < len(text):
            regions.append(("narrative", last_pos, len(text), 0))

    region_utt_ids: List[Optional[str]] = []
    active_u_idx = start_utt_idx
    for r_kind, r_s, r_e, _ in regions:
        slice_txt = text[r_s:r_e].strip()
        if not slice_txt and r_kind == "narrative":
            region_utt_ids.append(None)
        else:
            region_utt_ids.append(f"{seg_id}_u{active_u_idx:02d}" if seg_id else (utt_id or None))
            active_u_idx += 1

    def get_region_for_pos(pos: int) -> Tuple[str, str, Dict[str, Any]]:
        for reg_idx, (r_kind, r_s, r_e, q_ord) in enumerate(regions):
            if r_s <= pos < r_e or (pos == r_e == len(text)):
                assigned_id = region_utt_ids[reg_idx] if reg_idx < len(region_utt_ids) else None

                meta: Dict[str, Any] = {}
                if assigned_id:
                    meta["utt_id"] = assigned_id

                if r_kind == "dialogue":
                    u_info = None
                    if utterance_lookup:
                        cand_info = utterance_lookup.get(assigned_id) if assigned_id else None
                        if cand_info and cand_info.get("role") == "character":
                            u_info = cand_info
                        elif seg_id and q_ord > 0 and (seg_id, q_ord) in utterance_lookup:
                            u_info = utterance_lookup[(seg_id, q_ord)]
                        elif cand_info:
                            u_info = cand_info

                    if u_info:
                        meta["layer"] = u_info.get("layer", default_layer)
                        meta["role"] = u_info.get("role") or u_info.get("candidate_role", "character")
                        meta["speaker_id"] = u_info.get("speaker_id") or u_info.get("candidate_speaker_id", "archetype_soldier_messenger")
                        meta["speaker_name"] = u_info.get("speaker_name") or u_info.get("candidate_alias", "Nhân vật")
                        meta["alias_used"] = u_info.get("alias_used") or u_info.get("candidate_alias", meta["speaker_name"])
                        meta["voice_id"] = u_info.get("voice_id") or u_info.get("candidate_voice_id", meta["speaker_id"])
                        meta["emotion"] = u_info.get("emotion") or u_info.get("candidate_emotion", "neutral_narrative")
                    elif assigned_id:
                        meta["layer"] = default_layer
                        meta["role"] = "character"
                        meta["speaker_id"] = "archetype_soldier_messenger"
                        meta["speaker_name"] = "Nhân vật"
                        meta["alias_used"] = "Nhân vật"
                        meta["voice_id"] = "archetype_soldier_messenger"
                        meta["emotion"] = "neutral_narrative"
                else:
                    # Non-dialogue narrative span: NEVER let utterance_lookup overwrite default layer/role/speaker
                    if assigned_id:
                        meta["layer"] = default_layer
                        meta["role"] = default_role
                        meta["speaker_id"] = default_speaker_id
                        meta["speaker_name"] = default_speaker_name
                        meta["alias_used"] = default_speaker_name
                        meta["voice_id"] = default_voice_id
                        meta["emotion"] = default_emotion

                return r_kind, assigned_id or "", meta

        # Fallback
        return "narrative", utt_id or "", {}

    # 4. Construct flat sequence with dialogue splitting and metadata
    spans: List[Dict[str, Any]] = []

    def emit_text_slice(start: int, end: int):
        if start >= end:
            return
        break_points = {start, end}
        for _, r_s, r_e, _ in regions:
            if start < r_s < end:
                break_points.add(r_s)
            if start < r_e < end:
                break_points.add(r_e)
        sorted_points = sorted(break_points)
        for i in range(len(sorted_points) - 1):
            p1, p2 = sorted_points[i], sorted_points[i + 1]
            if p1 >= p2:
                continue
            slice_text = text[p1:p2]
            if not slice_text:
                continue
            r_kind, _, meta = get_region_for_pos(p1)
            span_dict = {
                "type": "dialogue" if r_kind == "dialogue" else "text",
                "text": slice_text,
            }
            if slice_text.strip():
                span_dict.update(meta)
            spans.append(span_dict)

    curr_pos = 0
    for s, e, span_data in atomic_non_overlapping:
        if s > curr_pos:
            emit_text_slice(curr_pos, s)
        _, _, meta = get_region_for_pos(s)
        enriched_atomic = dict(span_data)
        enriched_atomic.update(meta)
        spans.append(enriched_atomic)
        curr_pos = e

    if curr_pos < len(text):
        emit_text_slice(curr_pos, len(text))

    if not spans:
        _, _, meta = get_region_for_pos(0)
        spans = [{"type": "text", "text": text, **meta}]

    return spans


def classify_segment_blocks(
    text: str,
    seg_type: str = "paragraph",
    terms: Optional[List[Dict[str, Any]]] = None,
    footnotes_map: Optional[Dict[str, str]] = None,
    seg_id: Optional[str] = None,
    is_chapter_opening: bool = False,
    source_lang: Optional[str] = None,
    utterance_lookup: Optional[Dict[str, Any]] = None,
    start_utt_idx: int = 1,
) -> List[Dict[str, Any]]:
    """
    Classifies a segment's translated markdown into rich blocks and inline spans.
    Supports multi-paragraph segment splitting (\n\n+), multi-transition mid-paragraph
    commentary splitting (REQ-008, P1-001), sub-span continuous S-XXXX_uNN utterance numbering,
    and voice metadata enrichment.
    Returns List[RichBlock].
    """
    if not text:
        return []

    # Strip footnote definition section if present at the end of the text
    clean_text = text
    note_idx = clean_text.find("### Chú thích:")
    if note_idx == -1:
        note_idx = clean_text.find("## Chú thích:")
    if note_idx == -1:
        note_idx = clean_text.find("### Chú thích\n")
    if note_idx != -1:
        clean_text = clean_text[:note_idx].rstrip()

    # 1. Code blocks (do not split by \n\n+)
    if seg_type == "code" or clean_text.startswith("```"):
        code_lines = clean_text.splitlines()
        lang = ""
        if code_lines and code_lines[0].startswith("```"):
            lang = code_lines[0][3:].strip()
            body = "\n".join(code_lines[1:-1] if code_lines[-1].startswith("```") else code_lines[1:])
        else:
            body = clean_text
        return [{
            "kind": "code",
            "language": lang,
            "code": body,
        }]

    # 2. Figure blocks (do not split by \n\n+)
    if seg_type == "figure" or clean_text.startswith("!["):
        fig_m = re.match(r'^!\[(.*?)\]\((.*?)\)', clean_text)
        if fig_m:
            return [{
                "kind": "figure",
                "alt": fig_m.group(1),
                "image_src": fig_m.group(2),
            }]

    # 3. Multi-paragraph segment splitting (REQ-002, REQ-008):
    # Split paragraphs by \n\n+ first, advancing current_utt_idx continuously across paragraphs
    paras = [p.strip() for p in re.split(r'\n{2,}', clean_text) if p.strip()]
    if len(paras) > 1:
        blocks: List[Dict[str, Any]] = []
        current_utt_idx = start_utt_idx
        for p in paras:
            p_type = "heading" if p.startswith("#") else seg_type
            p_blocks = classify_segment_blocks(
                text=p,
                seg_type=p_type,
                terms=terms,
                footnotes_map=footnotes_map,
                seg_id=seg_id,
                is_chapter_opening=is_chapter_opening,
                source_lang=source_lang,
                utterance_lookup=utterance_lookup,
                start_utt_idx=current_utt_idx,
            )
            blocks.extend(p_blocks)
            for b in p_blocks:
                for s in b.get("spans", []):
                    uid = s.get("utt_id")
                    if uid and "_u" in uid:
                        try:
                            u_num = int(uid.split("_u")[-1])
                            if u_num >= current_utt_idx:
                                current_utt_idx = u_num + 1
                        except ValueError:
                            pass
        return blocks

    blocks: List[Dict[str, Any]] = []
    current_utt_idx = start_utt_idx

    # 4. Heading
    if seg_type == "heading" or clean_text.startswith("#"):
        heading_level = 1
        m_level = re.match(r'^(#{1,6})\s+(.*)$', clean_text)
        if m_level:
            heading_level = len(m_level.group(1))
            heading_title = m_level.group(2).strip()
        else:
            heading_title = clean_text.lstrip("#").strip()

        from scripts.compilers.base import BaseCompiler
        slug = BaseCompiler.slugify(heading_title)
        blocks.append({
            "kind": "heading",
            "level": heading_level,
            "heading_id": slug,
            "spans": parse_inline_spans(
                heading_title,
                terms,
                seg_id=seg_id,
                start_utt_idx=current_utt_idx,
                default_layer="chinh_van",
                default_role="narrator",
                default_speaker_id="narrator_tran_tho",
                default_speaker_name="Trần Thọ",
                default_voice_id="narrator_tran_tho",
                utterance_lookup=utterance_lookup,
            ),
        })
        return blocks

    # 4. Blockquote
    if seg_type == "blockquote" or clean_text.startswith(">"):
        quote_body = re.sub(r'^>\s?', '', clean_text, flags=re.MULTILINE).strip()
        blocks.append({
            "kind": "blockquote",
            "spans": parse_inline_spans(
                quote_body,
                terms,
                seg_id=seg_id,
                start_utt_idx=current_utt_idx,
                default_layer="chinh_van",
                default_role="narrator",
                default_speaker_id="narrator_tran_tho",
                default_speaker_name="Trần Thọ",
                default_voice_id="narrator_tran_tho",
                utterance_lookup=utterance_lookup,
            ),
        })
        return blocks

    # 5. Epigraph
    if clean_text.startswith("—") or clean_text.startswith("--"):
        blocks.append({
            "kind": "epigraph",
            "attribution": clean_text.lstrip("—- ").strip(),
            "spans": parse_inline_spans(
                clean_text,
                terms,
                seg_id=seg_id,
                start_utt_idx=current_utt_idx,
                default_layer="chinh_van",
                default_role="narrator",
                default_speaker_id="narrator_tran_tho",
                default_speaker_name="Trần Thọ",
                default_voice_id="narrator_tran_tho",
                utterance_lookup=utterance_lookup,
            ),
        })
        return blocks

    # 6. Technical callout
    tc_m = TECHNICAL_CALLOUT_RE.match(clean_text)
    if tc_m:
        title = tc_m.group(1).strip()
        blocks.append({
            "kind": "technical_callout",
            "callout_title": title,
            "spans": parse_inline_spans(
                clean_text,
                terms,
                seg_id=seg_id,
                start_utt_idx=current_utt_idx,
                default_layer="chinh_van",
                default_role="narrator",
                default_speaker_id="narrator_tran_tho",
                default_speaker_name="Trần Thọ",
                default_voice_id="narrator_tran_tho",
                utterance_lookup=utterance_lookup,
            ),
        })
        return blocks

    # 7. Principle card
    pc_m = PRINCIPLE_CARD_RE.match(clean_text)
    if pc_m:
        num = pc_m.group(1) or pc_m.group(2)
        blocks.append({
            "kind": "principle_card",
            "principle_number": num,
            "spans": parse_inline_spans(
                clean_text,
                terms,
                seg_id=seg_id,
                start_utt_idx=current_utt_idx,
                default_layer="chinh_van",
                default_role="narrator",
                default_speaker_id="narrator_tran_tho",
                default_speaker_name="Trần Thọ",
                default_voice_id="narrator_tran_tho",
                utterance_lookup=utterance_lookup,
            ),
        })
        return blocks

    # 8. Multi-transition Commentary & Chronicle Citations (REQ-006, REQ-008, P1-001, P1-05)
    matches = list(MID_PARAGRAPH_COMMENTARY_RE.finditer(clean_text))
    if not matches:
        ch_m = CHRONICLE_ATTRIBUTION_RE.match(clean_text)
        if ch_m:
            raw_label = ch_m.group(0)
            has_fn = bool(re.search(r'(\(\d+\)|\[\^?\d+\])', raw_label))
            if has_fn:
                norm_label = "Bùi Tùng Chi chú"
                part_text = clean_text
            else:
                norm_label = normalize_commentary_source_label(raw_label)
                stripped = clean_text[len(raw_label):].lstrip(" :\n\t")
                part_text = stripped if stripped else clean_text

            spans = parse_inline_spans(
                part_text,
                terms,
                seg_id=seg_id,
                start_utt_idx=current_utt_idx,
                default_layer="bui_tung_chi_chu",
                default_role="commentator",
                default_speaker_id="commentator_bui_tung_chi",
                default_speaker_name="Bùi Tùng Chi",
                default_voice_id="commentator_bui_tung_chi",
                utterance_lookup=utterance_lookup,
            )
            blocks.append({
                "kind": "chronicle_citation",
                "source_label": norm_label,
                "spans": spans,
            })
            return blocks

    if matches:
        if matches[0].start() > 0:
            part0 = clean_text[:matches[0].start()].strip()
            if part0:
                spans0 = parse_inline_spans(
                    part0,
                    terms,
                    seg_id=seg_id,
                    start_utt_idx=current_utt_idx,
                    default_layer="chinh_van",
                    default_role="narrator",
                    default_speaker_id="narrator_tran_tho",
                    default_speaker_name="Trần Thọ",
                    default_voice_id="narrator_tran_tho",
                    utterance_lookup=utterance_lookup,
                )
                blocks.append({
                    "kind": "paragraph",
                    "spans": spans0,
                })
                max_u = max([int(s["utt_id"].split("_u")[-1]) for s in spans0 if s.get("utt_id") and "_u" in s["utt_id"]], default=current_utt_idx)
                current_utt_idx = max_u + 1

        for i in range(len(matches)):
            start_pos = matches[i].start()
            end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(clean_text)
            part_text = clean_text[start_pos:end_pos].strip()
            raw_label = matches[i].group(0)
            has_fn = bool(re.search(r'(\(\d+\)|\[\^?\d+\])', raw_label))
            if has_fn:
                norm_label = "Bùi Tùng Chi chú"
            else:
                norm_label = normalize_commentary_source_label(raw_label)
                if part_text.startswith(raw_label):
                    stripped = part_text[len(raw_label):].lstrip(" :\n\t")
                    if stripped:
                        part_text = stripped

            spans_c = parse_inline_spans(
                part_text,
                terms,
                seg_id=seg_id,
                start_utt_idx=current_utt_idx,
                default_layer="bui_tung_chi_chu",
                default_role="commentator",
                default_speaker_id="commentator_bui_tung_chi",
                default_speaker_name="Bùi Tùng Chi",
                default_voice_id="commentator_bui_tung_chi",
                utterance_lookup=utterance_lookup,
            )
            blocks.append({
                "kind": "chronicle_citation",
                "source_label": norm_label,
                "spans": spans_c,
            })
            max_u = max([int(s["utt_id"].split("_u")[-1]) for s in spans_c if s.get("utt_id") and "_u" in s["utt_id"]], default=current_utt_idx)
            current_utt_idx = max_u + 1

        return blocks

    # 10. Default narrative paragraph
    spans_p = parse_inline_spans(
        clean_text,
        terms,
        seg_id=seg_id,
        start_utt_idx=current_utt_idx,
        default_layer="chinh_van",
        default_role="narrator",
        default_speaker_id="narrator_tran_tho",
        default_speaker_name="Trần Thọ",
        default_voice_id="narrator_tran_tho",
        utterance_lookup=utterance_lookup,
    )
    blocks.append({
        "kind": "paragraph",
        "spans": spans_p,
    })

    return blocks


# ---------------------------------------------------------------------------
# Schema Validators
# ---------------------------------------------------------------------------

def validate_manifest(manifest: Dict[str, Any]) -> None:
    """Validates Manifest dictionary against BookPackage v1.0 schema."""
    if not isinstance(manifest, dict):
        raise ValueError("Manifest must be a JSON object")

    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Invalid schema_version: expected '{SCHEMA_VERSION}'")

    for field in [
        "book_slug", "title", "author", "source_lang", "target_lang",
        "genre_key", "genre_label", "register", "style_rules",
        "book_profile", "stats", "chapters",
    ]:
        if field not in manifest:
            raise ValueError(f"Missing required manifest field: '{field}'")

    if manifest["source_lang"] not in ("en", "zh"):
        raise ValueError(f"Invalid source_lang: {manifest['source_lang']}")

    if manifest["target_lang"] != "vi":
        raise ValueError(f"Invalid target_lang: {manifest['target_lang']}")

    if manifest["genre_key"] not in VALID_GENRE_KEYS:
        raise ValueError(f"Invalid genre_key: {manifest['genre_key']}")

    bp = manifest["book_profile"]
    if not isinstance(bp, dict):
        raise ValueError("book_profile must be an object")

    for bp_field in ["theme_preset", "bilingual_layers", "has_drop_caps", "ornament_style", "toc_groups", "block_styles"]:
        if bp_field not in bp:
            raise ValueError(f"Missing required book_profile field: '{bp_field}'")

    if bp["theme_preset"] not in VALID_THEMES:
        raise ValueError(f"Invalid theme_preset: {bp['theme_preset']}")

    if bp["ornament_style"] not in VALID_ORNAMENTS:
        raise ValueError(f"Invalid ornament_style: {bp['ornament_style']}")

    stats = manifest["stats"]
    if not isinstance(stats, dict):
        raise ValueError("stats must be an object")
    for s_field in ["chapter_count", "total_segments", "total_words", "average_score"]:
        if s_field not in stats:
            raise ValueError(f"Missing required stats field: '{s_field}'")

    if not isinstance(manifest["chapters"], list):
        raise ValueError("chapters must be a list")

    for ch in manifest["chapters"]:
        for ch_req in ["ch_id", "ch_num", "title", "segment_count", "word_count", "subheadings"]:
            if ch_req not in ch:
                raise ValueError(f"Missing chapter summary field: '{ch_req}'")
        if not re.match(r'^ch\d{2,}$', str(ch["ch_id"])):
            raise ValueError(f"Invalid chapter ch_id: '{ch['ch_id']}'")


def validate_glossary(glossary: Dict[str, Any]) -> None:
    """Validates Glossary dictionary against BookPackage v1.0 schema."""
    if not isinstance(glossary, dict):
        raise ValueError("Glossary must be a JSON object")

    if glossary.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Invalid schema_version: expected '{SCHEMA_VERSION}'")

    if "book_slug" not in glossary:
        raise ValueError("Missing 'book_slug' in glossary")

    if not isinstance(glossary.get("entries"), list):
        raise ValueError("Missing or invalid 'entries' list in glossary")

    if not isinstance(glossary.get("pronouns"), list):
        raise ValueError("Missing or invalid 'pronouns' list in glossary")

    for entry in glossary["entries"]:
        for req in ["id", "category", "source", "target", "aliases", "variants", "notes"]:
            if req not in entry:
                raise ValueError(f"Missing glossary entry field: '{req}'")
        if entry["category"] not in VALID_GLOSSARY_CATEGORIES:
            raise ValueError(f"Invalid glossary entry category: '{entry['category']}'")

    for rule in glossary["pronouns"]:
        for req in ["pair", "a_to_b", "b_to_a", "basis"]:
            if req not in rule:
                raise ValueError(f"Missing pronoun rule field: '{req}'")
        if not isinstance(rule["pair"], list) or len(rule["pair"]) != 2:
            raise ValueError(f"Pronoun rule 'pair' must be a 2-element list: {rule.get('pair')}")


def validate_chapter_document(chapter: Dict[str, Any]) -> None:
    """Validates ChapterDocument dictionary against BookPackage v1.0 schema."""
    if not isinstance(chapter, dict):
        raise ValueError("ChapterDocument must be a JSON object")

    if chapter.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Invalid schema_version: expected '{SCHEMA_VERSION}'")

    for req in ["book_slug", "ch_id", "ch_num", "title", "subheadings", "footnotes", "segments"]:
        if req not in chapter:
            raise ValueError(f"Missing chapter document field: '{req}'")

    if not isinstance(chapter["segments"], list):
        raise ValueError("segments must be a list")

    for seg in chapter["segments"]:
        for s_req in ["seg_id", "seg_order", "seg_type", "original", "translate", "matched_terms", "blocks"]:
            if s_req not in seg:
                raise ValueError(f"Missing segment field: '{s_req}'")
        if not re.match(r'^S-\d{4,}$', str(seg["seg_id"])):
            raise ValueError(f"Invalid segment seg_id pattern: '{seg['seg_id']}'")
        if seg["seg_type"] not in VALID_SEG_TYPES:
            raise ValueError(f"Invalid segment seg_type: '{seg['seg_type']}'")
        for b in seg["blocks"]:
            if "kind" not in b or b["kind"] not in VALID_BLOCK_KINDS:
                raise ValueError(f"Invalid block kind: '{b.get('kind')}'")


def validate_feedback_export(feedback: Dict[str, Any]) -> None:
    """Validates FeedbackExport dictionary against BookPackage v1.0 schema."""
    if not isinstance(feedback, dict):
        raise ValueError("FeedbackExport must be a JSON object")

    if feedback.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Invalid schema_version: expected '{SCHEMA_VERSION}'")

    for req in ["book_slug", "exported_at", "items"]:
        if req not in feedback:
            raise ValueError(f"Missing feedback field: '{req}'")

    if not isinstance(feedback["items"], list):
        raise ValueError("items must be a list")

    for it in feedback["items"]:
        for i_req in ["comment_id", "chapter_id", "segment_id", "category", "span_source", "span_target", "problem", "suggested_fix", "status"]:
            if i_req not in it:
                raise ValueError(f"Missing feedback item field: '{i_req}'")
        if not re.match(r'^S-\d{4,}$', str(it["segment_id"])):
            raise ValueError(f"Invalid feedback item segment_id: '{it['segment_id']}'")
        if it["category"] not in VALID_FEEDBACK_CATEGORIES:
            raise ValueError(f"Invalid feedback item category: '{it['category']}'")
        if it["status"] not in VALID_FEEDBACK_STATUSES:
            raise ValueError(f"Invalid feedback item status: '{it['status']}'")
