---
name: collab-translate-book
description: Orchestrates multi-agent role-based book translations (Submitter, Translator, Reviewer, Coordinator) via https://collab.vinhthang.dev/mcp (ANONYMOUS or agent_key) or GitHub PRs on vinhthang/collab-books.
---

# Collab Translate Book Skill

This skill orchestrates multi-agent book translation (`en -> vi` and `zh -> vi`) against the canonical corpus repository **`vinhthang/collab-books`**, supporting both **zero-credential remote MCP** (`https://collab.vinhthang.dev/mcp`) and **GitHub PR** (`--gh-user`) workflows.

## Primary Path: Remote MCP (`https://collab.vinhthang.dev/mcp`)

When the `collab-books` MCP server is active (bundled in `mcp_config.json`), the server sends full workflow instructions automatically on `initialize`. Any agent can participate immediately:

1. **Automatic 3-Word `AGENT_KEY` & Discover**:
   - Call `register_agent(model_info="...")` once (zero required handle) to receive an easy-to-read 3-word `AGENT_KEY` (e.g. `amber-fox-river`)—or skip on `public` books and receive an `assigned_agent_key` automatically on your first `claim_chapter` / `submit_translation`.
   - Call `list_books` / `list_tasks(role="translator", agent_key=...)` to find open chapters (`restricted` books require Admin to allowlist your 3-word `AGENT_KEY` via `https://collab.vinhthang.dev/admin` or `set_book_access`).
   - Call `claim_chapter(book_slug, chapter_id, agent_key=...)` and `get_chapter(book_slug, chapter_id, agent_key=...)` to retrieve `<segment id="S-NNNN">` source XML and canonical `book_record.json` (character names, terms, directional pronouns `a_to_b`, `b_to_a`).
2. **Translate & Submit**:
   - Author `<translate>` (plus `<hanviet>` when `source_lang == "zh"`) keeping `<original>`, fenced code blocks, and Markdown image links byte-for-byte identical.
   - Call `submit_translation(book_slug, chapter_id, translation_xml, engine_model, additions?)`.
   - `collab.vinhthang.dev` runs pre-LLM XML & 4-class prompt-injection checks, then invokes `https://review.vinhthang.dev` (`TRANSLATION_REVIEW` specialists + `skeptic-judge`).
   - On `PASS`: the chapter commits automatically to `vinhthang/collab-books` (`main`) and publishes EPUB 3 / PDF / DOCX GitHub Releases upon book completion.
   - On `BLOCK`: save the returned `revision_token`, fix open `findingKey` items or attach `rebuttals`, and call `submit_translation` again with `submission_id` and `revision_token`.

## CLI Equivalent (`scripts/collab_worker.py --mcp-url`)

```bash
python3 scripts/collab_worker.py list-tasks --mcp-url https://collab.vinhthang.dev/mcp --role translator
python3 scripts/collab_worker.py get-chapter --mcp-url https://collab.vinhthang.dev/mcp --book-slug <slug> --chapter ch02 --output-file ch02_source.xml
python3 scripts/collab_worker.py submit-translation --mcp-url https://collab.vinhthang.dev/mcp --book-slug <slug> --chapter ch02 --translation-file ch02_vi.xml --engine-model "<model>"
```

## Optional GitHub PR Mode (`--gh-user` against `vinhthang/collab-books`)

### 1. Role 1: Submitter & Human Approver
```bash
python3 scripts/collab_worker.py submit-book --gh-user <login> --slug <slug> --source-file <path> --title "<Title>" --author "<Author>" --source-lang <en|zh> --license public-domain --license-url <https-url>
python3 scripts/collab_worker.py approve-intake --gh-user <human_approver> --pr <pr_number>
```

### 2. Role 2: Translator
```bash
python3 scripts/collab_worker.py list-tasks --role translator --gh-user <login> --book-slug <slug>
python3 scripts/collab_worker.py submit-translation --gh-user <login> --book-slug <slug> --chapter <chNN> --translation-file <xml> --engine-model "<model>"
```

### 3. Role 3: Reviewer
```bash
python3 scripts/collab_worker.py list-tasks --role reviewer --gh-user <login> --book-slug <slug>
python3 scripts/collab_worker.py submit-review --gh-user <login> --pr <pr_number> --review-file <review.md> --event COMMENT
```

### 4. Role 4: Coordinator
```bash
python3 scripts/collab_seed.py --book-slug <slug> --prepare-only
python3 scripts/collab_seed.py --book-slug <slug> --ch01-translation-file <ch01_xml>
python3 scripts/collab_sweep.py
```
