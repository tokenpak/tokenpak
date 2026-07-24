"""Token-budget allocation helpers for LangChain contexts."""

from __future__ import annotations


class TokenPakContextManager:
    def __init__(self, total_budget: int = 8000, avg_tokens_per_char: float = 0.25) -> None:
        self.total_budget = total_budget
        self.avg_tokens_per_char = avg_tokens_per_char
        self._allocations: dict[str, int] = {}

    def allocate(self, source: str, tokens: int) -> int:
        actual = min(tokens, self.remaining)
        self._allocations[source] = actual
        return actual

    @property
    def allocated(self) -> int:
        return sum(self._allocations.values())

    @property
    def remaining(self) -> int:
        return max(0, self.total_budget - self.allocated)

    def estimate_tokens(self, text: str) -> int:
        return max(1, int(len(text) * self.avg_tokens_per_char))

    def fits(self, source: str, text: str) -> bool:
        return self.estimate_tokens(text) <= self._allocations.get(source, 0)

    def trim_to_budget(self, source: str, text: str) -> str:
        allocation = self._allocations.get(source, 0)
        if allocation <= 0:
            return ""
        return text[: int(allocation / self.avg_tokens_per_char)]

    def status(self) -> dict[str, int | dict[str, int]]:
        return {
            "total": self.total_budget,
            "allocated": self.allocated,
            "remaining": self.remaining,
            "allocations": dict(self._allocations),
        }
