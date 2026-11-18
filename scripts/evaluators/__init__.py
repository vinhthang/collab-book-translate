"""
scripts/evaluators — Modular Evaluation Architecture Registry
Provides unified factory access to deterministic evaluation engines:
- 'fallback': Fast deterministic rule-based evaluation
"""

from typing import Dict, Type

from scripts.evaluators.base import BaseEvaluator
from scripts.evaluators.fallback import FallbackEvaluator

_REGISTRY: Dict[str, Type[BaseEvaluator]] = {
    "fallback": FallbackEvaluator,
}


def get_evaluator(name: str) -> BaseEvaluator:
    """
    Factory function returning an instance of the requested evaluator engine.

    Args:
        name: Name of the evaluator engine ('fallback')

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
    "FallbackEvaluator",
    "get_evaluator",
]
