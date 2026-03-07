"""TokenPakMessage utilities for AutoGen conversations."""

from typing import Any, Dict


class TokenPakMessage:
    """Utilities for compressing AutoGen-style messages."""

    @staticmethod
    def compress_content(content: str, max_tokens: int = 200) -> str:
        """Compress content with conservative truncation."""
        if max_tokens <= 0:
            return "..."
        if len(content) // 4 <= max_tokens:
            return content
        new_len = max_tokens * 4
        return content[:new_len] + "..."

    @staticmethod
    def compress_message(message: Dict[str, Any], max_tokens: int = 200) -> Dict[str, Any]:
        """Compress a message dict while preserving existing keys."""
        return {
            **message,
            "content": TokenPakMessage.compress_content(message.get("content", ""), max_tokens=max_tokens),
        }
