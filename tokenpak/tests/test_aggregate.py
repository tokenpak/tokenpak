"""Unit tests for tokenpak.cli.aggregate module."""

import json
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from tokenpak.cli.aggregate import (
    AggregateRow,
    parse_since,
    load_requests,
    aggregate_records,
    format_tokens,
    fmt_cost,
    _parse_iso,
    _coerce_int,
    _coerce_float,
)


class TestParseSince:
    """Tests for parse_since function."""

    def test_parse_since_days(self):
        """Test parsing relative date with 'd' suffix."""
        result = parse_since("7d")
        assert result is not None
        # Should be approximately 7 days ago
        diff = datetime.now(timezone.utc) - result
        assert timedelta(days=6, hours=23) < diff < timedelta(days=7, hours=1)

    def test_parse_since_hours(self):
        """Test parsing relative date with 'h' suffix."""
        result = parse_since("12h")
        assert result is not None
        # Should be approximately 12 hours ago
        diff = datetime.now(timezone.utc) - result
        assert timedelta(hours=11, minutes=59) < diff < timedelta(hours=12, minutes=1)

    def test_parse_since_minutes(self):
        """Test parsing relative date with 'm' suffix."""
        result = parse_since("30m")
        assert result is not None
        # Should be approximately 30 minutes ago
        diff = datetime.now(timezone.utc) - result
        assert timedelta(minutes=29) < diff < timedelta(minutes=31)

    def test_parse_since_iso_datetime(self):
        """Test parsing ISO 8601 datetime string."""
        iso_str = "2026-01-01T12:00:00+00:00"
        result = parse_since(iso_str)
        assert result is not None
        assert result.year == 2026
        assert result.month == 1
        assert result.day == 1

    def test_parse_since_iso_with_z(self):
        """Test parsing ISO 8601 with Z suffix."""
        iso_str = "2026-01-01T12:00:00Z"
        result = parse_since(iso_str)
        assert result is not None
        assert result.tzinfo == timezone.utc

    def test_parse_since_none_input(self):
        """Test parse_since with None input."""
        result = parse_since(None)
        assert result is None

    def test_parse_since_empty_string(self):
        """Test parse_since with empty string."""
        result = parse_since("")
        assert result is None

    def test_parse_since_invalid_format(self):
        """Test parse_since with invalid format."""
        result = parse_since("invalid")
        assert result is None


class TestLoadRequests:
    """Tests for load_requests function."""

    def test_load_requests_empty_file(self):
        """Test loading from non-existent file."""
        result = load_requests(Path("/nonexistent/path/requests.jsonl"))
        assert result == []

    def test_load_requests_valid_jsonl(self):
        """Test loading valid JSONL records."""
        with TemporaryDirectory() as tmpdir:
            requests_file = Path(tmpdir) / "requests.jsonl"
            records = [
                {"agent": "cali", "model": "claude-sonnet", "input_tokens": 100, "output_tokens": 50, "cost": 0.001, "saved_cost": 0.0, "timestamp": "2026-03-27T00:00:00Z"},
                {"agent": "trix", "model": "claude-haiku", "input_tokens": 50, "output_tokens": 25, "cost": 0.0001, "saved_cost": 0.0001, "timestamp": "2026-03-27T01:00:00Z"},
            ]
            requests_file.write_text("\n".join(json.dumps(r) for r in records))
            
            result = load_requests(requests_file)
            assert len(result) == 2
            assert result[0]["agent"] == "cali"
            assert result[1]["agent"] == "trix"

    def test_load_requests_with_time_filter(self):
        """Test loading requests with time-based filtering."""
        with TemporaryDirectory() as tmpdir:
            requests_file = Path(tmpdir) / "requests.jsonl"
            cutoff = datetime(2026, 3, 27, 12, 0, 0, tzinfo=timezone.utc)
            records = [
                {"agent": "cali", "model": "claude-sonnet", "input_tokens": 100, "output_tokens": 50, "cost": 0.001, "saved_cost": 0.0, "timestamp": "2026-03-27T10:00:00Z"},  # before cutoff
                {"agent": "trix", "model": "claude-haiku", "input_tokens": 50, "output_tokens": 25, "cost": 0.0001, "saved_cost": 0.0001, "timestamp": "2026-03-27T14:00:00Z"},  # after cutoff
            ]
            requests_file.write_text("\n".join(json.dumps(r) for r in records))
            
            result = load_requests(requests_file, since=cutoff)
            assert len(result) == 1
            assert result[0]["agent"] == "trix"

    def test_load_requests_skip_empty_lines(self):
        """Test that empty lines are skipped."""
        with TemporaryDirectory() as tmpdir:
            requests_file = Path(tmpdir) / "requests.jsonl"
            content = '{"agent": "cali", "model": "claude", "input_tokens": 100, "output_tokens": 50, "cost": 0.001, "saved_cost": 0.0, "timestamp": "2026-03-27T00:00:00Z"}\n\n{"agent": "trix", "model": "claude", "input_tokens": 50, "output_tokens": 25, "cost": 0.0001, "saved_cost": 0.0001, "timestamp": "2026-03-27T01:00:00Z"}\n'
            requests_file.write_text(content)
            
            result = load_requests(requests_file)
            assert len(result) == 2

    def test_load_requests_skip_malformed_json(self):
        """Test that malformed JSON lines are skipped."""
        with TemporaryDirectory() as tmpdir:
            requests_file = Path(tmpdir) / "requests.jsonl"
            content = '{"agent": "cali", "model": "claude", "input_tokens": 100, "output_tokens": 50, "cost": 0.001, "saved_cost": 0.0, "timestamp": "2026-03-27T00:00:00Z"}\nmalformed json line\n{"agent": "trix", "model": "claude", "input_tokens": 50, "output_tokens": 25, "cost": 0.0001, "saved_cost": 0.0001, "timestamp": "2026-03-27T01:00:00Z"}\n'
            requests_file.write_text(content)
            
            result = load_requests(requests_file)
            assert len(result) == 2


class TestAggregateRecords:
    """Tests for aggregate_records function."""

    def test_aggregate_single_record(self):
        """Test aggregating a single request record."""
        records = [
            {"agent": "cali", "model": "claude-sonnet", "input_tokens": 100, "output_tokens": 50, "cost": 0.001, "saved_cost": 0.0}
        ]
        rows, totals = aggregate_records(records, "agent-3")
        
        assert len(rows) == 1
        assert rows[0].agent == "cali"
        assert rows[0].machine == "agent-3"
        assert rows[0].model == "claude-sonnet"
        assert rows[0].requests == 1
        assert rows[0].tokens == 150
        assert rows[0].cost == 0.001
        assert totals["requests"] == 1
        assert totals["tokens"] == 150

    def test_aggregate_multiple_records_same_agent_model(self):
        """Test aggregating multiple records for the same agent/model pair."""
        records = [
            {"agent": "cali", "model": "claude-sonnet", "input_tokens": 100, "output_tokens": 50, "cost": 0.001, "saved_cost": 0.0},
            {"agent": "cali", "model": "claude-sonnet", "input_tokens": 200, "output_tokens": 100, "cost": 0.002, "saved_cost": 0.0001},
        ]
        rows, totals = aggregate_records(records, "agent-3")
        
        assert len(rows) == 1
        assert rows[0].requests == 2
        assert rows[0].tokens == 450
        assert rows[0].cost == 0.003
        assert rows[0].saved == 0.0001

    def test_aggregate_multiple_agents_models(self):
        """Test aggregating records from multiple agents and models."""
        records = [
            {"agent": "cali", "model": "claude-sonnet", "input_tokens": 100, "output_tokens": 50, "cost": 0.001, "saved_cost": 0.0},
            {"agent": "cali", "model": "claude-haiku", "input_tokens": 50, "output_tokens": 25, "cost": 0.0001, "saved_cost": 0.00005},
            {"agent": "trix", "model": "claude-sonnet", "input_tokens": 200, "output_tokens": 100, "cost": 0.002, "saved_cost": 0.0},
        ]
        rows, totals = aggregate_records(records, "agent-3")
        
        assert len(rows) == 3
        assert totals["requests"] == 3
        assert totals["tokens"] == 525
        assert totals["cost"] == 0.0031

    def test_aggregate_missing_fields(self):
        """Test aggregating records with missing fields (should coerce to 0)."""
        records = [
            {"agent": "cali", "model": "claude-sonnet"},  # missing tokens and cost
        ]
        rows, totals = aggregate_records(records, "agent-3")
        
        assert len(rows) == 1
        assert rows[0].tokens == 0
        assert rows[0].cost == 0.0

    def test_aggregate_empty_records(self):
        """Test aggregating empty record list."""
        rows, totals = aggregate_records([], "agent-3")
        
        assert len(rows) == 0
        assert totals["requests"] == 0
        assert totals["tokens"] == 0
        assert totals["cost"] == 0.0

    def test_aggregate_sort_by_cost_descending(self):
        """Test that rows are sorted by cost (descending)."""
        records = [
            {"agent": "cali", "model": "cheap", "input_tokens": 10, "output_tokens": 5, "cost": 0.0001, "saved_cost": 0.0},
            {"agent": "trix", "model": "expensive", "input_tokens": 100, "output_tokens": 50, "cost": 0.005, "saved_cost": 0.0},
            {"agent": "sue", "model": "medium", "input_tokens": 50, "output_tokens": 25, "cost": 0.001, "saved_cost": 0.0},
        ]
        rows, totals = aggregate_records(records, "agent-3")
        
        assert len(rows) == 3
        assert rows[0].cost == 0.005
        assert rows[1].cost == 0.001
        assert rows[2].cost == 0.0001


class TestFormatTokens:
    """Tests for format_tokens function."""

    def test_format_tokens_millions(self):
        """Test formatting tokens in millions."""
        assert format_tokens(1_500_000) == "1.5M"
        assert format_tokens(2_000_000) == "2.0M"

    def test_format_tokens_thousands(self):
        """Test formatting tokens in thousands."""
        assert format_tokens(500_000) == "500K"
        assert format_tokens(1_500) == "2K"  # Implementation rounds to 0 decimals

    def test_format_tokens_hundreds(self):
        """Test formatting tokens as-is."""
        assert format_tokens(999) == "999"
        assert format_tokens(100) == "100"
        assert format_tokens(0) == "0"


class TestFmtCost:
    """Tests for fmt_cost function."""

    def test_fmt_cost_dollars(self):
        """Test formatting costs in dollars."""
        assert fmt_cost(1.5) == "$1.50"
        assert fmt_cost(10.0) == "$10.00"

    def test_fmt_cost_cents(self):
        """Test formatting costs in cents."""
        assert fmt_cost(0.15) == "$0.15"
        assert fmt_cost(0.01) == "$0.01"

    def test_fmt_cost_fractions(self):
        """Test formatting very small costs."""
        assert fmt_cost(0.001) == "$0.0010"
        assert fmt_cost(0.0001) == "$0.0001"


class TestCoerceFunctions:
    """Tests for coercion helper functions."""

    def test_coerce_int_valid(self):
        """Test coercing valid integers."""
        assert _coerce_int(42) == 42
        assert _coerce_int("100") == 100

    def test_coerce_int_invalid(self):
        """Test coercing invalid integers."""
        assert _coerce_int("abc") == 0
        assert _coerce_int(None) == 0

    def test_coerce_float_valid(self):
        """Test coercing valid floats."""
        assert _coerce_float(3.14) == 3.14
        assert _coerce_float("2.5") == 2.5

    def test_coerce_float_invalid(self):
        """Test coercing invalid floats."""
        assert _coerce_float("abc") == 0.0
        assert _coerce_float(None) == 0.0


class TestParseIso:
    """Tests for _parse_iso function."""

    def test_parse_iso_valid(self):
        """Test parsing valid ISO datetime."""
        result = _parse_iso("2026-03-27T12:00:00+00:00")
        assert result is not None
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 27

    def test_parse_iso_with_z(self):
        """Test parsing ISO with Z suffix."""
        result = _parse_iso("2026-03-27T12:00:00Z")
        assert result is not None
        assert result.tzinfo == timezone.utc

    def test_parse_iso_invalid(self):
        """Test parsing invalid ISO."""
        result = _parse_iso("not-a-date")
        assert result is None

    def test_parse_iso_empty(self):
        """Test parsing empty string."""
        result = _parse_iso("")
        assert result is None
