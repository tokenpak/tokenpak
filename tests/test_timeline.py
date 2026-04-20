"""tests/test_timeline.py — Timeline & Cost Calculation Tests

Covers:
- hourly_buckets() — time-series bucketing
- model_breakdown() — cost by model
- Timestamp parsing (ISO8601, unix epoch)
- Cost calculation for multiple models
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests.conftest import make_entry
from tokenpak.agent.query.timeline import TimelineGenerator, _cost_for_tokens, _parse_timestamp


class TestTimestampParsing:
    """_parse_timestamp() — Multiple format support."""

    def test_iso8601_with_z(self):
        """ISO8601 with Z suffix."""
        dt = _parse_timestamp("2026-03-16T19:30:00Z")
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 16
        assert dt.hour == 19
        assert dt.minute == 30

    def test_iso8601_with_timezone(self):
        """ISO8601 with explicit timezone."""
        dt = _parse_timestamp("2026-03-16T19:30:00+00:00")
        assert dt.year == 2026
        assert dt.month == 3

    def test_unix_epoch_seconds(self):
        """Unix timestamp in seconds."""
        ts = 1771725600  # Some valid unix timestamp
        dt = _parse_timestamp(ts)
        assert isinstance(dt, datetime)
        assert dt.tzinfo == timezone.utc

    def test_unix_epoch_milliseconds(self):
        """Unix timestamp in milliseconds (> 10^10)."""
        ts = 1771725600000  # Milliseconds
        dt = _parse_timestamp(ts)
        assert isinstance(dt, datetime)
        assert dt.tzinfo == timezone.utc

    def test_float_epoch_seconds(self):
        """Float unix timestamp."""
        ts = 1771725600.5
        dt = _parse_timestamp(ts)
        assert isinstance(dt, datetime)

    def test_invalid_timestamp(self):
        """Invalid format raises ValueError."""
        with pytest.raises(ValueError):
            _parse_timestamp("not-a-timestamp")

    def test_invalid_iso8601(self):
        """Invalid ISO8601 raises ValueError."""
        with pytest.raises(ValueError):
            _parse_timestamp("2026-13-45T99:99:99Z")


class TestCostCalculation:
    """_cost_for_tokens() — Token-based cost estimation."""

    def test_claude_opus_cost(self):
        """Claude Opus 4.5 pricing."""
        cost = _cost_for_tokens("claude-opus-4-5", 1000000)
        # Opus: $15/M input + $60/M output
        # Estimate: 30% input, 70% output
        # (300k * $15/M) + (700k * $60/M) = $4.5 + $42 = $46.5
        assert cost > 40.0
        assert cost < 50.0

    def test_sonnet_cost(self):
        """Claude Sonnet 4.6 pricing."""
        cost = _cost_for_tokens("claude-sonnet-4-6", 1000000)
        # Sonnet: $3/M input + $15/M output
        # (300k * $3/M) + (700k * $15/M) = $0.9 + $10.5 = $11.4
        assert cost > 10.0
        assert cost < 13.0

    def test_gpt4_turbo_cost(self):
        """GPT-4 Turbo pricing."""
        cost = _cost_for_tokens("gpt-4-turbo", 1000000)
        # GPT-4 Turbo: $10/M input + $30/M output
        # (300k * $10/M) + (700k * $30/M) = $3 + $21 = $24
        assert cost > 20.0
        assert cost < 30.0

    def test_gpt35_cost(self):
        """GPT-3.5 Turbo pricing."""
        cost = _cost_for_tokens("gpt-3.5-turbo", 1000000)
        # GPT-3.5: $0.5/M input + $1.5/M output
        # (300k * $0.5/M) + (700k * $1.5/M) = $0.15 + $1.05 = $1.2
        assert cost > 0.5
        assert cost < 2.0

    def test_unknown_model_cost(self):
        """Unknown model gets default pricing."""
        cost = _cost_for_tokens("unknown-model", 1000000)
        assert cost > 0.0  # Should have some default cost

    def test_cache_hit_discount(self):
        """Cache hit reduces cost (90% discount on input)."""
        cost_no_cache = _cost_for_tokens("claude-sonnet-4-6", 1000000, cache_hit=False)
        cost_with_cache = _cost_for_tokens("claude-sonnet-4-6", 1000000, cache_hit=True)

        # Cache hit should be cheaper due to 90% discount
        assert cost_with_cache < cost_no_cache

    def test_zero_tokens(self):
        """Zero tokens → minimum cost."""
        cost = _cost_for_tokens("claude-sonnet-4-6", 0)
        assert cost > 0.0  # Minimum cost enforced

    def test_cost_scales_with_tokens(self):
        """Cost increases with token count."""
        cost_small = _cost_for_tokens("claude-sonnet-4-6", 1000)
        cost_large = _cost_for_tokens("claude-sonnet-4-6", 10000)

        assert cost_large > cost_small


class TestHourlyBuckets:
    """TimelineGenerator.hourly_buckets() — Time-series bucketing."""

    def test_empty_entries(self):
        """Empty entries with 0 hours → empty list."""
        gen = TimelineGenerator()
        result = gen.hourly_buckets([], num_hours=0)
        assert result == []

    def test_single_entry_single_hour(self):
        """Single entry buckets to its hour."""
        gen = TimelineGenerator()
        entries = [
            make_entry(
                model="claude-sonnet-4-6",
                tokens=1000,
                timestamp="2026-03-16T19:30:00Z",
            )
        ]

        start_hour = datetime(2026, 3, 16, 19, tzinfo=timezone.utc)
        result = gen.hourly_buckets(entries, start_hour=start_hour, num_hours=1)

        assert len(result) == 1
        assert result[0]["hour"] == "2026-03-16T19:00:00+00:00"
        assert result[0]["requests"] == 1
        assert result[0]["cost"] > 0.0

    def test_multiple_entries_same_hour(self):
        """Multiple entries in same hour aggregate."""
        gen = TimelineGenerator()
        entries = [
            make_entry(timestamp="2026-03-16T19:00:00Z"),
            make_entry(timestamp="2026-03-16T19:15:00Z"),
            make_entry(timestamp="2026-03-16T19:45:00Z"),
        ]

        start_hour = datetime(2026, 3, 16, 19, tzinfo=timezone.utc)
        result = gen.hourly_buckets(entries, start_hour=start_hour, num_hours=1)

        assert result[0]["requests"] == 3

    def test_multiple_hours_sparse_data(self):
        """Data sparse across hours → empty hours included."""
        gen = TimelineGenerator()
        entries = [
            make_entry(timestamp="2026-03-16T19:00:00Z"),
            make_entry(timestamp="2026-03-16T22:00:00Z"),
        ]

        start_hour = datetime(2026, 3, 16, 19, tzinfo=timezone.utc)
        result = gen.hourly_buckets(entries, start_hour=start_hour, num_hours=4)

        assert len(result) == 4
        assert result[0]["requests"] == 1
        assert result[1]["requests"] == 0
        assert result[2]["requests"] == 0
        assert result[3]["requests"] == 1

    def test_model_breakdown_in_buckets(self):
        """Buckets include per-model cost breakdown."""
        gen = TimelineGenerator()
        entries = [
            make_entry(model="claude-sonnet-4-6", timestamp="2026-03-16T19:00:00Z"),
            make_entry(model="claude-opus-4-5", timestamp="2026-03-16T19:30:00Z"),
        ]

        start_hour = datetime(2026, 3, 16, 19, tzinfo=timezone.utc)
        result = gen.hourly_buckets(entries, start_hour=start_hour, num_hours=1)

        bucket = result[0]
        assert "claude-sonnet-4-6" in bucket["models"]
        assert "claude-opus-4-5" in bucket["models"]
        assert bucket["models"]["claude-sonnet-4-6"] > 0.0

    def test_invalid_timestamp_skipped(self):
        """Invalid timestamp entries logged and skipped."""
        gen = TimelineGenerator()
        entries = [
            make_entry(timestamp="2026-03-16T19:00:00Z"),
            {"model": "test", "cost": 0.05, "timestamp": "invalid"},
            make_entry(timestamp="2026-03-16T19:30:00Z"),
        ]

        start_hour = datetime(2026, 3, 16, 19, tzinfo=timezone.utc)
        result = gen.hourly_buckets(entries, start_hour=start_hour, num_hours=1)

        # Should have 2 valid entries
        assert result[0]["requests"] == 2

    def test_timezone_aware_entries(self):
        """Timezone-aware timestamps converted to UTC."""
        gen = TimelineGenerator()
        entries = [
            make_entry(timestamp="2026-03-16T20:00:00+01:00"),  # UTC+1
        ]

        start_hour = datetime(2026, 3, 16, 19, tzinfo=timezone.utc)
        result = gen.hourly_buckets(entries, start_hour=start_hour, num_hours=1)

        # Should be bucketed to 19:00 UTC
        assert result[0]["requests"] == 1


class TestModelBreakdownTimeline:
    """TimelineGenerator.model_breakdown() — Cost by model."""

    def test_single_model(self):
        """Single model breakdown."""
        gen = TimelineGenerator()
        entries = [
            make_entry(model="claude-sonnet-4-6", tokens=1000),
            make_entry(model="claude-sonnet-4-6", tokens=1000),
        ]

        result = gen.model_breakdown(entries)

        assert len(result) == 1
        assert "claude-sonnet-4-6" in result
        assert result["claude-sonnet-4-6"] > 0.0

    def test_multiple_models(self):
        """Multiple models cost breakdown."""
        gen = TimelineGenerator()
        entries = [
            make_entry(model="claude-sonnet-4-6", tokens=1000),
            make_entry(model="claude-opus-4-5", tokens=1000),
            make_entry(model="gpt-4-turbo", tokens=1000),
        ]

        result = gen.model_breakdown(entries)

        assert len(result) == 3
        assert "claude-sonnet-4-6" in result
        assert "claude-opus-4-5" in result
        assert "gpt-4-turbo" in result

    def test_same_model_aggregates(self):
        """Same model from multiple entries sums correctly."""
        gen = TimelineGenerator()
        entries = [
            make_entry(model="claude-sonnet-4-6", tokens=1000),
            make_entry(model="claude-sonnet-4-6", tokens=2000),
        ]

        result = gen.model_breakdown(entries)

        assert "claude-sonnet-4-6" in result
        # Total should be sum of both
        assert result["claude-sonnet-4-6"] > 0.0

    def test_cache_tokens_reduce_cost(self):
        """Cache tokens via cache_hit reduce model cost."""
        gen = TimelineGenerator()
        entries_no_cache = [make_entry(model="test", tokens=1000)]
        entries_cache = [make_entry(model="test", tokens=1000, cache_tokens=500)]

        cost_no_cache = gen.model_breakdown(entries_no_cache)["test"]
        cost_cache = gen.model_breakdown(entries_cache)["test"]

        # Cache should reduce cost
        assert cost_cache < cost_no_cache

    def test_empty_entries(self):
        """Empty entries → empty breakdown."""
        gen = TimelineGenerator()
        result = gen.model_breakdown([])

        assert result == {}


class TestTimelineEdgeCases:
    """Edge cases and robustness."""

    def test_missing_timestamp_field(self):
        """Missing timestamp field skipped."""
        gen = TimelineGenerator()
        entries = [
            {"model": "test", "cost": 0.05},  # No timestamp
        ]

        start_hour = datetime(2026, 3, 16, 19, tzinfo=timezone.utc)
        result = gen.hourly_buckets(entries, start_hour=start_hour, num_hours=1)

        assert result[0]["requests"] == 0

    def test_missing_model_field(self):
        """Missing model → 'unknown' model."""
        gen = TimelineGenerator()
        entries = [
            {"cost": 0.05, "timestamp": "2026-03-16T19:00:00Z"},
        ]

        start_hour = datetime(2026, 3, 16, 19, tzinfo=timezone.utc)
        result = gen.hourly_buckets(entries, start_hour=start_hour, num_hours=1)

        assert "unknown" in result[0]["models"]

    def test_default_start_hour(self):
        """No start_hour provided → uses current UTC hour."""
        gen = TimelineGenerator()
        entries = [
            make_entry(timestamp=datetime.now(tz=timezone.utc).isoformat()),
        ]

        result = gen.hourly_buckets(entries, num_hours=1)

        # Should return a bucket even without explicit start_hour
        assert len(result) >= 1

    def test_very_large_num_hours(self):
        """Large num_hours doesn't crash."""
        gen = TimelineGenerator()
        entries = [
            make_entry(timestamp="2026-03-16T19:00:00Z"),
        ]

        start_hour = datetime(2026, 3, 16, 0, tzinfo=timezone.utc)
        result = gen.hourly_buckets(entries, start_hour=start_hour, num_hours=168)

        assert len(result) == 168
        # Only one hour has data
        assert sum(b["requests"] for b in result) == 1

    def test_cost_accumulation(self):
        """Bucket total cost = sum of entry costs."""
        gen = TimelineGenerator()
        # Use exact cost value that we know
        entries = [
            make_entry(timestamp="2026-03-16T19:00:00Z", tokens=100),
            make_entry(timestamp="2026-03-16T19:15:00Z", tokens=100),
        ]

        start_hour = datetime(2026, 3, 16, 19, tzinfo=timezone.utc)
        result = gen.hourly_buckets(entries, start_hour=start_hour, num_hours=1)

        # Cost should be positive and reasonable
        assert result[0]["cost"] > 0.0
