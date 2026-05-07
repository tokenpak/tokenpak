"""Tests for cost tracking."""


import pytest

pytest.importorskip("tokenpak.pro.routing.costs", reason="module not available in current build")
from datetime import datetime, timedelta

import pytest

from tokenpak.pro.routing.costs import (
    CostEntry,
    CostModel,
    CostTracker,
    ProviderCostSummary,
)


class TestCostEntry:
    """Test cost entry data structure."""

    def test_cost_entry_creation(self):
        """Test creating a cost entry."""
        now = datetime.utcnow()
        entry = CostEntry(
            provider="anthropic",
            timestamp=now,
            input_tokens=100,
            output_tokens=50,
            input_cost=0.01,
            output_cost=0.005,
            request_cost=0.001,
            model="claude-3",
            status="success",
        )

        assert entry.provider == "anthropic"
        assert entry.input_tokens == 100
        assert entry.output_tokens == 50
        assert entry.total_cost == 0.016  # 0.01 + 0.005 + 0.001

    def test_cost_entry_to_dict(self):
        """Test converting cost entry to dict."""
        now = datetime.utcnow()
        entry = CostEntry(
            provider="anthropic",
            timestamp=now,
            input_tokens=100,
            output_tokens=50,
            input_cost=0.01,
            output_cost=0.005,
            request_cost=0.001,
        )

        data = entry.to_dict()
        assert data["provider"] == "anthropic"
        assert data["input_tokens"] == 100
        assert isinstance(data["timestamp"], str)


class TestProviderCostSummary:
    """Test provider cost summary."""

    def test_summary_creation(self):
        """Test creating a summary."""
        summary = ProviderCostSummary(provider="anthropic")
        assert summary.provider == "anthropic"
        assert summary.total_cost == 0.0
        assert summary.request_count == 0

    def test_summary_to_dict(self):
        """Test converting summary to dict."""
        now = datetime.utcnow()
        summary = ProviderCostSummary(
            provider="anthropic",
            total_cost=10.0,
            request_count=100,
            token_count=50000,
            error_count=2,
            avg_cost_per_request=0.1,
            last_updated=now,
        )

        data = summary.to_dict()
        assert data["provider"] == "anthropic"
        assert data["total_cost"] == 10.0
        assert data["request_count"] == 100


class TestCostTracker:
    """Test cost tracking."""

    def setup_method(self):
        """Set up cost tracker."""
        self.tracker = CostTracker()

    def test_tracker_creation(self):
        """Test tracker initialization."""
        assert self.tracker is not None
        assert len(self.tracker.entries) == 0
        assert len(self.tracker.summaries) == 0

    def test_track_single_request(self):
        """Test tracking a single request."""
        entry = self.tracker.track_request(
            provider="anthropic",
            input_tokens=100,
            output_tokens=50,
            input_cost=0.01,
            output_cost=0.005,
            request_cost=0.001,
            model="claude-3",
        )

        assert entry.provider == "anthropic"
        assert entry.total_cost == 0.016
        assert len(self.tracker.entries) == 1

    def test_multiple_requests_tracked(self):
        """Test tracking multiple requests."""
        self.tracker.track_request(
            provider="anthropic",
            input_tokens=100,
            output_tokens=50,
            input_cost=0.01,
            output_cost=0.005,
        )
        self.tracker.track_request(
            provider="openai",
            input_tokens=200,
            output_tokens=100,
            input_cost=0.02,
            output_cost=0.01,
        )

        assert len(self.tracker.entries) == 2
        assert self.tracker.get_total_cost() == 0.045

    def test_summary_creation_on_track(self):
        """Test that summaries are created when tracking."""
        self.tracker.track_request(
            provider="anthropic",
            input_tokens=100,
            output_tokens=50,
            input_cost=0.01,
            output_cost=0.005,
            request_cost=0.001,
        )

        assert "anthropic" in self.tracker.summaries
        summary = self.tracker.summaries["anthropic"]
        assert summary.total_cost == 0.016
        assert summary.request_count == 1
        assert summary.token_count == 150

    def test_summary_accumulation(self):
        """Test that summaries accumulate correctly."""
        self.tracker.track_request(
            provider="anthropic",
            input_tokens=100,
            output_tokens=50,
            input_cost=0.01,
            output_cost=0.005,
        )
        self.tracker.track_request(
            provider="anthropic",
            input_tokens=50,
            output_tokens=25,
            input_cost=0.005,
            output_cost=0.0025,
        )

        summary = self.tracker.summaries["anthropic"]
        assert summary.total_cost == 0.0225  # 0.015 + 0.0075
        assert summary.request_count == 2
        assert summary.token_count == 225  # 150 + 75
        assert summary.avg_cost_per_request == 0.01125

    def test_error_status_tracking(self):
        """Test tracking errors."""
        self.tracker.track_request(
            provider="anthropic",
            model="claude-3",
            status="error",
        )

        summary = self.tracker.summaries["anthropic"]
        assert summary.error_count == 1

    def test_mixed_status_tracking(self):
        """Test tracking mixed success/error."""
        self.tracker.track_request(
            provider="anthropic",
            input_cost=0.01,
            status="success",
        )
        self.tracker.track_request(
            provider="anthropic",
            status="error",
        )
        self.tracker.track_request(
            provider="anthropic",
            status="timeout",
        )

        summary = self.tracker.summaries["anthropic"]
        assert summary.request_count == 3
        assert summary.error_count == 1  # Only explicit "error" status

    def test_get_provider_summary(self):
        """Test getting provider summary."""
        self.tracker.track_request(
            provider="anthropic",
            input_cost=0.01,
            output_cost=0.005,
        )

        summary = self.tracker.get_provider_summary("anthropic")
        assert summary is not None
        assert summary.total_cost == 0.015

    def test_get_provider_summary_missing(self):
        """Test getting missing provider summary."""
        summary = self.tracker.get_provider_summary("nonexistent")
        assert summary is None

    def test_get_all_summaries(self):
        """Test getting all summaries."""
        self.tracker.track_request(provider="anthropic", input_cost=0.01)
        self.tracker.track_request(provider="openai", input_cost=0.02)

        summaries = self.tracker.get_all_summaries()
        assert len(summaries) == 2
        assert "anthropic" in summaries
        assert "openai" in summaries

    def test_get_total_cost(self):
        """Test getting total cost."""
        self.tracker.track_request(provider="anthropic", input_cost=0.01)
        self.tracker.track_request(provider="openai", input_cost=0.02)
        self.tracker.track_request(provider="google", input_cost=0.015)

        total = self.tracker.get_total_cost()
        assert total == 0.045

    def test_get_entries_by_provider(self):
        """Test filtering entries by provider."""
        self.tracker.track_request(provider="anthropic", input_cost=0.01)
        self.tracker.track_request(provider="anthropic", input_cost=0.02)
        self.tracker.track_request(provider="openai", input_cost=0.03)

        entries = self.tracker.get_entries_by_provider("anthropic")
        assert len(entries) == 2
        assert all(e.provider == "anthropic" for e in entries)

    def test_get_entries_by_status(self):
        """Test filtering entries by status."""
        self.tracker.track_request(provider="anthropic", status="success")
        self.tracker.track_request(provider="anthropic", status="error")
        self.tracker.track_request(provider="openai", status="success")

        success_entries = self.tracker.get_entries_by_status("success")
        assert len(success_entries) == 2
        assert all(e.status == "success" for e in success_entries)

    def test_get_entries_by_model(self):
        """Test filtering entries by model."""
        self.tracker.track_request(
            provider="anthropic", model="claude-3", input_cost=0.01
        )
        self.tracker.track_request(
            provider="anthropic", model="claude-2", input_cost=0.02
        )

        entries = self.tracker.get_entries_by_model("claude-3")
        assert len(entries) == 1
        assert entries[0].model == "claude-3"

    def test_clear(self):
        """Test clearing tracker."""
        self.tracker.track_request(provider="anthropic", input_cost=0.01)
        self.tracker.track_request(provider="openai", input_cost=0.02)

        assert len(self.tracker.entries) == 2
        assert len(self.tracker.summaries) == 2

        self.tracker.clear()

        assert len(self.tracker.entries) == 0
        assert len(self.tracker.summaries) == 0
        assert self.tracker.get_total_cost() == 0.0

    def test_export_entries_json(self):
        """Test exporting entries as JSON."""
        self.tracker.track_request(
            provider="anthropic", input_cost=0.01, model="claude-3"
        )

        json_str = self.tracker.export_entries()
        assert "anthropic" in json_str
        assert "claude-3" in json_str
        assert "0.01" in json_str

    def test_export_summaries_json(self):
        """Test exporting summaries as JSON."""
        self.tracker.track_request(provider="anthropic", input_cost=0.01)

        json_str = self.tracker.export_summaries()
        assert "anthropic" in json_str

    def test_get_cost_by_period(self):
        """Test getting costs for a time period."""
        now = datetime.utcnow()
        past = now - timedelta(hours=2)
        future = now + timedelta(hours=2)

        self.tracker.track_request(provider="anthropic", input_cost=0.01)
        self.tracker.track_request(provider="openai", input_cost=0.02)

        costs = self.tracker.get_cost_by_period(past, future)
        assert "anthropic" in costs
        assert "openai" in costs
        assert costs["anthropic"] == 0.01
        assert costs["openai"] == 0.02

    def test_get_cost_by_period_outside_range(self):
        """Test getting costs outside time range."""
        now = datetime.utcnow()
        past_start = now - timedelta(hours=5)
        past_end = now - timedelta(hours=3)

        self.tracker.track_request(provider="anthropic", input_cost=0.01)

        costs = self.tracker.get_cost_by_period(past_start, past_end)
        assert len(costs) == 0

    def test_metadata_tracking(self):
        """Test tracking with metadata."""
        metadata = {"request_id": "123", "user": "test"}
        entry = self.tracker.track_request(
            provider="anthropic",
            input_cost=0.01,
            metadata=metadata,
        )

        assert entry.metadata["request_id"] == "123"
        assert entry.metadata["user"] == "test"

    def test_cost_model_registration(self):
        """Test registering cost models."""
        self.tracker.register_cost_model("anthropic", CostModel.TOKEN_BASED)
        self.tracker.register_cost_model("openai", CostModel.HYBRID)

        assert self.tracker.cost_models["anthropic"] == CostModel.TOKEN_BASED
        assert self.tracker.cost_models["openai"] == CostModel.HYBRID
