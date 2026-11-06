#!/usr/bin/env python3
"""
scripts/compilers/web_compiler.py — Universal Portable Web Package Compiler.
Compiles translated books from runs/<slug>/ into BookPackage v1.0 (.web.zip)
deliverables containing manifest.json, glossary.json, chapters/chNN.json, and images/.
"""

import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from scripts.compilers.base import BaseCompiler
from scripts.compilers.book_package_schema import (
    SCHEMA_VERSION,
    build_book_profile,
    classify_segment_blocks,
    extract_chapter_footnotes,
    normalize_book_metadata_and_glossary,
    validate_chapter_document,
    validate_glossary,
    validate_manifest,
)
from scripts.helper import parse_segments
from scripts.utils.hanviet import transcribe_to_hanviet


class WebCompiler(BaseCompiler):
    """
    Compiler that generates self-contained BookPackage v1.0 (.web.zip)
    packages for the Interactive Book Web Reader.
    """

    def compile(
        self,
        run_dir: Path,
        output_file: Optional[Path] = None,
        title: Optional[str] = None,
        author: Optional[str] = None,
        include_toc: bool = True,
        include_index: bool = True,
        chapters: Optional[str] = None,
        **kwargs,
    ) -> Path:
        """
        Compiles the translated book in run_dir into a validated BookPackage v1.0 (.web.zip).
        Returns the Path to the compiled .web.zip archive.
        """
        run_dir = Path(run_dir).resolve()
        if not run_dir.is_dir():
            raise FileNotFoundError(f"Run directory not found: {run_dir}")

        slug = run_dir.name
        if not output_file:
            output_file = run_dir / f"{slug}_vietnamese.web.zip"
        output_file = Path(output_file).resolve()
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # 1. Normalize metadata and glossary
        profile_override = kwargs.get("profile_override")
        if profile_override is None:
            web_profile_path = run_dir / "web_profile.json"
            if web_profile_path.is_file():
                try:
                    with open(web_profile_path, "r", encoding="utf-8") as f:
                        profile_override = json.load(f)
                except Exception:
                    pass

        metadata, glossary = normalize_book_metadata_and_glossary(
            run_dir, profile_override=profile_override
        )
        if title:
            metadata["title"] = title
        if author:
            metadata["author"] = author

        book_record = self.load_book_record(run_dir)
        source_lang = metadata.get("source_lang", "en")
        book_profile = metadata["book_profile"]

        # 2. Discover checklist scores & metadata
        checklist_map: Dict[int, Dict[str, Any]] = {}
        checklist_path = run_dir / "checklist.json"
        if checklist_path.is_file():
            try:
                with open(checklist_path, "r", encoding="utf-8") as f:
                    cl = json.load(f)
                    for item in cl:
                        num = item.get("chapter_num")
                        if num is not None:
                            checklist_map[int(num)] = item
            except Exception:
                pass

        # 3. Discover chapters
        ch_filter = self.parse_chapters_filter(chapters)
        discovered = self.discover_chapters(run_dir, ch_filter)
        if not discovered:
            raise RuntimeError(f"No chapters found to compile in {run_dir}")

        # 4. Prepare images map: {zip_path: local_file_path}
        image_files: Dict[str, Path] = {}
        images_dir = run_dir / "images"
        if images_dir.is_dir():
            for img_p in sorted(images_dir.rglob("*")):
                if img_p.is_file() and not img_p.name.startswith("."):
                    rel = img_p.relative_to(run_dir)
                    image_files[str(rel).replace("\\", "/")] = img_p

        # Check cover image
        for ext in ("webp", "png", "jpg", "jpeg", "svg"):
            cov = images_dir / f"cover.{ext}"
            if cov.is_file():
                metadata["cover_image"] = f"images/cover.{ext}"
                image_files[f"images/cover.{ext}"] = cov
                break

        # 5. Process chapters and build chapter documents
        chapter_summaries: List[Dict[str, Any]] = []
        chapter_documents: List[Dict[str, Any]] = []
        scores: List[float] = []

        for ch_info in discovered:
            ch_num = int(ch_info["ch_num"])
            ch_id = f"ch{ch_num:02d}"
            ch_dir = ch_info.get("dir") or ch_info.get("ch_dir") or (run_dir / f"ch{ch_num:02d}")

            cl_entry = checklist_map.get(ch_num, {})
            score = cl_entry.get("score")
            if score is not None:
                try:
                    scores.append(float(score))
                except (ValueError, TypeError):
                    pass

            # Detect chapter banner image
            banner_rel_path: Optional[str] = None
            for ext in ("webp", "png", "jpg", "jpeg", "svg"):
                cand1 = images_dir / f"ch{ch_num:02d}_banner.{ext}"
                cand2 = ch_dir / f"banner.{ext}"
                if cand1.is_file():
                    banner_rel_path = f"images/ch{ch_num:02d}_banner.{ext}"
                    image_files[banner_rel_path] = cand1
                    break
                elif cand2.is_file():
                    banner_rel_path = f"images/ch{ch_num:02d}_banner.{ext}"
                    image_files[banner_rel_path] = cand2
                    break

            # Find matching group_id in toc_groups
            group_id: Optional[str] = None
            for g in book_profile.get("toc_groups", []):
                if ch_id in g.get("chapter_ids", []):
                    group_id = g["id"]
                    break

            def _round_key(p: Path) -> int:
                m = re.search(r'round(\d+)', p.name)
                return int(m.group(1)) if m else 0

            # Parse XML segments from highest round file or source (R2-P2-08)
            raw_segments: List[Dict[str, Any]] = []
            cand_xml_files = sorted(ch_dir.glob("round*_refine.txt"), key=_round_key)
            if not cand_xml_files:
                cand_xml_files = sorted(ch_dir.glob("round*_translate.txt"), key=_round_key)
            if not cand_xml_files:
                source_file = ch_dir / "source.txt"
                if source_file.is_file():
                    cand_xml_files = [source_file]

            xml_content = ""
            if cand_xml_files:
                xml_content = cand_xml_files[-1].read_text(encoding="utf-8")
                raw_segments = parse_segments(xml_content)

            final_file = ch_dir / "final.txt"
            final_content = ""
            if final_file.is_file():
                final_content = final_file.read_text(encoding="utf-8")

            # Extract footnotes
            combined_text = f"{final_content}\n\n{xml_content}"
            footnotes = extract_chapter_footnotes(combined_text)

            # Build utterance_lookup if dramatic_script.json or audio manifest exists
            utterance_lookup: Dict[Any, Dict[str, Any]] = {}
            quote_counters: Dict[str, int] = {}
            script_file = ch_dir / "dramatic_script.json"
            manifest_file = run_dir / "audio" / f"{ch_id}.manifest.json"
            utts_source: List[Dict[str, Any]] = []
            if script_file.is_file():
                try:
                    with open(script_file, "r", encoding="utf-8") as f:
                        utts_source = json.load(f).get("utterances", [])
                except Exception:
                    utts_source = []
            elif manifest_file.is_file():
                try:
                    with open(manifest_file, "r", encoding="utf-8") as f:
                        utts_source = json.load(f).get("utterances", [])
                except Exception:
                    utts_source = []

            for u in utts_source:
                uid = u.get("utt_id")
                if uid:
                    utterance_lookup[uid] = u
                sid = u.get("seg_id") or u.get("segment_id")
                if sid and u.get("role") == "character":
                    quote_counters[sid] = quote_counters.get(sid, 0) + 1
                    utterance_lookup[(sid, quote_counters[sid])] = u

            # Reconcile segments with final.txt
            segments: List[Dict[str, Any]] = []
            final_paragraphs = [p.strip() for p in final_content.split("\n\n") if p.strip()]

            if raw_segments:
                for idx, rseg in enumerate(raw_segments):
                    seg_id = rseg.get("id", f"S-{idx + 1:04d}")
                    if not re.match(r'^S-\d{4,}$', seg_id):
                        seg_id = f"S-{idx + 1:04d}"

                    seg_type = rseg.get("type", "paragraph")
                    original = rseg.get("original", "").strip()
                    translate = rseg.get("translate")
                    if translate is None:
                        # Fallback to final_paragraphs if length aligns or original
                        if idx < len(final_paragraphs) and "<segment" not in final_content:
                            translate = final_paragraphs[idx]
                        else:
                            translate = original
                    translate = translate.strip()

                    # Strip footnote definition section if embedded inside segment translate
                    fn_cut = translate.find("### Chú thích:")
                    if fn_cut == -1:
                        fn_cut = translate.find("## Chú thích:")
                    if fn_cut != -1:
                        translate = translate[:fn_cut].rstrip()

                    # Transcribe Hán-Việt if zh
                    hanviet = rseg.get("hanviet")
                    if not hanviet and source_lang == "zh":
                        hanviet = transcribe_to_hanviet(original, book_record=book_record)

                    # Match terms
                    matched: List[str] = []
                    for entry in glossary.get("entries", []):
                        tgt = entry.get("target")
                        src = entry.get("source")
                        if tgt and (tgt in translate):
                            matched.append(tgt)
                        elif src and (src in original):
                            matched.append(tgt or src)
                    matched_unique = sorted(list(set(matched)))

                    blocks = classify_segment_blocks(
                        text=translate,
                        seg_type=seg_type,
                        terms=glossary.get("entries", []),
                        footnotes_map=footnotes,
                        seg_id=seg_id,
                        source_lang=source_lang,
                        utterance_lookup=utterance_lookup,
                    )

                    segments.append({
                        "seg_id": seg_id,
                        "seg_order": idx + 1,
                        "seg_type": seg_type,
                        "original": original,
                        "hanviet": hanviet,
                        "translate": translate,
                        "matched_terms": matched_unique,
                        "blocks": blocks,
                    })
            else:
                # If no segment XML existed, synthesize segments from final.txt
                for idx, para in enumerate(final_paragraphs):
                    seg_id = f"S-{idx + 1:04d}"
                    seg_type = "heading" if para.startswith("#") else "paragraph"
                    fn_cut = para.find("### Chú thích:")
                    if fn_cut == -1:
                        fn_cut = para.find("## Chú thích:")
                    clean_para = para[:fn_cut].rstrip() if fn_cut != -1 else para

                    blocks = classify_segment_blocks(
                        text=clean_para,
                        seg_type=seg_type,
                        terms=glossary.get("entries", []),
                        footnotes_map=footnotes,
                        seg_id=seg_id,
                        source_lang=source_lang,
                        utterance_lookup=utterance_lookup,
                    )

                    segments.append({
                        "seg_id": seg_id,
                        "seg_order": idx + 1,
                        "seg_type": seg_type,
                        "original": clean_para,
                        "hanviet": None,
                        "translate": clean_para,
                        "matched_terms": [],
                        "blocks": blocks,
                    })

            # Determine chapter title and original title
            ch_title = ""
            for line in final_content.splitlines():
                if line.startswith("# "):
                    ch_title = line[2:].strip()
                    break
            if not ch_title:
                ch_title = cl_entry.get("title") or ch_info.get("title") or f"Chương {ch_num}"

            title_original = cl_entry.get("title_original") or cl_entry.get("title")

            # Extract subheadings for TOC
            subheadings: List[Dict[str, Any]] = []
            for seg in segments:
                if seg["seg_type"] == "heading" or seg["translate"].startswith("#"):
                    for b in seg["blocks"]:
                        if b.get("kind") == "heading":
                            # Extract heading title from spans
                            spans = b.get("spans", [])
                            h_title = "".join(s.get("text", "") for s in spans).strip()
                            if h_title and not h_title.lower().startswith("chú thích"):
                                subheadings.append({
                                    "id": b.get("heading_id") or self.slugify(h_title),
                                    "title": h_title,
                                    "seg_id": seg["seg_id"],
                                })

            # Calculate word count
            word_count = sum(len(s["translate"].split()) for s in segments)

            # Construct ChapterDocument
            ch_doc = {
                "schema_version": SCHEMA_VERSION,
                "book_slug": slug,
                "ch_id": ch_id,
                "ch_num": ch_num,
                "group_id": group_id,
                "title": ch_title,
                "title_original": title_original,
                "banner_image": banner_rel_path,
                "subheadings": subheadings,
                "footnotes": footnotes,
                "segments": segments,
            }
            validate_chapter_document(ch_doc)
            chapter_documents.append(ch_doc)

            # Construct ChapterSummary for Manifest
            ch_summary = {
                "ch_id": ch_id,
                "ch_num": ch_num,
                "group_id": group_id,
                "title": ch_title,
                "title_original": title_original,
                "banner_image": banner_rel_path,
                "segment_count": len(segments),
                "word_count": word_count,
                "score": score,
                "subheadings": subheadings,
            }
            chapter_summaries.append(ch_summary)

        # 6. Build Manifest stats and validate
        avg_score = round(sum(scores) / len(scores), 2) if scores else None
        manifest: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "book_slug": metadata["book_slug"],
            "title": metadata["title"],
            "title_original": metadata["title_original"],
            "author": metadata["author"],
            "source_lang": metadata["source_lang"],
            "target_lang": metadata["target_lang"],
            "genre_key": metadata["genre_key"],
            "genre_label": metadata["genre_label"],
            "register": metadata["register"],
            "style_rules": metadata["style_rules"],
            "cover_image": metadata["cover_image"],
            "book_profile": metadata["book_profile"],
            "stats": {
                "chapter_count": len(chapter_summaries),
                "total_segments": sum(c["segment_count"] for c in chapter_summaries),
                "total_words": sum(c["word_count"] for c in chapter_summaries),
                "average_score": avg_score,
            },
            "chapters": chapter_summaries,
        }

        validate_manifest(manifest)
        validate_glossary(glossary)

        # 7. Package into .web.zip
        with zipfile.ZipFile(output_file, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
            zf.writestr(
                "glossary.json",
                json.dumps(glossary, ensure_ascii=False, indent=2),
            )
            for ch_doc in chapter_documents:
                zf.writestr(
                    f"chapters/{ch_doc['ch_id']}.json",
                    json.dumps(ch_doc, ensure_ascii=False, indent=2),
                )
            for rel_zip_path, src_img_path in image_files.items():
                if src_img_path.is_file():
                    zf.write(src_img_path, arcname=rel_zip_path)

            # Audio files & manifests if present
            audio_dir = run_dir / "audio"
            if audio_dir.is_dir():
                for a_file in sorted(audio_dir.iterdir()):
                    if a_file.is_file() and not a_file.name.startswith("."):
                        zf.write(a_file, arcname=f"audio/{a_file.name}")

        return output_file


def compile_web_package(
    run_dir: Path,
    output_file: Optional[Path] = None,
    profile_override: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Path:
    """Convenience function to compile a BookPackage v1.0 (.web.zip) archive."""
    compiler = WebCompiler()
    return compiler.compile(
        run_dir=run_dir,
        output_file=output_file,
        profile_override=profile_override,
        **kwargs,
    )

