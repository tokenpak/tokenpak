# SPDX-License-Identifier: MIT
"""
Unit tests for tokenpak.version_check module.

Tests version checking, config hash validation, and startup checks.
"""

import json
import tempfile
from pathlib import Path
from unittest import mock
from typing import Optional

import pytest

from tokenpak import version_check


class TestComputeConfigHash:
    """Tests for _compute_config_hash()."""

    def test_simple_config(self):
        """Test hashing a simple config."""
        cfg = {"proxy": {"port": 8766}, "models": ["gpt-4"]}
        h = version_check._compute_config_hash(cfg)
        assert h.startswith("sha256:")
        assert len(h) == 19  # "sha256:" + 12 hex chars

    def test_hash_consistency(self):
        """Same config produces same hash."""
        cfg = {"a": 1, "b": 2}
        h1 = version_check._compute_config_hash(cfg)
        h2 = version_check._compute_config_hash(cfg)
        assert h1 == h2

    def test_hash_order_independence(self):
        """Hash is same regardless of key order."""
        cfg1 = {"a": 1, "b": 2, "c": 3}
        cfg2 = {"c": 3, "a": 1, "b": 2}
        h1 = version_check._compute_config_hash(cfg1)
        h2 = version_check._compute_config_hash(cfg2)
        assert h1 == h2

    def test_hash_ignores_meta(self):
        """Meta field is excluded from hash."""
        cfg1 = {"proxy": {"port": 8766}}
        cfg2 = {"proxy": {"port": 8766}, "meta": {"ignored": True}}
        h1 = version_check._compute_config_hash(cfg1)
        h2 = version_check._compute_config_hash(cfg2)
        assert h1 == h2

    def test_different_values_different_hash(self):
        """Different configs produce different hashes."""
        cfg1 = {"port": 8766}
        cfg2 = {"port": 9000}
        h1 = version_check._compute_config_hash(cfg1)
        h2 = version_check._compute_config_hash(cfg2)
        assert h1 != h2


class TestQueryProxyVersion:
    """Tests for _query_proxy_version()."""

    def test_successful_query(self):
        """Test querying a reachable proxy."""
        mock_response = json.dumps({"version": "1.2.3", "status": "ok"})
        with mock.patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = (
                mock_response.encode()
            )
            result = version_check._query_proxy_version()
            assert result == {"version": "1.2.3", "status": "ok"}
            mock_open.assert_called_once()

    def test_proxy_unreachable(self):
        """Test handling unreachable proxy."""
        with mock.patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = Exception("Connection refused")
            result = version_check._query_proxy_version()
            assert result is None

    def test_proxy_invalid_json(self):
        """Test handling invalid JSON from proxy."""
        with mock.patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = (
                b"invalid json"
            )
            result = version_check._query_proxy_version()
            assert result is None


class TestLoadLock:
    """Tests for _load_lock()."""

    def test_load_valid_lock(self):
        """Test loading a valid lock file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_file = Path(tmpdir) / "tokenpak.lock.json"
            lock_data = {"proxyVersion": "1.2.3", "configHash": "sha256:abc123"}
            lock_file.write_text(json.dumps(lock_data))
            with mock.patch.object(version_check, "LOCK_FILE", lock_file):
                result = version_check._load_lock()
                assert result == lock_data

    def test_load_missing_lock(self):
        """Test handling missing lock file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_file = Path(tmpdir) / "nonexistent.json"
            with mock.patch.object(version_check, "LOCK_FILE", lock_file):
                result = version_check._load_lock()
                assert result == {}

    def test_load_invalid_lock(self):
        """Test handling corrupted lock file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_file = Path(tmpdir) / "tokenpak.lock.json"
            lock_file.write_text("{ invalid json")
            with mock.patch.object(version_check, "LOCK_FILE", lock_file):
                result = version_check._load_lock()
                assert result == {}


class TestLoadConfig:
    """Tests for _load_config()."""

    def test_load_valid_config(self):
        """Test loading a valid config file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "openclaw.json"
            cfg_data = {"proxy": {"port": 8766}, "models": ["gpt-4"]}
            cfg_file.write_text(json.dumps(cfg_data))
            with mock.patch.object(version_check, "OPENCLAW_CFG", cfg_file):
                result = version_check._load_config()
                assert result == cfg_data

    def test_load_missing_config(self):
        """Test handling missing config file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "nonexistent.json"
            with mock.patch.object(version_check, "OPENCLAW_CFG", cfg_file):
                result = version_check._load_config()
                assert result is None

    def test_load_invalid_config(self):
        """Test handling corrupted config file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "openclaw.json"
            cfg_file.write_text("{ broken config")
            with mock.patch.object(version_check, "OPENCLAW_CFG", cfg_file):
                result = version_check._load_config()
                assert result is None


class TestLogWarning:
    """Tests for _log_warning()."""

    def test_append_warning(self):
        """Test appending a warning to memory file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mem_dir = Path(tmpdir)
            mem_file = mem_dir / "2026-03-27.md"
            with mock.patch.object(version_check, "MEMORY_DIR", mem_dir):
                version_check._log_warning("Test warning 1")
                assert mem_file.exists()
                content = mem_file.read_text()
                assert "Test warning 1" in content
                assert "## Startup Warnings" in content

    def test_append_multiple_warnings(self):
        """Test appending multiple warnings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mem_dir = Path(tmpdir)
            mem_file = mem_dir / "2026-03-27.md"
            with mock.patch.object(version_check, "MEMORY_DIR", mem_dir):
                version_check._log_warning("Warning 1")
                version_check._log_warning("Warning 2")
                content = mem_file.read_text()
                assert "Warning 1" in content
                assert "Warning 2" in content

    def test_warning_logging_graceful_failure(self):
        """Test that logging failures don't crash."""
        # Should not raise even if memory dir is inaccessible
        with mock.patch.object(
            version_check, "MEMORY_DIR", Path("/root/impossible/path")
        ):
            # Should not raise
            version_check._log_warning("test warning")


class TestRunStartupCheck:
    """Tests for run_startup_check()."""

    def test_all_checks_pass(self):
        """Test successful startup when all checks pass."""
        proxy_response = {"version": "1.2.3"}
        cfg = {"proxy": {"port": 8766}}
        lock = {
            "proxyVersion": "1.2.3",
            "configHash": version_check._compute_config_hash(cfg),
        }
        with mock.patch(
            "tokenpak.version_check._query_proxy_version", return_value=proxy_response
        ), mock.patch(
            "tokenpak.version_check._load_config", return_value=cfg
        ), mock.patch(
            "tokenpak.version_check._load_lock", return_value=lock
        ), mock.patch(
            "tokenpak.version_check._log_warning"
        ):
            warnings = version_check.run_startup_check()
            assert warnings == []

    def test_proxy_unreachable_warning(self):
        """Test warning when proxy is unreachable."""
        with mock.patch(
            "tokenpak.version_check._query_proxy_version", return_value=None
        ), mock.patch(
            "tokenpak.version_check._load_config", return_value=None
        ), mock.patch(
            "tokenpak.version_check._load_lock", return_value={}
        ), mock.patch(
            "tokenpak.version_check._log_warning"
        ) as mock_log:
            warnings = version_check.run_startup_check()
            assert len(warnings) == 1
            assert "proxy not reachable" in warnings[0]
            mock_log.assert_called()

    def test_proxy_version_drift_warning(self):
        """Test warning when proxy version drifts from lock."""
        proxy_response = {"version": "2.0.0"}
        lock = {"proxyVersion": "1.2.3"}
        with mock.patch(
            "tokenpak.version_check._query_proxy_version", return_value=proxy_response
        ), mock.patch(
            "tokenpak.version_check._load_config", return_value=None
        ), mock.patch(
            "tokenpak.version_check._load_lock", return_value=lock
        ), mock.patch(
            "tokenpak.version_check._log_warning"
        ) as mock_log:
            warnings = version_check.run_startup_check()
            assert any("version drift" in w for w in warnings)
            mock_log.assert_called()

    def test_config_hash_drift_warning(self):
        """Test warning when config hash drifts from lock."""
        cfg = {"proxy": {"port": 8766}}
        lock = {"configHash": "sha256:oldoldold"}
        with mock.patch(
            "tokenpak.version_check._query_proxy_version", return_value=None
        ), mock.patch(
            "tokenpak.version_check._load_config", return_value=cfg
        ), mock.patch(
            "tokenpak.version_check._load_lock", return_value=lock
        ), mock.patch(
            "tokenpak.version_check._log_warning"
        ) as mock_log:
            warnings = version_check.run_startup_check()
            assert any("hash drift" in w for w in warnings)
            mock_log.assert_called()

    def test_deprecated_field_warning(self):
        """Test warning for deprecated config fields."""
        cfg = {
            "proxy": {"port": 8766},
            "meta": {"legacyMode": True},  # deprecated
        }
        with mock.patch(
            "tokenpak.version_check._query_proxy_version", return_value=None
        ), mock.patch(
            "tokenpak.version_check._load_config", return_value=cfg
        ), mock.patch(
            "tokenpak.version_check._load_lock", return_value={}
        ), mock.patch(
            "tokenpak.version_check._log_warning"
        ) as mock_log:
            warnings = version_check.run_startup_check()
            assert any("deprecated" in w.lower() for w in warnings)

    def test_multiple_warnings_accumulated(self):
        """Test that multiple warnings are accumulated."""
        proxy_response = None  # Unreachable
        cfg = {"proxy": {"port": 8766}}
        lock = {"configHash": "sha256:badbadbad"}
        with mock.patch(
            "tokenpak.version_check._query_proxy_version", return_value=proxy_response
        ), mock.patch(
            "tokenpak.version_check._load_config", return_value=cfg
        ), mock.patch(
            "tokenpak.version_check._load_lock", return_value=lock
        ), mock.patch(
            "tokenpak.version_check._log_warning"
        ):
            warnings = version_check.run_startup_check()
            assert len(warnings) >= 2  # At least proxy + hash warnings


class TestStartupCheckIntegration:
    """Integration tests for version_check module."""

    def test_main_execution_no_errors(self):
        """Test that the module can be imported and functions exist."""
        assert callable(version_check.run_startup_check)
        assert callable(version_check._compute_config_hash)
        assert callable(version_check._query_proxy_version)
        assert callable(version_check._load_lock)
        assert callable(version_check._load_config)
        assert callable(version_check._log_warning)

    def test_deprecated_fields_constant_exists(self):
        """Test that deprecated fields are defined."""
        assert hasattr(version_check, "DEPRECATED_CONFIG_FIELDS")
        assert isinstance(version_check.DEPRECATED_CONFIG_FIELDS, set)
        assert len(version_check.DEPRECATED_CONFIG_FIELDS) > 0
