"""Tests for intent_policy module — classifier-first routing decisions.

Verifies:
  - Intent classification is deterministic (same text → same intent)
  - Slot extraction works for all canonical intents
  - Policy resolution maps (intent, slots, confidence) → recipe + action
  - Fallback behavior for unknown/low-confidence intents
  - Action profiles (lightweight, compress, verbose, retrieve, standard)
"""


import pytest
pytest.importorskip("tokenpak.proxy.intent_policy", reason="module not available in current build")
import pytest
from tokenpak.proxy.intent_policy import (
    decide,
    resolve_policy,
    known_intents,
    CONFIDENCE_THRESHOLD,
    FALLBACK_POLICY,
    DecisionAction,
    RoutingDecision,
)
from tokenpak.compression.slot_filler import SlotFiller


class TestIntentPolicyBasics:
    """Test basic policy resolution."""

    def test_known_intents_list(self):
        """Verify canonical intent set is defined."""
        intents = known_intents()
        assert isinstance(intents, list)
        assert len(intents) > 0
        assert "status" in intents
        assert "usage" in intents
        assert "debug" in intents
        assert "summarize" in intents
        assert "plan" in intents
        assert "execute" in intents
        assert "explain" in intents
        assert "search" in intents
        assert "create" in intents
        assert "query" in intents

    def test_status_intent_policy(self):
        """Status intent should be lightweight, no compression."""
        decision = decide("status", {}, confidence=1.0)
        assert decision.intent == "status"
        assert decision.recipe_id == "status-report"
        assert decision.action.compress is False
        assert decision.action.skip_compression is True
        assert decision.fallback is False

    def test_usage_intent_policy(self):
        """Usage intent should be lightweight, no compression."""
        decision = decide("usage", {}, confidence=1.0)
        assert decision.intent == "usage"
        assert decision.recipe_id == "usage-report"
        assert decision.action.compress is False
        assert decision.action.skip_compression is True

    def test_debug_intent_policy(self):
        """Debug intent should use verbose action profile, no compression."""
        decision = decide("debug", {}, confidence=1.0)
        assert decision.intent == "debug"
        assert decision.recipe_id == "debug-trace"
        assert decision.action.compress is False
        assert decision.action.skip_compression is True

    def test_summarize_intent_policy(self):
        """Summarize intent should use compress profile."""
        decision = decide("summarize", {}, confidence=1.0)
        assert decision.intent == "summarize"
        assert decision.recipe_id == "summarize-compress"
        assert decision.action.compress is True
        assert decision.action.retrieve is False

    def test_search_intent_policy(self):
        """Search intent should use retrieve profile."""
        decision = decide("search", {}, confidence=1.0)
        assert decision.intent == "search"
        assert decision.recipe_id == "search-retrieve"
        assert decision.action.retrieve is True
        assert decision.action.compress is False

    def test_plan_intent_policy(self):
        """Plan intent should use standard profile + retrieve."""
        decision = decide("plan", {}, confidence=1.0)
        assert decision.intent == "plan"
        assert decision.recipe_id == "plan-scaffold"
        assert decision.action.compress is True
        assert decision.action.retrieve is True

    def test_create_intent_policy(self):
        """Create intent should use standard profile."""
        decision = decide("create", {}, confidence=1.0)
        assert decision.intent == "create"
        assert decision.recipe_id == "create-scaffold"
        assert decision.action.compress is True

    def test_query_intent_policy(self):
        """Query is safe fallback intent."""
        decision = decide("query", {}, confidence=1.0)
        assert decision.intent == "query"
        assert decision.recipe_id == "pipeline-v1"
        assert decision.action.compress is True


class TestFallback:
    """Test fallback behavior for unknown/low-confidence intents."""

    def test_unknown_intent_fallback(self):
        """Unknown intent should fall back to pipeline-v1."""
        decision = decide("unknown_intent_xyz", {}, confidence=1.0)
        assert decision.fallback is True
        assert decision.fallback_reason == "unknown_intent"
        assert decision.recipe_id == FALLBACK_POLICY.recipe_id

    def test_low_confidence_fallback(self):
        """Low confidence (below threshold) should trigger fallback."""
        low_conf = CONFIDENCE_THRESHOLD - 0.1
        decision = decide("status", {}, confidence=low_conf)
        assert decision.fallback is True
        assert "low_confidence" in decision.fallback_reason
        assert decision.confidence == low_conf

    def test_fallback_preserves_intent(self):
        """Fallback decision should still record the classified intent."""
        decision = decide("summarize", {}, confidence=0.1)
        assert decision.intent == "summarize"
        assert decision.fallback is True


class TestSlotRefinements:
    """Test slot-based policy refinements."""

    def test_debug_verbose_refinement(self):
        """Debug + verbose detail level should refine to verbose action profile."""
        slots = {"detail_level": "verbose"}
        decision = decide("debug", slots, confidence=1.0)
        assert decision.action.skip_compression is True

    def test_execute_dry_run_mode(self):
        """Execute + dry_run mode should set dry_run flag."""
        slots = {"mode": "dry_run"}
        decision = decide("execute", slots, confidence=1.0)
        assert decision.action.dry_run is True
        assert decision.action.skip_compression is True

    def test_plan_detailed_scope(self):
        """Plan + detailed scope should enable retrieval."""
        slots = {"scope": "detailed"}
        decision = decide("plan", slots, confidence=1.0)
        assert decision.action.retrieve is True

    def test_search_always_retrieves(self):
        """Search should always have retrieve=True regardless of slots."""
        decision = decide("search", {"target": "vault"}, confidence=1.0)
        assert decision.action.retrieve is True


class TestDeterminism:
    """Test that decisions are deterministic (same input → same output)."""

    def test_deterministic_status(self):
        """Same status intent should produce identical decisions."""
        d1 = decide("status", {}, 1.0)
        d2 = decide("status", {}, 1.0)
        assert d1.recipe_id == d2.recipe_id
        assert d1.action.compress == d2.action.compress
        assert d1.action.retrieve == d2.action.retrieve

    def test_deterministic_slots(self):
        """Same intent+slots should produce identical decisions."""
        slots = {"target": "vault", "period": "7d"}
        d1 = decide("summarize", slots, 0.9)
        d2 = decide("summarize", slots, 0.9)
        assert d1.recipe_id == d2.recipe_id
        assert d1.slots_used == d2.slots_used

    def test_deterministic_with_slot_filler(self):
        """Decisions for same text (through SlotFiller) should be consistent."""
        filler = SlotFiller()
        text1 = "summarize the vault for the last 7 days"
        text2 = "summarize the vault for the last 7 days"

        filled1 = filler.fill("summarize", text1)
        filled2 = filler.fill("summarize", text2)

        d1 = decide("summarize", filled1.slots, filled1.confidence)
        d2 = decide("summarize", filled2.slots, filled2.confidence)

        assert d1.recipe_id == d2.recipe_id
        assert d1.action.compress == d2.action.compress


class TestRoutingDecisionStructure:
    """Test the RoutingDecision dataclass."""

    def test_routing_decision_frozen(self):
        """RoutingDecision should be immutable (frozen)."""
        decision = decide("status", {}, 1.0)
        with pytest.raises(AttributeError):
            decision.intent = "debug"

    def test_decision_action_frozen(self):
        """DecisionAction should be immutable (frozen)."""
        action = DecisionAction(compress=True, retrieve=False)
        with pytest.raises(AttributeError):
            action.compress = False

    def test_decision_has_all_fields(self):
        """RoutingDecision should have all required fields."""
        decision = decide("plan", {"scope": "detailed"}, 0.85)
        assert hasattr(decision, "intent")
        assert hasattr(decision, "recipe_id")
        assert hasattr(decision, "slots_used")
        assert hasattr(decision, "action")
        assert hasattr(decision, "fallback")
        assert hasattr(decision, "fallback_reason")
        assert hasattr(decision, "confidence")


class TestResolvePolicy:
    """Test the legacy resolve_policy function (for backwards compat)."""

    def test_resolve_policy_returns_policy_result(self):
        """resolve_policy should return PolicyResult (old API)."""
        from tokenpak.proxy.intent_policy import PolicyResult
        result = resolve_policy("status", {}, 1.0)
        assert isinstance(result, PolicyResult)
        assert result.recipe_id == "status-report"

    def test_resolve_policy_consistency_with_decide(self):
        """Both APIs should map the same intent to the same recipe."""
        policy_result = resolve_policy("status", {}, 1.0)
        decision = decide("status", {}, 1.0)
        assert policy_result.recipe_id == decision.recipe_id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
