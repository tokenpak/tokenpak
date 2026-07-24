import pytest

pytest.importorskip("tokenpak.budget_controller", reason="module not available in current build")
import pytest
from tokenpak.budget_controller import (
    BudgetController,
    ClassificationResult,
    IntentClass,
)


@pytest.mark.parametrize(
    "intent,expected",
    [
        (IntentClass.GEN_Q, "T0_8K"),
        (IntentClass.CODE_Q, "T1_16K"),
        (IntentClass.DEBUG, "T2_32K"),
        (IntentClass.CODE_EDIT, "T2_32K"),
        (IntentClass.DOC_EDIT, "T1_16K"),
        (IntentClass.PLAN, "T1_16K"),
        (IntentClass.REVIEW, "T2_32K"),
    ],
)
def test_intent_to_tier_mapping(intent, expected):
    c = BudgetController()
    decision = c.decide(ClassificationResult(intent=intent, complexity_score=0.4))
    assert decision.target_tier == expected


def test_escalation_triggers_on_low_coverage():
    c = BudgetController()
    base = c.decide(ClassificationResult(intent=IntentClass.CODE_Q, complexity_score=0.3))
    bumped = c.maybe_escalate(base, coverage_score=0.3, intent=IntentClass.CODE_Q)
    assert bumped.target_tier == "T2_32K"


def test_no_escalation_when_coverage_sufficient():
    c = BudgetController()
    base = c.decide(ClassificationResult(intent=IntentClass.CODE_Q, complexity_score=0.3))
    same = c.maybe_escalate(base, coverage_score=0.7, intent=IntentClass.CODE_Q)
    assert same.target_tier == "T1_16K"


def test_max_auto_tier_respected():
    c = BudgetController()
    base = c.decide(ClassificationResult(intent=IntentClass.REVIEW, complexity_score=0.9))
    bumped1 = c.maybe_escalate(base, coverage_score=0.2, intent=IntentClass.REVIEW)
    bumped2 = c.maybe_escalate(bumped1, coverage_score=0.2, intent=IntentClass.REVIEW)
    assert bumped1.target_tier == "T3_64K"
    assert bumped2.target_tier == "T3_64K"


def test_t4_only_with_multi_module_gate():
    c = BudgetController()
    t3 = c.maybe_escalate(
        c.decide(ClassificationResult(intent=IntentClass.REVIEW, complexity_score=0.9)),
        coverage_score=0.2,
        intent=IntentClass.REVIEW,
    )
    t4 = c.maybe_escalate(
        t3,
        coverage_score=0.2,
        intent=IntentClass.REVIEW,
        multi_module_edit=True,
    )
    assert t4.target_tier == "T4_128K"


def test_t4_not_allowed_for_non_edit_intent():
    c = BudgetController()
    t3 = c.maybe_escalate(
        c.decide(ClassificationResult(intent=IntentClass.DEBUG, complexity_score=0.9)),
        coverage_score=0.2,
        intent=IntentClass.DEBUG,
    )
    not_t4 = c.maybe_escalate(
        t3,
        coverage_score=0.2,
        intent=IntentClass.DEBUG,
        multi_module_edit=True,
    )
    assert not_t4.target_tier == "T3_64K"


def test_config_override_threshold():
    c = BudgetController(coverage_threshold=0.2)
    base = c.decide(ClassificationResult(intent=IntentClass.CODE_Q, complexity_score=0.1))
    same = c.maybe_escalate(base, coverage_score=0.3, intent=IntentClass.CODE_Q)
    assert same.target_tier == "T1_16K"
