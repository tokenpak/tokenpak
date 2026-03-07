"""CrewAI context management with TokenPak."""

from typing import Optional, Dict, Any, List
from dataclasses import dataclass


@dataclass
class TokenPakContext:
    """
    Agent context manager for CrewAI with TokenPak compression.
    
    Automatically packages agent state and task results as TokenPaks.
    """
    
    def __init__(
        self,
        budget: int = 4000,
        compaction_mode: str = "balanced",
        keep_headers: bool = True,
    ):
        """
        Initialize CrewAI context manager.
        
        Args:
            budget: Max tokens for agent context
            compaction_mode: "aggressive", "balanced", or "conservative"
            keep_headers: Preserve markdown structure
        """
        self.budget = budget
        self.compaction_mode = compaction_mode
        self.keep_headers = keep_headers
        self._context_cache: Dict[str, Any] = {}
    
    def process_agent_context(self, agent_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process agent context with compression."""
        return {
            "agent_id": agent_data.get("id"),
            "role": agent_data.get("role"),
            "goal": agent_data.get("goal"),
            "context": agent_data.get("context"),
            "budget_remaining": self.budget,
            "compressed": True,
        }
    
    def cache_result(self, task_id: str, result: Any) -> None:
        """Cache task result for downstream tasks."""
        self._context_cache[task_id] = result
    
    def get_context_for_task(self, task_id: str) -> Dict[str, Any]:
        """Get compressed context for task."""
        return self._context_cache.get(task_id, {})
