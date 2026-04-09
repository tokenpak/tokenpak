from typing import Any, Dict, List, Optional


class TokenPakMessage:
    def __init__(
        self,
        role: str,
        content: str,
        budget: Optional[int] = None,
        avg_tokens_per_char: float = 0.25,
    ) -> None:
        self.role = role
        self._content = content
        self.budget = budget
        self.avg_tokens_per_char = avg_tokens_per_char

    @property
    def content(self) -> str:
        if self.budget is not None:
            max_chars = int(self.budget / self.avg_tokens_per_char)
            return self._content[:max_chars]
        return self._content

    @property
    def token_count(self) -> int:
        return max(1, int(len(self.content) * self.avg_tokens_per_char))

    def to_dict(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content}


def compress_messages(
    messages: List[Dict[str, Any]],
    budget: int = 4000,
    avg_tokens_per_char: float = 0.25,
) -> List[Dict[str, Any]]:
    def estimate(text: str) -> int:
        return max(1, int(len(text) * avg_tokens_per_char))

    selected: List[Dict[str, Any]] = []
    used = 0
    for msg in reversed(messages):
        tokens = estimate(msg.get("content", ""))
        if used + tokens <= budget:
            selected.insert(0, msg)
            used += tokens
        else:
            if msg.get("role") == "system":
                remaining_chars = int((budget - used) / avg_tokens_per_char)
                if remaining_chars > 50:
                    truncated = dict(msg)
                    truncated["content"] = msg["content"][:remaining_chars] + "..."
                    selected.insert(0, truncated)
            break
    return selected
