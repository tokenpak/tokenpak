"""Cost tracking per provider and request."""

from typing import Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import json
import logging

logger = logging.getLogger(__name__)


class CostModel(str, Enum):
    """Cost models for different providers."""
    TOKEN_BASED = "token_based"  # Cost per input/output tokens
    REQUEST_BASED = "request_based"  # Fixed cost per request
    HYBRID = "hybrid"  # Both


@dataclass
class CostEntry:
    """A single cost tracking entry."""
    provider: str
    timestamp: datetime
    input_tokens: int = 0
    output_tokens: int = 0
    input_cost: float = 0.0
    output_cost: float = 0.0
    request_cost: float = 0.0
    model: Optional[str] = None
    status: str = "success"  # success, error, timeout
    metadata: Dict = field(default_factory=dict)

    @property
    def total_cost(self) -> float:
        """Calculate total cost."""
        return self.input_cost + self.output_cost + self.request_cost

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "provider": self.provider,
            "timestamp": self.timestamp.isoformat(),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "input_cost": self.input_cost,
            "output_cost": self.output_cost,
            "request_cost": self.request_cost,
            "total_cost": self.total_cost,
            "model": self.model,
            "status": self.status,
            "metadata": self.metadata,
        }


@dataclass
class ProviderCostSummary:
    """Summary of costs for a provider."""
    provider: str
    total_cost: float = 0.0
    request_count: int = 0
    token_count: int = 0
    error_count: int = 0
    avg_cost_per_request: float = 0.0
    last_updated: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "provider": self.provider,
            "total_cost": self.total_cost,
            "request_count": self.request_count,
            "token_count": self.token_count,
            "error_count": self.error_count,
            "avg_cost_per_request": self.avg_cost_per_request,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }


class CostTracker:
    """Track costs per provider and request."""

    def __init__(self, cost_models: Optional[Dict[str, CostModel]] = None):
        """
        Initialize cost tracker.

        Args:
            cost_models: Optional mapping of provider -> cost model
        """
        self.cost_models = cost_models or {}
        self.entries: list = []
        self.summaries: Dict[str, ProviderCostSummary] = {}

    def register_cost_model(self, provider: str, model: CostModel) -> None:
        """Register cost model for provider."""
        self.cost_models[provider] = model

    def track_request(
        self,
        provider: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        input_cost: float = 0.0,
        output_cost: float = 0.0,
        request_cost: float = 0.0,
        model: Optional[str] = None,
        status: str = "success",
        metadata: Optional[dict] = None,
    ) -> CostEntry:
        """
        Track a request's cost.

        Args:
            provider: Provider name
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            input_cost: Cost for input tokens
            output_cost: Cost for output tokens
            request_cost: Fixed request cost
            model: Model name
            status: Request status (success, error, timeout)
            metadata: Additional metadata

        Returns:
            CostEntry object
        """
        entry = CostEntry(
            provider=provider,
            timestamp=datetime.utcnow(),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost=input_cost,
            output_cost=output_cost,
            request_cost=request_cost,
            model=model,
            status=status,
            metadata=metadata or {},
        )

        self.entries.append(entry)
        self._update_summary(provider, entry)

        logger.debug(
            f"Tracked cost: {provider} = ${entry.total_cost:.6f} ({input_tokens} in, {output_tokens} out)"
        )

        return entry

    def _update_summary(self, provider: str, entry: CostEntry) -> None:
        """Update summary stats for provider."""
        if provider not in self.summaries:
            self.summaries[provider] = ProviderCostSummary(provider=provider)

        summary = self.summaries[provider]
        summary.total_cost += entry.total_cost
        summary.request_count += 1
        summary.token_count += entry.input_tokens + entry.output_tokens
        if entry.status == "error":
            summary.error_count += 1
        summary.last_updated = datetime.utcnow()
        summary.avg_cost_per_request = summary.total_cost / summary.request_count

    def get_provider_summary(self, provider: str) -> Optional[ProviderCostSummary]:
        """Get cost summary for provider."""
        return self.summaries.get(provider)

    def get_all_summaries(self) -> Dict[str, ProviderCostSummary]:
        """Get all provider summaries."""
        return self.summaries.copy()

    def get_total_cost(self) -> float:
        """Get total cost across all providers."""
        return sum(entry.total_cost for entry in self.entries)

    def get_entries_by_provider(self, provider: str) -> list:
        """Get all entries for a provider."""
        return [e for e in self.entries if e.provider == provider]

    def get_entries_by_status(self, status: str) -> list:
        """Get all entries with given status."""
        return [e for e in self.entries if e.status == status]

    def get_entries_by_model(self, model: str) -> list:
        """Get all entries for a model."""
        return [e for e in self.entries if e.model == model]

    def clear(self) -> None:
        """Clear all tracked entries and summaries."""
        self.entries.clear()
        self.summaries.clear()

    def export_entries(self) -> str:
        """Export entries as JSON."""
        return json.dumps([e.to_dict() for e in self.entries], indent=2)

    def export_summaries(self) -> str:
        """Export summaries as JSON."""
        return json.dumps(
            {k: v.to_dict() for k, v in self.summaries.items()},
            indent=2,
        )

    def get_cost_by_period(
        self, start_time: datetime, end_time: datetime
    ) -> Dict[str, float]:
        """
        Get costs by provider for a time period.

        Args:
            start_time: Start of period
            end_time: End of period

        Returns:
            Dict mapping provider -> total cost
        """
        result = {}
        for entry in self.entries:
            if start_time <= entry.timestamp <= end_time:
                result[entry.provider] = result.get(entry.provider, 0) + entry.total_cost
        return result
