"""Budgeting and deterministic compression helpers for CrewAI contexts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional


def estimate_tokens(text: str) -> int:
    """Approximate token count without external tokenizer dependencies."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


@dataclass(frozen=True)
class CompressionResult:
    """Result returned by the deterministic context compressor."""

    text: str
    original_tokens: int
    compressed_tokens: int
    was_compressed: bool
    budget: int

    @property
    def savings_tokens(self) -> int:
        """Number of estimated tokens removed by compression."""
        return max(0, self.original_tokens - self.compressed_tokens)

    @property
    def savings_ratio(self) -> float:
        """Fraction of estimated tokens removed by compression."""
        if self.original_tokens == 0:
            return 0.0
        return self.savings_tokens / self.original_tokens


@dataclass(frozen=True)
class AgentContextConfig:
    """Optional per-agent override for context compression."""

    budget: Optional[int] = None
    prefix: str = ""
    suffix: str = ""


class TokenPakContext:
    """Manage budgets and usage accounting for multi-agent CrewAI runs."""

    def __init__(
        self,
        total_budget: int = 8000,
        per_agent_budget: Optional[int] = None,
        agent_overrides: Optional[Mapping[str, AgentContextConfig]] = None,
    ) -> None:
        self.total_budget = max(1, total_budget)
        self.per_agent_budget = per_agent_budget or max(1, total_budget // 4)
        self.agent_overrides = dict(agent_overrides or {})
        self._agent_tokens: Dict[str, int] = {}

    def allocate_budget(self, agent_id: str) -> int:
        """Return the configured budget for an agent."""
        override = self.agent_overrides.get(agent_id)
        if override and override.budget is not None:
            return max(1, override.budget)
        return self.per_agent_budget

    def record_usage(self, agent_id: str, tokens_used: int) -> None:
        """Record non-negative token usage for an agent."""
        self._agent_tokens[agent_id] = max(0, tokens_used)

    def get_usage(self) -> Dict[str, int]:
        """Get a copy of per-agent token usage."""
        return self._agent_tokens.copy()

    def remaining_budget(self) -> int:
        """Return the remaining global budget after recorded usage."""
        return max(0, self.total_budget - sum(self._agent_tokens.values()))

    def reset_usage(self) -> None:
        """Clear recorded usage."""
        self._agent_tokens.clear()

    def report(self) -> Dict[str, int]:
        """Return a compact usage summary suitable for tests and logging."""
        return {
            "total_budget": self.total_budget,
            "per_agent_budget": self.per_agent_budget,
            "remaining_budget": self.remaining_budget(),
            "agents_tracked": len(self._agent_tokens),
        }

    def compress_text(self, text: str, budget: int) -> CompressionResult:
        """Compress text into a token budget using simple deterministic rules."""
        original_tokens = estimate_tokens(text)
        normalized = _normalize_text(text)
        if estimate_tokens(normalized) <= budget:
            return CompressionResult(
                text=normalized,
                original_tokens=original_tokens,
                compressed_tokens=estimate_tokens(normalized),
                was_compressed=normalized != text,
                budget=budget,
            )

        deduped = _dedupe_lines(normalized)
        if estimate_tokens(deduped) <= budget:
            return CompressionResult(
                text=deduped,
                original_tokens=original_tokens,
                compressed_tokens=estimate_tokens(deduped),
                was_compressed=True,
                budget=budget,
            )

        target_chars = max(1, budget * 4)
        clipped = _clip_text(deduped, target_chars)
        return CompressionResult(
            text=clipped,
            original_tokens=original_tokens,
            compressed_tokens=estimate_tokens(clipped),
            was_compressed=True,
            budget=budget,
        )


def _normalize_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def _dedupe_lines(text: str) -> str:
    seen: set[str] = set()
    kept: list[str] = []
    for line in text.splitlines():
        if line in seen:
            continue
        seen.add(line)
        kept.append(line)
    return "\n".join(kept)


def _clip_text(text: str, target_chars: int) -> str:
    if len(text) <= target_chars:
        return text
    if target_chars <= 12:
        return text[:target_chars]

    head = max(1, target_chars // 2 - 3)
    tail = max(1, target_chars - head - 5)
    return f"{text[:head]} ... {text[-tail:]}"
