---
name: collab-translate-book
description: Orchestrates multi-agent role-based book translations (Submitter, Translator, Reviewer, Coordinator) on GitHub via collab_worker.py, collab_seed.py, and collab_sweep.py.
---

# Collab Translate Book Skill

This skill provides operational guidance and command execution for autonomous agents participating in collaborative book translations on GitHub.

## Operational Roles

### 1. Role 1: Submitter & Human Approver
- **Responsibilities**: Propose book intake PRs (`book/<slug>`) with source texts, inspect pre-screen reports, and approve intake (`/approve-intake <head_oid>`).
- **CLI Commands**:
  ```bash
  # Submit book proposal
  python3 scripts/collab_worker.py submit-book \
    --gh-user <login> \
    --slug <slug> \
    --source-file <path_to_source> \
    --title "<Title>" \
    --author "<Author>" \
    --source-lang <en|zh> \
    --target-lang vi \
    --license <license-key> \
    --license-url <license-url>

  # Approve book intake after PASS pre-screen
  python3 scripts/collab_worker.py approve-intake \
    --gh-user <human_approver> \
    --pr <pr_number>
  ```

### 2. Role 2: Translator
- **Responsibilities**: Inspect open chapter issues (`ch02..chNN`), claim issues (`/claim`), author literary Vietnamese translations adhering to XML segment structure and `runs/<slug>/book_record.json`, submit translation PRs (`translate/<slug>-<chNN>-<login>`), and fix reviews (`status:revise`).
- **CLI Commands**:
  ```bash
  # List assigned translation tasks
  python3 scripts/collab_worker.py list-tasks \
    --role translator \
    --gh-user <login> \
    --book-slug <slug> \
    --workers vinhthang,1quachuoi9vang \
    --mode sharded

  # Submit chapter translation
  python3 scripts/collab_worker.py submit-translation \
    --gh-user <login> \
    --book-slug <slug> \
    --chapter <chNN> \
    --translation-file <path_to_translation.xml> \
    --engine-model "<Engine/Model>" \
    --agent-name "<agent_tag>"
  ```

### 3. Role 3: Reviewer
- **Responsibilities**: Review PRs authored by other logins (`reviewer != pr.author`), verify segment alignment, terminology fidelity, and absence of translationese, and post 6-dimension rubric scores (`Fidelity`, `Fluency`, `Terminology`, `Register`, `Structure`, `Completeness` in 1.0–5.0) with segment line comments (`S-NNNN`).
- **CLI Commands**:
  ```bash
  # List translation PRs needing peer review
  python3 scripts/collab_worker.py list-tasks \
    --role reviewer \
    --gh-user <login> \
    --book-slug <slug>

  # Submit peer review
  python3 scripts/collab_worker.py submit-review \
    --gh-user <login> \
    --pr <pr_number> \
    --review-file <path_to_review.md> \
    --event COMMENT
  ```

### 4. Role 4: Coordinator
- **Responsibilities**: Extract source text and seed opening sample and `ch01` (`scripts/collab_seed.py`), run continuous sweep cycles (`scripts/collab_sweep.py`), validate peer reviews, merge passing PRs immediately without wait timers, and package final releases (`epub`, `pdf`, `docx`).
- **CLI Commands**:
  ```bash
  # Prepare book workspace & extract chapters
  python3 scripts/collab_seed.py --book-slug <slug> --prepare-only

  # Seed ch01 and open ch02..chNN issues
  python3 scripts/collab_seed.py --book-slug <slug> --ch01-translation-file <ch01_xml>

  # Run coordinator sweep
  python3 scripts/collab_sweep.py
  ```
