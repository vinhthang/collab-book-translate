# Specification: Collaborative Multi-Agent Book Translation on GitHub

- **Feature ID**: `2026-11-06-collab-multi-agent-translation`
- **Type**: `enhancement`
- **Status**: `In-Testing`
- **Version**: `1.2.0` (amends v1.1.0 per the Opus round-2 review, 2026-11-06; R2-P1-3 prompt-injection controls deferred to thang, who handles them himself)
- **Sign-off**: `Approved by thang (Proceed, 2026-11-07)`
- **Lineage**:
  - **Extends / Modifies**: `None`
  - **Supersedes**: `None`
- **Governed by**: `spec-driven` skill v2.2.0 (`vinhthang/spec-driven-plugin`, `d029752`, REQ-034–037); this spec follows that standard and does not restate it.

## 1. Problem Statement & User Intent

### 1.1 Observed Repository Facts
`agent-translator` (private, `vinhthang/agent-translator`, `main`) is the tool repository: multi-format ingestion (PDF, EPUB, DOCX, MOBI, Markdown) into canonical bilingual XML segments, translation, modular multi-dimensional evaluation (Fidelity, Fluency, Terminology, Register, Structure, Completeness, 5-point scale), terminology propagation through `book_record.json`, and multi-format compilation (EPUB 3, PDF, DOCX) via `scripts/compile_book.py`. Its showcase is *Tam Quốc Chí*: 18 chapters, 1,185 segments, 391,310 Vietnamese words, 18/18 chapters accepted, average score 4.66/5.00. Its specs live in `docs/specs/` (33 prior packages plus this one, INDEX.md, per-package ID schemes such as `REQ-001..007` and domain-prefixed `REQ-ZH-`, `REQ-2STG-`, `REQ-TERM-`).

The book corpus does NOT live in this repo. `config.json` sets `"workspace_dir": "/Users/thanghoang/github/books"`, and that separate external workspace holds `books/`, `inputs/`, and `runs/`. `books/`, `runs/`, `inputs/`, and `.github/` do not exist at the `agent-translator` repo root. Collaboration therefore happens in a separate public repository, `collab-book-translate`, holding `books/<slug>/`, `runs/<slug>/`, `.github/`, `CONTRIBUTING.md`, and a ledger; the main agent's `workspace_dir` points at a local clone of it. The main agent authenticates to GitHub as its own dedicated bot account (`collab-main-agent`, Maintain role), never as `vinhthang`; `vinhthang` is the only human approver. Post-merge commits (ledger, `bind=True`, `final.txt`) are pushed straight to `main` under a branch-protection bypass granted to the bot account for post-merge commits only.

Engine choice is a model question, not a CLI ban. `cbc` (CodeBuddy CLI) is the harness that calls the models; DeepSeek and other Chinese models are good at Chinese work, so the zh→vi pipeline translates through DeepSeek-v4.1-Flash via `cbc` (dual-envelope prompting, Two-Stage Sino-Vietnamese pipeline — the path Tam Quốc Chí was translated on). `rules/AGENTS.md` restricts `cbc` only for en→vi translation and refinement, which must be authored directly by the Primary Agent; invoking `cbc` (`deepseek-v4.1-flash`, `engine="workbuddy"`) is permitted and required for independent chapter evaluation (`scripts/evaluate.py`); heuristic fallback evaluation never counts as a verified live evaluation. There is no blanket rule against `cbc`. `runs/<book_slug>/book_record.json` is the single source of truth for character names, terminology, and relationship pronouns. Markdown code blocks and image anchors must remain 100% byte-for-byte identical. Provenance engines in code are `agent-offline`, `agent-refine`, `cbc`, `custom` (`scripts/translate_chapter.py`).

Pipeline facts the design depends on: `scripts/orchestrate_run.py` translates chapters in a sequential loop (`for entry in checklist:`) and calls `assert_fiction_pronouns_populated` before any chapter is translated, so a populated `book_record.json` (the `analyze` Brief, characters, `a_to_b` / `b_to_a` pronouns) is required before translation; `update_book_record_from_eval` binds accepted terms after each chapter. The pass threshold is `resolve_pass_threshold` in `scripts/helper.py`: CLI `--threshold` > `config.json` > `brief.genre` (literary/fiction → 3.5, default → 4.0). Any critical issue forces FAIL whatever the score (`scripts/evaluate.py`); multi-pass quorum (`check_critical_quorum`) can yield FAIL / CONTESTED; below 2.5 the `check_redraft` rule applies. `review.vinhthang.dev` (mcp-reviewer task stream) is NOT integrated in this spec; GitHub issues and labels are the only task state.

### 1.2 Authorizing User Intent (Verbatim)

Original prompts (v1.0.0):
1. *"I am preparing for a collab project between many agents, with this spec skill, and the review.vinhthang.dev as the proactive MCP server"*
2. *"or I want to use GitHub and the hook, but the github have limited access, so maybe I want to setup my own git server - with task and transition"*
3. *"the story is, I want to do a book translate, the main again will break the book into trunks, then create task for each trunk, then other agent could take task and translate, make pull request, then other agent could do the review, and comment + make score to the translate work"*
4. *"the main agent can do the final review and merge the request"*
5. *"when all the trunk (chapter) is done, then the work is done, main agent will produce the final book, and publish it"*
6. *"I think github is my choose, since I want the translation is not just me and my agents. I could setup several accounts, and maybe use my own account, each agent can use different label, right?"*
7. *"if yes, no need to just only translate into Vietnamese, we can use git branch for each book, anyone can submit their book, like merge quest to books folder, the main agent can based on that merge branch and work with that book"*
8. *"yes work on the spec now, it will be review by opus, and implement by gemini argon, I think you write the spec, and then review yourself first"*

"Trunks" in prompts 3 and 5 means chapters: one chapter is one task and one translation unit, unless a chapter is split under REQ-COLLAB-004.

Grill-session decisions (Opus review §2, thang's answers, recorded verbatim there):
9. Q1 — Where does collaboration happen? *"Separate repo `collab-book-translate`"*
10. Q2 — Task hub vs GitHub issues: *"Drop the task hub. Trigger by git hook, scheduled fetch, or manual run. Translation takes days, so there is no need for push latency"*
11. Q3 — Agent identity: *"One GitHub account per agent / contributor"*; identity = PR or review author login; `agent:*` labels are informational only
12. Q4 — Terminology under parallelism: *"Seed-then-parallel: whole-book `analyze`, then the main agent translates ch01 as reference, then other chapters open in parallel against a frozen glossary; new terms are *proposals* that only the main agent binds at merge"*
13. Q5 — Translation engine for contributors: *"Engine-agnostic"*, provenance declared in the PR body; quality is gated by review and evaluation; AGENTS.md stays scoped to the local pipeline; the collab repo has its own CONTRIBUTING
14. Q6 — Score of record: *"The main agent evaluates both the reviewer's comments and the translation PR. There can be many PRs per chapter"*, from any GitHub user
15. Q6b — Competing PRs: *"Open competition, soft claim"*: claiming is a courtesy; each PR needs ≥1 independent review; the main agent picks the best PASS PR and closes the rest with a reason; a submission window applies
16. Q7 — Non-Vietnamese targets: *"Split out"*: v1 accepts only `en→vi` and `zh→vi`; other pairs are rejected at intake; a follow-up spec makes the pipeline target-agnostic
17. Q8 — Licence gate: *"The agent pre-screens, a human decides"*; the allowed-licence list lives in CONTRIBUTING.md
18. Q9 — Pilot book: *"A short public-domain English novella (3–5 chapters), en→vi"*, not Tam Quốc Chí
19. Q10 — Publication target: *"GitHub Releases"* (tag per book, EPUB/PDF/DOCX as assets); git keeps text only; large sources go through LFS or a size cap
20. Q11 — Where the main agent runs: first answer was "GitHub Actions"; *withdrawn by thang: "I do not know about github action. ignore it"*. The main agent runs locally on thang's machine with agent-translator as the plugin, triggered by a scheduled fetch or a manual run.
21. Open questions, thang: *"this is agent work, it will be quicker than you estimated, reduce to once a hour, 1 days, and 3 per PR, the collab-book-translate is public"* — hourly sweep (OQ-1), 1-day submission window (OQ-2), 3 REVISE rounds per PR (OQ-3), `collab-book-translate` public (OQ-4).
22. Engine correction, thang (2026-11-06, after the v1.1.0 amendment): *"there is no cbc rule, it is deepseek and other chineze model that I think good at chinese work"* — there is no blanket rule against `cbc`; `cbc` is the harness, DeepSeek and other Chinese models are the engines, and they are good at Chinese work.
23. Round-2 decisions, thang (2026-11-06, via the Opus round-2 grill, review §9.6): **D1** — the main agent uses its own dedicated bot account (e.g. `collab-main-agent`, Maintain role, allowed to push post-merge commits straight to `main`); `vinhthang` is the only human approver. **D2** — ch01 merges on a live `evaluate.py` PASS only; the bot merges ch01 itself with no human step (fastest). **D3** — after 2 days with no PASS candidate, the main agent translates the chapter itself and submits a normal PR; it goes through the same checks and score of record, and other PRs can still compete during the 1-day window.
24. Prompt-injection controls, thang (2026-11-06): *"ignore the p3, I will handle myself"* — Opus round-2 finding R2-P1-3 (prompt injection against the LLM judge) is deferred to thang, who handles it himself; it is not addressed by this amendment.

## 2. Non-Goals (Out of Scope)
- **No task-hub (review.vinhthang.dev) integration in this spec**: GitHub issues and labels are the only task state.
- **No GitHub Actions / CI and no webhook-driven automation**: the main agent runs locally, triggered by a scheduled fetch or a manual run.
- **Own git server**: no Gitea / Forgejo deployment in this spec. The repository layout is remote-agnostic, so a later move off GitHub does not change the workflow.
- **No automatic merge**: no bot merges a translation PR without the main agent's final review.
- **No copyrighted-book ingestion**: books without a public-domain status or an explicit licence are never accepted; the licence decision is human, not autonomous.
- **No target-agnostic pipeline (non-`vi` targets)**: v1 supports only `en→vi` and `zh→vi`; other pairs are a follow-up spec.
- **No audio, image, or web-reader work**: audio pipelines, illustrations, and reader UX are separate existing specs and are unchanged by this spec.
- **No prompt-injection controls in this amendment**: Opus round-2 finding R2-P1-3 (prompt injection against the LLM judge in a public repo) is deferred to thang, who handles it himself; the sweep's deterministic checks are the only content controls specified here.

## 3. Normative Requirements (EARS Syntax)

### `REQ-COLLAB-001` [AMENDED v1.1.0]: Book submission by branch and pull request to `books/` in `collab-book-translate`
- **Source Intent**: Verbatim user prompts 7 and 9 ("use git branch for each book, anyone can submit their book, like merge quest to books folder"; "Separate repo `collab-book-translate`") and Opus review P0-1 / P2-1.
- **Statement**: WHEN a contributor submits a book, THE contributor SHALL open a pull request from a branch named `book/<slug>` (head ref; forks allowed) adding exactly one book under `books/<slug>/` in the public `collab-book-translate` repo, containing `manifest.json` and `source/` with the source book file, and no translated text.
- **Acceptance Criteria**:
  - `AC-COLLAB-001-1` (Happy Path): A submission PR from `book/<slug>` containing `books/<slug>/manifest.json` (slug, title, author, `source_lang`, `target_lang`, `license`, `license_url`, `submitter`) and `books/<slug>/source/` with one readable source file is accepted for intake review.
  - `AC-COLLAB-001-2` (Error / Boundary Case): IF a submission PR touches any path outside `books/<slug>/manifest.json` and `books/<slug>/source/**`, OR contains translated text, OR adds more than one book, OR has a missing or invalid `manifest.json`, OR has an unreadable or oversize source file (over the 50 MB cap, unless stored in LFS), THEN the main agent SHALL reject the PR with a comment naming the defect, and SHALL NOT start intake.

### `REQ-COLLAB-002` [AMENDED v1.2.0]: Main-agent pre-screen and human licence decision
- **Source Intent**: Verbatim user prompts 7 and 17 ("the main agent can based on that merge branch and work with that book"; "The agent pre-screens, a human decides"), Opus review P1-4, and thang's decision D1 (intent 23: dedicated bot account; `vinhthang` the only human approver).
- **Statement**: WHEN a book submission PR is open, THE main agent SHALL post a pre-screen comment covering manifest schema, format readable by `extract.py`, language check, supported-pair check, and a licence assessment, and THE PR SHALL be merged only after a human approval from the login `vinhthang`, distinct from the main agent's bot account.
- **Acceptance Criteria**:
  - `AC-COLLAB-002-1` (Happy Path): A submission whose licence is on the allowed-licence list in CONTRIBUTING.md, whose source format is one of PDF, EPUB, DOCX, MOBI, or Markdown, whose language check matches `manifest.json` `source_lang`, and whose pair is supported, receives a passing pre-screen comment and a human approval, and is merged.
  - `AC-COLLAB-002-2` (Error / Boundary Case): IF the licence is missing, unclear, or not on the allowed list, OR the format is unsupported, OR the language check disagrees with the manifest, OR the pair is unsupported, THEN the main agent SHALL NOT merge, SHALL comment with the failing check, and SHALL close the PR or request changes; a PR without human approval SHALL NOT be merged even if the pre-screen passes. An approval from the bot account alone SHALL NOT count as human approval.

### `REQ-COLLAB-003` [AMENDED v1.2.0]: Main agent seeds, then opens chapter issues in parallel
- **Source Intent**: Verbatim user prompts 3 and 12 ("the main again will break the book into trunks, then create task for each trunk"; "Seed-then-parallel"), Opus review P0-3 / P2-2, and thang's decision D2 (intent 23: ch01 merges on a live `evaluate.py` PASS only, bot merges it itself, no human step).
- **Statement**: WHEN a book is merged into `books/<slug>/`, THE main agent SHALL ingest it into `runs/<slug>/` with the existing extractor, run whole-book `analyze` (Brief, glossary, pronouns), translate ch01 as the reference chapter and merge it, freeze the glossary and record its commit SHA, and create one GitHub tracking issue per remaining chapter (ch02..chNN), each naming its chunk ID (`<slug>-chNN`), source range as segment IDs `seg_first..seg_last`, target language, and frozen glossary SHA. The ch01 seed PR is authored by the main agent's bot account and merged by the bot on a live `evaluate.py` PASS only, with no human step (D2 — an explicit exception to REQ-COLLAB-005 non-author review and REQ-COLLAB-013 window and selection, for ch01 only); the merge comment lists the glossary terms bound by the seed so thang can audit them afterwards.
- **Acceptance Criteria**:
  - `AC-COLLAB-003-1` (Happy Path): After ingestion and analyze, ch01 is merged as reference by the bot on a live, non-fallback `evaluate.py` PASS with zero critical issues and all sweep checks passing, the merge comment lists the glossary terms bound by the seed, the glossary is frozen with a recorded SHA, and every remaining chapter has exactly one tracking issue, covering every remaining chapter exactly once.
  - `AC-COLLAB-003-2` (Error / Boundary Case): IF ingestion fails for a chapter, OR `analyze` fails, OR a chapter has zero segments, THEN the main agent SHALL NOT create an issue for that chapter, SHALL record the failure against the book, and SHALL report it to the user instead of silently skipping the chapter; IF the ch01 evaluation is fallback or FAIL, THEN ch01 SHALL NOT be merged and no chapter issues SHALL be opened.

### `REQ-COLLAB-004` [AMENDED v1.2.0]: Open competition; soft claim; PR per chunk
- **Source Intent**: Verbatim user prompts 3, 6, 11, and 15 ("other agent could take task and translate, make pull request"; "each agent can use different label, right?"; "One GitHub account per agent / contributor"; "Open competition, soft claim"), Opus review P0-4 / P2-3 / P2-9, and Opus round-2 finding R2-P1-2 (contributor file contract).
- **Statement**: WHEN a chapter issue is open, ANY GitHub user SHALL be able to open a translation PR from a branch named `translate/<slug>-<chunk>-<agent>` containing only that chapter's `translation.xml` and, optionally, `runs/<slug>/chNN/analyze_additions.json` with term proposals, with a PR body stating Agent (GitHub login), Engine/Model provenance, source language, target language, chunk ID, issue number, and frozen glossary SHA; claiming SHALL be a soft courtesy via a `/claim` issue comment, and multiple PRs per chapter SHALL be allowed. The one contributor file is `runs/<slug>/chNN/translation.xml`: its segments are exactly the ids and types of `chNN/source.txt`, in order; each `<segment>` keeps `<original>` byte-identical and adds `<translate>`; `<hanviet>` is required for `zh→vi`. A REVISE push overwrites the same file. The main agent derives `round<R>_*.txt` / `final.txt` after merge, in the post-merge commit. `chNN/source.txt` is committed by the main agent at ingestion, so contributors can read it.
- **Acceptance Criteria**:
  - `AC-COLLAB-004-1` (Happy Path): A PR from `translate/<slug>-<chunk>-<agent>` with a complete PR body, only that chapter's files changed, and the frozen glossary SHA recorded, enters review; a second PR for the same chapter also enters review.
  - `AC-COLLAB-004-2` (Error / Boundary Case): IF a PR changes files outside `runs/<slug>/chNN/{translation.xml, analyze_additions.json}`, OR touches `book_record.json` or `checklist.json`, OR the PR body is missing Agent, Engine/Model, languages, chunk ID, issue number, or glossary SHA, OR `translation.xml` does not contain exactly the segments of `chNN/source.txt` in order with `<original>` byte-identical, OR a `zh→vi` segment lacks `<hanviet>`, THEN the PR SHALL NOT enter review, and the main agent SHALL comment naming the defect. A PR SHALL NOT be rejected merely because another PR for the same chapter already exists.
- **Open question [PROVISIONAL] (1 sentence, owner-deferred)**: Whether a chapter above the existing orchestrator batch limits is split into smaller tasks, and at what segment threshold, is undecided and belongs to the design phase; with a novella pilot it is not exercised, and a split chunk ID is `<slug>-chNN-pK`.

### `REQ-COLLAB-005` [AMENDED v1.1.0]: Independent review by login, score as input
- **Source Intent**: Verbatim user prompts 3, 11, and 14 ("other agent could do the review, and comment + make score to the translate work"; "One GitHub account per agent / contributor"; "The main agent evaluates both the reviewer's comments and the translation PR") and Opus review P1-1 / P2-5.
- **Statement**: WHEN a translation PR is in review, THE reviewer SHALL be any GitHub user whose login differs from the PR author's login, SHALL post at least one line comment and a 6-dimension score block (Fidelity, Fluency, Terminology, Register, Structure, Completeness, 5-point scale), and THE main agent SHALL judge the review's quality and weigh the reviewer's score as an input, not as the score of record.
- **Acceptance Criteria**:
  - `AC-COLLAB-005-1` (Happy Path): A review by a login different from the PR author, with at least one line comment and a complete 6-dimension score block, counts as a valid review of that PR.
  - `AC-COLLAB-005-2` (Error / Boundary Case): IF the reviewer login equals the PR author login, OR the review has no line comment, OR the score block is missing or incomplete, THEN the review SHALL be treated as invalid and SHALL NOT count toward a merge decision. The main agent SHALL count valid reviews itself and SHALL NOT rely on GitHub required approvals, which outside reviewers without write access cannot satisfy.

### `REQ-COLLAB-006` [AMENDED v1.1.0]: Main-agent final review, score of record, merge
- **Source Intent**: Verbatim user prompts 4 and 14 ("the main agent can do the final review and merge the request"; "The main agent evaluates both the reviewer's comments and the translation PR") and Opus review P1-1 / P2-4.
- **Statement**: WHEN a translation PR has at least one valid review, THE main agent SHALL run `scripts/evaluate.py` (WorkBuddy live, not fallback) on the PR to produce the score of record, SHALL apply the `resolve_pass_threshold` threshold with critical-issue and quorum overrides, SHALL verify zero unresolved comments on the selected PR only, and SHALL merge the selected PR only when the score of record passes.
- **Acceptance Criteria**:
  - `AC-COLLAB-006-1` (Happy Path): A PR whose score of record passes the resolved threshold, with no critical issue, no quorum failure, and zero unresolved comments, is a PASS candidate for selection.
  - `AC-COLLAB-006-2` (Error / Boundary Case): IF the score of record is FAIL and `check_redraft` does not apply, THEN the PR SHALL be returned to REVISE; IF the score of record is FAIL and `check_redraft` applies, THEN the PR SHALL be closed as REASSIGN and the chapter SHALL stay open; IF a review comment is unresolved on the selected PR, THEN the main agent SHALL NOT merge it.

### `REQ-COLLAB-007` [AMENDED v1.1.0]: v1 language pairs are only `en→vi` and `zh→vi`
- **Source Intent**: Verbatim user prompt 16 ("Split out": v1 accepts only `en→vi` and `zh→vi`) and Opus review P1-3.
- **Statement**: WHEN a book is submitted, THE main agent SHALL accept only the pairs `en→vi` and `zh→vi`, declared in `manifest.json`, and SHALL reject any other pair at intake.
- **Acceptance Criteria**:
  - `AC-COLLAB-007-1` (Happy Path): A book whose manifest declares `en→vi` or `zh→vi` is accepted for translation into Vietnamese.
  - `AC-COLLAB-007-2` (Error / Boundary Case): IF `target_lang` is not `vi`, OR `source_lang` is not `en` or `zh`, THEN the main agent SHALL reject the submission at intake with `unsupported pair` and SHALL NOT create chapter issues for that book.

### `REQ-COLLAB-008` [DEPRECATED]: Task-hub integration
- **Source Intent**: Deprecated per verbatim user prompt 10 ("Drop the task hub") and Opus review P0-2. The v1.0.0 requirement (task-hub extension for translation work) is retired in full; GitHub issues and labels are the only task state.
- **Statement**: This requirement is DEPRECATED and SHALL NOT be implemented. No task-hub (review.vinhthang.dev) integration exists in this spec.
- **Acceptance Criteria**: None. A deprecated requirement carries no ACs (round-2 amendment, R2-P2-4); its Gate 2 rows are marked N/A.

### `REQ-COLLAB-009` [AMENDED v1.1.0]: Frozen glossary, proposals bound only at merge
- **Source Intent**: Verbatim user prompt 12 ("Seed-then-parallel"; new terms are proposals that only the main agent binds at merge) and Opus review P0-3 / P2-3.
- **Statement**: WHEN chapters are translated in parallel, THE translators SHALL translate against the frozen glossary snapshot recorded in their chapter issue, MAY add term proposals in `runs/<slug>/chNN/analyze_additions.json` with `status: proposed` (the existing `bind=False` path), and THE main agent SHALL bind approved proposals with `bind=True` at merge; `book_record.json` SHALL be writable only by the main agent.
- **Acceptance Criteria**:
  - `AC-COLLAB-009-1` (Happy Path): A proposal in `analyze_additions.json` with `status: proposed` is bound by the main agent at merge with `bind=True` and appears in `book_record.json` for later chapters.
  - `AC-COLLAB-009-2` (Error / Boundary Case): IF a proposal conflicts with a term already bound since the frozen snapshot, THEN the PR SHALL be returned to REVISE; IF any PR touches `book_record.json`, THEN that PR SHALL NOT enter review.

### `REQ-COLLAB-010` [AMENDED v1.1.0]: Final assembly and publication as a GitHub Release
- **Source Intent**: Verbatim user prompts 5 and 19 ("when all the trunk (chapter) is done, then the work is done, main agent will produce the final book, and publish it"; "GitHub Releases") and Opus review P1-10 / P2-6.
- **Statement**: WHEN every chapter issue of a book is Done, THE main agent SHALL verify completeness (every chapter merged, segment counts matching via the existing segment-parity and byte-invariance checks), compile the book with the existing compiler into EPUB 3, PDF, and DOCX, and publish them as assets of a GitHub Release tagged `<slug>-v<N>`; compiled binaries SHALL never be committed to git.
- **Acceptance Criteria**:
  - `AC-COLLAB-010-1` (Happy Path): A book with all chapters Done compiles into EPUB 3, PDF, and DOCX, each non-empty, with a complete table of contents, and is published as a GitHub Release `<slug>-v<N>` with those three assets.
  - `AC-COLLAB-010-2` (Error / Boundary Case): IF any chapter is not Done, OR segment counts do not match, THEN the main agent SHALL NOT publish, SHALL list the incomplete chapters, and SHALL NOT mark the book done; IF a compiled binary is committed to git, THEN it SHALL be removed and the release flow SHALL be used instead.

### `REQ-COLLAB-011` [DEPRECATED]: Spec process roles as a requirement
- **Source Intent**: Deprecated per Opus review P1-7. The v1.0.0 requirement (all agents work under the spec-driven skill, reviewed by Opus, implemented by Gemini Argon) describes process, not system behaviour; the roles live in ADR-COLLAB-007 and the spec's `Sign-off` line, and the spec-driven skill already governs the process.
- **Statement**: This requirement is DEPRECATED and SHALL NOT be implemented as a system requirement. The roles (Muse authors, Opus reviews, Gemini Argon implements, thang Proceeds) are recorded in ADR-COLLAB-007 and in this spec's `Sign-off` line.
- **Acceptance Criteria**: None. A deprecated requirement carries no ACs (round-2 amendment, R2-P2-4); its Gate 2 rows are marked N/A.

### `REQ-COLLAB-012` [AMENDED v1.1.0]: Audit trail as an in-repo ledger
- **Source Intent**: Derived from the task-stream spec `2026-10-28-task-stream` (REQ-TS-006 audit logging) and Opus review P2-7.
- **Statement**: WHEN a chapter is merged, THE main agent SHALL write, in a post-merge commit, a ledger entry in `runs/<slug>/collab_ledger.json` recording the issue number, selected PR number, rejected PR numbers, translator login, reviewer logins, reviewer score, score of record, verdict, merge SHA, and glossary SHA, so the chapter's history reconstructs end to end.
- **Acceptance Criteria**:
  - `AC-COLLAB-012-1` (Happy Path): For a merged chapter, the issue, selected PR, rejected PRs, logins, reviewer score, score of record, verdict, merge SHA, and glossary SHA are all recorded in the ledger and mutually linked.
  - `AC-COLLAB-012-2` (Error / Boundary Case): IF any link in that chain is missing for a merged chapter, THEN the merge SHALL be flagged as unaudited and SHALL be reported to the main agent instead of being treated as done.

### `REQ-COLLAB-013` [AMENDED v1.2.0]: Competing-PR selection
- **Source Intent**: Verbatim user prompts 14 and 15 ("There can be many PRs per chapter"; "Open competition, soft claim"), Opus review P1-2 / §6 OQ-2 ("1 day (24 h) after the first PASS candidate"), Opus round-2 findings R2-P2-7 (window clock) and R2-P2-8 (starved chapter), and thang's decision D3 (intent 23).
- **Statement**: WHEN a chapter has at least one PASS candidate PR, THE main agent SHALL wait a submission window of 1 day (24 h) after the first PASS candidate, THEN select the best PASS PR by deterministic ranking (highest score-of-record weighted total, tie-break by fewer critical/major issues, then earliest PR), merge it, and close every non-selected PR with a comment giving its score and the reason. The window starts at the sweep timestamp that first labels a PR `status:candidate`, recorded in an issue comment and the ledger; later sweeps read it and never recompute it. IF no PR reaches PASS within 2 days of the chapter opening, THEN the main agent translates the chapter itself and submits a normal PR (D3), which goes through the same checks and score of record, and other PRs can still compete during the 1-day window.
- **Acceptance Criteria**:
  - `AC-COLLAB-013-1` (Happy Path): A chapter with two or more PASS candidate PRs has exactly one merged (the highest-ranked) and every other closed with its score and the reason.
  - `AC-COLLAB-013-2` (Error / Boundary Case): IF the window has not yet closed, THEN the main agent SHALL NOT select or merge; IF two PRs tie on score and issues, THEN the earliest PR SHALL be selected; IF no PR has reached PASS within 2 days of the chapter opening, THEN the main agent SHALL translate the chapter itself and submit a normal PR, and SHALL NOT close the chapter as untranslatable.

### `REQ-COLLAB-014` [AMENDED v1.2.0]: Workflow state machine, labels, revision cap
- **Source Intent**: Verbatim user prompt 2 ("with task and transition"), Opus review P1-5 / §4 / §6 OQ-3 ("3 REVISE rounds per PR"), and Opus round-2 finding R2-P2-2 ("CI" wording contradicts the no-CI Non-Goal).
- **Statement**: WHEN a chapter or a candidate PR moves through the workflow, THE system SHALL follow the state machine in design.md §3.5 (chapter issue: Seeding → Open → Candidates → Done; candidate PR: NeedsChecks → NeedsReview → Evaluating → Revise / Reassign / Candidate → Merged / NotSelected), SHALL apply `status:*` labels only by the main agent, and SHALL allow at most 3 REVISE rounds per PR, after which the PR is closed as REASSIGN and the chapter stays open.
- **Acceptance Criteria**:
  - `AC-COLLAB-014-1` (Happy Path): A chapter and its PRs move through the defined states with the defined labels, and a PR in its third REVISE round that is pushed again re-enters NeedsChecks.
  - `AC-COLLAB-014-2` (Error / Boundary Case): IF a PR reaches a fourth REVISE round, THEN it SHALL be closed as REASSIGN and the chapter SHALL stay open; IF a `status:*` label is applied by anyone other than the main agent, THEN it SHALL be corrected by the main agent on the next sweep.

### `REQ-COLLAB-015` [AMENDED v1.2.0]: Runtime model — local main agent, idempotent sweep
- **Source Intent**: Verbatim user prompts 10, 20, and 21 ("Drop the task hub. Trigger by git hook, scheduled fetch, or manual run"; "I do not know about github action. ignore it"; "reduce to once a hour"), Opus review P1-6 / §6 OQ-1 (hourly scheduled fetch), and Opus round-2 findings R2-P2-5 (lock timeout) and R2-P2-6 (one-state latency).
- **Statement**: WHEN the main agent works, IT SHALL run locally on thang's machine with agent-translator as the plugin, with `workspace_dir` pointing at a local clone of `collab-book-translate`, and SHALL be triggered by an hourly scheduled fetch or a manual run; each run SHALL be an idempotent sweep holding a lock, doing `git fetch`, listing open PRs, issues, and reviews through `rtk gh … --json`, advancing each item until it blocks on external input (review, push, window) per REQ-COLLAB-014, and writing the ledger; no GitHub Actions and no inbound webhook SHALL be used.
- **Acceptance Criteria**:
  - `AC-COLLAB-015-1` (Happy Path): An hourly scheduled fetch or a manual run advances each open item until it blocks on external input (review, push, window), writes the ledger, and leaves no item processed twice when sweeps overlap.
  - `AC-COLLAB-015-2` (Error / Boundary Case): IF a sweep overlaps another sweep, THEN the second SHALL attempt a non-blocking lock acquire, and if the lock is held it SHALL log "sweep already running" and exit 0 without double-processing any PR; IF a deterministic check fails (path allowlist, one-chapter diff, PR body fields, segment parity, byte-invariance, `book_record.json` untouched), THEN the failure SHALL be reported as a PR comment in that sweep.
