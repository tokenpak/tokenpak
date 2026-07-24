"""Bounded in-memory conversation history for LangChain integrations."""

from __future__ import annotations


class TokenPakMemory:
    def __init__(
        self,
        budget: int = 2000,
        compression_ratio: float = 0.5,
        avg_tokens_per_char: float = 0.25,
    ) -> None:
        self.budget = budget
        self.compression_ratio = compression_ratio
        self.avg_tokens_per_char = avg_tokens_per_char
        self._messages: list[dict[str, str]] = []
        self._compressed_summary: str | None = None

    def _estimate_tokens(self, text: str) -> int:
        return max(1, int(len(text) * self.avg_tokens_per_char))

    def _total_tokens(self) -> int:
        total = 0
        if self._compressed_summary:
            total += self._estimate_tokens(self._compressed_summary)
        for msg in self._messages:
            total += self._estimate_tokens(msg["content"])
        return total

    def _compress(self) -> None:
        if not self._messages:
            return
        keep_count = max(1, int(len(self._messages) * (1 - self.compression_ratio)))
        old_messages = self._messages[:-keep_count]
        self._messages = self._messages[-keep_count:]
        parts = []
        if self._compressed_summary:
            parts.append(self._compressed_summary)
        for msg in old_messages:
            parts.append(f"[{msg['role']}]: {msg['content'][:100]}...")
        self._compressed_summary = " | ".join(parts)

    def add_message(self, role: str, content: str) -> None:
        self._messages.append({"role": role, "content": content})
        while self._total_tokens() > self.budget and len(self._messages) > 1:
            self._compress()

    def get_history(self) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        if self._compressed_summary:
            result.append({"role": "system", "content": f"[Summary]: {self._compressed_summary}"})
        result.extend(self._messages)
        return result

    def clear(self) -> None:
        self._messages = []
        self._compressed_summary = None

    @property
    def token_usage(self) -> dict[str, int]:
        used = self._total_tokens()
        return {
            "used": used,
            "budget": self.budget,
            "remaining": max(0, self.budget - used),
            "messages": len(self._messages),
        }
