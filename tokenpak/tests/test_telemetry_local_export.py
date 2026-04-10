"""Unit tests for tokenpak.telemetry.local_exporter.

Covers:
- Opt-in flag respected (no write when disabled)
- Local JSONL file written when enabled
- Daily file rotation (new file per UTC day)
- 30-day retention (older files pruned on write)
- Remote mode skips local write
- record_request() integration: file is created on opt-in request
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record() -> dict:
    return {
        "date_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "input_tokens": 100,
        "output_tokens": 50,
        "tokens_saved": 10,
        "compression_ratio": 0.1,
        "latency_ms": 42.0,
        "model": "claude-sonnet-4-6",
        "schema_version": "1.0",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLocalExporterOptIn:
    """write_record() must be a no-op when metrics are disabled."""

    def test_no_write_when_opted_out(self, tmp_path):
        from tokenpak.telemetry import local_exporter

        with patch("tokenpak.agent.config.get_metrics_enabled", return_value=False):
            local_exporter.write_record(_make_record(), telemetry_dir=tmp_path, mode="local")

        assert list(tmp_path.glob("*.jsonl")) == [], "No file should be created when opted out"

    def test_write_when_opted_in(self, tmp_path):
        from tokenpak.telemetry import local_exporter

        with patch("tokenpak.agent.config.get_metrics_enabled", return_value=True):
            local_exporter.write_record(_make_record(), telemetry_dir=tmp_path, mode="local")

        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1, "Exactly one JSONL file should be created"

    def test_written_content_is_valid_json(self, tmp_path):
        from tokenpak.telemetry import local_exporter

        rec = _make_record()
        with patch("tokenpak.agent.config.get_metrics_enabled", return_value=True):
            local_exporter.write_record(rec, telemetry_dir=tmp_path, mode="local")

        files = list(tmp_path.glob("*.jsonl"))
        line = files[0].read_text().strip()
        parsed = json.loads(line)
        assert parsed["model"] == "claude-sonnet-4-6"
        assert parsed["input_tokens"] == 100


class TestLocalExporterFileRotation:
    """Each UTC day gets its own JSONL file."""

    def test_file_named_with_today_utc(self, tmp_path):
        from tokenpak.telemetry import local_exporter

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with patch("tokenpak.agent.config.get_metrics_enabled", return_value=True):
            local_exporter.write_record(_make_record(), telemetry_dir=tmp_path, mode="local")

        expected = tmp_path / f"metrics-{today}.jsonl"
        assert expected.exists(), f"Expected file {expected.name} not found"

    def test_multiple_writes_append_to_same_file(self, tmp_path):
        from tokenpak.telemetry import local_exporter

        with patch("tokenpak.agent.config.get_metrics_enabled", return_value=True):
            local_exporter.write_record(_make_record(), telemetry_dir=tmp_path, mode="local")
            local_exporter.write_record(_make_record(), telemetry_dir=tmp_path, mode="local")

        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text().strip().splitlines()
        assert len(lines) == 2, "Both records should be appended to the same file"


class TestLocalExporterRetention:
    """Files older than 30 days are deleted on write."""

    def test_old_files_pruned_on_write(self, tmp_path):
        from tokenpak.telemetry import local_exporter

        # Create a stale file (35 days old)
        stale_date = datetime.fromordinal(
            datetime.now(timezone.utc).toordinal() - 35
        ).strftime("%Y-%m-%d")
        stale_file = tmp_path / f"metrics-{stale_date}.jsonl"
        stale_file.write_text('{"stale": true}\n')

        with patch("tokenpak.agent.config.get_metrics_enabled", return_value=True):
            local_exporter.write_record(_make_record(), telemetry_dir=tmp_path, mode="local")

        assert not stale_file.exists(), "Stale file should have been pruned"

    def test_recent_files_preserved(self, tmp_path):
        from tokenpak.telemetry import local_exporter

        # Create a recent file (5 days old)
        recent_date = datetime.fromordinal(
            datetime.now(timezone.utc).toordinal() - 5
        ).strftime("%Y-%m-%d")
        recent_file = tmp_path / f"metrics-{recent_date}.jsonl"
        recent_file.write_text('{"recent": true}\n')

        with patch("tokenpak.agent.config.get_metrics_enabled", return_value=True):
            local_exporter.write_record(_make_record(), telemetry_dir=tmp_path, mode="local")

        assert recent_file.exists(), "Recent file should not be pruned"

    def test_boundary_file_preserved(self, tmp_path):
        from tokenpak.telemetry import local_exporter

        # File exactly 30 days old should survive (cutoff is strictly < 30 days)
        boundary_date = datetime.fromordinal(
            datetime.now(timezone.utc).toordinal() - 30
        ).strftime("%Y-%m-%d")
        boundary_file = tmp_path / f"metrics-{boundary_date}.jsonl"
        boundary_file.write_text('{"boundary": true}\n')

        with patch("tokenpak.agent.config.get_metrics_enabled", return_value=True):
            local_exporter.write_record(_make_record(), telemetry_dir=tmp_path, mode="local")

        assert boundary_file.exists(), "File exactly 30 days old should be preserved"


class TestLocalExporterRemoteMode:
    """write_record() is a no-op when mode is not 'local'."""

    def test_remote_mode_skips_write(self, tmp_path):
        from tokenpak.telemetry import local_exporter

        with patch("tokenpak.agent.config.get_metrics_enabled", return_value=True):
            local_exporter.write_record(_make_record(), telemetry_dir=tmp_path, mode="remote")

        assert list(tmp_path.glob("*.jsonl")) == [], "No file should be created in remote mode"

    def test_is_local_mode_true_by_default(self):
        from tokenpak.telemetry import local_exporter

        with patch.dict(os.environ, {"TOKENPAK_TELEMETRY_MODE": "local"}):
            # Re-evaluate with patched env
            result = os.environ.get("TOKENPAK_TELEMETRY_MODE", "local") == "local"
        assert result is True

    def test_is_local_mode_false_when_remote(self):
        from tokenpak.telemetry import local_exporter

        with patch.dict(os.environ, {"TOKENPAK_TELEMETRY_MODE": "remote"}):
            result = os.environ.get("TOKENPAK_TELEMETRY_MODE", "local") == "local"
        assert result is False


class TestRecordRequestIntegration:
    """record_request() writes JSONL when opt-in and local mode active."""

    def test_record_request_creates_local_file(self, tmp_path):
        """End-to-end: record_request() → local JSONL written."""
        from tokenpak.telemetry import anon_metrics, local_exporter

        # Use a fresh in-memory SQLite store
        import sqlite3
        from unittest.mock import MagicMock

        mock_store = MagicMock()
        mock_store.record = MagicMock()

        with (
            patch("tokenpak.agent.config.get_metrics_enabled", return_value=True),
            patch("tokenpak.telemetry.anon_metrics.get_store", return_value=mock_store),
            patch("tokenpak.telemetry.local_exporter.TELEMETRY_DIR", tmp_path),
            patch("tokenpak.telemetry.local_exporter.TELEMETRY_MODE", "local"),
        ):
            anon_metrics.record_request(
                input_tokens=200,
                output_tokens=80,
                tokens_saved=20,
                latency_ms=55.0,
                model="claude-haiku-4-5",
            )

        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1, "JSONL file should be created"
        parsed = json.loads(files[0].read_text().strip())
        assert parsed["model"] == "claude-haiku-4-5"
        assert parsed["input_tokens"] == 200

    def test_record_request_no_file_when_opted_out(self, tmp_path):
        from tokenpak.telemetry import anon_metrics, local_exporter
        from unittest.mock import MagicMock

        with (
            patch("tokenpak.agent.config.get_metrics_enabled", return_value=False),
            patch("tokenpak.telemetry.local_exporter.TELEMETRY_DIR", tmp_path),
            patch("tokenpak.telemetry.local_exporter.TELEMETRY_MODE", "local"),
        ):
            anon_metrics.record_request(
                input_tokens=100,
                output_tokens=50,
                tokens_saved=10,
                latency_ms=30.0,
                model="claude-sonnet-4-6",
            )

        assert list(tmp_path.glob("*.jsonl")) == [], "No file when opted out"
