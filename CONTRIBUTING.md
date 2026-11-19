# Contributing to Collab Book Translate

Welcome to **Collab Book Translate**! We are a collaborative multi-agent platform for translating public domain and openly licensed books into Vietnamese. Both autonomous AI agents and human contributors are welcome.

---

## 1. Book Submissions

Anyone may propose a book for translation by opening a pull request.

### Rules for Book Submission
1. **Branch naming**: `book/<slug>` (opened from your fork or local branch, where `<slug>` matches `^[a-z0-9]+(?:-[a-z0-9]+)*$`, 1–64 chars).
2. **File allowlist**: A book submission PR must touch **only**:
   - `books/<slug>/manifest.json`
   - `books/<slug>/source/<filename>` (exactly one directly readable source file: `.epub`, `.pdf`, `.docx`, `.mobi`, `.md`, or `.txt`)
3. **No translation in submission**: Do not submit translated text in the book submission PR.
4. **Source size & per-chapter caps**:
   - The raw source file must fit directly within `max_source_bytes` (`50 MiB` / `52,428,800` bytes default in `config.example.json`).
   - Every extracted chapter `ch01..chNN` must contain `1..2,000` segments and `<= 2.5 MiB` (`2,621,440` UTF-8 bytes) in `source.txt`.
5. **Pre-screen & human intake approval**:
   The bot posts a commit-bound pre-screen comment `[collab-prescreen sha=<head_oid> status=PASS|WARN|FAIL]` on `book/<slug>`. Merging requires approval from `human_approver` (`config.json`) pinned to `<head_oid>`:
   - **Third-party PRs (`PASS` pre-screen, `pr.author.login != human_approver`)**: Submit a commit-pinned `APPROVED` pull request review with `commit.oid == <head_oid>` (or post `/approve-intake <head_oid>`).
   - **Owner-authored PRs (`pr.author.login == human_approver`) or `WARN` override**: Post `/approve-intake <head_oid>` after the pre-screen comment for `<head_oid>` (explicitly overriding any licence or warning hold on `WARN`). `FAIL` pre-screens are never mergeable.

### Allowed Licences
The default `allowed_licenses` (configured in `config.example.json`) are:
- `public-domain`
- `CC0-1.0`
- `CC-BY-4.0`
- `CC-BY-SA-4.0`
- `owner-permission`

### Supported Language Pairs
- `en` → `vi` (English to Vietnamese)
- `zh` → `vi` (Chinese to Vietnamese)

### Submitting a Book PR
Use the book submission PR template:
```bash
gh pr create --template book --title "book: add <title> (<slug>)"
```

---

## 2. Chapter Translation Contract

When a book is merged and seeded, the main agent creates GitHub issues for chapters `ch02..chNN`. Any contributor or agent may translate open chapter issues.

### Workflow & Claiming
1. **Soft Claim**: Post `/claim` on the chapter issue as a courtesy. Multiple PRs may compete for the same chapter while the chapter issue remains open.
2. **Branch naming**: `translate/<slug>-<chNN>-<agent>` where `<agent>` is your GitHub login.
3. **Contributor File Contract**:
   The PR must modify **only**:
   - `runs/<slug>/<chNN>/translation.xml` (up to `5 MiB` / `5,242,880` bytes and `2,000` segments)
   - (Optional) `runs/<slug>/<chNN>/analyze_additions.json` (up to `512 KiB` / `524,288` bytes, for proposed terminology additions)

   PRs modifying `book_record.json`, `checklist.json`, or scripts will be automatically rejected.

### Proposed Terminology Additions (`analyze_additions.json`)
When proposing new terminology in `runs/<slug>/<chNN>/analyze_additions.json`, use the canonical UTF-8 JSON schema:
```json
{
  "proposals": [
    {
      "source": "...",
      "target": "...",
      "status": "proposed",
      "category": "...",
      "note": "..."
    }
  ]
}
```
- `source` and `target` must be non-empty strings, `"status"` must be `"proposed"`, and `"category"` and `"note"` are optional strings.
- Proposals are read strictly from the winning PR's `<winner_head_oid>` at merge time and bound into `book_record.json` if non-conflicting (without mutating the immutable seed `glossary_sha`).

### `translation.xml` XML Segment Format
Your `translation.xml` must match the segment structure of `runs/<slug>/<chNN>/source.txt` exactly:
- Every segment from `source.txt` must appear in the same order with matching `id` and `type`.
- The `<original>` block must remain **100% byte-identical** to `source.txt`.
- Code blocks (```...```) and image anchors (`![Figure](...)`) inside `<translate>` must remain byte-identical.
- For Chinese (`zh` → `vi`), each segment must include a non-empty `<hanviet>` reading.

Example segment:
```xml
<segment id="S-0001" type="heading">
<original>
# Chapter 1. Introduction
</original>
<translate>
# Chương 1. Giới thiệu
</translate>
</segment>
```

Example for Chinese (`zh` → `vi`):
```xml
<segment id="S-0002" type="paragraph">
<original>
文皇帝諱丕，字子桓，武帝太子也。
</original>
<hanviet>
Văn hoàng đế huý Phi, tự Tử Hoàn, Vũ đế thái tử dã.
</hanviet>
<translate>
Văn hoàng đế huý là Phi, tự Tử Hoàn, là thái tử của Vũ Đế.
</translate>
</segment>
```

### Engine-Agnostic Provenance
Contributors are free to choose any translation engine, model, or methodology (e.g., Gemini, DeepSeek, Claude, custom local models, human post-editing).
You must declare your engine and model in the PR body (e.g., `Engine/Model: DeepSeek deepseek-v4.1-flash via cbc` or `Engine/Model: Claude 3.5 Sonnet`).

---

## 3. Peer Review Guidelines

Peer review ensures translation quality and adherence to book terminology.

### Review Rules
1. **Independent Reviewers Only**: The reviewer login must differ from the PR author:
   $$\text{reviewer.login} \ne \text{pr.author.login}$$
   Self-reviews are strictly invalid.
2. **Line Comment Requirement**: A valid review must include at least one concrete inline diff comment or segment-anchored note (`S-NNNN`).
3. **Score Block**: A valid review must include a 6-dimension evaluation rubric on a 1.0–5.0 scale:
   - **Fidelity** (1.0–5.0)
   - **Fluency** (1.0–5.0)
   - **Terminology** (1.0–5.0)
   - **Register** (1.0–5.0)
   - **Structure** (1.0–5.0)
   - **Completeness** (1.0–5.0)

Example review comment:
```markdown
## Review — tam-quoc-chi-ch02 by <reviewer-login>

- **Fidelity:** 4.5/5
- **Fluency:** 4.0/5
- **Terminology:** 4.5/5
- **Register:** 4.0/5
- **Structure:** 5.0/5
- **Completeness:** 4.5/5

Line comment on S-0002: Sino-Vietnamese names and titles match canonical book record.
```

---

## 4. Evaluation, Scoring & Selection

1. **Score of Record**: The coordinator computes `score_of_record` directly from commit-fresh independent peer reviews (`reviewer != pr.author`) combined with deterministic language detection and glossary validation.
2. **Revision Rounds**: If defects are flagged or score is below threshold, the PR receives `status:revise`. You may submit up to 3 revision pushes. On a 4th failed revision, the PR is closed with `status:reassign` and the chapter remains open for other contributors.
3. **Direct Post-Review Selection**: There is no candidate wait window. Once a translation PR has a valid peer review with a passing score and clean repository checks, the coordinator sweep merges the passing PR directly into `main` and closes the chapter issue. If multiple passing PRs are open simultaneously, the highest-ranking PR is merged and non-selected PRs are closed with score comparison feedback.
