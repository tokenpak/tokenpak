"""Budget allocation using quadratic importance weighting."""

from dataclasses import dataclass
from typing import List, Dict


@dataclass
class BudgetBlock:
    """Block metadata for budget allocation."""
    ref: str
    relevance_score: float = 0.5  # 0-1
    recency_score: float = 0.5    # 0-1
    quality_score: float = 1.0    # 0-1
    type_weight: float = 0.5      # 0-1

    @property
    def importance(self) -> float:
        """Composite importance score (0-10)."""
        score = (
            0.4 * self.relevance_score +
            0.2 * self.recency_score +
            0.2 * self.quality_score +
            0.2 * self.type_weight
        )
        return max(0.0, min(10.0, score * 10))


def quadratic_allocate(blocks: List[BudgetBlock], total_budget: int, floor_ratio: float = 0.03) -> Dict[str, int]:
    """
    Allocate token budget with quadratic weighting.

    - Importance is squared to emphasize high-value blocks.
    - Every block gets a minimum floor (default 3%).
    """
    if not blocks or total_budget <= 0:
        return {}

    block_count = len(blocks)
    floor_tokens = int(total_budget * floor_ratio)

    # Prevent impossible allocations
    if floor_tokens * block_count > total_budget:
        floor_tokens = max(1, total_budget // block_count)

    remaining = total_budget - (floor_tokens * block_count)

    squared_importance = {b.ref: max(0.0, b.importance) ** 2 for b in blocks}
    total_sq = sum(squared_importance.values())

    allocations = {b.ref: floor_tokens for b in blocks}

    if total_sq > 0 and remaining > 0:
        for ref, sq in squared_importance.items():
            allocations[ref] += int((sq / total_sq) * remaining)

    # Distribute rounding remainder
    allocated = sum(allocations.values())
    remainder = total_budget - allocated
    if remainder > 0:
        # Give remainder to highest-importance blocks first
        ranked = sorted(blocks, key=lambda b: b.importance, reverse=True)
        for i in range(remainder):
            allocations[ranked[i % len(ranked)].ref] += 1

    return allocations
