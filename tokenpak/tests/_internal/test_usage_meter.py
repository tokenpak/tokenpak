"""
Tests for WS-5 client-side UsageMeter.

Test 1: record() appends to the buffer
Test 2: flush() POSTs buffered events to the license server
Test 3: flush() failure leaves the buffer intact (graceful degradation)
Test 4: Buffer persists across process restart (on-disk fallback)
Test 5: Daily heartbeat thread starts on import
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tokenpak._internal.license.usage_meter import UsageMeter


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_meter(tmp_path: Path, **kwargs) -> UsageMeter:
    """Create a UsageMeter with a temp buffer path and heartbeat disabled."""
    return UsageMeter(
        license_server_url="http://localhost:9999",
        buffer_path=tmp_path / "usage_buffer.jsonl",
        _start_heartbeat=False,
        **kwargs,
    )


# ── Test 1 ───────────────────────────────────────────────────────────────────


def test_record_appends_to_buffer(tmp_path):
    """record() appends an event to the in-memory buffer without doing I/O."""
    meter = _make_meter(tmp_path)
    assert meter._buffer_snapshot() == []

    meter.record("TPAK-1234", 100, 50, "claude-sonnet-4-6")
    meter.record("TPAK-1234", 200, 80, "gpt-4o")

    buf = meter._buffer_snapshot()
    assert len(buf) == 2
    assert buf[0]["license_id"] == "TPAK-1234"
    assert buf[0]["tokens_in"] == 100
    assert buf[0]["tokens_out"] == 50
    assert buf[1]["model"] == "gpt-4o"


# ── Test 2 ───────────────────────────────────────────────────────────────────


def test_flush_posts_buffer_to_license_server(tmp_path):
    """flush() POSTs each buffered event to /usage and clears the buffer."""
    meter = _make_meter(tmp_path)
    meter.record("TPAK-AAAA", 300, 150, "claude-haiku-4-5")
    meter.record("TPAK-AAAA", 400, 200, "claude-haiku-4-5")

    mock_response = MagicMock()
    mock_response.status_code = 201

    with patch("requests.post", return_value=mock_response) as mock_post:
        result = meter.flush()

    assert result is True
    assert mock_post.call_count == 2

    # Verify POST body for first call
    first_call_body = mock_post.call_args_list[0][1]["json"]
    assert first_call_body["license_id"] == "TPAK-AAAA"
    assert first_call_body["tokens_in"] == 300

    # Buffer should be cleared after successful flush
    assert meter._buffer_snapshot() == []

    # Disk buffer file should be removed
    assert not (tmp_path / "usage_buffer.jsonl").exists()


# ── Test 3 ───────────────────────────────────────────────────────────────────


def test_flush_failure_keeps_buffer_intact(tmp_path):
    """flush() on network error keeps the buffer and writes it to disk."""
    meter = _make_meter(tmp_path)
    meter.record("TPAK-BBBB", 100, 50, "gpt-4o")
    meter.record("TPAK-BBBB", 200, 100, "gpt-4o")

    with patch("requests.post", side_effect=ConnectionError("server unreachable")):
        result = meter.flush()

    assert result is False

    # Buffer must still contain the events
    buf = meter._buffer_snapshot()
    assert len(buf) == 2

    # Disk buffer must have been written for next-heartbeat retry
    disk_path = tmp_path / "usage_buffer.jsonl"
    assert disk_path.exists()
    lines = [json.loads(l) for l in disk_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 2
    assert lines[0]["license_id"] == "TPAK-BBBB"


# ── Test 4 ───────────────────────────────────────────────────────────────────


def test_disk_buffer_survives_restart(tmp_path):
    """Events written to disk are reloaded by a new UsageMeter instance."""
    # First instance records and fails to flush (simulating a crash)
    meter1 = _make_meter(tmp_path)
    meter1.record("TPAK-CCCC", 500, 250, "claude-opus-4-6")

    with patch("requests.post", side_effect=ConnectionError("down")):
        meter1.flush()

    # Verify disk file was created
    disk_path = tmp_path / "usage_buffer.jsonl"
    assert disk_path.exists()

    # Second instance (simulates process restart) loads from disk
    meter2 = _make_meter(tmp_path)
    buf = meter2._buffer_snapshot()
    assert len(buf) == 1
    assert buf[0]["license_id"] == "TPAK-CCCC"
    assert buf[0]["tokens_in"] == 500

    # Successful flush from the new instance clears the disk file
    mock_response = MagicMock()
    mock_response.status_code = 201
    with patch("requests.post", return_value=mock_response):
        result = meter2.flush()

    assert result is True
    assert not disk_path.exists()


# ── Test 5 ───────────────────────────────────────────────────────────────────


def test_heartbeat_thread_starts_on_init(tmp_path):
    """UsageMeter starts a daemon heartbeat thread when _start_heartbeat=True."""
    meter = UsageMeter(
        license_server_url="http://localhost:9999",
        buffer_path=tmp_path / "hb_buffer.jsonl",
        heartbeat_interval=9999,  # long interval so it doesn't actually fire
        _start_heartbeat=True,
    )

    assert hasattr(meter, "_heartbeat_thread")
    thread = meter._heartbeat_thread
    assert isinstance(thread, threading.Thread)
    assert thread.is_alive()
    assert thread.daemon  # must be daemon so it doesn't block process exit
    assert "heartbeat" in thread.name


# ── Bonus: record() with empty license_id is a no-op ─────────────────────────


def test_record_skips_empty_license_id(tmp_path):
    """record() does nothing when license_id is empty (no license configured)."""
    meter = _make_meter(tmp_path)
    meter.record("", 100, 50, "claude-sonnet-4-6")
    assert meter._buffer_snapshot() == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
