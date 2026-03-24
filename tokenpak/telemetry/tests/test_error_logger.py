"""Tests for error telemetry logger."""

import pytest
import json
import datetime
from pathlib import Path
from tokenpak.telemetry.error_logger import (
    log_error, get_error_summary, get_error_logs, 
    clear_error_metrics, ErrorRecord, LOGS_DIR
)


@pytest.fixture
def cleanup_logs():
    """Clean up test logs."""
    yield
    for log_file in LOGS_DIR.glob("errors-*.jsonl"):
        log_file.unlink(missing_ok=True)
    clear_error_metrics()


class TestErrorLogging:
    """Test error logging functionality."""
    
    def test_log_valid_exception(self, cleanup_logs):
        """Test logging a basic exception."""
        try:
            raise ValueError("Test error")
        except ValueError as e:
            log_error(e, request_id="req-123", model="gpt-4")
        
        logs = get_error_logs()
        assert len(logs) == 1
        assert logs[0]["error_type"] == "ValueError"
        assert logs[0]["message"] == "Test error"
    
    def test_log_with_full_context(self, cleanup_logs):
        """Test logging with complete context."""
        try:
            raise RuntimeError("Provider timeout")
        except RuntimeError as e:
            log_error(e, request_id="req-456", model="claude-opus", 
                     provider="Anthropic", input_size=2048, cost_estimate=0.015)
        
        logs = get_error_logs()
        context = logs[0]["context"]
        assert context["provider"] == "Anthropic"
        assert context["input_size"] == 2048
        assert context["cost_estimate"] == 0.015
    
    def test_error_summary(self, cleanup_logs):
        """Test error count summary by type."""
        for i in range(3):
            try:
                raise ValueError(f"Error {i}")
            except ValueError as e:
                log_error(e)
        
        for i in range(2):
            try:
                raise RuntimeError(f"Runtime {i}")
            except RuntimeError as e:
                log_error(e)
        
        summary = get_error_summary()
        assert summary["ValueError"] == 3
        assert summary["RuntimeError"] == 2
    
    def test_missing_context_fields(self, cleanup_logs):
        """Test logging without all context fields."""
        try:
            raise TypeError("Type mismatch")
        except TypeError as e:
            log_error(e)  # No optional context
        
        logs = get_error_logs()
        assert len(logs) == 1
        assert logs[0]["error_type"] == "TypeError"
        assert logs[0]["context"] == {}
    
    def test_error_record_dataclass(self):
        """Test ErrorRecord serialization."""
        record = ErrorRecord(
            timestamp="2026-03-23T15:30:00",
            error_type="ValueError",
            message="Test error",
            stack_trace="Traceback...",
            context={"model": "gpt-4", "request_id": "test-123"}
        )
        
        jsonl = record.to_jsonl()
        parsed = json.loads(jsonl)
        
        assert parsed["error_type"] == "ValueError"
        assert parsed["context"]["model"] == "gpt-4"
