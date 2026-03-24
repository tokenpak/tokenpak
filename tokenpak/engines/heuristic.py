"""Heuristic-based compaction engine (fast, no ML dependencies)."""

from typing import Optional

from .base import CompactionEngine, CompactionHints


class HeuristicEngine(CompactionEngine):
    """
    Fast heuristic compaction using rule-based text processing.

    No ML dependencies required. Suitable for:
    - Real-time interactive use
    - Resource-constrained environments
    - Baseline comparison
    """

    name = "heuristic"

    def __init__(self) -> None:
        # Import here to avoid circular imports
        from ..processors.text import TextProcessor

        self._processor = TextProcessor(aggressive=True)

    def compact(self, text: str, hints: Optional[CompactionHints] = None) -> str:
        """Compact using heuristic rules."""
        if not text:
            return text

        hints = hints or CompactionHints()

        # Use the existing text processor
        result = self._processor.process(text, "")

        # If still over target, truncate intelligently
        if hints.target_tokens > 0:
            est_tokens = self.estimate_tokens(result)
            if est_tokens > hints.target_tokens:
                # Truncate to approximate target
                target_chars = hints.target_tokens * 4
                if len(result) > target_chars:
                    result = result[:target_chars].rsplit("\n", 1)[0] + "\n…"

        return result
