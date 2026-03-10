"""
tests/test_request_logging.py

Tests for tokenpak.monitoring.request_logger and tokenpak.monitoring.audit_trail.

Coverage targets:
  - Request ID generation (UUID + X-Request-ID honour)
  - JSON serialisation of RequestLogRecord
  - Log level filtering (debug < info < warn)
  - File rotation logic (_FileWriter)
  - AuditTrail recording and flush
  - Integration: log_request() module-level helper
  - Performance: logging overhead < 5 ms per call
"""
from __future__ import annotations

import json
import queue
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from tokenpak.monitoring.request_logger import (
    LEVEL_DEBUG,
    LEVEL_INFO,
    LEVEL_WARN,
    RequestLogRecord,
    RequestLogger,
    _FileWriter,
    log_request,
    new_request_id,
)
from tokenpak.monitoring.audit_trail import AuditTrail


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _CapturingWriter:
    """In-memory writer that captures lines for test assertions."""

    def __init__(self):
        self.lines: List[str] = []
        self._lock = threading.Lock()

    def write(self, line: str) -> None:
        with self._lock:
            self.lines.append(line)

    def close(self) -> None:
        pass


def _make_logger(writer=None, level=LEVEL_DEBUG) -> RequestLogger:
    """Build a RequestLogger with an in-memory writer (no disk I/O)."""
    rl = RequestLogger(config={"enabled": True, "level": level, "destination": "stdout"})
    if writer is not None:
        rl._writer = writer
    return rl


# ---------------------------------------------------------------------------
# 1. Request ID generation
# ---------------------------------------------------------------------------

class TestRequestIdGeneration:
    def test_generates_uuid_without_headers(self):
        rid = new_request_id()
        assert len(rid) == 36  # UUID v4 format
        uuid.UUID(rid)  # raises ValueError if invalid

    def test_unique_ids(self):
        ids = {new_request_id() for _ in range(100)}
        assert len(ids) == 100

    def test_honours_x_request_id_header(self):
        custom = "my-custom-id-123"
        rid = new_request_id({"X-Request-ID": custom})
        assert rid == custom

    def test_honours_lowercase_x_request_id(self):
        custom = "lowercase-id-456"
        rid = new_request_id({"x-request-id": custom})
        assert rid == custom

    def test_ignores_other_headers(self):
        rid = new_request_id({"Authorization": "Bearer token"})
        uuid.UUID(rid)  # should be a new UUID


# ---------------------------------------------------------------------------
# 2. JSON serialisation
# ---------------------------------------------------------------------------

class TestRequestLogRecordJson:
    def _make_record(self, **kwargs):
        defaults = dict(
            request_id="test-id-1",
            timestamp="2026-01-01T00:00:00Z",
            level=LEVEL_INFO,
            client_ip="127.0.0.1",
            method="POST",
            endpoint="/v1/chat/completions",
            request_body_size=1024,
            response_status=200,
            response_body_size=512,
            compression_ratio=0.72,
            latency_ms=95.5,
            model="claude-3-5-sonnet",
            provider="anthropic",
        )
        defaults.update(kwargs)
        return RequestLogRecord(**defaults)

    def test_to_json_is_valid_json(self):
        r = self._make_record()
        payload = json.loads(r.to_json())
        assert isinstance(payload, dict)

    def test_required_fields_present(self):
        r = self._make_record()
        d = r.to_dict()
        for field in [
            "request_id", "timestamp", "level", "method", "endpoint",
            "request_body_size", "response_status", "response_body_size",
            "latency_ms",
        ]:
            assert field in d, f"Missing field: {field}"

    def test_compression_ratio_included(self):
        r = self._make_record(compression_ratio=0.72)
        assert abs(r.to_dict()["compression_ratio"] - 0.72) < 0.001

    def test_no_compression_ratio_when_none(self):
        r = self._make_record(compression_ratio=None)
        assert "compression_ratio" not in r.to_dict()

    def test_extra_fields_merged(self):
        r = self._make_record(extra={"custom_key": "custom_val"})
        d = r.to_dict()
        assert d["custom_key"] == "custom_val"

    def test_to_text_contains_key_fields(self):
        r = self._make_record()
        text = r.to_text()
        assert "test-id-1" in text
        assert "200" in text
        assert "95.5" in text


# ---------------------------------------------------------------------------
# 3. Log level filtering
# ---------------------------------------------------------------------------

class TestLogLevelFiltering:
    def _capture(self, min_level: str) -> tuple[RequestLogger, _CapturingWriter]:
        writer = _CapturingWriter()
        rl = _make_logger(writer=writer, level=min_level)
        return rl, writer

    def _make_record(self, level: str) -> RequestLogRecord:
        return RequestLogRecord(
            request_id=str(uuid.uuid4()),
            timestamp="2026-01-01T00:00:00Z",
            level=level,
        )

    def test_debug_level_passes_all(self):
        rl, writer = self._capture(LEVEL_DEBUG)
        rl.log(self._make_record(LEVEL_DEBUG))
        rl.log(self._make_record(LEVEL_INFO))
        rl.log(self._make_record(LEVEL_WARN))
        rl.stop()
        assert len(writer.lines) == 3

    def test_info_level_filters_debug(self):
        rl, writer = self._capture(LEVEL_INFO)
        rl.log(self._make_record(LEVEL_DEBUG))
        rl.log(self._make_record(LEVEL_INFO))
        rl.log(self._make_record(LEVEL_WARN))
        rl.stop()
        assert len(writer.lines) == 2

    def test_warn_level_filters_debug_and_info(self):
        rl, writer = self._capture(LEVEL_WARN)
        rl.log(self._make_record(LEVEL_DEBUG))
        rl.log(self._make_record(LEVEL_INFO))
        rl.log(self._make_record(LEVEL_WARN))
        rl.stop()
        assert len(writer.lines) == 1

    def test_disabled_logger_logs_nothing(self):
        writer = _CapturingWriter()
        rl = RequestLogger(config={"enabled": False, "level": LEVEL_DEBUG, "destination": "stdout"})
        rl._writer = writer
        rl.log(self._make_record(LEVEL_INFO))
        rl.stop()
        assert len(writer.lines) == 0


# ---------------------------------------------------------------------------
# 4. File rotation logic
# ---------------------------------------------------------------------------

class TestFileRotation:
    def test_creates_log_file_with_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            writer = _FileWriter(log_dir=log_dir, retention_days=30)
            writer.write('{"test": true}')
            writer.close()
            files = list(log_dir.glob("proxy-*.log"))
            assert len(files) == 1
            assert files[0].read_text().strip() == '{"test": true}'

    def test_prunes_old_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            # Create a "stale" log file with old mtime
            stale = log_dir / "proxy-2020-01-01.log"
            stale.write_text("old log")
            import os
            os.utime(stale, (0, 0))  # epoch = very old
            writer = _FileWriter(log_dir=log_dir, retention_days=1)
            writer.write("new entry")
            writer._prune_old_logs()
            writer.close()
            assert not stale.exists()

    def test_appends_to_existing_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            writer = _FileWriter(log_dir=log_dir, retention_days=30)
            writer.write("line 1")
            writer.write("line 2")
            writer.close()
            files = list(log_dir.glob("proxy-*.log"))
            lines = files[0].read_text().strip().splitlines()
            assert len(lines) == 2


# ---------------------------------------------------------------------------
# 5. AuditTrail
# ---------------------------------------------------------------------------

class TestAuditTrail:
    def test_records_compile_event(self):
        trail = AuditTrail("req-123")
        trail.record_compile(
            input_block_count=10,
            output_block_count=6,
            blocks_removed=[{"id": "kb-1", "reason": "low_relevance"}],
            compression_method="extractive",
            stage_timings={"parse": 3.0, "compile": 80.0, "render": 2.5},
        )
        assert len(trail) == 1

    def test_records_cache_event(self):
        trail = AuditTrail("req-456")
        trail.record_cache(operation="get", block_id="kb-2", hit=True, cached_size=2048)
        assert len(trail) == 1

    def test_records_metrics_event(self):
        trail = AuditTrail("req-789")
        trail.record_metrics(aggregation_window="5m", data_points_returned=42)
        assert len(trail) == 1

    def test_records_error_event(self):
        trail = AuditTrail("req-err")
        trail.record_error(error_type="TimeoutError", message="upstream timed out")
        assert len(trail) == 1

    def test_flush_enqueues_to_logger(self):
        writer = _CapturingWriter()
        rl = RequestLogger(config={"enabled": True, "level": LEVEL_DEBUG, "destination": "stdout"})
        rl._writer = writer
        # Temporarily replace singleton
        RequestLogger._instance = rl

        try:
            trail = AuditTrail("req-flush")
            trail.record_compile(input_block_count=5, output_block_count=3)
            trail.record_cache(operation="set", block_id="kb-99")
            trail.flush()
            rl.stop()
            assert len(writer.lines) == 2
            # Verify request IDs are present
            for line in writer.lines:
                d = json.loads(line)
                assert d["request_id"] == "req-flush"
        finally:
            RequestLogger._instance = None

    def test_flush_clears_events(self):
        trail = AuditTrail("req-clear")
        trail.record_compile(input_block_count=3, output_block_count=2)
        trail.flush()
        assert len(trail) == 0


# ---------------------------------------------------------------------------
# 6. Integration: log_request() helper
# ---------------------------------------------------------------------------

class TestLogRequestHelper:
    def test_log_request_does_not_raise(self):
        # Calls the singleton — just verify it doesn't throw
        log_request(
            request_id="integration-test",
            client_ip="10.0.0.1",
            method="POST",
            endpoint="/v1/chat/completions",
            request_body_size=2000,
            response_status=200,
            response_body_size=500,
            compression_ratio=0.65,
            latency_ms=120.0,
            model="gpt-4o",
            provider="openai",
        )

    def test_log_request_with_error_status(self):
        """400+ responses should be logged at WARN level."""
        log_request(
            request_id="err-req",
            response_status=401,
            latency_ms=5.0,
        )


# ---------------------------------------------------------------------------
# 7. Performance: overhead < 5 ms per log call
# ---------------------------------------------------------------------------

class TestLoggingPerformance:
    def test_log_overhead_under_5ms(self):
        """Logging enqueue must be < 5 ms per call (async queue, no blocking I/O)."""
        writer = _CapturingWriter()
        rl = _make_logger(writer=writer, level=LEVEL_INFO)

        record = rl.build_record(
            request_id="perf-test",
            client_ip="127.0.0.1",
            method="POST",
            endpoint="/v1/chat/completions",
            request_body_size=4096,
            response_status=200,
            response_body_size=1024,
            compression_ratio=0.7,
            latency_ms=100.0,
            model="claude-3-5-sonnet",
        )

        N = 1000
        t0 = time.perf_counter()
        for _ in range(N):
            rl.log(record)
        elapsed = time.perf_counter() - t0

        avg_ms = (elapsed / N) * 1000
        rl.stop()
        assert avg_ms < 5.0, f"Average log overhead {avg_ms:.3f}ms exceeds 5ms limit"

    def test_queue_does_not_block_on_full(self):
        """When queue is full, log() must drop silently (non-blocking)."""
        writer = _CapturingWriter()
        rl = RequestLogger(config={"enabled": True, "level": LEVEL_INFO, "destination": "stdout"})
        rl._writer = writer
        # Fill queue
        rl._queue = queue.Queue(maxsize=1)
        record = rl.build_record(request_id="overflow", response_status=200)

        t0 = time.perf_counter()
        for _ in range(50):
            rl.log(record)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        rl.stop(timeout=0.1)
        # Should complete near-instantly even though queue overflows
        assert elapsed_ms < 500, "log() blocked on full queue"
