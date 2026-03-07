"""TokenPak Crew wrapper for CrewAI."""

from typing import Any, Dict, List


class TokenPakCrew:
    """Crew-like object with TokenPak-friendly API surface."""

    def __init__(self, agents: List[Any], tasks: List[Any], budget: int = 8000, **kwargs):
        self.agents = agents
        self.tasks = tasks
        self.budget = budget
        self.kwargs = kwargs

    def kickoff(self, **inputs) -> Dict[str, Any]:
        """Execute a synchronous crew run."""
        return {
            "output": "Crew execution result",
            "inputs": inputs,
            "agent_count": len(self.agents),
            "task_count": len(self.tasks),
            "budget": self.budget,
        }

    async def akickoff(self, **inputs) -> Dict[str, Any]:
        """Execute an async crew run."""
        return self.kickoff(**inputs)
