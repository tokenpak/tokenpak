"""Unit tests for tokenpak.telemetry.metrics.prometheus module."""

import sqlite3
import tempfile
import time
from unittest.mock import MagicMock

import pytest

from tokenpak.telemetry.metrics.prometheus import (
    PrometheusRegistry,
    _counter,
    _escape_label_value,
    _gauge,
    _histogram_lines,
    _labels,
    build_metrics_text,
)

# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestEscapeLabelValue:
    """Tests for _escape_label_value."""

    def test_plain_string_unchanged(self):
        assert _escape_label_value("hello") == "hello"

    def test_backslash_escaped(self):
        assert _escape_label_value("a\\b") == "a\\\\b"

    def test_double_quote_escaped(self):
        assert _escape_label_value('say "hi"') == 'say \\"hi\\"'

    def test_newline_escaped(self):
        assert _escape_label_value("line1\nline2") == "line1\\nline2"

    def test_empty_string(self):
        assert _escape_label_value("") == ""

    def test_all_special_chars(self):
        result = _escape_label_value('\\"test\n')
        assert "\\\\" in result
        assert '\\"' in result
        assert "\\n" in result


class TestLabels:
    """Tests for _labels."""

    def test_no_kwargs_returns_empty(self):
        assert _labels() == ""

    def test_single_label(self):
        result = _labels(model="gpt-4")
        assert result == '{model="gpt-4"}'

    def test_multiple_labels(self):
        result = _labels(model="gpt-4", status="success")
        assert "model=" in result
        assert "status=" in result
        assert result.startswith("{")
        assert result.endswith("}")

    def test_none_value_excluded(self):
        result = _labels(model="gpt-4", status=None)
        assert "status" not in result
        assert "model" in result

    def test_special_chars_in_value(self):
        result = _labels(model='my"model')
        assert '\\"' in result


class TestCounter:
    """Tests for _counter."""

    def test_returns_three_lines(self):
        lines = _counter("my_counter", "A help text", 42.0)
        assert len(lines) == 3

    def test_help_line(self):
        lines = _counter("my_counter", "A help text", 42.0)
        assert lines[0] == "# HELP my_counter A help text"

    def test_type_line(self):
        lines = _counter("my_counter", "A help text", 42.0)
        assert lines[1] == "# TYPE my_counter counter"

    def test_value_line(self):
        lines = _counter("my_counter", "A help text", 42.0)
        assert lines[2] == "my_counter 42.0"

    def test_with_labels(self):
        lines = _counter("my_counter", "help", 5.0, status="ok")
        assert 'status="ok"' in lines[2]


class TestGauge:
    """Tests for _gauge."""

    def test_returns_three_lines(self):
        lines = _gauge("my_gauge", "A gauge help", 3.14)
        assert len(lines) == 3

    def test_type_line(self):
        lines = _gauge("my_gauge", "A gauge help", 3.14)
        assert lines[1] == "# TYPE my_gauge gauge"

    def test_value_line(self):
        lines = _gauge("my_gauge", "A gauge help", 99.0)
        assert lines[2] == "my_gauge 99.0"

    def test_with_labels(self):
        lines = _gauge("my_gauge", "help", 1.0, region="us-east")
        assert 'region="us-east"' in lines[2]


class TestHistogramLines:
    """Tests for _histogram_lines."""

    def test_help_and_type_lines_present(self):
        lines = _histogram_lines("dur", "Duration", [(0.1, 5), (float("inf"), 3)], 8, 1.5)
        assert lines[0] == "# HELP dur Duration"
        assert lines[1] == "# TYPE dur histogram"

    def test_bucket_cumulative(self):
        lines = _histogram_lines("dur", "Duration", [(0.1, 2), (0.5, 3), (float("inf"), 1)], 6, 1.0)
        bucket_lines = [l for l in lines if "_bucket" in l]
        assert len(bucket_lines) == 3
        # Cumulative: 2, 5, 6
        assert 'le="0.1"} 2' in bucket_lines[0]
        assert 'le="0.5"} 5' in bucket_lines[1]
        assert 'le="+Inf"} 6' in bucket_lines[2]

    def test_inf_bucket_label(self):
        lines = _histogram_lines("dur", "Duration", [(float("inf"), 10)], 10, 2.0)
        bucket_line = next(l for l in lines if "_bucket" in l)
        assert 'le="+Inf"' in bucket_line

    def test_count_and_sum_lines(self):
        lines = _histogram_lines("dur", "Duration", [(1.0, 3), (float("inf"), 2)], 5, 2.5)
        count_line = next(l for l in lines if l.startswith("dur_count"))
        sum_line = next(l for l in lines if l.startswith("dur_sum"))
        assert count_line == "dur_count 5"
        assert "2.500000" in sum_line

    def test_buckets_sorted(self):
        # Pass buckets out of order — should still be sorted ascending
        lines = _histogram_lines("dur", "D", [(1.0, 1), (0.1, 4), (float("inf"), 2)], 7, 3.0)
        bucket_lines = [l for l in lines if "_bucket" in l]
        # First bucket should have le=0.1 (smallest), cumulative=4
        assert 'le="0.1"} 4' in bucket_lines[0]


# ---------------------------------------------------------------------------
# PrometheusRegistry initialization tests
# ---------------------------------------------------------------------------


class TestPrometheusRegistryInit:
    """Tests for PrometheusRegistry.__init__."""

    def test_stores_session(self):
        session = {"requests": 10}
        reg = PrometheusRegistry(session)
        assert reg._session is session

    def test_stores_monitor(self):
        monitor = MagicMock()
        reg = PrometheusRegistry({}, monitor)
        assert reg._monitor is monitor

    def test_monitor_defaults_to_none(self):
        reg = PrometheusRegistry({})
        assert reg._monitor is None

    def test_duration_buckets_defined(self):
        reg = PrometheusRegistry({})
        assert float("inf") in reg.DURATION_BUCKETS_S
        assert len(reg.DURATION_BUCKETS_S) > 0


# ---------------------------------------------------------------------------
# DB query method tests (with mocked monitor)
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db():
    """Create a temporary SQLite database with a requests table."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE requests (
            id INTEGER PRIMARY KEY,
            model TEXT,
            status_code INTEGER,
            latency_ms REAL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            compressed_tokens INTEGER
        )
    """)
    conn.commit()
    conn.close()
    yield db_path
    import os
    os.unlink(db_path)


@pytest.fixture
def monitor_with_db(tmp_db):
    """Return a mock monitor pointing at the temp DB."""
    m = MagicMock()
    m.db_path = tmp_db
    return m


def _insert_request(db_path, model, status_code, latency_ms, input_tok, output_tok, compressed_tok):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO requests (model, status_code, latency_ms, input_tokens, output_tokens, compressed_tokens) VALUES (?,?,?,?,?,?)",
        (model, status_code, latency_ms, input_tok, output_tok, compressed_tok),
    )
    conn.commit()
    conn.close()


class TestQueryByModelStatus:
    """Tests for PrometheusRegistry._query_by_model_status."""

    def test_no_monitor_returns_empty(self):
        reg = PrometheusRegistry({}, None)
        assert reg._query_by_model_status() == []

    def test_empty_db_returns_empty(self, monitor_with_db):
        reg = PrometheusRegistry({}, monitor_with_db)
        assert reg._query_by_model_status() == []

    def test_success_row(self, monitor_with_db, tmp_db):
        _insert_request(tmp_db, "gpt-4", 200, 100.0, 100, 50, 80)
        reg = PrometheusRegistry({}, monitor_with_db)
        rows = reg._query_by_model_status()
        assert len(rows) == 1
        assert rows[0] == ("gpt-4", "success", 1)

    def test_error_row(self, monitor_with_db, tmp_db):
        _insert_request(tmp_db, "gpt-4", 500, 50.0, 100, 0, 100)
        reg = PrometheusRegistry({}, monitor_with_db)
        rows = reg._query_by_model_status()
        assert any(r[1] == "error" for r in rows)

    def test_null_model_becomes_unknown(self, monitor_with_db, tmp_db):
        _insert_request(tmp_db, None, 200, 100.0, 10, 5, 10)
        reg = PrometheusRegistry({}, monitor_with_db)
        rows = reg._query_by_model_status()
        assert rows[0][0] == "unknown"

    def test_multiple_models_and_statuses(self, monitor_with_db, tmp_db):
        _insert_request(tmp_db, "claude-3", 200, 80.0, 200, 100, 150)
        _insert_request(tmp_db, "claude-3", 429, 10.0, 50, 0, 50)
        _insert_request(tmp_db, "gpt-4", 200, 120.0, 300, 200, 250)
        reg = PrometheusRegistry({}, monitor_with_db)
        rows = reg._query_by_model_status()
        assert len(rows) == 3

    def test_db_error_returns_empty(self):
        m = MagicMock()
        m.db_path = "/nonexistent/path/to.db"
        reg = PrometheusRegistry({}, m)
        assert reg._query_by_model_status() == []


class TestQueryLatencyHistogram:
    """Tests for PrometheusRegistry._query_latency_histogram."""

    def test_no_monitor_returns_zero_counts(self):
        reg = PrometheusRegistry({}, None)
        buckets, count, total = reg._query_latency_histogram()
        assert count == 0
        assert total == 0.0
        assert len(buckets) == len(reg.DURATION_BUCKETS_S)

    def test_empty_db_returns_zeros(self, monitor_with_db):
        reg = PrometheusRegistry({}, monitor_with_db)
        buckets, count, total = reg._query_latency_histogram()
        assert count == 0
        assert total == 0.0

    def test_single_latency(self, monitor_with_db, tmp_db):
        _insert_request(tmp_db, "gpt-4", 200, 200.0, 100, 50, 80)  # 200ms = 0.2s
        reg = PrometheusRegistry({}, monitor_with_db)
        buckets, count, total = reg._query_latency_histogram()
        assert count == 1
        assert abs(total - 0.2) < 1e-6

    def test_bucket_boundaries(self, monitor_with_db, tmp_db):
        # Insert one request at 100ms (0.1s) — should fall in <=0.1 bucket
        _insert_request(tmp_db, "gpt-4", 200, 100.0, 100, 50, 80)
        reg = PrometheusRegistry({}, monitor_with_db)
        buckets, count, total = reg._query_latency_histogram()
        # The 0.1s bucket (index 1 in DURATION_BUCKETS_S=[0.05, 0.1, ...]) should have count=1
        bucket_dict = dict(buckets)
        assert bucket_dict[0.1] == 1
        # Buckets smaller than 0.1 should have count=0
        assert bucket_dict[0.05] == 0

    def test_multiple_latencies(self, monitor_with_db, tmp_db):
        _insert_request(tmp_db, "m", 200, 50.0, 10, 5, 10)   # 0.05s
        _insert_request(tmp_db, "m", 200, 500.0, 10, 5, 10)  # 0.5s
        _insert_request(tmp_db, "m", 200, 3000.0, 10, 5, 10) # 3.0s
        reg = PrometheusRegistry({}, monitor_with_db)
        buckets, count, total = reg._query_latency_histogram()
        assert count == 3
        assert abs(total - 3.55) < 1e-3

    def test_db_error_returns_zeros(self):
        m = MagicMock()
        m.db_path = "/no/such/file.db"
        reg = PrometheusRegistry({}, m)
        buckets, count, total = reg._query_latency_histogram()
        assert count == 0
        assert total == 0.0


class TestQueryTokensByModel:
    """Tests for PrometheusRegistry._query_tokens_by_model."""

    def test_no_monitor_returns_empty(self):
        reg = PrometheusRegistry({}, None)
        assert reg._query_tokens_by_model() == []

    def test_empty_db_returns_empty(self, monitor_with_db):
        reg = PrometheusRegistry({}, monitor_with_db)
        assert reg._query_tokens_by_model() == []

    def test_token_aggregation(self, monitor_with_db, tmp_db):
        _insert_request(tmp_db, "claude-3", 200, 100.0, 200, 100, 150)
        _insert_request(tmp_db, "claude-3", 200, 80.0, 300, 150, 200)
        reg = PrometheusRegistry({}, monitor_with_db)
        rows = reg._query_tokens_by_model()
        assert len(rows) == 1
        model, inp, out, saved = rows[0]
        assert model == "claude-3"
        assert inp == 500
        assert out == 250
        # saved = sum(input_tokens - compressed_tokens) = (200-150)+(300-200) = 150
        assert saved == 150

    def test_null_model_becomes_unknown(self, monitor_with_db, tmp_db):
        _insert_request(tmp_db, None, 200, 100.0, 100, 50, 80)
        reg = PrometheusRegistry({}, monitor_with_db)
        rows = reg._query_tokens_by_model()
        assert rows[0][0] == "unknown"

    def test_negative_saved_clamped_to_zero(self, monitor_with_db, tmp_db):
        # compressed_tokens > input_tokens → saved would be negative → clamp to 0
        _insert_request(tmp_db, "m", 200, 100.0, 50, 25, 100)
        reg = PrometheusRegistry({}, monitor_with_db)
        rows = reg._query_tokens_by_model()
        assert rows[0][3] == 0

    def test_db_error_returns_empty(self):
        m = MagicMock()
        m.db_path = "/no/such.db"
        reg = PrometheusRegistry({}, m)
        assert reg._query_tokens_by_model() == []


# ---------------------------------------------------------------------------
# PrometheusRegistry.render() tests
# ---------------------------------------------------------------------------


class TestPrometheusRegistryRender:
    """Tests for PrometheusRegistry.render()."""

    def _make_session(self, **overrides):
        base = {
            "start_time": time.time() - 100,
            "requests": 42,
            "input_tokens": 1000,
            "output_tokens": 500,
            "saved_tokens": 200,
            "sent_input_tokens": 800,
            "errors": 3,
            "cost": 0.012345,
            "cache_read_tokens": 150,
        }
        base.update(overrides)
        return base

    def test_output_ends_with_newline(self):
        reg = PrometheusRegistry(self._make_session())
        assert reg.render().endswith("\n")

    def test_required_metric_names_present(self):
        reg = PrometheusRegistry(self._make_session())
        text = reg.render()
        required = [
            "tokenpak_requests_total",
            "tokenpak_request_duration_seconds",
            "tokenpak_tokens_input_total",
            "tokenpak_tokens_output_total",
            "tokenpak_tokens_saved_total",
            "tokenpak_compression_ratio",
            "tokenpak_errors_total",
            "tokenpak_cost_usd_total",
            "tokenpak_cache_read_tokens_total",
            "tokenpak_uptime_seconds",
            "tokenpak_vault_blocks",
        ]
        for name in required:
            assert name in text, f"Missing metric: {name}"

    def test_help_and_type_lines(self):
        reg = PrometheusRegistry(self._make_session())
        text = reg.render()
        assert "# HELP " in text
        assert "# TYPE " in text

    def test_session_values_appear_in_output(self):
        session = self._make_session(saved_tokens=999, errors=7, cost=0.0500, cache_read_tokens=88)
        reg = PrometheusRegistry(session)
        text = reg.render()
        assert "999" in text
        assert "7" in text
        assert "0.050000" in text
        assert "88" in text

    def test_compression_ratio_computed(self):
        # raw=1000, sent=500 → ratio=2.0
        session = self._make_session(input_tokens=1000, sent_input_tokens=500)
        reg = PrometheusRegistry(session)
        text = reg.render()
        assert "tokenpak_compression_ratio 2.0" in text

    def test_compression_ratio_defaults_to_one_when_sent_zero(self):
        session = self._make_session(input_tokens=0, sent_input_tokens=0)
        reg = PrometheusRegistry(session)
        text = reg.render()
        assert "tokenpak_compression_ratio 1.0" in text

    def test_uptime_is_positive(self):
        session = self._make_session(start_time=time.time() - 300)
        reg = PrometheusRegistry(session)
        text = reg.render()
        line = next(l for l in text.splitlines() if l.startswith("tokenpak_uptime_seconds"))
        uptime_val = int(line.split()[-1])
        assert uptime_val >= 299

    def test_fallback_requests_without_monitor(self):
        session = self._make_session(requests=77)
        reg = PrometheusRegistry(session)
        text = reg.render()
        assert "77" in text

    def test_with_monitor_uses_db_data(self, monitor_with_db, tmp_db):
        _insert_request(tmp_db, "claude-3", 200, 150.0, 200, 100, 150)
        session = self._make_session()
        reg = PrometheusRegistry(session, monitor_with_db)
        text = reg.render()
        assert 'model="claude-3"' in text
        assert 'status="success"' in text

    def test_vault_blocks_from_monitor(self):
        monitor = MagicMock()
        monitor._vault_blocks = 42
        session = self._make_session()
        reg = PrometheusRegistry(session, monitor)
        text = reg.render()
        assert "tokenpak_vault_blocks 42" in text

    def test_vault_blocks_zero_without_attribute(self):
        monitor = MagicMock(spec=[])  # no attributes
        session = self._make_session()
        reg = PrometheusRegistry(session, monitor)
        text = reg.render()
        assert "tokenpak_vault_blocks 0" in text

    def test_no_monitor_vault_blocks_zero(self):
        reg = PrometheusRegistry(self._make_session())
        text = reg.render()
        assert "tokenpak_vault_blocks 0" in text

    def test_counter_type_lines(self):
        reg = PrometheusRegistry(self._make_session())
        text = reg.render()
        assert "# TYPE tokenpak_requests_total counter" in text
        assert "# TYPE tokenpak_tokens_input_total counter" in text

    def test_gauge_type_lines(self):
        reg = PrometheusRegistry(self._make_session())
        text = reg.render()
        assert "# TYPE tokenpak_compression_ratio gauge" in text
        assert "# TYPE tokenpak_uptime_seconds gauge" in text

    def test_histogram_type_line(self):
        reg = PrometheusRegistry(self._make_session())
        text = reg.render()
        assert "# TYPE tokenpak_request_duration_seconds histogram" in text

    def test_histogram_bucket_inf(self):
        reg = PrometheusRegistry(self._make_session())
        text = reg.render()
        assert 'le="+Inf"' in text

    def test_session_missing_keys_dont_crash(self):
        # Minimal session — should not raise
        reg = PrometheusRegistry({})
        text = reg.render()
        assert "tokenpak_requests_total" in text

    def test_cost_formatted_to_six_decimals(self):
        session = self._make_session(cost=0.001)
        reg = PrometheusRegistry(session)
        text = reg.render()
        assert "tokenpak_cost_usd_total 0.001000" in text


# ---------------------------------------------------------------------------
# build_metrics_text tests
# ---------------------------------------------------------------------------


class TestBuildMetricsText:
    """Tests for build_metrics_text convenience function."""

    def test_returns_string(self):
        result = build_metrics_text({"requests": 5})
        assert isinstance(result, str)

    def test_contains_required_metrics(self):
        result = build_metrics_text({"requests": 5})
        assert "tokenpak_requests_total" in result

    def test_vault_blocks_injected_into_monitor(self):
        monitor = MagicMock()
        monitor._vault_blocks = 0
        del monitor._vault_blocks  # remove so hasattr returns False
        build_metrics_text({"requests": 1}, monitor=monitor, vault_blocks=99)
        assert monitor._vault_blocks == 99

    def test_vault_blocks_not_set_when_monitor_already_has_attribute(self):
        monitor = MagicMock()
        monitor._vault_blocks = 55
        build_metrics_text({"requests": 1}, monitor=monitor, vault_blocks=99)
        # vault_blocks should NOT be overwritten because hasattr returns True
        assert monitor._vault_blocks == 55

    def test_vault_blocks_not_set_when_no_monitor(self):
        # Should not raise even when monitor=None and vault_blocks given
        result = build_metrics_text({"requests": 1}, monitor=None, vault_blocks=10)
        assert "tokenpak_vault_blocks" in result

    def test_no_monitor_no_vault_blocks(self):
        result = build_metrics_text({})
        assert "tokenpak_vault_blocks 0" in result

    def test_with_real_db(self, monitor_with_db, tmp_db):
        _insert_request(tmp_db, "gpt-4", 200, 250.0, 500, 200, 400)
        result = build_metrics_text({"requests": 1}, monitor=monitor_with_db)
        assert 'model="gpt-4"' in result
