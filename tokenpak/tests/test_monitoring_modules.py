# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for tokenpak.telemetry.monitoring modules.

Covers: health, metrics, provider_health, request_logger, request_size,
        audit_trail, swap_alert.

All external I/O (HTTP, filesystem, /proc) is mocked — no live calls are made.
"""

from __future__ import annotations

import json
import threading
import time
import unittest
from unittest import mock

# ---------------------------------------------------------------------------
# monitoring.health
# ---------------------------------------------------------------------------


class TestEstimateDictMemoryMb(unittest.TestCase):
    def test_empty_dict(self):
        from tokenpak.telemetry.monitoring.health import _estimate_dict_memory_mb

        result = _estimate_dict_memory_mb({})
        self.assertIsInstance(result, float)
        self.assertGreaterEqual(result, 0.0)

    def test_nonempty_dict_positive(self):
        from tokenpak.telemetry.monitoring.health import _estimate_dict_memory_mb

        # Use values large enough (>1KB each) so the estimate rounds above 0.0 MB
        d = {"key1": "x" * 2000, "key2": "y" * 2000}
        result = _estimate_dict_memory_mb(d)
        self.assertGreater(result, 0.0)

    def test_returns_float(self):
        from tokenpak.telemetry.monitoring.health import _estimate_dict_memory_mb

        result = _estimate_dict_memory_mb({"a": 1})
        self.assertIsInstance(result, float)


class TestAggregateStatus(unittest.TestCase):
    def test_all_ok_returns_healthy(self):
        from tokenpak.telemetry.monitoring.health import aggregate_status

        providers = {
            "anthropic": {"status": "ok"},
            "openai": {"status": "ok"},
        }
        self.assertEqual(aggregate_status(providers, cache_ok=True), "healthy")

    def test_cache_down_returns_unhealthy(self):
        from tokenpak.telemetry.monitoring.health import aggregate_status

        providers = {"anthropic": {"status": "ok"}}
        self.assertEqual(aggregate_status(providers, cache_ok=False), "unhealthy")

    def test_single_provider_timeout_is_degraded(self):
        from tokenpak.telemetry.monitoring.health import aggregate_status

        # Single provider that timed out with multiple providers → degraded
        providers = {
            "anthropic": {"status": "timeout"},
            "openai": {"status": "ok"},
        }
        self.assertEqual(aggregate_status(providers, cache_ok=True), "degraded")

    def test_two_provider_failures_returns_unhealthy(self):
        from tokenpak.telemetry.monitoring.health import aggregate_status

        providers = {
            "anthropic": {"status": "timeout"},
            "openai": {"status": "error"},
        }
        self.assertEqual(aggregate_status(providers, cache_ok=True), "unhealthy")

    def test_single_provider_error_returns_unhealthy(self):
        from tokenpak.telemetry.monitoring.health import aggregate_status

        providers = {"anthropic": {"status": "error"}}
        self.assertEqual(aggregate_status(providers, cache_ok=True), "unhealthy")

    def test_single_provider_ok_returns_healthy(self):
        from tokenpak.telemetry.monitoring.health import aggregate_status

        providers = {"anthropic": {"status": "ok"}}
        self.assertEqual(aggregate_status(providers, cache_ok=True), "healthy")

    def test_empty_providers_no_bad_returns_healthy(self):
        from tokenpak.telemetry.monitoring.health import aggregate_status

        self.assertEqual(aggregate_status({}, cache_ok=True), "healthy")


class TestCheckProviderMocked(unittest.TestCase):
    def test_ok_on_2xx_response(self):
        from tokenpak.telemetry.monitoring.health import _check_provider

        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        with mock.patch("httpx.head", return_value=mock_resp):
            result = _check_provider("anthropic", "https://api.anthropic.com")
        self.assertEqual(result["status"], "ok")
        self.assertIn("last_check", result)
        self.assertIn("response_time_ms", result)

    def test_ok_on_401_response(self):
        """Any non-5xx response means network path is open."""
        from tokenpak.telemetry.monitoring.health import _check_provider

        mock_resp = mock.MagicMock()
        mock_resp.status_code = 401
        with mock.patch("httpx.head", return_value=mock_resp):
            result = _check_provider("anthropic", "https://api.anthropic.com")
        self.assertEqual(result["status"], "ok")

    def test_error_on_5xx_response(self):
        from tokenpak.telemetry.monitoring.health import _check_provider

        mock_resp = mock.MagicMock()
        mock_resp.status_code = 503
        with mock.patch("httpx.head", return_value=mock_resp):
            result = _check_provider("anthropic", "https://api.anthropic.com")
        self.assertEqual(result["status"], "error")

    def test_timeout_status(self):
        import httpx

        from tokenpak.telemetry.monitoring.health import _check_provider

        with mock.patch("httpx.head", side_effect=httpx.TimeoutException("timeout")):
            result = _check_provider("anthropic", "https://api.anthropic.com")
        self.assertEqual(result["status"], "timeout")

    def test_generic_exception_returns_error(self):
        from tokenpak.telemetry.monitoring.health import _check_provider

        with mock.patch("httpx.head", side_effect=Exception("network down")):
            result = _check_provider("anthropic", "https://api.anthropic.com")
        self.assertEqual(result["status"], "error")


class TestHealthChecker(unittest.TestCase):
    def _make_checker_with_mock_providers(self, provider_status="ok"):
        """Return a HealthChecker with mocked check_providers and get_cache_metrics."""
        from tokenpak.telemetry.monitoring.health import HealthChecker

        checker = HealthChecker(start_time=time.time() - 60, version="1.1.0")
        mock_providers = {"anthropic": {"status": provider_status}}
        mock_cache = {"entries": 5, "memory_used_mb": 0.1, "compression_ratio": 0.75}

        with (
            mock.patch(
                "tokenpak.telemetry.monitoring.health.check_providers", return_value=mock_providers
            ),
            mock.patch(
                "tokenpak.telemetry.monitoring.health.get_cache_metrics", return_value=mock_cache
            ),
        ):
            result = checker.check()
        return result

    def test_check_returns_required_keys(self):
        result = self._make_checker_with_mock_providers()
        for key in ("status", "timestamp", "uptime_seconds", "proxy_version", "providers", "cache"):
            self.assertIn(key, result)

    def test_check_uptime_is_positive(self):
        result = self._make_checker_with_mock_providers()
        self.assertGreater(result["uptime_seconds"], 0)

    def test_check_version_default(self):
        import tokenpak
        from tokenpak.telemetry.monitoring.health import HealthChecker

        checker = HealthChecker(start_time=time.time())
        with (
            mock.patch("tokenpak.telemetry.monitoring.health.check_providers", return_value={}),
            mock.patch(
                "tokenpak.telemetry.monitoring.health.get_cache_metrics",
                return_value={"entries": 0, "memory_used_mb": 0.0, "compression_ratio": 0.0},
            ),
        ):
            result = checker.check()
        self.assertEqual(result["proxy_version"], tokenpak.__version__)

    def test_check_status_healthy_when_ok(self):
        result = self._make_checker_with_mock_providers("ok")
        self.assertEqual(result["status"], "healthy")

    def test_check_status_unhealthy_when_error(self):
        result = self._make_checker_with_mock_providers("error")
        self.assertEqual(result["status"], "unhealthy")

    def test_init_with_explicit_start_time(self):
        from tokenpak.telemetry.monitoring.health import HealthChecker

        t0 = time.time() - 100
        checker = HealthChecker(start_time=t0, version="0.0.1")
        self.assertEqual(checker._version, "0.0.1")
        self.assertAlmostEqual(checker._start_time, t0, delta=1)


# ---------------------------------------------------------------------------
# monitoring.metrics
# ---------------------------------------------------------------------------


class TestMetricsFormatHelpers(unittest.TestCase):
    def test_escape_label_value_backslash(self):
        from tokenpak.telemetry.monitoring.metrics import _escape_label_value

        self.assertEqual(_escape_label_value("a\\b"), "a\\\\b")

    def test_escape_label_value_double_quote(self):
        from tokenpak.telemetry.monitoring.metrics import _escape_label_value

        self.assertEqual(_escape_label_value('say "hi"'), 'say \\"hi\\"')

    def test_escape_label_value_newline(self):
        from tokenpak.telemetry.monitoring.metrics import _escape_label_value

        self.assertEqual(_escape_label_value("line\nnext"), "line\\nnext")

    def test_label_str_single(self):
        from tokenpak.telemetry.monitoring.metrics import _label_str

        result = _label_str(provider="anthropic")
        self.assertEqual(result, '{provider="anthropic"}')

    def test_label_str_multiple(self):
        from tokenpak.telemetry.monitoring.metrics import _label_str

        result = _label_str(provider="openai", model="gpt-4")
        self.assertIn('provider="openai"', result)
        self.assertIn('model="gpt-4"', result)

    def test_label_str_empty_value_omitted(self):
        from tokenpak.telemetry.monitoring.metrics import _label_str

        result = _label_str(provider="anthropic", model="")
        self.assertNotIn("model", result)

    def test_label_str_no_labels(self):
        from tokenpak.telemetry.monitoring.metrics import _label_str

        result = _label_str()
        self.assertEqual(result, "")

    def test_fmt_integer(self):
        from tokenpak.telemetry.monitoring.metrics import _fmt

        self.assertEqual(_fmt(42.0), "42")

    def test_fmt_inf(self):
        from tokenpak.telemetry.monitoring.metrics import _fmt

        self.assertEqual(_fmt(float("inf")), "+Inf")

    def test_fmt_nan(self):
        from tokenpak.telemetry.monitoring.metrics import _fmt

        result = _fmt(float("nan"))
        self.assertEqual(result, "NaN")

    def test_fmt_float(self):
        from tokenpak.telemetry.monitoring.metrics import _fmt

        result = _fmt(3.14159)
        self.assertIn("3.14159", result)


class TestProxyMetricsCollector(unittest.TestCase):
    def _make_collector(self):
        from tokenpak.telemetry.monitoring.metrics import ProxyMetricsCollector

        return ProxyMetricsCollector(proxy_server=None, db_path="/nonexistent/db.db")

    def test_collect_returns_string(self):
        collector = self._make_collector()
        result = collector.collect()
        self.assertIsInstance(result, str)

    def test_collect_ends_with_newline(self):
        collector = self._make_collector()
        result = collector.collect()
        self.assertTrue(result.endswith("\n"))

    def test_collect_contains_help_and_type(self):
        collector = self._make_collector()
        result = collector.collect()
        self.assertIn("# HELP tokenpak_requests_total", result)
        self.assertIn("# TYPE tokenpak_requests_total counter", result)

    def test_collect_contains_up_metric(self):
        collector = self._make_collector()
        result = collector.collect()
        self.assertIn("tokenpak_up", result)

    def test_collect_contains_cache_metrics(self):
        collector = self._make_collector()
        result = collector.collect()
        self.assertIn("tokenpak_cache_entries", result)
        self.assertIn("tokenpak_cache_memory_bytes", result)

    def test_get_session_no_proxy_returns_defaults(self):
        collector = self._make_collector()
        session = collector._get_session()
        self.assertEqual(session["requests"], 0)
        self.assertEqual(session["saved_tokens"], 0)

    def test_get_up_status_no_proxy_returns_1(self):
        collector = self._make_collector()
        self.assertEqual(collector._get_up_status(), 1)

    def test_get_up_status_shutting_down_returns_0(self):
        from tokenpak.telemetry.monitoring.metrics import ProxyMetricsCollector

        mock_ps = mock.MagicMock()
        mock_ps.shutdown.is_shutting_down = True
        collector = ProxyMetricsCollector(proxy_server=mock_ps, db_path="/nonexistent/db.db")
        self.assertEqual(collector._get_up_status(), 0)

    def test_emit_cache_hit_ratio_zero_division(self):
        collector = self._make_collector()
        lines = []
        session = {"input_tokens": 0, "cache_read_tokens": 100}
        collector._emit_cache_hit_ratio(lines, {}, session)
        output = "\n".join(lines)
        self.assertIn("tokenpak_cache_hit_ratio 0", output)

    def test_emit_cache_hit_ratio_with_tokens(self):
        collector = self._make_collector()
        lines = []
        session = {"input_tokens": 1000, "cache_read_tokens": 200}
        collector._emit_cache_hit_ratio(lines, {}, session)
        output = "\n".join(lines)
        self.assertIn("tokenpak_cache_hit_ratio 0.2", output)

    def test_query_telemetry_db_nonexistent_returns_empty(self):
        collector = self._make_collector()
        rows = collector._query_telemetry_db()
        self.assertEqual(rows, [])


# ---------------------------------------------------------------------------
# monitoring.provider_health
# ---------------------------------------------------------------------------


class TestProviderMetrics(unittest.TestCase):
    def test_to_dict_structure(self):
        from collections import deque

        from tokenpak.telemetry.monitoring.provider_health import ProviderMetrics

        m = ProviderMetrics(provider="anthropic", latencies_ms=deque())
        m.request_count = 10
        m.error_count = 1
        m.success_count = 9
        m.error_rate = 0.1
        m.success_rate = 0.9
        m.p50_latency = 120.0
        m.p99_latency = 500.0
        m.status = "GREEN"
        m.last_seen = "2026-04-12T20:00:00+00:00"

        d = m.to_dict()
        self.assertEqual(d["provider"], "anthropic")
        self.assertEqual(d["request_count"], 10)
        self.assertAlmostEqual(d["error_rate"], 10.0)  # percent
        self.assertAlmostEqual(d["success_rate"], 90.0)  # percent
        self.assertEqual(d["status"], "GREEN")
        self.assertNotIn("latencies_ms", d)


class TestProviderHealthMonitor(unittest.TestCase):
    def setUp(self):
        from tokenpak.telemetry.monitoring.provider_health import ProviderHealthMonitor

        self.monitor = ProviderHealthMonitor()

    def test_init_empty(self):
        result = self.monitor.get_all_health()
        self.assertEqual(result["total_providers"], 0)
        self.assertEqual(result["providers"], {})

    def test_record_single_request_ok(self):
        self.monitor.record_request("anthropic", 150.0, 200)
        health = self.monitor.get_provider_health("anthropic")
        self.assertIsNotNone(health)
        self.assertEqual(health["request_count"], 1)
        self.assertEqual(health["success_count"], 1)
        self.assertEqual(health["error_count"], 0)

    def test_record_5xx_increments_error_count(self):
        self.monitor.record_request("anthropic", 200.0, 503)
        health = self.monitor.get_provider_health("anthropic")
        self.assertEqual(health["error_count"], 1)
        self.assertEqual(health["success_count"], 0)

    def test_status_green_high_success_low_latency(self):
        # >99% success, p99 < 2000ms → GREEN
        for _ in range(100):
            self.monitor.record_request("anthropic", 100.0, 200)
        health = self.monitor.get_provider_health("anthropic")
        self.assertEqual(health["status"], "GREEN")

    def test_status_red_low_success_rate(self):
        # <95% success → RED
        for _ in range(90):
            self.monitor.record_request("anthropic", 100.0, 200)
        for _ in range(10):
            self.monitor.record_request("anthropic", 100.0, 500)
        health = self.monitor.get_provider_health("anthropic")
        self.assertEqual(health["status"], "RED")

    def test_multiple_providers_tracked_independently(self):
        self.monitor.record_request("anthropic", 100.0, 200)
        self.monitor.record_request("openai", 200.0, 200)
        result = self.monitor.get_all_health()
        self.assertIn("anthropic", result["providers"])
        self.assertIn("openai", result["providers"])
        self.assertEqual(result["total_providers"], 2)

    def test_unknown_provider_returns_none(self):
        health = self.monitor.get_provider_health("nonexistent")
        self.assertIsNone(health)

    def test_clear_resets_all_metrics(self):
        self.monitor.record_request("anthropic", 100.0, 200)
        self.monitor.clear()
        self.assertEqual(self.monitor.get_all_health()["total_providers"], 0)

    def test_latency_percentiles_computed(self):
        latencies = [100.0 * i for i in range(1, 11)]  # 100..1000ms
        for lat in latencies:
            self.monitor.record_request("anthropic", lat, 200)
        health = self.monitor.get_provider_health("anthropic")
        self.assertGreater(health["p50_latency_ms"], 0)
        self.assertGreater(health["p99_latency_ms"], 0)

    def test_get_all_health_timestamp_format(self):
        result = self.monitor.get_all_health()
        # Should be ISO 8601
        ts = result["timestamp"]
        self.assertIn("T", ts)

    def test_thread_safety(self):
        """Parallel writes from multiple threads must not raise."""
        errors = []

        def worker(provider):
            try:
                for _ in range(50):
                    self.monitor.record_request(provider, 100.0, 200)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(f"p{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


class TestGetMonitorSingleton(unittest.TestCase):
    def test_returns_same_instance(self):
        from tokenpak.telemetry.monitoring import provider_health

        # Reset singleton for isolation
        provider_health._monitor = None
        m1 = provider_health.get_monitor()
        m2 = provider_health.get_monitor()
        self.assertIs(m1, m2)

    def test_record_provider_request_convenience(self):
        from tokenpak.telemetry.monitoring import provider_health

        provider_health._monitor = None
        provider_health.record_provider_request("anthropic", 123.0, 200)
        health = provider_health.get_monitor().get_provider_health("anthropic")
        self.assertIsNotNone(health)


# ---------------------------------------------------------------------------
# monitoring.request_logger
# ---------------------------------------------------------------------------


class TestRequestLogRecord(unittest.TestCase):
    def _make_record(self, **kwargs):
        from tokenpak.telemetry.monitoring.request_logger import LEVEL_INFO, RequestLogRecord

        defaults = dict(
            request_id="req-001",
            timestamp="2026-04-12T20:00:00Z",
            level=LEVEL_INFO,
            client_ip="127.0.0.1",
            method="POST",
            endpoint="/v1/messages",
            request_body_size=1024,
            response_status=200,
            response_body_size=512,
            latency_ms=120.5,
        )
        defaults.update(kwargs)
        return RequestLogRecord(**defaults)

    def test_to_dict_required_keys(self):
        record = self._make_record()
        d = record.to_dict()
        for key in (
            "request_id",
            "timestamp",
            "level",
            "method",
            "endpoint",
            "response_status",
            "latency_ms",
        ):
            self.assertIn(key, d)

    def test_to_dict_optional_model_present(self):
        record = self._make_record(model="claude-3-5-sonnet", provider="anthropic")
        d = record.to_dict()
        self.assertEqual(d["model"], "claude-3-5-sonnet")
        self.assertEqual(d["provider"], "anthropic")

    def test_to_dict_model_absent_when_empty(self):
        record = self._make_record(model="")
        d = record.to_dict()
        self.assertNotIn("model", d)

    def test_compression_ratio_included_when_set(self):
        record = self._make_record(compression_ratio=0.72)
        d = record.to_dict()
        self.assertAlmostEqual(d["compression_ratio"], 0.72, places=4)

    def test_compression_ratio_absent_when_none(self):
        record = self._make_record(compression_ratio=None)
        d = record.to_dict()
        self.assertNotIn("compression_ratio", d)

    def test_to_json_valid_json(self):
        record = self._make_record()
        raw = record.to_json()
        parsed = json.loads(raw)
        self.assertEqual(parsed["request_id"], "req-001")

    def test_to_text_contains_request_id(self):
        record = self._make_record()
        text = record.to_text()
        self.assertIn("req-001", text)

    def test_to_text_contains_endpoint(self):
        record = self._make_record()
        text = record.to_text()
        self.assertIn("/v1/messages", text)

    def test_to_text_ratio_shown_when_set(self):
        record = self._make_record(compression_ratio=0.5)
        text = record.to_text()
        self.assertIn("ratio=", text)

    def test_extra_fields_merged_into_dict(self):
        record = self._make_record(extra={"custom_field": "hello"})
        d = record.to_dict()
        self.assertEqual(d["custom_field"], "hello")


class TestRequestLoggerInit(unittest.TestCase):
    def tearDown(self):
        from tokenpak.telemetry.monitoring.request_logger import RequestLogger

        RequestLogger.reset_instance()

    def test_init_with_stdout_config(self):
        from tokenpak.telemetry.monitoring.request_logger import RequestLogger, _StdoutWriter

        rl = RequestLogger(
            config={
                "enabled": True,
                "level": "info",
                "destination": "stdout",
                "retention_days": 30,
            }
        )
        self.assertIsInstance(rl._writer, _StdoutWriter)
        rl.stop()

    def test_new_request_id_generates_uuid(self):
        import uuid

        from tokenpak.telemetry.monitoring.request_logger import RequestLogger

        rid = RequestLogger.new_request_id()
        self.assertIsNotNone(uuid.UUID(rid))  # validates UUID format

    def test_new_request_id_honours_x_request_id_header(self):
        from tokenpak.telemetry.monitoring.request_logger import RequestLogger

        rid = RequestLogger.new_request_id({"X-Request-ID": "my-custom-id"})
        self.assertEqual(rid, "my-custom-id")

    def test_new_request_id_case_insensitive_header(self):
        from tokenpak.telemetry.monitoring.request_logger import RequestLogger

        rid = RequestLogger.new_request_id({"x-request-id": "lower-case-id"})
        self.assertEqual(rid, "lower-case-id")

    def test_get_instance_returns_singleton(self):
        from tokenpak.telemetry.monitoring.request_logger import RequestLogger

        # Use stdout to avoid filesystem I/O
        with mock.patch.object(
            RequestLogger,
            "__init__",
            lambda self, config=None: (
                (
                    setattr(
                        self,
                        "_cfg",
                        {
                            "enabled": True,
                            "level": "info",
                            "destination": "stdout",
                            "retention_days": 30,
                        },
                    ),
                    setattr(self, "_enabled", True),
                    setattr(self, "_level", "info"),
                    setattr(self, "_destination", "stdout"),
                    setattr(self, "_retention_days", 30),
                    setattr(self, "_writer", mock.MagicMock()),
                    setattr(self, "_queue", __import__("queue").Queue()),
                    setattr(self, "_thread", mock.MagicMock(start=lambda: None)),
                )
                and None
            ),
        ):
            pass

        rl1 = RequestLogger(
            config={
                "enabled": True,
                "level": "info",
                "destination": "stdout",
                "retention_days": 7,
            }
        )
        RequestLogger._instance = rl1
        rl2 = RequestLogger.get_instance()
        self.assertIs(rl1, rl2)
        rl1.stop()

    def test_build_record_warn_level_for_4xx(self):
        from tokenpak.telemetry.monitoring.request_logger import LEVEL_WARN, RequestLogger

        rl = RequestLogger(
            config={
                "enabled": True,
                "level": "info",
                "destination": "stdout",
                "retention_days": 7,
            }
        )
        record = rl.build_record(
            request_id="r1",
            response_status=404,
            endpoint="/missing",
        )
        self.assertEqual(record.level, LEVEL_WARN)
        rl.stop()

    def test_build_record_info_level_for_2xx(self):
        from tokenpak.telemetry.monitoring.request_logger import LEVEL_INFO, RequestLogger

        rl = RequestLogger(
            config={
                "enabled": True,
                "level": "info",
                "destination": "stdout",
                "retention_days": 7,
            }
        )
        record = rl.build_record(request_id="r2", response_status=200, endpoint="/ok")
        self.assertEqual(record.level, LEVEL_INFO)
        rl.stop()

    def test_log_disabled_does_not_enqueue(self):
        from tokenpak.telemetry.monitoring.request_logger import (
            LEVEL_INFO,
            RequestLogger,
            RequestLogRecord,
        )

        rl = RequestLogger(
            config={
                "enabled": False,
                "level": "info",
                "destination": "stdout",
                "retention_days": 7,
            }
        )
        record = RequestLogRecord(
            request_id="r3",
            timestamp="2026-04-12T20:00:00Z",
            level=LEVEL_INFO,
        )
        rl.log(record)
        self.assertEqual(rl._queue.qsize(), 0)
        rl.stop()

    def test_log_level_filtered(self):
        from tokenpak.telemetry.monitoring.request_logger import (
            LEVEL_DEBUG,
            RequestLogger,
            RequestLogRecord,
        )

        rl = RequestLogger(
            config={
                "enabled": True,
                "level": "info",
                "destination": "stdout",
                "retention_days": 7,
            }
        )
        record = RequestLogRecord(
            request_id="r4",
            timestamp="2026-04-12T20:00:00Z",
            level=LEVEL_DEBUG,  # below threshold
        )
        rl.log(record)
        self.assertEqual(rl._queue.qsize(), 0)
        rl.stop()


class TestModuleLevelConvenienceFunctions(unittest.TestCase):
    def test_new_request_id_module_level(self):
        from tokenpak.telemetry.monitoring.request_logger import new_request_id

        rid = new_request_id()
        self.assertIsInstance(rid, str)
        self.assertGreater(len(rid), 0)

    def test_new_request_id_with_header(self):
        from tokenpak.telemetry.monitoring.request_logger import new_request_id

        rid = new_request_id({"X-Request-ID": "trace-999"})
        self.assertEqual(rid, "trace-999")


# ---------------------------------------------------------------------------
# monitoring.request_size
# ---------------------------------------------------------------------------


class TestAlertLevel(unittest.TestCase):
    def test_enum_values(self):
        from tokenpak.telemetry.monitoring.request_size import AlertLevel

        self.assertEqual(AlertLevel.YELLOW.value, "yellow")
        self.assertEqual(AlertLevel.ORANGE.value, "orange")
        self.assertEqual(AlertLevel.RED.value, "red")


class TestRequestSizeMonitor(unittest.TestCase):
    def setUp(self):
        from tokenpak.telemetry.monitoring.request_size import RequestSizeConfig, RequestSizeMonitor

        self.config = RequestSizeConfig(
            enabled=True,
            yellow_threshold=300_000,
            orange_threshold=500_000,
            red_threshold=700_000,
        )
        self.monitor = RequestSizeMonitor(config=self.config)

    def test_below_threshold_returns_none(self):
        result = self.monitor.check_request_size(100_000)
        self.assertIsNone(result)

    def test_yellow_threshold_triggers_alert(self):
        from tokenpak.telemetry.monitoring.request_size import AlertLevel

        result = self.monitor.check_request_size(350_000)
        self.assertIsNotNone(result)
        self.assertEqual(result.level, AlertLevel.YELLOW)

    def test_orange_threshold_triggers_alert(self):
        from tokenpak.telemetry.monitoring.request_size import AlertLevel

        result = self.monitor.check_request_size(550_000)
        self.assertIsNotNone(result)
        self.assertEqual(result.level, AlertLevel.ORANGE)

    def test_red_threshold_triggers_alert(self):
        from tokenpak.telemetry.monitoring.request_size import AlertLevel

        result = self.monitor.check_request_size(750_000)
        self.assertIsNotNone(result)
        self.assertEqual(result.level, AlertLevel.RED)

    def test_duplicate_alert_same_level_same_session_suppressed(self):
        # First alert at yellow
        self.monitor.check_request_size(350_000, session_id="sess1")
        # Second alert at same level for same session → suppressed
        result = self.monitor.check_request_size(350_000, session_id="sess1")
        self.assertIsNone(result)

    def test_different_sessions_alerted_independently(self):
        self.monitor.check_request_size(350_000, session_id="sess1")
        result = self.monitor.check_request_size(350_000, session_id="sess2")
        self.assertIsNotNone(result)

    def test_reset_session_allows_re_alert(self):
        from tokenpak.telemetry.monitoring.request_size import AlertLevel

        self.monitor.check_request_size(350_000, session_id="sess1")
        self.monitor.reset_session("sess1")
        result = self.monitor.check_request_size(350_000, session_id="sess1")
        self.assertIsNotNone(result)
        self.assertEqual(result.level, AlertLevel.YELLOW)

    def test_get_stats_structure(self):
        stats = self.monitor.get_stats()
        self.assertIn("enabled", stats)
        self.assertIn("thresholds", stats)
        self.assertIn("alert_counts", stats)
        self.assertIn("active_sessions", stats)

    def test_get_stats_alert_counts_increment(self):
        self.monitor.check_request_size(350_000, session_id="s1")
        stats = self.monitor.get_stats()
        self.assertEqual(stats["alert_counts"]["yellow"], 1)

    def test_get_alert_history(self):
        self.monitor.check_request_size(350_000, session_id="h1")
        history = self.monitor.get_alert_history(limit=10)
        self.assertEqual(len(history), 1)
        self.assertIn("level", history[0])
        self.assertIn("size_bytes", history[0])

    def test_disabled_monitor_returns_none(self):
        from tokenpak.telemetry.monitoring.request_size import RequestSizeConfig, RequestSizeMonitor

        mon = RequestSizeMonitor(config=RequestSizeConfig(enabled=False))
        self.assertIsNone(mon.check_request_size(999_999))

    def test_to_dict_structure(self):
        d = self.monitor.to_dict()
        self.assertEqual(d["type"], "request_size_alert")
        self.assertIn("stats", d)
        self.assertIn("recent_alerts", d)

    def test_alert_message_contains_size(self):
        result = self.monitor.check_request_size(350_000)
        self.assertIn("KB", result.message)

    def test_alert_has_session_id(self):
        result = self.monitor.check_request_size(350_000, session_id="my-session")
        self.assertEqual(result.session_id, "my-session")


class TestRequestSizeMonitorSingleton(unittest.TestCase):
    def tearDown(self):
        from tokenpak.telemetry.monitoring import request_size

        request_size._monitor = None

    def test_get_monitor_returns_singleton(self):
        from tokenpak.telemetry.monitoring.request_size import get_monitor

        m1 = get_monitor()
        m2 = get_monitor()
        self.assertIs(m1, m2)

    def test_reset_monitor_clears_singleton(self):
        from tokenpak.telemetry.monitoring.request_size import get_monitor, reset_monitor

        m1 = get_monitor()
        reset_monitor()
        m2 = get_monitor()
        self.assertIsNot(m1, m2)


# ---------------------------------------------------------------------------
# monitoring.audit_trail
# ---------------------------------------------------------------------------


class TestAuditTrail(unittest.TestCase):
    def _make_trail(self):
        from tokenpak.telemetry.monitoring.audit_trail import AuditTrail

        return AuditTrail(request_id="req-audit-001")

    def test_init_empty(self):
        trail = self._make_trail()
        self.assertEqual(len(trail), 0)

    def test_record_compile_adds_event(self):
        trail = self._make_trail()
        trail.record_compile(
            input_block_count=10,
            output_block_count=7,
            blocks_removed=[{"id": "b1", "reason": "low_relevance"}],
            compression_method="extractive",
        )
        self.assertEqual(len(trail), 1)

    def test_record_compile_event_structure(self):
        trail = self._make_trail()
        trail.record_compile(
            input_block_count=10,
            output_block_count=7,
            blocks_removed=[{"id": "b1", "reason": "low_relevance"}],
            compression_method="extractive",
            stage_timings={"parse": 4.2, "compile": 88.1},
            tokens_before=5000,
            tokens_after=3000,
        )
        event = trail._events[0]
        self.assertEqual(event["event"], "compile")
        self.assertEqual(event["input_block_count"], 10)
        self.assertEqual(event["blocks_removed_count"], 1)
        self.assertIn("stage_timings_ms", event)
        self.assertEqual(event["tokens_before"], 5000)

    def test_record_cache_adds_event(self):
        trail = self._make_trail()
        trail.record_cache(operation="get", block_id="b1", hit=True, cached_size=2048)
        self.assertEqual(len(trail), 1)
        event = trail._events[0]
        self.assertEqual(event["event"], "cache")
        self.assertEqual(event["cache_hit"], True)
        self.assertEqual(event["cached_size"], 2048)

    def test_record_metrics_adds_event(self):
        trail = self._make_trail()
        trail.record_metrics(aggregation_window="1h", data_points_returned=42)
        self.assertEqual(len(trail), 1)
        event = trail._events[0]
        self.assertEqual(event["event"], "metrics")
        self.assertEqual(event["data_points_returned"], 42)

    def test_record_error_adds_event(self):
        trail = self._make_trail()
        trail.record_error(error_type="ValueError", message="bad input", field="model")
        self.assertEqual(len(trail), 1)
        event = trail._events[0]
        self.assertEqual(event["event"], "error")
        self.assertEqual(event["error_type"], "ValueError")
        self.assertEqual(event["field"], "model")

    def test_flush_sends_to_logger(self):
        from tokenpak.telemetry.monitoring.request_logger import RequestLogger

        trail = self._make_trail()
        trail.record_compile(input_block_count=5, output_block_count=3)

        with mock.patch.object(RequestLogger, "get_instance") as mock_get:
            mock_logger = mock.MagicMock()
            mock_get.return_value = mock_logger

            trail.flush()

        mock_logger.log.assert_called_once()

    def test_flush_clears_events(self):
        trail = self._make_trail()
        trail.record_compile(input_block_count=5, output_block_count=3)

        with mock.patch(
            "tokenpak.telemetry.monitoring.audit_trail.RequestLogger.get_instance"
        ) as mock_get:
            mock_logger = mock.MagicMock()
            mock_get.return_value = mock_logger
            trail.flush()

        self.assertEqual(len(trail), 0)

    def test_flush_empty_trail_is_noop(self):
        from tokenpak.telemetry.monitoring.request_logger import RequestLogger

        trail = self._make_trail()
        with mock.patch.object(RequestLogger, "get_instance") as mock_get:
            mock_logger = mock.MagicMock()
            mock_get.return_value = mock_logger
            trail.flush()
        mock_logger.log.assert_not_called()

    def test_multiple_events_flushed_in_order(self):
        trail = self._make_trail()
        trail.record_compile(input_block_count=5, output_block_count=3)
        trail.record_cache(operation="get", block_id="b1", hit=False)
        trail.record_error(error_type="Timeout", message="upstream slow")

        calls = []
        with mock.patch(
            "tokenpak.telemetry.monitoring.audit_trail.RequestLogger.get_instance"
        ) as mock_get:
            mock_logger = mock.MagicMock()
            mock_logger.log.side_effect = lambda r: calls.append(r.extra["event"])
            mock_get.return_value = mock_logger
            trail.flush()

        self.assertEqual(calls, ["compile", "cache", "error"])

    def test_repr_contains_request_id(self):
        trail = self._make_trail()
        self.assertIn("req-audit-001", repr(trail))

    def test_record_compile_no_blocks_removed(self):
        trail = self._make_trail()
        trail.record_compile(input_block_count=3, output_block_count=3, blocks_removed=None)
        event = trail._events[0]
        self.assertEqual(event["blocks_removed_count"], 0)
        self.assertNotIn("blocks_removed", event)


# ---------------------------------------------------------------------------
# monitoring.swap_alert
# ---------------------------------------------------------------------------


class TestGetSwapMb(unittest.TestCase):
    def test_reads_proc_meminfo(self):
        """_get_swap_mb should parse /proc/meminfo and return floats."""
        fake_meminfo = (
            "MemTotal:       16384000 kB\n"
            "MemFree:         8192000 kB\n"
            "SwapTotal:       2097152 kB\n"
            "SwapFree:        1048576 kB\n"
        )
        mock_open = mock.mock_open(read_data=fake_meminfo)
        with mock.patch("builtins.open", mock_open):
            from tokenpak.telemetry.monitoring.swap_alert import _get_swap_mb

            used_mb, total_mb, pct = _get_swap_mb()

        self.assertAlmostEqual(total_mb, 2048.0)  # 2097152 kB / 1024
        self.assertAlmostEqual(used_mb, 1024.0)  # 2097152-1048576 kB / 1024
        self.assertAlmostEqual(pct, 50.0, delta=1.0)

    def test_no_swap_returns_zero_pct(self):
        fake_meminfo = "MemTotal:       16384000 kB\nSwapTotal:       0 kB\nSwapFree:        0 kB\n"
        mock_open = mock.mock_open(read_data=fake_meminfo)
        with mock.patch("builtins.open", mock_open):
            from tokenpak.telemetry.monitoring.swap_alert import _get_swap_mb

            used_mb, total_mb, pct = _get_swap_mb()

        self.assertEqual(pct, 0.0)


class TestGetSwapStats(unittest.TestCase):
    def test_returns_dict_with_expected_keys(self):
        import tokenpak.telemetry.monitoring.swap_alert as sa

        with mock.patch.object(sa, "_get_swap_mb", return_value=(512.0, 2048.0, 25.0)):
            stats = sa.get_swap_stats()

        self.assertIn("swap_used_mb", stats)
        self.assertIn("swap_total_mb", stats)
        self.assertIn("swap_pct", stats)
        self.assertIn("alert_threshold_mb", stats)

    def test_values_match_mock(self):
        import tokenpak.telemetry.monitoring.swap_alert as sa

        with mock.patch.object(sa, "_get_swap_mb", return_value=(512.0, 2048.0, 25.0)):
            stats = sa.get_swap_stats()

        self.assertAlmostEqual(stats["swap_used_mb"], 512.0)
        self.assertAlmostEqual(stats["swap_total_mb"], 2048.0)
        self.assertAlmostEqual(stats["swap_pct"], 25.0)

    def test_last_alert_ago_none_when_no_alert(self):
        import tokenpak.telemetry.monitoring.swap_alert as sa

        sa._last_alert_time = 0.0
        with mock.patch.object(sa, "_get_swap_mb", return_value=(0.0, 0.0, 0.0)):
            stats = sa.get_swap_stats()
        self.assertIsNone(stats["last_alert_ago_s"])


class TestCheckSwapPressure(unittest.TestCase):
    def setUp(self):
        # Reset module-level state before each test
        import tokenpak.telemetry.monitoring.swap_alert as sa

        sa._last_alert_time = 0.0

    def test_below_threshold_no_alert(self):
        import tokenpak.telemetry.monitoring.swap_alert as sa

        with mock.patch.object(sa, "_get_swap_mb", return_value=(100.0, 2048.0, 5.0)):
            result = sa.check_swap_pressure(threshold_mb=1024, cooldown_s=60)
        self.assertFalse(result)

    def test_above_threshold_sends_telegram(self):
        import tokenpak.telemetry.monitoring.swap_alert as sa

        with (
            mock.patch.object(sa, "_get_swap_mb", return_value=(1500.0, 2048.0, 73.0)),
            mock.patch.object(sa, "_send_telegram", return_value=True) as mock_send,
        ):
            result = sa.check_swap_pressure(threshold_mb=1024, cooldown_s=60)
        self.assertTrue(result)
        mock_send.assert_called_once()

    def test_rate_limited_does_not_send(self):
        import tokenpak.telemetry.monitoring.swap_alert as sa

        sa._last_alert_time = time.time()  # alert was just sent
        with (
            mock.patch.object(sa, "_get_swap_mb", return_value=(1500.0, 2048.0, 73.0)),
            mock.patch.object(sa, "_send_telegram", return_value=True) as mock_send,
        ):
            result = sa.check_swap_pressure(threshold_mb=1024, cooldown_s=1800)
        self.assertFalse(result)
        mock_send.assert_not_called()

    def test_alert_updates_last_alert_time(self):
        import tokenpak.telemetry.monitoring.swap_alert as sa

        sa._last_alert_time = 0.0
        before = time.time()
        with (
            mock.patch.object(sa, "_get_swap_mb", return_value=(2000.0, 4096.0, 50.0)),
            mock.patch.object(sa, "_send_telegram", return_value=True),
        ):
            sa.check_swap_pressure(threshold_mb=1024, cooldown_s=0)
        self.assertGreaterEqual(sa._last_alert_time, before)

    def test_telegram_failure_returns_false(self):
        import tokenpak.telemetry.monitoring.swap_alert as sa

        sa._last_alert_time = 0.0
        with (
            mock.patch.object(sa, "_get_swap_mb", return_value=(2000.0, 4096.0, 50.0)),
            mock.patch.object(sa, "_send_telegram", return_value=False),
        ):
            result = sa.check_swap_pressure(threshold_mb=1024, cooldown_s=0)
        self.assertFalse(result)


class TestGetTelegramToken(unittest.TestCase):
    def test_reads_from_config_json(self):
        import tokenpak.telemetry.monitoring.swap_alert as sa

        fake_config = {"channels": {"telegram": {"botToken": "bot-from-config"}}}
        mock_open = mock.mock_open(read_data=json.dumps(fake_config))
        with mock.patch("builtins.open", mock_open), mock.patch.dict("os.environ", {}, clear=True):
            token = sa._get_telegram_token()
        self.assertEqual(token, "bot-from-config")

    def test_falls_back_to_env_var(self):
        import tokenpak.telemetry.monitoring.swap_alert as sa

        with (
            mock.patch("builtins.open", side_effect=FileNotFoundError),
            mock.patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "env-token"}),
        ):
            token = sa._get_telegram_token()
        self.assertEqual(token, "env-token")

    def test_returns_none_when_no_token(self):
        import tokenpak.telemetry.monitoring.swap_alert as sa

        with (
            mock.patch("builtins.open", side_effect=FileNotFoundError),
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            token = sa._get_telegram_token()
        self.assertIsNone(token)


if __name__ == "__main__":
    unittest.main()
