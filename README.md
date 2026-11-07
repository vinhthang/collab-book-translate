# Collab Book Translate

Collaborative multi-agent book translation platform on GitHub.

This repository orchestrates end-to-end book translations from English (`en`) and Chinese (`zh`) into Vietnamese (`vi`). Autonomous agents and human contributors collaborate through GitHub issues, pull requests, and peer reviews, governed by deterministic validation and independent automated evaluations.

## Architecture & Workflow

```text
[Book Submission PR]
       │
       ▼
 [Intake Pre-screen] ──(Human Approval @vinhthang)──► [Merge to books/<slug>/]
                                                             │
                                                             ▼
                                                    [Whole-book Analyze]
                                                             │
                                                             ▼
                                                    [ch01 Seed Translate]
                                                             │
                                                             ▼
                                            [Open Parallel Issues ch02..chNN]
                                                             │
       ┌─────────────────────────────────────────────────────┴─────────────────────────────────┐
       ▼                                                                                       ▼
[Translator PR: ch02]                                                                 [Translator PR: ch03]
  - translation.xml contract                                                            - translation.xml contract
  - Engine-agnostic provenance                                                          - Engine-agnostic provenance
       │                                                                                       │
       ▼                                                                                       ▼
[Peer Review (reviewer != author)]                                                    [Peer Review (reviewer != author)]
  - Line comments                                                                       - Line comments
  - 6-dimension score block                                                             - 6-dimension score block
       │                                                                                       │
       ▼                                                                                       ▼
[Automated Evaluation (WorkBuddy)]                                                    [Automated Evaluation (WorkBuddy)]
  - Score of record                                                                     - Score of record
  - PASS / REVISE (<=3 rounds) / REASSIGN                                               - PASS / REVISE (<=3 rounds) / REASSIGN
       │                                                                                       │
       ▼                                                                                       ▼
[24h Candidate Window & Selection]                                                    [24h Candidate Window & Selection]
       │                                                                                       │
       └─────────────────────────────────────────────────────┬─────────────────────────────────┘
                                                             ▼
                                                [Post-Merge Ledger & State]
                                                             │
                                                             ▼
                                           [Release Packaging: EPUB, PDF, DOCX]
```

## Repository Structure

- `books/<slug>/`: Source book inputs and `manifest.json`.
- `runs/<slug>/`: Running workspaces, canonical `book_record.json`, `checklist.json`, `translation.xml`, and `collab_ledger.json`.
- `scripts/`: Validation, ingestion, evaluation, and compilation tools.
- `docs/specs/`: Formal specifications and architecture decisions.

## Published Translations

- **[A Scandal in Bohemia (*Vụ Bê Bối Ở Xứ Bohemia*)](https://github.com/vinhthang/collab-book-translate/releases/tag/scandal-in-bohemia-v1.0)** (v1.0):
  - Author: Arthur Conan Doyle
  - Note: 3-chapter, 12-segment public-domain pilot abridgment of Arthur Conan Doyle's *A Scandal in Bohemia* (Project Gutenberg #1661).
  - Deliverables: EPUB 3, PDF, DOCX
  - Quality scores of record: 4.60 (ch01), 4.63 (ch02), 4.50 (ch03)
  - Full audit trail: [`runs/scandal-in-bohemia/collab_ledger.json`](runs/scandal-in-bohemia/collab_ledger.json)

## How to Contribute

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines on:
- Submitting books (`gh pr create --template book`).
- Translating chapters using the `translation.xml` contract.
- Submitting independent reviews with line comments and 6-dimension scores.
