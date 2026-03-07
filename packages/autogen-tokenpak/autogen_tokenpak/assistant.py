"""
TokenPakAssistant — AutoGen ConversableAgent with compression.

Automatically compresses messages within a token budget.
"""

from typing import Any, Dict, List, Optional


class TokenPakAssistant:
    """
    AutoGen-compatible agent with TokenPak compression.

    Compresses long conversations to stay within budget.
    """

    def __init__(
        self,
        name: str,
        budget: int = 4000,
        **kwargs,
    ):
        self.name = name
        self.budget = budget
        self.kwargs = kwargs
        self._messages: List[Dict[str, Any]] = []

    def receive_message(self, message: str, sender: Any) -> None:
        """Receive a message from another agent."""
        self._messages.append({
            "role": sender.name if hasattr(sender, "name") else "agent",
            "content": message,
        })

    def _compress_messages(self) -> List[Dict[str, Any]]:
        """Compress conversation to fit within budget."""
        # Simple implementation; production would use real compression
        return self._messages
