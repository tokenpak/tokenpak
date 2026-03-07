"""
TokenPakMessage — Message utilities for AutoGen.
"""

from typing import Any, Dict, Optional


class TokenPakMessage:
    """Utilities for compressing AutoGen messages."""

    @staticmethod
    def compress_content(
        content: str,
        max_tokens: int = 200,
    ) -> str:
        """Compress message content."""
        if len(content) // 4 <= max_tokens:
            return content
        
        # Simple truncation; production uses real compression
        new_len = max_tokens * 4
        return content[:new_len] + "..."

    @staticmethod
    def compress_message(
        message: Dict[str, Any],
        max_tokens: int = 200,
    ) -> Dict[str, Any]:
        """Compress entire message."""
        return {
            **message,
            "content": TokenPakMessage.compress_content(
                message.get("content", ""),
                max_tokens,
            ),
        }
