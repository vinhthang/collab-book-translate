"""
scripts/evaluators/base.py — Abstract Base Evaluator Protocol
Defines the standard BaseEvaluator interface for translation evaluators.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseEvaluator(ABC):
    """Abstract base class that all evaluation engine implementations must satisfy."""

    @abstractmethod
    def evaluate(
        self,
        source_text: str,
        draft_text: str,
        book_record: Optional[Dict[str, Any]] = None,
        timeout: int = 300,
        source_lang: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Evaluates a source text block against draft translation and returns a structured issue envelope.

        Parameters:
            source_text: Original English text (or segmented block)
            draft_text: Target Vietnamese translation (or segmented block)
            book_record: Optional canonical glossary, terminology, and character record
            timeout: Maximum execution timeout in seconds

        Returns:
            Dictionary with 'issues', 'dimensions', 'weighted_total', 'verdict', and 'notes'.
        """
        pass
