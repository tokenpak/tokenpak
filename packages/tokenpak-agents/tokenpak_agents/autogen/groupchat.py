"""TokenPakGroupChat for AutoGen-style group conversations."""

from typing import Any, Dict, List

from .message import TokenPakMessage


class TokenPakGroupChat:
    """Group chat container with optional budget-based compression."""

    def __init__(self, agents: List[Any], budget: int = 8000, **kwargs):
        self.agents = agents
        self.budget = budget
        self.kwargs = kwargs
        self.messages: List[Dict[str, Any]] = []

    def add_message(self, message: Dict[str, Any]) -> None:
        """Append a message dict to history."""
        self.messages.append(message)

    def _compress_history(self) -> List[Dict[str, Any]]:
        """Return history compressed to current budget."""
        compressed: List[Dict[str, Any]] = []
        token_estimate = 0
        for message in reversed(self.messages):
            est = len(message.get("content", "")) // 4
            if token_estimate + est > self.budget:
                break
            compressed.insert(0, TokenPakMessage.compress_message(message, max_tokens=200))
            token_estimate += est
        return compressed
