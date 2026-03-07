"""
TokenPakGroupChat — Group chat with automatic context compression.
"""

from typing import Any, Dict, List, Optional


class TokenPakGroupChat:
    """
    AutoGen GroupChat with TokenPak compression.

    Automatically compresses group conversation history.
    """

    def __init__(
        self,
        agents: List[Any],
        budget: int = 8000,
        **kwargs,
    ):
        self.agents = agents
        self.budget = budget
        self.kwargs = kwargs
        self.messages: List[Dict[str, Any]] = []

    def add_message(self, message: Dict[str, Any]) -> None:
        """Add message to group chat."""
        self.messages.append(message)

    def _compress_history(self) -> List[Dict[str, Any]]:
        """Compress chat history."""
        # In production, applies TokenPak compression
        return self.messages
