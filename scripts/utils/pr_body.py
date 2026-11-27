"""Shared PR body builders for collaborative book translation workflows."""

from __future__ import annotations


def build_translation_pr_body(
    *,
    agent_login: str,
    engine_model: str,
    source_lang: str,
    target_lang: str,
    book_slug: str,
    ch_name: str,
    seg_first: str,
    seg_last: str,
    issue_ref: str,
    glossary_sha: str,
) -> str:
    """Construct a deterministic REQ-021 compliant translation PR body."""
    seg_range = seg_first if seg_first == seg_last else f"{seg_first}..{seg_last}"
    return (
        f"- **Agent:** {agent_login}\n"
        f"- **Engine/Model:** {engine_model}\n"
        f"- **Source language:** {source_lang}\n"
        f"- **Target language:** {target_lang}\n"
        f"- **Book:** {book_slug}\n"
        f"- **Chunk ID:** {book_slug}-{ch_name}\n"
        f"- **Segments:** {seg_range}\n"
        f"- **Issue:** {issue_ref}\n"
        f"- **Glossary SHA:** {glossary_sha}\n"
    )
