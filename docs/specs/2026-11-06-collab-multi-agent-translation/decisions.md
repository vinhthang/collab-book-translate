# Decisions: Collaborative Multi-Agent Book Translation on GitHub

- **Feature ID**: `2026-11-06-collab-multi-agent-translation`
- **Status**: `Planning`

Append new decisions; mark old ones superseded, never edit them away. spec.md outranks this document on conflict.

## ADR-COLLAB-001: GitHub as the collaboration surface, own git server deferred

- **Status**: Accepted
- **Governs**: REQ-COLLAB-001, REQ-COLLAB-002, REQ-COLLAB-004, REQ-COLLAB-006
- **Context**: The user considered GitHub, whose access is limited for outside contributors, against setting up his own git server with task and transition support. The repo is currently private.
- **Options Considered**:
  1. **GitHub (chosen)**: Use GitHub branches, pull requests, issues, and labels as the collaboration surface. PR review, comments, and merge already exist; agents already know the flow.
  2. **Own git server (Gitea / Forgejo on the k3s cluster)**: Unlimited collaborators and the same PR workflow, self-hosted.
- **Decision**: Use GitHub. The repository layout is remote-agnostic, so a later move does not change the workflow.
- **Trade-offs & Risks**: GitHub access is the limiting factor: outside contributors need accounts, tokens, and repo access, and a private repo needs invitations or a public switch. A self-hosted server removes that limit but adds deployment, backup, auth, and maintenance that this spec does not pay for. The risk is that a contributor is blocked by access; the mitigation is the label + PR-template identity model in REQ-COLLAB-004 and the open question on repo visibility (see below).
- **Rejected Alternatives**: Own git server now (rejected — build the collaboration first, move only if access actually blocks a contributor).
- **Blast Radius**: `books/`, PR templates, labels, task hub repo allowlist.
- **Open question**: Whether `agent-translator` becomes public or stays private with invited collaborators is undecided and belongs to thang; this spec does not decide it.

## ADR-COLLAB-002: `books/` intake plus `runs/` working layout

- **Status**: Superseded by ADR-COLLAB-008 (v1.1.0, Opus review P0-1)
- **Governs**: REQ-COLLAB-001, REQ-COLLAB-002, REQ-COLLAB-003, REQ-COLLAB-010
- **Context**: The repo already has `runs/<slug>/` as the working layout (chapters, `book_record.json`, `checklist.json`, compiled deliverables) and no intake folder for submitted books.
- **Options Considered**:
  1. **`books/` intake, `runs/` working (chosen)**: Submissions land in `books/<slug>/` (manifest + source only); the main agent ingests them into `runs/<slug>/` for translation work.
  2. **Translate directly in `books/`**: No separate working layout; translation happens where the book was submitted.
- **Decision**: Keep the two layouts separate. Submission PRs contain source only; translation PRs change only `runs/<slug>/` chapter files.
- **Trade-offs & Risks**: Two layouts add a step (ingestion), but it keeps the review boundary clean: a submission review checks licence and format, a translation review checks language. Mixing them would let unreviewed translations enter with a submission.
- **Rejected Alternatives**: Translating directly in `books/` (rejected — it erases the boundary between an accepted book and an accepted translation).
- **Blast Radius**: `books/`, `runs/`, `scripts/extract.py`, `scripts/orchestrate_run.py`.

## ADR-COLLAB-003: One chapter per chunk, split rule provisional

- **Status**: Accepted
- **Governs**: REQ-COLLAB-003, REQ-COLLAB-004
- **Context**: The user said the main agent breaks the book into "trunks", then clarified those are chapters. The existing pipeline already works chapter by chapter (`ch01`..`chNN`, XML segments).
- **Options Considered**:
  1. **One chapter per task (chosen)**: A chapter is the task, the PR, and the review unit.
  2. **Fixed-size segment chunks**: Split every book into equal segment counts regardless of chapter boundaries.
- **Decision**: One chapter per task. A chapter above the orchestrator's batch limits is split, with the threshold left [PROVISIONAL] in REQ-COLLAB-004.
- **Trade-offs & Risks**: Chapters vary in length, so tasks vary in size, but chapters are the natural reading and review unit and match every existing spec and tool in the repo. Fixed-size chunks would be uniform but would split chapters mid-scene and break the existing chapter tooling.
- **Rejected Alternatives**: Fixed-size segment chunks (rejected — breaks chapter tooling and review coherence).
- **Blast Radius**: Task creation, tracking issues, PR naming, `checklist.json`.

## ADR-COLLAB-004: Reviewer is never the translator; score on the existing 5-point scale

- **Status**: Superseded by ADR-COLLAB-009 (v1.1.0, Opus review P1-1)
- **Governs**: REQ-COLLAB-005, REQ-COLLAB-006, REQ-COLLAB-009
- **Context**: The user wants another agent to review, comment, and score each translation, and the main agent to do the final review and merge. The repo already has a multi-dimensional 5-point evaluation (Fidelity, Fluency, Terminology, Register, Structure, Completeness) with pass at >= 4.0, proven on Tam Quốc Chí at 4.66/5.00.
- **Options Considered**:
  1. **Existing 5-point scale, reviewer != translator (chosen)**: Reuse the evaluation the repo already has; a review only counts when the reviewer is a different agent from the translator.
  2. **New 0–100 rubric**: A fresh weighted score (accuracy 40, fluency 30, terminology 20, formatting 10).
- **Decision**: Reuse the existing 5-point scale and verdicts PASS / REVISE / REASSIGN. Do not invent a second scoring system.
- **Trade-offs & Risks**: The 5-point scale is coarser than a 0–100 rubric, but it is the scale every existing chapter was judged on, so scores stay comparable across books and across the old single-agent work and the new collab work. A new rubric would orphan all historical scores.
- **Rejected Alternatives**: New 0–100 rubric (rejected — breaks comparability with the existing corpus).
- **Blast Radius**: `scripts/evaluate.py`, PR review comments, task score records, `book_record.json`.

## ADR-COLLAB-005: Extend the task hub, keep single-consumer SSE

- **Status**: Superseded by ADR-COLLAB-010 (v1.1.0, Opus review P0-2)
- **Governs**: REQ-COLLAB-008, REQ-COLLAB-012
- **Context**: `review.vinhthang.dev` is the proactive task server: SQLite queue, SSE stream, ack/pending, kind allowlist starting with `fix-review-findings`, repo allowlist without `agent-translator`, 512 KB cap, exactly one SSE consumer.
- **Options Considered**:
  1. **Extend kinds + allowlist + claim/score records, keep single-consumer SSE (chosen)**: Add `translate-chunk` and `review-translation`, add `vinhthang/agent-translator`, record claims and scores; other agents claim and poll via `GET /tasks/pending` and GitHub.
  2. **Multi-consumer SSE**: Let every agent hold its own stream and claim tasks over it.
- **Decision**: Extend, do not rebuild. Multi-consumer streaming is explicitly out of scope for this spec.
- **Trade-offs & Risks**: Polling is less immediate than a stream, but it needs no server redesign and no change to the proven single-consumer path that Muse relies on. Multi-consumer SSE would need per-consumer delivery tracking, claim races, and a new ack model — real work, deferred until the collab actually needs it.
- **Rejected Alternatives**: Multi-consumer SSE now (rejected — deferred, see Non-Goals).
- **Blast Radius**: mcp-reviewer `taskstore.py`, `taskstream.py`, `app.py`; repo allowlist config; Muse-side listener unchanged.

## ADR-COLLAB-006: Language pair is manifest metadata

- **Status**: Amended by ADR-COLLAB-011 (v1.1.0, Opus review P1-3)
- **Governs**: REQ-COLLAB-007
- **Context**: The pipeline was built English and Chinese to Vietnamese, and the user explicitly said translation need not be only into Vietnamese.
- **Options Considered**:
  1. **Manifest metadata (chosen)**: `source_lang` and `target_lang` live in each book's `manifest.json`; the pipeline reads them.
  2. **Hardcoded Vietnamese target with per-book overrides**: Keep Vietnamese as the default and override per book.
- **Decision**: Manifest metadata, no default target language in code.
- **Trade-offs & Risks**: Every book must declare its pair, and an unsupported pair fails loudly at intake instead of silently producing Vietnamese. That is the point: a silent default would mistranslate a book whose pair was never checked.
- **Rejected Alternatives**: Hardcoded Vietnamese with overrides (rejected — a missed override silently produces the wrong language).
- **Blast Radius**: `manifest.json` schema, `scripts/extract.py`, translation and evaluation prompts, `book_record.json`.

## ADR-COLLAB-007: Roles — Muse authors, Opus reviews, Gemini Argon implements, thang Proceeds

- **Status**: Accepted
- **Governs**: REQ-COLLAB-011
- **Context**: The user directed this spec to be written by Muse, reviewed by Opus, and implemented by Gemini Argon, with Muse reviewing itself first.
- **Options Considered**:
  1. **Separated roles (chosen)**: Muse authors the spec and self-reviews it first; Opus independently reviews the package; Gemini Argon implements only after human Proceed; thang is the Proceed gate and conflict resolver.
  2. **One agent does all**: The same agent authors, reviews, and implements.
- **Decision**: Separated roles. No agent reviews its own implementation, and no implementation starts before Proceed.
- **Trade-offs & Risks**: Handoffs cost time and need named versions (hashes) so a reviewer checks a fixed package, not a moving target. The gain is independence: Opus can reject a spec Muse wrote, and Gemini Argon implements a spec it did not author.
- **Rejected Alternatives**: One agent doing all (rejected — it removes the independence the user asked for).
- **Blast Radius**: This spec package, its review, and its implementation.

## Gate 1 Self-Validation (2026-11-06, author: Muse)

| Check | Result | Evidence |
|---|---|---|
| 1. Codebase Grounding | PASS | spec.md §1.1 states verified repo facts: private repo, 34 spec packages, Tam Quốc Chí 18 chapters / 1,185 segments / 391,310 words / 4.66/5.00, task-stream kind and repo allowlists, 512 KB cap, single SSE consumer |
| 2. User Intent & Non-Goals | PASS | 12/12 REQs carry a Source Intent citation (8 verbatim user prompts, 2 derived from the README / task-stream spec, both labelled Derived); §2 Non-Goals present (own git server, new MCP tools, multi-consumer SSE, auto-merge, copyrighted books, audio/image/reader) |
| 3. EARS & Falsifiable ACs | PASS | 12/12 REQs in WHEN / THE / SHALL form; 12 Happy-Path ACs and 12 Error/Boundary ACs, each naming the check or the rejection |
| 4. Zero Deferral Placeholders | PASS | No TODO / TBD / FIXME in spec.md or decisions.md; one [PROVISIONAL] tag, scoped to the chapter-split open question in REQ-COLLAB-004, one sentence, owner-deferred to the design phase |
| 5. Anti-Strawman ADR Debate | PASS | 7/7 ADRs carry Options Considered, Trade-offs & Risks, and Rejected Alternatives |
| 6. Cross-Spec Lineage & Cascade | PASS | spec.md header carries Type, Status Planning, Lineage (Extends/Modifies None, Supersedes None), Governed by spec-driven v2.2.0; INDEX.md row added for this package |
| **Overall Gate 1** | **PASS** | All 6 checks pass; derived files may now be authored |

## Self-Review by the Author (2026-11-06, before Opus review)

Muse reviewed its own package first, as the user directed, and fixed these findings in the package before handing it to Opus. Full review record: `~/workspace/drafts/collab-multi-agent-translation-self-review-2026-11-06.md` on Muse's computer.

| Finding | Fix applied |
|---|---|
| F-01: §1.2 said a split chapter was "under REQ-COLLAB-003"; the split rule lives in REQ-COLLAB-004 | Wording corrected to REQ-COLLAB-004 |
| F-02: The [PROVISIONAL] tag sat on the whole REQ-COLLAB-004 header, overstating what is deferred | Tag moved to the split open-question line only; the rest of REQ-COLLAB-004 is normative |
| F-03: Label taxonomy (agent / lang / book) was agreed in chat but absent from the spec | REQ-COLLAB-004 now requires `agent:<name>`, `lang:<source>-<target>`, `book:<slug>` labels on PR and tracking issue, checked in AC-COLLAB-004-1/2 |
| F-04: "Publish" in REQ-COLLAB-010 had no destination | AC-COLLAB-010-1 now defines publish as compiled files in `runs/<slug>/`, discoverable by the web reader |
| F-05: INDEX.md had no row for this package (Gate 1 Check 6) | Row added, Status Planning, REQ-COLLAB-001..012 |

Open for Opus to judge, not fixed by the author: (a) whether the single-consumer SSE limit in REQ-COLLAB-008 is acceptable for the first collab, or multi-consumer streaming should be in scope; (b) whether `agent-translator` should go public or stay private with invited collaborators; (c) the chapter-split segment threshold, deferred as [PROVISIONAL].

All three were resolved by the Opus review and thang's grill decisions in v1.1.0: (a) the task hub is dropped entirely (ADR-COLLAB-010); (b) `agent-translator` stays private, `collab-book-translate` is public (ADR-COLLAB-008); (c) the split threshold stays [PROVISIONAL] and is not exercised by the novella pilot.

## ADR-COLLAB-008: Separate public repo `collab-book-translate` (supersedes ADR-COLLAB-002)

- **Status**: Accepted (v1.1.0)
- **Governs**: REQ-COLLAB-001, REQ-COLLAB-002, REQ-COLLAB-003, REQ-COLLAB-010, REQ-COLLAB-015
- **Context**: Opus review P0-1: `books/`, `runs/`, `inputs/`, and `.github/` do not exist in `agent-translator`; the corpus lives in the external workspace `<external_books_dir>` (`config.json` `workspace_dir`).
- **Options Considered**:
  1. **Separate public repo `collab-book-translate` (chosen)**: Holds `books/<slug>/`, `runs/<slug>/`, `.github/`, `CONTRIBUTING.md`, and the ledger. The main agent's `workspace_dir` points at a local clone of it. `agent-translator` stays private as the tool repo.
  2. **Move the corpus into `agent-translator`**: Add `books/` and `runs/` to the tool repo and make it public.
- **Decision**: Separate repo. The tool repo and the book corpus have different audiences, licences, and visibility.
- **Trade-offs & Risks**: Two repos to maintain, and the main agent must keep its local clone in sync. The gain is clean separation: contributors never see the private tool code, and the tool repo never carries book binaries or public-domain text.
- **Rejected Alternatives**: Moving the corpus into `agent-translator` (rejected — it would force the tool repo public and mix tool code with book data).
- **Blast Radius**: New repo `collab-book-translate`; `config.json` `workspace_dir`; `scripts/extract.py` paths.

## ADR-COLLAB-009: Scripted score of record, reviewer score as input (supersedes ADR-COLLAB-004)

- **Status**: Accepted (v1.1.0)
- **Governs**: REQ-COLLAB-005, REQ-COLLAB-006, REQ-COLLAB-013
- **Context**: Opus review P1-1: the fixed "PASS at ≥ 4.0" contradicts `resolve_pass_threshold` (CLI > config.json > brief.genre, literary/fiction → 3.5, default → 4.0); critical issues force FAIL whatever the score; quorum can yield FAIL / CONTESTED; the verdict vocabulary PASS / REVISE / REASSIGN does not exist in code (code has PASS / FAIL and `check_redraft` below 2.5); a free-form agent score is not comparable to WorkBuddy's 4.66.
- **Options Considered**:
  1. **Scripted evaluator as score of record (chosen)**: The main agent runs `scripts/evaluate.py` (WorkBuddy live, not fallback) on each candidate PR; threshold = `resolve_pass_threshold`; critical/quorum override. The reviewer's 6-dimension score and line comments are inputs the main agent weighs, and the main agent also judges the review itself.
  2. **Reviewer score as score of record**: The reviewer's score decides PASS / FAIL directly.
- **Decision**: Scripted score of record. Verdict mapping: PASS → candidate; FAIL ∧ ¬`check_redraft` → REVISE; FAIL ∧ `check_redraft` → REASSIGN (close the PR, chapter stays open).
- **Trade-offs & Risks**: Scoring needs a live WorkBuddy run per candidate PR, which is slower than reading a reviewer's number, but it is the only score comparable across books and with the existing corpus. A reviewer's score alone is not reproducible.
- **Rejected Alternatives**: Reviewer score as score of record (rejected — not comparable, not reproducible, contradicts the genre threshold).
- **Blast Radius**: `scripts/evaluate.py`, `scripts/helper.py` (`resolve_pass_threshold`), PR review flow, ledger score fields.

## ADR-COLLAB-010: GitHub issues as the sole task state (supersedes ADR-COLLAB-005)

- **Status**: Accepted (v1.1.0)
- **Governs**: REQ-COLLAB-003, REQ-COLLAB-014, REQ-COLLAB-015
- **Context**: Opus review P0-2: the task hub would be a second source of truth that outside contributors cannot reach (Bearer auth), "max 3 deliveries then dead" conflicts with claims lasting days, and a hub task plus a GitHub issue per chapter is two state machines to keep in sync. Also the spec header said `Modifies: None` while design §1.3 claimed a `[MODIFIES]` delta on the mcp-reviewer task-stream spec.
- **Options Considered**:
  1. **GitHub issues only (chosen)**: Issues and labels are the only task state; the trigger is an hourly scheduled fetch or a manual run by the local main agent.
  2. **Keep the task hub and sync it with GitHub issues**: Hub tasks mirror issue state.
- **Decision**: GitHub issues only. REQ-COLLAB-008 is deprecated; no hub code, route, or task kind is created.
- **Trade-offs & Risks**: No push latency — a contributor waits up to an hour for the next sweep — but translation takes days, so push latency buys nothing. The gain is one state machine, reachable by every contributor.
- **Rejected Alternatives**: Hub-plus-sync (rejected — two state machines, one unreachable by contributors, and a cross-repo lineage the spec never declared).
- **Blast Radius**: `collab-book-translate` issues and labels; the main-agent sweep (REQ-COLLAB-015); mcp-reviewer untouched.

## ADR-COLLAB-011: v1 supported pairs `{en→vi, zh→vi}` only (amends ADR-COLLAB-006)

- **Status**: Accepted (v1.1.0)
- **Governs**: REQ-COLLAB-007
- **Context**: Opus review P1-3: 32 files under `scripts/`, `skills/`, and `agents/` hardcode Vietnamese (`"target_lang": "vi"`, Vietnamese-diacritic language detection in the fallback evaluator, "no Vietnamese translation was produced" checks, `anti_convert.py` and the Sino-Vietnamese stage only making sense for zh→vi). Design v1.0.0's "the only change is that `source_lang` / `target_lang` come from `manifest.json`" understated the blast radius by an order of magnitude.
- **Options Considered**:
  1. **Narrow v1 to `{en→vi, zh→vi}` (chosen)**: Pairs declared in the manifest; any other pair rejected at intake with `unsupported pair`; target-agnostic pipeline is a follow-up spec.
  2. **Make the pipeline target-agnostic now**: Rewrite the 32 files, the fallback detector, and the checks for arbitrary targets.
- **Decision**: Narrow v1. The manifest still carries the pair, but only the two supported pairs are accepted.
- **Trade-offs & Risks**: Non-Vietnamese targets wait for a follow-up spec, but v1 ships on the pipeline that actually exists instead of a rewrite presented as a one-line change.
- **Rejected Alternatives**: Target-agnostic now (rejected — a large rewrite, not a manifest change).
- **Blast Radius**: `manifest.json` validation, intake check, CONTRIBUTING.md.

## ADR-COLLAB-012: Seed-then-parallel terminology

- **Status**: Accepted (v1.1.0)
- **Governs**: REQ-COLLAB-003, REQ-COLLAB-009
- **Context**: Opus review P0-3: REQ-COLLAB-009 v1.0.0 assumed "the next chapter uses the terms of the previous accepted chapter", which holds only because `orchestrate_run.py` translates chapters sequentially. Many agents translating in parallel break it. Also `assert_fiction_pronouns_populated` requires a populated `book_record.json` before any chapter is translated, and v1.0.0 created tasks straight after extraction with no analyze step.
- **Options Considered**:
  1. **Seed-then-parallel (chosen)**: Whole-book `analyze`, then the main agent translates ch01 as reference, then other chapters open in parallel against a frozen glossary; new terms are proposals (`analyze_additions.json`, `status: proposed`, `bind=False`) that only the main agent binds at merge (`bind=True`).
  2. **Sequential chapters, one agent at a time**: Keep the old order and let parallel agents wait their turn.
- **Decision**: Seed-then-parallel. `book_record.json` is writable only by the main agent (CODEOWNERS plus a CI diff check); a proposal conflicting with a term bound since the snapshot → REVISE.
- **Trade-offs & Risks**: Parallel chapters cannot invent terminology freely, and the main agent does the ch01 seed work, but every translator works from the same frozen glossary and no chapter silently diverges.
- **Rejected Alternatives**: Sequential chapters (rejected — it throws away the parallelism the whole project is for).
- **Blast Radius**: `scripts/orchestrate_run.py` analyze step, `book_record.json`, `analyze_additions.json`, CODEOWNERS.

## ADR-COLLAB-013: Login-based identity

- **Status**: Accepted (v1.1.0)
- **Governs**: REQ-COLLAB-004, REQ-COLLAB-005
- **Context**: Opus review P0-4: `agent:*` labels are spoofable and outside contributors cannot apply labels anyway — applying labels or assignees needs triage or write permission, so a fork-based contributor cannot label their own PR, and under v1.0.0 AC-COLLAB-004-2 every outside PR would be rejected.
- **Options Considered**:
  1. **GitHub login as identity (chosen)**: Identity = PR author login and review author login; reviewer ≠ translator means `review.user.login != pr.user.login`; labels are applied by the main agent / CI only, never required from the contributor; a soft claim is a `/claim` issue comment.
  2. **Label-based identity (v1.0.0)**: `agent:<name>` labels plus PR body as identity.
- **Decision**: Login-based identity. `agent:*` labels become optional filters.
- **Trade-offs & Risks**: Identity depends on GitHub accounts (one per agent / contributor), but GitHub already blocks self-approval and already records who wrote what, so nothing new needs to be trusted.
- **Rejected Alternatives**: Label-based identity (rejected — spoofable and unusable by fork contributors).
- **Blast Radius**: PR templates, issue comments, label application in the sweep, branch protection.

## ADR-COLLAB-014: Engine-agnostic contributors, provenance declared

- **Status**: Accepted (v1.1.0)
- **Governs**: REQ-COLLAB-004, REQ-COLLAB-007
- **Context**: Opus review P0-5 framed this as a contradiction: spec §1.1 said translation goes through `cbc`, while `rules/AGENTS.md` says NEVER invoke `cbc` for en→vi translation or refinement. Thang corrected the framing (2026-11-06): there is no blanket `cbc` rule — `cbc` is the harness, DeepSeek and other Chinese models are the engines, and they are good at Chinese work. The AGENTS.md restriction covers en→vi only; the zh→vi pipeline itself translates through DeepSeek-v4.1-Flash via `cbc`, and the code has provenance engines `agent-offline`, `agent-refine`, `cbc`, `custom`.
- **Options Considered**:
  1. **Engine-agnostic contributors (chosen)**: Contributors use any engine; the PR body declares `Engine/Model` provenance, mapped to the existing provenance field (`custom` plus free-text model). The main agent's own ch01 seed translation still follows AGENTS.md (Primary Agent authorship). AGENTS.md governs the main agent's local pipeline only; `collab-book-translate/CONTRIBUTING.md` governs contributors.
  2. **Require every contributor to use the local pipeline**: Contributors must run agent-translator themselves.
- **Decision**: Engine-agnostic contributors; quality is gated by review and evaluation, not by engine.
- **Trade-offs & Risks**: Translation quality varies by engine, but the review + score-of-record gates catch it, and requiring the local pipeline would exclude every contributor who does not run thang's machine.
- **Rejected Alternatives**: Require the local pipeline for all (rejected — excludes outside contributors).
- **Blast Radius**: PR template (Engine/Model field), CONTRIBUTING.md, provenance field mapping.

## ADR-COLLAB-015: Open competition, soft claim

- **Status**: Accepted (v1.1.0)
- **Governs**: REQ-COLLAB-004, REQ-COLLAB-013, REQ-COLLAB-014
- **Context**: Opus review P1-2: v1.0.0 AC-COLLAB-004-2 rejected a PR "IF a task is already claimed", but thang expects several PRs per chapter from different GitHub users.
- **Options Considered**:
  1. **Open competition (chosen)**: Multiple PRs per chapter; claiming is a courtesy (`/claim` comment); each PR needs ≥1 independent review; the main agent picks the best PASS PR after a 1-day window and closes the rest with a reason.
  2. **Exclusive claim**: One translator per chapter, others are rejected.
- **Decision**: Open competition with a deterministic selection rule (highest score-of-record weighted total, tie-break fewer critical/major issues, then earliest PR).
- **Trade-offs & Risks**: More PRs to review and score, but competition improves quality and no chapter is blocked by one slow claimant.
- **Rejected Alternatives**: Exclusive claim (rejected — contradicts thang's expectation and blocks chapters on one translator).
- **Blast Radius**: REQ-COLLAB-013 selection, REQ-COLLAB-014 state machine, sweep logic.

## ADR-COLLAB-016: Human licence gate

- **Status**: Accepted (v1.1.0)
- **Governs**: REQ-COLLAB-002
- **Context**: Opus review P1-4: v1.0.0 let the main agent decide licence validity and merge, but no licence logic exists in code, and no language detection exists either (`langdetect` / `detect_language` absent from `scripts/`).
- **Options Considered**:
  1. **Agent pre-screens, human decides (chosen)**: The main agent posts a pre-screen comment (manifest schema, format readable, language check, licence assessment); a human approval (thang's account) is required to merge a `books/` PR, enforced by CODEOWNERS on `books/**` plus branch protection; the allowed-licence list lives in CONTRIBUTING.md; the language check for `{en, zh}` is a script-ratio heuristic (CJK-codepoint share), needing no new dependency.
  2. **Agent decides autonomously**: The main agent merges when its own checks pass.
- **Decision**: Human licence gate. An autonomous licence decision in a public repo is not acceptable.
- **Trade-offs & Risks**: Every book waits for a human approval, but a wrongly merged copyrighted book is far costlier than a wait.
- **Rejected Alternatives**: Autonomous agent licence decision (rejected — no licence logic exists and the consequence of error is a copyrighted book in a public repo).
- **Blast Radius**: CODEOWNERS, branch protection, CONTRIBUTING.md, pre-screen comment.

> **Note (2026-11-06, round-2 amendment, R2-P2-1 — append-only, ADR body above unchanged):** the Context above repeated Opus round-1 P1-4's error. `detect_source_language` exists in `scripts/utils/language.py` (L38–58): CJK density above 15% → `zh`, else `en`. The design reuses it instead of inventing a new heuristic. Its limit stands: any Latin-script text (French, Vietnamese) returns `en`, so the intake adds a Vietnamese-diacritic-density check so an already-translated `vi` source declared as `en` is caught; `scripts/evaluators/fallback.py` already measures diacritics (`VI_DIACRITICS`, ≥ 5% of alpha characters). Verified by reading both files on the Mac this session.

## ADR-COLLAB-017: Publish as GitHub Releases, binaries never committed

- **Status**: Accepted (v1.1.0)
- **Governs**: REQ-COLLAB-010
- **Context**: Opus review P1-10: v1.0.0 AC-COLLAB-010-1 put compiled files in `runs/<slug>/`; in a public repo every rebuild adds megabytes of EPUB/PDF/DOCX to history permanently, and GitHub rejects files over 100 MB outright.
- **Options Considered**:
  1. **GitHub Releases (chosen)**: Tag `<slug>-v<N>`, EPUB/PDF/DOCX as release assets; ignore `*.epub` / `*.pdf` / `*.docx` under `runs/` in `.gitignore`; cap source files at 50 MB or store them in LFS.
  2. **Commit compiled files to git**: Deliverables live in `runs/<slug>/` in the repo.
- **Decision**: Releases. Git keeps text only.
- **Trade-offs & Risks**: Readers must fetch assets from a release instead of the tree, but history stays small and rebuilds do not bloat the repo forever.
- **Rejected Alternatives**: Committing binaries (rejected — irreversible history bloat, 100 MB file limit).
- **Blast Radius**: `.gitignore`, release flow in the sweep, `scripts/compile_book.py` output handling.

## ADR-COLLAB-018: Main agent runs as its own bot account (D1)

- **Status**: Accepted (v1.2.0)
- **Governs**: REQ-COLLAB-002, REQ-COLLAB-015
- **Context**: Opus round-2 finding R2-P1-1: the package never said which GitHub login the main agent uses. The default on thang's machine is thang's own `gh auth` (`vinhthang`). If the agent ran as `vinhthang`, its approval and thang's approval would be the same login, so AC-COLLAB-002-2 ("a PR without human approval SHALL NOT be merged") would have no observable difference to test; GitHub also blocks self-approval, so the agent could never get its own ch01 seed PR approved; and the post-merge commits (ledger, `bind=True`, `final.txt`) are direct pushes to `main` needing a branch-protection bypass nobody had granted.
- **Options Considered**:
  1. **Dedicated bot account (chosen, thang D1)**: The main agent authenticates as `collab-main-agent` (Maintain role, branch-protection bypass for post-merge commits only). `vinhthang` is the only human approver. CODEOWNERS `books/** @vinhthang`; branch protection "Require review from Code Owners".
  2. **Main agent runs as `vinhthang`**: No new account; the human gate becomes untestable (see Context).
- **Decision**: Dedicated bot account. Login-based identity (ADR-COLLAB-013) now extends to the main agent itself.
- **Trade-offs & Risks**: A new GitHub account must be created and given a token the main agent runs with; signup is thang's step. In return the human gate is a real, observable check: an approval from any login other than `vinhthang` does not count.
- **Rejected Alternatives**: Running as `vinhthang` (rejected — untestable gate, self-approval blocked, no bypass for post-merge commits).
- **Blast Radius**: GitHub account setup, CODEOWNERS, branch protection, sweep authentication, ch01 seed PR authorship.

## ADR-COLLAB-019: ch01 seed merges on a live PASS only, no human step (D2)

- **Status**: Accepted (v1.2.0)
- **Governs**: REQ-COLLAB-003
- **Context**: Opus round-2 finding R2-P1-1: ch01 had no defined review at all. REQ-COLLAB-005 requires a non-author review and REQ-COLLAB-003 just said "translate ch01 … and merge it". With ADR-COLLAB-018 the bot authors the seed PR, so a human review would be the only way to approve it — but the seed is the reference the whole book builds on, and waiting on a human for every book slows the pipeline to the human's schedule.
- **Options Considered**:
  1. **Live PASS only, bot merges itself (chosen, thang D2)**: The ch01 seed PR is authored by the bot and merged by the bot only on a live, non-fallback `evaluate.py` PASS with zero critical issues and all sweep checks passing. No human step.
  2. **Human approval for ch01**: thang approves every seed PR before any chapter opens.
- **Decision**: Live PASS only. This is an explicit exception to REQ-COLLAB-005 (non-author review) and REQ-COLLAB-013 (window and selection), for ch01 only, stated as such in REQ-COLLAB-003. IF the evaluation is fallback or FAIL, THEN ch01 is not merged and no chapter issues are opened. The merge comment lists the glossary terms bound by the seed, so thang can audit them afterwards.
- **Trade-offs & Risks**: No human signs off ch01, so the frozen glossary is only as good as `analyze` + ch01. The audit list in the merge comment is the mitigation; a bad seed is caught when the first parallel chapters fail review against it.
- **Rejected Alternatives**: Human approval for ch01 (rejected — slowest path, and the live evaluator plus zero-critical-issues bar is the same bar every other chapter must pass).
- **Blast Radius**: REQ-COLLAB-003 ACs, seed task T-202, seed test T-109.

## ADR-COLLAB-020: Starved-chapter fallback — the main agent translates it itself (D3)

- **Status**: Accepted (v1.2.0)
- **Governs**: REQ-COLLAB-013, REQ-COLLAB-014
- **Context**: Opus round-2 finding R2-P2-8: if no PR ever reaches PASS, the chapter stays open forever and the book never completes (REQ-COLLAB-010). A chapter with no competent contributor must not block the whole book.
- **Options Considered**:
  1. **Main agent translates it after 2 days (chosen, thang D3)**: After 2 days with no PASS candidate, the main agent translates the chapter itself and submits a normal PR. It goes through the same checks and score of record, and other PRs can still compete during the 1-day window.
  2. **Leave the chapter open indefinitely**: The book waits for a contributor.
- **Decision**: 2-day fallback. The bot's fallback PR must also be checked for self-review: the bot is its author, so a valid review must come from any login other than the bot. The score of record still decides, as for every other PR.
- **Trade-offs & Risks**: The main agent does translation work it was meant to delegate, but only after the community had 2 days, and only for chapters nobody could pass.
- **Rejected Alternatives**: Leaving the chapter open (rejected — a book that can never complete).
- **Blast Radius**: REQ-COLLAB-013 ACs, selection task T-304, state machine.

## Gate 1 Self-Validation — Re-run (2026-11-06, v1.1.0, author: Muse)

The v1.0.0 Gate 1 table above is kept for history. Opus overturned its PASS claims (review §5); this table re-validates the amended normative source.

| Check | Result | Evidence |
|---|---|---|
| 1. Codebase Grounding | PASS | spec.md §1.1 now states the verified layout: corpus in external workspace `<external_books_dir>` (`config.json` `workspace_dir`), `collab-book-translate` as the collab repo, AGENTS.md en→vi authorship rule and the zh→vi DeepSeek-via-`cbc` path, sequential orchestrator loop + `assert_fiction_pronouns_populated`, `resolve_pass_threshold` precedence (CLI > config > genre 3.5/4.0), provenance engines, critical/quorum/redraft rules; all verified by reading the Mac files this session |
| 2. User Intent & Non-Goals | PASS | 15/15 REQs carry a Source Intent citation (verbatim thang prompts 1–8, grill decisions 9–21, engine correction 22, or explicit derivation labelled Derived); §2 Non-Goals updated (task-hub integration, GitHub Actions/webhooks, own git server, autonomous merge, copyrighted books, target-agnostic pipeline, audio/image/reader) |
| 3. EARS & Falsifiable ACs | PASS | 13 active REQs in WHEN / THE / SHALL form (REQ-COLLAB-008 and 011 are [DEPRECATED] with explicit non-implementation statements); every active REQ has a Happy-Path and an Error/Boundary AC; AC-COLLAB-001-2 now has a path-allowlist detection rule (P2-1) |
| 4. Zero Deferral Placeholders | PASS | No TODO / TBD / FIXME in spec.md or decisions.md; one [PROVISIONAL] tag, scoped to the chapter-split open question in REQ-COLLAB-004, one sentence, owner-deferred to the design phase, with the novella-pilot non-exercise noted |
| 5. Anti-Strawman ADR Debate | PASS | 17 ADRs; ADR-COLLAB-009 replaces the v1.0.0 strawman ("New 0–100 rubric") with the live alternative (reviewer score vs scripted evaluator as score of record); every new ADR compares ≥ 2 viable options with Trade-offs & Rejected Alternatives; superseded ADR bodies preserved append-only |
| 6. Cross-Spec Lineage & Cascade | PASS | spec.md header carries Type, Status Planning, Version 1.1.0, Lineage (Extends/Modifies None, Supersedes None), Governed by spec-driven v2.2.0; the v1.0.0 design §1.3 `[MODIFIES]` claim on the task-stream spec is gone with the hub (REQ-COLLAB-008 deprecated); INDEX.md row updated to v1.1.0 / REQ-COLLAB-001..015 |
| **Overall Gate 1 (re-run)** | **PASS** | All 6 checks pass on the amended normative source; derived files were regenerated after this table |

## Round-2 Amendment Notes (2026-11-06, v1.2.0, author: Muse)

Opus round-2 review (review §9, 2026-11-06): REQUEST CHANGES (minor), 0 P0, 4 P1, 11 P2. All 5 round-1 P0s resolved. Thang confirmed D1–D3 are his decisions and told Muse to ignore R2-P1-3 and handle it himself.

- **R2-P1-3 (prompt injection) — DEFERRED TO THANG, not addressed by this amendment.** Thang (2026-11-06): "ignore the p3, I will handle myself". Recorded in spec.md §2 as a Non-Goal so Opus round 3 does not re-raise it as a miss. No requirement, AC, task, or test covers it here.
- **Premises verified by reading the Mac files this session** (not taken from the review on trust): `detect_source_language` exists (`scripts/utils/language.py` L38–58, CJK density > 15% → `zh` else `en`); `interprocess_lock` defaults to a 30 s timeout and raises (`scripts/utils/terminology.py` L89); the contributor segment format is `<segment id type><original>…</original>[<hanviet>…</hanviet>]<translate>…</translate></segment>` (`scripts/translate_chapter.py` L599, L657, prompt rules); `scripts/evaluators/fallback.py` measures Vietnamese diacritics (`VI_DIACRITICS`, ≥ 5%); the Tam Quốc Chí edition actually used is `Ancient-China-Books/sanguozhi` (complete punctuated 65-volume edition), not Gutenberg #25606, which lacked 35 of 65 volumes.
- **One self-caught fix during the amendment:** the design §6.2 example first said `round1_translate.txt`; per R2-P1-2 the one contributor file is `translation.xml`, and the example now says so.

## Gate 1 Self-Validation — Re-run 2 (2026-11-06, v1.2.0, author: Muse)

The v1.1.0 re-run table above is kept for history. Opus round-2 review §9.5 overturned its Check 3 PASS (undefined "translation file", untestable human gate, unfalsifiable AC-011-2) and its Gate 2 test-first claim; this table re-validates the amended normative source.

| Check | Result | Evidence |
|---|---|---|
| 1. Codebase Grounding | PASS | §1.1 bot-account identity (D1) and the `translation.xml` contract name the verified segment format (`translate_chapter.py` L599/L657); `detect_source_language` (language.py L38–58) and the diacritic check (`evaluators/fallback.py`) are cited, not invented; `interprocess_lock` 30 s default (terminology.py L89) grounds the non-blocking acquire; sanguozhi edition corrected to `Ancient-China-Books/sanguozhi` |
| 2. User Intent & Non-Goals | PASS | 13/13 active REQs carry a Source Intent citation (verbatim thang prompts 1–8, grill decisions 9–21, engine correction 22, round-2 decisions D1–D3 as intent 23, P1-3 deferral as intent 24, or explicit derivation labelled Derived); §2 Non-Goals adds the prompt-injection deferral |
| 3. EARS & Falsifiable ACs | PASS | 13 active REQs in WHEN / THE / SHALL form (REQ-COLLAB-008 and 011 are [DEPRECATED] and now carry no ACs, R2-P2-4); every active REQ has a Happy-Path and an Error/Boundary AC (26 ACs); AC-COLLAB-004-2 now names the `translation.xml` contract with segment-order, `<original>` byte-identity, and `<hanviet>` rules; AC-COLLAB-002-2 makes a bot-only approval untestable-no-more (it SHALL NOT count); AC-COLLAB-003-1/2 carry the D2 live-PASS rule and its fallback/FAIL branch; AC-COLLAB-013-2 carries the D3 2-day fallback |
| 4. Zero Deferral Placeholders | PASS | No TODO / TBD / FIXME in spec.md or decisions.md; one [PROVISIONAL] tag, scoped to the chapter-split open question in REQ-COLLAB-004, one sentence, owner-deferred to the design phase, with the novella-pilot non-exercise noted |
| 5. Anti-Strawman ADR Debate | PASS | 20 ADRs; ADR-COLLAB-018/019/020 each compare ≥ 2 viable options with Trade-offs & Rejected Alternatives; ADR-COLLAB-016 corrected by append-only note, body unchanged; superseded ADR bodies preserved append-only |
| 6. Cross-Spec Lineage & Cascade | PASS | spec.md header carries Type, Status Planning, Version 1.2.0, Lineage (Extends/Modifies None, Supersedes None), Governed by spec-driven v2.2.0; REQ headings use `[AMENDED v1.1.0]` for v1.1.0 changes and `[AMENDED v1.2.0]` for this round's changes (R2-P2-3); INDEX.md row updated to v1.2.0 / REQ-COLLAB-001..015 (13 active) |
| **Overall Gate 1 (re-run 2)** | **PASS** | All 6 checks pass on the amended normative source; derived files were regenerated after this table |

