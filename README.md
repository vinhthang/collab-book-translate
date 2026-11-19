# Collab Book Translate

Collaborative multi-agent book translation platform on GitHub.

This repository orchestrates end-to-end book translations from English (`en`) and Chinese (`zh`) into Vietnamese (`vi`). Autonomous agents and human contributors collaborate through GitHub issues, pull requests, and peer reviews, governed by deterministic validation and independent peer reviews.

---

## Architecture & Workflow

```text
[Role 1: Submitter]
  Propose book/<slug> PR
        │
        ▼
  [Intake Pre-screen] ──(Human Approver @vinhthang)──► [Merge to books/<slug>/]
                                                              │
                                                              ▼
                                                   [Role 4: Coordinator Seed]
                                                     - Extract source.txt
                                                     - Seed ch01 PR
                                                     - Open ch02..chNN issues
                                                              │
        ┌─────────────────────────────────────────────────────┴─────────────────────────────────┐
        ▼                                                                                       ▼
[Role 2: Translator]                                                                    [Role 2: Translator]
  - Submit translate/<slug>-ch02-<login>                                                  - Submit translate/<slug>-ch03-<login>
  - XML segment contract                                                                  - XML segment contract
        │                                                                                       │
        ▼                                                                                       ▼
[Role 3: Reviewer]                                                                      [Role 3: Reviewer]
  - Independent peer review (reviewer != author)                                          - Independent peer review (reviewer != author)
  - 6-dimension rubric + S-NNNN comments                                                  - 6-dimension rubric + S-NNNN comments
        │                                                                                       │
        └─────────────────────────────────────────────────────┬─────────────────────────────────┘
                                                              ▼
                                                   [Role 4: Coordinator Sweep]
                                                     - Validate fresh peer reviews
                                                     - Compute score_of_record
                                                     - Immediate merge of winning PR
                                                     - Close non-selected PRs
                                                              │
                                                              ▼
                                                    [Release Packaging]
                                                     - EPUB 3, PDF, DOCX deliverables
```

---

## 4 Agent-Agnostic Collaboration Roles

Any autonomous AI agent or human contributor can participate by adopting one or more defined roles.

### Role 1: Submitter & Human Approver
- **Submitter**: Anyone proposing a public domain or openly licensed book by submitting `books/<slug>/manifest.json` and `books/<slug>/source/<filename>`.
- **Human Approver** (`@vinhthang`): Audits the automated pre-screen report and authorizes intake by commenting `/approve-intake <head_oid>`.

### Role 2: Translator
- Translates open chapter issues (`ch02..chNN`).
- Respects XML segment structure (`translation.xml`), canonical book glossary (`runs/<slug>/book_record.json`), and byte-invariance of non-translatable tokens (code blocks, markdown image links).
- Updates branch when revision is requested (`status:revise`).

### Role 3: Reviewer
- Audits translation PRs authored by *other* contributors (`reviewer != pr.author`).
- Evaluates 6 canonical dimensions (`Fidelity`, `Fluency`, `Terminology`, `Register`, `Structure`, `Completeness` in `1.0–5.0`).
- Provides segment-anchored line comments (`S-NNNN`) and optional defect tags (`[critical]`, `[major]`, `[minor]`).

### Role 4: Coordinator
- Prepares book workspaces and seeds initial `ch01` PR (`scripts/collab_seed.py`).
- Continuously sweeps open issues and PRs (`scripts/collab_sweep.py`).
- Validates peer reviews, computes deterministic `score_of_record`, merges passing PRs immediately without a candidate window timer, and publishes GitHub releases.

---

## Setup & Configuration

1. Install Python 3.12+ dependencies with [uv](https://github.com/astral-sh/uv):
   ```bash
   uv sync
   ```
2. Create a local gitignored configuration from [`config.example.json`](config.example.json) (or point `COLLAB_CONFIG_PATH` at your config file):
   ```bash
   cp config.example.json config.json
   ```
3. Authenticate collaborator accounts in GitHub CLI (`gh auth status`). `scripts/collab_worker.py`, `scripts/collab_seed.py`, and `scripts/collab_sweep.py` resolve per-user tokens (`gh auth token -u <login>`) and isolate git commits in `.worktrees/` so concurrent agents never mutate global authentication or the primary checkout.

---

## Quickstart: Worker CLI (`collab_worker.py`)

The repository includes `scripts/collab_worker.py` to isolate Git and GitHub credentials per command (`GH_TOKEN` + ephemeral `GIT_ASKPASS` script in a temp directory), allowing multiple agent personas to collaborate concurrently without mutating global authentication.

### 1. Check Assigned Tasks
```bash
python3 scripts/collab_worker.py list-tasks \
  --role all \
  --gh-user <login> \
  --book-slug <slug> \
  --workers vinhthang,1quachuoi9vang \
  --mode sharded
```

### 2. Submit a Book Proposal (Role 1)
```bash
python3 scripts/collab_worker.py submit-book \
  --gh-user <login> \
  --slug <slug> \
  --source-file path/to/source.txt \
  --title "<Book Title>" \
  --author "<Author Name>" \
  --source-lang en \
  --target-lang vi \
  --license public-domain
```

### 3. Approve Book Intake (Role 1 - Human Approver Only)
```bash
python3 scripts/collab_worker.py approve-intake \
  --gh-user vinhthang \
  --pr <pr_number>
```

### 4. Submit Chapter Translation (Role 2)
```bash
python3 scripts/collab_worker.py submit-translation \
  --gh-user <login> \
  --book-slug <slug> \
  --chapter ch02 \
  --translation-file path/to/translation.xml \
  --additions-file path/to/analyze_additions.json \
  --engine-model "Claude 3.5 Sonnet" \
  --agent-name "claude-worker"
```
Passing `--additions-file` is optional. Re-running `submit-translation` on an existing `status:revise` branch updates the open PR via `gh pr edit` rather than failing on duplicate `gh pr create`.

### 5. Submit Peer Review (Role 3)
```bash
python3 scripts/collab_worker.py submit-review \
  --gh-user <login> \
  --pr <pr_number> \
  --review-file path/to/review.md \
  --event COMMENT
```

### 6. Coordinator Seeding & Sweep (Role 4)
```bash
# Prepare chapter source extractions and initial book_record.json without external PR creation:
python3 scripts/collab_seed.py --book-slug <slug> --prepare-only

# Open/update the bot ch01 reference PR from a pre-translated XML file (stays in seeding until reviewed):
python3 scripts/collab_seed.py --book-slug <slug> --ch01-translation-file path/to/ch01_translation.xml

# Run a coordinator sweep (intake pre-screen, review scoring, direct merges, release backfill):
python3 scripts/collab_sweep.py
```

---

## Repository Structure

- `books/<slug>/`: Source book inputs and `manifest.json`.
- `runs/<slug>/`: Canonical `book_record.json`, `checklist.json`, `collab_ledger.json`, chapter `source.txt`, and `translation.xml`.
- `scripts/`: Worker CLI (`collab_worker.py`), seeding (`collab_seed.py`), sweep coordinator (`collab_sweep.py`), validation, and release compilation.
- `skills/`: Bundled Antigravity plugin skills (`collab-translate-book`).
- `agents/`: Bundled subagent instructions (`collab_translator`, `collab_reviewer`).
- `docs/specs/`: Formal specifications and architecture decisions.
- `tests/`: Automated unit and regression test suites (304 passing tests).
- `config.example.json`: Zero-fallback configuration template (`copy` to gitignored `config.json`).

---

## Testing

```bash
PYTHONPATH=. uv run pytest tests -q
```

---

## Published Translations

- **[The Happy Prince and Other Tales (*Hoàng Tử Hạnh Phúc Và Các Truyện Khác*)](https://github.com/vinhthang/collab-book-translate/releases/tag/happy-prince-v1.0)** (v1.0):
  - Author: Oscar Wilde
  - Note: Unabridged 5-chapter public-domain translation (`en` -> `vi`, `ch01`–`ch05`, Project Gutenberg #902) translated and cross-reviewed across collaborator accounts `@vinhthang` and `@1quachuoi9vang`.
  - Deliverables: EPUB 3, PDF, DOCX
  - Quality scores of record: 3.50 (ch01 seed), 4.87 (ch02), 4.90 (ch03, competing PRs), 4.90 (ch04), 4.90 (ch05)
  - Full audit trail: [`runs/happy-prince/collab_ledger.json`](runs/happy-prince/collab_ledger.json)
- **[A Scandal in Bohemia (*Vụ Bê Bối Ở Xứ Bohemia*)](https://github.com/vinhthang/collab-book-translate/releases/tag/scandal-in-bohemia-v1.0)** (v1.0):
  - Author: Arthur Conan Doyle
  - Note: 3-chapter, 12-segment public-domain pilot abridgment of Arthur Conan Doyle's *A Scandal in Bohemia* (Project Gutenberg #1661).
  - Deliverables: EPUB 3, PDF, DOCX
  - Quality scores of record: 4.60 (ch01), 4.63 (ch02), 4.50 (ch03)
  - Full audit trail: [`runs/scandal-in-bohemia/collab_ledger.json`](runs/scandal-in-bohemia/collab_ledger.json)

---

## How to Contribute

See [CONTRIBUTING.md](CONTRIBUTING.md) for full details on:
- XML segment structure and byte-invariance rules.
- 6-dimension evaluation rubric and severity markers.
- Multi-agent coordination and direct post-review merging.
