"""
Tests for debug mode toggle and logging.
"""

import io
import json
import os
import pytest
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from tokenpak.agent.config import (
    get_debug_enabled,
    set_debug_enabled,
    debug_log,
    CONFIG_PATH,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_config(tmp_path, monkeypatch):
    """Use a temp config file for tests."""
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("tokenpak.agent.config.CONFIG_PATH", config_path)
    return config_path


@pytest.fixture
def clean_env(monkeypatch):
    """Remove debug env var for clean tests."""
    monkeypatch.delenv("TOKENPAK_DEBUG", raising=False)


# ─────────────────────────────────────────────────────────────────────────────
# get_debug_enabled / set_debug_enabled
# ─────────────────────────────────────────────────────────────────────────────

class TestDebugEnabled:
    def test_default_is_false(self, temp_config, clean_env):
        """Debug mode defaults to off."""
        assert get_debug_enabled() is False

    def test_set_to_true(self, temp_config, clean_env):
        """Can enable debug mode."""
        set_debug_enabled(True)
        assert get_debug_enabled() is True

    def test_set_to_false(self, temp_config, clean_env):
        """Can disable debug mode."""
        set_debug_enabled(True)
        set_debug_enabled(False)
        assert get_debug_enabled() is False

    def test_persists_to_file(self, temp_config, clean_env):
        """Debug state is persisted to config file."""
        set_debug_enabled(True)
        data = json.loads(temp_config.read_text())
        assert data.get("debug") is True

    def test_env_var_overrides_file_enabled(self, temp_config, monkeypatch):
        """TOKENPAK_DEBUG=1 enables debug even if file says off."""
        set_debug_enabled(False)
        monkeypatch.setenv("TOKENPAK_DEBUG", "1")
        assert get_debug_enabled() is True

    def test_env_var_overrides_file_disabled(self, temp_config, monkeypatch):
        """TOKENPAK_DEBUG=0 disables debug even if file says on."""
        set_debug_enabled(True)
        monkeypatch.setenv("TOKENPAK_DEBUG", "0")
        assert get_debug_enabled() is False

    def test_env_var_false_string(self, temp_config, monkeypatch):
        """TOKENPAK_DEBUG=false disables debug."""
        set_debug_enabled(True)
        monkeypatch.setenv("TOKENPAK_DEBUG", "false")
        assert get_debug_enabled() is False

    def test_env_var_true_string(self, temp_config, monkeypatch):
        """TOKENPAK_DEBUG=true enables debug."""
        monkeypatch.setenv("TOKENPAK_DEBUG", "true")
        assert get_debug_enabled() is True


# ─────────────────────────────────────────────────────────────────────────────
# debug_log
# ─────────────────────────────────────────────────────────────────────────────

class TestDebugLog:
    def test_no_output_when_disabled(self, temp_config, clean_env, capsys):
        """debug_log produces no output when debug mode is off."""
        set_debug_enabled(False)
        debug_log("test message")
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_output_when_enabled(self, temp_config, clean_env, capsys):
        """debug_log produces output when debug mode is on."""
        set_debug_enabled(True)
        debug_log("test message")
        captured = capsys.readouterr()
        assert "[DEBUG" in captured.err
        assert "test message" in captured.err

    def test_context_kwargs_in_output(self, temp_config, clean_env, capsys):
        """debug_log includes context kwargs in output."""
        set_debug_enabled(True)
        debug_log("request", model="gpt-4", tokens=100)
        captured = capsys.readouterr()
        assert "model=gpt-4" in captured.err
        assert "tokens=100" in captured.err

    def test_timestamp_in_output(self, temp_config, clean_env, capsys):
        """debug_log includes timestamp."""
        set_debug_enabled(True)
        debug_log("test")
        captured = capsys.readouterr()
        # Should have HH:MM:SS format
        import re
        assert re.search(r"\d{2}:\d{2}:\d{2}", captured.err)


# ─────────────────────────────────────────────────────────────────────────────
# CLI commands
# ─────────────────────────────────────────────────────────────────────────────

class TestDebugCLI:
    def test_cmd_debug_on(self, temp_config, clean_env, capsys):
        """tokenpak debug on enables debug mode."""
        from tokenpak.cli import cmd_debug_on
        from types import SimpleNamespace
        
        cmd_debug_on(SimpleNamespace())
        captured = capsys.readouterr()
        
        assert "enabled" in captured.out.lower()
        assert get_debug_enabled() is True

    def test_cmd_debug_off(self, temp_config, clean_env, capsys):
        """tokenpak debug off disables debug mode."""
        from tokenpak.cli import cmd_debug_off
        from types import SimpleNamespace
        
        set_debug_enabled(True)
        cmd_debug_off(SimpleNamespace())
        captured = capsys.readouterr()
        
        assert "disabled" in captured.out.lower()
        assert get_debug_enabled() is False

    def test_cmd_debug_status_off(self, temp_config, clean_env, capsys):
        """tokenpak debug status shows OFF state."""
        from tokenpak.cli import cmd_debug_status
        from types import SimpleNamespace
        
        set_debug_enabled(False)
        cmd_debug_status(SimpleNamespace())
        captured = capsys.readouterr()
        
        assert "OFF" in captured.out

    def test_cmd_debug_status_on(self, temp_config, clean_env, capsys):
        """tokenpak debug status shows ON state."""
        from tokenpak.cli import cmd_debug_status
        from types import SimpleNamespace
        
        set_debug_enabled(True)
        cmd_debug_status(SimpleNamespace())
        captured = capsys.readouterr()
        
        assert "ON" in captured.out

    def test_cmd_debug_status_shows_env_override(self, temp_config, monkeypatch, capsys):
        """tokenpak debug status shows env var override."""
        from tokenpak.cli import cmd_debug_status
        from types import SimpleNamespace
        
        monkeypatch.setenv("TOKENPAK_DEBUG", "1")
        cmd_debug_status(SimpleNamespace())
        captured = capsys.readouterr()
        
        assert "TOKENPAK_DEBUG" in captured.out
