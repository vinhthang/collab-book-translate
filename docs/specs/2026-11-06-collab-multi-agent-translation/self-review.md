# Self-Review: Collaborative Multi-Agent Book Translation Spec

Author: Muse (spec author). This file is the author's self-review, committed
into the package so any reviewer can reproduce it (Opus review P1-7).

## v1.0.0 self-review (2026-11-06, before Opus review)

Gate 1 passed all 6 checks as claimed by the author at the time: 12 REQs,
12/12 EARS WHEN/THE/SHALL, 12/12 Source Intent, 12 Happy + 12 Error ACs,
7/7 ADRs with options/trade-offs/rejected alternatives, no TODO/TBD/FIXME,
one [PROVISIONAL] scoped to the chapter-split question.

Findings fixed by the author before handing the package to Opus:

- F-01: §1.2 said a split chapter was "under REQ-COLLAB-003"; the split
  rule lives in REQ-COLLAB-004. Fixed.
- F-02: The [PROVISIONAL] tag sat on the whole REQ-COLLAB-004 header,
  overstating what is deferred. Moved to the split open-question line only.
- F-03: Label taxonomy (agent / lang / book) was agreed in chat but absent
  from the spec. REQ-COLLAB-004 v1.0.0 required `agent:<name>`,
  `lang:<source>-<target>`, `book:<slug>` labels on PR and tracking issue.
- F-04: "Publish" in REQ-COLLAB-010 had no destination. AC-COLLAB-010-1
  defined publish as compiled files in `runs/<slug>/`, discoverable
  by the web reader.
- F-05: INDEX.md had no row for this package (Gate 1 Check 6). Row added.

Open for Opus to judge, not fixed by the author: (a) the single-consumer
SSE limit, (b) repo public vs private, (c) the chapter-split threshold.

Opus then overturned the Gate 1 and Gate 2 PASS claims (review §5):
Gate 1 Check 1 FAIL (wrong ground), Check 3 FAIL (REQ-COLLAB-011 not
system behaviour, AC-COLLAB-001-2 no detection rule, AC-COLLAB-004-2
rejects fork contributors), Check 5 FAIL (ADR-COLLAB-004 strawman),
Check 6 FAIL (header `Modifies: None` vs design §1.3 `[MODIFIES]` claim).
The author accepts the overturn: the v1.0.0 grounding was wrong on the
repo layout, the sequential terminology model, and the label identity model.

## v1.1.0 amendment self-check (2026-11-06, after the Opus review)

The author applied the Opus amendment plan (§8) in full:

- spec.md bumped to 1.1.0; §1.1 rewritten with the verified layout
  (external workspace `<external_books_dir>`, `collab-book-translate`,
  AGENTS.md `cbc` rule, sequential loop + `assert_fiction_pronouns_populated`,
  `resolve_pass_threshold` precedence, provenance engines); grill decisions
  recorded as verbatim intents 9–21; Non-Goals updated; §7 dispositions applied
  (008 and 011 [DEPRECATED], 013/014/015 [NEW]); 15 REQs, 13 active.
- decisions.md: ADR-COLLAB-002 superseded by 008, ADR-COLLAB-004 by 009,
  ADR-COLLAB-005 by 010, ADR-COLLAB-006 amended by 011; new ADRs 008–017;
  Gate 1 re-run table recorded; the v1.0.0 table kept for history.
- design.md regenerated around `collab-book-translate`; hub section removed.
- tasks.md rewritten (hub tasks T-101..T-105 dropped, test-first tasks added,
  split by repo) with the Gate 2 traceability matrix in this file.
- verification.md rewritten with one automated test per code-backed AC
  plus the novella pilot end-to-end row.
- INDEX.md row updated to v1.1.0, REQ-COLLAB-001..015, Status Planning.

Key Opus claims re-verified by the author on the Mac before amending:
`config.json` `workspace_dir` = `<external_books_dir>` and that
workspace holds `books/`, `inputs/`, `runs/`; `rules/AGENTS.md` NEVER invoke
`cbc` for en→vi translation, `cbc` required for evaluation; `helper.py`
`resolve_pass_threshold` (CLI > config > genre 3.5/4.0); `orchestrate_run.py`
`assert_fiction_pronouns_populated` before the sequential chapter loop.

No code was written and nothing was committed or pushed. The amended
package is handed back to Opus for re-review at a fixed state; Proceed is
thang's, then Gemini Argon implements.

## Post-amendment corrections (2026-11-06, before the Opus re-review)

- Engine correction (thang, intent 22): the "cbc rule" framing was wrong.
  spec.md §1.1 and ADR-COLLAB-014 were corrected in place — `cbc` is the
harness, DeepSeek and other Chinese models are the engines, good at Chinese
  work; the AGENTS.md restriction covers en→vi only. The Gate 1 re-run
  Check 2 evidence was updated to cite prompts 1–8, grill decisions 9–21,
  and engine correction 22.
- PR template examples (thang): design.md §6 added with the exact template
  text, grounded in Tam Quốc Chí ch02 (S-0001..S-0039, verified against the
  file) and The Dark Forest glossary; T-004 points at design §6. One
  self-caught fix during this step: the example first said S-0001..S-0148,
  corrected to S-0001..S-0039 after reading the last segment of the file.
- Final readiness re-check before the Opus re-review: 15 REQs, 15 Source
  Intents, 15 Happy + 15 Error ACs, 13 active REQs in EARS form, 30 Gate 2
  rows in tasks.md, no TODO/TBD/FIXME, one [PROVISIONAL].

## Round-2 amendment (v1.2.0, 2026-11-06)

Opus round-2 review (§9): REQUEST CHANGES (minor), 0 P0, 4 P1, 11 P2.
Thang confirmed D1–D3 are his decisions and told Muse to ignore R2-P1-3
and handle it himself.

Applied: R2-P1-1 (bot account, ADR-018; ch01 exception, ADR-019; D2 live-
PASS rule in REQ-003 with the audit list in the merge comment);
R2-P1-2 (`translation.xml` contract in REQ-004, design §3.3/§6.2);
R2-P1-4 (RED tasks T-106..T-110, mutation-guard T-404, pilot only for
genuinely end-to-end ACs); R2-P2-1 (`detect_source_language` reuse, ADR-016
append-only note); R2-P2-2 ("CI" → sweep checks / `NeedsChecks`);
R2-P2-3 (`[MODIFIES v1.0.0]` → `[AMENDED v1.1.0]` / `[AMENDED v1.2.0]`);
R2-P2-4 (deprecated REQs carry no ACs; recount 13 active / 26 ACs);
R2-P2-5 (non-blocking lock acquire, log + exit 0); R2-P2-6 (advance until
blocked); R2-P2-7 (window clock from the first `status:candidate` sweep);
R2-P2-8 (D3 2-day starved-chapter fallback, ADR-020); R2-P2-9 (branch
protection applies to every PR into `main`); R2-P2-10 (translation
template default, submission under `PULL_REQUEST_TEMPLATE/book.md`);
R2-P2-11 (Tam Quốc Chí example now cites `Ancient-China-Books/sanguozhi`).

NOT applied: R2-P1-3 (prompt injection) — deferred to thang per his
instruction; recorded as a Non-Goal in spec.md §2 and in decisions.md
round-2 notes.

Premises verified by reading the Mac files, not taken from the review
on trust: `detect_source_language` (language.py L38–58), `interprocess_lock`
30 s default (terminology.py L89), segment format (translate_chapter.py
L599/L657), diacritic measurement (evaluators/fallback.py), sanguozhi
edition (2026-10-21 spec). One self-caught fix: the design §6.2 example
first said `round1_translate.txt`; corrected to `translation.xml`.

Final readiness re-check for the Opus round-3 review: 15 REQ headers,
13 active REQs in EARS form, 26 ACs (13 Happy + 13 Error), 4 deprecated
rows N/A, 30 Gate 2 rows in tasks.md, no TODO/TBD/FIXME, one [PROVISIONAL].
