from typing import Any, Dict, List, Optional
from .message import compress_messages


class TokenPakAssistant:
    def __init__(self, agent: Any, budget: int = 6000) -> None:
        self.agent = agent
        self.budget = budget
        self.name = getattr(agent, "name", "tokenpak_assistant")

    def compress_history(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return compress_messages(messages, budget=self.budget)

    def initiate_chat(self, recipient: Any, message: str, **kwargs: Any) -> Any:
        return self.agent.initiate_chat(recipient, message=message, **kwargs)

    def generate_reply(
        self,
        messages: Optional[List[Dict[str, Any]]] = None,
        sender: Optional[Any] = None,
        **kwargs: Any,
    ) -> Any:
        if messages:
            messages = self.compress_history(messages)
        return self.agent.generate_reply(messages=messages, sender=sender, **kwargs)

    @property
    def budget_status(self) -> Dict[str, int]:
        return {"budget": self.budget}
