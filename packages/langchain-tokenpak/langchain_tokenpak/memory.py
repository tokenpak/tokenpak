"""
TokenPakMemory — LangChain-compatible chat message history with compression.

Automatically compresses older messages when exceeding a token budget,
keeping recent turns intact for quality.
"""

from typing import Optional, List, Dict, Any
import hashlib


class TokenPakMemory:
    """
    LangChain-compatible chat message history with TokenPak compression.

    Compresses older messages when exceeding a token budget, preserving
    recent conversation turns for better context quality.

    Usage:
        memory = TokenPakMemory(max_tokens=2000, keep_recent_turns=4)
        memory.add_user_message("Hello!")
        memory.add_ai_message("Hi there!")
        messages = memory.messages  # auto-compressed if over budget
    """

    def __init__(
        self,
        max_tokens: int = 2000,
        keep_recent_turns: int = 4,
        session_id: Optional[str] = None,
    ):
        """
        Initialize TokenPakMemory.

        Args:
            max_tokens: Maximum tokens for entire message history
            keep_recent_turns: Number of user-AI turns to always keep uncompressed
            session_id: Optional session identifier
        """
        self.max_tokens = max_tokens
        self.keep_recent_turns = keep_recent_turns
        self.session_id = session_id or "default"
        self._messages: List[Dict[str, Any]] = []

    def add_user_message(self, content: str) -> None:
        """Add a user message."""
        self._messages.append({
            "role": "human",
            "content": content,
            "type": "human",
        })

    def add_ai_message(self, content: str) -> None:
        """Add an AI message."""
        self._messages.append({
            "role": "ai",
            "content": content,
            "type": "ai",
        })

    def add_message(self, role: str, content: str) -> None:
        """Add a message with explicit role."""
        self._messages.append({
            "role": role,
            "content": content,
            "type": role,
        })

    @property
    def messages(self) -> List[Dict[str, Any]]:
        """
        Return messages, compressing older ones if over budget.

        This is the property LangChain chains call to get message history.
        """
        if self._estimate_tokens() <= self.max_tokens:
            return self._messages

        # Keep recent turns uncompressed
        keep_count = self.keep_recent_turns * 2
        if len(self._messages) <= keep_count:
            return self._messages

        recent = self._messages[-keep_count:]
        older = self._messages[:-keep_count]

        # Compress older messages
        compressed_older = [self._compress_message(m) for m in older]
        return compressed_older + recent

    def _estimate_tokens(self) -> int:
        """Estimate total tokens in all messages."""
        return sum(len(m.get("content", "")) // 4 for m in self._messages)

    def _compress_message(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """Compress a single message."""
        content = msg.get("content", "")
        if not content or len(content) < 100:
            return msg

        # Simple compression: take summary (first 50 chars + last 50 chars)
        if len(content) > 200:
            compressed = content[:50] + f"\n[...{len(content)-100} chars...]\n" + content[-50:]
        else:
            compressed = content

        return {
            **msg,
            "content": compressed,
            "_compressed": True,
            "_original_length": len(content),
        }

    def clear(self) -> None:
        """Clear all messages."""
        self._messages = []

    def __len__(self) -> int:
        """Return number of messages."""
        return len(self._messages)

    def __repr__(self) -> str:
        return f"TokenPakMemory(messages={len(self._messages)}, tokens={self._estimate_tokens()})"
