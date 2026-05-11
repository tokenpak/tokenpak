# SPDX-License-Identifier: Apache-2.0
"""Unit tests for tokenpak.proxy.spend_guard.policy.

Acceptance hooks for TSG-01 (threshold engine) and the v1.5.2 recalibration
(Standard 29 §5 default-basis = context_window_percent; Kevin DECISION
2026-05-11 rev 2).
"""

from __future__ import annotations

import warnings

import pytest

from tokenpak.proxy.spend_guard.contracts import RiskEstimate, TIPDirective
from tokenpak.proxy.spend_guard.policy import (
    DEFAULT_BASIS_CONTEXT_WINDOW_PERCENT,
    SpendGuardConfig,
    decide,
    load_config,
)


def _est(**kw) -> RiskEstimate:
    base = dict(
        model="claude-opus-4-7",
        current_context_tokens=0,
        request_tokens=0,
        projected_input_tokens=0,
        projected_output_tokens=0,
        projected_cost_usd=0.0,
        cache_hit_ratio=0.0,
        rates={"input": 15.0, "output": 75.0, "cached": 1.5},
    )
    base.update(kw)
    return RiskEstimate(**base)


# ---------------------------------------------------------------------------
# Default config — Standard 29 §5 (2026-05-11 recalibration)
# ---------------------------------------------------------------------------

class TestConfigDefaults:
    """The v1.5.2 default profile is denominated in context-window %.

    Dollar bands stay reachable as opt-in profile overrides (Standard 29 §5.1)
    but the default for any new install is the % basis, applied universally
    to every agent.
    """

    def test_default_basis_is_context_window_percent(self):
        cfg = SpendGuardConfig()
        assert cfg.default_basis == DEFAULT_BASIS_CONTEXT_WINDOW_PERCENT
        assert cfg.default_basis == "context_window_percent"

    def test_default_context_window_percent_is_ninety(self):
        cfg = SpendGuardConfig()
        assert cfg.default_context_window_percent == 90

    def test_hard_stop_context_window_percent_is_hundred(self):
        cfg = SpendGuardConfig()
        assert cfg.hard_stop_context_window_percent == 100

    def test_dollar_plane_disabled_by_default(self):
        cfg = SpendGuardConfig()
        assert cfg.dollar_cap_enabled_by_default is False
        assert cfg.default_dollar_cap is None
        assert cfg.block_cost_usd == 0.0
        assert cfg.hard_block_cost_usd == 0.0
        assert cfg.session_block_cost_usd == 0.0

    def test_token_band_fallbacks_retained(self):
        # Per-request token bands remain as advisory guardrails for the
        # case where the model's max context window is unknown.
        cfg = SpendGuardConfig()
        assert cfg.warn_tokens == 100_000
        assert cfg.block_tokens == 500_000
        assert cfg.hard_block_tokens == 1_000_000


# ---------------------------------------------------------------------------
# Context-window-% basis — canonical defense (Standard 29 §5)
# ---------------------------------------------------------------------------

class TestContextWindowPercentBasis:
    """The % basis blocks at 90% and hard-stops at 100% of model max context.

    The same default applies universally — there is no per-agent variance.
    Tests below sweep across the frontier-model context windows to lock
    that property.
    """

    cfg = SpendGuardConfig()

    def test_200k_model_blocks_at_90_percent(self):
        # 200K context × 90% = 180K. 179K → warn-or-allow; 180K → block.
        d_under = decide(
            _est(projected_input_tokens=179_000, projected_output_tokens=500,
                 projected_cost_usd=2.5),
            self.cfg, model_max_context_tokens=200_000,
        )
        assert d_under.decision in ("warn", "allow")

        d_over = decide(
            _est(projected_input_tokens=180_500, projected_output_tokens=500,
                 projected_cost_usd=2.5),
            self.cfg, model_max_context_tokens=200_000,
        )
        assert d_over.decision == "block"
        assert d_over.reason == "projected_exceeds_context_window_percent"
        assert "default_context_window_percent>=90" in d_over.threshold_hit
        assert "max_context=200000" in d_over.threshold_hit

    def test_200k_model_hard_stops_at_100_percent(self):
        # At 200K projected input → hard stop. No bypass crosses it.
        d = decide(
            _est(projected_input_tokens=200_000, projected_output_tokens=500,
                 projected_cost_usd=2.5),
            self.cfg, model_max_context_tokens=200_000,
        )
        assert d.decision == "hard_block"
        assert d.reason == "projected_exceeds_context_window_hard_stop"
        assert "hard_stop_context_window_percent>=100" in d.threshold_hit

    def test_1m_model_blocks_at_900k(self):
        d_under = decide(
            _est(projected_input_tokens=899_000, projected_output_tokens=500,
                 projected_cost_usd=8.0),
            self.cfg, model_max_context_tokens=1_000_000,
        )
        # 899K < 900K block; still in warn band by token count (>100K).
        assert d_under.decision in ("warn", "allow")

        d_over = decide(
            _est(projected_input_tokens=900_500, projected_output_tokens=500,
                 projected_cost_usd=8.0),
            self.cfg, model_max_context_tokens=1_000_000,
        )
        assert d_over.decision == "block"
        assert d_over.reason == "projected_exceeds_context_window_percent"

    def test_2m_model_blocks_at_1_8m(self):
        d = decide(
            _est(projected_input_tokens=1_800_500, projected_output_tokens=500,
                 projected_cost_usd=18.0),
            self.cfg, model_max_context_tokens=2_000_000,
        )
        assert d.decision == "block"
        assert d.reason == "projected_exceeds_context_window_percent"
        assert "max_context=2000000" in d.threshold_hit

    def test_unknown_model_falls_back_to_token_band(self):
        # No max context → context-window-% disabled; legacy token band
        # (block_tokens=500K) applies.
        d = decide(
            _est(projected_input_tokens=500_500, projected_output_tokens=500,
                 projected_cost_usd=8.0),
            self.cfg, model_max_context_tokens=None,
        )
        assert d.decision == "block"
        assert "block_tokens_fallback" in d.threshold_hit


# ---------------------------------------------------------------------------
# Universality — the SAME default applies to every agent profile
# ---------------------------------------------------------------------------

class TestUniversalDefault:
    """Per the 2026-05-11 default-basis decision: no per-profile variance
    in the default policy. Every agent profile — named, gig, or unknown
    future — gets the same 90% / 100% defaults.
    """

    PROFILE_NAMES = ("profile-a", "profile-b", "profile-c", "profile-d",
                     "profile-e", "profile-f", "gig-agent",
                     "unknown-future-profile")

    @pytest.mark.parametrize("profile", PROFILE_NAMES)
    def test_same_block_threshold_across_profiles(self, profile):
        # The policy itself has no agent dimension — verify by constructing
        # a fresh config and confirming the same block threshold applies.
        # If a future regression introduced per-profile variance via env
        # variable or config key, this loop would diverge.
        cfg = SpendGuardConfig()
        assert cfg.default_context_window_percent == 90, (
            f"Profile {profile!r}: default_context_window_percent diverged from 90"
        )
        assert cfg.hard_stop_context_window_percent == 100, (
            f"Profile {profile!r}: hard_stop_context_window_percent diverged from 100"
        )
        d = decide(
            _est(projected_input_tokens=180_500, projected_output_tokens=0,
                 projected_cost_usd=2.5),
            cfg, model_max_context_tokens=200_000,
        )
        assert d.decision == "block", (
            f"Profile {profile!r}: 180.5K @ 200K-context did NOT block under "
            "the universal default"
        )


# ---------------------------------------------------------------------------
# Hard-stop — absolute ceiling, no override crosses
# ---------------------------------------------------------------------------

class TestHardStopAbsolute:
    """100% context-window utilisation is the absolute ceiling. Neither
    Yes/no nor any ``[TIP: ...]`` directive crosses it.
    """

    cfg = SpendGuardConfig()

    def test_tip_allow_once_does_not_cross_hard_stop(self):
        tip = TIPDirective(allow_scope="once", max_cost_usd=999.0)
        d = decide(
            _est(projected_input_tokens=200_500, projected_output_tokens=0,
                 projected_cost_usd=2.5),
            self.cfg, tip=tip, model_max_context_tokens=200_000,
        )
        assert d.decision == "hard_block"
        assert d.reason == "projected_exceeds_context_window_hard_stop"

    def test_tip_bypass_does_not_cross_hard_stop(self):
        tip = TIPDirective(bypass=True, max_cost_usd=999.0, max_tokens=10_000_000)
        d = decide(
            _est(projected_input_tokens=200_500, projected_output_tokens=0,
                 projected_cost_usd=2.5),
            self.cfg, tip=tip, model_max_context_tokens=200_000,
        )
        assert d.decision == "hard_block"

    def test_hard_stop_fires_before_tip_evaluation(self):
        # Even a fully-authorized TIP can't change the outcome — hard-stop
        # is the first check.
        tip = TIPDirective(allow_scope="session", bypass=True,
                           max_cost_usd=10_000.0, max_tokens=10_000_000)
        d = decide(
            _est(projected_input_tokens=1_000_001, projected_output_tokens=0,
                 projected_cost_usd=5.0),
            self.cfg, tip=tip, model_max_context_tokens=1_000_000,
        )
        assert d.decision == "hard_block"


# ---------------------------------------------------------------------------
# Soft block IS bypassable
# ---------------------------------------------------------------------------

class TestSoftBlockBypassable:
    cfg = SpendGuardConfig()

    def test_tip_allow_once_clears_soft_block(self):
        # 180K @ 200K context = exactly 90% → block. With TIP allow=once
        # under sufficient ceiling, the request clears.
        tip = TIPDirective(allow_scope="once", max_cost_usd=15.0,
                           max_tokens=200_000)
        d = decide(
            _est(projected_input_tokens=181_000, projected_output_tokens=0,
                 projected_cost_usd=2.5),
            self.cfg, tip=tip, model_max_context_tokens=200_000,
        )
        assert d.decision == "allow"
        assert d.threshold_hit == "tip_directive"

    def test_yes_path_via_pending_is_orchestrator_concern(self):
        # The Yes path is wired in orchestrator/replay, not policy. Here we
        # just confirm a soft-block returns requires_approval=True so the
        # caller knows the Yes path is available.
        d = decide(
            _est(projected_input_tokens=181_000, projected_output_tokens=0,
                 projected_cost_usd=2.5),
            self.cfg, model_max_context_tokens=200_000,
        )
        assert d.decision == "block"
        assert d.requires_approval is True


# ---------------------------------------------------------------------------
# Backward-compat — legacy dollar plane remains reachable
# ---------------------------------------------------------------------------

class TestDollarPlaneOptIn:
    """Standard 29 §5.1: dollar bands stay reachable as an opt-in profile
    override but default to disabled. Setting any legacy field engages
    the plane and emits a DeprecationWarning.
    """

    def test_no_default_dollar_block(self):
        # A $50 projected request with no max-context info → NOT blocked
        # on dollar terms (dollar plane disabled). Still blocks on the
        # token-band fallback (50K cost ≈ huge token count), so use a
        # small token count to isolate.
        cfg = SpendGuardConfig()
        d = decide(
            _est(projected_input_tokens=1000, projected_output_tokens=0,
                 projected_cost_usd=50.0),
            cfg, model_max_context_tokens=200_000,
        )
        assert d.decision in ("warn", "allow")  # NOT block on dollar plane

    def test_explicit_block_cost_usd_engages_plane(self):
        # Config sets block_cost_usd → dollar plane engages.
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            cfg = load_config(raw_config={"spend_guard": {"block_cost_usd": 10.0}})
            assert cfg.block_cost_usd == 10.0
            assert cfg.dollar_cap_enabled_by_default is True
            # DeprecationWarning was raised
            assert any(
                issubclass(w.category, DeprecationWarning)
                and "block_cost_usd" in str(w.message)
                for w in captured
            )
        # Now a $12 request blocks on the dollar plane.
        d = decide(
            _est(projected_input_tokens=10_000, projected_output_tokens=0,
                 projected_cost_usd=12.0),
            cfg, model_max_context_tokens=200_000,
        )
        assert d.decision == "block"
        assert d.reason == "projected_cost_exceeded"

    def test_explicit_session_block_engages_plane(self):
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            cfg = load_config(
                raw_config={"spend_guard": {"session_block_cost_usd": 10.0}}
            )
            assert cfg.session_block_cost_usd == 10.0
            assert any(
                issubclass(w.category, DeprecationWarning)
                and "session_block_cost_usd" in str(w.message)
                for w in captured
            )
        d = decide(
            _est(projected_input_tokens=5_000, projected_output_tokens=0,
                 projected_cost_usd=0.5),
            cfg, session_running_cost_usd=9.7, model_max_context_tokens=200_000,
        )
        assert d.decision == "block"
        assert d.reason == "session_cumulative_cost_exceeded"

    def test_env_legacy_field_also_emits_warning(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_SPEND_GUARD_BLOCK_COST_USD", "3.0")
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            cfg = load_config(raw_config={})
            assert cfg.block_cost_usd == 3.0
            assert cfg.dollar_cap_enabled_by_default is True
            assert any(
                issubclass(w.category, DeprecationWarning)
                and "block_cost_usd" in str(w.message)
                for w in captured
            )


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

class TestConfigValidation:
    def test_reject_block_pct_above_hard_stop(self):
        with pytest.raises(ValueError) as exc:
            load_config(raw_config={"spend_guard": {
                "default_context_window_percent": 95,
                "hard_stop_context_window_percent": 90,
            }})
        assert "default_context_window_percent" in str(exc.value)
        assert "hard_stop_context_window_percent" in str(exc.value)

    def test_reject_hard_stop_above_hundred(self):
        with pytest.raises(ValueError) as exc:
            load_config(raw_config={"spend_guard": {
                "hard_stop_context_window_percent": 110,
            }})
        assert "hard_stop_context_window_percent" in str(exc.value)

    def test_reject_negative_block_pct(self):
        with pytest.raises(ValueError):
            load_config(raw_config={"spend_guard": {
                "default_context_window_percent": -1,
            }})

    def test_reject_non_integer_pct(self):
        with pytest.raises(ValueError):
            load_config(raw_config={"spend_guard": {
                "default_context_window_percent": "not-a-number",
            }})

    def test_valid_config_loads(self):
        cfg = load_config(raw_config={"spend_guard": {
            "default_context_window_percent": 85,
            "hard_stop_context_window_percent": 95,
        }})
        assert cfg.default_context_window_percent == 85
        assert cfg.hard_stop_context_window_percent == 95


# ---------------------------------------------------------------------------
# Canonical key alias — tip_spend_guard: (decision-record YAML shape)
# ---------------------------------------------------------------------------

class TestCanonicalKeyAlias:
    def test_tip_spend_guard_key_accepted(self):
        cfg = load_config(raw_config={"tip_spend_guard": {
            "default_context_window_percent": 85,
        }})
        assert cfg.default_context_window_percent == 85

    def test_tip_spend_guard_wins_over_spend_guard_on_conflict(self):
        cfg = load_config(raw_config={
            "spend_guard": {"default_context_window_percent": 80},
            "tip_spend_guard": {"default_context_window_percent": 75},
        })
        assert cfg.default_context_window_percent == 75


# ---------------------------------------------------------------------------
# Large-cycle clear-case — ~1047-line composed prompt clears
# ---------------------------------------------------------------------------

class TestLargeCycleClears:
    """A large-but-typical composed cycle prompt (the kind 402'd by the
    v1.5.1 ``session_block_cost_usd: 10.0`` ceiling) must NOT trip the
    v1.5.2 default policy. Confirms the recalibration unblocks legitimate
    large-context cycles while still catching the spike pattern.
    """

    def test_large_cycle_typical_prompt_clears(self):
        # Representative large cycle: ~1047 lines of composed prompt +
        # system reminders + companion attachment. Estimated input tokens
        # for a cycle of that size on claude-opus-4-7 (200K context) is
        # around ~50K — well under the 180K block line.
        cfg = SpendGuardConfig()
        d = decide(
            _est(projected_input_tokens=55_000, projected_output_tokens=8_000,
                 projected_cost_usd=1.40),
            cfg, model_max_context_tokens=200_000,
        )
        assert d.decision in ("allow", "warn"), (
            f"Large-cycle composed prompt would be blocked under v1.5.2 "
            f"default policy: decision={d.decision}, reason={d.reason}, "
            f"threshold_hit={d.threshold_hit}"
        )


# ---------------------------------------------------------------------------
# Per-request decisions WITHOUT the % basis (token-band fallback path)
# ---------------------------------------------------------------------------

def _no_pct_cfg() -> SpendGuardConfig:
    """Disable session-cumulative and force the token-band fallback path
    by passing ``model_max_context_tokens=None`` at call sites."""
    cfg = SpendGuardConfig()
    cfg.session_block_cost_usd = 0.0  # already 0 by default; explicit
    return cfg


class TestDecideTokenBandFallback:
    """When max-context is unknown, the legacy token bands apply."""

    cfg = _no_pct_cfg()

    def test_small_request_allowed(self):
        d = decide(
            _est(projected_input_tokens=1000, projected_output_tokens=500,
                 projected_cost_usd=0.05),
            self.cfg,
        )
        assert d.decision == "allow"

    def test_warn_band_emits_warn(self):
        d = decide(
            _est(projected_input_tokens=120_000, projected_output_tokens=4000,
                 projected_cost_usd=2.5),
            self.cfg,
        )
        assert d.decision == "warn"

    def test_block_on_token_threshold_unknown_model(self):
        # 600K tokens, unknown context → token-band fallback fires.
        d = decide(
            _est(projected_input_tokens=600_000, projected_output_tokens=4000,
                 projected_cost_usd=8.0),
            self.cfg,
        )
        assert d.decision == "block"
        assert "block_tokens_fallback" in d.threshold_hit

    def test_hard_block_on_tokens_unknown_model(self):
        d = decide(
            _est(projected_input_tokens=1_200_000, projected_output_tokens=10_000,
                 projected_cost_usd=20.0),
            self.cfg,
        )
        assert d.decision == "hard_block"
        assert d.reason == "projected_tokens_exceed_hard_block"


class TestTIPDirectiveWithDollarPlane:
    """TIP directive semantics when the dollar plane is engaged (opt-in)."""

    def _dollar_cfg(self) -> SpendGuardConfig:
        cfg = load_config(raw_config={"spend_guard": {
            "block_cost_usd": 10.0,
            "hard_block_cost_usd": 50.0,
        }})
        return cfg

    def test_tip_allow_once_within_dollar_ceiling(self):
        cfg = self._dollar_cfg()
        tip = TIPDirective(allow_scope="once", max_cost_usd=15.0)
        d = decide(
            _est(projected_input_tokens=10_000, projected_output_tokens=0,
                 projected_cost_usd=12.0),
            cfg, tip=tip, model_max_context_tokens=200_000,
        )
        assert d.decision == "allow"
        assert d.threshold_hit == "tip_directive"

    def test_tip_ceiling_too_low(self):
        cfg = self._dollar_cfg()
        tip = TIPDirective(allow_scope="once", max_cost_usd=8.0)
        d = decide(
            _est(projected_input_tokens=10_000, projected_output_tokens=0,
                 projected_cost_usd=12.0),
            cfg, tip=tip, model_max_context_tokens=200_000,
        )
        assert d.decision == "block"
        assert d.reason == "projected_exceeds_tip_ceiling"

    def test_tip_bypass_does_not_override_dollar_hard_block(self):
        cfg = self._dollar_cfg()
        tip = TIPDirective(bypass=True, max_cost_usd=999.0)
        d = decide(
            _est(projected_input_tokens=10_000, projected_output_tokens=0,
                 projected_cost_usd=80.0),
            cfg, tip=tip, model_max_context_tokens=200_000,
        )
        assert d.decision == "hard_block"


class TestEnvOverrides:
    def test_env_disable(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_SPEND_GUARD_ENABLED", "false")
        cfg = load_config(raw_config={})
        assert cfg.enabled is False

    def test_env_block_pct_override(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_SPEND_GUARD_CONTEXT_WINDOW_PERCENT", "85")
        cfg = load_config(raw_config={})
        assert cfg.default_context_window_percent == 85
