"""
TokenPakState — State management for LangGraph with compression.

Manages state in multi-agent workflows with automatic context compression
between agent calls.
"""

from typing import Any, Dict, Optional


class TokenPakState:
    """
    LangGraph-compatible state object with TokenPak compression.

    Automatically compresses messages and context when the state
    exceeds a token budget.

    Usage:
        state = TokenPakState(max_tokens=4000)
        state.append_message("agent_name", "response text")
        compressed_messages = state.messages  # auto-compressed
    """

    def __init__(
        self,
        max_tokens: int = 4000,
        keep_recent_messages: int = 10,
    ):
        """
        Initialize TokenPakState.

        Args:
            max_tokens: Maximum tokens in the state
            keep_recent_messages: Number of recent messages to preserve uncompressed
        """
        self.max_tokens = max_tokens
        self.keep_recent_messages = keep_recent_messages
        self._messages: list[Dict[str, Any]] = []

    def append_message(
        self,
        agent: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add a message from an agent."""
        self._messages.append({
            "agent": agent,
            "content": content,
            "metadata": metadata or {},
        })

    @property
    def messages(self) -> list[Dict[str, Any]]:
        """Get messages, compressing older ones if needed."""
        if self._estimate_tokens() <= self.max_tokens:
            return self._messages

        # Keep recent messages
        keep_count = self.keep_recent_messages
        if len(self._messages) <= keep_count:
            return self._messages

        recent = self._messages[-keep_count:]
        older = self._messages[:-keep_count]

        # Compress older messages
        compressed_older = [self._compress_message(m) for m in older]
        return compressed_older + recent

    def _estimate_tokens(self) -> int:
        """Estimate total tokens in state."""
        return sum(len(m.get("content", "")) // 4 for m in self._messages)

    def _compress_message(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """Compress a single message."""
        content = msg.get("content", "")
        if len(content) <= 100:
            return msg

        # Keep first and last parts
        compressed = content[:50] + f"\n[...{len(content)-100} chars...]\n" + content[-50:]
        return {
            **msg,
            "content": compressed,
            "_compressed": True,
        }

    def clear(self) -> None:
        """Clear all messages."""
        self._messages = []

    def __len__(self) -> int:
        """Return number of messages."""
        return len(self._messages)
