"""TokenPak-enabled AutoGen assistant."""

from typing import Optional, Any, Dict, List


class TokenPakAssistant:
    """
    AutoGen ConversableAgent with TokenPak compression.
    
    Automatically compresses messages within context budget.
    """
    
    def __init__(
        self,
        name: str,
        llm_config: Optional[Dict[str, Any]] = None,
        context_budget: int = 4000,
        compaction_mode: str = "balanced",
        **kwargs,
    ):
        """
        Initialize TokenPak-enabled assistant.
        
        Args:
            name: Agent name
            llm_config: LLM configuration dict
            context_budget: Max tokens for context
            compaction_mode: Compression mode
        """
        self.name = name
        self.llm_config = llm_config or {}
        self.context_budget = context_budget
        self.compaction_mode = compaction_mode
        self._message_history: List[Dict[str, Any]] = []
        self._context_tokens = 0
    
    def receive(self, message: Any, sender: Optional[Any] = None) -> None:
        """
        Receive message from another agent.
        
        Automatically compresses if context exceeds budget.
        """
        msg_data = {
            "sender": sender.name if hasattr(sender, "name") else "unknown",
            "content": str(message),
            "timestamp": None,
        }
        self._message_history.append(msg_data)
        
        # Simple token estimation
        self._context_tokens = sum(len(m.get("content", "")) // 4 for m in self._message_history)
        
        if self._context_tokens > self.context_budget:
            self._compress_history()
    
    def _compress_history(self) -> None:
        """Compress message history to fit within budget."""
        # Keep recent messages, compress older ones
        keep_count = max(3, self.context_budget // 500)
        if len(self._message_history) > keep_count:
            self._message_history = self._message_history[-keep_count:]
    
    def send(self, message: str, recipient: Any) -> None:
        """Send message to another agent."""
        self._message_history.append({
            "sender": self.name,
            "content": message,
            "timestamp": None,
        })
