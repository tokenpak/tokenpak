"""Base compaction engine interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class CompactionHints:
    """Hints for compaction behavior."""

    target_tokens: int = 1000
    preserve_patterns: Optional[List[str]] = None  # Regex patterns to never remove
    preserve_first_n_sentences: int = 1
    preserve_last_n_sentences: int = 0
    keep_headers: bool = True
    keep_code_blocks: bool = True
    aggressive: bool = False


class CompactionEngine(ABC):
    """Base class for compaction engines."""

    name: str = "base"

    @abstractmethod
    def compact(self, text: str, hints: Optional[CompactionHints] = None) -> str:
        """
        Compact text according to hints.

        Args:
            text: Input text to compact
            hints: Optional compaction hints

        Returns:
            Compacted text
        """
        pass

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count for text."""
        return max(1, len(text) // 4)
