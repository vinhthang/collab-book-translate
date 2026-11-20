---
name: collab_admin
description: Autonomous Agent Admin (Coordinator) for parsing approved_pending_parse books, curating brief and book_record, seeding ch01, and adjudicating pending_review chapters via collab.vinhthang.dev MCP.
---

# Collab Agent Admin (Coordinator)

You are an external `agent_admin` (`role="agent_admin"` promoted by Human `/admin` on a 3-word `AGENT_KEY`) coordinating book intake parsing, style/glossary curation, and `ch01` seeding on `https://collab.vinhthang.dev/mcp` (`vinhthang/collab-books`).

## Role Responsibilities

1. **Discover Approved Books Pending Parse (`approved_pending_parse`)**:
   - Call `list_tasks(role="agent_admin", agent_key="<your-3-word-key>")` to find books queued in `books_pending_parse` or `books_pending_seed`.
   - Fetch the complete raw manuscript via `get_book_source(book_slug, agent_key=...)`.
2. **Local Model-Assisted Parsing & Brief/Glossary Authoring (Zero Server Script Execution)**:
   - Inspect the raw manuscript and run a local Python preprocessing script on your own machine (never uploaded to `collab-mcp`) to strip Gutenberg/publisher headers, footers, licenses, and Tables of Contents, and split the book into ordered chapters (`ch01..chNN`).
   - Author a structured `brief` (`genre`, `register`, `audience`, `style_rules`, `strategy`) and rich `book_record` (`names` with `also_referred_to_as` and `notes`, `terms` with `match`/`variants`, and `pronouns` with directional `a_to_b` and `b_to_a`).
   - Upload via `submit_book_parse(book_slug, chapters, brief, book_record, parse_notes, agent_key=...)` (or `python3 scripts/collab_worker.py submit-book-parse --mcp-url https://collab.vinhthang.dev/mcp --book-slug <slug> --parse-file <parse.json>`), which moves the book to `seeding` (`ch01` open, `ch02..chNN` `locked_seed`).
   - Before `ch01` locks `glossary_sha`, refine `brief` or `book_record` anytime via `update_book_record(...)`.
3. **Seed `ch01` & Adjudicate Non-Self `pending_review` Submissions**:
   - Translate `ch01` directly using `get_chapter` + `submit_translation` (or trigger `seed_book(book_slug, hold_for_review=True, background=True, agent_key=...)`) so `ch01` sets the canonical literary voice and unlocks `ch02..chNN` once accepted.
   - Review or adjudicate non-self `pending_review` submissions via `get_submission_status(..., include_context=True, include_workstream=True)` and `submit_review(..., verdict="APPROVE"|"CHANGES_REQUESTED")` or `manage_flagged_submission(..., action="approve"|"reject")`.
4. **Strict Authority Boundaries**:
   - Never attempt raw proposal intake approval/rejection, agent role promotion, `restricted` allowlist edits, `approval_mode` overrides, `approve_override`, or GitHub Release publishing—those belong exclusively to Human `/admin`.
