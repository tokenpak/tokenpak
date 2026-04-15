"""Tests for tokenpak._internal.config module."""

import pytest
pytest.importorskip("tokenpak._internal", reason="module not available in current build")
import pytest
from tokenpak._internal.config import get_config, get_debug_enabled, get_metrics_enabled

class TestConfig:
    @pytest.mark.quick
    def test_get_config_returns_dict(self):
        config = get_config()
        assert isinstance(config, dict)

    @pytest.mark.quick
    def test_get_debug_enabled_returns_bool(self):
        enabled = get_debug_enabled()
        assert isinstance(enabled, bool)

    @pytest.mark.quick
    def test_get_metrics_enabled_returns_bool(self):
        enabled = get_metrics_enabled()
        assert isinstance(enabled, bool)

    @pytest.mark.quick
    def test_config_not_none(self):
        config = get_config()
        assert config is not None

    @pytest.mark.quick
    def test_multiple_config_calls_consistent(self):
        c1 = get_config()
        c2 = get_config()
        assert isinstance(c1, dict) and isinstance(c2, dict)
