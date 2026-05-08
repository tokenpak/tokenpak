# SPDX-License-Identifier: Apache-2.0
"""Unit tests for tokenpak.proxy.spend_guard.policy.

Acceptance hooks for TSG-01: threshold engine.
"""

from __future__ import annotations

from tokenpak.proxy.spend_guard.contracts import RiskEstimate, TIPDirective
from tokenpak.proxy.spend_guard.policy import SpendGuardConfig, decide, load_config


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


class TestKevinDefaults:
    def test_block_thresholds_match_overrides(self):
        cfg = SpendGuardConfig()
        assert cfg.block_tokens == 500_000      # Kevin override (was 250_000)
        assert cfg.block_cost_usd == 10.0       # Kevin override (was 5.0)
        assert cfg.hard_block_tokens == 1_000_000
        assert cfg.hard_block_cost_usd == 50.0


def _per_request_cfg() -> SpendGuardConfig:
    """Disable the session-cumulative check so the per-request bands are
    the only thing under test in this class."""
    cfg = SpendGuardConfig()
    cfg.session_block_cost_usd = 0.0
    return cfg


class TestDecide:
    cfg = _per_request_cfg()

    def test_small_request_allowed(self):
        d = decide(_est(projected_input_tokens=1000, projected_output_tokens=500,
                        projected_cost_usd=0.05), self.cfg)
        assert d.decision == "allow"
        assert d.requires_approval is False

    def test_warn_band_emits_warn(self):
        d = decide(_est(projected_input_tokens=120_000, projected_output_tokens=4000,
                        projected_cost_usd=2.5), self.cfg)
        assert d.decision == "warn"
        assert d.requires_approval is False

    def test_block_on_token_threshold(self):
        # Acceptance from TSG-01 packet: 230K context + 18K request = projected
        # 248K. With Kevin's 500K threshold, that should NOT block. But with
        # 600K it should block. We test the latter to verify the engine works.
        d = decide(_est(projected_input_tokens=600_000, projected_output_tokens=4000,
                        projected_cost_usd=8.0), self.cfg)
        assert d.decision == "block"
        assert d.reason == "projected_tokens_exceeded"
        assert d.requires_approval is True

    def test_block_on_cost_threshold(self):
        d = decide(_est(projected_input_tokens=200_000, projected_output_tokens=10_000,
                        projected_cost_usd=12.0), self.cfg)
        assert d.decision == "block"
        assert d.reason == "projected_cost_exceeded"
        assert d.requires_approval is True

    def test_hard_block_on_cost(self):
        d = decide(_est(projected_input_tokens=200_000, projected_output_tokens=10_000,
                        projected_cost_usd=60.0), self.cfg)
        assert d.decision == "hard_block"
        assert d.requires_approval is False

    def test_hard_block_on_tokens(self):
        d = decide(_est(projected_input_tokens=1_200_000, projected_output_tokens=10_000,
                        projected_cost_usd=20.0), self.cfg)
        assert d.decision == "hard_block"
        assert d.requires_approval is False


class TestTIPDirective:
    cfg = _per_request_cfg()

    def test_tip_allow_once_within_ceiling(self):
        tip = TIPDirective(allow_scope="once", max_cost_usd=15.0)
        d = decide(_est(projected_input_tokens=600_000, projected_output_tokens=2000,
                        projected_cost_usd=12.0), self.cfg, tip=tip)
        assert d.decision == "allow"
        assert d.threshold_hit == "tip_directive"

    def test_tip_ceiling_too_low(self):
        tip = TIPDirective(allow_scope="once", max_cost_usd=8.0)
        d = decide(_est(projected_input_tokens=400_000, projected_output_tokens=2000,
                        projected_cost_usd=12.0), self.cfg, tip=tip)
        assert d.decision == "block"
        assert d.reason == "projected_exceeds_tip_ceiling"

    def test_tip_bypass_does_not_override_hard_block(self):
        # Proposal §4 second example: hard cap survives bypass.
        tip = TIPDirective(bypass=True, max_cost_usd=999.0)
        d = decide(_est(projected_input_tokens=1_500_000, projected_output_tokens=10_000,
                        projected_cost_usd=80.0), self.cfg, tip=tip)
        assert d.decision == "hard_block"


class TestSessionCumulative:
    """Defense against the death-by-1000-cuts spike pattern."""

    def test_default_session_threshold_is_10_dollars(self):
        cfg = SpendGuardConfig()
        assert cfg.session_block_cost_usd == 10.0

    def test_session_block_when_running_plus_cost_exceeds(self):
        cfg = SpendGuardConfig()
        d = decide(_est(projected_cost_usd=0.5),
                   cfg, session_running_cost_usd=9.7)
        assert d.decision == "block"
        assert d.reason == "session_cumulative_cost_exceeded"

    def test_session_allow_when_under(self):
        cfg = SpendGuardConfig()
        d = decide(_est(projected_cost_usd=0.5),
                   cfg, session_running_cost_usd=5.0)
        assert d.decision == "allow"

    def test_session_disabled_when_zero(self):
        cfg = SpendGuardConfig()
        cfg.session_block_cost_usd = 0.0
        d = decide(_est(projected_cost_usd=0.5),
                   cfg, session_running_cost_usd=100.0)
        assert d.decision == "allow"


class TestEnvOverrides:
    def test_env_disable(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_SPEND_GUARD_ENABLED", "false")
        cfg = load_config(raw_config={})
        assert cfg.enabled is False

    def test_env_threshold_override(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_SPEND_GUARD_BLOCK_COST_USD", "3.0")
        cfg = load_config(raw_config={})
        assert cfg.block_cost_usd == 3.0


class TestConfigDictLoad:
    def test_yaml_block_loaded(self):
        raw = {
            "spend_guard": {
                "enabled": True,
                "block_tokens": 250_000,
                "block_cost_usd": 5.0,
            }
        }
        cfg = load_config(raw_config=raw)
        assert cfg.block_tokens == 250_000
        assert cfg.block_cost_usd == 5.0
        # Unspecified keys keep defaults
        assert cfg.hard_block_cost_usd == 50.0
