"""TokenPakContext for CrewAI workflows."""

from typing import Any, Dict, Optional


class TokenPakContext:
    """Manage token budgets and usage accounting for CrewAI agents."""

    def __init__(self, total_budget: int = 8000, per_agent_budget: Optional[int] = None):
        self.total_budget = total_budget
        self.per_agent_budget = per_agent_budget or (total_budget // 4)
        self._agent_tokens: Dict[str, int] = {}

    def allocate_budget(self, agent_id: str) -> int:
        """Return budget assigned to an agent."""
        return self.per_agent_budget

    def record_usage(self, agent_id: str, tokens_used: int) -> None:
        """Record usage for one agent."""
        self._agent_tokens[agent_id] = max(0, tokens_used)

    def get_usage(self) -> Dict[str, int]:
        """Get usage snapshot."""
        return self._agent_tokens.copy()

    def remaining_budget(self) -> int:
        """Return remaining global budget after recorded usage."""
        used = sum(self._agent_tokens.values())
        return max(0, self.total_budget - used)

    def reset_usage(self) -> None:
        """Clear all usage records."""
        self._agent_tokens.clear()
