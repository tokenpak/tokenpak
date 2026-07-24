"""Tests for TOKENPAK_PROFILE named workflow profile resolution."""

# ---------------------------------------------------------------------------
# Profile presets (duplicated here to avoid importing proxy.py at module load)
# ---------------------------------------------------------------------------
# Kept in sync with the authoritative table in tokenpak.proxy.config._PROFILE_PRESETS.
# test_local_presets_match_source() below guards against drift between the two.
_PROFILE_PRESETS = {
    "safe": {
        "TOKENPAK_MODE": "safe",
        "TOKENPAK_STABLE_CACHE_CONTROL_AUTO": "true",
        "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "8000",
        "TOKENPAK_SKELETON_ENABLED": "false",
        "TOKENPAK_CAPSULE_BUILDER": "false",
        "TOKENPAK_SHADOW_ENABLED": "true",
        "TOKENPAK_BUDGET_CONTROLLER": "true",
        "TOKENPAK_TRACE": "true",
    },
    "balanced": {
        "TOKENPAK_MODE": "hybrid",
        "TOKENPAK_COMPACT_THRESHOLD_TOKENS": "1500",
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
        "TOKENPAK_CAPSULE_BUILDER": "1",
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
    def test_safe_profile_sets_safe_mode(self):
        env = _simulate_profile_injection("safe")
        assert env["TOKENPAK_MODE"] == "safe"

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
        # Preset writes "1" so every downstream truthy gate agrees it is ON.
        assert env["TOKENPAK_CAPSULE_BUILDER"] == "1"

    def test_balanced_profile_hybrid_mode(self):
        env = _simulate_profile_injection("balanced")
        assert env["TOKENPAK_MODE"] == "hybrid"

    def test_balanced_profile_mid_threshold(self):
        env = _simulate_profile_injection("balanced")
        assert int(env["TOKENPAK_COMPACT_THRESHOLD_TOKENS"]) == 1500

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
        env = _simulate_profile_injection(
            "aggressive", {"TOKENPAK_COMPACT_THRESHOLD_TOKENS": "9999"}
        )
        assert env["TOKENPAK_COMPACT_THRESHOLD_TOKENS"] == "9999"

    def test_unknown_profile_leaves_env_unchanged(self):
        """Unknown profile name should not inject any keys."""
        env = _simulate_profile_injection("nonexistent")
        assert "TOKENPAK_MODE" not in env

    def test_all_profiles_set_shadow_enabled(self):
        for profile in _PROFILE_PRESETS:
            env = _simulate_profile_injection(profile)
            assert env.get("TOKENPAK_SHADOW_ENABLED") == "true", (
                f"Profile {profile} should enable shadow reader"
            )

    def test_all_profiles_set_budget_controller(self):
        for profile in _PROFILE_PRESETS:
            env = _simulate_profile_injection(profile)
            assert env.get("TOKENPAK_BUDGET_CONTROLLER") == "true", (
                f"Profile {profile} should enable budget controller"
            )

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


# ---------------------------------------------------------------------------
# Single-source-of-truth guards — the local copy above must never drift from
# the authoritative table in tokenpak.proxy.config.
# ---------------------------------------------------------------------------


class TestPresetSourceOfTruth:
    def test_local_presets_match_source(self):
        from tokenpak.proxy.config import _PROFILE_PRESETS as SOURCE

        assert _PROFILE_PRESETS == SOURCE, (
            "Local preset copy has drifted from tokenpak.proxy.config._PROFILE_PRESETS"
        )

    def test_explain_uses_source_presets(self):
        # `tokenpak explain` must render the authoritative table, not a private copy.
        import io
        from contextlib import redirect_stdout
        from types import SimpleNamespace

        from tokenpak._cli_core import cmd_explain
        from tokenpak.proxy.config import _PROFILE_PRESETS as SOURCE

        for name in ("balanced", "safe"):
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_explain(SimpleNamespace(profile=name))
            out = buf.getvalue()
            for key, value in SOURCE[name].items():
                assert f"{key:<40} = {value}" in out, (
                    f"explain --profile {name} missing {key}={value}"
                )

    def test_aggressive_capsule_gates_all_agree(self, monkeypatch):
        # Under the aggressive preset every request-path capsule-builder gate
        # must agree ON. These are function-level gates (no module reload).
        for var in ("TOKENPAK_CAPSULE_BUILDER", "TOKENPAK_CAPSULE_BUILDER_ENABLED"):
            monkeypatch.delenv(var, raising=False)

        from tokenpak.proxy.config import _PROFILE_PRESETS as SOURCE

        # Apply the preset value the same way proxy startup does.
        monkeypatch.setenv(
            "TOKENPAK_CAPSULE_BUILDER", SOURCE["aggressive"]["TOKENPAK_CAPSULE_BUILDER"]
        )

        import tokenpak.core.config as core_config
        import tokenpak.proxy.capsule_builder as capsule_builder
        import tokenpak.proxy.capsule_integration as capsule_integration

        capsule_integration.clear_cache()
        try:
            gates = {
                "core.config": core_config.get_capsule_builder_enabled(),
                "capsule_integration": capsule_integration._is_capsule_enabled(),
                "capsule_builder": capsule_builder.make_capsule_builder()._enabled,
            }
            assert all(gates.values()), f"Capsule gates disagree: {gates}"
        finally:
            capsule_integration.clear_cache()

    def test_string_true_and_one_both_enable_capsule(self, monkeypatch):
        # Both "1" and "true" must be treated as ON at every request-path gate —
        # the original bug was that some gates only accepted exactly "1".
        import tokenpak.core.config as core_config
        import tokenpak.proxy.capsule_builder as capsule_builder
        import tokenpak.proxy.capsule_integration as capsule_integration

        monkeypatch.delenv("TOKENPAK_CAPSULE_BUILDER_ENABLED", raising=False)
        for value in ("1", "true", "TRUE", "yes", "on"):
            monkeypatch.setenv("TOKENPAK_CAPSULE_BUILDER", value)
            capsule_integration.clear_cache()
            try:
                assert core_config.get_capsule_builder_enabled() is True, value
                assert capsule_integration._is_capsule_enabled() is True, value
                assert capsule_builder.make_capsule_builder()._enabled is True, value
            finally:
                capsule_integration.clear_cache()
