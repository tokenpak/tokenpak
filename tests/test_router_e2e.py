"""End-to-end tests for classifier-first router in tokenpak.proxy.

Verifies the complete flow:
  1. Extract user text from request
  2. Classify intent
  3. Extract slots
  4. Resolve policy
  5. Apply compression conditionally
  6. Return meaningful metadata
"""

import pytest

pytest.importorskip("tokenpak.proxy.intent_policy", reason="module not available in current build")
import sys
from pathlib import Path

import pytest

# Add proxy location to path
sys.path.insert(0, str(Path.home()))

from tokenpak.proxy.intent_policy import known_intents


class TestRouterE2E:
    """End-to-end router behavior tests."""

    def test_slot_filler_duration(self):
        """Slot filler should extract duration values."""
        from tokenpak.compression.slot_filler import SlotFiller

        filler = SlotFiller()
        result = filler.fill("summarize", "summarize the vault for the last 7 days")
        assert "period" in result.slots
        assert "7d" in result.slots["period"]

    def test_slot_filler_multiple_intents(self):
        """Slot filler should work across all canonical intents."""
        from tokenpak.compression.slot_filler import SlotFiller

        filler = SlotFiller()

        # Test each intent
        test_cases = [
            ("status", "what is the status of the proxy", ["target"]),
            ("usage", "how much have we spent in the last month", ["period"]),
            ("debug", "debug the router in verbose mode", []),
            ("summarize", "summarize for 7 days", ["period"]),
            ("plan", "plan a detailed feature", ["scope"]),
            ("create", "create a new module", ["target"]),
        ]

        for intent, text, expected_slots in test_cases:
            result = filler.fill(intent, text)
            assert result.intent == intent

    def test_intent_policy_all_intents(self):
        """Policy should have entries for all canonical intents."""
        from tokenpak.proxy.intent_policy import decide

        intents = known_intents()
        for intent in intents:
            decision = decide(intent, {}, 1.0)
            assert decision.recipe_id != ""
            assert isinstance(decision.action.compress, bool)
            assert isinstance(decision.action.retrieve, bool)

    def test_policy_consistency_across_calls(self):
        """Multiple calls with same args should produce identical decisions."""
        from tokenpak.proxy.intent_policy import decide

        slots = {"period": "7d", "target": "vault"}
        d1 = decide("summarize", slots, 0.9)
        d2 = decide("summarize", slots, 0.9)

        assert d1.recipe_id == d2.recipe_id
        assert d1.action.compress == d2.action.compress
        assert d1.action.retrieve == d2.action.retrieve
        assert d1.fallback == d2.fallback

    def test_slot_filler_with_policy(self):
        """Full pipeline: fill slots → resolve policy."""
        from tokenpak.proxy.intent_policy import decide

        from tokenpak.compression.slot_filler import SlotFiller

        filler = SlotFiller()

        text = "summarize the vault for the last 7 days in full detail"
        filled = filler.fill("summarize", text)
        decision = decide("summarize", filled.slots, filled.confidence)

        assert decision.intent == "summarize"
        assert decision.action.compress is True
        assert decision.recipe_id == "summarize-compress"

    def test_low_confidence_fallback_behavior(self):
        """Low-confidence results should use fallback recipe."""
        from tokenpak.proxy.intent_policy import CONFIDENCE_THRESHOLD, decide

        low_conf = CONFIDENCE_THRESHOLD - 0.05
        decision = decide("plan", {}, low_conf)

        assert decision.fallback is True
        assert "low_confidence" in decision.fallback_reason
        assert decision.recipe_id == "pipeline-v1"

    def test_unknown_intent_fallback(self):
        """Unknown intent should use fallback recipe."""
        from tokenpak.proxy.intent_policy import decide

        decision = decide("unknown_intent_xyz_123", {}, 1.0)

        assert decision.fallback is True
        assert "unknown_intent" in decision.fallback_reason
        assert decision.recipe_id == "pipeline-v1"

    def test_search_always_retrieves(self):
        """Search intent should always enable retrieval."""
        from tokenpak.proxy.intent_policy import decide

        decision = decide("search", {}, 1.0)
        assert decision.action.retrieve is True
        assert decision.recipe_id == "search-retrieve"

    def test_status_no_compression(self):
        """Status intent should skip compression."""
        from tokenpak.proxy.intent_policy import decide

        decision = decide("status", {}, 1.0)
        assert decision.action.compress is False
        assert decision.action.skip_compression is True

    def test_plan_with_detailed_scope(self):
        """Plan intent with detailed scope should enable retrieval."""
        from tokenpak.proxy.intent_policy import decide

        slots = {"scope": "detailed"}
        decision = decide("plan", slots, 1.0)
        assert decision.action.retrieve is True

    def test_execute_dry_run_mode(self):
        """Execute intent with dry_run mode should set flag."""
        from tokenpak.proxy.intent_policy import decide

        slots = {"mode": "dry_run"}
        decision = decide("execute", slots, 1.0)
        assert decision.action.dry_run is True
        assert decision.action.skip_compression is True

    def test_decision_is_immutable(self):
        """RoutingDecision should be frozen (immutable)."""
        from tokenpak.proxy.intent_policy import decide

        decision = decide("status", {}, 1.0)

        with pytest.raises(AttributeError):
            decision.intent = "debug"

        with pytest.raises(AttributeError):
            decision.action.compress = True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
