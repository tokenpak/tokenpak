from typing import Any, Dict


class TokenPakHandoff:
    def __init__(self, budget=2000, avg_tokens_per_char=0.25):
        self.budget = budget
        self.avg_tokens_per_char = avg_tokens_per_char

    def _estimate_tokens(self, text):
        return max(1, int(len(text) * self.avg_tokens_per_char))

    def compress(self, state: Dict[str, Any]) -> Dict[str, Any]:
        result = {}
        budget_remaining = self.budget
        for key, value in state.items():
            if isinstance(value, str):
                tokens = self._estimate_tokens(value)
                if tokens <= budget_remaining:
                    result[key] = value
                    budget_remaining -= tokens
                else:
                    max_chars = int(budget_remaining / self.avg_tokens_per_char)
                    result[key] = value[:max_chars] + "..." if max_chars > 10 else "[truncated]"
                    budget_remaining = 0
            else:
                result[key] = value
        return result

    def package(self, agent_output, metadata=None):
        return self.compress({"output": agent_output})
