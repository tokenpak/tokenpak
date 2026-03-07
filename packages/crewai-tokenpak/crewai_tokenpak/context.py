"""
TokenPakContext — Manages context across CrewAI agents.

Coordinates token budgets and compression across multi-agent workflows.
"""

from typing import Any, Dict, List, Optional


class TokenPakContext:
    """
    Manages TokenPak compression context for CrewAI crews.

    Coordinates budgets across multiple agents and tasks.
    """

    def __init__(
        self,
        total_budget: int = 8000,
        per_agent_budget: Optional[int] = None,
    ):
        self.total_budget = total_budget
        self.per_agent_budget = per_agent_budget or (total_budget // 4)
        self._agent_tokens: Dict[str, int] = {}

    def allocate_budget(self, agent_id: str) -> int:
        """Get token budget for agent."""
        return self.per_agent_budget

    def record_usage(self, agent_id: str, tokens_used: int) -> None:
        """Record token usage by agent."""
        self._agent_tokens[agent_id] = tokens_used

    def get_usage(self) -> Dict[str, int]:
        """Get token usage per agent."""
        return self._agent_tokens.copy()
