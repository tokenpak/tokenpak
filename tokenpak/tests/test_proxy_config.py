"""
Tests for tokenpak/proxy/config.py — configuration loading and validation.

Covers:
1. Module imports cleanly
2. Profile presets load and apply env var defaults
3. Config loads from default values (no env overrides)
4. Config loads from environment variable overrides
5. Boolean env var parsing (true/false/1/0 variants)
6. Int env var parsing with cast
7. Port default and override
8. Listen address default and override
9. Vault path default
10. Build upstream routes (env override)
11. Edge case: unknown profile falls back to custom
12. Edge case: empty env var not applied
"""

import importlib
import os
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reload_config(env_overrides: dict | None = None):
    """Reload the config module with a clean env, then restore."""
    saved = {k: v for k, v in os.environ.items() if k.startswith("TOKENPAK_")}
    # Remove all TOKENPAK_ vars
    for k in list(os.environ.keys()):
        if k.startswith("TOKENPAK_"):
            del os.environ[k]
    # Apply overrides
    if env_overrides:
        os.environ.update(env_overrides)

    # Force re-import
    module_name = "tokenpak.proxy.config"
    if module_name in sys.modules:
        del sys.modules[module_name]
    mod = importlib.import_module(module_name)

    # Restore original env
    for k in list(os.environ.keys()):
        if k.startswith("TOKENPAK_"):
            del os.environ[k]
    os.environ.update(saved)

    return mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConfigModuleImport:
    """Test 1: Module imports cleanly."""

    def test_import_succeeds(self):
        """Config module can be imported without raising."""
        import tokenpak.proxy.config as cfg  # noqa: F401 — just confirming import

        assert cfg is not None

    def test_module_has_expected_attributes(self):
        """Key config attributes are present after import."""
        import tokenpak.proxy.config as cfg

        expected = [
            "PROXY_PORT",
            "LISTEN_ADDRESS",
            "ACTIVE_PROFILE",
            "COMPILATION_MODE",
            "ENABLE_COMPACTION",
            "COMPACT_THRESHOLD_TOKENS",
            "VAULT_INDEX_PATH",
            "UPSTREAM_ROUTES",
            "ADAPTER_REGISTRY",
        ]
        for attr in expected:
            assert hasattr(cfg, attr), f"Missing expected attribute: {attr}"


class TestProfilePresets:
    """Test 2: Profile presets load and apply env var defaults."""

    def test_balanced_profile_is_default(self):
        cfg = _reload_config({})
        assert cfg.ACTIVE_PROFILE == "balanced"

    def test_safe_profile_sets_strict_mode(self):
        cfg = _reload_config({"TOKENPAK_PROFILE": "safe"})
        assert cfg.ACTIVE_PROFILE == "safe"
        # safe profile sets TOKENPAK_MODE=strict → COMPILATION_MODE
        assert cfg.COMPILATION_MODE == "strict"

    def test_aggressive_profile_lowers_threshold(self):
        cfg = _reload_config({"TOKENPAK_PROFILE": "aggressive"})
        assert cfg.ACTIVE_PROFILE == "aggressive"
        # aggressive profile sets threshold to 2000
        assert cfg.COMPACT_THRESHOLD_TOKENS == 2000

    def test_unknown_profile_falls_back_to_custom(self):
        """Edge case: unrecognized profile → 'custom', no crash."""
        cfg = _reload_config({"TOKENPAK_PROFILE": "nonexistent_profile_xyz"})
        assert cfg.ACTIVE_PROFILE == "custom"


class TestDefaultValues:
    """Test 3: Config loads from default values (no env overrides)."""

    def test_default_proxy_port(self):
        cfg = _reload_config({})
        assert cfg.PROXY_PORT == 8766

    def test_default_listen_address(self):
        cfg = _reload_config({})
        assert cfg.LISTEN_ADDRESS == "127.0.0.1"

    def test_default_compact_max_chars(self):
        cfg = _reload_config({})
        assert cfg.COMPACT_MAX_CHARS == 120

    def test_default_inject_top_k(self):
        cfg = _reload_config({})
        assert cfg.INJECT_TOP_K == 5

    def test_default_inject_min_score(self):
        cfg = _reload_config({})
        assert cfg.INJECT_MIN_SCORE == pytest.approx(2.0)

    def test_default_vault_index_path_contains_home(self):
        cfg = _reload_config({})
        assert str(Path.home()) in cfg.VAULT_INDEX_PATH


class TestEnvVarOverrides:
    """Test 4: Config loads from environment variable overrides."""

    def test_proxy_port_from_env(self):
        cfg = _reload_config({"TOKENPAK_PORT": "9000"})
        assert cfg.PROXY_PORT == 9000

    def test_listen_address_from_env(self):
        cfg = _reload_config({"TOKENPAK_BIND_ADDRESS": "0.0.0.0"})
        assert cfg.LISTEN_ADDRESS == "0.0.0.0"

    def test_compact_threshold_from_env(self):
        cfg = _reload_config({"TOKENPAK_COMPACT_THRESHOLD_TOKENS": "3000"})
        assert cfg.COMPACT_THRESHOLD_TOKENS == 3000

    def test_compilation_mode_from_env(self):
        cfg = _reload_config({"TOKENPAK_MODE": "strict"})
        assert cfg.COMPILATION_MODE == "strict"

    def test_inject_budget_from_env(self):
        cfg = _reload_config({"TOKENPAK_INJECT_BUDGET": "6000"})
        assert cfg.INJECT_BUDGET == 6000

    def test_env_var_overrides_profile_preset(self):
        """Explicit env var wins over profile preset (setdefault semantics)."""
        cfg = _reload_config(
            {
                "TOKENPAK_PROFILE": "safe",  # safe preset: mode=strict
                "TOKENPAK_MODE": "aggressive",  # explicit override
            }
        )
        assert cfg.COMPILATION_MODE == "aggressive"


class TestBooleanParsing:
    """Test 5: Boolean env var parsing (true/false/1/0 variants)."""

    def test_bool_true_string(self):
        cfg = _reload_config({"TOKENPAK_COMPACT": "true"})
        assert cfg.ENABLE_COMPACTION is True

    def test_bool_false_string(self):
        cfg = _reload_config({"TOKENPAK_COMPACT": "false"})
        assert cfg.ENABLE_COMPACTION is False

    def test_bool_numeric_one(self):
        cfg = _reload_config({"TOKENPAK_COMPACT": "1"})
        assert cfg.ENABLE_COMPACTION is True

    def test_bool_numeric_zero(self):
        cfg = _reload_config({"TOKENPAK_COMPACT": "0"})
        assert cfg.ENABLE_COMPACTION is False

    def test_bool_yes_string(self):
        cfg = _reload_config({"TOKENPAK_COMPACT": "yes"})
        assert cfg.ENABLE_COMPACTION is True


class TestIntParsing:
    """Test 6: Int env var parsing with cast."""

    def test_int_cast_upstream_timeout(self):
        cfg = _reload_config({"TOKENPAK_UPSTREAM_TIMEOUT": "120"})
        assert cfg.UPSTREAM_TIMEOUT == 120
        assert isinstance(cfg.UPSTREAM_TIMEOUT, int)

    def test_int_cast_compact_cache_size(self):
        cfg = _reload_config({"TOKENPAK_COMPACT_CACHE_SIZE": "500"})
        assert cfg.COMPACT_CACHE_SIZE == 500
        assert isinstance(cfg.COMPACT_CACHE_SIZE, int)

    def test_int_cast_ws_port(self):
        cfg = _reload_config({"TOKENPAK_WS_PORT": "8800"})
        assert cfg.WS_PORT == 8800
        assert isinstance(cfg.WS_PORT, int)


class TestUpstreamRoutes:
    """Test 10: Build upstream routes (env override)."""

    def test_upstream_routes_is_dict(self):
        cfg = _reload_config({})
        assert isinstance(cfg.UPSTREAM_ROUTES, dict)

    def test_upstream_routes_not_empty(self):
        cfg = _reload_config({})
        assert len(cfg.UPSTREAM_ROUTES) > 0

    def test_upstream_env_override_applied(self):
        """TOKENPAK_UPSTREAM_ANTHROPIC_MESSAGES sets an env-override route."""
        cfg = _reload_config(
            {"TOKENPAK_UPSTREAM_ANTHROPIC_MESSAGES": "https://custom.anthropic.example.com"}
        )
        assert "anthropic-messages" in cfg.UPSTREAM_ROUTES
        assert cfg.UPSTREAM_ROUTES["anthropic-messages"] == "https://custom.anthropic.example.com"


class TestEdgeCases:
    """Test 12: Edge cases."""

    def test_empty_proxy_auth_key_is_empty_string(self):
        cfg = _reload_config({})
        assert cfg.PROXY_AUTH_KEY == ""

    def test_budget_daily_limit_default_zero(self):
        cfg = _reload_config({})
        assert cfg.BUDGET_DAILY_LIMIT_USD == 0.0

    def test_budget_alert_threshold_default(self):
        cfg = _reload_config({})
        assert cfg.BUDGET_ALERT_THRESHOLD_PCT == pytest.approx(80.0)

    def test_vault_index_reload_interval_constant(self):
        """VAULT_INDEX_RELOAD_INTERVAL is a fixed constant (300 seconds)."""
        cfg = _reload_config({})
        assert cfg.VAULT_INDEX_RELOAD_INTERVAL == 300

    def test_adapter_registry_has_adapters(self):
        """ADAPTER_REGISTRY is populated and has at least one adapter."""
        cfg = _reload_config({})
        formats = cfg.ADAPTER_REGISTRY.list_formats()
        assert len(formats) > 0
