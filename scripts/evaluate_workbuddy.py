#!/usr/bin/env python3
"""
scripts/evaluate_workbuddy.py — Backward-Compatibility Shim
Redirects evaluation invocations to the unified scripts/evaluate.py runner
and modular evaluators in scripts/evaluators/.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.evaluate import (
    evaluate_file,
    main,
    parse_args,
    partition_segments_into_windows,
    run_evaluation,
)
from scripts.evaluators.fallback import (
    COMMON_EN_WORDS,
    DEFAULT_RUBRIC_WEIGHTS,
    VI_DIACRITICS,
    _extract_table,
    compute_weighted_total,
    evaluate_fallback,
)
from scripts.evaluators.workbuddy import (
    build_evaluation_prompt,
    extract_json_from_response,
    find_cbc_binary,
)

# Backward-compatibility alias
run_workbuddy_evaluation = run_evaluation
evaluate_workbuddy = evaluate_file

__all__ = [
    "run_workbuddy_evaluation",
    "evaluate_workbuddy",
    "evaluate_file",
    "parse_args",
    "main",
    "partition_segments_into_windows",
    "find_cbc_binary",
    "build_evaluation_prompt",
    "extract_json_from_response",
    "evaluate_fallback",
    "compute_weighted_total",
    "_extract_table",
    "DEFAULT_RUBRIC_WEIGHTS",
    "VI_DIACRITICS",
    "COMMON_EN_WORDS",
]

if __name__ == "__main__":
    main()
