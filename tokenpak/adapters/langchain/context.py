from typing import Dict


class TokenPakContextManager:
    def __init__(self, total_budget=8000, avg_tokens_per_char=0.25):
        self.total_budget = total_budget
        self.avg_tokens_per_char = avg_tokens_per_char
        self._allocations: Dict[str, int] = {}

    def allocate(self, source, tokens):
        actual = min(tokens, self.remaining)
        self._allocations[source] = actual
        return actual

    @property
    def allocated(self):
        return sum(self._allocations.values())

    @property
    def remaining(self):
        return max(0, self.total_budget - self.allocated)

    def estimate_tokens(self, text):
        return max(1, int(len(text) * self.avg_tokens_per_char))

    def fits(self, source, text):
        return self.estimate_tokens(text) <= self._allocations.get(source, 0)

    def trim_to_budget(self, source, text):
        allocation = self._allocations.get(source, 0)
        if allocation <= 0:
            return ""
        return text[: int(allocation / self.avg_tokens_per_char)]

    def status(self):
        return {
            "total": self.total_budget,
            "allocated": self.allocated,
            "remaining": self.remaining,
            "allocations": dict(self._allocations),
        }
