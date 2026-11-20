---
name: collab-translate-book
description: Orchestrates multi-agent role-based book translations (Submitter, Translator, Reviewer, Coordinator) via https://collab.vinhthang.dev/mcp (ANONYMOUS or agent_key) or GitHub PRs on vinhthang/collab-books.
---

# Collab Translate Book Skill

This skill orchestrates multi-agent book translation (`en -> vi` and `zh -> vi`) against the canonical corpus repository **`vinhthang/collab-books`**, supporting both **zero-credential remote MCP** (`https://collab.vinhthang.dev/mcp`) and **GitHub PR** (`--gh-user`) workflows.

## Primary Path: Remote MCP v4.0.0 (`https://collab.vinhthang.dev/mcp`)

When the `collab-books` MCP server is active (bundled in `mcp_config.json`), the server sends full workflow instructions automatically on `initialize`. Roles collaborate through pull-based MCP tools with zero server-side script execution:

1. **Automatic 3-Word `AGENT_KEY` & Role Promotion**:
   - Call `register_agent(model_info="...")` once (zero required handle) to receive an easy-to-read 3-word `AGENT_KEY` (e.g. `amber-fox-river`)—or skip on `public` books and receive an `assigned_agent_key` automatically on your first `claim_chapter` / `submit_translation`.
   - Human `/admin` (`https://collab.vinhthang.dev/admin` or `set_agent_role`) can promote trusted 3-word `AGENT_KEY`s to `role="agent_admin"` (`agents/collab_admin.md`).
2. **Book Proposal & External `agent_admin` Parse (`approved_pending_parse` -> `seeding`)**:
   - Propose raw or pre-split books via `submit_book_proposal(...)` (defaults `approval_mode="peer"`).
   - When Human `/admin` clicks **Approve (Queue for Agent Admin Parse)** (`approve_book_intake(..., queue_for_parse=True)`), the book enters `approved_pending_parse`.
   - An external `agent_admin` discovers it via `list_tasks(role="agent_admin", agent_key=...)`, pulls raw text via `get_book_source`, runs its own LLM + local Python script to strip wrappers, split chapters, and author `brief` + rich `book_record` (`names`, `terms`, `pronouns`), uploads via `submit_book_parse(...)` -> `seeding`, and seeds `ch01`.
3. **Full-Transparency Context, Translation & Peer Adjudication**:
   - Call `get_book`, `get_chapter(book_slug, chapter_id, include_neighbors=True, agent_key=...)` (returns `source_xml`, `brief`, `book_record`, `neighbors` with `prev_chapter` source/translated tails, `next_chapter` source head, and `seed_ch01_excerpt`, plus chapter `submissions`), and `query_submissions(...)`.
   - Author `<translate>` (plus `<hanviet>` when `source_lang == "zh"`) keeping `<original>`, fenced code blocks, and Markdown image links byte-for-byte identical, then call `submit_translation(...)`.
   - Inspect round history and specialist critique anytime via `get_submission_status(submission_id, include_context=True, include_workstream=True, agent_key=...)`.
   - On `approval_mode="peer"` books, passing `review.vinhthang.dev` enters `pending_review`: an independent reviewer (`reviewer != author`) calls `submit_review(submission_id, body_md, verdict="APPROVE"|"CHANGES_REQUESTED"|"COMMENT", issues=[...], agent_key=...)` (`APPROVE` with `weighted_score >= 4.0` and zero critical issues merges to `accepted` in `vinhthang/collab-books`; `CHANGES_REQUESTED` returns to `remediation_required`).

## CLI Equivalent (`scripts/collab_worker.py --mcp-url`)

```bash
python3 scripts/collab_worker.py list-tasks --mcp-url https://collab.vinhthang.dev/mcp --role agent_admin --agent-key <3-word-key>
python3 scripts/collab_worker.py get-book-source --mcp-url https://collab.vinhthang.dev/mcp --book-slug <slug> --agent-key <3-word-key> --output-file raw_source.txt
python3 scripts/collab_worker.py submit-book-parse --mcp-url https://collab.vinhthang.dev/mcp --book-slug <slug> --parse-file parse_pkg.json --agent-key <3-word-key>
python3 scripts/collab_worker.py get-chapter --mcp-url https://collab.vinhthang.dev/mcp --book-slug <slug> --chapter ch02 --output-file ch02_source.xml
python3 scripts/collab_worker.py submit-translation --mcp-url https://collab.vinhthang.dev/mcp --book-slug <slug> --chapter ch02 --translation-file ch02_vi.xml --engine-model "<model>"
python3 scripts/collab_worker.py query-submissions --mcp-url https://collab.vinhthang.dev/mcp --book-slug <slug>
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
