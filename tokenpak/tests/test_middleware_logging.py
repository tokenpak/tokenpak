"""
Unit tests for tokenpak.proxy.middleware.logger and tokenpak.proxy.middleware.logging_middleware

Covers: LogRecord, LoggingConfig, AsyncLogger, RequestLogger, init/get_logger,
        LoggingMiddleware (wrap_request, audit log helpers, _get_client_ip).

External I/O is avoided by using destination="stdout" for AsyncLogger instances
and mocking RequestLogger when testing LoggingMiddleware.
"""

import json
import unittest
from unittest.mock import MagicMock

import tokenpak.proxy.middleware.logger as logger_mod
from tokenpak.proxy.middleware.audit_trail import (
    BlockType,
    MetricsAudit,
    create_cache_audit,
    create_compile_audit,
)
from tokenpak.proxy.middleware.logger import (
    AsyncLogger,
    LoggingConfig,
    LogRecord,
    RequestLogger,
    get_logger,
    init_logger,
)
from tokenpak.proxy.middleware.logging_middleware import LoggingMiddleware

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_log_record(**overrides) -> LogRecord:
    defaults = dict(
        timestamp="2026-04-12T12:00:00Z",
        request_id="req-test",
        level="info",
        endpoint="/compile",
        client_ip="10.0.0.1",
        method="POST",
        status_code=200,
        request_size=100,
        response_size=80,
        latency_ms=42.5,
        compression_ratio=0.8,
        message="OK",
        context={},
    )
    defaults.update(overrides)
    return LogRecord(**defaults)


def _stdout_config(**overrides) -> LoggingConfig:
    """LoggingConfig that writes to stdout (no file I/O, no directory creation)."""
    defaults = dict(destination="stdout", flush_interval_sec=999)
    defaults.update(overrides)
    return LoggingConfig(**defaults)


# ---------------------------------------------------------------------------
# LogRecord
# ---------------------------------------------------------------------------


class TestLogRecord(unittest.TestCase):
    def test_to_json_is_valid_json(self):
        r = _make_log_record()
        parsed = json.loads(r.to_json())
        self.assertIsInstance(parsed, dict)

    def test_to_json_contains_fields(self):
        r = _make_log_record(request_id="req-xyz", status_code=201)
        parsed = json.loads(r.to_json())
        self.assertEqual(parsed["request_id"], "req-xyz")
        self.assertEqual(parsed["status_code"], 201)

    def test_to_text_includes_request_id(self):
        r = _make_log_record(request_id="req-abc")
        self.assertIn("req-abc", r.to_text())

    def test_to_text_includes_method_and_endpoint(self):
        r = _make_log_record(method="GET", endpoint="/metrics")
        text = r.to_text()
        self.assertIn("GET", text)
        self.assertIn("/metrics", text)

    def test_to_text_includes_status_code(self):
        r = _make_log_record(status_code=404)
        self.assertIn("404", r.to_text())

    def test_to_text_includes_ratio_when_present(self):
        r = _make_log_record(compression_ratio=0.75)
        self.assertIn("75.0%", r.to_text())

    def test_to_text_no_ratio_string_when_absent(self):
        r = _make_log_record(compression_ratio=None)
        self.assertNotIn("ratio", r.to_text())

    def test_to_text_level_uppercased(self):
        r = _make_log_record(level="error")
        self.assertIn("ERROR", r.to_text())


# ---------------------------------------------------------------------------
# LoggingConfig
# ---------------------------------------------------------------------------


class TestLoggingConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = LoggingConfig()
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.level, "info")
        self.assertEqual(cfg.destination, "file")
        self.assertEqual(cfg.retention_days, 30)
        self.assertFalse(cfg.include_request_body)
        self.assertFalse(cfg.include_response_body)
        self.assertIsNone(cfg.log_dir)

    def test_resolve_log_dir_default_contains_tokenpak(self):
        cfg = LoggingConfig()
        d = cfg.resolve_log_dir()
        self.assertIn(".tokenpak", d)
        self.assertIn("logs", d)

    def test_resolve_log_dir_custom(self):
        cfg = LoggingConfig(log_dir="/tmp/my_logs")
        self.assertEqual(cfg.resolve_log_dir(), "/tmp/my_logs")


# ---------------------------------------------------------------------------
# AsyncLogger
# ---------------------------------------------------------------------------


class TestAsyncLogger(unittest.TestCase):
    def setUp(self):
        logger_mod._logger = None

    def tearDown(self):
        logger_mod._logger = None

    def _make(self, **kwargs) -> AsyncLogger:
        return AsyncLogger(_stdout_config(**kwargs))

    def test_flush_thread_starts(self):
        al = self._make()
        self.assertTrue(al.flush_thread.is_alive())
        al.stop()

    def test_flush_thread_is_daemon(self):
        al = self._make()
        self.assertTrue(al.flush_thread.daemon)
        al.stop()

    def test_log_enqueues_record_when_enabled(self):
        al = self._make()
        al.log(_make_log_record())
        self.assertEqual(len(al.buffer), 1)
        al.stop()

    def test_log_skips_record_when_disabled(self):
        al = self._make(enabled=False)
        al.log(_make_log_record())
        self.assertEqual(len(al.buffer), 0)
        al.stop()

    def test_stop_sets_stop_event(self):
        al = self._make()
        al.stop()
        self.assertTrue(al.stop_event.is_set())

    def test_stop_drains_buffer(self):
        al = self._make()
        for _ in range(3):
            al.log(_make_log_record())
        al.stop()
        self.assertEqual(len(al.buffer), 0)

    def test_unknown_destination_raises_value_error(self):
        cfg = LoggingConfig()
        cfg.destination = "telegraph"
        with self.assertRaises(ValueError):
            AsyncLogger(cfg)


# ---------------------------------------------------------------------------
# RequestLogger
# ---------------------------------------------------------------------------


class TestRequestLogger(unittest.TestCase):
    def setUp(self):
        logger_mod._logger = None

    def tearDown(self):
        logger_mod._logger = None

    def _make(self, **kwargs) -> RequestLogger:
        return RequestLogger(_stdout_config(**kwargs))

    def test_initialization_creates_async_logger(self):
        rl = self._make()
        self.assertIsInstance(rl.async_logger, AsyncLogger)
        rl.stop()

    def test_log_request_enqueues_when_enabled(self):
        rl = self._make()
        rl.log_request(endpoint="/compile", message="ok", request_id="r1")
        self.assertEqual(len(rl.async_logger.buffer), 1)
        rl.stop()

    def test_log_request_skips_when_disabled(self):
        rl = self._make(enabled=False)
        rl.log_request(endpoint="/compile", message="ok")
        self.assertEqual(len(rl.async_logger.buffer), 0)
        rl.stop()

    def test_log_request_auto_generates_request_id(self):
        rl = self._make()
        rl.log_request(endpoint="/compile")
        record = rl.async_logger.buffer[0]
        self.assertIsNotNone(record.request_id)
        self.assertNotEqual(record.request_id, "")
        rl.stop()

    def test_log_request_uses_provided_request_id(self):
        rl = self._make()
        rl.log_request(endpoint="/compile", request_id="explicit-id")
        record = rl.async_logger.buffer[0]
        self.assertEqual(record.request_id, "explicit-id")
        rl.stop()

    def test_log_request_uses_provided_level(self):
        rl = self._make()
        rl.log_request(endpoint="/compile", level="error", request_id="e1")
        record = rl.async_logger.buffer[0]
        self.assertEqual(record.level, "error")
        rl.stop()


# ---------------------------------------------------------------------------
# init_logger / get_logger
# ---------------------------------------------------------------------------


class TestInitGetLogger(unittest.TestCase):
    def setUp(self):
        logger_mod._logger = None

    def tearDown(self):
        if logger_mod._logger:
            logger_mod._logger.stop()
        logger_mod._logger = None

    def test_get_logger_returns_none_before_init(self):
        self.assertIsNone(get_logger())

    def test_init_logger_sets_global(self):
        logger = init_logger(_stdout_config())
        self.assertIsNotNone(logger)
        self.assertIs(get_logger(), logger)

    def test_init_logger_returns_request_logger_instance(self):
        logger = init_logger(_stdout_config())
        self.assertIsInstance(logger, RequestLogger)


# ---------------------------------------------------------------------------
# LoggingMiddleware
# ---------------------------------------------------------------------------


class TestLoggingMiddlewareInit(unittest.TestCase):
    def test_stores_logger_reference(self):
        mock_logger = MagicMock(spec=RequestLogger)
        mw = LoggingMiddleware(mock_logger)
        self.assertIs(mw.logger, mock_logger)

    def test_request_contexts_starts_empty(self):
        mw = LoggingMiddleware(MagicMock(spec=RequestLogger))
        self.assertEqual(mw._request_contexts, {})


class TestWrapRequest(unittest.TestCase):
    def _make_mw(self):
        self.mock_logger = MagicMock(spec=RequestLogger)
        return LoggingMiddleware(self.mock_logger)

    # --- success path ---

    def test_handler_return_value_preserved(self):
        mw = self._make_mw()

        @mw.wrap_request("/test")
        def handler():
            return "my-result"

        self.assertEqual(handler(), "my-result")

    def test_success_logs_status_200(self):
        mw = self._make_mw()

        @mw.wrap_request("/test")
        def handler():
            return "ok"

        handler()
        kw = self.mock_logger.log_request.call_args.kwargs
        self.assertEqual(kw["status_code"], 200)

    def test_success_logs_correct_endpoint(self):
        mw = self._make_mw()

        @mw.wrap_request("/my-endpoint", method="GET")
        def handler():
            return None

        handler()
        kw = self.mock_logger.log_request.call_args.kwargs
        self.assertEqual(kw["endpoint"], "/my-endpoint")
        self.assertEqual(kw["method"], "GET")

    def test_success_logs_info_level(self):
        mw = self._make_mw()

        @mw.wrap_request("/test")
        def handler():
            return None

        handler()
        kw = self.mock_logger.log_request.call_args.kwargs
        self.assertEqual(kw["level"], "info")

    # --- tuple response ---

    def test_tuple_response_extracts_status_code(self):
        mw = self._make_mw()

        @mw.wrap_request("/tuple")
        def handler():
            return ("data", 201)

        result = handler()
        self.assertEqual(result, ("data", 201))
        kw = self.mock_logger.log_request.call_args.kwargs
        self.assertEqual(kw["status_code"], 201)

    def test_tuple_response_returned_intact(self):
        mw = self._make_mw()

        @mw.wrap_request("/tuple3")
        def handler():
            return ("data", 202, {"X-Custom": "val"})

        self.assertEqual(handler(), ("data", 202, {"X-Custom": "val"}))

    # --- error path ---

    def test_exception_is_reraised(self):
        mw = self._make_mw()

        @mw.wrap_request("/err")
        def bad():
            raise ValueError("boom")

        with self.assertRaises(ValueError, msg="boom"):
            bad()

    def test_exception_logs_status_500(self):
        mw = self._make_mw()

        @mw.wrap_request("/err")
        def bad():
            raise RuntimeError("oops")

        try:
            bad()
        except RuntimeError:
            pass
        kw = self.mock_logger.log_request.call_args.kwargs
        self.assertEqual(kw["status_code"], 500)

    def test_exception_message_included_in_log(self):
        mw = self._make_mw()

        @mw.wrap_request("/err")
        def bad():
            raise RuntimeError("specific-error-msg")

        try:
            bad()
        except RuntimeError:
            pass
        kw = self.mock_logger.log_request.call_args.kwargs
        self.assertIn("specific-error-msg", kw["message"])

    def test_exception_logs_error_level(self):
        mw = self._make_mw()

        @mw.wrap_request("/err")
        def bad():
            raise Exception("fail")

        try:
            bad()
        except Exception:
            pass
        kw = self.mock_logger.log_request.call_args.kwargs
        self.assertEqual(kw["level"], "error")

    # --- context lifecycle ---

    def test_context_cleaned_up_after_success(self):
        mw = self._make_mw()

        @mw.wrap_request("/ctx")
        def handler():
            return None

        handler()
        self.assertEqual(len(mw._request_contexts), 0)

    def test_context_cleaned_up_after_error(self):
        mw = self._make_mw()

        @mw.wrap_request("/ctx-err")
        def bad():
            raise Exception("fail")

        try:
            bad()
        except Exception:
            pass
        self.assertEqual(len(mw._request_contexts), 0)

    # --- request body size ---

    def test_body_kwarg_size_measured(self):
        mw = self._make_mw()

        @mw.wrap_request("/body")
        def handler(**kwargs):
            return None

        handler(body="hello world")
        kw = self.mock_logger.log_request.call_args.kwargs
        self.assertGreater(kw["request_size"], 0)


# ---------------------------------------------------------------------------
# _get_client_ip
# ---------------------------------------------------------------------------


class TestGetClientIp(unittest.TestCase):
    def _make_mw(self):
        return LoggingMiddleware(MagicMock(spec=RequestLogger))

    def test_from_remote_addr_in_args(self):
        class FakeRequest:
            remote_addr = "192.168.1.10"

        mw = self._make_mw()
        self.assertEqual(mw._get_client_ip((FakeRequest(),), {}), "192.168.1.10")

    def test_from_client_host_in_args(self):
        class FakeClient:
            host = "10.0.0.99"

        class FakeRequest:
            client = FakeClient()

        mw = self._make_mw()
        self.assertEqual(mw._get_client_ip((FakeRequest(),), {}), "10.0.0.99")

    def test_from_remote_addr_in_kwargs(self):
        class FakeRequest:
            remote_addr = "172.16.0.5"

        mw = self._make_mw()
        self.assertEqual(mw._get_client_ip((), {"req": FakeRequest()}), "172.16.0.5")

    def test_returns_none_when_no_request_object(self):
        mw = self._make_mw()
        self.assertIsNone(mw._get_client_ip((42, "string"), {"key": "value"}))

    def test_returns_none_for_empty_args(self):
        mw = self._make_mw()
        self.assertIsNone(mw._get_client_ip((), {}))


# ---------------------------------------------------------------------------
# LoggingMiddleware audit log helpers
# ---------------------------------------------------------------------------


class TestLogCompileAudit(unittest.TestCase):
    def _make_mw(self):
        self.mock_logger = MagicMock(spec=RequestLogger)
        return LoggingMiddleware(self.mock_logger)

    def test_calls_log_request_once(self):
        mw = self._make_mw()
        audit = create_compile_audit(
            request_id="req-ca",
            input_block_count=5,
            input_blocks_by_type={BlockType.INSTRUCTION: 5},
            input_total_size=1000,
        )
        audit.output_block_count = 3
        audit.compression_ratio = 0.6
        audit.total_latency_ms = 50.0
        mw.log_compile_audit(audit)
        self.mock_logger.log_request.assert_called_once()

    def test_endpoint_is_compile(self):
        mw = self._make_mw()
        audit = create_compile_audit("r1", 2, {BlockType.KNOWLEDGE: 2}, 400)
        audit.output_block_count = 1
        audit.compression_ratio = 0.5
        audit.total_latency_ms = 10.0
        mw.log_compile_audit(audit)
        kw = self.mock_logger.log_request.call_args.kwargs
        self.assertEqual(kw["endpoint"], "/compile")

    def test_message_contains_block_counts(self):
        mw = self._make_mw()
        audit = create_compile_audit("r2", 4, {BlockType.EXAMPLE: 4}, 800)
        audit.output_block_count = 2
        audit.compression_ratio = 0.5
        audit.total_latency_ms = 20.0
        mw.log_compile_audit(audit)
        kw = self.mock_logger.log_request.call_args.kwargs
        self.assertIn("4", kw["message"])
        self.assertIn("2", kw["message"])


class TestLogCacheAudit(unittest.TestCase):
    def _make_mw(self):
        self.mock_logger = MagicMock(spec=RequestLogger)
        return LoggingMiddleware(self.mock_logger)

    def test_calls_log_request_once(self):
        mw = self._make_mw()
        audit = create_cache_audit("req-cache", "get", "b-001")
        mw.log_cache_audit(audit)
        self.mock_logger.log_request.assert_called_once()

    def test_hit_message_contains_hit(self):
        mw = self._make_mw()
        audit = create_cache_audit("req-cache-hit", "get", "b-002")
        audit.cache_hit = True
        mw.log_cache_audit(audit)
        kw = self.mock_logger.log_request.call_args.kwargs
        self.assertIn("hit", kw["message"])

    def test_miss_message_contains_miss(self):
        mw = self._make_mw()
        audit = create_cache_audit("req-cache-miss", "get", "b-003")
        audit.cache_hit = False
        mw.log_cache_audit(audit)
        kw = self.mock_logger.log_request.call_args.kwargs
        self.assertIn("miss", kw["message"])


class TestLogMetricsAudit(unittest.TestCase):
    def _make_mw(self):
        self.mock_logger = MagicMock(spec=RequestLogger)
        return LoggingMiddleware(self.mock_logger)

    def test_calls_log_request_once(self):
        mw = self._make_mw()
        audit = MetricsAudit(
            request_id="req-m",
            timestamp="2026-04-12T12:00:00Z",
            aggregation_window="1h",
            data_points_returned=12,
        )
        mw.log_metrics_audit(audit)
        self.mock_logger.log_request.assert_called_once()

    def test_endpoint_is_metrics(self):
        mw = self._make_mw()
        audit = MetricsAudit(
            request_id="req-m2",
            timestamp="2026-04-12T12:00:00Z",
            aggregation_window="6h",
        )
        mw.log_metrics_audit(audit)
        kw = self.mock_logger.log_request.call_args.kwargs
        self.assertEqual(kw["endpoint"], "/metrics")

    def test_message_contains_window(self):
        mw = self._make_mw()
        audit = MetricsAudit(
            request_id="req-m3",
            timestamp="2026-04-12T12:00:00Z",
            aggregation_window="24h",
            data_points_returned=48,
        )
        mw.log_metrics_audit(audit)
        kw = self.mock_logger.log_request.call_args.kwargs
        self.assertIn("24h", kw["message"])

    def test_message_contains_data_point_count(self):
        mw = self._make_mw()
        audit = MetricsAudit(
            request_id="req-m4",
            timestamp="2026-04-12T12:00:00Z",
            aggregation_window="1h",
            data_points_returned=7,
        )
        mw.log_metrics_audit(audit)
        kw = self.mock_logger.log_request.call_args.kwargs
        self.assertIn("7", kw["message"])


if __name__ == "__main__":
    unittest.main()
