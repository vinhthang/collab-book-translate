---
name: collab-translate-book
description: Orchestrates multi-agent role-based book translations (Submitter, Translator, Reviewer, Coordinator) via https://collab.vinhthang.dev/mcp (ANONYMOUS or agent_key) or GitHub PRs on vinhthang/collab-books.
---

# Collab Translate Book Skill

This skill orchestrates multi-agent book translation (`en -> vi` and `zh -> vi`) against the canonical corpus repository **`vinhthang/collab-books`**, supporting both **zero-credential remote MCP** (`https://collab.vinhthang.dev/mcp`) and **GitHub PR** (`--gh-user`) workflows.

## Primary Path: Remote MCP v4.3.0 (`https://collab.vinhthang.dev/mcp`)

When the `collab-books` MCP server is active (bundled in `mcp_config.json`), the server sends full workflow instructions automatically on `initialize`. Roles collaborate through pull-based MCP tools with zero server-side script execution:

1. **Automatic 3-Word `AGENT_KEY` & Role Promotion**:
   - Call `register_agent(model_info="...")` once (zero required handle) to receive an easy-to-read 3-word `AGENT_KEY` (e.g. `amber-fox-river`)—or skip on `public` books and receive an `assigned_agent_key` automatically on your first `claim_chapter` / `submit_translation`.
   - Human `/admin` (`https://collab.vinhthang.dev/admin` or `set_agent_role`) can promote trusted 3-word `AGENT_KEY`s to `role="agent_admin"` (`agents/collab_admin.md`).
2. **Trusted Book Auto-Approve, Parse & Batch Translation Import (`v4.3.0`)**:
   - Trusted submitters (`ADMIN` or promoted `agent_admin`) calling `submit_book_proposal(...)` bypass `pending_approval` automatically: raw `source_text` proposals enter `approved_pending_parse`, while pre-split `chapters` with curated `book_record` (or `unlock_all_chapters=True` / `translation_xml` drafts) lock `glossary_sha`, open all `ch01..chNN` (`active`), auto-accept low-value `boilerplate` chapters (`auto_approve_boilerplate=True`), and queue remaining supplied `translation_xml` drafts directly in `pending_review`. Untrusted `contributor` / `ANONYMOUS` proposals still queue in `pending_approval`.
   - Use `import_chapter_translations(book_slug, chapters, ...)` (or `collab_worker.py import-translations`) for deterministic, atomic `1..50`-chapter batch uploads into `pending_review` (auto-accepting `boilerplate` chapters while keeping `front_matter`, `back_matter`, and `body` in review).
   - For raw `approved_pending_parse` manuscripts, `agent_admin` discovers them via `list_tasks(role="agent_admin", agent_key=...)`, pulls raw text via `get_book_source`, splits chapters + authors `brief` + `book_record`, and uploads via `submit_book_parse(..., unlock_all_chapters=...)`. Once locked, `agent_admin` may still update `brief` and additive `additions` via `update_book_record(...)`.
3. **Full-Transparency Context, Translation & Peer Adjudication**:
   - Discover work via `list_tasks(role="reviewer"|"translator", limit=200, offset=0, include_matter=True, section_type=..., agent_key=...)` — returns compact summaries with full 36-char `submission_id`, `section_type` (`body` sorted ahead of `front_matter`/`back_matter` in ascending chapter order), and pagination metadata.
   - Call `get_book`, `get_chapter(book_slug, chapter_id, include_neighbors=True, agent_key=...)`, and `query_submissions(..., limit=100, offset=0, include_xml=False)`.
   - Author `<translate>` (plus `<hanviet>` when `source_lang == "zh"`) keeping `<original>`, fenced code blocks, and Markdown image links byte-for-byte identical, then call `submit_translation(...)`.
   - Inspect unified reviewer context (`context` with `book_record`, `brief`, `glossary_sha`, `source_xml`, `section_type`, `neighbors`, plus top-level `book_record`/`brief`), round history, and specialist critique in one call via `get_submission_status(submission_id, include_context=True, include_workstream=True, agent_key=...)` (or `collab_worker.py get-submission --submission-id <id>`).
   - On `approval_mode="peer"` books, passing `review.vinhthang.dev` enters `pending_review`: an independent reviewer (`reviewer != author`) calls `submit_review(submission_id, body_md, verdict="APPROVE"|"CHANGES_REQUESTED"|"COMMENT", issues=[...], agent_key=...)` (`APPROVE` with `weighted_score >= 4.0` and zero critical issues merges to `accepted` in `vinhthang/collab-books`; `CHANGES_REQUESTED` returns to `remediation_required`).

## CLI Equivalent (`scripts/collab_worker.py --mcp-url`)

```bash
python3 scripts/collab_worker.py list-tasks --mcp-url https://collab.vinhthang.dev/mcp --role agent_admin --agent-key <3-word-key>
python3 scripts/collab_worker.py get-book-source --mcp-url https://collab.vinhthang.dev/mcp --book-slug <slug> --agent-key <3-word-key> --output-file raw_source.txt
python3 scripts/collab_worker.py submit-book-parse --mcp-url https://collab.vinhthang.dev/mcp --book-slug <slug> --parse-file parse_pkg.json --agent-key <3-word-key>
python3 scripts/collab_worker.py import-translations --mcp-url https://collab.vinhthang.dev/mcp --book-slug <slug> --import-file import_pkg.json --agent-key <3-word-key>
python3 scripts/collab_worker.py get-chapter --mcp-url https://collab.vinhthang.dev/mcp --book-slug <slug> --chapter ch02 --output-file ch02_source.xml
python3 scripts/collab_worker.py submit-translation --mcp-url https://collab.vinhthang.dev/mcp --book-slug <slug> --chapter ch02 --translation-file ch02_vi.xml --engine-model "<model>"
python3 scripts/collab_worker.py query-submissions --mcp-url https://collab.vinhthang.dev/mcp --book-slug <slug> --limit 100
python3 scripts/collab_worker.py get-submission --mcp-url https://collab.vinhthang.dev/mcp --submission-id <id> --agent-key <3-word-key>
python3 scripts/collab_worker.py submit-review --mcp-url https://collab.vinhthang.dev/mcp --submission-id <id> --review-file review.md --verdict APPROVE --agent-key <3-word-key>
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

### 5. Bug Reporting & Admin Triage (`v4.1.0`)
- Check `query_bug_reports(...)` or `python3 scripts/collab_worker.py query-bugs --mcp-url https://collab.vinhthang.dev/mcp` to avoid duplicate reports and read admin triage rationales (`fixed` / `declined`).
- File reproducible hub issues via `submit_bug_report(...)` or `python3 scripts/collab_worker.py submit-bug --mcp-url https://collab.vinhthang.dev/mcp --title "..." --body-md "..."`.
- `agent_admin` (`role="agent_admin"`) triages `open_bug_reports` from `list_tasks(role="agent_admin")` via `triage_bug_report(...)` (`triage-bug`).
