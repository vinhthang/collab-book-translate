"""
scripts/evaluators/workbuddy.py — WorkBuddy Evaluation Engine
Executes an independent evaluation pass using CodeBuddy CLI (`cbc`) with
model `deepseek-v4.1-flash`. Falls back gracefully to FallbackEvaluator.
"""

import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.evaluators.base import BaseEvaluator
from scripts.evaluators.fallback import (
    FallbackEvaluator,
    _extract_table,
    compute_weighted_total,
)
from scripts.evaluators.scoring import (
    DIMENSION_WEIGHTS,
    SEVERITY_WEIGHTS,
    compute_hybrid_weighted_total,
    filter_shielded_verbatim_issues,
    validate_and_realign_issue_spans,
)
from scripts.utils.terminology import extract_verbatim_terms, find_cbc_binary


def build_evaluation_prompt(
    source_text: str,
    draft_text: str,
    book_record: Optional[Dict[str, Any]] = None,
    source_lang: str = "en",
) -> str:
    """
    Builds the evaluation prompt with Vietnamese quality & grammar rubric,
    source, draft, and book record terminology.
    """
    effective_lang = source_lang
    if book_record and isinstance(book_record, dict) and book_record.get("source_lang"):
        effective_lang = book_record["source_lang"]

    record_summary = ""
    if book_record:
        terms = _extract_table(book_record, ["term_table", "terms"])
        names = _extract_table(book_record, ["name_table", "names"])
        pronouns = _extract_table(book_record, ["pronoun_table", "pronouns"])
        brief = book_record.get("brief", "")
        verbatim_terms = extract_verbatim_terms(book_record)
        verbatim_instruction = ""
        if verbatim_terms:
            verbatim_instruction = (
                f"\nQUY CHUẨN THƯƠNG HIỆU & THUẬT NGỮ CHUẨN QUỐC TẾ:\n"
                f"Theo Book Record, các tên sản phẩm, thương hiệu và thuật ngữ chuẩn công nghệ sau BẮT BUỘC giữ nguyên tiếng Anh (ví dụ: Google Cloud Spanner, Cloud Native, OLTP, OLAP...): {', '.join(verbatim_terms[:40])}.\n"
                f"Giám định viên TUYỆT ĐỐI KHÔNG coi việc giữ nguyên tiếng Anh các từ này là lỗi fidelity hay completeness; trái lại, đây là chuẩn mực dịch thuật công nghệ chính xác.\n"
            )

        record_summary = f"""
## Canonical Book Record
Brief: {brief}
Name Table: {json.dumps(names, ensure_ascii=False)}
Term Table: {json.dumps(terms, ensure_ascii=False)}
Pronoun Table: {json.dumps(pronouns, ensure_ascii=False)}
{verbatim_instruction}
"""

    if effective_lang == "zh":
        intro_sentence = "You are an expert bilingual translation evaluator judging a Chinese-to-Vietnamese translation of a book chapter."
        lang_rubric = """
## Chinese-to-Vietnamese Translation & Anti-Convert Rules (source_lang='zh'):
1. **Zero Convert / Raw Hán-Việt Calques**:
   - Strictly prohibit crude word-by-word Chinese translationese / convert artifacts.
   - Flag words like "bất quá" (tuy nhiên / nhưng), "đốn thời" (ngay lập tức / tức thì), "phất thủ" (xua tay / vung tay), "hạ nhất khắc" (ngay sau đó), "sắc mặt ngưng trọng" (vẻ mặt nghiêm nghị), "chỉ kiến" (chỉ thấy), "nhãn mâu" (ánh mắt / đôi mắt), "khóe miệng co giật" (khóe môi giật giật), "tâm đắc" (kinh nghiệm tâm huyết).
2. **Proper Nouns & Names**:
   - Historical Chinese names, dynasties, and locations must use standard Sino-Vietnamese readings (e.g. 曹操 -> Tào Tháo, 诸葛亮 -> Gia Cát Lượng).
3. **Contextual Interpersonal Pronouns**:
   - Convert "你/我/他" flexibly according to Vietnamese social hierarchy, age, and relationship (ta/ngươi, tôi/ông, huynh/đệ, etc.) as recorded in the Book Record.
4. **Punctuation Typography**:
   - Chinese full-width punctuation (，。！？：“”) must be converted to standard Vietnamese punctuation.
"""
    else:
        intro_sentence = "You are an expert bilingual translation evaluator judging an English-to-Vietnamese translation of a technical book chapter."
        lang_rubric = """
## Vietnamese Grammar & Quality Rubric
1. **Natural SVO Word Order**:
   - Vietnamese syntax naturally follows Subject-Verb-Object order.
   - Reject awkwardly literal translations that mirror inverted or overly complex English subordinate clauses.
2. **Active vs Passive Voice**:
   - Do NOT mechanically translate English passive verbs into passive calques with "được" or "bị" (e.g. avoid "được thực hiện bởi...", "được viết bởi...").
   - Vietnamese uses "được" primarily for positive/beneficial outcomes and "bị" for adverse/unfortunate events. Use active constructions or topic-comment structures for neutral technical actions.
3. **Calque & Collocation Elimination**:
   - Eliminate word-for-word calques of English phrases (e.g. "trong một nỗ lực để", "tại cuối ngày").
   - Phrasing must read as natural, idiomatic Vietnamese written by a native subject-matter expert.
4. **Pronoun Redundancy**:
   - Eliminate redundant third-person pronouns ("nó", "chúng nó", "chúng") where Vietnamese naturally omits the subject or repeats the topic noun.
"""

    prompt = f"""{intro_sentence}
Your evaluation must be independent, rigorous, and completely honest.

{record_summary}

{lang_rubric}
5. **Terminology & Consistency**:
   - Strictly adhere to canonical terms and names from the Book Record.
6. **Structure & Byte-Invariance**:
   - Code fences (```...```) and image anchors (![Figure](...)) must remain byte-for-byte identical.
7. **Completeness & Fidelity**:
   - Meaning equivalence: nothing omitted, nothing added.

## Source Chapter:
{source_text}

## Translation Draft:
{draft_text}

## Output Format Requirements:
You must output ONLY a valid JSON object matching the schema below. No markdown fences, no explanatory preambles or notes.

{{
  "issues": [
    {{
      "id": "i1",
      "dimension": "fidelity|fluency|terminology|register|structure|completeness",
      "severity": "critical|major|minor",
      "pervasive": false,
      "span_source": "exact source span",
      "span_target": "exact target span",
      "problem": "one concise sentence describing the issue",
      "suggestion": "concrete instruction to fix"
    }}
  ],
  "dimensions": {{
    "fidelity": 5.0,
    "fluency": 5.0,
    "terminology": 5.0,
    "register": 5.0,
    "structure": 5.0,
    "completeness": 5.0
  }},
  "notes": "Summary of evaluation"
}}
"""
    return prompt.strip()


def extract_json_from_response(raw_text: str) -> Optional[Dict[str, Any]]:
    """Extracts and parses JSON object from model response text."""
    if not raw_text:
        return None

    # Try direct parse
    try:
        data = json.loads(raw_text.strip())
        if isinstance(data, dict) and "issues" in data and "dimensions" in data:
            return data
    except Exception:
        pass

    # Extract first { to last }
    first_brace = raw_text.find("{")
    last_brace = raw_text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        snippet = raw_text[first_brace : last_brace + 1]
        try:
            data = json.loads(snippet)
            if isinstance(data, dict) and "issues" in data and "dimensions" in data:
                return data
        except Exception:
            pass

    return None


class WorkBuddyEvaluator(BaseEvaluator):
    """WorkBuddy evaluation engine backed by CBC CLI and DeepSeek model."""

    def __init__(self, fallback_evaluator: Optional[BaseEvaluator] = None):
        self.fallback = fallback_evaluator or FallbackEvaluator()

    def evaluate(
        self,
        source_text: str,
        draft_text: str,
        book_record: Optional[Dict[str, Any]] = None,
        timeout: int = 300,
        source_lang: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Evaluates using CBC CLI deepseek-v4.1-flash, falling back to deterministic heuristics."""
        effective_source_lang = source_lang or (book_record.get("source_lang", "en") if book_record else "en")
        cbc_bin = find_cbc_binary()
        if not cbc_bin:
            sys.stderr.write("Notice: cbc binary not found on system. Using fallback evaluation.\n")
            return self.fallback.evaluate(
                source_text, draft_text, book_record, timeout=timeout, source_lang=effective_source_lang, **kwargs
            )

        prompt = build_evaluation_prompt(source_text, draft_text, book_record, source_lang=effective_source_lang)
        for attempt in range(4):
            try:
                proc = subprocess.run(
                    [cbc_bin, "-p", "--model", "deepseek-v4.1-flash"],
                    input=prompt.encode("utf-8"),
                    shell=False,
                    capture_output=True,
                    timeout=timeout,
                )
                err_output = proc.stderr.decode("utf-8", errors="replace").strip() if proc.stderr else ""
                raw_output = proc.stdout.decode("utf-8", errors="replace").strip() if proc.stdout else ""
                if "429" in err_output.lower() or "too many requests" in err_output.lower() or "429" in raw_output.lower() or "too many requests" in raw_output.lower():
                    if attempt < 3:
                        sys.stderr.write(f"Warning: cbc evaluate 429 Too Many Requests on attempt {attempt+1}. Retrying after backoff...\n")
                        time.sleep(6 * (attempt + 1) + random.uniform(2.0, 5.0))
                        continue

                if proc.returncode == 0 and raw_output:
                    parsed = extract_json_from_response(raw_output)
                    if parsed:
                        if "issues" in parsed:
                            parsed["issues"], _ = validate_and_realign_issue_spans(parsed["issues"], draft_text)
                            if book_record:
                                parsed["issues"], suppressed_cnt = filter_shielded_verbatim_issues(parsed["issues"], book_record)
                                parsed["suppressed_verbatim_count"] = suppressed_cnt
                        parsed["weighted_total"] = compute_hybrid_weighted_total(parsed, draft_text)
                        if "verdict" not in parsed:
                            wt = parsed.get("weighted_total", 5.0)
                            crit = [
                                i for i in parsed.get("issues", [])
                                if isinstance(i, dict) and i.get("severity") == "critical"
                            ]
                            parsed["verdict"] = "FAIL" if (crit or wt < 4.0) else "PASS"
                        return parsed
                    else:
                        if attempt < 3:
                            time.sleep(2 * (attempt + 1))
                            continue
                        sys.stderr.write("Warning: Failed to parse JSON from cbc output. Falling back.\n")
                else:
                    if attempt < 3:
                        time.sleep(3 * (attempt + 1))
                        continue
                    sys.stderr.write(f"Warning: cbc exited with code {proc.returncode}: {err_output}\n")
            except Exception as e:
                if attempt < 3:
                    time.sleep(3 * (attempt + 1))
                    continue
                sys.stderr.write(f"Warning: cbc invocation failed ({e}). Falling back.\n")

        return self.fallback.evaluate(source_text, draft_text, book_record, timeout, source_lang=source_lang)
