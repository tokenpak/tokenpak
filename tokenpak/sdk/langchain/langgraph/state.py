from typing import Dict, List


class TokenPakState:
    def __init__(self, budget=8000):
        self.budget = budget
        self._context: Dict[str, List[str]] = {}
        self._messages: List[Dict[str, str]] = []
        self.avg_tokens_per_char = 0.25

    def _estimate_tokens(self, text):
        return max(1, int(len(text) * self.avg_tokens_per_char))

    def add_context(self, key, content):
        if self.used_tokens + self._estimate_tokens(content) > self.budget:
            return False
        self._context.setdefault(key, []).append(content)
        return True

    def add_message(self, role, content):
        self._messages.append({"role": role, "content": content})

    @property
    def used_tokens(self):
        total = 0
        for items in self._context.values():
            for item in items:
                total += self._estimate_tokens(item)
        for msg in self._messages:
            total += self._estimate_tokens(msg["content"])
        return total

    @property
    def remaining_tokens(self):
        return max(0, self.budget - self.used_tokens)

    def get_context(self, key):
        return self._context.get(key, [])

    def to_dict(self):
        return {
            "budget": self.budget,
            "used_tokens": self.used_tokens,
            "remaining_tokens": self.remaining_tokens,
            "context": dict(self._context),
            "messages": list(self._messages),
        }

    @classmethod
    def from_dict(cls, data):
        state = cls(budget=data.get("budget", 8000))
        state._context = data.get("context", {})
        state._messages = data.get("messages", [])
        return state
