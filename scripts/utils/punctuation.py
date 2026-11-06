"""scripts/utils/punctuation.py — Segment-scoped CJK punctuation normalization utilities."""

import re

INLINE_PROTECTED_RE = re.compile(r"(`+[^`]+`+|https?://[^\s\u3000-\u303F\uFF00-\uFFEF\u4E00-\u9FFF]+)")
FENCED_CODE_RE = re.compile(r"```[\s\S]*?```")

PUNCTUATION_MAP = {
    "，": ", ",
    "。": ". ",
    "！": "! ",
    "？": "? ",
    "：": ": ",
    "；": "; ",
    "（": " (",
    "）": ") ",
    "【": " [",
    "】": "] ",
    "《": '"',
    "》": '"',
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
    "「": '"',
    "」": '"',
    "『": '"',
    "』": '"',
    "、": ", ",
}


def normalize_cjk_punctuation_in_prose(text: str) -> str:
    """Normalizes CJK full-width punctuation marks to standard Latin/Vietnamese typography.

    Shields fenced code blocks (```...```), inline code (`...`), and URLs (https?://...)
    from alteration and eliminates redundant whitespace around punctuation.
    """
    stashed: list[str] = []

    def _stash(m: re.Match) -> str:
        stashed.append(m.group(0))
        return f"__PROTECTED_SPAN_{len(stashed) - 1}__"

    shielded = FENCED_CODE_RE.sub(_stash, text)
    shielded = INLINE_PROTECTED_RE.sub(_stash, shielded)

    for cjk_char, latin_char in PUNCTUATION_MAP.items():
        shielded = shielded.replace(cjk_char, latin_char)
    shielded = re.sub(r" +([,.:;!?])", r"\1", shielded)
    shielded = re.sub(r" {2,}", " ", shielded)

    for idx, orig in enumerate(stashed):
        shielded = shielded.replace(f"__PROTECTED_SPAN_{idx}__", orig)
    return shielded


def normalize_cjk_punctuation_in_xml(xml_content: str) -> str:
    """Applies punctuation normalization strictly inside <translate> of heading and paragraph segments.

    Leaves code, figure, and non-translatable segments completely byte-identical.
    """
    def _replace_segment(m: re.Match) -> str:
        seg_tag, inner = m.group(1), m.group(2)
        if (
            'type="heading"' in seg_tag
            or 'type="paragraph"' in seg_tag
            or "type='heading'" in seg_tag
            or "type='paragraph'" in seg_tag
        ):
            def _replace_translate(tm: re.Match) -> str:
                return f"<translate>{normalize_cjk_punctuation_in_prose(tm.group(1))}</translate>"

            inner = re.sub(r"<translate>([\s\S]*?)</translate>", _replace_translate, inner)
        return f"{seg_tag}{inner}</segment>"

    return re.sub(r"(<segment\b[^>]*>)([\s\S]*?)</segment>", _replace_segment, xml_content)


# Backward-compatible alias for existing callers
normalize_cjk_punctuation = normalize_cjk_punctuation_in_prose


def ensure_footnote_numbering(orig_text: str, trans_text: str) -> str:
    """Ensures translated text preserves the leading footnote marker (e.g. '(16) ') from orig_text."""
    if not orig_text or not trans_text:
        return trans_text
    m_fn = re.match(r"^(\(\d+\))\s*", orig_text)
    if not m_fn:
        return trans_text
    fn_marker = m_fn.group(1)
    m_trans = re.match(r"^(?:\(\d+\)|\d+[\.:\)])\s*", trans_text)
    if m_trans:
        return f"{fn_marker} {trans_text[m_trans.end():].lstrip()}"
    if not trans_text.startswith(fn_marker):
        return f"{fn_marker} {trans_text.lstrip()}"
    return trans_text

