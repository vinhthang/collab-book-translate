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
  --engine-model "Claude 3.5 Sonnet" \
  --agent-name "claude-worker"
```

### 5. Submit Peer Review (Role 3)
```bash
python3 scripts/collab_worker.py submit-review \
  --gh-user <login> \
  --pr <pr_number> \
  --review-file path/to/review.md \
  --event COMMENT
```

---

## Repository Structure

- `books/<slug>/`: Source book inputs and `manifest.json`.
- `runs/<slug>/`: Canonical `book_record.json`, `checklist.json`, chapter `source.txt`, and `translation.xml`.
- `scripts/`: Worker CLI (`collab_worker.py`), seeding (`collab_seed.py`), sweep coordinator (`collab_sweep.py`), validation, and release compilation.
- `skills/`: Bundled Antigravity plugin skills (`collab-translate-book`).
- `agents/`: Bundled subagent instructions (`collab_translator`, `collab_reviewer`).
- `docs/specs/`: Formal specifications and architecture decisions.

---

## Published Translations

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
