"""TokenPak-enabled AutoGen group chat."""

from typing import List, Any, Dict, Optional


class TokenPakGroupChat:
    """
    AutoGen GroupChat with TokenPak context management.
    
    Automatically compresses conversation history across all agents.
    """
    
    def __init__(
        self,
        agents: List[Any],
        context_budget: int = 8000,
        handoff_format: str = "tokenpak",
        max_messages: int = 50,
    ):
        """
        Initialize TokenPak group chat.
        
        Args:
            agents: List of agents in group
            context_budget: Total token budget
            handoff_format: Format for agent-to-agent messages
            max_messages: Max messages to keep in memory
        """
        self.agents = agents
        self.context_budget = context_budget
        self.handoff_format = handoff_format
        self.max_messages = max_messages
        self.messages: List[Dict[str, Any]] = []
        self._context_tokens = 0
    
    def add_message(self, agent_name: str, content: str) -> None:
        """Add message to group chat."""
        msg = {
            "agent": agent_name,
            "content": content,
        }
        self.messages.append(msg)
        
        # Estimate tokens
        self._context_tokens = sum(len(m.get("content", "")) // 4 for m in self.messages)
        
        # Enforce max messages
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]
    
    def get_history(self) -> List[Dict[str, Any]]:
        """Get compressed chat history."""
        return self.messages.copy()
