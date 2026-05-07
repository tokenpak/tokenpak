"""
Tests for usage metering system.
"""


import pytest

pytest.importorskip("tokenpak.metering", reason="module not available in current build")
import sqlite3
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from tokenpak.metering import UsageMeter, UsageMeterManager, UsageRecord


@pytest.fixture
def temp_db():
    """Create temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_usage.db"
        yield db_path


@pytest.fixture
def meter(temp_db):
    """Create meter instance with temp database."""
    return UsageMeter("test-key-123", db_path=temp_db)


class TestUsageRecord:
    """Test UsageRecord data class."""

    def test_create_record(self):
        """Test creating a usage record."""
        record = UsageRecord(
            model="claude-sonnet",
            input_tokens=1000,
            output_tokens=200,
            saved_tokens=50,
            request_type="chat"
        )
        assert record.model == "claude-sonnet"
        assert record.input_tokens == 1000
        assert record.timestamp is not None

    def test_record_timestamp_auto(self):
        """Test that timestamp is auto-set if not provided."""
        record = UsageRecord(
            model="gpt-4",
            input_tokens=100,
            output_tokens=50,
            saved_tokens=10,
            request_type="completion"
        )
        assert record.timestamp is not None
        assert isinstance(record.timestamp, str)
        # Should be ISO format
        datetime.fromisoformat(record.timestamp)


class TestUsageMeter:
    """Test UsageMeter class."""

    def test_init_creates_database(self, temp_db):
        """Test that init creates database."""
        meter = UsageMeter("test-key", db_path=temp_db)
        assert temp_db.exists()

    def test_schema_created(self, meter):
        """Test that schema is properly created."""
        # Check table exists
        with sqlite3.connect(meter.db_path) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='usage'"
            )
            assert cursor.fetchone() is not None

    def test_record_single_request(self, meter):
        """Test recording a single request."""
        meter.record(
            model="claude-sonnet",
            input_tokens=1000,
            output_tokens=200,
            saved_tokens=50,
            request_type="chat"
        )

        # Wait a moment for async insert
        import time
        time.sleep(0.1)

        # Verify data was recorded
        with sqlite3.connect(meter.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM usage")
            count = cursor.fetchone()[0]
            assert count == 1

    def test_record_multiple_requests(self, meter):
        """Test recording multiple requests."""
        for i in range(5):
            meter.record(
                model="claude-sonnet" if i % 2 == 0 else "claude-opus",
                input_tokens=1000 + (i * 100),
                output_tokens=200 + (i * 20),
                saved_tokens=50 + (i * 5),
                request_type="chat"
            )

        import time
        time.sleep(0.2)

        with sqlite3.connect(meter.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM usage")
            count = cursor.fetchone()[0]
            assert count == 5

    def test_get_daily_summary_empty(self, meter):
        """Test summary for empty day."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        summary = meter.get_daily_summary(today)

        assert summary["total_requests"] == 0
        assert summary["total_input_tokens"] == 0
        assert summary["by_model"] == {}

    def test_get_daily_summary_single_model(self, meter):
        """Test summary aggregation for single model."""
        meter.record("claude-sonnet", 1000, 200, 50, "chat")
        meter.record("claude-sonnet", 2000, 300, 100, "chat")
        meter.record("claude-sonnet", 1500, 250, 75, "completion")

        import time
        time.sleep(0.2)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        summary = meter.get_daily_summary(today)

        assert summary["total_requests"] == 3
        assert summary["total_input_tokens"] == 4500
        assert summary["total_output_tokens"] == 750
        assert summary["total_saved_tokens"] == 225
        assert "claude-sonnet" in summary["by_model"]
        assert summary["by_model"]["claude-sonnet"]["requests"] == 3

    def test_get_daily_summary_multiple_models(self, meter):
        """Test summary with multiple models."""
        meter.record("claude-sonnet", 1000, 200, 50, "chat")
        meter.record("claude-opus", 2000, 400, 100, "chat")
        meter.record("gpt-4", 1500, 300, 75, "completion")

        import time
        time.sleep(0.2)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        summary = meter.get_daily_summary(today)

        assert summary["total_requests"] == 3
        assert len(summary["by_model"]) == 3
        assert summary["by_model"]["claude-sonnet"]["input_tokens"] == 1000
        assert summary["by_model"]["claude-opus"]["input_tokens"] == 2000
        assert summary["by_model"]["gpt-4"]["input_tokens"] == 1500

    def test_get_daily_summary_by_type(self, meter):
        """Test summary aggregation by request type."""
        meter.record("claude-sonnet", 1000, 200, 50, "chat")
        meter.record("claude-sonnet", 2000, 300, 100, "chat")
        meter.record("claude-sonnet", 1500, 250, 75, "completion")

        import time
        time.sleep(0.2)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        summary = meter.get_daily_summary(today)

        assert summary["by_type"]["chat"]["requests"] == 2
        assert summary["by_type"]["chat"]["input_tokens"] == 3000
        assert summary["by_type"]["completion"]["requests"] == 1
        assert summary["by_type"]["completion"]["input_tokens"] == 1500

    @patch('tokenpak.metering.requests.post')
    def test_report_to_server_success(self, mock_post, meter):
        """Test successful report to server."""
        # Record some data
        meter.record("claude-sonnet", 1000, 200, 50, "chat")

        import time
        time.sleep(0.1)

        # Mock server response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # Report
        success = meter.report_to_server("http://localhost:8900")

        assert success is True
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "localhost:8900/usage" in call_args[0][0]

    @patch('tokenpak.metering.requests.post')
    def test_report_to_server_no_data(self, mock_post, meter):
        """Test report when no unreported data."""
        success = meter.report_to_server("http://localhost:8900")

        assert success is True
        mock_post.assert_not_called()

    @patch('tokenpak.metering.requests.post')
    def test_report_to_server_network_error(self, mock_post, meter):
        """Test handling of network error."""
        meter.record("claude-sonnet", 1000, 200, 50, "chat")

        import time
        time.sleep(0.1)

        # Mock network error
        mock_post.side_effect = requests.ConnectionError("Network unreachable")

        success = meter.report_to_server("http://localhost:8900")

        assert success is False

    @patch('tokenpak.metering.requests.post')
    def test_report_marks_as_reported(self, mock_post, meter):
        """Test that successful report marks rows as reported."""
        meter.record("claude-sonnet", 1000, 200, 50, "chat")

        import time
        time.sleep(0.1)

        # Mock successful response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # Before report
        with sqlite3.connect(meter.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM usage WHERE reported = 0")
            unreported_before = cursor.fetchone()[0]
        assert unreported_before == 1

        # Report
        meter.report_to_server("http://localhost:8900")

        # After report
        with sqlite3.connect(meter.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM usage WHERE reported = 0")
            unreported_after = cursor.fetchone()[0]
        assert unreported_after == 0

    def test_cleanup_old_data(self, meter):
        """Test cleanup of old usage data."""
        # Record data
        meter.record("claude-sonnet", 1000, 200, 50, "chat")

        import time
        time.sleep(0.1)

        # Manually insert old data (90+ days ago)
        old_date = (datetime.now(timezone.utc) - timedelta(days=95)).isoformat()
        with sqlite3.connect(meter.db_path) as conn:
            conn.execute(
                "INSERT INTO usage (key_id, timestamp, model, input_tokens, output_tokens, saved_tokens, request_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("test-key-123", old_date, "old-model", 100, 20, 5, "chat")
            )
            conn.commit()

        # Verify we have 2 rows
        with sqlite3.connect(meter.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM usage")
            assert cursor.fetchone()[0] == 2

        # Cleanup
        deleted = meter.cleanup_old_data(days=90)

        assert deleted == 1

        # Verify only recent data remains
        with sqlite3.connect(meter.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM usage")
            assert cursor.fetchone()[0] == 1


class TestUsageMeterManager:
    """Test UsageMeterManager singleton."""

    @pytest.fixture(autouse=True)
    def reset_manager(self):
        """Reset manager singleton before each test."""
        UsageMeterManager._instance = None
        UsageMeterManager._lock = threading.Lock()
        yield
        UsageMeterManager._instance = None
        UsageMeterManager._lock = threading.Lock()

    def test_singleton(self):
        """Test that manager is singleton."""
        manager1 = UsageMeterManager()
        manager2 = UsageMeterManager()
        assert manager1 is manager2

    def test_get_meter(self):
        """Test getting/creating meters."""
        manager = UsageMeterManager()

        meter1 = manager.get_meter("key1")
        meter2 = manager.get_meter("key1")
        assert meter1 is meter2

        meter3 = manager.get_meter("key2")
        assert meter3 is not meter1

    def test_record_usage(self):
        """Test recording usage through manager."""
        manager = UsageMeterManager()
        key_id = f"manager-test-key-{id(manager)}"  # Unique per manager instance

        manager.record_usage(
            key_id,
            "claude-sonnet",
            1000,
            200,
            50,
            "chat"
        )

        import time
        time.sleep(0.1)

        meter = manager.get_meter(key_id)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        summary = meter.get_daily_summary(today)

        assert summary["total_requests"] == 1
        assert summary["total_input_tokens"] == 1000

    @patch('tokenpak.metering.requests.post')
    def test_report_all(self, mock_post):
        """Test reporting all meters."""
        manager = UsageMeterManager()

        manager.record_usage("key1", "claude-sonnet", 1000, 200, 50, "chat")
        manager.record_usage("key2", "claude-opus", 2000, 400, 100, "chat")

        import time
        time.sleep(0.2)

        # Mock successful response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        results = manager.report_all("http://localhost:8900")

        assert "key1" in results
        assert "key2" in results
        assert results["key1"] is True
        assert results["key2"] is True


class TestIntegration:
    """Integration tests."""

    def test_end_to_end_recording_and_summary(self, meter):
        """Test full flow: record → summarize."""
        # Simulate a day of requests
        # i=0: 1000, i=1: 1100, i=2: 1200, ..., i=9: 1900
        # Sum = 1000 + 1100 + 1200 + ... + 1900 = 10 * (1000 + 1900) / 2 = 14500
        for i in range(10):
            meter.record(
                "claude-sonnet",
                1000 + (i * 100),
                200 + (i * 20),
                50 + (i * 5),
                "chat"
            )

        import time
        time.sleep(0.3)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        summary = meter.get_daily_summary(today)

        assert summary["total_requests"] == 10
        assert summary["total_input_tokens"] == 14500  # sum of 1000, 1100, ..., 1900
        assert summary["by_model"]["claude-sonnet"]["requests"] == 10

    @patch('tokenpak.metering.requests.post')
    def test_end_to_end_with_report(self, mock_post, meter):
        """Test full flow including reporting."""
        # Record data
        meter.record("claude-sonnet", 1000, 200, 50, "chat")
        meter.record("claude-opus", 2000, 300, 100, "chat")

        import time
        time.sleep(0.2)

        # Mock successful response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # Report
        success = meter.report_to_server("http://localhost:8900")

        assert success is True

        # Verify payload
        call_args = mock_post.call_args
        payload = call_args[1]["json"]

        assert payload["key_id"] == "test-key-123"
        assert len(payload["usage"]) == 1  # 1 day of data
        assert payload["usage"][0]["total_requests"] == 2
        assert payload["usage"][0]["total_input_tokens"] == 3000
