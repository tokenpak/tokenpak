"""Query result dataclasses for TokenPak telemetry API."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CostSummary:
    """Aggregated cost summary across all sessions."""

    total_cost: float = 0.0
    by_model: dict[str, float] = field(default_factory=dict)
    by_provider: dict[str, float] = field(default_factory=dict)
    daily: list[dict] = field(default_factory=list)
    period_days: int = 30


@dataclass
class ModelUsage:
    """Per-model token usage statistics."""

    model: str = ""
    provider: str = ""
    request_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    avg_latency_ms: float | None = None


@dataclass
class SavingsReport:
    """Token savings summary comparing raw tokens vs compressed tokens."""

    total_cost: float = 0.0
    estimated_without_compression: float = 0.0
    savings_amount: float = 0.0
    savings_pct: float = 0.0
    cache_hit_rate: float = 0.0


@dataclass
class DailyTrend:
    """Daily aggregated usage entry for trend visualisation."""

    date: str = ""
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    request_count: int = 0
