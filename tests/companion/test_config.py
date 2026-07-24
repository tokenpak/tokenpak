# SPDX-License-Identifier: Apache-2.0
"""Tests for CompanionConfig — env var loading and profile overrides."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from tokenpak.companion.config import CompanionConfig

# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------


def test_default_config_enabled():
    """Default config has companion enabled."""
    with patch.dict(os.environ, {}, clear=False):
        # Remove companion vars so defaults apply
        env_clean = {k: v for k, v in os.environ.items() if not k.startswith("TOKENPAK_COMPANION_")}
        with patch.dict(os.environ, env_clean, clear=True):
            cfg = CompanionConfig.from_env()
    assert cfg.enabled is True


def test_default_profile_is_balanced():
    """Default profile is 'balanced'."""
    env_clean = {k: v for k, v in os.environ.items() if not k.startswith("TOKENPAK_COMPANION_")}
    with patch.dict(os.environ, env_clean, clear=True):
        cfg = CompanionConfig.from_env()
    assert cfg.profile == "balanced"


def test_default_budget_is_zero():
    """Default daily budget is 0 (unlimited)."""
    env_clean = {k: v for k, v in os.environ.items() if not k.startswith("TOKENPAK_COMPANION_")}
    with patch.dict(os.environ, env_clean, clear=True):
        cfg = CompanionConfig.from_env()
    assert cfg.budget_daily_usd == 0.0


def test_default_hooks_enabled():
    """Hooks are enabled by default."""
    env_clean = {k: v for k, v in os.environ.items() if not k.startswith("TOKENPAK_COMPANION_")}
    with patch.dict(os.environ, env_clean, clear=True):
        cfg = CompanionConfig.from_env()
    assert cfg.hooks_enabled is True


def test_default_prune_threshold():
    """Default prune threshold is 50000 tokens."""
    env_clean = {k: v for k, v in os.environ.items() if not k.startswith("TOKENPAK_COMPANION_")}
    with patch.dict(os.environ, env_clean, clear=True):
        cfg = CompanionConfig.from_env()
    assert cfg.prune_threshold == 50_000


# ---------------------------------------------------------------------------
# Env var overrides
# ---------------------------------------------------------------------------


def test_env_disable_companion():
    """TOKENPAK_COMPANION_ENABLED=0 disables the companion."""
    with patch.dict(os.environ, {"TOKENPAK_COMPANION_ENABLED": "0"}):
        cfg = CompanionConfig.from_env()
    assert cfg.enabled is False


def test_env_enable_companion_true_string():
    """TOKENPAK_COMPANION_ENABLED=true enables the companion."""
    with patch.dict(os.environ, {"TOKENPAK_COMPANION_ENABLED": "true"}):
        cfg = CompanionConfig.from_env()
    assert cfg.enabled is True


def test_env_enable_companion_yes_string():
    """TOKENPAK_COMPANION_ENABLED=yes enables the companion."""
    with patch.dict(os.environ, {"TOKENPAK_COMPANION_ENABLED": "yes"}):
        cfg = CompanionConfig.from_env()
    assert cfg.enabled is True


def test_env_budget_float():
    """TOKENPAK_COMPANION_BUDGET sets daily budget as float."""
    with patch.dict(os.environ, {"TOKENPAK_COMPANION_BUDGET": "5.50"}):
        cfg = CompanionConfig.from_env()
    assert cfg.budget_daily_usd == 5.50


def test_env_budget_invalid_falls_back_to_zero():
    """Invalid TOKENPAK_COMPANION_BUDGET falls back to 0.0."""
    with patch.dict(os.environ, {"TOKENPAK_COMPANION_BUDGET": "not-a-number"}):
        cfg = CompanionConfig.from_env()
    assert cfg.budget_daily_usd == 0.0


def test_env_profile_override():
    """TOKENPAK_COMPANION_PROFILE sets the profile name."""
    with patch.dict(os.environ, {"TOKENPAK_COMPANION_PROFILE": "lean"}):
        cfg = CompanionConfig.from_env()
    assert cfg.profile == "lean"


def test_env_journal_dir(tmp_path):
    """TOKENPAK_COMPANION_JOURNAL_DIR sets journal_dir path."""
    with patch.dict(os.environ, {"TOKENPAK_COMPANION_JOURNAL_DIR": str(tmp_path)}):
        cfg = CompanionConfig.from_env()
    assert cfg.journal_dir == tmp_path


def test_env_hooks_disabled():
    """TOKENPAK_COMPANION_HOOKS=0 disables hooks."""
    with patch.dict(os.environ, {"TOKENPAK_COMPANION_HOOKS": "0"}):
        cfg = CompanionConfig.from_env()
    assert cfg.hooks_enabled is False


def test_env_mcp_disabled():
    """TOKENPAK_COMPANION_MCP=0 disables MCP server."""
    with patch.dict(os.environ, {"TOKENPAK_COMPANION_MCP": "0"}):
        cfg = CompanionConfig.from_env()
    assert cfg.mcp_enabled is False


def test_env_show_cost_disabled():
    """TOKENPAK_COMPANION_SHOW_COST=0 disables cost display."""
    with patch.dict(os.environ, {"TOKENPAK_COMPANION_SHOW_COST": "0"}):
        cfg = CompanionConfig.from_env()
    assert cfg.show_cost is False


def test_env_prune_threshold():
    """TOKENPAK_COMPANION_PRUNE_THRESHOLD sets the token threshold."""
    with patch.dict(os.environ, {"TOKENPAK_COMPANION_PRUNE_THRESHOLD": "10000"}):
        cfg = CompanionConfig.from_env()
    assert cfg.prune_threshold == 10_000


# ---------------------------------------------------------------------------
# Profile overrides
# ---------------------------------------------------------------------------


def test_profile_lean_lowers_threshold():
    """lean profile sets prune_threshold to 20000."""
    with patch.dict(os.environ, {"TOKENPAK_COMPANION_PROFILE": "lean"}):
        cfg = CompanionConfig.from_env()
    cfg.profile_overrides()
    assert cfg.prune_threshold == 20_000
    assert cfg.show_cost is True  # lean also forces show_cost on


def test_profile_verbose_raises_threshold():
    """verbose profile sets prune_threshold to 100000."""
    with patch.dict(os.environ, {"TOKENPAK_COMPANION_PROFILE": "verbose"}):
        cfg = CompanionConfig.from_env()
    cfg.profile_overrides()
    assert cfg.prune_threshold == 100_000


def test_profile_balanced_no_override():
    """balanced profile leaves defaults unchanged."""
    env_clean = {k: v for k, v in os.environ.items() if not k.startswith("TOKENPAK_COMPANION_")}
    with patch.dict(os.environ, env_clean, clear=True):
        cfg = CompanionConfig.from_env()
    default_threshold = cfg.prune_threshold
    cfg.profile_overrides()
    assert cfg.prune_threshold == default_threshold


# ---------------------------------------------------------------------------
# run_dir property
# ---------------------------------------------------------------------------


def test_run_dir_is_under_home():
    """run_dir is always inside ~/.tokenpak/companion/run."""
    cfg = CompanionConfig()
    assert cfg.run_dir == Path.home() / ".tokenpak" / "companion" / "run"
