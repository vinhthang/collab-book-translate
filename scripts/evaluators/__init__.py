"""
scripts/evaluators — Modular Evaluation Architecture Registry
Provides unified factory access to evaluation engines:
- 'workbuddy': DeepSeek evaluation via CodeBuddy CLI (`cbc`)
- 'fallback': Fast deterministic rule-based evaluation
"""

from typing import Dict, Type

from scripts.evaluators.base import BaseEvaluator
from scripts.evaluators.fallback import FallbackEvaluator
from scripts.evaluators.workbuddy import WorkBuddyEvaluator

_REGISTRY: Dict[str, Type[BaseEvaluator]] = {
    "workbuddy": WorkBuddyEvaluator,
    "fallback": FallbackEvaluator,
}


def get_evaluator(name: str) -> BaseEvaluator:
    """
    Factory function returning an instance of the requested evaluator engine.

    Args:
        name: Name of the evaluator engine ('workbuddy', 'fallback')

    Returns:
        Instance of BaseEvaluator

    Raises:
        ValueError: If engine name is not recognized in registry.
    """
    normalized_name = (name or "").strip().lower()
    if normalized_name not in _REGISTRY:
        supported = ", ".join(sorted(_REGISTRY.keys()))
        raise ValueError(f"Unknown evaluator engine '{name}'. Supported engines: {supported}")
    return _REGISTRY[normalized_name]()


__all__ = [
    "BaseEvaluator",
    "WorkBuddyEvaluator",
    "FallbackEvaluator",
    "get_evaluator",
]
