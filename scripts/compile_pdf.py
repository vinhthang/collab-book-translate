#!/usr/bin/env python3
"""
scripts/compile_pdf.py — Vietnamese PDF Book Compiler
Compiles translated chapters (ch<NN>/final.txt or fallback ch<NN>/source.txt)
into a single beautifully-typeset PDF book with embedded images, Unicode fonts,
dynamic Table of Contents (Mục Lục), and Alphabetical Term Index (Bảng Tra Cứu Thuật Ngữ).
Uses WeasyPrint (weasyprint).
"""

import argparse
import gc
import html
import json
import os
import platform
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# On macOS Apple Silicon, hook CFFI dlopen to resolve Homebrew shared libraries
if platform.system() == "Darwin":
    import ctypes
    for lib_name in [
        "libgobject-2.0.dylib",
        "libpango-1.0.dylib",
        "libharfbuzz.dylib",
        "libfontconfig.dylib",
        "libpangoft2-1.0.dylib",
    ]:
        p = f"/opt/homebrew/lib/{lib_name}"
        if os.path.exists(p):
            try:
                ctypes.CDLL(p)
            except Exception:
                pass

    try:
        import cffi
        orig_dlopen = cffi.FFI.dlopen

        def hooked_dlopen(self, name, flags=None):
            if isinstance(name, str) and not os.path.isabs(name):
                for prefix in ["/opt/homebrew/lib", "/usr/local/lib"]:
                    cand = f"{prefix}/{name}"
                    if os.path.exists(cand):
                        name = cand
                        break
                    if "." in name:
                        base = name.split(".")[0]
                        cand_dylib = f"{prefix}/{base}.dylib"
                        if os.path.exists(cand_dylib):
                            name = cand_dylib
                            break
            if flags is not None:
                return orig_dlopen(self, name, flags)
            return orig_dlopen(self, name)

        cffi.FFI.dlopen = hooked_dlopen
    except Exception:
        pass

from urllib.parse import urlparse
from urllib.request import url2pathname
from scripts.utils.language import get_pinyin_initial, is_cjk_codepoint

try:
    import weasyprint
except ImportError:
    weasyprint = None

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None

ALLOWED_FONT_DIRS = [
    Path("/Library/Fonts"),
    Path("/System/Library/Fonts"),
    Path.home() / ".fonts",
    Path("/usr/share/fonts"),
    Path.home() / "Library/Fonts",
]


def build_safe_url_fetcher(allowed_workspace_dirs: List[Path]):
    """Builds a URL fetcher for WeasyPrint restricting file:// URIs strictly to approved directories."""
    roots = [d.resolve() for d in allowed_workspace_dirs] + [d.resolve() for d in ALLOWED_FONT_DIRS if d.exists()]

    if weasyprint is not None:
        if hasattr(weasyprint, "default_url_fetcher"):
            _fetcher = weasyprint.default_url_fetcher
        elif hasattr(weasyprint, "urls") and hasattr(weasyprint.urls, "URLFetcher"):
            _fetcher = weasyprint.urls.URLFetcher()
        else:
            _fetcher = None
    else:
        _fetcher = None

    def safe_url_fetcher(url: str):
        parsed = urlparse(url)
        if parsed.scheme != "file":
            raise PermissionError(f"External network or non-file scheme blocked in WeasyPrint: {url}")
        local_path = Path(url2pathname(parsed.path)).resolve()
        if not any(local_path.is_relative_to(r) for r in roots):
            raise PermissionError(f"Local file outside approved directories blocked: {local_path}")
        if _fetcher is not None:
            return _fetcher(url)
        if weasyprint is None:
            raise RuntimeError("weasyprint is required for PDF compilation")
        return weasyprint.urls.URLFetcher()(url)

    safe_url_fetcher._fail_on_errors = False
    return safe_url_fetcher


def slugify(text: str) -> str:
    """Converts a string into a clean lowercase URL/anchor slug."""
    if not text or not str(text).strip():
        return "item"
    s = str(text).strip()
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', s.lower()).strip('-')
    if slug:
        return slug
    hex_parts = [f"u{ord(c):04x}" for c in s if not c.isspace()]
    return "-".join(hex_parts) if hex_parts else "item"


def parse_chapters_filter(filter_str: Optional[str]) -> Optional[set]:
    """Parses chapter filter string like '1-3' or '1,2'."""
    if not filter_str or filter_str.lower() in ("all", "*"):
        return None
    chapters = set()
    for part in filter_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                start_s, end_s = part.split("-", 1)
                chapters.update(range(int(start_s.strip()), int(end_s.strip()) + 1))
            except ValueError:
                pass
        else:
            try:
                chapters.add(int(part))
            except ValueError:
                pass
    return chapters if chapters else None


def extract_headings_from_markdown(md_text: str, ch_num: int = 1) -> List[Dict[str, Any]]:
    """Extracts level 1 and level 2 headings from markdown text with deterministic anchor IDs."""
    headings: List[Dict[str, Any]] = []
    lines = md_text.split("\n")
    in_code = False
    seen_ids: Set[str] = set()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue

        m = re.match(r'^(#{1,2})\s+(.*)$', stripped)
        if m:
            level = len(m.group(1))
            h_raw = m.group(2).strip()
            slug = slugify(h_raw)
            ch_prefix = f"ch{ch_num:02d}-"
            h_id = f"{ch_prefix}{slug}" if slug else f"{ch_prefix}heading-{len(seen_ids)+1}"
            if h_id in seen_ids:
                h_id = f"{h_id}-{len(seen_ids)+1}"
            seen_ids.add(h_id)
            headings.append({
                "level": level,
                "title": h_raw,
                "id": h_id,
                "ch_num": ch_num,
            })

    return headings


def extract_terms_from_book_record(book_record_or_path: Any) -> List[Dict[str, str]]:
    """
    Extracts terms and names from book_record.json or parsed dict.
    Supports top-level or nested 'book_record':
    - terms / term_table: list of {source, target} or dict {source: target}
    - names / name_table: list of {source, target} or dict {source: target}
    Returns a deduplicated list of dicts: [{'source': ..., 'target': ...}, ...]
    sorted strictly by English source alphabetically.
    """
    data = None
    if isinstance(book_record_or_path, (str, Path)):
        p = Path(book_record_or_path)
        if p.is_dir():
            cand = p / "book_record.json"
            if cand.is_file():
                p = cand
        if p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                return []
        else:
            return []
    elif isinstance(book_record_or_path, dict):
        data = book_record_or_path

    if not data:
        return []

    collected: Dict[str, tuple] = {}

    def process_container(container):
        if isinstance(container, list):
            for it in container:
                if isinstance(it, dict) and "source" in it:
                    src = str(it["source"]).strip()
                    tgt = str(it.get("target", src)).strip()
                    if src and src.lower() not in collected:
                        collected[src.lower()] = (src, tgt)
        elif isinstance(container, dict):
            for k, v in container.items():
                src = str(k).strip()
                if isinstance(v, dict):
                    tgt = str(v.get("target", src)).strip()
                else:
                    tgt = str(v).strip()
                if src and src.lower() not in collected:
                    collected[src.lower()] = (src, tgt)

    keys = ["terms", "term_table", "names", "name_table"]
    for k in keys:
        if k in data:
            process_container(data[k])

    if "book_record" in data and isinstance(data["book_record"], dict):
        for k in keys:
            if k in data["book_record"]:
                process_container(data["book_record"][k])

    result = [{"source": src, "target": tgt} for src, tgt in collected.values()]
    result.sort(key=lambda x: x["source"].lower())
    return result


GFM_TABLE_SEP_RE = re.compile(r"^\|?[\s:]*-{3,}[\s:]*(?:\|[\s:]*-{3,}[\s:]*)*\|?$")


def _inject_term_anchors(
    text: str,
    terms: List[Dict[str, str]],
    anchor_tracker: Dict[str, List[str]],
    ch_term_counts: Dict[str, int],
    max_per_chapter: int = 2,
) -> str:
    """Safely injects <span id="idx-{term_slug}-{i}"></span> anchors into text without corrupting HTML tags."""
    tags: List[str] = []

    def stash_tag(m):
        tags.append(m.group(0))
        return f"__TAG_{len(tags)-1}__"

    protected_text = re.sub(r'<[^>]+>', stash_tag, text)

    # Sort terms by length descending to match specific multi-word terms first
    sorted_terms = sorted(terms, key=lambda x: len(x.get("source", "")), reverse=True)

    for item in sorted_terms:
        source = item.get("source", "").strip()
        target = item.get("target", "").strip()
        if not source:
            continue
        if ch_term_counts.get(source, 0) >= max_per_chapter:
            continue

        slug = slugify(source)
        patterns = []
        if len(source) >= 2:
            patterns.append(re.compile(r'(?<![\w])' + re.escape(source) + r'(?![\w])', re.IGNORECASE))
        if target and target.lower() != source.lower() and len(target) >= 2:
            patterns.append(re.compile(r'(?<![\w])' + re.escape(target) + r'(?![\w])', re.IGNORECASE))

        matched = False
        for pat in patterns:
            if pat.search(protected_text):
                def replace_first(m):
                    nonlocal matched
                    if matched:
                        return m.group(0)
                    matched = True
                    idx = len(anchor_tracker.get(source, [])) + 1
                    anchor_id = f"idx-{slug}-{idx}"
                    anchor_tracker.setdefault(source, []).append(anchor_id)
                    ch_term_counts[source] = ch_term_counts.get(source, 0) + 1
                    new_tag = f'<span id="{anchor_id}"></span>'
                    tags.append(new_tag)
                    tag_placeholder = f"__TAG_{len(tags)-1}__"
                    return f"{tag_placeholder}{m.group(0)}"

                protected_text = pat.sub(replace_first, protected_text, count=1)
                if matched:
                    break

    # Restore HTML tags
    for i, tag in enumerate(tags):
        protected_text = protected_text.replace(f"__TAG_{i}__", tag)

    return protected_text


def markdown_to_html(
    md_text: str,
    run_dir: str = "",
    ch_dir: str = "",
    ch_num: Optional[int] = None,
    terms: Optional[List[Dict[str, str]]] = None,
    anchor_tracker: Optional[Dict[str, List[str]]] = None,
    add_heading_ids: bool = False,
    ch_idx: Optional[int] = None,
) -> str:
    """
    Converts markdown text to clean HTML, resolving image anchors
    ![Figure](images/...) to absolute file:// URLs.
    Supports optional heading IDs for TOC and term anchor spans for Index.
    Renders GFM pipe tables and footnotes (REQ-007, REQ-008).
    """
    active_ch = ch_idx if ch_idx is not None else (ch_num if ch_num is not None else 0)
    lines = md_text.split("\n")
    html_parts: List[str] = []
    in_code = False
    code_lang = ""
    code_lines: List[str] = []
    para_lines: List[str] = []
    seen_heading_ids: Set[str] = set()
    ch_term_counts: Dict[str, int] = {}

    fn_map: Dict[str, str] = {}
    fn_style: Dict[str, str] = {}
    seen_fnrefs: Set[str] = set()

    sec_m = re.search(
        r'^(#{2,3})\s+(Chú thích|Tài liệu tham khảo|References):?\s*$',
        md_text,
        re.MULTILINE | re.IGNORECASE,
    )
    if sec_m:
        chu_thich_section = md_text[sec_m.start():]
        current_num = None
        for line in chu_thich_section.splitlines():
            line_str = line.strip()
            if not line_str or sec_m.group(0).strip().lower() in line_str.lower():
                continue
            m_paren = re.match(r'^\((\d{1,4})\)\s*(.+)$', line_str)
            m_bracket = re.match(r'^\[(\d{1,4})\]\s*(.+)$', line_str)
            if m_paren:
                current_num = m_paren.group(1)
                fn_map[current_num] = m_paren.group(2).strip()
                fn_style[current_num] = "paren"
            elif m_bracket:
                current_num = m_bracket.group(1)
                fn_map[current_num] = m_bracket.group(2).strip()
                fn_style[current_num] = "bracket"
            elif current_num is not None:
                fn_map[current_num] += " " + line_str

    def flush_paragraph():
        nonlocal para_lines
        if not para_lines:
            return
        p_text = " ".join(line.strip() for line in para_lines if line.strip())
        if p_text:
            # Inline formatting while escaping raw < and > in prose outside code spans
            parts = re.split(r'(`[^`]+?`)', p_text)
            formatted_parts = []
            for part in parts:
                if part.startswith('`') and part.endswith('`') and len(part) >= 2:
                    code_content = html.escape(part[1:-1])
                    formatted_parts.append(f"<code>{code_content}</code>")
                else:
                    escaped = html.escape(part, quote=False)
                    # Convert markdown links [text](url) (excluding images ![...])
                    escaped = re.sub(
                        r'(?<!\!)\[([^\]]+)\]\(([^)]+)\)',
                        r'<a href="\2">\1</a>',
                        escaped,
                    )
                    escaped = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', escaped)
                    escaped = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)
                    escaped = re.sub(r'\*([^*]+?)\*', r'<em>\1</em>', escaped)
                    # Inline footnote reference [^N]
                    def _replace_caret_fn(match: re.Match) -> str:
                        num = match.group(1)
                        if num not in seen_fnrefs:
                            seen_fnrefs.add(num)
                            id_attr = f' id="fnref-ch{active_ch}-{num}"'
                        else:
                            id_attr = ""
                        return f'<sup class="footnote-ref"><a{id_attr} href="#fn-ch{active_ch}-{num}">[{num}]</a></sup>'

                    escaped = re.sub(
                        r'\[\^(\d+)\]',
                        _replace_caret_fn,
                        escaped,
                    )
                    # Inline footnote reference (N) matching Chú thích:
                    def _replace_fnref(match: re.Match) -> str:
                        num = match.group(1) or match.group(2)
                        if num in fn_map and fn_style.get(num) == "paren":
                            if num not in seen_fnrefs:
                                seen_fnrefs.add(num)
                                id_attr = f' id="fnref-ch{active_ch}-{num}"'
                            else:
                                id_attr = ""
                            return f'<sup class="footnote-ref"><a{id_attr} href="#fn-ch{active_ch}-{num}">({num})</a></sup>'
                        return match.group(0)

                    escaped = re.sub(
                        r'(?<=[a-zA-ZÀ-Ỹa-ỹ0-9_])\((\d{1,4})\)|\((\d{1,4})\)(?=\s|[.,;:!?]|$)',
                        _replace_fnref,
                        escaped,
                    )

                    # Inline bracketed citations [N]
                    def _replace_bracket_fn(match: re.Match) -> str:
                        num = match.group(1)
                        if num in fn_map and fn_style.get(num) == "bracket":
                            if num not in seen_fnrefs:
                                seen_fnrefs.add(num)
                                id_attr = f' id="fnref-ch{active_ch}-{num}"'
                            else:
                                id_attr = ""
                            return f'<sup class="footnote-ref"><a{id_attr} href="#fn-ch{active_ch}-{num}">[{num}]</a></sup>'
                        return match.group(0)

                    escaped = re.sub(
                        r'(?<!\!)\[(\d{1,4})\](?!\s*[\(:])',
                        _replace_bracket_fn,
                        escaped,
                    )
                    formatted_parts.append(escaped)
            p_text = "".join(formatted_parts)

            if terms and anchor_tracker is not None:
                p_text = _inject_term_anchors(
                    p_text, terms, anchor_tracker, ch_term_counts, max_per_chapter=2
                )

            html_parts.append(f"<p>{p_text}</p>")
        para_lines = []

    def flush_code():
        nonlocal in_code, code_lines, code_lang
        if not in_code:
            return
        joined_code = "\n".join(code_lines)
        escaped_code = html.escape(joined_code)
        lang_class = f' class="language-{code_lang}"' if code_lang else ""
        html_parts.append(f'<pre><code{lang_class}>{escaped_code}</code></pre>')
        in_code = False
        code_lines = []
        code_lang = ""

    def resolve_image_path(src: str) -> str:
        if not run_dir and not ch_dir:
            return src
        cand1 = os.path.join(run_dir, src) if run_dir else ""
        if cand1 and os.path.exists(cand1):
            return Path(cand1).resolve().as_uri()
        cand2 = os.path.join(ch_dir, src) if ch_dir else ""
        if cand2 and os.path.exists(cand2):
            return Path(cand2).resolve().as_uri()
        cand3 = os.path.join(run_dir, "images", os.path.basename(src)) if run_dir else ""
        if cand3 and os.path.exists(cand3):
            return Path(cand3).resolve().as_uri()
        return src

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Code fences
        if stripped.startswith("```"):
            if in_code:
                flush_code()
            else:
                flush_paragraph()
                in_code = True
                code_lang = stripped[3:].strip()
                code_lines = []
            i += 1
            continue

        if in_code:
            code_lines.append(line)
            i += 1
            continue

        # Blank line separates paragraphs
        if not stripped:
            flush_paragraph()
            i += 1
            continue

        # Image anchor: ![alt](path)
        img_match = re.match(r'^!\[(.*?)\]\((.*?)\)$', stripped)
        if img_match:
            flush_paragraph()
            alt_text = html.escape(img_match.group(1))
            img_src = img_match.group(2)
            resolved_uri = resolve_image_path(img_src)
            caption_html = f"<figcaption>{alt_text}</figcaption>" if alt_text and alt_text.lower() != "figure" else ""
            html_parts.append(f'<figure><img src="{resolved_uri}" alt="{alt_text}"/>{caption_html}</figure>')
            i += 1
            continue

        # Footnote definition: [^N]: Footnote body
        fn_match = re.match(r'^\[\^(\d+)\]:\s*(.+)$', stripped)
        if fn_match:
            flush_paragraph()
            fn_num = fn_match.group(1)
            fn_body = fn_match.group(2)
            # Escape HTML in footnote body before inline formatting
            parts = re.split(r'(`[^`]+?`)', fn_body)
            formatted_parts = []
            for part in parts:
                if part.startswith('`') and part.endswith('`') and len(part) >= 2:
                    code_content = html.escape(part[1:-1])
                    formatted_parts.append(f"<code>{code_content}</code>")
                else:
                    escaped = html.escape(part, quote=False)
                    # Convert markdown links [text](url) (excluding images ![...])
                    escaped = re.sub(
                        r'(?<!\!)\[([^\]]+)\]\(([^)]+)\)',
                        r'<a href="\2">\1</a>',
                        escaped,
                    )
                    escaped = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', escaped)
                    escaped = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)
                    escaped = re.sub(r'\*([^*]+?)\*', r'<em>\1</em>', escaped)
                    formatted_parts.append(escaped)
            formatted_fn_body = "".join(formatted_parts)
            html_parts.append(
                f'<aside class="footnote" id="fn-ch{active_ch}-{fn_num}">'
                f'<span class="fn-num">[{fn_num}]</span> {formatted_fn_body}</aside>'
            )
            i += 1
            continue

        # Header: ### Chú thích:, ### Tài liệu tham khảo, ## References, etc.
        sec_header_m = re.match(r'^(#{2,3})\s+(Chú thích|Tài liệu tham khảo|References):?\s*$', stripped, re.IGNORECASE)
        if sec_header_m:
            flush_paragraph()
            h_tag = "h2" if len(sec_header_m.group(1)) == 2 else "h3"
            h_title = sec_header_m.group(2).strip()
            if not h_title.endswith(":"):
                h_title += ":"
            html_parts.append(f"<{h_tag}>{html.escape(h_title)}</{h_tag}>")
            i += 1
            continue

        # Footnote definition: (N) Footnote body
        paren_fn_match = re.match(r'^\((\d{1,4})\)\s*(.+)$', stripped)
        if paren_fn_match and paren_fn_match.group(1) in fn_map:
            flush_paragraph()
            fn_num = paren_fn_match.group(1)
            fn_body = paren_fn_match.group(2)
            parts = re.split(r'(`[^`]+?`)', fn_body)
            formatted_parts = []
            for part in parts:
                if part.startswith('`') and part.endswith('`') and len(part) >= 2:
                    code_content = html.escape(part[1:-1])
                    formatted_parts.append(f"<code>{code_content}</code>")
                else:
                    escaped = html.escape(part, quote=False)
                    # Convert markdown links [text](url) (excluding images ![...])
                    escaped = re.sub(
                        r'(?<!\!)\[([^\]]+)\]\(([^)]+)\)',
                        r'<a href="\2">\1</a>',
                        escaped,
                    )
                    escaped = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', escaped)
                    escaped = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)
                    escaped = re.sub(r'\*([^*]+?)\*', r'<em>\1</em>', escaped)
                    formatted_parts.append(escaped)
            formatted_fn_body = "".join(formatted_parts)
            html_parts.append(
                f'<aside class="footnote" id="fn-ch{active_ch}-{fn_num}">'
                f'<a href="#fnref-ch{active_ch}-{fn_num}" class="fn-num">({fn_num})</a> {formatted_fn_body}</aside>'
            )
            i += 1
            continue

        # Footnote definition: [N] Footnote body
        bracket_fn_match = re.match(r'^\[(\d{1,4})\]\s*(.+)$', stripped)
        if bracket_fn_match and bracket_fn_match.group(1) in fn_map and fn_style.get(bracket_fn_match.group(1)) == "bracket":
            flush_paragraph()
            fn_num = bracket_fn_match.group(1)
            fn_body = bracket_fn_match.group(2)
            parts = re.split(r'(`[^`]+?`)', fn_body)
            formatted_parts = []
            for part in parts:
                if part.startswith('`') and part.endswith('`') and len(part) >= 2:
                    code_content = html.escape(part[1:-1])
                    formatted_parts.append(f"<code>{code_content}</code>")
                else:
                    escaped = html.escape(part, quote=False)
                    # Convert markdown links [text](url) (excluding images ![...])
                    escaped = re.sub(
                        r'(?<!\!)\[([^\]]+)\]\(([^)]+)\)',
                        r'<a href="\2">\1</a>',
                        escaped,
                    )
                    escaped = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', escaped)
                    escaped = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)
                    escaped = re.sub(r'\*([^*]+?)\*', r'<em>\1</em>', escaped)
                    formatted_parts.append(escaped)
            formatted_fn_body = "".join(formatted_parts)
            html_parts.append(
                f'<aside class="footnote" id="fn-ch{active_ch}-{fn_num}">'
                f'<a href="#fnref-ch{active_ch}-{fn_num}" class="fn-num">[{fn_num}]</a> {formatted_fn_body}</aside>'
            )
            i += 1
            continue

        # GFM Table: lines starting with | followed by a strict separator row
        if stripped.startswith("|") and (i + 1 < len(lines) and bool(GFM_TABLE_SEP_RE.match(lines[i+1].strip()))):
            flush_paragraph()
            table_lines = [stripped]
            i += 1
            while i < len(lines):
                next_l = lines[i].strip()
                if not next_l or not next_l.startswith("|"):
                    break
                table_lines.append(next_l)
                i += 1

            if len(table_lines) >= 2:
                def format_cell(cell_str: str) -> str:
                    escaped = html.escape(cell_str.strip(), quote=False)
                    escaped = re.sub(r"&lt;br\s*/?&gt;", "<br/>", escaped, flags=re.IGNORECASE)
                    c_parts = re.split(r'(`[^`]+?`)', escaped)
                    f_parts = []
                    for cp in c_parts:
                        if cp.startswith('`') and cp.endswith('`') and len(cp) >= 2:
                            f_parts.append(f"<code>{cp[1:-1]}</code>")
                        else:
                            p = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', cp)
                            p = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', p)
                            p = re.sub(r'\*([^*]+?)\*', r'<em>\1</em>', p)
                            p = p.replace(r"\|", "|")
                            f_parts.append(p)
                    return "".join(f_parts)

                header_cells = [c.strip() for c in re.split(r"(?<!\\)\|", table_lines[0].strip("|"))]
                sep_idx = 1 if bool(GFM_TABLE_SEP_RE.match(table_lines[1])) else -1
                body_start = 2 if sep_idx == 1 else 1

                th_html = "".join(f"<th>{format_cell(c)}</th>" for c in header_cells)
                thead_html = f"<thead><tr>{th_html}</tr></thead>"

                tbody_rows = []
                for row_line in table_lines[body_start:]:
                    row_cells = [c.strip() for c in re.split(r"(?<!\\)\|", row_line.strip("|"))]
                    td_html = "".join(f"<td>{format_cell(c)}</td>" for c in row_cells)
                    tbody_rows.append(f"<tr>{td_html}</tr>")
                tbody_html = f"<tbody>{''.join(tbody_rows)}</tbody>"

                html_parts.append(f"<table>{thead_html}{tbody_html}</table>")
                continue

        # Headings: #, ##, ###, ####
        heading_match = re.match(r'^(#{1,6})\s+(.*)$', stripped)
        if heading_match:
            flush_paragraph()
            level = len(heading_match.group(1))
            h_text = html.escape(heading_match.group(2))
            if add_heading_ids:
                h_slug = slugify(heading_match.group(2))
                ch_prefix = f"ch{active_ch:02d}-" if active_ch is not None else ""
                h_id = f"{ch_prefix}{h_slug}" if h_slug else f"{ch_prefix}heading-{len(seen_heading_ids)+1}"
                if h_id in seen_heading_ids:
                    h_id = f"{h_id}-{len(seen_heading_ids)+1}"
                seen_heading_ids.add(h_id)
                html_parts.append(f'<h{level} id="{h_id}">{h_text}</h{level}>')
            else:
                html_parts.append(f"<h{level}>{h_text}</h{level}>")
            i += 1
            continue

        # Blockquote: > text
        if stripped.startswith(">"):
            flush_paragraph()
            bq_text = html.escape(stripped.lstrip(">").strip())
            html_parts.append(f"<blockquote><p>{bq_text}</p></blockquote>")
            i += 1
            continue

        # Horizontal rule: --- or ***
        if re.match(r'^(-{3,}|\*{3,}|_{3,})$', stripped):
            flush_paragraph()
            html_parts.append("<hr/>")
            i += 1
            continue

        # Normal paragraph line
        para_lines.append(line)
        i += 1

    flush_paragraph()
    flush_code()

    return "\n".join(html_parts)


def generate_table_of_contents_html(
    headings_or_chapters: Any = None,
    title: str = "Mục Lục",
) -> str:
    """
    Builds a <section class="toc-section"> using CSS Paged Media leaders
    (.toc-item a::after { content: leader('.') " " target-counter(attr(href), page); })
    to resolve page numbers dynamically in WeasyPrint.
    """
    headings: List[Dict[str, Any]] = []

    if headings_or_chapters is not None:
        if isinstance(headings_or_chapters, str):
            headings = extract_headings_from_markdown(headings_or_chapters)
        elif isinstance(headings_or_chapters, list):
            for item in headings_or_chapters:
                if isinstance(item, dict):
                    if "raw_content" in item or "content" in item:
                        text = item.get("raw_content") or item.get("content", "")
                        ch_num = item.get("ch_num", 1)
                        headings.extend(extract_headings_from_markdown(text, ch_num=ch_num))
                    elif "title" in item or "text" in item or "name" in item:
                        h_title = item.get("title") or item.get("text") or item.get("name", "")
                        h_id = item.get("id") or item.get("href") or item.get("anchor") or slugify(h_title)
                        if h_id.startswith("#"):
                            h_id = h_id[1:]
                        level = item.get("level", 1)
                        headings.append({"title": h_title, "id": h_id, "level": level})

    toc_items_html = []
    for h in headings:
        h_title = html.escape(h.get("title", ""))
        h_id = html.escape(h.get("id", ""))
        level = h.get("level", 1)
        level_class = f"toc-item-level-{level}"
        toc_items_html.append(
            f'<li class="toc-item {level_class}"><a href="#{h_id}">{h_title}</a></li>'
        )

    toc_list_content = "\n".join(toc_items_html)

    return f"""<section class="toc-section" id="table-of-contents">
<style>
.toc-section {{
  page-break-before: always;
  page-break-after: always;
}}
.toc-title {{
  text-align: center;
  font-size: 20pt;
  margin-top: 35pt;
  margin-bottom: 25pt;
  bookmark-level: 1;
  -weasy-bookmark-level: 1;
  bookmark-label: "{title}";
  -weasy-bookmark-label: "{title}";
}}
.toc-list {{
  list-style: none;
  padding: 0;
  margin: 0;
}}
.toc-item {{
  margin-bottom: 8pt;
  line-height: 1.5;
}}
.toc-item-level-1 {{
  font-weight: bold;
  margin-top: 12pt;
}}
.toc-item-level-2 {{
  margin-left: 1.5em;
  font-weight: normal;
  font-size: 9.5pt;
}}
.toc-item a {{
  text-decoration: none;
  color: inherit;
  display: block;
}}
.toc-item a::after {{
  content: leader('.') " " target-counter(attr(href), page);
  float: right;
  font-weight: normal;
}}
</style>
<h1 class="toc-title">{html.escape(title)}</h1>
<ul class="toc-list">
{toc_list_content}
</ul>
</section>
"""


def generate_alphabetical_index_html(
    entries: Any = None,
    anchor_map: Optional[Dict[str, List[str]]] = None,
    run_dir: Optional[str] = None,
    title: str = "Bảng Tra Cứu Thuật Ngữ",
) -> str:
    """
    Builds a <section class="index-section"> at the back of the book with a
    multi-column layout (column-count: 2).
    Canonical terms are grouped strictly by English initial letter (A, B, C...)
    and formatted as [English Term] ([Vietnamese Canonical Term]) [page numbers]
    using target-counter(attr(href), page).
    """
    items: List[Dict[str, Any]] = []
    if entries is not None:
        if isinstance(entries, dict):
            if "terms" in entries or "names" in entries or "book_record" in entries:
                items = extract_terms_from_book_record(entries)
            else:
                for k, v in entries.items():
                    if isinstance(v, dict):
                        items.append({
                            "source": k,
                            "target": v.get("target", k),
                            "anchors": v.get("anchors"),
                        })
                    else:
                        items.append({"source": k, "target": str(v)})
        elif isinstance(entries, list):
            for it in entries:
                if isinstance(it, dict):
                    items.append(dict(it))
                elif isinstance(it, str):
                    items.append({"source": it, "target": it})
    elif run_dir:
        items = extract_terms_from_book_record(run_dir)

    if not items:
        return ""

    anchor_map = anchor_map or {}
    normalized = []
    for it in items:
        source = (it.get("source") or it.get("term") or it.get("en") or "").strip()
        if not source:
            continue
        target = (it.get("target") or it.get("gloss") or it.get("canonical") or it.get("vi") or source).strip()
        slug = slugify(source)
        anchors = it.get("anchors") or anchor_map.get(source) or [f"idx-{slug}-1"]
        normalized.append({
            "source": source,
            "target": target,
            "anchors": anchors,
        })

    if not normalized:
        return ""

    # Sort strictly by English source alphabetically
    normalized.sort(key=lambda x: x["source"].lower())

    # Group by initial letter
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for it in normalized:
        first_char = it["source"][:1].upper()
        if first_char and is_cjk_codepoint(ord(first_char)):
            letter = get_pinyin_initial(first_char)
        else:
            letter = first_char if ("A" <= first_char <= "Z") else "#"
        groups.setdefault(letter, []).append(it)

    sorted_letters = sorted([k for k in groups.keys() if k != "#"])
    if "#" in groups:
        sorted_letters.append("#")

    group_html_parts = []
    for letter in sorted_letters:
        entries_html = []
        for it in groups[letter]:
            en_term = html.escape(it["source"])
            vi_term = html.escape(it["target"])
            anchors = it["anchors"]
            page_links = [
                f'<a href="#{html.escape(a)}" class="index-page-link"></a>'
                for a in anchors
            ]
            pages_str = ", ".join(page_links)
            gloss_str = f' <span class="index-gloss">({vi_term})</span>' if vi_term else ""
            entries_html.append(
                f'<li class="index-item"><span class="index-term">{en_term}</span>{gloss_str} <span class="index-pages">{pages_str}</span></li>'
            )

        entries_joined = "\n".join(entries_html)
        group_html_parts.append(
            f'<div class="index-group">\n<h2 class="index-letter">{letter}</h2>\n<ul class="index-list">\n{entries_joined}\n</ul>\n</div>'
        )

    columns_content = "\n".join(group_html_parts)

    return f"""<section class="index-section" id="alphabetical-index">
<style>
.index-section {{
  page-break-before: always;
}}
.index-title {{
  text-align: center;
  font-size: 20pt;
  margin-top: 35pt;
  margin-bottom: 25pt;
  break-after: avoid;
  page-break-after: avoid;
  bookmark-level: 1;
  -weasy-bookmark-level: 1;
  bookmark-label: "{title}";
  -weasy-bookmark-label: "{title}";
}}
.index-columns {{
  column-count: 2;
  column-gap: 20pt;
}}
.index-group {{
  break-inside: avoid;
  margin-bottom: 12pt;
}}
.index-letter {{
  font-size: 13pt;
  font-weight: bold;
  border-bottom: 1px solid #ccc;
  margin-top: 10pt;
  margin-bottom: 6pt;
  page-break-after: avoid;
}}
.index-list {{
  list-style: none;
  padding: 0;
  margin: 0;
}}
.index-item {{
  font-size: 9pt;
  line-height: 1.4;
  margin-bottom: 4pt;
}}
.index-term {{
  font-weight: bold;
}}
.index-gloss {{
  color: #444;
}}
.index-pages {{
  margin-left: 4pt;
}}
.index-page-link {{
  text-decoration: none;
  color: inherit;
}}
.index-page-link::after {{
  content: target-counter(attr(href), page);
}}
</style>
<h1 class="index-title">{html.escape(title)}</h1>
<div class="index-columns">
{columns_content}
</div>
</section>
"""


def get_pdf_css() -> str:
    """Returns complete CSS styles for compiled book PDF including tables and footnotes."""
    return """
@page {
  size: A5;
  margin: 22mm 16mm 22mm 16mm;
  @top-right {
    content: string(chapter-title);
    font-family: "Times New Roman", "DejaVu Serif", "Noto Serif", serif;
    font-size: 8pt;
    font-style: italic;
    color: #555;
  }
  @bottom-center {
    content: counter(page);
    font-family: "Times New Roman", "DejaVu Serif", "Noto Serif", serif;
    font-size: 9pt;
    color: #444;
  }
}

@page :first {
  @top-right { content: none; }
  @bottom-center { content: none; }
}

body {
  font-family: "Times New Roman", "DejaVu Serif", "Noto Serif", serif;
  font-size: 10.5pt;
  line-height: 1.6;
  color: #1a1a1a;
  text-align: justify;
  text-justify: inter-word;
}

h1 {
  bookmark-level: 1;
  -weasy-bookmark-level: 1;
  bookmark-label: content();
  -weasy-bookmark-label: content();
  string-set: chapter-title content();
  page-break-before: always;
  font-size: 20pt;
  margin-top: 35pt;
  margin-bottom: 25pt;
  text-align: center;
  line-height: 1.3;
}

h2 {
  bookmark-level: 2;
  -weasy-bookmark-level: 2;
  bookmark-label: content();
  -weasy-bookmark-label: content();
  font-size: 13.5pt;
  margin-top: 18pt;
  margin-bottom: 8pt;
  page-break-after: avoid;
}

h3 {
  font-size: 11.5pt;
  margin-top: 14pt;
  margin-bottom: 6pt;
  page-break-after: avoid;
}

p {
  margin-top: 0;
  margin-bottom: 8pt;
  text-indent: 1.5em;
}

p:first-of-type, h1 + p, h2 + p, h3 + p {
  text-indent: 0;
}

pre {
  background-color: #f7f7f8;
  border: 1px solid #e1e4e8;
  border-radius: 4px;
  padding: 8pt 10pt;
  font-family: "Ubuntu Mono", "Courier New", Courier, monospace;
  font-size: 8.5pt;
  line-height: 1.4;
  overflow-x: auto;
  page-break-inside: avoid;
  margin: 10pt 0;
  text-indent: 0;
}

code {
  font-family: "Ubuntu Mono", "Courier New", Courier, monospace;
  font-size: 9pt;
  background-color: #f3f4f4;
  padding: 1pt 3pt;
  border-radius: 2px;
}

pre code {
  background-color: transparent;
  padding: 0;
}

figure {
  margin: 14pt auto;
  text-align: center;
  page-break-inside: avoid;
}

figure img {
  max-width: 95%;
  height: auto;
}

figcaption {
  font-size: 8pt;
  color: #555;
  margin-top: 5pt;
  font-style: italic;
}

blockquote {
  margin: 10pt 0 10pt 15pt;
  padding-left: 10pt;
  border-left: 3px solid #b4b4b4;
  color: #333;
  font-style: italic;
}

hr {
  border: 0;
  border-top: 1px solid #ccc;
  margin: 20pt auto;
  width: 50%;
}

.toc-item a::after {
  content: leader('.') " " target-counter(attr(href), page);
}

.index-page-link::after {
  content: target-counter(attr(href), page);
}

/* Tables (REQ-007) */
table {
  width: 100%;
  border-collapse: collapse;
  margin: 12pt 0;
  font-size: 9pt;
  page-break-inside: avoid;
}

th, td {
  border: 1px solid #d0d7de;
  padding: 6pt 8pt;
  text-align: left;
}

th {
  background-color: #f6f8fa;
  font-weight: bold;
}

/* Footnotes (REQ-008) */
.footnote {
  font-size: 8pt;
  line-height: 1.4;
  color: #444;
  margin-top: 6pt;
  margin-bottom: 4pt;
  padding-top: 4pt;
  border-top: 0.5pt solid #ddd;
  text-indent: 0;
}

.footnote .fn-num, .footnote .footnote-num {
  font-weight: bold;
  margin-right: 4pt;
}

sup.footnote-ref {
  font-size: 7.5pt;
  line-height: 0;
  vertical-align: super;
}

sup.footnote-ref a {
  color: #0366d6;
  text-decoration: none;
}
"""


def build_full_html(
    chapters_html: List[Dict[str, Any]],
    title: str = "Sách Dịch",
    toc_html: Optional[str] = None,
    index_html: Optional[str] = None,
    author: str = "",
) -> str:
    """Wraps converted chapters in a complete HTML document with book CSS styling, TOC, and Index."""
    css_styles = get_pdf_css()
    body_content = []
    if toc_html:
        body_content.append(toc_html)

    for ch in chapters_html:
        body_content.append(f'<div class="chapter" id="chapter-{ch["ch_num"]}">')
        body_content.append(ch["html"])
        body_content.append('</div>')

    if index_html:
        body_content.append(index_html)

    author_meta = f'<meta name="author" content="{html.escape(author)}"/>\n' if author else ""
    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
{author_meta}<style>
{css_styles}
</style>
</head>
<body>
{"".join(body_content)}
</body>
</html>
"""


def _collect_anchor_pages(weasy_doc: Any, page_offset: int) -> Dict[str, int]:
    """
    Collects 1-based global page numbers for all element IDs in a rendered WeasyPrint document.
    """
    anchor_pages: Dict[str, int] = {}
    for page_idx, page in enumerate(weasy_doc.pages):
        global_page = page_offset + page_idx + 1
        for d in page._page_box.descendants():
            elem = getattr(d, "element", None)
            if elem is not None and hasattr(elem, "attrib"):
                eid = elem.attrib.get("id")
                if eid and eid not in anchor_pages:
                    anchor_pages[eid] = global_page
    return anchor_pages


def compile_book(
    run_dir: str,
    output_pdf: str,
    chapters_filter: Optional[set] = None,
    title: Optional[str] = None,
    include_toc: bool = True,
    include_index: bool = True,
    author: Optional[str] = None,
    chunk_size: int = 30,
    allow_incomplete: bool = False,
) -> str:
    """
    Compiles all chapters in run_dir into output_pdf.
    Reads ch<NN>/final.txt (or fallback to ch<NN>/source.txt).
    Generates Table of Contents (Mục Lục) and Alphabetical Term Index (Bảng Tra Cứu Thuật Ngữ) if enabled.
    Supports memory-bounded chunked compilation for books with > chunk_size chapters (REQ-012).
    """
    run_path = os.path.abspath(run_dir)
    if not os.path.isdir(run_path):
        raise FileNotFoundError(f"Run directory not found: {run_path}")

    # Discover chapter directories: ch01, ch02, ...
    ch_dirs = []
    for item in os.listdir(run_path):
        subpath = os.path.join(run_path, item)
        if os.path.isdir(subpath):
            m = re.match(r'^ch(\d+)$', item)
            if m:
                ch_num = int(m.group(1))
                ch_dirs.append((ch_num, subpath))

    ch_dirs.sort(key=lambda x: x[0])
    if chapters_filter:
        ch_dirs = [x for x in ch_dirs if x[0] in chapters_filter]

    if not ch_dirs:
        raise ValueError(f"No chapter directories found in: {run_path}")

    print(f"Found {len(ch_dirs)} chapter(s) in {run_path}")

    # Load book record metadata if available to resolve title and author
    br_path = os.path.join(run_path, "book_record.json")
    br_data: Dict[str, Any] = {}
    if os.path.isfile(br_path):
        try:
            with open(br_path, "r", encoding="utf-8") as f:
                br_data = json.load(f)
        except Exception:
            br_data = {}

    br_meta = br_data.get("book_record", {}) if isinstance(br_data, dict) else {}

    book_title = (
        title
        or (br_data.get("title_vietnamese") if isinstance(br_data, dict) else None)
        or (br_data.get("title") if isinstance(br_data, dict) else None)
        or (br_meta.get("title_vietnamese") if isinstance(br_meta, dict) else None)
        or (br_meta.get("title") if isinstance(br_meta, dict) else None)
        or os.path.basename(run_path).replace("_", " ").title()
    )

    book_author = (
        author
        or (br_data.get("author") if isinstance(br_data, dict) else None)
        or (br_meta.get("author") if isinstance(br_meta, dict) else None)
        or ""
    )

    terms = extract_terms_from_book_record(run_path) if include_index else []
    all_headings: List[Dict[str, Any]] = []
    anchor_tracker: Dict[str, List[str]] = {}
    chapters_html = []

    for ch_num, ch_path in ch_dirs:
        final_txt = os.path.join(ch_path, "final.txt")
        source_txt = os.path.join(ch_path, "source.txt")

        if os.path.isfile(final_txt):
            target_file = final_txt
            print(f"Chapter {ch_num:02d}: using final.txt")
        elif allow_incomplete and os.path.isfile(source_txt):
            target_file = source_txt
            print(f"Chapter {ch_num:02d}: final.txt not found, falling back to source.txt")
        elif not allow_incomplete:
            raise FileNotFoundError(f"Chapter {ch_num:02d}: final.txt not found in {ch_path} and allow_incomplete is False")
        else:
            print(f"Chapter {ch_num:02d}: Neither final.txt nor source.txt found, skipping.")
            continue

        with open(target_file, "r", encoding="utf-8") as f:
            raw_content = f.read()

        if include_toc:
            ch_headings = extract_headings_from_markdown(raw_content, ch_num=ch_num)
            all_headings.extend(ch_headings)

        ch_html = markdown_to_html(
            raw_content,
            run_path,
            ch_path,
            ch_num=ch_num,
            terms=terms if include_index else None,
            anchor_tracker=anchor_tracker if include_index else None,
            add_heading_ids=True,
        )
        chapters_html.append({
            "ch_num": ch_num,
            "html": ch_html,
        })

    toc_html = generate_table_of_contents_html(all_headings) if (include_toc and all_headings) else None
    index_html = generate_alphabetical_index_html(terms, anchor_map=anchor_tracker) if (include_index and terms) else None

    out_pdf_path = os.path.abspath(output_pdf)
    os.makedirs(os.path.dirname(os.path.abspath(out_pdf_path)), exist_ok=True)

    if weasyprint is None:
        raise RuntimeError("weasyprint is required for PDF compilation")

    fetcher = build_safe_url_fetcher([Path(run_path) / "images", Path(run_path)])

    # Single-pass execution for small books (AC-012.2)
    if chunk_size <= 0 or len(chapters_html) <= chunk_size:
        full_html = build_full_html(
            chapters_html,
            title=book_title,
            toc_html=toc_html,
            index_html=index_html,
            author=book_author,
        )
        print(f"Rendering PDF with WeasyPrint -> {out_pdf_path}...")
        doc = weasyprint.HTML(string=full_html, base_url=run_path, url_fetcher=fetcher)
        doc.write_pdf(out_pdf_path)

        pdf_size = os.path.getsize(out_pdf_path)
        print(f"Successfully compiled PDF: {out_pdf_path} ({pdf_size} bytes)")
        return out_pdf_path

    # Zero-Delta Two-Pass Chunked WeasyPrint & PyMuPDF Pipeline (AC-012.1)
    if fitz is None:
        raise RuntimeError("pymupdf (fitz) is required for chunked PDF compilation")

    print(f"Rendering chunked PDF ({len(chapters_html)} chapters, chunk_size={chunk_size}) -> {out_pdf_path}...")

    with tempfile.TemporaryDirectory() as tmp_work_dir:
        # Step 1: Exact Front-Matter Page Count
        front_pages = 0
        if toc_html:
            front_full_html = build_full_html([], title=book_title, toc_html=toc_html, author=book_author)
            front_doc = weasyprint.HTML(string=front_full_html, base_url=run_path, url_fetcher=fetcher).render()
            front_pages = len(front_doc.pages)
            del front_doc
            gc.collect()

        # Step 2: Sequential Body Chunks
        chunks = [chapters_html[i:i + chunk_size] for i in range(0, len(chapters_html), chunk_size)]

        while True:
            current_page_offset = front_pages
            global_anchor_pages: Dict[str, int] = {}
            chunk_pdf_paths: List[str] = []
            chunk_tocs: List[Tuple[int, List[Any]]] = []

            for chunk_idx, chunk in enumerate(chunks):
                override_css = ""
                if current_page_offset > 0:
                    override_css = f"""
                    @page :first {{
                      counter-reset: page {current_page_offset + 1};
                      @top-right {{ content: string(chapter-title); }}
                      @bottom-center {{ content: counter(page); }}
                    }}
                    """
                chunk_html = build_full_html(chunk, title=book_title, toc_html=None, index_html=None, author=book_author)
                if override_css:
                    chunk_html = chunk_html.replace("</style>", f"{override_css}\n</style>")

                chunk_doc = weasyprint.HTML(string=chunk_html, base_url=run_path, url_fetcher=fetcher).render()
                anchors = _collect_anchor_pages(chunk_doc, current_page_offset)
                global_anchor_pages.update(anchors)

                chunk_pdf = os.path.join(tmp_work_dir, f"chunk_{chunk_idx}.pdf")
                chunk_doc.write_pdf(chunk_pdf)
                chunk_pdf_paths.append(chunk_pdf)

                cdoc = fitz.open(chunk_pdf)
                chunk_tocs.append((current_page_offset, cdoc.get_toc()))
                cdoc.close()

                current_page_offset += len(chunk_doc.pages)
                del chunk_doc
                gc.collect()

            # Step 3: Resolved Front-Matter & Back-Matter Index
            front_pdf: Optional[str] = None
            front_toc: List[Any] = []
            if toc_html:
                resolved_toc_html = re.sub(r'href="#([a-zA-Z0-9_\-]+)"', r'href="http://internal.local/#\1"', toc_html)
                toc_overrides = []
                for hid in [h["id"] for h in all_headings]:
                    pnum = global_anchor_pages.get(hid)
                    if pnum is not None:
                        toc_overrides.append(f'.toc-item a[href="http://internal.local/#{hid}"]::after {{ content: leader(".") " " "{pnum}" !important; }}')

                resolved_toc_style = "<style>\n" + "\n".join(toc_overrides) + "\n</style>"
                final_front_html = build_full_html([], title=book_title, toc_html=resolved_toc_style + resolved_toc_html, author=book_author)
                final_front_doc = weasyprint.HTML(string=final_front_html, base_url=run_path, url_fetcher=fetcher).render()

                actual_front_pages = len(final_front_doc.pages)
                if actual_front_pages != front_pages:
                    front_pages = actual_front_pages
                    del final_front_doc
                    gc.collect()
                    continue

                front_pdf = os.path.join(tmp_work_dir, "front.pdf")
                final_front_doc.write_pdf(front_pdf)
                fdoc = fitz.open(front_pdf)
                front_toc = fdoc.get_toc()
                fdoc.close()
                del final_front_doc
                gc.collect()

            break

        index_pdf: Optional[str] = None
        index_toc: List[Any] = []
        index_offset = current_page_offset
        if index_html:
            resolved_index_html = re.sub(r'href="#([a-zA-Z0-9_\-]+)"', r'href="http://internal.local/#\1"', index_html)
            index_overrides = []
            for aid, pnum in global_anchor_pages.items():
                if aid.startswith("idx-"):
                    index_overrides.append(f'.index-page-link[href="http://internal.local/#{aid}"]::after {{ content: "{pnum}" !important; }}')

            index_page_css = f"""
            @page :first {{
              counter-reset: page {current_page_offset + 1};
              @top-right {{ content: string(chapter-title); }}
              @bottom-center {{ content: counter(page); }}
            }}
            """
            resolved_index_style = "<style>\n" + index_page_css + "\n" + "\n".join(index_overrides) + "\n</style>"
            final_index_html = build_full_html([], title=book_title, index_html=resolved_index_style + resolved_index_html, author=book_author)
            final_index_doc = weasyprint.HTML(string=final_index_html, base_url=run_path, url_fetcher=fetcher).render()

            index_pdf = os.path.join(tmp_work_dir, "index.pdf")
            final_index_doc.write_pdf(index_pdf)
            idoc = fitz.open(index_pdf)
            index_toc = idoc.get_toc()
            idoc.close()
            current_page_offset += len(final_index_doc.pages)
            del final_index_doc
            gc.collect()

        # Step 4: PyMuPDF Merge & Bookmarks
        merged_doc = fitz.open()

        if front_pdf:
            fdoc = fitz.open(front_pdf)
            merged_doc.insert_pdf(fdoc)
            fdoc.close()

        for cpdf in chunk_pdf_paths:
            cdoc = fitz.open(cpdf)
            chunk_start_page = len(merged_doc)
            merged_doc.insert_pdf(cdoc)
            for src_pno in range(len(cdoc)):
                cpage = cdoc[src_pno]
                target_pno = chunk_start_page + src_pno
                tpage = merged_doc[target_pno]
                for l in cpage.get_links():
                    if l.get("kind") in (fitz.LINK_GOTO, fitz.LINK_NAMED):
                        if "page" in l and l["page"] >= 0:
                            tpage.insert_link({
                                "kind": fitz.LINK_GOTO,
                                "from": l["from"],
                                "page": chunk_start_page + l["page"],
                                "to": l.get("to", fitz.Point(0, 0)),
                            })
                    elif l.get("kind") == fitz.LINK_URI:
                        tpage.insert_link(l)
            cdoc.close()

        if index_pdf:
            idoc = fitz.open(index_pdf)
            index_start_page = len(merged_doc)
            merged_doc.insert_pdf(idoc)
            for src_pno in range(len(idoc)):
                ipage = idoc[src_pno]
                target_pno = index_start_page + src_pno
                tpage = merged_doc[target_pno]
                for l in ipage.get_links():
                    if l.get("kind") in (fitz.LINK_GOTO, fitz.LINK_NAMED):
                        if "page" in l and l["page"] >= 0:
                            tpage.insert_link({
                                "kind": fitz.LINK_GOTO,
                                "from": l["from"],
                                "page": index_start_page + l["page"],
                                "to": l.get("to", fitz.Point(0, 0)),
                            })
                    elif l.get("kind") == fitz.LINK_URI:
                        tpage.insert_link(l)
            idoc.close()

        for pidx in range(len(merged_doc)):
            page = merged_doc[pidx]
            for l in list(page.get_links()):
                dest = None
                uri = l.get("uri", "")
                if uri.startswith("http://internal.local/#"):
                    dest = uri.split("#", 1)[1]
                elif l.get("nameddest"):
                    dest = l.get("nameddest")

                if dest and dest in global_anchor_pages:
                    target_page_pno = global_anchor_pages[dest] - 1
                    page.delete_link(l)
                    page.insert_link({
                        "kind": fitz.LINK_GOTO,
                        "from": l["from"],
                        "page": target_page_pno,
                    })

        global_toc: List[Any] = []
        for item in front_toc:
            global_toc.append([item[0], item[1], item[2]])
        for off, ctoc in chunk_tocs:
            for item in ctoc:
                global_toc.append([item[0], item[1], off + item[2]])
        for item in index_toc:
            global_toc.append([item[0], item[1], index_offset + item[2]])

        if global_toc:
            merged_doc.set_toc(global_toc)

        merged_doc.save(out_pdf_path)
        merged_doc.close()

        pdf_size = os.path.getsize(out_pdf_path)
        print(f"Successfully compiled chunked PDF: {out_pdf_path} ({pdf_size} bytes)")
        return out_pdf_path


compile_pdf = compile_book


def main():
    parser = argparse.ArgumentParser(description="Compile translated chapters to a styled PDF.")
    parser.add_argument("--run-dir", required=True, help="Path to book run workspace (e.g. runs/rust_atomics_and_locks)")
    parser.add_argument("--output-pdf", default=None, help="Destination PDF file path")
    parser.add_argument("--formats", default=None, help="Target formats, comma-separated (e.g. 'epub,pdf')")
    parser.add_argument("--chapters", default=None, help="Filter chapters to compile, e.g. '1-3' or '1'")
    parser.add_argument("--title", default=None, help="Book title for document metadata")
    parser.add_argument("--toc", action=argparse.BooleanOptionalAction, default=True, help="Generate Table of Contents (default: True)")
    parser.add_argument("--index", action=argparse.BooleanOptionalAction, default=True, help="Generate Alphabetical Index (default: True)")
    parser.add_argument("--chunk-size", type=int, default=30, help="Max chapters per compilation chunk (default: 30)")
    parser.add_argument("--allow-incomplete", action="store_true", default=False, help="Allow chapters with missing final.txt")

    args = parser.parse_args()

    if args.formats:
        from scripts.compilers.docx_compiler import DOCXCompiler
        from scripts.compilers.epub_compiler import EPUBCompiler
        from scripts.compilers.web_compiler import WebCompiler

        registry = {
            "epub": EPUBCompiler,
            "docx": DOCXCompiler,
            "web": WebCompiler,
        }
        try:
            run_dir = Path(args.run_dir).resolve()
            output_dir = run_dir
            slug = run_dir.name
            format_list = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
            for fmt in format_list:
                if fmt == "pdf":
                    output_file = output_dir / f"{slug}_vietnamese.pdf"
                    print(f"Compiling [PDF] deliverable -> {output_file.name}...")
                    ch_filter = parse_chapters_filter(args.chapters)
                    compile_book(
                        run_dir=str(run_dir),
                        output_pdf=str(output_file),
                        chapters_filter=ch_filter,
                        title=args.title,
                        include_toc=args.toc,
                        include_index=args.index,
                        allow_incomplete=args.allow_incomplete,
                    )
                    continue
                compiler_cls = registry.get(fmt)
                if not compiler_cls:
                    supported = ", ".join(sorted(list(registry.keys()) + ["pdf"]))
                    raise ValueError(f"Unsupported format '{fmt}'. Supported formats: {supported}")
                compiler = compiler_cls()
                if fmt == "web":
                    output_file = output_dir / f"{slug}_vietnamese.web.zip"
                else:
                    output_file = output_dir / f"{slug}_vietnamese.{fmt}"
                print(f"Compiling [{fmt.upper()}] deliverable -> {output_file.name}...")
                compiler.compile(
                    run_dir=run_dir,
                    output_file=output_file,
                    title=args.title,
                    include_toc=args.toc,
                    include_index=args.index,
                    chapters=args.chapters,
                    allow_incomplete=args.allow_incomplete,
                )
            return
        except Exception as e:
            sys.stderr.write(f"Compilation error: {e}\n")
            sys.exit(1)

    out_pdf = args.output_pdf
    if not out_pdf:
        slug = Path(args.run_dir).resolve().name
        out_pdf = str(Path(args.run_dir) / f"{slug}_vietnamese.pdf")

    ch_filter = parse_chapters_filter(args.chapters)

    try:
        compile_book(
            args.run_dir,
            out_pdf,
            chapters_filter=ch_filter,
            title=args.title,
            include_toc=args.toc,
            include_index=args.index,
            chunk_size=args.chunk_size,
            allow_incomplete=args.allow_incomplete,
        )
    except Exception as e:
        sys.stderr.write(f"Compilation error: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
