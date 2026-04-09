"""
Unit tests for tokenpak/metering.py.

Covers:
- UsageRecord dataclass defaults
- UsageMeter: record, get_daily_summary (single/multi model, by_type, empty)
- UsageMeter: cleanup_old_data
- UsageMeterManager: get_meter, record_usage, get_daily_summary
- Edge cases: zero tokens, large values, unknown models
"""

import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tokenpak.metering import UsageMeter, UsageMeterManager, UsageRecord


class TestUsageRecord(unittest.TestCase):

    def test_defaults_set(self):
        rec = UsageRecord(
            model="claude-sonnet-4-6",
            input_tokens=100,
            output_tokens=10,
            saved_tokens=5,
            request_type="chat",
        )
        self.assertEqual(rec.model, "claude-sonnet-4-6")
        self.assertIsNotNone(rec.timestamp)

    def test_auto_timestamp_is_iso_format(self):
        rec = UsageRecord(
            model="m", input_tokens=0, output_tokens=0, saved_tokens=0, request_type="chat"
        )
        # Should parse without error
        datetime.fromisoformat(rec.timestamp)

    def test_explicit_timestamp_preserved(self):
        ts = "2026-03-27T00:00:00+00:00"
        rec = UsageRecord(
            model="m", input_tokens=0, output_tokens=0, saved_tokens=0,
            request_type="chat", timestamp=ts
        )
        self.assertEqual(rec.timestamp, ts)


class TestUsageMeter(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "usage.db"
        self.meter = UsageMeter(key_id="test-key", db_path=self.db_path)
        self.today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def tearDown(self):
        self._tmpdir.cleanup()

    def _record_sync(self, model="claude-sonnet-4-6", input_tokens=100,
                     output_tokens=10, saved_tokens=5, request_type="chat"):
        """Record and wait for async background thread to finish."""
        self.meter.record(model, input_tokens, output_tokens, saved_tokens, request_type)
        time.sleep(0.1)

    def test_empty_summary(self):
        summary = self.meter.get_daily_summary(self.today)
        self.assertEqual(summary["total_requests"], 0)
        self.assertEqual(summary["by_model"], {})
        self.assertEqual(summary["by_type"], {})
        self.assertEqual(summary["key_id"], "test-key")

    def test_single_record(self):
        self._record_sync(model="claude-sonnet-4-6", input_tokens=500,
                          output_tokens=50, saved_tokens=25, request_type="chat")
        summary = self.meter.get_daily_summary(self.today)
        self.assertEqual(summary["total_requests"], 1)
        self.assertEqual(summary["total_input_tokens"], 500)
        self.assertEqual(summary["total_output_tokens"], 50)
        self.assertEqual(summary["total_saved_tokens"], 25)

    def test_multi_record_accumulation(self):
        self._record_sync(input_tokens=100, output_tokens=10, saved_tokens=5)
        self._record_sync(input_tokens=200, output_tokens=20, saved_tokens=10)
        summary = self.meter.get_daily_summary(self.today)
        self.assertEqual(summary["total_requests"], 2)
        self.assertEqual(summary["total_input_tokens"], 300)
        self.assertEqual(summary["total_output_tokens"], 30)
        self.assertEqual(summary["total_saved_tokens"], 15)

    def test_by_model_breakdown(self):
        self._record_sync(model="claude-sonnet-4-6", input_tokens=100, output_tokens=10, saved_tokens=5)
        self._record_sync(model="claude-haiku-4-5", input_tokens=50, output_tokens=5, saved_tokens=2)
        summary = self.meter.get_daily_summary(self.today)
        self.assertIn("claude-sonnet-4-6", summary["by_model"])
        self.assertIn("claude-haiku-4-5", summary["by_model"])
        self.assertEqual(summary["by_model"]["claude-sonnet-4-6"]["requests"], 1)
        self.assertEqual(summary["by_model"]["claude-haiku-4-5"]["input_tokens"], 50)

    def test_by_type_breakdown(self):
        self._record_sync(request_type="chat", input_tokens=100, output_tokens=10, saved_tokens=0)
        self._record_sync(request_type="completion", input_tokens=50, output_tokens=5, saved_tokens=0)
        summary = self.meter.get_daily_summary(self.today)
        self.assertIn("chat", summary["by_type"])
        self.assertIn("completion", summary["by_type"])
        self.assertEqual(summary["by_type"]["chat"]["requests"], 1)

    def test_zero_tokens(self):
        self._record_sync(input_tokens=0, output_tokens=0, saved_tokens=0)
        summary = self.meter.get_daily_summary(self.today)
        self.assertEqual(summary["total_requests"], 1)
        self.assertEqual(summary["total_input_tokens"], 0)

    def test_large_token_values(self):
        large = 10_000_000
        self._record_sync(input_tokens=large, output_tokens=large, saved_tokens=large)
        summary = self.meter.get_daily_summary(self.today)
        self.assertEqual(summary["total_input_tokens"], large)

    def test_unknown_model_handled(self):
        self._record_sync(model="unknown-model-xyz", input_tokens=10, output_tokens=1, saved_tokens=0)
        summary = self.meter.get_daily_summary(self.today)
        self.assertIn("unknown-model-xyz", summary["by_model"])

    def test_different_date_not_included(self):
        self._record_sync(input_tokens=100, output_tokens=10, saved_tokens=0)
        summary = self.meter.get_daily_summary("2020-01-01")
        self.assertEqual(summary["total_requests"], 0)

    def test_cleanup_old_data_removes_rows(self):
        # Insert an old record directly with a past timestamp
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO usage (key_id, timestamp, model, input_tokens, output_tokens, saved_tokens, request_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("test-key", "2020-01-01T00:00:00+00:00", "old-model", 100, 10, 0, "chat"),
            )
            conn.commit()
        deleted = self.meter.cleanup_old_data(days=30)
        self.assertGreaterEqual(deleted, 1)

    def test_cleanup_preserves_recent_data(self):
        self._record_sync(input_tokens=100, output_tokens=10, saved_tokens=0)
        deleted = self.meter.cleanup_old_data(days=90)
        self.assertEqual(deleted, 0)
        summary = self.meter.get_daily_summary(self.today)
        self.assertEqual(summary["total_requests"], 1)

    def test_concurrent_records_safe(self):
        """Multiple threads recording simultaneously should not corrupt DB."""
        threads = []
        for _ in range(10):
            t = threading.Thread(
                target=lambda: self.meter.record(
                    "claude-sonnet-4-6", 100, 10, 5, "chat"
                )
            )
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        time.sleep(0.3)
        summary = self.meter.get_daily_summary(self.today)
        self.assertEqual(summary["total_requests"], 10)


class TestUsageMeterManager(unittest.TestCase):

    def setUp(self):
        # Reset singleton for isolated tests
        UsageMeterManager._instance = None
        self._tmpdir = tempfile.TemporaryDirectory()
        self.manager = UsageMeterManager()
        self.today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def tearDown(self):
        UsageMeterManager._instance = None
        self._tmpdir.cleanup()

    def test_get_meter_returns_same_instance(self):
        m1 = self.manager.get_meter("key-a")
        m2 = self.manager.get_meter("key-a")
        self.assertIs(m1, m2)

    def test_different_keys_get_different_meters(self):
        m1 = self.manager.get_meter("key-a")
        m2 = self.manager.get_meter("key-b")
        self.assertIsNot(m1, m2)

    def test_record_usage_delegates_to_meter(self):
        before = self.manager.get_daily_summary("key-x", self.today)["total_requests"]
        self.manager.record_usage(
            key_id="key-x",
            model="claude-sonnet-4-6",
            input_tokens=200,
            output_tokens=20,
            saved_tokens=10,
            request_type="chat",
        )
        time.sleep(0.1)
        summary = self.manager.get_daily_summary("key-x", self.today)
        self.assertEqual(summary["total_requests"], before + 1)

    def test_report_all_with_no_server(self):
        """report_all should return False for non-reachable server, not crash."""
        self.manager.record_usage("key-r", "m", 10, 1, 0, "chat")
        time.sleep(0.1)
        results = self.manager.report_all("http://localhost:19999")
        self.assertIn("key-r", results)
        self.assertFalse(results["key-r"])


if __name__ == "__main__":
    unittest.main()
