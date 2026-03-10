"""
Integration tests for logging middleware.
"""

import pytest
import tempfile
from tokenpak.middleware.logger import RequestLogger, LoggingConfig
from tokenpak.middleware.logging_middleware import LoggingMiddleware
from tokenpak.middleware.audit_trail import (
    create_compile_audit,
    create_cache_audit,
    create_metrics_audit,
    BlockType,
)


class TestLoggingMiddleware:
    """Test LoggingMiddleware."""
    
    def test_middleware_creation(self):
        """Test creating middleware."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = LoggingConfig(log_dir=tmpdir)
            logger = RequestLogger(config)
            middleware = LoggingMiddleware(logger)
            
            assert middleware.logger == logger
            logger.stop()
    
    def test_wrap_request_success(self):
        """Test wrapping a successful request."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = LoggingConfig(log_dir=tmpdir)
            logger = RequestLogger(config)
            middleware = LoggingMiddleware(logger)
            
            @middleware.wrap_request("/compile", "POST")
            def handler(body):
                return {"result": "compressed"}, 200
            
            result = handler({"blocks": 10})
            
            assert result[0]["result"] == "compressed"
            assert result[1] == 200
            
            logger.stop()
    
    def test_wrap_request_error(self):
        """Test wrapping a request that raises an error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = LoggingConfig(log_dir=tmpdir)
            logger = RequestLogger(config)
            middleware = LoggingMiddleware(logger)
            
            @middleware.wrap_request("/compile", "POST")
            def handler(body):
                raise ValueError("Invalid input")
            
            with pytest.raises(ValueError):
                handler({"blocks": 10})
            
            logger.stop()
    
    def test_log_compile_audit(self):
        """Test logging a compile audit trail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = LoggingConfig(log_dir=tmpdir)
            logger = RequestLogger(config)
            middleware = LoggingMiddleware(logger)
            
            audit = create_compile_audit(
                request_id="req-123",
                input_block_count=20,
                input_blocks_by_type={BlockType.INSTRUCTION: 5},
                input_total_size=50000,
            )
            
            middleware.log_compile_audit(audit)
            logger.stop()
    
    def test_log_cache_audit(self):
        """Test logging a cache audit trail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = LoggingConfig(log_dir=tmpdir)
            logger = RequestLogger(config)
            middleware = LoggingMiddleware(logger)
            
            audit = create_cache_audit(
                request_id="req-123",
                operation="get",
                block_id="block-1",
            )
            
            middleware.log_cache_audit(audit)
            logger.stop()
    
    def test_log_metrics_audit(self):
        """Test logging a metrics audit trail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = LoggingConfig(log_dir=tmpdir)
            logger = RequestLogger(config)
            middleware = LoggingMiddleware(logger)
            
            audit = create_metrics_audit(
                request_id="req-123",
                aggregation_window="1h",
            )
            
            middleware.log_metrics_audit(audit)
            logger.stop()


class TestPerformanceOverhead:
    """Test that logging doesn't add significant overhead."""
    
    def test_logging_latency_minimal(self):
        """Test that logging adds minimal latency."""
        import time
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config = LoggingConfig(log_dir=tmpdir, destination="stdout")
            logger = RequestLogger(config)
            middleware = LoggingMiddleware(logger)
            
            @middleware.wrap_request("/test", "GET")
            def handler():
                time.sleep(0.001)  # 1ms work
                return {"result": "ok"}, 200
            
            start = time.time()
            for _ in range(100):
                handler()
            elapsed_ms = (time.time() - start) * 1000
            
            # 100 requests, ~1ms each + logging overhead should be < 200ms
            assert elapsed_ms < 200
            
            logger.stop()
