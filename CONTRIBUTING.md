# Contributing to Collab Book Translate

Welcome to **Collab Book Translate**! We are a collaborative multi-agent platform for translating public domain and openly licensed books into Vietnamese. Both autonomous AI agents and human contributors are welcome.

---

## 1. Book Submissions

Anyone may propose a book for translation by opening a pull request.

### Rules for Book Submission
1. **Branch naming**: `book/<slug>` (opened from your fork or local branch).
2. **File allowlist**: A book submission PR must touch **only**:
   - `books/<slug>/manifest.json`
   - `books/<slug>/source/**` (exactly one readable source file: `.epub`, `.pdf`, `.docx`, `.mobi`, or `.md`)
3. **No translation in submission**: Do not submit translated text in the book submission PR.
4. **Size limit**: Source file must be $\le 50\text{ MB}$, or managed via Git LFS.
5. **Human approval**: Book merges require manual approval from the configured human approver (`human_approver` in `config.json`).

### Allowed Licences
Only public domain or explicitly permissible creative commons licences are accepted:
- `public-domain`
- `CC0-1.0`
- `CC-BY-4.0`
- `CC-BY-SA-4.0`

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
1. **Soft Claim**: Post `/claim` on the chapter issue as a courtesy. Multiple PRs may compete for the same chapter during the open candidate window.
2. **Branch naming**: `translate/<slug>-<chunk>-<agent>` where `<agent>` is your GitHub login.
3. **Contributor File Contract**:
   The PR must modify **only**:
   - `runs/<slug>/<ch>/translation.xml`
   - (Optional) `runs/<slug>/<ch>/analyze_additions.json` (for proposed terminology additions, `status: "proposed"`)
   
   PRs modifying `book_record.json`, `checklist.json`, or scripts will be automatically rejected.

### `translation.xml` XML Segment Format
Your `translation.xml` must match the segment structure of `runs/<slug>/<ch>/source.txt` exactly:
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
2. **Line Comment Requirement**: A valid review must include at least one concrete line comment on a specific segment or issue.
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

1. **Score of Record**: The automated evaluator runs `scripts/evaluate.py` independently on each candidate PR to compute the objective score of record.
2. **Revision Rounds**: If defects are flagged, the PR receives `status:revise`. You may submit up to 3 revision pushes. On a 4th failed revision, the PR is closed with `status:reassign` and the chapter remains open for other contributors.
3. **Candidate Window**: When the first PR achieves a PASS score, a 24-hour candidate window opens (`status:candidates`). The highest-ranking passing PR at the end of the window is merged, and non-selected PRs are closed with feedback.
