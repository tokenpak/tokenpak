from typing import Any, Dict, List

from .message import compress_messages


class TokenPakGroupChat:
    def __init__(self, groupchat: Any, manager: Any, budget: int = 4000) -> None:
        self.groupchat = groupchat
        self.manager = manager
        self.budget = budget

    def get_compressed_history(self) -> List[Dict[str, Any]]:
        messages = getattr(self.groupchat, "messages", [])
        return compress_messages(messages, budget=self.budget)

    def run(self, initiator: Any, message: str, **kwargs: Any) -> Any:
        return initiator.initiate_chat(self.manager, message=message, **kwargs)

    @property
    def message_count(self) -> int:
        return len(getattr(self.groupchat, "messages", []))

    @property
    def budget_status(self) -> Dict[str, Any]:
        return {"budget": self.budget, "messages": self.message_count}
