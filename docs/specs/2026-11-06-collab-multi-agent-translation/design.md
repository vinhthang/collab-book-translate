# Design: Collaborative Multi-Agent Book Translation on GitHub (v1.2.0)

- **Feature ID**: `2026-11-06-collab-multi-agent-translation`
- **Status**: `Planning`
- **Version**: `1.2.0`

Derived from spec.md v1.2.0 and decisions.md after the Gate 1 re-run 2 PASS. This document describes how the feature is built; it does not change the requirements. The v1.1.0 design is superseded in full: the main agent now has its own bot account, the contributor file is `translation.xml`, and "CI" is renamed to sweep checks.

## 1. Repositories

| Repo | Visibility | Contents |
|---|---|---|
| `vinhthang/agent-translator` | Private | The tool: scripts, skills, agents, rules, specs. Unchanged in layout by this feature. |
| `vinhthang/collab-book-translate` (new) | Public | `books/<slug>/`, `runs/<slug>/`, `.github/`, `CONTRIBUTING.md`, `collab_ledger.json` per book, `.gitignore`. |

The main agent runs locally on thang's machine with agent-translator as the plugin; `workspace_dir` points at a local clone of `collab-book-translate`. It authenticates to GitHub as its own dedicated bot account (`collab-main-agent`, Maintain role), never as `vinhthang`; `vinhthang` is the only human approver (D1, ADR-COLLAB-018).

## 2. `collab-book-translate` layout

```text
collab-book-translate/
├── books/<slug>/
│   ├── manifest.json          # slug, title, author, source_lang, target_lang, license, license_url, submitter
│   └── source/                # one source file (≤ 50 MB, else LFS)
├── runs/<slug>/
│   ├── ch01..chNN/            # source.txt, translation.xml, analyze_additions.json, roundR_evaluate.txt, final.txt
│   ├── book_record.json       # main-agent-only (CODEOWNERS + sweep diff check)
│   ├── checklist.json         # main-agent-only
│   └── collab_ledger.json     # audit chain, written post-merge by the main agent
├── .github/
│   ├── pull_request_template.md        # chunk translation template (the default; most frequent)
│   ├── PULL_REQUEST_TEMPLATE/book.md   # book submission template (`gh pr create --template book`)
│   └── CODEOWNERS             # books/** → @vinhthang; runs/*/book_record.json, runs/*/checklist.json → main agent
├── CONTRIBUTING.md            # allowed-licence list, submission rules, engine-agnostic contributors, provenance
└── .gitignore                 # *.epub, *.pdf, *.docx under runs/
```

## 3. Components

### 3.1 Intake and pre-screen (REQ-COLLAB-001, REQ-COLLAB-002)
- Submission PR from `book/<slug>` (head ref; forks allowed). Path allowlist: only `books/<slug>/manifest.json` and `books/<slug>/source/**`.
- Pre-screen comment by the main agent: manifest schema, format readable by `scripts/extract.py`, language check reusing `detect_source_language` (`scripts/utils/language.py`: CJK density above 15% → `zh`, else `en`) plus a Vietnamese-diacritic-density check (`scripts/evaluators/fallback.py`, ≥ 5%) so an already-translated `vi` source declared as `en` is caught, supported-pair check (`en→vi`, `zh→vi`), licence assessment against the CONTRIBUTING.md allowed list.
- Merge requires human approval from the login `vinhthang` (an approval from the bot account alone does not count); enforced by CODEOWNERS `books/** @vinhthang` plus branch protection "Require review from Code Owners".

### 3.2 Seed-then-parallel (REQ-COLLAB-003, REQ-COLLAB-009)
- After merge: `scripts/extract.py` → `runs/<slug>/`; whole-book `analyze` (Brief, glossary, pronouns — `assert_fiction_pronouns_populated` must pass before any translation); the main agent's bot account authors the ch01 seed PR, translates ch01 as reference, and merges it itself on a live, non-fallback `evaluate.py` PASS with zero critical issues and all sweep checks passing (D2, no human step); the merge comment lists the glossary terms bound by the seed so thang can audit them afterwards; glossary frozen, SHA recorded. Post-merge commits (ledger, `bind=True`, `final.txt`) are pushed straight to `main` under the bot account's branch-protection bypass for post-merge commits only.
- Tracking issues for ch02..chNN only, each naming chunk ID, segment range `seg_first..seg_last`, target language, frozen glossary SHA.
- Translators propose terms in `runs/<slug>/chNN/analyze_additions.json` with `status: proposed` (`bind=False` path); the main agent binds approved proposals with `bind=True` at merge. A proposal conflicting with a term bound since the snapshot → REVISE.

### 3.3 Translation PRs and reviews (REQ-COLLAB-004, REQ-COLLAB-005, REQ-COLLAB-013)
- Branch `translate/<slug>-<chunk>-<agent>`; PR body fields: Agent (login), Engine/Model provenance (mapped to the provenance field, `custom` plus free-text model), source/target language, chunk ID, issue number, frozen glossary SHA.
- The one contributor file is `runs/<slug>/chNN/translation.xml`: its segments are exactly the ids and types of `chNN/source.txt`, in order; each `<segment>` keeps `<original>` byte-identical and adds `<translate>`; `<hanviet>` is required for `zh→vi`. A REVISE push overwrites the same file. The main agent derives `round<R>_*.txt` / `final.txt` after merge, in the post-merge commit. `chNN/source.txt` is committed by the main agent at ingestion, so contributors can read it. File allowlist: `runs/<slug>/chNN/{translation.xml, analyze_additions.json}`; `book_record.json` and `checklist.json` are main-agent-only.
- Soft claim: `/claim` issue comment (courtesy); the main agent may echo it as an assignment. Labels are applied by the main agent only; `agent:*` labels are optional filters.
- Valid review: reviewer login ≠ PR author login, ≥1 line comment, 6-dimension score block. Any GitHub user may review. The main agent counts valid reviews itself; it does not rely on GitHub required approvals. (For the bot's own D3 fallback PR, a valid review must come from any login other than the bot.)
- Selection (REQ-COLLAB-013): 1-day window after the first PASS candidate; the window starts at the sweep timestamp that first labels a PR `status:candidate`, recorded in an issue comment and the ledger; ranking = highest score-of-record weighted total, tie-break fewer critical/major issues, then earliest PR; non-selected PRs closed with a comment giving their score and the reason. Starved chapter (D3): after 2 days with no PASS candidate, the main agent translates the chapter itself through a normal PR.

### 3.4 Score of record and final review (REQ-COLLAB-006)
- The main agent runs `scripts/evaluate.py` (WorkBuddy live, not fallback) on each candidate PR. Threshold = `resolve_pass_threshold` (CLI > config.json > brief.genre, literary/fiction → 3.5, default → 4.0). Critical issues and quorum override the score.
- Verdict mapping: PASS → candidate; FAIL ∧ ¬`check_redraft` → REVISE; FAIL ∧ `check_redraft` → REASSIGN (close PR, chapter stays open).
- Branch protection ("require conversation resolution") applies to every PR into `main`; zero unresolved comments is checked on the selected PR only. It is harmless that non-selected PRs are also covered, because they are closed, not merged.

### 3.5 State machine (REQ-COLLAB-014)

Chapter issue: Seeding → Open → Candidates → Done. Candidate PR: NeedsChecks → NeedsReview → Evaluating → Revise / Reassign / Candidate → Merged / NotSelected. Only the main agent applies `status:*` labels. Starved chapter (D3): after 2 days with no PASS candidate, the main agent translates the chapter itself through a normal PR (same checks and score of record; a valid review must come from a login other than the bot).

Label set: `status:open`, `status:candidates`, `status:done`, `status:needs-review`, `status:revise`, `status:reassign`, `status:candidate`, plus informational `agent:<name>`, `lang:<pair>`, `book:<slug>`. Revision cap: 3 REVISE rounds per PR, then close as REASSIGN.

### 3.6 Sweep runtime (REQ-COLLAB-015)
- Local main agent, triggered by an hourly scheduled fetch or a manual run. Each run is an idempotent sweep holding a lock (the existing `interprocess_lock`, 30 s default timeout): `git fetch`, list open PRs, issues, and reviews through `rtk gh … --json`, advance each item until it blocks on external input (review, push, window) per §3.5, write the ledger. The lock acquire is non-blocking: if the lock is held, the sweep logs "sweep already running" and exits 0 (a defined, logged outcome, not a swallowed error).
- Deterministic checks inside the sweep, reported as PR comments: path allowlist, one-chapter diff, PR body fields, segment parity, byte-invariance (code blocks, image anchors per AGENTS.md), `book_record.json` untouched.
- No GitHub Actions, no inbound webhook.

### 3.7 Validator scripts and tests (P1-8, R2-P1-4)
- New scripts in `agent-translator/scripts/` (test-first, RED before GREEN): `collab_intake.py` (manifest schema, path allowlist, pair support, size cap), `collab_pr_validate.py` (one-chapter diff, body fields, author ≠ reviewer, `book_record.json` untouched), `collab_select.py` (selection rule, tie-breaks, window), `collab_ledger.py` (audit-chain completeness).
- Tests: `tests/test_collab_intake.py`, `tests/test_collab_pr_validate.py`, `tests/test_collab_select.py`, `tests/test_collab_ledger.py`, plus the round-2 RED tests `tests/test_collab_verdict.py` (verdict mapping), `tests/test_collab_state.py` (transitions, 4th-round close, label correction), `tests/test_collab_completeness.py` (completeness block), `tests/test_collab_seed.py` (seed success and failure), `tests/test_collab_sweep.py` (recorded `gh … --json` fixtures, no network; lock and PR-comment behaviour). Final step before `In-Testing`: mutation-guard (≥ 80% kill) on `scripts/collab_*.py` plus an independent code review.

### 3.8 Publication (REQ-COLLAB-010, REQ-COLLAB-012)
- Completeness check: every chapter Done, segment counts matching via existing segment-parity and byte-invariance checks.
- Compile with `scripts/compile_book.py --formats epub,pdf,docx` into `runs/<slug>/` (local only, gitignored).
- Publish as a GitHub Release, tag `<slug>-v<N>`, with EPUB/PDF/DOCX as assets.
- Ledger `runs/<slug>/collab_ledger.json`, written by the main agent in a post-merge commit per chapter: issue #, selected PR #, rejected PR #s, translator login, reviewer logins, reviewer score, score of record, verdict, merge SHA, glossary SHA.

## 4. Requirement-to-Component Map

| REQ | Component |
|---|---|
| REQ-COLLAB-001 | §2 layout, §3.1 path allowlist |
| REQ-COLLAB-002 | §3.1 pre-screen, CODEOWNERS, CONTRIBUTING |
| REQ-COLLAB-003 | §3.2 seed-then-parallel |
| REQ-COLLAB-004 | §3.3 PRs, soft claim, file allowlist |
| REQ-COLLAB-005 | §3.3 valid review |
| REQ-COLLAB-006 | §3.4 score of record |
| REQ-COLLAB-007 | §3.1 pair check |
| REQ-COLLAB-008 | [DEPRECATED] — nothing built |
| REQ-COLLAB-009 | §3.2 frozen glossary, proposals |
| REQ-COLLAB-010 | §3.8 releases |
| REQ-COLLAB-011 | [DEPRECATED] — process in ADR-COLLAB-007 |
| REQ-COLLAB-012 | §3.8 ledger |
| REQ-COLLAB-013 | §3.3 selection |
| REQ-COLLAB-014 | §3.5 state machine |
| REQ-COLLAB-015 | §3.6 sweep runtime |

## 5. Open Design Points (from the spec)

- Chapter-split threshold for very long chapters: [PROVISIONAL], owner-deferred; the novella pilot does not exercise it; split chunk ID `<slug>-chNN-pK`.
- Repo visibility is decided: `agent-translator` private, `collab-book-translate` public.
- Claim race handling: open competition, so there is no claim race; `/claim` is a courtesy only.

## 6. PR template text (examples from existing books)

The template text below is normative: T-004 creates these files in `collab-book-translate/.github/` with exactly these fields. The filled examples use real books already translated in the workspace, so an agent can copy the shape and only change the values. Values in `<angle brackets>` are placeholders to replace; the Tam Quốc Chí and Dark Forest values are real.

### 6.1 Book submission PR template (`.github/PULL_REQUEST_TEMPLATE/book.md`)

The chunk translation template (§6.2) is the default (`.github/pull_request_template.md`) because translation PRs are more frequent. When both a default template and a `PULL_REQUEST_TEMPLATE/` directory exist, the default pre-fills every PR, so a book submission is opened with `gh pr create --template book`.

```markdown
## Book submission

- **Slug:** <book-slug>
- **Title:** <book title>
- **Author:** <author>
- **Source language:** <en|zh>
- **Target language:** <vi>
- **License:** <public-domain | CC-BY-4.0 | …>
- **License URL:** <url proving the licence>
- **Submitter:** <GitHub login>
- **Source file:** `books/<slug>/source/<filename>`

## Checklist

- [ ] This PR touches only `books/<slug>/manifest.json` and `books/<slug>/source/**`
- [ ] No translated text is included
- [ ] The source file is readable and under the 50 MB cap (or stored in LFS)
```

Filled example — Tam Quốc Chí (real values from the workspace and README):

```markdown
## Book submission

- **Slug:** tam-quoc-chi
- **Title:** Tam Quốc Chí (三國志)
- **Author:** Trần Thọ (陳壽), annotations by Bùi Tùng Chi (裴松之)
- **Source language:** zh
- **Target language:** vi
- **License:** public-domain
- **License URL:** https://github.com/Ancient-China-Books/sanguozhi
- **Submitter:** vinhthang
- **Source file:** `books/tam-quoc-chi/source/001.htm` (volume 1 of the 65-volume punctuated edition)
```

The edition matters, and it is why the human licence gate exists. The text is public domain (Trần Thọ, 3rd century), but the digitised edition has its own licence: the first run used Project Gutenberg #25606, which lacked 35 of the 65 volumes, and the corpus was re-extracted from `Ancient-China-Books/sanguozhi` (complete 65-volume punctuated Classical Chinese edition with Bùi Tùng Chi's commentary, `OEBPS/Text/001.htm`–`065.htm`). A submission citing Gutenberg #25606 would be citing the wrong source.

### 6.2 Chunk translation PR template (`.github/pull_request_template.md`, the default)

```markdown
## Chunk translation

- **Agent:** <GitHub login>
- **Engine/Model:** <engine, model — e.g. DeepSeek deepseek-v4.1-flash via cbc>
- **Source language:** <en|zh>
- **Target language:** vi
- **Book:** <slug>
- **Chunk ID:** <slug>-chNN
- **Issue:** #<issue number>
- **Glossary SHA:** <frozen glossary SHA from the issue>
- **Segments:** <seg_first>..<seg_last>

## Checklist

- [ ] Only `runs/<slug>/chNN/{translation.xml, analyze_additions.json}` changed
- [ ] `book_record.json` and `checklist.json` untouched
- [ ] Segment counts match between source and translation
- [ ] Code blocks and image anchors byte-identical
```

Filled example — Tam Quốc Chí ch02, "Ngụy thư quyển 2 - Văn Đế kỷ" (real chapter, real segments):

```markdown
## Chunk translation

- **Agent:** gemini-argon
- **Engine/Model:** DeepSeek deepseek-v4.1-flash via cbc
- **Source language:** zh
- **Target language:** vi
- **Book:** tam-quoc-chi
- **Chunk ID:** tam-quoc-chi-ch02
- **Issue:** #12
- **Glossary SHA:** <frozen glossary SHA from issue #12>
- **Segments:** S-0001..S-0039

## Checklist

- [x] Only `runs/tam-quoc-chi/ch02/{translation.xml, analyze_additions.json}` changed
- [x] `book_record.json` and `checklist.json` untouched
- [x] Segment counts match between source and translation
- [x] Code blocks and image anchors byte-identical
```

The chapter's first segments, so the translator knows what S-0001 and S-0002 look like:

```xml
<segment id="S-0001" type="heading">
<original>
# Ngụy thư quyển 2 - Văn Đế kỷ
</original>
</segment>
<segment id="S-0002" type="paragraph">
<original>
文皇帝諱丕，字子桓，武帝太子也。中平四年冬，生于譙。
</original>
</segment>
```

And its accepted Vietnamese rendering (from `runs/tam_quoc_chi/ch02/final.txt`): "Văn hoàng đế huý là Phi, tự Tử Hoàn, thái tử của Vũ Đế. Mùa đông năm Trung Bình thứ tư, sinh ở Tiêu."

A second example of the glossary the translator works from — The Dark Forest (`runs/cixin_liu_the_dark_forest/book_record.json`, en→vi): Luo Ji → La Tập, Zhang Beihai → Trương Bắc Hải, Wu Yue → Ngô Nhạc. A translator who writes "Luo Ji" in Pinyin instead of "La Tập" has broken the book's style rule and fails review.

### 6.3 Valid review comment (example)

```markdown
## Review — tam-quoc-chi-ch02 by <reviewer login>

- **Fidelity:** 4.5/5
- **Fluency:** 4.0/5
- **Terminology:** 4.5/5
- **Register:** 4.0/5
- **Structure:** 5.0/5
- **Completeness:** 4.5/5

Line comment on S-0002: "文皇帝諱丕" is rendered "Văn hoàng đế huý là Phi" —
correct Sino-Vietnamese name, register fits the chronicle. One note: the
commentary footnote numbering in the final text must match the source order.
```

### 6.4 Rejection comment (example)

```markdown
This PR does not enter review: it touches `runs/tam-quoc-chi/book_record.json`,
which is main-agent-only (REQ-COLLAB-004, AC-COLLAB-004-2). Remove that file
from the PR and re-open. The frozen glossary SHA field is also missing from
the PR body.
```

