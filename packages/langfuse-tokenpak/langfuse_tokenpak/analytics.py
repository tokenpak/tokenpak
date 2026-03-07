"""
analytics.py — Token analytics aggregation for Langfuse dashboards.

Tracks compression savings and block usage over time via a lightweight
in-memory store. Data can be flushed to Langfuse as custom events.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from collections import defaultdict
from datetime import datetime, timezone


class TokenPakAnalytics:
    """
    In-session analytics aggregator for TokenPak traces.

    Tracks:
    - Token usage by block type across multiple packs
    - Compression savings (before vs. after token counts)
    - Top blocks by token usage

    Usage:
        analytics = TokenPakAnalytics()
        analytics.record_pack(blocks=pack.blocks, budget=8000, raw_tokens=5000)
        report = analytics.get_report()
    """

    def __init__(self) -> None:
        self._type_tokens: Dict[str, int] = defaultdict(int)
        self._type_counts: Dict[str, int] = defaultdict(int)
        self._total_raw_tokens: int = 0
        self._total_compiled_tokens: int = 0
        self._pack_count: int = 0
        self._top_blocks: List[Dict[str, Any]] = []
        self._started_at: str = datetime.now(timezone.utc).isoformat()

    def record_pack(
        self,
        blocks: List[Any],
        budget: Optional[int] = None,
        raw_tokens: Optional[int] = None,
    ) -> None:
        """
        Record one pack compilation for analytics aggregation.

        Args:
            blocks: List of block dicts or Block objects
            budget: Token budget for this pack
            raw_tokens: Total tokens BEFORE compression (for savings calc)
        """
        self._pack_count += 1

        compiled_tokens = 0
        for block in blocks:
            if isinstance(block, dict):
                btype = block.get("type", "unknown")
                tok = block.get("tokens", 0)
                bid = block.get("id", "unknown")
            else:
                btype = getattr(block, "type", "unknown")
                tok = getattr(block, "tokens", 0)
                bid = getattr(block, "id", "unknown")

            self._type_tokens[btype] += tok
            self._type_counts[btype] += 1
            compiled_tokens += tok

            # Track top blocks (keep top 10 by tokens)
            self._top_blocks.append({"id": bid, "type": btype, "tokens": tok})
            self._top_blocks.sort(key=lambda x: x["tokens"], reverse=True)
            self._top_blocks = self._top_blocks[:10]

        self._total_compiled_tokens += compiled_tokens
        if raw_tokens is not None:
            self._total_raw_tokens += raw_tokens
        else:
            # Assume no compression if raw_tokens not provided
            self._total_raw_tokens += compiled_tokens

    def get_report(self) -> Dict[str, Any]:
        """
        Return aggregated analytics suitable for Langfuse custom events.
        """
        savings = self._total_raw_tokens - self._total_compiled_tokens
        savings_pct = (
            round(savings / self._total_raw_tokens * 100, 1)
            if self._total_raw_tokens > 0
            else 0.0
        )
        compression_ratio = (
            round(self._total_compiled_tokens / self._total_raw_tokens, 3)
            if self._total_raw_tokens > 0
            else 1.0
        )

        type_distribution = {}
        grand_total = self._total_compiled_tokens or 1
        for btype, tokens in self._type_tokens.items():
            type_distribution[btype] = {
                "tokens": tokens,
                "count": self._type_counts[btype],
                "percent": round(tokens / grand_total * 100, 1),
            }

        return {
            "pack_count": self._pack_count,
            "total_tokens_before": self._total_raw_tokens,
            "total_tokens_after": self._total_compiled_tokens,
            "tokens_saved": savings,
            "savings_percent": savings_pct,
            "compression_ratio": compression_ratio,
            "type_distribution": type_distribution,
            "top_blocks": self._top_blocks[:10],
            "started_at": self._started_at,
        }

    def reset(self) -> None:
        """Reset all counters for a new analytics window."""
        self.__init__()
