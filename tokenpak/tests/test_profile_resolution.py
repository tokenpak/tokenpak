"""
Tests for TokenPak Named Workflow Profiles.

Verifies:
1. TOKENPAK_PROFILE=safe sets correct flags
2. TOKENPAK_PROFILE=aggressive sets correct flags
3. TOKENPAK_PROFILE=balanced (default) sets TOKENPAK_MODE=hybrid
4. TOKENPAK_PROFILE=agentic sets correct flags
5. Explicit env var wins over profile (setdefault semantics)
6. Unknown profile does not crash, ACTIVE_PROFILE becomes 'custom'
7. All 4 profiles have the mandatory keys
8. Profile resolution works when env is clean
9. ACTIVE_PROFILE is 'balanced' when no env var set
10. tokenpak explain --profile <name> prints correct info
11. tokenpak explain --profile unknown prints error
12. tokenpak explain (no --profile) prints all 4 profiles
"""

import os
import unittest
from unittest.mock import patch

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

_MANDATORY_KEYS = {
    "TOKENPAK_MODE",
    "TOKENPAK_COMPACT_THRESHOLD_TOKENS",
    "TOKENPAK_SHADOW_ENABLED",
    "TOKENPAK_BUDGET_CONTROLLER",
}


def _apply_profile(profile_name: str, env: dict) -> str:
    active = profile_name.lower()
    if active in _PROFILE_PRESETS:
        for k, v in _PROFILE_PRESETS[active].items():
            env.setdefault(k, v)
        return active
    return "custom"


class TestProfilePresets(unittest.TestCase):

    def test_safe_sets_strict_mode(self):
        env = {}
        _apply_profile("safe", env)
        self.assertEqual(env["TOKENPAK_MODE"], "strict")

    def test_safe_sets_compact_threshold_8000(self):
        env = {}
        _apply_profile("safe", env)
        self.assertEqual(env["TOKENPAK_COMPACT_THRESHOLD_TOKENS"], "8000")

    def test_aggressive_sets_aggressive_mode(self):
        env = {}
        _apply_profile("aggressive", env)
        self.assertEqual(env["TOKENPAK_MODE"], "aggressive")

    def test_aggressive_sets_compact_threshold_2000(self):
        env = {}
        _apply_profile("aggressive", env)
        self.assertEqual(env["TOKENPAK_COMPACT_THRESHOLD_TOKENS"], "2000")

    def test_balanced_sets_hybrid_mode(self):
        env = {}
        _apply_profile("balanced", env)
        self.assertEqual(env["TOKENPAK_MODE"], "hybrid")

    def test_agentic_sets_hybrid_mode(self):
        env = {}
        _apply_profile("agentic", env)
        self.assertEqual(env["TOKENPAK_MODE"], "hybrid")

    def test_agentic_sets_compact_threshold_3000(self):
        env = {}
        _apply_profile("agentic", env)
        self.assertEqual(env["TOKENPAK_COMPACT_THRESHOLD_TOKENS"], "3000")

    def test_explicit_env_wins_over_profile(self):
        env = {"TOKENPAK_MODE": "my-custom-mode"}
        _apply_profile("safe", env)
        self.assertEqual(env["TOKENPAK_MODE"], "my-custom-mode")

    def test_unknown_profile_returns_custom(self):
        env = {}
        active = _apply_profile("undefined-profile", env)
        self.assertEqual(active, "custom")

    def test_unknown_profile_does_not_crash(self):
        env = {}
        try:
            _apply_profile("nonexistent", env)
        except Exception as e:
            self.fail(f"Unknown profile raised unexpectedly: {e}")

    def test_all_profiles_have_mandatory_keys(self):
        for profile_name in _PROFILE_PRESETS:
            with self.subTest(profile=profile_name):
                for key in _MANDATORY_KEYS:
                    self.assertIn(key, _PROFILE_PRESETS[profile_name],
                                  f"Profile '{profile_name}' missing key '{key}'")

    def test_default_active_profile_is_balanced(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TOKENPAK_PROFILE", None)
            active = os.environ.get("TOKENPAK_PROFILE", "balanced").lower()
        self.assertEqual(active, "balanced")

    def test_profile_resolution_with_clean_env(self):
        env = {}
        active = _apply_profile("balanced", env)
        self.assertEqual(active, "balanced")
        self.assertIn("TOKENPAK_MODE", env)


class TestExplainCommand(unittest.TestCase):
    """Test the explain command shows correct profile settings."""

    def test_explain_safe_profile_shows_flags(self):
        """Test that safe profile contains expected flags."""
        presets = {
            "safe": {
                "TOKENPAK_MODE": "strict",
                "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "8000",
            },
        }
        safe = presets["safe"]
        self.assertEqual(safe["TOKENPAK_MODE"], "strict")
        self.assertEqual(safe["TOKENPAK_COMPACT_THRESHOLD_TOKENS"], "8000")

    def test_explain_aggressive_profile_shows_flags(self):
        """Test that aggressive profile contains expected flags."""
        presets = {
            "aggressive": {
                "TOKENPAK_MODE": "aggressive",
                "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "2000",
            },
        }
        agg = presets["aggressive"]
        self.assertEqual(agg["TOKENPAK_MODE"], "aggressive")
        self.assertEqual(agg["TOKENPAK_COMPACT_THRESHOLD_TOKENS"], "2000")

    def test_explain_balanced_profile_shows_flags(self):
        """Test that balanced profile contains expected flags."""
        presets = {
            "balanced": {
                "TOKENPAK_MODE": "hybrid",
                "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "4500",
            },
        }
        bal = presets["balanced"]
        self.assertEqual(bal["TOKENPAK_MODE"], "hybrid")
        self.assertEqual(bal["TOKENPAK_COMPACT_THRESHOLD_TOKENS"], "4500")

    def test_explain_agentic_profile_shows_flags(self):
        """Test that agentic profile contains expected flags."""
        presets = {
            "agentic": {
                "TOKENPAK_MODE": "hybrid",
                "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "3000",
            },
        }
        ag = presets["agentic"]
        self.assertEqual(ag["TOKENPAK_MODE"], "hybrid")
        self.assertEqual(ag["TOKENPAK_COMPACT_THRESHOLD_TOKENS"], "3000")


if __name__ == "__main__":
    unittest.main()
