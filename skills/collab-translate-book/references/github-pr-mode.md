# Optional GitHub PR Mode (`--gh-user` against `vinhthang/collab-books`)

Use these CLI equivalents via `collab_worker.py` / `collab_seed.py` / `collab_sweep.py` when operating in GitHub PR mode instead of the primary Remote MCP server (`https://collab.vinhthang.dev/mcp`):

## 1. Role 1: Submitter & Human Approver
```bash
rtk uv run --project ~/.gemini/config/plugins/collab-book-translate python3 ~/.gemini/config/plugins/collab-book-translate/scripts/collab_worker.py submit-book --gh-user <login> --slug <slug> --source-file <path> --title "<Title>" --author "<Author>" --source-lang <en|zh> --license public-domain --license-url <https-url>
rtk uv run --project ~/.gemini/config/plugins/collab-book-translate python3 ~/.gemini/config/plugins/collab-book-translate/scripts/collab_worker.py approve-intake --gh-user <human_approver> --pr <pr_number>
```

## 2. Role 2: Translator
```bash
rtk uv run --project ~/.gemini/config/plugins/collab-book-translate python3 ~/.gemini/config/plugins/collab-book-translate/scripts/collab_worker.py list-tasks --role translator --gh-user <login> --book-slug <slug>
rtk uv run --project ~/.gemini/config/plugins/collab-book-translate python3 ~/.gemini/config/plugins/collab-book-translate/scripts/collab_worker.py submit-translation --gh-user <login> --book-slug <slug> --chapter <chNN> --translation-file <xml> --engine-model "<model>"
```

## 3. Role 3: Reviewer
```bash
rtk uv run --project ~/.gemini/config/plugins/collab-book-translate python3 ~/.gemini/config/plugins/collab-book-translate/scripts/collab_worker.py list-tasks --role reviewer --gh-user <login> --book-slug <slug>
rtk uv run --project ~/.gemini/config/plugins/collab-book-translate python3 ~/.gemini/config/plugins/collab-book-translate/scripts/collab_worker.py submit-review --gh-user <login> --pr <pr_number> --review-file <review.md> --event COMMENT
```

## 4. Role 4: Coordinator
```bash
rtk uv run --project ~/.gemini/config/plugins/collab-book-translate python3 ~/.gemini/config/plugins/collab-book-translate/scripts/collab_seed.py --book-slug <slug> --prepare-only
rtk uv run --project ~/.gemini/config/plugins/collab-book-translate python3 ~/.gemini/config/plugins/collab-book-translate/scripts/collab_seed.py --book-slug <slug> --ch01-translation-file <ch01_xml>
rtk uv run --project ~/.gemini/config/plugins/collab-book-translate python3 ~/.gemini/config/plugins/collab-book-translate/scripts/collab_sweep.py
```
