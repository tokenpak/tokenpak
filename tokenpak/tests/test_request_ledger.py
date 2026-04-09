"""Unit tests for request_ledger.py — request record appending and ledger management."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from tokenpak.request_ledger import (
    REQUESTS_PATH,
    append_request,
    MAX_REQUESTS,
)


class TestAppendRequest:
    """Test suite for append_request() function."""

    def test_append_request_writes_to_file(self, tmp_path):
        """Test that append_request writes a record to the specified file."""
        ledger_path = tmp_path / "requests.jsonl"
        record = {"request_id": "req-001", "model": "gpt-4"}

        append_request(record, path=ledger_path)

        assert ledger_path.exists()
        content = ledger_path.read_text().strip()
        written_record = json.loads(content)
        assert written_record["request_id"] == "req-001"
        assert written_record["model"] == "gpt-4"

    def test_append_request_adds_timestamp_if_missing(self, tmp_path):
        """Test that append_request injects a timestamp when missing."""
        ledger_path = tmp_path / "requests.jsonl"
        record = {"request_id": "req-002"}

        append_request(record, path=ledger_path)

        content = ledger_path.read_text().strip()
        written_record = json.loads(content)
        assert "timestamp" in written_record
        # Verify it's a valid ISO 8601 timestamp
        datetime.fromisoformat(written_record["timestamp"])

    def test_append_request_preserves_existing_timestamp(self, tmp_path):
        """Test that append_request does NOT override an existing timestamp."""
        ledger_path = tmp_path / "requests.jsonl"
        custom_timestamp = "2026-01-15T10:30:00+00:00"
        record = {
            "request_id": "req-003",
            "timestamp": custom_timestamp,
        }

        append_request(record, path=ledger_path)

        content = ledger_path.read_text().strip()
        written_record = json.loads(content)
        assert written_record["timestamp"] == custom_timestamp

    def test_multiple_appends_accumulate_in_jsonl_format(self, tmp_path):
        """Test that multiple appends create valid JSONL (one JSON per line)."""
        ledger_path = tmp_path / "requests.jsonl"
        records = [
            {"request_id": "req-A", "cost": 0.01},
            {"request_id": "req-B", "cost": 0.02},
            {"request_id": "req-C", "cost": 0.03},
        ]

        for record in records:
            append_request(record, path=ledger_path)

        lines = ledger_path.read_text().strip().split("\n")
        assert len(lines) == 3
        for i, line in enumerate(lines):
            parsed = json.loads(line)
            assert parsed["request_id"] == records[i]["request_id"]

    def test_append_request_uses_default_path_when_none(self, tmp_path):
        """Test that None path defaults to REQUESTS_PATH."""
        with patch("tokenpak.request_ledger.REQUESTS_PATH", tmp_path / "default.jsonl"):
            record = {"request_id": "req-default"}
            append_request(record, path=None)

            ledger_path = tmp_path / "default.jsonl"
            assert ledger_path.exists()
            content = ledger_path.read_text().strip()
            written = json.loads(content)
            assert written["request_id"] == "req-default"

    def test_append_request_creates_parent_directory_if_missing(self, tmp_path):
        """Test that append_request creates nested directories as needed."""
        ledger_path = tmp_path / "deep" / "nested" / "dir" / "requests.jsonl"
        record = {"request_id": "req-nested"}

        append_request(record, path=ledger_path)

        assert ledger_path.exists()
        assert ledger_path.parent.exists()

    def test_append_request_with_empty_record(self, tmp_path):
        """Test that append_request handles empty records gracefully."""
        ledger_path = tmp_path / "requests.jsonl"
        record = {}

        append_request(record, path=ledger_path)

        content = ledger_path.read_text().strip()
        written = json.loads(content)
        # Should have at least a timestamp
        assert "timestamp" in written

    def test_append_request_preserves_all_record_fields(self, tmp_path):
        """Test that all fields in a record are preserved on round-trip."""
        ledger_path = tmp_path / "requests.jsonl"
        record = {
            "request_id": "req-full",
            "model": "claude-opus",
            "tokens_in": 1500,
            "tokens_out": 800,
            "cost": 0.045,
            "status": "success",
            "metadata": {"region": "us-west"},
            "timestamp": "2026-03-27T10:00:00+00:00",
        }

        append_request(record, path=ledger_path)

        content = ledger_path.read_text().strip()
        written = json.loads(content)
        for key, value in record.items():
            assert written[key] == value

    def test_append_request_truncates_when_exceeding_max_requests(self, tmp_path):
        """Test that ledger is truncated to MAX_REQUESTS when limit exceeded."""
        ledger_path = tmp_path / "requests.jsonl"

        # Write MAX_REQUESTS + 10 records
        for i in range(MAX_REQUESTS + 10):
            record = {"request_id": f"req-{i:04d}", "index": i}
            append_request(record, path=ledger_path)

        lines = ledger_path.read_text().strip().split("\n")
        # Should be exactly MAX_REQUESTS
        assert len(lines) == MAX_REQUESTS
        # Verify we kept the LAST MAX_REQUESTS (indices 10 through 1009)
        first_line = json.loads(lines[0])
        assert first_line["index"] == 10
        last_line = json.loads(lines[-1])
        assert last_line["index"] == MAX_REQUESTS + 9

    def test_append_request_truncation_preserves_jsonl_format(self, tmp_path):
        """Test that truncated ledger remains valid JSONL."""
        ledger_path = tmp_path / "requests.jsonl"

        # Write more than MAX_REQUESTS
        for i in range(MAX_REQUESTS + 5):
            record = {"request_id": f"req-{i:04d}"}
            append_request(record, path=ledger_path)

        # Verify all remaining lines are valid JSON
        lines = ledger_path.read_text().strip().split("\n")
        for line in lines:
            parsed = json.loads(line)
            assert "request_id" in parsed
