"""LLMLingua-based compaction engine (ML-powered, higher quality)."""

from typing import Optional

from .base import CompactionEngine, CompactionHints


class LLMLinguaEngine(CompactionEngine):
    """
    ML-powered compaction using Microsoft LLMLingua.

    Requires: pip install llmlingua

    Provides:
    - Higher compression ratios (5-20x vs 2-5x heuristic)
    - Better semantic preservation
    - Configurable force tokens

    Tradeoffs:
    - Slower (requires model inference)
    - Higher memory usage
    - Requires model download on first use
    """

    name = "llmlingua"

    def __init__(self, model_name: str = "microsoft/llmlingua-2-xlm-roberta-large-meetingbank"):
        try:
            from llmlingua import PromptCompressor

            self._compressor = PromptCompressor(
                model_name=model_name, use_llmlingua2=True, device_map="auto"
            )
            self._available = True
        except ImportError as e:
            self._available = False
            self._error = str(e)

    def compact(self, text: str, hints: Optional[CompactionHints] = None) -> str:
        """Compact using LLMLingua-2."""
        if not self._available:
            raise RuntimeError(f"LLMLingua not available: {self._error}")

        if not text:
            return text

        hints = hints or CompactionHints()

        # Build force tokens list from preserve patterns
        force_tokens = []
        if hints.preserve_patterns:
            import re

            for pattern in hints.preserve_patterns:
                matches = re.findall(pattern, text)
                force_tokens.extend(matches)

        # Calculate target ratio from target_tokens
        current_tokens = self.estimate_tokens(text)
        if hints.target_tokens > 0 and current_tokens > hints.target_tokens:
            target_ratio = hints.target_tokens / current_tokens
        else:
            target_ratio = 0.5  # Default 50% compression

        # Run LLMLingua compression
        result = self._compressor.compress_prompt(
            text,
            rate=target_ratio,
            force_tokens=force_tokens if force_tokens else None,
            force_reserve_digit=True,
            drop_consecutive=True,
        )

        return result.get("compressed_prompt", text)

    def estimate_tokens(self, text: str) -> int:
        """Estimate tokens using the model's tokenizer if available."""
        if self._available and hasattr(self._compressor, "tokenizer"):
            return len(self._compressor.tokenizer.encode(text))
        return max(1, len(text) // 4)
