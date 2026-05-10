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


class TestConfigDefaults:
    def test_static_fallback_thresholds(self):
        cfg = SpendGuardConfig()
        # block_tokens is the *fallback* used only when the selected model's
        # context window is unavailable. Frontier-model traffic gets the
        # dynamic 80%-of-context derivation instead (see TestDynamicBlockThreshold).
        assert cfg.block_tokens == 500_000
        assert cfg.block_cost_usd == 10.0
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


class TestDynamicBlockThreshold:
    """End-to-end behavior of the dynamic block-tokens band inside decide().

    The block-tokens band derives as ``80% × model_max_context_tokens`` when
    a context is supplied. When unsupplied (``None``), the configured
    ``cfg.block_tokens`` fallback applies. The hard-block ceiling caps the
    derived value as a safety net.
    """

    cfg = _per_request_cfg()

    def test_200k_context_blocks_at_160k(self):
        # Under 160K → allow; at/above 160K → block.
        d_under = decide(
            _est(projected_input_tokens=159_000, projected_output_tokens=500,
                 projected_cost_usd=2.5),
            self.cfg, model_max_context_tokens=200_000,
        )
        assert d_under.decision == "warn"  # in warn band but under 160K

        d_over = decide(
            _est(projected_input_tokens=160_500, projected_output_tokens=500,
                 projected_cost_usd=2.5),
            self.cfg, model_max_context_tokens=200_000,
        )
        assert d_over.decision == "block"
        assert d_over.reason == "projected_tokens_exceeded"
        assert "160000" in d_over.threshold_hit
        assert "block_tokens_dynamic" in d_over.threshold_hit
        assert "max_context=200000" in d_over.threshold_hit

    def test_1m_context_blocks_at_800k(self):
        d = decide(
            _est(projected_input_tokens=800_500, projected_output_tokens=500,
                 projected_cost_usd=8.0),
            self.cfg, model_max_context_tokens=1_000_000,
        )
        assert d.decision == "block"
        assert "800000" in d.threshold_hit
        assert "block_tokens_dynamic" in d.threshold_hit

    def test_500k_context_blocks_at_400k(self):
        d = decide(
            _est(projected_input_tokens=400_500, projected_output_tokens=500,
                 projected_cost_usd=8.0),
            self.cfg, model_max_context_tokens=500_000,
        )
        assert d.decision == "block"
        assert "400000" in d.threshold_hit

    def test_2m_context_capped_by_hard_block(self):
        # 2M context → derived 1.6M → capped by hard_block_tokens=1M.
        # A 1.6M-token request first trips hard_block (≥1M) before the
        # block check ever fires; verify hard-block fires on a 1.05M request.
        d = decide(
            _est(projected_input_tokens=1_050_000, projected_output_tokens=10_000,
                 projected_cost_usd=18.0),
            self.cfg, model_max_context_tokens=2_000_000,
        )
        assert d.decision == "hard_block"

    def test_unknown_context_falls_back_to_static_block_tokens(self):
        # No model_max_context_tokens → fallback to cfg.block_tokens (500K).
        d = decide(
            _est(projected_input_tokens=500_500, projected_output_tokens=500,
                 projected_cost_usd=8.0),
            self.cfg, model_max_context_tokens=None,
        )
        assert d.decision == "block"
        assert "500000" in d.threshold_hit
        assert "block_tokens_fallback" in d.threshold_hit

    def test_unknown_context_below_fallback_allowed(self):
        d = decide(
            _est(projected_input_tokens=400_000, projected_output_tokens=2000,
                 projected_cost_usd=6.5),
            self.cfg, model_max_context_tokens=None,
        )
        # 402K < 500K fallback → allow (cost also under block_cost_usd=10)
        assert d.decision == "warn"

    def test_model_switch_changes_effective_threshold(self):
        # Same request, different model → different decision.
        # 170K projected tokens.
        est = _est(projected_input_tokens=169_500, projected_output_tokens=500,
                   projected_cost_usd=2.6)

        # 200K-context model: 170K > 160K block → block.
        d_200k = decide(est, self.cfg, model_max_context_tokens=200_000)
        assert d_200k.decision == "block"

        # 1M-context model: 170K < 800K block → warn (it's in warn band by token count).
        d_1m = decide(est, self.cfg, model_max_context_tokens=1_000_000)
        assert d_1m.decision == "warn"

    def test_dynamic_threshold_never_silently_assumes_one_million(self):
        # Pin the documented behavior: 800K is NOT a default — it only
        # results from a 1M context model. Unknown context with the
        # default fallback (500K) blocks at 500K, not 800K.
        d = decide(
            _est(projected_input_tokens=800_000, projected_output_tokens=500,
                 projected_cost_usd=8.0),
            self.cfg, model_max_context_tokens=None,
        )
        assert d.decision == "block"
        # Threshold message must reflect the fallback, not 800K.
        assert "500000" in d.threshold_hit
        assert "block_tokens_fallback" in d.threshold_hit

    def test_hard_block_still_immutable_in_dynamic_path(self):
        # Even if a model has 5M context (derived 4M), the hard 1M ceiling
        # still wins on a 1.5M-token request.
        d = decide(
            _est(projected_input_tokens=1_500_000, projected_output_tokens=0,
                 projected_cost_usd=22.0),
            self.cfg, model_max_context_tokens=5_000_000,
        )
        assert d.decision == "hard_block"
