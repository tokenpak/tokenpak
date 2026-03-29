"""Tests for TOKENPAK_PROFILE named workflow profile resolution."""
import os
import importlib
import sys
import pytest


# ---------------------------------------------------------------------------
# Profile presets (duplicated here to avoid importing proxy.py at module load)
# ---------------------------------------------------------------------------
_PROFILE_PRESETS = {
    "safe": {
        "TOKENPAK_MODE": "strict",
        "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "8000",
        "TOKENPAK_SKELETON_ENABLED": "false",
        "TOKENPAK_CAPSULE_BUILDER": "false",
        "TOKENPAK_SHADOW_ENABLED": "true",
        "TOKENPAK_BUDGET_CONTROLLER": "true",
        "TOKENPAK_TRACE": "true",
    },
    "balanced": {
        "TOKENPAK_MODE": "hybrid",
        "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "4500",
        "TOKENPAK_SKELETON_ENABLED": "true",
        "TOKENPAK_CAPSULE_BUILDER": "false",
        "TOKENPAK_SHADOW_ENABLED": "true",
        "TOKENPAK_BUDGET_CONTROLLER": "true",
        "TOKENPAK_TRACE": "true",
    },
    "aggressive": {
        "TOKENPAK_MODE": "aggressive",
        "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "2000",
        "TOKENPAK_SKELETON_ENABLED": "true",
        "TOKENPAK_CAPSULE_BUILDER": "true",
        "TOKENPAK_SHADOW_ENABLED": "true",
        "TOKENPAK_BUDGET_CONTROLLER": "true",
        "TOKENPAK_TRACE": "true",
    },
    "agentic": {
        "TOKENPAK_MODE": "hybrid",
        "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "3000",
        "TOKENPAK_SKELETON_ENABLED": "true",
        "TOKENPAK_CAPSULE_BUILDER": "false",
        "TOKENPAK_SHADOW_ENABLED": "true",
        "TOKENPAK_BUDGET_CONTROLLER": "true",
        "TOKENPAK_TRACE": "true",
    },
}

ALL_PROFILE_KEYS = {
    "TOKENPAK_MODE",
    "TOKENPAK_COMPACT_THRESHOLD_TOKENS",
    "TOKENPAK_SKELETON_ENABLED",
    "TOKENPAK_CAPSULE_BUILDER",
    "TOKENPAK_SHADOW_ENABLED",
    "TOKENPAK_BUDGET_CONTROLLER",
    "TOKENPAK_TRACE",
}


def _simulate_profile_injection(profile_name: str, env_overrides: dict | None = None) -> dict:
    """
    Simulate the setdefault-based profile injection as it happens in proxy.py.
    Returns the resulting env var values for all profile keys.
    """
    # Start clean env with only the overrides
    sim_env = dict(env_overrides or {})

    if profile_name in _PROFILE_PRESETS:
        for k, v in _PROFILE_PRESETS[profile_name].items():
            sim_env.setdefault(k, v)

    return sim_env


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestProfileResolution:
    def test_safe_profile_sets_strict_mode(self):
        env = _simulate_profile_injection("safe")
        assert env["TOKENPAK_MODE"] == "strict"

    def test_safe_profile_high_threshold(self):
        env = _simulate_profile_injection("safe")
        assert int(env["TOKENPAK_COMPACT_THRESHOLD_TOKENS"]) == 8000

    def test_safe_profile_disables_skeleton(self):
        env = _simulate_profile_injection("safe")
        assert env["TOKENPAK_SKELETON_ENABLED"] == "false"

    def test_safe_profile_disables_capsule(self):
        env = _simulate_profile_injection("safe")
        assert env["TOKENPAK_CAPSULE_BUILDER"] == "false"

    def test_aggressive_profile_sets_aggressive_mode(self):
        env = _simulate_profile_injection("aggressive")
        assert env["TOKENPAK_MODE"] == "aggressive"

    def test_aggressive_profile_low_threshold(self):
        env = _simulate_profile_injection("aggressive")
        assert int(env["TOKENPAK_COMPACT_THRESHOLD_TOKENS"]) == 2000

    def test_aggressive_profile_enables_capsule(self):
        env = _simulate_profile_injection("aggressive")
        assert env["TOKENPAK_CAPSULE_BUILDER"] == "true"

    def test_balanced_profile_hybrid_mode(self):
        env = _simulate_profile_injection("balanced")
        assert env["TOKENPAK_MODE"] == "hybrid"

    def test_balanced_profile_mid_threshold(self):
        env = _simulate_profile_injection("balanced")
        assert int(env["TOKENPAK_COMPACT_THRESHOLD_TOKENS"]) == 4500

    def test_agentic_profile_preserves_schemas(self):
        """Agentic profile disables capsule builder to protect tool schemas."""
        env = _simulate_profile_injection("agentic")
        assert env["TOKENPAK_CAPSULE_BUILDER"] == "false"

    def test_agentic_profile_conservative_threshold(self):
        env = _simulate_profile_injection("agentic")
        assert int(env["TOKENPAK_COMPACT_THRESHOLD_TOKENS"]) == 3000

    def test_explicit_env_override_wins_over_profile(self):
        """Explicit env var beats profile — setdefault semantics."""
        env = _simulate_profile_injection("safe", {"TOKENPAK_MODE": "hybrid"})
        assert env["TOKENPAK_MODE"] == "hybrid", "Explicit env var should override profile"

    def test_explicit_threshold_override_wins(self):
        env = _simulate_profile_injection("aggressive", {"TOKENPAK_COMPACT_THRESHOLD_TOKENS": "9999"})
        assert env["TOKENPAK_COMPACT_THRESHOLD_TOKENS"] == "9999"

    def test_unknown_profile_leaves_env_unchanged(self):
        """Unknown profile name should not inject any keys."""
        env = _simulate_profile_injection("nonexistent")
        assert "TOKENPAK_MODE" not in env

    def test_all_profiles_set_shadow_enabled(self):
        for profile in _PROFILE_PRESETS:
            env = _simulate_profile_injection(profile)
            assert env.get("TOKENPAK_SHADOW_ENABLED") == "true", f"Profile {profile} should enable shadow reader"

    def test_all_profiles_set_budget_controller(self):
        for profile in _PROFILE_PRESETS:
            env = _simulate_profile_injection(profile)
            assert env.get("TOKENPAK_BUDGET_CONTROLLER") == "true", f"Profile {profile} should enable budget controller"

    def test_all_profiles_set_trace(self):
        for profile in _PROFILE_PRESETS:
            env = _simulate_profile_injection(profile)
            assert env.get("TOKENPAK_TRACE") == "true", f"Profile {profile} should enable trace"

    def test_all_four_profiles_defined(self):
        assert set(_PROFILE_PRESETS.keys()) == {"safe", "balanced", "aggressive", "agentic"}

    def test_all_profiles_have_all_keys(self):
        for profile, flags in _PROFILE_PRESETS.items():
            missing = ALL_PROFILE_KEYS - set(flags.keys())
            assert not missing, f"Profile {profile!r} missing keys: {missing}"
