# SPDX-License-Identifier: Apache-2.0
"""Unit tests for budget_controller.py — tier selection, threshold alerts, and escalation logic."""

from __future__ import annotations

import pytest

from tokenpak.budget_controller import (
    BudgetController,
    BudgetDecision,
    BudgetThresholdAlert,
    ClassificationResult,
    DEFAULT_TIER_ORDER,
    DEFAULT_TIER_TOKENS,
    IntentClass,
    TIER_MAP,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _classify(intent: IntentClass, complexity: float = 0.5) -> ClassificationResult:
    return ClassificationResult(intent=intent, complexity_score=complexity)


def _controller(**kwargs) -> BudgetController:
    return BudgetController(**kwargs)


# ---------------------------------------------------------------------------
# IntentClass enum
# ---------------------------------------------------------------------------


class TestIntentClass:
    def test_all_intents_defined(self):
        expected = {"GEN_Q", "CODE_Q", "CODE_EDIT", "DEBUG", "DOC_EDIT", "PLAN", "REVIEW"}
        assert {i.value for i in IntentClass} == expected

    def test_string_enum_identity(self):
        assert IntentClass.GEN_Q == "GEN_Q"
        assert IntentClass("CODE_EDIT") is IntentClass.CODE_EDIT


# ---------------------------------------------------------------------------
# BudgetController construction
# ---------------------------------------------------------------------------


class TestBudgetControllerInit:
    def test_defaults(self):
        bc = BudgetController()
        assert bc.tier_map == dict(TIER_MAP)
        assert bc.tier_tokens == dict(DEFAULT_TIER_TOKENS)
        assert bc.tier_order == list(DEFAULT_TIER_ORDER)
        assert bc.coverage_threshold == 0.55
        assert bc.max_auto_tier == "T3_64K"
        assert bc.t4_intents == {IntentClass.CODE_EDIT, IntentClass.REVIEW}

    def test_invalid_max_auto_tier_raises(self):
        with pytest.raises(ValueError, match="max_auto_tier"):
            BudgetController(max_auto_tier="T99_UNKNOWN")

    def test_custom_coverage_threshold(self):
        bc = BudgetController(coverage_threshold=0.75)
        assert bc.coverage_threshold == 0.75

    def test_custom_max_auto_tier(self):
        bc = BudgetController(max_auto_tier="T2_32K")
        assert bc.max_auto_tier == "T2_32K"

    def test_custom_t4_intents(self):
        bc = BudgetController(t4_intents=(IntentClass.DEBUG,))
        assert bc.t4_intents == {IntentClass.DEBUG}

    def test_custom_tier_tokens(self):
        custom_tokens = {"T0_8K": 9000, "T1_16K": 17000, "T2_32K": 33000, "T3_64K": 65000, "T4_128K": 129000}
        bc = BudgetController(tier_tokens=custom_tokens)
        assert bc.tier_tokens == custom_tokens

    def test_custom_tier_map(self):
        custom_map = {IntentClass.GEN_Q: "T2_32K"}
        bc = BudgetController(tier_map=custom_map, max_auto_tier="T2_32K")
        assert bc.tier_map == custom_map


# ---------------------------------------------------------------------------
# BudgetController.decide
# ---------------------------------------------------------------------------


class TestDecide:
    def test_gen_q_maps_to_t0(self):
        bc = BudgetController()
        decision = bc.decide(_classify(IntentClass.GEN_Q))
        assert decision.target_tier == "T0_8K"
        assert decision.target_token_budget == 8_000

    def test_code_q_maps_to_t1(self):
        bc = BudgetController()
        decision = bc.decide(_classify(IntentClass.CODE_Q))
        assert decision.target_tier == "T1_16K"
        assert decision.target_token_budget == 16_000

    def test_debug_maps_to_t2(self):
        bc = BudgetController()
        decision = bc.decide(_classify(IntentClass.DEBUG))
        assert decision.target_tier == "T2_32K"
        assert decision.target_token_budget == 32_000

    def test_code_edit_maps_to_t2(self):
        bc = BudgetController()
        decision = bc.decide(_classify(IntentClass.CODE_EDIT))
        assert decision.target_tier == "T2_32K"

    def test_doc_edit_maps_to_t1(self):
        bc = BudgetController()
        decision = bc.decide(_classify(IntentClass.DOC_EDIT))
        assert decision.target_tier == "T1_16K"

    def test_plan_maps_to_t1(self):
        bc = BudgetController()
        decision = bc.decide(_classify(IntentClass.PLAN))
        assert decision.target_tier == "T1_16K"

    def test_review_maps_to_t2(self):
        bc = BudgetController()
        decision = bc.decide(_classify(IntentClass.REVIEW))
        assert decision.target_tier == "T2_32K"

    def test_decision_reason_contains_intent_and_complexity(self):
        bc = BudgetController()
        decision = bc.decide(_classify(IntentClass.GEN_Q, complexity=0.75))
        reasons = " ".join(decision.reason)
        assert "intent=GEN_Q" in reasons
        assert "complexity=0.75" in reasons

    def test_decision_allow_escalation_true(self):
        bc = BudgetController()
        decision = bc.decide(_classify(IntentClass.DEBUG))
        assert decision.allow_escalation is True

    def test_decision_max_auto_tier_propagated(self):
        bc = BudgetController(max_auto_tier="T2_32K")
        decision = bc.decide(_classify(IntentClass.GEN_Q))
        assert decision.max_auto_tier == "T2_32K"

    def test_unknown_intent_defaults_to_t1(self):
        # Provide a non-empty map that omits GEN_Q so .get() uses the default
        bc = BudgetController(tier_map={IntentClass.DEBUG: "T2_32K"})
        decision = bc.decide(_classify(IntentClass.GEN_Q))
        assert decision.target_tier == "T1_16K"
        assert decision.target_token_budget == 16_000


# ---------------------------------------------------------------------------
# BudgetController.check_spending_threshold
# ---------------------------------------------------------------------------


class TestCheckSpendingThreshold:
    def test_zero_budget_returns_empty(self):
        bc = BudgetController()
        assert bc.check_spending_threshold(spent_usd=50.0, budget_usd=0) == []

    def test_negative_budget_returns_empty(self):
        bc = BudgetController()
        assert bc.check_spending_threshold(spent_usd=50.0, budget_usd=-10.0) == []

    def test_below_warning_no_alerts(self):
        bc = BudgetController()
        alerts = bc.check_spending_threshold(spent_usd=7.0, budget_usd=100.0)
        assert alerts == []

    def test_exactly_at_warning_threshold(self):
        bc = BudgetController()
        alerts = bc.check_spending_threshold(spent_usd=80.0, budget_usd=100.0)
        assert len(alerts) == 1
        assert alerts[0].level == "warning"
        assert alerts[0].pct_used == pytest.approx(80.0)

    def test_between_warning_and_critical(self):
        bc = BudgetController()
        alerts = bc.check_spending_threshold(spent_usd=95.0, budget_usd=100.0)
        assert len(alerts) == 1
        assert alerts[0].level == "warning"

    def test_exactly_at_critical_threshold(self):
        bc = BudgetController()
        alerts = bc.check_spending_threshold(spent_usd=100.0, budget_usd=100.0)
        assert len(alerts) == 1
        assert alerts[0].level == "critical"
        assert alerts[0].pct_used == pytest.approx(100.0)

    def test_between_critical_and_overage(self):
        bc = BudgetController()
        alerts = bc.check_spending_threshold(spent_usd=105.0, budget_usd=100.0)
        assert len(alerts) == 1
        assert alerts[0].level == "critical"

    def test_at_overage_threshold(self):
        bc = BudgetController()
        alerts = bc.check_spending_threshold(spent_usd=110.0, budget_usd=100.0)
        assert len(alerts) == 1
        assert alerts[0].level == "overage"
        assert alerts[0].pct_used == pytest.approx(110.0)

    def test_overage_message_contains_budget(self):
        bc = BudgetController()
        alerts = bc.check_spending_threshold(spent_usd=220.0, budget_usd=100.0)
        assert len(alerts) == 1
        assert alerts[0].level == "overage"
        assert "$100.00" in alerts[0].message

    def test_warning_message_contains_pct(self):
        bc = BudgetController()
        alerts = bc.check_spending_threshold(spent_usd=85.0, budget_usd=100.0)
        assert "85.0%" in alerts[0].message

    def test_critical_message_contains_pct(self):
        bc = BudgetController()
        alerts = bc.check_spending_threshold(spent_usd=100.0, budget_usd=100.0)
        assert "100.0%" in alerts[0].message

    def test_returns_at_most_one_alert_per_check(self):
        # Thresholds are mutually exclusive (if/elif/elif)
        bc = BudgetController()
        alerts = bc.check_spending_threshold(spent_usd=120.0, budget_usd=100.0)
        assert len(alerts) == 1


# ---------------------------------------------------------------------------
# BudgetController.maybe_escalate
# ---------------------------------------------------------------------------


class TestMaybeEscalate:
    def _decision(self, tier: str, bc: BudgetController | None = None) -> BudgetDecision:
        _bc = bc or BudgetController()
        return _bc.decide(_classify(IntentClass.CODE_EDIT if tier == "T2_32K" else IntentClass.GEN_Q))

    def test_no_escalation_when_coverage_sufficient(self):
        bc = BudgetController(coverage_threshold=0.55)
        decision = bc.decide(_classify(IntentClass.GEN_Q))
        result = bc.maybe_escalate(decision, coverage_score=0.60, intent=IntentClass.GEN_Q)
        assert result.target_tier == "T0_8K"
        assert any("no_escalation" in r for r in result.reason)

    def test_no_escalation_at_exact_coverage_threshold(self):
        bc = BudgetController(coverage_threshold=0.55)
        decision = bc.decide(_classify(IntentClass.GEN_Q))
        result = bc.maybe_escalate(decision, coverage_score=0.55, intent=IntentClass.GEN_Q)
        assert result.target_tier == "T0_8K"

    def test_escalates_plus_one_tier_on_low_coverage(self):
        bc = BudgetController(coverage_threshold=0.55)
        decision = bc.decide(_classify(IntentClass.GEN_Q))  # T0_8K
        result = bc.maybe_escalate(decision, coverage_score=0.40, intent=IntentClass.GEN_Q)
        assert result.target_tier == "T1_16K"
        assert any("escalated(+1_low_coverage)" in r for r in result.reason)

    def test_escalation_capped_at_max_auto_tier(self):
        bc = BudgetController(max_auto_tier="T2_32K")
        # DEBUG maps to T2_32K which is already max_auto_tier
        decision = bc.decide(_classify(IntentClass.DEBUG))  # T2_32K
        result = bc.maybe_escalate(decision, coverage_score=0.10, intent=IntentClass.DEBUG)
        assert result.target_tier == "T2_32K"
        assert any("escalation_capped" in r for r in result.reason)

    def test_t4_escalation_code_edit_multi_module(self):
        bc = BudgetController(max_auto_tier="T3_64K")
        # Force the decision to be at T3_64K (the max_auto_tier)
        decision = BudgetDecision(
            target_tier="T3_64K",
            target_token_budget=64_000,
            reason=["intent=CODE_EDIT"],
            allow_escalation=True,
            max_auto_tier="T3_64K",
        )
        result = bc.maybe_escalate(
            decision,
            coverage_score=0.10,
            intent=IntentClass.CODE_EDIT,
            multi_module_edit=True,
        )
        assert result.target_tier == "T4_128K"
        assert any("escalated_to_T4" in r for r in result.reason)

    def test_t4_escalation_review_multi_module(self):
        bc = BudgetController(max_auto_tier="T3_64K")
        decision = BudgetDecision(
            target_tier="T3_64K",
            target_token_budget=64_000,
            reason=["intent=REVIEW"],
            allow_escalation=True,
            max_auto_tier="T3_64K",
        )
        result = bc.maybe_escalate(
            decision,
            coverage_score=0.10,
            intent=IntentClass.REVIEW,
            multi_module_edit=True,
        )
        assert result.target_tier == "T4_128K"

    def test_no_t4_escalation_when_not_multi_module(self):
        bc = BudgetController(max_auto_tier="T3_64K")
        decision = BudgetDecision(
            target_tier="T3_64K",
            target_token_budget=64_000,
            reason=["intent=CODE_EDIT"],
            allow_escalation=True,
            max_auto_tier="T3_64K",
        )
        result = bc.maybe_escalate(
            decision,
            coverage_score=0.10,
            intent=IntentClass.CODE_EDIT,
            multi_module_edit=False,
        )
        # Should cap, not escalate to T4
        assert result.target_tier == "T3_64K"
        assert any("escalation_capped" in r for r in result.reason)

    def test_no_t4_escalation_for_non_t4_intent(self):
        bc = BudgetController(max_auto_tier="T3_64K")
        decision = BudgetDecision(
            target_tier="T3_64K",
            target_token_budget=64_000,
            reason=["intent=DEBUG"],
            allow_escalation=True,
            max_auto_tier="T3_64K",
        )
        result = bc.maybe_escalate(
            decision,
            coverage_score=0.10,
            intent=IntentClass.DEBUG,
            multi_module_edit=True,
        )
        assert result.target_tier == "T3_64K"

    def test_escalate_preserves_allow_escalation(self):
        bc = BudgetController()
        decision = bc.decide(_classify(IntentClass.GEN_Q))
        result = bc.maybe_escalate(decision, coverage_score=0.10, intent=IntentClass.GEN_Q)
        assert result.allow_escalation == decision.allow_escalation

    def test_escalate_appends_coverage_to_reason(self):
        bc = BudgetController()
        decision = bc.decide(_classify(IntentClass.GEN_Q))
        result = bc.maybe_escalate(decision, coverage_score=0.30, intent=IntentClass.GEN_Q)
        assert any("coverage=0.30" in r for r in result.reason)

    def test_no_t4_when_max_auto_tier_already_t4(self):
        bc = BudgetController(max_auto_tier="T4_128K")
        decision = BudgetDecision(
            target_tier="T4_128K",
            target_token_budget=128_000,
            reason=["intent=CODE_EDIT"],
            allow_escalation=True,
            max_auto_tier="T4_128K",
        )
        result = bc.maybe_escalate(
            decision,
            coverage_score=0.10,
            intent=IntentClass.CODE_EDIT,
            multi_module_edit=True,
        )
        # When max_auto_tier IS T4, the T4 gate condition is False — it caps
        assert result.target_tier == "T4_128K"
