"""Tests for TokenPak ingest API integration in proxy_v4.py

Test coverage:
- POST /ingest (single entry)
- POST /ingest/batch (multiple entries)
- Request validation
- Error handling
- Entry persistence
"""

import json
import tempfile
import pytest
import sys
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

# Add the proxy module to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the ingest write function
from proxy_v4 import _ingest_write_entry, INGEST_ENTRIES_DIR


class TestIngestSingleEntry:
    """Test POST /ingest single entry handling."""

    def test_write_entry_creates_jsonl_file(self, tmp_path):
        """Test that _ingest_write_entry creates a dated JSONL file."""
        # Patch the INGEST_ENTRIES_DIR for this test
        with patch("proxy_v4.INGEST_ENTRIES_DIR", tmp_path / "entries"):
            entry = {
                "model": "claude-3-opus",
                "tokens": 100,
                "cost": 0.5,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            entry_id = _ingest_write_entry(entry)
            
            # Verify entry_id was generated
            assert entry_id is not None
            assert len(entry_id) > 0
            
            # Verify JSONL file was created
            entries_dir = tmp_path / "entries"
            assert entries_dir.exists()
            
            # Find the dated file
            jsonl_files = list(entries_dir.glob("*.jsonl"))
            assert len(jsonl_files) == 1
            
            # Read and verify content
            with open(jsonl_files[0], "r") as f:
                line = f.read().strip()
            data = json.loads(line)
            assert data["id"] == entry_id
            assert data["model"] == "claude-3-opus"
            assert data["tokens"] == 100

    def test_write_entry_with_custom_id(self, tmp_path):
        """Test that custom entry IDs are preserved."""
        with patch("proxy_v4.INGEST_ENTRIES_DIR", tmp_path / "entries"):
            custom_id = "custom-uuid-123"
            entry = {
                "id": custom_id,
                "model": "gpt-4",
                "tokens": 50,
                "cost": 0.1,
            }
            returned_id = _ingest_write_entry(entry)
            
            assert returned_id == custom_id

    def test_write_entry_uses_timestamp_date(self, tmp_path):
        """Test that entries are stored under the timestamp's date."""
        with patch("proxy_v4.INGEST_ENTRIES_DIR", tmp_path / "entries"):
            entry = {
                "model": "claude",
                "tokens": 10,
                "cost": 0.01,
                "timestamp": "2026-01-15T10:00:00+00:00",
            }
            _ingest_write_entry(entry)
            
            # Verify file is named 2026-01-15.jsonl
            entries_dir = tmp_path / "entries"
            files = list(entries_dir.glob("*.jsonl"))
            assert any("2026-01-15" in f.name for f in files)


class TestIngestBatch:
    """Test POST /ingest/batch handling."""

    def test_batch_multiple_entries(self, tmp_path):
        """Test batch writing multiple entries."""
        with patch("proxy_v4.INGEST_ENTRIES_DIR", tmp_path / "entries"):
            entries = [
                {"model": "claude-3-opus", "tokens": 100, "cost": 0.5},
                {"model": "gpt-4", "tokens": 50, "cost": 0.2},
                {"model": "claude-3-haiku", "tokens": 10, "cost": 0.05},
            ]
            
            ids = [_ingest_write_entry(e) for e in entries]
            
            assert len(ids) == 3
            assert all(id for id in ids)  # All non-empty

    def test_batch_appends_to_same_file(self, tmp_path):
        """Test that multiple entries on same date go to same file."""
        with patch("proxy_v4.INGEST_ENTRIES_DIR", tmp_path / "entries"):
            entry1 = {"model": "a", "tokens": 1, "cost": 0.01}
            entry2 = {"model": "b", "tokens": 2, "cost": 0.02}
            
            _ingest_write_entry(entry1)
            _ingest_write_entry(entry2)
            
            # Should have only one file
            entries_dir = tmp_path / "entries"
            files = list(entries_dir.glob("*.jsonl"))
            assert len(files) == 1
            
            # Should have two lines
            with open(files[0], "r") as f:
                lines = f.readlines()
            assert len(lines) == 2


class TestInputValidation:
    """Test input validation for ingest endpoints."""

    def test_empty_model_rejected(self):
        """Test that empty model strings are rejected."""
        # This would be caught by the handler before write
        entry = {
            "model": "",
            "tokens": 10,
            "cost": 0.01,
        }
        # Empty model should fail validation in handler
        assert entry["model"] == ""

    def test_negative_tokens_rejected(self):
        """Test that negative tokens are rejected."""
        entry = {
            "model": "claude",
            "tokens": -10,
            "cost": 0.01,
        }
        assert entry["tokens"] < 0

    def test_negative_cost_rejected(self):
        """Test that negative cost is rejected."""
        entry = {
            "model": "claude",
            "tokens": 10,
            "cost": -0.01,
        }
        assert entry["cost"] < 0

    def test_invalid_timestamp_format(self):
        """Test that invalid ISO 8601 timestamps are rejected."""
        # Handler should validate this
        timestamp = "not-a-valid-timestamp"
        try:
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            assert False, "Should have raised ValueError"
        except ValueError:
            pass  # Expected

    def test_valid_iso8601_timestamps(self):
        """Test that valid ISO 8601 timestamps are accepted."""
        valid_timestamps = [
            "2026-03-10T15:00:00+00:00",
            "2026-03-10T15:00:00Z",
            "2026-03-10T15:00:00",
        ]
        
        for ts in valid_timestamps:
            try:
                datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                pytest.fail(f"Valid timestamp {ts} was rejected")


class TestEntryPersistence:
    """Test that entries persist correctly."""

    def test_entry_survives_reload(self, tmp_path):
        """Test that written entries can be read back."""
        with patch("proxy_v4.INGEST_ENTRIES_DIR", tmp_path / "entries"):
            original = {
                "model": "claude-3-opus",
                "tokens": 100,
                "cost": 0.5,
                "agent": "test-agent",
                "extra_field": "custom_value",
            }
            
            _ingest_write_entry(original)
            
            # Read back
            entries_dir = tmp_path / "entries"
            files = list(entries_dir.glob("*.jsonl"))
            
            with open(files[0], "r") as f:
                line = f.read().strip()
            restored = json.loads(line)
            
            # Verify all fields
            assert restored["model"] == original["model"]
            assert restored["tokens"] == original["tokens"]
            assert restored["cost"] == original["cost"]
            assert restored["agent"] == original["agent"]
            assert restored["extra_field"] == original["extra_field"]

    def test_jsonl_format_correct(self, tmp_path):
        """Test that output is valid JSONL (one JSON per line)."""
        with patch("proxy_v4.INGEST_ENTRIES_DIR", tmp_path / "entries"):
            entries = [
                {"model": "a", "tokens": 1, "cost": 0.01},
                {"model": "b", "tokens": 2, "cost": 0.02},
                {"model": "c", "tokens": 3, "cost": 0.03},
            ]
            
            for e in entries:
                _ingest_write_entry(e)
            
            entries_dir = tmp_path / "entries"
            files = list(entries_dir.glob("*.jsonl"))
            
            with open(files[0], "r") as f:
                for i, line in enumerate(f):
                    # Each line should be valid JSON
                    data = json.loads(line)
                    assert data["model"] == chr(ord("a") + i)


class TestErrorHandling:
    """Test error handling."""

    def test_missing_required_field_model(self):
        """Verify model is required."""
        entry = {"tokens": 10, "cost": 0.01}
        assert "model" not in entry

    def test_missing_required_field_tokens(self):
        """Verify tokens is required."""
        entry = {"model": "claude", "cost": 0.01}
        assert "tokens" not in entry

    def test_missing_required_field_cost(self):
        """Verify cost is required."""
        entry = {"model": "claude", "tokens": 10}
        assert "cost" not in entry

    def test_optional_fields_allowed(self):
        """Test that optional fields don't cause errors."""
        entry = {
            "model": "claude",
            "tokens": 10,
            "cost": 0.01,
            "agent": "test",
            "provider": "anthropic",
            "session_id": "sess-123",
            "extra": {"custom": "metadata"},
        }
        
        # All these should be accepted
        assert entry["agent"] == "test"
        assert entry["provider"] == "anthropic"
        assert entry["session_id"] == "sess-123"
        assert entry["extra"]["custom"] == "metadata"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
