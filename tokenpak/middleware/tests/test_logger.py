"""
Unit tests for logger module.
"""

import json
import tempfile
from pathlib import Path

from tokenpak.middleware.logger import (
    AsyncLogger,
    LoggingConfig,
    LogRecord,
    RequestLogger,
    init_logger,
)


class TestLogRecord:
    """Test LogRecord data class."""

    def test_log_record_creation(self):
        """Test creating a log record."""
        record = LogRecord(
            timestamp="2026-03-10T06:00:00Z",
            request_id="test-123",
            level="info",
            endpoint="/compile",
            client_ip="127.0.0.1",
            method="POST",
            status_code=200,
            request_size=1000,
            response_size=500,
            latency_ms=45.5,
            compression_ratio=0.5,
            message="Compilation successful",
            context={"blocks": 10},
        )

        assert record.request_id == "test-123"
        assert record.status_code == 200
        assert record.compression_ratio == 0.5

    def test_log_record_to_json(self):
        """Test converting log record to JSON."""
        record = LogRecord(
            timestamp="2026-03-10T06:00:00Z",
            request_id="test-123",
            level="info",
            endpoint="/compile",
            client_ip="127.0.0.1",
            method="POST",
            status_code=200,
            request_size=1000,
            response_size=500,
            latency_ms=45.5,
            compression_ratio=0.5,
            message="Test",
            context={},
        )

        json_str = record.to_json()
        data = json.loads(json_str)

        assert data["request_id"] == "test-123"
        assert data["status_code"] == 200
        assert data["endpoint"] == "/compile"

    def test_log_record_to_text(self):
        """Test converting log record to text."""
        record = LogRecord(
            timestamp="2026-03-10T06:00:00Z",
            request_id="test-123",
            level="info",
            endpoint="/compile",
            client_ip="127.0.0.1",
            method="POST",
            status_code=200,
            request_size=1000,
            response_size=500,
            latency_ms=45.5,
            compression_ratio=0.5,
            message="Test",
            context={},
        )

        text = record.to_text()

        assert "test-123" in text
        assert "INFO" in text
        assert "/compile" in text
        assert "200" in text
        assert "45.5ms" in text


class TestLoggingConfig:
    """Test LoggingConfig."""

    def test_default_config(self):
        """Test default configuration."""
        config = LoggingConfig()

        assert config.enabled is True
        assert config.level == "info"
        assert config.destination == "file"
        assert config.retention_days == 30

    def test_resolve_log_dir_default(self):
        """Test default log directory resolution."""
        config = LoggingConfig()
        log_dir = config.resolve_log_dir()

        assert ".tokenpak" in log_dir
        assert "logs" in log_dir

    def test_resolve_log_dir_custom(self):
        """Test custom log directory."""
        custom_dir = "/tmp/custom_logs"
        config = LoggingConfig(log_dir=custom_dir)

        assert config.resolve_log_dir() == custom_dir


class TestAsyncLogger:
    """Test AsyncLogger."""

    def test_logger_creation(self):
        """Test creating an async logger."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = LoggingConfig(
                destination="file",
                log_dir=tmpdir,
            )
            logger = AsyncLogger(config)

            assert logger.config == config
            assert logger.logger is not None

            logger.stop()

    def test_log_to_file(self):
        """Test logging to file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = LoggingConfig(
                destination="file",
                log_dir=tmpdir,
                flush_interval_sec=1,
            )
            logger = AsyncLogger(config)

            record = LogRecord(
                timestamp="2026-03-10T06:00:00Z",
                request_id="test-123",
                level="info",
                endpoint="/compile",
                client_ip="127.0.0.1",
                method="POST",
                status_code=200,
                request_size=1000,
                response_size=500,
                latency_ms=45.5,
                compression_ratio=0.5,
                message="Test log entry",
                context={},
            )

            logger.log(record)
            import time
            time.sleep(2)  # Wait for flush
            logger.stop()

            # Check that file was created
            log_files = list(Path(tmpdir).glob("*.log"))
            assert len(log_files) > 0

    def test_logger_disabled(self):
        """Test disabled logger."""
        config = LoggingConfig(enabled=False)
        logger = AsyncLogger(config)

        record = LogRecord(
            timestamp="2026-03-10T06:00:00Z",
            request_id="test-123",
            level="info",
            endpoint="/compile",
            client_ip="127.0.0.1",
            method="POST",
            status_code=200,
            request_size=1000,
            response_size=500,
            latency_ms=45.5,
            compression_ratio=0.5,
            message="Test",
            context={},
        )

        # Should not raise even if disabled
        logger.log(record)
        logger.stop()


class TestRequestLogger:
    """Test RequestLogger."""

    def test_log_request(self):
        """Test logging a request."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = LoggingConfig(
                destination="file",
                log_dir=tmpdir,
            )
            logger = RequestLogger(config)

            logger.log_request(
                endpoint="/compile",
                method="POST",
                client_ip="127.0.0.1",
                request_size=1000,
                response_size=500,
                status_code=200,
                latency_ms=45.5,
                compression_ratio=0.5,
            )

            logger.stop()

    def test_log_request_generates_request_id(self):
        """Test that request ID is generated if not provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = LoggingConfig(
                destination="file",
                log_dir=tmpdir,
            )
            logger = RequestLogger(config)

            logger.log_request(
                endpoint="/compile",
                method="POST",
                client_ip="127.0.0.1",
                request_size=1000,
                response_size=500,
                status_code=200,
            )

            logger.stop()

    def test_log_with_context(self):
        """Test logging with context data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = LoggingConfig(
                destination="file",
                log_dir=tmpdir,
            )
            logger = RequestLogger(config)

            logger.log_request(
                endpoint="/compile",
                method="POST",
                status_code=200,
                context={
                    "blocks": 10,
                    "compression_ratio": 0.5,
                    "methods": ["truncation", "dedup"],
                },
            )

            logger.stop()


class TestGlobalLogger:
    """Test global logger initialization."""

    def test_init_logger(self):
        """Test initializing global logger."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = LoggingConfig(log_dir=tmpdir)
            logger = init_logger(config)

            assert logger is not None
            logger.stop()

    def test_get_logger(self):
        """Test getting initialized logger."""
        from tokenpak.middleware.logger import get_logger

        with tempfile.TemporaryDirectory() as tmpdir:
            config = LoggingConfig(log_dir=tmpdir)
            init_logger(config)

            logger = get_logger()
            assert logger is not None
            logger.stop()
