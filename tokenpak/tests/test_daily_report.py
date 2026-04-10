# SPDX-License-Identifier: Apache-2.0
"""Unit tests for daily_report.py module."""

import json
from datetime import datetime
from unittest import mock

import pytest

from tokenpak.daily_report import (
    DailySavingsData,
    ModelCompressionRow,
    _calculate_data,
    _format_compression_table_terminal,
    _format_json,
    _format_markdown,
    _format_terminal,
    _get_model_compression_breakdown,
    _get_savings_report,
    _proxy_get,
    generate_report,
)


class TestDailySavingsData:
    """Test DailySavingsData dataclass."""

    def test_dataclass_creation(self):
        """Test basic dataclass instantiation."""
        data = DailySavingsData(
            timestamp="2026-03-27T16:00:00",
            requests=150,
            savings_amount=12.50,
            savings_percent=15.0,
            cache_hit_rate=0.75,
            compression_percent=20.0,
            top_model="claude-sonnet-4-6",
            top_model_savings=10.00,
            uptime_hours=24,
            uptime_minutes=30,
            errors=2,
            estimated_monthly_rate=375.0,
        )
        assert data.requests == 150
        assert data.savings_amount == 12.50
        assert data.cache_hit_rate == 0.75

    def test_dataclass_serialization_to_dict(self):
        """Test dataclass can be converted to dict."""
        from dataclasses import asdict

        data = DailySavingsData(
            timestamp="2026-03-27T16:00:00",
            requests=100,
            savings_amount=5.00,
            savings_percent=10.0,
            cache_hit_rate=0.50,
            compression_percent=15.0,
            top_model="gpt-4",
            top_model_savings=3.00,
            uptime_hours=12,
            uptime_minutes=45,
            errors=1,
            estimated_monthly_rate=150.0,
        )
        d = asdict(data)
        assert isinstance(d, dict)
        assert d["requests"] == 100
        assert d["savings_amount"] == 5.00

    def test_dataclass_with_zero_values(self):
        """Test dataclass handles zero values correctly."""
        data = DailySavingsData(
            timestamp="2026-03-27T16:00:00",
            requests=0,
            savings_amount=0.0,
            savings_percent=0.0,
            cache_hit_rate=0.0,
            compression_percent=0.0,
            top_model="unknown",
            top_model_savings=0.0,
            uptime_hours=0,
            uptime_minutes=0,
            errors=0,
            estimated_monthly_rate=0.0,
        )
        assert data.requests == 0
        assert data.savings_percent == 0.0


class TestProxyGet:
    """Test _proxy_get function."""

    @mock.patch("urllib.request.urlopen")
    def test_proxy_get_success(self, mock_urlopen):
        """Test successful proxy request."""
        mock_response = mock.Mock()
        mock_response.read.return_value = json.dumps(
            {"status": "ok", "uptime": 3600}
        ).encode()
        mock_urlopen.return_value = mock_response

        result = _proxy_get("/health", port=8766)
        assert result["status"] == "ok"
        assert result["uptime"] == 3600

    @mock.patch("urllib.request.urlopen")
    def test_proxy_get_unreachable(self, mock_urlopen):
        """Test proxy request when unreachable."""
        mock_urlopen.side_effect = ConnectionRefusedError("Connection refused")

        result = _proxy_get("/health", port=8766)
        assert result is None

    @mock.patch("urllib.request.urlopen")
    def test_proxy_get_timeout(self, mock_urlopen):
        """Test proxy request timeout."""
        mock_urlopen.side_effect = TimeoutError("Request timed out")

        result = _proxy_get("/stats", port=8766)
        assert result is None

    @mock.patch("urllib.request.urlopen")
    def test_proxy_get_custom_port(self, mock_urlopen):
        """Test proxy request with custom port."""
        mock_response = mock.Mock()
        mock_response.read.return_value = json.dumps({}).encode()
        mock_urlopen.return_value = mock_response

        _proxy_get("/stats", port=9000)
        # Verify the correct URL was called
        mock_urlopen.assert_called_once()
        args = mock_urlopen.call_args[0]
        assert "9000" in args[0]


class TestGetSavingsReport:
    """Test _get_savings_report function."""

    @mock.patch("tokenpak.telemetry.query.get_savings_report")
    def test_savings_report_success(self, mock_get_saved_report):
        """Test successful savings report retrieval."""
        # Mock the telemetry query function
        mock_report = mock.Mock()
        mock_report.total_cost = 100.0
        mock_report.estimated_without_compression = 120.0
        mock_report.savings_amount = 20.0
        mock_report.savings_pct = 16.67
        mock_report.cache_hit_rate = 0.50
        mock_get_saved_report.return_value = mock_report

        result = _get_savings_report()
        assert result["savings_amount"] == 20.0
        assert result["savings_pct"] == 16.67

    @mock.patch("tokenpak.telemetry.query.get_savings_report")
    def test_savings_report_exception_returns_zeros(self, mock_fn):
        """Test exception handling returns zero values."""
        # When telemetry module is not available or errors, should return zeros
        mock_fn.side_effect = Exception("Telemetry unavailable")
        result = _get_savings_report()
        assert result["savings_amount"] == 0.0
        assert result["cache_hit_rate"] == 0.0


class TestCalculateData:
    """Test _calculate_data function."""

    @mock.patch("tokenpak.daily_report._get_savings_report")
    @mock.patch("tokenpak.daily_report._proxy_get")
    @mock.patch("time.time")
    def test_calculate_data_with_live_proxy(
        self, mock_time, mock_proxy_get, mock_savings
    ):
        """Test _calculate_data with mocked live proxy responses."""
        # Mock time (1 hour ago)
        mock_time.return_value = 1000000.0

        def proxy_side_effect(path, port=None):
            responses = {
                "/health": {
                    "stats": {
                        "start_time": 999996.0,  # 4 seconds ago
                    }
                },
                "/stats": {
                    "requests": 100,
                    "errors": 2,
                    "input_tokens": 10000,
                    "saved_tokens": 2000,
                },
                "/cache-stats": {
                    "cache_hits": 50,
                    "cache_misses": 50,
                },
            }
            return responses.get(path)

        mock_proxy_get.side_effect = proxy_side_effect
        mock_savings.return_value = {
            "total_cost": 100.0,
            "estimated_without_compression": 120.0,
            "savings_amount": 20.0,
            "savings_pct": 16.67,
            "cache_hit_rate": 0.50,
        }

        data = _calculate_data()
        assert data.requests == 100
        assert data.errors == 2
        assert data.cache_hit_rate == 0.5  # 50 / 100
        assert data.compression_percent == 20.0  # 2000 / 10000 * 100

    @mock.patch("tokenpak.daily_report._get_savings_report")
    @mock.patch("tokenpak.daily_report._proxy_get")
    def test_calculate_data_all_zeros(self, mock_proxy_get, mock_savings):
        """Test _calculate_data with all zero responses."""
        mock_proxy_get.return_value = None
        mock_savings.return_value = {
            "total_cost": 0.0,
            "estimated_without_compression": 0.0,
            "savings_amount": 0.0,
            "savings_pct": 0.0,
            "cache_hit_rate": 0.0,
        }

        data = _calculate_data()
        assert data.requests == 0
        assert data.savings_amount == 0.0
        assert data.cache_hit_rate == 0.0

    @mock.patch("tokenpak.daily_report._get_savings_report")
    @mock.patch("tokenpak.daily_report._proxy_get")
    def test_calculate_data_zero_input_tokens(self, mock_proxy_get, mock_savings):
        """Test compression percent calculation with zero input tokens."""
        def proxy_side_effect(path, port=None):
            responses = {
                "/health": {"stats": {"start_time": 0}},
                "/stats": {
                    "requests": 0,
                    "errors": 0,
                    "input_tokens": 0,
                    "saved_tokens": 0,
                },
                "/cache-stats": {"cache_hits": 0, "cache_misses": 0},
            }
            return responses.get(path)

        mock_proxy_get.side_effect = proxy_side_effect
        mock_savings.return_value = {
            "savings_amount": 0.0,
            "savings_pct": 0.0,
            "cache_hit_rate": 0.0,
        }

        data = _calculate_data()
        assert data.compression_percent == 0.0  # Should not divide by zero


class TestFormatTerminal:
    """Test _format_terminal function."""

    def test_format_terminal_basic(self):
        """Test terminal formatting."""
        data = DailySavingsData(
            timestamp="2026-03-27T16:00:00",
            requests=150,
            savings_amount=12.50,
            savings_percent=15.0,
            cache_hit_rate=0.75,
            compression_percent=20.0,
            top_model="claude-sonnet-4-6",
            top_model_savings=10.00,
            uptime_hours=24,
            uptime_minutes=30,
            errors=2,
            estimated_monthly_rate=375.0,
        )

        output = _format_terminal(data)
        assert "📊 TokenPak Daily Report" in output
        assert "150" in output  # requests
        assert "12.50" in output  # savings
        assert "75%" in output  # cache hit rate
        assert "24h" in output  # uptime

    def test_format_terminal_includes_all_fields(self):
        """Test that all metrics are included in terminal format."""
        data = DailySavingsData(
            timestamp="2026-03-27T16:00:00",
            requests=0,
            savings_amount=0.0,
            savings_percent=0.0,
            cache_hit_rate=0.0,
            compression_percent=0.0,
            top_model="unknown",
            top_model_savings=0.0,
            uptime_hours=0,
            uptime_minutes=0,
            errors=0,
            estimated_monthly_rate=0.0,
        )

        output = _format_terminal(data)
        assert "Requests" in output
        assert "Saved" in output
        assert "Cache Hit" in output
        assert "Compression" in output
        assert "Top Model" in output
        assert "Uptime" in output
        assert "Errors" in output
        assert "Monthly Rate" in output


class TestFormatMarkdown:
    """Test _format_markdown function."""

    def test_format_markdown_basic(self):
        """Test markdown formatting."""
        data = DailySavingsData(
            timestamp="2026-03-27T16:00:00",
            requests=150,
            savings_amount=12.50,
            savings_percent=15.0,
            cache_hit_rate=0.75,
            compression_percent=20.0,
            top_model="claude-sonnet-4-6",
            top_model_savings=10.00,
            uptime_hours=24,
            uptime_minutes=30,
            errors=2,
            estimated_monthly_rate=375.0,
        )

        output = _format_markdown(data)
        assert "## 📊 TokenPak Daily Report" in output
        assert "| Requests | 150 |" in output
        assert "| Savings | $12.50" in output
        assert "| Cache Hit Rate | 75% |" in output

    def test_format_markdown_has_table_structure(self):
        """Test markdown output has proper table structure."""
        data = DailySavingsData(
            timestamp="2026-03-27T16:00:00",
            requests=100,
            savings_amount=5.0,
            savings_percent=10.0,
            cache_hit_rate=0.5,
            compression_percent=15.0,
            top_model="gpt-4",
            top_model_savings=3.0,
            uptime_hours=12,
            uptime_minutes=45,
            errors=1,
            estimated_monthly_rate=150.0,
        )

        output = _format_markdown(data)
        assert "| Metric | Value |" in output
        assert "|" in output  # Table separators


class TestFormatJson:
    """Test _format_json function."""

    def test_format_json_basic(self):
        """Test JSON formatting."""
        data = DailySavingsData(
            timestamp="2026-03-27T16:00:00",
            requests=150,
            savings_amount=12.50,
            savings_percent=15.0,
            cache_hit_rate=0.75,
            compression_percent=20.0,
            top_model="claude-sonnet-4-6",
            top_model_savings=10.00,
            uptime_hours=24,
            uptime_minutes=30,
            errors=2,
            estimated_monthly_rate=375.0,
        )

        output = _format_json(data)
        assert isinstance(output, dict)
        assert output["requests"] == 150
        assert output["savings_amount"] == 12.50
        assert output["top_model"] == "claude-sonnet-4-6"

    def test_format_json_serializable(self):
        """Test JSON output is JSON-serializable."""
        data = DailySavingsData(
            timestamp="2026-03-27T16:00:00",
            requests=100,
            savings_amount=5.0,
            savings_percent=10.0,
            cache_hit_rate=0.5,
            compression_percent=15.0,
            top_model="gpt-4",
            top_model_savings=3.0,
            uptime_hours=12,
            uptime_minutes=45,
            errors=1,
            estimated_monthly_rate=150.0,
        )

        output = _format_json(data)
        # Should not raise
        json_str = json.dumps(output)
        assert isinstance(json_str, str)


class TestGenerateReport:
    """Test generate_report function."""

    @mock.patch("tokenpak.daily_report._calculate_data")
    def test_generate_report_terminal_format(self, mock_calc):
        """Test generate_report with terminal format."""
        mock_calc.return_value = DailySavingsData(
            timestamp="2026-03-27T16:00:00",
            requests=150,
            savings_amount=12.50,
            savings_percent=15.0,
            cache_hit_rate=0.75,
            compression_percent=20.0,
            top_model="claude-sonnet-4-6",
            top_model_savings=10.00,
            uptime_hours=24,
            uptime_minutes=30,
            errors=2,
            estimated_monthly_rate=375.0,
        )

        output = generate_report(format="terminal")
        assert isinstance(output, str)
        assert "📊 TokenPak Daily Report" in output

    @mock.patch("tokenpak.daily_report._calculate_data")
    def test_generate_report_markdown_format(self, mock_calc):
        """Test generate_report with markdown format."""
        mock_calc.return_value = DailySavingsData(
            timestamp="2026-03-27T16:00:00",
            requests=100,
            savings_amount=5.0,
            savings_percent=10.0,
            cache_hit_rate=0.5,
            compression_percent=15.0,
            top_model="gpt-4",
            top_model_savings=3.0,
            uptime_hours=12,
            uptime_minutes=45,
            errors=1,
            estimated_monthly_rate=150.0,
        )

        output = generate_report(format="markdown")
        assert isinstance(output, str)
        assert "## 📊" in output

    @mock.patch("tokenpak.daily_report._calculate_data")
    def test_generate_report_json_format(self, mock_calc):
        """Test generate_report with JSON format."""
        mock_calc.return_value = DailySavingsData(
            timestamp="2026-03-27T16:00:00",
            requests=150,
            savings_amount=12.50,
            savings_percent=15.0,
            cache_hit_rate=0.75,
            compression_percent=20.0,
            top_model="claude-sonnet-4-6",
            top_model_savings=10.00,
            uptime_hours=24,
            uptime_minutes=30,
            errors=2,
            estimated_monthly_rate=375.0,
        )

        output = generate_report(format="json")
        assert isinstance(output, dict)
        assert output["requests"] == 150

    @mock.patch("tokenpak.daily_report._calculate_data")
    def test_generate_report_default_format(self, mock_calc):
        """Test generate_report defaults to terminal format."""
        mock_calc.return_value = DailySavingsData(
            timestamp="2026-03-27T16:00:00",
            requests=50,
            savings_amount=2.50,
            savings_percent=5.0,
            cache_hit_rate=0.25,
            compression_percent=10.0,
            top_model="claude-haiku-4-5",
            top_model_savings=1.00,
            uptime_hours=6,
            uptime_minutes=15,
            errors=0,
            estimated_monthly_rate=75.0,
        )

        output = generate_report()  # No format specified
        assert isinstance(output, str)
        assert "📊" in output


# ---------------------------------------------------------------------------
# New tests: per-model compression breakdown (TPK-RPT-COMPRESSION-MODEL)
# ---------------------------------------------------------------------------

_SAMPLE_ROWS = [
    ModelCompressionRow(
        model="claude-sonnet-4-6",
        request_count=80,
        avg_compression_ratio=0.72,
        tokens_saved=5600,
        savings_amount=0.0112,
    ),
    ModelCompressionRow(
        model="claude-haiku-4-5",
        request_count=120,
        avg_compression_ratio=0.85,
        tokens_saved=1800,
        savings_amount=0.0023,
    ),
    ModelCompressionRow(
        model="claude-opus-4-6",
        request_count=10,
        avg_compression_ratio=1.0,   # no compression data
        tokens_saved=0,
        savings_amount=0.0,
    ),
]


class TestModelCompressionRow:
    """Test ModelCompressionRow dataclass."""

    def test_basic_creation(self):
        r = ModelCompressionRow(
            model="claude-sonnet-4-6",
            request_count=100,
            avg_compression_ratio=0.75,
            tokens_saved=2500,
            savings_amount=0.005,
        )
        assert r.model == "claude-sonnet-4-6"
        assert r.request_count == 100
        assert r.avg_compression_ratio == 0.75
        assert r.tokens_saved == 2500
        assert r.savings_amount == 0.005

    def test_zero_values(self):
        r = ModelCompressionRow(
            model="unknown",
            request_count=0,
            avg_compression_ratio=1.0,
            tokens_saved=0,
            savings_amount=0.0,
        )
        assert r.tokens_saved == 0
        assert r.savings_amount == 0.0

    def test_ratio_less_than_one_means_compressed(self):
        """Ratio < 1.0 means the model's input was compressed."""
        r = ModelCompressionRow(
            model="claude-haiku-4-5",
            request_count=50,
            avg_compression_ratio=0.6,
            tokens_saved=4000,
            savings_amount=0.002,
        )
        assert r.avg_compression_ratio < 1.0


class TestGetModelCompressionBreakdown:
    """Test _get_model_compression_breakdown()."""

    @mock.patch("tokenpak.telemetry.query.get_model_compression_breakdown")
    def test_returns_populated_list(self, mock_fn):
        """Test happy path: telemetry returns data, mapped to ModelCompressionRow."""
        from tokenpak.telemetry.query_models import ModelCompressionBreakdown

        mock_fn.return_value = [
            ModelCompressionBreakdown(
                model="claude-sonnet-4-6",
                request_count=80,
                avg_compression_ratio=0.72,
                tokens_saved=5600,
                avg_raw_tokens=20000.0,
                avg_final_tokens=14400.0,
                savings_amount=0.0112,
            ),
        ]
        result = _get_model_compression_breakdown()
        assert len(result) == 1
        assert isinstance(result[0], ModelCompressionRow)
        assert result[0].model == "claude-sonnet-4-6"
        assert result[0].tokens_saved == 5600

    @mock.patch("tokenpak.telemetry.query.get_model_compression_breakdown")
    def test_returns_empty_list_on_exception(self, mock_fn):
        """Test that any exception yields an empty list (graceful degradation)."""
        mock_fn.side_effect = Exception("DB unavailable")
        result = _get_model_compression_breakdown()
        assert result == []

    @mock.patch("tokenpak.telemetry.query.get_model_compression_breakdown")
    def test_returns_empty_list_when_no_data(self, mock_fn):
        """Test empty DB → empty list."""
        mock_fn.return_value = []
        result = _get_model_compression_breakdown()
        assert result == []


class TestFormatCompressionTableTerminal:
    """Test _format_compression_table_terminal()."""

    def test_empty_rows_returns_no_data_message(self):
        lines = _format_compression_table_terminal([])
        assert len(lines) == 1
        assert "no per-model" in lines[0].lower()

    def test_populated_rows_contain_model_name(self):
        lines = _format_compression_table_terminal(_SAMPLE_ROWS)
        combined = "\n".join(lines)
        assert "claude-sonnet-4-6" in combined
        assert "claude-haiku-4-5" in combined

    def test_populated_rows_contain_header(self):
        lines = _format_compression_table_terminal(_SAMPLE_ROWS)
        combined = "\n".join(lines)
        assert "Model" in combined
        assert "Ratio" in combined or "Reqs" in combined

    def test_no_compression_shows_zero_pct(self):
        """Rows with ratio=1.0 should show 0.0% compression."""
        rows = [
            ModelCompressionRow(
                model="opus-no-compress",
                request_count=5,
                avg_compression_ratio=1.0,
                tokens_saved=0,
                savings_amount=0.0,
            )
        ]
        lines = _format_compression_table_terminal(rows)
        combined = "\n".join(lines)
        assert "0.0%" in combined


class TestDailySavingsDataWithCompression:
    """Test DailySavingsData with model_compression field."""

    def test_default_model_compression_is_empty_list(self):
        data = DailySavingsData(
            timestamp="2026-03-28T15:00:00",
            requests=100,
            savings_amount=5.0,
            savings_percent=10.0,
            cache_hit_rate=0.5,
            compression_percent=15.0,
            top_model="claude-sonnet-4-6",
            top_model_savings=3.0,
            uptime_hours=12,
            uptime_minutes=30,
            errors=0,
            estimated_monthly_rate=150.0,
        )
        assert data.model_compression == []

    def test_model_compression_set_explicitly(self):
        data = DailySavingsData(
            timestamp="2026-03-28T15:00:00",
            requests=100,
            savings_amount=5.0,
            savings_percent=10.0,
            cache_hit_rate=0.5,
            compression_percent=15.0,
            top_model="claude-sonnet-4-6",
            top_model_savings=3.0,
            uptime_hours=12,
            uptime_minutes=30,
            errors=0,
            estimated_monthly_rate=150.0,
            model_compression=_SAMPLE_ROWS,
        )
        assert len(data.model_compression) == 3
        assert data.model_compression[0].model == "claude-sonnet-4-6"


class TestFormatTerminalWithCompression:
    """Test _format_terminal includes per-model compression breakdown."""

    def _make_data(self, rows):
        return DailySavingsData(
            timestamp="2026-03-28T15:00:00",
            requests=200,
            savings_amount=10.0,
            savings_percent=12.0,
            cache_hit_rate=0.6,
            compression_percent=18.0,
            top_model="claude-sonnet-4-6",
            top_model_savings=7.0,
            uptime_hours=20,
            uptime_minutes=5,
            errors=1,
            estimated_monthly_rate=300.0,
            model_compression=rows,
        )

    def test_terminal_includes_breakdown_section(self):
        data = self._make_data(_SAMPLE_ROWS)
        output = _format_terminal(data)
        assert "Per-Model Compression" in output
        assert "claude-sonnet-4-6" in output

    def test_terminal_no_data_shows_placeholder(self):
        data = self._make_data([])
        output = _format_terminal(data)
        assert "no per-model" in output.lower()


class TestFormatMarkdownWithCompression:
    """Test _format_markdown includes per-model compression breakdown."""

    def _make_data(self, rows):
        return DailySavingsData(
            timestamp="2026-03-28T15:00:00",
            requests=200,
            savings_amount=10.0,
            savings_percent=12.0,
            cache_hit_rate=0.6,
            compression_percent=18.0,
            top_model="claude-sonnet-4-6",
            top_model_savings=7.0,
            uptime_hours=20,
            uptime_minutes=5,
            errors=1,
            estimated_monthly_rate=300.0,
            model_compression=rows,
        )

    def test_markdown_includes_breakdown_heading(self):
        data = self._make_data(_SAMPLE_ROWS)
        output = _format_markdown(data)
        assert "Per-Model Compression Breakdown" in output

    def test_markdown_breakdown_has_table_structure(self):
        data = self._make_data(_SAMPLE_ROWS)
        output = _format_markdown(data)
        assert "| Model |" in output
        assert "claude-sonnet-4-6" in output
        assert "claude-haiku-4-5" in output

    def test_markdown_no_data_shows_placeholder(self):
        data = self._make_data([])
        output = _format_markdown(data)
        assert "No per-model compression data" in output

    def test_markdown_tokens_saved_and_ratio_present(self):
        data = self._make_data(_SAMPLE_ROWS)
        output = _format_markdown(data)
        # Tokens saved (5600) and ratio for sonnet (28.0%)
        assert "5,600" in output or "5600" in output
        assert "28.0%" in output  # (1 - 0.72) * 100


class TestFormatJsonWithCompression:
    """Test _format_json includes model_compression field."""

    def test_json_output_includes_model_compression(self):
        data = DailySavingsData(
            timestamp="2026-03-28T15:00:00",
            requests=100,
            savings_amount=5.0,
            savings_percent=10.0,
            cache_hit_rate=0.5,
            compression_percent=15.0,
            top_model="claude-sonnet-4-6",
            top_model_savings=3.0,
            uptime_hours=12,
            uptime_minutes=30,
            errors=0,
            estimated_monthly_rate=150.0,
            model_compression=_SAMPLE_ROWS,
        )
        output = _format_json(data)
        assert "model_compression" in output
        assert isinstance(output["model_compression"], list)
        assert len(output["model_compression"]) == 3
        assert output["model_compression"][0]["model"] == "claude-sonnet-4-6"

    def test_json_model_compression_empty_list(self):
        data = DailySavingsData(
            timestamp="2026-03-28T15:00:00",
            requests=0,
            savings_amount=0.0,
            savings_percent=0.0,
            cache_hit_rate=0.0,
            compression_percent=0.0,
            top_model="unknown",
            top_model_savings=0.0,
            uptime_hours=0,
            uptime_minutes=0,
            errors=0,
            estimated_monthly_rate=0.0,
        )
        output = _format_json(data)
        assert "model_compression" in output
        assert output["model_compression"] == []
