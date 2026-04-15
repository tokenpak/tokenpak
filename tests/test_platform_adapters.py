"""
Tests for tokenpak.adapters — platform detection and configuration.
"""


import pytest
pytest.importorskip("tokenpak.adapters.claude_cli", reason="module not available in current build")
import pytest

from tokenpak.adapters.base import BaseAdapter
from tokenpak.adapters.openclaw import OpenClawAdapter
from tokenpak.adapters.claude_cli import ClaudeCLIAdapter
from tokenpak.adapters.generic import GenericAdapter
from tokenpak.adapters.registry import detect_platform


# ---------------------------------------------------------------------------
# OpenClaw adapter
# ---------------------------------------------------------------------------

class TestOpenClawAdapter:
    def test_detect_via_header(self):
        assert OpenClawAdapter.detect({"X-OpenClaw-Session": "abc123"}, {}) is True

    def test_detect_via_header_case_insensitive(self):
        assert OpenClawAdapter.detect({"x-openclaw-session": "abc123"}, {}) is True

    def test_detect_via_env(self):
        assert OpenClawAdapter.detect({}, {"OPENCLAW_SESSION": "sess-xyz"}) is True

    def test_no_detect_empty_env(self):
        assert OpenClawAdapter.detect({}, {"OPENCLAW_SESSION": ""}) is False

    def test_no_detect_missing(self):
        assert OpenClawAdapter.detect({"User-Agent": "curl/7.0"}, {}) is False

    def test_platform_name(self):
        assert OpenClawAdapter().platform_name == "openclaw"

    def test_get_config_keys(self):
        cfg = OpenClawAdapter().get_config()
        assert "compression_ratio_target" in cfg
        assert "vault_aware" in cfg
        assert "preserve_code_blocks" in cfg
        assert "prefer_fast_models" in cfg
        assert "routing_hints" in cfg

    def test_get_config_values(self):
        cfg = OpenClawAdapter().get_config()
        assert cfg["compression_ratio_target"] >= 0.7
        assert cfg["vault_aware"] is True
        assert cfg["prefer_fast_models"] is True


# ---------------------------------------------------------------------------
# Claude CLI adapter
# ---------------------------------------------------------------------------

class TestClaudeCLIAdapter:
    def test_detect_via_user_agent(self):
        assert ClaudeCLIAdapter.detect({"User-Agent": "claude-cli/1.0"}, {}) is True

    def test_detect_via_user_agent_case_insensitive(self):
        assert ClaudeCLIAdapter.detect({"user-agent": "Claude-CLI/2.3.1"}, {}) is True

    def test_detect_via_env(self):
        assert ClaudeCLIAdapter.detect({}, {"CLAUDE_CLI": "1"}) is True

    def test_no_detect_env_zero(self):
        assert ClaudeCLIAdapter.detect({}, {"CLAUDE_CLI": "0"}) is False

    def test_no_detect_missing(self):
        assert ClaudeCLIAdapter.detect({"User-Agent": "python-httpx/0.24"}, {}) is False

    def test_platform_name(self):
        assert ClaudeCLIAdapter().platform_name == "claude_cli"

    def test_get_config_keys(self):
        cfg = ClaudeCLIAdapter().get_config()
        assert "compression_ratio_target" in cfg
        assert "vault_aware" in cfg
        assert "preserve_code_blocks" in cfg

    def test_get_config_values(self):
        cfg = ClaudeCLIAdapter().get_config()
        assert cfg["compression_ratio_target"] == 0.5
        assert cfg["vault_aware"] is False
        assert cfg["preserve_code_blocks"] is True


# ---------------------------------------------------------------------------
# Generic adapter
# ---------------------------------------------------------------------------

class TestGenericAdapter:
    def test_always_detects(self):
        assert GenericAdapter.detect({}, {}) is True
        assert GenericAdapter.detect({"X-Random": "header"}, {"RANDOM": "var"}) is True

    def test_platform_name(self):
        assert GenericAdapter().platform_name == "generic"

    def test_get_config_values(self):
        cfg = GenericAdapter().get_config()
        assert cfg["compression_ratio_target"] == 0.3
        assert cfg["vault_aware"] is False
        assert cfg["prefer_fast_models"] is False


# ---------------------------------------------------------------------------
# Registry / detect_platform
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_openclaw_wins_via_header(self):
        adapter = detect_platform({"X-OpenClaw-Session": "abc"}, {})
        assert adapter.platform_name == "openclaw"

    def test_openclaw_wins_via_env(self):
        adapter = detect_platform({}, {"OPENCLAW_SESSION": "xyz"})
        assert adapter.platform_name == "openclaw"

    def test_claude_cli_wins_via_user_agent(self):
        adapter = detect_platform({"User-Agent": "claude-cli/1.0"}, {})
        assert adapter.platform_name == "claude_cli"

    def test_claude_cli_wins_via_env(self):
        adapter = detect_platform({}, {"CLAUDE_CLI": "1"})
        assert adapter.platform_name == "claude_cli"

    def test_generic_fallback(self):
        adapter = detect_platform({}, {})
        assert adapter.platform_name == "generic"

    def test_generic_fallback_unrecognised_headers(self):
        adapter = detect_platform({"User-Agent": "custom-bot/9.0"}, {})
        assert adapter.platform_name == "generic"

    def test_openclaw_beats_claude_cli(self):
        """If both signals are present, OpenClaw takes priority."""
        adapter = detect_platform(
            {"X-OpenClaw-Session": "s1", "User-Agent": "claude-cli/1.0"},
            {},
        )
        assert adapter.platform_name == "openclaw"

    def test_openclaw_beats_generic(self):
        adapter = detect_platform({"X-OpenClaw-Session": "s1"}, {})
        assert adapter.platform_name == "openclaw"

    def test_returns_base_adapter_instance(self):
        adapter = detect_platform({}, {})
        assert isinstance(adapter, BaseAdapter)

    def test_env_defaults_to_os_environ(self):
        """detect_platform should work without explicit env arg (uses os.environ)."""
        # No OPENCLAW_SESSION or CLAUDE_CLI in real env during tests — should be generic.
        import os
        env_backup = os.environ.copy()
        os.environ.pop("OPENCLAW_SESSION", None)
        os.environ.pop("CLAUDE_CLI", None)
        try:
            adapter = detect_platform({})
            assert adapter.platform_name == "generic"
        finally:
            # Restore
            for k, v in env_backup.items():
                os.environ[k] = v
