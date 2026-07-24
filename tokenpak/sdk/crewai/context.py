from typing import Dict, List


class TokenPakContext:
    def __init__(self, total_budget=8000, avg_tokens_per_char=0.25):
        self.total_budget = total_budget
        self.avg_tokens_per_char = avg_tokens_per_char
        self._agent_budgets: Dict[str, int] = {}
        self._agent_context: Dict[str, List[str]] = {}

    def register_agent(self, agent_name, budget):
        self._agent_budgets[agent_name] = budget
        self._agent_context[agent_name] = []

    def _estimate_tokens(self, text):
        return max(1, int(len(text) * self.avg_tokens_per_char))

    def _agent_tokens_used(self, agent_name):
        return sum(self._estimate_tokens(c) for c in self._agent_context.get(agent_name, []))

    def add_context(self, agent_name, content):
        budget = self._agent_budgets.get(agent_name, 1000)
        used = self._agent_tokens_used(agent_name)
        if used + self._estimate_tokens(content) > budget:
            return False
        self._agent_context.setdefault(agent_name, []).append(content)
        return True

    def get_context(self, agent_name):
        return "\n\n".join(self._agent_context.get(agent_name, []))

    def get_usage(self, agent_name):
        budget = self._agent_budgets.get(agent_name, 0)
        used = self._agent_tokens_used(agent_name)
        return {"budget": budget, "used": used, "remaining": max(0, budget - used)}
