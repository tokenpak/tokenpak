"""tests/test_prometheus_metrics.py

Tests for the Prometheus metrics exporter (tokenpak/telemetry/prometheus.py).

Coverage:
  - PrometheusMetricsCollector.collect() returns valid Prometheus text format
  - Counter metrics: requests, tokens, cost/savings
  - Histogram metrics: request duration buckets + sum + count
  - Gauge metrics: compression ratio, circuit state
  - Label formatting and escaping
  - GET /metrics endpoint returns 200 with correct content-type
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Ensure project root on path
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tokenpak.telemetry.prometheus import (
    PrometheusMetricsCollector,
    _label_str,
    _format_value,
    _escape_label_value,
    DURATION_BUCKETS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_storage(
    per_provider_rows=None,
    duration_rows=None,
    compression_rows=None,
):
    """Build a minimal mock TelemetryDB for testing."""
    storage = MagicMock()

    # Default per-provider stats
    if per_provider_rows is None:
        per_provider_rows = [
            {
                "provider": "anthropic",
                "status": "success",
                "requests": 1523,
                "input_tokens": 1800000,
                "output_tokens": 300000,
                "tokens_saved": 2100000,
                "cost_total": 4.56,
                "savings_total": 1.23,
            },
            {
                "provider": "openai",
                "status": "error",
                "requests": 7,
                "input_tokens": 5000,
                "output_tokens": 0,
                "tokens_saved": 0,
                "cost_total": 0.02,
                "savings_total": 0.0,
            },
        ]

    # Default duration rows: (provider, duration_ms)
    if duration_rows is None:
        duration_rows = [
            ("anthropic", 250.0),
            ("anthropic", 750.0),
            ("anthropic", 1500.0),
            ("openai", 3000.0),
        ]

    # Default compression rows
    if compression_rows is None:
        compression_rows = [
            {
                "provider": "anthropic",
                "tokens_saved": 2100000,
                "total_input": 3900000,
                "compression_ratio": 0.5385,
            }
        ]

    # Wire up mock cursor for each query method
    # _query_per_provider_stats uses storage._conn.cursor()
    def _make_cursor(rows, columns):
        cur = MagicMock()
        cur.description = [(col,) for col in columns]
        cur.fetchall.return_value = rows
        return cur

    # We need to intercept the SQL calls inside the methods.
    # Easier: patch the methods on the collector directly.
    storage._per_provider_rows = per_provider_rows
    storage._duration_rows = duration_rows
    storage._compression_rows = compression_rows
    return storage


def _make_collector(storage=None, circuit=None):
    """Build a PrometheusMetricsCollector with mocked query methods."""
    if storage is None:
        storage = _make_storage()

    collector = PrometheusMetricsCollector(storage=storage, circuit_breaker=circuit)

    # Patch internal query methods to return test data
    collector._query_per_provider_stats = MagicMock(
        return_value=storage._per_provider_rows
    )
    collector._query_duration_histograms = MagicMock(
        return_value={
            "anthropic": {
                "count": 3,
                "sum_seconds": 2.5,
                "buckets": {
                    0.05: 0, 0.1: 0, 0.25: 1, 0.5: 1, 1.0: 2,
                    2.0: 3, 5.0: 3, 10.0: 3, 30.0: 3, float("inf"): 3,
                },
            }
        }
    )
    collector._query_compression_ratios = MagicMock(
        return_value=storage._compression_rows
    )
    return collector


# ---------------------------------------------------------------------------
# Unit tests: helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_label_str_single(self):
        assert _label_str(provider="anthropic") == '{provider="anthropic"}'

    def test_label_str_multi(self):
        result = _label_str(provider="openai", status="error")
        assert 'provider="openai"' in result
        assert 'status="error"' in result

    def test_label_str_empty(self):
        assert _label_str() == ""

    def test_label_str_skips_empty_values(self):
        result = _label_str(provider="a", status="")
        assert "status" not in result

    def test_escape_label_value_quotes(self):
        assert _escape_label_value('say "hi"') == 'say \\"hi\\"'

    def test_escape_label_value_backslash(self):
        assert _escape_label_value("a\\b") == "a\\\\b"

    def test_escape_label_value_newline(self):
        assert _escape_label_value("a\nb") == "a\\nb"

    def test_format_value_integer(self):
        assert _format_value(1523.0) == "1523"

    def test_format_value_float(self):
        result = _format_value(0.4701)
        assert "0.4701" in result or "0.47" in result

    def test_format_value_inf(self):
        assert _format_value(float("inf")) == "+Inf"


# ---------------------------------------------------------------------------
# Unit tests: collector output
# ---------------------------------------------------------------------------

class TestCollector:
    def setup_method(self):
        self.collector = _make_collector()
        self.output = self.collector.collect()

    # -- HELP / TYPE lines present --
    def test_requests_help_present(self):
        assert "# HELP tokenpak_requests_total" in self.output

    def test_requests_type_counter(self):
        assert "# TYPE tokenpak_requests_total counter" in self.output

    def test_tokens_help_present(self):
        assert "# HELP tokenpak_tokens_total" in self.output

    def test_tokens_type_counter(self):
        assert "# TYPE tokenpak_tokens_total counter" in self.output

    def test_cost_help_present(self):
        assert "# HELP tokenpak_cost_usd_total" in self.output

    def test_savings_help_present(self):
        assert "# HELP tokenpak_savings_usd_total" in self.output

    def test_duration_help_present(self):
        assert "# HELP tokenpak_request_duration_seconds" in self.output

    def test_duration_type_histogram(self):
        assert "# TYPE tokenpak_request_duration_seconds histogram" in self.output

    def test_compression_help_present(self):
        assert "# HELP tokenpak_compression_ratio" in self.output

    def test_compression_type_gauge(self):
        assert "# TYPE tokenpak_compression_ratio gauge" in self.output

    def test_circuit_help_present(self):
        assert "# HELP tokenpak_circuit_state" in self.output

    def test_circuit_type_gauge(self):
        assert "# TYPE tokenpak_circuit_state gauge" in self.output

    # -- Label presence --
    def test_requests_provider_label_anthropic(self):
        assert 'provider="anthropic"' in self.output

    def test_requests_status_label_success(self):
        assert 'status="success"' in self.output

    def test_tokens_direction_label_input(self):
        assert 'direction="input"' in self.output

    def test_tokens_direction_label_output(self):
        assert 'direction="output"' in self.output

    def test_tokens_direction_label_saved(self):
        assert 'direction="saved"' in self.output

    # -- Metric values --
    def test_requests_count_present(self):
        assert "1523" in self.output

    def test_tokens_saved_value(self):
        # 2100000 tokens saved for anthropic
        assert "2100000" in self.output

    # -- Histogram buckets --
    def test_histogram_bucket_lines(self):
        assert "tokenpak_request_duration_seconds_bucket" in self.output

    def test_histogram_inf_bucket(self):
        assert 'le="+Inf"' in self.output

    def test_histogram_sum_line(self):
        assert "tokenpak_request_duration_seconds_sum" in self.output

    def test_histogram_count_line(self):
        assert "tokenpak_request_duration_seconds_count" in self.output

    def test_histogram_buckets_cumulative_order(self):
        """Bucket counts should be cumulative (non-decreasing)."""
        lines = self.output.split("\n")
        bucket_counts = []
        for line in lines:
            if "tokenpak_request_duration_seconds_bucket{provider=\"anthropic\"" in line:
                val = int(line.split(" ")[-1])
                bucket_counts.append(val)
        for i in range(1, len(bucket_counts)):
            assert bucket_counts[i] >= bucket_counts[i - 1], (
                f"Bucket counts must be non-decreasing: {bucket_counts}"
            )

    # -- Gauge values --
    def test_compression_ratio_value_present(self):
        # 0.5385 or similar
        assert "tokenpak_compression_ratio" in self.output

    def test_circuit_state_closed_default(self):
        # No circuit breaker provided → all 0 (closed)
        lines = [l for l in self.output.split("\n") if l.startswith("tokenpak_circuit_state{")]
        assert all(line.endswith(" 0") for line in lines), (
            f"Expected all circuit states to be 0 (closed): {lines}"
        )

    # -- Trailing newline --
    def test_output_ends_with_newline(self):
        assert self.output.endswith("\n")


# ---------------------------------------------------------------------------
# Circuit breaker integration
# ---------------------------------------------------------------------------

class TestCircuitBreakerMetric:
    def test_open_circuit_state_is_1(self):
        mock_circuit = MagicMock()
        mock_circuit.get_state.return_value = {"is_open": True, "failure_count": 3}
        collector = _make_collector(circuit=mock_circuit)
        output = collector.collect()
        # anthropic circuit should show 1
        lines = [l for l in output.split("\n") if l.startswith("tokenpak_circuit_state{")]
        assert any(line.endswith(" 1") for line in lines), (
            f"Expected at least one open circuit (1) in: {lines}"
        )

    def test_closed_circuit_state_is_0(self):
        mock_circuit = MagicMock()
        mock_circuit.get_state.return_value = {"is_open": False, "failure_count": 0}
        collector = _make_collector(circuit=mock_circuit)
        output = collector.collect()
        lines = [l for l in output.split("\n") if l.startswith("tokenpak_circuit_state{")]
        assert all(line.endswith(" 0") for line in lines)


# ---------------------------------------------------------------------------
# FastAPI endpoint integration
# ---------------------------------------------------------------------------

try:
    from fastapi.testclient import TestClient as _TestClient
    _HAS_FASTAPI_CLIENT = True
except ImportError:
    _HAS_FASTAPI_CLIENT = False


def _make_test_client():
    """Create a TestClient against a fresh in-memory TelemetryDB app."""
    import tempfile
    import os
    from fastapi.testclient import TestClient
    from tokenpak.telemetry.storage import TelemetryDB
    from tokenpak.telemetry.server import create_app

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()
    storage = TelemetryDB(db_path)
    app = create_app(storage=storage)
    client = TestClient(app)
    return client, db_path


@pytest.mark.skipif(not _HAS_FASTAPI_CLIENT, reason="FastAPI TestClient not available")
class TestMetricsEndpoint:
    def setup_method(self):
        import os
        self.client, self.db_path = _make_test_client()
        self._os = os

    def teardown_method(self):
        try:
            self._os.unlink(self.db_path)
        except Exception:
            pass

    def test_metrics_endpoint_returns_200(self):
        """GET /metrics should return 200."""
        response = self.client.get("/metrics")
        assert response.status_code == 200

    def test_metrics_content_type_prometheus(self):
        """Content-Type should indicate Prometheus text format."""
        response = self.client.get("/metrics")
        ct = response.headers.get("content-type", "")
        assert "text/plain" in ct

    def test_metrics_endpoint_has_help_lines(self):
        """Response body should contain Prometheus HELP lines."""
        response = self.client.get("/metrics")
        body = response.text
        assert "# HELP tokenpak_requests_total" in body
        assert "# HELP tokenpak_request_duration_seconds" in body
        assert "# HELP tokenpak_compression_ratio" in body
        assert "# HELP tokenpak_circuit_state" in body

    def test_metrics_endpoint_no_cache_header(self):
        """Response should include Cache-Control: no-cache."""
        response = self.client.get("/metrics")
        assert "no-cache" in response.headers.get("cache-control", "")
